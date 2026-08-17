"""Execution idempotency / orphan-recovery regression tests.

Context — the PENDING -> placeOrder -> set order_id sequence in
``order_executor.place_order`` (order_executor.py ~295-319) writes a
``PENDING`` Trade row *before* it calls ``ibkr.ib.placeOrder`` and only
back-fills ``order_id`` after the broker accepts the order::

    trade_rec = Trade(..., status="PENDING", order_id=None, ...)
    await save_trade(trade_rec)            # row exists, no broker order yet
    ib_trade = ibkr.ib.placeOrder(...)     # <-- crash here = orphan
    trade_rec.order_id = ib_trade.order.orderId
    await save_trade(trade_rec)

A process crash or timeout in that window leaves a ``PENDING`` row with no
``order_id``. That local state is ambiguous: the broker may have accepted the
order even though the client never received/persisted its id. The CURRENT
reaper still sweeps such rows to terminal ``ERROR``. This is characterization
of known-unsafe behavior, not an endorsed recovery contract.

This module pins only the current orphan cleanup behavior:

1. CURRENT/UNSAFE recovery — an ambiguous PENDING row older than the orphan
   threshold is reaped to terminal ERROR. (Runs green today; blocks LIVE.)

It deliberately makes no idempotency claim. ``orderRef`` is correlation
metadata, not a broker uniqueness guarantee. The previous test used a fake
broker that invented reference-based deduplication and therefore produced
false confidence. ADR 0006 and the pre-live fault matrix define the real
durable-intent/reconcile-before-retry requirement.

These complement (do not duplicate) test_orphan_reaper_and_rate_cap.py,
which exercises the reaper's threshold/timezone edge cases. Here we tie the
reaper back to the exact place_order orphan window and document the
forward-looking dedupe contract.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import config
from database import get_trades, init_db, save_trade
from models import Trade


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def _isolated_db(tmp_path, monkeypatch):
    """Point the authoritative config at a throwaway DB for this test."""
    db_path = str(tmp_path / "execution_idempotency.db")
    monkeypatch.setattr(config.cfg, "DB_PATH", db_path)
    return db_path


def _pending_orphan(*, trade_id: str, timestamp_iso: str, order_id: int | None = None) -> Trade:
    """Build a Trade row exactly as ``place_order`` does at the PENDING save.

    Mirrors order_executor.py ~295-310: status=PENDING, order_id=None,
    position_id == id, mode=LIVE, opened_at == timestamp.
    """
    return Trade(
        id=trade_id,
        rule_id="r-orphan",
        rule_name="orphan-recovery-test",
        symbol="AAPL",
        action="BUY",  # type: ignore[arg-type]
        asset_type="STK",
        quantity=10,
        order_type="LMT",
        limit_price=100.0,
        fill_price=None,
        status="PENDING",  # type: ignore[arg-type]
        order_id=order_id,
        timestamp=timestamp_iso,
        mode="LIVE",
        position_id=trade_id,
        opened_at=timestamp_iso,
    )


def _iso_minutes_ago(minutes: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()


# ---------------------------------------------------------------------------
# 1. CURRENT recovery — orphan PENDING (no order_id, stale) -> terminal ERROR
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_current_unsafe_reaper_terminalizes_ambiguous_order(_isolated_db, anyio_backend):
    """Characterize the LIVE-blocking behavior until UNKNOWN quarantine lands.

    ``order_id=None`` cannot distinguish "never submitted" from "accepted but
    response lost." The current terminal ERROR transition is therefore unsafe.
    """
    from order_executor import reap_orphan_pending_trades

    await init_db()

    # Orphaned at the place_order PENDING save, older than the 600s threshold.
    orphan = _pending_orphan(
        trade_id="orphan-stale-1",
        timestamp_iso=_iso_minutes_ago(20),
        order_id=None,
    )
    await save_trade(orphan)

    reaped = await reap_orphan_pending_trades(stale_after_seconds=600)
    assert reaped == 1, "characterize the current unsafe terminalization"

    rows = await get_trades(limit=10)
    row = next(r for r in rows if r.id == "orphan-stale-1")
    assert row.status == "ERROR", "current unsafe behavior changed unexpectedly"
    # Terminal == not still in flight: definitely not PENDING anymore.
    assert row.status not in {"PENDING"}


@pytest.mark.anyio
async def test_reaper_emits_grepable_orphan_token(_isolated_db, anyio_backend, caplog):
    """Operators rely on the ``orphan_pending_reaped`` WARN token to find
    these crashes — pin the named outcome alongside the state transition.
    """
    import logging

    from order_executor import reap_orphan_pending_trades

    await init_db()
    await save_trade(
        _pending_orphan(trade_id="orphan-stale-2", timestamp_iso=_iso_minutes_ago(20))
    )

    with caplog.at_level(logging.WARNING):
        reaped = await reap_orphan_pending_trades(stale_after_seconds=600)

    assert reaped == 1
    assert any("orphan_pending_reaped" in r.getMessage() for r in caplog.records), (
        "reaper must emit the grep-able orphan_pending_reaped token for ops"
    )


@pytest.mark.anyio
async def test_reaper_does_not_touch_pending_with_order_id(_isolated_db, anyio_backend):
    """Boundary of the recovery: a PENDING row that *did* get an order_id is
    past the crash window — ``reconcile_pending_orders`` owns it, the reaper
    must leave it alone so we don't ERROR a live in-flight order.
    """
    from order_executor import reap_orphan_pending_trades

    await init_db()
    await save_trade(
        _pending_orphan(
            trade_id="has-order-id",
            timestamp_iso=_iso_minutes_ago(20),
            order_id=98765,
        )
    )

    reaped = await reap_orphan_pending_trades(stale_after_seconds=600)
    assert reaped == 0, "PENDING-with-order_id is reconcile's job, not the reaper's"

    rows = await get_trades(limit=10)
    row = next(r for r in rows if r.id == "has-order-id")
    assert row.status == "PENDING"

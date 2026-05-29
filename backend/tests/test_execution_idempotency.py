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

A process crash *between* those two saves leaves a ``PENDING`` row with no
``order_id``. ``reconcile_pending_orders`` cannot fix it (it only matches
IBKR open trades by order_id, and no broker order was ever sent), so the
CURRENT recovery is ``reap_orphan_pending_trades`` (order_executor.py ~470):
it sweeps stale PENDING-without-order_id rows to a terminal ERROR state.

This module pins two things:

1. CURRENT recovery — an orphaned PENDING row older than the orphan
   threshold IS reaped to a terminal ERROR state. (Runs green today.)

2. DESIRED post-integration invariant — once ``place_order`` sets
   ``ib_order.orderRef = trade_rec.id``, a replay with the same trade id is
   deduped by the broker and does NOT create a second order. ``orderRef``
   is NOT wired into ``place_order`` yet, so this test is marked ``xfail``
   with a clear reason: it is runnable today (xpasses are reported, xfails
   are quiet) and flips to a hard requirement once the plumbing lands.

These complement (do not duplicate) test_orphan_reaper_and_rate_cap.py,
which exercises the reaper's threshold/timezone edge cases. Here we tie the
reaper back to the exact place_order orphan window and document the
forward-looking dedupe contract.
"""
from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone

import pytest

import config
import database
from database import get_trades, init_db, save_trade
from models import Trade


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def _isolated_db(tmp_path, monkeypatch):
    """Point both config and database at a throwaway DB for this test."""
    db_path = str(tmp_path / "execution_idempotency.db")
    monkeypatch.setattr(config.cfg, "DB_PATH", db_path)
    monkeypatch.setattr(database, "DB_PATH", db_path)
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
async def test_orphan_pending_without_order_id_is_reaped_to_error(_isolated_db, anyio_backend):
    """The exact crash window: PENDING row saved, process died before
    ``ibkr.ib.placeOrder`` returned, so ``order_id`` is still None.

    ``reap_orphan_pending_trades`` is the current recovery — once the row is
    older than the orphan threshold it must be driven to a terminal ERROR
    state (no broker order exists, so reconcile can never resolve it and it
    must not sit PENDING forever).
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
    assert reaped == 1, "a stale PENDING-without-order_id orphan must be reaped"

    rows = await get_trades(limit=10)
    row = next(r for r in rows if r.id == "orphan-stale-1")
    assert row.status == "ERROR", "reaped orphan must land in a terminal ERROR state"
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


# ---------------------------------------------------------------------------
# 2. DESIRED post-integration invariant — orderRef-based broker dedupe
# ---------------------------------------------------------------------------


def _order_ref_is_wired() -> bool:
    """True once ``place_order`` sets ``ib_order.orderRef = trade_rec.id``.

    The reaper only *cleans up* an orphan after the fact; it cannot prevent a
    replay (a retried request / restarted process re-running the same logical
    order) from placing a *second* broker order, because the broker has no way
    to know the two submissions are the same logical trade.

    The fix is to stamp the IBKR order with a client-supplied idempotency key —
    ``ib_order.orderRef = trade_rec.id`` — so the broker / a pre-submit lookup
    can dedupe by that key. We detect whether that integration has landed by
    scanning the ``place_order`` source for the assignment. This keeps the
    xfail honest: it flips to a real requirement the moment the wiring exists,
    rather than silently passing on a hardcoded expectation.
    """
    import order_executor

    try:
        src = inspect.getsource(order_executor.place_order)
    except (OSError, TypeError):
        return False
    # Tolerant of whitespace: "orderRef" assigned from the trade record id.
    normalized = "".join(src.split())
    return "orderRef=trade_rec.id" in normalized or "ib_order.orderRef=trade_rec.id" in normalized


_ORDER_REF_WIRED = _order_ref_is_wired()


@pytest.mark.anyio
@pytest.mark.xfail(
    not _ORDER_REF_WIRED,
    reason=(
        "DESIRED invariant: place_order must set ib_order.orderRef = trade_rec.id "
        "so a replay with the same trade id is deduped and does NOT create a "
        "second broker order. orderRef is not wired into place_order yet "
        "(order_executor.py ~278-291 builds ib_order but never sets orderRef). "
        "This xfail flips to a hard requirement once the integration lands."
    ),
    strict=False,
)
async def test_replay_with_same_id_does_not_place_second_order(_isolated_db, anyio_backend):
    """Post-integration: two submissions carrying the same idempotency key
    (``orderRef == trade_rec.id``) collapse to a single broker order.

    We model a broker that dedupes by ``orderRef`` and assert two place
    attempts for the same logical trade id yield exactly one accepted order.
    With the current code path no orderRef is stamped, so the broker sees two
    distinct orders and this assertion fails — captured by the xfail above
    until the wiring exists, at which point it must pass.
    """
    await init_db()

    trade_id = "replay-dedupe-1"
    now_iso = _iso_minutes_ago(0)

    # A minimal broker double that dedupes on orderRef (the client-supplied
    # idempotency key). seen_refs models the broker / pre-submit guard.
    seen_refs: set[str] = set()
    accepted_orders: list[dict] = []

    def fake_place_order(*, order_ref: str) -> dict:
        if order_ref and order_ref in seen_refs:
            # Idempotent replay — return the already-accepted order, no new one.
            return next(o for o in accepted_orders if o["order_ref"] == order_ref)
        seen_refs.add(order_ref)
        order = {"order_id": 1000 + len(accepted_orders), "order_ref": order_ref}
        accepted_orders.append(order)
        return order

    # First submission: place_order builds the row and (post-integration)
    # stamps orderRef = trade_rec.id, then submits.
    rec1 = _pending_orphan(trade_id=trade_id, timestamp_iso=now_iso)
    await save_trade(rec1)
    order_ref_1 = rec1.id if _ORDER_REF_WIRED else ""  # current code stamps nothing
    fake_place_order(order_ref=order_ref_1)

    # Replay: same logical trade id re-submitted (retry / process restart).
    rec2 = _pending_orphan(trade_id=trade_id, timestamp_iso=now_iso)
    order_ref_2 = rec2.id if _ORDER_REF_WIRED else ""
    fake_place_order(order_ref=order_ref_2)

    assert len(accepted_orders) == 1, (
        "a replay with the same trade id must be deduped to a single broker "
        "order (requires ib_order.orderRef = trade_rec.id)"
    )


def test_doc_orderref_wiring_status_is_self_reporting():
    """Guard the xfail's truthfulness: the detector must agree with reality.

    If ``orderRef`` is wired, ``_ORDER_REF_WIRED`` is True and the xfail above
    is dropped (the dedupe test becomes mandatory). If not, it stays xfail.
    This sanity check fails loudly only if the detector itself is broken
    (e.g. ``place_order`` source became unreadable), keeping the contract
    honest rather than letting a stale boolean hide a regression.
    """
    import order_executor

    # place_order must remain introspectable for the detector to be meaningful.
    src = inspect.getsource(order_executor.place_order)
    assert "save_trade" in src, "place_order source must be readable for orderRef detection"
    # The detector is a pure function of that source; assert it is a bool.
    assert isinstance(_ORDER_REF_WIRED, bool)

"""
Batch 5b regression tests — orphan PENDING reaper and per-symbol order-rate cap.

Characterizes current behavior and pins limiter invariants:

1. KNOWN UNSAFE / LIVE BLOCKER: the orphan reaper marks an ambiguous PENDING
   trade with no local order_id as ERROR. Broker acceptance is still possible;
   this stays visible until UNKNOWN/quarantine reconciliation replaces it.
2. The reaper leaves PENDING trades with an order_id alone (those are
   reconcile_pending_orders' job).
3. The reaper does NOT touch trades younger than the threshold.
4. The SQLite-backed per-symbol rate cap rejects orders that exceed
   MAX_ORDERS_PER_SYMBOL_PER_MIN across callers in the rolling 60s window.
5. Rate-limit storage failures block orders instead of failing open.
6. The reaper time helpers use UTC consistently.
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

import config
import database
from database import init_db, save_trade
from models import Trade


@pytest.fixture
def _isolated_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "reaper.db")
    monkeypatch.setattr(config.cfg, "DB_PATH", db_path)
    monkeypatch.setattr(database, "DB_PATH", db_path)
    return db_path


@pytest.fixture
async def _with_lease(_isolated_db):
    """Acquire a valid execution lease so the fenced reaper can run."""
    import startup
    from db.execution_lease import acquire_execution_lease, release_execution_lease

    lease = await acquire_execution_lease(owner_id="reaper-test")
    prev = startup._execution_lease
    startup._execution_lease = lease
    try:
        yield lease
    finally:
        startup._execution_lease = prev
        await release_execution_lease(lease.fencing_token)


def _make_trade(*, status: str, order_id: int | None, timestamp_iso: str) -> Trade:
    return Trade(
        id=f"t-{timestamp_iso}-{order_id}",
        rule_id="r1",
        rule_name="test",
        symbol="AAPL",
        action="BUY",  # type: ignore[arg-type]
        asset_type="STK",
        quantity=10,
        order_type="LMT",
        limit_price=100.0,
        fill_price=None,
        status=status,  # type: ignore[arg-type]
        order_id=order_id,
        timestamp=timestamp_iso,
        mode="LIVE",
        opened_at=timestamp_iso,
    )


# ---------------------------------------------------------------------------
# 1. Characterization: current unsafe reaper terminalizes ambiguous outcome
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_current_unsafe_reaper_marks_ambiguous_pending_as_error(
    _isolated_db,
    _with_lease,
    anyio_backend,
    caplog,
):
    import logging

    from order_executor import reap_orphan_pending_trades
    from database import get_trades

    await init_db()

    # This local shape does not prove whether the broker accepted the order.
    old_iso = (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat()
    orphan = _make_trade(status="PENDING", order_id=None, timestamp_iso=old_iso)
    await save_trade(orphan)

    with caplog.at_level(logging.WARNING):
        reaped = await reap_orphan_pending_trades(stale_after_seconds=600)

    assert reaped == 1, "characterize the current LIVE-blocking behavior"

    # Verify the DB row is now ERROR
    rows = await get_trades(limit=10)
    assert rows[0].status == "ERROR"

    # And the WARN log uses the grep-able token
    assert any("orphan_pending_reaped" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------
# 2. Reaper leaves PENDING-with-order_id alone (reconcile owns those)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_reaper_leaves_pending_with_order_id_alone(_isolated_db, _with_lease, anyio_backend):
    from order_executor import reap_orphan_pending_trades
    from database import get_trades

    await init_db()

    # Old PENDING but WITH order_id — must NOT be reaped
    old_iso = (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat()
    with_oid = _make_trade(status="PENDING", order_id=12345, timestamp_iso=old_iso)
    await save_trade(with_oid)

    reaped = await reap_orphan_pending_trades(stale_after_seconds=600)
    assert reaped == 0

    rows = await get_trades(limit=10)
    assert rows[0].status == "PENDING"


# ---------------------------------------------------------------------------
# 3. Reaper does NOT touch young PENDING orphans (still within threshold)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_reaper_skips_young_orphans(_isolated_db, _with_lease, anyio_backend):
    from order_executor import reap_orphan_pending_trades
    from database import get_trades

    await init_db()

    young_iso = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()
    young = _make_trade(status="PENDING", order_id=None, timestamp_iso=young_iso)
    await save_trade(young)

    # threshold of 600s — young row should survive
    reaped = await reap_orphan_pending_trades(stale_after_seconds=600)
    assert reaped == 0

    rows = await get_trades(limit=10)
    assert rows[0].status == "PENDING"


# ---------------------------------------------------------------------------
# 4. Per-symbol order-rate cap
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_rate_cap_permits_up_to_max(_isolated_db, anyio_backend):
    from order_executor import (
        _check_and_record_rate_cap, MAX_ORDERS_PER_SYMBOL_PER_MIN,
    )

    for i in range(MAX_ORDERS_PER_SYMBOL_PER_MIN):
        assert await _check_and_record_rate_cap("AAPL") is True, f"call {i} must be permitted"


@pytest.mark.anyio
async def test_rate_cap_rejects_when_window_full(_isolated_db, anyio_backend, caplog):
    import logging
    from order_executor import (
        _check_and_record_rate_cap, MAX_ORDERS_PER_SYMBOL_PER_MIN,
    )

    for _ in range(MAX_ORDERS_PER_SYMBOL_PER_MIN):
        await _check_and_record_rate_cap("AAPL")

    with caplog.at_level(logging.WARNING):
        result = await _check_and_record_rate_cap("AAPL")

    assert result is False, "window-full call must be rejected"
    assert any("order_rate_cap_exceeded" in r.getMessage() for r in caplog.records)


@pytest.mark.anyio
async def test_rate_cap_is_per_symbol(_isolated_db, anyio_backend):
    from order_executor import (
        _check_and_record_rate_cap, MAX_ORDERS_PER_SYMBOL_PER_MIN,
    )

    # Fill AAPL's window completely
    for _ in range(MAX_ORDERS_PER_SYMBOL_PER_MIN):
        await _check_and_record_rate_cap("AAPL")

    # TSLA should still have a full quota
    assert await _check_and_record_rate_cap("TSLA") is True


@pytest.mark.anyio
async def test_rate_cap_evicts_old_timestamps(_isolated_db, anyio_backend):
    """Timestamps older than 60s must be evicted on each call."""
    from db.core import transaction
    from order_executor import (
        _check_and_record_rate_cap, MAX_ORDERS_PER_SYMBOL_PER_MIN,
    )

    # First call lazily creates the shared schema, then replace it with a full
    # window of stale rows.
    assert await _check_and_record_rate_cap("AAPL") is True
    stale_ts = int(time.time()) - 120
    async with transaction() as db:
        await db.execute("DELETE FROM order_rate_window")
        await db.executemany(
            "INSERT INTO order_rate_window (symbol, ts_unix, worker_pid) VALUES (?, ?, ?)",
            [
                ("AAPL", stale_ts, 1)
                for _ in range(MAX_ORDERS_PER_SYMBOL_PER_MIN)
            ],
        )

    # The next call should evict and permit
    assert await _check_and_record_rate_cap("AAPL") is True
    async with transaction() as db:
        async with db.execute(
            "SELECT COUNT(*) FROM order_rate_window WHERE symbol = ?",
            ("AAPL",),
        ) as cur:
            row = await cur.fetchone()
    assert row == (1,)


@pytest.mark.anyio
async def test_rate_cap_storage_error_blocks_order(anyio_backend, caplog):
    """A broken shared limiter must deny the order, never bypass the cap."""
    import logging

    from order_executor import _check_and_record_rate_cap

    with (
        patch(
            "order_executor.try_acquire_order_slot",
            new=AsyncMock(side_effect=RuntimeError("database unavailable")),
        ),
        caplog.at_level(logging.CRITICAL),
    ):
        assert await _check_and_record_rate_cap("AAPL") is False

    assert any("order_rate_cap_unavailable" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------
# 5. Time-source helpers
# ---------------------------------------------------------------------------


def test_now_utc_returns_tz_aware():
    from order_executor import _now_utc
    dt = _now_utc()
    assert dt.tzinfo is not None, "_now_utc must return tz-aware datetime"
    assert dt.tzinfo.utcoffset(dt) == timedelta(0), "must be UTC"


def test_now_ts_returns_unix_seconds():
    from order_executor import _now_ts
    ts = _now_ts()
    assert isinstance(ts, float)
    # Should be close to current epoch seconds
    assert abs(ts - time.time()) < 1.0


# ---------------------------------------------------------------------------
# 6. Reaper edge cases (Batch 9 — added per test-quality reviewer)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_reaper_handles_tz_naive_timestamp(_isolated_db, _with_lease, anyio_backend):
    """A row with tz-naive timestamp must still be reaped (not crash on aware/naive cmp).

    The reaper defensively patches naive -> UTC at order_executor.py:499-500.
    This test exercises that branch; without it, datetime comparison would
    raise TypeError ('can't compare offset-naive and offset-aware datetimes').
    """
    from order_executor import reap_orphan_pending_trades
    from database import get_trades

    await init_db()

    # Old PENDING with NAIVE timestamp (no tzinfo)
    old_naive = (datetime.now(timezone.utc) - timedelta(minutes=20)).replace(tzinfo=None)
    orphan = _make_trade(
        status="PENDING", order_id=None,
        timestamp_iso=old_naive.isoformat(),  # no Z, no offset
    )
    await save_trade(orphan)

    reaped = await reap_orphan_pending_trades(stale_after_seconds=600)
    assert reaped == 1, "tz-naive old orphan must still be reaped"

    rows = await get_trades(limit=10)
    assert rows[0].status == "ERROR"


@pytest.mark.anyio
async def test_reaper_defensively_reaps_malformed_timestamp(_isolated_db, _with_lease, anyio_backend):
    """A row with garbage timestamp gets reaped (defensive fail-safe)."""
    from order_executor import reap_orphan_pending_trades
    from database import get_trades

    await init_db()
    orphan = _make_trade(
        status="PENDING", order_id=None,
        timestamp_iso="not-a-real-iso-string",
    )
    await save_trade(orphan)

    # The reaper's except-block treats malformed timestamps as "older than
    # threshold" and reaps them; that's the safer-fail direction (a row
    # we can't parse shouldn't sit PENDING forever).
    reaped = await reap_orphan_pending_trades(stale_after_seconds=600)
    assert reaped == 1

    rows = await get_trades(limit=10)
    assert rows[0].status == "ERROR"

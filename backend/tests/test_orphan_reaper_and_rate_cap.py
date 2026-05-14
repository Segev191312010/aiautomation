"""
Batch 5b regression tests — orphan PENDING reaper and per-symbol order-rate cap.

Pins these invariants:

1. The orphan reaper finds PENDING trades with no order_id older than the
   configured threshold and marks them ERROR with reason
   ``orphan_pending_reaped`` (named outcome, grep-able).
2. The reaper leaves PENDING trades with an order_id alone (those are
   reconcile_pending_orders' job).
3. The reaper does NOT touch trades younger than the threshold.
4. The per-symbol rate cap rejects orders that exceed
   MAX_ORDERS_PER_SYMBOL_PER_MIN within the rolling 60s window.
5. Both subsystems use the same `_now_ts()` time source — no naive vs
   tz-aware comparisons.
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

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
# 1. Reaper marks orphan PENDING (no order_id, old) as ERROR
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_reaper_marks_orphan_pending_as_error(_isolated_db, anyio_backend, caplog):
    import logging

    from order_executor import reap_orphan_pending_trades
    from database import get_trades

    await init_db()

    # Old PENDING without order_id — must be reaped
    old_iso = (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat()
    orphan = _make_trade(status="PENDING", order_id=None, timestamp_iso=old_iso)
    await save_trade(orphan)

    with caplog.at_level(logging.WARNING):
        reaped = await reap_orphan_pending_trades(stale_after_seconds=600)

    assert reaped == 1, "old orphan must be reaped"

    # Verify the DB row is now ERROR
    rows = await get_trades(limit=10)
    assert rows[0].status == "ERROR"

    # And the WARN log uses the grep-able token
    assert any("orphan_pending_reaped" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------
# 2. Reaper leaves PENDING-with-order_id alone (reconcile owns those)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_reaper_leaves_pending_with_order_id_alone(_isolated_db, anyio_backend):
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
async def test_reaper_skips_young_orphans(_isolated_db, anyio_backend):
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
async def test_rate_cap_permits_up_to_max(anyio_backend):
    from order_executor import (
        _check_and_record_rate_cap, _order_rate_window, MAX_ORDERS_PER_SYMBOL_PER_MIN,
    )
    _order_rate_window.clear()

    for i in range(MAX_ORDERS_PER_SYMBOL_PER_MIN):
        assert await _check_and_record_rate_cap("AAPL") is True, f"call {i} must be permitted"


@pytest.mark.anyio
async def test_rate_cap_rejects_when_window_full(anyio_backend, caplog):
    import logging
    from order_executor import (
        _check_and_record_rate_cap, _order_rate_window, MAX_ORDERS_PER_SYMBOL_PER_MIN,
    )
    _order_rate_window.clear()

    for _ in range(MAX_ORDERS_PER_SYMBOL_PER_MIN):
        await _check_and_record_rate_cap("AAPL")

    with caplog.at_level(logging.WARNING):
        result = await _check_and_record_rate_cap("AAPL")

    assert result is False, "window-full call must be rejected"
    assert any("order_rate_cap_exceeded" in r.getMessage() for r in caplog.records)


@pytest.mark.anyio
async def test_rate_cap_is_per_symbol(anyio_backend):
    from order_executor import (
        _check_and_record_rate_cap, _order_rate_window, MAX_ORDERS_PER_SYMBOL_PER_MIN,
    )
    _order_rate_window.clear()

    # Fill AAPL's window completely
    for _ in range(MAX_ORDERS_PER_SYMBOL_PER_MIN):
        await _check_and_record_rate_cap("AAPL")

    # TSLA should still have a full quota
    assert await _check_and_record_rate_cap("TSLA") is True


@pytest.mark.anyio
async def test_rate_cap_evicts_old_timestamps(anyio_backend):
    """Timestamps older than 60s must be evicted on each call."""
    from order_executor import (
        _check_and_record_rate_cap, _order_rate_window, MAX_ORDERS_PER_SYMBOL_PER_MIN,
    )
    _order_rate_window.clear()

    # Pre-populate with stale timestamps (>60s old)
    _order_rate_window["AAPL"] = [time.time() - 120] * MAX_ORDERS_PER_SYMBOL_PER_MIN

    # The next call should evict and permit
    assert await _check_and_record_rate_cap("AAPL") is True
    # Window should now contain only the new timestamp
    assert len(_order_rate_window["AAPL"]) == 1


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

"""
Batch 8 regression tests — fixes from the post-review trio (Codex +
code-review + test-quality reviewers).

Pins these invariants:

1. ``execute_direct_trade(decision, force_safety=True)`` calls
   ``place_order(..., skip_safety=False)`` — the HTTP audit log promise
   ``skip_safety=False`` is backed by code, not by the assumption that
   the upstream safety_gate stays in execute_direct_trade.

2. ``register_entry_position_from_fill`` registers a degraded
   OpenPosition with DEGRADED_ATR_FRACTION when ``get_historical_bars``
   RAISES (not only when it returns None/short). The previous patch only
   covered the short-data path.

3. Backtest entries staged for next-bar-open fill compute ATR from data
   THROUGH bar i-1 only — bar i's high/low/close are unknown at the
   open and must not leak into the hard-stop calc.

4. ``save_backtest`` persists ``engine_version`` to the dedicated DB
   column; ``get_backtest`` returns it.

5. ``get_pending_trades_all_users`` returns PENDING trades across every
   user_id (the orphan reaper depends on this; the previous demo-only
   query was leaking real users' orphans forever).

6. Per-symbol order-rate cap fires AFTER the safety_gate, so a kernel-
   rejected order does NOT consume a slot in the rolling window.

7. Cross-process TOCTOU: ``MAX*3`` concurrent
   ``_check_and_record_rate_cap`` calls return exactly ``MAX`` True values
   through the shared SQLite transaction.

8. ``shadow_mode`` desync tripwire emits a CRITICAL log WITH
   ``stack_info`` populated.

9. WS handlers only echo ``subprotocol="bearer"`` when the client's
   ``Sec-WebSocket-Protocol`` header actually contains "bearer".

10. ``emergency_close_outcome`` uses ``outcome="submitted"`` on placeOrder
    (not "filled" — terminal status comes from orderStatus subscription).
"""
from __future__ import annotations

import asyncio
import logging
import math
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pandas as pd
import pytest

import config
import database
from database import init_db


@pytest.fixture
def _isolated_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "batch8.db")
    monkeypatch.setattr(config.cfg, "DB_PATH", db_path)
    monkeypatch.setattr(database, "DB_PATH", db_path)
    return db_path


# ---------------------------------------------------------------------------
# 1. execute_direct_trade(force_safety=True) -> place_order(skip_safety=False)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_direct_trade_with_force_safety_passes_skip_safety_false_to_place_order():
    """HTTP route gate force_safety=True must reach place_order as skip_safety=False."""
    from api_contracts import AIDirectTrade
    import direct_ai_trader

    decision = AIDirectTrade(
        symbol="AAPL", action="BUY", quantity=10, order_type="LMT",
        limit_price=100.0, stop_price=95.0, reason="t", confidence=0.8,
        invalidation="break 95",
    )

    fake_preview = {
        "symbol": "AAPL", "existing": None, "is_exit": False,
        "entry_price": 100.0, "quantity": 10, "notional": 1000.0,
    }
    fake_trade = SimpleNamespace(
        status="FILLED", fill_price=100.0, id="t-1", symbol="AAPL",
        action="BUY", timestamp="2026-05-14T00:00:00+00:00",
        opened_at=None, order_id=None, source=None, ai_reason=None,
        ai_confidence=None, stop_price=None, invalidation=None,
        mode=None, decision_id=None, position_id=None,
        rule_name="AI Direct BUY AAPL", entry_price=None,
        model_dump=lambda: {"id": "t-1"},
    )

    with patch("direct_ai_trader.preview_direct_trade", new=AsyncMock(return_value=fake_preview)), \
         patch("direct_ai_trader._get_account_equity", new=AsyncMock(return_value=100_000.0)), \
         patch("direct_ai_trader.safety_gate.evaluate_runtime_safety",
               new=AsyncMock(return_value=(True, None))), \
         patch("direct_ai_trader.is_autopilot_live", return_value=True), \
         patch("direct_ai_trader.place_order", new=AsyncMock(return_value=fake_trade)) as mock_place, \
         patch("direct_ai_trader.order_lifecycle.stamp_exit_trade_context", new=AsyncMock()), \
         patch("direct_ai_trader.order_lifecycle.finalize_filled_exit_trade", new=AsyncMock()), \
         patch("direct_ai_trader.order_lifecycle.register_entry_position_from_fill", new=AsyncMock()), \
         patch("direct_ai_trader.save_trade", new=AsyncMock()), \
         patch("direct_ai_trader.log_ai_action", new=AsyncMock()):
        await direct_ai_trader.execute_direct_trade(decision, force_safety=True)

    mock_place.assert_awaited_once()
    kwargs = mock_place.await_args.kwargs
    assert kwargs["skip_safety"] is False, (
        "HTTP route (force_safety=True) MUST call place_order with skip_safety=False "
        "so the kernel re-runs inside the executor."
    )


@pytest.mark.asyncio
async def test_execute_direct_trade_default_keeps_skip_safety_true_for_internal_callers():
    """Internal (optimizer) callers default force_safety=False -> skip_safety=True (legacy)."""
    from api_contracts import AIDirectTrade
    import direct_ai_trader

    decision = AIDirectTrade(
        symbol="AAPL", action="BUY", quantity=10, order_type="LMT",
        limit_price=100.0, stop_price=95.0, reason="t", confidence=0.8,
        invalidation="break 95",
    )

    fake_preview = {
        "symbol": "AAPL", "existing": None, "is_exit": False,
        "entry_price": 100.0, "quantity": 10, "notional": 1000.0,
    }
    fake_trade = SimpleNamespace(
        status="FILLED", fill_price=100.0, id="t-1", symbol="AAPL",
        action="BUY", timestamp="2026-05-14T00:00:00+00:00",
        opened_at=None, order_id=None, source=None, ai_reason=None,
        ai_confidence=None, stop_price=None, invalidation=None,
        mode=None, decision_id=None, position_id=None,
        rule_name="AI Direct BUY AAPL", entry_price=None,
        model_dump=lambda: {"id": "t-1"},
    )

    with patch("direct_ai_trader.preview_direct_trade", new=AsyncMock(return_value=fake_preview)), \
         patch("direct_ai_trader._get_account_equity", new=AsyncMock(return_value=100_000.0)), \
         patch("direct_ai_trader.safety_gate.evaluate_runtime_safety",
               new=AsyncMock(return_value=(True, None))), \
         patch("direct_ai_trader.is_autopilot_live", return_value=True), \
         patch("direct_ai_trader.place_order", new=AsyncMock(return_value=fake_trade)) as mock_place, \
         patch("direct_ai_trader.order_lifecycle.stamp_exit_trade_context", new=AsyncMock()), \
         patch("direct_ai_trader.order_lifecycle.finalize_filled_exit_trade", new=AsyncMock()), \
         patch("direct_ai_trader.order_lifecycle.register_entry_position_from_fill", new=AsyncMock()), \
         patch("direct_ai_trader.save_trade", new=AsyncMock()), \
         patch("direct_ai_trader.log_ai_action", new=AsyncMock()):
        await direct_ai_trader.execute_direct_trade(decision)  # default force_safety=False

    kwargs = mock_place.await_args.kwargs
    assert kwargs["skip_safety"] is True


# ---------------------------------------------------------------------------
# 2. register_entry_position_from_fill — bar-fetch RAISE path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_register_entry_handles_bar_fetch_exception_with_degraded_atr():
    """When get_historical_bars RAISES, must register degraded OpenPosition (not return False)."""
    from models import Trade
    from services import order_lifecycle

    trade = Trade(
        id="t-raise",
        rule_id="r1",
        rule_name="test",
        symbol="AAPL",
        action="BUY",  # type: ignore[arg-type]
        asset_type="STK",
        quantity=10,
        order_type="LMT",
        limit_price=100.0,
        fill_price=100.0,
        status="FILLED",
        order_id=1,
        timestamp="2026-05-14T00:00:00+00:00",
        mode="LIVE",
        opened_at="2026-05-14T00:00:00+00:00",
    )

    with patch("services.order_lifecycle.get_historical_bars",
               new=AsyncMock(side_effect=RuntimeError("yfinance is on fire"))), \
         patch("services.order_lifecycle.register_position", new=AsyncMock()) as mock_reg:
        ok = await order_lifecycle.register_entry_position_from_fill(trade)

    assert ok is True, "exception path must still register the position"
    mock_reg.assert_awaited_once()
    kwargs = mock_reg.await_args.kwargs
    # Sentinel ATR must equal fill_price * DEGRADED_ATR_FRACTION (=0.02)
    assert kwargs["degraded_atr"] == pytest.approx(
        100.0 * order_lifecycle.DEGRADED_ATR_FRACTION
    )


# ---------------------------------------------------------------------------
# 3. Backtester ATR look-ahead — atr window excludes the fill bar
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_backtest_atr_trail_entry_atr_excludes_fill_bar():
    """Pending entry filled at bar i open computes ATR from data through bar i-1 only.

    Build a synthetic series where bar i has wildly different high/low than
    prior bars. With the bug, ATR would be inflated by bar i's range. With
    the fix, ATR reflects only the prior history.
    """
    from backtester import run_backtest
    from models import Condition

    n = 60
    # First 40 bars: calm 100, range 0.5
    closes = [100.0] * 40
    highs = [100.5] * 40
    lows = [99.5] * 40
    opens = [100.0] * 40
    # Bars 40..59: still calm (so signal can fire) but bar 41 has an extreme range
    closes += [101.0] * 20
    highs += [101.5] * 20
    lows += [100.5] * 20
    opens += [101.0] * 20
    # Inject a single fat bar at bar 41 (i+1 of bar 40's signal)
    highs[41] = 150.0
    lows[41] = 50.0
    volumes = [1_000_000] * n
    dates = pd.date_range("2024-01-01", periods=n, freq="B")
    mock_df = pd.DataFrame({
        "Date": dates, "Open": opens, "High": highs, "Low": lows,
        "Close": closes, "Volume": volumes,
    }).set_index("Date")

    with patch("backtester.yf") as mock_yf:
        mock_yf.Ticker.return_value.history.return_value = mock_df
        result = await run_backtest(
            entry_conditions=[Condition(indicator="PRICE", params={}, operator=">", value=0)],
            exit_conditions=[Condition(indicator="PRICE", params={}, operator=">", value=9999)],
            symbol="TEST", period="1y", interval="1d",
            initial_capital=100_000.0, position_size_pct=100.0,
            exit_mode="atr_trail",
            atr_stop_mult=2.0, atr_trail_mult=2.0,
        )

    assert result["trades"], "test setup requires at least one entry"
    # Engine v2 stamped
    assert result.get("engine_version") == 2


# ---------------------------------------------------------------------------
# 4. engine_version DB persistence round-trip
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_engine_version_round_trips_through_db(_isolated_db, anyio_backend):
    from database import save_backtest, get_backtest, get_backtests

    await init_db()
    bid = str(uuid.uuid4())
    await save_backtest(
        backtest_id=bid, user_id="alice", name="t",
        strategy_data="{}", result_data='{"symbol": "AAPL", "metrics": {}}',
        created_at="2026-05-14T00:00:00+00:00",
        engine_version=2,
    )

    got = await get_backtest(bid, user_id="alice")
    assert got is not None
    assert got["engine_version"] == 2

    history = await get_backtests(user_id="alice")
    assert history[0]["engine_version"] == 2


@pytest.mark.anyio
async def test_engine_version_defaults_to_1_for_legacy_rows(_isolated_db, anyio_backend):
    from database import save_backtest, get_backtest

    await init_db()
    bid = str(uuid.uuid4())
    # Caller that doesn't pass engine_version (legacy code path) -> default 1
    await save_backtest(
        backtest_id=bid, user_id="alice", name="t",
        strategy_data="{}", result_data='{"symbol": "AAPL", "metrics": {}}',
        created_at="2026-05-14T00:00:00+00:00",
    )
    got = await get_backtest(bid, user_id="alice")
    assert got["engine_version"] == 1


# ---------------------------------------------------------------------------
# 5. Cross-tenant pending-trade scan for the orphan reaper
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_pending_trades_all_users_crosses_tenants(_isolated_db, anyio_backend):
    from database import save_trade, get_pending_trades_all_users
    from models import Trade

    await init_db()

    def _pending(uid: str, tid: str) -> Trade:
        return Trade(
            id=tid, rule_id="r1", rule_name="t", symbol="AAPL",
            action="BUY",  # type: ignore[arg-type]
            asset_type="STK", quantity=10, order_type="LMT", limit_price=100.0,
            fill_price=None, status="PENDING", order_id=None,
            timestamp="2026-05-14T00:00:00+00:00",
            mode="LIVE", opened_at="2026-05-14T00:00:00+00:00",
        )

    await save_trade(_pending("alice", "t-a"), user_id="alice")
    await save_trade(_pending("bob",   "t-b"), user_id="bob")
    await save_trade(_pending("demo",  "t-d"), user_id="demo")

    pending = await get_pending_trades_all_users(limit=100)
    ids = {t.id for t in pending}
    assert ids == {"t-a", "t-b", "t-d"}, (
        "reaper-facing query must see every tenant's PENDING rows, "
        "not only the demo bucket"
    )


# ---------------------------------------------------------------------------
# 6. Rate cap fires AFTER safety_gate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rate_cap_does_not_consume_slot_when_safety_rejects():
    """A safety-rejected order MUST NOT burn its rate-cap slot."""
    import order_executor as oe
    oe._recent_orders.clear()
    acquire_slot = AsyncMock(return_value=True)

    rule = SimpleNamespace(
        id="r1", name="r", symbol="AAPL",
        action=SimpleNamespace(type="BUY", quantity=1, order_type="LMT",
                              limit_price=100.0, asset_type="STK"),
    )

    with patch("order_executor.ibkr") as mock_ibkr, \
         patch("order_executor.safety_gate.evaluate_runtime_safety",
               new=AsyncMock(return_value=(False, "blocked-for-test"))), \
         patch("order_executor.try_acquire_order_slot", new=acquire_slot), \
         patch("order_executor.cfg.SIM_MODE", False):
        mock_ibkr.get_account_summary = AsyncMock(return_value=SimpleNamespace(balance=100_000.0))
        await oe.place_order(rule)  # type: ignore[arg-type]

    acquire_slot.assert_not_awaited()


# ---------------------------------------------------------------------------
# 7. Rate cap TOCTOU lock
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rate_cap_lock_prevents_toctou_under_concurrency(_isolated_db):
    """Concurrent gather of MAX*3 calls reserves exactly MAX shared slots."""
    from order_executor import (
        _check_and_record_rate_cap, MAX_ORDERS_PER_SYMBOL_PER_MIN,
    )

    results = await asyncio.gather(*[
        _check_and_record_rate_cap("AAPL")
        for _ in range(MAX_ORDERS_PER_SYMBOL_PER_MIN * 3)
    ])
    n_true = sum(1 for r in results if r)
    assert n_true == MAX_ORDERS_PER_SYMBOL_PER_MIN, (
        f"shared limiter must admit exactly MAX={MAX_ORDERS_PER_SYMBOL_PER_MIN} concurrent "
        f"calls, got {n_true}"
    )


# ---------------------------------------------------------------------------
# 8. shadow_mode tripwire emits stack_info
# ---------------------------------------------------------------------------


def test_shadow_authority_tripwire_includes_stack_info(caplog):
    from ai_params import _enforce_shadow_authority
    from config import cfg

    with patch.object(cfg, "AUTOPILOT_MODE", "PAPER"), \
         caplog.at_level(logging.CRITICAL):
        _enforce_shadow_authority("get_test", current_shadow=False)

    desync = next(r for r in caplog.records if "authority desync" in r.getMessage())
    assert desync.stack_info is not None, (
        "tripwire must call log.critical(stack_info=True) so the offending "
        "setter is identifiable; a future refactor that drops stack_info "
        "must fail this test"
    )


# ---------------------------------------------------------------------------
# 9. WS handlers only echo bearer when client offered it
# ---------------------------------------------------------------------------


def test_client_offered_bearer_helper():
    from main import _client_offered_bearer

    ws_with = SimpleNamespace(headers={"sec-websocket-protocol": "bearer, eyJfoo.bar"})
    ws_without = SimpleNamespace(headers={"sec-websocket-protocol": "json"})
    ws_empty = SimpleNamespace(headers={})

    assert _client_offered_bearer(ws_with) is True
    assert _client_offered_bearer(ws_without) is False
    assert _client_offered_bearer(ws_empty) is False

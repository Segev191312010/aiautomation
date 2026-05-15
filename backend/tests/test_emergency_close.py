"""
Batch 2 regression tests — emergency-close hardening in safety_kernel.

Pins three invariants:

1. Marketable LimitOrder is used when finite bid/ask/last quotes are
   available, NOT a naked MarketOrder + outsideRth=True.
2. MKT fallback fires ONLY when (a) no finite quote AND (b) inside RTH.
   Outside RTH with no finite quote we record `emergency_close_no_price`
   and SKIP the position (no garbage-quote MKT fills).
3. Every position emits a structured `emergency_close_outcome` audit
   log with the documented schema: position_id, symbol, qty,
   close_action, outcome, reason, latency_ms, ts, source.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import safety_kernel
from models import OpenPosition


def _make_open_position(symbol: str, side: str, qty: float) -> OpenPosition:
    return OpenPosition(
        id=f"pos-{symbol}",
        symbol=symbol,
        side=side,  # type: ignore[arg-type]
        quantity=qty,
        entry_price=100.0,
        entry_time="2026-05-14T00:00:00+00:00",
        atr_at_entry=2.0,
        hard_stop_price=96.0,
        atr_stop_mult=2.0,
        atr_trail_mult=2.0,
        high_watermark=100.0,
        rule_id="r1",
        rule_name="t",
        user_id="demo",
    )


def _fake_ibkr(bid=99.5, ask=100.5, last=100.0):
    """Build a fake IBKR client with a configurable ticker."""
    ticker = SimpleNamespace(bid=bid, ask=ask, last=last, close=last)
    mock_ib = MagicMock()
    mock_ib.qualifyContractsAsync = AsyncMock()
    mock_ib.reqTickersAsync = AsyncMock(return_value=[ticker])
    mock_ib.placeOrder = MagicMock()
    return SimpleNamespace(
        ib=mock_ib,
        is_connected=lambda: True,
        make_stock_contract=lambda sym: SimpleNamespace(symbol=sym),
    ), mock_ib


# ---------------------------------------------------------------------------
# 1. Marketable LIMIT when finite quotes available
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_emergency_close_uses_marketable_limit_when_quotes_finite(caplog):
    """Long position with finite bid/ask should fire a LIMIT order, not a MKT."""
    fake_ibkr_obj, mock_ib = _fake_ibkr(bid=99.5, ask=100.5, last=100.0)
    long_pos = _make_open_position("AAPL", "BUY", 10.0)

    with patch("config.cfg.SIM_MODE", False), \
         patch("ibkr_client.ibkr", fake_ibkr_obj), \
         patch("database.get_open_positions", new=AsyncMock(return_value=[long_pos])), \
         caplog.at_level(logging.CRITICAL):
        await safety_kernel._emergency_close_all_positions("test")

    # One placeOrder call, with a LimitOrder
    assert mock_ib.placeOrder.call_count == 1
    contract_arg, order_arg = mock_ib.placeOrder.call_args.args
    assert order_arg.__class__.__name__ == "LimitOrder"
    assert order_arg.action == "SELL"  # closing a BUY (long)
    # 99.5 * 0.98 = 97.51 (cross the spread aggressively to sell)
    assert order_arg.lmtPrice == pytest.approx(97.51, abs=0.01)
    # Structured outcome log emitted with the SUBMITTED outcome (terminal
    # state requires an orderStatus subscription — Batch 8 corrects the
    # earlier "filled-on-submit" lie).
    outcome_logs = [r for r in caplog.records if "emergency_close_outcome" in r.message]
    assert outcome_logs, "must emit at least one emergency_close_outcome log"
    assert any("'submitted'" in r.getMessage() for r in outcome_logs), \
        "submit-only path must use outcome='submitted', not 'filled'"


# ---------------------------------------------------------------------------
# 2. MKT only inside RTH with no finite quote
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_emergency_close_skips_when_no_quote_outside_rth(caplog):
    """No finite quote + outside RTH must SKIP the position (no MKT fallback)."""
    fake_ibkr_obj, mock_ib = _fake_ibkr(bid=float("nan"), ask=float("nan"), last=float("nan"))
    long_pos = _make_open_position("AAPL", "BUY", 10.0)

    with patch("config.cfg.SIM_MODE", False), \
         patch("ibkr_client.ibkr", fake_ibkr_obj), \
         patch("database.get_open_positions", new=AsyncMock(return_value=[long_pos])), \
         patch("safety_kernel._is_regular_trading_hours", return_value=False), \
         caplog.at_level(logging.CRITICAL):
        await safety_kernel._emergency_close_all_positions("test")

    assert mock_ib.placeOrder.call_count == 0, "must NOT fire MKT after-hours on garbage quotes"
    # Must log emergency_close_no_price
    no_price_logs = [r for r in caplog.records if "emergency_close_no_price" in r.message]
    assert no_price_logs, "must log emergency_close_no_price reason"
    # Outcome payload must have outcome=skipped_no_price
    outcome_logs = [r for r in caplog.records if "emergency_close_outcome" in r.message]
    assert any("skipped_no_price" in r.message for r in outcome_logs)


@pytest.mark.asyncio
async def test_emergency_close_mkt_fallback_inside_rth(caplog):
    """No finite quote + inside RTH = MKT is the last resort."""
    fake_ibkr_obj, mock_ib = _fake_ibkr(bid=float("nan"), ask=float("nan"), last=float("nan"))
    long_pos = _make_open_position("AAPL", "BUY", 10.0)

    with patch("config.cfg.SIM_MODE", False), \
         patch("ibkr_client.ibkr", fake_ibkr_obj), \
         patch("database.get_open_positions", new=AsyncMock(return_value=[long_pos])), \
         patch("safety_kernel._is_regular_trading_hours", return_value=True), \
         caplog.at_level(logging.CRITICAL):
        await safety_kernel._emergency_close_all_positions("test")

    assert mock_ib.placeOrder.call_count == 1, "must fire MKT fallback inside RTH"
    _, order_arg = mock_ib.placeOrder.call_args.args
    assert order_arg.__class__.__name__ == "MarketOrder"
    assert order_arg.outsideRth is False, "RTH MKT fallback must set outsideRth=False"


# ---------------------------------------------------------------------------
# 3. emergency_close_outcome audit schema is locked
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_emergency_close_outcome_schema_contains_required_keys(caplog):
    """The audit log payload schema is contract-locked."""
    fake_ibkr_obj, _mock_ib = _fake_ibkr(bid=99.5, ask=100.5, last=100.0)
    long_pos = _make_open_position("AAPL", "BUY", 10.0)

    with patch("config.cfg.SIM_MODE", False), \
         patch("ibkr_client.ibkr", fake_ibkr_obj), \
         patch("database.get_open_positions", new=AsyncMock(return_value=[long_pos])), \
         caplog.at_level(logging.CRITICAL):
        await safety_kernel._emergency_close_all_positions("circuit_breaker")

    # The message format is `"emergency_close_outcome %s" % outcome_payload`,
    # so the payload dict appears as the Python repr in the formatted message.
    outcome_msgs = [r.getMessage() for r in caplog.records
                    if "emergency_close_outcome" in r.getMessage()]
    assert outcome_msgs, "must emit at least one outcome log"
    msg = outcome_msgs[0]

    required_keys = [
        "'type'", "'position_id'", "'symbol'", "'qty'",
        "'close_action'", "'outcome'", "'reason'",
        "'latency_ms'", "'ts'", "'source'",
    ]
    for key in required_keys:
        assert key in msg, f"emergency_close_outcome schema missing key {key} in: {msg}"


# ---------------------------------------------------------------------------
# 4. Side derivation for short positions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_emergency_close_short_position_uses_buy_action():
    """A SELL/short position must be closed with a BUY action."""
    fake_ibkr_obj, mock_ib = _fake_ibkr(bid=99.5, ask=100.5, last=100.0)
    short_pos = _make_open_position("TSLA", "SELL", 5.0)

    with patch("config.cfg.SIM_MODE", False), \
         patch("ibkr_client.ibkr", fake_ibkr_obj), \
         patch("database.get_open_positions", new=AsyncMock(return_value=[short_pos])):
        await safety_kernel._emergency_close_all_positions("test")

    assert mock_ib.placeOrder.call_count == 1
    _, order_arg = mock_ib.placeOrder.call_args.args
    assert order_arg.action == "BUY", "short position must close with BUY"


# ---------------------------------------------------------------------------
# 5. RTH helper
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("hour,minute,weekday,expected", [
    # EDT season (May 2026 — UTC-4). Open bell 09:30 ET = 13:30 UTC.
    (14, 0, 1, True),    # Tuesday 14:00 UTC = 10:00 EDT = inside RTH
    (13, 30, 2, True),   # Wednesday 13:30 UTC = 09:30 EDT = open bell
    (20, 0, 3, True),    # Thursday 20:00 UTC = 16:00 EDT = close bell
    (12, 0, 1, False),   # Tuesday 12:00 UTC = pre-market EDT
    (21, 0, 1, False),   # Tuesday 21:00 UTC = after-hours EDT
    (14, 0, 5, False),   # Saturday = weekend
    (14, 0, 6, False),   # Sunday = weekend
])
def test_is_regular_trading_hours_edt(hour, minute, weekday, expected):
    """During EDT (UTC-4), 09:30-16:00 ET maps to 13:30-20:00 UTC."""
    base = datetime(2026, 5, 12, hour, minute, tzinfo=timezone.utc)
    days_to_add = (weekday - base.weekday()) % 7
    test_dt = base + timedelta(days=days_to_add)
    assert safety_kernel._is_regular_trading_hours(test_dt) is expected


# ---------------------------------------------------------------------------
# 6. Terminal outcome via orderStatus subscription (Batch 9)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_emergency_close_emits_terminal_outcome_on_fill(caplog):
    """After placeOrder, the statusEvent handler must emit a SECOND outcome
    log with outcome='filled' when ib_trade.orderStatus reaches 'Filled'.
    """
    # Build a fake ib_trade with an event-like statusEvent (supports += and
    # callable subscribers). Use a simple list-backed shim.

    class _FakeEvent:
        def __init__(self):
            self._subs = []

        def __iadd__(self, handler):
            self._subs.append(handler)
            return self

        def fire(self, payload):
            for h in self._subs:
                h(payload)

    fake_event = _FakeEvent()
    fake_ib_trade = SimpleNamespace(
        statusEvent=fake_event,
        orderStatus=SimpleNamespace(status="Submitted", avgFillPrice=0.0),
    )

    fake_ibkr_obj, mock_ib = _fake_ibkr(bid=99.5, ask=100.5, last=100.0)
    mock_ib.placeOrder = MagicMock(return_value=fake_ib_trade)

    long_pos = _make_open_position("AAPL", "BUY", 10.0)

    with patch("config.cfg.SIM_MODE", False), \
         patch("ibkr_client.ibkr", fake_ibkr_obj), \
         patch("database.get_open_positions", new=AsyncMock(return_value=[long_pos])), \
         caplog.at_level(logging.CRITICAL):
        await safety_kernel._emergency_close_all_positions("test")

        # Now simulate the broker reporting a Filled status
        fake_ib_trade.orderStatus.status = "Filled"
        fake_ib_trade.orderStatus.avgFillPrice = 99.55
        fake_event.fire(fake_ib_trade)

    outcome_logs = [r.getMessage() for r in caplog.records
                    if "emergency_close_outcome" in r.getMessage()]
    # Must see at least two: the submitted log + the terminal-filled log
    assert any("'submitted'" in m for m in outcome_logs), \
        "must emit submitted outcome on placeOrder"
    assert any("'filled'" in m for m in outcome_logs), \
        "must emit terminal filled outcome after statusEvent reports Filled"


@pytest.mark.asyncio
async def test_emergency_close_emits_terminal_outcome_on_cancel(caplog):
    """A Cancelled status must map to terminal outcome='rejected'."""
    class _FakeEvent:
        def __init__(self):
            self._subs = []
        def __iadd__(self, h):
            self._subs.append(h)
            return self
        def fire(self, payload):
            for h in self._subs:
                h(payload)

    fake_event = _FakeEvent()
    fake_ib_trade = SimpleNamespace(
        statusEvent=fake_event,
        orderStatus=SimpleNamespace(status="Submitted", avgFillPrice=0.0),
    )
    fake_ibkr_obj, mock_ib = _fake_ibkr(bid=99.5, ask=100.5, last=100.0)
    mock_ib.placeOrder = MagicMock(return_value=fake_ib_trade)
    long_pos = _make_open_position("AAPL", "BUY", 10.0)

    with patch("config.cfg.SIM_MODE", False), \
         patch("ibkr_client.ibkr", fake_ibkr_obj), \
         patch("database.get_open_positions", new=AsyncMock(return_value=[long_pos])), \
         caplog.at_level(logging.CRITICAL):
        await safety_kernel._emergency_close_all_positions("test")
        fake_ib_trade.orderStatus.status = "Cancelled"
        fake_event.fire(fake_ib_trade)

    outcome_logs = [r.getMessage() for r in caplog.records
                    if "emergency_close_outcome" in r.getMessage()]
    assert any("'rejected'" in m for m in outcome_logs), \
        "Cancelled must map to terminal outcome='rejected'"


@pytest.mark.parametrize("hour,minute,expected", [
    # EST season (Jan 2026 — UTC-5). Open bell 09:30 ET = 14:30 UTC.
    # Batch 9 fix: the old static-UTC window returned True for 13:30 UTC
    # in January (it's actually 08:30 EST — pre-market), and False for
    # 20:30 UTC (which is 15:30 EST — inside RTH).
    (14, 30, True),    # 14:30 UTC = 09:30 EST = open bell
    (21, 0,  True),    # 21:00 UTC = 16:00 EST = close bell
    (13, 30, False),   # 13:30 UTC = 08:30 EST = pre-market (was BUG=True)
    (22, 0,  False),   # 22:00 UTC = 17:00 EST = after-hours
    (20, 30, True),    # 20:30 UTC = 15:30 EST = inside RTH (was BUG=False)
])
def test_is_regular_trading_hours_est_winter_dst_off(hour, minute, expected):
    """During EST (UTC-5, Nov-Mar), 09:30-16:00 ET maps to 14:30-21:00 UTC.

    Pins the DST fix: the old static-UTC window 13:30-20:00 was wrong for
    half the year. Calling with a January datetime exercises EST, where
    the previous code would have returned True for 13:30 UTC (pre-market)
    and False for 20:30 UTC (mid-session).
    """
    # 2026-01-13 is a Tuesday — definitely EST.
    test_dt = datetime(2026, 1, 13, hour, minute, tzinfo=timezone.utc)
    assert safety_kernel._is_regular_trading_hours(test_dt) is expected

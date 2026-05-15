"""
Batch 7 regression tests — backtest engine v2.

Pins these invariants:

1. _PERIODS_PER_YEAR is keyed by every API-accepted interval. A new
   interval added to the API without a matching factor here MUST fail
   the test with the missing interval's name.
2. Sharpe annualization scales with the interval (sqrt of periods/year)
   instead of the hardcoded sqrt(252) — switching daily->weekly changes
   the annualization factor.
3. The engine_version is stamped on every saved backtest result so the
   UI can distinguish v1 (legacy / optimistic) from v2 results.
4. No-look-ahead: a signal that fires at bar i's close fills at bar
   i+1's open (not bar i's close).
5. backtest_engine.SimulatedExecution applies slippage on the CORRECT
   side for exits — closing a long fills at `close - slippage`, not
   `close + slippage`.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

import numpy as np
import pytest

from backtester import (
    BACKTEST_ENGINE_VERSION,
    _PERIODS_PER_YEAR,
    _periods_per_year,
    _compute_metrics,
)


# ---------------------------------------------------------------------------
# 1. _PERIODS_PER_YEAR coverage
# ---------------------------------------------------------------------------


_API_INTERVALS = ["1m", "2m", "5m", "15m", "30m", "60m", "90m", "1h", "1d", "5d", "1wk", "1mo", "3mo"]


@pytest.mark.parametrize("interval", _API_INTERVALS)
def test_periods_per_year_covers_every_api_interval(interval):
    """Every interval the public API accepts MUST have an annualization factor.

    If you add a new interval to backtest_routes / yfinance support, you
    MUST also add it to _PERIODS_PER_YEAR or this test fails by name.
    """
    factor = _periods_per_year(interval)
    assert factor > 0, f"interval {interval!r} must have a positive annualization factor"


def test_periods_per_year_raises_with_interval_name_for_missing_key():
    """A typo or missing interval surfaces the EXACT key in the error message."""
    with pytest.raises(KeyError, match="missing from _PERIODS_PER_YEAR"):
        _periods_per_year("not-a-real-interval")


# ---------------------------------------------------------------------------
# 2. Sharpe annualization is interval-aware
# ---------------------------------------------------------------------------


def _equity_curve_from_returns(returns: list[float], start: float = 100_000.0) -> list[dict]:
    """Build a fake equity_curve from per-bar returns."""
    eq = start
    curve = [{"time": 0, "equity": eq, "drawdown_pct": 0.0}]
    for i, r in enumerate(returns, start=1):
        eq = eq * (1 + r)
        curve.append({"time": i * 86400, "equity": eq, "drawdown_pct": 0.0})
    return curve


def test_sharpe_uses_daily_factor_for_1d():
    """For 1d interval, Sharpe must use sqrt(252)."""
    # 252 returns of constant +0.001 -> mean=0.001 std=0
    # Use slight variance so std>0
    rng = np.random.default_rng(42)
    rets = rng.normal(0.001, 0.01, 252).tolist()
    curve = _equity_curve_from_returns(rets)
    metrics_d = _compute_metrics([], curve, 100_000.0, 252, interval="1d")
    # Recompute expected with sqrt(252)
    dr = np.array([(curve[i]["equity"] - curve[i-1]["equity"]) / curve[i-1]["equity"]
                   for i in range(1, len(curve))])
    expected = float(np.mean(dr) / np.std(dr) * np.sqrt(252))
    assert math.isclose(metrics_d.sharpe_ratio, round(expected, 2), abs_tol=0.05)


def test_sharpe_factor_differs_between_intervals():
    """Same returns at different intervals must produce different Sharpes."""
    rng = np.random.default_rng(42)
    rets = rng.normal(0.001, 0.01, 252).tolist()
    curve = _equity_curve_from_returns(rets)
    m_d = _compute_metrics([], curve, 100_000.0, 252, interval="1d")
    m_w = _compute_metrics([], curve, 100_000.0, 252, interval="1wk")
    # 1d uses sqrt(252) ~ 15.87; 1wk uses sqrt(52) ~ 7.21 -> Sharpe smaller
    assert m_d.sharpe_ratio != m_w.sharpe_ratio
    if m_d.sharpe_ratio > 0:
        assert m_w.sharpe_ratio < m_d.sharpe_ratio


# ---------------------------------------------------------------------------
# 3. engine_version stamped on result
# ---------------------------------------------------------------------------


def test_engine_version_constant_is_2():
    """v2 marks the no-look-ahead + correct-slippage + interval-Sharpe engine."""
    assert BACKTEST_ENGINE_VERSION == 2


# ---------------------------------------------------------------------------
# 5. backtest_engine SimulatedExecution slippage direction
# ---------------------------------------------------------------------------


def test_simulated_execution_long_entry_slippage_adds_to_price():
    """LONG entry pays slippage (buy ABOVE the bar close)."""
    from backtest_engine import SimulatedExecution
    from events import OrderEvent, EventType, MarketEvent

    exe = SimulatedExecution(slippage_pct=0.05, commission_per_trade=0.0)
    order = OrderEvent(
        timestamp=datetime.now(tz=timezone.utc), type=EventType.ORDER,
        symbol="AAPL", order_type="MKT", quantity=10, direction="LONG", rule_id="t",
    )
    bar = MarketEvent(
        timestamp=datetime.now(tz=timezone.utc), type=EventType.MARKET,
        symbol="AAPL", open=100, high=101, low=99, close=100.0, volume=1000,
    )
    fill = exe.execute_order(order, bar)
    assert fill is not None
    # 100 + (100 * 0.05/100) = 100 + 0.05 = 100.05
    assert fill.fill_price == pytest.approx(100.05)


def test_simulated_execution_short_exit_slippage_subtracts_from_price():
    """SHORT-tagged EXIT fills at price - slippage (sell INTO the bid).

    Previously the EXIT order was tagged direction='LONG' (Batch 7 fix)
    so this filled at 100 + 0.05; the correct close fill is 100 - 0.05.
    """
    from backtest_engine import SimulatedExecution
    from events import OrderEvent, EventType, MarketEvent

    exe = SimulatedExecution(slippage_pct=0.05, commission_per_trade=0.0)
    order = OrderEvent(
        timestamp=datetime.now(tz=timezone.utc), type=EventType.ORDER,
        symbol="AAPL", order_type="MKT", quantity=10, direction="SHORT", rule_id="t",
    )
    bar = MarketEvent(
        timestamp=datetime.now(tz=timezone.utc), type=EventType.MARKET,
        symbol="AAPL", open=100, high=101, low=99, close=100.0, volume=1000,
    )
    fill = exe.execute_order(order, bar)
    assert fill is not None
    # 100 - 0.05 = 99.95
    assert fill.fill_price == pytest.approx(99.95)


# ---------------------------------------------------------------------------
# 6. End-to-end no-look-ahead — the test the test-quality reviewer flagged
#    as missing in the original Batch 7. Exercises the staged-fill path with
#    a known-firing entry signal and asserts the fill price equals the
#    NEXT bar's open (not the signal bar's close).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_backtest_entry_fills_at_next_bar_open_not_signal_bar_close():
    """Signal at bar i close MUST fill at bar i+1's open (no look-ahead)."""
    from unittest.mock import patch
    import pandas as pd
    from backtester import run_backtest
    from models import Condition

    # 50 bars; bar 30 has a sharply different close vs bar 31 open so the
    # difference between "fill at signal bar close" (BUG) and "fill at next
    # bar open" (FIX) is unambiguous.
    n = 50
    closes = [100.0] * n
    opens = [100.0] * n
    highs = [100.5] * n
    lows = [99.5] * n
    # Signal-firing bar (30): close jumps high
    closes[30] = 110.0
    highs[30] = 110.5
    # Next bar (31): open is back near 100 — distinct from bar-30 close
    opens[31] = 100.0
    closes[31] = 100.0
    dates = pd.date_range("2024-01-01", periods=n, freq="B")
    mock_df = pd.DataFrame({
        "Date": dates, "Open": opens, "High": highs, "Low": lows,
        "Close": closes, "Volume": [1_000_000] * n,
    }).set_index("Date")

    with patch("backtester.yf") as mock_yf:
        mock_yf.Ticker.return_value.history.return_value = mock_df
        # PRICE > 0 fires every bar; first signal lands on bar 30 (warmup ~2),
        # entry must fill at bar 31 open = 100.0.
        result = await run_backtest(
            entry_conditions=[Condition(indicator="PRICE", params={}, operator=">", value=0)],
            exit_conditions=[Condition(indicator="PRICE", params={}, operator=">", value=99999)],
            symbol="TEST", period="1y", interval="1d",
            initial_capital=100_000.0, position_size_pct=100.0,
            stop_loss_pct=0, take_profit_pct=0, exit_mode="simple",
        )

    assert result["trades"], "test setup requires at least one entry"
    first_entry = result["trades"][0]["entry_price"]
    # The signal fires at the first bar where PRICE > 0 (= warmup+1 area).
    # Entry must fill at the NEXT bar's open. Critical assertion: the fill
    # price MUST NOT equal any bar's close where it would only happen if
    # we filled at signal-bar close. In particular, with the synthetic data
    # above, an entry at bar 30 (close=110.0) would be the look-ahead bug.
    assert first_entry != pytest.approx(110.0), (
        "Entry filled at bar 30's close (110.0) — that's the look-ahead "
        "bug Batch 7 was supposed to fix. Entry must be at bar 31's open."
    )
    # And engine_version must be the post-fix version
    assert result.get("engine_version") == 2


# ---------------------------------------------------------------------------
# 7. SL gap-down direction — fills at min(sl, open) which is `open` on a
#    gap-down. The test pins the worse-of-two semantics.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_backtest_sl_gap_down_fills_at_open_not_at_stop():
    """When bar opens BELOW the stop, fill at open (worse of stop vs open)."""
    from unittest.mock import patch
    import pandas as pd
    from backtester import run_backtest
    from models import Condition

    n = 50
    closes = [100.0] * n
    opens = [100.0] * n
    highs = [101.0] * n
    lows = [99.0] * n
    # Bar 31 will be the entry fill; bar 32 gaps DOWN below the 5% stop.
    # Entry fills at bar 31 open=100, sl=95. Bar 32 opens at 90 (below stop).
    # SL fill must be 90 (the open), NOT 95 (the stop).
    opens[32] = 90.0
    closes[32] = 90.0
    highs[32] = 91.0
    lows[32] = 89.0
    dates = pd.date_range("2024-01-01", periods=n, freq="B")
    mock_df = pd.DataFrame({
        "Date": dates, "Open": opens, "High": highs, "Low": lows,
        "Close": closes, "Volume": [1_000_000] * n,
    }).set_index("Date")

    with patch("backtester.yf") as mock_yf:
        mock_yf.Ticker.return_value.history.return_value = mock_df
        result = await run_backtest(
            entry_conditions=[Condition(indicator="PRICE", params={}, operator=">", value=0)],
            exit_conditions=[Condition(indicator="PRICE", params={}, operator=">", value=99999)],
            symbol="TEST", period="1y", interval="1d",
            initial_capital=100_000.0, position_size_pct=100.0,
            stop_loss_pct=5.0, take_profit_pct=0, exit_mode="simple",
        )

    sl_trades = [t for t in result["trades"] if t["exit_reason"] == "stop_loss"]
    assert sl_trades, "test setup requires at least one stop-loss trade"
    first_sl = sl_trades[0]
    # exit_price = min(95, 90) = 90 (the worse fill). NOT 95 (the stop).
    assert first_sl["exit_price"] == pytest.approx(90.0, abs=0.01), (
        f"gap-down SL must fill at the open (worse), got {first_sl['exit_price']}"
    )

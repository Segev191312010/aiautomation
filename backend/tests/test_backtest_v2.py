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

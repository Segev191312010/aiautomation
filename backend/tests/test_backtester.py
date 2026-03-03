"""
Tests for the backtesting engine.

Covers warmup detection, evaluate_conditions, look-ahead bias,
SL/TP gap-aware fills, metrics computation, and full backtest flow.
"""
from __future__ import annotations

import math
from unittest.mock import AsyncMock, patch

import numpy as np
import pandas as pd
import pytest

from models import Condition, Rule, TradeAction
from rule_engine import evaluate_conditions, evaluate_rule
from backtester import _determine_warmup, _compute_metrics, run_backtest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_df(closes: list[float], n: int | None = None) -> pd.DataFrame:
    """Build a minimal OHLCV DataFrame from a list of close prices."""
    if n is None:
        n = len(closes)
    data = {
        "time": list(range(1_000_000, 1_000_000 + n)),
        "open":   closes[:n],
        "high":  [c * 1.02 for c in closes[:n]],
        "low":   [c * 0.98 for c in closes[:n]],
        "close": closes[:n],
        "volume": [1000] * n,
    }
    return pd.DataFrame(data)


def _trending_up(start: float, n: int, step: float = 1.0) -> list[float]:
    """Generate a steadily rising price series."""
    return [start + i * step for i in range(n)]


def _trending_down(start: float, n: int, step: float = 1.0) -> list[float]:
    """Generate a steadily falling price series."""
    return [start - i * step for i in range(n)]


def _make_df_with_ohlc(
    opens: list[float],
    highs: list[float],
    lows: list[float],
    closes: list[float],
) -> pd.DataFrame:
    """Build OHLCV DataFrame with explicit OHLC values."""
    n = len(closes)
    return pd.DataFrame({
        "time": list(range(1_000_000, 1_000_000 + n)),
        "open": opens,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": [1000] * n,
    })


# ---------------------------------------------------------------------------
# 1. Warmup detection tests
# ---------------------------------------------------------------------------

class TestWarmup:
    def test_sma_length(self):
        conds = [Condition(indicator="SMA", params={"length": 50}, operator=">", value=100)]
        assert _determine_warmup(conds, []) >= 50

    def test_rsi_length(self):
        conds = [Condition(indicator="RSI", params={"length": 14}, operator="<", value=30)]
        assert _determine_warmup(conds, []) >= 14

    def test_macd_slow_signal(self):
        conds = [Condition(indicator="MACD", params={"slow": 26, "signal": 9}, operator=">", value=0)]
        assert _determine_warmup(conds, []) >= 35

    def test_stoch_combined(self):
        conds = [Condition(indicator="STOCH", params={"k": 14, "smooth_k": 3, "d": 3}, operator="<", value=20)]
        assert _determine_warmup(conds, []) >= 20

    def test_combined_max(self):
        entry = [Condition(indicator="SMA", params={"length": 200}, operator=">", value="SMA_50")]
        exit_ = [Condition(indicator="RSI", params={"length": 14}, operator=">", value=70)]
        assert _determine_warmup(entry, exit_) >= 200

    def test_value_reference_sma_200(self):
        conds = [Condition(indicator="PRICE", params={}, operator=">", value="SMA_200")]
        assert _determine_warmup(conds, []) >= 200

    def test_max_warmup_cap(self):
        conds = [Condition(indicator="SMA", params={"length": 2000}, operator=">", value=0)]
        warmup = _determine_warmup(conds, [])
        assert warmup <= 1000

    def test_unknown_indicator_defaults_zero(self):
        conds = [Condition(indicator="PRICE", params={}, operator=">", value=100)]
        # PRICE is known but has 0 lookback
        assert _determine_warmup(conds, []) == 0


# ---------------------------------------------------------------------------
# 2. evaluate_conditions tests
# ---------------------------------------------------------------------------

class TestEvaluateConditions:
    def test_and_logic_all_true(self):
        # SMA(5) on a rising series — close > SMA should be true
        closes = _trending_up(100, 20, 2.0)
        df = _make_df(closes)
        conds = [
            Condition(indicator="PRICE", params={}, operator=">", value="SMA_5"),
        ]
        result = evaluate_conditions(conds, df, "AND")
        assert result is True

    def test_or_logic_one_true(self):
        closes = _trending_up(100, 20, 2.0)
        df = _make_df(closes)
        conds = [
            Condition(indicator="PRICE", params={}, operator=">", value=9999),  # False
            Condition(indicator="PRICE", params={}, operator=">", value=0),     # True
        ]
        result = evaluate_conditions(conds, df, "OR")
        assert result is True

    def test_empty_df_returns_false(self):
        df = pd.DataFrame(columns=["time", "open", "high", "low", "close", "volume"])
        conds = [Condition(indicator="PRICE", params={}, operator=">", value=0)]
        assert evaluate_conditions(conds, df, "AND") is False

    def test_single_bar_returns_false(self):
        df = _make_df([100], n=1)
        conds = [Condition(indicator="PRICE", params={}, operator=">", value=0)]
        assert evaluate_conditions(conds, df, "AND") is False


# ---------------------------------------------------------------------------
# 3. Backward compatibility
# ---------------------------------------------------------------------------

class TestBackwardCompat:
    def test_evaluate_rule_still_works(self):
        """Existing evaluate_rule() function works unchanged for bot_runner."""
        rule = Rule(
            name="Test Rule",
            symbol="AAPL",
            enabled=True,
            conditions=[
                Condition(indicator="PRICE", params={}, operator=">", value=0),
            ],
            action=TradeAction(type="BUY", quantity=10),
            cooldown_minutes=0,
        )
        df = _make_df(_trending_up(100, 10))
        result = evaluate_rule(rule, df)
        assert result is True

    def test_disabled_rule_returns_false(self):
        rule = Rule(
            name="Disabled",
            symbol="AAPL",
            enabled=False,
            conditions=[
                Condition(indicator="PRICE", params={}, operator=">", value=0),
            ],
            action=TradeAction(type="BUY", quantity=10),
        )
        df = _make_df(_trending_up(100, 10))
        assert evaluate_rule(rule, df) is False


# ---------------------------------------------------------------------------
# 4. No look-ahead bias
# ---------------------------------------------------------------------------

class TestNoLookAhead:
    def test_slice_vs_full_df(self):
        """evaluate_conditions on df[:n] should match df[:n] from full df."""
        full = _make_df(_trending_up(100, 50))
        n = 25
        slice_short = full.iloc[:n].copy()
        slice_from_full = full.iloc[:n].copy()

        conds = [Condition(indicator="SMA", params={"length": 5}, operator="<", value="PRICE")]

        r1 = evaluate_conditions(conds, slice_short, "AND")
        r2 = evaluate_conditions(conds, slice_from_full, "AND")
        assert r1 == r2


# ---------------------------------------------------------------------------
# 5. Metrics computation
# ---------------------------------------------------------------------------

class TestMetrics:
    def test_known_trade_list(self):
        trades = [
            {"pnl": 100, "pnl_pct": 10, "duration_days": 5, "duration_bars": 5},
            {"pnl": -50, "pnl_pct": -5, "duration_days": 3, "duration_bars": 3},
            {"pnl": 200, "pnl_pct": 20, "duration_days": 10, "duration_bars": 10},
            {"pnl": -30, "pnl_pct": -3, "duration_days": 2, "duration_bars": 2},
        ]
        equity_curve = [{"time": i, "equity": 100_000 + i * 10} for i in range(252)]
        metrics = _compute_metrics(trades, equity_curve, 100_000, 252)

        assert metrics.win_rate == 50.0
        assert metrics.profit_factor == pytest.approx(3.75, rel=0.01)
        assert metrics.avg_win == pytest.approx(150, rel=0.01)
        assert metrics.avg_loss == pytest.approx(-40, rel=0.01)
        assert metrics.num_trades == 4
        assert metrics.longest_win_streak == 1
        assert metrics.longest_lose_streak == 1

    def test_streaks(self):
        trades = [
            {"pnl": 10, "pnl_pct": 1, "duration_days": 1, "duration_bars": 1},
            {"pnl": 20, "pnl_pct": 2, "duration_days": 1, "duration_bars": 1},
            {"pnl": 30, "pnl_pct": 3, "duration_days": 1, "duration_bars": 1},
            {"pnl": -5, "pnl_pct": -0.5, "duration_days": 1, "duration_bars": 1},
            {"pnl": -3, "pnl_pct": -0.3, "duration_days": 1, "duration_bars": 1},
        ]
        equity_curve = [{"time": i, "equity": 100_000} for i in range(100)]
        metrics = _compute_metrics(trades, equity_curve, 100_000, 100)
        assert metrics.longest_win_streak == 3
        assert metrics.longest_lose_streak == 2

    def test_no_trades(self):
        equity_curve = [{"time": i, "equity": 100_000} for i in range(100)]
        metrics = _compute_metrics([], equity_curve, 100_000, 100)
        assert metrics.num_trades == 0
        assert metrics.win_rate == 0.0
        assert metrics.profit_factor == 999.99


# ---------------------------------------------------------------------------
# 6. Full backtest with mocked yfinance
# ---------------------------------------------------------------------------

def _mock_yf_history(n: int = 300, trend: str = "up") -> pd.DataFrame:
    """Create a deterministic DataFrame that mimics yfinance output."""
    base = 100.0
    closes = []
    for i in range(n):
        if trend == "up":
            base += np.sin(i / 10) * 2 + 0.1  # slight upward bias
        else:
            base -= np.sin(i / 10) * 2 + 0.1
        closes.append(max(base, 1.0))

    dates = pd.date_range("2023-01-01", periods=n, freq="B")
    return pd.DataFrame({
        "Date": dates,
        "Open": [c * 0.999 for c in closes],
        "High": [c * 1.02 for c in closes],
        "Low": [c * 0.98 for c in closes],
        "Close": closes,
        "Volume": [1_000_000] * n,
    }).set_index("Date")


class TestFullBacktest:
    @pytest.mark.asyncio
    async def test_basic_rsi_backtest(self):
        """Full backtest: RSI(14) < 30 entry, RSI(14) > 70 exit."""
        mock_df = _mock_yf_history(300)

        with patch("backtester.yf") as mock_yf:
            mock_ticker = mock_yf.Ticker.return_value
            mock_ticker.history.return_value = mock_df

            entry = [Condition(indicator="RSI", params={"length": 14}, operator="<", value=30)]
            exit_ = [Condition(indicator="RSI", params={"length": 14}, operator=">", value=70)]

            result = await run_backtest(
                entry_conditions=entry,
                exit_conditions=exit_,
                symbol="TEST",
                period="2y",
                interval="1d",
                initial_capital=100_000,
                position_size_pct=100,
                stop_loss_pct=0,
                take_profit_pct=0,
            )

            assert result["warmup_period"] >= 14
            assert result["total_bars"] == 300
            assert len(result["equity_curve"]) > 0
            assert len(result["buy_hold_curve"]) > 0
            assert len(result["equity_curve"]) == len(result["buy_hold_curve"])
            assert result["metrics"]["num_trades"] >= 0
            assert result["initial_capital"] == 100_000

    @pytest.mark.asyncio
    async def test_baseline_alignment(self):
        """Both equity curves start at the same timestamp and capital."""
        mock_df = _mock_yf_history(200)

        with patch("backtester.yf") as mock_yf:
            mock_ticker = mock_yf.Ticker.return_value
            mock_ticker.history.return_value = mock_df

            entry = [Condition(indicator="PRICE", params={}, operator=">", value="SMA_5")]
            exit_ = [Condition(indicator="PRICE", params={}, operator="<", value="SMA_5")]

            result = await run_backtest(
                entry_conditions=entry,
                exit_conditions=exit_,
                symbol="TEST",
                period="1y",
                interval="1d",
                initial_capital=100_000,
                position_size_pct=100,
                stop_loss_pct=0,
                take_profit_pct=0,
            )

            ec = result["equity_curve"]
            bh = result["buy_hold_curve"]
            assert len(ec) == len(bh)
            assert ec[0]["time"] == bh[0]["time"]
            # Buy-and-hold starts at initial capital
            assert bh[0]["equity"] == pytest.approx(100_000, rel=0.01)

    @pytest.mark.asyncio
    async def test_force_close_at_end(self):
        """Open position at end of data should be force-closed."""
        # Create a strong uptrend so entry triggers but exit never does
        closes = _trending_up(50, 200, 0.5)
        dates = pd.date_range("2023-01-01", periods=200, freq="B")
        mock_df = pd.DataFrame({
            "Date": dates,
            "Open": [c * 0.999 for c in closes],
            "High": [c * 1.02 for c in closes],
            "Low": [c * 0.98 for c in closes],
            "Close": closes,
            "Volume": [1_000_000] * 200,
        }).set_index("Date")

        with patch("backtester.yf") as mock_yf:
            mock_ticker = mock_yf.Ticker.return_value
            mock_ticker.history.return_value = mock_df

            # Always-true entry, never-true exit
            entry = [Condition(indicator="PRICE", params={}, operator=">", value=0)]
            exit_ = [Condition(indicator="PRICE", params={}, operator="<", value=0)]

            result = await run_backtest(
                entry_conditions=entry,
                exit_conditions=exit_,
                symbol="TEST",
                period="1y",
                interval="1d",
                initial_capital=100_000,
                position_size_pct=100,
                stop_loss_pct=0,
                take_profit_pct=0,
            )

            # Should have exactly 1 trade, closed at end
            assert len(result["trades"]) >= 1
            last_trade = result["trades"][-1]
            assert last_trade["exit_reason"] == "end_of_data"

    @pytest.mark.asyncio
    async def test_minimum_bar_guard(self):
        """Too few bars should raise ValueError."""
        # Only 10 bars — not enough for any meaningful warmup
        dates = pd.date_range("2023-01-01", periods=10, freq="B")
        mock_df = pd.DataFrame({
            "Date": dates,
            "Open": [100] * 10,
            "High": [102] * 10,
            "Low": [98] * 10,
            "Close": [100] * 10,
            "Volume": [1000] * 10,
        }).set_index("Date")

        with patch("backtester.yf") as mock_yf:
            mock_ticker = mock_yf.Ticker.return_value
            mock_ticker.history.return_value = mock_df

            entry = [Condition(indicator="SMA", params={"length": 5}, operator=">", value=0)]
            exit_ = [Condition(indicator="PRICE", params={}, operator="<", value=0)]

            with pytest.raises(ValueError, match="Not enough bars"):
                await run_backtest(
                    entry_conditions=entry,
                    exit_conditions=exit_,
                    symbol="TEST",
                    period="1mo",
                    interval="1d",
                    initial_capital=100_000,
                    position_size_pct=100,
                    stop_loss_pct=0,
                    take_profit_pct=0,
                )


# ---------------------------------------------------------------------------
# 7. Stop-loss / Take-profit gap-aware tests
# ---------------------------------------------------------------------------

class TestSLTP:
    @pytest.mark.asyncio
    async def test_stop_loss_intraday(self):
        """SL hit during the bar (low <= SL) → fill at SL price."""
        # Build data: enter at bar 5 (close=100), bar 6 low dips to 95 (SL=97)
        n = 50
        closes = [100.0] * n
        opens = [100.0] * n
        highs = [102.0] * n
        lows = [98.0] * n

        # Bar 6: low dips to 95 (below SL of 97)
        lows[6] = 95.0
        closes[6] = 99.0
        opens[6] = 100.0

        dates = pd.date_range("2023-01-01", periods=n, freq="B")
        mock_df = pd.DataFrame({
            "Date": dates,
            "Open": opens,
            "High": highs,
            "Low": lows,
            "Close": closes,
            "Volume": [1000] * n,
        }).set_index("Date")

        with patch("backtester.yf") as mock_yf:
            mock_ticker = mock_yf.Ticker.return_value
            mock_ticker.history.return_value = mock_df

            entry = [Condition(indicator="PRICE", params={}, operator=">", value=0)]
            exit_ = [Condition(indicator="PRICE", params={}, operator="<", value=0)]

            result = await run_backtest(
                entry_conditions=entry,
                exit_conditions=exit_,
                symbol="TEST",
                period="1y",
                interval="1d",
                initial_capital=100_000,
                position_size_pct=100,
                stop_loss_pct=3.0,  # SL at 97
                take_profit_pct=0,
            )

            # Should have a stop_loss trade
            sl_trades = [t for t in result["trades"] if t["exit_reason"] == "stop_loss"]
            assert len(sl_trades) >= 1
            # SL price = 97, open = 100: fill at min(97, 100) = 97
            assert sl_trades[0]["exit_price"] == pytest.approx(97.0, abs=0.1)

    @pytest.mark.asyncio
    async def test_stop_loss_gap_down(self):
        """Gap-down below SL → fill at open (worse than SL)."""
        n = 50
        closes = [100.0] * n
        opens = [100.0] * n
        highs = [102.0] * n
        lows = [98.0] * n

        # Bar 6: gap-down — open at 94, below SL of 97
        opens[6] = 94.0
        lows[6] = 93.0
        closes[6] = 95.0
        highs[6] = 96.0

        dates = pd.date_range("2023-01-01", periods=n, freq="B")
        mock_df = pd.DataFrame({
            "Date": dates,
            "Open": opens,
            "High": highs,
            "Low": lows,
            "Close": closes,
            "Volume": [1000] * n,
        }).set_index("Date")

        with patch("backtester.yf") as mock_yf:
            mock_ticker = mock_yf.Ticker.return_value
            mock_ticker.history.return_value = mock_df

            entry = [Condition(indicator="PRICE", params={}, operator=">", value=0)]
            exit_ = [Condition(indicator="PRICE", params={}, operator="<", value=0)]

            result = await run_backtest(
                entry_conditions=entry,
                exit_conditions=exit_,
                symbol="TEST",
                period="1y",
                interval="1d",
                initial_capital=100_000,
                position_size_pct=100,
                stop_loss_pct=3.0,  # SL at 97
                take_profit_pct=0,
            )

            sl_trades = [t for t in result["trades"] if t["exit_reason"] == "stop_loss"]
            assert len(sl_trades) >= 1
            # Gap-down: fill at min(SL=97, open=94) = 94
            assert sl_trades[0]["exit_price"] == pytest.approx(94.0, abs=0.1)

    @pytest.mark.asyncio
    async def test_take_profit_intraday(self):
        """TP hit during the bar → fill at TP price."""
        n = 50
        closes = [100.0] * n
        opens = [100.0] * n
        highs = [102.0] * n
        lows = [98.0] * n

        # Bar 6: high reaches 106 (TP=105 with 5% TP)
        highs[6] = 106.0
        closes[6] = 104.0
        opens[6] = 100.0

        dates = pd.date_range("2023-01-01", periods=n, freq="B")
        mock_df = pd.DataFrame({
            "Date": dates,
            "Open": opens,
            "High": highs,
            "Low": lows,
            "Close": closes,
            "Volume": [1000] * n,
        }).set_index("Date")

        with patch("backtester.yf") as mock_yf:
            mock_ticker = mock_yf.Ticker.return_value
            mock_ticker.history.return_value = mock_df

            entry = [Condition(indicator="PRICE", params={}, operator=">", value=0)]
            exit_ = [Condition(indicator="PRICE", params={}, operator="<", value=0)]

            result = await run_backtest(
                entry_conditions=entry,
                exit_conditions=exit_,
                symbol="TEST",
                period="1y",
                interval="1d",
                initial_capital=100_000,
                position_size_pct=100,
                stop_loss_pct=0,
                take_profit_pct=5.0,  # TP at 105
            )

            tp_trades = [t for t in result["trades"] if t["exit_reason"] == "take_profit"]
            assert len(tp_trades) >= 1
            # TP price = 105, open = 100: fill at max(105, 100) = 105
            assert tp_trades[0]["exit_price"] == pytest.approx(105.0, abs=0.1)

    @pytest.mark.asyncio
    async def test_take_profit_gap_up(self):
        """Gap-up above TP → fill at open (better than TP)."""
        n = 50
        closes = [100.0] * n
        opens = [100.0] * n
        highs = [102.0] * n
        lows = [98.0] * n

        # Bar 6: gap-up — open at 108, above TP of 105
        opens[6] = 108.0
        highs[6] = 110.0
        lows[6] = 107.0
        closes[6] = 109.0

        dates = pd.date_range("2023-01-01", periods=n, freq="B")
        mock_df = pd.DataFrame({
            "Date": dates,
            "Open": opens,
            "High": highs,
            "Low": lows,
            "Close": closes,
            "Volume": [1000] * n,
        }).set_index("Date")

        with patch("backtester.yf") as mock_yf:
            mock_ticker = mock_yf.Ticker.return_value
            mock_ticker.history.return_value = mock_df

            entry = [Condition(indicator="PRICE", params={}, operator=">", value=0)]
            exit_ = [Condition(indicator="PRICE", params={}, operator="<", value=0)]

            result = await run_backtest(
                entry_conditions=entry,
                exit_conditions=exit_,
                symbol="TEST",
                period="1y",
                interval="1d",
                initial_capital=100_000,
                position_size_pct=100,
                stop_loss_pct=0,
                take_profit_pct=5.0,  # TP at 105
            )

            tp_trades = [t for t in result["trades"] if t["exit_reason"] == "take_profit"]
            assert len(tp_trades) >= 1
            # Gap-up: fill at max(TP=105, open=108) = 108
            assert tp_trades[0]["exit_price"] == pytest.approx(108.0, abs=0.1)


# ---------------------------------------------------------------------------
# 8. Commission correctness
# ---------------------------------------------------------------------------

class TestCommission:
    @pytest.mark.asyncio
    async def test_commission_applied(self):
        """Commission should reduce PnL."""
        n = 50
        closes = _trending_up(100, n, 1.0)
        dates = pd.date_range("2023-01-01", periods=n, freq="B")
        mock_df = pd.DataFrame({
            "Date": dates,
            "Open": [c * 0.999 for c in closes],
            "High": [c * 1.02 for c in closes],
            "Low": [c * 0.98 for c in closes],
            "Close": closes,
            "Volume": [1000] * n,
        }).set_index("Date")

        with patch("backtester.yf") as mock_yf:
            mock_ticker = mock_yf.Ticker.return_value
            mock_ticker.history.return_value = mock_df

            entry = [Condition(indicator="PRICE", params={}, operator=">", value=0)]
            exit_ = [Condition(indicator="PRICE", params={}, operator=">", value=999)]

            result = await run_backtest(
                entry_conditions=entry,
                exit_conditions=exit_,
                symbol="TEST",
                period="1y",
                interval="1d",
                initial_capital=100_000,
                position_size_pct=100,
                stop_loss_pct=0,
                take_profit_pct=0,
            )

            # Should have end_of_data trade — PnL should account for 2x commission
            if result["trades"]:
                trade = result["trades"][-1]
                qty = trade["qty"]
                raw_pnl = (trade["exit_price"] - trade["entry_price"]) * qty
                # PnL should be raw_pnl minus 2x commission ($1 each)
                assert trade["pnl"] == pytest.approx(raw_pnl - 2.0, abs=0.1)

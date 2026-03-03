"""
Backtesting engine — event-driven, bar-by-bar.

Processes historical bars sequentially. Each bar only sees data up to
that point (no look-ahead bias). Uses the same ``_evaluate_condition()``
logic as the live rule engine via ``evaluate_conditions()``.
"""
from __future__ import annotations

import asyncio
import logging
import math
import re
from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd
import yfinance as yf

from config import cfg
from models import BacktestMetrics, BacktestResult, BacktestTrade, Condition
from rule_engine import evaluate_conditions

log = logging.getLogger(__name__)

MAX_WARMUP = 1000
MIN_BARS_AFTER_WARMUP = 20


# ---------------------------------------------------------------------------
# Warmup detection
# ---------------------------------------------------------------------------

def _determine_warmup(
    entry_conditions: list[Condition],
    exit_conditions: list[Condition],
) -> int:
    """
    Scan all conditions and return the maximum indicator lookback period.

    Also parses string value references like ``"SMA_200"`` → 200.
    Caps at ``MAX_WARMUP`` and warns on unknown indicators.
    """
    lookbacks: list[int] = []

    for cond in [*entry_conditions, *exit_conditions]:
        ind = cond.indicator.upper()
        p = cond.params

        if ind in ("SMA", "EMA", "BBANDS"):
            lookbacks.append(int(p.get("length", 20)))
        elif ind in ("RSI", "ATR"):
            lookbacks.append(int(p.get("length", 14)))
        elif ind == "MACD":
            lookbacks.append(int(p.get("slow", 26)) + int(p.get("signal", 9)))
        elif ind == "STOCH":
            lookbacks.append(
                int(p.get("k", 14)) + int(p.get("smooth_k", 3)) + int(p.get("d", 3))
            )
        elif ind == "PRICE":
            lookbacks.append(0)
        else:
            log.warning("Unknown indicator '%s' in warmup — defaulting to 0", ind)
            lookbacks.append(0)

        # Also check string value references like "SMA_200"
        if isinstance(cond.value, str):
            m = re.match(r"^([A-Z]+)_(\d+)$", cond.value.upper())
            if m:
                lookbacks.append(int(m.group(2)))

    warmup = max(lookbacks) if lookbacks else 0
    if warmup > MAX_WARMUP:
        log.warning("Warmup %d exceeds MAX_WARMUP=%d — clamping", warmup, MAX_WARMUP)
        warmup = MAX_WARMUP
    return warmup


# ---------------------------------------------------------------------------
# Metrics computation
# ---------------------------------------------------------------------------

def _compute_metrics(
    trades: list[dict[str, Any]],
    equity_curve: list[dict[str, Any]],
    initial_capital: float,
    total_bars: int,
) -> BacktestMetrics:
    """Compute all performance metrics from trade list and equity curve."""
    final_equity = equity_curve[-1]["equity"] if equity_curve else initial_capital

    # -- Total return --
    total_return_pct = ((final_equity - initial_capital) / initial_capital) * 100

    # -- Trading days for annualization --
    trading_days = len(equity_curve) if equity_curve else 1

    # -- CAGR --
    if trading_days > 1 and final_equity > 0 and initial_capital > 0:
        years = trading_days / 252
        cagr = ((final_equity / initial_capital) ** (1 / years) - 1) * 100 if years > 0 else 0.0
    else:
        cagr = 0.0

    # -- Daily returns for Sharpe/Sortino --
    equities = [e["equity"] for e in equity_curve]
    daily_returns: list[float] = []
    for i in range(1, len(equities)):
        if equities[i - 1] != 0:
            daily_returns.append((equities[i] - equities[i - 1]) / equities[i - 1])

    dr = np.array(daily_returns) if daily_returns else np.array([0.0])

    # -- Sharpe --
    if np.std(dr) > 0:
        sharpe_ratio = float(np.mean(dr) / np.std(dr) * np.sqrt(252))
    else:
        sharpe_ratio = 0.0

    # -- Sortino --
    neg_returns = dr[dr < 0]
    if len(neg_returns) > 0 and np.std(neg_returns) > 0:
        sortino_ratio = float(np.mean(dr) / np.std(neg_returns) * np.sqrt(252))
    else:
        sortino_ratio = 0.0

    # -- Max drawdown --
    max_drawdown_pct = 0.0
    if equity_curve:
        peak = equity_curve[0]["equity"]
        for e in equity_curve:
            if e["equity"] > peak:
                peak = e["equity"]
            dd = ((peak - e["equity"]) / peak) * 100 if peak > 0 else 0.0
            if dd > max_drawdown_pct:
                max_drawdown_pct = dd

    # -- Calmar --
    calmar_ratio = (cagr / max_drawdown_pct) if max_drawdown_pct > 0 else 0.0

    # -- Trade-based metrics --
    num_trades = len(trades)
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]

    win_rate = (len(wins) / num_trades * 100) if num_trades > 0 else 0.0
    avg_win = float(np.mean([t["pnl"] for t in wins])) if wins else 0.0
    avg_loss = float(np.mean([t["pnl"] for t in losses])) if losses else 0.0

    total_win_pnl = sum(t["pnl"] for t in wins)
    total_loss_pnl = abs(sum(t["pnl"] for t in losses))
    profit_factor = (total_win_pnl / total_loss_pnl) if total_loss_pnl > 0 else 999.99

    # -- Streaks --
    longest_win_streak = 0
    longest_lose_streak = 0
    current_win = 0
    current_lose = 0
    for t in trades:
        if t["pnl"] > 0:
            current_win += 1
            current_lose = 0
            longest_win_streak = max(longest_win_streak, current_win)
        else:
            current_lose += 1
            current_win = 0
            longest_lose_streak = max(longest_lose_streak, current_lose)

    # -- Average trade duration --
    avg_trade_duration_days = (
        float(np.mean([t["duration_days"] for t in trades])) if trades else 0.0
    )

    return BacktestMetrics(
        total_return_pct=round(total_return_pct, 2),
        cagr=round(cagr, 2),
        sharpe_ratio=round(sharpe_ratio, 2),
        sortino_ratio=round(sortino_ratio, 2),
        calmar_ratio=round(calmar_ratio, 2),
        max_drawdown_pct=round(max_drawdown_pct, 2),
        win_rate=round(win_rate, 2),
        profit_factor=round(profit_factor, 2),
        num_trades=num_trades,
        avg_win=round(avg_win, 2),
        avg_loss=round(avg_loss, 2),
        longest_win_streak=longest_win_streak,
        longest_lose_streak=longest_lose_streak,
        avg_trade_duration_days=round(avg_trade_duration_days, 2),
    )


# ---------------------------------------------------------------------------
# Core backtest runner
# ---------------------------------------------------------------------------

async def run_backtest(
    entry_conditions: list[Condition],
    exit_conditions: list[Condition],
    symbol: str,
    period: str = "2y",
    interval: str = "1d",
    initial_capital: float = 100_000.0,
    position_size_pct: float = 100.0,
    stop_loss_pct: float = 0.0,
    take_profit_pct: float = 0.0,
    condition_logic: str = "AND",
) -> dict:
    """
    Run an event-driven, bar-by-bar backtest.

    All signals are evaluated at bar close. SL/TP detection uses the
    current bar's low/high; fills happen at the detected price (or
    gap-open if the gap is worse for SL / better for TP).
    Entry fills at current bar's close price.
    """

    # -- Fetch historical data (in thread to avoid blocking event loop) --
    def _fetch() -> Any:
        return yf.Ticker(symbol).history(period=period, interval=interval)

    raw = await asyncio.to_thread(_fetch)
    if raw.empty:
        raise ValueError(f"No data returned for {symbol} ({period}/{interval})")

    # Reset index first (moves DatetimeIndex 'Date' to a column),
    # then normalise all columns to lowercase.
    raw = raw.reset_index()
    raw.columns = [c.lower() for c in raw.columns]

    # Normalise datetime column → unix timestamps
    date_col = "date" if "date" in raw.columns else "datetime"
    if date_col in raw.columns:
        raw["time"] = raw[date_col].apply(
            lambda x: int(pd.Timestamp(x).timestamp())
        )
    elif "time" not in raw.columns:
        raw["time"] = range(len(raw))

    # Ensure required columns exist
    for col in ("open", "high", "low", "close", "volume"):
        if col not in raw.columns:
            raise ValueError(f"Missing column '{col}' in data for {symbol}")

    df = raw[["time", "open", "high", "low", "close", "volume"]].copy()
    df = df.reset_index(drop=True)

    # -- Warmup --
    warmup = _determine_warmup(entry_conditions, exit_conditions)
    total_bars = len(df)

    if total_bars < warmup + MIN_BARS_AFTER_WARMUP:
        raise ValueError(
            f"Not enough bars ({total_bars}) for warmup ({warmup}) + "
            f"minimum {MIN_BARS_AFTER_WARMUP} trading bars"
        )

    # -- State --
    cash = initial_capital
    position_qty = 0
    entry_price = 0.0
    entry_bar = 0
    entry_time = 0
    sl_price = 0.0
    tp_price = 0.0
    commission = cfg.SIM_COMMISSION

    trades: list[dict] = []
    equity_curve: list[dict] = []
    buy_hold_curve: list[dict] = []
    running_peak = initial_capital

    start_close = df.at[warmup, "close"]

    # -- Bar-by-bar loop --
    for i in range(warmup, total_bars):
        bar = df.iloc[i]
        bar_time = int(bar["time"])
        current_open = float(bar["open"])
        current_high = float(bar["high"])
        current_low = float(bar["low"])
        current_close = float(bar["close"])

        # Slice for condition evaluation — only data up to current bar
        df_slice = df.iloc[: i + 1]

        exit_price = 0.0
        exit_reason = ""

        if position_qty > 0:
            # -- Check stop-loss (gap-aware) --
            if stop_loss_pct > 0 and sl_price > 0:
                if current_low <= sl_price:
                    # Intraday hit: fill at SL price
                    # Gap-down: fill at open (worse than SL)
                    exit_price = min(sl_price, current_open)
                    exit_reason = "stop_loss"

            # -- Check take-profit (gap-aware) --
            if not exit_reason and take_profit_pct > 0 and tp_price > 0:
                if current_high >= tp_price:
                    # Intraday hit: fill at TP price
                    # Gap-up: fill at open (better than TP)
                    exit_price = max(tp_price, current_open)
                    exit_reason = "take_profit"

            # -- Check exit conditions --
            if not exit_reason:
                if evaluate_conditions(exit_conditions, df_slice, condition_logic):
                    exit_price = current_close
                    exit_reason = "signal"

            # -- Execute exit --
            if exit_reason:
                proceeds = position_qty * exit_price - commission
                pnl = (exit_price - entry_price) * position_qty - 2 * commission
                pnl_pct = ((exit_price - entry_price) / entry_price) * 100 if entry_price > 0 else 0.0

                cash += proceeds

                # Calculate duration
                entry_dt = datetime.fromtimestamp(entry_time, tz=timezone.utc)
                exit_dt = datetime.fromtimestamp(bar_time, tz=timezone.utc)
                duration_days = (exit_dt - entry_dt).total_seconds() / 86400

                trades.append({
                    "entry_date": entry_dt.isoformat(),
                    "exit_date": exit_dt.isoformat(),
                    "entry_price": round(entry_price, 2),
                    "exit_price": round(exit_price, 2),
                    "qty": position_qty,
                    "pnl": round(pnl, 2),
                    "pnl_pct": round(pnl_pct, 2),
                    "duration_bars": i - entry_bar,
                    "duration_days": round(duration_days, 2),
                    "exit_reason": exit_reason,
                })

                position_qty = 0
                entry_price = 0.0
                sl_price = 0.0
                tp_price = 0.0

        # -- Check entry conditions (only if not in position) --
        if position_qty == 0:
            if evaluate_conditions(entry_conditions, df_slice, condition_logic):
                # BUY at current close
                available = cash * (position_size_pct / 100)
                qty = math.floor(available / current_close) if current_close > 0 else 0
                if qty > 0:
                    cost = qty * current_close + commission
                    cash -= cost
                    position_qty = qty
                    entry_price = current_close
                    entry_bar = i
                    entry_time = bar_time

                    # Set SL/TP levels
                    if stop_loss_pct > 0:
                        sl_price = entry_price * (1 - stop_loss_pct / 100)
                    if take_profit_pct > 0:
                        tp_price = entry_price * (1 + take_profit_pct / 100)

        # -- Mark-to-market equity --
        equity = cash + position_qty * current_close
        if equity > running_peak:
            running_peak = equity
        dd_pct = ((running_peak - equity) / running_peak * 100) if running_peak > 0 else 0.0

        equity_curve.append({
            "time": bar_time,
            "equity": round(equity, 2),
            "drawdown_pct": round(dd_pct, 2),
        })

        # -- Buy-and-hold curve (aligned) --
        bh_equity = initial_capital * (current_close / start_close) if start_close > 0 else initial_capital
        buy_hold_curve.append({
            "time": bar_time,
            "equity": round(bh_equity, 2),
        })

    # -- Force-close any open position at end of data --
    if position_qty > 0:
        last_close = float(df.iloc[-1]["close"])
        last_time = int(df.iloc[-1]["time"])
        pnl = (last_close - entry_price) * position_qty - 2 * commission
        pnl_pct = ((last_close - entry_price) / entry_price) * 100 if entry_price > 0 else 0.0

        entry_dt = datetime.fromtimestamp(entry_time, tz=timezone.utc)
        exit_dt = datetime.fromtimestamp(last_time, tz=timezone.utc)
        duration_days = (exit_dt - entry_dt).total_seconds() / 86400

        trades.append({
            "entry_date": entry_dt.isoformat(),
            "exit_date": exit_dt.isoformat(),
            "entry_price": round(entry_price, 2),
            "exit_price": round(last_close, 2),
            "qty": position_qty,
            "pnl": round(pnl, 2),
            "pnl_pct": round(pnl_pct, 2),
            "duration_bars": (total_bars - 1) - entry_bar,
            "duration_days": round(duration_days, 2),
            "exit_reason": "end_of_data",
        })

        cash += position_qty * last_close - commission
        position_qty = 0

    # -- Compute metrics --
    metrics = _compute_metrics(trades, equity_curve, initial_capital, total_bars)

    final_equity = equity_curve[-1]["equity"] if equity_curve else initial_capital

    return {
        "symbol": symbol,
        "period": period,
        "interval": interval,
        "initial_capital": initial_capital,
        "final_equity": final_equity,
        "equity_curve": equity_curve,
        "buy_hold_curve": buy_hold_curve,
        "trades": trades,
        "metrics": metrics.model_dump(),
        "warmup_period": warmup,
        "total_bars": total_bars,
        "entry_conditions": [c.model_dump() for c in entry_conditions],
        "exit_conditions": [c.model_dump() for c in exit_conditions],
        "condition_logic": condition_logic,
        "position_size_pct": position_size_pct,
        "stop_loss_pct": stop_loss_pct,
        "take_profit_pct": take_profit_pct,
    }

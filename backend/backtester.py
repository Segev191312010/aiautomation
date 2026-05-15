"""
Backtesting engine — event-driven, bar-by-bar.

Processes historical bars sequentially. Each bar only sees data up to
that point (no look-ahead bias). Uses the same ``_evaluate_condition()``
logic as the live rule engine via ``evaluate_conditions()``.

Exit modes:
  - ``simple``    : percentage-based SL/TP (original behavior)
  - ``atr_trail`` : ATR hard stop + trailing stop + EMA/SMA/RSI/MACD exits
                    (mirrors position_tracker.check_exits from the live bot)
"""
from __future__ import annotations

import asyncio
import logging
import math
import re
from datetime import datetime, timezone
from typing import Any, Literal

import numpy as np
import pandas as pd
import yfinance as yf

from config import cfg
from indicators import _atr, _ema, _macd, _rsi, _sma, detect_cross
from models import BacktestMetrics, BacktestResult, BacktestTrade, Condition
from rule_engine import clear_indicator_cache, evaluate_conditions

log = logging.getLogger(__name__)

MAX_WARMUP = 1000
MIN_BARS_AFTER_WARMUP = 20

# Engine version. Bumped when result-changing semantics are fixed (no
# look-ahead entries, correct slippage sign, interval-aware Sharpe, explicit
# auto_adjust). Stored on every saved backtest row so v1 results are
# visually marked as legacy/optimistic in the UI.
BACKTEST_ENGINE_VERSION = 2

# Interval -> approximate bars per year (US equities, ~6.5h trading day,
# 252 trading days). Used for interval-aware Sharpe/Sortino annualization.
# A regression test iterates every interval the API accepts and fails
# loudly with the missing interval's name if a new one isn't added here.
_PERIODS_PER_YEAR: dict[str, float] = {
    "1m": 252 * 6.5 * 60,    # ~98,280
    "2m": 252 * 6.5 * 30,    # ~49,140
    "5m": 252 * 6.5 * 12,    # ~19,656
    "15m": 252 * 6.5 * 4,    # ~6,552
    "30m": 252 * 6.5 * 2,    # ~3,276
    "60m": 252 * 6.5,        # ~1,638
    "90m": 252 * 6.5 / 1.5,  # ~1,092
    "1h": 252 * 6.5,         # ~1,638
    "1d": 252,
    "5d": 52,
    "1wk": 52,
    "1mo": 12,
    "3mo": 4,
}


def _periods_per_year(interval: str) -> float:
    """Lookup with a clear failure mode if a new interval is added.

    Tests pin every API-accepted interval; if the API later adds an
    interval string that isn't in the table, the test fails loudly with
    the exact interval name so a dev cannot silently regress Sharpe.
    """
    if interval not in _PERIODS_PER_YEAR:
        raise KeyError(
            f"interval {interval!r} missing from _PERIODS_PER_YEAR — "
            "add an annualization factor before using this interval for backtests"
        )
    return _PERIODS_PER_YEAR[interval]


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
# ATR-based exit checks (mirrors position_tracker.check_exits)
# ---------------------------------------------------------------------------

def _check_atr_exits(
    df_slice: pd.DataFrame,
    current_price: float,
    entry_price: float,
    hard_stop_price: float,
    high_watermark: float,
    atr_trail_mult: float,
    side: str = "BUY",
) -> tuple[bool, str, float]:
    """
    Check ATR-based exit conditions matching live bot's position_tracker.

    Checks in priority order:
      1. Hard stop (ATR-based, fixed at entry)
      2. Trailing stop (ATR-based, from watermark)
      3. EMA(21) cross below (needs 21+ bars)
      4. SMA(50) cross below (needs 50+ bars)
      5. RSI > 70 overbought (needs 30+ bars)
      6. MACD histogram < 0 cross (needs 35+ bars)

    Returns (should_exit, reason, exit_price).
    """
    close = df_slice["close"]
    n = len(df_slice)

    # 1. Hard stop
    if side == "BUY" and current_price <= hard_stop_price:
        return True, "hard_stop", current_price
    if side == "SELL" and current_price >= hard_stop_price:
        return True, "hard_stop", current_price

    # 2. Trailing stop
    if n >= 14:
        atr_series = _atr(df_slice["high"], df_slice["low"], close, 14)
        atr_raw = atr_series.iloc[-1]
        if pd.notna(atr_raw):
            current_atr = float(atr_raw)
            if side == "BUY":
                trail = round(high_watermark - atr_trail_mult * current_atr, 4)
                effective = max(hard_stop_price, trail)
                if current_price <= effective:
                    return True, "trail_stop", current_price
            else:
                trail = round(high_watermark + atr_trail_mult * current_atr, 4)
                effective = min(hard_stop_price, trail)
                if current_price >= effective:
                    return True, "trail_stop", current_price

    # 3. EMA(21) cross below
    if n >= 21:
        ema21 = _ema(close, 21)
        cross = detect_cross(close, ema21)
        if side == "BUY" and cross == "below":
            return True, "ema21_cross", current_price
        if side == "SELL" and cross == "above":
            return True, "ema21_cross", current_price

    # 4. SMA(50) cross below
    if n >= 50:
        sma50 = _sma(close, 50)
        cross = detect_cross(close, sma50)
        if side == "BUY" and cross == "below":
            return True, "sma50_cross", current_price
        if side == "SELL" and cross == "above":
            return True, "sma50_cross", current_price

    # 5. RSI overbought / oversold
    if n >= 30:
        rsi_series = _rsi(close, 14)
        rsi_raw = rsi_series.iloc[-1]
        if pd.notna(rsi_raw):
            rsi_val = float(rsi_raw)
            if side == "BUY" and rsi_val > 70:
                return True, "rsi_overbought", current_price
            if side == "SELL" and rsi_val < 30:
                return True, "rsi_oversold", current_price

    # 6. MACD histogram zero-cross
    if n >= 35:
        _, _, hist = _macd(close)
        hist_clean = hist.dropna()
        if len(hist_clean) >= 2:
            prev_h = float(hist_clean.iloc[-2])
            curr_h = float(hist_clean.iloc[-1])
            if side == "BUY" and prev_h >= 0 and curr_h < 0:
                return True, "macd_cross", current_price
            if side == "SELL" and prev_h <= 0 and curr_h > 0:
                return True, "macd_cross", current_price

    return False, "", 0.0


# ---------------------------------------------------------------------------
# Metrics computation
# ---------------------------------------------------------------------------

def _compute_metrics(
    trades: list[dict[str, Any]],
    equity_curve: list[dict[str, Any]],
    initial_capital: float,
    total_bars: int,
    interval: str = "1d",
) -> BacktestMetrics:
    """Compute all performance metrics from trade list and equity curve.

    Sharpe and Sortino annualization use ``_periods_per_year(interval)``
    instead of a hard-coded ``sqrt(252)`` — the old constant inflated
    intraday backtests (~sqrt(6.5)x for 1h, ~sqrt(78)x for 5m) and
    deflated weekly/monthly ones.
    """
    final_equity = equity_curve[-1]["equity"] if equity_curve else initial_capital

    # -- Total return --
    total_return_pct = ((final_equity - initial_capital) / initial_capital) * 100

    # -- Trading days for annualization --
    # Use actual calendar days between first and last bar (works for any interval),
    # then scale to trading-year equivalent at 252 days/year.
    if len(equity_curve) >= 2:
        first_ts = equity_curve[0]["time"]
        last_ts = equity_curve[-1]["time"]
        calendar_days = (last_ts - first_ts) / 86400
        trading_days = max(calendar_days * (252 / 365), 1)
    else:
        trading_days = 1

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

    # -- Sharpe (interval-aware annualization) --
    annualization = float(np.sqrt(_periods_per_year(interval)))
    if np.std(dr) > 0:
        sharpe_ratio = float(np.mean(dr) / np.std(dr) * annualization)
    else:
        sharpe_ratio = 0.0

    # -- Sortino (interval-aware annualization) --
    neg_returns = dr[dr < 0]
    if len(neg_returns) > 0 and np.std(neg_returns) > 0:
        sortino_ratio = float(np.mean(dr) / np.std(neg_returns) * annualization)
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
    exit_mode: str = "simple",
    atr_stop_mult: float = 0.0,
    atr_trail_mult: float = 0.0,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict:
    """
    Run an event-driven, bar-by-bar backtest.

    All signals are evaluated at bar close. SL/TP detection uses the
    current bar's low/high; fills happen at the detected price (or
    gap-open if the gap is worse for SL / better for TP).
    Entry fills at current bar's close price.

    Args:
        exit_mode: ``"simple"`` for percentage SL/TP (default), or
                   ``"atr_trail"`` for ATR-based exits matching the live bot.
        atr_stop_mult: ATR multiplier for hard stop (0 = use config default).
        atr_trail_mult: ATR multiplier for trailing stop (0 = use config default).
        start_date: ISO date string for custom date range (overrides period).
        end_date: ISO date string for custom date range end.
    """
    # Resolve ATR multipliers
    _atr_stop_m = atr_stop_mult if atr_stop_mult > 0 else cfg.ATR_STOP_MULT
    _atr_trail_m = atr_trail_mult if atr_trail_mult > 0 else cfg.ATR_TRAIL_MULT

    # Batch 9: clear the rule-engine indicator cache at the start of every
    # backtest run. The cache key is (cache_scope, len, last_time, indicator,
    # params); empty cache_scope (used by evaluate_conditions) can collide
    # across tests/backtests run in the same process, causing the second
    # call to see stale series from the first. The rule engine already
    # clears at the top of evaluate_all; mirror it here so backtests are
    # self-isolated.
    clear_indicator_cache()

    # -- Fetch historical data (in thread to avoid blocking event loop) --
    # auto_adjust=True is explicit so the adjusted close pairs with adjusted
    # OHLC consistently. Before Batch 7 the default was version-dependent,
    # which let bars on split days mix adjusted close with raw high/low and
    # produced phantom stop-outs.
    def _fetch() -> Any:
        if start_date:
            return yf.Ticker(symbol).history(
                start=start_date,
                end=end_date or None,
                interval=interval,
                auto_adjust=True,
            )
        return yf.Ticker(symbol).history(period=period, interval=interval, auto_adjust=True)

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

    # ATR-trail state (only used when exit_mode == "atr_trail")
    hard_stop_price = 0.0
    high_watermark = 0.0

    just_exited = False  # prevents same-bar re-entry after an exit
    trades: list[dict] = []
    equity_curve: list[dict] = []
    buy_hold_curve: list[dict] = []
    running_peak = initial_capital

    start_close = df.at[warmup, "close"]

    # Batch 7: no-look-ahead staging. A signal that fires at bar i's close
    # cannot fill at bar i's close — there is no time left to send the
    # order. The fill is at bar i+1's open. SL/TP exits are intra-bar
    # (price hit during the bar) and remain unchanged.
    pending_signal_entry: bool = False
    pending_signal_exit: bool = False

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

        just_exited = False
        exit_price = 0.0
        exit_reason = ""

        # ── Stage-1: execute any signal staged on bar i-1 at THIS bar's open ──
        # Signal-based fills happen at next-bar open (no look-ahead). SL/TP
        # checks remain intra-bar below.
        if pending_signal_exit and position_qty > 0:
            exit_price = current_open
            exit_reason = "signal"
            pending_signal_exit = False

        # ── Stage-2: intra-bar SL/TP checks (only if no staged signal exit) ──
        if position_qty > 0 and not exit_reason:
            # Update watermark for ATR trail mode
            if exit_mode == "atr_trail" and current_close > high_watermark:
                high_watermark = current_close

            if exit_mode == "atr_trail":
                # ATR-based exit checks (mirrors live bot position_tracker).
                # Note: this path still uses current_close — ATR-trail exits
                # are not signal-based in the same sense; they react to
                # intra-bar price levels. Future iteration can stage these too.
                should_exit, reason, _ep = _check_atr_exits(
                    df_slice, current_close, entry_price,
                    hard_stop_price, high_watermark, _atr_trail_m,
                )
                if should_exit:
                    exit_price = current_close
                    exit_reason = reason
            else:
                if stop_loss_pct > 0 and sl_price > 0:
                    if current_low <= sl_price:
                        exit_price = min(sl_price, current_open)
                        exit_reason = "stop_loss"
                if not exit_reason and take_profit_pct > 0 and tp_price > 0:
                    if current_high >= tp_price:
                        exit_price = max(tp_price, current_open)
                        exit_reason = "take_profit"

        # ── Stage-3: execute any exit booked above ──
        if exit_reason and position_qty > 0:
            proceeds = position_qty * exit_price - commission
            pnl = (exit_price - entry_price) * position_qty - 2 * commission
            pnl_pct = ((exit_price - entry_price) / entry_price) * 100 if entry_price > 0 else 0.0

            cash += proceeds

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
            hard_stop_price = 0.0
            high_watermark = 0.0
            just_exited = True

        # ── Stage-4: execute any signal-staged entry at THIS bar's open ──
        # Same no-look-ahead invariant: a signal from bar i-1 fills at bar i open.
        if pending_signal_entry and position_qty == 0 and not just_exited:
            pending_signal_entry = False
            available = cash * (position_size_pct / 100)
            qty = math.floor(available / current_open) if current_open > 0 else 0
            if qty > 0:
                cost = qty * current_open + commission
                cash -= cost
                position_qty = qty
                entry_price = current_open
                entry_bar = i
                entry_time = bar_time

                if exit_mode == "atr_trail":
                    # Batch 8: compute ATR from data through bar i-1 only.
                    # The pending entry was signaled on bar i-1 and fills at
                    # bar i open — bar i's high/low/close are unknown at the
                    # open and including them would leak future information
                    # into the hard-stop calc (the same look-ahead bug Batch 7
                    # was supposed to close, but missed inside the entry path).
                    atr_window = df.iloc[: i]  # exclusive of fill bar
                    atr_at_entry = 0.0
                    if len(atr_window) >= 14:
                        atr_s = _atr(atr_window["high"], atr_window["low"],
                                     atr_window["close"], 14)
                        atr_raw = atr_s.iloc[-1]
                        if pd.notna(atr_raw):
                            atr_at_entry = float(atr_raw)
                    hard_stop_price = (
                        entry_price - _atr_stop_m * atr_at_entry
                        if atr_at_entry > 0
                        else entry_price * 0.97
                    )
                    high_watermark = entry_price
                else:
                    # Simple percentage SL/TP
                    if stop_loss_pct > 0:
                        sl_price = entry_price * (1 - stop_loss_pct / 100)
                    if take_profit_pct > 0:
                        tp_price = entry_price * (1 + take_profit_pct / 100)

        # ── Stage-5: end-of-bar signal evaluation for NEXT-bar fill ──
        # Only stage if there IS a next bar; the final bar has no fill window.
        if i + 1 < total_bars:
            if position_qty > 0 and exit_conditions and not exit_reason:
                # Evaluate signal exits using data up to this bar (no look-ahead).
                if evaluate_conditions(exit_conditions, df_slice, condition_logic):
                    pending_signal_exit = True
            elif position_qty == 0 and entry_conditions and not just_exited:
                if evaluate_conditions(entry_conditions, df_slice, condition_logic):
                    pending_signal_entry = True

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
    metrics = _compute_metrics(trades, equity_curve, initial_capital, total_bars, interval=interval)

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
        "exit_mode": exit_mode,
        "atr_stop_mult": _atr_stop_m if exit_mode == "atr_trail" else 0.0,
        "atr_trail_mult": _atr_trail_m if exit_mode == "atr_trail" else 0.0,
        # Batch 7: stamp every saved backtest with the engine version so the
        # UI can visually distinguish v1 (legacy, optimistic) from v2
        # (no-look-ahead, correct slippage sign, interval-aware Sharpe).
        "engine_version": BACKTEST_ENGINE_VERSION,
    }

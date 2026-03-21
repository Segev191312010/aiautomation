"""
Position tracker — manages open positions with ATR-based stop-loss and MA/indicator exit logic.

Exit conditions (OR logic — first trigger closes the position):
  1. Hard stop   : price ≤ entry_price - ATR_STOP_MULT × ATR(14)_at_entry  (never moves)
  2. Trail stop  : price ≤ high_watermark - ATR_TRAIL_MULT × ATR(14)_current
  3. EMA(21) cross : price crossed below EMA(21)  (BUY positions)
  4. SMA(50) cross : price crossed below SMA(50)  (BUY positions)
  5. RSI >70       : overbought momentum exhaustion (BUY positions)
  6. MACD hist     : histogram crossed below zero  (BUY positions)
  (Conditions 3-6 are direction-reversed for SELL/short positions.)
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import pandas as pd

from config import cfg
from database import delete_open_position, save_open_position
from indicators import _atr, _ema, _macd, _rsi, _sma, detect_cross
from models import OpenPosition, Trade

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

async def register_position(
    trade: Trade,
    df: pd.DataFrame,
    rule_name: str,
    user_id: str = "demo",
) -> OpenPosition:
    """
    Create and persist an OpenPosition after a fill.

    Args:
        trade:     Filled Trade record (fill_price must be set).
        df:        OHLCV DataFrame used to compute ATR(14) at entry.
        rule_name: Human-readable rule name stored for display.
        user_id:   Owner of the position.

    Returns:
        The persisted OpenPosition.
    """
    entry_price = float(trade.fill_price or 0.0)
    if entry_price <= 0:
        raise ValueError(f"Cannot register position for {trade.symbol}: fill_price={trade.fill_price}")

    # ATR(14) at entry — needs at least 14 bars
    atr_val = 0.0
    if df is not None and len(df) >= 14:
        atr_series = _atr(df["high"], df["low"], df["close"], 14)
        atr_raw = atr_series.iloc[-1]
        if pd.notna(atr_raw):
            atr_val = float(atr_raw)

    # Use AI-optimized exit params if available, else config defaults
    from ai_params import ai_params
    exit_p = ai_params.get_exit_params(trade.symbol.upper())
    atr_stop_mult = exit_p["atr_stop_mult"]
    atr_trail_mult = exit_p["atr_trail_mult"]

    hard_stop = entry_price - atr_stop_mult * atr_val if atr_val > 0 else entry_price * 0.97

    pos = OpenPosition(
        id=trade.id,
        symbol=trade.symbol.upper(),
        side=trade.action,
        quantity=float(trade.quantity),
        entry_price=entry_price,
        entry_time=trade.timestamp,
        atr_at_entry=round(atr_val, 6),
        hard_stop_price=round(hard_stop, 4),
        atr_stop_mult=atr_stop_mult,
        atr_trail_mult=atr_trail_mult,
        high_watermark=entry_price,
        rule_id=trade.rule_id,
        rule_name=rule_name,
        user_id=user_id,
    )
    await save_open_position(pos, user_id=user_id)
    log.info(
        "Position registered: %s %s @ %.4f  hard_stop=%.4f  atr=%.4f",
        pos.side, pos.symbol, entry_price, hard_stop, atr_val,
    )
    return pos


# ---------------------------------------------------------------------------
# Trail stop calculation
# ---------------------------------------------------------------------------

def compute_trail_stop(pos: OpenPosition, current_atr: float) -> float:
    """
    Compute the current trailing stop price.

    For BUY: watermark - atr_trail_mult × current_atr
    For SELL: watermark + atr_trail_mult × current_atr

    Returns raw trail stop; effective stop = max(hard_stop, trail_stop) for BUY.
    """
    if pos.side == "BUY":
        return round(pos.high_watermark - pos.atr_trail_mult * current_atr, 4)
    return round(pos.high_watermark + pos.atr_trail_mult * current_atr, 4)


# ---------------------------------------------------------------------------
# Exit condition checks
# ---------------------------------------------------------------------------

def check_exits(
    pos: OpenPosition,
    df: pd.DataFrame,
    current_price: float,
) -> tuple[bool, str]:
    """
    Evaluate all exit conditions for an open position.

    Conditions are checked in priority order; returns on the first match.

    Args:
        pos:           The tracked open position.
        df:            OHLCV DataFrame (at least 2 bars).
        current_price: Latest close price.

    Returns:
        (should_exit: bool, reason: str)
    """
    if df is None or len(df) < 2:
        return False, ""

    close = df["close"]

    # 1. Hard stop — ATR-based floor set at entry, never moves
    if pos.side == "BUY" and current_price <= pos.hard_stop_price:
        return True, f"Hard stop: {current_price:.2f} ≤ {pos.hard_stop_price:.2f}"
    if pos.side == "SELL" and current_price >= pos.hard_stop_price:
        return True, f"Hard stop: {current_price:.2f} ≥ {pos.hard_stop_price:.2f}"

    # 2. Trailing stop — recalculated each cycle with current ATR
    if len(df) >= 14:
        atr_series = _atr(df["high"], df["low"], close, 14)
        atr_raw = atr_series.iloc[-1]
        if pd.notna(atr_raw):
            current_atr = float(atr_raw)
            trail = compute_trail_stop(pos, current_atr)
            if pos.side == "BUY":
                effective = max(pos.hard_stop_price, trail)
                if current_price <= effective:
                    return True, (
                        f"Trail stop: {current_price:.2f} ≤ {effective:.2f} "
                        f"(watermark={pos.high_watermark:.2f})"
                    )
            else:
                effective = min(pos.hard_stop_price, trail)
                if current_price >= effective:
                    return True, (
                        f"Trail stop: {current_price:.2f} ≥ {effective:.2f} "
                        f"(watermark={pos.high_watermark:.2f})"
                    )

    # 3. EMA(21) cross — needs 21+ bars
    if len(df) >= 21:
        ema21 = _ema(close, 21)
        cross = detect_cross(close, ema21)
        if pos.side == "BUY" and cross == "below":
            return True, f"Price crossed below EMA(21): {current_price:.2f}"
        if pos.side == "SELL" and cross == "above":
            return True, f"Price crossed above EMA(21): {current_price:.2f}"

    # 4. SMA(50) cross — needs 50+ bars
    if len(df) >= 50:
        sma50 = _sma(close, 50)
        cross = detect_cross(close, sma50)
        if pos.side == "BUY" and cross == "below":
            return True, f"Price crossed below SMA(50): {current_price:.2f}"
        if pos.side == "SELL" and cross == "above":
            return True, f"Price crossed above SMA(50): {current_price:.2f}"

    # 5. RSI overbought / oversold — needs 30+ bars for reliable signal
    if len(df) >= 30:
        rsi_series = _rsi(close, 14)
        rsi_raw = rsi_series.iloc[-1]
        if pd.notna(rsi_raw):
            rsi_val = float(rsi_raw)
            if pos.side == "BUY" and rsi_val > 70:
                return True, f"RSI overbought: {rsi_val:.1f}"
            if pos.side == "SELL" and rsi_val < 30:
                return True, f"RSI oversold: {rsi_val:.1f}"

    # 6. MACD histogram zero-cross — needs 35+ bars
    if len(df) >= 35:
        _, _, hist = _macd(close)
        hist_clean = hist.dropna()
        if len(hist_clean) >= 2:
            prev_h = float(hist_clean.iloc[-2])
            curr_h = float(hist_clean.iloc[-1])
            if pos.side == "BUY" and prev_h >= 0 and curr_h < 0:
                return True, "MACD histogram crossed below zero"
            if pos.side == "SELL" and prev_h <= 0 and curr_h > 0:
                return True, "MACD histogram crossed above zero"

    return False, ""


# ---------------------------------------------------------------------------
# Watermark update
# ---------------------------------------------------------------------------

def update_watermarks(
    positions: list[OpenPosition],
    bars_by_symbol: dict[str, pd.DataFrame],
) -> list[OpenPosition]:
    """
    Raise (BUY) or lower (SELL) the high_watermark for positions whose
    current price has moved favourably since last check.

    Returns only positions that were actually updated — caller persists them.
    """
    updated: list[OpenPosition] = []
    for pos in positions:
        df = bars_by_symbol.get(pos.symbol.upper())
        if df is None or len(df) == 0:
            continue
        price = float(df["close"].iloc[-1])
        if pos.side == "BUY" and price > pos.high_watermark:
            updated.append(pos.model_copy(update={"high_watermark": price}))
        elif pos.side == "SELL" and price < pos.high_watermark:
            updated.append(pos.model_copy(update={"high_watermark": price}))
    return updated

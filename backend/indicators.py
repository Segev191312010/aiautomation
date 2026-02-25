"""
Technical indicator engine — pure pandas/numpy (no external TA library required).

Supported indicators:
  RSI, SMA, EMA, MACD, BBANDS, ATR, STOCH, PRICE

Cross detection:
  detect_cross(series_a, series_b) → "above" | "below" | None
  (checks whether series_a crossed series_b on the last bar)
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Low-level indicator functions (pandas/numpy only)
# ---------------------------------------------------------------------------

def _rsi(close: pd.Series, length: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=length - 1, min_periods=length).mean()
    avg_loss = loss.ewm(com=length - 1, min_periods=length).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def _sma(close: pd.Series, length: int = 20) -> pd.Series:
    return close.rolling(window=length).mean()


def _ema(close: pd.Series, length: int = 20) -> pd.Series:
    return close.ewm(span=length, adjust=False).mean()


def _macd(
    close: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Returns (macd_line, signal_line, histogram)."""
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def _bbands(
    close: pd.Series,
    length: int = 20,
    std: float = 2.0,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Returns (upper, mid, lower)."""
    mid = close.rolling(window=length).mean()
    dev = close.rolling(window=length).std(ddof=0)
    return mid + std * dev, mid, mid - std * dev


def _atr(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    length: int = 14,
) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return tr.ewm(com=length - 1, min_periods=length).mean()


def _stoch(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    k: int = 14,
    d: int = 3,
    smooth_k: int = 3,
) -> tuple[pd.Series, pd.Series]:
    """Returns (%K smoothed, %D)."""
    lowest_low = low.rolling(window=k).min()
    highest_high = high.rolling(window=k).max()
    k_raw = 100 * (close - lowest_low) / (highest_high - lowest_low)
    k_smooth = k_raw.rolling(window=smooth_k).mean()
    d_line = k_smooth.rolling(window=d).mean()
    return k_smooth, d_line


# ---------------------------------------------------------------------------
# Public API: calculate()
# ---------------------------------------------------------------------------

def calculate(df: pd.DataFrame, indicator: str, params: dict[str, Any]) -> pd.Series:
    """
    Calculate a technical indicator on OHLCV DataFrame.

    Args:
        df:         DataFrame with columns [time, open, high, low, close, volume]
        indicator:  One of RSI, SMA, EMA, MACD, BBANDS, ATR, STOCH, PRICE
        params:     Indicator parameters (e.g. {"length": 14})

    Returns:
        pd.Series of indicator values, aligned with df index.
        For multi-output indicators (MACD, BBANDS, STOCH), returns the primary signal line.
    """
    ind = indicator.upper()

    if ind == "PRICE":
        return df["close"]

    if ind == "RSI":
        return _rsi(df["close"], length=int(params.get("length", 14)))

    if ind == "SMA":
        return _sma(df["close"], length=int(params.get("length", 20)))

    if ind == "EMA":
        return _ema(df["close"], length=int(params.get("length", 20)))

    if ind == "MACD":
        macd_line, _, _ = _macd(
            df["close"],
            fast=int(params.get("fast", 12)),
            slow=int(params.get("slow", 26)),
            signal=int(params.get("signal", 9)),
        )
        return macd_line

    if ind == "BBANDS":
        upper, mid, lower = _bbands(
            df["close"],
            length=int(params.get("length", 20)),
            std=float(params.get("std", 2.0)),
        )
        band = str(params.get("band", "mid")).lower()
        return upper if band == "upper" else (lower if band == "lower" else mid)

    if ind == "ATR":
        return _atr(
            df["high"], df["low"], df["close"],
            length=int(params.get("length", 14)),
        )

    if ind == "STOCH":
        k_line, _ = _stoch(
            df["high"], df["low"], df["close"],
            k=int(params.get("k", 14)),
            d=int(params.get("d", 3)),
            smooth_k=int(params.get("smooth_k", 3)),
        )
        return k_line

    raise ValueError(f"Unknown indicator: {indicator}")


# ---------------------------------------------------------------------------
# Cross detection
# ---------------------------------------------------------------------------

def detect_cross(series_a: pd.Series, series_b: pd.Series) -> Optional[str]:
    """
    Detect if series_a crossed series_b on the most recent bar.

    Returns:
        "above"  — series_a crossed above series_b (was below, now above)
        "below"  — series_a crossed below series_b (was above, now below)
        None     — no cross on the last bar
    """
    a = series_a.dropna()
    b = series_b.dropna()

    common = a.index.intersection(b.index)
    if len(common) < 2:
        return None

    a = a.loc[common]
    b = b.loc[common]

    prev_a, curr_a = a.iloc[-2], a.iloc[-1]
    prev_b, curr_b = b.iloc[-2], b.iloc[-1]

    if prev_a <= prev_b and curr_a > curr_b:
        return "above"
    if prev_a >= prev_b and curr_a < curr_b:
        return "below"
    return None


# ---------------------------------------------------------------------------
# Helper: resolve a "value" from rule condition (numeric or special string)
# ---------------------------------------------------------------------------

def resolve_value(
    value: float | str,
    df: pd.DataFrame,
    indicator_cache: dict[str, pd.Series],
) -> pd.Series | float:
    """
    Convert a condition value to a comparable form.

    - If value is a number → return it as-is (scalar)
    - If value is "PRICE"  → return df["close"]
    - If value is "SMA_200" style → calculate that indicator and return its Series
    """
    if isinstance(value, (int, float)):
        return float(value)

    v = str(value).upper()

    if v == "PRICE":
        return df["close"]

    if "_" in v:
        parts = v.split("_", 1)
        ind_name = parts[0]
        param_val = parts[1]
        cache_key = v
        if cache_key not in indicator_cache:
            try:
                indicator_cache[cache_key] = calculate(df, ind_name, {"length": int(param_val)})
            except Exception:
                log.warning("Could not resolve value '%s'", value)
                return float("nan")
        return indicator_cache[cache_key]

    log.warning("Unrecognised condition value '%s'", value)
    return float("nan")


# ---------------------------------------------------------------------------
# Serialization helper
# ---------------------------------------------------------------------------

def series_to_json(series: pd.Series, df: pd.DataFrame) -> list[dict]:
    """
    Convert a pandas Series to [{time, value}, ...] for JSON response.

    Uses the 'time' column from df for timestamps.
    Drops NaN values (indicator warmup period).
    """
    result = []
    for idx, val in series.items():
        if pd.notna(val):
            t = df.at[idx, "time"] if "time" in df.columns else int(idx)
            result.append({"time": int(t), "value": round(float(val), 6)})
    return result

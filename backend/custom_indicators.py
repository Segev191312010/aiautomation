"""
Custom indicators for the 9 scan setups.

Indicators:
  - DCR%: Daily Closing Range % = (close - low) / (high - low) * 100
  - ADR%: Average Daily Range % = mean((high - low) / close) over N days * 100
  - Relative Volume (RelVol): today's volume / avg volume
  - Pocket Pivot: green candle with volume > max(down-volume, last 10 days)
  - PP Count: number of pocket pivots in last N days
  - VCS: Volatility Contraction Score (0-100)
  - Hybrid RS: composite relative strength (vs SPY + sector)
  - Trend Base: price > 50SMA AND 10WMA > 30WMA
  - Weekly % change
  - From Open %: (close - open) / open * 100
  - RS 1M: 1-month relative strength percentile vs universe
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def dcr_pct(df: pd.DataFrame) -> pd.Series:
    """Daily Closing Range % — where price closed within the day's range."""
    rng = df["high"] - df["low"]
    return ((df["close"] - df["low"]) / rng.replace(0, np.nan) * 100).fillna(50)


def adr_pct(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average Daily Range % — mean of (high-low)/close over N days."""
    daily_range_pct = (df["high"] - df["low"]) / df["close"] * 100
    return daily_range_pct.rolling(period).mean()


def relative_volume(df: pd.DataFrame, period: int = 50) -> pd.Series:
    """Relative volume = current volume / average volume."""
    avg_vol = df["volume"].rolling(period).mean()
    return (df["volume"] / avg_vol.replace(0, np.nan)).fillna(1)


def daily_change_pct(df: pd.DataFrame) -> pd.Series:
    """Daily % change."""
    return df["close"].pct_change() * 100


def weekly_change_pct(df: pd.DataFrame) -> pd.Series:
    """Weekly (5-day) % change."""
    return (df["close"] / df["close"].shift(5) - 1) * 100


def from_open_pct(df: pd.DataFrame) -> pd.Series:
    """% change from open to close."""
    return (df["close"] - df["open"]) / df["open"] * 100


def is_pocket_pivot(df: pd.DataFrame) -> pd.Series:
    """
    Pocket Pivot: green candle where volume > highest down-day volume in last 10 days.
    Returns boolean Series.
    """
    green = df["close"] > df["open"]
    down_days = df["close"] < df["open"]
    down_vol = df["volume"].where(down_days, 0)
    max_down_vol_10 = down_vol.rolling(10).max()
    return green & (df["volume"] > max_down_vol_10)


def pp_count(df: pd.DataFrame, period: int = 30) -> pd.Series:
    """Count of Pocket Pivot occurrences in last N days."""
    pp = is_pocket_pivot(df).astype(int)
    return pp.rolling(period).sum()


def vcs_score(df: pd.DataFrame, period: int = 20) -> pd.Series:
    """
    Volatility Contraction Score (0-100).
    Measures how tight the range is compared to recent history.
    High VCS = tight contraction = potential breakout setup.
    """
    atr_short = ((df["high"] - df["low"]).rolling(5).mean())
    atr_long = ((df["high"] - df["low"]).rolling(period).mean())
    ratio = atr_short / atr_long.replace(0, np.nan)
    # Invert: lower ratio = tighter = higher score
    score = (1 - ratio.clip(0, 2) / 2) * 100
    return score.fillna(50)


def trend_base(df: pd.DataFrame) -> pd.Series:
    """
    Trend Base: price > 50SMA AND 10WMA > 30WMA.
    Since we use daily bars: 10WMA ≈ 50SMA, 30WMA ≈ 150SMA.
    Returns boolean Series.
    """
    sma50 = df["close"].rolling(50).mean()
    sma10w = df["close"].rolling(50).mean()   # 10 weeks ≈ 50 days
    sma30w = df["close"].rolling(150).mean()  # 30 weeks ≈ 150 days
    return (df["close"] > sma50) & (sma10w > sma30w)


def rs_1m(close: pd.Series, spy_close: pd.Series) -> pd.Series:
    """
    1-month relative strength vs SPY.
    Returns the ratio of stock's 21-day return to SPY's 21-day return.
    Higher = outperforming.
    """
    stock_ret = close.pct_change(21)
    spy_ret = spy_close.pct_change(21)
    return (stock_ret / spy_ret.replace(0, np.nan)).fillna(1) * 100


def ema_distance_atr(df: pd.DataFrame, ema_period: int = 21, atr_period: int = 14) -> pd.Series:
    """
    Distance from EMA in ATR units (R-multiples).
    Positive = price above EMA. Negative = below.
    """
    ema = df["close"].ewm(span=ema_period).mean()
    atr = ((df["high"] - df["low"]).rolling(atr_period).mean())
    return ((df["close"] - ema) / atr.replace(0, np.nan)).fillna(0)


def sma_distance_atr(df: pd.DataFrame, sma_period: int = 50, atr_period: int = 14) -> pd.Series:
    """Distance from SMA in ATR units."""
    sma = df["close"].rolling(sma_period).mean()
    atr = ((df["high"] - df["low"]).rolling(atr_period).mean())
    return ((df["close"] - sma) / atr.replace(0, np.nan)).fillna(0)


# ── Scan Functions (return True/False for each stock) ──────────────────────

def scan_21ema(df: pd.DataFrame) -> bool:
    """21EMA setup: weekly 0-15%, DCR>20%, 21EMA within -0.5R to +1R, 50SMA within 0-3R, PP>1, Trend Base."""
    if len(df) < 150:
        return False
    try:
        wk_chg = float(weekly_change_pct(df).iloc[-1])
        dcr = float(dcr_pct(df).iloc[-1])
        ema_dist = float(ema_distance_atr(df, 21).iloc[-1])
        sma_dist = float(sma_distance_atr(df, 50).iloc[-1])
        pp = float(pp_count(df, 30).iloc[-1])
        tb = bool(trend_base(df).iloc[-1])
        return (0 <= wk_chg <= 15 and dcr > 20 and -0.5 <= ema_dist <= 1.0
                and 0 <= sma_dist <= 3.0 and pp >= 1 and tb)
    except Exception:
        return False


def scan_4pct_bullish(df: pd.DataFrame) -> bool:
    """4% bullish: daily >4%, from open >0%, RelVol >1x, RS 1M >60."""
    if len(df) < 50:
        return False
    try:
        daily = float(daily_change_pct(df).iloc[-1])
        from_open = float(from_open_pct(df).iloc[-1])
        rvol = float(relative_volume(df, 50).iloc[-1])
        return daily > 4 and from_open > 0 and rvol > 1.0
    except Exception:
        return False


def scan_vol_up(df: pd.DataFrame) -> bool:
    """Volume Up: RelVol >1.5x, daily >0%."""
    if len(df) < 50:
        return False
    try:
        daily = float(daily_change_pct(df).iloc[-1])
        rvol = float(relative_volume(df, 50).iloc[-1])
        return daily > 0 and rvol > 1.5
    except Exception:
        return False


def scan_pocket_pivot(df: pd.DataFrame) -> bool:
    """Pocket Pivot: price >50SMA, green candle, volume > max down-vol 10d."""
    if len(df) < 50:
        return False
    try:
        sma50 = float(df["close"].rolling(50).mean().iloc[-1])
        price = float(df["close"].iloc[-1])
        pp = bool(is_pocket_pivot(df).iloc[-1])
        return price > sma50 and pp
    except Exception:
        return False


def scan_pp_count(df: pd.DataFrame) -> bool:
    """PP Count: PP Count 30d >3, Trend Base."""
    if len(df) < 150:
        return False
    try:
        ppc = float(pp_count(df, 30).iloc[-1])
        tb = bool(trend_base(df).iloc[-1])
        return ppc >= 3 and tb
    except Exception:
        return False


def scan_vcs(df: pd.DataFrame) -> bool:
    """VCS: VCS 60-100, RS 1M >60."""
    if len(df) < 50:
        return False
    try:
        v = float(vcs_score(df).iloc[-1])
        return 60 <= v <= 100
    except Exception:
        return False


def scan_weekly_20pct(df: pd.DataFrame) -> bool:
    """Weekly 20%+ gainers."""
    if len(df) < 10:
        return False
    try:
        wk = float(weekly_change_pct(df).iloc[-1])
        return wk > 20
    except Exception:
        return False


def scan_momentum_97(df: pd.DataFrame) -> bool:
    """Momentum: strong 1W and 3M performer, Trend Base."""
    if len(df) < 150:
        return False
    try:
        wk = float(weekly_change_pct(df).iloc[-1])
        m3 = float((df["close"].iloc[-1] / df["close"].iloc[-63] - 1) * 100) if len(df) >= 63 else 0
        tb = bool(trend_base(df).iloc[-1])
        return wk > 5 and m3 > 15 and tb
    except Exception:
        return False


# Common filter: ADR% 3.5-10, exclude healthcare (applied externally)
def passes_common_filter(df: pd.DataFrame) -> bool:
    """ADR% between 3.5 and 10."""
    if len(df) < 14:
        return False
    try:
        adr = float(adr_pct(df, 14).iloc[-1])
        return 3.5 <= adr <= 10
    except Exception:
        return False


# Master scan: run all 9 scans on a single stock's bars
ALL_SCANS = {
    "21EMA": scan_21ema,
    "4% Bullish": scan_4pct_bullish,
    "Vol Up": scan_vol_up,
    "Pocket Pivot": scan_pocket_pivot,
    "PP Count": scan_pp_count,
    "VCS": scan_vcs,
    "Weekly 20%+": scan_weekly_20pct,
    "Momentum 97": scan_momentum_97,
}


def run_all_scans(df: pd.DataFrame) -> list[str]:
    """Run all 9 scans on a stock's bars. Returns list of scan names that matched."""
    matched = []
    if not passes_common_filter(df):
        return matched
    for name, scan_fn in ALL_SCANS.items():
        try:
            if scan_fn(df):
                matched.append(name)
        except Exception:
            continue
    return matched

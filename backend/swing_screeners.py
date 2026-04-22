"""
Swing Screener Dashboard — computation engine.

Implements:
  - Market breadth metrics (4 universes)
  - Guru screeners: Qullamaggie, Minervini, O'Neil (stub)
  - ATR Matrix for Sector SPDRs
  - 97 Club (multi-timeframe RS percentile)
  - Stockbee scans (9M movers, 20% weekly, 4% daily)
  - Weinstein stage classification
  - Relative trend strength grading

Architecture:
  - Uses screener.refresh_cache() to populate bar data (same yfinance pipeline)
  - Cross-sectional RS percentiles computed once per scan cycle
  - Module-level cache with per-section TTLs
  - All heavy computation wrapped in asyncio.to_thread() to avoid blocking
"""
from __future__ import annotations

import asyncio
import bisect
import datetime
import logging
import time
from typing import Any

import numpy as np
import pandas as pd

from indicators import _sma, _ema, _atr
from custom_indicators import (
    adr_pct, vcs_score, sma_distance_atr,
    daily_change_pct, weekly_change_pct,
)
from screener import load_universe, refresh_cache, _bar_cache, CacheKey
from models import (
    ATRMatrixRow, BreadthMetrics, BreadthRow, Club97Entry,
    GuruScreenerResult, StageDistribution,
    StockbeeMover, SwingDashboardResponse,
    TrendGradeDistribution, TrendGradeEntry,
)

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

INTERVAL = "1d"
PERIOD = "1y"

ATR_MATRIX_SYMBOLS: dict[str, str] = {
    "XLK": "Technology", "XLY": "Cons. Discretionary", "XLC": "Communication",
    "XLI": "Industrials", "XLF": "Financials", "XLB": "Materials",
    "XLP": "Cons. Staples", "XLRE": "Real Estate", "XLV": "Healthcare",
    "XLE": "Energy", "XLU": "Utilities", "RSP": "S&P 500 EW", "QQQE": "Nasdaq-100 EW",
}

# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

_cache: dict[str, tuple[float, Any]] = {}


def _cache_get(key: str, ttl: float) -> Any | None:
    entry = _cache.get(key)
    if entry is None:
        return None
    ts, val = entry
    if time.time() - ts > ttl:
        return None
    return val


def _cache_set(key: str, val: Any) -> None:
    _cache[key] = (time.time(), val)


# ---------------------------------------------------------------------------
# Universe helpers
# ---------------------------------------------------------------------------

def build_composite() -> list[str]:
    """SP500 + NASDAQ100 + DJIA = ~509 unique tickers."""
    sp = set(load_universe("sp500"))
    nq = set(load_universe("nasdaq100"))
    dj = set(load_universe("djia"))
    return sorted(sp | nq | dj)


def _get_bars(symbol: str) -> pd.DataFrame | None:
    """Get bars from the screener cache."""
    entry = _bar_cache.get((symbol, INTERVAL, PERIOD))
    return entry.df if entry is not None else None


# ---------------------------------------------------------------------------
# Percentile ranking utility (O(n log n) via bisect)
# ---------------------------------------------------------------------------

def _rank_percentiles(values: dict[str, float]) -> dict[str, float]:
    """Rank values as percentiles (0-100). Midpoint ranking via bisect."""
    if not values:
        return {}
    sorted_vals = sorted(values.values())
    n = len(sorted_vals)
    return {
        sym: round(((bisect.bisect_left(sorted_vals, val) + bisect.bisect_right(sorted_vals, val)) / 2 / n) * 100, 1)
        for sym, val in values.items()
    }


# ---------------------------------------------------------------------------
# Cross-sectional RS percentiles
# ---------------------------------------------------------------------------

def _compute_returns(df: pd.DataFrame) -> dict[str, float]:
    """Compute returns for multiple timeframes."""
    close = df["close"]
    n = len(close)
    result: dict[str, float] = {}
    for label, period in [("1d", 1), ("1w", 5), ("1m", 21), ("3m", 63), ("6m", 126), ("9m", 189), ("12m", 252)]:
        if n >= period + 1:
            prev = float(close.iloc[-(period + 1)])
            if prev > 0:
                result[label] = float(close.iloc[-1]) / prev - 1
    return result


def compute_rs_percentiles(
    symbols: list[str],
    timeframes: list[str],
) -> dict[str, dict[str, float]]:
    """Universe-wide RS percentile ranks. Returns {symbol: {tf: percentile}}."""
    all_returns: dict[str, dict[str, float]] = {}
    for sym in symbols:
        df = _get_bars(sym)
        if df is None or len(df) < 30:
            continue
        all_returns[sym] = _compute_returns(df)

    if not all_returns:
        return {}

    result: dict[str, dict[str, float]] = {}
    for tf in timeframes:
        tf_values = {s: rets[tf] for s, rets in all_returns.items() if tf in rets}
        ranked = _rank_percentiles(tf_values)
        for sym, pctile in ranked.items():
            result.setdefault(sym, {})[tf] = pctile

    return result


def compute_ibd_rs_ranks(symbols: list[str]) -> dict[str, float]:
    """IBD-style weighted RS: 40% 3mo + 20% 6mo + 20% 9mo + 20% 12mo."""
    raw: dict[str, float] = {}
    for sym in symbols:
        df = _get_bars(sym)
        if df is None or len(df) < 253:
            continue
        rets = _compute_returns(df)
        r3, r6, r9, r12 = rets.get("3m"), rets.get("6m"), rets.get("9m"), rets.get("12m")
        if any(v is None for v in [r3, r6, r9, r12]):
            continue
        raw[sym] = (r3 * 2 + r6 + r9 + r12) / 4  # type: ignore[operator]

    return _rank_percentiles(raw)


def compute_atr_rs(symbols: list[str]) -> dict[str, float]:
    """ATR RS percentile: rank each stock's ADR% across the universe."""
    adr_values: dict[str, float] = {}
    for sym in symbols:
        df = _get_bars(sym)
        if df is not None and len(df) >= 20:
            v = float(adr_pct(df, 14).iloc[-1])
            if not np.isnan(v):
                adr_values[sym] = v
    return _rank_percentiles(adr_values)


# ---------------------------------------------------------------------------
# ATR Matrix
# ---------------------------------------------------------------------------

def _compute_atr_matrix() -> list[ATRMatrixRow]:
    """ATR extension matrix for 13 sector SPDRs + benchmarks."""
    results: list[ATRMatrixRow] = []
    for sym, name in ATR_MATRIX_SYMBOLS.items():
        df = _get_bars(sym)
        if df is None or len(df) < 50:
            continue
        try:
            close = float(df["close"].iloc[-1])
            ema21 = float(_ema(df["close"], 21).iloc[-1])
            atr14 = float(_atr(df["high"], df["low"], df["close"], 14).iloc[-1])
            if np.isnan(ema21) or np.isnan(atr14) or atr14 <= 0:
                continue
            results.append(ATRMatrixRow(
                symbol=sym, name=name, close=round(close, 2),
                atr_pct=round((atr14 / close) * 100, 2),
                price_vs_21ema_atr=round((close - ema21) / atr14, 2),
                atr_14=round(atr14, 2),
            ))
        except Exception:
            log.warning("ATR matrix failed for %s", sym, exc_info=True)

    results.sort(key=lambda r: r.price_vs_21ema_atr, reverse=True)
    return results


# ---------------------------------------------------------------------------
# Stockbee Scans
# ---------------------------------------------------------------------------

def _run_stockbee(scan_name: str, symbols: list[str]) -> list[StockbeeMover]:
    results: list[StockbeeMover] = []
    for sym in symbols:
        df = _get_bars(sym)
        if df is None or len(df) < 50:
            continue
        try:
            close = float(df["close"].iloc[-1])
            vol = int(df["volume"].iloc[-1])
            avg_vol = int(df["volume"].rolling(50).mean().iloc[-1])
            chg_d = float(daily_change_pct(df).iloc[-1])

            if scan_name == "9m_movers":
                if vol < 9_000_000 or vol <= avg_vol:
                    continue
                chg = chg_d
            elif scan_name == "weekly_20pct":
                wk = float(weekly_change_pct(df).iloc[-1])
                if abs(wk) < 20:
                    continue
                chg = wk
            elif scan_name == "daily_4pct":
                if chg_d < 4.0:
                    continue
                chg = chg_d
            else:
                continue

            results.append(StockbeeMover(
                symbol=sym, price=round(close, 2), change_pct=round(chg, 2),
                volume=vol, avg_volume=avg_vol,
            ))
        except Exception:
            continue

    results.sort(key=lambda r: abs(r.change_pct), reverse=True)
    return results


# ---------------------------------------------------------------------------
# Qullamaggie Screener
# ---------------------------------------------------------------------------

def _scan_qullamaggie(
    symbols: list[str],
    rs_pctiles: dict[str, dict[str, float]],
    atr_rs_lookup: dict[str, float],
) -> list[GuruScreenerResult]:
    """RS >= 97 (any TF) + MA stack + ATR RS >= 50 + price in upper 50% of 20d range."""
    results: list[GuruScreenerResult] = []
    for sym in symbols:
        # Fast reject on RS
        sym_rs = rs_pctiles.get(sym, {})
        max_rs = max(sym_rs.values()) if sym_rs else 0
        if max_rs < 97:
            continue

        df = _get_bars(sym)
        if df is None or len(df) < 200:
            continue

        # MA stack: Price >= EMA10 >= SMA20 >= SMA50 >= SMA100 >= SMA200
        c = float(df["close"].iloc[-1])
        e10 = float(_ema(df["close"], 10).iloc[-1])
        s20 = float(_sma(df["close"], 20).iloc[-1])
        s50 = float(_sma(df["close"], 50).iloc[-1])
        s100 = float(_sma(df["close"], 100).iloc[-1])
        s200 = float(_sma(df["close"], 200).iloc[-1])
        if any(np.isnan(v) for v in [e10, s20, s50, s100, s200]):
            continue
        if not (c >= e10 >= s20 >= s50 >= s100 >= s200):
            continue

        # ATR RS >= 50
        if atr_rs_lookup.get(sym, 0) < 50:
            continue

        # Price in upper 50% of 20-day range
        h20 = float(df["high"].tail(20).max())
        l20 = float(df["low"].tail(20).min())
        p20d = ((c - l20) / (h20 - l20) * 100) if h20 != l20 else 50.0
        if p20d < 50:
            continue

        chg = float(daily_change_pct(df).iloc[-1])
        vol = int(df["volume"].iloc[-1])
        atr_ext = float(sma_distance_atr(df, 50, 14).iloc[-1])
        v = float(vcs_score(df).iloc[-1])
        best_tf = max(sym_rs, key=sym_rs.get) if sym_rs else "1m"  # type: ignore[arg-type]

        results.append(GuruScreenerResult(
            symbol=sym, price=round(c, 2), change_pct=round(chg, 2),
            volume=vol, rs_rank=round(max_rs, 1), vcs=round(v, 1),
            setup_notes=[
                f"RS {max_rs:.0f} ({best_tf})",
                f"MA stack aligned",
                f"{atr_ext:.1f}x ATR ext",
            ],
        ))

    results.sort(key=lambda r: r.rs_rank, reverse=True)
    return results[:50]


# ---------------------------------------------------------------------------
# Minervini Trend Template
# ---------------------------------------------------------------------------

def _scan_minervini(
    symbols: list[str],
    ibd_rs: dict[str, float],
) -> list[GuruScreenerResult]:
    """8-criteria Trend Template + green candle. Market cap not filtered (needs enrichment)."""
    results: list[GuruScreenerResult] = []
    for sym in symbols:
        df = _get_bars(sym)
        if df is None or len(df) < 253:
            continue

        close_s = df["close"]
        c = float(close_s.iloc[-1])
        o = float(df["open"].iloc[-1])
        c_prev = float(close_s.iloc[-2])

        sma50 = float(_sma(close_s, 50).iloc[-1])
        sma150 = float(_sma(close_s, 150).iloc[-1])
        sma200_s = _sma(close_s, 200)
        sma200 = float(sma200_s.iloc[-1])

        if any(np.isnan(v) for v in [sma50, sma150, sma200]):
            continue

        # Criteria 1-5: MA alignment
        if not (c > sma150 and c > sma200):
            continue
        if not (sma150 > sma200):
            continue
        sma200_past = float(sma200_s.iloc[-22])
        if np.isnan(sma200_past) or not (sma200 > sma200_past):
            continue
        if not (sma50 > sma150 and sma50 > sma200):
            continue
        if not (c > sma50):
            continue

        # Criteria 6-7: 52-week position
        low_52w = float(df["low"].tail(252).min())
        high_52w = float(df["high"].tail(252).max())
        if low_52w <= 0:
            continue
        pct_above_low = (c - low_52w) / low_52w * 100
        pct_below_high = (high_52w - c) / high_52w * 100
        if pct_above_low < 30 or pct_below_high > 25:
            continue

        # Criterion 8: RS rank >= 70
        rs = ibd_rs.get(sym, 0)
        if rs < 70:
            continue

        # Criterion 9: Green candle
        if not (c >= o and c >= c_prev):
            continue

        vol = int(df["volume"].iloc[-1])
        chg = float(daily_change_pct(df).iloc[-1])

        results.append(GuruScreenerResult(
            symbol=sym, price=round(c, 2), change_pct=round(chg, 2),
            volume=vol, rs_rank=round(rs, 1),
            setup_notes=[
                "8/8 template",
                f"{pct_above_low:.0f}% above 52W low",
                f"Within {pct_below_high:.0f}% of 52W high",
            ],
        ))

    results.sort(key=lambda r: r.rs_rank, reverse=True)
    return results[:50]


# ---------------------------------------------------------------------------
# 97 Club
# ---------------------------------------------------------------------------

def _compute_97_club(symbols: list[str]) -> list[Club97Entry]:
    """Top 3% on ALL THREE RS timeframes (day, week, month) vs SPY."""
    spy_df = _get_bars("SPY")
    if spy_df is None or len(spy_df) < 25:
        return []
    spy_rets = _compute_returns(spy_df)

    excess: dict[str, dict[str, float]] = {}
    for sym in symbols:
        df = _get_bars(sym)
        if df is None or len(df) < 25:
            continue
        rets = _compute_returns(df)
        sym_ex: dict[str, float] = {}
        for tf in ["1d", "1w", "1m"]:
            r, s = rets.get(tf), spy_rets.get(tf)
            if r is not None and s is not None:
                sym_ex[tf] = r - s
        if len(sym_ex) == 3:
            excess[sym] = sym_ex

    if not excess:
        return []

    # Rank per timeframe
    ranked: dict[str, dict[str, float]] = {}
    for tf in ["1d", "1w", "1m"]:
        tf_vals = {s: ex[tf] for s, ex in excess.items()}
        pctiles = _rank_percentiles(tf_vals)
        for sym, p in pctiles.items():
            ranked.setdefault(sym, {})[tf] = p

    results: list[Club97Entry] = []
    for sym, pcts in ranked.items():
        d, w, m = pcts.get("1d", 0), pcts.get("1w", 0), pcts.get("1m", 0)
        if d >= 97 and w >= 97 and m >= 97:
            df = _get_bars(sym)
            price = float(df["close"].iloc[-1]) if df is not None else 0
            results.append(Club97Entry(
                symbol=sym, price=round(price, 2),
                rs_day_pctile=d, rs_week_pctile=w, rs_month_pctile=m,
                is_tml=(d >= 99 and w >= 99),
            ))

    results.sort(key=lambda r: r.rs_day_pctile + r.rs_week_pctile + r.rs_month_pctile, reverse=True)
    return results


# ---------------------------------------------------------------------------
# Weinstein Stage Analysis
# ---------------------------------------------------------------------------

def _classify_stage(df: pd.DataFrame) -> int | None:
    """Classify into Weinstein Stage 1-4 using SMA150 (30-week MA)."""
    if len(df) < 200:
        return None
    close = float(df["close"].iloc[-1])
    sma150_s = _sma(df["close"], 150)
    sma150 = float(sma150_s.iloc[-1])
    sma150_10ago = float(sma150_s.iloc[-11])

    if np.isnan(sma150) or np.isnan(sma150_10ago) or sma150_10ago <= 0:
        return None

    slope = (sma150 - sma150_10ago) / sma150_10ago

    if close > sma150 and slope > 0.005:
        return 2
    if close < sma150 and slope < -0.005:
        return 4
    if abs(close - sma150) / sma150 < 0.05 and abs(slope) <= 0.005:
        return 3 if close > sma150 else 1
    return 2 if close > sma150 else 4


def _compute_stages(symbols: list[str]) -> StageDistribution:
    stages: dict[int, list[str]] = {1: [], 2: [], 3: [], 4: []}
    for sym in symbols:
        df = _get_bars(sym)
        if df is None:
            continue
        s = _classify_stage(df)
        if s is not None:
            stages[s].append(sym)

    return StageDistribution(
        stage_1=len(stages[1]), stage_2=len(stages[2]),
        stage_3=len(stages[3]), stage_4=len(stages[4]),
        stage_1_symbols=stages[1][:10], stage_2_symbols=stages[2][:10],
        stage_3_symbols=stages[3][:10], stage_4_symbols=stages[4][:10],
    )


# ---------------------------------------------------------------------------
# Breadth Metrics
# ---------------------------------------------------------------------------

def _compute_breadth(symbols: list[str]) -> dict[str, float]:
    up_d = dn_d = up_w = dn_w = up_m = dn_m = 0
    above_20 = above_50 = above_200 = 0
    new_high = new_low = total = 0

    for sym in symbols:
        df = _get_bars(sym)
        if df is None or len(df) < 22:
            continue
        total += 1
        c = float(df["close"].iloc[-1])
        close_s = df["close"]

        # Day/Week/Month
        if c >= float(close_s.iloc[-2]):
            up_d += 1
        else:
            dn_d += 1
        if len(close_s) >= 6 and c >= float(close_s.iloc[-6]):
            up_w += 1
        else:
            dn_w += 1
        if c >= float(close_s.iloc[-22]):
            up_m += 1
        else:
            dn_m += 1

        # SMA checks
        for sma_len, counter_name in [(20, "above_20"), (50, "above_50"), (200, "above_200")]:
            if len(close_s) >= sma_len:
                sma_val = float(_sma(close_s, sma_len).iloc[-1])
                if not np.isnan(sma_val) and c >= sma_val:
                    if counter_name == "above_20":
                        above_20 += 1
                    elif counter_name == "above_50":
                        above_50 += 1
                    else:
                        above_200 += 1

        # 20-day highs/lows
        if len(df) >= 20:
            if c >= float(df["high"].tail(20).max()):
                new_high += 1
            if c <= float(df["low"].tail(20).min()):
                new_low += 1

    t = max(total, 1)
    return {
        "up_d": up_d, "dn_d": dn_d, "ratio_d": round(up_d / max(dn_d, 1), 2),
        "up_w": up_w, "dn_w": dn_w, "ratio_w": round(up_w / max(dn_w, 1), 2),
        "up_m": up_m, "dn_m": dn_m, "ratio_m": round(up_m / max(dn_m, 1), 2),
        "pct_above_20": round(above_20 / t * 100, 1),
        "pct_above_50": round(above_50 / t * 100, 1),
        "pct_above_200": round(above_200 / t * 100, 1),
        "high_20": new_high, "low_20": new_low,
    }


def _build_breadth(universes: dict[str, list[str]]) -> BreadthMetrics:
    data: dict[str, dict[str, float]] = {}
    for uid, syms in universes.items():
        data[uid] = _compute_breadth(syms)

    row_defs = [
        ("Stocks Up (Day)", "up_d"), ("Stocks Down (Day)", "dn_d"), ("Up/Down Ratio (Day)", "ratio_d"),
        ("Stocks Up (Week)", "up_w"), ("Stocks Down (Week)", "dn_w"), ("Up/Down Ratio (Week)", "ratio_w"),
        ("Stocks Up (Month)", "up_m"), ("Stocks Down (Month)", "dn_m"), ("Up/Down Ratio (Month)", "ratio_m"),
        ("% Above SMA 20", "pct_above_20"), ("% Above SMA 50", "pct_above_50"),
        ("% Above SMA 200", "pct_above_200"),
        ("New 20-Day Highs", "high_20"), ("New 20-Day Lows", "low_20"),
    ]

    return BreadthMetrics(
        rows=[
            BreadthRow(
                label=label,
                nasdaq100=data.get("nasdaq100", {}).get(key, 0),
                sp500=data.get("sp500", {}).get(key, 0),
                composite=data.get("composite", {}).get(key, 0),
                billion_plus=data.get("composite", {}).get(key, 0),  # proxy until $1B+ filter built
            )
            for label, key in row_defs
        ],
        timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
    )


# ---------------------------------------------------------------------------
# Trend Grades
# ---------------------------------------------------------------------------

def _compute_grades(symbols: list[str], rs_pctiles: dict[str, dict[str, float]]) -> TrendGradeDistribution:
    grades: dict[str, int] = {}
    entries: list[TrendGradeEntry] = []

    for sym in symbols:
        df = _get_bars(sym)
        if df is None or len(df) < 200:
            continue
        pcts = rs_pctiles.get(sym, {})
        if not pcts:
            continue

        avg_rs = sum(pcts.values()) / len(pcts)

        # MA alignment bonus
        c = float(df["close"].iloc[-1])
        s50 = float(_sma(df["close"], 50).iloc[-1])
        s200 = float(_sma(df["close"], 200).iloc[-1])
        aligned = not np.isnan(s50) and not np.isnan(s200) and c > s50 > s200

        if avg_rs >= 95 and aligned:
            g = "A+"
        elif avg_rs >= 90 and aligned:
            g = "A"
        elif avg_rs >= 85:
            g = "A-"
        elif avg_rs >= 80:
            g = "B+"
        elif avg_rs >= 70:
            g = "B"
        elif avg_rs >= 60:
            g = "B-"
        elif avg_rs >= 50:
            g = "C+"
        elif avg_rs >= 40:
            g = "C"
        elif avg_rs >= 30:
            g = "C-"
        elif avg_rs >= 25:
            g = "D+"
        elif avg_rs >= 20:
            g = "D"
        elif avg_rs >= 15:
            g = "D-"
        elif avg_rs >= 10:
            g = "E+"
        elif avg_rs >= 5:
            g = "E"
        elif avg_rs >= 3:
            g = "E-"
        else:
            g = "F"

        grades[g] = grades.get(g, 0) + 1
        if g in ("A+", "A"):
            chg = float(daily_change_pct(df).iloc[-1])
            entries.append(TrendGradeEntry(
                symbol=sym, price=round(c, 2), change_pct=round(chg, 2),
                grade=g, rs_composite=round(avg_rs, 1),
            ))

    entries.sort(key=lambda x: x.rs_composite, reverse=True)
    return TrendGradeDistribution(grades=grades, top_graded=entries[:15])


# ---------------------------------------------------------------------------
# Master orchestrator (sync — run via asyncio.to_thread)
# ---------------------------------------------------------------------------

def _build_dashboard_sync(symbols: list[str]) -> SwingDashboardResponse:
    """Synchronous dashboard builder. Called via asyncio.to_thread()."""
    # Pre-compute cross-sectional metrics (used by multiple sections)
    rs_pctiles = compute_rs_percentiles(symbols, ["1w", "1m", "3m", "6m"])
    ibd_rs = compute_ibd_rs_ranks(symbols)
    atr_rs_lookup = compute_atr_rs(symbols)

    universes = {
        "nasdaq100": load_universe("nasdaq100"),
        "sp500": load_universe("sp500"),
        "composite": symbols,
    }

    return SwingDashboardResponse(
        breadth=_build_breadth(universes),
        guru_results={
            "qullamaggie": _scan_qullamaggie(symbols, rs_pctiles, atr_rs_lookup),
            "minervini": _scan_minervini(symbols, ibd_rs),
            "oneil": [],  # Stub — requires fundamental data (Phase 4)
        },
        atr_matrix=_compute_atr_matrix(),
        club97=_compute_97_club(symbols),
        stockbee={
            "9m_movers": _run_stockbee("9m_movers", symbols),
            "weekly_20pct": _run_stockbee("weekly_20pct", symbols),
            "daily_4pct": _run_stockbee("daily_4pct", symbols),
        },
        stages=_compute_stages(symbols),
        grades=_compute_grades(symbols, rs_pctiles),
    )


# ---------------------------------------------------------------------------
# Public async API (called by routes)
# ---------------------------------------------------------------------------

_compute_lock = asyncio.Lock()


async def _ensure_bars(symbols: list[str]) -> list[str]:
    """Build full symbol list and populate bar cache."""
    all_symbols = list(set(symbols) | set(ATR_MATRIX_SYMBOLS.keys()) | {"SPY"})
    await refresh_cache(all_symbols, INTERVAL, PERIOD)
    return symbols


async def fetch_and_compute_dashboard() -> SwingDashboardResponse:
    """Fetch bar data, then compute full dashboard. Non-blocking."""
    async with _compute_lock:
        cached = _cache_get("full_dashboard", 300)
        if cached is not None:
            return cached

        symbols = await _ensure_bars(build_composite())
        result = await asyncio.to_thread(_build_dashboard_sync, symbols)
        _cache_set("full_dashboard", result)
        return result


async def fetch_and_compute_section(section: str) -> Any:
    """Fetch data for a specific section. Non-blocking, per-section caching."""
    cache_key = f"section_{section}"
    cached = _cache_get(cache_key, 300)
    if cached is not None:
        return cached

    symbols = await _ensure_bars(build_composite())

    # All computation (including RS pre-computation) runs in thread pool
    def _compute() -> Any:
        if section == "breadth":
            return _build_breadth({
                "nasdaq100": load_universe("nasdaq100"),
                "sp500": load_universe("sp500"),
                "composite": symbols,
            })
        elif section == "atr_matrix":
            return _compute_atr_matrix()
        elif section == "club97":
            return _compute_97_club(symbols)
        elif section == "stages":
            return _compute_stages(symbols)
        elif section == "grades":
            rs = compute_rs_percentiles(symbols, ["1w", "1m", "3m"])
            return _compute_grades(symbols, rs)
        elif section.startswith("stockbee_"):
            return _run_stockbee(section.replace("stockbee_", ""), symbols)
        elif section == "guru_qullamaggie":
            rs = compute_rs_percentiles(symbols, ["1w", "1m", "3m", "6m"])
            atr_rs = compute_atr_rs(symbols)
            return _scan_qullamaggie(symbols, rs, atr_rs)
        elif section == "guru_minervini":
            ibd = compute_ibd_rs_ranks(symbols)
            return _scan_minervini(symbols, ibd)
        elif section == "guru_oneil":
            return []  # Stub — requires fundamental data (Phase 4)
        raise ValueError(f"Unknown section: {section}")

    result = await asyncio.to_thread(_compute)
    _cache_set(cache_key, result)
    return result

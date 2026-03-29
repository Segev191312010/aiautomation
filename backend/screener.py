"""
Stock screener engine — bulk scanning with cached bar data and concurrent fetching.

Cache: (symbol, interval, period) key, 15-min TTL, LRU eviction at 3000 entries.
Concurrency: asyncio.Semaphore(3), exponential backoff retry per batch.
"""
from __future__ import annotations

import asyncio
from collections import Counter
import functools
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any

import pandas as pd
import numpy as np
import yfinance as yf

from models import (
    FilterValue, ScanFilter, ScanRequest, ScanResponse, ScanResultRow,
    EnrichRequest, EnrichResult, ScreenerPreset,
)
from indicators import calculate as ind_calculate, detect_cross
from risk_manager import get_sector

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Universe loading
# ---------------------------------------------------------------------------

_UNIVERSE_DIR = os.path.join(os.path.dirname(__file__), "data", "universes")

_UNIVERSE_NAMES = {
    "sp500": "S&P 500",
    "nasdaq100": "NASDAQ 100",
    "etfs": "ETFs",
    "us_all": "All US Stocks",
}


def load_universe(universe_id: str) -> list[str]:
    path = os.path.join(_UNIVERSE_DIR, f"{universe_id}.json")
    if not os.path.isfile(path):
        return []
    with open(path, "r") as f:
        return json.load(f)


def list_universes() -> list[dict[str, Any]]:
    result = []
    for uid, name in _UNIVERSE_NAMES.items():
        symbols = load_universe(uid)
        result.append({"id": uid, "name": name, "count": len(symbols)})
    return result


# ---------------------------------------------------------------------------
# Timeframe validation
# ---------------------------------------------------------------------------

VALID_COMBOS: dict[str, list[str]] = {
    "1m":  ["1d", "5d", "7d"],
    "5m":  ["1d", "5d", "1mo"],
    "15m": ["1d", "5d", "1mo"],
    "1h":  ["5d", "1mo", "3mo", "6mo"],
    "1d":  ["1mo", "3mo", "6mo", "1y", "2y", "5y", "max"],
    "1wk": ["3mo", "6mo", "1y", "2y", "5y", "max"],
}


def validate_timeframe(interval: str, period: str) -> bool:
    allowed = VALID_COMBOS.get(interval)
    if allowed is None:
        return False
    return period in allowed


# ---------------------------------------------------------------------------
# Bar cache
# ---------------------------------------------------------------------------

CacheKey = tuple[str, str, str]  # (symbol, interval, period)

CACHE_TTL = 900        # 15 minutes
MAX_CACHE_SIZE = 3000


@dataclass
class CacheEntry:
    df: pd.DataFrame
    fetched_at: float


_bar_cache: dict[CacheKey, CacheEntry] = {}
_cache_lock: asyncio.Lock | None = None


def _get_cache_lock() -> asyncio.Lock:
    global _cache_lock
    if _cache_lock is None:
        _cache_lock = asyncio.Lock()
    return _cache_lock


def _evict_if_full() -> None:
    """Drop oldest entries when cache exceeds MAX_CACHE_SIZE."""
    if len(_bar_cache) <= MAX_CACHE_SIZE:
        return
    entries = sorted(_bar_cache.items(), key=lambda x: x[1].fetched_at)
    to_remove = len(_bar_cache) - MAX_CACHE_SIZE
    for key, _ in entries[:to_remove]:
        del _bar_cache[key]


def _is_stale(entry: CacheEntry) -> bool:
    return (time.time() - entry.fetched_at) > CACHE_TTL


# ---------------------------------------------------------------------------
# Concurrent fetching
# ---------------------------------------------------------------------------

MAX_CONCURRENT_BATCHES = 3
BATCH_SIZE = 50
MAX_RETRIES = 3
BATCH_TIMEOUT = 30


def _chunk(lst: list, n: int) -> list[list]:
    return [lst[i:i + n] for i in range(0, len(lst), n)]


def _extract_symbol(raw_df: pd.DataFrame, sym: str, total_symbols: int) -> pd.DataFrame | None:
    """Extract a single symbol's OHLCV from a yfinance multi-download DataFrame."""
    try:
        if total_symbols == 1:
            df = raw_df.copy()
        else:
            if isinstance(raw_df.columns, pd.MultiIndex):
                level_vals = raw_df.columns.get_level_values(0)
                # yfinance normalizes BRK-B → BRK.B; try both forms
                candidates = [sym, sym.upper(), sym.replace("-", "."), sym.upper().replace("-", ".")]
                matched = None
                for candidate in candidates:
                    if candidate in level_vals:
                        matched = candidate
                        break
                if matched is not None:
                    df = raw_df[matched].copy()
                else:
                    return None
            else:
                return None

        # Normalize column names to lowercase
        df.columns = [c.lower() for c in df.columns]

        # Ensure required columns exist
        required = {"open", "high", "low", "close", "volume"}
        if not required.issubset(set(df.columns)):
            return None

        df = df.dropna(subset=["close"])
        if df.empty:
            return None

        # Add time column as unix timestamps (handle tz-aware DatetimeIndex)
        idx = df.index
        if hasattr(idx, 'tz') and idx.tz is not None:
            idx = idx.tz_convert("UTC").tz_localize(None)
        df.index = idx
        df["time"] = idx.astype(np.int64) // 10**9

        return df
    except Exception as e:
        log.debug("Failed to extract %s: %s", sym, e)
        return None


async def _fetch_batch(
    symbols: list[str],
    sem: asyncio.Semaphore,
    interval: str,
    period: str,
    loop: asyncio.AbstractEventLoop,
) -> list[str]:
    """Fetch a batch of symbols via yfinance. Returns list of skipped symbols."""
    skipped: list[str] = []

    async with sem:
        for attempt in range(MAX_RETRIES):
            try:
                raw_df = await asyncio.wait_for(
                    loop.run_in_executor(
                        None,
                        functools.partial(
                            yf.download,
                            tickers=" ".join(symbols),
                            period=period,
                            interval=interval,
                            group_by="ticker",
                            auto_adjust=False,
                            threads=False,
                            progress=False,
                        ),
                    ),
                    timeout=BATCH_TIMEOUT,
                )

                now = time.time()
                async with _get_cache_lock():
                    _evict_if_full()
                    for sym in symbols:
                        df_sym = _extract_symbol(raw_df, sym, len(symbols))
                        if df_sym is not None and not df_sym.empty:
                            _bar_cache[(sym, interval, period)] = CacheEntry(df_sym, now)
                        else:
                            skipped.append(sym)
                return skipped

            except Exception as e:
                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(2 ** attempt)
                else:
                    log.warning("Batch fetch failed after %d retries: %s", MAX_RETRIES, e)
                    return symbols  # all symbols in batch are skipped

    return skipped


async def refresh_cache(
    symbols: list[str],
    interval: str,
    period: str,
) -> list[str]:
    """Fetch bar data for symbols not in cache (or stale). Returns skipped symbols."""
    # Determine which symbols need fetching (hold lock to avoid TOCTOU race)
    to_fetch: list[str] = []
    async with _get_cache_lock():
        for sym in symbols:
            key = (sym, interval, period)
            entry = _bar_cache.get(key)
            if entry is None or _is_stale(entry):
                to_fetch.append(sym)

    if not to_fetch:
        return []

    loop = asyncio.get_running_loop()
    sem = asyncio.Semaphore(MAX_CONCURRENT_BATCHES)
    batches = _chunk(to_fetch, BATCH_SIZE)

    results = await asyncio.gather(
        *[_fetch_batch(b, sem, interval, period, loop) for b in batches],
        return_exceptions=True,
    )

    skipped: list[str] = []
    for r in results:
        if isinstance(r, Exception):
            log.warning("Batch error: %s", r)
        elif isinstance(r, list):
            skipped.extend(r)

    return skipped


# ---------------------------------------------------------------------------
# Indicator key generation
# ---------------------------------------------------------------------------

def make_indicator_key(indicator: str, params: dict[str, Any]) -> str:
    """Generate deterministic indicator key: RSI_14, SMA_50, MACD_12_26_9, etc."""
    ind = indicator.upper()

    if ind in ("PRICE", "VOLUME", "CHANGE_PCT"):
        return ind

    if ind == "MACD":
        fast = params.get("fast", 12)
        slow = params.get("slow", 26)
        signal = params.get("signal", 9)
        return f"MACD_{fast}_{slow}_{signal}"

    if ind == "STOCH":
        k = params.get("k", 14)
        d = params.get("d", 3)
        return f"STOCH_{k}_{d}"

    if ind == "BBANDS":
        length = params.get("length", 20)
        band = params.get("band", "mid")
        return f"BBANDS_{length}_{band}"

    # RSI, SMA, EMA, ATR — single length param
    length = params.get("length", 14 if ind in ("RSI", "ATR") else 20)
    return f"{ind}_{length}"


# ---------------------------------------------------------------------------
# Filter evaluation
# ---------------------------------------------------------------------------

def _compute_indicator_series(df: pd.DataFrame, indicator: str, params: dict[str, Any]) -> pd.Series:
    """Compute indicator series, handling VOLUME and CHANGE_PCT specially."""
    ind = indicator.upper()

    if ind == "VOLUME":
        length = params.get("length")
        if length:
            return df["volume"].rolling(window=int(length)).mean()
        return df["volume"].astype(float)

    if ind == "CHANGE_PCT":
        return df["close"].pct_change() * 100

    return ind_calculate(df, ind, params)


def compute_filter_value(df: pd.DataFrame, fv: FilterValue) -> float | pd.Series:
    """Resolve a FilterValue to a number or indicator series, with multiplier."""
    if fv.type == "number":
        assert fv.number is not None
        return fv.number * fv.multiplier

    # indicator type
    assert fv.indicator is not None
    series = _compute_indicator_series(df, fv.indicator, fv.params)
    return series * fv.multiplier


def evaluate_symbol(
    df: pd.DataFrame,
    filters: list[ScanFilter],
) -> dict[str, float] | None:
    """
    Evaluate all filters against a symbol's DataFrame.
    Returns indicator values dict if ALL filters pass, else None.
    """
    indicator_values: dict[str, float] = {}

    for filt in filters:
        try:
            lhs_series = _compute_indicator_series(df, filt.indicator, filt.params)
            rhs = compute_filter_value(df, filt.value)

            lhs_key = make_indicator_key(filt.indicator, filt.params)

            # Get the last value of LHS for result output
            lhs_clean = lhs_series.dropna()
            lhs_last = lhs_clean.iloc[-1] if not lhs_clean.empty else None
            if lhs_last is None:
                return None

            indicator_values[lhs_key] = round(float(lhs_last), 4)

            # Also record RHS if it's an indicator
            if filt.value.type == "indicator":
                assert filt.value.indicator is not None
                rhs_key = make_indicator_key(filt.value.indicator, filt.value.params)
                if isinstance(rhs, pd.Series):
                    rhs_clean = rhs.dropna()
                    rhs_last = rhs_clean.iloc[-1] if not rhs_clean.empty else None
                    if rhs_last is not None:
                        indicator_values[rhs_key] = round(float(rhs_last), 4)

            # Evaluate operator
            op = filt.operator

            if op in ("CROSSES_ABOVE", "CROSSES_BELOW"):
                if not isinstance(rhs, pd.Series):
                    # Can't do cross detection against a scalar — create constant series
                    rhs = pd.Series(rhs, index=df.index)

                cross = detect_cross(lhs_series, rhs)
                if op == "CROSSES_ABOVE" and cross != "above":
                    return None
                if op == "CROSSES_BELOW" and cross != "below":
                    return None
            else:
                # Comparison operators — use last values
                if isinstance(rhs, pd.Series):
                    rhs_val = rhs.dropna().iloc[-1] if not rhs.dropna().empty else None
                    if rhs_val is None:
                        return None
                    rhs_val = float(rhs_val)
                else:
                    rhs_val = float(rhs)

                lhs_val = float(lhs_last)

                if op == "GT" and not (lhs_val > rhs_val):
                    return None
                if op == "LT" and not (lhs_val < rhs_val):
                    return None
                if op == "GTE" and not (lhs_val >= rhs_val):
                    return None
                if op == "LTE" and not (lhs_val <= rhs_val):
                    return None

        except Exception as e:
            log.debug("Filter evaluation error: %s", e)
            return None

    return indicator_values


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _pct_move(current: float, previous: float | None) -> float:
    if previous in (None, 0):
        return 0.0
    return ((current - previous) / previous) * 100.0


def _series_last(series: pd.Series, fallback: float) -> float:
    clean = series.dropna()
    if clean.empty:
        return fallback
    return float(clean.iloc[-1])


def compute_screener_snapshot(df: pd.DataFrame) -> dict[str, Any]:
    """
    Derive a ranked setup snapshot from OHLCV bars.

    The goal is not just "did it match filters?" but "how compelling is the
    current long setup compared with other matches?".
    """
    close = df["close"].astype(float)
    volume = df["volume"].astype(float)
    price = float(close.iloc[-1])

    sma20 = _series_last(_compute_indicator_series(df, "SMA", {"length": 20}), price)
    sma50 = _series_last(_compute_indicator_series(df, "SMA", {"length": 50}), price)
    sma200 = _series_last(_compute_indicator_series(df, "SMA", {"length": 200}), price)
    rsi14 = _series_last(_compute_indicator_series(df, "RSI", {"length": 14}), 50.0)
    atr14 = _series_last(_compute_indicator_series(df, "ATR", {"length": 14}), 0.0)

    avg_volume20 = float(volume.tail(20).mean()) if len(volume) >= 20 else float(volume.mean() or 0.0)
    relative_volume = (float(volume.iloc[-1]) / avg_volume20) if avg_volume20 > 0 else 0.0
    dollar_volume = price * avg_volume20
    atr_pct = (atr14 / price * 100.0) if price > 0 else 0.0

    ret_5 = _pct_move(price, float(close.iloc[-6]) if len(close) >= 6 else None)
    ret_20 = _pct_move(price, float(close.iloc[-21]) if len(close) >= 21 else None)
    ret_60 = _pct_move(price, float(close.iloc[-61]) if len(close) >= 61 else None)
    high_20 = float(close.tail(20).max()) if len(close) >= 20 else price

    trend_aligned = price > sma20 > sma50 > sma200
    above_sma50 = price > sma50
    above_sma200 = price > sma200
    near_20d_high = price >= high_20 * 0.98 if high_20 > 0 else False
    pullback_zone = trend_aligned and abs(price - sma20) / max(sma20, 1e-9) <= 0.03 and 40 <= rsi14 <= 58
    reversal_setup = rsi14 < 38 and ret_5 > 0 and price > sma20
    breakout_setup = trend_aligned and near_20d_high and relative_volume >= 1.4 and ret_20 > 4

    trend_score = 0.0
    if trend_aligned:
        trend_score = 32.0
    elif above_sma50 and above_sma200:
        trend_score = 24.0
    elif above_sma200:
        trend_score = 16.0
    elif price > sma20:
        trend_score = 9.0

    momentum_score = _clamp((ret_20 * 1.1) + (ret_60 * 0.35) + 10.0, 0.0, 24.0)
    relative_volume_score = _clamp((relative_volume - 1.0) * 14.0, 0.0, 18.0)
    liquidity_score = 0.0
    if dollar_volume >= 1_000_000_000:
        liquidity_score = 12.0
    elif dollar_volume >= 300_000_000:
        liquidity_score = 9.0
    elif dollar_volume >= 100_000_000:
        liquidity_score = 6.0
    elif dollar_volume >= 25_000_000:
        liquidity_score = 3.0

    stability_score = 0.0
    if 1.5 <= atr_pct <= 6.0:
        stability_score = 8.0
    elif 0.8 <= atr_pct <= 8.0:
        stability_score = 5.0
    else:
        stability_score = 2.0

    rsi_score = 0.0
    if 48 <= rsi14 <= 68:
        rsi_score = 8.0
    elif 40 <= rsi14 <= 75:
        rsi_score = 5.0
    elif breakout_setup or reversal_setup:
        rsi_score = 4.0

    setup_bonus = 0.0
    setup = "mixed"
    if breakout_setup:
        setup = "breakout"
        setup_bonus = 10.0
    elif pullback_zone:
        setup = "pullback"
        setup_bonus = 8.0
    elif reversal_setup:
        setup = "reversal"
        setup_bonus = 6.0
    elif trend_aligned:
        setup = "trend"
        setup_bonus = 5.0

    screener_score = round(
        _clamp(
            trend_score
            + momentum_score
            + relative_volume_score
            + liquidity_score
            + stability_score
            + rsi_score
            + setup_bonus,
            0.0,
            100.0,
        ),
        1,
    )

    notes: list[str] = []
    if trend_aligned:
        notes.append("MA stack aligned")
    elif above_sma200:
        notes.append("Above 200-day trend")
    if near_20d_high:
        notes.append("Pressing 20-day highs")
    if relative_volume >= 1.5:
        notes.append(f"RVOL {relative_volume:.1f}x")
    if ret_20 >= 8:
        notes.append(f"20D momentum {ret_20:.1f}%")
    if pullback_zone:
        notes.append("Constructive pullback near trend support")
    if reversal_setup:
        notes.append("Oversold reversal improving")

    return {
        "screener_score": screener_score,
        "setup": setup,
        "relative_volume": round(relative_volume, 2),
        "momentum_20d": round(ret_20, 2),
        "trend_strength": round(trend_score, 1),
        "notes": notes[:3],
        "rsi14": round(rsi14, 2),
        "atr_pct": round(atr_pct, 2),
        "ret_5": round(ret_5, 2),
        "ret_60": round(ret_60, 2),
        "near_20d_high": near_20d_high,
        "dollar_volume": round(dollar_volume, 2),
    }


def _result_sort_key(row: ScanResultRow) -> tuple[float, float, float, float]:
    return (
        row.screener_score,
        row.relative_volume,
        row.momentum_20d,
        row.change_pct,
    )


async def run_scan(request: ScanRequest) -> ScanResponse:
    """Execute a full scan: resolve universe, fetch data, evaluate filters.

    Universe + symbols interaction:
    - universe="custom" → uses only the provided symbols list.
    - universe="sp500"|"nasdaq100"|"etfs" → loads that universe;
      if symbols is also provided, the result is the *intersection*
      (only universe members that also appear in the symbols list).
    """
    # Resolve symbols
    if request.universe == "custom":
        symbols = [s.upper() for s in (request.symbols or [])]
    else:
        symbols = load_universe(request.universe)
        if request.symbols:
            custom = {s.upper() for s in request.symbols}
            symbols = [s for s in symbols if s in custom]

    if not symbols:
        return ScanResponse(results=[], skipped_symbols=[])

    # Fetch bar data
    skipped = await refresh_cache(symbols, request.interval, request.period)
    skipped_set = set(skipped)

    # Evaluate each symbol
    results: list[ScanResultRow] = []

    for sym in symbols:
        if sym in skipped_set:
            continue

        key = (sym, request.interval, request.period)
        entry = _bar_cache.get(key)
        if entry is None or entry.df.empty:
            skipped_set.add(sym)
            continue

        df = entry.df
        indicator_values = evaluate_symbol(df, request.filters)
        if indicator_values is None:
            continue
        snapshot = compute_screener_snapshot(df)

        # Build result row
        close = df["close"]
        last_price = float(close.iloc[-1])
        prev_price = float(close.iloc[-2]) if len(close) >= 2 else last_price
        change_pct = ((last_price - prev_price) / prev_price * 100) if prev_price else 0.0
        raw_vol = df["volume"].iloc[-1]
        volume = int(raw_vol) if pd.notna(raw_vol) else 0

        results.append(ScanResultRow(
            symbol=sym,
            price=round(last_price, 4),
            change_pct=round(change_pct, 2),
            volume=volume,
            indicators=indicator_values,
            screener_score=snapshot["screener_score"],
            setup=snapshot["setup"],
            relative_volume=snapshot["relative_volume"],
            momentum_20d=snapshot["momentum_20d"],
            trend_strength=snapshot["trend_strength"],
            notes=snapshot["notes"],
        ))
    ranked_results = sorted(results, key=_result_sort_key, reverse=True)[:request.limit]
    return ScanResponse(results=ranked_results, skipped_symbols=sorted(skipped_set))


async def build_market_opportunity_snapshot(
    *,
    universe_ids: tuple[str, ...] = ("nasdaq100", "sp500", "etfs"),
    interval: str = "1d",
    period: str = "6mo",
    limit: int = 12,
) -> dict[str, Any]:
    """
    Build a ranked opportunity board for the AI optimizer.

    This is intentionally broader than a user-defined scan: it sweeps a liquid
    universe, scores every symbol, and returns the strongest long setups with
    concise rationale for Claude.
    """
    symbols: list[str] = []
    seen: set[str] = set()
    for universe_id in universe_ids:
        for symbol in load_universe(universe_id):
            sym = symbol.upper()
            if sym not in seen:
                seen.add(sym)
                symbols.append(sym)

    if not symbols:
        return {
            "available": False,
            "reason": "no_universe_symbols",
            "candidates": [],
            "setup_counts": {},
            "sector_counts": {},
        }

    skipped = await refresh_cache(symbols, interval, period)
    skipped_set = set(skipped)
    candidates: list[dict[str, Any]] = []
    setup_counts: Counter[str] = Counter()
    sector_counts: Counter[str] = Counter()

    for sym in symbols:
        if sym in skipped_set:
            continue
        entry = _bar_cache.get((sym, interval, period))
        if entry is None or entry.df.empty:
            continue

        df = entry.df
        close = df["close"].astype(float)
        if close.empty:
            continue

        last_price = float(close.iloc[-1])
        if last_price < 5:
            continue

        volume = df["volume"].astype(float)
        avg_volume20 = float(volume.tail(20).mean()) if len(volume) >= 20 else float(volume.mean() or 0.0)
        if avg_volume20 < 300_000:
            continue

        snapshot = compute_screener_snapshot(df)
        if snapshot["screener_score"] < 45:
            continue

        sector = get_sector(sym) or "Unknown"
        candidate = {
            "symbol": sym,
            "price": round(last_price, 2),
            "change_pct": round(_pct_move(last_price, float(close.iloc[-2]) if len(close) >= 2 else None), 2),
            "screener_score": snapshot["screener_score"],
            "setup": snapshot["setup"],
            "relative_volume": snapshot["relative_volume"],
            "momentum_20d": snapshot["momentum_20d"],
            "trend_strength": snapshot["trend_strength"],
            "sector": sector,
            "notes": snapshot["notes"],
        }
        candidates.append(candidate)
        setup_counts[snapshot["setup"]] += 1
        sector_counts[sector] += 1

    candidates.sort(
        key=lambda item: (
            float(item["screener_score"]),
            float(item["relative_volume"]),
            float(item["momentum_20d"]),
            float(item["change_pct"]),
        ),
        reverse=True,
    )

    def _top_for_setup(setup_name: str) -> list[dict[str, Any]]:
        return [candidate for candidate in candidates if candidate["setup"] == setup_name][:3]

    return {
        "available": bool(candidates),
        "universe_ids": list(universe_ids),
        "interval": interval,
        "period": period,
        "candidate_count": len(candidates),
        "skipped_symbols": len(skipped_set),
        "candidates": candidates[:limit],
        "top_breakouts": _top_for_setup("breakout"),
        "top_pullbacks": _top_for_setup("pullback"),
        "top_reversals": _top_for_setup("reversal"),
        "setup_counts": dict(setup_counts),
        "sector_counts": dict(sector_counts.most_common(6)),
    }


# ---------------------------------------------------------------------------
# Enrichment
# ---------------------------------------------------------------------------

async def enrich_symbols(symbols: list[str]) -> list[EnrichResult]:
    """Fetch name, sector, market_cap for a list of symbols via yfinance."""

    def _fetch_one(sym: str) -> EnrichResult | None:
        try:
            t = yf.Ticker(sym)
            info = t.info or {}
            return EnrichResult(
                symbol=sym,
                name=info.get("shortName") or info.get("longName") or sym,
                sector=info.get("sector"),
                market_cap=info.get("marketCap"),
            )
        except Exception:
            return EnrichResult(symbol=sym, name=sym)

    def _fetch_all():
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=min(len(symbols), 10)) as ex:
            return [r for r in ex.map(_fetch_one, symbols) if r is not None]

    return await asyncio.to_thread(_fetch_all)

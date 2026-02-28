"""
Stock screener engine — bulk scanning with cached bar data and concurrent fetching.

Cache: (symbol, interval, period) key, 15-min TTL, LRU eviction at 3000 entries.
Concurrency: asyncio.Semaphore(3), exponential backoff retry per batch.
"""
from __future__ import annotations

import asyncio
import functools
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any

import pandas as pd
import numpy as np

from models import (
    FilterValue, ScanFilter, ScanRequest, ScanResponse, ScanResultRow,
    EnrichRequest, EnrichResult, ScreenerPreset,
)
from indicators import calculate as ind_calculate, detect_cross

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Universe loading
# ---------------------------------------------------------------------------

_UNIVERSE_DIR = os.path.join(os.path.dirname(__file__), "data", "universes")

_UNIVERSE_NAMES = {
    "sp500": "S&P 500",
    "nasdaq100": "NASDAQ 100",
    "etfs": "ETFs",
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
_cache_lock = asyncio.Lock()


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
                if sym in raw_df.columns.get_level_values(0):
                    df = raw_df[sym].copy()
                elif sym.upper() in raw_df.columns.get_level_values(0):
                    df = raw_df[sym.upper()].copy()
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
    import yfinance as yf

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
                async with _cache_lock:
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
    # Determine which symbols need fetching
    to_fetch: list[str] = []
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
        return fv.number * fv.multiplier  # type: ignore[operator]

    # indicator type
    series = _compute_indicator_series(df, fv.indicator, fv.params)  # type: ignore[arg-type]
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
            lhs_last = lhs_series.dropna().iloc[-1] if not lhs_series.dropna().empty else None
            if lhs_last is None:
                return None

            indicator_values[lhs_key] = round(float(lhs_last), 4)

            # Also record RHS if it's an indicator
            if filt.value.type == "indicator":
                rhs_key = make_indicator_key(filt.value.indicator, filt.value.params)  # type: ignore[arg-type]
                if isinstance(rhs, pd.Series):
                    rhs_last = rhs.dropna().iloc[-1] if not rhs.dropna().empty else None
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


async def run_scan(request: ScanRequest) -> ScanResponse:
    """Execute a full scan: resolve universe, fetch data, evaluate filters."""
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

        # Build result row
        close = df["close"]
        last_price = float(close.iloc[-1])
        prev_price = float(close.iloc[-2]) if len(close) >= 2 else last_price
        change_pct = ((last_price - prev_price) / prev_price * 100) if prev_price else 0.0
        volume = int(df["volume"].iloc[-1])

        results.append(ScanResultRow(
            symbol=sym,
            price=round(last_price, 4),
            change_pct=round(change_pct, 2),
            volume=volume,
            indicators=indicator_values,
        ))

        if len(results) >= request.limit:
            break

    return ScanResponse(results=results, skipped_symbols=sorted(skipped_set))


# ---------------------------------------------------------------------------
# Enrichment
# ---------------------------------------------------------------------------

async def enrich_symbols(symbols: list[str]) -> list[EnrichResult]:
    """Fetch name, sector, market_cap for a list of symbols via yfinance."""
    import yfinance as yf

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

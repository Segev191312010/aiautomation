"""Yahoo Finance data helpers — shared by market routes, alerts, orders, simulation."""
from __future__ import annotations

import asyncio
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

log = logging.getLogger(__name__)

# Optional data health recording (set by main.py at startup)
_record_success = None
_record_failure = None


def set_health_callbacks(success_fn, failure_fn):
    global _record_success, _record_failure
    _record_success = success_fn
    _record_failure = failure_fn


async def yf_quotes(symbols_str: str, source: str = "watchlist_quotes") -> list[dict]:
    """Batch fetch quotes via yfinance."""
    import yfinance as yf

    syms = [s.strip() for s in symbols_str.split(",") if s.strip()]
    if not syms:
        return []
    started = time.perf_counter()

    def _one(sym: str):
        try:
            fi = yf.Ticker(sym).fast_info
            prev = getattr(fi, "previous_close", None) or 0
            price = getattr(fi, "last_price", None) or 0
            chg = price - prev
            chg_p = (chg / prev * 100) if prev else 0
            return {
                "symbol": sym,
                "price": round(price, 4),
                "change": round(chg, 4),
                "change_pct": round(chg_p, 2),
                "year_high": getattr(fi, "year_high", None),
                "year_low": getattr(fi, "year_low", None),
                "market_cap": getattr(fi, "market_cap", None),
                "avg_volume": getattr(fi, "three_month_average_volume", None),
                "last_update": datetime.now(timezone.utc).isoformat(),
            }
        except Exception as e:
            log.warning("yfinance error %s: %s", sym, e)
            return None

    def _all():
        with ThreadPoolExecutor(max_workers=min(len(syms), 10)) as ex:
            return [r for r in ex.map(_one, syms) if r is not None]

    try:
        quotes = await asyncio.to_thread(_all)
    except Exception as exc:
        duration_ms = (time.perf_counter() - started) * 1000.0
        if _record_failure:
            _record_failure(source, str(exc), duration_ms=duration_ms)
        raise

    duration_ms = (time.perf_counter() - started) * 1000.0
    if quotes:
        if _record_success:
            _record_success(source, count=len(quotes), duration_ms=duration_ms)
    else:
        if _record_failure:
            _record_failure(source, "empty quote response", duration_ms=duration_ms)
    return quotes


async def yf_bars(symbol: str, period: str, interval: str) -> list[dict]:
    """Fetch OHLCV bars via yfinance."""
    started = time.perf_counter()

    def _fetch():
        import yfinance as yf
        intraday = interval.endswith("m") or interval.endswith("h")
        df = yf.Ticker(symbol).history(period=period, interval=interval, prepost=intraday)
        if df.empty:
            return []
        df = df.dropna(subset=["Close"])
        df = df.fillna(0)
        if df.empty:
            return []
        return [
            {
                "time": int(ts.timestamp()),
                "open": round(float(row["Open"]), 4),
                "high": round(float(row["High"]), 4),
                "low": round(float(row["Low"]), 4),
                "close": round(float(row["Close"]), 4),
                "volume": int(row["Volume"] or 0),
            }
            for ts, row in df.iterrows()
        ]

    try:
        bars = await asyncio.to_thread(_fetch)
    except Exception as exc:
        duration_ms = (time.perf_counter() - started) * 1000.0
        if _record_failure:
            _record_failure("yahoo_bars", str(exc), duration_ms=duration_ms)
        raise

    duration_ms = (time.perf_counter() - started) * 1000.0
    if bars:
        if _record_success:
            _record_success("yahoo_bars", count=len(bars), duration_ms=duration_ms)
    else:
        if _record_failure:
            _record_failure("yahoo_bars", f"no bars for {symbol}:{period}:{interval}", duration_ms=duration_ms)
    return bars

"""
Market data service — historical bars and real-time tick subscriptions.
"""
from __future__ import annotations
import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Callable, Optional
import pandas as pd
from ib_insync import BarData, RealTimeBarList, Stock, Ticker
from ibkr_client import ibkr
from models import PriceBar

log = logging.getLogger(__name__)

# Mapping from user-friendly bar size strings to IBKR format
_BAR_SIZE_MAP = {
    "1m": "1 min",
    "5m": "5 mins",
    "15m": "15 mins",
    "30m": "30 mins",
    "1h": "1 hour",
    "4h": "4 hours",
    "1D": "1 day",
}

# Cache: symbol → DataFrame (refreshed each bot cycle)
_bar_cache: dict[str, pd.DataFrame] = {}
_BAR_CACHE_MAX = 2000  # safety cap to prevent unbounded growth

# Real-time tick callbacks: symbol → list of callbacks
_tick_callbacks: dict[str, list[Callable]] = {}
_ticker_map: dict[str, Ticker] = {}
_pending_ticker_handlers: dict[str, Callable] = {}

# Real-time 5-second bars: symbol → RealTimeBarList
_rt_bars_map: dict[str, RealTimeBarList] = {}
_rt_bar_callbacks: dict[str, list[Callable]] = {}


async def get_historical_bars(
    symbol: str,
    duration: str = "60 D",
    bar_size: str = "1D",
    use_cache: bool = True,
) -> pd.DataFrame:
    """
    Fetch historical OHLCV bars from IBKR.

    Returns a DataFrame with columns: time, open, high, low, close, volume.
    Caches the last result per symbol to avoid duplicate requests within a bot cycle.
    """
    cache_key = f"{symbol}:{bar_size}"
    if use_cache and cache_key in _bar_cache:
        return _bar_cache[cache_key]

    # Try IBKR first
    if ibkr.is_connected():
        try:
            contract = ibkr.make_stock_contract(symbol)
            await ibkr.ib.qualifyContractsAsync(contract)

            bars: list[BarData] = await ibkr.ib.reqHistoricalDataAsync(
                contract,
                endDateTime="",
                durationStr=duration,
                barSizeSetting=_BAR_SIZE_MAP.get(bar_size, bar_size),
                whatToShow="TRADES",
                useRTH=True,
                formatDate=1,
            )

            if bars:
                df = pd.DataFrame(
                    [
                        {
                            "time": b.date,
                            "open": b.open,
                            "high": b.high,
                            "low": b.low,
                            "close": b.close,
                            "volume": b.volume,
                        }
                        for b in bars
                    ]
                )
                df["time"] = pd.to_datetime(df["time"])
                df = df.sort_values("time").reset_index(drop=True)
                if len(_bar_cache) >= _BAR_CACHE_MAX:
                    _bar_cache.clear()  # full eviction when cap hit
                _bar_cache[cache_key] = df
                return df
        except Exception as exc:
            log.warning("IBKR historical bars failed for %s: %s", symbol, exc)

    # Fallback: Yahoo Finance
    try:
        import yfinance as yf

        # Map IBKR duration to yfinance period
        _dur_to_period = {"60 D": "3mo", "120 D": "6mo", "365 D": "1y", "730 D": "2y"}
        yf_period = _dur_to_period.get(duration, "3mo")
        # Map IBKR bar size to yfinance interval
        _bar_to_interval = {"1 min": "1m", "5 mins": "5m", "15 mins": "15m", "30 mins": "30m",
                            "1 hour": "1h", "4 hours": "4h", "1 day": "1d"}
        yf_interval = _bar_to_interval.get(_BAR_SIZE_MAP.get(bar_size, bar_size), "1d")

        def _fetch():
            return yf.Ticker(symbol).history(period=yf_period, interval=yf_interval)

        raw = await asyncio.to_thread(_fetch)
        if raw is not None and not raw.empty:
            df = pd.DataFrame({
                "time": raw.index,
                "open": raw["Open"].values,
                "high": raw["High"].values,
                "low": raw["Low"].values,
                "close": raw["Close"].values,
                "volume": raw["Volume"].values,
            })
            df["time"] = pd.to_datetime(df["time"])
            df = df.sort_values("time").reset_index(drop=True)
            _bar_cache[cache_key] = df
            return df
    except Exception as exc:
        log.warning("Yahoo bars fallback failed for %s: %s", symbol, exc)

    log.warning("No historical bars available for %s", symbol)
    return pd.DataFrame()


def clear_bar_cache() -> None:
    """Call at the start of each bot cycle to force fresh data."""
    _bar_cache.clear()


async def get_latest_price(symbol: str) -> Optional[float]:
    """Return the last traded price for a symbol (IBKR → Yahoo fallback)."""
    # Try IBKR first
    if ibkr.is_connected():
        try:
            contract = ibkr.make_stock_contract(symbol)
            await ibkr.ib.qualifyContractsAsync(contract)
            ticker = ibkr.ib.reqMktData(contract, "", False, False)
            await asyncio.sleep(2)  # allow tick to populate
            price = ticker.last or ticker.close or None
            ibkr.ib.cancelMktData(contract)
            if price is not None:
                return price
        except Exception as exc:
            log.warning("IBKR price fetch failed for %s: %s", symbol, exc)

    # Fallback: Yahoo Finance
    try:
        import yfinance as yf

        def _fetch():
            fi = yf.Ticker(symbol).fast_info
            return fi.get("lastPrice") or fi.get("last_price") or fi.get("previousClose")

        price = await asyncio.to_thread(_fetch)
        if price is not None and price > 0:
            return float(price)
    except Exception as exc:
        log.warning("Yahoo price fallback failed for %s: %s", symbol, exc)

    return None


async def subscribe_realtime(symbol: str, on_tick: Callable[[str, float], None]) -> bool:
    """Subscribe to real-time ticks for a symbol, calling on_tick(symbol, price) on each update."""
    symbol = symbol.upper()
    if symbol in _ticker_map:
        callbacks = _tick_callbacks.setdefault(symbol, [])
        if on_tick not in callbacks:
            callbacks.append(on_tick)
        return True

    if not ibkr.is_connected():
        return False

    contract = ibkr.make_stock_contract(symbol)
    await ibkr.ib.qualifyContractsAsync(contract)
    ticker = ibkr.ib.reqMktData(contract, "", False, False)
    _ticker_map[symbol] = ticker
    _tick_callbacks[symbol] = [on_tick]

    def _on_pending_tickers(tickers):
        for t in tickers:
            if t.contract.symbol == symbol:
                price = t.last or t.close
                if price:
                    for cb in _tick_callbacks.get(symbol, []):
                        cb(symbol, price)

    ibkr.ib.pendingTickersEvent += _on_pending_tickers
    _pending_ticker_handlers[symbol] = _on_pending_tickers
    log.info("Subscribed to real-time ticks for %s", symbol)
    return True


def unsubscribe_realtime(symbol: str) -> None:
    symbol = symbol.upper()
    handler = _pending_ticker_handlers.pop(symbol, None)
    if handler is not None:
        try:
            ibkr.ib.pendingTickersEvent -= handler
        except Exception:
            pass
    ticker = _ticker_map.pop(symbol, None)
    if ticker:
        try:
            ibkr.ib.cancelMktData(ticker.contract)
        except Exception:
            pass
    _tick_callbacks.pop(symbol, None)


async def subscribe_realtime_bars(symbol: str, on_bar: Callable[[dict], None]) -> bool:
    """
    Subscribe to 5-second OHLCV real-time bars via reqRealTimeBars.

    Calls on_bar({"time": unix_seconds, "open": …, "high": …, "low": …, "close": …, "volume": …})
    each time a new 5-second bar completes.  Returns True if subscribed successfully.
    """
    if not ibkr.is_connected():
        return False

    if symbol in _rt_bars_map:
        _rt_bar_callbacks.setdefault(symbol, []).append(on_bar)
        return True

    contract = ibkr.make_stock_contract(symbol)
    await ibkr.ib.qualifyContractsAsync(contract)
    bars: RealTimeBarList = ibkr.ib.reqRealTimeBars(contract, 5, "TRADES", False)
    _rt_bars_map[symbol] = bars
    _rt_bar_callbacks[symbol] = [on_bar]

    def _on_update(bars_list: RealTimeBarList, hasNewBar: bool) -> None:
        if not hasNewBar or not bars_list:
            return
        bar = bars_list[-1]
        # bar.time is a datetime object in ib_insync; convert to Unix seconds
        t = bar.time
        unix_secs = int(t.timestamp()) if hasattr(t, "timestamp") else int(t)
        data = {
            "time": unix_secs,
            "open": bar.open_,
            "high": bar.high,
            "low": bar.low,
            "close": bar.close,
            "volume": bar.volume,
        }
        for cb in _rt_bar_callbacks.get(symbol, []):
            cb(data)

    bars.updateEvent += _on_update
    log.info("Subscribed to real-time 5-second bars for %s", symbol)
    return True


def unsubscribe_realtime_bars(symbol: str) -> None:
    """Cancel the real-time bars subscription for a symbol."""
    bars = _rt_bars_map.pop(symbol, None)
    if bars:
        try:
            ibkr.ib.cancelRealTimeBars(bars)
        except Exception:
            pass
    _rt_bar_callbacks.pop(symbol, None)
    log.info("Unsubscribed from real-time bars for %s", symbol)

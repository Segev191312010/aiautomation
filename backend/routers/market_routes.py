"""Market data routes — /api/market/*, /api/yahoo/*, /api/watchlist"""
import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException

from auth import get_current_user
from config import cfg
from ibkr_client import ibkr
from market_data import get_historical_bars, get_latest_price, subscribe_realtime_bars, unsubscribe_realtime_bars
from yahoo_data import yf_quotes, yf_bars

log = logging.getLogger(__name__)

router = APIRouter(tags=["market"])

_active_rt_subs: set[str] = set()

_VALID_INTERVALS = {"1m", "2m", "5m", "15m", "30m", "60m", "90m", "1h", "1d", "5d", "1wk", "1mo"}
_UNSUPPORTED_INTERVALS = {"4h", "2h"}
_INTERVAL_MAX_DAYS = {
    "1m": 7, "2m": 60, "5m": 60, "15m": 60, "30m": 60,
    "60m": 730, "90m": 60, "1h": 730,
}


def _period_to_days(p: str) -> int:
    p = p.lower()
    if p.endswith("d"):
        return int(p[:-1])
    if p.endswith("mo"):
        return int(p[:-2]) * 30
    if p.endswith("y"):
        return int(p[:-1]) * 365
    return 9999


@router.get("/api/market/{symbol}/bars")
async def get_bars(symbol: str, bar_size: str = "1D", duration: str = "60 D"):
    if not ibkr.is_connected():
        raise HTTPException(503, "IBKR not connected")
    df = await get_historical_bars(symbol.upper(), duration=duration, bar_size=bar_size, use_cache=False)
    if df.empty:
        raise HTTPException(404, f"No bars found for {symbol}")
    return [
        {
            "time": int(row["time"].timestamp()),
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "volume": float(row["volume"]),
        }
        for _, row in df.iterrows()
    ]


@router.get("/api/market/{symbol}/price")
async def get_price(symbol: str):
    sym = symbol.upper()
    price = await get_latest_price(sym)
    if price is not None:
        return {"symbol": sym, "price": price}
    try:
        quotes = await yf_quotes(sym, source="price_fallback")
        if quotes and quotes[0].get("price"):
            return {"symbol": sym, "price": quotes[0]["price"], "source": "yahoo"}
    except Exception as exc:
        log.warning("Yahoo price fallback failed for %s: %s", sym, exc)
    raise HTTPException(503, "No market data available")


@router.post("/api/market/{symbol}/subscribe")
async def subscribe_market_bars(symbol: str, _user=Depends(get_current_user)):
    sym = symbol.upper()
    if sym in _active_rt_subs:
        return {"subscribed": True, "symbol": sym}

    async def _on_bar_broadcast(bar_data: dict) -> None:
        from runtime_state import get_ws_manager
        mgr = get_ws_manager()
        if mgr:
            await mgr.broadcast({"type": "bar", "symbol": sym, **bar_data})

    def _on_bar(bar_data: dict) -> None:
        asyncio.create_task(_on_bar_broadcast(bar_data))

    ok = await subscribe_realtime_bars(sym, _on_bar) if ibkr.is_connected() else False
    if ok:
        _active_rt_subs.add(sym)
    return {"subscribed": ok, "symbol": sym}


@router.post("/api/market/{symbol}/unsubscribe")
async def unsubscribe_market_bars(symbol: str, _user=Depends(get_current_user)):
    sym = symbol.upper()
    unsubscribe_realtime_bars(sym)
    _active_rt_subs.discard(sym)
    return {"subscribed": False, "symbol": sym}


@router.get("/api/watchlist")
async def get_watchlist_quotes(symbols: str = "BTC-USD,ETH-USD,AAPL,TSLA,SPY,QQQ,NVDA"):
    syms = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    if not syms:
        return []
    try:
        quotes = await yf_quotes(",".join(syms))
        if quotes:
            return quotes
    except Exception as exc:
        log.warning("Yahoo Finance failed: %s", exc)
    raise HTTPException(503, "No market data available")


@router.get("/api/yahoo/{symbol}/bars")
async def get_yahoo_bars(symbol: str, period: str = "5d", interval: str = "5m"):
    if interval in _UNSUPPORTED_INTERVALS:
        raise HTTPException(400, f"Interval '{interval}' is not supported by Yahoo Finance. Use 1h instead.")
    if interval not in _VALID_INTERVALS:
        raise HTTPException(400, f"Invalid interval '{interval}'. Valid: {sorted(_VALID_INTERVALS)}")
    max_days = _INTERVAL_MAX_DAYS.get(interval)
    if max_days is not None:
        req_days = _period_to_days(period)
        if req_days > max_days:
            raise HTTPException(400, f"{interval} interval requires period <= {max_days}d")
    try:
        bars = await yf_bars(symbol, period, interval)
        if bars:
            return bars
    except Exception as exc:
        log.warning("Yahoo bars failed for %s: %s", symbol, exc)
    raise HTTPException(404, f"No data for {symbol}")


@router.get("/api/market/{symbol}/indicators")
async def get_indicators(
    symbol: str, indicator: str, length: int = 0,
    period: str = "1y", interval: str = "1d",
):
    from indicators import calculate as ind_calculate, series_to_json
    try:
        bars = await yf_bars(symbol.upper(), period, interval)
    except Exception:
        raise HTTPException(503, "Failed to fetch data")

    if not bars:
        raise HTTPException(404, f"No data for {symbol}")

    import pandas as pd
    df = pd.DataFrame(bars)
    if "close" not in df.columns:
        raise HTTPException(400, "Data missing 'close' column")

    params = {"length": length} if length > 0 else {}
    ind = indicator.upper()

    try:
        series = ind_calculate(df, ind, params)
    except ValueError as e:
        raise HTTPException(400, str(e))

    return series_to_json(series, df)

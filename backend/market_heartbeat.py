"""
Market heartbeat loop — periodic Yahoo quote fetch + position broadcast.

Extracted from main.py. Called by lifespan startup/shutdown.
"""
from __future__ import annotations

import asyncio
import logging
import os

from config import cfg
from ibkr_client import ibkr
from ws_manager import _broadcast
from ws_quote_state import (
    _market_dynamic_symbols,
    _normalize_market_state,
    _ws_price_cache,
)
import time as _time

log = logging.getLogger(__name__)


def _env_int(name: str, fallback: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return fallback
    try:
        return int(raw)
    except ValueError:
        log.warning("Invalid %s=%r. Using %d.", name, raw, fallback)
        return fallback


_MARKET_HEARTBEAT_INTERVAL_SECONDS = max(5, _env_int("MARKET_HEARTBEAT_INTERVAL_SECONDS", 30))
_MARKET_HEARTBEAT_ENABLED = os.getenv("MARKET_HEARTBEAT_ENABLED", "true").lower() not in ("false", "0", "no")
_DEFAULT_WATCHLIST = "SPY,QQQ,IWM,DIA,XLF,XLE,XLK,XLV,XLU,AAPL,MSFT,NVDA,AMZN,GOOGL,META,TSLA"


def _split_symbols(raw: str) -> list[str]:
    return [s.strip().upper() for s in raw.split(",") if s.strip()]


def _core_heartbeat_symbols() -> list[str]:
    configured = _split_symbols(os.getenv("MARKET_HEARTBEAT_SYMBOLS", _DEFAULT_WATCHLIST))
    if configured:
        return configured
    return _split_symbols(_DEFAULT_WATCHLIST)


def _all_heartbeat_symbols() -> list[str]:
    merged = set(_core_heartbeat_symbols())
    merged.update(_market_dynamic_symbols)
    return sorted(merged)


def _track_heartbeat_symbols(symbols: list[str]) -> None:
    for sym in symbols:
        if sym:
            _market_dynamic_symbols.add(sym.upper())


def _untrack_heartbeat_symbols(symbols: list[str]) -> None:
    for sym in symbols:
        if sym:
            _market_dynamic_symbols.discard(sym.upper())


def _cache_prices_from_quotes(quotes: list[dict]) -> None:
    import ws_quote_state
    ts = _time.time()
    quote_ts = int(ts)
    updated = False
    for quote in quotes:
        sym = str(quote.get("symbol", "")).upper()
        price = quote.get("price")
        if not sym:
            continue
        if isinstance(price, (int, float)) and price > 0:
            state = _normalize_market_state(None, sym, quote_ts)
            _ws_price_cache[sym] = (round(float(price), 4), ts, quote_ts, state)
            updated = True
    if updated:
        ws_quote_state._ws_last_yahoo_quote_ts = ts


_position_push_counter = 0
_market_heartbeat_task: asyncio.Task | None = None


async def _market_heartbeat_loop() -> None:
    global _position_push_counter
    while True:
        await asyncio.sleep(_MARKET_HEARTBEAT_INTERVAL_SECONDS)
        symbols = _all_heartbeat_symbols()
        if not symbols:
            continue
        try:
            from yahoo_data import yf_quotes
            quotes = await yf_quotes(",".join(symbols), source="heartbeat_quotes")
            if quotes:
                _cache_prices_from_quotes(quotes)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.debug("Market heartbeat fetch failed: %s", exc)

        _position_push_counter += 1
        if _position_push_counter % 3 == 0 and ibkr.is_connected() and not cfg.SIM_MODE:
            try:
                positions = [p.model_dump() for p in await ibkr.get_positions()]
                acct = await ibkr.get_account_summary()
                await _broadcast({
                    "type": "positions_update",
                    "positions": positions,
                    "account": acct.model_dump(),
                })
            except Exception as exc:
                log.debug("Position broadcast failed: %s", exc)


async def _start_market_heartbeat() -> None:
    global _market_heartbeat_task
    if not _MARKET_HEARTBEAT_ENABLED:
        log.info("Market heartbeat disabled via MARKET_HEARTBEAT_ENABLED=false")
        return
    if _market_heartbeat_task and not _market_heartbeat_task.done():
        return
    _market_heartbeat_task = asyncio.create_task(_market_heartbeat_loop())
    log.info(
        "Market heartbeat started (interval=%ds, symbols=%d)",
        _MARKET_HEARTBEAT_INTERVAL_SECONDS,
        len(_all_heartbeat_symbols()),
    )


async def _stop_market_heartbeat() -> None:
    global _market_heartbeat_task
    if not _market_heartbeat_task:
        return
    _market_heartbeat_task.cancel()
    try:
        await _market_heartbeat_task
    except asyncio.CancelledError:
        pass
    _market_heartbeat_task = None
    log.info("Market heartbeat stopped")

"""
Shared live-quote fanout state for WebSocket streaming.

All mutable globals that track WS subscriptions, IBKR quotes, and Yahoo
price cache live here so that ws_data_feed.py, market_heartbeat.py, and
main.py can all share them without circular imports.
"""
from __future__ import annotations

import threading
import time as _time
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from config import cfg

# ---------------------------------------------------------------------------
# Module-level quote cache and subscription tracking
# ---------------------------------------------------------------------------

_ws_price_cache: dict[str, tuple[float, float, int, str]] = {}   # symbol -> (price, fetch_ts, quote_ts, market_state)
_ws_ibkr_quotes: dict[str, tuple[float, int, str]] = {}          # symbol -> (price, quote_ts, market_state)
_ws_symbol_ref_counts: dict[str, int] = {}                       # symbol -> subscriber count
_ws_ibkr_subscribed_symbols: set[str] = set()
_market_dynamic_symbols: set[str] = set()
_ws_lock = threading.Lock()

_ws_last_ibkr_quote_ts: float = 0.0
_ws_last_yahoo_quote_ts: float = 0.0

# ---------------------------------------------------------------------------
# Tuning constants (derived from config)
# ---------------------------------------------------------------------------

_WS_CACHE_TTL = max(0.5, float(cfg.WS_CACHE_TTL_SECONDS))
_WS_PUSH_INTERVAL = max(0.5, float(cfg.WS_PUSH_INTERVAL_SECONDS))
_WS_STALE_WARN_SECONDS = max(1.0, float(cfg.WS_STALE_WARN_SECONDS))
_WS_STALE_CRITICAL_SECONDS = max(_WS_STALE_WARN_SECONDS, float(cfg.WS_STALE_CRITICAL_SECONDS))
_WS_HEARTBEAT_INTERVAL_SECONDS = 5.0

_US_EASTERN = ZoneInfo("America/New_York")

# ---------------------------------------------------------------------------
# Pure helpers — no I/O
# ---------------------------------------------------------------------------


def _normalize_symbol(symbol: str) -> str:
    return symbol.strip().upper()


def _ws_symbol_variants(symbol: str) -> list[str]:
    base = _normalize_symbol(symbol)
    variants = [base]
    dash = base.replace(".", "-")
    dot = base.replace("-", ".")
    for candidate in (dash, dot):
        if candidate and candidate not in variants:
            variants.append(candidate)
    return variants


def _market_state_from_schedule(symbol: str, quote_ts: int | None = None) -> str:
    # Crypto pairs are effectively always open.
    if symbol.endswith("-USD"):
        return "open"
    ts = quote_ts or int(_time.time())
    dt = datetime.fromtimestamp(ts, tz=_US_EASTERN)
    if dt.weekday() >= 5:
        return "closed"
    minutes = dt.hour * 60 + dt.minute
    if 9 * 60 + 30 <= minutes < 16 * 60:
        return "open"
    if 4 * 60 <= minutes < 9 * 60 + 30 or 16 * 60 <= minutes < 20 * 60:
        return "extended"
    return "closed"


def _normalize_market_state(state: str | None, symbol: str, quote_ts: int | None = None) -> str:
    if state:
        candidate = str(state).strip().lower()
        if candidate in {"open", "extended", "closed", "unknown"}:
            return candidate
    return _market_state_from_schedule(symbol, quote_ts)


def _build_ws_quote(
    symbol: str,
    price: float,
    quote_ts: int,
    source: str,
    market_state: str | None = None,
) -> dict[str, Any]:
    now_ts = _time.time()
    safe_ts = int(quote_ts or now_ts)
    stale_s = round(max(0.0, now_ts - safe_ts), 3)
    return {
        "type": "quote",
        "symbol": symbol,
        "price": round(float(price), 4),
        "time": safe_ts,
        "source": source,
        "market_state": _normalize_market_state(market_state, symbol, safe_ts),
        "stale_s": stale_s,
    }

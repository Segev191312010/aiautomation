"""
FastAPI application  --  REST API, WebSocket, and static frontend serving.

Run:
    uvicorn main:app --host 0.0.0.0 --port 8000 --reload

Endpoints overview
------------------
GET  /api/status                         --  system health
POST /api/ibkr/connect|disconnect        --  IBKR connection control

GET  /api/account/summary                --  normalised account KPIs (IBKR | sim)
GET  /api/positions                      --  live IBKR positions 

GET  /api/simulation/account             --  virtual sim account
GET  /api/simulation/positions           --  virtual sim positions
POST /api/simulation/order               --  place a virtual order
POST /api/simulation/reset               --  wipe virtual account

GET  /api/simulation/playback            --  replay state
POST /api/simulation/playback/load       --  load symbol + bars for replay
POST /api/simulation/playback/play       --  start/resume replay
POST /api/simulation/playback/pause      --  pause
POST /api/simulation/playback/stop       --  reset to beginning
POST /api/simulation/playback/speed      --  set replay speed

GET  /api/watchlist                      --  quote cards for default watchlist symbols
GET  /api/yahoo/{symbol}/bars            --  OHLCV bars via Yahoo Finance
GET  /api/market/{symbol}/price          --  single price (IBKR or Yahoo)
GET  /api/market/{symbol}/bars           --  IBKR historical bars
POST /api/market/{symbol}/subscribe      --  subscribe to 5-s real-time bars
POST /api/market/{symbol}/unsubscribe    --  unsubscribe

GET  /api/orders                         --  open IBKR orders
DELETE /api/orders/{id}                  --  cancel IBKR order
POST /api/orders/manual                  --  place manual IBKR order

GET|POST|PUT|DELETE /api/rules/*         --  automation rules CRUD
POST /api/rules/{id}/toggle              --  enable / disable rule

POST /api/bot/start|stop                 --  start / stop rule-evaluation loop
GET  /api/bot/status

GET  /api/trades                         --  trade execution log

WS   /ws                                 --  general events (bot, fills, IBKR state)
WS   /ws/market-data                     --  streaming price updates for a symbol list
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import urllib.request
from pathlib import Path
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Literal
from zoneinfo import ZoneInfo

import time as _time

from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError

# eventkit (bundled with ib_insync) calls asyncio.get_event_loop() at import
# time, which raises RuntimeError on Python 3.14 when no loop exists yet.
# Create one so the import succeeds; uvicorn replaces it with its own loop.
asyncio.set_event_loop(asyncio.new_event_loop())

from config import cfg
from database import (
    delete_rule, get_rule, get_rules, get_trades, init_db, save_rule, save_trade,
    get_screener_presets, save_screener_preset, delete_screener_preset,
    save_backtest, get_backtests, get_backtest, delete_backtest,
    get_alerts, get_alert, save_alert, delete_alert, get_alert_history,
)
from ibkr_client import ibkr
from market_data import (
    get_historical_bars, get_latest_price,
    subscribe_realtime, unsubscribe_realtime,
    subscribe_realtime_bars, unsubscribe_realtime_bars,
)
from models import (
    AccountSummary, PlaybackState, Rule, RuleCreate, RuleUpdate,
    Trade, TradeAction, ScanRequest, ScanFilter, ScreenerPreset, EnrichRequest,
    BacktestRequest, BacktestSaveRequest, Alert, AlertCreate, AlertUpdate,
)
from order_executor import OrderError, cancel_order, get_open_orders, on_fill, place_order
from simulation import replay_engine, sim_engine
from auth import create_token, get_current_user
from indicators import calculate as ind_calculate, series_to_json
from settings import get_settings, update_settings
from data_health import DataFreshnessMonitor
from diagnostics_api import create_diagnostics_router
from diagnostics_service import DiagnosticsService
from screener import (
    run_scan, list_universes, validate_timeframe, enrich_symbols,
)
import bot_runner
import alert_engine
from sector_rotation import get_sector_rotation, get_sector_leaders, get_rotation_heatmap
from stock_profile_service import StockProfileService
from stock_profile_api import create_stock_profile_router
from rule_builder_api import router as rule_builder_router
from advisor_api import router as advisor_router
from risk_api import router as risk_router
from health import router as health_router
from notification_service import notification_service

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s  --  %(message)s",
)
log = logging.getLogger(__name__)


_data_health = DataFreshnessMonitor(
    {
        "watchlist_quotes": 30.0,
        "ws_quotes": float(cfg.WS_STALE_WARN_SECONDS),
        "ws_ibkr_quotes": float(cfg.WS_STALE_WARN_SECONDS),
        "ws_yahoo_quotes": float(cfg.WS_STALE_WARN_SECONDS),
        "yahoo_bars": 300.0,
        "heartbeat_quotes": 45.0,
        "diag_indicators": 3600.0,
        "diag_market_map": 3600.0,
        "diag_sector_projections": 3600.0,
        "diag_news_cache": 3600.0,
        "diag_refresh_jobs": 3600.0,
    }
)


def _record_data_success(source: str, *, count: int = 0, duration_ms: float | None = None) -> None:
    _data_health.record_success(source, count=count, duration_ms=duration_ms)


def _record_data_failure(source: str, error: str, *, duration_ms: float | None = None) -> None:
    _data_health.record_failure(source, error, duration_ms=duration_ms)


_diag_service = DiagnosticsService(
    record_success=lambda source: _record_data_success(source),
    record_failure=lambda source, error: _record_data_failure(source, error),
)


# ---------------------------------------------------------------------------
# WebSocket connection manager
# ---------------------------------------------------------------------------

class ConnectionManager:
    def __init__(self) -> None:
        self._connections: list[WebSocket] = []

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._connections.append(ws)

    def disconnect(self, ws: WebSocket) -> None:
        try:
            self._connections.remove(ws)
        except ValueError:
            pass

    async def broadcast(self, data: dict) -> None:
        dead: list[WebSocket] = []
        for ws in list(self._connections):
            try:
                await ws.send_text(json.dumps(data))
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


manager = ConnectionManager()


async def _broadcast(payload: dict) -> None:
    await manager.broadcast(payload)


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    # â"€â"€ Startup â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€
    from startup import validate_startup
    await validate_startup()
    await init_db()
    await sim_engine.initialize()
    await _diag_service.ensure_catalog_seeded()

    bot_runner.set_broadcast(_broadcast)
    sim_engine.set_broadcast(_broadcast)
    replay_engine.set_broadcast(_broadcast)
    ibkr.set_broadcast(_broadcast)
    alert_engine.set_broadcast(_broadcast)
    notification_service.set_ws_broadcast(_broadcast)

    # Register order-fill â†’ WS broadcast
    async def _on_trade_fill(trade: Trade) -> None:
        await _broadcast(
            {
                "type":     "filled",
                "order_id": trade.order_id,
                "trade_id": trade.id,
                "symbol":   trade.symbol,
                "action":   trade.action,
                "qty":      trade.quantity,
                "price":    trade.fill_price,
            }
        )
    on_fill(lambda t: asyncio.create_task(_on_trade_fill(t)))

    # Register order-fill → position tracker (exit management)
    async def _on_trade_fill_register(trade: Trade) -> None:
        if trade.action != "BUY" or not trade.fill_price:
            return
        try:
            from position_tracker import register_position
            from market_data import get_historical_bars
            df = await get_historical_bars(trade.symbol, duration="60 D", bar_size="1D")
            if df is not None and len(df) >= 14:
                await register_position(trade, df, trade.rule_name)
        except Exception as exc:
            log.error("Position registration failed for %s: %s", trade.id, exc)
    on_fill(lambda t: asyncio.create_task(_on_trade_fill_register(t)))

    # Attempt IBKR connection (non-blocking)
    connected = await ibkr.connect()
    if connected:
        log.info("IBKR connected on startup")
        await ibkr.start_reconnect_loop()
        from order_executor import reconcile_pending_orders
        asyncio.create_task(reconcile_pending_orders())
    else:
        log.warning("IBKR not connected  --  auto-reconnect running in background")
        await ibkr.start_reconnect_loop()

    await _start_market_heartbeat()
    await alert_engine.start()

    # Start AI optimization background loop (if API key configured)
    if cfg.ANTHROPIC_API_KEY:
        from ai_optimizer import ai_optimization_loop
        from ai_learning import ai_learning_loop
        asyncio.create_task(ai_optimization_loop())
        asyncio.create_task(ai_learning_loop())
        log.info("AI optimization loop started (interval=%ds)", cfg.AI_OPTIMIZE_INTERVAL_SECONDS)
        log.info("AI learning loop started (interval=6h)")

    yield

    # â"€â"€ Shutdown â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€
    await _stop_market_heartbeat()
    await alert_engine.stop()
    await bot_runner.stop()
    await replay_engine.stop()
    await ibkr.disconnect()


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="Trading Dashboard", version="2.0.0", lifespan=lifespan)

# ── Stock profile router
app.include_router(create_stock_profile_router(StockProfileService()))
app.include_router(create_diagnostics_router(_diag_service))
app.include_router(rule_builder_router)
app.include_router(risk_router)
app.include_router(health_router)
app.include_router(advisor_router)
from autopilot_api import router as autopilot_router
app.include_router(autopilot_router)


# ── Event system endpoints ───────────────────────────────────────────────────

@app.get("/api/events/metrics")
async def get_event_metrics():
    """Live metrics from the event system."""
    from bot_runner import event_bus, event_logger, metrics
    return {
        "event_bus": {"total_events": event_bus.event_count, "handlers": event_bus.handler_count()},
        "event_logger": {"events_logged": event_logger.event_count, "log_file": str(event_logger.log_path)},
        "metrics": metrics.summary(),
    }


@app.get("/api/events/log")
async def get_event_log(last_n: int = 50):
    """Recent events from the JSONL log."""
    from bot_runner import event_logger
    from event_logger import EventLogger
    events = EventLogger.replay(event_logger.log_path)
    return {"events": events[-last_n:], "total": len(events)}


@app.get("/api/events/sessions")
async def get_event_sessions():
    """List all event log sessions."""
    from event_logger import EventLogger
    return EventLogger.list_sessions()


# â"€â"€ CORS â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",   # Vite dev server
        "http://localhost:5174",   # Vite dev server (fallback port)
        "http://localhost:8000",   # Same-origin
        "http://127.0.0.1:5173",
        "http://127.0.0.1:5174",
        "http://127.0.0.1:8000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# â"€â"€ Static assets â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€

_FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend")
if os.path.isdir(_FRONTEND_DIR):
    app.mount("/static", StaticFiles(directory=_FRONTEND_DIR), name="static")

_DASHBOARD_DIR = os.path.join(os.path.dirname(__file__), cfg.DASHBOARD_BUILD_DIR)
_ASSETS_DIR = Path(os.path.join(_DASHBOARD_DIR, "assets")).resolve()


# ---------------------------------------------------------------------------
# Global error handlers  --  all errors return {error, detail} JSON format
# ---------------------------------------------------------------------------

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": "HTTPException", "detail": str(exc.detail)},
    )


@app.exception_handler(RequestValidationError)
async def request_validation_handler(request: Request, exc: RequestValidationError):
    log.warning("Validation error on %s %s: %s", request.method, request.url, exc.errors())
    return JSONResponse(
        status_code=422,
        content={"error": "ValidationError", "detail": exc.errors()},
    )


@app.exception_handler(ValidationError)
async def validation_exception_handler(request: Request, exc: ValidationError):
    return JSONResponse(
        status_code=422,
        content={"error": "ValidationError", "detail": str(exc)},
    )


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    log.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"error": "InternalServerError", "detail": "An unexpected error occurred."},
    )


# ---------------------------------------------------------------------------
# Request logging middleware
# ---------------------------------------------------------------------------

@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = _time.perf_counter()
    response = await call_next(request)
    duration_ms = (_time.perf_counter() - start) * 1000
    log.info("%s %s â†’ %d (%.0fms)", request.method, request.url.path, response.status_code, duration_ms)
    return response


@app.get("/trading", response_class=FileResponse)
async def serve_legacy_frontend():
    p = os.path.join(_FRONTEND_DIR, "trading.html")
    return FileResponse(p) if os.path.exists(p) else HTMLResponse("<h1>Not found</h1>", 404)


@app.get("/assets/{file_path:path}")
async def serve_spa_assets(file_path: str):
    """
    Serve Vite-built JS/CSS/etc. assets.
    Vite emits absolute /assets/... URLs (base='/'), so we intercept them here.
    This route works even when the server was started before `npm run build`.
    """
    full = (_ASSETS_DIR / file_path).resolve()
    if not str(full).startswith(str(_ASSETS_DIR)):
        raise HTTPException(400, "Invalid asset path")
    if full.is_file():
        return FileResponse(str(full))
    raise HTTPException(404, f"Asset not found: {file_path}")


@app.get("/app")
@app.get("/app/{path:path}")
async def serve_react_app(path: str = ""):
    """Serve the React SPA for any /app/* route (client-side routing)."""
    index = os.path.join(_DASHBOARD_DIR, "index.html")
    if not os.path.exists(index):
        return HTMLResponse(
            "<h3>React dashboard not built.</h3>"
            "<pre>cd dashboard && npm install && npm run build</pre>",
            status_code=200,
        )
    return FileResponse(index)


# ---------------------------------------------------------------------------
# WebSocket origin validation
# ---------------------------------------------------------------------------

_ALLOWED_WS_ORIGINS = {
    "http://localhost:5173", "http://localhost:5174",
    "http://localhost:8000",
    "http://127.0.0.1:5173", "http://127.0.0.1:5174",
    "http://127.0.0.1:8000",
}


def _check_ws_origin(ws: WebSocket) -> bool:
    origin = ws.headers.get("origin", "")
    return origin in _ALLOWED_WS_ORIGINS or not origin  # allow non-browser


# ---------------------------------------------------------------------------
# General WebSocket
# ---------------------------------------------------------------------------

@app.websocket("/ws")
async def ws_general(ws: WebSocket):
    if not _check_ws_origin(ws):
        await ws.accept()
        await ws.close(code=4003)
        return
    await manager.connect(ws)
    try:
        while True:
            data = await ws.receive_text()
            if data == "ping":
                await ws.send_text(json.dumps({"type": "pong"}))
    except WebSocketDisconnect:
        manager.disconnect(ws)


# ---------------------------------------------------------------------------
# Shared live quote fanout cache/state for WebSocket streaming
# ---------------------------------------------------------------------------

_ws_price_cache: dict[str, tuple[float, float, int, str]] = {}  # symbol -> (price, fetch_ts, quote_ts, market_state)
_ws_ibkr_quotes: dict[str, tuple[float, int, str]] = {}         # symbol -> (price, quote_ts, market_state)
_ws_symbol_ref_counts: dict[str, int] = {}                      # symbol -> subscriber count
_ws_ibkr_subscribed_symbols: set[str] = set()
_ws_lock = threading.Lock()

_ws_last_ibkr_quote_ts: float = 0.0
_ws_last_yahoo_quote_ts: float = 0.0

_WS_CACHE_TTL = max(0.5, float(cfg.WS_CACHE_TTL_SECONDS))
_WS_PUSH_INTERVAL = max(0.5, float(cfg.WS_PUSH_INTERVAL_SECONDS))
_WS_STALE_WARN_SECONDS = max(1.0, float(cfg.WS_STALE_WARN_SECONDS))
_WS_STALE_CRITICAL_SECONDS = max(_WS_STALE_WARN_SECONDS, float(cfg.WS_STALE_CRITICAL_SECONDS))
_WS_HEARTBEAT_INTERVAL_SECONDS = 5.0

_US_EASTERN = ZoneInfo("America/New_York")


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


def _on_ibkr_tick(symbol: str, price: float) -> None:
    global _ws_last_ibkr_quote_ts
    try:
        value = float(price)
    except (TypeError, ValueError):
        return
    if value <= 0:
        return
    sym = _normalize_symbol(symbol)
    ts = int(_time.time())
    state = _market_state_from_schedule(sym, ts)
    with _ws_lock:
        _ws_ibkr_quotes[sym] = (round(value, 4), ts, state)
        _ws_last_ibkr_quote_ts = _time.time()
    _record_data_success("ws_ibkr_quotes", count=1)


async def _ws_sync_ibkr_subscriptions() -> None:
    with _ws_lock:
        tracked = [sym for sym, count in _ws_symbol_ref_counts.items() if count > 0]
        if not ibkr.is_connected():
            _ws_ibkr_subscribed_symbols.clear()
            return
        missing = [sym for sym in tracked if sym not in _ws_ibkr_subscribed_symbols]
        _ws_ibkr_subscribed_symbols.update(missing)

    for sym in missing:
        try:
            ok = await subscribe_realtime(sym, _on_ibkr_tick)
        except Exception as exc:
            _record_data_failure("ws_ibkr_quotes", str(exc))
            log.debug("IBKR realtime subscribe failed for %s: %s", sym, exc)
            with _ws_lock:
                _ws_ibkr_subscribed_symbols.discard(sym)
            continue
        if not ok:
            with _ws_lock:
                _ws_ibkr_subscribed_symbols.discard(sym)


async def _ws_add_symbol_refs(symbols: list[str]) -> None:
    added: list[str] = []
    with _ws_lock:
        for raw in symbols:
            sym = _normalize_symbol(raw)
            if not sym:
                continue
            prev = _ws_symbol_ref_counts.get(sym, 0)
            _ws_symbol_ref_counts[sym] = prev + 1
            if prev == 0:
                added.append(sym)
    if added:
        _track_heartbeat_symbols(added)
    await _ws_sync_ibkr_subscriptions()


async def _ws_remove_symbol_refs(symbols: list[str]) -> None:
    removed: list[str] = []
    with _ws_lock:
        for raw in symbols:
            sym = _normalize_symbol(raw)
            if not sym:
                continue
            prev = _ws_symbol_ref_counts.get(sym, 0)
            if prev <= 1:
                _ws_symbol_ref_counts.pop(sym, None)
                removed.append(sym)
                _ws_ibkr_subscribed_symbols.discard(sym)
            else:
                _ws_symbol_ref_counts[sym] = prev - 1
    for sym in removed:
        unsubscribe_realtime(sym)
    if removed:
        _untrack_heartbeat_symbols(removed)


def _ws_get_ibkr_quote(symbol: str) -> tuple[float, int, str] | None:
    with _ws_lock:
        return _ws_ibkr_quotes.get(symbol)


def _is_crypto_usd_symbol(symbol: str) -> bool:
    sym = _normalize_symbol(symbol)
    if not sym.endswith("-USD"):
        return False
    base = sym[:-4]
    return bool(base) and base.replace("-", "").isalnum()


def _fetch_coinbase_spot(symbol: str) -> tuple[float, int, str] | None:
    """
    Fetch spot price from Coinbase for crypto USD pairs.

    Returns (price, unix_ts, market_state) or None on any failure.
    """
    sym = _normalize_symbol(symbol)
    if not _is_crypto_usd_symbol(sym):
        return None
    try:
        exchange_req = urllib.request.Request(
            f"https://api.exchange.coinbase.com/products/{sym}/ticker",
            headers={"User-Agent": "trading-dashboard/1.0"},
        )
        with urllib.request.urlopen(exchange_req, timeout=2.5) as resp:  # nosec B310
            payload = json.loads(resp.read().decode("utf-8"))
        exchange_price = payload.get("price")
        if exchange_price is not None:
            price = float(exchange_price)
            if price > 0:
                raw_time = payload.get("time")
                quote_ts = int(_time.time())
                if isinstance(raw_time, str):
                    try:
                        quote_ts = int(datetime.fromisoformat(raw_time.replace("Z", "+00:00")).timestamp())
                    except ValueError:
                        pass
                return (round(price, 4), quote_ts, "open")
    except Exception:
        pass

    try:
        spot_req = urllib.request.Request(
            f"https://api.coinbase.com/v2/prices/{sym}/spot",
            headers={"User-Agent": "trading-dashboard/1.0"},
        )
        with urllib.request.urlopen(spot_req, timeout=2.5) as resp:  # nosec B310
            payload = json.loads(resp.read().decode("utf-8"))
        amount = payload.get("data", {}).get("amount")
        price = float(amount)
        if price <= 0:
            return None
        return (round(price, 4), int(_time.time()), "open")
    except Exception:
        return None


async def _ws_batch_prices(symbols: list[str]) -> dict[str, dict[str, Any]]:
    """
    Resolve live-ish quotes from Yahoo with 1-second TTL cache.

    Strategy:
    1) Ticker.fast_info price fields (best effort)
    2) 1m pre/post close fallback when fast_info is missing
    """
    import pandas as pd
    import yfinance as yf

    global _ws_last_yahoo_quote_ts
    started = _time.perf_counter()
    now = _time.time()
    results: dict[str, dict[str, Any]] = {}
    stale: list[str] = []

    for raw in symbols:
        sym = _normalize_symbol(raw)
        if not sym:
            continue
        cached = _ws_price_cache.get(sym)
        if cached and (now - cached[1]) < _WS_CACHE_TTL:
            price, _fetch_ts, quote_ts, state = cached
            results[sym] = _build_ws_quote(sym, price, quote_ts, "yahoo", state)
        else:
            stale.append(sym)

    fetch_failed = False
    fetched: dict[str, tuple[float, int, str]] = {}

    if stale:
        def _from_fast_info(sym: str) -> tuple[float, int, str] | None:
            for candidate in _ws_symbol_variants(sym):
                try:
                    fi = yf.Ticker(candidate).fast_info
                except Exception:
                    continue
                raw_price = (
                    getattr(fi, "last_price", None)
                    or getattr(fi, "regular_market_price", None)
                    or getattr(fi, "post_market_price", None)
                    or getattr(fi, "pre_market_price", None)
                )
                try:
                    price = float(raw_price)
                except (TypeError, ValueError):
                    continue
                if price <= 0:
                    continue
                ts = int(_time.time())
                state = _normalize_market_state(getattr(fi, "market_state", None), sym, ts)
                return (round(price, 4), ts, state)
            return None

        def _resolve_df_for_symbol(raw_df: Any, sym: str) -> Any:
            if raw_df is None:
                return None
            if not isinstance(getattr(raw_df, "columns", None), pd.MultiIndex):
                return raw_df
            col0 = {str(c).upper() for c in raw_df.columns.get_level_values(0)}
            for candidate in _ws_symbol_variants(sym):
                if candidate in col0:
                    return raw_df[candidate]
            return None

        def _fetch_batch() -> dict[str, tuple[float, int, str]]:
            out: dict[str, tuple[float, int, str]] = {}
            unresolved: list[str] = []

            crypto_symbols = [sym for sym in stale if _is_crypto_usd_symbol(sym)]
            non_crypto_symbols = [sym for sym in stale if sym not in crypto_symbols]

            # For crypto, prefer Coinbase spot (more responsive than Yahoo fallback).
            if crypto_symbols:
                worker_count = max(1, min(len(crypto_symbols), 6))
                with ThreadPoolExecutor(max_workers=worker_count) as ex:
                    for sym, resolved in zip(crypto_symbols, ex.map(_fetch_coinbase_spot, crypto_symbols)):
                        if resolved:
                            out[sym] = resolved
                        else:
                            unresolved.append(sym)

            if non_crypto_symbols:
                worker_count = max(1, min(len(non_crypto_symbols), 8))
                with ThreadPoolExecutor(max_workers=worker_count) as ex:
                    for sym, resolved in zip(non_crypto_symbols, ex.map(_from_fast_info, non_crypto_symbols)):
                        if resolved:
                            out[sym] = resolved
                        else:
                            unresolved.append(sym)

            if unresolved:
                raw_df = yf.download(
                    tickers=" ".join(unresolved),
                    period="1d",
                    interval="1m",
                    group_by="ticker",
                    auto_adjust=False,
                    threads=False,
                    progress=False,
                    prepost=True,
                )
                if raw_df is None or raw_df.empty:
                    return out
                for sym in unresolved:
                    try:
                        df_sym = _resolve_df_for_symbol(raw_df, sym)
                        if df_sym is None or "Close" not in df_sym.columns:
                            continue
                        close = df_sym["Close"].dropna()
                        if close.empty:
                            continue
                        price = float(close.iloc[-1])
                        if price <= 0:
                            continue
                        idx = close.index[-1]
                        quote_ts = int(pd.Timestamp(idx).timestamp())
                        state = _market_state_from_schedule(sym, quote_ts)
                        out[sym] = (round(price, 4), quote_ts, state)
                    except Exception:
                        continue
            return out

        try:
            fetched = await asyncio.to_thread(_fetch_batch)
            fetch_ts = _time.time()
            if fetched:
                _ws_last_yahoo_quote_ts = fetch_ts
            for sym, (price, quote_ts, state) in fetched.items():
                _ws_price_cache[sym] = (price, fetch_ts, quote_ts, state)
                results[sym] = _build_ws_quote(sym, price, quote_ts, "yahoo", state)
        except Exception as exc:
            log.warning("WS batch Yahoo fetch failed: %s", exc)
            _record_data_failure("ws_yahoo_quotes", str(exc))
            fetch_failed = True

    # Keep stale cache visible if Yahoo is slow/rate-limited.
    for raw in symbols:
        sym = _normalize_symbol(raw)
        if sym in results:
            continue
        cached = _ws_price_cache.get(sym)
        if cached:
            price, _fetch_ts, quote_ts, state = cached
            results[sym] = _build_ws_quote(sym, price, quote_ts, "yahoo", state)

    duration_ms = (_time.perf_counter() - started) * 1000.0
    if results:
        _record_data_success("ws_quotes", count=len(results), duration_ms=duration_ms)
        _record_data_success("ws_yahoo_quotes", count=len(fetched), duration_ms=duration_ms)
    elif symbols and not fetch_failed:
        _record_data_failure("ws_quotes", "no prices resolved", duration_ms=duration_ms)

    return results


async def _ws_collect_quotes(symbols: list[str]) -> dict[str, dict[str, Any]]:
    await _ws_sync_ibkr_subscriptions()
    now = _time.time()
    resolved: dict[str, dict[str, Any]] = {}
    fallback_syms: list[str] = []

    for raw in symbols:
        sym = _normalize_symbol(raw)
        if not sym:
            continue
        ibkr_quote = _ws_get_ibkr_quote(sym)
        if ibkr.is_connected() and ibkr_quote:
            price, quote_ts, state = ibkr_quote
            age = now - quote_ts
            if age <= _WS_STALE_WARN_SECONDS:
                resolved[sym] = _build_ws_quote(sym, price, quote_ts, "ibkr", state)
                continue
        fallback_syms.append(sym)

    if fallback_syms:
        resolved.update(await _ws_batch_prices(fallback_syms))

    # Keep last known IBKR quote if Yahoo has nothing for the symbol.
    for sym in fallback_syms:
        if sym in resolved:
            continue
        ibkr_quote = _ws_get_ibkr_quote(sym)
        if ibkr_quote:
            price, quote_ts, state = ibkr_quote
            resolved[sym] = _build_ws_quote(sym, price, quote_ts, "ibkr", state)

    return resolved


# ---------------------------------------------------------------------------
# Dedicated market-data WebSocket
# Clients send:  {"action":"subscribe","symbols":["AAPL","BTC-USD"]}
#                {"action":"unsubscribe","symbols":["AAPL"]}
# Server pushes: {"type":"quote","symbol":"AAPL","price":220.0,...}
# ---------------------------------------------------------------------------

@app.websocket("/ws/market-data")
async def ws_market_data(ws: WebSocket):
    if not _check_ws_origin(ws):
        await ws.accept()
        await ws.close(code=4003)
        return
    await ws.accept()
    subscribed: set[str] = set()
    push_task: asyncio.Task | None = None

    async def _push_loop() -> None:
        last_heartbeat = 0.0
        while True:
            syms = list(subscribed)
            if syms:
                quotes = await _ws_collect_quotes(syms)
                for payload in quotes.values():
                    try:
                        await ws.send_text(json.dumps(payload))
                    except Exception:
                        return

            now = _time.time()
            if now - last_heartbeat >= _WS_HEARTBEAT_INTERVAL_SECONDS:
                try:
                    await ws.send_text(
                        json.dumps({"type": "heartbeat", "time": int(now)})
                    )
                    last_heartbeat = now
                except Exception:
                    return
            await asyncio.sleep(_WS_PUSH_INTERVAL)

    try:
        push_task = asyncio.create_task(_push_loop())
        while True:
            raw = await ws.receive_text()
            msg = json.loads(raw)
            action = msg.get("action") or msg.get("type") or ""
            symbols = [_normalize_symbol(s) for s in msg.get("symbols", []) if s]
            if action == "subscribe":
                fresh = [sym for sym in symbols if sym and sym not in subscribed]
                if fresh:
                    subscribed.update(fresh)
                    await _ws_add_symbol_refs(fresh)
            elif action == "unsubscribe":
                removed = [sym for sym in symbols if sym in subscribed]
                if removed:
                    for sym in removed:
                        subscribed.discard(sym)
                    await _ws_remove_symbol_refs(removed)
            elif action == "ping":
                await ws.send_text(json.dumps({"type": "pong", "time": int(_time.time())}))
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        log.error("Unexpected WS error in market-data handler: %s", exc, exc_info=True)
    finally:
        if subscribed:
            await _ws_remove_symbol_refs(list(subscribed))
        if push_task:
            push_task.cancel()

# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

@app.get("/api/status")
async def get_status():
    return {
        "ibkr_connected":       ibkr.is_connected(),
        "is_paper":             cfg.IS_PAPER,
        "sim_mode":             cfg.SIM_MODE,
        "bot_running":          bot_runner.is_running(),
        "last_run":             bot_runner.get_last_run(),
        "next_run":             bot_runner.get_next_run(),
        "bot_interval_seconds": cfg.BOT_INTERVAL_SECONDS,
    }


@app.get("/api/health")
async def health_check():
    """Lightweight health probe for Docker / load balancers."""
    import aiosqlite
    db_ok = True
    try:
        async with aiosqlite.connect(cfg.DB_PATH) as db:
            await db.execute("SELECT 1")
    except Exception:
        db_ok = False
    return {
        "status": "healthy" if db_ok else "degraded",
        "db": "ok" if db_ok else "unreachable",
        "version": app.version,
    }


@app.get("/api/data/health")
async def get_data_health():
    snapshot = _data_health.snapshot()
    now = _time.time()
    ibkr_age = None if _ws_last_ibkr_quote_ts <= 0 else round(max(0.0, now - _ws_last_ibkr_quote_ts), 3)
    yahoo_age = None if _ws_last_yahoo_quote_ts <= 0 else round(max(0.0, now - _ws_last_yahoo_quote_ts), 3)
    with _ws_lock:
        active_symbols = sum(1 for count in _ws_symbol_ref_counts.values() if count > 0)
        ibkr_symbols = len(_ws_ibkr_subscribed_symbols)
    snapshot["streaming"] = {
        "push_interval_s": _WS_PUSH_INTERVAL,
        "cache_ttl_s": _WS_CACHE_TTL,
        "stale_warn_s": _WS_STALE_WARN_SECONDS,
        "stale_critical_s": _WS_STALE_CRITICAL_SECONDS,
        "active_symbols": active_symbols,
        "ibkr_subscribed_symbols": ibkr_symbols,
        "ibkr_connected": ibkr.is_connected(),
        "ibkr_last_quote_age_s": ibkr_age,
        "yahoo_last_quote_age_s": yahoo_age,
    }
    snapshot["diagnostics"] = {
        "enabled": _diag_service.enabled,
    }
    return snapshot


# ---------------------------------------------------------------------------
# IBKR connection control
# ---------------------------------------------------------------------------

@app.post("/api/ibkr/connect")
async def connect_ibkr():
    ok = await ibkr.connect()
    if not ok:
        raise HTTPException(502, "Could not connect to IBKR. Is IB Gateway running?")
    await ibkr.start_reconnect_loop()
    return {"connected": True}


@app.post("/api/ibkr/disconnect")
async def disconnect_ibkr():
    await ibkr.disconnect()
    return {"connected": False}


# ---------------------------------------------------------------------------
# Account summary  (IBKR live | simulation)
# ---------------------------------------------------------------------------

@app.get("/api/account/summary")
async def get_account_summary():
    """
    Returns a unified account summary regardless of connection mode:
      - SIM_MODE â†’ virtual sim account
      - IBKR connected â†’ real account summary
    """
    if cfg.SIM_MODE:
        account = await sim_engine.get_account()
        return account.model_dump()

    if ibkr.is_connected():
        try:
            summary = await ibkr.get_account_summary()
            return summary.model_dump()
        except Exception as exc:
            log.warning("Account fetch failed: %s", exc)

    raise HTTPException(503, "IBKR not connected")


# Backwards-compatible alias
@app.get("/api/account")
async def get_account():
    if not ibkr.is_connected():
        raise HTTPException(503, "IBKR not connected")
    return (await ibkr.get_account_summary()).model_dump()


# ---------------------------------------------------------------------------
# Positions
# ---------------------------------------------------------------------------

@app.get("/api/positions")
async def get_positions():
    if cfg.SIM_MODE:
        positions = await sim_engine.get_positions()
        return [p.model_dump() for p in positions]

    if not ibkr.is_connected():
        raise HTTPException(503, "IBKR not connected")
    return [p.model_dump() for p in await ibkr.get_positions()]


@app.get("/api/positions/summary")
async def get_positions_summary():
    """EOD summary: reasoning for each position — P&L, rule, hold time, signals."""
    if not ibkr.is_connected():
        raise HTTPException(503, "IBKR not connected")

    positions = await ibkr.get_positions()
    trades = await get_trades(limit=500)
    acct = await ibkr.get_account_summary()

    # Map trades to positions by symbol (most recent BUY)
    trade_by_sym: dict[str, Any] = {}
    for t in trades:
        if t.action == "BUY" and t.status == "FILLED" and t.symbol not in trade_by_sym:
            trade_by_sym[t.symbol] = t

    # Get open bracket orders
    bracket_orders: dict[str, dict] = {}
    for ib_trade in ibkr.ib.openTrades():
        sym = ib_trade.contract.symbol
        if sym not in bracket_orders:
            bracket_orders[sym] = {}
        if ib_trade.order.orderType == "STP":
            bracket_orders[sym]["sl"] = ib_trade.order.auxPrice
        elif ib_trade.order.orderType == "LMT" and ib_trade.order.action == "SELL":
            bracket_orders[sym]["tp"] = ib_trade.order.lmtPrice

    summaries = []
    for pos in positions:
        sym = pos.symbol
        entry_trade = trade_by_sym.get(sym)
        entry_date = entry_trade.timestamp if entry_trade else None
        rule_name = entry_trade.rule_name if entry_trade else "Unknown"

        hold_days = 0
        if entry_date:
            try:
                from datetime import datetime as dt
                entry_dt = dt.fromisoformat(entry_date.replace("Z", "+00:00"))
                hold_days = (dt.now(entry_dt.tzinfo) - entry_dt).days
            except Exception:
                pass

        brackets = bracket_orders.get(sym, {})
        pnl = pos.unrealized_pnl
        pnl_pct = ((pos.market_price / pos.avg_cost) - 1) * 100 if pos.avg_cost > 0 else 0

        summaries.append({
            "symbol": sym,
            "entry_date": entry_date,
            "hold_time_days": hold_days,
            "qty": pos.qty,
            "avg_cost": round(pos.avg_cost, 2),
            "current_price": round(pos.market_price, 2),
            "pnl": round(pnl, 2),
            "pnl_pct": round(pnl_pct, 2),
            "rule_trigger": rule_name,
            "sl_price": brackets.get("sl"),
            "tp_price": brackets.get("tp"),
            "pct_of_account": round(abs(pos.market_value) / acct.balance * 100, 2) if acct.balance > 0 else 0,
        })

    return {"positions_summary": summaries, "account": acct.model_dump()}


@app.get("/api/positions/tracked")
async def get_tracked_positions():
    """Open positions monitored by the exit manager, enriched with live stop levels."""
    from database import get_open_positions
    from position_tracker import compute_trail_stop
    from market_data import get_latest_price, get_historical_bars
    from indicators import _atr

    positions = await get_open_positions()
    result = []
    for pos in positions:
        try:
            price = await get_latest_price(pos.symbol) or pos.entry_price
            df = await get_historical_bars(pos.symbol, "60 D", "1D")
            if df is not None and len(df) >= 14:
                current_atr = float(_atr(df["high"], df["low"], df["close"], 14).iloc[-1])
            else:
                current_atr = pos.atr_at_entry
            trail_stop = compute_trail_stop(pos, current_atr)
            effective_stop = max(pos.hard_stop_price, trail_stop)
        except Exception:
            price = pos.entry_price
            trail_stop = pos.hard_stop_price
            effective_stop = pos.hard_stop_price

        result.append({
            **pos.model_dump(),
            "current_price": round(price, 4),
            "trail_stop_price": round(trail_stop, 4),
            "effective_stop_price": round(effective_stop, 4),
            "unrealized_pnl": round((price - pos.entry_price) * pos.quantity, 2),
            "unrealized_pct": round(((price - pos.entry_price) / pos.entry_price) * 100, 2),
        })
    return result


# ---------------------------------------------------------------------------
# Simulation endpoints
# ---------------------------------------------------------------------------

@app.get("/api/simulation/account")
async def sim_account():
    positions = await sim_engine.get_positions()
    account = await sim_engine.get_account(positions)
    return account.model_dump()


@app.get("/api/simulation/positions")
async def sim_positions():
    def _price(sym: str) -> float | None:
        return None   # resolved asynchronously by get_positions

    positions = await sim_engine.get_positions(price_fn=_price)
    return [p.model_dump() for p in positions]


@app.get("/api/simulation/orders")
async def sim_orders(limit: int = 100):
    orders = await sim_engine.get_orders(limit)
    return [o.model_dump() for o in orders]


class SimOrderRequest(BaseModel):
    symbol: str
    action: Literal["BUY", "SELL"]
    qty: float = Field(gt=0)
    price: float = Field(gt=0)


@app.post("/api/simulation/order", status_code=201)
async def sim_place_order(body: SimOrderRequest):
    ok, msg = await sim_engine.execute_order(
        symbol=body.symbol.upper(),
        action=body.action,
        qty=body.qty,
        price=body.price,
    )
    if not ok:
        raise HTTPException(400, msg)
    return {"success": True, "message": msg}


@app.post("/api/simulation/reset")
async def sim_reset():
    await sim_engine.reset()
    return {"reset": True, "initial_cash": cfg.SIM_INITIAL_CASH}


# ---------------------------------------------------------------------------
# Replay / Playback endpoints
# ---------------------------------------------------------------------------

@app.get("/api/simulation/playback")
async def playback_state():
    return replay_engine.state.model_dump()


class LoadReplayRequest(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=10, pattern=r'^[A-Za-z0-9.\-]+$')
    period: Literal["1mo", "3mo", "6mo", "1y", "2y", "5y", "10y", "max"] = "1y"
    interval: Literal["1m", "2m", "5m", "15m", "30m", "60m", "90m", "1h", "1d", "5d", "1wk", "1mo"] = "1d"


@app.post("/api/simulation/playback/load")
async def playback_load(body: LoadReplayRequest):
    sym = body.symbol.upper()
    # Fetch bars from Yahoo Finance
    bars: list[dict] = []
    try:
        bars = await _yf_bars(sym, body.period, body.interval)
    except Exception as exc:
        log.warning("Yahoo bars failed for replay (%s): %s", sym, exc)

    if not bars:
        raise HTTPException(404, f"No replay data for {sym}")

    await replay_engine.load(sym, bars)
    return replay_engine.state.model_dump()


@app.post("/api/simulation/playback/play")
async def playback_play():
    await replay_engine.play()
    return replay_engine.state.model_dump()


@app.post("/api/simulation/playback/pause")
async def playback_pause():
    await replay_engine.pause()
    return replay_engine.state.model_dump()


@app.post("/api/simulation/playback/stop")
async def playback_stop():
    await replay_engine.stop()
    return replay_engine.state.model_dump()


class SpeedRequest(BaseModel):
    speed: int = Field(ge=1, le=100)


@app.post("/api/simulation/playback/speed")
async def playback_speed(body: SpeedRequest):
    replay_engine.set_speed(body.speed)
    return {"speed": replay_engine.state.speed}


# ---------------------------------------------------------------------------
# Orders
# ---------------------------------------------------------------------------

@app.get("/api/orders")
async def get_orders():
    return await get_open_orders()


@app.delete("/api/orders/{order_id}")
async def cancel_order_route(order_id: int):
    ok = await cancel_order(order_id)
    if not ok:
        raise HTTPException(404, "Order not found")
    return {"cancelled": True}


class ManualOrderRequest(BaseModel):
    symbol: str
    action: Literal["BUY", "SELL"]
    quantity: int = Field(gt=0)
    order_type: Literal["MKT", "LMT"] = "MKT"
    limit_price: float | None = None
    asset_type: Literal["STK", "OPT", "FUT"] = "STK"


@app.post("/api/orders/manual", status_code=201)
async def place_manual_order(body: ManualOrderRequest):
    """Place a manual order  --  routes to sim if SIM_MODE, else IBKR."""
    if cfg.SIM_MODE:
        sym = body.symbol.upper()
        price = await get_latest_price(sym)
        if price is None:
            try:
                quotes = await _yf_quotes(sym, source="sim_order_price")
                if quotes and quotes[0].get("price"):
                    price = quotes[0]["price"]
            except Exception:
                pass
        if price is None:
            raise HTTPException(503, "No market data available for " + sym)
        ok, msg = await sim_engine.execute_order(
            symbol=sym,
            action=body.action,
            qty=float(body.quantity),
            price=price,
        )
        if not ok:
            raise HTTPException(400, msg)
        return {"success": True, "message": msg, "sim": True}

    if not ibkr.is_connected():
        raise HTTPException(503, "IBKR not connected  --  start IB Gateway first")

    rule = Rule(
        name="Manual",
        symbol=body.symbol.upper(),
        enabled=True,
        conditions=[],
        action=TradeAction(
            type=body.action,
            asset_type=body.asset_type,
            quantity=body.quantity,
            order_type=body.order_type,
            limit_price=body.limit_price,
        ),
        cooldown_minutes=0,
    )
    try:
        trade = await place_order(rule)
    except OrderError as exc:
        raise HTTPException(400, str(exc))
    if not trade:
        raise HTTPException(502, "Order placement failed — check IBKR logs")
    return trade.model_dump()


# ---------------------------------------------------------------------------
# Trades
# ---------------------------------------------------------------------------

@app.get("/api/trades")
async def get_trade_log(limit: int = 200):
    trades = await get_trades(limit)
    return [t.model_dump() for t in trades]


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------

@app.get("/api/rules")
async def list_rules():
    return [r.model_dump() for r in await get_rules()]


@app.get("/api/rules/{rule_id}")
async def get_rule_route(rule_id: str):
    # Skip reserved sub-paths handled by rule_builder_router
    if rule_id in ("templates", "validate", "from-template", "export", "import"):
        raise HTTPException(404, "Use the specific endpoint")
    rule = await get_rule(rule_id)
    if not rule:
        raise HTTPException(404, "Rule not found")
    return rule.model_dump()


@app.post("/api/rules", status_code=201)
async def create_rule(body: RuleCreate):
    rule = Rule(**body.model_dump())
    await save_rule(rule)
    return rule.model_dump()


@app.put("/api/rules/{rule_id}")
async def update_rule_route(rule_id: str, body: RuleUpdate):
    rule = await get_rule(rule_id)
    if not rule:
        raise HTTPException(404, "Rule not found")
    updated = rule.model_copy(update=body.model_dump(exclude_none=True))
    await save_rule(updated)
    return updated.model_dump()


@app.delete("/api/rules/{rule_id}")
async def delete_rule_route(rule_id: str):
    if not await delete_rule(rule_id):
        raise HTTPException(404, "Rule not found")
    return {"deleted": True}


@app.post("/api/rules/{rule_id}/toggle")
async def toggle_rule(rule_id: str):
    rule = await get_rule(rule_id)
    if not rule:
        raise HTTPException(404, "Rule not found")
    rule.enabled = not rule.enabled
    await save_rule(rule)
    return {"id": rule_id, "enabled": rule.enabled}


# ---------------------------------------------------------------------------
# Bot control
# ---------------------------------------------------------------------------

@app.post("/api/bot/start")
async def start_bot():
    await bot_runner.start()
    return {"running": True}


@app.post("/api/bot/stop")
async def stop_bot():
    await bot_runner.stop()
    return {"running": False}


@app.get("/api/bot/status")
async def bot_status_route():
    return {
        "running":  bot_runner.is_running(),
        "last_run": bot_runner.get_last_run(),
        "next_run": bot_runner.get_next_run(),
    }


# ---------------------------------------------------------------------------
# IBKR market data (real-time bars + historical)
# ---------------------------------------------------------------------------

_active_rt_subs: set[str] = set()


@app.get("/api/market/{symbol}/bars")
async def get_bars(symbol: str, bar_size: str = "1D", duration: str = "60 D"):
    if not ibkr.is_connected():
        raise HTTPException(503, "IBKR not connected")

    df = await get_historical_bars(symbol.upper(), duration=duration, bar_size=bar_size, use_cache=False)
    if df.empty:
        raise HTTPException(404, f"No bars found for {symbol}")

    return [
        {
            "time":   int(row["time"].timestamp()),
            "open":   float(row["open"]),
            "high":   float(row["high"]),
            "low":    float(row["low"]),
            "close":  float(row["close"]),
            "volume": float(row["volume"]),
        }
        for _, row in df.iterrows()
    ]


@app.get("/api/market/{symbol}/price")
async def get_price(symbol: str):
    sym = symbol.upper()
    price = await get_latest_price(sym)
    if price is not None:
        return {"symbol": sym, "price": price}
    try:
        quotes = await _yf_quotes(sym, source="price_fallback")
        if quotes and quotes[0].get("price"):
            return {"symbol": sym, "price": quotes[0]["price"], "source": "yahoo"}
    except Exception as exc:
        log.warning("Yahoo price fallback failed for %s: %s", sym, exc)
    raise HTTPException(503, "No market data available")


@app.post("/api/market/{symbol}/subscribe")
async def subscribe_market_bars(symbol: str):
    sym = symbol.upper()
    if sym in _active_rt_subs:
        return {"subscribed": True, "symbol": sym}

    def _on_bar(bar_data: dict) -> None:
        asyncio.create_task(_broadcast({"type": "bar", "symbol": sym, **bar_data}))

    ok = await subscribe_realtime_bars(sym, _on_bar) if ibkr.is_connected() else False
    if ok:
        _active_rt_subs.add(sym)
    return {"subscribed": ok, "symbol": sym}


@app.post("/api/market/{symbol}/unsubscribe")
async def unsubscribe_market_bars(symbol: str):
    sym = symbol.upper()
    unsubscribe_realtime_bars(sym)
    _active_rt_subs.discard(sym)
    return {"subscribed": False, "symbol": sym}


# ---------------------------------------------------------------------------
# Yahoo Finance  --  watchlist quotes + bars
# ---------------------------------------------------------------------------

async def _yf_quotes(symbols_str: str, source: str = "watchlist_quotes") -> list[dict]:
    import yfinance as yf
    from concurrent.futures import ThreadPoolExecutor

    syms = [s.strip() for s in symbols_str.split(",") if s.strip()]
    if not syms:
        return []
    started = _time.perf_counter()

    def _one(sym: str):
        try:
            fi = yf.Ticker(sym).fast_info
            prev  = getattr(fi, "previous_close", None) or 0
            price = getattr(fi, "last_price", None) or 0
            chg   = price - prev
            chg_p = (chg / prev * 100) if prev else 0
            return {
                "symbol":     sym,
                "price":      round(price, 4),
                "change":     round(chg, 4),
                "change_pct": round(chg_p, 2),
                "year_high":  getattr(fi, "year_high", None),
                "year_low":   getattr(fi, "year_low", None),
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
        duration_ms = (_time.perf_counter() - started) * 1000.0
        _record_data_failure(source, str(exc), duration_ms=duration_ms)
        raise

    duration_ms = (_time.perf_counter() - started) * 1000.0
    if quotes:
        _record_data_success(source, count=len(quotes), duration_ms=duration_ms)
    else:
        _record_data_failure(source, "empty quote response", duration_ms=duration_ms)
    return quotes


async def _yf_bars(symbol: str, period: str, interval: str) -> list[dict]:
    started = _time.perf_counter()

    def _fetch():
        import yfinance as yf
        intraday = interval.endswith("m") or interval.endswith("h")
        df = yf.Ticker(symbol).history(period=period, interval=interval, prepost=intraday)
        if df.empty:
            return []
        df = df.dropna(subset=["Close"])
        df = df.fillna(0)  # fill remaining NaN (Volume etc.)
        if df.empty:
            return []
        return [
            {
                "time":   int(ts.timestamp()),
                "open":   round(float(row["Open"]),  4),
                "high":   round(float(row["High"]),  4),
                "low":    round(float(row["Low"]),   4),
                "close":  round(float(row["Close"]), 4),
                "volume": int(row["Volume"] or 0),
            }
            for ts, row in df.iterrows()
        ]
    try:
        bars = await asyncio.to_thread(_fetch)
    except Exception as exc:
        duration_ms = (_time.perf_counter() - started) * 1000.0
        _record_data_failure("yahoo_bars", str(exc), duration_ms=duration_ms)
        raise

    duration_ms = (_time.perf_counter() - started) * 1000.0
    if bars:
        _record_data_success("yahoo_bars", count=len(bars), duration_ms=duration_ms)
    else:
        _record_data_failure(
            "yahoo_bars",
            f"no bars for {symbol}:{period}:{interval}",
            duration_ms=duration_ms,
        )
    return bars


_DEFAULT_WATCHLIST = "BTC-USD,ETH-USD,AAPL,TSLA,SPY,QQQ,NVDA"
_market_heartbeat_task: asyncio.Task | None = None
_market_dynamic_symbols: set[str] = set()
_MARKET_HEARTBEAT_ENABLED = os.getenv("MARKET_HEARTBEAT_ENABLED", "true").lower() == "true"


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
    global _ws_last_yahoo_quote_ts
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
        _ws_last_yahoo_quote_ts = ts


_position_push_counter = 0

async def _market_heartbeat_loop() -> None:
    global _position_push_counter
    while True:
        await asyncio.sleep(_MARKET_HEARTBEAT_INTERVAL_SECONDS)
        symbols = _all_heartbeat_symbols()
        if not symbols:
            continue
        try:
            quotes = await _yf_quotes(",".join(symbols), source="heartbeat_quotes")
            if quotes:
                _cache_prices_from_quotes(quotes)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.debug("Market heartbeat fetch failed: %s", exc)

        # Push live position + account updates every 3rd cycle (~15s)
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
            except Exception:
                pass


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


@app.get("/api/watchlist")
async def get_watchlist_quotes(symbols: str = _DEFAULT_WATCHLIST):
    syms = _split_symbols(symbols)
    if not syms:
        return []

    _track_heartbeat_symbols(syms)

    try:
        quotes = await _yf_quotes(",".join(syms))
        if quotes:
            _cache_prices_from_quotes(quotes)
            return quotes
    except Exception as exc:
        log.warning("Yahoo Finance failed: %s ï¿½ ", exc)

    raise HTTPException(503, "No market data available")


_VALID_INTERVALS = {"1m", "2m", "5m", "15m", "30m", "60m", "90m", "1h", "1d", "5d", "1wk", "1mo", "3mo"}
_UNSUPPORTED_INTERVALS = {"4h", "2h"}  # yfinance does not support these natively

# period string â†’ approximate days
def _period_to_days(p: str) -> int:
    p = p.lower()
    if p.endswith("d"):   return int(p[:-1])
    if p.endswith("mo"):  return int(p[:-2]) * 30
    if p.endswith("y"):   return int(p[:-1]) * 365
    return 9999  # "max" or unknown â†’ unlimited

# max period (in days) per interval
_INTERVAL_MAX_DAYS = {
    "1m":  7,
    "2m":  60,
    "5m":  60,
    "15m": 60,
    "30m": 60,
    "60m": 730,
    "90m": 60,
    "1h":  730,
}


@app.get("/api/yahoo/{symbol}/bars")
async def get_yahoo_bars(symbol: str, period: str = "5d", interval: str = "5m"):
    # Validate interval
    if interval in _UNSUPPORTED_INTERVALS:
        raise HTTPException(400, f"Interval '{interval}' is not supported by Yahoo Finance. Use 1h instead.")
    if interval not in _VALID_INTERVALS:
        raise HTTPException(400, f"Invalid interval '{interval}'. Valid: {sorted(_VALID_INTERVALS)}")

    # Validate period vs interval limits
    max_days = _INTERVAL_MAX_DAYS.get(interval)
    if max_days is not None:
        req_days = _period_to_days(period)
        if req_days > max_days:
            raise HTTPException(
                400,
                f"{interval} interval requires period <= {max_days}d, but '{period}' â‰ˆ {req_days}d",
            )

    try:
        bars = await _yf_bars(symbol, period, interval)
        if bars:
            return bars
    except Exception as exc:
        log.warning("Yahoo bars failed for %s: %s", symbol, exc)

    raise HTTPException(404, f"No data for {symbol}")


# â"€â"€ Server-side indicator endpoint â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€

@app.get("/api/market/{symbol}/indicators")
async def get_indicators(
    symbol: str,
    indicator: str,
    length: int = 0,
    period: str = "1y",
    interval: str = "1d",
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
    band: str = "mid",
):
    """Calculate a technical indicator server-side and return [{time, value}, ...]."""
    import pandas as pd

    bars = await _yf_bars(symbol.upper(), period, interval)
    if not bars:
        raise HTTPException(404, f"No bar data for {symbol}")

    df = pd.DataFrame(bars)

    # Build params dict
    params: dict[str, Any] = {}
    ind = indicator.upper()
    if length > 0:
        params["length"] = length
    if ind == "MACD":
        params["fast"] = fast
        params["slow"] = slow
        params["signal"] = signal
    if ind == "BBANDS":
        params["band"] = band

    try:
        series = ind_calculate(df, ind, params)
    except ValueError as e:
        raise HTTPException(400, str(e))

    return series_to_json(series, df)


# ---------------------------------------------------------------------------
# Screener endpoints
# ---------------------------------------------------------------------------

@app.post("/api/screener/scan")
async def screener_scan(body: ScanRequest):
    # Validate filters count
    if len(body.filters) > 15:
        raise HTTPException(400, "Maximum 15 filters allowed")

    # Validate symbol count
    if body.symbols and len(body.symbols) > 600:
        raise HTTPException(400, "Maximum 600 symbols allowed")

    # Validate timeframe combo
    if not validate_timeframe(body.interval, body.period):
        raise HTTPException(
            400,
            f"Invalid interval/period combination: {body.interval}/{body.period}",
        )

    response = await run_scan(body)
    return response.model_dump()


@app.get("/api/screener/universes")
async def screener_universes():
    return list_universes()


@app.get("/api/screener/presets")
async def screener_list_presets():
    presets = await get_screener_presets()
    return [p.model_dump() for p in presets]


class SavePresetRequest(BaseModel):
    name: str
    filters: list[ScanFilter]


@app.post("/api/screener/presets", status_code=201)
async def screener_save_preset(body: SavePresetRequest):
    preset = ScreenerPreset(
        name=body.name,
        filters=body.filters,
        built_in=False,
        user_id="demo",
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    await save_screener_preset(preset)
    return preset.model_dump()


@app.delete("/api/screener/presets/{preset_id}")
async def screener_delete_preset(preset_id: str):
    if not await delete_screener_preset(preset_id):
        raise HTTPException(404, "Preset not found or is built-in")
    return {"deleted": True}


@app.post("/api/screener/enrich")
async def screener_enrich(body: EnrichRequest):
    results = await enrich_symbols(body.symbols)
    return [r.model_dump() for r in results]


# ---------------------------------------------------------------------------
# Sector Rotation endpoints
# ---------------------------------------------------------------------------

@app.get("/api/sectors/rotation")
async def sector_rotation(lookback_days: int = 90):
    """Sector RS ratio, momentum, and quadrant placement vs SPY."""
    return await get_sector_rotation(lookback_days)


@app.get("/api/sectors/heatmap")
async def sector_heatmap():
    """Multi-timeframe sector performance grid."""
    return await get_rotation_heatmap()


@app.get("/api/sectors/{sector_etf}/leaders")
async def sector_leaders(sector_etf: str, top_n: int = 10, period: str = "3mo"):
    """Top performing stocks within a sector."""
    return await get_sector_leaders(sector_etf.upper(), top_n, period)


# ---------------------------------------------------------------------------
# Backtesting endpoints
# ---------------------------------------------------------------------------

@app.post("/api/backtest/run")
async def api_backtest_run(req: BacktestRequest):
    """Run a backtest and return results. Does NOT save automatically."""
    from backtester import run_backtest
    try:
        result = await run_backtest(
            entry_conditions=req.entry_conditions,
            exit_conditions=req.exit_conditions,
            symbol=req.symbol.upper(),
            period=req.period,
            interval=req.interval,
            initial_capital=req.initial_capital,
            position_size_pct=req.position_size_pct,
            stop_loss_pct=req.stop_loss_pct,
            take_profit_pct=req.take_profit_pct,
            condition_logic=req.condition_logic,
        )
        return result
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        log.error("Backtest failed: %s", e, exc_info=True)
        raise HTTPException(500, "Internal error during backtest execution")


@app.post("/api/backtest/save")
async def api_backtest_save(req: BacktestSaveRequest):
    """Save a backtest result for later retrieval."""
    created_at = datetime.now(timezone.utc).isoformat()
    strategy_data = json.dumps({
        "entry_conditions": [c.model_dump() for c in req.result.entry_conditions],
        "exit_conditions": [c.model_dump() for c in req.result.exit_conditions],
        "condition_logic": req.result.condition_logic,
        "position_size_pct": req.result.position_size_pct,
        "stop_loss_pct": req.result.stop_loss_pct,
        "take_profit_pct": req.result.take_profit_pct,
    })
    result_data = req.result.model_dump_json()
    import uuid as _uuid_mod
    backtest_id = str(_uuid_mod.uuid4())
    await save_backtest(
        backtest_id=backtest_id,
        user_id="demo",
        name=req.name,
        strategy_data=strategy_data,
        result_data=result_data,
        created_at=created_at,
    )
    return {"id": backtest_id, "saved": True}


@app.get("/api/backtest/history")
async def api_backtest_history():
    """List saved backtests."""
    return await get_backtests(user_id="demo")


@app.get("/api/backtest/{backtest_id}")
async def api_backtest_get(backtest_id: str):
    """Retrieve a specific saved backtest."""
    result = await get_backtest(backtest_id)
    if not result:
        raise HTTPException(404, "Backtest not found")
    return result


@app.delete("/api/backtest/{backtest_id}")
async def api_backtest_delete(backtest_id: str):
    """Delete a saved backtest."""
    deleted = await delete_backtest(backtest_id)
    return {"deleted": deleted}


# ---------------------------------------------------------------------------
# Alerts endpoints
# ---------------------------------------------------------------------------

def _alert_condition_summary(alert: Alert | AlertCreate) -> str:
    params = getattr(alert.condition, "params", {}) or {}
    params_str = ", ".join(str(value) for value in params.values())
    indicator = f"{alert.condition.indicator}({params_str})" if params_str else alert.condition.indicator
    return f"{indicator} {alert.condition.operator} {alert.condition.value}"


async def _resolve_alert_test_price(symbol: str, fallback: float | None = None) -> float:
    sym = symbol.upper()
    price = await get_latest_price(sym)
    if price is not None:
        return float(price)
    try:
        quotes = await _yf_quotes(sym, source="price_fallback")
        if quotes and isinstance(quotes[0].get("price"), (int, float)):
            return float(quotes[0]["price"])
    except Exception:
        pass
    if fallback is not None:
        return float(fallback)
    return 0.0


@app.get("/api/alerts")
async def api_alerts_list(user=Depends(get_current_user)):
    alerts = await get_alerts(user.id)
    return [alert.model_dump() for alert in alerts]


@app.post("/api/alerts", status_code=201)
async def api_alerts_create(body: AlertCreate, user=Depends(get_current_user)):
    alert = Alert(
        user_id=user.id,
        name=body.name,
        symbol=body.symbol.upper(),
        condition=body.condition,
        alert_type=body.alert_type,
        cooldown_minutes=body.cooldown_minutes,
        enabled=body.enabled,
    )
    await save_alert(alert, user.id)
    return alert.model_dump()


@app.get("/api/alerts/history")
async def api_alerts_history(limit: int = 100, alert_id: str | None = None, user=Depends(get_current_user)):
    history = await get_alert_history(user.id, limit=limit, alert_id=alert_id)
    return [entry.model_dump() for entry in history]


@app.post("/api/alerts/test")
async def api_alerts_test(body: AlertCreate, user=Depends(get_current_user)):
    fallback = None
    if isinstance(body.condition.value, (int, float)):
        fallback = float(body.condition.value)
    price = await _resolve_alert_test_price(body.symbol, fallback=fallback)
    temp_alert = Alert(
        user_id=user.id,
        name=body.name,
        symbol=body.symbol.upper(),
        condition=body.condition,
        alert_type=body.alert_type,
        cooldown_minutes=body.cooldown_minutes,
        enabled=body.enabled,
    )
    summary = _alert_condition_summary(temp_alert)
    now = datetime.now(timezone.utc).isoformat()
    await _broadcast(
        {
            "type": "alert_fired",
            "alert_id": temp_alert.id,
            "name": temp_alert.name,
            "symbol": temp_alert.symbol,
            "condition_summary": summary,
            "price": price,
            "timestamp": now,
        }
    )
    return {
        "alert_id": temp_alert.id,
        "symbol": temp_alert.symbol,
        "price": price,
        "triggered": True,
        "condition_summary": summary,
    }


@app.get("/api/alerts/{alert_id}")
async def api_alerts_get(alert_id: str, user=Depends(get_current_user)):
    alert = await get_alert(alert_id, user.id)
    if not alert:
        raise HTTPException(404, "Alert not found")
    return alert.model_dump()


@app.put("/api/alerts/{alert_id}")
async def api_alerts_update(alert_id: str, body: AlertUpdate, user=Depends(get_current_user)):
    alert = await get_alert(alert_id, user.id)
    if not alert:
        raise HTTPException(404, "Alert not found")

    patch = body.model_dump(exclude_unset=True, exclude_none=True)
    if "symbol" in patch:
        patch["symbol"] = str(patch["symbol"]).upper()
    updated = alert.model_copy(update=patch)
    await save_alert(updated, user.id)
    return updated.model_dump()


@app.delete("/api/alerts/{alert_id}")
async def api_alerts_delete(alert_id: str, user=Depends(get_current_user)):
    deleted = await delete_alert(alert_id, user.id)
    if not deleted:
        raise HTTPException(404, "Alert not found")
    return {"deleted": True}


@app.post("/api/alerts/{alert_id}/toggle")
async def api_alerts_toggle(alert_id: str, user=Depends(get_current_user)):
    alert = await get_alert(alert_id, user.id)
    if not alert:
        raise HTTPException(404, "Alert not found")
    alert.enabled = not alert.enabled
    await save_alert(alert, user.id)
    return {"id": alert.id, "enabled": alert.enabled}


# ---------------------------------------------------------------------------
# Auth endpoints
# ---------------------------------------------------------------------------

@app.get("/api/auth/me")
async def auth_me(user=Depends(get_current_user)):
    return {"id": user.id, "email": user.email, "settings": user.settings}


@app.post("/api/auth/token")
async def auth_token():
    """Issue a demo token (for frontend bootstrap). Full login in Stage 8."""
    token = create_token("demo")
    return {"access_token": token, "token_type": "bearer"}


# ---------------------------------------------------------------------------
# Settings endpoints
# ---------------------------------------------------------------------------

@app.get("/api/settings")
async def get_user_settings(user=Depends(get_current_user)):
    return await get_settings(user.id)


@app.put("/api/settings")
async def update_user_settings(request: Request, user=Depends(get_current_user)):
    body = await request.json()
    return await update_settings(user.id, body)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host=cfg.HOST, port=cfg.PORT, reload=True)


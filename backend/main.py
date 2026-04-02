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
from runtime_state import initialize_runtime_state, reset_runtime_state

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
# WebSocket connection manager (extracted to ws_manager.py)
# ---------------------------------------------------------------------------
from ws_manager import ConnectionManager, manager, _broadcast  # noqa: E402

initialize_runtime_state(ws_manager=manager, data_health=_data_health, diag_service=_diag_service)


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
    initialize_runtime_state(ws_manager=manager, data_health=_data_health, diag_service=_diag_service)

    # Register order-fill ? tracked position lifecycle
    async def _on_trade_fill_register(trade: Trade) -> None:
        from services import order_lifecycle

        await order_lifecycle.register_entry_position_from_fill(trade, rule_name=trade.rule_name)
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

    # Sync autopilot mode from DB → cfg on startup (C-4/H-1 FIX)
    try:
        from ai_guardrails import _load_guardrails_from_db
        db_config = await _load_guardrails_from_db()
        mode = db_config.autopilot_mode
        if mode in ("OFF", "PAPER", "LIVE"):
            cfg.AUTOPILOT_MODE = mode
            cfg.AI_AUTONOMY_ENABLED = mode in ("PAPER", "LIVE")
            cfg.AI_SHADOW_MODE = mode == "OFF"
            from ai_params import ai_params
            ai_params.shadow_mode = mode == "OFF"
            log.info("Autopilot mode synced from DB: %s", mode)
    except Exception as e:
        log.warning("Failed to sync autopilot mode from DB: %s", e)

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
    reset_runtime_state()


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

# ── Extracted domain routers (Stage 1A)
from routers import register_routers
register_routers(app)


# ── Event system endpoints (moved to routers/events.py) ──────────────────────


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
# Shared live quote fanout state (extracted to ws_quote_state.py)
# ---------------------------------------------------------------------------
from ws_quote_state import (  # noqa: E402
    _ws_price_cache, _ws_ibkr_quotes, _ws_symbol_ref_counts,
    _ws_ibkr_subscribed_symbols, _market_dynamic_symbols, _ws_lock,
    _ws_last_ibkr_quote_ts, _ws_last_yahoo_quote_ts,
    _WS_CACHE_TTL, _WS_PUSH_INTERVAL, _WS_STALE_WARN_SECONDS,
    _WS_STALE_CRITICAL_SECONDS, _WS_HEARTBEAT_INTERVAL_SECONDS, _US_EASTERN,
    _normalize_symbol, _ws_symbol_variants, _market_state_from_schedule,
    _normalize_market_state, _build_ws_quote,
)


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
    except Exception as exc:
        log.debug("Coinbase exchange API failed for %s: %s", sym, exc)

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
    except Exception as exc:
        log.debug("Coinbase spot API failed for %s: %s", sym, exc)
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
                except Exception as exc:
                    log.debug("fast_info failed for %s: %s", candidate, exc)
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
                    except Exception as exc:
                        log.debug("yfinance download parse failed for %s: %s", sym, exc)
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
                    except Exception as exc:
                        log.debug("WS quote send failed, closing: %s", exc)
                        return

            now = _time.time()
            if now - last_heartbeat >= _WS_HEARTBEAT_INTERVAL_SECONDS:
                try:
                    await ws.send_text(
                        json.dumps({"type": "heartbeat", "time": int(now)})
                    )
                    last_heartbeat = now
                except Exception as exc:
                    log.debug("WS heartbeat send failed, closing: %s", exc)
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

# ── Status route (moved to routers/status.py) ────────────────────────────────


# -- Health check (already in health.py router) -------------------------------


# -- data/health route (moved to routers/status.py) --
# ---------------------------------------------------------------------------
# IBKR connection control
# ---------------------------------------------------------------------------

# -- IBKR connect/disconnect (moved to routers/status.py) ---------------------


# ---------------------------------------------------------------------------
# Account summary  (IBKR live | simulation)
# ---------------------------------------------------------------------------

# -- Account + Positions routes (moved to routers/positions.py) -----------------
# -- Account + Positions routes (moved to routers/positions.py) -----------------

# Simulation endpoints
# ---------------------------------------------------------------------------

# -- Simulation routes (moved to routers/simulation_routes.py) -----------------




# ---------------------------------------------------------------------------
# Replay / Playback endpoints
# ---------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# Orders
# ---------------------------------------------------------------------------

# ── Orders routes (moved to routers/orders.py) ───────────────────────────────


# ---------------------------------------------------------------------------
# Trades
# ---------------------------------------------------------------------------

# ── Trades + Rules + Bot routes (moved to routers/bot_routes.py, rules_routes.py) ──


# ---------------------------------------------------------------------------
# IBKR market data (real-time bars + historical)
# ---------------------------------------------------------------------------

# -- Market data routes (moved to routers/market_routes.py + yahoo_data.py) --


# ---------------------------------------------------------------------------
# Market heartbeat (extracted to market_heartbeat.py)
# ---------------------------------------------------------------------------
from market_heartbeat import (  # noqa: E402
    _start_market_heartbeat, _stop_market_heartbeat,
    _track_heartbeat_symbols, _untrack_heartbeat_symbols,
    _cache_prices_from_quotes, _all_heartbeat_symbols,
    _MARKET_HEARTBEAT_ENABLED, _DEFAULT_WATCHLIST,
)


# ---------------------------------------------------------------------------
# Screener endpoints
# ---------------------------------------------------------------------------

# ── Screener routes (moved to routers/screener_routes.py) ─────────────────────


# ---------------------------------------------------------------------------
# Sector Rotation endpoints
# ---------------------------------------------------------------------------

# ── Sector routes (moved to routers/sectors.py) ──────────────────────────────


# ---------------------------------------------------------------------------
# Backtesting endpoints
# ---------------------------------------------------------------------------

# ── Backtest routes (moved to routers/backtest_routes.py) ─────────────────────


# ---------------------------------------------------------------------------
# Alerts endpoints
# ---------------------------------------------------------------------------

# ── Alert routes (moved to routers/alerts_routes.py) ──────────────────────────


# ---------------------------------------------------------------------------
# Auth endpoints
# ---------------------------------------------------------------------------

# ── Auth routes (moved to routers/auth.py) ────────────────────────────────────
# ── Settings routes (moved to routers/settings_routes.py) ─────────────────────


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host=cfg.HOST, port=cfg.PORT, reload=True)






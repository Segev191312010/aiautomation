"""
FastAPI application — REST API, WebSocket, and static frontend serving.

Run:
    uvicorn main:app --host 0.0.0.0 --port 8000 --reload

Endpoints overview
------------------
GET  /api/status                        — system health
POST /api/ibkr/connect|disconnect       — IBKR connection control

GET  /api/account/summary               — normalised account KPIs (IBKR | sim | mock)
GET  /api/positions                     — live IBKR positions (or mock)

GET  /api/simulation/account            — virtual sim account
GET  /api/simulation/positions          — virtual sim positions
POST /api/simulation/order              — place a virtual order
POST /api/simulation/reset              — wipe virtual account

GET  /api/simulation/playback           — replay state
POST /api/simulation/playback/load      — load symbol + bars for replay
POST /api/simulation/playback/play      — start/resume replay
POST /api/simulation/playback/pause     — pause
POST /api/simulation/playback/stop      — reset to beginning
POST /api/simulation/playback/speed     — set replay speed

GET  /api/watchlist                     — quote cards for default watchlist symbols
GET  /api/yahoo/{symbol}/bars           — OHLCV bars via Yahoo Finance
GET  /api/market/{symbol}/price         — single price (IBKR or mock)
GET  /api/market/{symbol}/bars          — IBKR historical bars
POST /api/market/{symbol}/subscribe     — subscribe to 5-s real-time bars
POST /api/market/{symbol}/unsubscribe   — unsubscribe

GET  /api/orders                        — open IBKR orders
DELETE /api/orders/{id}                 — cancel IBKR order
POST /api/orders/manual                 — place manual IBKR order

GET|POST|PUT|DELETE /api/rules/*        — automation rules CRUD
POST /api/rules/{id}/toggle             — enable / disable rule

POST /api/bot/start|stop                — start / stop rule-evaluation loop
GET  /api/bot/status

GET  /api/trades                        — trade execution log

WS   /ws                                — general events (bot, fills, IBKR state)
WS   /ws/market-data                    — streaming price updates for a symbol list
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Literal

import time as _time

from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError

from config import cfg
from database import (
    delete_rule, get_rule, get_rules, get_trades, init_db, save_rule, save_trade,
)
from ibkr_client import ibkr
from market_data import (
    get_historical_bars, get_latest_price,
    subscribe_realtime_bars, unsubscribe_realtime_bars,
)
from mock_data import (
    get_mock_account_summary, get_mock_ohlcv, get_mock_price, get_mock_quotes,
)
from models import (
    AccountSummary, PlaybackState, Rule, RuleCreate, RuleUpdate,
    Trade, TradeAction,
)
from order_executor import cancel_order, get_open_orders, on_fill, place_order
from simulation import replay_engine, sim_engine
from auth import create_token, get_current_user
from indicators import calculate as ind_calculate, series_to_json
from settings import get_settings, update_settings
import bot_runner

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
log = logging.getLogger(__name__)


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
    # ── Startup ────────────────────────────────────────────────────────────
    await init_db()
    await sim_engine.initialize()

    bot_runner.set_broadcast(_broadcast)
    sim_engine.set_broadcast(_broadcast)
    replay_engine.set_broadcast(_broadcast)
    ibkr.set_broadcast(_broadcast)

    # Register order-fill → WS broadcast
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

    # Attempt IBKR connection (non-blocking)
    connected = await ibkr.connect()
    if connected:
        log.info("IBKR connected on startup")
        await ibkr.start_reconnect_loop()
    else:
        log.warning("IBKR not connected — auto-reconnect running in background")
        await ibkr.start_reconnect_loop()

    yield

    # ── Shutdown ────────────────────────────────────────────────────────────
    await bot_runner.stop()
    await replay_engine.stop()
    await ibkr.disconnect()


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="Trading Dashboard", version="2.0.0", lifespan=lifespan)

# ── Static assets ─────────────────────────────────────────────────────────

_FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend")
if os.path.isdir(_FRONTEND_DIR):
    app.mount("/static", StaticFiles(directory=_FRONTEND_DIR), name="static")

_DASHBOARD_DIR = os.path.join(os.path.dirname(__file__), cfg.DASHBOARD_BUILD_DIR)


# ---------------------------------------------------------------------------
# Global error handlers — all errors return {error, detail} JSON format
# ---------------------------------------------------------------------------

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": "HTTPException", "detail": str(exc.detail)},
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
        content={"error": type(exc).__name__, "detail": str(exc)},
    )


# ---------------------------------------------------------------------------
# Request logging middleware
# ---------------------------------------------------------------------------

@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = _time.perf_counter()
    response = await call_next(request)
    duration_ms = (_time.perf_counter() - start) * 1000
    log.info("%s %s → %d (%.0fms)", request.method, request.url.path, response.status_code, duration_ms)
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
    full = os.path.join(_DASHBOARD_DIR, "assets", file_path)
    if os.path.isfile(full):
        return FileResponse(full)
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
# General WebSocket
# ---------------------------------------------------------------------------

@app.websocket("/ws")
async def ws_general(ws: WebSocket):
    await manager.connect(ws)
    try:
        while True:
            data = await ws.receive_text()
            if data == "ping":
                await ws.send_text(json.dumps({"type": "pong"}))
    except WebSocketDisconnect:
        manager.disconnect(ws)


# ---------------------------------------------------------------------------
# Dedicated market-data WebSocket
# Clients send:  {"action":"subscribe","symbols":["AAPL","BTC-USD"]}
#                {"action":"unsubscribe","symbols":["AAPL"]}
# Server pushes: {"type":"quote","symbol":"AAPL","price":220.0,"change_pct":1.2,...}
# ---------------------------------------------------------------------------

@app.websocket("/ws/market-data")
async def ws_market_data(ws: WebSocket):
    await ws.accept()
    subscribed: set[str] = set()
    push_task: asyncio.Task | None = None

    async def _push_loop() -> None:
        while True:
            for sym in list(subscribed):
                price = (
                    get_mock_price(sym)
                    if (not ibkr.is_connected() and cfg.MOCK_MODE)
                    else (await get_latest_price(sym) or get_mock_price(sym))
                )
                try:
                    await ws.send_text(
                        json.dumps({
                            "type":   "quote",
                            "symbol": sym,
                            "price":  price,
                            "time":   int(datetime.now(timezone.utc).timestamp()),
                        })
                    )
                except Exception:
                    return
            await asyncio.sleep(1)

    try:
        push_task = asyncio.create_task(_push_loop())
        while True:
            raw = await ws.receive_text()
            msg = json.loads(raw)
            action  = msg.get("action", "")
            symbols = [s.upper() for s in msg.get("symbols", [])]
            if action == "subscribe":
                subscribed.update(symbols)
            elif action == "unsubscribe":
                subscribed.difference_update(symbols)
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        if push_task:
            push_task.cancel()


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

@app.get("/api/status")
async def get_status():
    return {
        "ibkr_connected":       ibkr.is_connected(),
        "ibkr_host":            cfg.IBKR_HOST,
        "ibkr_port":            cfg.IBKR_PORT,
        "is_paper":             cfg.IS_PAPER,
        "sim_mode":             cfg.SIM_MODE,
        "mock_mode":            cfg.MOCK_MODE,
        "bot_running":          bot_runner.is_running(),
        "last_run":             bot_runner.get_last_run(),
        "next_run":             bot_runner.get_next_run(),
        "bot_interval_seconds": cfg.BOT_INTERVAL_SECONDS,
    }


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
# Account summary  (normalised — IBKR live | simulation | mock)
# ---------------------------------------------------------------------------

@app.get("/api/account/summary")
async def get_account_summary():
    """
    Returns a unified account summary regardless of connection mode:
      - SIM_MODE → virtual sim account
      - IBKR connected → real account summary
      - fallback → mock data
    """
    if cfg.SIM_MODE:
        account = await sim_engine.get_account()
        return account.model_dump()

    if ibkr.is_connected():
        try:
            summary = await ibkr.get_account_summary()
            return summary.model_dump()
        except Exception as exc:
            log.warning("Account fetch failed: %s — falling back to mock", exc)

    if cfg.MOCK_MODE:
        return get_mock_account_summary()

    raise HTTPException(503, "IBKR not connected and MOCK_MODE is disabled")


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
        if ibkr.is_connected():
            return None   # will be resolved asynchronously by get_positions
        return get_mock_price(sym) if cfg.MOCK_MODE else None

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
    symbol: str
    period: str = "1y"    # Yahoo Finance period
    interval: str = "1d"  # Yahoo Finance interval


@app.post("/api/simulation/playback/load")
async def playback_load(body: LoadReplayRequest):
    sym = body.symbol.upper()
    # Try Yahoo Finance first; fall back to mock data
    bars: list[dict] = []
    try:
        bars = await _yf_bars(sym, body.period, body.interval)
    except Exception as exc:
        log.warning("Yahoo bars failed for replay (%s): %s — using mock data", sym, exc)

    if not bars:
        num = 252 if "d" in body.interval else 100
        bars = get_mock_ohlcv(sym, num_bars=num)

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
    """Place a manual order — routes to sim if SIM_MODE, else IBKR."""
    if cfg.SIM_MODE:
        price = (
            get_mock_price(body.symbol.upper())
            if not ibkr.is_connected()
            else (await get_latest_price(body.symbol.upper()) or get_mock_price(body.symbol.upper()))
        )
        ok, msg = await sim_engine.execute_order(
            symbol=body.symbol.upper(),
            action=body.action,
            qty=float(body.quantity),
            price=price,
        )
        if not ok:
            raise HTTPException(400, msg)
        return {"success": True, "message": msg, "sim": True}

    if not ibkr.is_connected():
        raise HTTPException(503, "IBKR not connected — start IB Gateway first")

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
    trade = await place_order(rule)
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
        if cfg.MOCK_MODE:
            return get_mock_ohlcv(symbol.upper(), num_bars=90)
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
    if ibkr.is_connected():
        price = await get_latest_price(symbol.upper())
        if price is not None:
            return {"symbol": symbol.upper(), "price": price, "is_mock": False}
    if cfg.MOCK_MODE:
        return {"symbol": symbol.upper(), "price": get_mock_price(symbol.upper()), "is_mock": True}
    raise HTTPException(503, "IBKR not connected and MOCK_MODE disabled")


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
# Yahoo Finance — watchlist quotes + bars
# ---------------------------------------------------------------------------

async def _yf_quotes(symbols_str: str) -> list[dict]:
    import yfinance as yf
    from concurrent.futures import ThreadPoolExecutor

    syms = [s.strip() for s in symbols_str.split(",") if s.strip()]

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
                "is_mock":    False,
            }
        except Exception as e:
            log.warning("yfinance error %s: %s", sym, e)
            return None

    def _all():
        with ThreadPoolExecutor(max_workers=min(len(syms), 10)) as ex:
            return [r for r in ex.map(_one, syms) if r is not None]

    return await asyncio.to_thread(_all)


async def _yf_bars(symbol: str, period: str, interval: str) -> list[dict]:
    def _fetch():
        import yfinance as yf
        df = yf.Ticker(symbol).history(period=period, interval=interval)
        if df.empty:
            return []
        return [
            {
                "time":   int(ts.timestamp()),
                "open":   round(float(row["Open"]),  4),
                "high":   round(float(row["High"]),  4),
                "low":    round(float(row["Low"]),   4),
                "close":  round(float(row["Close"]), 4),
                "volume": int(row["Volume"]),
            }
            for ts, row in df.iterrows()
        ]
    return await asyncio.to_thread(_fetch)


_DEFAULT_WATCHLIST = "BTC-USD,ETH-USD,AAPL,TSLA,SPY,QQQ,NVDA"


@app.get("/api/watchlist")
async def get_watchlist_quotes(symbols: str = _DEFAULT_WATCHLIST):
    syms = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    try:
        quotes = await _yf_quotes(",".join(syms))
        if quotes:
            return quotes
    except Exception as exc:
        log.warning("Yahoo Finance failed: %s — using mock data", exc)

    if cfg.MOCK_MODE:
        return get_mock_quotes(syms)

    raise HTTPException(503, "No market data available")


# ── Yahoo Finance interval validation ──────────────────────────────────────

_VALID_INTERVALS = {"1m", "2m", "5m", "15m", "30m", "60m", "90m", "1h", "1d", "5d", "1wk", "1mo", "3mo"}
_UNSUPPORTED_INTERVALS = {"4h", "2h"}  # yfinance does not support these natively

# period string → approximate days
def _period_to_days(p: str) -> int:
    p = p.lower()
    if p.endswith("d"):   return int(p[:-1])
    if p.endswith("mo"):  return int(p[:-2]) * 30
    if p.endswith("y"):   return int(p[:-1]) * 365
    return 9999  # "max" or unknown → unlimited

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
                f"{interval} interval requires period <= {max_days}d, but '{period}' ≈ {req_days}d",
            )

    try:
        bars = await _yf_bars(symbol, period, interval)
        if bars:
            return bars
    except Exception as exc:
        log.warning("Yahoo bars failed for %s: %s", symbol, exc)

    if cfg.MOCK_MODE:
        num = 100 if "d" in interval else 200
        sec = 86_400 if interval.endswith("d") else 300
        return get_mock_ohlcv(symbol.upper(), num_bars=num, bar_seconds=sec)

    raise HTTPException(404, f"No data for {symbol}")


# ── Server-side indicator endpoint ─────────────────────────────────────────

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
        if cfg.MOCK_MODE:
            num = 200 if "d" in interval else 300
            sec = 86_400 if interval.endswith("d") else 300
            bars = get_mock_ohlcv(symbol.upper(), num_bars=num, bar_seconds=sec)
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

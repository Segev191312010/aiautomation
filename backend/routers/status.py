"""Status & IBKR connection routes - /api/status, /api/health, /api/data/health, /api/ibkr/*"""
from __future__ import annotations

import logging
import os
import time as _time

from fastapi import APIRouter, Depends, HTTPException

from auth import get_current_user
import bot_runner
from config import cfg
from ibkr_client import ibkr
from runtime_state import get_data_health, get_diag_service
from startup import get_execution_fencing_token
from db.execution_lease import validate_fencing_token

log = logging.getLogger(__name__)

router = APIRouter(tags=["status"])

_START_TIME = _time.time()


@router.get("/api/health")
async def health_check():
    """Deep health check — DB writable, IBKR status, bot alive, memory."""
    checks: dict = {}
    overall = "healthy"

    # DB writable check
    try:
        from database import get_db
        t0 = _time.time()
        async with get_db() as db:
            await db.execute("SELECT 1")
        checks["database"] = {"status": "ok", "latency_ms": round((_time.time() - t0) * 1000, 1)}
    except Exception as e:
        checks["database"] = {"status": "error", "detail": str(e)}
        overall = "unhealthy"

    # IBKR connection
    connected = ibkr.is_connected()
    if cfg.SIM_MODE:
        checks["ibkr"] = {"status": "skipped", "detail": "SIM_MODE"}
    elif connected:
        checks["ibkr"] = {"status": "connected"}
    else:
        checks["ibkr"] = {"status": "disconnected"}
        overall = "degraded" if overall == "healthy" else overall

    # Bot status
    bot_running = bot_runner.is_running()
    last_run = bot_runner.get_last_run()
    next_run = bot_runner.get_next_run()
    checks["bot"] = {
        "status": "running" if bot_running else "stopped",
        "last_cycle": last_run,
        "next_cycle": next_run,
    }

    # Memory usage
    try:
        import psutil
        proc = psutil.Process(os.getpid())
        checks["memory_mb"] = round(proc.memory_info().rss / 1024 / 1024, 1)
    except ImportError:
        checks["memory_mb"] = None

    # Execution ownership (Stage 9B Phase 1 SF1a)
    lease_token = get_execution_fencing_token()
    lease = await validate_fencing_token(lease_token) if lease_token else None
    checks["execution_lease"] = {
        "status": "held" if lease else "not_held",
        "owner": lease.owner_id if lease else None,
        "expires_at": lease.expires_at if lease else None,
    }
    if not lease:
        overall = "degraded" if overall == "healthy" else overall

    return {
        "status": overall,
        "checks": checks,
        "uptime_seconds": round(_time.time() - _START_TIME, 0),
        "version": "1.0.0-beta",
    }


@router.get("/api/status")
async def get_status():
    from ai_guardrails import get_autopilot_config_dict

    autopilot = await get_autopilot_config_dict()
    return {
        "ibkr_connected": ibkr.is_connected(),
        "is_paper": cfg.IS_PAPER,
        "sim_mode": cfg.SIM_MODE,
        "bot_running": bot_runner.is_running(),
        "last_run": bot_runner.get_last_run(),
        "next_run": bot_runner.get_next_run(),
        "bot_interval_seconds": cfg.BOT_INTERVAL_SECONDS,
        "autopilot_mode": autopilot["autopilot_mode"],
        "autopilot_emergency_stop": autopilot["emergency_stop"],
        "autopilot_daily_loss_locked": autopilot["daily_loss_locked"],
        "features": {
            "market_diagnostics": cfg.ENABLE_MARKET_DIAGNOSTICS,
            "autopilot_console": True,
        },
    }


@router.get("/api/data/health")
async def get_data_health_route():
    import time as _time

    data_health = get_data_health()
    diag_service = get_diag_service()
    if not data_health:
        return {"error": "Data health monitor not initialized"}

    snapshot = data_health.snapshot()

    try:
        import ws_quote_state

        now = _time.time()
        ibkr_age = None if ws_quote_state._ws_last_ibkr_quote_ts <= 0 else round(max(0.0, now - ws_quote_state._ws_last_ibkr_quote_ts), 3)
        yahoo_age = None if ws_quote_state._ws_last_yahoo_quote_ts <= 0 else round(max(0.0, now - ws_quote_state._ws_last_yahoo_quote_ts), 3)
        with ws_quote_state._ws_lock:
            active_symbols = sum(1 for count in ws_quote_state._ws_symbol_ref_counts.values() if count > 0)
            ibkr_symbols = len(ws_quote_state._ws_ibkr_subscribed_symbols)
        snapshot["streaming"] = {
            "push_interval_s": ws_quote_state._WS_PUSH_INTERVAL,
            "cache_ttl_s": ws_quote_state._WS_CACHE_TTL,
            "stale_warn_s": ws_quote_state._WS_STALE_WARN_SECONDS,
            "stale_critical_s": ws_quote_state._WS_STALE_CRITICAL_SECONDS,
            "active_symbols": active_symbols,
            "ibkr_subscribed_symbols": ibkr_symbols,
            "ibkr_connected": ibkr.is_connected(),
            "ibkr_last_quote_age_s": ibkr_age,
            "yahoo_last_quote_age_s": yahoo_age,
        }
    except Exception as exc:
        log.debug("Data health streaming metadata unavailable: %s", exc)
        snapshot.setdefault("streaming", {})

    snapshot["diagnostics"] = {
        "enabled": bool(getattr(diag_service, "enabled", False)),
    }
    return snapshot


@router.post("/api/ibkr/connect")
async def connect_ibkr(_user=Depends(get_current_user)):
    if cfg.SIM_MODE:
        raise HTTPException(
            409,
            "IBKR connection is disabled while SIM_MODE=true. "
            "Restart with an approved broker-capable configuration.",
        )
    from startup import real_money_broker_configured

    if real_money_broker_configured(
        is_paper=cfg.IS_PAPER,
        sim_mode=cfg.SIM_MODE,
        ibkr_port=cfg.IBKR_PORT,
    ):
        raise HTTPException(
            409,
            "Real-money IBKR connection is disabled by the Stage 9A release fence.",
        )
    # Cross-host execution lease: only the current lease holder may open a
    # broker connection.  This prevents a stale or duplicate process from
    # attaching to TWS/Gateway.
    if not await validate_fencing_token(get_execution_fencing_token()):
        raise HTTPException(
            409,
            "Execution lease invalid or held by another process. "
            "Cannot open broker connection.",
        )
    ok = await ibkr.connect()
    if not ok:
        raise HTTPException(502, "Could not connect to IBKR. Is IB Gateway running?")
    await ibkr.start_reconnect_loop()
    return {"connected": True}


@router.post("/api/ibkr/disconnect")
async def disconnect_ibkr(_user=Depends(get_current_user)):
    # Disconnect is safe from any process; it only drops the local socket.
    await ibkr.disconnect()
    return {"connected": False}

"""Status & IBKR connection routes - /api/status, /api/data/health, /api/ibkr/*"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from auth import get_current_user
import bot_runner
from config import cfg
from ibkr_client import ibkr
from runtime_state import get_data_health, get_diag_service

log = logging.getLogger(__name__)

router = APIRouter(tags=["status"])


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
        import main as main_module

        now = _time.time()
        ibkr_age = None if main_module._ws_last_ibkr_quote_ts <= 0 else round(max(0.0, now - main_module._ws_last_ibkr_quote_ts), 3)
        yahoo_age = None if main_module._ws_last_yahoo_quote_ts <= 0 else round(max(0.0, now - main_module._ws_last_yahoo_quote_ts), 3)
        with main_module._ws_lock:
            active_symbols = sum(1 for count in main_module._ws_symbol_ref_counts.values() if count > 0)
            ibkr_symbols = len(main_module._ws_ibkr_subscribed_symbols)
        snapshot["streaming"] = {
            "push_interval_s": main_module._WS_PUSH_INTERVAL,
            "cache_ttl_s": main_module._WS_CACHE_TTL,
            "stale_warn_s": main_module._WS_STALE_WARN_SECONDS,
            "stale_critical_s": main_module._WS_STALE_CRITICAL_SECONDS,
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
    ok = await ibkr.connect()
    if not ok:
        raise HTTPException(502, "Could not connect to IBKR. Is IB Gateway running?")
    await ibkr.start_reconnect_loop()
    return {"connected": True}


@router.post("/api/ibkr/disconnect")
async def disconnect_ibkr(_user=Depends(get_current_user)):
    await ibkr.disconnect()
    return {"connected": False}

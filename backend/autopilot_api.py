"""Autopilot API — control plane, status, kill switch, daily loss lock."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from config import cfg
from safety_kernel import is_autopilot_live, is_autopilot_active, get_autopilot_mode

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/autopilot", tags=["autopilot"])


# ── Status ───────────────────────────────────────────────────────────────────

@router.get("/status")
async def get_autopilot_status():
    """Current autopilot state — mode, kill switch, daily loss, broker, counts."""
    from ai_guardrails import _load_guardrails_from_db
    from database import get_open_positions, get_rules

    config = await _load_guardrails_from_db()
    positions = await get_open_positions()
    rules = await get_rules()

    ai_rules = [r for r in rules if getattr(r, 'ai_generated', False)]

    return {
        "mode": get_autopilot_mode(),
        "kill_switch_active": config.emergency_stop,
        "daily_loss_locked": getattr(config, 'daily_loss_locked', False),
        "broker_connected": not cfg.SIM_MODE,  # simplified — real check in bot_runner
        "open_positions_count": len(positions),
        "active_rules_count": len([r for r in rules if r.enabled]),
        "ai_rules_count": len(ai_rules),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ── Mode Control ─────────────────────────────────────────────────────────────

class ModeRequest(BaseModel):
    mode: str  # OFF, PAPER, LIVE
    reason: str = ""


@router.post("/mode")
async def set_autopilot_mode(request: ModeRequest):
    """Change autopilot mode. Validates transitions."""
    mode = request.mode.upper()
    if mode not in ("OFF", "PAPER", "LIVE"):
        raise HTTPException(400, f"Invalid mode: {mode}. Must be OFF, PAPER, or LIVE.")

    current = get_autopilot_mode()
    if current == mode:
        return {"mode": mode, "message": f"Already in {mode} mode"}

    # Update config (runtime only — persists via env var or DB in future)
    cfg.AUTOPILOT_MODE = mode
    cfg._apm = mode
    cfg.AI_AUTONOMY_ENABLED = mode in ("PAPER", "LIVE")
    cfg.AI_SHADOW_MODE = mode != "LIVE"

    from ai_params import ai_params
    ai_params.shadow_mode = mode != "LIVE"

    log.info("Autopilot mode changed: %s → %s (reason: %s)", current, mode, request.reason)

    return {
        "mode": mode,
        "previous": current,
        "message": f"Autopilot switched to {mode}",
    }


# ── Kill Switch ──────────────────────────────────────────────────────────────

@router.post("/kill")
async def kill_autopilot():
    """Emergency stop — blocks ALL new AI entries immediately."""
    from ai_guardrails import _load_guardrails_from_db, save_guardrails_to_db

    config = await _load_guardrails_from_db()
    config = config.model_copy(update={"emergency_stop": True})
    await save_guardrails_to_db(config)

    cfg.AUTOPILOT_MODE = "OFF"
    cfg._apm = "OFF"
    cfg.AI_AUTONOMY_ENABLED = False
    cfg.AI_SHADOW_MODE = True

    from ai_params import ai_params
    ai_params.shadow_mode = True

    log.critical("AUTOPILOT KILLED — all AI entries blocked")

    return {
        "killed": True,
        "mode": "OFF",
        "message": "Autopilot KILLED — all AI activity stopped. Exits still allowed.",
    }


@router.post("/reset-kill")
async def reset_kill():
    """Reset kill switch — returns to OFF mode (must manually set PAPER/LIVE)."""
    from ai_guardrails import _load_guardrails_from_db, save_guardrails_to_db

    config = await _load_guardrails_from_db()
    config = config.model_copy(update={"emergency_stop": False})
    await save_guardrails_to_db(config)

    log.info("Kill switch reset — autopilot in OFF mode, set PAPER or LIVE to resume")

    return {
        "killed": False,
        "mode": get_autopilot_mode(),
        "message": "Kill switch reset. Set mode to PAPER or LIVE to resume.",
    }


# ── Daily Loss Lock ──────────────────────────────────────────────────────────

@router.post("/daily-lock/reset")
async def reset_daily_lock():
    """Reset daily loss lock — allows new entries again."""
    from ai_guardrails import _load_guardrails_from_db, save_guardrails_to_db

    config = await _load_guardrails_from_db()
    if hasattr(config, 'daily_loss_locked'):
        config = config.model_copy(update={"daily_loss_locked": False})
        await save_guardrails_to_db(config)

    log.info("Daily loss lock reset — new entries allowed")

    return {"locked": False, "message": "Daily loss lock reset."}

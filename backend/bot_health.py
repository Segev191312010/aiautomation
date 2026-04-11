"""
Bot health state tracking — cycle counters, error timestamps, IBKR heartbeat.

Extracted from bot_runner.py to reduce god-file size and allow independent testing.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Callable, Optional

from config import cfg

log = logging.getLogger(__name__)

# ── Bot health state ────────────────────────────────────────────────────────
_cycle_day: Optional[str] = None
_cycle_count_today: int = 0
_error_timestamps: list[float] = []
_degraded_timestamps: list[float] = []
_last_error_message: Optional[str] = None
_last_signal_symbol: Optional[str] = None
_last_cycle_started_at: Optional[str] = None
_last_cycle_completed_at: Optional[str] = None
_last_successful_ibkr_heartbeat_at: Optional[str] = None
_last_order_submit_at: Optional[str] = None
_last_fill_event_at: Optional[str] = None
_last_bot_health_emit_at: float = 0.0

# Broadcast callback — set by bot_runner at startup
_broadcast: Optional[Callable] = None


def set_health_broadcast(fn: Callable) -> None:
    global _broadcast
    _broadcast = fn


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _prune_timestamps(bucket: list[float], *, window_seconds: int = 86_400) -> None:
    cutoff = time.time() - window_seconds
    while bucket and bucket[0] < cutoff:
        bucket.pop(0)


def record_cycle_start() -> None:
    global _cycle_day, _cycle_count_today, _last_cycle_started_at
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if _cycle_day != today:
        _cycle_day = today
        _cycle_count_today = 0
    _cycle_count_today += 1
    _last_cycle_started_at = _now_iso()


def record_cycle_complete() -> None:
    global _last_cycle_completed_at
    _last_cycle_completed_at = _now_iso()


def record_bot_error(message: str) -> None:
    global _last_error_message
    _error_timestamps.append(time.time())
    _prune_timestamps(_error_timestamps)
    _last_error_message = message


def record_degraded_event() -> None:
    _degraded_timestamps.append(time.time())
    _prune_timestamps(_degraded_timestamps)


def record_signal_symbol(symbol: str | None) -> None:
    global _last_signal_symbol
    if symbol:
        _last_signal_symbol = symbol.upper()


def record_ibkr_heartbeat() -> None:
    global _last_successful_ibkr_heartbeat_at
    _last_successful_ibkr_heartbeat_at = _now_iso()


def record_order_submit() -> None:
    global _last_order_submit_at
    _last_order_submit_at = _now_iso()


def record_fill_event() -> None:
    global _last_fill_event_at
    _last_fill_event_at = _now_iso()


def get_bot_health(*, is_running: bool = False) -> dict:
    _prune_timestamps(_error_timestamps)
    _prune_timestamps(_degraded_timestamps)

    now = datetime.now(timezone.utc)
    minutes_since_last_cycle: float | None = None
    stale_warning = False
    ibkr_connected = False

    if _last_cycle_completed_at:
        try:
            last_cycle = datetime.fromisoformat(_last_cycle_completed_at.replace("Z", "+00:00"))
            minutes_since_last_cycle = round((now - last_cycle).total_seconds() / 60.0, 2)
            # Use bot interval (not WS threshold) — bot cycles every BOT_INTERVAL_SECONDS
            stale_threshold = max(cfg.BOT_INTERVAL_SECONDS * 2, 60)
            stale_warning = (now - last_cycle).total_seconds() > stale_threshold
        except (TypeError, ValueError):
            minutes_since_last_cycle = None

    if cfg.SIM_MODE:
        ibkr_connected = False
    else:
        try:
            from ibkr_client import ibkr as _ibkr_health

            ibkr_connected = bool(_ibkr_health.is_connected())
            if ibkr_connected:
                record_ibkr_heartbeat()
        except Exception as exc:
            log.debug("Health probe: ibkr_connected check failed: %s", exc)
            ibkr_connected = bool(_last_successful_ibkr_heartbeat_at)

    # Surface bull/bear debate parse-failure count so silent degradation to
    # NEUTRAL is visible in health telemetry (P2-3 / F2-08).
    try:
        from ai_advisor import get_debate_failure_count
        ai_debate_parse_failures_24h = int(get_debate_failure_count())
    except Exception:
        ai_debate_parse_failures_24h = 0

    return {
        "is_running": is_running,
        "minutes_since_last_cycle": minutes_since_last_cycle,
        "total_cycles_today": _cycle_count_today,
        "error_count_24h": len(_error_timestamps),
        "ibkr_connected": ibkr_connected,
        "stale_warning": stale_warning,
        "last_error_message": _last_error_message,
        "last_signal_symbol": _last_signal_symbol,
        "last_successful_ibkr_heartbeat_at": _last_successful_ibkr_heartbeat_at,
        "last_order_submit_at": _last_order_submit_at,
        "last_fill_event_at": _last_fill_event_at,
        "degraded_mode_count_24h": len(_degraded_timestamps),
        "ai_debate_parse_failures_24h": ai_debate_parse_failures_24h,
    }


async def emit_bot_health(*, is_running: bool = False, force: bool = False) -> None:
    global _last_bot_health_emit_at
    if not cfg.ENABLE_BOT_HEALTH_MONITORING:
        return
    now = time.time()
    if not force and (now - _last_bot_health_emit_at) < 60:
        return
    _last_bot_health_emit_at = now
    if _broadcast:
        await _broadcast({
            "type": "bot_health",
            **get_bot_health(is_running=is_running),
        })

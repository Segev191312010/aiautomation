"""
Safety kernel for AI-managed trading actions.

This module owns non-negotiable runtime checks for Autopilot-controlled
entries. It is intentionally conservative and shared by both AI rule
management and direct AI trade execution.

Includes consecutive failure circuit breaker (inspired by nofx):
when AI fails N times in a row, auto-activates emergency stop to protect positions.
"""
from __future__ import annotations

import logging
import time
from collections import defaultdict

from config import cfg

log = logging.getLogger(__name__)

_recent_checks: dict[str, float] = {}
DEDUP_WINDOW = 5  # seconds

# ── Consecutive Failure Circuit Breaker ─────────────────────────────────────
# Tracks AI failures per source. After CONSECUTIVE_FAILURE_THRESHOLD failures
# without any success, auto-triggers emergency stop.

CONSECUTIVE_FAILURE_THRESHOLD = int(
    getattr(cfg, "AI_CONSECUTIVE_FAILURE_THRESHOLD", 3)
)

_failure_counts: dict[str, int] = defaultdict(int)
_last_failure_times: dict[str, float] = {}
_breaker_tripped = False


def record_ai_success(source: str) -> None:
    """Record a successful AI action — resets the failure counter for this source."""
    prev = _failure_counts.get(source, 0)
    _failure_counts[source] = 0
    if prev > 0:
        log.info("AI failure counter reset for '%s' (was %d)", source, prev)


def record_ai_failure(source: str) -> bool:
    """
    Record a failed AI action. Returns True if the circuit breaker tripped.

    When any source accumulates CONSECUTIVE_FAILURE_THRESHOLD failures without
    an intervening success, the breaker trips and emergency_stop is activated.
    """
    global _breaker_tripped
    _failure_counts[source] += 1
    _last_failure_times[source] = time.time()
    count = _failure_counts[source]

    log.warning(
        "AI failure #%d for '%s' (threshold=%d)",
        count, source, CONSECUTIVE_FAILURE_THRESHOLD,
    )

    if count >= CONSECUTIVE_FAILURE_THRESHOLD and not _breaker_tripped:
        _breaker_tripped = True
        log.critical(
            "CIRCUIT BREAKER TRIPPED: %d consecutive AI failures from '%s' — "
            "activating emergency stop to protect positions",
            count, source,
        )
        return True
    return False


async def trip_circuit_breaker(source: str) -> None:
    """Activate emergency stop due to consecutive AI failures."""
    try:
        from ai_guardrails import _load_guardrails_from_db, save_guardrails_to_db, log_ai_action

        config = await _load_guardrails_from_db()
        if not config.emergency_stop:
            config.emergency_stop = True
            await save_guardrails_to_db(config)
            await log_ai_action(
                action_type="circuit_breaker_trip",
                category="safety",
                description=(
                    f"Emergency stop activated: {_failure_counts[source]} consecutive "
                    f"AI failures from '{source}'"
                ),
                old_value={"emergency_stop": False},
                new_value={"emergency_stop": True, "trigger_source": source},
                reason=f"Consecutive failure threshold ({CONSECUTIVE_FAILURE_THRESHOLD}) exceeded",
                confidence=1.0,
                status="applied",
            )
            log.critical("Emergency stop ACTIVATED by circuit breaker (source=%s)", source)
    except Exception as exc:
        log.error("Failed to activate emergency stop via circuit breaker: %s", exc)


def reset_circuit_breaker() -> None:
    """Manually reset the circuit breaker (e.g., after human review)."""
    global _breaker_tripped
    _breaker_tripped = False
    _failure_counts.clear()
    _last_failure_times.clear()
    log.info("Circuit breaker manually reset")


def get_failure_status() -> dict:
    """Return current failure tracking state for diagnostics."""
    return {
        "breaker_tripped": _breaker_tripped,
        "threshold": CONSECUTIVE_FAILURE_THRESHOLD,
        "counts": dict(_failure_counts),
        "last_failure_times": {
            k: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(v))
            for k, v in _last_failure_times.items()
        },
    }


class SafetyViolation(Exception):
    """Raised when an AI action violates a hard safety rule."""


async def check_all(
    symbol: str,
    side: str,
    quantity: int,
    source: str,
    account_equity: float = 0,
    price_estimate: float = 0,
    *,
    stop_price: float | None = None,
    is_exit: bool = False,
    has_existing_position: bool = False,
    require_autopilot_authority: bool = True,
) -> None:
    """Run the shared runtime safety checks for an order."""
    if require_autopilot_authority and not is_exit:
        await assert_not_killed()
        await assert_daily_loss_not_locked(is_exit=False)
    assert_no_shorts(side, is_exit=is_exit, has_existing_position=has_existing_position)
    if not is_exit:
        assert_risk_budget(quantity, price_estimate, account_equity, stop_price=stop_price)
        assert_not_duplicate(symbol, side, source)
    log.debug(
        "Safety kernel PASS: side=%s symbol=%s qty=%s source=%s exit=%s authority=%s",
        side,
        symbol,
        quantity,
        source,
        is_exit,
        require_autopilot_authority,
    )


async def assert_not_killed() -> None:
    """Reject if the Autopilot kill switch is active."""
    try:
        from ai_guardrails import _load_guardrails_from_db

        config = await _load_guardrails_from_db()
        if config.autopilot_mode == "OFF":
            raise SafetyViolation("Autopilot is OFF")
        if config.emergency_stop:
            raise SafetyViolation("Kill switch active - all new AI entries blocked")
    except SafetyViolation:
        raise
    except Exception as exc:
        log.error("Kill switch check FAILED (DB unavailable) - blocking for safety: %s", exc)
        raise SafetyViolation("Kill switch check unavailable - blocking for safety")


async def assert_daily_loss_not_locked(*, is_exit: bool = False) -> None:
    """Reject new entries if the daily loss lock is active."""
    if is_exit:
        return
    try:
        from ai_guardrails import _load_guardrails_from_db

        config = await _load_guardrails_from_db()
        if config.daily_loss_locked:
            raise SafetyViolation("Daily loss lock active - new AI entries blocked")
    except SafetyViolation:
        raise
    except Exception as exc:
        log.error("Daily loss check FAILED (DB unavailable) - blocking for safety: %s", exc)
        raise SafetyViolation("Daily loss check unavailable - blocking for safety")


def assert_no_shorts(side: str, *, is_exit: bool = False, has_existing_position: bool = False) -> None:
    """Block any sell-to-open style action."""
    if side.upper() == "SELL" and not is_exit and not has_existing_position:
        raise SafetyViolation("Short entries are disabled")


def assert_risk_budget(
    quantity: int,
    price_estimate: float,
    account_equity: float,
    *,
    stop_price: float | None = None,
) -> None:
    """
    Enforce the 1% hard risk limit.

    For direct AI trades we expect a stop price and measure true per-share risk.
    For rule-driven entries without a concrete stop, we conservatively fall back
    to order notional.
    """
    if quantity <= 0 or account_equity <= 0:
        return
    if price_estimate <= 0:
        raise SafetyViolation("price_estimate is zero - cannot verify risk budget")

    max_risk = account_equity * cfg.RISK_PER_TRADE_PCT / 100
    if stop_price is not None and stop_price > 0:
        risk_amount = abs(price_estimate - stop_price) * quantity
    else:
        risk_amount = quantity * price_estimate

    if risk_amount > max_risk:
        raise SafetyViolation(
            f"Per-trade risk ${risk_amount:.2f} exceeds {cfg.RISK_PER_TRADE_PCT:.2f}% "
            f"of net liq (${max_risk:.2f})"
        )


def assert_not_duplicate(symbol: str, side: str, source: str) -> None:
    """Reject duplicate orders within a small rolling window."""
    key = f"{symbol.upper()}:{side.upper()}:{source}"
    now = time.time()

    stale = [k for k, ts in _recent_checks.items() if (now - ts) > DEDUP_WINDOW * 2]
    for key_to_remove in stale:
        del _recent_checks[key_to_remove]

    last = _recent_checks.get(key)
    if last and (now - last) < DEDUP_WINDOW:
        raise SafetyViolation(f"Duplicate {side} {symbol} from {source} within {DEDUP_WINDOW}s")
    _recent_checks[key] = now


def is_autopilot_live() -> bool:
    return cfg.AUTOPILOT_MODE == "LIVE"


def is_autopilot_active() -> bool:
    return cfg.AUTOPILOT_MODE in ("PAPER", "LIVE")


def get_autopilot_mode() -> str:
    return cfg.AUTOPILOT_MODE

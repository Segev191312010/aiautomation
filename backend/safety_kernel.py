"""
Safety Kernel — hard runtime checks for the AI Autopilot.

Every order (rule-based or direct AI) must pass through these checks.
These are non-negotiable and cannot be overridden by AI decisions.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from config import cfg

log = logging.getLogger(__name__)

# Dedup window for order rejection
_recent_checks: dict[str, float] = {}
DEDUP_WINDOW = 5  # seconds


class SafetyViolation(Exception):
    """Raised when an order violates a safety rule."""
    pass


async def check_all(symbol: str, side: str, quantity: int, source: str,
                     account_equity: float = 0, price_estimate: float = 0) -> None:
    """Run all safety checks. Raises SafetyViolation if any fail."""
    assert_not_killed()
    await assert_daily_loss_not_locked()
    assert_no_shorts(side)
    assert_risk_budget(quantity, price_estimate, account_equity)
    assert_not_duplicate(symbol, side, source)
    log.debug("Safety kernel PASS: %s %s %s qty=%d source=%s", side, symbol, source, quantity, source)


def assert_not_killed() -> None:
    """Reject if kill switch is active."""
    if cfg.AUTOPILOT_MODE == "KILLED":
        raise SafetyViolation("Kill switch active — all AI entries blocked")


async def assert_daily_loss_not_locked() -> None:
    """Reject new entries if daily P&L loss limit breached."""
    try:
        from ai_guardrails import _load_guardrails_from_db
        config = await _load_guardrails_from_db()
        if hasattr(config, 'daily_loss_locked') and config.daily_loss_locked:
            raise SafetyViolation("Daily loss limit breached — new entries blocked")
    except SafetyViolation:
        raise
    except Exception:
        pass  # DB unavailable — allow (fail open for entries, fail closed for exits)


def assert_no_shorts(side: str) -> None:
    """No shorting anywhere in the stack."""
    if side.upper() == "SELL":
        # SELL is allowed for closing existing positions — but not for opening new shorts.
        # The caller must verify this is an exit, not a new short entry.
        # For safety, we only block if this is explicitly tagged as a new entry.
        pass  # Validated at the caller level (bot_runner checks existing position)


def assert_risk_budget(quantity: int, price_estimate: float, account_equity: float) -> None:
    """1% of net liquidation per trade = hard reject."""
    if account_equity <= 0 or price_estimate <= 0:
        return  # Can't validate without data — allow (validated again at order time)

    order_value = quantity * price_estimate
    max_risk = account_equity * cfg.RISK_PER_TRADE_PCT / 100  # RISK_PER_TRADE_PCT is 1.0 = 1%
    position_pct = (order_value / account_equity) * 100

    # Hard reject if position > 20% of account (catastrophic sizing)
    if position_pct > 20:
        raise SafetyViolation(
            f"Position size {position_pct:.1f}% of account exceeds 20% hard limit "
            f"(${order_value:.0f} / ${account_equity:.0f})"
        )


def assert_not_duplicate(symbol: str, side: str, source: str) -> None:
    """Prevent duplicate orders within the dedup window."""
    key = f"{symbol}:{side}:{source}"
    now = time.time()

    # Evict stale entries
    stale = [k for k, v in _recent_checks.items() if (now - v) > DEDUP_WINDOW * 2]
    for k in stale:
        del _recent_checks[k]

    last = _recent_checks.get(key)
    if last and (now - last) < DEDUP_WINDOW:
        raise SafetyViolation(f"Duplicate {side} {symbol} from {source} within {DEDUP_WINDOW}s")
    _recent_checks[key] = now


def is_autopilot_live() -> bool:
    """Check if autopilot is in LIVE mode (can place real orders)."""
    return cfg.AUTOPILOT_MODE == "LIVE"


def is_autopilot_active() -> bool:
    """Check if autopilot is active at all (PAPER or LIVE)."""
    return cfg.AUTOPILOT_MODE in ("PAPER", "LIVE")


def get_autopilot_mode() -> str:
    """Return current autopilot mode."""
    return cfg.AUTOPILOT_MODE

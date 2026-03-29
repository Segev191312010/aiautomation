"""Shared runtime safety gate orchestration."""
from __future__ import annotations

import logging

from safety_kernel import SafetyViolation, check_all

log = logging.getLogger(__name__)


async def evaluate_runtime_safety(
    *,
    symbol: str,
    side: str,
    quantity: int,
    source: str,
    account_equity: float = 0.0,
    price_estimate: float = 0.0,
    stop_price: float | None = None,
    is_exit: bool = False,
    has_existing_position: bool = False,
    require_autopilot_authority: bool = True,
) -> tuple[bool, str | None]:
    """Run the runtime safety kernel and normalize failures into a simple result."""
    try:
        await check_all(
            symbol=symbol,
            side=side,
            quantity=quantity,
            source=source,
            account_equity=account_equity,
            price_estimate=price_estimate,
            stop_price=stop_price,
            is_exit=is_exit,
            has_existing_position=has_existing_position,
            require_autopilot_authority=require_autopilot_authority,
        )
        return True, None
    except SafetyViolation as exc:
        return False, str(exc)
    except Exception:
        log.exception(
            "Runtime safety gate failed unexpectedly: source=%s side=%s symbol=%s qty=%s exit=%s",
            source,
            side,
            symbol,
            quantity,
            is_exit,
        )
        return False, "Runtime safety gate unavailable - blocking for safety"

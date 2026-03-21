"""Manual intervention queue helpers for Autopilot failures."""
from __future__ import annotations

from database import (
    acknowledge_manual_intervention,
    get_manual_interventions,
    open_manual_intervention,
    resolve_manual_intervention,
)


async def raise_intervention(
    *,
    severity: str,
    category: str,
    source: str,
    summary: str,
    required_action: str,
    symbol: str | None = None,
) -> int:
    return await open_manual_intervention(
        severity=severity,
        category=category,
        source=source,
        summary=summary,
        required_action=required_action,
        symbol=symbol,
    )


async def list_interventions(include_resolved: bool = False) -> list[dict]:
    return await get_manual_interventions(include_resolved=include_resolved)


async def acknowledge_intervention(intervention_id: int) -> bool:
    return await acknowledge_manual_intervention(intervention_id)


async def resolve_intervention(intervention_id: int, resolved_by: str = "operator") -> bool:
    return await resolve_manual_intervention(intervention_id, resolved_by=resolved_by)

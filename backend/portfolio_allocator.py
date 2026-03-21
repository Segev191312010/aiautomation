"""Simple risk allocator for mixed Autopilot candidates."""
from __future__ import annotations

from config import cfg


def allocate_candidates(candidates: list[dict], max_total_risk_pct: float | None = None) -> list[dict]:
    """
    Keep the highest scored candidates until the cycle risk budget is consumed.

    Each candidate may provide ``risk_pct``; if omitted, it defaults to the
    configured per-trade risk percentage.
    """
    budget = max_total_risk_pct if max_total_risk_pct is not None else cfg.MAX_DAILY_RISK * 100
    allocated: list[dict] = []
    consumed = 0.0

    for candidate in sorted(candidates, key=lambda item: float(item.get("score", 0)), reverse=True):
        risk_pct = float(candidate.get("risk_pct", cfg.RISK_PER_TRADE_PCT))
        if consumed + risk_pct > budget:
            continue
        consumed += risk_pct
        next_candidate = dict(candidate)
        next_candidate["allocated_risk_pct"] = round(risk_pct, 3)
        allocated.append(next_candidate)
    return allocated

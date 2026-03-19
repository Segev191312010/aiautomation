"""AI Advisor API — analysis, recommendations, daily reports, auto-tune."""
from __future__ import annotations

import logging
import time
from fastapi import APIRouter, Query, HTTPException

from ai_advisor import build_full_report

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/advisor", tags=["advisor"])

# Simple in-memory cache (1 hour TTL)
_cache: dict[str, dict] = {}
_CACHE_TTL = 3600


async def _get_report(lookback_days: int, refresh: bool = False) -> dict:
    key = f"report:{lookback_days}"
    if not refresh and key in _cache and time.time() - _cache[key]["ts"] < _CACHE_TTL:
        return _cache[key]["data"]
    report = await build_full_report(lookback_days=lookback_days)
    _cache[key] = {"data": report, "ts": time.time()}
    return report


@router.get("/report")
async def get_advisor_report(
    lookback_days: int = Query(default=90, ge=7, le=365),
    refresh: bool = Query(default=False),
):
    """Full analysis report. Cached 1 hour unless ?refresh=true."""
    return await _get_report(lookback_days, refresh)


@router.get("/recommendations")
async def get_recommendations(
    lookback_days: int = Query(default=90, ge=7, le=365),
    max_priority: str = Query(default="low"),
):
    """Recommendations filtered by max priority (high/medium/low)."""
    report = await _get_report(lookback_days)
    priority_order = {"high": 1, "medium": 2, "low": 3}
    max_p = priority_order.get(max_priority, 3)
    recs = [r for r in report.get("recommendations", []) if priority_order.get(r.get("priority", "low"), 3) <= max_p]
    return {"recommendations": recs, "total": len(recs)}


@router.get("/analysis")
async def get_analysis(lookback_days: int = Query(default=90, ge=7, le=365)):
    """Rule performance + sector stats for the dashboard."""
    report = await _get_report(lookback_days)
    return {
        "rule_performance": report.get("rule_performance", []),
        "sector_performance": report.get("sector_performance", []),
        "time_patterns": report.get("time_patterns", []),
        "score_analysis": report.get("score_analysis", {}),
        "bracket_analysis": report.get("bracket_analysis", {}),
    }


@router.get("/daily-report")
async def get_daily_report(lookback_days: int = Query(default=90, ge=7, le=365)):
    """AI-generated natural language summary."""
    report = await _get_report(lookback_days, refresh=True)  # Always fresh for narrative
    return {"report": report.get("report", "No report available.")}


@router.post("/auto-tune")
async def post_auto_tune(
    apply: bool = Query(default=False),
    lookback_days: int = Query(default=90),
):
    """Preview (apply=false) or apply auto-tune changes."""
    report = await build_full_report(lookback_days=lookback_days, apply_tune=apply)
    return {
        "applied": apply,
        "changes": report["auto_tune_preview"]["changes"],
        "warnings": report["auto_tune_preview"]["warnings"],
        "rules_to_disable": report["auto_tune_preview"]["rules_to_disable"],
    }


@router.get("/rule/{rule_id}")
async def get_rule_analysis(rule_id: str, lookback_days: int = Query(default=90)):
    """Deep stats for a single rule."""
    report = await _get_report(lookback_days)
    rule_data = next(
        (r for r in report.get("rule_performance", []) if r["rule_id"] == rule_id or r["rule_name"] == rule_id),
        None,
    )
    if not rule_data:
        raise HTTPException(404, f"Rule '{rule_id}' not found or has no trades")
    return rule_data

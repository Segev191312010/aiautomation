"""AI Advisor API — analysis, recommendations, daily reports, auto-tune, guardrails, audit."""
from __future__ import annotations

import logging
import time
from fastapi import APIRouter, Query, HTTPException

from ai_advisor import build_full_report
from ai_guardrails import (
    GuardrailEnforcer,
    get_ai_audit_log,
    get_ai_status_dict,
    get_shadow_decisions,
    analyze_shadow_performance,
    save_guardrails_to_db,
)
from api_contracts import (
    AdvisorReportResponse,
    AIStatusResponse,
    AuditLogEntryResponse,
    AuditLogResponse,
    AutoTuneResultResponse,
    CostReportResponse,
    EconomicReportResponse,
    GuardrailConfigResponse,
    LearningMetricsResponse,
    RulePerformanceResponse,
    ShadowPerformanceResponse,
)
from pydantic import BaseModel as _BaseModel

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


@router.get("/report", response_model=AdvisorReportResponse)
async def get_advisor_report(
    lookback_days: int = Query(default=90, ge=7, le=365),
    refresh: bool = Query(default=False),
):
    """Full analysis report. Cached 1 hour unless ?refresh=true."""
    return await _get_report(lookback_days, refresh)


@router.get("/recommendations")  # response is {recommendations: list, total: int}
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


@router.post("/auto-tune", response_model=AutoTuneResultResponse)
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


@router.get("/rule/{rule_id}", response_model=RulePerformanceResponse)
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


# ── Guardrails (DB-backed) ────────────────────────────────────────────────────

_enforcer = GuardrailEnforcer()


@router.get("/guardrails", response_model=GuardrailConfigResponse)
async def get_guardrails():
    """Get current AI guardrail configuration."""
    return await _enforcer.load_config()


@router.put("/guardrails", response_model=GuardrailConfigResponse)
async def update_guardrails(config: GuardrailConfigResponse):
    """Update AI guardrail configuration."""
    await save_guardrails_to_db(config)
    return config


@router.post("/emergency-stop")
async def toggle_emergency_stop():
    """Toggle emergency stop (kills all AI autonomy)."""
    config = await _enforcer.load_config()
    config = config.model_copy(update={"emergency_stop": not config.emergency_stop})
    await save_guardrails_to_db(config)
    return {
        "emergency_stop": config.emergency_stop,
        "message": "AI autonomy STOPPED" if config.emergency_stop else "AI autonomy RESUMED",
    }


# ── Audit Log (DB-backed) ────────────────────────────────────────────────────

@router.get("/audit-log", response_model=AuditLogResponse)
async def get_audit_log_endpoint(
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    """Paginated AI audit log."""
    entries, total = await get_ai_audit_log(limit, offset)
    return {"entries": entries, "total": total, "offset": offset, "limit": limit}


@router.post("/audit-log/{entry_id}/revert")
async def revert_audit_entry(entry_id: int):
    """Revert a specific AI-initiated change."""
    result = await _enforcer.revert_action(entry_id)
    if not result.get("reverted"):
        raise HTTPException(400, result.get("reason", "Revert failed"))
    return result


# ── AI Status (DB-backed) ────────────────────────────────────────────────────

@router.get("/ai-status", response_model=AIStatusResponse)
async def get_ai_status():
    """Current AI autonomy status."""
    return await get_ai_status_dict()


# ── Cost Tracking (stub — wired in Phase 3) ──────────────────────────────────

@router.get("/costs", response_model=CostReportResponse)
async def get_ai_costs(days: int = Query(default=30, ge=1, le=365)):
    """Real Claude API cost breakdown from audit log token counts."""
    try:
        from ai_learning import compute_cost_report
        return await compute_cost_report(days)
    except Exception as e:
        log.exception("Cost report failed: %s", e)
        return {"days": days, "total_cost_usd": 0, "total_calls": 0, "daily": []}


@router.get("/learning-metrics", response_model=LearningMetricsResponse)
async def get_learning_metrics(window_days: int = Query(default=30, ge=1, le=365)):
    """AI self-evaluation metrics for a given window."""
    try:
        from ai_learning import evaluate_past_decisions
        return await evaluate_past_decisions(window_days)
    except Exception as e:
        log.exception("Learning metrics failed: %s", e)
        raise HTTPException(500, f"Learning metrics failed: {e}")


@router.get("/economic-report", response_model=EconomicReportResponse)
async def get_economic_report(days: int = Query(default=30, ge=1, le=365)):
    """AI ROI analysis: is the AI paying for itself?"""
    try:
        from ai_learning import compute_economic_report
        return await compute_economic_report(days)
    except Exception as e:
        log.exception("Economic report failed: %s", e)
        raise HTTPException(500, f"Economic report failed: {e}")


# ── Shadow Mode Validation ───────────────────────────────────────────────────

@router.get("/shadow-decisions")
async def get_shadow_decisions_endpoint(
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    param_type: str | None = Query(default=None),
    symbol: str | None = Query(default=None),
    regime: str | None = Query(default=None),
    min_confidence: float | None = Query(default=None, ge=0, le=1),
):
    """Paginated shadow decisions with filters."""
    entries, total = await get_shadow_decisions(
        limit=limit, offset=offset, param_type=param_type,
        symbol=symbol, regime=regime, min_confidence=min_confidence,
    )
    return {"entries": entries, "total": total, "offset": offset, "limit": limit}


@router.get("/shadow-performance", response_model=ShadowPerformanceResponse)
async def get_shadow_performance_endpoint():
    """Aggregated shadow mode effectiveness metrics with gating conditions."""
    try:
        return await analyze_shadow_performance()
    except Exception as e:
        log.exception("Shadow performance analysis failed: %s", e)
        raise HTTPException(500, f"Shadow analysis failed: {e}")


class ShadowModeRequest(_BaseModel):
    enable: bool
    force: bool = False


@router.post("/shadow-mode")
async def toggle_shadow_mode(request: ShadowModeRequest):
    """Toggle shadow mode. B1 FIX: Persists to DB + updates in-memory."""
    from ai_params import ai_params

    try:
        if not request.enable:
            perf = await analyze_shadow_performance()
            if not perf.get("ready_for_live", False) and not request.force:
                raise HTTPException(
                    400,
                    "Shadow performance criteria not met. Use force=true to override."
                )

        # B1 FIX: Persist to DB guardrails record
        config = await _enforcer.load_config()
        config = config.model_copy(update={"shadow_mode": request.enable})
        await save_guardrails_to_db(config)
        # Also update in-memory
        ai_params.shadow_mode = request.enable

        return {
            "shadow_mode": request.enable,
            "message": "Shadow mode ENABLED" if request.enable else "Shadow mode DISABLED — AI is now LIVE",
        }
    except HTTPException:
        raise
    except Exception as e:
        log.exception("Failed to toggle shadow mode: %s", e)
        raise HTTPException(500, f"Shadow toggle failed: {e}")

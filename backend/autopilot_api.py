"""Autopilot API — control plane, rule lab, feed, performance, and interventions."""
from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from ai_guardrails import (
    GuardrailEnforcer,
    get_ai_audit_log,
    get_ai_status_dict,
    get_autopilot_config_dict,
    log_ai_action,
    update_autopilot_config,
)
from ai_params import ai_params
from ai_rule_lab import apply_rule_actions, list_ai_rules
from api_contracts import (
    AIDirectTrade,
    AIRuleAction,
    AIStatusResponse,
    AuditLogResponse,
    AutopilotConfigResponse,
    AutopilotModeRequest,
    AutopilotPerformanceResponse,
    CostReportResponse,
    EconomicReportResponse,
    LearningMetricsResponse,
    RulePromotionReadinessResponse,
    RuleValidationRunResponse,
    RuleVersionResponse,
    SourcePerformanceResponse,
)
from config import cfg
from database import get_rule, get_rule_validation_runs, get_rule_versions, save_rule, save_rule_version
from direct_ai_trader import execute_direct_trade
from manual_intervention import (
    acknowledge_intervention,
    list_interventions,
    resolve_intervention,
)
from performance_ledger import (
    compute_autopilot_performance,
    compute_rule_performance,
    compute_source_performance,
)
from rule_validation import evaluate_promotion_gate, evaluate_validation_run, record_validation_result

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/autopilot", tags=["autopilot"])
_enforcer = GuardrailEnforcer()


class AutopilotConfigUpdateRequest(BaseModel):
    daily_loss_limit_pct: float | None = None


class RuleLabApplyRequest(BaseModel):
    actions: list[AIRuleAction]
    author: str = "ai"
    allow_active: bool = False


class ManualRuleActionRequest(BaseModel):
    reason: str = ""


class ResolveInterventionRequest(BaseModel):
    resolved_by: str = "operator"


class RuleValidationRequest(BaseModel):
    validation_mode: Literal["paper", "replay", "manual"] = "paper"
    trades_count: int = 0
    hit_rate: float | None = None
    net_pnl: float | None = None
    expectancy: float | None = None
    max_drawdown: float | None = None
    overlap_score: float | None = None
    notes: str = ""


class PromoteRuleRequest(BaseModel):
    reason: str = "Promoted after validation gate"


def _sync_mode_runtime(mode: Literal["OFF", "PAPER", "LIVE"]) -> None:
    cfg.AUTOPILOT_MODE = mode
    cfg.AI_AUTONOMY_ENABLED = mode in ("PAPER", "LIVE")
    cfg.AI_SHADOW_MODE = mode == "OFF"
    ai_params.shadow_mode = mode == "OFF"


@router.get("/status", response_model=AIStatusResponse)
async def get_autopilot_status():
    return await get_ai_status_dict()


@router.get("/config", response_model=AutopilotConfigResponse)
async def get_autopilot_config():
    return await get_autopilot_config_dict()


@router.put("/config", response_model=AutopilotConfigResponse)
async def update_autopilot_settings(payload: AutopilotConfigUpdateRequest):
    config = await update_autopilot_config(
        daily_loss_limit_pct=payload.daily_loss_limit_pct,
    )
    return {
        "autopilot_mode": config.autopilot_mode,
        "emergency_stop": config.emergency_stop,
        "daily_loss_locked": config.daily_loss_locked,
        "daily_loss_limit_pct": config.daily_loss_limit_pct,
    }


@router.post("/mode", response_model=AutopilotConfigResponse)
async def set_autopilot_mode(request: AutopilotModeRequest):
    config = await update_autopilot_config(autopilot_mode=request.mode)
    _sync_mode_runtime(request.mode)
    await log_ai_action(
        action_type="autopilot_mode_changed",
        category="autopilot",
        description=f"Autopilot mode set to {request.mode}",
        old_value=None,
        new_value={"mode": request.mode},
        reason=request.reason or "Mode updated by operator",
        confidence=1.0,
        status="applied",
    )
    return {
        "autopilot_mode": config.autopilot_mode,
        "emergency_stop": config.emergency_stop,
        "daily_loss_locked": config.daily_loss_locked,
        "daily_loss_limit_pct": config.daily_loss_limit_pct,
    }


@router.post("/kill")
async def activate_kill_switch():
    config = await update_autopilot_config(emergency_stop=True)
    await log_ai_action(
        action_type="kill_switch_triggered",
        category="autopilot",
        description="Emergency stop activated",
        old_value=False,
        new_value=True,
        reason="Operator kill switch",
        confidence=1.0,
        status="applied",
    )
    return {
        "emergency_stop": config.emergency_stop,
        "message": "Autopilot emergency stop activated",
    }


@router.post("/kill/reset")
async def reset_kill_switch():
    config = await update_autopilot_config(emergency_stop=False)
    await log_ai_action(
        action_type="kill_switch_reset",
        category="autopilot",
        description="Emergency stop cleared",
        old_value=True,
        new_value=False,
        reason="Operator resumed autopilot",
        confidence=1.0,
        status="applied",
    )
    return {
        "emergency_stop": config.emergency_stop,
        "message": "Autopilot emergency stop cleared",
    }


@router.post("/daily-loss/reset", response_model=AutopilotConfigResponse)
async def reset_daily_loss_lock():
    config = await update_autopilot_config(daily_loss_locked=False)
    await log_ai_action(
        action_type="daily_loss_lock_reset",
        category="autopilot",
        description="Daily loss lock cleared",
        old_value=True,
        new_value=False,
        reason="Operator reset daily loss lock",
        confidence=1.0,
        status="applied",
    )
    return {
        "autopilot_mode": config.autopilot_mode,
        "emergency_stop": config.emergency_stop,
        "daily_loss_locked": config.daily_loss_locked,
        "daily_loss_limit_pct": config.daily_loss_limit_pct,
    }


@router.get("/feed", response_model=AuditLogResponse)
async def get_autopilot_feed(
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    entries, total = await get_ai_audit_log(limit, offset)
    return {"entries": entries, "total": total, "offset": offset, "limit": limit}


@router.post("/feed/{entry_id}/revert")
async def revert_feed_entry(entry_id: int):
    result = await _enforcer.revert_action(entry_id)
    if not result.get("reverted"):
        raise HTTPException(400, result.get("reason", "Revert failed"))
    return result


@router.get("/rules")
async def get_autopilot_rules():
    return await list_ai_rules()


@router.get("/rules/{rule_id}")
async def get_autopilot_rule(rule_id: str):
    rule = await get_rule(rule_id)
    if not rule:
        raise HTTPException(404, f"Rule '{rule_id}' not found")
    return rule


@router.get("/rules/{rule_id}/versions", response_model=list[RuleVersionResponse])
async def get_autopilot_rule_versions(rule_id: str):
    rule = await get_rule(rule_id)
    if not rule:
        raise HTTPException(404, f"Rule '{rule_id}' not found")
    return await get_rule_versions(rule_id)


@router.get("/rules/{rule_id}/validations", response_model=list[RuleValidationRunResponse])
async def get_autopilot_rule_validations(rule_id: str) -> list[RuleValidationRunResponse]:
    rule = await get_rule(rule_id)
    if not rule:
        raise HTTPException(404, f"Rule '{rule_id}' not found")
    return await get_rule_validation_runs(rule_id)


@router.get("/rules/{rule_id}/promotion-readiness", response_model=RulePromotionReadinessResponse)
async def get_autopilot_rule_promotion_readiness(rule_id: str):
    rule = await get_rule(rule_id)
    if not rule:
        raise HTTPException(404, f"Rule '{rule_id}' not found")
    eligible, reasons, latest = await evaluate_promotion_gate(rule)
    return {
        "rule_id": rule.id,
        "status": rule.status or "active",
        "eligible": eligible,
        "reasons": reasons,
        "latest_validation": latest,
    }


@router.post("/rules/{rule_id}/manual-pause")
async def manual_pause_rule(rule_id: str, payload: ManualRuleActionRequest):
    rule = await get_rule(rule_id)
    if not rule:
        raise HTTPException(404, f"Rule '{rule_id}' not found")
    original = rule.model_dump()
    rule.status = "paused"
    rule.enabled = False
    rule.ai_reason = payload.reason or "Paused by operator"
    await save_rule(rule)
    await save_rule_version(rule, diff_summary=rule.ai_reason, author="operator")
    await log_ai_action(
        action_type="rule_pause",
        category="operator",
        description=f"Operator paused rule '{rule.name}'",
        old_value=original,
        new_value=rule.model_dump(),
        reason=rule.ai_reason,
        confidence=1.0,
        status="applied",
    )
    return rule


@router.post("/rules/{rule_id}/manual-retire")
async def manual_retire_rule(rule_id: str, payload: ManualRuleActionRequest):
    rule = await get_rule(rule_id)
    if not rule:
        raise HTTPException(404, f"Rule '{rule_id}' not found")
    original = rule.model_dump()
    rule.status = "retired"
    rule.enabled = False
    rule.ai_reason = payload.reason or "Retired by operator"
    await save_rule(rule)
    await save_rule_version(rule, diff_summary=rule.ai_reason, author="operator")
    await log_ai_action(
        action_type="rule_retire",
        category="operator",
        description=f"Operator retired rule '{rule.name}'",
        old_value=original,
        new_value=rule.model_dump(),
        reason=rule.ai_reason,
        confidence=1.0,
        status="applied",
    )
    return rule


@router.post("/rules/{rule_id}/validations")
async def record_rule_validation(rule_id: str, payload: RuleValidationRequest):
    rule = await get_rule(rule_id)
    if not rule:
        raise HTTPException(404, f"Rule '{rule_id}' not found")

    passed, reasons = evaluate_validation_run(
        trades_count=payload.trades_count,
        expectancy=payload.expectancy,
        max_drawdown=payload.max_drawdown,
        overlap_score=payload.overlap_score,
    )
    await record_validation_result(
        rule=rule,
        validation_mode=payload.validation_mode,
        trades_count=payload.trades_count,
        hit_rate=payload.hit_rate,
        net_pnl=payload.net_pnl,
        expectancy=payload.expectancy,
        max_drawdown=payload.max_drawdown,
        overlap_score=payload.overlap_score,
        passed=passed,
        notes=payload.notes or "; ".join(reasons) or None,
    )
    return {
        "rule_id": rule.id,
        "version": rule.version,
        "passed": passed,
        "reasons": reasons,
    }


@router.post("/rules/{rule_id}/promote")
async def promote_autopilot_rule(rule_id: str, payload: PromoteRuleRequest):
    rule = await get_rule(rule_id)
    if not rule:
        raise HTTPException(404, f"Rule '{rule_id}' not found")

    eligible, reasons, latest = await evaluate_promotion_gate(rule)
    if not eligible:
        raise HTTPException(409, {"message": "Rule is not eligible for promotion", "reasons": reasons})

    original = rule.model_dump()
    rule.status = "active"
    rule.enabled = True
    rule.ai_reason = payload.reason
    await save_rule(rule)
    await save_rule_version(
        rule,
        diff_summary=f"{payload.reason} (validated {latest.get('validation_mode', 'paper')})",
        author="operator",
    )
    await log_ai_action(
        action_type="rule_promote",
        category="operator",
        description=f"Promoted rule '{rule.name}' to active",
        old_value=original,
        new_value=rule.model_dump(),
        reason=payload.reason,
        confidence=1.0,
        status="applied",
    )
    return {
        "rule": rule,
        "validation": latest,
    }


@router.post("/rule-lab/apply")
async def apply_rule_lab_actions(payload: RuleLabApplyRequest):
    return {
        "results": await apply_rule_actions(
            payload.actions,
            author=payload.author,
            allow_active=payload.allow_active,
        )
    }


@router.post("/direct-trades/execute")
async def execute_autopilot_direct_trade(decision: AIDirectTrade):
    return await execute_direct_trade(decision)


@router.get("/performance", response_model=AutopilotPerformanceResponse)
async def get_autopilot_performance(window: int = Query(default=30, ge=1, le=365)):
    return await compute_autopilot_performance(window)


@router.get("/performance/sources", response_model=list[SourcePerformanceResponse])
async def get_autopilot_source_performance(window: int = Query(default=30, ge=1, le=365)):
    return await compute_source_performance(window)


@router.get("/performance/rules")
async def get_autopilot_rule_performance(window: int = Query(default=30, ge=1, le=365)):
    return await compute_rule_performance(window)


@router.get("/interventions")
async def get_autopilot_interventions(include_resolved: bool = Query(default=False)):
    return await list_interventions(include_resolved=include_resolved)


@router.post("/interventions/{intervention_id}/ack")
async def acknowledge_autopilot_intervention(intervention_id: int):
    ok = await acknowledge_intervention(intervention_id)
    if not ok:
        raise HTTPException(404, f"Intervention '{intervention_id}' not found")
    return {"acknowledged": True}


@router.post("/interventions/{intervention_id}/resolve")
async def resolve_autopilot_intervention(intervention_id: int, payload: ResolveInterventionRequest):
    ok = await resolve_intervention(intervention_id, resolved_by=payload.resolved_by)
    if not ok:
        raise HTTPException(404, f"Intervention '{intervention_id}' not found")
    return {"resolved": True, "resolved_by": payload.resolved_by}


# ── IBKR Scanner ─────────────────────────────────────────────────────────────

@router.get("/scanner/templates")
async def get_scanner_templates():
    """List available IBKR scan templates."""
    from ibkr_scanner import get_available_scans
    return get_available_scans()


@router.get("/scanner/run/{scan_name}")
async def run_scanner(scan_name: str, max_results: int = Query(default=50, ge=1, le=100)):
    """Run an IBKR server-side market scan."""
    from ibkr_scanner import run_scan
    results = await run_scan(scan_name, max_results)
    return {"scan": scan_name, "count": len(results), "results": results}


@router.get("/scanner/multi")
async def run_multi_scanner():
    """Run multiple scans and return combined results."""
    from ibkr_scanner import run_multi_scan
    results = await run_multi_scan()
    total = sum(len(v) for v in results.values())
    return {"scans": list(results.keys()), "total": total, "results": results}


@router.get("/costs", response_model=CostReportResponse)
async def get_autopilot_costs(days: int = Query(default=30, ge=1, le=365)):
    try:
        from ai_learning import compute_cost_report

        return await compute_cost_report(days)
    except Exception as exc:
        log.exception("Autopilot cost report failed: %s", exc)
        return {"days": days, "total_cost_usd": 0, "total_calls": 0, "daily": []}


@router.get("/learning-metrics", response_model=LearningMetricsResponse)
async def get_autopilot_learning_metrics(window_days: int = Query(default=30, ge=1, le=365)):
    try:
        from ai_learning import evaluate_past_decisions

        return await evaluate_past_decisions(window_days)
    except Exception as exc:
        log.exception("Autopilot learning metrics failed: %s", exc)
        raise HTTPException(500, f"Learning metrics failed: {exc}")


@router.get("/economic-report", response_model=EconomicReportResponse)
async def get_autopilot_economic_report(days: int = Query(default=30, ge=1, le=365)):
    try:
        from ai_learning import compute_economic_report

        return await compute_economic_report(days)
    except Exception as exc:
        log.exception("Autopilot economic report failed: %s", exc)
        raise HTTPException(500, f"Economic report failed: {exc}")

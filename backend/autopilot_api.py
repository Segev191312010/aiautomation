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
    DecisionItemResponse,
    DecisionRunResponse,
    EconomicReportResponse,
    EvaluationCompareResponse,
    EvaluationRunResponse,
    EvaluationSliceResponse,
    LearningMetricsResponse,
    ReplayRequest,
    RulePromotionReadinessResponse,
    RuleValidationRunResponse,
    RuleVersionResponse,
    SourcePerformanceResponse,
)
from config import cfg
from database import get_rule, get_rule_validation_runs, get_rule_versions, save_rule, save_rule_version, persist_rule_revision
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
    # S9: build data quality note from latest validation evidence
    data_quality_note = None
    if latest:
        canonical = latest.get("evaluated_closed_count")
        legacy = latest.get("excluded_legacy_count", 0)
        quality = latest.get("data_quality")
        if canonical is not None:
            parts = [f"{canonical} canonical trades"]
            if legacy:
                parts.append(f"{legacy} legacy excluded")
            if quality:
                parts.append(f"quality: {quality}")
            data_quality_note = " | ".join(parts)
    return {
        "rule_id": rule.id,
        "status": rule.status or "active",
        "eligible": eligible,
        "reasons": reasons,
        "latest_validation": latest,
        "data_quality_note": data_quality_note,
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
    await persist_rule_revision(rule, diff_summary=rule.ai_reason, author="operator")
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
    await persist_rule_revision(rule, diff_summary=rule.ai_reason, author="operator")
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
    await persist_rule_revision(
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
    # Server-side gate: allow_active only when autopilot is actually LIVE
    effective_allow_active = payload.allow_active and cfg.AUTOPILOT_MODE == "LIVE"
    return {
        "results": await apply_rule_actions(
            payload.actions,
            author=payload.author,
            allow_active=effective_allow_active,
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


# ── S10: Decision Ledger Endpoints ───────────────────────────────────────────

@router.get("/decision-runs", response_model=list[DecisionRunResponse])
async def get_decision_runs_endpoint(
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    source: str | None = Query(default=None),
    mode: str | None = Query(default=None),
    status: str | None = Query(default=None),
):
    from ai_decision_ledger import get_decision_runs
    return await get_decision_runs(limit=limit, offset=offset, source=source, mode=mode, status=status)


@router.get("/decision-runs/{run_id}", response_model=DecisionRunResponse)
async def get_decision_run_endpoint(run_id: str):
    from ai_decision_ledger import get_decision_run
    run = await get_decision_run(run_id)
    if not run:
        raise HTTPException(404, f"Decision run '{run_id}' not found")
    return run


@router.get("/decision-runs/{run_id}/items", response_model=list[DecisionItemResponse])
async def get_decision_run_items_endpoint(run_id: str):
    from ai_decision_ledger import get_decision_run, get_decision_items
    run = await get_decision_run(run_id)
    if not run:
        raise HTTPException(404, f"Decision run '{run_id}' not found")
    return await get_decision_items(run_id)


# ── S10: Evaluation Endpoints ────────────────────────────────────────────────

@router.post("/evaluation/replay", response_model=EvaluationRunResponse)
async def launch_evaluation_replay(payload: ReplayRequest):
    from ai_evaluator import create_evaluation_run, complete_evaluation_run, build_slices_from_items, save_evaluation_slices, get_evaluation_run
    from ai_decision_ledger import get_decision_items as get_items
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    window_start = (now - timedelta(days=payload.window_days)).isoformat()
    window_end = now.isoformat()

    eval_id = await create_evaluation_run(
        candidate_type=payload.candidate_type,
        candidate_key=payload.candidate_key,
        baseline_key=payload.baseline_key,
        evaluation_mode=payload.evaluation_mode,
        window_start=window_start,
        window_end=window_end,
        request_json=payload.model_dump(),
    )

    try:
        if payload.evaluation_mode == "rule_backtest":
            from ai_replay import run_rule_backtest_replay
            result = await run_rule_backtest_replay(payload.candidate_key, window_days=payload.window_days)
            await complete_evaluation_run(eval_id, summary=result)

        elif payload.evaluation_mode == "stored_context_existing":
            from ai_replay import run_stored_context_existing
            result = await run_stored_context_existing(
                window_days=payload.window_days, limit_runs=payload.limit_runs,
                min_confidence=payload.min_confidence,
                symbols=payload.symbols or None,
                action_types=payload.action_types or None,
            )
            all_items = result.get("items", [])
            if all_items:
                slices = build_slices_from_items(all_items)
                await save_evaluation_slices(eval_id, slices)
                overall = next((s for s in slices if s["slice_type"] == "overall"), {})
                await complete_evaluation_run(eval_id, summary=overall.get("metrics", {}))
            else:
                await complete_evaluation_run(eval_id, summary={"warning": "No items to evaluate"})

        elif payload.evaluation_mode == "stored_context_generate":
            from ai_replay import run_stored_context_generate
            result = await run_stored_context_generate(
                candidate_key=payload.candidate_key,
                baseline_key=payload.baseline_key,
                candidate_type=payload.candidate_type,
                window_days=payload.window_days,
                limit_runs=payload.limit_runs,
            )
            scored = result.get("scored_items", [])
            if scored:
                slices = build_slices_from_items(scored)
                await save_evaluation_slices(eval_id, slices)
                overall = next((s for s in slices if s["slice_type"] == "overall"), {})
                await complete_evaluation_run(eval_id, summary=overall.get("metrics", {}))
            else:
                await complete_evaluation_run(eval_id, summary={
                    "warning": "No scored items",
                    "runs_evaluated": result.get("runs_evaluated", 0),
                    "errors": result.get("errors", []),
                })

        else:
            await complete_evaluation_run(eval_id, summary={}, status="failed",
                                          error=f"Unknown evaluation_mode: {payload.evaluation_mode}")

    except Exception as exc:
        log.exception("Evaluation replay failed: %s", exc)
        await complete_evaluation_run(eval_id, summary={}, status="failed", error=str(exc))

    return await get_evaluation_run(eval_id)


@router.get("/evaluation/runs", response_model=list[EvaluationRunResponse])
async def get_evaluation_runs_endpoint(
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    from ai_evaluator import get_evaluation_runs
    return await get_evaluation_runs(limit=limit, offset=offset)


@router.get("/evaluation/{evaluation_id}", response_model=EvaluationRunResponse)
async def get_evaluation_run_endpoint(evaluation_id: str):
    from ai_evaluator import get_evaluation_run
    run = await get_evaluation_run(evaluation_id)
    if not run:
        raise HTTPException(404, f"Evaluation run '{evaluation_id}' not found")
    return run


@router.get("/evaluation/{evaluation_id}/slices", response_model=list[EvaluationSliceResponse])
async def get_evaluation_slices_endpoint(evaluation_id: str):
    from ai_evaluator import get_evaluation_slices
    return await get_evaluation_slices(evaluation_id)


@router.get("/evaluation/compare", response_model=EvaluationCompareResponse)
async def compare_evaluations_endpoint(
    baseline: str = Query(..., description="Baseline evaluation run ID"),
    candidate: str = Query(..., description="Candidate evaluation run ID"),
):
    from ai_evaluator import compare_evaluations
    return await compare_evaluations(baseline, candidate)

"""
AI Optimizer — Claude API-powered decision engine.

Runs on a schedule (default every 4 hours). Each cycle:
1. Gather context (trade history, rule performance, regime, etc.)
2. Call Claude API for structured parameter recommendations
3. Validate and apply through guardrails

Uses tiered models: cfg.AI_MODEL_OPTIMIZER for decisions.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone

from config import cfg
from ai_params import ai_params
from ai_guardrails import (
    GuardrailEnforcer,
    log_ai_action,
    log_shadow_decision,
    save_param_snapshot,
    get_autopilot_config_dict,
)
from context_builder import build_optimizer_context
from ai_decision_ledger import (
    start_decision_run,
    record_decision_items,
    mark_decision_item_applied,
    mark_decision_item_blocked,
    mark_decision_item_shadow,
    finalize_decision_run,
)

log = logging.getLogger(__name__)

_optimizer_running = False
_last_optimization: float = 0


# ── Context Builder ──────────────────────────────────────────────────────────

async def _build_context() -> dict:
    """Collect all data Claude needs to make decisions."""
    try:
        return await build_optimizer_context(lookback_days=cfg.ADVISOR_LOOKBACK_DAYS)
    except Exception as e:
        log.warning("Failed to fetch advisor data for optimizer: %s", e)
        return {}


# ── Claude API Call ──────────────────────────────────────────────────────────

# Prompt templates and formatters — imported from shared module.
# Kept as module-level names for backward compat with tests and candidate_registry.
from optimizer_prompts import (
    OPTIMIZER_SYSTEM_PROMPT,
    OPTIMIZER_USER_TEMPLATE,
    format_market_snapshot as _format_market_snapshot,
    format_sector_performance as _format_sector_performance,
    format_time_patterns as _format_time_patterns,
    format_rule_performance as _format_rule_performance,
)


async def _get_ai_decisions(context: dict) -> dict | None:
    """Call Claude API for structured parameter recommendations.

    Uses ai_model_router for automatic fallback and circuit breaker integration.
    """
    api_key = cfg.ANTHROPIC_API_KEY
    if not api_key:
        log.info("No ANTHROPIC_API_KEY set — skipping AI optimization")
        return None

    rule_perf_text = _format_rule_performance(context.get("rule_performance", []))
    sector_perf_text = _format_sector_performance(context.get("sector_performance", []))
    time_pattern_text = _format_time_patterns(context.get("time_patterns", []))
    market_snapshot_text = _format_market_snapshot(context.get("market_snapshot", {}))

    prompt = OPTIMIZER_USER_TEMPLATE.format(
        lookback_days=context.get("lookback_days", 90),
        trade_count=context.get("trade_count", 0),
        current_regime=context.get("current_regime", "unknown"),
        pnl_summary=json.dumps(context.get("pnl_summary", {})),
        rule_perf_text=rule_perf_text,
        sector_perf_text=sector_perf_text,
        time_pattern_text=time_pattern_text,
        score_analysis=json.dumps(context.get("score_analysis", {})),
        bracket_analysis=json.dumps(context.get("bracket_analysis", {})),
        current_params=json.dumps(context.get("current_params", {})),
        market_snapshot_text=market_snapshot_text,
    )

    from ai_model_router import ai_call

    result = await ai_call(
        system=OPTIMIZER_SYSTEM_PROMPT,
        prompt=prompt,
        source="optimizer",
        model=cfg.AI_MODEL_OPTIMIZER,
        max_tokens=2000,
        temperature=0,
    )

    if not result.ok:
        log.warning("AI optimizer call failed (all models): %s", result.error)
        return None

    # Parse JSON from response (handle ```json fences)
    text = result.text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        lines = lines[1:]  # drop opening fence (```json or ```)
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]  # drop closing fence
        text = "\n".join(lines).strip()

    try:
        decisions = json.loads(text)
    except json.JSONDecodeError as e:
        log.warning("AI optimizer returned invalid JSON: %s", e)
        return None

    decisions["_input_tokens"] = result.tokens_in
    decisions["_output_tokens"] = result.tokens_out
    decisions["_model_used"] = result.model_used
    decisions["_fallback_used"] = result.fallback_used

    log.info(
        "AI optimizer returned decisions (confidence=%.2f, model=%s, tokens=%d/%d%s)",
        decisions.get("confidence", 0), result.model_used,
        result.tokens_in, result.tokens_out,
        " [FALLBACK]" if result.fallback_used else "",
    )
    return decisions


# ── Decision Application ─────────────────────────────────────────────────────

def _build_ledger_items(decisions: dict) -> list[dict]:
    """Build decision_item dicts from an AIDecisionPayload for the ledger.

    Delegates to decision_item_factory.build_ledger_items().
    Kept as a thin wrapper for backward compat with tests/candidate_registry.
    """
    from decision_item_factory import build_ledger_items
    return build_ledger_items(decisions)


async def _apply_decisions(decisions: dict, context: dict, *, run_id: str | None = None, item_ids: list[str] | None = None) -> dict:
    """Apply AI decisions through guardrails. Returns summary of applied/blocked changes."""
    enforcer = GuardrailEnforcer()
    confidence = decisions.get("confidence", 0.5)
    input_tokens = decisions.get("_input_tokens")
    output_tokens = decisions.get("_output_tokens")
    results = {"applied": [], "blocked": [], "shadow": []}

    # S10: track which item_id corresponds to which decision
    # item_ids follows the same order as _build_ledger_items output
    _item_idx = 0

    def _next_item_id() -> str | None:
        nonlocal _item_idx
        if item_ids and _item_idx < len(item_ids):
            iid = item_ids[_item_idx]
            _item_idx += 1
            return iid
        _item_idx += 1
        return None

    # ── Min Score ────────────────────────────────────────────────────────────
    min_score_rec = decisions.get("min_score")
    if min_score_rec and isinstance(min_score_rec, dict):
        cur_item_id = _next_item_id()
        new_score = min_score_rec.get("value", 50)
        old_score = ai_params.get_min_score()

        if ai_params.shadow_mode:
            delta = new_score - old_score
            await log_shadow_decision("min_score", None, min_score_rec, old_score,
                                      delta_value=delta, confidence=confidence,
                                      regime=context.get("current_regime"),
                                      decision_run_id=run_id, decision_item_id=cur_item_id)
            if cur_item_id:
                await mark_decision_item_shadow(cur_item_id, notes="shadow mode")
            results["shadow"].append(f"min_score: {old_score} -> {new_score} (shadow)")
        elif abs(new_score - old_score) >= 1:
            result = await enforcer.execute_with_audit(
                action_type="score_threshold",
                category="signal_weight",
                description=f"Adjust min score: {old_score} -> {new_score}",
                old_value=old_score,
                new_value=new_score,
                reason=decisions.get("reasoning", ""),
                confidence=confidence,
                apply_fn=lambda s=new_score: _set_min_score(s),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                decision_run_id=run_id,
                decision_item_id=cur_item_id,
            )
            if cur_item_id:
                if result.get("applied"):
                    await mark_decision_item_applied(cur_item_id, applied_json={"value": new_score})
                else:
                    await mark_decision_item_blocked(cur_item_id, reason=result.get("reason", "guardrail"))
            key = "applied" if result.get("applied") else "blocked"
            results[key].append(f"min_score: {old_score} -> {new_score}")
        else:
            if cur_item_id:
                await mark_decision_item_applied(cur_item_id, applied_json={"value": new_score, "no_change": True})

    # ── Risk Multiplier ──────────────────────────────────────────────────────
    risk_rec = decisions.get("risk_multiplier")
    if risk_rec and isinstance(risk_rec, dict):
        cur_item_id = _next_item_id()
        new_mult = risk_rec.get("value", 1.0)
        old_mult = ai_params.get_risk_multiplier()

        if ai_params.shadow_mode:
            delta = new_mult - old_mult
            await log_shadow_decision("risk_multiplier", None, risk_rec, old_mult,
                                      delta_value=delta, confidence=confidence,
                                      regime=context.get("current_regime"),
                                      decision_run_id=run_id, decision_item_id=cur_item_id)
            if cur_item_id:
                await mark_decision_item_shadow(cur_item_id, notes="shadow mode")
            results["shadow"].append(f"risk_mult: {old_mult} -> {new_mult} (shadow)")
        elif abs(new_mult - old_mult) >= 0.05:
            increase_pct = ((new_mult - old_mult) / old_mult * 100) if old_mult > 0 else 0
            result = await enforcer.execute_with_audit(
                action_type="risk_adjust",
                category="risk_param",
                description=f"Adjust risk multiplier: {old_mult:.2f} -> {new_mult:.2f}",
                old_value=old_mult,
                new_value={"value": new_mult, "increase_pct": max(0, increase_pct)},
                reason=decisions.get("reasoning", ""),
                confidence=confidence,
                apply_fn=lambda m=new_mult: _set_risk_mult(m),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                decision_run_id=run_id,
                decision_item_id=cur_item_id,
            )
            if cur_item_id:
                if result.get("applied"):
                    await mark_decision_item_applied(cur_item_id, applied_json={"value": new_mult})
                else:
                    await mark_decision_item_blocked(cur_item_id, reason=result.get("reason", "guardrail"))
            key = "applied" if result.get("applied") else "blocked"
            results[key].append(f"risk_mult: {old_mult:.2f} -> {new_mult:.2f}")
        else:
            if cur_item_id:
                await mark_decision_item_applied(cur_item_id, applied_json={"value": new_mult, "no_change": True})

    # ── Rule Changes ─────────────────────────────────────────────────────────
    rule_changes = decisions.get("rule_changes", [])
    for rc in rule_changes:
        cur_item_id = _next_item_id()
        rule_id = rc.get("rule_id", "")
        action = rc.get("action", "")
        reason = rc.get("reason", "")

        if not rule_id or action not in ("disable", "enable", "boost", "reduce"):
            continue

        if ai_params.shadow_mode:
            rule_delta = {"disable": -1.0, "enable": 1.0,
                          "boost": float(rc.get("sizing_mult", 1.3)) - 1.0,
                          "reduce": float(rc.get("sizing_mult", 0.7)) - 1.0}.get(action, 0.0)
            await log_shadow_decision("rule_change", None, rc, {"rule_id": rule_id, "action": "none"},
                                      delta_value=rule_delta, confidence=confidence,
                                      regime=context.get("current_regime"),
                                      decision_run_id=run_id, decision_item_id=cur_item_id)
            if cur_item_id:
                await mark_decision_item_shadow(cur_item_id, notes="shadow mode")
            results["shadow"].append(f"rule {action}: {rule_id} (shadow)")
            continue

        if action in ("disable", "enable"):
            result = await enforcer.execute_with_audit(
                action_type=f"rule_{action}",
                category="rule_change",
                description=f"AI {action} rule: {rule_id}",
                old_value={"rule_id": rule_id, "enabled": action == "disable"},
                new_value={"rule_id": rule_id, "enabled": action == "enable"},
                reason=reason,
                confidence=confidence,
                apply_fn=lambda rid=rule_id, act=action: _toggle_rule(rid, act == "enable"),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                decision_run_id=run_id,
                decision_item_id=cur_item_id,
            )
            if cur_item_id:
                if result.get("applied"):
                    await mark_decision_item_applied(cur_item_id)
                else:
                    await mark_decision_item_blocked(cur_item_id, reason=result.get("reason", "guardrail"))
            key = "applied" if result.get("applied") else "blocked"
            results[key].append(f"rule_{action}: {rule_id}")

        elif action in ("boost", "reduce"):
            raw_sizing = rc.get("sizing_mult", 1.3 if action == "boost" else 0.7)
            sizing = max(0.1, min(3.0, float(raw_sizing)))
            old_sizing = ai_params.get_rule_sizing_multiplier(rule_id)

            async def _apply_sizing(rid=rule_id, s=sizing):
                ai_params.set_rule_sizing_multiplier(rid, s)

            result = await enforcer.execute_with_audit(
                action_type=f"rule_{action}",
                category="rule_change",
                description=f"AI {action} rule sizing: {rule_id} x{sizing:.2f}",
                old_value=old_sizing,
                new_value=sizing,
                reason=reason,
                confidence=confidence,
                apply_fn=_apply_sizing,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                decision_run_id=run_id,
                decision_item_id=cur_item_id,
            )
            if cur_item_id:
                if result.get("applied"):
                    await mark_decision_item_applied(cur_item_id, applied_json={"sizing_mult": sizing})
                else:
                    await mark_decision_item_blocked(cur_item_id, reason=result.get("reason", "guardrail"))
            key = "applied" if result.get("applied") else "blocked"
            results[key].append(f"rule_{action}: {rule_id} x{sizing:.2f}")

    # ── Rule Lab Actions (create/modify/pause/retire rules) ────────────────
    rule_actions = decisions.get("rule_actions", [])
    if rule_actions:
        # Collect item_ids for rule_action items
        ra_item_ids = [_next_item_id() for _ in rule_actions]
        try:
            from ai_rule_lab import apply_rule_actions
            from safety_kernel import is_autopilot_live

            # S10-BE-07: pass run_id and item_ids for origin tracking
            lab_results = await apply_rule_actions(
                rule_actions, author="ai", allow_active=is_autopilot_live(),
                decision_run_id=run_id, decision_item_ids=ra_item_ids,
            )
            for idx, lr in enumerate(lab_results):
                iid = ra_item_ids[idx] if idx < len(ra_item_ids) else None
                if lr.get("ok"):
                    results["applied"].append(f"rule_{lr['action']}: {lr.get('rule_id', '?')}")
                    if iid:
                        await mark_decision_item_applied(
                            iid, created_rule_id=lr.get("rule_id"),
                        )
                else:
                    results["blocked"].append(f"rule_{lr.get('action', '?')}: {lr.get('reason', 'failed')}")
                    if iid:
                        await mark_decision_item_blocked(iid, reason=lr.get("reason", "failed"))
        except Exception as e:
            log.error("Rule Lab actions failed: %s", e)
            results["blocked"].append(f"rule_lab_error: {e}")

    # ── Direct AI Trades ─────────────────────────────────────────────────────
    direct_trades = decisions.get("direct_trades", [])
    if direct_trades:
        # Collect item_ids for direct_trade items
        dt_item_ids = [_next_item_id() for _ in direct_trades]
        try:
            from execution_brain import queue_direct_candidates

            # S10-BE-06: inject decision_item_id into each trade dict for propagation
            for i, dt in enumerate(direct_trades):
                if dt_item_ids[i]:
                    if isinstance(dt, dict):
                        dt["decision_id"] = dt_item_ids[i]

            queued = await queue_direct_candidates(direct_trades)
            for i, dt in enumerate(direct_trades):
                iid = dt_item_ids[i] if i < len(dt_item_ids) else None
                if i < queued:
                    # HB1-06: Leave as pending — actual execution path marks applied on success
                    results["applied"].append(
                        f"direct_trade_queued: {dt.get('symbol', '?')} {dt.get('action', '?')}"
                    )
                    # item stays 'pending' until execute_direct_trade succeeds
                else:
                    results["blocked"].append(
                        f"direct_trade_queued: {dt.get('symbol', '?')} {dt.get('action', '?')}"
                    )
                    if iid:
                        await mark_decision_item_blocked(iid, reason="not queued")
            log.info("Queued %d/%d direct AI trade candidates for bot-cycle execution", queued, len(direct_trades))
        except Exception as exc:
            log.error("Direct AI trade engine unavailable: %s", exc)
            results["blocked"].append(f"direct_trade_engine: {exc}")

    return results


async def _set_min_score(score: float) -> None:
    ai_params.set_min_score(score)


async def _set_risk_mult(mult: float) -> None:
    ai_params.set_risk_multiplier(mult)


async def _toggle_rule(rule_id: str, enable: bool) -> None:
    from database import get_rules, save_rule
    rules = await get_rules()
    for rule in rules:
        if rule.id == rule_id:
            rule.enabled = enable
            await save_rule(rule)
            log.info("AI toggled rule %s: enabled=%s", rule_id, enable)
            return
    raise ValueError(f"Rule {rule_id} not found — cannot toggle")


# ── Orchestrator ─────────────────────────────────────────────────────────────

async def run_full_optimization() -> dict:
    """Run a full AI optimization cycle."""
    global _optimizer_running, _last_optimization
    if _optimizer_running:
        log.info("AI optimizer already running, skipping")
        return {"skipped": True, "reason": "already running"}

    _optimizer_running = True
    start = time.time()
    try:
        # H-6 FIX: Check emergency_stop before running (fail-closed on error)
        try:
            config = await get_autopilot_config_dict()
            if config.get("emergency_stop"):
                return {"skipped": True, "reason": "emergency_stop active"}
        except Exception as cfg_exc:
            log.error("Cannot verify emergency_stop — blocking optimizer for safety: %s", cfg_exc)
            return {"skipped": True, "reason": f"config_unavailable: {cfg_exc}"}

        log.info("AI optimization cycle starting...")

        # Step 1: Gather context
        context = await _build_context()
        if not context:
            return {"skipped": True, "reason": "no context data"}

        # Step 2: Get AI decisions
        decisions = await _get_ai_decisions(context)
        if not decisions:
            return {"skipped": True, "reason": "no API key or API failed"}

        # S10: Create decision run and items in ledger
        run_id = None
        item_ids = None
        try:
            context_json = json.dumps({
                "pnl_summary": context.get("pnl_summary", {}),
                "trade_count": context.get("trade_count", 0),
                "current_regime": context.get("current_regime"),
                "lookback_days": context.get("lookback_days"),
                "rule_performance": context.get("rule_performance", []),
                "score_analysis": context.get("score_analysis", {}),
                "sector_performance": context.get("sector_performance", []),
                "time_patterns": context.get("time_patterns", []),
                "bracket_analysis": context.get("bracket_analysis", {}),
                "current_params": context.get("current_params", {}),
                "market_snapshot": context.get("market_snapshot", {}),
            }, default=str)

            run_id = await start_decision_run(
                source="optimizer",
                mode=cfg.AUTOPILOT_MODE,
                provider="anthropic",
                model=cfg.AI_MODEL_OPTIMIZER,
                prompt_version="v2_market_snapshot",
                context_json=context_json,
                reasoning=decisions.get("reasoning", ""),
                aggregate_confidence=decisions.get("confidence"),
                abstained=decisions.get("abstained", False),
                input_tokens=decisions.get("_input_tokens"),
                output_tokens=decisions.get("_output_tokens"),
            )

            ledger_items = _build_ledger_items(decisions)
            if ledger_items:
                item_ids = await record_decision_items(
                    run_id, ledger_items,
                    regime=context.get("current_regime"),
                )
        except Exception as ledger_exc:
            log.warning("Decision ledger recording failed (non-fatal): %s", ledger_exc)

        # Step 3: Apply through guardrails
        results = await _apply_decisions(decisions, context, run_id=run_id, item_ids=item_ids)

        # Finalize decision run
        if run_id:
            try:
                await finalize_decision_run(run_id, status="completed")
            except Exception as exc:
                log.warning("Failed to finalize decision run %s: %s", run_id, exc)

        # Step 4: Evaluate paper rules + auto-promote if ready
        try:
            from rule_validation import evaluate_paper_rules, auto_promote_paper_rules
            eval_results = await evaluate_paper_rules()
            promoted = await auto_promote_paper_rules()
            if promoted:
                for p in promoted:
                    results["applied"].append(f"rule_promoted: {p['name']} (paper → active)")
                log.info("Auto-promoted %d paper rules to active", len(promoted))
        except Exception as e:
            log.warning("Paper rule evaluation/promotion failed: %s", e)

        _last_optimization = time.time()
        ai_params.last_recompute = _last_optimization

        # AI-5: persist optimized params so they survive restart
        try:
            await ai_params.save_to_db()
        except Exception as exc:
            log.warning("Failed to persist AI params snapshot: %s", exc)

        elapsed = time.time() - start
        log.info(
            "AI optimization complete in %.1fs: %d applied, %d blocked, %d shadow",
            elapsed, len(results["applied"]), len(results["blocked"]), len(results.get("shadow", [])),
        )

        return {
            "success": True,
            "elapsed_seconds": round(elapsed, 1),
            "confidence": decisions.get("confidence", 0),
            "reasoning": decisions.get("reasoning", ""),
            "results": results,
        }

    except Exception as e:
        log.exception("AI optimization failed: %s", e)
        return {"success": False, "error": str(e)}
    finally:
        _optimizer_running = False
        # Always update timestamp to prevent retry storms on persistent failures
        _last_optimization = time.time()


def should_recompute() -> bool:
    """Check if it's time to run AI optimization."""
    if not cfg.ANTHROPIC_API_KEY:
        return False
    if cfg.AUTOPILOT_MODE not in ("PAPER", "LIVE"):
        return False
    elapsed = time.time() - _last_optimization
    return elapsed >= cfg.AI_OPTIMIZE_INTERVAL_SECONDS


# ── Background Loop ──────────────────────────────────────────────────────────

async def ai_optimization_loop() -> None:
    """Background task: run AI optimizer on a schedule."""
    log.info(
        "AI optimization loop started (interval=%ds, shadow=%s)",
        cfg.AI_OPTIMIZE_INTERVAL_SECONDS, ai_params.shadow_mode,
    )
    while True:
        try:
            config = await get_autopilot_config_dict()
            ai_params.shadow_mode = config["autopilot_mode"] == "OFF"
            if should_recompute():
                await run_full_optimization()
        except Exception as e:
            log.error("AI optimization loop error: %s", e)
        await asyncio.sleep(60)  # Check every minute if it's time to optimize

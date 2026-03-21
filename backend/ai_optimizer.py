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
)
from ai_advisor import fetch_advisor_data, analyze_rule_performance, analyze_score_effectiveness

log = logging.getLogger(__name__)

_optimizer_running = False
_last_optimization: float = 0


# ── Context Builder ──────────────────────────────────────────────────────────

async def _build_context() -> dict:
    """Collect all data Claude needs to make decisions."""
    try:
        data = await fetch_advisor_data(lookback_days=cfg.ADVISOR_LOOKBACK_DAYS)
    except Exception as e:
        log.warning("Failed to fetch advisor data for optimizer: %s", e)
        return {}

    matched = data.get("matched_trades", [])
    rules = data.get("rules", [])
    rule_perf = analyze_rule_performance(matched, rules)
    score_analysis = analyze_score_effectiveness(matched)

    # Current AI parameters
    current_params = {
        "min_score": ai_params.get_min_score(),
        "risk_multiplier": ai_params.get_risk_multiplier(),
        "signal_weights": ai_params._signal_weights,
        "exit_params": ai_params._exit_params,
        "sizing_multipliers": ai_params._rule_sizing_multipliers,
    }

    # B11 FIX: Get current regime from DB snapshots
    current_regime = None
    try:
        from database import get_db
        async with get_db() as db:
            cur = await db.execute(
                "SELECT regime FROM regime_snapshots ORDER BY timestamp DESC LIMIT 1"
            )
            row = await cur.fetchone()
            if row:
                current_regime = row[0]
    except Exception:
        pass  # regime_snapshots may not have data yet

    return {
        "pnl_summary": data.get("pnl_summary", {}),
        "trade_count": len(matched),
        "rule_performance": rule_perf[:20],
        "score_analysis": score_analysis,
        "current_params": current_params,
        "lookback_days": data.get("lookback_days", 90),
        "current_regime": current_regime,
    }


# ── Claude API Call ──────────────────────────────────────────────────────────

OPTIMIZER_SYSTEM_PROMPT = (
    "You are an autonomous AI trading strategist. You optimize parameters, "
    "create new rules, pause underperforming rules, and retire broken ones. "
    "Return ONLY valid JSON. No markdown fences, no explanations outside JSON. "
    "CONSTRAINTS: No shorting (BUY only). 1% risk per trade. Intraday + swing styles."
)

OPTIMIZER_USER_TEMPLATE = """Trading bot performance data (last {lookback_days} days, {trade_count} trades):

P&L Summary: {pnl_summary}

Rule Performance (top rules by trade count):
{rule_perf_text}

Score Analysis: {score_analysis}

Current AI Parameters: {current_params}

Analyze this data and return a JSON object with your decisions:
{{
  "min_score": {{"value": 55, "lower": 50, "upper": 60}},
  "risk_multiplier": {{"value": 1.0, "lower": 0.8, "upper": 1.2}},
  "rule_actions": [
    {{
      "action": "create",
      "rule_payload": {{
        "name": "AI: Descriptive Name",
        "symbol": "AAPL",
        "conditions": [{{"indicator": "RSI", "params": {{"period": 14}}, "operator": "<", "value": 30}}],
        "logic": "AND",
        "action_type": "BUY",
        "cooldown_minutes": 120,
        "thesis": "Why this rule",
        "hold_style": "swing"
      }},
      "reason": "Why creating",
      "confidence": 0.7
    }},
    {{
      "action": "pause",
      "rule_id": "existing-id",
      "reason": "Why pausing",
      "confidence": 0.8
    }}
  ],
  "reasoning": "2-3 sentence strategy summary",
  "confidence": 0.75
}}

Rules:
- New rules start as 'paper' (not live) — BUY only, no shorts
- Only pause/retire rules with clear evidence of poor performance
- Max 3 new rules per cycle
- Include a thesis for new rules explaining the edge
- If everything looks fine, return empty rule_actions
- min_score: adjust if score analysis shows a better threshold
- risk_multiplier: 1.0 = no change, <1.0 = reduce risk, >1.0 = increase risk"""


async def _get_ai_decisions(context: dict) -> dict | None:
    """Call Claude API for structured parameter recommendations."""
    api_key = cfg.ANTHROPIC_API_KEY
    if not api_key:
        log.info("No ANTHROPIC_API_KEY set — skipping AI optimization")
        return None

    rule_perf = context.get("rule_performance", [])
    rule_perf_text = "\n".join(
        f"  - {r['rule_name']}: {r['total_trades']} trades, "
        f"{r['win_rate']}% WR, PF {r['profit_factor']}, "
        f"${r['total_pnl']:.0f} P&L, verdict={r['verdict']}"
        for r in rule_perf[:15]
    ) or "  No trade data available."

    prompt = OPTIMIZER_USER_TEMPLATE.format(
        lookback_days=context.get("lookback_days", 90),
        trade_count=context.get("trade_count", 0),
        pnl_summary=json.dumps(context.get("pnl_summary", {})),
        rule_perf_text=rule_perf_text,
        score_analysis=json.dumps(context.get("score_analysis", {})),
        current_params=json.dumps(context.get("current_params", {})),
    )

    try:
        import anthropic
        client = anthropic.AsyncAnthropic(api_key=api_key)

        msg = await client.messages.create(
            model=cfg.AI_MODEL_OPTIMIZER,
            max_tokens=2000,
            temperature=0,
            system=OPTIMIZER_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )

        input_tokens = msg.usage.input_tokens
        output_tokens = msg.usage.output_tokens
        raw_text = msg.content[0].text

        # Parse JSON from response (handle ```json fences)
        text = raw_text.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            lines = lines[1:]  # drop opening fence (```json or ```)
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]  # drop closing fence
            text = "\n".join(lines).strip()

        decisions = json.loads(text)
        decisions["_input_tokens"] = input_tokens
        decisions["_output_tokens"] = output_tokens
        log.info(
            "AI optimizer returned decisions (confidence=%.2f, tokens=%d/%d)",
            decisions.get("confidence", 0), input_tokens, output_tokens,
        )
        return decisions

    except json.JSONDecodeError as e:
        log.warning("AI optimizer returned invalid JSON: %s", e)
        return None
    except Exception as e:
        log.warning("AI optimizer API call failed: %s", e)
        return None


# ── Decision Application ─────────────────────────────────────────────────────

async def _apply_decisions(decisions: dict, context: dict) -> dict:
    """Apply AI decisions through guardrails. Returns summary of applied/blocked changes."""
    enforcer = GuardrailEnforcer()
    confidence = decisions.get("confidence", 0.5)
    input_tokens = decisions.get("_input_tokens")
    output_tokens = decisions.get("_output_tokens")
    results = {"applied": [], "blocked": [], "shadow": []}

    # ── Min Score ────────────────────────────────────────────────────────────
    min_score_rec = decisions.get("min_score")
    if min_score_rec and isinstance(min_score_rec, dict):
        new_score = min_score_rec.get("value", 50)
        old_score = ai_params.get_min_score()

        if ai_params.shadow_mode:
            delta = new_score - old_score
            await log_shadow_decision("min_score", None, min_score_rec, old_score,
                                      delta_value=delta, confidence=confidence,
                                      regime=context.get("current_regime"))
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
            )
            key = "applied" if result.get("applied") else "blocked"
            results[key].append(f"min_score: {old_score} -> {new_score}")

    # ── Risk Multiplier ──────────────────────────────────────────────────────
    risk_rec = decisions.get("risk_multiplier")
    if risk_rec and isinstance(risk_rec, dict):
        new_mult = risk_rec.get("value", 1.0)
        old_mult = ai_params.get_risk_multiplier()

        if ai_params.shadow_mode:
            delta = new_mult - old_mult
            await log_shadow_decision("risk_multiplier", None, risk_rec, old_mult,
                                      delta_value=delta, confidence=confidence,
                                      regime=context.get("current_regime"))
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
            )
            key = "applied" if result.get("applied") else "blocked"
            results[key].append(f"risk_mult: {old_mult:.2f} -> {new_mult:.2f}")

    # ── Rule Changes ─────────────────────────────────────────────────────────
    rule_changes = decisions.get("rule_changes", [])
    for rc in rule_changes:
        rule_id = rc.get("rule_id", "")
        action = rc.get("action", "")
        reason = rc.get("reason", "")

        if not rule_id or action not in ("disable", "enable", "boost", "reduce"):
            continue

        if ai_params.shadow_mode:
            # B13 FIX: Meaningful delta for rule changes
            rule_delta = {"disable": -1.0, "enable": 1.0,
                          "boost": float(rc.get("sizing_mult", 1.3)) - 1.0,
                          "reduce": float(rc.get("sizing_mult", 0.7)) - 1.0}.get(action, 0.0)
            await log_shadow_decision("rule_change", None, rc, {"rule_id": rule_id, "action": "none"},
                                      delta_value=rule_delta, confidence=confidence,
                                      regime=context.get("current_regime"))
            results["shadow"].append(f"rule {action}: {rule_id} (shadow)")
            continue

        if action in ("disable", "enable"):
            result = await enforcer.execute_with_audit(
                action_type=f"rule_{action}",
                category="rule_change",
                description=f"AI {action} rule: {rule_id}",
                old_value={"rule_id": rule_id, "enabled": action != "disable"},
                new_value={"rule_id": rule_id, "enabled": action == "enable"},
                reason=reason,
                confidence=confidence,
                apply_fn=lambda rid=rule_id, act=action: _toggle_rule(rid, act == "enable"),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
            key = "applied" if result.get("applied") else "blocked"
            results[key].append(f"rule_{action}: {rule_id}")

        elif action in ("boost", "reduce"):
            raw_sizing = rc.get("sizing_mult", 1.3 if action == "boost" else 0.7)
            # Hard clamp: sizing multiplier must be between 0.1x and 3.0x
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
            )
            key = "applied" if result.get("applied") else "blocked"
            results[key].append(f"rule_{action}: {rule_id} x{sizing:.2f}")

    # ── Rule Lab Actions (create/modify/pause/retire rules) ────────────────
    rule_actions = decisions.get("rule_actions", [])
    if rule_actions:
        try:
            from ai_rule_lab import apply_rule_actions
            lab_results = await apply_rule_actions(rule_actions, author="ai", allow_active=False)
            for lr in lab_results:
                if lr.get("ok"):
                    results["applied"].append(f"rule_{lr['action']}: {lr.get('rule_id', '?')}")
                else:
                    results["blocked"].append(f"rule_{lr.get('action', '?')}: {lr.get('reason', 'failed')}")
        except Exception as e:
            log.error("Rule Lab actions failed: %s", e)
            results["blocked"].append(f"rule_lab_error: {e}")

    # ── Direct AI Trades ─────────────────────────────────────────────────────
    direct_trades = decisions.get("direct_trades", [])
    if direct_trades and ai_params.shadow_mode:
        for dt in direct_trades:
            results["shadow"].append(f"direct_trade: {dt.get('symbol', '?')} {dt.get('action', '?')} (shadow)")
    elif direct_trades:
        # Phase 5: Direct trade execution will be wired here
        for dt in direct_trades:
            results["applied"].append(f"direct_trade: {dt.get('symbol', '?')} (queued for Phase 5)")

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
        log.info("AI optimization cycle starting...")

        # Step 1: Gather context
        context = await _build_context()
        if not context:
            return {"skipped": True, "reason": "no context data"}

        # Step 2: Get AI decisions
        decisions = await _get_ai_decisions(context)
        if not decisions:
            return {"skipped": True, "reason": "no API key or API failed"}

        # Step 3: Apply through guardrails
        results = await _apply_decisions(decisions, context)

        _last_optimization = time.time()
        ai_params.last_recompute = _last_optimization

        elapsed = time.time() - start
        log.info(
            "AI optimization complete in %.1fs: %d applied, %d blocked, %d shadow",
            elapsed, len(results["applied"]), len(results["blocked"]), len(results["shadow"]),
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
            if should_recompute():
                await run_full_optimization()
        except Exception as e:
            log.error("AI optimization loop error: %s", e)
        await asyncio.sleep(60)  # Check every minute if it's time to optimize

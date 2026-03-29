"""Rule validation, paper evaluation, and auto-promotion for AI-managed rules."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from ai_guardrails import log_ai_action
from database import get_rules, get_trades, get_rule_validation_runs, save_rule_validation_run, persist_rule_revision
from models import Rule
from safety_kernel import is_autopilot_live
from screener import load_universe
from trade_utils import get_trade_realized_pnl, is_closed_canonical

log = logging.getLogger(__name__)


def validate_rule_schema(rule: Rule) -> tuple[bool, list[str]]:
    errors: list[str] = []
    if not rule.conditions:
        errors.append("Rule must have at least one condition")
    if rule.action.type == "SELL" and rule.status in ("draft", "paper", "active"):
        errors.append("Short-biased AI rules are not allowed in v1")
    if rule.status == "active" and not rule.enabled:
        errors.append("Active rules must be enabled")
    return (len(errors) == 0, errors)


def evaluate_validation_run(
    *,
    trades_count: int,
    expectancy: float | None,
    max_drawdown: float | None,
    overlap_score: float | None,
    min_trades: int = 5,
    max_drawdown_pct: float = 15.0,
    max_overlap_score: float = 0.85,
) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if trades_count < min_trades:
        reasons.append(f"Needs at least {min_trades} trades")
    if expectancy is None or expectancy <= 0:
        reasons.append("Expectancy must be positive")
    if max_drawdown is not None and max_drawdown > max_drawdown_pct:
        reasons.append(f"Max drawdown {max_drawdown:.2f}% exceeds {max_drawdown_pct:.2f}%")
    if overlap_score is not None and overlap_score > max_overlap_score:
        reasons.append(f"Overlap score {overlap_score:.2f} exceeds {max_overlap_score:.2f}")
    return (len(reasons) == 0, reasons)


async def record_validation_result(
    *,
    rule: Rule,
    validation_mode: str,
    trades_count: int,
    hit_rate: float | None,
    net_pnl: float | None,
    expectancy: float | None,
    max_drawdown: float | None,
    overlap_score: float | None,
    passed: bool,
    notes: str | None = None,
    details: dict | None = None,
) -> None:
    await save_rule_validation_run(
        rule_id=rule.id,
        version=rule.version,
        validation_mode=validation_mode,
        trades_count=trades_count,
        hit_rate=hit_rate,
        net_pnl=net_pnl,
        expectancy=expectancy,
        max_drawdown=max_drawdown,
        overlap_score=overlap_score,
        passed=passed,
        notes=notes,
        details=details,
    )


async def get_validation_history(rule: Rule) -> list[dict]:
    return await get_rule_validation_runs(rule.id)


async def evaluate_promotion_gate(rule: Rule) -> tuple[bool, list[str], dict | None]:
    schema_ok, schema_errors = validate_rule_schema(rule)
    if not schema_ok:
        return False, schema_errors, None
    if rule.status != "paper":
        return False, [f"Rule status must be 'paper' to promote, got '{rule.status}'"], None

    history = await get_validation_history(rule)
    if not history:
        return False, ["No validation runs recorded"], None

    latest = history[0]

    # HB1-03: Require canonical evidence for promotion — legacy fallback is diagnostic only
    data_quality = latest.get("data_quality")
    if data_quality and data_quality != "canonical":
        return False, [f"Promotion requires canonical evidence, got '{data_quality}'"], latest

    metrics_ok, metric_errors = evaluate_validation_run(
        trades_count=int(latest.get("trades_count", 0) or 0),
        expectancy=latest.get("expectancy"),
        max_drawdown=latest.get("max_drawdown"),
        overlap_score=latest.get("overlap_score"),
    )
    if not latest.get("passed"):
        metric_errors.insert(0, "Latest validation run did not pass")
    return bool(latest.get("passed")) and metrics_ok, metric_errors, latest


# ── Paper Rule Evaluation ────────────────────────────────────────────────────

async def evaluate_paper_rules() -> list[dict]:
    """Evaluate all paper rules against canonical closed trade outcomes."""
    rules = await get_rules()
    paper_rules = [r for r in rules if r.status == "paper" and r.ai_generated]

    if not paper_rules:
        return []

    trades = await get_trades(limit=2000)
    results = []

    for rule in paper_rules:
        # Primary: match by rule_id with canonical closed outcomes
        primary = [
            t for t in trades
            if t.rule_id == rule.id and is_closed_canonical(t)
        ]

        # Legacy fallback: symbol/universe match with metadata pnl
        legacy = []
        if not primary:
            if rule.universe:
                try:
                    _universe_syms = set(load_universe(rule.universe))
                except Exception:
                    _universe_syms = set()
            else:
                _universe_syms = set()
            legacy = [
                t for t in trades
                if (t.symbol == rule.symbol or (rule.universe and t.symbol in _universe_syms))
                and t.status == "FILLED"
                and t.metadata.get("pnl") is not None
            ]

        evaluated = primary or legacy
        is_canonical = bool(primary)
        trade_count = len(evaluated)
        symbols_seen = sorted(set(t.symbol for t in evaluated))

        if trade_count < 3:
            results.append({
                "rule_id": rule.id,
                "status": "insufficient_data",
                "trades": trade_count,
                "evaluated_closed_count": len(primary),
                "excluded_legacy_count": len(legacy) if not primary else 0,
                "data_quality": "canonical" if is_canonical else "legacy_fallback",
            })
            continue

        # Extract P&L — canonical first, never fall back if realized_pnl present
        pnl_entries: list[tuple[float, float | None, str | None]] = []  # (pnl, pnl_pct, closed_at)
        for t in evaluated:
            pnl = get_trade_realized_pnl(t)
            if pnl is not None:
                pnl_entries.append((pnl, t.pnl_pct, t.closed_at))

        # Sort by closed_at for drawdown computation
        pnl_entries.sort(key=lambda x: x[2] or "")
        pnls = [e[0] for e in pnl_entries]
        pnl_pcts = [e[1] for e in pnl_entries if e[1] is not None]

        if pnls:
            wins = sum(1 for p in pnls if p > 0)
            hit_rate = wins / len(pnls)
            avg_win = sum(p for p in pnls if p > 0) / max(wins, 1)
            losses = sum(1 for p in pnls if p <= 0)
            avg_loss = abs(sum(p for p in pnls if p <= 0) / max(losses, 1))
            expectancy = (hit_rate * avg_win) - ((1 - hit_rate) * avg_loss) if len(pnls) >= 5 else None
            net_pnl = sum(pnls)

            # Drawdown: use pnl_pct when available (canonical), fall back to dollar-based percent
            if pnl_pcts and len(pnl_pcts) == len(pnls):
                # Compute max drawdown from pnl_pct series
                cumulative_pct = 0.0
                peak_pct = 0.0
                max_dd = 0.0
                for pp in pnl_pcts:
                    cumulative_pct += pp
                    if cumulative_pct > peak_pct:
                        peak_pct = cumulative_pct
                    dd = peak_pct - cumulative_pct
                    if dd > max_dd:
                        max_dd = dd
            else:
                # Fallback: approximate from dollar P&L as percent of peak
                cumulative = 0.0
                peak = 0.0
                max_dd = 0.0
                for p in pnls:
                    cumulative += p
                    if cumulative > peak:
                        peak = cumulative
                    if peak > 0:
                        dd_pct = ((peak - cumulative) / peak) * 100.0
                        if dd_pct > max_dd:
                            max_dd = dd_pct
        else:
            hit_rate = None
            expectancy = None
            net_pnl = 0.0
            max_dd = 0.0

        # Gate: only pass if we have real evidence
        passed = (
            len(pnls) >= 5
            and hit_rate is not None
            and hit_rate >= 0.4
            and (expectancy or 0) > 0
        )

        # Build evidence details for S9-02b persistence
        # Compute validation window from trade timestamps
        eval_timestamps = [t.closed_at or t.timestamp for t in evaluated if (t.closed_at or t.timestamp)]
        if eval_timestamps:
            window_start = min(eval_timestamps)[:10]
            window_end = max(eval_timestamps)[:10]
            validation_window = f"{window_start} to {window_end}"
        else:
            validation_window = None

        evidence = {
            "evaluated_closed_count": len(primary),
            "excluded_legacy_count": len(legacy) if not primary else 0,
            "symbols_evaluated": symbols_seen,
            "data_quality": "canonical" if is_canonical else "legacy_fallback",
            "validation_window": validation_window,
        }

        await record_validation_result(
            rule=rule,
            validation_mode="paper_auto",
            trades_count=trade_count,
            hit_rate=hit_rate,
            net_pnl=net_pnl,
            expectancy=expectancy,
            max_drawdown=max_dd,
            overlap_score=0.0,
            passed=passed,
            notes=f"Auto-evaluated: {trade_count} trades ({len(primary)} canonical, {len(legacy)} legacy)",
            details=evidence,
        )

        results.append({
            "rule_id": rule.id,
            "name": rule.name,
            "status": "passed" if passed else "needs_more_data",
            "trades": trade_count,
            "passed": passed,
            "evaluated_closed_count": len(primary),
            "excluded_legacy_count": len(legacy) if not primary else 0,
            "data_quality": "canonical" if is_canonical else "legacy_fallback",
        })

    return results


# ── Auto-Promotion ───────────────────────────────────────────────────────────

async def auto_promote_paper_rules() -> list[dict]:
    """Check all paper rules for promotion eligibility and promote if ready."""
    if not is_autopilot_live():
        log.debug("Auto-promotion skipped — autopilot not LIVE")
        return []

    rules = await get_rules()
    paper_rules = [r for r in rules if r.status == "paper" and r.ai_generated]
    promoted = []

    for rule in paper_rules:
        can_promote, reasons, latest_run = await evaluate_promotion_gate(rule)

        if can_promote:
            # Promote: paper → active
            rule.status = "active"
            rule.enabled = True
            await persist_rule_revision(rule, diff_summary="Auto-promoted: passed validation", author="ai")

            await log_ai_action(
                action_type="rule_promote",
                category="rule_lab",
                description=f"Auto-promoted rule: {rule.name} (paper → active)",
                old_value={"status": "paper"},
                new_value={"status": "active", "enabled": True},
                reason="Passed all promotion gates",
                confidence=1.0,
                status="applied",
                param_type="rule",
            )

            promoted.append({"rule_id": rule.id, "name": rule.name})
            log.info("AUTO-PROMOTED rule: %s (paper → active)", rule.name)

        else:
            log.debug("Rule %s not ready for promotion: %s", rule.name, reasons)

    return promoted

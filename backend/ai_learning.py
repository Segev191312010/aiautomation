"""
AI Learning Loop — Self-evaluation, cost tracking, graded autonomy.

Runs every 6 hours:
1. evaluate_past_decisions() — score AI decisions across 7/30/90d windows
2. check_auto_tighten() — 3-level safety waterfall
3. compute_cost_report() — real Claude API costs from audit log
4. compute_economic_report() — ROI analysis: is AI paying for itself?
"""
from __future__ import annotations

import asyncio
import bisect
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from database import get_db
from ai_guardrails import _load_guardrails_from_db, save_guardrails_to_db, log_ai_action
from ai_params import ai_params

log = logging.getLogger(__name__)

SUPPORTED_WINDOWS = (7, 30, 90)
MIN_TRADES_PER_WINDOW = 20
TRADE_WINDOW_SIZE = 50

# Sonnet 4 pricing ($/MTok)
MODEL_PRICING = {
    "claude-sonnet-4-20250514": (3.0, 15.0),
    "claude-3-5-sonnet-20241022": (3.0, 15.0),
    "claude-haiku-4-5-20251001": (0.25, 1.25),
    "claude-3-5-haiku-20241022": (0.25, 1.25),
}
DEFAULT_PRICING = (3.0, 15.0)  # Sonnet fallback


# ── Cost Report ──────────────────────────────────────────────────────────────

async def compute_cost_report(days: int = 30) -> dict:
    """S10: Aggregate costs from ai_decision_runs (model-aware), with audit_log fallback."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    # Primary: read from decision runs (has model column)
    async with get_db() as db:
        try:
            cur = await db.execute(
                "SELECT created_at, model, input_tokens, output_tokens "
                "FROM ai_decision_runs "
                "WHERE input_tokens IS NOT NULL AND created_at >= ? "
                "ORDER BY created_at ASC",
                (cutoff,),
            )
            rows = await cur.fetchall()
        except Exception as exc:
            log.debug("Decision run query failed, falling back to audit_log: %s", exc)
            rows = []

    # Fallback: audit_log if no decision runs yet
    if not rows:
        async with get_db() as db:
            cur = await db.execute(
                "SELECT timestamp, input_tokens, output_tokens "
                "FROM ai_audit_log "
                "WHERE input_tokens IS NOT NULL AND timestamp >= ? "
                "ORDER BY timestamp ASC",
                (cutoff,),
            )
            legacy_rows = await cur.fetchall()

        if not legacy_rows:
            return {"days": days, "total_cost_usd": 0, "total_calls": 0, "daily": []}

        # Legacy path: single-model pricing
        rate_in, rate_out = DEFAULT_PRICING
        by_day: dict[str, dict] = {}
        for ts, in_tok, out_tok in legacy_rows:
            day = ts[:10] if ts else "unknown"
            if day not in by_day:
                by_day[day] = {"calls": 0, "input_tokens": 0, "output_tokens": 0}
            by_day[day]["calls"] += 1
            by_day[day]["input_tokens"] += in_tok or 0
            by_day[day]["output_tokens"] += out_tok or 0

        daily = []
        total_cost = 0.0
        total_calls = 0
        for day in sorted(by_day.keys()):
            d = by_day[day]
            cost = (d["input_tokens"] * rate_in + d["output_tokens"] * rate_out) / 1_000_000
            daily.append({"date": day, "calls": d["calls"], "input_tokens": d["input_tokens"],
                          "output_tokens": d["output_tokens"], "estimated_cost_usd": round(cost, 4)})
            total_cost += cost
            total_calls += d["calls"]
        return {"days": days, "total_cost_usd": round(total_cost, 4), "total_calls": total_calls, "daily": daily}

    # S10 path: model-aware pricing from decision runs
    by_day = {}
    for ts, model, in_tok, out_tok in rows:
        day = ts[:10] if ts else "unknown"
        if day not in by_day:
            by_day[day] = {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cost": 0.0}
        by_day[day]["calls"] += 1
        by_day[day]["input_tokens"] += in_tok or 0
        by_day[day]["output_tokens"] += out_tok or 0
        rate_in, rate_out = _get_model_pricing(model)
        by_day[day]["cost"] += ((in_tok or 0) * rate_in + (out_tok or 0) * rate_out) / 1_000_000

    daily = []
    total_cost = 0.0
    total_calls = 0
    for day in sorted(by_day.keys()):
        d = by_day[day]
        daily.append({"date": day, "calls": d["calls"], "input_tokens": d["input_tokens"],
                      "output_tokens": d["output_tokens"], "estimated_cost_usd": round(d["cost"], 4)})
        total_cost += d["cost"]
        total_calls += d["calls"]

    return {"days": days, "total_cost_usd": round(total_cost, 4), "total_calls": total_calls, "daily": daily}


def _get_model_pricing(model: str | None) -> tuple[float, float]:
    """Look up pricing for a model name, with fuzzy matching."""
    if not model:
        return DEFAULT_PRICING
    for key, pricing in MODEL_PRICING.items():
        if key in model or model in key:
            return pricing
    # Check for model family
    m = model.lower()
    if "haiku" in m:
        return (0.25, 1.25)
    if "opus" in m:
        return (15.0, 75.0)
    return DEFAULT_PRICING


# ── Decision Evaluation ──────────────────────────────────────────────────────

async def evaluate_past_decisions(window_days: int = 30) -> dict:
    """S10: Ledger-backed evaluation of AI decisions. Falls back to audit heuristic if no ledger data."""
    if window_days not in SUPPORTED_WINDOWS:
        window_days = 30

    cutoff = (datetime.now(timezone.utc) - timedelta(days=window_days)).isoformat()

    # Try S10 ledger first
    async with get_db() as db:
        try:
            cur = await db.execute(
                "SELECT COUNT(*) FROM ai_decision_runs WHERE created_at >= ?", (cutoff,)
            )
            run_count = (await cur.fetchone())[0]
        except Exception as exc:
            log.debug("Decision run count query failed: %s", exc)
            run_count = 0

    if run_count > 0:
        return await _evaluate_from_ledger(window_days, cutoff)

    # Fallback: legacy audit-log heuristic
    return await _evaluate_from_audit_log(window_days, cutoff)


async def _evaluate_from_ledger(window_days: int, cutoff: str) -> dict:
    """Read from ai_decision_runs + ai_decision_items for metrics."""
    async with get_db() as db:
        # Count runs
        cur = await db.execute(
            "SELECT COUNT(*) FROM ai_decision_runs WHERE created_at >= ?", (cutoff,)
        )
        total_runs = (await cur.fetchone())[0]

        # Fetch all items in window
        cur = await db.execute(
            "SELECT i.item_type, i.gate_status, i.confidence, i.realized_pnl, "
            "i.score_status, i.regime "
            "FROM ai_decision_items i "
            "JOIN ai_decision_runs r ON i.run_id = r.id "
            "WHERE r.created_at >= ? "
            "ORDER BY i.created_at",
            (cutoff,),
        )
        items_raw = await cur.fetchall()

        # Count abstained runs
        cur = await db.execute(
            "SELECT COUNT(*) FROM ai_decision_runs WHERE created_at >= ? AND abstained = 1",
            (cutoff,),
        )
        abstained_runs = (await cur.fetchone())[0]

    total_items = len(items_raw)
    if total_items == 0:
        return _empty_metrics(window_days, "No decision items in window")

    # Compute metrics
    scored = [r for r in items_raw if r[4] != "unscored" and r[3] is not None]
    scored_count = len(scored)
    wins = sum(1 for r in scored if (r[3] or 0) > 0)
    hit_rate = (wins / scored_count) if scored_count else None
    net_pnl = sum(float(r[3] or 0) for r in scored)

    # By action type
    by_action: dict[str, dict] = {}
    for r in items_raw:
        at = r[0]
        by_action.setdefault(at, {"count": 0, "scored": 0, "hits": 0, "net_pnl": 0.0})
        by_action[at]["count"] += 1
        if r[4] != "unscored" and r[3] is not None:
            by_action[at]["scored"] += 1
            by_action[at]["net_pnl"] += float(r[3] or 0)
            if (r[3] or 0) > 0:
                by_action[at]["hits"] += 1

    action_breakdown = {}
    for action, data in by_action.items():
        action_breakdown[action] = {
            "count": data["count"],
            "hit_rate": round(data["hits"] / max(data["scored"], 1), 4) if data["scored"] else None,
            "net_pnl": round(data["net_pnl"], 2),
        }

    # By regime
    by_regime: dict[str, dict] = {}
    for r in items_raw:
        regime = r[5] or "unknown"
        by_regime.setdefault(regime, {"count": 0, "scored": 0, "hits": 0})
        by_regime[regime]["count"] += 1
        if r[4] != "unscored" and r[3] is not None:
            by_regime[regime]["scored"] += 1
            if (r[3] or 0) > 0:
                by_regime[regime]["hits"] += 1

    regime_breakdown = {}
    for regime, data in by_regime.items():
        regime_breakdown[regime] = {
            "count": data["count"],
            "hit_rate": round(data["hits"] / max(data["scored"], 1), 4) if data["scored"] else None,
        }

    # Confidence + calibration
    confidences = [float(r[2]) for r in items_raw if r[2] is not None]
    avg_confidence = (sum(confidences) / len(confidences)) if confidences else None
    scored_confs = [float(r[2]) for r in scored if r[2] is not None]
    calibration_error = None
    if scored_confs and hit_rate is not None:
        calibration_error = round(abs(sum(scored_confs) / len(scored_confs) - hit_rate), 4)

    # Data quality
    if scored_count == 0:
        quality = "insufficient"
    elif scored_count < 10:
        quality = "low"
    elif scored_count < 30:
        quality = "moderate"
    else:
        quality = "good"

    return {
        "window_days": window_days,
        "total_decisions": total_items,
        "total_runs": total_runs,
        "scored_decisions": scored_count,
        "hit_rate": round(hit_rate, 4) if hit_rate is not None else None,
        "net_score": wins - (scored_count - wins),
        "net_pnl_impact": round(net_pnl, 2) if net_pnl else None,
        "data_quality": quality,
        "_source": "ledger",
        "abstain_rate": round(abstained_runs / max(total_runs, 1), 4) if total_runs else None,
        "avg_confidence": round(avg_confidence, 4) if avg_confidence is not None else None,
        "calibration_error": calibration_error,
        "by_action_type": action_breakdown,
        "by_regime": regime_breakdown,
        "warning": None,
    }


async def _evaluate_from_audit_log(window_days: int, cutoff: str) -> dict:
    """Legacy fallback: heuristic pre/post trade-window scoring from ai_audit_log."""
    async with get_db() as db:
        cur = await db.execute(
            "SELECT id, timestamp, action_type, description "
            "FROM ai_audit_log "
            "WHERE status = 'applied' AND timestamp >= ? "
            "ORDER BY timestamp ASC",
            (cutoff,),
        )
        decisions = await cur.fetchall()

        if not decisions:
            return _empty_metrics(window_days, "No applied decisions in window")

        earliest_ts = decisions[0][1]
        cur = await db.execute(
            "SELECT id, symbol, timestamp, data FROM trades "
            "WHERE timestamp < ? ORDER BY timestamp DESC LIMIT ?",
            (earliest_ts, TRADE_WINDOW_SIZE),
        )
        pre_trades_raw = list(reversed(await cur.fetchall()))

        cur = await db.execute(
            "SELECT id, symbol, timestamp, data FROM trades "
            "WHERE timestamp >= ? ORDER BY timestamp ASC LIMIT ?",
            (earliest_ts, len(decisions) * TRADE_WINDOW_SIZE + TRADE_WINDOW_SIZE),
        )
        post_trades_raw = await cur.fetchall()

    all_trades_raw = pre_trades_raw + post_trades_raw
    trades = []
    for t in all_trades_raw:
        try:
            tdata = json.loads(t[3]) if t[3] else {}
            pnl_val = tdata.get("realized_pnl")
            if pnl_val is None:
                pnl_val = tdata.get("pnl")
            pnl = float(pnl_val) if pnl_val is not None else 0.0
            trades.append({"id": t[0], "symbol": t[1], "timestamp": t[2], "pnl": pnl})
        except Exception as exc:
            log.debug("Skipping malformed trade row: %s", exc)
            continue

    if not trades:
        return _empty_metrics(window_days, "No trades available for evaluation")

    trade_timestamps = [t["timestamp"] for t in trades]
    hits = 0
    misses = 0
    by_action: dict[str, dict] = {}

    for d_id, d_ts, d_action, d_desc in decisions:
        if not d_ts:
            continue
        by_action.setdefault(d_action, {"count": 0, "hits": 0, "pnl_impacts": []})
        by_action[d_action]["count"] += 1

        idx = bisect.bisect_left(trade_timestamps, d_ts)
        pre = trades[max(0, idx - MIN_TRADES_PER_WINDOW):idx]
        post = trades[idx:idx + MIN_TRADES_PER_WINDOW]
        if len(pre) < MIN_TRADES_PER_WINDOW or len(post) < MIN_TRADES_PER_WINDOW:
            continue

        pnl_pre = sum(t["pnl"] for t in pre)
        pnl_post = sum(t["pnl"] for t in post)
        is_conservative = _is_conservative(d_action, d_desc)
        hit = pnl_post >= pnl_pre * 0.95 if is_conservative else pnl_post > pnl_pre * 1.02

        if hit:
            hits += 1
            by_action[d_action]["hits"] += 1
        else:
            misses += 1
        by_action[d_action]["pnl_impacts"].append(pnl_post - pnl_pre)

    scored = hits + misses
    hit_rate = hits / scored if scored > 0 else None

    quality = "insufficient" if scored == 0 else "low" if scored < 10 else "moderate" if scored < 30 else "good"
    action_breakdown = {
        action: {
            "count": data["count"],
            "hit_rate": data["hits"] / max(1, len(data["pnl_impacts"])) if data["pnl_impacts"] else None,
            "net_pnl": sum(data["pnl_impacts"]) if data["pnl_impacts"] else 0,
        }
        for action, data in by_action.items()
    }

    return {
        "window_days": window_days,
        "total_decisions": len(decisions),
        "scored_decisions": scored,
        "hit_rate": round(hit_rate, 4) if hit_rate is not None else None,
        "net_score": hits - misses,
        "net_pnl_impact": None,
        "data_quality": quality,
        "_source": "legacy_fallback",
        "by_action_type": action_breakdown,
        "warning": f"Legacy heuristic evaluation ({scored} scored)" if scored > 0 else "No scored decisions",
    }


def _is_conservative(action_type: str, description: str) -> bool:
    """Conservative = safer bet (tightening risk, raising min_score)."""
    desc = description.lower()
    conservative_words = ("raise", "increase", "tighten", "reduce risk", "disable")
    return any(w in desc for w in conservative_words)


def _empty_metrics(window_days: int, warning: str) -> dict:
    return {
        "window_days": window_days,
        "total_decisions": 0,
        "scored_decisions": 0,
        "hit_rate": None,
        "net_score": 0,
        "net_pnl_impact": None,
        "data_quality": "insufficient",
        "_source": "insufficient",
        "by_action_type": {},
        "warning": warning,
    }


# ── Economic Report ──────────────────────────────────────────────────────────

async def compute_economic_report(days: int = 30) -> dict:
    """S10: ROI analysis from evaluated decision impact vs actual token cost.

    Threads data_quality and metric_source so operators know if the ROI
    numbers are canonical (ledger-backed) or degraded (audit-log proxy).
    """
    learning = await evaluate_past_decisions(days)
    costs = await compute_cost_report(days)

    ai_pnl = learning.get("net_pnl_impact") or 0
    total_cost = costs.get("total_cost_usd", 0)
    total_decisions = learning.get("total_decisions", 0)
    total_runs = learning.get("total_runs", total_decisions)
    learning_quality = learning.get("data_quality", "insufficient")
    metric_source = learning.pop("_source", "insufficient")

    return {
        "days": days,
        "ai_pnl_impact": round(ai_pnl, 2),
        "total_cost": round(total_cost, 4),
        "cost_per_decision": round(total_cost / max(total_runs, 1), 4),
        "roi_estimate": round(ai_pnl / max(total_cost, 0.001), 2) if total_cost > 0 else None,
        "cost_as_pct_pnl": round((total_cost / max(abs(ai_pnl), 1.0)) * 100, 2) if ai_pnl != 0 else None,
        "decisions_per_day": round(total_runs / max(days, 1), 2),
        "data_quality": learning_quality,
        "metric_source": metric_source,
    }


# ── Auto-Tighten (3-Level Safety Waterfall) ──────────────────────────────────

async def check_auto_tighten() -> dict:
    """Graded safety response to AI underperformance."""
    config = await _load_guardrails_from_db()
    if not config.auto_tighten_enabled:
        return {"action": "disabled", "reason": "auto_tighten_enabled=false"}

    actions_taken = []

    # Level 1: 7-day fast reaction
    metrics_7d = await evaluate_past_decisions(7)
    if (metrics_7d["hit_rate"] is not None
            and metrics_7d["hit_rate"] < config.auto_tighten_bad_hit_rate_7d
            and metrics_7d["scored_decisions"] >= config.auto_tighten_min_decisions_7d
            and not config.guardrails_currently_tightened):

        # Scale down aggressive limits by 50%
        config = config.model_copy(update={
            "max_changes_per_day": max(1, config.max_changes_per_day // 2),
            "max_position_size_increase_pct": config.max_position_size_increase_pct * 0.5,
            "max_weight_change_pct": config.max_weight_change_pct * 0.5,
            "min_score_floor": min(80, config.min_score_floor + 2),
            "guardrails_currently_tightened": True,
            "tightened_at": datetime.now(timezone.utc).isoformat(),
            "tightened_reason": f"Level 1: 7d hit_rate={metrics_7d['hit_rate']:.2f}",
        })
        await save_guardrails_to_db(config)
        await log_ai_action(
            action_type="auto_tighten_level1",
            category="self_evaluation",
            description=f"AI Level 1 tighten: 7d hit_rate={metrics_7d['hit_rate']:.2f}",
            confidence=1.0,
            status="applied",
        )
        actions_taken.append("level1_tightened")
        log.warning("AI auto-tighten LEVEL 1: 7d hit_rate=%.2f", metrics_7d["hit_rate"])

    # Level 2: 30-day prolonged failure (already tightened)
    metrics_30d = await evaluate_past_decisions(30)
    if (config.guardrails_currently_tightened
            and metrics_30d["hit_rate"] is not None
            and metrics_30d["hit_rate"] < config.auto_tighten_bad_hit_rate_30d
            and metrics_30d["scored_decisions"] >= config.auto_tighten_min_decisions_30d):

        # Revert to PAPER mode: keep AI active, but move execution back to simulated-only.
        config = config.model_copy(update={"autopilot_mode": "PAPER"})
        await save_guardrails_to_db(config)
        ai_params.shadow_mode = False  # PAPER = AI still active (creates paper rules), but no live orders
        await log_ai_action(
            action_type="auto_tighten_level2",
            category="self_evaluation",
            description=f"AI Level 2 paper revert: 30d hit_rate={metrics_30d['hit_rate']:.2f}",
            confidence=1.0,
            status="applied",
        )
        actions_taken.append("level2_paper_revert")
        log.warning("AI auto-tighten LEVEL 2: PAPER mode re-enabled, 30d hit_rate=%.2f", metrics_30d["hit_rate"])

    # Recovery: performance improves while tightened (elif to prevent same-cycle oscillation)
    elif (config.guardrails_currently_tightened
            and metrics_30d["hit_rate"] is not None
            and metrics_30d["hit_rate"] > 0.55
            and metrics_30d["scored_decisions"] >= 50):

        config = config.model_copy(update={
            "guardrails_currently_tightened": False,
            "tightened_at": None,
            "tightened_reason": None,
            # Restore defaults
            "max_changes_per_day": 10,
            "max_position_size_increase_pct": 25.0,
            "max_weight_change_pct": 30.0,
        })
        await save_guardrails_to_db(config)
        await log_ai_action(
            action_type="auto_tighten_recovery",
            category="self_evaluation",
            description=f"AI Recovery: 30d hit_rate={metrics_30d['hit_rate']:.2f} — limits restored",
            confidence=1.0,
            status="applied",
        )
        actions_taken.append("recovery_restored")
        log.info("AI auto-tighten RECOVERY: limits restored, 30d hit_rate=%.2f", metrics_30d["hit_rate"])

    return {"actions_taken": actions_taken}


# ── Background Loop ──────────────────────────────────────────────────────────

async def ai_learning_loop() -> None:
    """Background task: evaluate AI performance every 6 hours."""
    log.info("AI learning loop started (interval=6h)")
    while True:
        try:
            metrics = await evaluate_past_decisions(30)
            log.info(
                "AI learning eval: %d scored, hit_rate=%s, quality=%s",
                metrics["scored_decisions"],
                f"{metrics['hit_rate']:.2f}" if metrics["hit_rate"] else "N/A",
                metrics["data_quality"],
            )
            await check_auto_tighten()
        except Exception as e:
            log.exception("AI learning loop error: %s", e)
        await asyncio.sleep(6 * 3600)

"""
AI Trading Advisor — analyze bot performance, generate recommendations, auto-tune rules.

Layers:
  1. Data Collection   — async DB fetch
  2. Analysis Engine   — pure Python metrics
  3. Recommendations   — prioritized actionable list
  4. Auto-tune         — compute + apply rule changes
  5. AI Narrative      — Claude API or template fallback
  6. Orchestrator      — build_full_report()
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from config import cfg
from database import get_rules, get_trades, save_rule
from models import Rule, Trade
from portfolio_analytics import compute_realized_pnl, compute_performance_metrics
from risk_manager import get_sector

log = logging.getLogger(__name__)

MIN_TRADES = getattr(cfg, "ADVISOR_MIN_TRADES", 5)


# ── Layer 1: Data Collection ─────────────────────────────────────────────────

async def fetch_advisor_data(lookback_days: int = 90) -> dict:
    """Fetch all data needed for analysis."""
    trades = await get_trades(limit=2000)
    trade_dicts = [t.model_dump() for t in trades]

    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    recent = [t for t in trade_dicts if t.get("timestamp", "") > cutoff.isoformat()]

    pnl = compute_realized_pnl(recent)
    rules = await get_rules()

    return {
        "trades": recent,
        "matched_trades": pnl.get("matched_trades", []),
        "pnl_summary": {k: v for k, v in pnl.items() if k != "matched_trades"},
        "rules": rules,
        "lookback_days": lookback_days,
    }


# ── Layer 2: Analysis Engine ─────────────────────────────────────────────────

def analyze_rule_performance(matched_trades: list[dict], rules: list[Rule]) -> list[dict]:
    """Per-rule: win_rate, profit_factor, total_pnl, trade count, verdict."""
    rule_names = {r.id: r.name for r in rules}
    by_rule: dict[str, list[dict]] = defaultdict(list)

    for t in matched_trades:
        # Try to find rule_id from the trade's entry
        rule_name = t.get("rule_name", t.get("entry_rule", "Unknown"))
        by_rule[rule_name].append(t)

    results = []
    for name, trades in by_rule.items():
        pnls = [t["pnl"] for t in trades]
        winners = [p for p in pnls if p > 0]
        losers = [p for p in pnls if p <= 0]
        total = len(trades)
        win_rate = len(winners) / total if total > 0 else 0
        pf = abs(sum(winners)) / abs(sum(losers)) if losers and sum(losers) != 0 else 999

        if total >= MIN_TRADES * 2 and win_rate < 0.30:
            verdict = "disable"
        elif total >= MIN_TRADES and win_rate >= 0.65 and pf >= 1.5:
            verdict = "boost"
        elif total < MIN_TRADES:
            verdict = "watch"
        elif win_rate < 0.40 or pf < 1.0:
            verdict = "reduce"
        else:
            verdict = "hold"

        status = "good" if verdict in ("hold", "boost") else "bad" if verdict == "disable" else "ok"

        results.append({
            "rule_id": name,
            "rule_name": name,
            "total_trades": total,
            "win_rate": round(win_rate * 100, 1),
            "profit_factor": round(pf, 2) if pf < 100 else 999,
            "total_pnl": round(sum(pnls), 2),
            "avg_pnl": round(sum(pnls) / total, 2) if total > 0 else 0,
            "avg_win": round(sum(winners) / len(winners), 2) if winners else 0,
            "avg_loss": round(sum(losers) / len(losers), 2) if losers else 0,
            "verdict": verdict,
            "status": status,
        })

    return sorted(results, key=lambda x: x["total_trades"], reverse=True)


def analyze_sector_performance(matched_trades: list[dict]) -> list[dict]:
    """Per-sector stats."""
    by_sector: dict[str, list[float]] = defaultdict(list)
    for t in matched_trades:
        sector = get_sector(t.get("symbol", ""))
        by_sector[sector].append(t.get("pnl", 0))

    results = []
    for sector, pnls in by_sector.items():
        total = len(pnls)
        winners = [p for p in pnls if p > 0]
        win_rate = len(winners) / total if total > 0 else 0
        total_pnl = sum(pnls)

        if total >= 5 and win_rate < 0.40 and total_pnl < 0:
            verdict = "avoid"
        elif win_rate >= 0.60 and total_pnl > 0:
            verdict = "favor"
        else:
            verdict = "neutral"

        results.append({
            "sector": sector,
            "trade_count": total,
            "win_rate": round(win_rate * 100, 1),
            "total_pnl": round(total_pnl, 2),
            "verdict": verdict,
        })

    return sorted(results, key=lambda x: x["total_pnl"], reverse=True)


def analyze_time_patterns(matched_trades: list[dict]) -> list[dict]:
    """Best/worst trading hours."""
    by_hour: dict[int, list[float]] = defaultdict(list)
    for t in matched_trades:
        ts = t.get("entry_date", t.get("exit_date", ""))
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            by_hour[dt.hour].append(t.get("pnl", 0))
        except Exception:
            continue

    results = []
    for hour, pnls in sorted(by_hour.items()):
        total = len(pnls)
        winners = [p for p in pnls if p > 0]
        results.append({
            "hour": hour,
            "trade_count": total,
            "win_rate": round(len(winners) / total * 100, 1) if total > 0 else 0,
            "avg_pnl": round(sum(pnls) / total, 2) if total > 0 else 0,
            "total_pnl": round(sum(pnls), 2),
        })
    return results


def analyze_score_effectiveness(matched_trades: list[dict]) -> dict:
    """Score buckets: which score ranges produce best results?"""
    buckets: dict[str, list[float]] = defaultdict(list)

    for t in matched_trades:
        score = t.get("signal_score") or t.get("composite_score")
        if score is None:
            continue
        bucket = f"{int(score // 5) * 5}-{int(score // 5) * 5 + 5}"
        buckets[bucket].append(t.get("pnl_pct", t.get("pnl", 0)))

    if not buckets:
        return {"available": False, "buckets": [], "optimal_min_score": 50}

    result_buckets = []
    best_bucket = None
    best_avg = -999
    for bucket, pnls in sorted(buckets.items()):
        avg = sum(pnls) / len(pnls) if pnls else 0
        wr = len([p for p in pnls if p > 0]) / len(pnls) * 100 if pnls else 0
        result_buckets.append({"range": bucket, "count": len(pnls), "avg_pnl": round(avg, 2), "win_rate": round(wr, 1)})
        if avg > best_avg and len(pnls) >= 3:
            best_avg = avg
            best_bucket = bucket

    optimal = int(best_bucket.split("-")[0]) if best_bucket else 50

    return {"available": True, "buckets": result_buckets, "optimal_min_score": optimal, "current_min_score": 50}


def analyze_bracket_effectiveness(matched_trades: list[dict]) -> dict:
    """How often SL vs TP hit."""
    sl_hits = tp_hits = other = 0
    for t in matched_trades:
        pnl = t.get("pnl", 0)
        pnl_pct = t.get("pnl_pct", 0)
        if pnl_pct <= -1.5:
            sl_hits += 1
        elif pnl_pct >= 3.0:
            tp_hits += 1
        else:
            other += 1

    total = sl_hits + tp_hits + other
    return {
        "total_closed": total,
        "sl_hits": sl_hits,
        "tp_hits": tp_hits,
        "other_exits": other,
        "sl_hit_pct": round(sl_hits / total * 100, 1) if total > 0 else 0,
        "tp_hit_pct": round(tp_hits / total * 100, 1) if total > 0 else 0,
        "brackets_too_tight": sl_hits > total * 0.5 if total >= 10 else False,
    }


# ── Layer 3: Recommendations ─────────────────────────────────────────────────

def generate_recommendations(rule_perf, sector_perf, score_analysis, bracket_analysis) -> list[dict]:
    """Generate prioritized recommendations."""
    recs = []

    # Rule recommendations
    for r in rule_perf:
        if r["verdict"] == "disable":
            recs.append({
                "type": "disable", "priority": "high",
                "message": f"Disable rule '{r['rule_name']}' — {r['win_rate']}% win rate over {r['total_trades']} trades, ${r['total_pnl']:.0f} total P&L",
                "rule_id": r["rule_id"],
                "category": "rule",
            })
        elif r["verdict"] == "boost":
            recs.append({
                "type": "boost", "priority": "medium",
                "message": f"Scale up '{r['rule_name']}' — {r['win_rate']}% win rate, {r['profit_factor']:.1f} profit factor, ${r['total_pnl']:.0f} P&L",
                "rule_id": r["rule_id"],
                "category": "rule",
            })
        elif r["verdict"] == "reduce":
            recs.append({
                "type": "adjust", "priority": "medium",
                "message": f"Reduce size for '{r['rule_name']}' — {r['win_rate']}% win rate, PF {r['profit_factor']:.1f}",
                "rule_id": r["rule_id"],
                "category": "rule",
            })

    # Sector recommendations
    for s in sector_perf:
        if s["verdict"] == "avoid":
            recs.append({
                "type": "warning", "priority": "medium",
                "message": f"Avoid {s['sector']} sector — ${s['total_pnl']:.0f} loss from {s['trade_count']} trades ({s['win_rate']}% win rate)",
                "category": "sector",
            })
        elif s["verdict"] == "favor":
            recs.append({
                "type": "boost", "priority": "low",
                "message": f"Favor {s['sector']} — {s['win_rate']}% win rate, ${s['total_pnl']:.0f} P&L",
                "category": "sector",
            })

    # Score threshold
    if score_analysis.get("available") and abs(score_analysis["optimal_min_score"] - score_analysis["current_min_score"]) >= 5:
        opt = score_analysis["optimal_min_score"]
        cur = score_analysis["current_min_score"]
        if opt > cur:
            recs.append({
                "type": "adjust", "priority": "medium",
                "message": f"Raise min signal score from {cur} to {opt} — higher scores have better win rates",
                "category": "score",
            })

    # Bracket analysis
    if bracket_analysis.get("brackets_too_tight"):
        recs.append({
            "type": "warning", "priority": "high",
            "message": f"Stop loss hit on {bracket_analysis['sl_hit_pct']:.0f}% of trades — consider widening ATR multiplier",
            "category": "bracket",
        })

    # Sort: high > medium > low
    priority_order = {"high": 1, "medium": 2, "low": 3}
    recs.sort(key=lambda r: priority_order.get(r.get("priority", "low"), 3))

    return recs


# ── Layer 4: Auto-tune ───────────────────────────────────────────────────────

def compute_auto_tune(rule_perf: list[dict], score_analysis: dict, rules: list[Rule]) -> dict:
    """Compute what should change."""
    rules_to_disable = []
    sizing_changes = {}
    new_min_score = None
    changes = []
    warnings = []

    rule_map = {r.name: r for r in rules}

    for rp in rule_perf:
        if rp["verdict"] == "disable" and rp["total_trades"] >= MIN_TRADES * 2:
            rule = rule_map.get(rp["rule_name"])
            if rule and rule.enabled:
                rules_to_disable.append(rule.id)
                changes.append(f"Disable '{rp['rule_name']}' — {rp['win_rate']}% win rate")
        elif rp["verdict"] == "boost":
            sizing_changes[rp["rule_id"]] = 1.5
            changes.append(f"Boost '{rp['rule_name']}' sizing by 1.5x")
        elif rp["verdict"] == "reduce":
            sizing_changes[rp["rule_id"]] = 0.5
            changes.append(f"Reduce '{rp['rule_name']}' sizing by 0.5x")

        if rp["total_trades"] < MIN_TRADES:
            warnings.append(f"'{rp['rule_name']}' has only {rp['total_trades']} trades — skip auto-tune")

    if score_analysis.get("available"):
        opt = score_analysis["optimal_min_score"]
        if abs(opt - 50) >= 5:
            new_min_score = max(40, min(80, opt))
            changes.append(f"Adjust min score: 50 → {new_min_score}")

    return {
        "rules_to_disable": rules_to_disable,
        "sizing_changes": sizing_changes,
        "new_min_score": new_min_score,
        "changes": changes,
        "warnings": warnings,
        "applied": False,
    }


async def apply_auto_tune(tune_result: dict) -> dict:
    """Apply auto-tune changes to DB."""
    rules = await get_rules()
    rule_map = {r.id: r for r in rules}

    for rule_id in tune_result.get("rules_to_disable", []):
        rule = rule_map.get(rule_id)
        if rule and rule.enabled:
            rule.enabled = False
            await save_rule(rule)
            log.info("Auto-tune DISABLED rule: %s", rule.name)

    tune_result["applied"] = True
    return tune_result


# ── Layer 5: AI Narrative ────────────────────────────────────────────────────

async def generate_daily_report(data: dict, recommendations: list[dict], use_ai: bool = True) -> str:
    """Generate natural language daily report. Uses Claude API if available."""
    api_key = getattr(cfg, "ANTHROPIC_API_KEY", "")

    if use_ai and api_key:
        try:
            import anthropic
            client = anthropic.AsyncAnthropic(api_key=api_key)
            prompt = f"""Trading bot performance data (last {data.get('lookback_days', 90)} days):
{_format_metrics_for_ai(data, recommendations)}

Write a 3-paragraph daily briefing: (1) overall performance, (2) what's working vs not, (3) top 3 actions for tomorrow. Be specific with numbers. Plain prose."""

            msg = await client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=800,
                temperature=0,
                system="You are a quantitative trading analyst. Write concise, actionable briefings.",
                messages=[{"role": "user", "content": prompt}],
            )
            return msg.content[0].text
        except Exception as e:
            log.warning("Claude API unavailable (%s) — using template", e)

    return _template_report(data, recommendations)


def _format_metrics_for_ai(data: dict, recommendations: list[dict]) -> str:
    """Format metrics as text for the AI prompt."""
    pnl = data.get("pnl_summary", {})
    lines = [
        f"Total P&L: ${pnl.get('total_pnl', 0):.2f}",
        f"Win rate: {pnl.get('win_rate', 0)}%",
        f"Profit factor: {pnl.get('profit_factor', 0)}",
        f"Total trades: {pnl.get('trade_count', 0)}",
        "",
        "Top recommendations:",
    ]
    for r in recommendations[:5]:
        lines.append(f"  - [{r.get('priority', '?')}] {r.get('message', '')}")
    return "\n".join(lines)


def _template_report(data: dict, recommendations: list[dict]) -> str:
    """Fallback template-based report."""
    pnl = data.get("pnl_summary", {})
    total_pnl = pnl.get("total_pnl", 0)
    win_rate = pnl.get("win_rate", 0)
    trade_count = pnl.get("trade_count", 0)
    pf = pnl.get("profit_factor", 0)
    days = data.get("lookback_days", 90)

    report = f"Over the last {days} days, the bot executed {trade_count} trades "
    report += f"with a {win_rate}% win rate and ${total_pnl:+,.2f} P&L "
    report += f"(profit factor {pf:.2f}).\n\n"

    if recommendations:
        high = [r for r in recommendations if r.get("priority") == "high"]
        if high:
            report += "URGENT: " + "; ".join(r["message"] for r in high[:2]) + "\n\n"
        medium = [r for r in recommendations if r.get("priority") == "medium"]
        if medium:
            report += "Suggestions: " + "; ".join(r["message"] for r in medium[:3]) + "\n"
    else:
        report += "No actionable recommendations at this time. Continue monitoring."

    return report


# ── Layer 6: Orchestrator ────────────────────────────────────────────────────

async def build_full_report(lookback_days: int = 90, apply_tune: bool = False) -> dict:
    """Run all analysis layers and return complete report."""
    data = await fetch_advisor_data(lookback_days)
    matched = data["matched_trades"]
    rules = data["rules"]

    rule_perf = analyze_rule_performance(matched, rules)
    sector_perf = analyze_sector_performance(matched)
    time_patterns = analyze_time_patterns(matched)
    score_analysis = analyze_score_effectiveness(matched)
    bracket_analysis = analyze_bracket_effectiveness(matched)
    recommendations = generate_recommendations(rule_perf, sector_perf, score_analysis, bracket_analysis)
    auto_tune = compute_auto_tune(rule_perf, score_analysis, rules)
    performance = compute_performance_metrics(matched)

    if apply_tune:
        auto_tune = await apply_auto_tune(auto_tune)

    report_text = await generate_daily_report(data, recommendations)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "lookback_days": lookback_days,
        "pnl_summary": data["pnl_summary"],
        "performance": performance,
        "rule_performance": rule_perf,
        "sector_performance": sector_perf,
        "time_patterns": time_patterns,
        "score_analysis": score_analysis,
        "bracket_analysis": bracket_analysis,
        "recommendations": recommendations,
        "auto_tune_preview": auto_tune,
        "report": report_text,
        "trade_count": len(matched),
        "data_warning": f"Only {len(matched)} trades — recommendations have high uncertainty" if len(matched) < 20 else None,
    }

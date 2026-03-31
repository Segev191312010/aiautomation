"""Optimizer prompt templates and formatters — shared by ai_optimizer and candidate_registry.

Extracted from ai_optimizer to eliminate private cross-module imports.
This module owns prompt text and context formatting. No business logic.
"""
from __future__ import annotations

import json


OPTIMIZER_SYSTEM_PROMPT = (
    "You are an autonomous AI trading strategist. You optimize parameters, "
    "create new rules, pause underperforming rules, and retire broken ones. "
    "Return ONLY valid JSON. No markdown fences, no explanations outside JSON. "
    "CONSTRAINTS: No shorting (BUY only). 1% risk per trade. Intraday + swing styles."
)

OPTIMIZER_USER_TEMPLATE = """Trading bot performance data (last {lookback_days} days, {trade_count} trades):

Current regime: {current_regime}

P&L Summary: {pnl_summary}

Rule Performance (top rules by trade count):
{rule_perf_text}

Sector Performance:
{sector_perf_text}

Time-of-Day Patterns:
{time_pattern_text}

Score Analysis: {score_analysis}

Bracket Analysis: {bracket_analysis}

Current AI Parameters: {current_params}

Live Opportunity Snapshot (ranked long setups from the screener):
{market_snapshot_text}

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
        "conditions": [{{"indicator": "RSI", "params": {{"length": 14}}, "operator": "LT", "value": 30}}],
        "logic": "AND",
        "action_type": "BUY",
        "cooldown_minutes": 120,
        "thesis": "Why this rule",
        "hold_style": "swing",
        "status": "paper"
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
  "direct_trades": [
    {{
      "symbol": "NVDA",
      "action": "BUY",
      "order_type": "MKT",
      "stop_price": 108.5,
      "invalidation": "Close back below 20-day trend support",
      "reason": "High-score breakout with strong relative volume",
      "confidence": 0.72
    }}
  ],
  "reasoning": "2-3 sentence strategy summary",
  "confidence": 0.75
}}

Rules:
- New rules start as 'paper' (not live) - BUY only, no shorts
- Only pause/retire rules with clear evidence of poor performance
- Max 3 new rules per cycle
- Include a thesis for new rules explaining the edge
- Direct trades should prefer names from the live opportunity snapshot when score >= 70
- Every direct trade must have a realistic stop_price below current price and a concrete invalidation
- If the opportunity board is weak or mixed, prefer empty direct_trades
- If everything looks fine, return empty rule_actions and empty direct_trades
- min_score: adjust if score analysis shows a better threshold
- risk_multiplier: 1.0 = no change, <1.0 = reduce risk, >1.0 = increase risk"""


def format_market_snapshot(snapshot: dict) -> str:
    if not snapshot or not snapshot.get("available"):
        return "  No live opportunity snapshot available."

    lines = []
    for candidate in snapshot.get("candidates", [])[:8]:
        notes = ", ".join(candidate.get("notes", [])) or "no extra notes"
        lines.append(
            f"  - {candidate.get('symbol', '?')}: score {candidate.get('screener_score', 0)}, "
            f"{candidate.get('setup', 'mixed')}, price ${float(candidate.get('price', 0) or 0):.2f}, "
            f"chg {float(candidate.get('change_pct', 0) or 0):+.2f}%, "
            f"RVOL {candidate.get('relative_volume', 0)}x, "
            f"mom20 {float(candidate.get('momentum_20d', 0) or 0):+.2f}%, "
            f"sector {candidate.get('sector', 'Unknown')}, notes: {notes}"
        )
    lines.append(f"  Setup counts: {json.dumps(snapshot.get('setup_counts', {}))}")
    lines.append(f"  Leading sectors: {json.dumps(snapshot.get('sector_counts', {}))}")
    return "\n".join(lines)


def format_sector_performance(rows: list[dict]) -> str:
    if not rows:
        return "  No sector-performance history available."
    return "\n".join(
        f"  - {row['sector']}: {row['trade_count']} trades, {row['win_rate']}% WR, "
        f"${row['total_pnl']:.0f} P&L, verdict={row['verdict']}"
        for row in rows[:8]
    )


def format_time_patterns(rows: list[dict]) -> str:
    if not rows:
        return "  No reliable time-pattern data available."
    return "\n".join(
        f"  - Hour {row['hour']:02d}: {row['trade_count']} trades, "
        f"{row['win_rate']}% WR, avg pnl {row['avg_pnl']}"
        for row in rows[:6]
    )


def format_rule_performance(rule_perf: list[dict], limit: int = 15) -> str:
    if not rule_perf:
        return "  No trade data available."
    return "\n".join(
        f"  - {r.get('rule_name', '?')}: {r.get('total_trades', 0)} trades, "
        f"{r.get('win_rate', 0)}% WR, PF {r.get('profit_factor', 0)}, "
        f"${r.get('total_pnl', 0):.0f} P&L, verdict={r.get('verdict', '?')}"
        for r in rule_perf[:limit]
    )

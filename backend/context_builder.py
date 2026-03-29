"""Context builders for Autopilot AI workflows."""
from __future__ import annotations

from config import cfg
from database import get_rules, get_trades


async def _load_current_regime() -> str | None:
    from database import get_db

    try:
        async with get_db() as db:
            cur = await db.execute(
                "SELECT regime FROM regime_snapshots ORDER BY timestamp DESC LIMIT 1"
            )
            row = await cur.fetchone()
            if row:
                return row[0]
    except Exception:
        return None
    return None


async def build_optimizer_context(lookback_days: int | None = None) -> dict:
    """Collect richer optimizer context: performance, regime, and live opportunity board."""
    from ai_advisor import (
        fetch_advisor_data,
        analyze_rule_performance,
        analyze_score_effectiveness,
        analyze_sector_performance,
        analyze_time_patterns,
        analyze_bracket_effectiveness,
    )
    from ai_params import ai_params
    from screener import build_market_opportunity_snapshot

    lookback = lookback_days or cfg.ADVISOR_LOOKBACK_DAYS
    advisor_data = await fetch_advisor_data(lookback_days=lookback)
    matched = advisor_data.get("matched_trades", [])
    rules = advisor_data.get("rules", [])

    market_snapshot: dict = {}
    try:
        market_snapshot = await build_market_opportunity_snapshot(limit=12)
    except Exception:
        market_snapshot = {"available": False, "reason": "market_snapshot_unavailable", "candidates": []}

    current_params = {
        "min_score": ai_params.get_min_score(),
        "risk_multiplier": ai_params.get_risk_multiplier(),
        "signal_weights": ai_params._signal_weights,
        "exit_params": ai_params._exit_params,
        "sizing_multipliers": ai_params._rule_sizing_multipliers,
    }

    return {
        "lookback_days": lookback,
        "trade_count": len(matched),
        "pnl_summary": advisor_data.get("pnl_summary", {}),
        "rule_performance": analyze_rule_performance(matched, rules)[:20],
        "score_analysis": analyze_score_effectiveness(matched),
        "sector_performance": analyze_sector_performance(matched)[:10],
        "time_patterns": analyze_time_patterns(matched)[:8],
        "bracket_analysis": analyze_bracket_effectiveness(matched),
        "current_params": current_params,
        "current_regime": await _load_current_regime(),
        "market_snapshot": market_snapshot,
    }


async def build_rule_lab_context(lookback_days: int | None = None) -> dict:
    """Collect lightweight context for AI-managed rule decisions."""
    from ai_advisor import fetch_advisor_data, analyze_rule_performance
    from screener import build_market_opportunity_snapshot

    lookback = lookback_days or cfg.ADVISOR_LOOKBACK_DAYS
    advisor_data = await fetch_advisor_data(lookback_days=lookback)
    rules = await get_rules()
    trades = await get_trades(limit=500)
    matched = advisor_data.get("matched_trades", [])
    rule_perf = analyze_rule_performance(matched, rules)
    try:
        market_snapshot = await build_market_opportunity_snapshot(limit=8)
    except Exception:
        market_snapshot = {"available": False, "reason": "market_snapshot_unavailable", "candidates": []}

    return {
        "lookback_days": lookback,
        "current_regime": await _load_current_regime(),
        "rules": [rule.model_dump() for rule in rules],
        "recent_trades": [trade.model_dump() for trade in trades[:100]],
        "rule_performance": rule_perf[:25],
        "pnl_summary": advisor_data.get("pnl_summary", {}),
        "performance": advisor_data.get("performance", {}),
        "market_snapshot": market_snapshot,
    }

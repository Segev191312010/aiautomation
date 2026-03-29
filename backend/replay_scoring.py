"""Replay Scoring Policy — centralized score-state logic by item type.

Key rule: generated candidate items are NEVER assigned invented outcomes.
Only items matchable by stable keys inherit scores from historical data.
"""
from __future__ import annotations


def resolve_score_status(item: dict) -> str:
    """Determine the score status of an item based on its current state."""
    if item.get("realized_pnl") is not None and item.get("realized_trade_id"):
        return "direct_realized"
    if item.get("score_source") == "rule_backtest":
        return "replay_scored"
    if item.get("score_source") == "proxy":
        return "proxy_scored"
    return "unscored"


def score_direct_trade_item(item: dict, realized_trade: dict | None) -> dict:
    """Score a direct_trade item from its linked canonical trade outcome."""
    if not realized_trade or realized_trade.get("realized_pnl") is None:
        return {"score_status": "unscored", "realized_pnl": None}
    return {
        "score_status": "direct_realized",
        "realized_pnl": realized_trade["realized_pnl"],
        "realized_trade_id": realized_trade.get("id"),
        "realized_at": realized_trade.get("closed_at"),
    }


def score_rule_action_item(item: dict, backtest_result: dict | None) -> dict:
    """Score a rule_action item from a deterministic backtest replay."""
    if not backtest_result:
        return {"score_status": "unscored", "realized_pnl": None}
    if backtest_result.get("not_replayable"):
        return {"score_status": "unscored", "realized_pnl": None, "notes": backtest_result.get("reason")}
    agg_return = backtest_result.get("aggregate_return_pct", 0)
    return {
        "score_status": "replay_scored",
        "score_source": "rule_backtest",
        "realized_pnl": agg_return,  # Using return % as proxy P&L
    }


def score_candidate_item_against_historical(
    candidate_item: dict,
    baseline_items: list[dict],
) -> dict:
    """Match a generated candidate item against historical realized items.

    Only matches by stable keys: (item_type, action_name, symbol, target_key).
    Unmatched items stay unscored — we NEVER invent outcomes.
    """
    match_keys = ("item_type", "action_name", "symbol", "target_key")

    for baseline in baseline_items:
        if all(candidate_item.get(k) == baseline.get(k) for k in match_keys if candidate_item.get(k)):
            if baseline.get("realized_pnl") is not None:
                return {
                    "score_status": baseline.get("score_status", "proxy_scored"),
                    "realized_pnl": baseline["realized_pnl"],
                    "score_source": "historical_match",
                    "matched_item_id": baseline.get("id"),
                }

    return {"score_status": "unscored", "realized_pnl": None}

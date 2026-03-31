"""Decision Item Factory — normalize AI decision payloads into ledger items.

Extracted from ai_optimizer._build_ledger_items to eliminate private
cross-module imports (ai_replay → ai_optimizer).

This module owns item normalization from AI payloads. No business logic.
"""
from __future__ import annotations


def build_ledger_items(decisions: dict) -> list[dict]:
    """Build decision_item dicts from an AI decision payload for the ledger."""
    items: list[dict] = []
    confidence = decisions.get("confidence", 0.5)

    # min_score
    min_score_rec = decisions.get("min_score")
    if min_score_rec and isinstance(min_score_rec, dict):
        items.append({
            "item_type": "score_threshold", "action_name": "adjust",
            "target_key": "min_score", "proposed": min_score_rec,
            "confidence": confidence,
        })

    # risk_multiplier
    risk_rec = decisions.get("risk_multiplier")
    if risk_rec and isinstance(risk_rec, dict):
        items.append({
            "item_type": "risk_adjust", "action_name": "adjust",
            "target_key": "risk_multiplier", "proposed": risk_rec,
            "confidence": confidence,
        })

    # rule_changes
    for rc in decisions.get("rule_changes", []):
        items.append({
            "item_type": "rule_change",
            "action_name": rc.get("action"),
            "target_key": rc.get("rule_id"),
            "proposed": rc,
            "confidence": confidence,
        })

    # rule_actions
    for ra in decisions.get("rule_actions", []):
        items.append({
            "item_type": "rule_action",
            "action_name": ra.get("action"),
            "target_key": ra.get("rule_id"),
            "symbol": (ra.get("rule_payload") or {}).get("symbol"),
            "proposed": ra,
            "confidence": ra.get("confidence", confidence),
        })

    # direct_trades
    for dt in decisions.get("direct_trades", []):
        items.append({
            "item_type": "direct_trade",
            "action_name": dt.get("action"),
            "symbol": dt.get("symbol"),
            "proposed": dt,
            "confidence": dt.get("confidence", confidence),
        })

    # abstain
    if decisions.get("abstained") and not items:
        items.append({
            "item_type": "abstain", "action_name": "abstain",
            "proposed": {"reason": decisions.get("reasoning", "No action")},
            "confidence": confidence,
        })

    return items

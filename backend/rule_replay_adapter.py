"""Rule Replay Adapter — fail-closed replayability guard for deterministic rule backtests.

Only rules with explicit replay metadata (replay_config) can be backtested.
No implicit exit guessing.
"""
from __future__ import annotations

import logging

from models import Rule
from config import cfg

log = logging.getLogger(__name__)


def is_rule_replayable(rule: Rule) -> tuple[bool, str]:
    """Check if a rule has enough metadata for deterministic replay.

    Returns (ok, reason). If not ok, reason explains why.
    """
    if not rule.conditions:
        return False, "Rule has no entry conditions"

    if not rule.action or not rule.action.type:
        return False, "Rule has no trade action"

    # Must have explicit replay_config with exit params
    if rule.replay_config and isinstance(rule.replay_config, dict):
        has_sl = "stop_loss_pct" in rule.replay_config
        has_tp = "take_profit_pct" in rule.replay_config
        if has_sl or has_tp:
            return True, "replay_config provides exit parameters"

    return False, "No explicit replay metadata (replay_config with stop_loss_pct/take_profit_pct required)"


def build_backtest_request_from_rule(rule: Rule) -> dict:
    """Convert a replayable rule into a deterministic backtest request.

    Only call this after is_rule_replayable() returns True.
    """
    rc = rule.replay_config or {}

    return {
        "entry_conditions": [c.model_dump() if hasattr(c, "model_dump") else c for c in rule.conditions],
        "exit_conditions": rc.get("exit_conditions", []),
        "symbol": rule.symbol or "SPY",
        "condition_logic": rule.logic,
        "stop_loss_pct": rc.get("stop_loss_pct", 0.0),
        "take_profit_pct": rc.get("take_profit_pct", 0.0),
        "position_size_pct": rc.get("position_size_pct", 10.0),
        "initial_capital": rc.get("initial_capital", 100_000.0),
        "period": rc.get("period", "2y"),
        "interval": rc.get("interval", "1d"),
    }


def build_default_replay_config() -> dict:
    """Build replay_config from current ATR config defaults.

    Used when AI creates a rule — stamps deterministic exit params for future replay.
    """
    stop_mult = getattr(cfg, "ATR_STOP_MULT", 3.0)
    trail_mult = getattr(cfg, "ATR_TRAIL_MULT", 2.0)
    reward_ratio = getattr(cfg, "REWARD_RATIO", 2.2)

    return {
        "stop_loss_pct": round(stop_mult * 1.0, 2),  # approximate as percentage
        "take_profit_pct": round(stop_mult * reward_ratio, 2),
        "exit_conditions": [],
        "position_size_pct": 10.0,
        "period": "2y",
        "interval": "1d",
        "source": "atr_config_snapshot",
        "atr_stop_mult": stop_mult,
        "atr_trail_mult": trail_mult,
    }

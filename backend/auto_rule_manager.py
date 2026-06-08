"""Hands-free automation rule bootstrap.

This module gives autopilot something executable to work with when the
operator has turned on PAPER/LIVE automation but has not created or enabled
any rules yet. It intentionally seeds small, long-only rules with quantity 1.
"""
from __future__ import annotations

import logging
import time
from typing import Sequence

from config import cfg
from database import get_rules, persist_rule_revision
from models import Rule

log = logging.getLogger(__name__)

AUTO_RULE_PREFIX = "auto:"

# Monotonic timestamp of the last bootstrap attempt that passed the eligibility
# gate. Used to enforce a quiet period so the bootstrap does not re-fire (and
# re-activate operator-disabled auto rules) on every bot cycle.
_last_bootstrap_at: float | None = None


def _reset_bootstrap_state() -> None:
    """Test/operational hook: clear the bootstrap cooldown timestamp."""
    global _last_bootstrap_at
    _last_bootstrap_at = None


def automation_rule_activation_allowed() -> bool:
    """Return True when AI/auto rules may be executable in the current account."""
    if cfg.AUTOPILOT_MODE == "PAPER":
        return cfg.SIM_MODE or cfg.IS_PAPER
    if cfg.AUTOPILOT_MODE == "LIVE":
        return not cfg.SIM_MODE and not cfg.IS_PAPER
    return False


def _auto_rule_quantity() -> int:
    return max(1, int(getattr(cfg, "FULLY_AUTO_RULE_QUANTITY", 1) or 1))


def _auto_rule_universe() -> str:
    return str(getattr(cfg, "FULLY_AUTO_RULE_UNIVERSE", "etfs") or "etfs").strip().lower()


def build_default_auto_rules() -> list[Rule]:
    """Build deterministic starter automation rules for the configured universe."""
    qty = _auto_rule_quantity()
    universe = _auto_rule_universe()
    common = {
        "symbol": "",
        "universe": universe,
        "enabled": True,
        "logic": "AND",
        "action": {"type": "BUY", "asset_type": "STK", "quantity": qty, "order_type": "MKT"},
        "cooldown_minutes": 1440,
        "status": "active",
        "ai_generated": True,
        "created_by": "auto",
        "hold_style": "swing",
    }
    return [
        Rule(
            id=f"{AUTO_RULE_PREFIX}etf-momentum-continuation:v1",
            name="Auto: ETF Momentum Continuation",
            conditions=[
                {"indicator": "PRICE", "params": {}, "operator": ">", "value": "SMA_20"},
                {"indicator": "RSI", "params": {"length": 14}, "operator": ">", "value": 55},
                {"indicator": "MACD", "params": {"fast": 12, "slow": 26, "signal": 9}, "operator": ">", "value": "MACD_SIGNAL"},
            ],
            thesis="Follow broad ETF momentum only when trend, RSI, and MACD agree.",
            ai_reason="Fully-auto bootstrap created a conservative momentum rule because no enabled rules existed.",
            **common,
        ),
        Rule(
            id=f"{AUTO_RULE_PREFIX}etf-trend-pullback:v1",
            name="Auto: ETF Trend Pullback",
            conditions=[
                {"indicator": "PRICE", "params": {}, "operator": ">", "value": "SMA_200"},
                {"indicator": "RSI", "params": {"length": 14}, "operator": "<", "value": 40},
            ],
            thesis="Buy liquid ETFs on pullbacks while the long-term trend remains intact.",
            ai_reason="Fully-auto bootstrap created a trend-filtered pullback rule because no enabled rules existed.",
            **common,
        ),
        Rule(
            id=f"{AUTO_RULE_PREFIX}etf-trend-stack:v1",
            name="Auto: ETF Trend Stack",
            conditions=[
                {"indicator": "PRICE", "params": {}, "operator": ">", "value": "SMA_50"},
                {"indicator": "SMA", "params": {"length": 20}, "operator": ">", "value": "SMA_50"},
            ],
            thesis="Participate when price and short-term trend are both above intermediate trend.",
            ai_reason="Fully-auto bootstrap created a trend-stack rule because no enabled rules existed.",
            **common,
        ),
    ]


async def ensure_auto_rules(existing_rules: Sequence[Rule] | None = None) -> dict:
    """Create or reactivate default auto rules when autopilot has no enabled rules."""
    if not getattr(cfg, "FULLY_AUTO_RULES_ENABLED", True):
        return {"created": [], "activated": [], "skipped": "disabled"}

    if not automation_rule_activation_allowed():
        return {"created": [], "activated": [], "skipped": f"mode:{cfg.AUTOPILOT_MODE}"}

    rules = list(existing_rules) if existing_rules is not None else await get_rules()
    if any(rule.enabled and rule.status == "active" for rule in rules):
        return {"created": [], "activated": [], "skipped": "enabled_rules_present"}

    # Quiet period: once a bootstrap attempt has run, suppress further attempts
    # for the cooldown window so disabling all rules is not silently undone every
    # bot cycle. Placed AFTER the eligibility checks so OFF/disabled still report
    # their specific skip reason.
    global _last_bootstrap_at
    cooldown = max(0, int(getattr(cfg, "FULLY_AUTO_RULE_BOOTSTRAP_COOLDOWN_SECONDS", 3600) or 0))
    now = time.monotonic()
    if cooldown and _last_bootstrap_at is not None and (now - _last_bootstrap_at) < cooldown:
        return {"created": [], "activated": [], "skipped": "cooldown"}

    try:
        defaults = build_default_auto_rules()
    except Exception as exc:
        log.error("Cannot build fully-auto rules: %s", exc)
        # Do NOT arm the cooldown on a build failure, so a corrected config takes
        # effect on the next cycle instead of waiting out the quiet window.
        return {"created": [], "activated": [], "skipped": f"invalid_auto_rule_config:{exc}"}

    # Arm the quiet period only after a successful build so a transient config
    # error cannot silently suppress recovery for the whole cooldown window.
    _last_bootstrap_at = now

    by_id = {rule.id: rule for rule in rules}
    created: list[str] = []
    activated: list[str] = []

    for rule in defaults:
        existing = by_id.get(rule.id)
        if existing is None:
            await persist_rule_revision(rule, diff_summary=rule.ai_reason, author="auto")
            created.append(rule.id)
            continue

        if existing.created_by != "auto" or existing.status == "retired":
            continue

        if not existing.enabled or existing.status != "active":
            existing.enabled = True
            existing.status = "active"
            existing.ai_generated = True
            existing.ai_reason = "Fully-auto bootstrap reactivated this rule because no enabled rules existed."
            await persist_rule_revision(existing, diff_summary=existing.ai_reason, author="auto")
            activated.append(existing.id)

    if created or activated:
        log.info(
            "Fully-auto rules ready: created=%d activated=%d universe=%s qty=%d",
            len(created),
            len(activated),
            _auto_rule_universe(),
            _auto_rule_quantity(),
        )

    return {"created": created, "activated": activated, "skipped": None}

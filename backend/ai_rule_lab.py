"""AI Rule Lab — apply AI-authored rule lifecycle actions to the live Rule model."""
from __future__ import annotations

import logging
from typing import Iterable

from api_contracts import AIRuleAction
from ai_guardrails import log_ai_action
from database import delete_rule, get_rule, get_rules, save_rule, save_rule_version
from models import Rule, RuleCreate

log = logging.getLogger(__name__)


def _as_action(value: AIRuleAction | dict) -> AIRuleAction:
    return value if isinstance(value, AIRuleAction) else AIRuleAction(**value)


async def apply_rule_actions(
    actions: Iterable[AIRuleAction | dict],
    *,
    author: str = "ai",
    allow_active: bool = False,
) -> list[dict]:
    """Apply AI-authored actions to the current rule model and snapshot versions."""
    results: list[dict] = []
    for raw_action in actions:
        action = _as_action(raw_action)
        try:
            result = await _apply_rule_action(action, author=author, allow_active=allow_active)
            results.append({"ok": True, **result})
        except Exception as exc:
            log.warning("Rule lab action failed: %s", exc)
            results.append({
                "ok": False,
                "action": action.action,
                "rule_id": action.rule_id,
                "reason": str(exc),
            })
    return results


async def _apply_rule_action(action: AIRuleAction, *, author: str, allow_active: bool) -> dict:
    if action.action == "create":
        if not action.rule_payload:
            raise ValueError("create requires rule_payload")
        create_payload = RuleCreate(**action.rule_payload)
        rule = Rule(**create_payload.model_dump())
        rule.ai_generated = True
        rule.created_by = author
        rule.ai_reason = action.reason
        if not allow_active and rule.status == "active":
            rule.status = "paper"
            rule.enabled = False
        if rule.status != "active":
            rule.enabled = False
        await save_rule(rule)
        await save_rule_version(rule, diff_summary=action.reason, author=author)
        await log_ai_action(
            action_type="rule_create",
            category="rule_lab",
            description=f"Created AI rule '{rule.name}'",
            old_value=None,
            new_value=rule.model_dump(),
            reason=action.reason,
            confidence=action.confidence,
            status="applied",
        )
        return {"action": "create", "rule_id": rule.id, "status": rule.status}

    if not action.rule_id:
        raise ValueError(f"{action.action} requires rule_id")

    existing = await get_rule(action.rule_id)
    if not existing:
        raise ValueError(f"Rule '{action.rule_id}' not found")

    original = existing.model_dump()
    updated = existing.model_copy(deep=True)
    updated.version = max(1, existing.version) + 1
    updated.created_by = author if existing.created_by == "human" else existing.created_by
    updated.ai_generated = True
    updated.ai_reason = action.reason

    if action.action == "update":
        if not action.rule_payload:
            raise ValueError("update requires rule_payload")
        patch = action.rule_payload.copy()
        if not allow_active and patch.get("status") == "active":
            patch["status"] = "paper"
        updated = updated.model_copy(update=patch)
        updated.version = max(1, existing.version) + 1
        if updated.status != "active":
            updated.enabled = False
        await save_rule(updated)
        await save_rule_version(updated, diff_summary=action.reason, author=author)
        action_type = "rule_update"
    elif action.action == "enable":
        updated.status = "active"
        updated.enabled = True
        await save_rule(updated)
        await save_rule_version(updated, diff_summary=action.reason, author=author)
        action_type = "rule_enable"
    elif action.action == "disable":
        updated.enabled = False
        await save_rule(updated)
        await save_rule_version(updated, diff_summary=action.reason, author=author)
        action_type = "rule_disable"
    elif action.action == "pause":
        updated.status = "paused"
        updated.enabled = False
        await save_rule(updated)
        await save_rule_version(updated, diff_summary=action.reason, author=author)
        action_type = "rule_pause"
    elif action.action == "retire":
        updated.status = "retired"
        updated.enabled = False
        await save_rule(updated)
        await save_rule_version(updated, diff_summary=action.reason, author=author)
        action_type = "rule_retire"
    elif action.action == "delete":
        await save_rule_version(updated, diff_summary=f"Deleted: {action.reason}", author=author)
        await delete_rule(updated.id)
        action_type = "rule_delete"
    else:
        raise ValueError(f"Unsupported rule action '{action.action}'")

    await log_ai_action(
        action_type=action_type,
        category="rule_lab",
        description=f"AI {action.action}d rule '{existing.name}'",
        old_value=original,
        new_value=None if action.action == "delete" else updated.model_dump(),
        reason=action.reason,
        confidence=action.confidence,
        status="applied",
    )
    return {"action": action.action, "rule_id": existing.id, "status": getattr(updated, "status", existing.status)}


async def list_ai_rules() -> list[Rule]:
    """Return rules ordered with AI-generated rules first."""
    rules = await get_rules()
    return sorted(rules, key=lambda rule: (not rule.ai_generated, rule.name.lower()))

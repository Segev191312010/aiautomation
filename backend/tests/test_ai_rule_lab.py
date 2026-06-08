"""Regression coverage for the AI Rule Lab using the existing Rule model."""
import pytest

from ai_rule_lab import apply_rule_actions
from database import get_rule, get_rule_versions, get_rules, init_db
from models import Rule


@pytest.mark.anyio
async def test_rule_lab_create_is_gated_to_paper(anyio_backend):
    await init_db()

    results = await apply_rule_actions([
        {
            "action": "create",
            "rule_payload": {
                "name": "AI Momentum Breakout",
                "symbol": "NVDA",
                "conditions": [],
                "logic": "AND",
                "action": {"type": "BUY", "asset_type": "STK", "quantity": 5, "order_type": "MKT"},
                "cooldown_minutes": 60,
                "status": "active",
                "thesis": "Follow strong trend continuation",
                "hold_style": "swing",
            },
            "reason": "Detected breakout regime",
            "confidence": 0.78,
        }
    ], author="ai", allow_active=False)

    assert results[0]["ok"] is True
    rules = await get_rules()
    created = next(rule for rule in rules if rule.name == "AI Momentum Breakout")
    assert created.ai_generated is True
    assert created.status == "paper"
    assert created.enabled is False


@pytest.mark.anyio
async def test_rule_lab_create_can_be_active_when_execution_is_allowed(anyio_backend):
    await init_db()

    results = await apply_rule_actions([
        {
            "action": "create",
            "rule_payload": {
                "name": "AI Executable Momentum",
                "symbol": "MSFT",
                "conditions": [
                    {"indicator": "rsi", "params": {"period": 14}, "operator": ">", "value": 55}
                ],
                "logic": "AND",
                "action_type": "BUY",
                "cooldown_minutes": 60,
                "status": "paper",
            },
            "reason": "Safe paper automation is enabled",
            "confidence": 0.78,
        }
    ], author="ai", allow_active=True)

    assert results[0]["ok"] is True
    rules = await get_rules()
    created = next(rule for rule in rules if rule.name == "AI Executable Momentum")
    assert created.status == "active"
    assert created.enabled is True
    assert created.conditions[0].indicator == "RSI"
    assert created.conditions[0].params["length"] == 14
    assert "period" not in created.conditions[0].params


@pytest.mark.anyio
async def test_rule_lab_pause_updates_existing_rule_and_versions(anyio_backend):
    await init_db()
    rule = Rule(
        name="Legacy Trend Rule",
        symbol="AAPL",
        enabled=True,
        conditions=[],
        logic="AND",
        action={"type": "BUY", "asset_type": "STK", "quantity": 3, "order_type": "MKT"},
        cooldown_minutes=15,
    )

    from database import save_rule

    await save_rule(rule)

    results = await apply_rule_actions([
        {
            "action": "pause",
            "rule_id": rule.id,
            "reason": "Weak recent expectancy",
            "confidence": 0.66,
        }
    ], author="ai", allow_active=False)

    assert results[0]["ok"] is True
    updated = await get_rule(rule.id)
    assert updated is not None
    assert updated.status == "paused"
    assert updated.enabled is False

    versions = await get_rule_versions(rule.id)
    assert versions
    assert versions[0]["status"] == "paused"

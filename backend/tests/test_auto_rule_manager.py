"""Regression coverage for hands-free automation rule bootstrapping."""
from __future__ import annotations

import pytest

import config
from database import get_rules, init_db


@pytest.fixture(autouse=True)
def restore_auto_flags():
    cfg = config.cfg
    import auto_rule_manager

    auto_rule_manager._reset_bootstrap_state()
    previous = {
        "AUTOPILOT_MODE": cfg.AUTOPILOT_MODE,
        "SIM_MODE": cfg.SIM_MODE,
        "IS_PAPER": cfg.IS_PAPER,
        "FULLY_AUTO_RULES_ENABLED": cfg.FULLY_AUTO_RULES_ENABLED,
        "FULLY_AUTO_RULE_UNIVERSE": cfg.FULLY_AUTO_RULE_UNIVERSE,
        "FULLY_AUTO_RULE_QUANTITY": cfg.FULLY_AUTO_RULE_QUANTITY,
        "FULLY_AUTO_RULE_BOOTSTRAP_COOLDOWN_SECONDS": getattr(
            cfg, "FULLY_AUTO_RULE_BOOTSTRAP_COOLDOWN_SECONDS", 3600
        ),
    }
    try:
        yield
    finally:
        auto_rule_manager._reset_bootstrap_state()
        for key, value in previous.items():
            setattr(cfg, key, value)


@pytest.mark.anyio
async def test_ensure_auto_rules_creates_active_rules_in_safe_paper_mode(tmp_path, monkeypatch, anyio_backend):
    db_path = str(tmp_path / "auto-rules.db")
    monkeypatch.setattr(config.cfg, "DB_PATH", db_path)
    config.cfg.AUTOPILOT_MODE = "PAPER"
    config.cfg.SIM_MODE = True
    config.cfg.IS_PAPER = False
    config.cfg.FULLY_AUTO_RULES_ENABLED = True
    config.cfg.FULLY_AUTO_RULE_UNIVERSE = "etfs"
    config.cfg.FULLY_AUTO_RULE_QUANTITY = 1

    await init_db()

    from auto_rule_manager import ensure_auto_rules

    result = await ensure_auto_rules()
    rules = await get_rules()
    auto_rules = [rule for rule in rules if rule.id.startswith("auto:")]

    assert len(result["created"]) == 3
    assert len(auto_rules) == 3
    assert all(rule.enabled for rule in auto_rules)
    assert all(rule.status == "active" for rule in auto_rules)
    assert all(rule.ai_generated for rule in auto_rules)
    assert all(rule.action.quantity == 1 for rule in auto_rules)


@pytest.mark.anyio
async def test_ensure_auto_rules_skips_when_autopilot_off(tmp_path, monkeypatch, anyio_backend):
    db_path = str(tmp_path / "auto-rules-off.db")
    monkeypatch.setattr(config.cfg, "DB_PATH", db_path)
    config.cfg.AUTOPILOT_MODE = "OFF"
    config.cfg.SIM_MODE = True
    config.cfg.IS_PAPER = True

    await init_db()

    from auto_rule_manager import ensure_auto_rules

    result = await ensure_auto_rules()
    rules = await get_rules()

    assert result["skipped"] == "mode:OFF"
    assert not [rule for rule in rules if rule.id.startswith("auto:")]


@pytest.mark.anyio
async def test_ensure_auto_rules_cooldown_suppresses_refire(tmp_path, monkeypatch, anyio_backend):
    """Once bootstrapped, disabling every auto rule must NOT re-fire the bootstrap
    on the next cycle while inside the cooldown window."""
    db_path = str(tmp_path / "auto-rules-cooldown.db")
    monkeypatch.setattr(config.cfg, "DB_PATH", db_path)
    config.cfg.AUTOPILOT_MODE = "PAPER"
    config.cfg.SIM_MODE = True
    config.cfg.IS_PAPER = False
    config.cfg.FULLY_AUTO_RULES_ENABLED = True
    config.cfg.FULLY_AUTO_RULE_BOOTSTRAP_COOLDOWN_SECONDS = 3600

    await init_db()

    from auto_rule_manager import ensure_auto_rules
    from database import persist_rule_revision

    first = await ensure_auto_rules()
    assert len(first["created"]) == 3

    # Operator disables every auto rule.
    for rule in await get_rules():
        rule.enabled = False
        await persist_rule_revision(rule, diff_summary="operator disabled", author="operator")

    # Within the cooldown window the bootstrap must NOT re-fire / re-activate them.
    second = await ensure_auto_rules()
    assert second["skipped"] == "cooldown"
    assert second["created"] == []
    assert second["activated"] == []

    # After the cooldown elapses, the bootstrap is allowed to run again.
    config.cfg.FULLY_AUTO_RULE_BOOTSTRAP_COOLDOWN_SECONDS = 0
    third = await ensure_auto_rules()
    assert third["skipped"] != "cooldown"
    assert len(third["activated"]) == 3

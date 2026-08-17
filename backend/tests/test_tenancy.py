"""
Batch 5 regression tests — multi-tenant user_id isolation.

Pins the invariant: a user cannot read or mutate another user's rules,
screener presets, or saved backtests via the HTTP API.

Tests use the DB-layer functions directly (the routes are tested at unit
level — they thread `user.id` from Depends(get_current_user) into the
same DB calls).
"""
from __future__ import annotations

import json
import uuid

import pytest

import config
from database import (
    init_db,
    save_rule,
    get_rule,
    get_rules,
    delete_rule,
    save_screener_preset,
    get_screener_presets,
    delete_screener_preset,
    save_backtest,
    get_backtest,
    delete_backtest,
)
from models import Rule, Condition, TradeAction, ScreenerPreset


@pytest.fixture
def _isolated_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "tenancy.db")
    monkeypatch.setattr(config.cfg, "DB_PATH", db_path)
    return db_path


def _make_rule(name: str) -> Rule:
    return Rule(
        name=name,
        symbol="AAPL",
        conditions=[Condition(indicator="RSI", params={"length": 14}, operator="<", value=30)],
        action=TradeAction(type="BUY", quantity=1),
        status="active",
    )


def _make_preset(name: str, user_id: str) -> ScreenerPreset:
    return ScreenerPreset(
        name=name,
        filters=[],
        built_in=False,
        user_id=user_id,
        created_at="2026-05-14T00:00:00+00:00",
    )


@pytest.mark.anyio
async def test_rules_isolated_per_user(_isolated_db, anyio_backend):
    await init_db()
    rule_a = _make_rule("alice rule")
    rule_b = _make_rule("bob rule")
    await save_rule(rule_a, user_id="alice")
    await save_rule(rule_b, user_id="bob")

    assert [r.name for r in await get_rules(user_id="alice")] == ["alice rule"]
    assert [r.name for r in await get_rules(user_id="bob")] == ["bob rule"]

    # Bob cannot read Alice's rule by id
    assert await get_rule(rule_a.id, user_id="bob") is None
    assert (await get_rule(rule_a.id, user_id="alice")).name == "alice rule"

    # Bob cannot delete Alice's rule
    assert await delete_rule(rule_a.id, user_id="bob") is False
    assert await get_rule(rule_a.id, user_id="alice") is not None  # still there


@pytest.mark.anyio
async def test_screener_presets_isolated_per_user(_isolated_db, anyio_backend):
    await init_db()
    preset_a = _make_preset("alice preset", user_id="alice")
    preset_b = _make_preset("bob preset", user_id="bob")
    await save_screener_preset(preset_a)
    await save_screener_preset(preset_b)

    # Alice sees her own preset (and any built-ins which are user-agnostic)
    alice_presets = await get_screener_presets(user_id="alice")
    custom_names = [p.name for p in alice_presets if not p.built_in]
    assert custom_names == ["alice preset"]

    bob_presets = await get_screener_presets(user_id="bob")
    custom_names = [p.name for p in bob_presets if not p.built_in]
    assert custom_names == ["bob preset"]

    # Bob cannot delete Alice's preset
    deleted = await delete_screener_preset(preset_a.id, user_id="bob")
    assert deleted is False
    # Alice still can
    deleted = await delete_screener_preset(preset_a.id, user_id="alice")
    assert deleted is True


@pytest.mark.anyio
async def test_backtests_isolated_per_user(_isolated_db, anyio_backend):
    await init_db()
    bt_a = str(uuid.uuid4())
    bt_b = str(uuid.uuid4())
    await save_backtest(
        backtest_id=bt_a, user_id="alice", name="alice bt",
        strategy_data="{}", result_data="{}",
        created_at="2026-05-14T00:00:00+00:00",
    )
    await save_backtest(
        backtest_id=bt_b, user_id="bob", name="bob bt",
        strategy_data="{}", result_data="{}",
        created_at="2026-05-14T00:00:00+00:00",
    )

    # Bob cannot read Alice's backtest by id (Batch 5 IDOR fix)
    assert await get_backtest(bt_a, user_id="bob") is None
    assert await get_backtest(bt_a, user_id="alice") is not None

    # Bob cannot delete Alice's backtest
    assert await delete_backtest(bt_a, user_id="bob") is False
    # Alice still can
    assert await delete_backtest(bt_a, user_id="alice") is True

    # After Alice deletes, even Alice can't read it
    assert await get_backtest(bt_a, user_id="alice") is None


@pytest.mark.anyio
async def test_backtest_delete_returns_false_for_demo_default_with_real_user(_isolated_db, anyio_backend):
    """Regression for the prior bug: route called delete_backtest(id) without
    user_id, so real users hit the 'demo' default and could never delete
    their own backtests. The route now threads user.id; this test pins that
    a 'demo' caller cannot delete a real user's row, and the real user can.
    """
    await init_db()
    bt = str(uuid.uuid4())
    await save_backtest(
        backtest_id=bt, user_id="real_user", name="t",
        strategy_data="{}", result_data="{}",
        created_at="2026-05-14T00:00:00+00:00",
    )

    # The legacy bug: 'demo' default could not delete a non-demo row.
    assert await delete_backtest(bt) is False  # uses demo default
    # The fix: real_user CAN delete their own row.
    assert await delete_backtest(bt, user_id="real_user") is True

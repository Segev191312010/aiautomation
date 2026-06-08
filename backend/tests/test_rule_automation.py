"""Regression coverage for deterministic custom-rule automation."""
from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, patch

import pytest

import bot_runner
from config import cfg
from models import OpenPosition, Rule, Trade


@pytest.fixture(autouse=True)
def restore_runtime_flags():
    previous = {
        "AUTOPILOT_MODE": cfg.AUTOPILOT_MODE,
        "SIM_MODE": cfg.SIM_MODE,
        "IS_PAPER": cfg.IS_PAPER,
        "ALLOW_LIVE_RULES_WHEN_AUTOPILOT_OFF": getattr(cfg, "ALLOW_LIVE_RULES_WHEN_AUTOPILOT_OFF", False),
    }
    try:
        yield
    finally:
        cfg.AUTOPILOT_MODE = previous["AUTOPILOT_MODE"]
        cfg.SIM_MODE = previous["SIM_MODE"]
        cfg.IS_PAPER = previous["IS_PAPER"]
        cfg.ALLOW_LIVE_RULES_WHEN_AUTOPILOT_OFF = previous["ALLOW_LIVE_RULES_WHEN_AUTOPILOT_OFF"]


def _rule() -> Rule:
    return Rule(
        id="rule-custom-001",
        name="Custom Momentum",
        symbol="AAPL",
        enabled=True,
        conditions=[{"indicator": "PRICE", "params": {}, "operator": ">", "value": 1}],
        logic="AND",
        action={"type": "BUY", "asset_type": "STK", "quantity": 3, "order_type": "MKT"},
        cooldown_minutes=0,
    )


def _position() -> OpenPosition:
    return OpenPosition(
        id="entry-001",
        symbol="AAPL",
        side="BUY",
        quantity=3,
        entry_price=100.0,
        entry_time="2026-05-16T10:00:00+00:00",
        atr_at_entry=2.0,
        hard_stop_price=94.0,
        atr_stop_mult=3.0,
        atr_trail_mult=2.0,
        high_watermark=105.0,
        rule_id="rule-custom-001",
        rule_name="Custom Momentum",
    )


def test_rule_entries_are_not_gated_by_autopilot_off_source_guard():
    src = inspect.getsource(bot_runner._run_cycle)

    assert "skipping new entries" not in src
    assert "triggered = evaluate_all(enabled, bars_by_symbol, universe_cache)" in src
    assert "direct AI candidate queue left untouched" in src
    assert "require_autopilot_authority=False" in src


def test_block_live_entry_in_recovery_matrix():
    """Recovery brake: AUTOPILOT_MODE=OFF blocks REAL-money rule ENTRIES by default,
    exempts exits, and is opt-out-able; paper/sim and PAPER/LIVE never block."""
    from bot_runner import _block_live_entry_in_recovery

    # Live account + OFF + entry + default flag => blocked
    cfg.AUTOPILOT_MODE = "OFF"
    cfg.SIM_MODE = False
    cfg.IS_PAPER = False
    cfg.ALLOW_LIVE_RULES_WHEN_AUTOPILOT_OFF = False
    assert _block_live_entry_in_recovery(is_exit=False) is True

    # Exits always flow even in recovery
    assert _block_live_entry_in_recovery(is_exit=True) is False

    # Explicit opt-in unblocks live entries while OFF
    cfg.ALLOW_LIVE_RULES_WHEN_AUTOPILOT_OFF = True
    assert _block_live_entry_in_recovery(is_exit=False) is False
    cfg.ALLOW_LIVE_RULES_WHEN_AUTOPILOT_OFF = False

    # Paper account is never blocked
    cfg.IS_PAPER = True
    assert _block_live_entry_in_recovery(is_exit=False) is False
    cfg.IS_PAPER = False

    # Sim mode is never blocked
    cfg.SIM_MODE = True
    assert _block_live_entry_in_recovery(is_exit=False) is False
    cfg.SIM_MODE = False

    # PAPER / LIVE autopilot is never blocked by this recovery gate
    cfg.AUTOPILOT_MODE = "LIVE"
    assert _block_live_entry_in_recovery(is_exit=False) is False
    cfg.AUTOPILOT_MODE = "PAPER"
    assert _block_live_entry_in_recovery(is_exit=False) is False


@pytest.mark.anyio
async def test_sim_rule_entry_routes_to_sim_and_registers_position(anyio_backend):
    cfg.SIM_MODE = True
    rule = _rule()

    with patch("simulation.sim_engine.execute_order", new=AsyncMock(return_value=(True, "filled"))) as mock_sim, patch(
        "bot_runner.save_trade",
        new=AsyncMock(),
    ) as mock_save, patch(
        "bot_runner.order_lifecycle.register_entry_position_from_fill",
        new=AsyncMock(return_value=True),
    ) as mock_register:
        trade = await bot_runner._create_paper_trade(rule, 101.25)

    mock_sim.assert_awaited_once_with(
        symbol="AAPL",
        action="BUY",
        qty=3.0,
        price=101.25,
    )
    mock_save.assert_awaited_once()
    mock_register.assert_awaited_once()
    assert trade.mode == "SIM"
    assert trade.source == "rule"
    assert trade.position_id == trade.id


@pytest.mark.anyio
async def test_sim_exit_uses_sim_engine_not_ibkr(anyio_backend):
    cfg.SIM_MODE = True
    pos = _position()
    finalized = Trade(
        id="exit-001",
        rule_id=pos.rule_id,
        rule_name=f"EXIT:{pos.rule_name}",
        symbol=pos.symbol,
        action="SELL",
        asset_type="STK",
        quantity=3,
        order_type="MKT",
        limit_price=None,
        fill_price=110.0,
        status="FILLED",
        order_id=None,
        timestamp="2026-05-16T10:30:00+00:00",
        realized_pnl=30.0,
    )

    with patch("bot_exits.get_open_position", new=AsyncMock(return_value=pos)), patch(
        "simulation.sim_engine.get_positions",
        new=AsyncMock(return_value=[type("P", (), {"symbol": "AAPL", "qty": 3})()]),
    ), patch(
        "simulation.sim_engine.execute_order",
        new=AsyncMock(return_value=(True, "filled")),
    ) as mock_sim, patch(
        "order_executor.place_order",
        new=AsyncMock(),
    ) as mock_place, patch(
        "services.order_lifecycle.stamp_exit_trade_context",
        new=AsyncMock(),
    ) as mock_stamp, patch(
        "services.order_lifecycle.finalize_filled_exit_trade",
        new=AsyncMock(return_value=finalized),
    ) as mock_finalize:
        await bot_runner._place_exit_order(pos, "AAPL", 3, 110.0, "test_exit")

    mock_sim.assert_awaited_once()
    mock_place.assert_not_awaited()
    mock_stamp.assert_awaited_once()
    mock_finalize.assert_awaited_once()

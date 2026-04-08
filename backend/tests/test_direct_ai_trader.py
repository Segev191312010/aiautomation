"""Direct AI trade execution regressions."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch, call

import pytest

from api_contracts import AIDirectTrade
from config import cfg
from direct_ai_trader import execute_direct_trade
from models import OpenPosition, Trade
from safety_kernel import SafetyViolation


@pytest.fixture(autouse=True)
def restore_runtime_flags():
    prev_mode = cfg.AUTOPILOT_MODE
    prev_sim = cfg.SIM_MODE
    try:
        yield
    finally:
        cfg.AUTOPILOT_MODE = prev_mode
        cfg.SIM_MODE = prev_sim


def _buy_decision() -> AIDirectTrade:
    return AIDirectTrade(
        symbol="NVDA",
        action="BUY",
        order_type="MKT",
        stop_price=95.0,
        invalidation="Break below trend support",
        reason="AI momentum continuation",
        confidence=0.74,
    )


@pytest.mark.anyio
async def test_execute_direct_trade_paper_buy_creates_simulated_trade(anyio_backend):
    cfg.AUTOPILOT_MODE = "PAPER"
    decision = _buy_decision()

    with patch("direct_ai_trader.get_open_positions", new=AsyncMock(return_value=[])), patch(
        "direct_ai_trader.get_latest_price",
        new=AsyncMock(return_value=100.0),
    ), patch(
        "direct_ai_trader._get_account_equity",
        new=AsyncMock(return_value=10_000.0),
    ), patch(
        "direct_ai_trader.calculate_position_size",
        return_value={"shares": 4},
    ), patch(
        "direct_ai_trader.safety_gate.evaluate_runtime_safety",
        new=AsyncMock(return_value=(True, None)),
    ) as mock_gate, patch("direct_ai_trader.save_trade", new=AsyncMock()) as mock_save, patch(
        "direct_ai_trader.order_lifecycle.register_entry_position_from_fill",
        new=AsyncMock(return_value=True),
    ) as mock_register, patch(
        "direct_ai_trader.log_ai_action",
        new=AsyncMock(),
    ), patch("direct_ai_trader.place_order", new=AsyncMock()) as mock_place:
        result = await execute_direct_trade(decision)

    assert result["mode"] == "PAPER"
    assert result["simulated"] is True
    assert result["trade"]["source"] == "ai_direct"
    assert result["trade"]["metadata"]["paper"] is True
    mock_gate.assert_awaited_once()
    mock_save.assert_awaited_once()
    mock_register.assert_awaited_once()
    mock_place.assert_not_called()


@pytest.mark.anyio
async def test_execute_direct_trade_paper_sell_uses_shared_exit_lifecycle(anyio_backend):
    cfg.AUTOPILOT_MODE = "PAPER"
    decision = AIDirectTrade(
        symbol="AAPL",
        action="SELL",
        order_type="MKT",
        stop_price=180.0,
        invalidation="Close broken",
        reason="Protect gains",
        confidence=0.61,
    )
    existing = OpenPosition(
        id="entry-001",
        symbol="AAPL",
        side="BUY",
        quantity=4.0,
        entry_price=100.0,
        entry_time="2026-03-20T10:00:00+00:00",
        atr_at_entry=3.0,
        hard_stop_price=91.0,
        atr_stop_mult=3.0,
        atr_trail_mult=2.0,
        high_watermark=110.0,
        rule_id="rule-001",
        rule_name="Test Rule",
    )

    with patch("direct_ai_trader.get_open_positions", new=AsyncMock(return_value=[existing])), patch(
        "direct_ai_trader.get_latest_price",
        new=AsyncMock(return_value=105.0),
    ), patch(
        "direct_ai_trader.safety_gate.evaluate_runtime_safety",
        new=AsyncMock(return_value=(True, None)),
    ) as mock_gate, patch(
        "direct_ai_trader.order_lifecycle.stamp_exit_trade_context",
        new=AsyncMock(),
    ) as mock_stamp, patch(
        "direct_ai_trader.order_lifecycle.finalize_filled_exit_trade",
        new=AsyncMock(),
    ) as mock_finalize, patch(
        "direct_ai_trader.log_ai_action",
        new=AsyncMock(),
    ), patch("direct_ai_trader.save_trade", new=AsyncMock()) as mock_save:
        result = await execute_direct_trade(decision)

    assert result["mode"] == "PAPER"
    assert result["simulated"] is True
    mock_gate.assert_awaited_once()
    mock_save.assert_not_called()
    mock_stamp.assert_awaited_once()
    mock_finalize.assert_awaited_once()


@pytest.mark.anyio
async def test_execute_direct_trade_sell_without_position_is_blocked(anyio_backend):
    decision = AIDirectTrade(
        symbol="AAPL",
        action="SELL",
        order_type="MKT",
        stop_price=180.0,
        invalidation="Close broken",
        reason="Protect gains",
        confidence=0.61,
    )

    with patch("direct_ai_trader.get_open_positions", new=AsyncMock(return_value=[])):
        with pytest.raises(SafetyViolation, match="Cannot SELL AAPL"):
            await execute_direct_trade(decision)


@pytest.mark.anyio
async def test_execute_direct_trade_live_buy_uses_order_executor(anyio_backend):
    cfg.AUTOPILOT_MODE = "LIVE"
    decision = _buy_decision()
    live_trade = Trade(
        rule_id="ai-direct:NVDA",
        rule_name="AI Direct BUY NVDA",
        symbol="NVDA",
        action="BUY",
        asset_type="STK",
        quantity=4,
        order_type="MKT",
        limit_price=None,
        fill_price=100.0,
        status="FILLED",
        order_id=42,
        timestamp="2026-03-21T12:00:00Z",
    )

    with patch("direct_ai_trader.get_open_positions", new=AsyncMock(return_value=[])), patch(
        "direct_ai_trader.get_latest_price",
        new=AsyncMock(return_value=100.0),
    ), patch(
        "direct_ai_trader._get_account_equity",
        new=AsyncMock(return_value=10_000.0),
    ), patch(
        "direct_ai_trader.calculate_position_size",
        return_value={"shares": 4},
    ), patch(
        "direct_ai_trader.safety_gate.evaluate_runtime_safety",
        new=AsyncMock(return_value=(True, None)),
    ) as mock_gate, patch(
        "direct_ai_trader.place_order",
        new=AsyncMock(return_value=live_trade),
    ) as mock_place, patch("direct_ai_trader.save_trade", new=AsyncMock()) as mock_save, patch(
        "direct_ai_trader.order_lifecycle.register_entry_position_from_fill",
        new=AsyncMock(return_value=True),
    ) as mock_register, patch(
        "direct_ai_trader.log_ai_action",
        new=AsyncMock(),
    ):
        result = await execute_direct_trade(decision)

    assert result["mode"] == "LIVE"
    assert result["simulated"] is False
    assert result["trade"]["order_id"] == 42
    mock_gate.assert_awaited_once()
    mock_place.assert_awaited_once()
    _, kwargs = mock_place.await_args
    assert kwargs["source"] == "ai_direct"
    assert kwargs["skip_safety"] is True
    assert kwargs["require_autopilot_authority"] is True
    assert kwargs["is_exit"] is False
    mock_save.assert_awaited_once()
    # HB1-01: live BUY must register tracked open-position lifecycle
    mock_register.assert_awaited_once()


@pytest.mark.anyio
async def test_execute_direct_trade_live_buy_rejects_error_trade(anyio_backend):
    cfg.AUTOPILOT_MODE = "LIVE"
    decision = _buy_decision()
    errored_trade = Trade(
        rule_id="ai-direct:NVDA",
        rule_name="AI Direct BUY NVDA",
        symbol="NVDA",
        action="BUY",
        asset_type="STK",
        quantity=4,
        order_type="MKT",
        limit_price=None,
        fill_price=None,
        status="ERROR",
        order_id=None,
        timestamp="2026-03-21T12:00:00Z",
    )

    with patch("direct_ai_trader.get_open_positions", new=AsyncMock(return_value=[])), patch(
        "direct_ai_trader.get_latest_price",
        new=AsyncMock(return_value=100.0),
    ), patch(
        "direct_ai_trader._get_account_equity",
        new=AsyncMock(return_value=10_000.0),
    ), patch(
        "direct_ai_trader.calculate_position_size",
        return_value={"shares": 4},
    ), patch(
        "direct_ai_trader.safety_gate.evaluate_runtime_safety",
        new=AsyncMock(return_value=(True, None)),
    ), patch(
        "direct_ai_trader.place_order",
        new=AsyncMock(return_value=errored_trade),
    ), patch("direct_ai_trader.save_trade", new=AsyncMock()) as mock_save:
        with pytest.raises(SafetyViolation, match="Failed to place direct AI trade"):
            await execute_direct_trade(decision)

    mock_save.assert_not_called()


@pytest.mark.anyio
async def test_execute_direct_trade_blocks_when_shared_gate_rejects(anyio_backend):
    cfg.AUTOPILOT_MODE = "PAPER"
    decision = _buy_decision()

    with patch("direct_ai_trader.get_open_positions", new=AsyncMock(return_value=[])), patch(
        "direct_ai_trader.get_latest_price",
        new=AsyncMock(return_value=100.0),
    ), patch(
        "direct_ai_trader._get_account_equity",
        new=AsyncMock(return_value=10_000.0),
    ), patch(
        "direct_ai_trader.calculate_position_size",
        return_value={"shares": 4},
    ), patch(
        "direct_ai_trader.safety_gate.evaluate_runtime_safety",
        new=AsyncMock(return_value=(False, "blocked for testing")),
    ):
        with pytest.raises(SafetyViolation, match="blocked for testing"):
            await execute_direct_trade(decision)


@pytest.mark.anyio
async def test_live_sell_pending_marks_position_for_reconciliation(anyio_backend):
    """A live SELL that returns PENDING must mark position for reconciliation."""
    cfg.AUTOPILOT_MODE = "LIVE"
    decision = AIDirectTrade(
        symbol="AAPL",
        action="SELL",
        order_type="MKT",
        stop_price=180.0,
        invalidation="Close broken",
        reason="Protect gains",
        confidence=0.61,
    )
    existing = OpenPosition(
        id="entry-001",
        symbol="AAPL",
        side="BUY",
        quantity=4.0,
        entry_price=100.0,
        entry_time="2026-03-20T10:00:00+00:00",
        atr_at_entry=3.0,
        hard_stop_price=91.0,
        atr_stop_mult=3.0,
        atr_trail_mult=2.0,
        high_watermark=110.0,
        rule_id="rule-001",
        rule_name="Test Rule",
    )
    pending_trade = Trade(
        rule_id="ai-direct:AAPL",
        rule_name="AI Direct SELL AAPL",
        symbol="AAPL",
        action="SELL",
        asset_type="STK",
        quantity=4,
        order_type="MKT",
        limit_price=None,
        fill_price=None,
        status="PENDING",
        order_id=99,
        timestamp="2026-03-21T12:00:00Z",
    )

    with patch("direct_ai_trader.get_open_positions", new=AsyncMock(return_value=[existing])), \
         patch("direct_ai_trader.get_latest_price", new=AsyncMock(return_value=105.0)), \
         patch("direct_ai_trader.safety_gate.evaluate_runtime_safety", new=AsyncMock(return_value=(True, None))), \
         patch("direct_ai_trader.place_order", new=AsyncMock(return_value=pending_trade)), \
         patch("direct_ai_trader.order_lifecycle.stamp_exit_trade_context", new=AsyncMock()) as mock_stamp, \
         patch("direct_ai_trader.order_lifecycle.finalize_filled_exit_trade", new=AsyncMock()) as mock_finalize, \
         patch("database.save_open_position", new=AsyncMock()) as mock_save_pos, \
         patch("services.order_recovery.mark_exit_pending_submitted") as mock_mark_pending, \
         patch("direct_ai_trader.log_ai_action", new=AsyncMock()):
        result = await execute_direct_trade(decision)

    assert result["mode"] == "LIVE"
    mock_stamp.assert_awaited_once()
    mock_finalize.assert_not_called()  # NOT filled, so no finalize
    mock_mark_pending.assert_called_once()
    mock_save_pos.assert_awaited_once_with(existing)

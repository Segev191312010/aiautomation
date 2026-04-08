"""Tests for shared order lifecycle helpers."""
from __future__ import annotations

from unittest.mock import ANY, AsyncMock, patch

import pytest

from models import OpenPosition, Trade
from services import order_lifecycle


def _trade(**overrides) -> Trade:
    base = {
        "id": "trade-001",
        "rule_id": "rule-001",
        "rule_name": "Rule 001",
        "symbol": "AAPL",
        "action": "BUY",
        "asset_type": "STK",
        "quantity": 10,
        "order_type": "MKT",
        "limit_price": None,
        "fill_price": 150.0,
        "status": "FILLED",
        "order_id": 123,
        "timestamp": "2026-03-29T10:00:00+00:00",
    }
    base.update(overrides)
    return Trade(**base)


def _position(**overrides) -> OpenPosition:
    base = {
        "id": "trade-001",
        "symbol": "AAPL",
        "side": "BUY",
        "quantity": 10.0,
        "entry_price": 150.0,
        "entry_time": "2026-03-29T10:00:00+00:00",
        "atr_at_entry": 2.0,
        "hard_stop_price": 144.0,
        "atr_stop_mult": 3.0,
        "atr_trail_mult": 2.0,
        "high_watermark": 155.0,
        "rule_id": "rule-001",
        "rule_name": "Rule 001",
    }
    base.update(overrides)
    return OpenPosition(**base)


@pytest.mark.anyio
async def test_persist_filled_trade_record_updates_status_and_entry_price(anyio_backend):
    trade = _trade(status="PENDING", fill_price=None)

    with patch("services.order_lifecycle.update_trade_status", new=AsyncMock()) as mock_update, patch(
        "services.order_lifecycle.save_trade",
        new=AsyncMock(),
    ) as mock_save:
        updated = await order_lifecycle.persist_filled_trade_record(trade, 151.25)

    mock_update.assert_awaited_once_with("trade-001", "FILLED", 151.25, db=ANY)
    mock_save.assert_awaited_once_with(trade, db=ANY)
    assert updated.status == "FILLED"
    assert updated.fill_price == 151.25
    assert updated.entry_price == 151.25


@pytest.mark.anyio
async def test_register_entry_position_from_fill_registers_buy(anyio_backend):
    trade = _trade()
    fake_df = [object()] * 14

    with patch("services.order_lifecycle.get_historical_bars", new=AsyncMock(return_value=fake_df)), patch(
        "services.order_lifecycle.register_position",
        new=AsyncMock(),
    ) as mock_register:
        registered = await order_lifecycle.register_entry_position_from_fill(trade, rule_name="Rule 001")

    assert registered is True
    mock_register.assert_awaited_once()


@pytest.mark.anyio
async def test_register_entry_position_from_fill_skips_non_buy(anyio_backend):
    trade = _trade(action="SELL")

    with patch("services.order_lifecycle.get_historical_bars", new=AsyncMock()) as mock_bars:
        registered = await order_lifecycle.register_entry_position_from_fill(trade)

    assert registered is False
    mock_bars.assert_not_called()


@pytest.mark.anyio
async def test_stamp_exit_trade_context_inherits_entry_trade_fields(anyio_backend):
    entry = _trade(id="entry-001")
    entry.mode = "LIVE"
    entry.source = "rule"
    entry.decision_id = "item-123"
    exit_trade = _trade(id="exit-001", action="SELL", fill_price=155.0)
    pos = _position(id="entry-001")

    with patch("services.order_lifecycle.get_trade", new=AsyncMock(return_value=entry)), patch(
        "services.order_lifecycle.save_trade",
        new=AsyncMock(),
    ) as mock_save:
        stamped = await order_lifecycle.stamp_exit_trade_context(exit_trade, pos)

    mock_save.assert_awaited_once_with(exit_trade)
    assert stamped.position_id == "entry-001"
    assert stamped.mode == "LIVE"
    assert stamped.source == "rule"
    assert stamped.decision_id == "item-123"


@pytest.mark.anyio
async def test_finalize_filled_exit_trade_finalizes_and_deletes_position(anyio_backend):
    exit_trade = _trade(id="exit-001", action="SELL", fill_price=155.0)
    pos = _position(id="entry-001")
    finalized = _trade(id="exit-001", action="SELL", fill_price=155.0)
    finalized.realized_pnl = 50.0

    with patch("services.order_lifecycle.finalize_trade_outcome", new=AsyncMock(return_value=finalized)) as mock_finalize, patch(
        "services.order_lifecycle.delete_open_position",
        new=AsyncMock(),
    ) as mock_delete:
        result = await order_lifecycle.finalize_filled_exit_trade(
            exit_trade,
            pos,
            close_reason="trail_stop",
            fallback_exit_price=155.0,
        )

    assert result is finalized
    mock_finalize.assert_awaited_once_with(
        "exit-001",
        position_side="BUY",
        entry_price=150.0,
        exit_price=155.0,
        fees=0.0,
        close_reason="trail_stop",
        position_id="entry-001",
        db=ANY,
    )
    mock_delete.assert_awaited_once_with("entry-001", db=ANY)

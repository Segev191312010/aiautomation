"""Shared order recovery helper tests."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, Mock, patch

import pytest

from models import OpenPosition, Trade
from services import order_recovery


def _make_position(**overrides) -> OpenPosition:
    defaults = {
        "id": "pos-001",
        "symbol": "AAPL",
        "side": "BUY",
        "quantity": 10.0,
        "entry_price": 150.0,
        "entry_time": "2026-03-20T10:00:00+00:00",
        "atr_at_entry": 3.0,
        "hard_stop_price": 141.0,
        "atr_stop_mult": 3.0,
        "atr_trail_mult": 2.0,
        "high_watermark": 155.0,
        "rule_id": "rule-001",
        "rule_name": "Test Rule",
    }
    defaults.update(overrides)
    return OpenPosition(**defaults)


def _make_trade(status: str = "PENDING", fill_price: float | None = None) -> Trade:
    return Trade(
        id="trade-001",
        rule_id="rule-001",
        rule_name="Test Rule",
        symbol="AAPL",
        action="BUY",
        asset_type="STK",
        quantity=10,
        order_type="MKT",
        limit_price=None,
        fill_price=fill_price,
        status=status,
        order_id=12345,
        timestamp="2026-03-20T14:00:00+00:00",
    )


def test_normalize_trade_status_maps_broker_variants():
    assert order_recovery.normalize_trade_status("Filled") == "FILLED"
    assert order_recovery.normalize_trade_status("ApiCancelled") == "CANCELLED"
    assert order_recovery.normalize_trade_status("PreSubmitted") == "PENDING"
    assert order_recovery.normalize_trade_status("ERROR") == "ERROR"
    assert order_recovery.normalize_trade_status("weird") == "UNKNOWN"


@pytest.mark.anyio
async def test_reconcile_trade_status_update_marks_filled_and_runs_callbacks(anyio_backend):
    trade = _make_trade(status="PENDING")
    callback = Mock()

    with patch(
        "services.order_recovery.order_lifecycle.persist_filled_trade_record",
        new=AsyncMock(return_value=trade),
    ) as mock_persist:
        resolved = await order_recovery.reconcile_trade_status_update(
            trade,
            "Filled",
            fill_price=151.25,
            fill_callbacks=[callback],
        )

    assert resolved == "FILLED"
    mock_persist.assert_awaited_once_with(trade, 151.25)
    callback.assert_called_once_with(trade)


@pytest.mark.anyio
async def test_reconcile_trade_status_update_marks_cancelled(anyio_backend):
    trade = _make_trade(status="PENDING")
    with patch("services.order_recovery.update_trade_status", new=AsyncMock()) as mock_update:
        resolved = await order_recovery.reconcile_trade_status_update(trade, "ApiCancelled")

    assert resolved == "CANCELLED"
    mock_update.assert_awaited_once_with(trade.id, "CANCELLED")
    assert trade.status == "CANCELLED"


def test_evaluate_pending_exit_resolution_filled():
    pos = _make_position(exit_pending_order_id=12345)
    trade = _make_trade(status="FILLED", fill_price=148.0)
    resolution = order_recovery.evaluate_pending_exit_resolution(
        pos,
        trade,
        now=datetime.now(timezone.utc),
        timeout_seconds=90,
    )
    assert resolution.state == "filled"
    assert resolution.reason is None


def test_evaluate_pending_exit_resolution_timeout_requests_cancel():
    old_time = (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat()
    pos = _make_position(exit_pending_order_id=12345, last_exit_attempt_at=old_time)
    trade = _make_trade(status="PENDING")
    resolution = order_recovery.evaluate_pending_exit_resolution(
        pos,
        trade,
        now=datetime.now(timezone.utc),
        timeout_seconds=90,
    )
    assert resolution.state == "retry"
    assert resolution.should_cancel is True
    assert "timed out" in (resolution.reason or "")


def test_mark_exit_retry_state_clears_pending_and_increments():
    pos = _make_position(exit_pending_order_id=12345, exit_attempts=1)
    updated = order_recovery.mark_exit_retry_state(
        pos,
        "timeout",
        now=datetime.now(timezone.utc),
    )
    assert updated.exit_pending_order_id is None
    assert updated.exit_attempts == 2
    assert updated.last_exit_error == "timeout"


def test_mark_exit_pending_submitted_tracks_order_id():
    pos = _make_position(exit_pending_order_id=None)
    updated = order_recovery.mark_exit_pending_submitted(
        pos,
        999,
        now=datetime.now(timezone.utc),
    )
    assert updated.exit_pending_order_id == 999

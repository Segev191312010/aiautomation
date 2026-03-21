"""
Exit lifecycle tests — Phase 1 account safety.

Verifies that tracked positions are NEVER deleted unless the exit
trade is confirmed FILLED. Covers: failed exits, pending exits,
timed-out exits, cancelled exits, retry cap, and short P&L sign.
"""
import pytest
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch, MagicMock

from models import OpenPosition, Trade, Rule, TradeAction


# ── Fixtures ─────────────────────────────────────────────────────────────────

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


def _make_trade(status="PENDING", order_id=12345, fill_price=None) -> Trade:
    return Trade(
        id="trade-exit-001",
        rule_id="rule-001",
        rule_name="EXIT:Test Rule",
        symbol="AAPL",
        action="SELL",
        asset_type="STK",
        quantity=10,
        order_type="MKT",
        limit_price=None,
        fill_price=fill_price,
        status=status,
        order_id=order_id,
        timestamp="2026-03-20T14:00:00+00:00",
    )


# ── Test: Failed exit keeps position ─────────────────────────────────────────

@pytest.mark.anyio
async def test_failed_exit_keeps_position(anyio_backend):
    """When place_order raises, position must NOT be deleted."""
    from bot_runner import _place_exit_order

    pos = _make_position()
    saved_positions = []

    with patch("bot_runner.place_order", side_effect=RuntimeError("IBKR connection lost")), \
         patch("bot_runner.save_open_position", new_callable=AsyncMock, side_effect=lambda p, **kw: saved_positions.append(p)), \
         patch("bot_runner.delete_open_position", new_callable=AsyncMock) as mock_delete, \
         patch("bot_runner._emit", new_callable=AsyncMock):

        await _place_exit_order(pos, "AAPL", 10, 145.0, "hard_stop")

    # Position NOT deleted
    mock_delete.assert_not_called()
    # Position saved with error info
    assert len(saved_positions) == 1
    assert saved_positions[0].exit_attempts == 1
    assert "IBKR connection lost" in saved_positions[0].last_exit_error


# ── Test: Pending exit blocks duplicate ──────────────────────────────────────

@pytest.mark.anyio
async def test_pending_exit_blocks_duplicate(anyio_backend):
    """A position with exit_pending_order_id should not get another exit order."""
    from bot_runner import _process_exits

    pos = _make_position(exit_pending_order_id=99999)

    with patch("bot_runner.update_watermarks", return_value=[]), \
         patch("bot_runner._reconcile_pending_exit", new_callable=AsyncMock) as mock_reconcile, \
         patch("bot_runner._place_exit_order", new_callable=AsyncMock) as mock_place:

        await _process_exits([pos], {"AAPL": MagicMock()})

    # Reconcile was called (to check the pending order)
    mock_reconcile.assert_called_once()
    # No NEW exit order placed
    mock_place.assert_not_called()


# ── Test: Filled exit deletes position ───────────────────────────────────────

@pytest.mark.anyio
async def test_filled_exit_deletes_position(anyio_backend):
    """When pending exit is FILLED, position should be deleted."""
    from bot_runner import _reconcile_pending_exit

    pos = _make_position(exit_pending_order_id=12345, last_exit_attempt_at="2026-03-20T14:00:00+00:00")
    filled_trade = _make_trade(status="FILLED", fill_price=148.0)

    with patch("database.get_trade_by_order_id", new_callable=AsyncMock, return_value=filled_trade), \
         patch("bot_runner.save_open_position", new_callable=AsyncMock), \
         patch("bot_runner.delete_open_position", new_callable=AsyncMock) as mock_delete, \
         patch("bot_runner._emit", new_callable=AsyncMock) as mock_emit:

        await _reconcile_pending_exit(pos)

    mock_delete.assert_called_once_with("pos-001")
    # Pending cleared before delete (B3 fix)
    assert pos.exit_pending_order_id is None
    # Exit event emitted
    mock_emit.assert_called_once()
    payload = mock_emit.call_args[0][0]
    assert payload["type"] == "exit"
    assert payload["pnl"] < 0  # BUY at 150, filled at 148 = loss


# ── Test: Cancelled exit clears pending + retries ────────────────────────────

@pytest.mark.anyio
async def test_cancelled_exit_clears_pending(anyio_backend):
    """Cancelled exit should clear pending state and increment attempts."""
    from bot_runner import _reconcile_pending_exit

    pos = _make_position(exit_pending_order_id=12345, exit_attempts=1)
    cancelled_trade = _make_trade(status="CANCELLED")

    saved = []
    with patch("database.get_trade_by_order_id", new_callable=AsyncMock, return_value=cancelled_trade), \
         patch("bot_runner.save_open_position", new_callable=AsyncMock, side_effect=lambda p, **kw: saved.append(p)), \
         patch("bot_runner.delete_open_position", new_callable=AsyncMock) as mock_delete:

        await _reconcile_pending_exit(pos)

    mock_delete.assert_not_called()
    assert len(saved) == 1
    assert saved[0].exit_pending_order_id is None
    assert saved[0].exit_attempts == 2
    assert "CANCELLED" in saved[0].last_exit_error


# ── Test: Timed-out pending triggers cancel + retry ──────────────────────────

@pytest.mark.anyio
async def test_timeout_triggers_cancel_and_retry(anyio_backend):
    """Pending exit older than 90s should be cancelled and retried."""
    from bot_runner import _reconcile_pending_exit

    old_time = (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat()
    pos = _make_position(exit_pending_order_id=12345, last_exit_attempt_at=old_time, exit_attempts=0)
    pending_trade = _make_trade(status="PENDING")

    saved = []
    with patch("database.get_trade_by_order_id", new_callable=AsyncMock, return_value=pending_trade), \
         patch("order_executor.cancel_order", new_callable=AsyncMock) as mock_cancel, \
         patch("bot_runner.save_open_position", new_callable=AsyncMock, side_effect=lambda p, **kw: saved.append(p)), \
         patch("bot_runner.delete_open_position", new_callable=AsyncMock) as mock_delete:

        await _reconcile_pending_exit(pos)

    mock_cancel.assert_called_once_with(12345)
    mock_delete.assert_not_called()
    assert len(saved) == 1
    assert saved[0].exit_pending_order_id is None
    assert saved[0].exit_attempts == 1
    assert "timed out" in saved[0].last_exit_error


# ── Test: Retry cap stops automation ─────────────────────────────────────────

@pytest.mark.anyio
async def test_retry_cap_stops_automation(anyio_backend):
    """Position at retry cap should not get new exit orders."""
    from bot_runner import _process_exits, MAX_EXIT_ATTEMPTS

    pos = _make_position(exit_attempts=MAX_EXIT_ATTEMPTS)

    with patch("bot_runner.update_watermarks", return_value=[]), \
         patch("bot_runner._place_exit_order", new_callable=AsyncMock) as mock_place, \
         patch("bot_runner.check_exits", return_value=(True, "hard_stop")):

        await _process_exits([pos], {"AAPL": MagicMock()})

    # No exit placed — cap reached
    mock_place.assert_not_called()


# ── Test: Short P&L positive when price falls ────────────────────────────────

@pytest.mark.anyio
async def test_short_pnl_positive_when_price_falls(anyio_backend):
    """SELL position should have positive PnL when price drops."""
    from bot_runner import _place_exit_order

    pos = _make_position(side="SELL", entry_price=150.0)
    filled_trade = _make_trade(status="FILLED", fill_price=140.0)

    emitted = []
    with patch("bot_runner.place_order", new_callable=AsyncMock, return_value=filled_trade), \
         patch("bot_runner.delete_open_position", new_callable=AsyncMock), \
         patch("bot_runner._emit", new_callable=AsyncMock, side_effect=lambda p: emitted.append(p)):

        await _place_exit_order(pos, "AAPL", 10, 140.0, "trail_stop")

    assert len(emitted) == 1
    pnl = emitted[0]["pnl"]
    assert pnl > 0, f"Short P&L should be positive when price falls, got {pnl}"
    assert pnl == 100.0  # (150 - 140) * 10


# ── Test: place_order returns None ───────────────────────────────────────────

@pytest.mark.anyio
async def test_place_order_returns_none_keeps_position(anyio_backend):
    """If place_order returns None, position stays and attempts increment."""
    from bot_runner import _place_exit_order

    pos = _make_position()
    saved = []

    with patch("bot_runner.place_order", new_callable=AsyncMock, return_value=None), \
         patch("bot_runner.save_open_position", new_callable=AsyncMock, side_effect=lambda p, **kw: saved.append(p)), \
         patch("bot_runner.delete_open_position", new_callable=AsyncMock) as mock_delete:

        await _place_exit_order(pos, "AAPL", 10, 145.0, "hard_stop")

    mock_delete.assert_not_called()
    assert saved[0].exit_attempts == 1
    assert "returned None" in saved[0].last_exit_error

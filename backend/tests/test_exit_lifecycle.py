"""
Exit lifecycle tests — Phase 1 account safety.

Verifies that tracked positions are NEVER deleted unless the exit
trade is confirmed FILLED. Covers: failed exits, pending exits,
timed-out exits, cancelled exits, retry cap, and short P&L sign.
"""
import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch, MagicMock

from database import init_db
from models import OpenPosition, Trade


@pytest.fixture(autouse=True)
async def _ensure_schema():
    """Ensure DB tables exist for all tests in this module."""
    await init_db()


# ── Fixtures ──────────────────────────────────────────────────────────────────

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


# ── Test: Failed exit keeps position ──────────────────────────────────────────

@pytest.mark.anyio
async def test_failed_exit_keeps_position(anyio_backend):
    """When place_order raises, position must NOT be deleted."""
    from bot_runner import _place_exit_order

    pos = _make_position()
    saved_positions = []

    with patch("order_executor.place_order", side_effect=RuntimeError("IBKR connection lost")), \
         patch("database.save_open_position", new_callable=AsyncMock, side_effect=lambda p, **kw: saved_positions.append(p)), \
         patch("bot_exits.get_open_position", new_callable=AsyncMock, return_value=pos), \
         patch("bot_exits._emit", new_callable=AsyncMock), \
         patch("services.order_lifecycle.stamp_exit_trade_context", new_callable=AsyncMock), \
         patch("services.order_lifecycle.finalize_filled_exit_trade", new_callable=AsyncMock):

        await _place_exit_order(pos, "AAPL", 10, 145.0, "hard_stop")

    assert len(saved_positions) == 1
    assert saved_positions[0].exit_attempts == 1
    assert "IBKR connection lost" in saved_positions[0].last_exit_error


# ── Test: Pending exit blocks duplicate ───────────────────────────────────────

@pytest.mark.anyio
async def test_pending_exit_blocks_duplicate(anyio_backend):
    """A position with exit_pending_order_id should not get another exit order."""
    from bot_runner import _process_exits

    pos = _make_position(exit_pending_order_id=99999)

    with patch("position_tracker.update_watermarks", return_value=[]), \
         patch("bot_exits._reconcile_pending_exit", new_callable=AsyncMock) as mock_reconcile, \
         patch("bot_exits._place_exit_order", new_callable=AsyncMock) as mock_place:

        await _process_exits([pos], {"AAPL": MagicMock()})

    mock_reconcile.assert_called_once()
    mock_place.assert_not_called()


# ── Test: Filled exit deletes position ────────────────────────────────────────

@pytest.mark.anyio
async def test_filled_exit_deletes_position(anyio_backend):
    """When pending exit is FILLED, position should be finalized through lifecycle helper."""
    from bot_runner import _reconcile_pending_exit

    pos = _make_position(exit_pending_order_id=12345, last_exit_attempt_at="2026-03-20T14:00:00+00:00")
    filled_trade = _make_trade(status="FILLED", fill_price=148.0)

    finalized_trade = _make_trade(status="FILLED", fill_price=148.0)
    finalized_trade.realized_pnl = -20.0  # (148-150)*10

    with patch("database.get_trade_by_order_id", new_callable=AsyncMock, return_value=filled_trade), \
         patch("database.save_open_position", new_callable=AsyncMock), \
         patch("bot_exits._emit", new_callable=AsyncMock) as mock_emit, \
         patch("services.order_lifecycle.finalize_filled_exit_trade", new_callable=AsyncMock, return_value=finalized_trade) as mock_finalize:

        await _reconcile_pending_exit(pos)

    mock_finalize.assert_awaited_once()
    assert pos.exit_pending_order_id is None
    mock_emit.assert_called_once()
    payload = mock_emit.call_args[0][0]
    assert payload["type"] == "exit"
    assert payload["pnl"] < 0


# ── Test: Cancelled exit clears pending + retries ─────────────────────────────

@pytest.mark.anyio
async def test_cancelled_exit_clears_pending(anyio_backend):
    """Cancelled exit should clear pending state and increment attempts."""
    from bot_runner import _reconcile_pending_exit

    pos = _make_position(exit_pending_order_id=12345, exit_attempts=1)
    cancelled_trade = _make_trade(status="CANCELLED")

    saved = []
    with patch("database.get_trade_by_order_id", new_callable=AsyncMock, return_value=cancelled_trade), \
         patch("database.save_open_position", new_callable=AsyncMock, side_effect=lambda p, **kw: saved.append(p)), \
         patch("database.delete_open_position", new_callable=AsyncMock) as mock_delete:

        await _reconcile_pending_exit(pos)

    mock_delete.assert_not_called()
    assert len(saved) == 1
    assert saved[0].exit_pending_order_id is None
    assert saved[0].exit_attempts == 2
    assert "CANCELLED" in saved[0].last_exit_error


# ── Test: Timed-out pending triggers cancel + retry ───────────────────────────

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
         patch("database.save_open_position", new_callable=AsyncMock, side_effect=lambda p, **kw: saved.append(p)), \
         patch("database.delete_open_position", new_callable=AsyncMock) as mock_delete:

        await _reconcile_pending_exit(pos)

    mock_cancel.assert_called_once_with(12345)
    mock_delete.assert_not_called()
    assert len(saved) == 1
    assert saved[0].exit_pending_order_id is None
    assert saved[0].exit_attempts == 1
    assert "timed out" in saved[0].last_exit_error


# ── Test: Retry cap stops automation ───────────────────────────────────────────

@pytest.mark.anyio
async def test_retry_cap_triggers_force_close_in_process_exits(anyio_backend):
    """Position at retry cap should trigger _check_retry_cap (force-close) instead of normal exit."""
    from bot_runner import _process_exits, MAX_EXIT_ATTEMPTS

    pos = _make_position(exit_attempts=MAX_EXIT_ATTEMPTS)

    with patch("position_tracker.update_watermarks", return_value=[]), \
         patch("bot_exits._check_retry_cap", new_callable=AsyncMock) as mock_cap, \
         patch("position_tracker.check_exits", return_value=(True, "hard_stop")):

        await _process_exits([pos], {"AAPL": MagicMock()})

    mock_cap.assert_called_once()


# ── Test: Short P&L positive when price falls ─────────────────────────────────

@pytest.mark.anyio
async def test_short_pnl_positive_when_price_falls(anyio_backend):
    """SELL position should have positive PnL when price drops."""
    from bot_runner import _place_exit_order

    pos = _make_position(side="SELL", entry_price=150.0)
    filled_trade = _make_trade(status="FILLED", fill_price=140.0)

    finalized_trade = _make_trade(status="FILLED", fill_price=140.0)
    finalized_trade.realized_pnl = 100.0  # (150-140)*10

    emitted = []
    with patch("order_executor.place_order", new_callable=AsyncMock, return_value=filled_trade), \
         patch("bot_exits.get_open_position", new_callable=AsyncMock, return_value=pos), \
         patch("bot_exits._emit", new_callable=AsyncMock, side_effect=lambda p: emitted.append(p)), \
         patch("services.order_lifecycle.stamp_exit_trade_context", new_callable=AsyncMock), \
         patch("services.order_lifecycle.finalize_filled_exit_trade", new_callable=AsyncMock, return_value=finalized_trade):

        await _place_exit_order(pos, "AAPL", 10, 140.0, "trail_stop")

    assert len(emitted) == 1
    pnl = emitted[0]["pnl"]
    assert pnl > 0, f"Short P&L should be positive when price falls, got {pnl}"
    assert pnl == 100.0


# ── Test: place_order returns None ────────────────────────────────────────────

@pytest.mark.anyio
async def test_place_order_returns_none_keeps_position(anyio_backend):
    """If place_order returns None, position stays and attempts increment."""
    from bot_runner import _place_exit_order

    pos = _make_position()
    saved = []

    with patch("order_executor.place_order", new_callable=AsyncMock, return_value=None), \
         patch("bot_exits.get_open_position", new_callable=AsyncMock, return_value=pos), \
         patch("database.save_open_position", new_callable=AsyncMock, side_effect=lambda p, **kw: saved.append(p)), \
         patch("database.delete_open_position", new_callable=AsyncMock) as mock_delete:

        await _place_exit_order(pos, "AAPL", 10, 145.0, "hard_stop")

    mock_delete.assert_not_called()
    assert saved[0].exit_attempts == 1
    assert "returned None" in saved[0].last_exit_error


# ?? Test: ERROR exit trade retries instead of tracking as pending ?????????????????

@pytest.mark.anyio
async def test_error_exit_trade_retries_instead_of_pending(anyio_backend):
    """If place_order returns an ERROR trade, the position should stay and retry state should advance."""
    from bot_runner import _place_exit_order

    pos = _make_position()
    errored_trade = _make_trade(status="ERROR", order_id=77777)
    saved = []

    with patch("order_executor.place_order", new_callable=AsyncMock, return_value=errored_trade), \
         patch("bot_exits.get_open_position", new_callable=AsyncMock, return_value=pos), \
         patch("database.save_open_position", new_callable=AsyncMock, side_effect=lambda p, **kw: saved.append(p)), \
         patch("bot_exits._check_retry_cap", new_callable=AsyncMock) as mock_retry_cap, \
         patch("services.order_lifecycle.stamp_exit_trade_context", new_callable=AsyncMock):

        await _place_exit_order(pos, "AAPL", 10, 145.0, "hard_stop")

    assert len(saved) == 1
    assert saved[0].exit_pending_order_id is None
    assert saved[0].exit_attempts == 1
    assert "ERROR" in (saved[0].last_exit_error or "")
    mock_retry_cap.assert_awaited_once()


# ── Test: Position re-fetched before exit order (BUG-7) ──────────────────────

@pytest.mark.anyio
async def test_place_exit_refetches_position(anyio_backend):
    """_place_exit_order must re-fetch position from DB; if qty changed, use fresh value."""
    from bot_runner import _place_exit_order

    pos = _make_position(quantity=10.0)
    fresh_pos = _make_position(quantity=5.0)  # simulates partial close by another path

    placed_orders = []

    async def capture_place_order(rule, **kw):
        placed_orders.append(rule)
        return _make_trade(status="FILLED", fill_price=145.0)

    finalized_trade = _make_trade(status="FILLED", fill_price=145.0)
    finalized_trade.realized_pnl = -25.0

    with patch("bot_exits.get_open_position", new_callable=AsyncMock, return_value=fresh_pos), \
         patch("order_executor.place_order", new_callable=AsyncMock, side_effect=capture_place_order), \
         patch("bot_exits._emit", new_callable=AsyncMock), \
         patch("services.order_lifecycle.stamp_exit_trade_context", new_callable=AsyncMock), \
         patch("services.order_lifecycle.finalize_filled_exit_trade", new_callable=AsyncMock, return_value=finalized_trade):

        await _place_exit_order(pos, "AAPL", 10, 145.0, "hard_stop")

    assert len(placed_orders) == 1
    assert placed_orders[0].action.quantity == 5, "Should use fresh qty (5), not stale (10)"


@pytest.mark.anyio
async def test_place_exit_skips_if_position_gone(anyio_backend):
    """If position no longer exists at exit time, skip the order."""
    from bot_runner import _place_exit_order

    pos = _make_position()

    with patch("bot_exits.get_open_position", new_callable=AsyncMock, return_value=None), \
         patch("order_executor.place_order", new_callable=AsyncMock) as mock_place:

        await _place_exit_order(pos, "AAPL", 10, 145.0, "hard_stop")

    mock_place.assert_not_called()


# ── Test: Retry cap triggers force-close (BUG-5) ─────────────────────────────

@pytest.mark.anyio
async def test_retry_cap_force_closes_position(anyio_backend):
    """When retry cap is reached, a force-close MKT order should be placed."""
    from bot_runner import _check_retry_cap, MAX_EXIT_ATTEMPTS

    pos = _make_position(exit_attempts=MAX_EXIT_ATTEMPTS, quantity=10.0)
    fresh_pos = _make_position(exit_attempts=MAX_EXIT_ATTEMPTS, quantity=10.0)
    filled_trade = _make_trade(status="FILLED", fill_price=145.0)

    finalized_trade = _make_trade(status="FILLED", fill_price=145.0)
    finalized_trade.realized_pnl = -50.0

    placed_orders = []

    async def capture_place_order(rule, **kw):
        placed_orders.append(rule)
        return filled_trade

    with patch("bot_exits._emit", new_callable=AsyncMock), \
         patch("bot_exits.get_open_position", new_callable=AsyncMock, return_value=fresh_pos), \
         patch("order_executor.place_order", new_callable=AsyncMock, side_effect=capture_place_order), \
         patch("services.order_lifecycle.stamp_exit_trade_context", new_callable=AsyncMock), \
         patch("services.order_lifecycle.finalize_filled_exit_trade", new_callable=AsyncMock, return_value=finalized_trade):

        await _check_retry_cap(pos)

    assert len(placed_orders) == 1, "Force-close MKT order should be placed at retry cap"
    assert placed_orders[0].action.order_type == "MKT"
    assert placed_orders[0].action.quantity == 10


@pytest.mark.anyio
async def test_force_close_failure_no_recursion(anyio_backend):
    """If force-close also fails, it should NOT recurse into _check_retry_cap again."""
    from bot_runner import _check_retry_cap, MAX_EXIT_ATTEMPTS

    pos = _make_position(exit_attempts=MAX_EXIT_ATTEMPTS, quantity=10.0)

    call_count = 0

    async def counting_check_retry_cap(p):
        nonlocal call_count
        call_count += 1
        if call_count > 1:
            raise AssertionError("_check_retry_cap called recursively!")
        # Call the real implementation
        from bot_exits import _check_retry_cap as real_check
        await real_check(p)

    with patch("bot_exits._emit", new_callable=AsyncMock), \
         patch("bot_exits.get_open_position", new_callable=AsyncMock, return_value=pos), \
         patch("order_executor.place_order", side_effect=RuntimeError("IBKR down")), \
         patch("database.save_open_position", new_callable=AsyncMock):

        # Should not raise or infinite loop — force_close=True skips _check_retry_cap
        await _check_retry_cap(pos)

    # If we got here without recursion error, the test passes

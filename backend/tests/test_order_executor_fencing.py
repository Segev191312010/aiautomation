"""Regression tests for execution lease fencing on broker mutations (SF1a)."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from config import cfg
from db.execution_lease import acquire_execution_lease, release_execution_lease
from models import Rule, TradeAction
from order_executor import cancel_order, place_order, _convert_mkt_orders_to_limit


@pytest.fixture
def isolated_db(tmp_path):
    """Fresh DB for lease ownership tests."""
    db_file = tmp_path / "fencing.db"
    original_cfg_db = cfg.DB_PATH
    cfg.DB_PATH = str(db_file)
    try:
        yield db_file
    finally:
        cfg.DB_PATH = original_cfg_db


@pytest.fixture
def sample_rule():
    return Rule(
        name="fencing-test",
        symbol="AAPL",
        enabled=True,
        conditions=[],
        action=TradeAction(type="BUY", quantity=1, order_type="MKT"),
        cooldown_minutes=0,
    )


@pytest.fixture
def live_mode():
    cfg.SIM_MODE = False
    yield
    cfg.SIM_MODE = True


@pytest.mark.anyio
async def test_place_order_refuses_without_lease(isolated_db, live_mode, sample_rule):
    """If this process holds no execution lease, place_order must not reach IBKR."""
    import startup

    # Ensure no process-global lease is published for this call.
    prev_lease = startup._execution_lease
    startup._execution_lease = None
    try:
        with patch("order_executor.ibkr.is_connected", return_value=True), patch(
            "order_executor.safety_gate.evaluate_runtime_safety",
            new=AsyncMock(return_value=(True, "")),
        ), patch("order_executor._check_and_record_rate_cap", new=AsyncMock(return_value=True)):
            result = await place_order(sample_rule, skip_safety=True, is_exit=True)
        assert result is None
    finally:
        startup._execution_lease = prev_lease


@pytest.mark.anyio
async def test_place_order_refuses_stale_fencing_token(isolated_db, live_mode, sample_rule):
    """A token that no longer matches the DB lease must block broker submission."""
    import startup

    lease = await acquire_execution_lease(owner_id="real-owner")
    try:
        # Publish a stale token into process-global state.
        prev_lease = startup._execution_lease
        stale_lease = type("StaleLease", (), {"fencing_token": "deadbeef" * 4})()
        startup._execution_lease = stale_lease
        try:
            with patch("order_executor.ibkr.is_connected", return_value=True), patch(
                "order_executor.safety_gate.evaluate_runtime_safety",
                new=AsyncMock(return_value=(True, "")),
            ), patch(
                "order_executor._check_and_record_rate_cap", new=AsyncMock(return_value=True)
            ):
                result = await place_order(sample_rule, skip_safety=True, is_exit=True)
            assert result is None
        finally:
            startup._execution_lease = prev_lease
    finally:
        await release_execution_lease(lease.fencing_token)


@pytest.mark.anyio
async def test_cancel_order_refuses_without_lease():
    """cancel_order must fail closed when no lease is held."""
    import startup

    prev_lease = startup._execution_lease
    startup._execution_lease = None
    try:
        result = await cancel_order(12345)
        assert result is False
    finally:
        startup._execution_lease = prev_lease


@pytest.mark.anyio
async def test_cancel_order_refuses_stale_fencing_token(isolated_db):
    """cancel_order must fail closed when the process token is stale."""
    import startup

    lease = await acquire_execution_lease(owner_id="real-owner")
    try:
        prev_lease = startup._execution_lease
        stale_lease = type("StaleLease", (), {"fencing_token": "deadbeef" * 4})()
        startup._execution_lease = stale_lease
        try:
            result = await cancel_order(12345)
            assert result is False
        finally:
            startup._execution_lease = prev_lease
    finally:
        await release_execution_lease(lease.fencing_token)


from types import SimpleNamespace


def _fake_open_trade():
    """Return a minimal fake ib_insync Trade-like object for resubmit tests."""
    order = SimpleNamespace(
        orderType="MKT",
        action="BUY",
        totalQuantity=10,
        orderId=42,
    )
    order_status = SimpleNamespace(status="Submitted")
    contract = SimpleNamespace(symbol="AAPL")
    return SimpleNamespace(order=order, orderStatus=order_status, contract=contract)


@pytest.mark.anyio
async def test_convert_mkt_to_limit_refuses_without_lease(isolated_db):
    """MKT→LIMIT resubmit must not call cancelOrder when no lease is held."""
    import startup

    prev_lease = startup._execution_lease
    startup._execution_lease = None
    try:
        fake = _fake_open_trade()

        def _cancel(order):
            fake.orderStatus.status = "Cancelled"

        with patch("order_executor.ibkr.is_connected", return_value=True), patch(
            "database.get_trades", new=AsyncMock(return_value=[])
        ), patch(
            "order_executor._get_limit_price", new=AsyncMock(return_value=150.0)
        ), patch(
            "order_executor.ibkr.ib.cancelOrder", side_effect=_cancel
        ) as mock_cancel:
            await _convert_mkt_orders_to_limit()
            mock_cancel.assert_not_called()
    finally:
        startup._execution_lease = prev_lease


@pytest.mark.anyio
async def test_convert_mkt_to_limit_refuses_stale_fencing_token(isolated_db):
    """MKT→LIMIT resubmit must not call cancelOrder when the token is stale."""
    import startup

    lease = await acquire_execution_lease(owner_id="real-owner")
    try:
        prev_lease = startup._execution_lease
        stale_lease = type("StaleLease", (), {"fencing_token": "deadbeef" * 4})()
        startup._execution_lease = stale_lease
        try:
            fake = _fake_open_trade()

            def _cancel(order):
                fake.orderStatus.status = "Cancelled"

            with patch("order_executor.ibkr.is_connected", return_value=True), patch(
                "database.get_trades", new=AsyncMock(return_value=[])
            ), patch(
                "order_executor._get_limit_price", new=AsyncMock(return_value=150.0)
            ), patch(
                "order_executor.ibkr.ib.cancelOrder", side_effect=_cancel
            ) as mock_cancel:
                await _convert_mkt_orders_to_limit()
                mock_cancel.assert_not_called()
        finally:
            startup._execution_lease = prev_lease
    finally:
        await release_execution_lease(lease.fencing_token)


# ============================================================================
# Reconciler Fencing Tests (SF1b closeout)
# ============================================================================


@pytest.mark.anyio
async def test_reconcile_pending_orders_refuses_without_lease(isolated_db):
    """reconcile_pending_orders returns early when no lease is held."""
    import startup
    from order_executor import reconcile_pending_orders

    prev_lease = startup._execution_lease
    startup._execution_lease = None
    try:
        with patch("order_executor.ibkr.is_connected", return_value=True), \
             patch("order_executor.ibkr.ib.openTrades") as mock_open_trades:
            await reconcile_pending_orders()
            mock_open_trades.assert_not_called()
    finally:
        startup._execution_lease = prev_lease


@pytest.mark.anyio
async def test_reconcile_pending_orders_refuses_stale_token(isolated_db):
    """reconcile_pending_orders returns early with stale (released) token."""
    import startup
    from order_executor import reconcile_pending_orders

    lease = await acquire_execution_lease(owner_id="real-owner")
    await release_execution_lease(lease.fencing_token)

    prev_lease = startup._execution_lease
    startup._execution_lease = lease  # stale — already released
    try:
        with patch("order_executor.ibkr.is_connected", return_value=True), \
             patch("order_executor.ibkr.ib.openTrades") as mock_open_trades:
            await reconcile_pending_orders()
            mock_open_trades.assert_not_called()
    finally:
        startup._execution_lease = prev_lease

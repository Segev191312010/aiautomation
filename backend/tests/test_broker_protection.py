"""Unit tests for ibkr_client.py guarded broker mutation wrappers.

Tests place_order_guarded and cancel_order_guarded enforce execution fencing
tokens before delegating to the broker. These are distinct from tests in
test_order_executor_fencing.py which test the callers (order_executor.py).

Part of Stage 9B Phase 1 SF1b / ADR 0007.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from config import cfg
from db.execution_lease import acquire_execution_lease, release_execution_lease
from ibkr_client import ibkr as ibkr_singleton


@pytest.fixture
def isolated_db(tmp_path):
    """Point DB_PATH at a fresh file so lease tests are isolated."""
    original = cfg.DB_PATH
    cfg.DB_PATH = str(tmp_path / "broker_protection.db")
    try:
        yield
    finally:
        cfg.DB_PATH = original


def _make_mock_ib():
    """Return a mock IB with placeOrder/cancelOrder spies."""
    mock = MagicMock()
    mock.placeOrder = MagicMock()
    mock.cancelOrder = MagicMock()
    return mock


# ── place_order_guarded ─────────────────────────────────────────────────


@pytest.mark.anyio
async def test_place_order_guarded_rejects_none_token(isolated_db):
    """None token → returns None, never calls ib.placeOrder."""
    mock_ib = _make_mock_ib()
    with patch.object(ibkr_singleton, "_ib", mock_ib):
        result = await ibkr_singleton.place_order_guarded(
            SimpleNamespace(), SimpleNamespace(), fencing_token=None
        )
    mock_ib.placeOrder.assert_not_called()
    assert result is None


@pytest.mark.anyio
async def test_place_order_guarded_rejects_stale_token(isolated_db):
    """Stale (released) token → returns None, no broker call."""
    lease = await acquire_execution_lease(owner_id="test")
    await release_execution_lease(lease.fencing_token)

    mock_ib = _make_mock_ib()
    with patch.object(ibkr_singleton, "_ib", mock_ib):
        result = await ibkr_singleton.place_order_guarded(
            SimpleNamespace(), SimpleNamespace(), fencing_token=lease.fencing_token
        )
    mock_ib.placeOrder.assert_not_called()
    assert result is None


@pytest.mark.anyio
async def test_place_order_guarded_delegates_with_valid_token(isolated_db):
    """Valid token → ib.placeOrder called, returns the IBTrade."""
    lease = await acquire_execution_lease(owner_id="test")
    try:
        fake_trade = SimpleNamespace()
        mock_ib = _make_mock_ib()
        mock_ib.placeOrder.return_value = fake_trade

        with patch.object(ibkr_singleton, "_ib", mock_ib):
            result = await ibkr_singleton.place_order_guarded(
                SimpleNamespace(), SimpleNamespace(), fencing_token=lease.fencing_token
            )
        mock_ib.placeOrder.assert_called_once()
        assert result is fake_trade
    finally:
        await release_execution_lease(lease.fencing_token)


# ── cancel_order_guarded ─────────────────────────────────────────────────


@pytest.mark.anyio
async def test_cancel_order_guarded_rejects_none_token(isolated_db):
    """None token → returns False, never calls ib.cancelOrder."""
    mock_ib = _make_mock_ib()
    with patch.object(ibkr_singleton, "_ib", mock_ib):
        result = await ibkr_singleton.cancel_order_guarded(
            SimpleNamespace(), fencing_token=None
        )
    mock_ib.cancelOrder.assert_not_called()
    assert result is False


@pytest.mark.anyio
async def test_cancel_order_guarded_rejects_stale_token(isolated_db):
    """Stale (released) token → returns False, no broker call."""
    lease = await acquire_execution_lease(owner_id="test")
    await release_execution_lease(lease.fencing_token)

    mock_ib = _make_mock_ib()
    with patch.object(ibkr_singleton, "_ib", mock_ib):
        result = await ibkr_singleton.cancel_order_guarded(
            SimpleNamespace(), fencing_token=lease.fencing_token
        )
    mock_ib.cancelOrder.assert_not_called()
    assert result is False


@pytest.mark.anyio
async def test_cancel_order_guarded_delegates_with_valid_token(isolated_db):
    """Valid token → ib.cancelOrder called, returns True."""
    lease = await acquire_execution_lease(owner_id="test")
    try:
        mock_ib = _make_mock_ib()
        with patch.object(ibkr_singleton, "_ib", mock_ib):
            result = await ibkr_singleton.cancel_order_guarded(
                SimpleNamespace(), fencing_token=lease.fencing_token
            )
        mock_ib.cancelOrder.assert_called_once()
        assert result is True
    finally:
        await release_execution_lease(lease.fencing_token)

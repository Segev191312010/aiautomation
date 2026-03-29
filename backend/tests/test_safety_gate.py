"""Shared runtime safety gate tests."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from safety_kernel import SafetyViolation
from services import safety_gate


@pytest.mark.anyio
async def test_evaluate_runtime_safety_passes_through_success(anyio_backend):
    with patch("services.safety_gate.check_all", new=AsyncMock(return_value=None)) as mock_check:
        allowed, reason = await safety_gate.evaluate_runtime_safety(
            symbol="AAPL",
            side="BUY",
            quantity=1,
            source="rule",
        )
    assert allowed is True
    assert reason is None
    mock_check.assert_awaited_once()


@pytest.mark.anyio
async def test_evaluate_runtime_safety_returns_reason_on_safety_violation(anyio_backend):
    with patch(
        "services.safety_gate.check_all",
        new=AsyncMock(side_effect=SafetyViolation("Kill switch active")),
    ):
        allowed, reason = await safety_gate.evaluate_runtime_safety(
            symbol="AAPL",
            side="BUY",
            quantity=1,
            source="rule",
        )
    assert allowed is False
    assert reason == "Kill switch active"


@pytest.mark.anyio
async def test_evaluate_runtime_safety_fails_closed_on_unexpected_error(anyio_backend):
    with patch(
        "services.safety_gate.check_all",
        new=AsyncMock(side_effect=RuntimeError("db down")),
    ):
        allowed, reason = await safety_gate.evaluate_runtime_safety(
            symbol="AAPL",
            side="BUY",
            quantity=1,
            source="rule",
        )
    assert allowed is False
    assert reason == "Runtime safety gate unavailable - blocking for safety"

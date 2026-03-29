"""Safety Kernel tests - non-negotiable runtime checks."""
from __future__ import annotations

import time
from unittest.mock import AsyncMock, Mock, patch

import pytest

from safety_kernel import (
    SafetyViolation,
    _recent_checks,
    assert_daily_loss_not_locked,
    assert_no_shorts,
    assert_not_duplicate,
    assert_not_killed,
    assert_risk_budget,
    check_all,
)


@pytest.mark.anyio
async def test_kill_switch_blocks(anyio_backend):
    with patch("ai_guardrails._load_guardrails_from_db", new_callable=AsyncMock) as mock_load:
        mock_config = type("C", (), {"emergency_stop": True, "autopilot_mode": "LIVE"})()
        mock_load.return_value = mock_config
        with pytest.raises(SafetyViolation, match="Kill switch"):
            await assert_not_killed()


@pytest.mark.anyio
async def test_kill_switch_allows_when_not_stopped(anyio_backend):
    with patch("ai_guardrails._load_guardrails_from_db", new_callable=AsyncMock) as mock_load:
        mock_config = type("C", (), {"emergency_stop": False, "autopilot_mode": "LIVE"})()
        mock_load.return_value = mock_config
        await assert_not_killed()


@pytest.mark.anyio
async def test_off_mode_blocks_ai_entries(anyio_backend):
    with patch("ai_guardrails._load_guardrails_from_db", new_callable=AsyncMock) as mock_load:
        mock_config = type("C", (), {"emergency_stop": False, "autopilot_mode": "OFF"})()
        mock_load.return_value = mock_config
        with pytest.raises(SafetyViolation, match="Autopilot is OFF"):
            await assert_not_killed()


@pytest.mark.anyio
async def test_daily_loss_lock_blocks_entries(anyio_backend):
    with patch("ai_guardrails._load_guardrails_from_db", new_callable=AsyncMock) as mock_load:
        mock_config = type("C", (), {"daily_loss_locked": True})()
        mock_load.return_value = mock_config
        with pytest.raises(SafetyViolation, match="Daily loss lock"):
            await assert_daily_loss_not_locked(is_exit=False)


@pytest.mark.anyio
async def test_daily_loss_lock_allows_exits(anyio_backend):
    await assert_daily_loss_not_locked(is_exit=True)


def test_risk_budget_rejects_oversized():
    with patch("safety_kernel.cfg") as mock_cfg:
        mock_cfg.RISK_PER_TRADE_PCT = 1.0
        with pytest.raises(SafetyViolation, match="risk"):
            assert_risk_budget(quantity=100, price_estimate=100.0, account_equity=10000.0)


def test_risk_budget_allows_small_position():
    with patch("safety_kernel.cfg") as mock_cfg:
        mock_cfg.RISK_PER_TRADE_PCT = 1.0
        assert_risk_budget(quantity=1, price_estimate=100.0, account_equity=10000.0, stop_price=99.0)


def test_risk_budget_rejects_with_wide_stop():
    with patch("safety_kernel.cfg") as mock_cfg:
        mock_cfg.RISK_PER_TRADE_PCT = 1.0
        with pytest.raises(SafetyViolation, match="risk"):
            assert_risk_budget(quantity=10, price_estimate=100.0, account_equity=10000.0, stop_price=50.0)


def test_risk_budget_skips_when_no_data():
    with patch("safety_kernel.cfg") as mock_cfg:
        mock_cfg.RISK_PER_TRADE_PCT = 1.0
        assert_risk_budget(quantity=100, price_estimate=100.0, account_equity=0)


def test_no_shorts_allows_buy():
    assert_no_shorts("BUY")


def test_no_shorts_allows_sell_exit():
    assert_no_shorts("SELL", is_exit=True)


def test_no_shorts_allows_sell_with_position():
    assert_no_shorts("SELL", has_existing_position=True)


def test_no_shorts_blocks_sell_open():
    with pytest.raises(SafetyViolation, match="Short"):
        assert_no_shorts("SELL", is_exit=False, has_existing_position=False)


def test_dedup_blocks_rapid_fire():
    _recent_checks.clear()
    assert_not_duplicate("AAPL", "BUY", "rule")
    with pytest.raises(SafetyViolation, match="Duplicate"):
        assert_not_duplicate("AAPL", "BUY", "rule")


def test_dedup_allows_different_symbols():
    _recent_checks.clear()
    assert_not_duplicate("AAPL", "BUY", "rule")
    assert_not_duplicate("TSLA", "BUY", "rule")


def test_dedup_allows_after_window():
    _recent_checks.clear()
    _recent_checks["AAPL:BUY:RULE"] = time.time() - 20
    assert_not_duplicate("AAPL", "BUY", "rule")


@pytest.mark.anyio
async def test_check_all_passes_clean(anyio_backend):
    _recent_checks.clear()
    with patch("safety_kernel.assert_not_killed", new_callable=AsyncMock) as mock_kill, patch(
        "safety_kernel.assert_daily_loss_not_locked", new_callable=AsyncMock
    ) as mock_daily:
        await check_all("AAPL", "BUY", 1, "rule", account_equity=10000, price_estimate=100, stop_price=98.0)
    mock_kill.assert_awaited_once()
    mock_daily.assert_awaited_once_with(is_exit=False)


@pytest.mark.anyio
async def test_check_all_rejects_killed(anyio_backend):
    _recent_checks.clear()
    with patch(
        "safety_kernel.assert_not_killed",
        new_callable=AsyncMock,
        side_effect=SafetyViolation("Kill switch"),
    ):
        with pytest.raises(SafetyViolation, match="Kill switch"):
            await check_all("AAPL", "BUY", 1, "rule")


@pytest.mark.anyio
async def test_check_all_exit_skips_authority_risk_and_dedup(anyio_backend):
    _recent_checks.clear()
    with patch("safety_kernel.assert_not_killed", new_callable=AsyncMock) as mock_kill, patch(
        "safety_kernel.assert_daily_loss_not_locked", new_callable=AsyncMock
    ) as mock_daily, patch("safety_kernel.assert_risk_budget", new=Mock()) as mock_risk, patch(
        "safety_kernel.assert_not_duplicate", new=Mock()
    ) as mock_dup:
        await check_all(
            "AAPL",
            "SELL",
            10,
            "rule",
            account_equity=10000,
            price_estimate=100,
            is_exit=True,
            has_existing_position=True,
        )
    mock_kill.assert_not_awaited()
    mock_daily.assert_not_awaited()
    mock_risk.assert_not_called()
    mock_dup.assert_not_called()


@pytest.mark.anyio
async def test_check_all_manual_bypasses_authority_but_keeps_common_entry_guards(anyio_backend):
    _recent_checks.clear()
    with patch("safety_kernel.assert_not_killed", new_callable=AsyncMock) as mock_kill, patch(
        "safety_kernel.assert_daily_loss_not_locked", new_callable=AsyncMock
    ) as mock_daily, patch("safety_kernel.assert_risk_budget", new=Mock()) as mock_risk, patch(
        "safety_kernel.assert_not_duplicate", new=Mock()
    ) as mock_dup:
        await check_all(
            "AAPL",
            "BUY",
            2,
            "manual",
            account_equity=10000,
            price_estimate=100,
            require_autopilot_authority=False,
        )
    mock_kill.assert_not_awaited()
    mock_daily.assert_not_awaited()
    mock_risk.assert_called_once()
    mock_dup.assert_called_once_with("AAPL", "BUY", "manual")

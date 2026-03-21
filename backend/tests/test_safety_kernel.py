"""Safety Kernel tests — non-negotiable runtime checks."""
import pytest
import time
from unittest.mock import patch, AsyncMock

from safety_kernel import (
    assert_not_killed,
    assert_no_shorts,
    assert_risk_budget,
    assert_not_duplicate,
    check_all,
    SafetyViolation,
    _recent_checks,
)


# ── Kill Switch ──────────────────────────────────────────────────────────────

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
        await assert_not_killed()  # should not raise


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
        from safety_kernel import assert_daily_loss_not_locked

        with pytest.raises(SafetyViolation, match="Daily loss lock"):
            await assert_daily_loss_not_locked(is_exit=False)


@pytest.mark.anyio
async def test_daily_loss_lock_allows_exits(anyio_backend):
    from safety_kernel import assert_daily_loss_not_locked

    await assert_daily_loss_not_locked(is_exit=True)


# ── Risk Budget ──────────────────────────────────────────────────────────────

def test_risk_budget_rejects_oversized():
    with patch("safety_kernel.cfg") as mock_cfg:
        mock_cfg.RISK_PER_TRADE_PCT = 1.0
        # No stop_price → uses notional: 100 * 100 = $10,000 > 1% of $10,000 = $100
        with pytest.raises(SafetyViolation, match="risk"):
            assert_risk_budget(quantity=100, price_estimate=100.0, account_equity=10000.0)


def test_risk_budget_allows_small_position():
    with patch("safety_kernel.cfg") as mock_cfg:
        mock_cfg.RISK_PER_TRADE_PCT = 1.0
        # 1 share at $100 with stop at $99 → risk = $1, 1% of $10,000 = $100 → allowed
        assert_risk_budget(quantity=1, price_estimate=100.0, account_equity=10000.0, stop_price=99.0)


def test_risk_budget_rejects_with_wide_stop():
    with patch("safety_kernel.cfg") as mock_cfg:
        mock_cfg.RISK_PER_TRADE_PCT = 1.0
        # 10 shares at $100 with stop at $50 → risk = $500, 1% of $10,000 = $100 → rejected
        with pytest.raises(SafetyViolation, match="risk"):
            assert_risk_budget(quantity=10, price_estimate=100.0, account_equity=10000.0, stop_price=50.0)


def test_risk_budget_skips_when_no_data():
    with patch("safety_kernel.cfg") as mock_cfg:
        mock_cfg.RISK_PER_TRADE_PCT = 1.0
        assert_risk_budget(quantity=100, price_estimate=100.0, account_equity=0)  # no equity → skip


# ── No Shorts ────────────────────────────────────────────────────────────────

def test_no_shorts_allows_buy():
    assert_no_shorts("BUY")  # should not raise


def test_no_shorts_allows_sell_exit():
    assert_no_shorts("SELL", is_exit=True)  # exit → allowed


def test_no_shorts_allows_sell_with_position():
    assert_no_shorts("SELL", has_existing_position=True)  # closing position → allowed


def test_no_shorts_blocks_sell_open():
    with pytest.raises(SafetyViolation, match="Short"):
        assert_no_shorts("SELL", is_exit=False, has_existing_position=False)


# ── Dedup ────────────────────────────────────────────────────────────────────

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


# ── Full Check ───────────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_check_all_passes_clean(anyio_backend):
    _recent_checks.clear()
    with patch("safety_kernel.assert_not_killed", new_callable=AsyncMock), \
         patch("safety_kernel.assert_daily_loss_not_locked", new_callable=AsyncMock):
        await check_all("AAPL", "BUY", 1, "rule", account_equity=10000, price_estimate=100, stop_price=98.0)


@pytest.mark.anyio
async def test_check_all_rejects_killed(anyio_backend):
    _recent_checks.clear()
    with patch("safety_kernel.assert_not_killed", new_callable=AsyncMock, side_effect=SafetyViolation("Kill switch")):
        with pytest.raises(SafetyViolation, match="Kill switch"):
            await check_all("AAPL", "BUY", 1, "rule")

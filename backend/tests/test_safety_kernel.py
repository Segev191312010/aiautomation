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

def test_kill_switch_blocks():
    with patch("safety_kernel.cfg") as mock_cfg:
        mock_cfg.AUTOPILOT_MODE = "KILLED"
        with pytest.raises(SafetyViolation, match="Kill switch"):
            assert_not_killed()


def test_kill_switch_allows_when_live():
    with patch("safety_kernel.cfg") as mock_cfg:
        mock_cfg.AUTOPILOT_MODE = "LIVE"
        assert_not_killed()  # should not raise


def test_kill_switch_allows_when_off():
    with patch("safety_kernel.cfg") as mock_cfg:
        mock_cfg.AUTOPILOT_MODE = "OFF"
        assert_not_killed()  # should not raise


# ── Risk Budget ──────────────────────────────────────────────────────────────

def test_risk_budget_rejects_oversized():
    with patch("safety_kernel.cfg") as mock_cfg:
        mock_cfg.RISK_PER_TRADE_PCT = 1.0
        # 100 shares at $100 = $10,000 on a $10,000 account = 100% → blocked
        with pytest.raises(SafetyViolation, match="exceeds 20%"):
            assert_risk_budget(quantity=100, price_estimate=100.0, account_equity=10000.0)


def test_risk_budget_allows_small_position():
    with patch("safety_kernel.cfg") as mock_cfg:
        mock_cfg.RISK_PER_TRADE_PCT = 1.0
        # 5 shares at $100 = $500 on $10,000 = 5% → allowed
        assert_risk_budget(quantity=5, price_estimate=100.0, account_equity=10000.0)


def test_risk_budget_skips_when_no_data():
    with patch("safety_kernel.cfg") as mock_cfg:
        mock_cfg.RISK_PER_TRADE_PCT = 1.0
        # No equity data — can't validate, allow
        assert_risk_budget(quantity=100, price_estimate=100.0, account_equity=0)


# ── No Shorts ────────────────────────────────────────────────────────────────

def test_no_shorts_allows_buy():
    assert_no_shorts("BUY")  # should not raise


def test_no_shorts_allows_sell_exit():
    # SELL for exit is allowed — caller must verify it's an exit
    assert_no_shorts("SELL")  # should not raise (validated at caller level)


# ── Dedup ────────────────────────────────────────────────────────────────────

def test_dedup_blocks_rapid_fire():
    _recent_checks.clear()
    assert_not_duplicate("AAPL", "BUY", "rule")  # first call — OK
    with pytest.raises(SafetyViolation, match="Duplicate"):
        assert_not_duplicate("AAPL", "BUY", "rule")  # second call — blocked


def test_dedup_allows_different_symbols():
    _recent_checks.clear()
    assert_not_duplicate("AAPL", "BUY", "rule")
    assert_not_duplicate("TSLA", "BUY", "rule")  # different symbol — OK


def test_dedup_allows_after_window():
    _recent_checks.clear()
    _recent_checks["AAPL:BUY:rule"] = time.time() - 20  # 20s ago — past window
    assert_not_duplicate("AAPL", "BUY", "rule")  # should not raise


# ── Full Check ───────────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_check_all_passes_clean(anyio_backend):
    _recent_checks.clear()
    with patch("safety_kernel.cfg") as mock_cfg:
        mock_cfg.AUTOPILOT_MODE = "LIVE"
        mock_cfg.RISK_PER_TRADE_PCT = 1.0
        with patch("safety_kernel.assert_daily_loss_not_locked", new_callable=AsyncMock):
            await check_all("AAPL", "BUY", 5, "rule", account_equity=10000, price_estimate=100)


@pytest.mark.anyio
async def test_check_all_rejects_killed(anyio_backend):
    _recent_checks.clear()
    with patch("safety_kernel.cfg") as mock_cfg:
        mock_cfg.AUTOPILOT_MODE = "KILLED"
        with pytest.raises(SafetyViolation, match="Kill switch"):
            await check_all("AAPL", "BUY", 5, "rule")

"""Portfolio concentration enforcement tests — Phase 1 hardening."""
from __future__ import annotations

import math
import pytest
from risk_manager import (
    PortfolioImpactResult,
    check_portfolio_impact,
    compute_current_sector_exposure,
    _count_correlated_positions,
)
from risk_config import RiskLimits


NET_LIQ = 100_000.0


def _pos(symbol: str, value: float) -> dict:
    return {"symbol": symbol, "market_value": value}


def _corr_matrix(symbols: list[str], matrix: list[list[float]]) -> dict:
    return {"symbols": symbols, "matrix": matrix}


# ── Sector exposure helper ────────────────────────────────────────────────


def test_sector_exposure_computes_correctly():
    positions = [_pos("AAPL", 15_000), _pos("MSFT", 15_000), _pos("JPM", 15_000)]
    exp = compute_current_sector_exposure(positions, NET_LIQ)
    assert exp["Tech"] == 30.0
    assert exp["Finance"] == 15.0


def test_sector_exposure_empty_positions():
    assert compute_current_sector_exposure([], NET_LIQ) == {}


def test_sector_exposure_zero_net_liq():
    assert compute_current_sector_exposure([_pos("AAPL", 10_000)], 0) == {}


# ── Correlation count helper ──────────────────────────────────────────────


def test_correlation_count_ignores_self():
    mat = _corr_matrix(["AAPL", "MSFT"], [[1.0, 0.95], [0.95, 1.0]])
    assert _count_correlated_positions("AAPL", ["AAPL", "MSFT"], mat, 0.80) == 1


def test_correlation_count_below_threshold():
    mat = _corr_matrix(["AAPL", "JPM"], [[1.0, 0.30], [0.30, 1.0]])
    assert _count_correlated_positions("AAPL", ["JPM"], mat, 0.80) == 0


def test_correlation_count_ignores_nan():
    mat = _corr_matrix(["AAPL", "MSFT", "GOOGL"], [
        [1.0, float("nan"), 0.90],
        [float("nan"), 1.0, 0.85],
        [0.90, 0.85, 1.0],
    ])
    assert _count_correlated_positions("AAPL", ["MSFT", "GOOGL"], mat, 0.80) == 1


def test_correlation_count_missing_symbol():
    mat = _corr_matrix(["AAPL", "MSFT"], [[1.0, 0.90], [0.90, 1.0]])
    assert _count_correlated_positions("XYZ", ["AAPL"], mat, 0.80) == 0


def test_correlation_count_empty_held():
    mat = _corr_matrix(["AAPL"], [[1.0]])
    assert _count_correlated_positions("AAPL", [], mat, 0.80) == 0


# ── check_portfolio_impact — sell always passes ───────────────────────────


def test_sell_always_passes():
    positions = [_pos("AAPL", 50_000)]
    result = check_portfolio_impact("AAPL", "SELL", positions, NET_LIQ)
    assert result.allowed is True
    assert result.reason == "sell_exit"


def test_sell_exit_passes():
    result = check_portfolio_impact("TSLA", "SELL_EXIT", [], NET_LIQ)
    assert result.allowed is True
    assert result.reason == "sell_exit"


# ── check_portfolio_impact — sector enforcement ──────────────────────────


def test_sector_blocks_over_30pct():
    positions = [_pos("AAPL", 14_000), _pos("MSFT", 14_000)]
    result = check_portfolio_impact(
        "NVDA", "BUY", positions, NET_LIQ, candidate_notional=7_000,
    )
    assert result.allowed is False
    assert result.reason == "sector_limit"
    assert result.sector == "Tech"
    assert result.sector_weight_after is not None
    assert result.sector_weight_after > 30.0


def test_sector_passes_under_limit():
    positions = [_pos("AAPL", 10_000), _pos("MSFT", 10_000)]
    result = check_portfolio_impact(
        "GOOGL", "BUY", positions, NET_LIQ, candidate_notional=5_000,
    )
    assert result.allowed is True


def test_sector_boundary_exact_30pct_passes():
    positions = [_pos("AAPL", 25_000)]
    result = check_portfolio_impact(
        "MSFT", "BUY", positions, NET_LIQ, candidate_notional=5_000,
    )
    assert result.allowed is True


def test_sector_boundary_just_over_blocks():
    positions = [_pos("AAPL", 25_000)]
    result = check_portfolio_impact(
        "MSFT", "BUY", positions, NET_LIQ, candidate_notional=5_001,
    )
    assert result.allowed is False
    assert result.reason == "sector_limit"


# ── check_portfolio_impact — correlation enforcement ──────────────────────


def test_correlation_blocks_3_correlated():
    positions = [_pos("AAPL", 5_000), _pos("MSFT", 5_000), _pos("GOOGL", 5_000)]
    mat = _corr_matrix(
        ["AAPL", "MSFT", "GOOGL", "NVDA"],
        [
            [1.0, 0.90, 0.85, 0.92],
            [0.90, 1.0, 0.88, 0.91],
            [0.85, 0.88, 1.0, 0.87],
            [0.92, 0.91, 0.87, 1.0],
        ],
    )
    result = check_portfolio_impact(
        "NVDA", "BUY", positions, NET_LIQ,
        candidate_notional=5_000, corr_matrix=mat,
    )
    assert result.allowed is False
    assert result.reason == "correlation_limit"
    assert result.correlated_count == 3


def test_correlation_passes_under_limit():
    positions = [_pos("AAPL", 5_000), _pos("JPM", 5_000)]
    mat = _corr_matrix(
        ["AAPL", "JPM", "MSFT"],
        [
            [1.0, 0.30, 0.90],
            [0.30, 1.0, 0.25],
            [0.90, 0.25, 1.0],
        ],
    )
    result = check_portfolio_impact(
        "MSFT", "BUY", positions, NET_LIQ,
        candidate_notional=5_000, corr_matrix=mat,
    )
    assert result.allowed is True
    assert result.correlated_count == 1


def test_custom_correlation_limit():
    positions = [_pos("AAPL", 5_000)]
    mat = _corr_matrix(["AAPL", "MSFT"], [[1.0, 0.90], [0.90, 1.0]])
    limits = RiskLimits(max_correlated_positions=1)
    result = check_portfolio_impact(
        "MSFT", "BUY", positions, NET_LIQ,
        candidate_notional=5_000, corr_matrix=mat, limits=limits,
    )
    assert result.allowed is False
    assert result.reason == "correlation_limit"


# ── check_portfolio_impact — degraded/error states ────────────────────────


def test_missing_corr_matrix_returns_degraded_skip():
    positions = [_pos("AAPL", 10_000)]
    result = check_portfolio_impact(
        "MSFT", "BUY", positions, NET_LIQ,
        candidate_notional=5_000, corr_matrix=None,
    )
    assert result.allowed is True
    assert "degraded" in result.reason


def test_missing_sector_returns_degraded_skip():
    positions = [_pos("AAPL", 10_000)]
    result = check_portfolio_impact(
        "XYZABC", "BUY", positions, NET_LIQ,
        candidate_notional=5_000, corr_matrix=None,
    )
    assert result.allowed is True
    assert "degraded" in result.reason


def test_unknown_sector_still_checks_correlation():
    positions = [_pos("AAPL", 5_000)]
    mat = _corr_matrix(["AAPL", "XYZABC"], [[1.0, 0.95], [0.95, 1.0]])
    limits = RiskLimits(max_correlated_positions=1)
    result = check_portfolio_impact(
        "XYZABC", "BUY", positions, NET_LIQ,
        candidate_notional=5_000, corr_matrix=mat, limits=limits,
    )
    assert result.allowed is False
    assert result.reason == "correlation_limit"


def test_error_skip_on_exception():
    result = check_portfolio_impact(
        "AAPL", "BUY",
        positions="not_a_list",  # type: ignore
        net_liq=NET_LIQ,
        candidate_notional=5_000,
    )
    assert result.allowed is True
    assert result.reason == "error_skip"


# ── check_portfolio_impact — approved candidates in same cycle ────────────


def test_approved_candidates_counted_in_exposure():
    positions = [_pos("AAPL", 20_000)]
    approved = [{"symbol": "GOOGL", "market_value": 10_000, "side": "BUY"}]
    result = check_portfolio_impact(
        "NVDA", "BUY", positions, NET_LIQ,
        candidate_notional=5_000,
        approved_candidates=approved,
    )
    assert result.allowed is False
    assert result.reason == "sector_limit"


def test_approved_sell_candidates_not_counted():
    positions = [_pos("AAPL", 20_000)]
    approved = [{"symbol": "GOOGL", "market_value": 10_000, "side": "SELL"}]
    result = check_portfolio_impact(
        "NVDA", "BUY", positions, NET_LIQ,
        candidate_notional=5_000,
        approved_candidates=approved,
    )
    assert result.allowed is True


# ── Custom limits ─────────────────────────────────────────────────────────


def test_custom_sector_limit_tight():
    positions = [_pos("AAPL", 8_000)]
    limits = RiskLimits(max_sector_pct=10.0)
    result = check_portfolio_impact(
        "MSFT", "BUY", positions, NET_LIQ,
        candidate_notional=3_000, limits=limits,
    )
    assert result.allowed is False
    assert result.reason == "sector_limit"


def test_custom_sector_limit_lenient():
    positions = [_pos("AAPL", 40_000)]
    limits = RiskLimits(max_sector_pct=50.0)
    result = check_portfolio_impact(
        "MSFT", "BUY", positions, NET_LIQ,
        candidate_notional=5_000, limits=limits,
    )
    assert result.allowed is True

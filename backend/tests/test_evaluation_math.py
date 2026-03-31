"""Pure math tests for Stage 10 evaluation helpers."""
from evaluation_math import (
    bucket_confidence,
    compute_abstain_rate,
    compute_avg_confidence,
    compute_calibration_error,
    compute_coverage,
    compute_expectancy,
    compute_hit_rate,
    compute_max_drawdown_pct_from_pnls,
    compute_net_pnl,
    empty_slice_metrics,
)


def test_compute_hit_rate_empty_returns_none():
    assert compute_hit_rate([]) is None


def test_compute_hit_rate_mixed():
    assert compute_hit_rate([10.0, -5.0, 7.0, 0.0]) == 0.5


def test_compute_net_pnl_sum():
    assert compute_net_pnl([10.0, -5.0, 2.5]) == 7.5


def test_compute_expectancy_requires_min_samples():
    assert compute_expectancy([10.0, -5.0], min_samples=3) is None


def test_compute_expectancy_matches_existing():
    assert compute_expectancy([10.0, -5.0, 20.0, -5.0], min_samples=3) == 5.0


def test_compute_max_drawdown_pct_from_pnls():
    assert compute_max_drawdown_pct_from_pnls([10.0, -5.0, 20.0, -10.0]) == 50.0


def test_compute_max_drawdown_empty_returns_none():
    assert compute_max_drawdown_pct_from_pnls([]) is None


def test_compute_max_drawdown_no_peak_returns_zero():
    # All-negative PnL series never reaches a positive peak, so drawdown is 0.0 (not None)
    assert compute_max_drawdown_pct_from_pnls([-5.0, 0.0, -1.0]) == 0.0


def test_compute_coverage_basic():
    assert compute_coverage(5, 3) == 0.6


def test_compute_abstain_rate_basic():
    assert compute_abstain_rate(5, 2) == 0.4


def test_compute_avg_confidence_ignores_none():
    items = [{"confidence": 0.2}, {"confidence": None}, {"confidence": 0.8}]
    assert compute_avg_confidence(items) == 0.5


def test_compute_calibration_error_matches_existing():
    scored_items = [
        {"confidence": 0.8},
        {"confidence": 0.7},
        {"confidence": None},
    ]
    assert compute_calibration_error(scored_items, 0.5) == 0.25


def test_empty_slice_metrics_shape():
    assert empty_slice_metrics() == {
        "count": 0,
        "scored_count": 0,
        "hit_rate": None,
        "net_pnl": None,
        "expectancy": None,
        "max_drawdown": None,
        "coverage": None,
        "abstain_rate": None,
        "avg_confidence": None,
        "calibration_error": None,
    }


def test_expectancy_breakeven_not_counted_as_loss():
    """Break-even trades (pnl=0.0) should not inflate loss count or deflate expectancy."""
    # [10, 0, -10]: 1 win, 1 loss, 1 break-even
    result = compute_expectancy([10.0, 0.0, -10.0], min_samples=3)
    # win_rate=1/3, loss_rate=1/3, avg_win=10, avg_loss=10
    # expectancy = (1/3)*10 - (1/3)*10 = 0.0
    assert result is not None
    assert abs(result - 0.0) < 0.001


def test_max_drawdown_monotonic_up_returns_zero():
    """Monotonically increasing PnL series should have 0.0 drawdown, not None."""
    assert compute_max_drawdown_pct_from_pnls([5.0, 3.0, 2.0]) == 0.0


def test_bucket_confidence_boundaries():
    assert bucket_confidence(None) == "0.0-0.1"
    assert bucket_confidence(0.0) == "0.0-0.1"
    assert bucket_confidence(0.099) == "0.0-0.1"
    assert bucket_confidence(0.1) == "0.1-0.2"
    assert bucket_confidence(0.999) == "0.9-1.0"
    assert bucket_confidence(1.0) == "0.9-1.0"

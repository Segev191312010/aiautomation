"""Regression tests for replay_scoring — match key logic and edge cases."""
from replay_scoring import score_candidate_item_against_historical


def _baseline(item_type, action_name, symbol, target_key, realized_pnl):
    return {
        "id": f"{symbol}-{action_name}",
        "item_type": item_type,
        "action_name": action_name,
        "symbol": symbol,
        "target_key": target_key,
        "realized_pnl": realized_pnl,
        "score_status": "direct_realized",
    }


def test_exact_match_scores():
    """Candidate matching all 4 keys inherits baseline PnL."""
    baseline = [_baseline("direct_trade", "BUY", "AAPL", None, 150.0)]
    candidate = {"item_type": "direct_trade", "action_name": "BUY", "symbol": "AAPL", "target_key": None}

    result = score_candidate_item_against_historical(candidate, baseline)
    assert result["score_status"] == "direct_realized"
    assert result["realized_pnl"] == 150.0


def test_symbol_mismatch_no_match():
    """Different symbols must not match."""
    baseline = [_baseline("direct_trade", "BUY", "AAPL", None, 150.0)]
    candidate = {"item_type": "direct_trade", "action_name": "BUY", "symbol": "MSFT", "target_key": None}

    result = score_candidate_item_against_historical(candidate, baseline)
    assert result["score_status"] == "unscored"


def test_candidate_missing_symbol_does_not_match_baseline_with_symbol():
    """Candidate with symbol=None must NOT match baseline with symbol=AAPL.
    This was bug #2: falsy candidate key was silently skipped."""
    baseline = [_baseline("direct_trade", "BUY", "AAPL", None, 150.0)]
    candidate = {"item_type": "direct_trade", "action_name": "BUY", "symbol": None, "target_key": None}

    result = score_candidate_item_against_historical(candidate, baseline)
    assert result["score_status"] == "unscored"


def test_both_sides_none_symbol_matches():
    """When both candidate and baseline have symbol=None (e.g. score_threshold), match by other keys."""
    baseline = [_baseline("score_threshold", "adjust", None, "min_score", 0.0)]
    candidate = {"item_type": "score_threshold", "action_name": "adjust", "symbol": None, "target_key": "min_score"}

    result = score_candidate_item_against_historical(candidate, baseline)
    assert result["realized_pnl"] == 0.0


def test_target_key_mismatch_no_match():
    """Different target_key must not match even if other keys align."""
    baseline = [_baseline("score_threshold", "adjust", None, "min_score", 10.0)]
    candidate = {"item_type": "score_threshold", "action_name": "adjust", "symbol": None, "target_key": "risk_multiplier"}

    result = score_candidate_item_against_historical(candidate, baseline)
    assert result["score_status"] == "unscored"


def test_no_baselines_returns_unscored():
    """Empty baseline list returns unscored."""
    candidate = {"item_type": "direct_trade", "action_name": "BUY", "symbol": "AAPL"}
    result = score_candidate_item_against_historical(candidate, [])
    assert result["score_status"] == "unscored"

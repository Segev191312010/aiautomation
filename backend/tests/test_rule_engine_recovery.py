"""
Robustness tests for rule_engine.

Covers two live-evaluation hardening guarantees:
  (a) The cross-cycle indicator cache is cleared at the start of every
      evaluate_all pass (prevents unbounded memory growth / stale series).
  (b) A rule with a malformed/empty last_triggered timestamp must not crash
      the evaluate_all cycle; it is treated as "never triggered" (cooldown
      ignored) — mirroring the sibling _check_symbol_cooldown guard.
"""
from __future__ import annotations

import pandas as pd

import rule_engine
from models import Rule


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _rising_df(n: int = 30, base: float = 100.0) -> pd.DataFrame:
    """A simple monotonically-rising OHLCV frame; last close = base + n - 1."""
    idx = pd.date_range("2024-01-01", periods=n, freq="D")
    close = [base + i for i in range(n)]
    return pd.DataFrame(
        {
            "open": close,
            "high": [c + 1 for c in close],
            "low": [c - 1 for c in close],
            "close": close,
            "volume": [1_000_000] * n,
        },
        index=idx,
    )


def _always_fire_rule(*, last_triggered, name: str = "Recovery Rule") -> Rule:
    """An enabled single-symbol rule whose single condition (PRICE > 50) is
    trivially satisfied by _rising_df (last close >> 50)."""
    return Rule(
        name=name,
        symbol="AAPL",
        enabled=True,
        conditions=[
            {"indicator": "PRICE", "params": {}, "operator": ">", "value": 50.0},
        ],
        logic="AND",
        action={"type": "BUY", "asset_type": "STK", "quantity": 1, "order_type": "MKT"},
        cooldown_minutes=60,
        status="active",
        last_triggered=last_triggered,
    )


# ---------------------------------------------------------------------------
# (a) Cache cleared per cycle
# ---------------------------------------------------------------------------

def test_clear_indicator_cache_empties_the_global_cache():
    rule_engine._indicator_cache[("sentinel-key",)] = object()
    assert rule_engine._indicator_cache  # non-empty
    rule_engine.clear_indicator_cache()
    assert rule_engine._indicator_cache == {}


def test_evaluate_all_clears_stale_cache_at_start_of_cycle():
    # Seed the cross-cycle cache with a stale sentinel from a "previous" cycle.
    rule_engine._indicator_cache[("stale", "prev-cycle")] = "STALE"
    assert ("stale", "prev-cycle") in rule_engine._indicator_cache

    # Running a cycle (even with no rules) must clear the stale entry up front.
    rule_engine.evaluate_all(rules=[], bars_by_symbol={})

    assert ("stale", "prev-cycle") not in rule_engine._indicator_cache


def test_evaluate_all_does_not_leak_keys_across_cycles():
    df = _rising_df()
    rule = _always_fire_rule(last_triggered=None)

    rule_engine.evaluate_all([rule], {"AAPL": df})
    after_first = len(rule_engine._indicator_cache)
    assert after_first > 0  # the PRICE series was cached within the cycle

    # A second cycle must start fresh (clear) rather than accumulate keys.
    rule_engine.evaluate_all([rule], {"AAPL": df})
    after_second = len(rule_engine._indicator_cache)
    assert after_second == after_first  # bounded, not growing


# ---------------------------------------------------------------------------
# (b) Malformed last_triggered does not crash + treated as never triggered
# ---------------------------------------------------------------------------

def test_malformed_last_triggered_does_not_crash_evaluate_all():
    df = _rising_df()
    bad_rule = _always_fire_rule(last_triggered="not-a-real-timestamp")
    # Must not raise; a malformed timestamp previously aborted the whole pass.
    fired = rule_engine.evaluate_all([bad_rule], {"AAPL": df})
    assert isinstance(fired, list)


def test_malformed_last_triggered_evaluates_as_never_triggered():
    df = _rising_df()
    bad_rule = _always_fire_rule(last_triggered="garbage-2024")
    fired = rule_engine.evaluate_all([bad_rule], {"AAPL": df})
    # Cooldown is ignored (never triggered) so the satisfied condition fires.
    assert (bad_rule, "AAPL") in fired


def test_empty_last_triggered_evaluates_as_never_triggered():
    df = _rising_df()
    rule = _always_fire_rule(last_triggered="")
    fired = rule_engine.evaluate_all([rule], {"AAPL": df})
    assert (rule, "AAPL") in fired


def test_evaluate_rule_malformed_timestamp_ignores_cooldown():
    df = _rising_df()
    bad_rule = _always_fire_rule(last_triggered="2024-99-99T99:99:99")
    # Direct single-rule path must also survive and treat as never triggered.
    assert rule_engine.evaluate_rule(bad_rule, df) is True


def test_one_bad_rule_does_not_block_sibling_rules():
    # A malformed-timestamp rule earlier in the list must not abort evaluation
    # of a later, well-formed rule in the same cycle.
    df = _rising_df()
    bad_rule = _always_fire_rule(last_triggered="@@@bad@@@", name="Bad")
    good_rule = _always_fire_rule(last_triggered=None, name="Good")
    fired = rule_engine.evaluate_all([bad_rule, good_rule], {"AAPL": df})
    fired_names = {r.name for r, _sym in fired}
    assert "Good" in fired_names
    assert "Bad" in fired_names

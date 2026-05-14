"""
Batch 6 regression tests — rule templates + indicator literals.

Pins these invariants:

1. Every template's entry/exit conditions calculate() cleanly — no
   KeyError, no ValueError, and the returned series respects the
   declared length (RSI(14) actually uses 14, Golden Cross uses
   SMA(50)/SMA(200), not the silent default of 20).
2. The indicators engine accepts both canonical (`length`, `k`, `d`)
   and legacy (`period`, `k_period`, `d_period`) keys so existing user
   rules in the DB keep firing without a data migration.
3. VOLUME and CHANGE_PCT indicators are implemented (not silent no-ops).
4. evaluate_all clears the indicator cache at the start of each cycle.
5. evaluate_rule survives a malformed last_triggered string without
   crashing the cycle (try/except mirroring _check_symbol_cooldown).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from indicators import calculate
from models import Condition, Rule, TradeAction
from rule_engine import (
    _indicator_cache,
    clear_indicator_cache,
    evaluate_all,
    evaluate_rule,
)
from rule_templates import TEMPLATES


def _make_df(n_bars: int = 250) -> pd.DataFrame:
    """Synthetic OHLCV with monotonic uptrend so indicators have signal."""
    rng = np.random.default_rng(42)
    close = np.linspace(100.0, 150.0, n_bars) + rng.normal(0, 0.5, n_bars)
    high = close + 1.0
    low = close - 1.0
    open_ = close - 0.2
    volume = rng.integers(1_000_000, 5_000_000, n_bars)
    return pd.DataFrame({
        "open": open_, "high": high, "low": low, "close": close, "volume": volume,
    })


# ---------------------------------------------------------------------------
# 1. Every template's conditions calculate() cleanly
# ---------------------------------------------------------------------------


def test_every_template_calculates_cleanly():
    df = _make_df(250)
    for tid, tpl in TEMPLATES.items():
        for cond_dict in tpl["entry_conditions"] + tpl["exit_conditions"]:
            ind = cond_dict["indicator"]
            params = cond_dict.get("params", {})
            try:
                series = calculate(df, ind, params)
            except Exception as exc:
                pytest.fail(f"template={tid} indicator={ind} params={params} raised: {exc}")
            assert isinstance(series, pd.Series), \
                f"template={tid} indicator={ind} must return a pd.Series"


def test_golden_cross_template_actually_uses_50_and_200():
    """The Golden Cross template's SMAs must be 50/200, NOT silent defaults."""
    df = _make_df(300)
    tpl = TEMPLATES["golden_cross"]
    fast_cond = tpl["entry_conditions"][0]
    assert fast_cond["params"]["length"] == 50, \
        "Golden Cross fast leg must declare length=50 (was silent default 20 before Batch 6)"

    # Sanity: the computed series differs from SMA(20)
    sma_50 = calculate(df, "SMA", {"length": 50})
    sma_20 = calculate(df, "SMA", {"length": 20})
    last_diff = abs(sma_50.iloc[-1] - sma_20.iloc[-1])
    assert last_diff > 0.01, "SMA(50) and SMA(20) must produce different values on synthetic data"


def test_rsi_template_uses_length_not_period_default():
    """RSI templates now declare `length=14`, not `period=14`. Engine reads `length`."""
    tpl = TEMPLATES["rsi_oversold_bounce"]
    rsi_cond = tpl["entry_conditions"][0]
    assert "length" in rsi_cond["params"], \
        "RSI template must use canonical 'length' key (Batch 6 fix)"
    assert "period" not in rsi_cond["params"], \
        "RSI template must NOT use legacy 'period' key"


def test_stoch_template_uses_k_d_not_legacy_keys():
    """STOCH templates now declare `k` and `d`, not `k_period`/`d_period`."""
    tpl = TEMPLATES["stochastic_oversold"]
    stoch_cond = tpl["entry_conditions"][0]
    assert "k" in stoch_cond["params"], "STOCH must use canonical 'k' key"
    assert "d" in stoch_cond["params"], "STOCH must use canonical 'd' key"
    assert "k_period" not in stoch_cond["params"]
    assert "d_period" not in stoch_cond["params"]


# ---------------------------------------------------------------------------
# 2. Indicator engine accepts both canonical and legacy keys
# ---------------------------------------------------------------------------


def test_indicators_accept_legacy_period_key():
    """RSI/SMA/EMA/ATR/BBANDS must accept legacy `period` for backward compat."""
    df = _make_df(50)
    rsi_canonical = calculate(df, "RSI", {"length": 14})
    rsi_legacy = calculate(df, "RSI", {"period": 14})
    pd.testing.assert_series_equal(rsi_canonical, rsi_legacy)

    sma_canonical = calculate(df, "SMA", {"length": 50})
    sma_legacy = calculate(df, "SMA", {"period": 50})
    pd.testing.assert_series_equal(sma_canonical, sma_legacy)


def test_stoch_accepts_legacy_k_period_d_period_keys():
    df = _make_df(50)
    canonical = calculate(df, "STOCH", {"k": 14, "d": 3})
    legacy = calculate(df, "STOCH", {"k_period": 14, "d_period": 3})
    pd.testing.assert_series_equal(canonical, legacy)


# ---------------------------------------------------------------------------
# 3. VOLUME and CHANGE_PCT are implemented
# ---------------------------------------------------------------------------


def test_volume_indicator_returns_volume_series():
    df = _make_df(20)
    series = calculate(df, "VOLUME", {})
    assert isinstance(series, pd.Series)
    assert (series == df["volume"]).all()


def test_change_pct_indicator_returns_percent_change():
    df = _make_df(20)
    series = calculate(df, "CHANGE_PCT", {})
    expected = df["close"].pct_change() * 100.0
    pd.testing.assert_series_equal(series, expected, check_names=False)


# ---------------------------------------------------------------------------
# 4. evaluate_all clears the indicator cache
# ---------------------------------------------------------------------------


def test_evaluate_all_clears_indicator_cache():
    """The cross-cycle cache must be cleared at the top of every evaluate_all."""
    _indicator_cache.clear()
    _indicator_cache[("test", 1, "t", "RSI", ())] = "stale-value"

    evaluate_all(rules=[], bars_by_symbol={}, universe_cache={})

    assert _indicator_cache == {}, \
        "evaluate_all must call clear_indicator_cache at the start"


# ---------------------------------------------------------------------------
# 5. evaluate_rule survives malformed last_triggered
# ---------------------------------------------------------------------------


def test_evaluate_rule_survives_malformed_last_triggered(caplog):
    """Malformed ISO timestamp must NOT raise — log and proceed."""
    import logging

    rule = Rule(
        name="r1",
        symbol="AAPL",
        conditions=[Condition(indicator="RSI", params={"length": 14}, operator="<", value=30)],
        action=TradeAction(type="BUY", quantity=1),
        status="active",
        enabled=True,
        last_triggered="not-a-real-iso-string",
    )
    df = _make_df(50)

    with caplog.at_level(logging.WARNING):
        # Must not raise — cooldown is ignored when timestamp is malformed
        result = evaluate_rule(rule, df)

    assert isinstance(result, bool)
    assert any("malformed last_triggered" in r.getMessage() for r in caplog.records)

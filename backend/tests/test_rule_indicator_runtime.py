"""Runtime indicator coverage for bot-evaluated custom rules."""
from __future__ import annotations

import pandas as pd

from indicators import calculate, resolve_value


def _bars() -> pd.DataFrame:
    close = pd.Series([100 + i for i in range(30)], dtype=float)
    return pd.DataFrame(
        {
            "open": close - 0.5,
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "volume": [1000 + i * 10 for i in range(30)],
        }
    )


def test_indicator_period_alias_matches_length():
    df = _bars()

    by_period = calculate(df, "SMA", {"period": 5})
    by_length = calculate(df, "SMA", {"length": 5})

    assert by_period.equals(by_length)


def test_volume_and_change_pct_are_supported_by_rule_engine_indicators():
    df = _bars()

    current_volume = calculate(df, "VOLUME", {})
    avg_volume = calculate(df, "VOLUME", {"length": 3})
    change_pct = calculate(df, "CHANGE_PCT", {})

    assert current_volume.iloc[-1] == df["volume"].iloc[-1]
    assert avg_volume.iloc[-1] == df["volume"].tail(3).mean()
    assert change_pct.iloc[-1] > 0


def test_bollinger_template_value_resolves_to_series():
    df = _bars()
    cache = {}

    upper = resolve_value("BBANDS_UPPER_20", df, cache)
    lower = resolve_value("BBANDS_LOWER_20", df, cache)

    assert isinstance(upper, pd.Series)
    assert isinstance(lower, pd.Series)
    assert upper.dropna().iloc[-1] > lower.dropna().iloc[-1]

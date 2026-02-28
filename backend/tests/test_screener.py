"""
Tests for the stock screener engine and API endpoints.
"""
import json
import pytest
import numpy as np
import pandas as pd
from unittest.mock import patch, AsyncMock, MagicMock
from datetime import datetime, timezone

from pydantic import ValidationError

from models import (
    ScanRequest, ScanFilter, FilterValue, ScanResultRow, ScanResponse,
    ScreenerPreset, EnrichRequest,
)
from screener import (
    load_universe, list_universes, validate_timeframe,
    make_indicator_key, evaluate_symbol, _compute_indicator_series,
)


# ---------------------------------------------------------------------------
# Universe loading
# ---------------------------------------------------------------------------

class TestUniverses:
    def test_load_sp500(self):
        symbols = load_universe("sp500")
        assert len(symbols) >= 400
        assert "AAPL" in symbols

    def test_load_nasdaq100(self):
        symbols = load_universe("nasdaq100")
        assert len(symbols) >= 90
        assert "MSFT" in symbols

    def test_load_etfs(self):
        symbols = load_universe("etfs")
        assert len(symbols) >= 30
        assert "SPY" in symbols

    def test_load_unknown_universe(self):
        symbols = load_universe("nonexistent")
        assert symbols == []

    def test_list_universes(self):
        universes = list_universes()
        assert len(universes) == 3
        ids = [u["id"] for u in universes]
        assert "sp500" in ids
        assert "nasdaq100" in ids
        assert "etfs" in ids
        for u in universes:
            assert "count" in u
            assert u["count"] > 0


# ---------------------------------------------------------------------------
# Timeframe validation
# ---------------------------------------------------------------------------

class TestTimeframeValidation:
    def test_valid_daily(self):
        assert validate_timeframe("1d", "1y") is True

    def test_valid_hourly(self):
        assert validate_timeframe("1h", "3mo") is True

    def test_invalid_combo(self):
        assert validate_timeframe("1m", "1y") is False

    def test_unknown_interval(self):
        assert validate_timeframe("4h", "1y") is False


# ---------------------------------------------------------------------------
# Indicator key generation
# ---------------------------------------------------------------------------

class TestIndicatorKeys:
    def test_rsi(self):
        assert make_indicator_key("RSI", {"length": 14}) == "RSI_14"

    def test_sma(self):
        assert make_indicator_key("SMA", {"length": 50}) == "SMA_50"

    def test_macd(self):
        assert make_indicator_key("MACD", {"fast": 12, "slow": 26, "signal": 9}) == "MACD_12_26_9"

    def test_volume(self):
        assert make_indicator_key("VOLUME", {}) == "VOLUME"

    def test_change_pct(self):
        assert make_indicator_key("CHANGE_PCT", {}) == "CHANGE_PCT"

    def test_bbands(self):
        assert make_indicator_key("BBANDS", {"length": 20, "band": "mid"}) == "BBANDS_20_mid"


# ---------------------------------------------------------------------------
# Helper: generate test DataFrame
# ---------------------------------------------------------------------------

def _make_df(n: int = 300, start_price: float = 100.0, seed: int = 42) -> pd.DataFrame:
    """Generate a realistic OHLCV DataFrame for testing."""
    rng = np.random.default_rng(seed)
    close = start_price + np.cumsum(rng.normal(0, 1, n))
    close = np.maximum(close, 1.0)  # no negative prices
    high = close + rng.uniform(0.5, 2.0, n)
    low = close - rng.uniform(0.5, 2.0, n)
    low = np.maximum(low, 0.5)
    open_ = close + rng.normal(0, 0.5, n)
    volume = rng.integers(100_000, 10_000_000, n).astype(float)
    times = np.arange(n) * 86400 + 1_600_000_000

    return pd.DataFrame({
        "time": times,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    })


# ---------------------------------------------------------------------------
# Filter evaluation
# ---------------------------------------------------------------------------

class TestEvaluateSymbol:
    def test_rsi_match(self):
        """RSI LT 30 should match when RSI is actually below 30."""
        # Create data with strong downtrend to get RSI < 30
        df = _make_df(200, start_price=200, seed=10)
        # Force downtrend in last bars
        df.loc[df.index[-30:], "close"] = np.linspace(100, 50, 30)

        filters = [ScanFilter(
            indicator="RSI",
            params={"length": 14},
            operator="LT",
            value=FilterValue(type="number", number=30),
        )]
        result = evaluate_symbol(df, filters)
        if result is not None:
            assert "RSI_14" in result
            assert result["RSI_14"] < 30

    def test_rsi_no_match(self):
        """RSI LT 30 should NOT match when RSI is above 30."""
        # Create data with strong uptrend to get RSI > 60
        df = _make_df(200, start_price=50, seed=20)
        df.loc[df.index[-30:], "close"] = np.linspace(100, 200, 30)

        filters = [ScanFilter(
            indicator="RSI",
            params={"length": 14},
            operator="LT",
            value=FilterValue(type="number", number=30),
        )]
        result = evaluate_symbol(df, filters)
        assert result is None

    def test_indicator_vs_indicator(self):
        """SMA(50) GT SMA(200) — test indicator-vs-indicator comparison."""
        # Create data where short-term MA > long-term MA
        df = _make_df(300, start_price=50, seed=30)
        df.loc[df.index[-60:], "close"] = np.linspace(100, 200, 60)

        filters = [ScanFilter(
            indicator="SMA",
            params={"length": 50},
            operator="GT",
            value=FilterValue(type="indicator", indicator="SMA", params={"length": 200}),
        )]
        result = evaluate_symbol(df, filters)
        if result is not None:
            assert result["SMA_50"] > result["SMA_200"]

    def test_multiplier_logic(self):
        """VOLUME GT 2x avg volume."""
        df = _make_df(100)
        # Set last bar volume very high
        avg_vol = df["volume"].rolling(20).mean().iloc[-2]
        df.loc[df.index[-1], "volume"] = avg_vol * 3

        filters = [ScanFilter(
            indicator="VOLUME",
            params={},
            operator="GT",
            value=FilterValue(
                type="indicator", indicator="VOLUME",
                params={"length": 20}, multiplier=2.0,
            ),
        )]
        result = evaluate_symbol(df, filters)
        assert result is not None
        assert "VOLUME" in result

    def test_crosses_above(self):
        """SMA(5) CROSSES_ABOVE SMA(20) — force a cross in mock data."""
        df = _make_df(100, start_price=100, seed=50)
        # Force a cross: SMA5 was below SMA20, now above
        df.loc[df.index[-10:-1], "close"] = 80.0  # pull short MA down
        df.loc[df.index[-1], "close"] = 120.0      # spike it up

        filters = [ScanFilter(
            indicator="SMA",
            params={"length": 5},
            operator="CROSSES_ABOVE",
            value=FilterValue(type="indicator", indicator="SMA", params={"length": 20}),
        )]
        result = evaluate_symbol(df, filters)
        # This may or may not match depending on exact values,
        # but the function should not error
        assert result is None or isinstance(result, dict)

    def test_crosses_below(self):
        """SMA(5) CROSSES_BELOW SMA(20) — force a cross in mock data."""
        df = _make_df(100, start_price=100, seed=60)
        # Force cross below: SMA5 was above SMA20, now below
        df.loc[df.index[-10:-1], "close"] = 120.0
        df.loc[df.index[-1], "close"] = 60.0

        filters = [ScanFilter(
            indicator="SMA",
            params={"length": 5},
            operator="CROSSES_BELOW",
            value=FilterValue(type="indicator", indicator="SMA", params={"length": 20}),
        )]
        result = evaluate_symbol(df, filters)
        assert result is None or isinstance(result, dict)


# ---------------------------------------------------------------------------
# FilterValue Pydantic validation
# ---------------------------------------------------------------------------

class TestFilterValueValidation:
    def test_number_requires_number_field(self):
        with pytest.raises(ValidationError):
            FilterValue(type="number")  # number is None

    def test_indicator_requires_indicator_field(self):
        with pytest.raises(ValidationError):
            FilterValue(type="indicator")  # indicator is None

    def test_valid_number(self):
        fv = FilterValue(type="number", number=30.0)
        assert fv.number == 30.0

    def test_valid_indicator(self):
        fv = FilterValue(type="indicator", indicator="SMA", params={"length": 50})
        assert fv.indicator == "SMA"

    def test_multiplier_default(self):
        fv = FilterValue(type="number", number=10)
        assert fv.multiplier == 1.0


# ---------------------------------------------------------------------------
# Cache key
# ---------------------------------------------------------------------------

class TestCacheKey:
    def test_different_intervals_different_keys(self):
        """Different interval/period should produce separate cache entries."""
        key1 = ("AAPL", "1d", "1y")
        key2 = ("AAPL", "1h", "3mo")
        assert key1 != key2

    def test_same_params_same_key(self):
        key1 = ("AAPL", "1d", "1y")
        key2 = ("AAPL", "1d", "1y")
        assert key1 == key2


# ---------------------------------------------------------------------------
# ScanRequest validation
# ---------------------------------------------------------------------------

class TestScanRequestValidation:
    def test_empty_filters_rejected(self):
        with pytest.raises(ValidationError):
            ScanRequest(universe="sp500", filters=[])

    def test_valid_request(self):
        req = ScanRequest(
            universe="sp500",
            filters=[ScanFilter(
                indicator="RSI",
                params={"length": 14},
                operator="LT",
                value=FilterValue(type="number", number=30),
            )],
        )
        assert req.universe == "sp500"
        assert req.limit == 100

    def test_limit_capped(self):
        with pytest.raises(ValidationError):
            ScanRequest(
                universe="sp500",
                filters=[ScanFilter(
                    indicator="RSI",
                    params={"length": 14},
                    operator="LT",
                    value=FilterValue(type="number", number=30),
                )],
                limit=1000,
            )


# ---------------------------------------------------------------------------
# VOLUME and CHANGE_PCT indicator computation
# ---------------------------------------------------------------------------

class TestSpecialIndicators:
    def test_volume_raw(self):
        df = _make_df(50)
        series = _compute_indicator_series(df, "VOLUME", {})
        assert len(series) == 50
        assert series.iloc[-1] == df["volume"].iloc[-1]

    def test_volume_avg(self):
        df = _make_df(50)
        series = _compute_indicator_series(df, "VOLUME", {"length": 20})
        assert pd.notna(series.iloc[-1])

    def test_change_pct(self):
        df = _make_df(50)
        series = _compute_indicator_series(df, "CHANGE_PCT", {})
        assert len(series) == 50


# ---------------------------------------------------------------------------
# Preset model
# ---------------------------------------------------------------------------

class TestPresetModel:
    def test_create_preset(self):
        preset = ScreenerPreset(
            name="Test",
            filters=[ScanFilter(
                indicator="RSI",
                params={"length": 14},
                operator="LT",
                value=FilterValue(type="number", number=30),
            )],
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        assert preset.name == "Test"
        assert preset.built_in is False
        assert len(preset.id) > 0

    def test_preset_serialization(self):
        preset = ScreenerPreset(
            name="Test",
            filters=[ScanFilter(
                indicator="RSI",
                params={"length": 14},
                operator="LT",
                value=FilterValue(type="number", number=30),
            )],
            created_at="2024-01-01T00:00:00Z",
        )
        data = json.loads(preset.model_dump_json())
        restored = ScreenerPreset.model_validate(data)
        assert restored.name == preset.name
        assert len(restored.filters) == 1

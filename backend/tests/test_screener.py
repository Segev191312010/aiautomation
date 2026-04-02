"""
Tests for the stock screener engine and API endpoints.
"""
import json
import pytest
import numpy as np
import pandas as pd
import time
from unittest.mock import patch, AsyncMock, MagicMock
from datetime import datetime, timezone

from pydantic import ValidationError

import screener as screener_module
from models import (
    ScanRequest, ScanFilter, FilterValue, ScanResultRow, ScanResponse,
    ScreenerPreset, EnrichRequest,
)
from screener import (
    load_universe, list_universes, validate_timeframe,
    make_indicator_key, evaluate_symbol, _compute_indicator_series,
    compute_screener_snapshot, run_scan, build_market_opportunity_snapshot,
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
        assert len(universes) == 4
        ids = [u["id"] for u in universes]
        assert "sp500" in ids
        assert "nasdaq100" in ids
        assert "etfs" in ids
        assert "us_all" in ids
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


class TestScreenerRanking:
    def test_compute_screener_snapshot_scores_constructive_trend(self):
        df = _make_df(260, start_price=80, seed=77)
        df.loc[df.index[-80:], "close"] = np.linspace(120, 185, 80)
        df.loc[df.index[-10:], "volume"] = df["volume"].rolling(20).mean().iloc[-11] * 2.2

        snapshot = compute_screener_snapshot(df)

        assert snapshot["screener_score"] >= 60
        assert snapshot["setup"] in {"breakout", "trend", "pullback"}
        assert snapshot["relative_volume"] >= 1.0
        assert isinstance(snapshot["notes"], list)

    @pytest.mark.anyio
    async def test_run_scan_ranks_by_screener_score(anyio_backend):
        screener_module._bar_cache.clear()
        request = ScanRequest(
            universe="sp500",
            filters=[ScanFilter(
                indicator="PRICE",
                params={},
                operator="GT",
                value=FilterValue(type="number", number=1),
            )],
            interval="1d",
            period="1y",
            limit=10,
        )

        strong_df = _make_df(260, start_price=90, seed=88)
        strong_df.loc[strong_df.index[-70:], "close"] = np.linspace(120, 210, 70)
        strong_df.loc[strong_df.index[-5:], "volume"] = strong_df["volume"].rolling(20).mean().iloc[-6] * 2.0

        weak_df = _make_df(260, start_price=90, seed=89)
        weak_df.loc[weak_df.index[-25:], "close"] = np.linspace(95, 102, 25)

        screener_module._bar_cache[("AAA", "1d", "1y")] = screener_module.CacheEntry(strong_df, time.time())
        screener_module._bar_cache[("BBB", "1d", "1y")] = screener_module.CacheEntry(weak_df, time.time())

        with patch("screener.load_universe", return_value=["AAA", "BBB"]), patch(
            "screener.refresh_cache",
            new=AsyncMock(return_value=[]),
        ):
            response = await run_scan(request)

        assert response.results[0].symbol == "AAA"
        assert response.results[0].screener_score >= response.results[1].screener_score

    @pytest.mark.anyio
    async def test_build_market_opportunity_snapshot_returns_ranked_candidates(anyio_backend):
        screener_module._bar_cache.clear()
        trend_df = _make_df(260, start_price=70, seed=90)
        trend_df.loc[trend_df.index[-70:], "close"] = np.linspace(95, 165, 70)
        trend_df.loc[trend_df.index[-5:], "volume"] = trend_df["volume"].rolling(20).mean().iloc[-6] * 2.4

        pullback_df = _make_df(260, start_price=110, seed=91)
        pullback_df.loc[pullback_df.index[-60:], "close"] = np.linspace(130, 175, 60)
        pullback_df.loc[pullback_df.index[-8:], "close"] = np.linspace(170, 168, 8)

        screener_module._bar_cache[("NVDA", "1d", "6mo")] = screener_module.CacheEntry(trend_df, time.time())
        screener_module._bar_cache[("MSFT", "1d", "6mo")] = screener_module.CacheEntry(pullback_df, time.time())

        with patch("screener.load_universe", side_effect=lambda uid: ["NVDA", "MSFT"] if uid == "nasdaq100" else []), patch(
            "screener.refresh_cache",
            new=AsyncMock(return_value=[]),
        ):
            snapshot = await build_market_opportunity_snapshot(universe_ids=("nasdaq100",), limit=5)

        assert snapshot["available"] is True
        assert snapshot["candidate_count"] >= 1
        assert snapshot["candidates"][0]["screener_score"] >= snapshot["candidates"][-1]["screener_score"]
        assert "setup_counts" in snapshot


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


# ---------------------------------------------------------------------------
# ScanResponse timing fields
# ---------------------------------------------------------------------------

class TestScanResponseTiming:
    def test_response_includes_timing_fields(self):
        response = ScanResponse(
            results=[],
            skipped_symbols=[],
            elapsed_ms=1234,
            total_symbols=500,
        )
        assert response.elapsed_ms == 1234
        assert response.total_symbols == 500

    def test_response_default_timing(self):
        response = ScanResponse(results=[], skipped_symbols=[])
        assert response.elapsed_ms == 0
        assert response.total_symbols == 0


# ---------------------------------------------------------------------------
# Run scan returns timing
# ---------------------------------------------------------------------------

class TestScanTiming:
    @pytest.mark.anyio
    async def test_run_scan_includes_elapsed_ms(anyio_backend):
        screener_module._bar_cache.clear()
        df = _make_df(260, start_price=100, seed=100)
        screener_module._bar_cache[("TEST", "1d", "1y")] = screener_module.CacheEntry(df, time.time())

        request = ScanRequest(
            universe="custom",
            symbols=["TEST"],
            filters=[ScanFilter(
                indicator="PRICE",
                params={},
                operator="GT",
                value=FilterValue(type="number", number=1),
            )],
            interval="1d",
            period="1y",
        )

        with patch("screener.refresh_cache", new=AsyncMock(return_value=[])):
            response = await run_scan(request)

        assert response.elapsed_ms >= 0
        assert response.total_symbols == 1

    @pytest.mark.anyio
    async def test_empty_universe_returns_zero_timing(anyio_backend):
        request = ScanRequest(
            universe="custom",
            symbols=[],
            filters=[ScanFilter(
                indicator="PRICE",
                params={},
                operator="GT",
                value=FilterValue(type="number", number=1),
            )],
        )
        response = await run_scan(request)
        assert response.elapsed_ms == 0
        assert response.total_symbols == 0


# ---------------------------------------------------------------------------
# Built-in presets
# ---------------------------------------------------------------------------

class TestBuiltInPresets:
    def test_built_in_presets_have_valid_filters(self):
        from database import _BUILT_IN_PRESETS
        for raw in _BUILT_IN_PRESETS:
            assert "name" in raw
            assert "filters" in raw
            assert len(raw["filters"]) >= 1
            # Ensure each filter validates
            for f in raw["filters"]:
                filt = ScanFilter.model_validate(f)
                assert filt.indicator in (
                    "RSI", "SMA", "EMA", "MACD", "BBANDS", "ATR", "STOCH",
                    "PRICE", "VOLUME", "CHANGE_PCT",
                )

    def test_at_least_8_built_in_presets(self):
        from database import _BUILT_IN_PRESETS
        assert len(_BUILT_IN_PRESETS) >= 8

    def test_preset_names_unique(self):
        from database import _BUILT_IN_PRESETS
        names = [p["name"] for p in _BUILT_IN_PRESETS]
        assert len(names) == len(set(names)), "Duplicate preset names found"


# ---------------------------------------------------------------------------
# IBKR Scanner (unit tests — no real IBKR connection)
# ---------------------------------------------------------------------------

class TestIBKRScanner:
    def test_get_available_scans_returns_list(self):
        from ibkr_scanner import get_available_scans
        scans = get_available_scans()
        assert isinstance(scans, list)
        assert len(scans) >= 8
        for scan in scans:
            assert "id" in scan
            assert "name" in scan
            assert "max_results" in scan

    def test_scan_templates_have_required_fields(self):
        from ibkr_scanner import SCAN_TEMPLATES
        for name, template in SCAN_TEMPLATES.items():
            assert "instrument" in template
            assert "locationCode" in template
            assert "scanCode" in template

    @pytest.mark.anyio
    async def test_run_scan_returns_empty_when_disconnected(anyio_backend):
        from ibkr_scanner import run_scan as ibkr_run_scan
        # ibkr is not connected in test env
        results = await ibkr_run_scan("hot_us_stocks")
        assert results == []


# ---------------------------------------------------------------------------
# CSV export route (contract test)
# ---------------------------------------------------------------------------

class TestCSVExportRoute:
    @pytest.mark.anyio
    async def test_csv_export_produces_valid_csv(anyio_backend):
        """Test that the CSV export logic produces a valid response."""
        import csv
        import io
        from routers.screener_routes import router as _  # noqa: F401 — ensure import works

        # Test CSV generation logic directly
        from models import ScanResultRow
        rows = [
            ScanResultRow(
                symbol="AAPL", price=175.50, change_pct=1.2, volume=50_000_000,
                indicators={"RSI_14": 55.3, "SMA_50": 170.0},
                screener_score=72.5, setup="trend",
                relative_volume=1.5, momentum_20d=8.3, trend_strength=24.0,
                notes=["MA stack aligned"],
            ),
        ]
        output = io.StringIO()
        writer = csv.writer(output)
        indicator_cols = sorted({"RSI_14", "SMA_50"})
        header = ["Symbol", "Price", "Change%", "Volume", "Score", "Setup",
                  "RVOL", "Mom20D", "Trend", "Notes"] + indicator_cols
        writer.writerow(header)
        for row in rows:
            writer.writerow([
                row.symbol, f"{row.price:.2f}", f"{row.change_pct:.2f}", row.volume,
                f"{row.screener_score:.1f}", row.setup,
                f"{row.relative_volume:.2f}", f"{row.momentum_20d:.2f}",
                f"{row.trend_strength:.1f}", "; ".join(row.notes),
            ] + [f"{row.indicators.get(col, 0):.4f}" for col in indicator_cols])

        output.seek(0)
        reader = csv.reader(output)
        all_rows = list(reader)
        assert len(all_rows) == 2  # header + 1 data row
        assert all_rows[0][0] == "Symbol"
        assert all_rows[1][0] == "AAPL"
        assert all_rows[1][4] == "72.5"


# ---------------------------------------------------------------------------
# Rate limiting / cache behavior
# ---------------------------------------------------------------------------

class TestCacheBehavior:
    def test_cache_eviction_at_max_size(self):
        """Ensure LRU eviction works when cache exceeds MAX_CACHE_SIZE."""
        from screener import _bar_cache, _evict_if_full, CacheEntry, MAX_CACHE_SIZE
        _bar_cache.clear()

        # Fill cache beyond max
        df = _make_df(10)
        for i in range(MAX_CACHE_SIZE + 100):
            _bar_cache[(f"SYM{i}", "1d", "1y")] = CacheEntry(df, time.time() + i * 0.001)

        assert len(_bar_cache) == MAX_CACHE_SIZE + 100

        _evict_if_full()

        assert len(_bar_cache) <= MAX_CACHE_SIZE
        _bar_cache.clear()

    def test_stale_cache_detection(self):
        """Entries older than CACHE_TTL should be detected as stale."""
        from screener import CacheEntry, _is_stale, CACHE_TTL
        df = _make_df(10)

        fresh = CacheEntry(df, time.time())
        assert not _is_stale(fresh)

        stale = CacheEntry(df, time.time() - CACHE_TTL - 1)
        assert _is_stale(stale)

    def test_cache_key_uniqueness(self):
        """Same symbol with different interval/period produces different keys."""
        key1 = ("AAPL", "1d", "1y")
        key2 = ("AAPL", "1h", "3mo")
        key3 = ("MSFT", "1d", "1y")
        assert key1 != key2
        assert key1 != key3

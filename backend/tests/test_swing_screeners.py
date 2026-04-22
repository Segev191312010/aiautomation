"""Phase-1 regression tests for the swing screener backend.

Covers:
  - build_composite universe helper (DJIA + SP500 + NASDAQ100 are stitched)
  - ATR matrix computation against synthetic OHLC data
  - Stockbee 9M / weekly / daily scans' filter logic

The screener cache (_bar_cache) is populated directly with in-memory DataFrames
so the tests do NOT hit yfinance.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import swing_screeners as ss
from screener import _bar_cache


@pytest.fixture(autouse=True)
def _clear_cache():
    """Each test starts with an empty bar cache."""
    _bar_cache.clear()
    ss._cache.clear()
    yield
    _bar_cache.clear()
    ss._cache.clear()


def _synthetic_bars(
    n: int = 260,
    base: float = 100.0,
    drift: float = 0.0,
    high_spread: float = 1.0,
    low_spread: float = 1.0,
    volume: int = 1_000_000,
) -> pd.DataFrame:
    """Build a DataFrame shaped like the screener's cached bars."""
    closes = base + drift * np.arange(n)
    return pd.DataFrame({
        "close": closes,
        "open":  closes,
        "high":  closes + high_spread,
        "low":   closes - low_spread,
        "volume": [volume] * n,
    })


def _prime(symbol: str, df: pd.DataFrame) -> None:
    from screener import CacheEntry

    _bar_cache[(symbol, ss.INTERVAL, ss.PERIOD)] = CacheEntry(df=df, fetched_at=0.0)


# ── F1: universe helpers ─────────────────────────────────────────────────────

def test_build_composite_returns_unique_sorted_symbols():
    """Composite = SP500 ∪ NASDAQ100 ∪ DJIA with no duplicates and stable order."""
    composite = ss.build_composite()
    assert len(composite) > 400, "SP500 alone has ~500 tickers"
    assert composite == sorted(composite), "composite should be sorted for stable caching"
    assert len(composite) == len(set(composite)), "no duplicate tickers"
    # DJIA members should all appear since they're also in SP500
    assert "AAPL" in composite
    assert "JPM" in composite


# ── F2: ATR matrix ──────────────────────────────────────────────────────────

def test_atr_matrix_produces_sorted_rows_for_primed_sector_etfs():
    """Matrix rows sort by price_vs_21ema_atr descending."""
    # Prime three sector ETFs with different extension characteristics.
    # XLK: close far above trend → positive extension
    _prime("XLK", _synthetic_bars(n=260, base=50.0, drift=0.5))
    # XLF: flat trend → near-zero extension
    _prime("XLF", _synthetic_bars(n=260, base=50.0, drift=0.0))
    # XLE: falling → negative extension
    _prime("XLE", _synthetic_bars(n=260, base=80.0, drift=-0.3))

    rows = ss._compute_atr_matrix()
    assert len(rows) == 3
    extensions = [r.price_vs_21ema_atr for r in rows]
    assert extensions == sorted(extensions, reverse=True), "must be sorted DESC"
    # XLK is the only rising series → it owns the most positive extension
    assert rows[0].symbol == "XLK"
    assert rows[0].price_vs_21ema_atr > 0


def test_atr_matrix_skips_symbols_without_enough_bars():
    """Symbols with < 50 bars should be skipped, not crash."""
    _prime("XLK", _synthetic_bars(n=30, base=50.0, drift=0.5))  # too short
    rows = ss._compute_atr_matrix()
    assert all(r.symbol != "XLK" for r in rows)


# ── F3: Stockbee scans ──────────────────────────────────────────────────────

def test_stockbee_9m_movers_requires_volume_above_9m_and_above_avg():
    """9M Movers = volume > avg_50d AND volume >= 9M."""
    # PASS: flat history with a spike bar at the end (volume=12M, avg=1M)
    df_pass = _synthetic_bars(n=60, base=100.0, drift=0.0, volume=1_000_000)
    df_pass.loc[df_pass.index[-1], "volume"] = 12_000_000
    df_pass.loc[df_pass.index[-1], "close"] = 105.0  # daily change > 0
    _prime("PASS", df_pass)

    # FAIL on volume floor: big spike relative to avg, but under 9M
    df_low = _synthetic_bars(n=60, base=100.0, drift=0.0, volume=200_000)
    df_low.loc[df_low.index[-1], "volume"] = 8_000_000
    _prime("LOWVOL", df_low)

    # FAIL on avg comparison: baseline already huge, spike matches → not a mover
    df_flat = _synthetic_bars(n=60, base=100.0, drift=0.0, volume=11_000_000)
    _prime("FLAT", df_flat)

    results = ss._run_stockbee("9m_movers", ["PASS", "LOWVOL", "FLAT"])
    symbols = {r.symbol for r in results}
    assert "PASS" in symbols
    assert "LOWVOL" not in symbols
    assert "FLAT" not in symbols


def test_stockbee_daily_4pct_requires_4pct_positive_change():
    # Build a 60-bar series where the last bar gaps up > 4% vs the previous close.
    df_up = _synthetic_bars(n=60, base=100.0, drift=0.0)
    df_up.loc[df_up.index[-1], "close"] = 106.0  # +6% vs 100
    _prime("UP", df_up)

    df_flat = _synthetic_bars(n=60, base=100.0, drift=0.0)
    df_flat.loc[df_flat.index[-1], "close"] = 101.0  # +1% — fails
    _prime("SMALL", df_flat)

    results = ss._run_stockbee("daily_4pct", ["UP", "SMALL"])
    symbols = {r.symbol for r in results}
    assert "UP" in symbols
    assert "SMALL" not in symbols


def test_stockbee_unknown_scan_returns_empty():
    _prime("ANY", _synthetic_bars(n=60, base=100.0, drift=0.0))
    assert ss._run_stockbee("bogus_scan", ["ANY"]) == []

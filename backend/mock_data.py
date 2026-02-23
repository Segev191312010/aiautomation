"""
Mock Market Data Service — Geometric Brownian Motion price simulation.

Used when IBKR is not connected and cfg.MOCK_MODE = True.
All prices drift from realistic 2026 baselines and evolve each call
using GBM so charts show genuine looking price action.

Public API
----------
get_mock_price(symbol)            → float
get_mock_quote(symbol)            → dict  (MarketQuote-compatible)
get_mock_quotes(symbols)          → list[dict]
get_mock_ohlcv(symbol, **kwargs)  → list[dict]  (lightweight-charts format)
get_mock_account_summary()        → dict  (AccountSummary-compatible)
reset_prices()                    → None  (restart simulation from base prices)
"""
from __future__ import annotations

import math
import random
import time
from datetime import datetime, timezone
from typing import Optional

# ---------------------------------------------------------------------------
# Baseline parameters  (as of early 2026 — update as needed)
# ---------------------------------------------------------------------------

_BASE_PRICES: dict[str, float] = {
    "AAPL":    220.0,
    "TSLA":    340.0,
    "NVDA":    890.0,
    "MSFT":    415.0,
    "AMZN":    210.0,
    "GOOGL":   175.0,
    "META":    580.0,
    "SPY":     575.0,
    "QQQ":     495.0,
    "IWM":     220.0,
    "BTC-USD": 98_000.0,
    "ETH-USD":  3_300.0,
    "SOL-USD":    200.0,
    "GLD":     185.0,
    "TLT":      88.0,
    "VXX":      14.0,
}

# Daily-equivalent volatility (σ) used in GBM steps
_SIGMA: dict[str, float] = {
    "AAPL":    0.015,
    "TSLA":    0.035,
    "NVDA":    0.030,
    "MSFT":    0.015,
    "AMZN":    0.018,
    "GOOGL":   0.016,
    "META":    0.022,
    "SPY":     0.010,
    "QQQ":     0.012,
    "IWM":     0.013,
    "BTC-USD": 0.045,
    "ETH-USD": 0.050,
    "SOL-USD": 0.060,
    "GLD":     0.010,
    "TLT":     0.008,
    "VXX":     0.060,
}

_MARKET_CAP: dict[str, Optional[float]] = {
    "AAPL":    3.30e12,
    "TSLA":    1.00e12,
    "NVDA":    2.20e12,
    "MSFT":    3.05e12,
    "AMZN":    2.25e12,
    "GOOGL":   2.05e12,
    "META":    1.45e12,
    "SPY":     None,
    "QQQ":     None,
    "IWM":     None,
    "BTC-USD": 1.95e12,
    "ETH-USD": 4.00e11,
    "SOL-USD": 9.00e10,
    "GLD":     None,
    "TLT":     None,
    "VXX":     None,
}

_AVG_VOLUME: dict[str, Optional[float]] = {
    "AAPL":    6.0e7,
    "TSLA":    1.0e8,
    "NVDA":    5.0e7,
    "MSFT":    2.5e7,
    "AMZN":    4.0e7,
    "GOOGL":   3.0e7,
    "META":    2.0e7,
    "SPY":     8.0e7,
    "QQQ":     5.0e7,
    "IWM":     3.0e7,
    "BTC-USD": 3.0e10,
    "ETH-USD": 1.5e10,
    "SOL-USD": 5.0e9,
    "GLD":     1.0e7,
    "TLT":     2.0e7,
    "VXX":     5.0e6,
}

# Persistent price state — starts at base and drifts across requests
_price_state: dict[str, float] = {}
# Previous-close snapshot (refreshed once per "day" via _maybe_advance_day)
_prev_close: dict[str, float] = {}
_last_day: int = 0  # unix day counter


def reset_prices() -> None:
    """Reset all simulated prices back to their base values."""
    _price_state.clear()
    _prev_close.clear()


# ---------------------------------------------------------------------------
# Internal GBM helpers
# ---------------------------------------------------------------------------

def _sigma(symbol: str) -> float:
    return _SIGMA.get(symbol, 0.020)


def _base(symbol: str) -> float:
    return _BASE_PRICES.get(symbol, 100.0)


def _gbm(price: float, mu: float, sigma: float, dt: float) -> float:
    """One GBM step: S(t+dt) = S(t) * exp((μ - σ²/2)dt + σ√dt Z)"""
    z = random.gauss(0.0, 1.0)
    return price * math.exp((mu - 0.5 * sigma ** 2) * dt + sigma * math.sqrt(dt) * z)


def _current(symbol: str) -> float:
    """Return the current (evolving) mock price for a symbol."""
    if symbol not in _price_state:
        _price_state[symbol] = _base(symbol) * random.uniform(0.92, 1.08)
    return _price_state[symbol]


def _advance(symbol: str) -> float:
    """Advance the GBM by one intraday step (~1/78 of a trading day) and return new price."""
    p = _current(symbol)
    dt = 1.0 / 78.0      # ~5-minute bars in a 6.5-hour session
    mu = 0.00005          # tiny positive drift
    new_p = _gbm(p, mu, _sigma(symbol), dt)
    _price_state[symbol] = new_p
    return new_p


def _ensure_prev_close(symbol: str) -> float:
    if symbol not in _prev_close:
        _prev_close[symbol] = _current(symbol) * random.uniform(0.98, 1.02)
    return _prev_close[symbol]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_mock_price(symbol: str) -> float:
    """Return an evolving mock last-trade price."""
    return round(_advance(symbol), 4)


def get_mock_quote(symbol: str) -> dict:
    """Full MarketQuote-compatible dict for one symbol."""
    price = _advance(symbol)
    prev = _ensure_prev_close(symbol)
    change = price - prev
    change_pct = change / prev * 100 if prev else 0.0

    base = _base(symbol)
    year_low  = base * random.uniform(0.65, 0.85)
    year_high = base * random.uniform(1.10, 1.45)

    avg_vol = _AVG_VOLUME.get(symbol)
    volume = int(avg_vol * random.uniform(0.6, 1.4)) if avg_vol else None

    spread = price * 0.0001
    return {
        "symbol":     symbol,
        "price":      round(price, 4),
        "change":     round(change, 4),
        "change_pct": round(change_pct, 2),
        "year_high":  round(year_high, 2),
        "year_low":   round(year_low, 2),
        "market_cap": _MARKET_CAP.get(symbol),
        "avg_volume": avg_vol,
        "volume":     volume,
        "bid":        round(price - spread, 4),
        "ask":        round(price + spread, 4),
        "last_update": datetime.now(timezone.utc).isoformat(),
        "is_mock":    True,
    }


def get_mock_quotes(symbols: list[str]) -> list[dict]:
    return [get_mock_quote(s) for s in symbols]


def get_mock_ohlcv(
    symbol: str,
    num_bars: int = 120,
    bar_seconds: int = 86_400,   # 1 day default
    end_ts: Optional[int] = None,
) -> list[dict]:
    """
    Generate a list of OHLCV bars in lightweight-charts wire format
    (time = Unix seconds, ascending order).

    Uses a seeded GBM walk so consecutive calls with the same symbol
    produce coherent series.
    """
    sigma = _sigma(symbol)
    base  = _base(symbol)
    avg_vol = _AVG_VOLUME.get(symbol) or 1_000_000

    end_ts = end_ts or int(time.time())
    # Snap to bar boundary
    end_ts = (end_ts // bar_seconds) * bar_seconds

    # dt expressed as fraction of a trading year
    dt = bar_seconds / (252 * 86_400)

    bars: list[dict] = []
    price = base * random.uniform(0.80, 1.20)

    for i in range(num_bars, 0, -1):
        ts = end_ts - i * bar_seconds

        # Skip weekends for daily+ bars
        if bar_seconds >= 86_400:
            from datetime import datetime
            dow = datetime.utcfromtimestamp(ts).weekday()
            if dow >= 5:
                continue

        open_ = price

        # Simulate intrabar using 4 sub-steps
        sub_prices = [open_]
        p = open_
        for _ in range(4):
            p = _gbm(p, 0.0001, sigma, dt / 4)
            sub_prices.append(p)

        high_  = max(sub_prices)
        low_   = min(sub_prices)
        close_ = sub_prices[-1]

        volume = max(int(random.gauss(avg_vol, avg_vol * 0.25)), 1_000)

        bars.append(
            {
                "time":   ts,
                "open":   round(open_,  4),
                "high":   round(high_,  4),
                "low":    round(low_,   4),
                "close":  round(close_, 4),
                "volume": volume,
            }
        )
        price = close_

    return bars


def get_mock_account_summary() -> dict:
    """AccountSummary-compatible dict for demo/offline mode."""
    return {
        "balance":        125_847.32,
        "cash":            85_234.18,
        "margin_used":          0.0,
        "unrealized_pnl":   4_213.45,
        "realized_pnl":    36_399.69,
        "currency":         "USD",
        "is_mock":          True,
    }

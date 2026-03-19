"""
Stage 5 — Regime Detection & Adaptive Weighting.

Classifies market regime from SPY/IWM/sector ETFs:
  BULL / BEAR / HIGH_VOL / LOW_VOL

Strategies and portfolio adjust behavior based on current regime.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Literal

import pandas as pd

from events import EventType, MarketEvent, RegimeEvent

log = logging.getLogger(__name__)

RegimeLabel = Literal["BULL", "BEAR", "HIGH_VOL", "LOW_VOL"]


class RegimeDetector:
    """Detect market regime from a basket of index/sector ETFs."""

    def __init__(self):
        self._current_regime: RegimeLabel = "BULL"
        self._volatility: float = 0.0
        self._market_score: float = 0.5

    @property
    def regime(self) -> RegimeLabel:
        return self._current_regime

    @property
    def volatility(self) -> float:
        return self._volatility

    def on_market_event(self, event: MarketEvent, spy_bars: pd.DataFrame | None = None) -> RegimeEvent:
        """Update regime from SPY daily bars."""
        if spy_bars is not None and len(spy_bars) >= 200:
            close = spy_bars["close"]
            price = float(close.iloc[-1])
            sma50 = float(close.rolling(50).mean().iloc[-1])
            sma200 = float(close.rolling(200).mean().iloc[-1])

            # ATR-based volatility
            h, l, c = spy_bars["high"], spy_bars["low"], spy_bars["close"]
            tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
            atr = float(tr.rolling(14).mean().iloc[-1])
            atr_pct = atr / price * 100
            self._volatility = atr_pct

            # Regime classification
            if price > sma200 * 1.02 and sma50 > sma200:
                self._current_regime = "BULL"
                self._market_score = 0.8
            elif price < sma200 * 0.98 and sma50 < sma200:
                self._current_regime = "BEAR"
                self._market_score = 0.2
            elif atr_pct > 2.5:
                self._current_regime = "HIGH_VOL"
                self._market_score = 0.4
            else:
                self._current_regime = "LOW_VOL" if atr_pct < 1.0 else "BULL"
                self._market_score = 0.6

        return RegimeEvent(
            timestamp=event.timestamp,
            type=EventType.REGIME,
            regime=self._current_regime,
            volatility=self._volatility,
            market_score=self._market_score,
        )

    def get_weight_adjustments(self) -> dict[str, float]:
        """Return signal weight multipliers based on current regime."""
        if self._current_regime == "BULL":
            return {"trend": 1.3, "momentum": 1.2, "mean_reversion": 0.7, "breakout": 1.1}
        elif self._current_regime == "BEAR":
            return {"trend": 0.6, "momentum": 0.5, "mean_reversion": 1.3, "breakout": 0.4}
        elif self._current_regime == "HIGH_VOL":
            return {"trend": 0.8, "momentum": 0.6, "mean_reversion": 1.2, "breakout": 0.7}
        else:  # LOW_VOL
            return {"trend": 1.0, "momentum": 0.8, "mean_reversion": 0.9, "breakout": 1.3}

    def get_risk_multiplier(self) -> float:
        """Scale position sizes by regime. 1.0 = normal, <1 = reduce, >1 = increase."""
        return {"BULL": 1.0, "BEAR": 0.5, "HIGH_VOL": 0.7, "LOW_VOL": 1.0}[self._current_regime]

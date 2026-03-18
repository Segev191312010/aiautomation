"""Signal scoring engine — ranks triggered signals by quality.

When 200 stocks match a rule, we score them and pick the top N.
"""
from __future__ import annotations

import logging
import pandas as pd

log = logging.getLogger(__name__)

DEFAULT_WEIGHTS = {
    "rsi": 20, "volume": 15, "trend": 20, "volatility": 10,
    "momentum": 10, "support": 10, "macd": 10, "bollinger": 5,
}


class SignalScorer:
    """Multi-factor signal scoring. Higher composite = better trade."""

    def score_signal(self, symbol: str, df: pd.DataFrame, side: str = "BUY") -> dict:
        if df is None or len(df) < 20:
            return {"symbol": symbol, "composite_score": 0, "factors": {}}

        close, volume = df["close"], df["volume"]
        price = float(close.iloc[-1])
        scores: dict[str, float] = {}

        # 1. RSI (0-100)
        rsi = self._rsi(close, 14)
        if side == "BUY":
            scores["rsi"] = max(0, min(100, (70 - rsi) * 2.5))
        else:
            scores["rsi"] = max(0, min(100, (rsi - 30) * 2.5))

        # 2. Volume confirmation
        avg_vol = float(volume.rolling(20).mean().iloc[-1]) if len(volume) >= 20 else float(volume.mean())
        vol_ratio = float(volume.iloc[-1]) / avg_vol if avg_vol > 0 else 1
        scores["volume"] = max(0, min(100, vol_ratio * 50))

        # 3. Trend alignment
        sma200 = float(close.rolling(200).mean().iloc[-1]) if len(close) >= 200 else float(close.mean())
        sma50 = float(close.rolling(50).mean().iloc[-1]) if len(close) >= 50 else float(close.mean())
        if side == "BUY":
            if price > sma200 and sma50 > sma200:
                scores["trend"] = 90
            elif price > sma200:
                scores["trend"] = 70
            elif price > sma50:
                scores["trend"] = 50
            else:
                scores["trend"] = 15
        else:
            if price < sma200 and sma50 < sma200:
                scores["trend"] = 90
            elif price < sma200:
                scores["trend"] = 70
            else:
                scores["trend"] = 30

        # 4. Volatility — moderate is best
        atr = self._atr(df, 14)
        atr_pct = (atr / price * 100) if price > 0 else 0
        if 1 <= atr_pct <= 3:
            scores["volatility"] = 90
        elif 0.5 <= atr_pct < 1 or 3 < atr_pct <= 5:
            scores["volatility"] = 55
        else:
            scores["volatility"] = 20

        # 5. Momentum (10-day ROC)
        if len(close) > 10:
            roc = ((price / float(close.iloc[-11])) - 1) * 100
            raw = 50 + roc * 5
            scores["momentum"] = max(0, min(100, raw if side == "BUY" else 100 - raw))
        else:
            scores["momentum"] = 50

        # 6. Support/resistance proximity
        if len(close) >= 20:
            low20 = float(close.rolling(20).min().iloc[-1])
            high20 = float(close.rolling(20).max().iloc[-1])
            rng = high20 - low20
            if rng > 0:
                pos = (price - low20) / rng
                scores["support"] = max(0, min(100, (1 - pos) * 100 if side == "BUY" else pos * 100))
            else:
                scores["support"] = 50
        else:
            scores["support"] = 50

        # 7. MACD histogram strength
        _, _, hist = self._macd(close)
        raw_macd = abs(hist) / max(price * 0.01, 0.01) * 50
        scores["macd"] = max(0, min(100, raw_macd))

        # 8. Bollinger position
        if len(close) >= 20:
            sma20 = float(close.rolling(20).mean().iloc[-1])
            std20 = float(close.rolling(20).std().iloc[-1])
            bb_u, bb_l = sma20 + 2 * std20, sma20 - 2 * std20
            bb_rng = bb_u - bb_l
            if bb_rng > 0:
                bb_pos = (price - bb_l) / bb_rng
                scores["bollinger"] = max(0, min(100, (1 - bb_pos) * 100 if side == "BUY" else bb_pos * 100))
            else:
                scores["bollinger"] = 50
        else:
            scores["bollinger"] = 50

        total_w = sum(DEFAULT_WEIGHTS.values())
        composite = sum(scores[k] * DEFAULT_WEIGHTS[k] for k in DEFAULT_WEIGHTS) / total_w

        return {
            "symbol": symbol,
            "composite_score": round(composite, 1),
            "factors": {k: round(v, 1) for k, v in scores.items()},
            "price": round(price, 2),
            "rsi": round(rsi, 1),
            "volume_ratio": round(vol_ratio, 2),
            "atr_pct": round(atr_pct, 2),
            "side": side,
        }

    def rank_signals(self, signals: list[dict], top_n: int = 5, min_score: float = 55) -> list[dict]:
        qualified = [s for s in signals if s["composite_score"] >= min_score]
        return sorted(qualified, key=lambda s: s["composite_score"], reverse=True)[:top_n]

    @staticmethod
    def _rsi(close: pd.Series, period: int = 14) -> float:
        delta = close.diff()
        gain = delta.clip(lower=0).ewm(com=period - 1, min_periods=period).mean()
        loss = (-delta.clip(upper=0)).ewm(com=period - 1, min_periods=period).mean()
        rs = gain / loss
        val = (100 - (100 / (1 + rs))).iloc[-1]
        return float(val) if pd.notna(val) else 50.0

    @staticmethod
    def _atr(df: pd.DataFrame, period: int = 14) -> float:
        h, l, c = df["high"], df["low"], df["close"]
        tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
        val = tr.rolling(period).mean().iloc[-1]
        return float(val) if pd.notna(val) else 0.0

    @staticmethod
    def _macd(close: pd.Series, fast: int = 12, slow: int = 26, sig: int = 9):
        ml = close.ewm(span=fast).mean() - close.ewm(span=slow).mean()
        sl = ml.ewm(span=sig).mean()
        return float(ml.iloc[-1]), float(sl.iloc[-1]), float((ml - sl).iloc[-1])


signal_scorer = SignalScorer()

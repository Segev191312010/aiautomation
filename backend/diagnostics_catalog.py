"""
Static diagnostics catalog definitions.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import time
from typing import Any


SECTOR_KEYS = [
    "XLB",
    "XLC",
    "XLE",
    "XLF",
    "XLV",
    "XLI",
    "XLK",
    "XLP",
    "XLRE",
    "XLU",
    "XLY",
]


@dataclass(frozen=True)
class IndicatorSpec:
    code: str
    name: str
    source: str
    frequency: str
    weight: float
    invert_sign: bool
    lookback_days: int
    expected_lag_business_days: int
    stale_warn_s: float | None
    stale_critical_s: float | None
    active: bool
    stage: str
    sector_weights: dict[str, float]
    metadata: dict[str, Any]


def _uniform_weights(value: float = 1.0) -> dict[str, float]:
    return {sector: value for sector in SECTOR_KEYS}


def _risk_on_weights() -> dict[str, float]:
    return {
        "XLB": 0.7,
        "XLC": 0.8,
        "XLE": 0.6,
        "XLF": 0.9,
        "XLV": 0.5,
        "XLI": 0.9,
        "XLK": 1.0,
        "XLP": 0.3,
        "XLRE": 0.6,
        "XLU": 0.2,
        "XLY": 0.8,
    }


def _risk_off_weights() -> dict[str, float]:
    return {
        "XLB": 0.4,
        "XLC": 0.5,
        "XLE": 0.5,
        "XLF": 0.3,
        "XLV": 0.9,
        "XLI": 0.4,
        "XLK": 0.4,
        "XLP": 1.0,
        "XLRE": 0.5,
        "XLU": 1.0,
        "XLY": 0.4,
    }


CATALOG: list[IndicatorSpec] = [
    IndicatorSpec(
        code="VIX",
        name="CBOE Volatility Index",
        source="yahoo",
        frequency="real_time",
        weight=1.0,
        invert_sign=True,
        lookback_days=365,
        expected_lag_business_days=0,
        stale_warn_s=10.0,
        stale_critical_s=30.0,
        active=True,
        stage="3A",
        sector_weights=_risk_off_weights(),
        metadata={"symbol": "^VIX"},
    ),
    IndicatorSpec(
        code="SPY_TREND",
        name="SPY 200D Trend Ratio",
        source="yahoo",
        frequency="real_time",
        weight=1.2,
        invert_sign=False,
        lookback_days=365,
        expected_lag_business_days=0,
        stale_warn_s=10.0,
        stale_critical_s=30.0,
        active=True,
        stage="3A",
        sector_weights=_risk_on_weights(),
        metadata={"symbol": "SPY"},
    ),
    IndicatorSpec(
        code="FED_FUNDS_MOMENTUM",
        name="Fed Funds Momentum",
        source="fred",
        frequency="daily",
        weight=1.0,
        invert_sign=True,
        lookback_days=365,
        expected_lag_business_days=1,
        stale_warn_s=None,
        stale_critical_s=None,
        active=True,
        stage="3A",
        sector_weights=_risk_off_weights(),
        metadata={"series": "DFF"},
    ),
    IndicatorSpec(
        code="UST_10Y_2Y",
        name="UST 10Y-2Y Spread",
        source="fred",
        frequency="daily",
        weight=1.0,
        invert_sign=True,
        lookback_days=365,
        expected_lag_business_days=1,
        stale_warn_s=None,
        stale_critical_s=None,
        active=True,
        stage="3A",
        sector_weights=_risk_on_weights(),
        metadata={"series": "T10Y2Y"},
    ),
    IndicatorSpec(
        code="BOND_MARKET_STABILITY",
        name="Bond Market Stability",
        source="fred",
        frequency="daily",
        weight=0.9,
        invert_sign=True,
        lookback_days=365,
        expected_lag_business_days=1,
        stale_warn_s=None,
        stale_critical_s=None,
        active=True,
        stage="3A",
        sector_weights=_risk_off_weights(),
        metadata={"series": "BAA10Y"},
    ),
    IndicatorSpec(
        code="LIQUIDITY_PROXY",
        name="Liquidity Proxy (TLT/HYG)",
        source="yahoo",
        frequency="real_time",
        weight=1.0,
        invert_sign=True,
        lookback_days=365,
        expected_lag_business_days=0,
        stale_warn_s=10.0,
        stale_critical_s=30.0,
        active=True,
        stage="3A",
        sector_weights=_risk_on_weights(),
        metadata={"num": "TLT", "den": "HYG"},
    ),
    IndicatorSpec(
        code="ANALYST_CONFIDENCE",
        name="Analyst Confidence",
        source="yahoo",
        frequency="daily",
        weight=0.8,
        invert_sign=True,
        lookback_days=365,
        expected_lag_business_days=1,
        stale_warn_s=None,
        stale_critical_s=None,
        active=True,
        stage="3A",
        sector_weights=_risk_on_weights(),
        metadata={"symbols": ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA"]},
    ),
    IndicatorSpec(
        code="SENTIMENT_COMPOSITE",
        name="Sentiment Composite",
        source="yahoo",
        frequency="daily",
        weight=1.1,
        invert_sign=False,
        lookback_days=365,
        expected_lag_business_days=1,
        stale_warn_s=None,
        stale_critical_s=None,
        active=True,
        stage="3A",
        sector_weights=_risk_on_weights(),
        metadata={"symbols": {"SPY": 0.5, "QQQ": 0.3, "IWM": 0.2}},
    ),
    IndicatorSpec(
        code="UNRATE",
        name="US Unemployment Rate",
        source="fred",
        frequency="monthly",
        weight=0.7,
        invert_sign=True,
        lookback_days=365,
        expected_lag_business_days=5,
        stale_warn_s=None,
        stale_critical_s=None,
        active=True,
        stage="3C",
        sector_weights=_risk_off_weights(),
        metadata={"series": "UNRATE", "deferred": True},
    ),
    IndicatorSpec(
        code="CONSUMER_HEALTH",
        name="Consumer Health Composite",
        source="fred",
        frequency="monthly",
        weight=0.7,
        invert_sign=False,
        lookback_days=365,
        expected_lag_business_days=5,
        stale_warn_s=None,
        stale_critical_s=None,
        active=True,
        stage="3C",
        sector_weights=_uniform_weights(0.6),
        metadata={"series": ["UMCSENT", "DSPIC96", "PCEC96"], "deferred": True},
    ),
]


def catalog_rows(now_ts: int | None = None) -> list[dict[str, Any]]:
    ts = int(now_ts or time.time())
    rows: list[dict[str, Any]] = []
    for spec in CATALOG:
        rows.append(
            {
                "code": spec.code,
                "name": spec.name,
                "source": spec.source,
                "frequency": spec.frequency,
                "weight": spec.weight,
                "invert_sign": 1 if spec.invert_sign else 0,
                "lookback_days": spec.lookback_days,
                "expected_lag_business_days": spec.expected_lag_business_days,
                "stale_warn_s": spec.stale_warn_s,
                "stale_critical_s": spec.stale_critical_s,
                "active": 1 if spec.active else 0,
                "stage": spec.stage,
                "sector_weight_json": json.dumps(spec.sector_weights, separators=(",", ":")),
                "heuristic_version": "1.0.0",
                "metadata_json": json.dumps(spec.metadata, separators=(",", ":")),
                "created_at": ts,
                "updated_at": ts,
            }
        )
    return rows

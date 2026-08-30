"""Structured AI input and output parsing for Autotrader V1."""
from __future__ import annotations

import json
from typing import Any

from trading.contracts import AIReview, StrategySpec


def parse_strategy_proposal(payload: str | dict[str, Any]) -> StrategySpec:
    data = json.loads(payload) if isinstance(payload, str) else payload
    return StrategySpec.model_validate(data)


def parse_trade_proposal(payload: str | dict[str, Any]) -> AIReview:
    data = json.loads(payload) if isinstance(payload, str) else payload
    return AIReview.model_validate(data)

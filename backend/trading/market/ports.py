"""Market-data boundary for the Autotrader V1 engine."""
from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from trading.contracts import MarketSnapshot, StrategySpec


class MarketDataFeed(Protocol):
    async def snapshots_for(self, strategy: StrategySpec) -> Sequence[MarketSnapshot]:
        """Return validated snapshots for the configured strategy universe."""

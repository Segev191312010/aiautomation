"""AI provider interfaces with no trading authority."""
from __future__ import annotations

from typing import Protocol

from trading.contracts import AIReview, RuleSetup, StrategySpec


class StrategyProposalProvider(Protocol):
    async def propose_strategy(self, *, context: str) -> StrategySpec:
        """Return a structured draft strategy with model and prompt provenance."""


class TradeProposalProvider(Protocol):
    async def propose_trade(
        self,
        *,
        setup: RuleSetup,
    ) -> AIReview:
        """Review an immutable setup without returning prices, quantity, or authority."""

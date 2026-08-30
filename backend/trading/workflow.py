"""Callable setup-to-shadow workflow with no scheduler or broker dependency."""
from __future__ import annotations

from collections.abc import Sequence

from trading.contracts import Decision, EngineMode, PortfolioSnapshot, RiskPolicy, RuleSetup, StrategyRecord
from trading.engine import AutotraderEngine
from trading.intelligence.provider import TradeProposalProvider


class ShadowWorkflow:
    def __init__(
        self,
        *,
        proposal_provider: TradeProposalProvider,
        engine: AutotraderEngine,
    ) -> None:
        self._proposal_provider = proposal_provider
        self._engine = engine

    async def assess_setups(
        self,
        *,
        strategy: StrategyRecord,
        setups: Sequence[RuleSetup],
        portfolio: PortfolioSnapshot,
        policy: RiskPolicy,
        admitted_symbols: frozenset[str],
    ) -> Sequence[Decision]:
        decisions: list[Decision] = []
        for setup in setups:
            review = await self._proposal_provider.propose_trade(setup=setup)
            decisions.append(
                await self._engine.assess(
                    mode=EngineMode.SHADOW,
                    strategy=strategy,
                    setup=setup,
                    review=review,
                    portfolio=portfolio,
                    policy=policy,
                    admitted_symbols=admitted_symbols,
                )
            )
        return decisions

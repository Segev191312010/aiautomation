"""Decision orchestration for the evidence-gated Autotrader V1 engine."""
from __future__ import annotations

from collections.abc import Awaitable, Callable

from trading.authority import EngineAuthority
from trading.contracts import (
    AIReview,
    Decision,
    DecisionOutcome,
    EngineMode,
    PortfolioSnapshot,
    RiskPolicy,
    RuleSetup,
    StrategyRecord,
    StrategyState,
    fingerprint_payload,
)
from trading.risk import size_and_evaluate_trade
from trading.storage.repository import record_decision

DecisionRecorder = Callable[..., Awaitable[None]]


_MODE_STATES: dict[EngineMode, StrategyState] = {
    EngineMode.BACKTEST: StrategyState.BACKTESTING,
    EngineMode.SHADOW: StrategyState.SHADOW,
    EngineMode.PAPER: StrategyState.PAPER,
}


class AutotraderEngine:
    """Assess immutable setups without granting AI order authority."""

    def __init__(
        self,
        *,
        decision_recorder: DecisionRecorder = record_decision,
    ) -> None:
        self._decision_recorder = decision_recorder

    async def assess(
        self,
        *,
        mode: EngineMode,
        strategy: StrategyRecord,
        setup: RuleSetup,
        review: AIReview,
        portfolio: PortfolioSnapshot,
        policy: RiskPolicy,
        admitted_symbols: frozenset[str],
    ) -> Decision:
        # Authority is deliberately constructed at the boundary. There is no
        # injectable production seam that could widen the enabled mode set.
        EngineAuthority().require_mode(mode)
        self._require_strategy_mode(strategy=strategy, mode=mode)
        self._require_setup_identity(strategy=strategy, setup=setup)
        if review.setup_fingerprint != setup.fingerprint():
            raise ValueError("AI review must match the immutable rule setup")

        risk = None
        if review.decision == "ABSTAIN":
            outcome = DecisionOutcome.AI_BLOCKED if review.failure_reason else DecisionOutcome.AI_ABSTAINED
        else:
            risk = size_and_evaluate_trade(
                policy=policy,
                portfolio=portfolio,
                setup=setup,
                review=review,
                admitted_symbols=admitted_symbols,
            )
            outcome = DecisionOutcome.SHADOW_ACCEPTED if risk.approved else DecisionOutcome.RISK_BLOCKED

        decision = Decision(
            run_id=setup.run_id,
            candidate_id=setup.candidate_id,
            strategy_id=strategy.strategy_id,
            strategy_version=strategy.version,
            decision_key=fingerprint_payload(
                {
                    "run_id": setup.run_id,
                    "candidate_id": setup.candidate_id,
                    "strategy_id": strategy.strategy_id,
                    "strategy_version": strategy.version,
                    "setup_fingerprint": setup.fingerprint(),
                }
            ),
            outcome=outcome,
            setup_fingerprint=setup.fingerprint(),
            ai_review=review,
            risk=risk,
        )
        await self._decision_recorder(decision=decision, owner_id=strategy.owner_id)
        return decision

    @staticmethod
    def _require_strategy_mode(*, strategy: StrategyRecord, mode: EngineMode) -> None:
        expected_state = _MODE_STATES[mode]
        if strategy.state != expected_state:
            raise ValueError(
                f"strategy {strategy.strategy_id} is {strategy.state.value}; "
                f"{mode.value} assessment requires {expected_state.value}"
            )

    @staticmethod
    def _require_setup_identity(*, strategy: StrategyRecord, setup: RuleSetup) -> None:
        if setup.strategy_id != strategy.strategy_id or setup.strategy_version != strategy.version:
            raise ValueError("rule setup must match the assessed strategy version")

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from trading.backtest.simulator import OHLCBar, simulate_bracket_trade
from trading.contracts import (
    AIReview,
    DecisionOutcome,
    FixedUniverse,
    PortfolioSnapshot,
    RiskPolicy,
    RuleCondition,
    RuleSetup,
    StrategyRecord,
    StrategySpec,
    StrategyState,
    TradeIntent,
)
from trading.engine import AutotraderEngine
from trading.workflow import ShadowWorkflow

HASH = "b" * 64


def _intent() -> TradeIntent:
    return TradeIntent(
        execution_key="execution-1",
        setup_fingerprint=HASH,
        approval_token="token" * 8,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=1),
        strategy_id="strategy-2",
        strategy_version=1,
        symbol="AAPL",
        quantity=10,
        entry_limit="100",
        stop_price="98",
        target_price="104",
    )


def _setup() -> RuleSetup:
    return RuleSetup(
        run_id="run-2",
        candidate_id="candidate-2",
        strategy_id="strategy-2",
        strategy_version=1,
        symbol="AAPL",
        opening_range_high="99",
        opening_range_low="97",
        atr="2",
        entry_limit="100",
        stop_price="98",
        target_price="104",
        bar_fingerprints=(HASH,),
        quote_fingerprint=HASH,
        detected_at=datetime.now(timezone.utc),
    )


def _strategy() -> StrategyRecord:
    spec = StrategySpec(
        name="Shadow opening breakout",
        universe=FixedUniverse(symbols=("AAPL",)),
        conditions=(RuleCondition(feature="rvol", operator="gte", value="1.5"),),
        rationale="Shadow workflow test.",
    )
    now = datetime.now(timezone.utc)
    return StrategyRecord(
        strategy_id="strategy-2",
        owner_id="demo",
        version=1,
        content_hash=spec.fingerprint(),
        spec=spec,
        state=StrategyState.SHADOW,
        created_at=now,
        updated_at=now,
    )


def _portfolio() -> PortfolioSnapshot:
    return PortfolioSnapshot(
        observed_at=datetime.now(timezone.utc),
        net_liquidation="100000",
    )


def test_backtest_uses_next_bar_and_assumes_stop_when_levels_collide():
    signal_at = datetime(2026, 1, 2, 14, 30, tzinfo=timezone.utc)
    bars = [
        OHLCBar(timestamp=signal_at, symbol="AAPL", open="100", high="101", low="99", close="100"),
        OHLCBar(timestamp=signal_at + timedelta(minutes=5), symbol="AAPL", open="100", high="101", low="99", close="100"),
        OHLCBar(timestamp=signal_at + timedelta(minutes=10), symbol="AAPL", open="100", high="105", low="97", close="102"),
    ]
    result = simulate_bracket_trade(signal_at=signal_at, intent=_intent(), bars=bars)
    assert result.status == "closed"
    assert result.entry_at == bars[1].timestamp
    assert result.exit_reason == "stop"


class _Provider:
    async def propose_trade(self, *, setup: RuleSetup):
        return AIReview(
            setup_fingerprint=setup.fingerprint(),
            decision="PROPOSE",
            confidence="0.8",
            rationale="Structured shadow review.",
            model_id="test-model",
            prompt_version="review-v1",
            response_hash=HASH,
            latency_ms=10,
            cost_usd="0",
        )


@pytest.mark.anyio
async def test_shadow_workflow_records_risk_sized_decision_without_execution():
    recorded: list[dict] = []

    async def recorder(**kwargs):
        recorded.append(kwargs)

    workflow = ShadowWorkflow(
        proposal_provider=_Provider(),
        engine=AutotraderEngine(decision_recorder=recorder),
    )
    decisions = await workflow.assess_setups(
        strategy=_strategy(),
        setups=[_setup()],
        portfolio=_portfolio(),
        policy=RiskPolicy(),
        admitted_symbols=frozenset({"AAPL"}),
    )

    assert len(decisions) == 1
    assert decisions[0].outcome == DecisionOutcome.SHADOW_ACCEPTED
    assert decisions[0].risk is not None and decisions[0].risk.intent is not None
    assert len(recorded) == 1

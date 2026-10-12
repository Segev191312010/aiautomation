from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from trading.contracts import (
    AIReview,
    Candidate,
    DecisionOutcome,
    FixedUniverse,
    PortfolioSnapshot,
    RuleCondition,
    RiskPolicy,
    StrategyRecord,
    StrategySpec,
    StrategyState,
)
from trading.orchestration import CandidateInput, ShadowPipeline
from trading.contracts import CompletedBar, QuoteSnapshot
from trading.scheduler.decision_keys import decision_key


HASH = "a" * 64
# 11:30 New York: inside the entry window. CUTOFF is the 12:00 entry cutoff.
NOW = datetime(2026, 1, 2, 16, 30, tzinfo=timezone.utc)
CUTOFF = datetime(2026, 1, 2, 17, 0, tzinfo=timezone.utc)


def _strategy() -> StrategyRecord:
    spec = StrategySpec(
        name="Pipeline ORB strategy",
        universe=FixedUniverse(symbols=("AAPL", "MSFT", "BAD")),
        conditions=(RuleCondition(feature="rvol", operator="gte", value="1.5"),),
        rationale="Pipeline fixture.",
    )
    return StrategyRecord(
        strategy_id="strategy-pipeline",
        owner_id="owner-a",
        version=1,
        content_hash=spec.fingerprint(),
        spec=spec,
        state=StrategyState.SHADOW,
        created_at=NOW,
        updated_at=NOW,
    )


def _candidate(symbol: str, con_id: int, rank: int = 1) -> Candidate:
    return Candidate(
        id=f"candidate-{symbol.lower()}",
        scanner_run_id="scanner-1",
        con_id=con_id,
        symbol=symbol,
        primary_exchange="NASDAQ",
        rank=rank,
        admission_status="admitted",
    )


def _bars(con_id: int, *, breakout: bool = True) -> tuple[CompletedBar, ...]:
    bars: list[CompletedBar] = []
    start = datetime(2026, 1, 2, 14, 30, tzinfo=timezone.utc)
    for index in range(18):
        open_price = Decimal("100")
        high = Decimal("101")
        close = Decimal("100.5")
        if index in {3, 4, 5}:
            high = Decimal("102")
            close = Decimal("101")
        if index >= 14:
            close = Decimal("103") if breakout else Decimal("100.5")
            high = Decimal("104") if breakout else Decimal("101")
        bars.append(
            CompletedBar(
                con_id=con_id,
                interval_start_utc=start + timedelta(minutes=5 * index),
                interval_end_utc=start + timedelta(minutes=5 * (index + 1)),
                open=open_price,
                high=high,
                low=Decimal("99"),
                close=close,
                volume=Decimal("10000"),
                rth=True,
                source="ibkr",
                data_mode="historical",
            )
        )
    return tuple(bars)


def _quote(con_id: int) -> QuoteSnapshot:
    return QuoteSnapshot(
        con_id=con_id,
        bid=Decimal("102.99"),
        ask=Decimal("103"),
        observed_at=NOW,
        min_tick=Decimal("0.01"),
    )


def _input(symbol: str, con_id: int, *, breakout: bool = True) -> CandidateInput:
    return CandidateInput(
        candidate=_candidate(symbol, con_id),
        bars=_bars(con_id, breakout=breakout),
        quote=_quote(con_id),
        reference_price="103",
        average_dollar_volume="25000000",
    )


def _portfolio() -> PortfolioSnapshot:
    return PortfolioSnapshot(observed_at=NOW, net_liquidation="100000")


class _Provider:
    def __init__(self, *, fail: bool = False):
        self.fail = fail
        self.calls = 0

    async def propose_trade(self, *, setup):
        self.calls += 1
        if self.fail:
            raise RuntimeError("provider unavailable")
        return AIReview(
            setup_fingerprint=setup.fingerprint(),
            decision="PROPOSE",
            confidence="0.8",
            rationale="Fixture review.",
            model_id="fixture-model",
            prompt_version="fixture-prompt",
            response_hash=HASH,
            latency_ms=1,
            cost_usd="0",
        )


@pytest.mark.anyio
async def test_pipeline_decides_breakouts_and_closes_pending_candidates_at_cutoff():
    recorded = []

    async def recorder(**kwargs):
        recorded.append(kwargs["decision"])

    provider = _Provider()
    pipeline = ShadowPipeline(proposal_provider=provider, decision_recorder=recorder)
    inputs = [_input("AAPL", 101), _input("MSFT", 102, breakout=False)]
    decisions = await pipeline.run(
        run_id="run-1",
        strategy=_strategy(),
        candidates=inputs,
        portfolio=_portfolio(),
        policy=RiskPolicy(),
        session_date=date(2026, 1, 2),
        now=NOW,
    )

    # MSFT has no breakout yet and stays pending inside the entry window.
    assert [decision.outcome for decision in decisions] == [DecisionOutcome.SHADOW_ACCEPTED]
    expected_snapshot = _input("AAPL", 101).fingerprint()
    assert decisions[0].decision_key == decision_key(
        strategy_id="strategy-pipeline",
        strategy_version=1,
        snapshot_fingerprint=expected_snapshot,
    )

    # The cutoff sweep re-polls with newer data: AAPL's recorded decision is
    # final despite a changed snapshot, and MSFT becomes terminal.
    later = [
        CandidateInput(
            candidate=item.candidate,
            bars=item.bars,
            quote=None,
            reference_price=item.reference_price,
            average_dollar_volume=item.average_dollar_volume,
        )
        for item in inputs
    ]
    final = await pipeline.run(
        run_id="run-1",
        strategy=_strategy(),
        candidates=later,
        portfolio=_portfolio(),
        policy=RiskPolicy(),
        session_date=date(2026, 1, 2),
        now=CUTOFF,
        existing_decisions={decisions[0].candidate_id: decisions[0]},
    )

    assert [decision.outcome for decision in final] == [
        DecisionOutcome.SHADOW_ACCEPTED,
        DecisionOutcome.RULE_REJECTED,
    ]
    assert final[0] == decisions[0]
    assert recorded == [decisions[0], final[1]]
    assert provider.calls == 1


@pytest.mark.anyio
async def test_breakout_without_quote_asks_for_one_and_transient_data_waits():
    pipeline = ShadowPipeline(proposal_provider=_Provider(), decision_recorder=_record_noop)
    item = _input("AAPL", 101)
    unquoted = CandidateInput(
        candidate=item.candidate,
        bars=item.bars,
        quote=None,
        reference_price=item.reference_price,
        average_dollar_volume=item.average_dollar_volume,
    )
    common = dict(
        run_id="run-q",
        strategy=_strategy(),
        portfolio=_portfolio(),
        policy=RiskPolicy(),
        session_date=date(2026, 1, 2),
        admitted_symbols=frozenset({"AAPL"}),
    )

    needs_quote = await pipeline.assess_one(item=unquoted, now=NOW, **common)
    assert needs_quote.decision is None and needs_quote.needs_quote is True

    stale = await pipeline.assess_one(item=item, now=NOW + timedelta(seconds=30), **common)
    assert stale.decision is None and stale.pending_reason == "quote_stale"

    no_bars = CandidateInput(
        candidate=item.candidate,
        bars=(),
        quote=None,
        reference_price=item.reference_price,
        average_dollar_volume=item.average_dollar_volume,
    )
    waiting = await pipeline.assess_one(item=no_bars, now=NOW, **common)
    assert waiting.decision is None
    closed = await pipeline.assess_one(item=no_bars, now=CUTOFF, **common)
    assert closed.decision is not None
    assert closed.decision.outcome == DecisionOutcome.DATA_BLOCKED


@pytest.mark.anyio
async def test_no_setup_is_built_at_or_after_the_entry_cutoff():
    provider = _Provider()
    pipeline = ShadowPipeline(proposal_provider=provider, decision_recorder=_record_noop)
    item = _input("AAPL", 101)
    late_quote = CandidateInput(
        candidate=item.candidate,
        bars=item.bars,
        quote=QuoteSnapshot(
            con_id=101, bid="102.99", ask="103", observed_at=CUTOFF, min_tick="0.01"
        ),
        reference_price=item.reference_price,
        average_dollar_volume=item.average_dollar_volume,
    )

    result = await pipeline.assess_one(
        run_id="run-late",
        strategy=_strategy(),
        item=late_quote,
        portfolio=_portfolio(),
        policy=RiskPolicy(),
        session_date=date(2026, 1, 2),
        admitted_symbols=frozenset({"AAPL"}),
        now=CUTOFF,
    )

    assert result.decision is not None
    assert result.decision.outcome == DecisionOutcome.DATA_BLOCKED
    assert provider.calls == 0


@pytest.mark.anyio
async def test_provider_failure_is_recorded_as_ai_blocked_without_rules_only_fallback():
    recorded = []

    async def recorder(**kwargs):
        recorded.append(kwargs["decision"])

    decisions = await ShadowPipeline(
        proposal_provider=_Provider(fail=True),
        decision_recorder=recorder,
    ).run(
        run_id="run-2",
        strategy=_strategy(),
        candidates=[_input("AAPL", 101)],
        portfolio=_portfolio(),
        policy=RiskPolicy(),
        session_date=date(2026, 1, 2),
        now=NOW,
    )

    assert decisions[0].outcome == DecisionOutcome.AI_BLOCKED
    assert decisions[0].ai_review is not None
    assert decisions[0].ai_review.failure_reason == "provider_unavailable"
    assert decisions[0].risk is None
    assert recorded == list(decisions)


@pytest.mark.anyio
async def test_missing_data_at_cutoff_is_terminal_and_does_not_call_ai():
    recorded = []

    async def recorder(**kwargs):
        recorded.append(kwargs["decision"])

    provider = _Provider()
    bad = _input("AAPL", 101)
    bad = CandidateInput(
        candidate=bad.candidate,
        bars=bad.bars,
        quote=None,
        reference_price=bad.reference_price,
        average_dollar_volume=bad.average_dollar_volume,
    )
    decisions = await ShadowPipeline(
        proposal_provider=provider,
        decision_recorder=recorder,
    ).run(
        run_id="run-3",
        strategy=_strategy(),
        candidates=[bad],
        portfolio=_portfolio(),
        policy=RiskPolicy(),
        session_date=date(2026, 1, 2),
        now=CUTOFF,
    )

    assert decisions[0].outcome == DecisionOutcome.DATA_BLOCKED
    assert provider.calls == 0
    assert recorded == list(decisions)


@pytest.mark.anyio
async def test_duplicate_candidate_ids_are_rejected_before_processing():
    async def recorder(**_: object) -> None:
        return None

    pipeline = ShadowPipeline(proposal_provider=_Provider(), decision_recorder=recorder)
    with pytest.raises(ValueError, match="more than once"):
        await pipeline.run(
            run_id="run-4",
            strategy=_strategy(),
            candidates=[_input("AAPL", 101), _input("AAPL", 101)],
            portfolio=_portfolio(),
            policy=RiskPolicy(),
            session_date=date(2026, 1, 2),
            now=NOW,
        )


@pytest.mark.anyio
async def test_invalid_provider_review_is_blocked_without_rules_only_fallback():
    class InvalidProvider:
        async def propose_trade(self, *, setup):
            return object()

    decisions = await ShadowPipeline(
        proposal_provider=InvalidProvider(),
        decision_recorder=_record_noop,
    ).run(
        run_id="run-5",
        strategy=_strategy(),
        candidates=[_input("AAPL", 101)],
        portfolio=_portfolio(),
        policy=RiskPolicy(),
        session_date=date(2026, 1, 2),
        now=NOW,
    )

    assert decisions[0].outcome == DecisionOutcome.AI_BLOCKED
    assert decisions[0].ai_review is not None
    assert decisions[0].ai_review.failure_reason == "invalid_provider_review"


async def _record_noop(**_: object) -> None:
    return None

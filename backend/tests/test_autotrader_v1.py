from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from trading.authority import EngineAuthorityError
from trading.contracts import (
    AIReview,
    Candidate,
    CompletedBar,
    Decision,
    DecisionOutcome,
    EngineMode,
    FixedUniverse,
    MarketSnapshot,
    PortfolioSnapshot,
    PositionExposure,
    RiskPolicy,
    RuleCondition,
    RuleSetup,
    ScannerUniverse,
    StrategyRecord,
    StrategySpec,
    StrategyState,
    QuoteSnapshot,
    RiskApproval,
)
from trading.engine import AutotraderEngine
from trading.market import scan_strategy
from trading.risk import size_and_evaluate_trade
from trading.strategy_lifecycle import require_strategy_transition

HASH = "a" * 64


def _spec(*, name: str = "Opening range breakout") -> StrategySpec:
    return StrategySpec(
        name=name,
        universe=FixedUniverse(symbols=("AAPL",)),
        timeframe="5m",
        conditions=(RuleCondition(feature="rvol", operator="gte", value="1.5"),),
        ai_generated=True,
        model_version="proposal-model-v1",
        prompt_version="strategy-prompt-v1",
        rationale="Structured strategy proposal.",
    )


def _strategy(*, state: StrategyState = StrategyState.SHADOW) -> StrategyRecord:
    spec = _spec()
    now = datetime.now(timezone.utc)
    return StrategyRecord(
        strategy_id="strategy-1",
        owner_id="demo",
        version=1,
        content_hash=spec.fingerprint(),
        spec=spec,
        state=state,
        created_at=now,
        updated_at=now,
    )


def _snapshot(*, rvol: str = "2", regular_session: bool = True) -> MarketSnapshot:
    return MarketSnapshot(
        symbol="AAPL",
        timeframe="5m",
        timestamp=datetime.now(timezone.utc),
        regular_session=regular_session,
        features={"rvol": Decimal(rvol), "close": Decimal("100")},
        source="historical",
    )


def _setup() -> RuleSetup:
    return RuleSetup(
        run_id="run-1",
        candidate_id="candidate-1",
        strategy_id="strategy-1",
        strategy_version=1,
        symbol="AAPL",
        opening_range_high="100",
        opening_range_low="98",
        atr="1",
        entry_limit="100.10",
        stop_price="99.10",
        target_price="102.10",
        bar_fingerprints=(HASH,),
        quote_fingerprint=HASH,
        detected_at=datetime.now(timezone.utc),
    )


def _review(setup: RuleSetup, *, decision: str = "PROPOSE") -> AIReview:
    return AIReview(
        setup_fingerprint=setup.fingerprint(),
        decision=decision,
        confidence="0.8",
        rationale="The setup satisfies the fixed review schema.",
        model_id="openai-test-model",
        prompt_version="review-v1",
        response_hash=HASH,
        latency_ms=50,
        cost_usd="0.01",
    )


def _portfolio() -> PortfolioSnapshot:
    return PortfolioSnapshot(
        observed_at=datetime.now(timezone.utc),
        net_liquidation="100000",
    )


def test_strategy_universe_is_immutable_and_scanner_policy_is_versioned():
    fixed = _spec()
    scanner = fixed.model_copy(
        update={
            "universe": ScannerUniverse(
                scanner_policy_id="hot-volume",
                scanner_policy_version=1,
                scanner_policy_hash=HASH,
            )
        }
    )

    assert fixed.fingerprint() != scanner.fingerprint()
    assert scanner.universe.kind == "scanner"


def test_ai_review_cannot_carry_order_fields_and_timestamps_require_timezone():
    setup = _setup()
    with pytest.raises(ValidationError, match="extra_forbidden"):
        AIReview.model_validate(
            {
                **_review(setup).model_dump(mode="json"),
                "quantity": 100,
                "entry_limit": "100",
            }
        )
    with pytest.raises(ValidationError, match="timezone-aware"):
        MarketSnapshot(
            symbol="AAPL",
            timestamp=datetime(2026, 1, 1, 9, 30),
            regular_session=True,
            features={"close": "100"},
            source="historical",
        )


def test_strategy_record_and_portfolio_fingerprints_are_immutable_provenance():
    spec = _spec()
    now = datetime.now(timezone.utc)
    with pytest.raises(ValidationError, match="content_hash"):
        StrategyRecord(
            strategy_id="strategy-1",
            owner_id="demo",
            version=1,
            content_hash=HASH,
            spec=spec,
            created_at=now,
            updated_at=now,
        )

    portfolio = _portfolio()
    assert len(portfolio.fingerprint) == 64
    changed = portfolio.model_copy(update={"net_liquidation": "99999"})
    assert changed.fingerprint != portfolio.fingerprint
    with pytest.raises(ValidationError, match="portfolio fingerprint"):
        PortfolioSnapshot(
            fingerprint=HASH,
            observed_at=portfolio.observed_at,
            net_liquidation=portfolio.net_liquidation,
        )


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        ("admitted", "not allowed on admitted candidate"),
        ("rejected", None),
        ("rejected", "   "),
    ],
)
def test_candidate_admission_requires_matching_rejection_shape(status, reason):
    values = {
        "scanner_run_id": "scan-1",
        "con_id": 101,
        "symbol": "AAPL",
        "primary_exchange": "NASDAQ",
        "rank": 1,
        "admission_status": status,
    }
    if reason is not None:
        values["rejection_reason"] = reason
    with pytest.raises(ValidationError, match="rejection"):
        Candidate(**values)

    admitted = Candidate(
        scanner_run_id="scan-1",
        con_id=101,
        symbol="AAPL",
        primary_exchange="NASDAQ",
        rank=1,
        admission_status="admitted",
    )
    rejected = Candidate(
        scanner_run_id="scan-1",
        con_id=101,
        symbol="AAPL",
        primary_exchange="NASDAQ",
        rank=1,
        admission_status="rejected",
        rejection_reason="not a common stock",
    )
    assert admitted.rejection_reason is None
    assert rejected.rejection_reason == "not a common stock"


def test_completed_bar_requires_exact_five_minute_interval():
    start = datetime(2026, 1, 2, 14, 30, tzinfo=timezone.utc)
    values = {
        "con_id": 101,
        "interval_start_utc": start,
        "interval_end_utc": start + timedelta(minutes=5),
        "open": "100",
        "high": "101",
        "low": "99",
        "close": "100.5",
        "volume": "1000",
        "rth": True,
        "source": "ibkr",
        "data_mode": "historical",
    }
    assert CompletedBar(**values).completed is True
    with pytest.raises(ValidationError, match="exactly five minutes"):
        CompletedBar(**{**values, "interval_end_utc": start + timedelta(minutes=1)})


def test_quote_snapshot_exposes_two_second_freshness_contract():
    observed_at = datetime(2026, 1, 2, 14, 30, tzinfo=timezone.utc)
    quote = QuoteSnapshot(
        con_id=101,
        bid="100",
        ask="100.05",
        observed_at=observed_at,
        min_tick="0.01",
    )
    assert quote.age_seconds(observed_at + timedelta(seconds=1)) == Decimal("1.0")
    assert quote.is_fresh(now=observed_at + timedelta(seconds=2))
    assert not quote.is_fresh(now=observed_at + timedelta(seconds=2, microseconds=1))
    assert not quote.is_fresh(now=observed_at - timedelta(microseconds=1))
    with pytest.raises(ValueError, match="stale"):
        quote.require_fresh(now=observed_at + timedelta(seconds=3))


def test_risk_approval_and_decision_outcomes_cannot_disagree():
    setup = _setup()
    review = _review(setup)
    portfolio = _portfolio()
    approval = size_and_evaluate_trade(
        policy=RiskPolicy(),
        portfolio=portfolio,
        setup=setup,
        review=review,
        admitted_symbols=frozenset({"AAPL"}),
        now=datetime(2026, 1, 2, 14, 30, tzinfo=timezone.utc),
    )
    assert approval.approved is True
    assert approval.outcome == "approved"
    assert approval.intent is not None

    approval_payload = approval.model_dump(mode="json")
    with pytest.raises(ValidationError, match="blocking codes"):
        RiskApproval.model_validate({**approval_payload, "codes": ["unexpected"]})
    with pytest.raises(ValidationError, match="at least one code"):
        RiskApproval.model_validate({**approval_payload, "approved": False, "intent": None})

    decision_values = {
        "run_id": setup.run_id,
        "candidate_id": setup.candidate_id,
        "strategy_id": setup.strategy_id,
        "strategy_version": setup.strategy_version,
        "decision_key": HASH,
        "setup_fingerprint": setup.fingerprint(),
        "ai_review": review,
        "risk": approval,
    }
    accepted = Decision(
        **decision_values,
        outcome=DecisionOutcome.SHADOW_ACCEPTED,
    )
    assert accepted.outcome == DecisionOutcome.SHADOW_ACCEPTED

    with pytest.raises(ValidationError, match="blocked risk approval"):
        Decision(**decision_values, outcome=DecisionOutcome.RISK_BLOCKED)
    with pytest.raises(ValidationError, match="data or rule rejection"):
        Decision(**decision_values, outcome=DecisionOutcome.DATA_BLOCKED)


def test_scanner_emits_only_fixed_regular_session_matches():
    record = _strategy()
    signals = scan_strategy(
        record=record,
        snapshots=[_snapshot(), _snapshot(regular_session=False), _snapshot(rvol="1.4")],
    )

    assert len(signals) == 1
    assert signals[0].strategy_id == record.strategy_id


def test_risk_owns_quantity_and_blocks_pyramiding():
    setup = _setup()
    review = _review(setup)
    policy = RiskPolicy()

    result = size_and_evaluate_trade(
        policy=policy,
        portfolio=_portfolio(),
        setup=setup,
        review=review,
        admitted_symbols=frozenset({"AAPL"}),
    )
    assert result.approved is True
    assert result.intent is not None
    assert result.intent.quantity == 149
    assert result.intent.notional_amount <= Decimal("15000")
    assert result.intent.risk_amount <= Decimal("500")

    crowded = _portfolio().model_copy(
        update={
            "positions": (
                PositionExposure(symbol="AAPL", quantity=1, market_value="100"),
            )
        }
    )
    blocked = size_and_evaluate_trade(
        policy=policy,
        portfolio=crowded,
        setup=setup,
        review=review,
        admitted_symbols=frozenset({"AAPL"}),
    )
    assert "pyramiding_blocked" in blocked.codes


def test_lifecycle_has_no_live_path_and_generic_paper_transition_is_denied():
    assert "live" not in {mode.value for mode in EngineMode}
    assert "live" not in {state.value for state in StrategyState}
    with pytest.raises(ValueError, match="not allowed"):
        require_strategy_transition(
            record=_strategy(state=StrategyState.SHADOW),
            target=StrategyState.PAPER,
        )


@pytest.mark.anyio
async def test_engine_records_shadow_decision_and_refuses_paper():
    recorded: list[dict] = []

    async def recorder(**kwargs):
        recorded.append(kwargs)

    setup = _setup()
    engine = AutotraderEngine(decision_recorder=recorder)
    decision = await engine.assess(
        mode=EngineMode.SHADOW,
        strategy=_strategy(),
        setup=setup,
        review=_review(setup),
        portfolio=_portfolio(),
        policy=RiskPolicy(),
        admitted_symbols=frozenset({"AAPL"}),
    )

    assert decision.outcome == DecisionOutcome.SHADOW_ACCEPTED
    assert decision.risk is not None and decision.risk.approved
    assert recorded[0]["decision"].id == decision.id

    with pytest.raises(EngineAuthorityError, match="paper mode is unavailable"):
        await engine.assess(
            mode=EngineMode.PAPER,
            strategy=_strategy(state=StrategyState.PAPER),
            setup=setup,
            review=_review(setup),
            portfolio=_portfolio(),
            policy=RiskPolicy(),
            admitted_symbols=frozenset({"AAPL"}),
        )


@pytest.mark.anyio
async def test_repository_publishes_immutable_versions_and_uses_cas(tmp_path, monkeypatch):
    from config import cfg
    from db.core import init_db
    from trading.storage.repository import (
        create_strategy,
        get_strategy,
        list_strategy_versions,
        publish_strategy_version,
        transition_strategy,
    )

    monkeypatch.setattr(cfg, "DB_PATH", str(tmp_path / "autotrader_v1.db"))
    await init_db()
    created = await create_strategy(spec=_spec(), owner_id="demo", actor="demo")
    version_two = await publish_strategy_version(
        strategy_id=created.strategy_id,
        owner_id="demo",
        base_version=1,
        spec=_spec(name="Opening range breakout v2"),
        actor="demo",
        reason="Versioned policy change",
    )

    versions = await list_strategy_versions(strategy_id=created.strategy_id, owner_id="demo")
    assert [record.version for record in versions] == [1, 2]
    assert versions[0].content_hash == created.content_hash
    assert version_two.state == StrategyState.DRAFT

    transitioned = await transition_strategy(
        strategy_id=created.strategy_id,
        version=1,
        owner_id="demo",
        expected_revision=0,
        target=StrategyState.BACKTESTING,
        actor="demo",
        reason="Begin evidence run",
    )
    assert transitioned.revision == 1
    with pytest.raises(ValueError, match="stale"):
        await transition_strategy(
            strategy_id=created.strategy_id,
            version=1,
            owner_id="demo",
            expected_revision=0,
            target=StrategyState.PAUSED,
            actor="demo",
            reason="Stale writer",
        )
    stored = await get_strategy(
        strategy_id=created.strategy_id, owner_id="demo", version=1
    )
    assert stored is not None and stored.state == StrategyState.BACKTESTING

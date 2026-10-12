"""The shadow pipeline and leased session run whichever strategy they are given."""
from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from trading.contracts import (
    AIReview,
    Candidate,
    CompletedBar,
    DecisionOutcome,
    EngineMode,
    FixedUniverse,
    PortfolioSnapshot,
    QuoteSnapshot,
    RiskPolicy,
    RuleCondition,
    RuleSetup,
    Run,
    ScannerRun,
    StrategyRecord,
    StrategySpec,
    StrategyState,
    fingerprint_payload,
)
from trading.orchestration import CandidateInput, RepositoryBackedShadowRunner, ShadowPipeline
from trading.orchestration.pipeline import entry_cutoff_utc
from trading.rules import (
    NO_SIGNAL_YET,
    QUOTE_REQUIRED,
    MomoStrategy,
    ORBConfig,
    ORBStrategy,
    StrategyResult,
    entry_window_utc,
    evaluate_orb,
)

NEW_YORK = ZoneInfo("America/New_York")
SESSION = date(2026, 8, 27)
PRIOR = date(2026, 8, 26)
BAR = timedelta(minutes=5)
HASH = "d" * 64
MOMO_CON_ID = 501
ORB_CON_ID = 502


def _et(day: date, hour: int, minute: int, second: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute, second), tzinfo=NEW_YORK).astimezone(
        timezone.utc
    )


def _bar(
    con_id: int,
    start: datetime,
    *,
    open_price: str = "100",
    high: str = "100.5",
    low: str = "99.5",
    close: str = "100",
    volume: str = "1000",
) -> CompletedBar:
    return CompletedBar(
        con_id=con_id,
        interval_start_utc=start,
        interval_end_utc=start + BAR,
        open=open_price,
        high=high,
        low=low,
        close=close,
        volume=volume,
        rth=True,
        source="ibkr",
        data_mode="historical",
    )


def _momo_bars(count: int = 13, *, signal: bool = True) -> tuple[CompletedBar, ...]:
    """A quiet prior session, then ``count`` session bars; the last may break out."""
    prior = [_bar(MOMO_CON_ID, _et(PRIOR, 9, 30) + BAR * index) for index in range(78)]
    start = _et(SESSION, 9, 30)
    today = [_bar(MOMO_CON_ID, start + BAR * index) for index in range(count - 1)]
    last = start + BAR * (count - 1)
    breakout = {"open_price": "100.2", "high": "101.2", "low": "100.1", "close": "101"}
    today.append(
        _bar(MOMO_CON_ID, last, volume="2000", **breakout) if signal else _bar(MOMO_CON_ID, last)
    )
    return (*prior, *today)


def _orb_bars() -> tuple[CompletedBar, ...]:
    """ORB fixture: 09:30-09:45 range high 101, first breakout close at 10:45 ET."""
    start = _et(SESSION, 9, 30)
    bars: list[CompletedBar] = []
    for index in range(18):
        high, close = ("102", "101") if index in {3, 4, 5} else ("101", "100.5")
        if index >= 14:
            high, close = "104", "103"
        bars.append(
            _bar(ORB_CON_ID, start + BAR * index, high=high, low="99", close=close, volume="10000")
        )
    return tuple(bars)


def _after(bars: Sequence[CompletedBar], seconds: int = 5) -> datetime:
    return bars[-1].interval_end_utc + timedelta(seconds=seconds)


def _candidate(symbol: str = "MOMO", con_id: int = MOMO_CON_ID) -> Candidate:
    return Candidate(
        id=f"candidate-{symbol.lower()}",
        scanner_run_id="scanner-injection",
        con_id=con_id,
        symbol=symbol,
        primary_exchange="NASDAQ",
        rank=1,
        admission_status="admitted",
    )


def _quote(
    now: datetime, *, bid: str = "100.99", ask: str = "101.00", con_id: int = MOMO_CON_ID
) -> QuoteSnapshot:
    return QuoteSnapshot(con_id=con_id, bid=bid, ask=ask, observed_at=now, min_tick="0.01")


def _item(
    bars: Sequence[CompletedBar],
    *,
    quote: QuoteSnapshot | None = None,
    candidate: Candidate | None = None,
) -> CandidateInput:
    return CandidateInput(
        candidate=candidate or _candidate(),
        bars=tuple(bars),
        quote=quote,
        reference_price="100",
        average_dollar_volume="25000000",
    )


def _strategy_record() -> StrategyRecord:
    spec = StrategySpec(
        name="Injection strategy",
        universe=FixedUniverse(symbols=("MOMO", "ORB")),
        conditions=(RuleCondition(feature="rvol", operator="gte", value="2"),),
        rationale="Strategy injection fixture.",
    )
    created = _et(SESSION, 8, 0)
    return StrategyRecord(
        strategy_id="strategy-injection",
        owner_id="owner-a",
        version=1,
        content_hash=spec.fingerprint(),
        spec=spec,
        state=StrategyState.SHADOW,
        created_at=created,
        updated_at=created,
    )


def _portfolio() -> PortfolioSnapshot:
    return PortfolioSnapshot(observed_at=_et(SESSION, 9, 0), net_liquidation="100000")


def _common(symbol: str = "MOMO") -> dict[str, object]:
    return {
        "run_id": "run-injection",
        "strategy": _strategy_record(),
        "portfolio": _portfolio(),
        "policy": RiskPolicy(),
        "session_date": SESSION,
        "admitted_symbols": frozenset({symbol}),
    }


class _Provider:
    def __init__(self) -> None:
        self.setups: list[RuleSetup] = []

    @property
    def calls(self) -> int:
        return len(self.setups)

    async def propose_trade(self, *, setup: RuleSetup) -> AIReview:
        self.setups.append(setup)
        return AIReview(
            setup_fingerprint=setup.fingerprint(),
            decision="PROPOSE",
            confidence="0.8",
            rationale="Injection review.",
            model_id="test-model",
            prompt_version="test-prompt",
            response_hash=HASH,
            latency_ms=1,
            cost_usd="0",
        )


class _Recorder:
    def __init__(self) -> None:
        self.decisions: list[object] = []

    async def __call__(self, **kwargs: object) -> None:
        self.decisions.append(kwargs["decision"])


class _StubStrategy:
    """Returns a fixed result inside a fixed window and records what it saw."""

    def __init__(
        self,
        result: StrategyResult,
        *,
        cutoff: datetime,
        start: datetime | None = None,
        kind: str = "stub_kind",
    ) -> None:
        self._result = result
        self._window = (start or cutoff - timedelta(hours=1), cutoff)
        self._kind = kind
        self.quotes: list[QuoteSnapshot | None] = []

    @property
    def strategy_kind(self) -> str:
        return self._kind

    def entry_window(self, session_date: date) -> tuple[datetime, datetime]:
        return self._window

    def evaluate(self, **kwargs: object) -> StrategyResult:
        quote = kwargs["quote"]
        assert quote is None or isinstance(quote, QuoteSnapshot)
        self.quotes.append(quote)
        return self._result


def _stub_setup(*, kind: str = "stub_kind", candidate: Candidate | None = None) -> RuleSetup:
    owner = candidate or _candidate()
    return RuleSetup(
        run_id="run-injection",
        candidate_id=owner.id,
        strategy_id="strategy-injection",
        strategy_version=1,
        strategy_kind=kind,
        symbol=owner.symbol,
        atr="1",
        entry_limit="101",
        stop_price="100",
        target_price="103",
        bar_fingerprints=(HASH,),
        quote_fingerprint=HASH,
        detected_at=_et(SESSION, 10, 35),
    )


@pytest.mark.anyio
async def test_momo_pipeline_requests_a_quote_then_accepts_the_setup():
    provider, recorder = _Provider(), _Recorder()
    pipeline = ShadowPipeline(
        proposal_provider=provider, decision_recorder=recorder, strategy=MomoStrategy()
    )
    assert pipeline.strategy.strategy_kind == "momo_hod"
    bars = _momo_bars()
    now = _after(bars)

    unquoted = await pipeline.assess_one(item=_item(bars), now=now, **_common())
    assert unquoted.decision is None
    assert unquoted.needs_quote is True and unquoted.pending_reason == QUOTE_REQUIRED

    accepted = await pipeline.assess_one(item=_item(bars, quote=_quote(now)), now=now, **_common())
    assert accepted.decision is not None
    assert accepted.decision.outcome == DecisionOutcome.SHADOW_ACCEPTED
    assert provider.calls == 1
    setup = provider.setups[0]
    assert setup.strategy_kind == "momo_hod" and set(setup.levels) == {
        "prior_day_high",
        "vwap",
        "signal_bar_volume",
        "avg_bar_volume",
        "rvol",
    }
    assert accepted.decision.setup_fingerprint == setup.fingerprint()
    assert accepted.decision.risk is not None and accepted.decision.risk.approved is True
    assert recorder.decisions == [accepted.decision]


@pytest.mark.anyio
async def test_retryable_momo_rejection_stays_pending_until_the_strategy_cutoff():
    provider, recorder = _Provider(), _Recorder()
    pipeline = ShadowPipeline(
        proposal_provider=provider, decision_recorder=recorder, strategy=MomoStrategy()
    )
    bars = _momo_bars()
    now = _after(bars)
    chased = await pipeline.assess_one(
        item=_item(bars, quote=_quote(now, bid="101.20", ask="101.25")), now=now, **_common()
    )
    assert chased.decision is None and chased.needs_quote is False
    assert chased.pending_reason == "breakout_chase_too_wide"

    # 12:00 ET is ORB's cutoff but inside MOMO's window, so nothing is final yet.
    noon = _momo_bars(30, signal=False)
    waiting = await pipeline.assess_one(item=_item(noon), now=_after(noon), **_common())
    assert waiting.decision is None and waiting.pending_reason == NO_SIGNAL_YET

    close = _momo_bars(66, signal=False)
    final = await pipeline.assess_one(item=_item(close), now=_et(SESSION, 15, 0), **_common())
    assert final.decision is not None
    assert final.decision.outcome == DecisionOutcome.RULE_REJECTED
    assert provider.calls == 0
    assert recorder.decisions == [final.decision]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("result", "before", "at_cutoff"),
    [
        (StrategyResult(None, "stub_retry", retryable=True), None, DecisionOutcome.RULE_REJECTED),
        (
            StrategyResult(None, "stub_reject"),
            DecisionOutcome.RULE_REJECTED,
            DecisionOutcome.RULE_REJECTED,
        ),
        # A data rejection waits even when the strategy does not flag it.
        (StrategyResult(None, "bar_feed_gap"), None, DecisionOutcome.DATA_BLOCKED),
        (StrategyResult(None, QUOTE_REQUIRED, retryable=True), None, DecisionOutcome.DATA_BLOCKED),
        # NO_SIGNAL_YET at the cutoff must not leave the candidate pending forever.
        (StrategyResult(None, NO_SIGNAL_YET, retryable=True), None, DecisionOutcome.RULE_REJECTED),
    ],
)
async def test_pending_and_terminal_mapping_follows_the_strategy_window(result, before, at_cutoff):
    cutoff = _et(SESSION, 11, 0)
    strategy = _StubStrategy(result, cutoff=cutoff)
    pipeline = ShadowPipeline(
        proposal_provider=_Provider(), decision_recorder=_Recorder(), strategy=strategy
    )
    quoted = _item(_momo_bars(), quote=_quote(cutoff - timedelta(seconds=1)))

    early = await pipeline.assess_one(item=quoted, now=cutoff - timedelta(seconds=1), **_common())
    if before is None:
        assert early.decision is None and early.pending_reason == result.rejection_reason
        assert early.needs_quote is (result.rejection_reason == QUOTE_REQUIRED)
    else:
        assert early.decision is not None and early.decision.outcome == before

    late = await pipeline.assess_one(item=quoted, now=cutoff, **_common())
    assert late.decision is not None and late.decision.outcome == at_cutoff
    # The pipeline withholds the quote from the strategy at its cutoff.
    assert strategy.quotes[-1] is None


def test_session_cutoff_comes_from_the_strategy_window():
    assert entry_cutoff_utc(SESSION, ORBStrategy()) == _et(SESSION, 12, 0)
    assert entry_cutoff_utc(SESSION, MomoStrategy()) == _et(SESSION, 15, 0)
    assert ORBStrategy().entry_window(SESSION) == (_et(SESSION, 9, 50), _et(SESSION, 12, 0))

    reject = StrategyResult(None, "stub_reject")
    naive = _StubStrategy(reject, cutoff=datetime(2026, 8, 27, 15, 0))
    with pytest.raises(ValueError):
        entry_window_utc(naive, SESSION)
    inverted = _StubStrategy(reject, cutoff=_et(SESSION, 11, 0), start=_et(SESSION, 12, 0))
    with pytest.raises(ValueError):
        entry_cutoff_utc(SESSION, inverted)


@pytest.mark.anyio
async def test_orb_remains_the_default_strategy_and_passes_through_unchanged():
    provider = _Provider()
    pipeline = ShadowPipeline(proposal_provider=provider, decision_recorder=_Recorder())
    assert isinstance(pipeline.strategy, ORBStrategy)
    assert pipeline.strategy.strategy_kind == "orb"
    assert pipeline.strategy.config == ORBConfig()

    bars = _orb_bars()
    now = _et(SESSION, 11, 30)
    quote = _quote(now, bid="102.99", ask="103", con_id=ORB_CON_ID)
    candidate = _candidate("ORB", ORB_CON_ID)
    params: dict[str, object] = {
        "candidate": candidate,
        "bars": bars,
        "session_date": SESSION,
        "run_id": "run-injection",
        "strategy_id": "strategy-injection",
        "strategy_version": 1,
        "reference_price": "103",
        "average_dollar_volume": "25000000",
        "candidate_input_fingerprint": HASH,
        "now": now,
    }
    direct = evaluate_orb(quote=quote, **params)
    adapted = ORBStrategy().evaluate(quote=quote, **params)
    assert direct.setup is not None and adapted.setup == direct.setup
    assert adapted.setup.strategy_kind == "orb" and adapted.setup.levels == {}

    assert ORBStrategy().evaluate(quote=None, **params).retryable is True
    chased = ORBStrategy().evaluate(
        quote=_quote(now, bid="103.99", ask="104", con_id=ORB_CON_ID), **params
    )
    assert chased.rejection_reason == "breakout_chase_too_wide" and chased.retryable is False

    item = CandidateInput(
        candidate=candidate,
        bars=bars,
        quote=quote,
        reference_price="103",
        average_dollar_volume="25000000",
    )
    decision = await pipeline.assess_one(item=item, now=now, **_common("ORB"))
    assert decision.decision is not None
    assert decision.decision.outcome == DecisionOutcome.SHADOW_ACCEPTED
    assert provider.setups[0].opening_range_high == Decimal("101")


@pytest.mark.anyio
async def test_a_strategy_setup_outside_its_window_or_identity_never_reaches_review():
    cutoff = _et(SESSION, 11, 0)
    provider = _Provider()
    late = ShadowPipeline(
        proposal_provider=provider,
        decision_recorder=_Recorder(),
        strategy=_StubStrategy(StrategyResult(_stub_setup()), cutoff=cutoff),
    )
    with pytest.raises(ValueError, match="cutoff"):
        await late.assess_one(item=_item(_momo_bars()), now=cutoff, **_common())

    foreign = ShadowPipeline(
        proposal_provider=provider,
        decision_recorder=_Recorder(),
        strategy=_StubStrategy(StrategyResult(_stub_setup(kind="other_kind")), cutoff=cutoff),
    )
    with pytest.raises(ValueError, match="does not belong"):
        await foreign.assess_one(
            item=_item(_momo_bars()), now=cutoff - timedelta(minutes=5), **_common()
        )
    assert provider.calls == 0

    with pytest.raises(TypeError):
        ShadowPipeline(proposal_provider=provider, strategy=ORBConfig())  # type: ignore[arg-type]


async def _shadow_world(tmp_path, monkeypatch, *, run_id: str):
    from config import cfg
    from db.core import init_db
    from trading.storage.repository import create_strategy, transition_strategy

    monkeypatch.setattr(cfg, "DB_PATH", str(tmp_path / "injection.db"))
    await init_db()
    spec = StrategySpec(
        name="Injected session strategy",
        universe=FixedUniverse(symbols=("MOMO",)),
        conditions=(RuleCondition(feature="rvol", operator="gte", value="2"),),
        rationale="Session injection fixture.",
    )
    strategy = await create_strategy(spec=spec, owner_id="owner-a", actor="owner-a")
    for revision, target in enumerate((StrategyState.BACKTESTING, StrategyState.SHADOW)):
        strategy = await transition_strategy(
            strategy_id=strategy.strategy_id,
            version=1,
            owner_id="owner-a",
            expected_revision=revision,
            target=target,
            actor="owner-a",
            reason="test",
        )
    run = Run(
        id=run_id,
        owner_id="owner-a",
        source_sha="abcdef1",
        strategy_id=strategy.strategy_id,
        strategy_version=strategy.version,
        policy_fingerprints={"risk": RiskPolicy().fingerprint()},
        session_date=SESSION,
        mode=EngineMode.SHADOW,
    )
    parameters = {"source": "injection", "limit": 1}
    scanner = ScannerRun(
        id=f"{run_id}-scanner",
        run_id=run.id,
        parameters=parameters,
        parameters_fingerprint=fingerprint_payload(parameters),
        status="completed",
        started_at=_et(SESSION, 9, 45),
    )
    candidate = Candidate(
        id=f"{run_id}-momo",
        scanner_run_id=scanner.id,
        con_id=MOMO_CON_ID,
        symbol="MOMO",
        primary_exchange="NASDAQ",
        rank=1,
        admission_status="admitted",
    )
    return strategy, run, scanner, candidate


@pytest.mark.anyio
async def test_leased_session_cutoff_and_inner_pipeline_follow_the_strategy(tmp_path, monkeypatch):
    strategy, run, scanner, candidate = await _shadow_world(
        tmp_path, monkeypatch, run_id="run-injected"
    )
    bars = _momo_bars()
    now = _after(bars)
    provider = _Provider()
    runner = RepositoryBackedShadowRunner(
        pipeline=ShadowPipeline(proposal_provider=provider, strategy=MomoStrategy()),
        clock=lambda: now,
    )
    async with runner.open_session(
        run=run,
        scanner_run=scanner,
        strategy=strategy,
        candidates=(candidate,),
        policy=RiskPolicy(),
        holder_id="daemon-momo",
    ) as session:
        assert session.cutoff == _et(SESSION, 15, 0)
        portfolio = PortfolioSnapshot(observed_at=now, net_liquidation="100000")
        assessment = await session.assess(
            _item(bars, quote=_quote(now), candidate=candidate), portfolio=portfolio, now=now
        )
    assert assessment.decision is not None
    assert assessment.decision.outcome == DecisionOutcome.SHADOW_ACCEPTED
    assert [setup.strategy_kind for setup in provider.setups] == ["momo_hod"]

    default_runner = RepositoryBackedShadowRunner(
        pipeline=ShadowPipeline(proposal_provider=_Provider()), clock=lambda: now
    )
    async with default_runner.open_session(
        run=run,
        scanner_run=scanner,
        strategy=strategy,
        candidates=(candidate,),
        policy=RiskPolicy(),
        holder_id="daemon-orb",
    ) as orb_session:
        assert orb_session.cutoff == _et(SESSION, 12, 0)

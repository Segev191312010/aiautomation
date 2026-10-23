"""Concurrent AI reviews within one strategy run (owner, 2026-10-23; plan v2 item 3b).

The reviews of one poll run at once, at most ``REVIEW_POOL_SIZE`` (4) in
flight, each still one PROPOSE/ABSTAIN per candidate. The run's cap (100
calls / $25) is reserved atomically before every call, so it is never
exceeded however many reviews arrive together; each call's cost is counted
exactly once; a 429 or a timeout under load is an ABSTAIN, never retried by
the provider. Decisions are made and recorded in the poll's candidate order,
each approval's 15 s expiry starting when its decision is made, and a
candidate is never decided twice.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import logging
import random
from typing import Any

from anthropic import AsyncAnthropic
import httpx2
import pytest

from tests.test_no_limit_test_mode import (
    HASH,
    OPEN,
    BreakoutMarket,
    _decisions_in_write_order,
    run_breakout_day,
)
from trading.contracts import AIReview, RiskPolicy, RuleSetup
from trading.intelligence.anthropic_transport import AnthropicMessagesTransport
from trading.intelligence.review_provider import (
    REVIEW_CALL_COST_RESERVE_USD,
    REVIEW_POOL_SIZE,
    AITransportResponse,
    ReviewSessionLimits,
    ReviewSessionUsage,
    StructuredAIReviewProvider,
)
from trading.orchestration.persistence import ShadowPersistenceConflictError
from trading.orchestration.session_runner import OrderedDecisions, ShadowSessionRunner
from trading.risk.no_limit_test import NO_LIMIT_TEST_POLICY

T1_LIMITS = ReviewSessionLimits(timeout_seconds=20.0, max_calls=100, max_cost_usd=Decimal("25"))
EIGHT = ("AAA", "BBB", "CCC", "DDD", "EEE", "FFF", "GGG", "HHH")
SAME_BAR = {symbol: OPEN + timedelta(minutes=40) for symbol in EIGHT}


def _setup(index: int) -> RuleSetup:
    return RuleSetup(
        run_id="run-1",
        candidate_id=f"candidate-{index}",
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
        detected_at=datetime(2026, 11, 2, 14, 30, tzinfo=timezone.utc) + timedelta(seconds=index),
    )


class CountingTransport:
    """Bills each call its cost after a delay; records the most calls in flight at once.

    Delays and costs come from two seeded generators, so the costs billed (in
    order of completion) are the same sequence whatever the interleaving.
    """

    def __init__(self, *, cost: Decimal | None = None, seed: int = 7, delay: float | None = None) -> None:
        self.random = random.Random(seed)
        self.costs = random.Random(seed + 1)
        self.cost = cost
        self.delay = delay
        self.calls = 0
        self.in_flight = 0
        self.max_in_flight = 0
        self.billed: list[Decimal] = []

    async def complete(self, *, setup, model_id, prompt_version):
        self.calls += 1
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            await asyncio.sleep(self.delay if self.delay is not None else self.random.uniform(0, 0.01))
        finally:
            self.in_flight -= 1
        cost = self.cost if self.cost is not None else Decimal(self.costs.randint(1, 20)) / 100
        self.billed.append(cost)
        return AITransportResponse(
            output={"decision": "PROPOSE", "confidence": "0.7", "rationale": "fixture"}, cost_usd=cost,
        )


def _provider(transport, limits: ReviewSessionLimits = T1_LIMITS, **options) -> StructuredAIReviewProvider:
    return StructuredAIReviewProvider(
        transport=transport, model_id="review-model", prompt_version="review-prompt", limits=limits, **options
    )


# --- the pool --------------------------------------------------------------------------


def test_the_review_pool_and_the_reservation_are_pinned():
    import inspect

    assert REVIEW_POOL_SIZE == 4
    assert REVIEW_CALL_COST_RESERVE_USD == Decimal("0.50")
    assert ReviewSessionLimits().call_cost_reserve_usd == REVIEW_CALL_COST_RESERVE_USD
    assert inspect.signature(ShadowSessionRunner).parameters["review_pool_size"].default == REVIEW_POOL_SIZE
    for size in (0, REVIEW_POOL_SIZE + 1, True, 2.0):
        with pytest.raises(ValueError):
            OrderedDecisions(object(), pool_size=size)  # type: ignore[arg-type]


# --- the cap under concurrency (provider) ------------------------------------------------


@pytest.mark.anyio
async def test_150_reviews_through_the_pool_make_exactly_100_calls_and_count_each_cost_once():
    """The run's pool (4 in flight) over 150 setups: 100 calls, then call_limit_exhausted."""
    transport = CountingTransport()
    provider = _provider(transport)
    pool = asyncio.Semaphore(REVIEW_POOL_SIZE)

    async def pooled(index: int) -> AIReview:
        async with pool:
            return await provider.propose_trade(setup=_setup(index))

    reviews = await asyncio.gather(*(pooled(index) for index in range(150)))

    made = [review for review in reviews if review.failure_reason is None]
    refused = [review for review in reviews if review.failure_reason is not None]
    assert transport.calls == len(made) == 100
    assert transport.max_in_flight == REVIEW_POOL_SIZE
    assert {review.failure_reason for review in refused} == {"call_limit_exhausted"} and len(refused) == 50
    assert all(review.decision == "ABSTAIN" for review in refused)
    # Exactly once per call: the run's spend is the sum of what the calls billed.
    assert provider.usage == ReviewSessionUsage(calls=100, cost_usd=sum(transport.billed, Decimal("0")))
    assert sum((review.cost_usd for review in reviews), Decimal("0")) == provider.usage.cost_usd
    assert provider.usage.cost_usd <= Decimal("25")
    assert provider.in_flight == 0


@pytest.mark.anyio
async def test_a_burst_of_150_at_once_never_passes_100_calls_or_25_dollars():
    """Without the pool every review arrives at once: the reservations alone hold the cap
    (50 x $0.50 fill $25), and each refusal is an ABSTAIN."""
    transport = CountingTransport(cost=Decimal("0.45"))
    provider = _provider(transport)

    reviews = await asyncio.gather(*(provider.propose_trade(setup=_setup(index)) for index in range(150)))

    made = [review for review in reviews if review.failure_reason is None]
    assert transport.calls == len(made) == provider.usage.calls <= 100
    assert provider.usage.cost_usd == sum(transport.billed, Decimal("0")) <= Decimal("25")
    assert {review.failure_reason for review in reviews if review.failure_reason} <= {
        "call_limit_exhausted", "budget_exhausted",
    }
    assert all(review.decision == "ABSTAIN" for review in reviews if review.failure_reason)
    assert provider.in_flight == 0


class SlowPersistence:
    """A durable usage store whose every write yields to the event loop."""

    def __init__(self, calls: int, cost: Decimal = Decimal("0")) -> None:
        self.start = ReviewSessionUsage(calls=calls, cost_usd=cost)
        self.persisted: list[ReviewSessionUsage] = []

    async def load_usage(self, *, run_id: str) -> ReviewSessionUsage:
        await asyncio.sleep(0)
        return self.start

    async def persist_usage(self, *, run_id: str, usage: ReviewSessionUsage) -> None:
        await asyncio.sleep(0)
        self.persisted.append(usage)

    async def persist_review(self, **_kwargs: Any) -> None:
        await asyncio.sleep(0)


@pytest.mark.anyio
@pytest.mark.parametrize("arriving", [2, 10])
async def test_the_call_cap_is_atomic_two_reviews_at_99_make_exactly_one_call(arriving):
    transport = CountingTransport(cost=Decimal("0.05"))
    store = SlowPersistence(calls=99)
    provider = _provider(transport, session_id="run-1", persistence=store, prompt_hash="a" * 64,
                         pricing_hash="b" * 64)

    reviews = await asyncio.gather(*(provider.propose_trade(setup=_setup(index)) for index in range(arriving)))

    assert transport.calls == 1
    assert sorted(str(review.failure_reason) for review in reviews) == sorted(
        ["None"] + ["call_limit_exhausted"] * (arriving - 1)
    )
    assert provider.usage == ReviewSessionUsage(calls=100, cost_usd=Decimal("0.05"))
    assert max(item.calls for item in store.persisted) == 100
    assert store.persisted[-1] == provider.usage


@pytest.mark.anyio
async def test_every_review_in_flight_holds_its_reservation_so_the_budget_is_never_passed():
    """$2 with $0.50 reserved per call: four calls fit at once, a fifth never starts;
    the four cost $1.60 together, and nothing more is called."""
    transport = CountingTransport(cost=Decimal("0.40"), delay=0.01)
    provider = _provider(transport, ReviewSessionLimits(max_calls=100, max_cost_usd=Decimal("2")))

    reviews = await asyncio.gather(*(provider.propose_trade(setup=_setup(index)) for index in range(20)))

    assert transport.calls == 4 and transport.max_in_flight == 4
    assert [review.failure_reason for review in reviews].count("budget_exhausted") == 16
    assert provider.usage == ReviewSessionUsage(calls=4, cost_usd=Decimal("1.60"))
    later = await provider.propose_trade(setup=_setup(99))  # $1.60 + $0.50 > $2: refused
    assert later.failure_reason == "budget_exhausted" and transport.calls == 4


@pytest.mark.anyio
async def test_a_call_dearer_than_its_reservation_is_counted_once_and_abstains_over_the_cap(caplog):
    transport = CountingTransport(cost=Decimal("3"), delay=0)
    provider = _provider(transport, ReviewSessionLimits(max_calls=100, max_cost_usd=Decimal("2.5")))
    caplog.set_level(logging.WARNING, logger="trading.intelligence.review_provider")

    first, second = await asyncio.gather(*(provider.propose_trade(setup=_setup(index)) for index in range(2)))

    assert transport.calls == 2  # $0 + 2 x $0.50 fits: both started before either billed
    assert {first.failure_reason, second.failure_reason} == {"budget_exceeded"}
    assert provider.usage == ReviewSessionUsage(calls=2, cost_usd=Decimal("6"))
    assert sum(1 for record in caplog.records if "above its reservation" in record.getMessage()) == 2
    third = await provider.propose_trade(setup=_setup(3))
    assert third.failure_reason == "budget_exhausted" and transport.calls == 2


@pytest.mark.anyio
async def test_a_timeout_and_a_cancel_release_their_reservations_and_cost_nothing():
    class Hanging:
        calls = 0

        async def complete(self, **_kwargs):
            self.calls += 1
            await asyncio.sleep(3600)

    transport = Hanging()
    provider = _provider(transport, ReviewSessionLimits(timeout_seconds=0.01, max_calls=3, max_cost_usd=Decimal("1")))
    timed_out = await provider.propose_trade(setup=_setup(1))
    assert (timed_out.decision, timed_out.failure_reason) == ("ABSTAIN", "provider_timeout")
    assert provider.in_flight == 0

    patient = _provider(transport, ReviewSessionLimits(timeout_seconds=60, max_calls=3, max_cost_usd=Decimal("1")))
    task = asyncio.create_task(patient.propose_trade(setup=_setup(2)))
    while patient.in_flight == 0:
        await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert patient.in_flight == 0
    assert patient.usage == ReviewSessionUsage(calls=1, cost_usd=Decimal("0"))


# --- 429 under load, through the real SDK (anthropic 1.x, httpx MockTransport: no network) ---


def _message(text: str) -> dict[str, Any]:
    return {
        "id": "msg_test", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
        "content": [{"type": "text", "text": text}], "stop_reason": "end_turn", "stop_sequence": None,
        "usage": {"input_tokens": 5000, "output_tokens": 900, "cache_read_input_tokens": 0,
                  "cache_creation_input_tokens": 0},
    }


class RateLimitedApi:
    """Answers 429 for the named symbols (every attempt), PROPOSE for the rest."""

    def __init__(self, limited: set[str]) -> None:
        self.limited = limited
        self.attempts: list[str] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        payload = json.loads(json.loads(request.content)["messages"][0]["content"])
        symbol = payload["setup"]["symbol"]
        self.attempts.append(symbol)
        if symbol in self.limited:
            return httpx2.Response(
                429, headers={"retry-after-ms": "1"},
                json={"type": "error", "error": {"type": "rate_limit_error", "message": "rate limited"}},
            )
        return httpx2.Response(200, json=_message(json.dumps(
            {"decision": "PROPOSE", "confidence": 0.7, "rationale": "Clean break."}
        )))


class CountingCalls:
    """Counts the provider's calls to the transport (the SDK's own retry is below it)."""

    def __init__(self, inner) -> None:
        self.inner = inner
        self.calls = 0
        self.prompt_hash = inner.prompt_hash
        self.pricing_hash = inner.pricing_hash

    async def complete(self, **kwargs):
        self.calls += 1
        return await self.inner.complete(**kwargs)


def _sdk_provider(api: RateLimitedApi) -> tuple[StructuredAIReviewProvider, CountingCalls]:
    client = AsyncAnthropic(
        api_key="sk-ant-test-not-a-real-key",
        base_url="http://anthropic.invalid",
        max_retries=1,  # as build_anthropic_client: the SDK's one retry, never the provider's
        timeout=20.0,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(api)),
    )
    transport = CountingCalls(AnthropicMessagesTransport(client=client, system_prompt="T1 fixture review prompt."))
    return _provider(transport), transport


@pytest.mark.anyio
async def test_a_429_under_load_is_an_abstain_never_retried_by_the_provider_and_never_charged(
    tmp_path, monkeypatch
):
    limited = {"BBB", "EEE", "FFF"}
    api = RateLimitedApi(limited)
    provider, transport = _sdk_provider(api)

    report, database, _provider_used = await run_breakout_day(
        tmp_path, monkeypatch, policy=NO_LIMIT_TEST_POLICY, breakouts=SAME_BAR, provider=provider,
    )

    decisions = _decisions_in_write_order(database, report.run_id)
    assert [symbol for symbol, _o, _p in decisions] == list(EIGHT)
    for symbol, outcome, payload in decisions:
        review = payload["ai_review"]
        if symbol in limited:
            assert outcome == "ai_blocked", symbol
            assert (review["decision"], review["failure_reason"]) == ("ABSTAIN", "provider_unavailable")
            assert review["model_id"] == "review-model"  # the provider's own ABSTAIN
            assert Decimal(review["cost_usd"]) == 0
        else:
            assert outcome == "shadow_accepted", symbol
            assert review["decision"] == "PROPOSE" and Decimal(review["cost_usd"]) > 0
    # One call per review: the provider never retries; the SDK retries a 429 once, as configured.
    assert transport.calls == provider.usage.calls == len(EIGHT)
    assert sorted(api.attempts) == sorted([*EIGHT, *limited])
    accepted_cost = sum(
        (Decimal(payload["ai_review"]["cost_usd"]) for _s, _o, payload in decisions), Decimal("0")
    )
    assert provider.usage.cost_usd == accepted_cost
    assert provider.in_flight == 0


# --- order, the pool bound and the expiry (the session runner) ---------------------------


class GatedProvider:
    """PROPOSEs every setup after a per-symbol real delay; records the reviews in flight."""

    def __init__(self, delays: dict[str, float]) -> None:
        self.delays = delays
        self.in_flight = 0
        self.max_in_flight = 0
        self.finished: list[str] = []

    async def propose_trade(self, *, setup):
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            await asyncio.sleep(self.delays.get(setup.symbol, 0.0))
        finally:
            self.in_flight -= 1
        self.finished.append(setup.symbol)
        return AIReview(
            setup_fingerprint=setup.fingerprint(), decision="PROPOSE", confidence="0.7",
            rationale="T1 fixture review.", model_id="fixture", prompt_version="fixture",
            response_hash=HASH, latency_ms=1, cost_usd="0.02",
        )


def _reverse_delays() -> dict[str, float]:
    # The first symbol is the slowest: reviews finish in reverse order.
    return {symbol: 0.02 * (len(EIGHT) - index) for index, symbol in enumerate(EIGHT)}


@pytest.mark.anyio
async def test_reviews_run_four_at_a_time_and_decisions_keep_the_serial_order(tmp_path, monkeypatch):
    concurrent = GatedProvider(_reverse_delays())
    report, database, _ = await run_breakout_day(
        tmp_path, monkeypatch, policy=NO_LIMIT_TEST_POLICY, breakouts=SAME_BAR, provider=concurrent,
        database_name="concurrent.db",
    )
    serial = GatedProvider(_reverse_delays())
    serial_report, serial_database, _ = await run_breakout_day(
        tmp_path, monkeypatch, policy=NO_LIMIT_TEST_POLICY, breakouts=SAME_BAR, provider=serial,
        review_pool_size=1, database_name="serial.db",
    )

    assert concurrent.max_in_flight == REVIEW_POOL_SIZE  # bounded, and the bound is reached
    assert serial.max_in_flight == 1
    assert concurrent.finished != list(EIGHT)  # the reviews finished out of order
    decided = _decisions_in_write_order(database, report.run_id)
    serial_decided = _decisions_in_write_order(serial_database, serial_report.run_id)
    # The recorded order is the candidates' (the bridge sends by created_at), exactly the serial run's.
    assert [(symbol, outcome) for symbol, outcome, _ in decided] == [
        (symbol, "shadow_accepted") for symbol in EIGHT
    ] == [(symbol, outcome) for symbol, outcome, _ in serial_decided]
    created = [datetime.fromisoformat(payload["created_at"]) for _s, _o, payload in decided]
    assert created == sorted(created)
    # One decision per candidate, one review per candidate.
    assert len({payload["candidate_id"] for _s, _o, payload in decided}) == len(EIGHT)
    assert sorted(concurrent.finished) == sorted(EIGHT)


@pytest.mark.anyio
async def test_each_approval_expires_15_s_after_its_decision_even_behind_a_slow_review(tmp_path, monkeypatch):
    """The first review takes 1.2 s; the rest finish at once and wait for its decision.
    Each risk check runs when its decision is made, so each approval still has its
    full 15 s when it is written, never 15 s from its own review."""
    from trading.orchestration import persistence

    written: list[tuple[str, datetime]] = []
    original = persistence._record_decision_idempotent

    async def spy(*, decision, owner_id):
        written.append((decision.candidate_id, datetime.now(timezone.utc)))
        await original(decision=decision, owner_id=owner_id)

    monkeypatch.setattr(persistence, "_record_decision_idempotent", spy)
    provider = GatedProvider({"AAA": 1.2})
    report, database, _ = await run_breakout_day(
        tmp_path, monkeypatch, policy=NO_LIMIT_TEST_POLICY, breakouts=SAME_BAR, provider=provider,
    )

    assert provider.finished[-1] == "AAA"  # every other review finished before the first one
    decided = _decisions_in_write_order(database, report.run_id)
    assert [symbol for symbol, _o, _p in decided] == list(EIGHT)
    write_time = dict(written)
    for _symbol, outcome, payload in decided:
        assert outcome == "shadow_accepted"
        expires_at = datetime.fromisoformat(payload["risk"]["expires_at"])
        assert expires_at - write_time[payload["candidate_id"]] > timedelta(seconds=14.5)


# --- never two decisions for one candidate ---------------------------------------------------


@pytest.mark.anyio
async def test_a_candidate_being_decided_is_never_evaluated_again_and_is_decided_once(tmp_path, monkeypatch):
    import sqlite3

    from config import cfg
    from db.core import init_db
    from trading.contracts import EngineMode, PortfolioSnapshot, Run, ScannerRun, fingerprint_payload
    from trading.orchestration import CandidateInput, RepositoryBackedShadowRunner, ShadowPipeline
    from trading.orchestration.session_runner import ensure_orb_strategy
    from tests.test_no_limit_test_mode import SESSION, ProposeAll

    monkeypatch.setattr(cfg, "DB_PATH", str(tmp_path / "once.db"))
    await init_db()
    now = OPEN + timedelta(minutes=46)  # just after the 10:10-10:15 breakout bar
    market = BreakoutMarket(lambda: now, {"AAA": OPEN + timedelta(minutes=40)})
    strategy = await ensure_orb_strategy(owner_id="demo")
    run = Run(
        id="run-once", owner_id="demo", source_sha="abcdef1", strategy_id=strategy.strategy_id,
        strategy_version=strategy.version, policy_fingerprints={"risk": NO_LIMIT_TEST_POLICY.fingerprint()},
        session_date=SESSION, mode=EngineMode.SHADOW,
    )
    scanner = ScannerRun(
        id="run-once-scan", run_id=run.id, parameters={"source": "test"},
        parameters_fingerprint=fingerprint_payload({"source": "test"}), status="completed",
        started_at=OPEN,
    )
    snapshot = await market.scanner_snapshot(scanner_run_id=scanner.id)
    (candidate,) = snapshot.admitted_candidates
    runner = RepositoryBackedShadowRunner(
        pipeline=ShadowPipeline(proposal_provider=ProposeAll()), clock=lambda: now,
        lease_ttl=timedelta(hours=6), lease_renew_interval=timedelta(hours=1),
    )
    portfolio = PortfolioSnapshot(observed_at=now, net_liquidation=Decimal("100000"))
    profile = await market.liquidity(con_id=candidate.con_id, session_date=SESSION)
    item = CandidateInput(
        candidate=candidate, bars=await market.completed_bars(con_id=candidate.con_id, now=now),
        quote=await market.quote(con_id=candidate.con_id), reference_price=profile.reference_price,
        average_dollar_volume=profile.average_dollar_volume,
    )
    async with runner.open_session(
        run=run, scanner_run=scanner, strategy=strategy, candidates=(candidate,),
        policy=NO_LIMIT_TEST_POLICY, holder_id="t1-test", now=now,
    ) as shadow:
        first = await shadow.evaluate(item, portfolio=portfolio, now=now)
        assert first.decides and first.setup is not None
        with pytest.raises(ShadowPersistenceConflictError, match="already being decided"):
            await shadow.evaluate(item, portfolio=portfolio, now=now)
        decided = await shadow.conclude(await shadow.review(first))
        assert decided.decision is not None and decided.decision.outcome.value == "shadow_accepted"
        # A concluded candidate evaluates to its recorded decision, and concluding it records nothing.
        again = await shadow.evaluate(item, portfolio=portfolio, now=now)
        assert again.prior == decided.decision and not again.decides
        assert (await shadow.conclude(again)).decision == decided.decision
        # The first evaluation, concluded a second time, is refused before any write.
        with pytest.raises(ShadowPersistenceConflictError, match="already has a decision"):
            await shadow.conclude(first)
    with sqlite3.connect(tmp_path / "once.db") as connection:
        rows = connection.execute(
            "SELECT candidate_id, outcome FROM autotrader_candidate_decisions WHERE run_id = ?", (run.id,)
        ).fetchall()
    assert rows == [(candidate.id, "shadow_accepted")]


@pytest.mark.anyio
async def test_a_failed_decision_ends_the_poll_and_no_later_decision_is_made(tmp_path, monkeypatch):
    """A recorder failure on the third candidate: the first two are recorded, nothing
    after the failure is, and the session ends with the error (as a serial poll did)."""
    from trading.orchestration import persistence

    original = persistence._record_decision_idempotent
    recorded: list[str] = []

    async def failing(*, decision, owner_id):
        if len(recorded) == 2:
            raise RuntimeError("disk full")
        recorded.append(decision.candidate_id)
        await original(decision=decision, owner_id=owner_id)

    monkeypatch.setattr(persistence, "_record_decision_idempotent", failing)
    with pytest.raises(RuntimeError, match="disk full"):
        await run_breakout_day(
            tmp_path, monkeypatch, policy=RiskPolicy(), breakouts=SAME_BAR,
            provider=GatedProvider(_reverse_delays()), database_name="failed.db",
        )
    import sqlite3

    with sqlite3.connect(tmp_path / "failed.db") as connection:
        symbols = [
            str(row[0]) for row in connection.execute(
                """SELECT c.symbol FROM autotrader_candidate_decisions d
                   JOIN autotrader_candidates c ON c.id = d.candidate_id ORDER BY d.rowid"""
            )
        ]
    assert len(recorded) == 2 and symbols == ["AAA", "BBB"]

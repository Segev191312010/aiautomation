"""Consumers of the screener's market context: Claude, MOMO's entry gate, the phone.

The context itself (``trading.market.context``) is tested by its own module.
Here a fake with the same fields drives each consumer, and the last tests run
the real reader against a stored sweep so the two sides cannot drift apart.
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
import inspect
import json
import logging
from types import SimpleNamespace

import pytest

from tests.test_anthropic_transport import MODEL, FakeMessages, _response, _setup
from tests.test_notify_service import (
    CHAT_ID,
    SESSION as NOTIFY_DAY,
    FakeTelegram,
    FakeTime,
    _environment,
    _use_database,
)
from tests.test_session_runner import (
    OPEN,
    SESSION as RUNNER_DAY,
    FakeMarket,
    Provider,
    _account,
    _world,
)
from tests.test_shadow_service import TUESDAY, et, runtime_config
from trading.contracts import RiskPolicy
from trading.intelligence import anthropic_transport
from trading.intelligence.anthropic_transport import AnthropicMessagesTransport
from trading.intelligence.prompts import MOMO_REVIEW_V2
from trading.intelligence.review_provider import StructuredAIReviewProvider
from trading.market.screener import HotListRow
from trading.operations import notify_service, shadow_service
from trading.operations.notifier import save_chat_id, telegram_config_file
from trading.operations.notify_service import (
    format_market_update,
    format_regime_change,
    run_notifier,
    state_file,
)
from trading.operations.paths import UserPaths
from trading.orchestration import RepositoryBackedShadowRunner, ShadowPipeline
from trading.orchestration.session_runner import (
    SessionReport,
    ShadowSessionRunner,
    ensure_orb_strategy,
)
from trading.scheduler.sessions import MARKET_TIMEZONE, market_session
from trading.storage.repository import list_decisions

FIVE_MINUTES = timedelta(minutes=5)


def _net(hour: int, minute: int, second: int = 0) -> datetime:
    """``hour:minute`` New York time on the notifier's test session."""
    return datetime.combine(NOTIFY_DAY, time(hour, minute, second), tzinfo=MARKET_TIMEZONE)


@dataclass(frozen=True)
class FakeMarketContext:
    """The fields and ``to_payload`` of ``trading.market.context.MarketContext``."""

    session_date: date
    observed_at: datetime
    regime: str
    summary: str
    spy_change_pct: Decimal | None = Decimal("0.42")
    qqq_change_pct: Decimal | None = Decimal("0.61")
    iwm_change_pct: Decimal | None = Decimal("-0.12")
    dia_change_pct: Decimal | None = Decimal("0.20")
    breadth_up_pct: Decimal | None = Decimal("58.35")
    advancers: int = 227
    decliners: int = 162
    median_change_pct: Decimal | None = Decimal("0.35")
    sector_leaders: tuple[tuple[str, Decimal], ...] = (
        ("Technology", Decimal("1.24")),
        ("Energy", Decimal("0.85")),
        ("Industrials", Decimal("0.40")),
    )
    sector_laggards: tuple[tuple[str, Decimal], ...] = (
        ("Utilities", Decimal("-0.92")),
        ("Real Estate", Decimal("-0.55")),
        ("Consumer Staples", Decimal("-0.31")),
    )

    def to_payload(self) -> dict[str, object]:
        def text(value: Decimal | None) -> str | None:
            return None if value is None else format(value, "f")

        return {
            "schema": "market_context.v1",
            "session_date": self.session_date.isoformat(),
            "observed_at": self.observed_at.astimezone(timezone.utc).isoformat(),
            "regime": self.regime,
            "spy_change_pct": text(self.spy_change_pct),
            "qqq_change_pct": text(self.qqq_change_pct),
            "iwm_change_pct": text(self.iwm_change_pct),
            "dia_change_pct": text(self.dia_change_pct),
            "breadth_up_pct": text(self.breadth_up_pct),
            "advancers": self.advancers,
            "decliners": self.decliners,
            "median_change_pct": text(self.median_change_pct),
            "sector_leaders": [[name, text(change)] for name, change in self.sector_leaders],
            "sector_laggards": [[name, text(change)] for name, change in self.sector_laggards],
            "summary": self.summary,
        }


def _context(
    regime: str, observed_at: datetime, *, summary: str | None = None, day: date = NOTIFY_DAY
) -> FakeMarketContext:
    return FakeMarketContext(
        session_date=day,
        observed_at=observed_at,
        regime=regime,
        summary=summary or f"{regime} at {observed_at.astimezone(MARKET_TIMEZONE):%H:%M:%S}",
    )


HOT_NAMES = (
    ("NVDA", "6.20"),
    ("SMCI", "5.80"),
    ("AMD", "4.10"),
    ("PLTR", "3.90"),
    ("TSLA", "3.10"),
    ("SOFI", "2.50"),
)


def _hot_rows(observed_at: datetime) -> tuple[HotListRow, ...]:
    return tuple(
        HotListRow(
            session_date=NOTIFY_DAY,
            observed_at=observed_at,
            hot_rank=rank,
            symbol=symbol,
            con_id=1000 + rank,
            primary_exchange="NASDAQ",
            source="watchlist",
            score=Decimal("0.9"),
            last=Decimal("100"),
            prior_close=Decimal("95"),
            open=None,
            day_high=None,
            day_low=None,
            change_pct=Decimal(change),
            gap_pct=None,
            volume_shares=None,
            rvol=None,
            dollar_volume=None,
            pct_from_high=None,
        )
        for rank, (symbol, change) in enumerate(HOT_NAMES, 1)
    )


@contextmanager
def _log_lines(name: str) -> Iterator[list[str]]:
    """``LEVEL message`` lines from one logger, whatever its propagation."""
    lines: list[str] = []

    class Collect(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            lines.append(f"{record.levelname} {record.getMessage()}")

    log = logging.getLogger(name)
    handler = Collect(level=logging.DEBUG)
    previous = log.level
    log.addHandler(handler)
    log.setLevel(logging.DEBUG)
    try:
        yield lines
    finally:
        log.removeHandler(handler)
        log.setLevel(previous)


class RecordingReader:
    """A ``latest_market_context`` stand-in answering from a script."""

    def __init__(self, *answers: FakeMarketContext | None) -> None:
        self.answers = list(answers)
        self.calls: list[tuple[date, datetime, timedelta]] = []

    async def __call__(
        self, session_date: date, *, now: datetime, max_age: timedelta
    ) -> FakeMarketContext | None:
        self.calls.append((session_date, now, max_age))
        return self.answers.pop(0)


# --------------------------------------------------------------------------
# 1. Claude sees the market
# --------------------------------------------------------------------------


def _momo_transport(provider) -> tuple[AnthropicMessagesTransport, FakeMessages]:
    body = json.dumps({"decision": "PROPOSE", "confidence": 0.7, "rationale": "Clean break."})
    messages = FakeMessages(_response(text=body))
    transport = AnthropicMessagesTransport(
        client=SimpleNamespace(messages=messages),
        system_prompt=MOMO_REVIEW_V2,
        context_provider=provider,
    )
    return transport, messages


def _review_provider(transport: AnthropicMessagesTransport) -> StructuredAIReviewProvider:
    return StructuredAIReviewProvider(
        transport=transport, model_id=MODEL, prompt_version="momo-review-v2"
    )


def _sent_request(messages: FakeMessages) -> dict:
    return json.loads(messages.calls[0]["messages"][0]["content"])


async def test_the_review_request_carries_the_market_context():
    context = _context("neutral", _net(10, 30))

    async def provide():
        return context.to_payload()

    transport, messages = _momo_transport(provide)

    review = await _review_provider(transport).propose_trade(setup=_setup())

    assert review.decision == "PROPOSE" and review.failure_reason is None
    request = _sent_request(messages)
    assert request["market_context"] == context.to_payload()
    assert request["setup"]["entry_limit"] == "100.10"
    # The context rides in the user turn; the system prompt (and its hash) is fixed.
    assert messages.calls[0]["system"] == MOMO_REVIEW_V2
    assert transport.prompt_hash == AnthropicMessagesTransport(
        client=SimpleNamespace(messages=messages), system_prompt=MOMO_REVIEW_V2
    ).prompt_hash


async def test_no_fresh_context_is_sent_as_unavailable():
    async def nothing():
        return None

    transport, messages = _momo_transport(nothing)

    await transport.complete(setup=_setup(), model_id=MODEL, prompt_version="momo-review-v2")

    assert _sent_request(messages)["market_context"] == {"available": False}


async def test_a_failing_context_provider_never_fails_the_review():
    async def broken():
        raise RuntimeError("database is locked")

    transport, messages = _momo_transport(broken)

    with _log_lines("autotrader.shadow.review") as lines:
        review = await _review_provider(transport).propose_trade(setup=_setup())

    assert review.decision == "PROPOSE" and review.failure_reason is None
    assert _sent_request(messages)["market_context"] == {"available": False}
    assert lines == ["WARNING market context unavailable for review: RuntimeError"]


@pytest.mark.parametrize(
    ("context", "warning"),
    [
        ({"spy_change_pct": Decimal("0.4")}, "not JSON-safe (TypeError)"),
        ({"spy_change_pct": float("nan")}, "not JSON-safe (ValueError)"),
        ([("regime", "neutral")], "is a list, not a mapping"),
        ({"summary": "x" * 9000}, "bytes (limit 8192)"),
    ],
    ids=["decimal", "nan", "not-a-mapping", "oversized"],
)
async def test_unsafe_context_is_sent_as_unavailable(context, warning):
    async def provide():
        return context

    transport, messages = _momo_transport(provide)

    with _log_lines("autotrader.shadow.review") as lines:
        await transport.complete(setup=_setup(), model_id=MODEL, prompt_version="momo-review-v2")

    assert _sent_request(messages)["market_context"] == {"available": False}
    assert len(lines) == 1 and lines[0].startswith("WARNING market context") and warning in lines[0]


async def test_a_slow_context_provider_cannot_stall_the_review(monkeypatch):
    monkeypatch.setattr(anthropic_transport, "MARKET_CONTEXT_TIMEOUT_SECONDS", 0.01)
    never = asyncio.Event()

    async def slow():
        await never.wait()

    transport, messages = _momo_transport(slow)

    with _log_lines("autotrader.shadow.review") as lines:
        await transport.complete(setup=_setup(), model_id=MODEL, prompt_version="momo-review-v2")

    assert _sent_request(messages)["market_context"] == {"available": False}
    assert lines == ["WARNING market context unavailable for review: TimeoutError"]


async def test_without_a_provider_the_request_is_byte_identical_to_before():
    body = json.dumps({"decision": "ABSTAIN", "confidence": 0.4, "rationale": "Wide."})
    messages = FakeMessages(_response(text=body))
    transport = AnthropicMessagesTransport(
        client=SimpleNamespace(messages=messages), system_prompt="ORB reviewer."
    )
    setup = _setup()

    await transport.complete(setup=setup, model_id=MODEL, prompt_version="orb-review-v1")

    assert messages.calls[0]["messages"][0]["content"] == json.dumps(
        {
            "prompt_version": "orb-review-v1",
            "setup": setup.model_dump(mode="json"),
            "constraints": {
                "allowed_decisions": ["PROPOSE", "ABSTAIN"],
                "order_fields_are_immutable": True,
            },
        },
        sort_keys=True,
        separators=(",", ":"),
    )


# --------------------------------------------------------------------------
# 2-3. MOMO's risk-off gate and its wiring
# --------------------------------------------------------------------------


async def test_the_momo_gate_blocks_only_while_a_fresh_context_is_risk_off():
    now = et(TUESDAY, 10, 30)
    reader = RecordingReader(
        None,
        _context("neutral", now, summary="Neutral · S&P -0.2%", day=TUESDAY),
        _context("risk_on", now, summary="Risk-on · S&P +0.9%", day=TUESDAY),
        _context("risk_off", now, summary="Risk-off · S&P -1.2% · 120↑/269↓", day=TUESDAY),
    )
    gate = shadow_service.risk_off_entry_gate(TUESDAY, clock=lambda: now, reader=reader)

    assert await gate() == (True, "no fresh market context")
    assert await gate() == (True, "Neutral · S&P -0.2%")
    assert await gate() == (True, "Risk-on · S&P +0.9%")
    assert await gate() == (False, "Risk-off · S&P -1.2% · 120↑/269↓")
    assert reader.calls == [(TUESDAY, now, FIVE_MINUTES)] * 4


async def test_momo_reviews_get_the_fresh_context_payload_or_nothing():
    now = et(TUESDAY, 10, 30)
    context = _context("neutral", now, day=TUESDAY)
    reader = RecordingReader(context, None)
    provide = shadow_service.market_context_provider(TUESDAY, clock=lambda: now, reader=reader)

    assert await provide() == context.to_payload()
    assert await provide() is None
    assert reader.calls == [(TUESDAY, now, FIVE_MINUTES)] * 2


@pytest.mark.parametrize("strategy_name", ["momo", "orb"])
async def test_only_momo_sessions_get_market_context_and_the_risk_off_gate(
    monkeypatch, strategy_name
):
    captured: dict[str, dict[str, object]] = {}
    risk_off = _context("risk_off", et(TUESDAY, 10, 30), summary="Risk-off · S&P -1.2%", day=TUESDAY)
    reader = RecordingReader(risk_off, risk_off)

    def record(name: str) -> Callable[..., SimpleNamespace]:
        def factory(*_args: object, **kwargs: object) -> SimpleNamespace:
            captured[name] = kwargs
            return SimpleNamespace(**kwargs)

        return factory

    class FakeActor:
        connected = True

        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        async def connect(self) -> None:
            pass

        async def disconnect(self) -> None:
            pass

        async def preflight(self, *, required_data_lines: int) -> SimpleNamespace:
            return SimpleNamespace(ready=True, reasons=())

        async def account_snapshot(self) -> None:
            raise AssertionError("the fake runner never reads the account")

    class FakeRunner:
        def __init__(self, **kwargs: object) -> None:
            captured["runner"] = kwargs

        async def run(self, **kwargs: object) -> SessionReport:
            captured["run"] = kwargs
            return SessionReport(run_id=str(kwargs["run_id"]), session_date=TUESDAY)

    async def strategy(**_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(strategy_id="f" * 32)

    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(shadow_service, "_read_market_context", reader)
    monkeypatch.setattr(shadow_service, "ensure_strategy", strategy)
    monkeypatch.setattr(shadow_service, "read_anthropic_api_key", lambda: "sk-test")
    monkeypatch.setattr(shadow_service, "build_anthropic_client", lambda **_kwargs: object())
    monkeypatch.setattr(shadow_service, "AnthropicMessagesTransport", record("transport"))
    monkeypatch.setattr(
        shadow_service,
        "StructuredAIReviewProvider",
        lambda **_kwargs: SimpleNamespace(usage=SimpleNamespace(calls=0, cost_usd=Decimal("0"))),
    )
    monkeypatch.setattr(shadow_service, "TWSPaperActor", FakeActor)
    monkeypatch.setattr(shadow_service, "PaperIBKRMarketData", record("market"))
    monkeypatch.setattr(shadow_service, "ShadowPipeline", record("pipeline"))
    monkeypatch.setattr(shadow_service, "RepositoryBackedShadowRunner", record("repository"))
    monkeypatch.setattr(shadow_service, "ShadowSessionRunner", FakeRunner)
    monkeypatch.setattr(shadow_service, "source_sha", lambda: "abc1234")

    await shadow_service.run_shadow_session(
        config=runtime_config(),
        owner_id="demo",
        session=market_session(TUESDAY),
        clock=lambda: et(TUESDAY, 9, 0),
        sleep=no_sleep,
        strategy_name=strategy_name,
        universe="watchlist" if strategy_name == "momo" else "scanner",
    )

    provide = captured["transport"]["context_provider"]
    gate = captured["runner"]["entry_gate"]
    prints = captured["run"]["policy_fingerprints"]
    if strategy_name == "momo":
        assert captured["runner"]["entry_gate_name"] == "momo"
        assert await provide() == risk_off.to_payload()
        assert await gate() == (False, "Risk-off · S&P -1.2%")
        assert [(call[0], call[2]) for call in reader.calls] == [(TUESDAY, FIVE_MINUTES)] * 2
        assert prints["market_regime_rule"] == "momo pauses in risk_off"
    else:
        assert provide is None and gate is None
        assert "market_regime_rule" not in prints
        assert reader.calls == []


# --------------------------------------------------------------------------
# 2. The session runner's entry gate
# --------------------------------------------------------------------------


class RecordingMarket(FakeMarket):
    """The session runner's fake data, noting when anything is requested."""

    def __init__(self, clock: Callable[[], datetime]) -> None:
        super().__init__(clock)
        self.requested_at: list[datetime] = []

    async def completed_bars(self, *, con_id, now):
        self.requested_at.append(self.clock())
        return await super().completed_bars(con_id=con_id, now=now)

    async def quote(self, *, con_id):
        self.requested_at.append(self.clock())
        return await super().quote(con_id=con_id)

    async def liquidity(self, *, con_id, session_date):
        self.requested_at.append(self.clock())
        return await super().liquidity(con_id=con_id, session_date=session_date)


@dataclass
class GatedDay:
    report: SessionReport
    market: RecordingMarket
    provider: Provider
    account_reads: list[datetime]
    log: list[str]


async def _gated_day(tmp_path, monkeypatch, make_gate) -> GatedDay:
    """The session runner's simulated ORB day with an entry gate named "momo"."""
    clock, sleep = await _world(tmp_path, monkeypatch)
    now = lambda: clock["now"]  # noqa: E731 - the shared simulated clock
    strategy = await ensure_orb_strategy(owner_id="demo")
    provider = Provider()
    market = RecordingMarket(now)
    account_reads: list[datetime] = []

    async def read_account():
        account_reads.append(now())
        return await _account()

    runner = ShadowSessionRunner(
        market=market,
        read_account=read_account,
        runner=RepositoryBackedShadowRunner(
            pipeline=ShadowPipeline(proposal_provider=provider),
            clock=now,
            lease_ttl=timedelta(hours=6),
            lease_renew_interval=timedelta(hours=1),
        ),
        clock=now,
        sleep=sleep,
        entry_gate=make_gate(now),
        entry_gate_name="momo",
    )
    with _log_lines("autotrader.shadow.session") as log:
        report = await runner.run(
            session=market_session(RUNNER_DAY),
            strategy=strategy,
            policy=RiskPolicy(),
            source_sha="abcdef1",
            policy_fingerprints={"risk": RiskPolicy().fingerprint()},
        )
    return GatedDay(report, market, provider, account_reads, log)


def _gate_changes(log: list[str]) -> list[str]:
    return [line for line in log if " paused: " in line or " resumed: " in line]


async def test_risk_off_pauses_assessments_then_resumes_logging_each_change_once(
    tmp_path, monkeypatch
):
    resume_at = OPEN + timedelta(minutes=60)  # 10:30 New York

    def make_gate(now):
        async def gate():
            if now() < resume_at:
                return False, "Risk-off · S&P -1.2% · Nasdaq -1.6%"
            return True, "Neutral · S&P -0.2% · Nasdaq -0.1%"

        return gate

    day = await _gated_day(tmp_path, monkeypatch, make_gate)

    # Polls 09:50:05 through 10:25:05 were paused: no data, account or review.
    assert (day.report.polls, day.report.paused_polls) == (26, 8)
    assert day.market.requested_at and min(day.market.requested_at) >= resume_at
    assert min(day.account_reads) >= resume_at
    assert _gate_changes(day.log) == [
        "WARNING momo paused: Risk-off · S&P -1.2% · Nasdaq -1.6%",
        "WARNING momo resumed: Neutral · S&P -0.2% · Nasdaq -0.1%",
    ]
    # Paused candidates stayed pending and were decided once the gate opened.
    assert dict(day.report.outcomes) == {
        "shadow_accepted": 1,
        "rule_rejected": 2,
        "data_blocked": 1,
    }
    assert day.report.summary()["paused_polls"] == 8


@pytest.mark.parametrize(
    "answer",
    [RuntimeError("context store locked"), None, ("no", "not a bool")],
    ids=["raises", "no-answer", "non-bool"],
)
async def test_a_broken_gate_never_blocks_entries(tmp_path, monkeypatch, answer):
    def make_gate(_now):
        async def gate():
            if isinstance(answer, Exception):
                raise answer
            return answer

        return gate

    day = await _gated_day(tmp_path, monkeypatch, make_gate)

    assert day.report.paused_polls == 0
    # Identical to the ungated day in test_session_runner.
    assert dict(day.report.outcomes) == {
        "shadow_accepted": 1,
        "rule_rejected": 2,
        "data_blocked": 1,
    }
    failures = [line for line in day.log if "entry gate failed" in line]
    assert len(failures) == 1
    assert failures[0].startswith("WARNING momo entry gate failed (")
    assert failures[0].endswith("); entries allowed until it recovers")
    assert _gate_changes(day.log) == []


async def test_candidates_paused_until_the_cutoff_are_still_finalized(tmp_path, monkeypatch):
    def make_gate(_now):
        async def gate():
            return False, "Risk-off · S&P -1.4%"

        return gate

    day = await _gated_day(tmp_path, monkeypatch, make_gate)

    assert day.report.polls == day.report.paused_polls == 26
    assert day.market.requested_at == [] and day.provider.calls == 0
    cutoff = datetime(2026, 8, 27, 16, 0, tzinfo=timezone.utc)  # 12:00 New York
    assert all(read >= cutoff for read in day.account_reads)
    # Never assessed, so the sweep records each as data-blocked; nothing is accepted.
    assert dict(day.report.outcomes) == {"data_blocked": 4}
    assert len(await list_decisions(owner_id="demo", run_id=day.report.run_id)) == 4
    assert _gate_changes(day.log) == ["WARNING momo paused: Risk-off · S&P -1.4%"]


# --------------------------------------------------------------------------
# 4. The phone
# --------------------------------------------------------------------------


def test_market_messages_are_plain_text_with_the_regime_up_front():
    context = _context("neutral", _net(9, 15))

    assert format_market_update(context, _hot_rows(_net(9, 15))).splitlines() == [
        "📈 Market · 09:15:00 ET · NEUTRAL",
        "neutral at 09:15:00",
        "Leading sectors: Technology +1.24%, Energy +0.85%, Industrials +0.40%",
        "Lagging sectors: Utilities -0.92%, Real Estate -0.55%, Consumer Staples -0.31%",
        "Hot list: NVDA +6.20%, SMCI +5.80%, AMD +4.10%, PLTR +3.90%, TSLA +3.10%",
    ]
    assert format_market_update(context, None).splitlines()[-1] == "Hot list: unavailable"
    assert format_market_update(context, ()).splitlines()[-1] == "Hot list: none"
    assert [
        format_regime_change(_context(regime, _net(10, 0))).splitlines()[0]
        for regime in ("risk_off", "neutral", "risk_on")
    ] == [
        "🧭 Market turned RISK-OFF — momentum entries paused",
        "🧭 Market turned NEUTRAL — entries allowed",
        "🧭 Market turned RISK-ON — entries allowed",
    ]
    assert format_regime_change(_context("risk_off", _net(10, 0))).splitlines()[1:] == [
        "risk_off at 10:00:00",
        "as of 10:00:00 ET",
    ]


# A sweep every 75 s from 07:55 puts 09:15, 12:00, 15:30 and 16:00 on the grid.
SWEEP = timedelta(seconds=75)


class MarketFeed:
    """The screener's sweeps from 07:55 to 16:00 ET, each carrying a context."""

    def __init__(
        self,
        clock: FakeTime,
        regimes: Sequence[tuple[datetime, str]],
        *,
        gaps: Sequence[tuple[datetime, datetime]] = (),
    ) -> None:
        self.clock = clock
        self.regimes = sorted(regimes)
        self.gaps = tuple(gaps)
        self.max_ages: list[timedelta] = []

    def sweep(self, now: datetime) -> datetime | None:
        first, last = _net(7, 55), _net(16, 0)
        moment = min(now, last)
        if moment < first:
            return None
        observed = first + SWEEP * ((moment - first) // SWEEP)
        for start, end in self.gaps:
            if start <= observed < end:
                return self.sweep(start - timedelta(microseconds=1))
        return observed

    def regime(self, observed: datetime) -> str:
        return [regime for start, regime in self.regimes if start <= observed][-1]

    async def context(
        self, session_date: date, *, now: datetime, max_age: timedelta
    ) -> FakeMarketContext | None:
        assert session_date == NOTIFY_DAY
        self.max_ages.append(max_age)
        observed = self.sweep(now)
        if observed is None or now - observed > max_age:
            return None
        return _context(self.regime(observed), observed)

    async def hot_list(self, session_date: date) -> tuple[HotListRow, ...]:
        observed = self.sweep(self.clock.now)
        return () if observed is None else _hot_rows(observed)


async def _notify_paths(tmp_path, monkeypatch) -> UserPaths:
    await _use_database(tmp_path, monkeypatch)
    # Coarser ticks keep a simulated trading day fast; every tick polls the market.
    monkeypatch.setattr(notify_service, "LOOP_TICK_SECONDS", notify_service.MARKET_POLL_SECONDS)
    paths = UserPaths(home=tmp_path / "home")
    save_chat_id(telegram_config_file(paths), CHAT_ID)
    return paths


async def _notify(
    paths: UserPaths,
    telegram: FakeTelegram,
    clock: FakeTime,
    feed: MarketFeed,
    *,
    stop_at: datetime | None = None,
) -> None:
    """Run the notifier; ``stop_at`` cancels it there, like a supervisor stop."""
    env = _environment(
        paths, telegram, clock, read_market_context=feed.context, read_hot_list=feed.hot_list
    )
    task = asyncio.ensure_future(run_notifier(session_date=NOTIFY_DAY, env=env))
    if stop_at is None:
        assert await task == 0
        return
    clock.at(stop_at, task.cancel)
    with pytest.raises(asyncio.CancelledError):
        await task


def _starting(telegram: FakeTelegram, prefix: str) -> list[str]:
    return [text for text in telegram.sent() if text.startswith(prefix)]


def _update(observed: str) -> str:
    return "\n".join(
        [
            f"📈 Market · {observed} ET · NEUTRAL",
            f"neutral at {observed}",
            "Leading sectors: Technology +1.24%, Energy +0.85%, Industrials +0.40%",
            "Lagging sectors: Utilities -0.92%, Real Estate -0.55%, Consumer Staples -0.31%",
            "Hot list: NVDA +6.20%, SMCI +5.80%, AMD +4.10%, PLTR +3.90%, TSLA +3.10%",
        ]
    )


async def test_market_updates_go_out_once_per_slot_across_a_restart(tmp_path, monkeypatch):
    paths = await _notify_paths(tmp_path, monkeypatch)
    clock = FakeTime(_net(9, 10))
    feed = MarketFeed(clock, [(_net(7, 55), "neutral")])

    morning = FakeTelegram()
    await _notify(paths, morning, clock, feed, stop_at=_net(12, 10))
    # Restarted inside the 12:00 update's grace window: it must not repeat.
    clock.now = _net(12, 20)
    afternoon = FakeTelegram()
    await _notify(paths, afternoon, clock, feed)

    assert _starting(morning, "📈") == [_update("09:15:00"), _update("12:00:00")]
    assert _starting(afternoon, "📈") == [_update("15:30:00")]
    assert _starting(morning, "🧭") == _starting(afternoon, "🧭") == []
    (report,) = _starting(afternoon, "📊 Daily report")
    # The screener's last sweep is 16:00; the report accepts it at 16:05.
    assert "Market at close: neutral at 16:00:00 (16:00:00 ET)" in report.splitlines()
    assert max(feed.max_ages) >= timedelta(hours=1) and min(feed.max_ages) == FIVE_MINUTES
    saved = json.loads(state_file(paths, NOTIFY_DAY).read_text(encoding="utf-8"))
    assert {key: saved["delivered"][key] for key in ("market:0915", "market:1200", "market:1530")} == {
        "market:0915": "sent",
        "market:1200": "sent",
        "market:1530": "sent",
    }
    assert saved["market_regime"] == "neutral"


@pytest.mark.parametrize(
    ("feed_resumes", "expected"),
    [((9, 30), [_update("09:30:00")]), ((9, 50), [])],
    ids=["late-but-in-grace", "skipped"],
)
async def test_an_update_waits_for_a_fresh_context_then_is_skipped(
    tmp_path, monkeypatch, feed_resumes, expected
):
    paths = await _notify_paths(tmp_path, monkeypatch)
    clock = FakeTime(_net(9, 10))
    feed = MarketFeed(
        clock, [(_net(7, 55), "neutral")], gaps=[(_net(9, 0), _net(*feed_resumes))]
    )
    telegram = FakeTelegram()

    with _log_lines("autotrader.shadow.notify") as log:
        await _notify(paths, telegram, clock, feed, stop_at=_net(10, 0))

    assert _starting(telegram, "📈") == expected
    skipped = [line for line in log if "skipped for today" in line]
    if expected:
        assert skipped == []
    else:
        assert skipped == ["WARNING market update 09:15 ET skipped for today: not sent by 09:45 ET"]


@pytest.mark.parametrize("problem", ["raises", "stale"])
async def test_an_unreadable_or_stale_hot_list_never_holds_the_update_back(
    tmp_path, monkeypatch, problem
):
    paths = await _notify_paths(tmp_path, monkeypatch)
    clock = FakeTime(_net(9, 14))
    feed = MarketFeed(clock, [(_net(7, 55), "neutral")])

    async def hot_list(session_date: date) -> tuple[HotListRow, ...]:
        if problem == "raises":
            raise ValueError("screener.v1 payload failed validation")
        return _hot_rows(clock.now - timedelta(minutes=10))

    feed.hot_list = hot_list  # type: ignore[method-assign]
    telegram = FakeTelegram()

    await _notify(paths, telegram, clock, feed, stop_at=_net(9, 20))

    (update,) = _starting(telegram, "📈")
    assert update.splitlines()[0] == "📈 Market · 09:15:00 ET · NEUTRAL"
    assert update.splitlines()[-1] == "Hot list: unavailable"


def _alert(regime: str, observed: str) -> str:
    effect = "momentum entries paused" if regime == "risk_off" else "entries allowed"
    label = {"risk_off": "RISK-OFF", "neutral": "NEUTRAL", "risk_on": "RISK-ON"}[regime]
    return "\n".join(
        [f"🧭 Market turned {label} — {effect}", f"{regime} at {observed}", f"as of {observed} ET"]
    )


async def test_regime_changes_alert_at_most_every_fifteen_minutes_and_converge(
    tmp_path, monkeypatch
):
    paths = await _notify_paths(tmp_path, monkeypatch)
    clock = FakeTime(_net(8, 0))
    feed = MarketFeed(
        clock,
        [
            (_net(7, 55), "risk_on"),
            (_net(8, 30), "neutral"),  # pre-market: only the baseline moves
            (_net(10, 0), "risk_off"),
            (_net(10, 5), "neutral"),  # held back by the 15-minute limit ...
            (_net(10, 8), "risk_off"),  # ... back where the phone already is
            (_net(10, 10), "neutral"),  # ... and reported once the limit allows
            (_net(11, 0), "risk_on"),
            (_net(11, 15), "risk_off"),  # while the notifier is down
        ],
    )

    first = FakeTelegram()
    await _notify(paths, first, clock, feed, stop_at=_net(11, 10))
    clock.now = _net(11, 20)
    second = FakeTelegram()
    await _notify(paths, second, clock, feed, stop_at=_net(11, 40))

    assert _starting(first, "📈 Market")[0].splitlines()[0] == "📈 Market · 09:15:00 ET · NEUTRAL"
    assert _starting(first, "🧭") == [
        _alert("risk_off", "10:00:00"),
        _alert("neutral", "10:15:00"),
        _alert("risk_on", "11:00:00"),
    ]
    # The regime the phone last heard survives the restart, so the change is news.
    assert _starting(second, "🧭") == [_alert("risk_off", "11:20:00")]


# --------------------------------------------------------------------------
# The real trading.market.context behind each consumer
# --------------------------------------------------------------------------


def test_the_market_context_module_matches_what_the_consumers_call():
    context_module = pytest.importorskip("trading.market.context")

    parameters = inspect.signature(context_module.latest_market_context).parameters
    assert list(parameters) == ["session_date", "now", "max_age"]
    assert parameters["now"].kind is inspect.Parameter.KEYWORD_ONLY
    assert parameters["max_age"].kind is inspect.Parameter.KEYWORD_ONLY
    assert parameters["max_age"].default == FIVE_MINUTES
    assert set(context_module.REGIMES) == {"risk_on", "neutral", "risk_off"}
    consumed = {"session_date", "observed_at", "regime", "summary", "sector_leaders", "sector_laggards"}
    assert consumed <= set(inspect.signature(context_module.MarketContext).parameters)


async def _store_sweep(observed_at: datetime, market: dict[str, object]) -> None:
    from db.core import get_db
    from trading.market.screener import ScreenerPayload, utc_iso

    def row(rank: int, symbol: str, change: str, *, hot: bool) -> dict[str, object]:
        return {
            "symbol": symbol,
            "con_id": 2000 + rank,
            "primary_exchange": "NASDAQ",
            "source": "watchlist",
            "last": "100.00",
            "bid": "99.99",
            "ask": "100.01",
            "prior_close": "95.00",
            "open": "96.00",
            "day_high": "101.00",
            "day_low": "95.50",
            "change_pct": change,
            "gap_pct": "1.05",
            "volume_shares": "1000000",
            "rvol": "2.50",
            "dollar_volume": "100000000.00",
            "pct_from_high": "-0.99",
            "score": f"{Decimal(100 - rank) / 100:.4f}",
            "hot": hot,
            "hot_rank": rank if hot else None,
        }

    rows = [row(rank, symbol, change, hot=True) for rank, (symbol, change) in enumerate(HOT_NAMES, 1)]
    rows.append(row(len(rows) + 1, "KO", "-0.40", hot=False))
    payload = ScreenerPayload.model_validate(
        {
            "schema": "screener.v1",
            "session_date": NOTIFY_DAY.isoformat(),
            "observed_at": utc_iso(observed_at),
            "sweep_seconds": 61.2,
            "universe_size": 503,
            "qualified": len(rows),
            "quoted": len(rows),
            "errors": 0,
            "rows": rows,
            "market": market,
        }
    )
    async with get_db() as db:
        await db.execute(
            "INSERT INTO autotrader_screener_snapshots VALUES (?,?,?,?)",
            ("sweep-1", payload.session_date, payload.observed_at, payload.to_json()),
        )
        await db.commit()


async def test_a_stored_risk_off_sweep_pauses_momo_and_reaches_claude_and_the_phone(
    tmp_path, monkeypatch
):
    context_module = pytest.importorskip("trading.market.context")
    await _use_database(tmp_path, monkeypatch)
    observed = _net(10, 30)
    stored = context_module.MarketContext(
        session_date=NOTIFY_DAY,
        observed_at=observed,
        regime="risk_off",
        spy_change_pct=Decimal("-1.20"),
        qqq_change_pct=Decimal("-1.60"),
        iwm_change_pct=Decimal("-1.90"),
        dia_change_pct=Decimal("-0.80"),
        breadth_up_pct=Decimal("30.85"),
        advancers=120,
        decliners=269,
        median_change_pct=Decimal("-1.05"),
        sector_leaders=(("Utilities", Decimal("0.42")), ("Consumer Staples", Decimal("0.10"))),
        sector_laggards=(("Technology", Decimal("-2.10")), ("Communication", Decimal("-1.70"))),
        summary="Risk-off · S&P -1.2% · Nasdaq -1.6% · 120↑/269↓",
    )
    await _store_sweep(observed, stored.to_payload())
    now = observed + timedelta(seconds=40)
    read = shadow_service._read_market_context

    gate = shadow_service.risk_off_entry_gate(NOTIFY_DAY, clock=lambda: now, reader=read)
    provide = shadow_service.market_context_provider(NOTIFY_DAY, clock=lambda: now, reader=read)
    assert await gate() == (False, stored.summary)
    assert await provide() == stored.to_payload()
    # Six minutes later the sweep is stale: entries reopen, Claude sees nothing.
    later = observed + timedelta(minutes=6)
    stale_gate = shadow_service.risk_off_entry_gate(NOTIFY_DAY, clock=lambda: later, reader=read)
    assert await stale_gate() == (True, "no fresh market context")

    context = await notify_service._latest_market_context(NOTIFY_DAY, now=now, max_age=FIVE_MINUTES)
    hot = await notify_service._latest_hot_list(NOTIFY_DAY)
    assert format_market_update(context, hot).splitlines() == [
        "📈 Market · 10:30:00 ET · RISK-OFF",
        "Risk-off · S&P -1.2% · Nasdaq -1.6% · 120↑/269↓",
        "Leading sectors: Utilities +0.42%, Consumer Staples +0.10%",
        "Lagging sectors: Technology -2.10%, Communication -1.70%",
        "Hot list: NVDA +6.20%, SMCI +5.80%, AMD +4.10%, PLTR +3.90%, TSLA +3.10%",
    ]
    assert format_regime_change(context).splitlines()[0] == (
        "🧭 Market turned RISK-OFF — momentum entries paused"
    )

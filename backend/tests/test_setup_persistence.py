"""Every formed setup is saved, and V2's decisions stay exactly what they were.

The golden values below were captured from the code before setup persistence
existed (7354b89): the real 2026-10-12 MOMO_HOD_V2 session (BF B, FIGS) through
the production runner, and the ORB pipeline fixture with three reviewers.
Decisions are compared without their random id, creation time, approval
token and expiry (now-based); everything else, keys and fingerprints
included, must match byte for byte.
"""
from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import json
import logging
from pathlib import Path
import sqlite3
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from tests.schema_support import apply_up_to
from trading.contracts import (
    AIReview,
    Candidate,
    CompletedBar,
    Decision,
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
from trading.market.ibkr_boundary import DailyBarBatch, IBKRDailyBar, liquidity_profile
from trading.orchestration import CandidateInput, RepositoryBackedShadowRunner, ShadowPipeline
from trading.orchestration.pipeline import SETUP_PERSIST_FAILED
from trading.orchestration.session_runner import momo_strategy_spec
from trading.rules.momo import MomoConfig, MomoStrategy
from trading.storage import manager_setups
from trading.storage.manager_setups import (
    BAR_INTERVAL,
    FormedSetup,
    formed_setup,
    record_formed_setup,
    signal_bar_start,
)

NEW_YORK = ZoneInfo("America/New_York")
FIXTURES = Path(__file__).parent / "fixtures" / "momo"
SESSION = date(2026, 10, 12)
TICK = Decimal("0.01")

GOLDEN_MOMO = {
    "BF B": (
        "shadow_accepted",
        "97b81903655d3c1d797dddcf7ae76bf0dc315648bcca7702805dcf126e17e50e",
        "54d16ad5c86cb16ad05a21b863a0bfaa9b048df04d5ea36cc4c046ddb74937c1",
        "b957927c8a181707740c75c295dbdde2667843ba33205a41c3adce7fc33b6e6f",
    ),
    "FIGS": (
        "rule_rejected",
        "f89276c046105b25ca5015035d340c961e92a40a56189c3f4860b9dbff85cc1a",
        None,
        "4a5e365ce1f0ae2f22c041ffca25e23efcb241ea004bdfcace43fda14eef06cd",
    ),
}
ORB_KEY = "50b844f3b6ac3c101cb25e3dd4c0f02809a06b7f58d48f606366446079efa955"
ORB_SETUP = "5543ef26c922b2f3616f8743e18f1af1997b3490f2275c9adc098a282d4c6e51"
GOLDEN_ORB = {
    "PROPOSE": ("shadow_accepted", "7fa19d5c7598aa035ffed57d2c728e4b608c1c035e272ed1e241021f4e9dd6c4"),
    "ABSTAIN": ("ai_abstained", "9bae7bb82bbc028eb2ffcbdff29c9eb1e1de872171e9b1b1632f0c4017a03874"),
    "fail": ("ai_blocked", "ae78065266c8a2a331991b28b1b6735ad1b39524ffa31514d97798714a20c176"),
}


def _normalized(payload: dict[str, Any]) -> str:
    """The decision without its random id, creation time, approval token and expiry."""
    payload = json.loads(json.dumps(payload))
    payload.pop("id")
    payload.pop("created_at")
    risk = payload.get("risk")
    if risk is not None:
        risk.pop("expires_at")
        intent = risk.get("intent")
        if intent is not None:
            intent.pop("expires_at")
            intent.pop("approval_token")
    return sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _digest(decision: Decision) -> str:
    return _normalized(decision.model_dump(mode="json"))


class _Records(logging.Handler):
    """Collects one logger's records whatever its parents' propagation is."""

    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@pytest.fixture
def pipeline_log() -> Iterator[_Records]:
    logger = logging.getLogger("autotrader.shadow.pipeline")
    handler = _Records()
    logger.addHandler(handler)
    try:
        yield handler
    finally:
        logger.removeHandler(handler)


def _failures(log: _Records) -> list[logging.LogRecord]:
    return [record for record in log.records if record.getMessage().startswith(SETUP_PERSIST_FAILED)]


# --- the real MOMO_HOD_V2 session --------------------------------------------------


def _et(day: date, hour: int, minute: int, second: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute, second), tzinfo=NEW_YORK).astimezone(timezone.utc)


@dataclass(frozen=True)
class _Replay:
    symbol: str
    con_id: int
    bars: tuple[CompletedBar, ...]
    reference_price: Decimal
    average_dollar_volume: Decimal
    signal_end: datetime
    live: dict[str, Any]

    def candidate(self, scanner_run_id: str, rank: int) -> Candidate:
        return Candidate(
            id=f"{scanner_run_id}-{self.con_id}",
            scanner_run_id=scanner_run_id,
            con_id=self.con_id,
            symbol=self.symbol,
            primary_exchange="NYSE",
            rank=rank,
            admission_status="admitted",
        )

    def bars_until(self, now: datetime) -> tuple[CompletedBar, ...]:
        return tuple(bar for bar in self.bars if bar.interval_end_utc <= now)

    def quote(self, now: datetime) -> QuoteSnapshot:
        latest = self.bars_until(now)[-1]
        if latest.interval_end_utc == self.signal_end:
            bid, ask = (Decimal(self.live["replay_quote"][side]) for side in ("bid", "ask"))
        else:
            bid, ask = latest.close, latest.close + TICK
        return QuoteSnapshot(con_id=self.con_id, bid=bid, ask=ask, observed_at=now, min_tick=TICK)


def _load(name: str) -> _Replay:
    raw = json.loads((FIXTURES / f"{name}_2026-10-12.json").read_text())
    con_id = raw["con_id"]
    bars = tuple(
        CompletedBar(
            con_id=con_id,
            interval_start_utc=datetime.fromisoformat(item["interval_start_utc"]),
            interval_end_utc=datetime.fromisoformat(item["interval_end_utc"]),
            open=item["open"],
            high=item["high"],
            low=item["low"],
            close=item["close"],
            volume=item["volume"],
            rth=True,
            source="ibkr",
            data_mode="historical",
        )
        for item in raw["bars"]
    )
    daily = DailyBarBatch(
        bars=tuple(
            IBKRDailyBar(
                con_id=con_id,
                session_date=date.fromisoformat(item["session_date"]),
                close=Decimal(item["close"]),
                volume=Decimal(item["volume"]),
            )
            for item in raw["daily_bars"]
        )
    )
    profile = liquidity_profile(daily, session_date=SESSION, volume_multiplier=Decimal("100"))
    hour, minute = (int(part) for part in raw["live_context"]["signal_bar_end_et"].split(":"))
    return _Replay(
        symbol=raw["symbol"],
        con_id=con_id,
        bars=bars,
        reference_price=profile.reference_price,
        average_dollar_volume=profile.average_dollar_volume,
        signal_end=_et(SESSION, hour, minute),
        live=raw["live_context"],
    )


class _Reviewer:
    def __init__(self) -> None:
        self.setups: list[RuleSetup] = []

    async def propose_trade(self, *, setup: RuleSetup) -> AIReview:
        self.setups.append(setup)
        return AIReview(
            setup_fingerprint=setup.fingerprint(),
            decision="PROPOSE",
            confidence="0.7",
            rationale="Replay review.",
            model_id="test-model",
            prompt_version="momo-review-v2",
            response_hash="e" * 64,
            latency_ms=1,
            cost_usd="0",
        )


async def _replay_momo_session(
    database: Path,
    monkeypatch,
    *,
    runner: RepositoryBackedShadowRunner | None = None,
    reviewer: _Reviewer | None = None,
    clock: dict[str, datetime] | None = None,
    **pipeline_options: Any,
) -> tuple[dict[str, Decision], _Reviewer]:
    """The 2026-10-12 MOMO_HOD_V2 day, polled like the session runner does.

    ``runner`` drives a runner the production composition root built
    (``shadow_service.run_shadow_session``) instead of this helper's own; it
    comes with the ``reviewer`` its pipeline asks and the ``clock`` dict its
    clock reads, which the replay moves forward poll by poll.
    """
    from config import cfg
    from db.core import get_db

    monkeypatch.setattr(cfg, "DB_PATH", str(database))
    spec = momo_strategy_spec()
    start = _et(SESSION, 9, 0)
    strategy = StrategyRecord(
        strategy_id="strategy-golden-momo",
        owner_id="owner-a",
        version=1,
        content_hash=spec.fingerprint(),
        spec=spec,
        state=StrategyState.SHADOW,
        created_at=start,
        updated_at=start,
    )
    async with get_db() as db:
        stamp = start.isoformat()
        await db.execute(
            "INSERT INTO autotrader_strategy_identities VALUES (?, ?, ?, NULL)",
            (strategy.strategy_id, "owner-a", stamp),
        )
        await db.execute(
            """INSERT INTO autotrader_strategy_versions
               (strategy_id, version, content_hash, spec_json, created_at, created_by)
               VALUES (?, 1, ?, ?, ?, ?)""",
            (strategy.strategy_id, spec.fingerprint(), json.dumps(spec.model_dump(mode="json"), sort_keys=True), stamp, "owner-a"),
        )
        await db.execute(
            "INSERT INTO autotrader_strategy_lifecycle_v2 VALUES (?, 1, 'shadow', 0, ?)",
            (strategy.strategy_id, stamp),
        )
        await db.commit()
    run = Run(
        id="run-golden-momo",
        owner_id="owner-a",
        source_sha="abcdef1",
        strategy_id=strategy.strategy_id,
        strategy_version=1,
        policy_fingerprints={"risk": RiskPolicy().fingerprint(), "momo_hod": MomoConfig().fingerprint()},
        session_date=SESSION,
        mode=EngineMode.SHADOW,
    )
    parameters = {"source": "screener_hotlist", "top": 35}
    scanner = ScannerRun(
        id=f"{run.id}-scanner",
        run_id=run.id,
        parameters=parameters,
        parameters_fingerprint=fingerprint_payload(parameters),
        status="completed",
        started_at=_et(SESSION, 9, 55),
    )
    replays = {replay.symbol: replay for replay in (_load("bf_b"), _load("figs"))}
    candidates = {
        symbol: replay.candidate(scanner.id, rank) for rank, (symbol, replay) in enumerate(replays.items(), 1)
    }
    if runner is None:
        reviewer = _Reviewer()
        clock = {"now": _et(SESSION, 10, 0, 5)}
        runner = RepositoryBackedShadowRunner(
            pipeline=ShadowPipeline(proposal_provider=reviewer, strategy=MomoStrategy(), **pipeline_options),
            clock=lambda: clock["now"],
            lease_ttl=timedelta(hours=6),
            lease_renew_interval=timedelta(hours=1),
        )
    else:
        assert reviewer is not None and clock is not None and not pipeline_options
        clock["now"] = _et(SESSION, 10, 0, 5)
    portfolio = PortfolioSnapshot(observed_at=clock["now"], net_liquidation="665000")

    def item(symbol: str, now: datetime, *, quoted: bool = False) -> CandidateInput:
        replay = replays[symbol]
        return CandidateInput(
            candidate=candidates[symbol],
            bars=replay.bars_until(now),
            quote=replay.quote(now) if quoted else None,
            reference_price=replay.reference_price,
            average_dollar_volume=replay.average_dollar_volume,
        )

    async with runner.open_session(
        run=run,
        scanner_run=scanner,
        strategy=strategy,
        candidates=tuple(candidates.values()),
        policy=RiskPolicy(),
        holder_id="golden",
    ) as session:
        poll = _et(SESSION, 10, 0, 5)
        latest: dict[str, CandidateInput] = {}
        while poll < session.cutoff:
            clock["now"] = poll
            for candidate in session.pending():
                latest[candidate.symbol] = item(candidate.symbol, poll)
                assessment = await session.assess(latest[candidate.symbol], portfolio=portfolio, now=poll)
                if assessment.needs_quote:
                    await session.assess(item(candidate.symbol, poll, quoted=True), portfolio=portfolio, now=poll)
            poll += BAR_INTERVAL
        clock["now"] = session.cutoff
        final = await session.finalize([latest["FIGS"]], portfolio=portfolio, now=session.cutoff)
    return {symbol: final[candidate.id] for symbol, candidate in candidates.items()}, reviewer


def _assert_golden_momo(decisions: dict[str, Decision], database: Path) -> None:
    for symbol, (outcome, key, setup_fingerprint, digest) in GOLDEN_MOMO.items():
        decision = decisions[symbol]
        assert (decision.outcome.value, decision.decision_key, decision.setup_fingerprint) == (
            outcome, key, setup_fingerprint,
        ), symbol
        assert _digest(decision) == digest, symbol
    with sqlite3.connect(database) as db:
        rows = db.execute(
            "SELECT candidate_id, payload_json FROM autotrader_candidate_decisions ORDER BY candidate_id"
        ).fetchall()
    assert [_normalized(json.loads(payload)) for _, payload in rows] == [
        GOLDEN_MOMO["BF B"][3], GOLDEN_MOMO["FIGS"][3],
    ]


def _setup_rows(database: Path) -> list[dict[str, Any]]:
    with sqlite3.connect(database) as db:
        db.row_factory = sqlite3.Row
        return [dict(row) for row in db.execute("SELECT * FROM autotrader_manager_setups ORDER BY id")]


@pytest.mark.anyio
async def test_momo_v2_decisions_are_golden_and_its_one_setup_is_saved(tmp_path, monkeypatch, pipeline_log):
    from config import cfg
    from db.core import init_db

    database = tmp_path / "momo.db"
    monkeypatch.setattr(cfg, "DB_PATH", str(database))
    await init_db()

    decisions, reviewer = await _replay_momo_session(database, monkeypatch)

    _assert_golden_momo(decisions, database)
    assert _failures(pipeline_log) == []
    [row] = _setup_rows(database)
    [reviewed] = reviewer.setups
    assert row["setup_fingerprint"] == GOLDEN_MOMO["BF B"][2] == reviewed.fingerprint()
    assert (row["run_id"], row["candidate_id"], row["symbol"], row["con_id"]) == (
        "run-golden-momo", "run-golden-momo-scanner-4931", "BF B", 4931,
    )
    assert (row["strategy_id"], row["strategy_version"], row["strategy_name"], row["strategy_kind"]) == (
        "strategy-golden-momo", 1, "MOMO_HOD_V2", reviewed.strategy_kind,
    )
    assert row["spec_fingerprint"] == momo_strategy_spec().fingerprint()
    # The 11:45-11:50 ET signal bar, as the live run logged it.
    assert row["session_date"] == "2026-10-12"
    assert row["bar_ts"] == _et(SESSION, 11, 45).isoformat()
    assert row["detected_at"] == _et(SESSION, 11, 50).isoformat()
    assert row["hour_bucket"] == 11
    assert (row["entry_price"], row["stop_price"], row["target_price"], row["atr"]) == (
        format(reviewed.entry_limit, "f"), format(reviewed.stop_price, "f"),
        format(reviewed.target_price, "f"), format(reviewed.atr, "f"),
    )
    # Entry 26.77, stop 26.51 below the signal bar, target at 2R.
    assert (row["entry_price"], row["stop_price"], row["target_price"]) == ("26.77", "26.51", "27.29")
    # Exactly the levels and the setup the reviewer received.
    payload = reviewed.model_dump(mode="json")
    assert json.loads(row["levels_json"]) == payload["levels"] and payload["levels"]
    assert json.loads(row["setup_json"]) == payload
    assert RuleSetup.model_validate(json.loads(row["setup_json"])).fingerprint() == row["setup_fingerprint"]


@pytest.mark.anyio
@pytest.mark.parametrize("failure", ["recorder_raises", "table_missing"])
async def test_momo_v2_decisions_are_golden_when_the_setup_cannot_be_saved(
    tmp_path, monkeypatch, pipeline_log, failure
):
    from config import cfg
    from db.core import init_db

    database = tmp_path / "momo.db"
    options: dict[str, Any] = {}
    if failure == "recorder_raises":
        monkeypatch.setattr(cfg, "DB_PATH", str(database))
        await init_db()

        async def broken(row: FormedSetup) -> bool:
            raise RuntimeError("disk full")

        options["setup_recorder"] = broken
    else:
        # The real recorder against a database one migration behind.
        await apply_up_to(database, 12)

    decisions, _reviewer = await _replay_momo_session(database, monkeypatch, **options)

    _assert_golden_momo(decisions, database)
    [failed] = _failures(pipeline_log)
    assert failed.levelno == logging.ERROR and failed.exc_info is not None
    assert GOLDEN_MOMO["BF B"][2] in failed.getMessage()


# --- the ORB pipeline with three reviewers ----------------------------------------------


NOW = datetime(2026, 1, 2, 16, 30, tzinfo=timezone.utc)


def _orb_strategy() -> StrategyRecord:
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


def _orb_bars(con_id: int, *, breakout: bool = True) -> tuple[CompletedBar, ...]:
    bars: list[CompletedBar] = []
    start = datetime(2026, 1, 2, 14, 30, tzinfo=timezone.utc)
    for index in range(18):
        high, close = Decimal("101"), Decimal("100.5")
        if index in {3, 4, 5}:
            high, close = Decimal("102"), Decimal("101")
        if index >= 14 and breakout:
            high, close = Decimal("104"), Decimal("103")
        bars.append(
            CompletedBar(
                con_id=con_id,
                interval_start_utc=start + timedelta(minutes=5 * index),
                interval_end_utc=start + timedelta(minutes=5 * (index + 1)),
                open=Decimal("100"),
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


def _orb_input(symbol: str = "AAPL", con_id: int = 101, *, breakout: bool = True) -> CandidateInput:
    candidate = Candidate(
        id=f"candidate-{symbol.lower()}",
        scanner_run_id="scanner-1",
        con_id=con_id,
        symbol=symbol,
        primary_exchange="NASDAQ",
        rank=1,
        admission_status="admitted",
    )
    quote = QuoteSnapshot(con_id=con_id, bid=Decimal("102.99"), ask=Decimal("103"), observed_at=NOW, min_tick=Decimal("0.01"))
    return CandidateInput(
        candidate=candidate,
        bars=_orb_bars(con_id, breakout=breakout),
        quote=quote,
        reference_price="103",
        average_dollar_volume="25000000",
    )


class _OrbReviewer:
    def __init__(self, mode: str) -> None:
        self.mode = mode

    async def propose_trade(self, *, setup: RuleSetup) -> AIReview:
        if self.mode == "fail":
            raise RuntimeError("provider unavailable")
        return AIReview(
            setup_fingerprint=setup.fingerprint(),
            decision=self.mode,
            confidence="0.8",
            rationale="Fixture review.",
            model_id="fixture-model",
            prompt_version="fixture-prompt",
            response_hash="a" * 64,
            latency_ms=1,
            cost_usd="0",
        )


@pytest.mark.anyio
@pytest.mark.parametrize("review", ["PROPOSE", "ABSTAIN", "fail"])
@pytest.mark.parametrize("saving", ["saved", "nothing", "fails"])
async def test_orb_decisions_are_golden_whatever_happens_to_the_setup(review, saving, pipeline_log):
    events: list[str] = []
    saved: list[FormedSetup] = []

    async def decisions_recorder(*, decision: Decision, owner_id: str) -> None:
        events.append("decision")

    async def setup_recorder(row: FormedSetup) -> bool:
        events.append("setup")
        if saving == "fails":
            raise sqlite3.OperationalError("database is locked")
        if saving == "saved":
            saved.append(row)
        return True

    pipeline = ShadowPipeline(
        proposal_provider=_OrbReviewer(review),
        decision_recorder=decisions_recorder,
        setup_recorder=setup_recorder,
    )
    [decision] = await pipeline.run(
        run_id="run-golden-orb",
        strategy=_orb_strategy(),
        candidates=[_orb_input()],
        portfolio=PortfolioSnapshot(observed_at=NOW, net_liquidation="100000"),
        policy=RiskPolicy(),
        session_date=date(2026, 1, 2),
        now=NOW,
    )

    outcome, digest = GOLDEN_ORB[review]
    assert (decision.outcome.value, decision.decision_key, decision.setup_fingerprint) == (outcome, ORB_KEY, ORB_SETUP)
    assert _digest(decision) == digest
    # The decision is recorded first; the setup only after it.
    assert events == ["decision", "setup"]
    assert len(_failures(pipeline_log)) == (1 if saving == "fails" else 0)
    if saving == "saved":
        [row] = saved
        assert (row.setup_fingerprint, row.run_id, row.candidate_id, row.con_id) == (ORB_SETUP, "run-golden-orb", "candidate-aapl", 101)
        assert (row.strategy_name, row.strategy_kind, row.session_date) == ("Pipeline ORB strategy", "orb", date(2026, 1, 2))
        assert row.detected_at - row.bar_ts == BAR_INTERVAL
        assert row.hour_bucket == row.bar_ts.astimezone(NEW_YORK).hour
        assert row.setup_payload["opening_range_high"] == "101"  # the first three bars


@pytest.mark.anyio
async def test_no_setup_no_row(pipeline_log):
    calls: list[FormedSetup] = []

    async def setup_recorder(row: FormedSetup) -> bool:
        calls.append(row)
        return True

    async def decisions_recorder(**_kwargs: object) -> None:
        return None

    pipeline = ShadowPipeline(
        proposal_provider=_OrbReviewer("PROPOSE"),
        decision_recorder=decisions_recorder,
        setup_recorder=setup_recorder,
    )
    common = dict(
        run_id="run-quiet",
        strategy=_orb_strategy(),
        portfolio=PortfolioSnapshot(observed_at=NOW, net_liquidation="100000"),
        policy=RiskPolicy(),
        session_date=date(2026, 1, 2),
    )
    pending = await pipeline.run(candidates=[_orb_input("MSFT", 102, breakout=False)], now=NOW, **common)
    rejected = await pipeline.run(
        candidates=[_orb_input("MSFT", 102, breakout=False)], now=datetime(2026, 1, 2, 17, 0, tzinfo=timezone.utc), **common
    )

    assert pending == () and rejected[0].outcome == DecisionOutcome.RULE_REJECTED
    assert calls == [] and pipeline_log.records == []


# --- the row and the writer ---------------------------------------------------------------


def test_the_signal_bar_is_the_provenance_bar_or_the_one_that_closed_at_detection():
    item = _orb_input()
    setup = _formed_orb_setup(item)

    assert setup.causal_provenance is not None
    by_provenance = signal_bar_start(setup, item.bars)
    assert by_provenance == setup.detected_at - BAR_INTERVAL
    stripped = RuleSetup.model_validate({**setup.model_dump(), "causal_provenance": None})
    assert signal_bar_start(stripped, item.bars) == by_provenance
    assert signal_bar_start(setup, ()) == by_provenance


def _formed_orb_setup(item: CandidateInput) -> RuleSetup:
    from trading.rules.strategy import ORBStrategy

    result = ORBStrategy().evaluate(
        candidate=item.candidate,
        bars=item.bars,
        quote=item.quote,
        session_date=date(2026, 1, 2),
        run_id="run-golden-orb",
        strategy_id="strategy-pipeline",
        strategy_version=1,
        reference_price=item.reference_price,
        average_dollar_volume=item.average_dollar_volume,
        candidate_input_fingerprint=item.fingerprint(),
        now=NOW,
    )
    assert result.setup is not None
    return result.setup


@pytest.mark.anyio
async def test_the_writer_saves_once_and_gives_up_quickly_on_a_locked_database(tmp_path, monkeypatch):
    from config import cfg
    from db.core import init_db

    database = tmp_path / "writer.db"
    monkeypatch.setattr(cfg, "DB_PATH", str(database))
    await init_db()
    item = _orb_input()
    setup = _formed_orb_setup(item)
    strategy = _orb_strategy()
    with sqlite3.connect(database) as db:
        stamp = NOW.isoformat()
        db.execute("INSERT INTO autotrader_strategy_identities VALUES ('strategy-pipeline', 'owner-a', ?, NULL)", (stamp,))
        db.execute(
            """INSERT INTO autotrader_strategy_versions
               (strategy_id, version, content_hash, spec_json, created_at, created_by)
               VALUES ('strategy-pipeline', 1, ?, ?, ?, 'owner-a')""",
            (strategy.content_hash, json.dumps(strategy.spec.model_dump(mode="json")), stamp),
        )
        db.execute(
            """INSERT INTO autotrader_runs
               (id, owner_id, source_sha, strategy_id, strategy_version, session_date, mode, status,
                policy_fingerprints_json)
               VALUES ('run-golden-orb', 'owner-a', 'abcdef1', 'strategy-pipeline', 1, '2026-01-02',
                       'shadow', 'running', '{}')"""
        )
        db.execute(
            """INSERT INTO autotrader_scanner_runs
               (id, run_id, parameters_json, parameters_fingerprint, status, started_at)
               VALUES ('scanner-1', 'run-golden-orb', '{}', ?, 'completed', ?)""",
            ("c" * 64, stamp),
        )
        db.execute(
            """INSERT INTO autotrader_candidates
               (id, scanner_run_id, con_id, symbol, rank, contract_json, admission_status)
               VALUES ('candidate-aapl', 'scanner-1', 101, 'AAPL', 1, '{}', 'admitted')"""
        )
    row = formed_setup(setup=setup, strategy=strategy, candidate=item.candidate, bars=item.bars, session_date=date(2026, 1, 2))

    assert await record_formed_setup(row) is True
    assert await record_formed_setup(row) is False  # once per run and fingerprint
    [stored] = _setup_rows(database)
    assert stored["bar_ts"] == row.bar_ts.isoformat() and stored["hour_bucket"] == row.hour_bucket
    assert json.loads(stored["levels_json"]) == setup.model_dump(mode="json")["levels"]

    monkeypatch.setattr(manager_setups, "SETUP_WRITE_BUSY_TIMEOUT_MS", 50)
    other = formed_setup(
        setup=RuleSetup.model_validate({**setup.model_dump(), "detected_at": setup.detected_at + timedelta(seconds=1)}),
        strategy=strategy, candidate=item.candidate, bars=item.bars, session_date=date(2026, 1, 2),
    )
    holder = sqlite3.connect(database, isolation_level=None)
    try:
        holder.execute("BEGIN IMMEDIATE")
        started = datetime.now(timezone.utc)
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            await record_formed_setup(other)
        assert datetime.now(timezone.utc) - started < timedelta(seconds=5)
    finally:
        holder.execute("ROLLBACK")
        holder.close()

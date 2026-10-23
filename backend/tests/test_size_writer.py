"""The nightly size writer (plan P3-E): the promoter's verdicts into the size store.

Owner decision D2 (2026-10-18): a step up is only ever a proposal that waits
for the owner's phone approval; a step down applies by itself at the next
session; below the bottom rung the identity is fenced through the Sentinel's
one fence path. Every test writes a real migrated store (migration 18, its
triggers included) with the real writer; the production-path test replays the
golden 2026-10-12 MOMO_HOD_V2 day through ``run_shadow_session`` after a step
down written the Friday night before.
"""
from __future__ import annotations

import ast
import asyncio
from collections.abc import Iterator
from contextlib import closing, contextmanager
import inspect
import logging
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import itertools
import json
import math
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo

import pytest

from tests.schema_support import migrate_database
from tests.test_nightly_evidence import night  # noqa: F401 - the fixture: the hermetic night
from tests.test_promotion_proposals import STRONG, _evidence, _history, _trade
from tests.test_size_store import SizeLedger, criticals, sizing_log  # noqa: F401 - the fixture
from trading.operations import sentinel
from trading.operations.db_admin import trading_day
from trading.research import size_writer
from trading.research.promotion_proposals import (
    DEMOTION_PROPOSED,
    PROPOSED,
    RULE_VERSION,
    IdentityEvidence,
    PromotionProposals,
    ShadowOutcome,
    evaluate_identity,
)
from trading.research.size_writer import (
    DEMOTION_MIN_NEW_TRADES,
    DEMOTION_WAITING,
    OWNER_RESUME_HOLDS,
    WRITE_MARGIN,
    NightPlan,
    plan_night,
    sizes_in_force,
    write_night,
)
from trading.risk import identity_sizing
from trading.risk.identity_sizing import (
    IDENTITY_SIZE_ABOVE_CEILING,
    IDENTITY_SIZE_STORE_UNREADABLE,
    SIZE_EVENT_CHAIN_BROKEN,
    SizeNotInForce,
    session_identity_multiplier,
    size_in_force,
)
from trading.scheduler.sessions import market_session
from trading.sendable_identities import SENDABLE_IDENTITIES
from trading.storage import size_schema
from trading.storage.runtime_fence import SQLitePaperKillStore

ET = ZoneInfo("America/New_York")
UTC = timezone.utc
BACKEND = Path(__file__).resolve().parents[1]
FRIDAY_BEFORE_GOLDEN = date(2026, 10, 9)
GOLDEN_MONDAY = date(2026, 10, 12)
MONDAY = date(2026, 10, 19)
TUESDAY = date(2026, 10, 20)
WEDNESDAY = date(2026, 10, 21)
THURSDAY = date(2026, 10, 22)
FRIDAY = date(2026, 10, 23)
NEXT_MONDAY = date(2026, 10, 26)
THIN = [["1"]] * 3
LOSING = [["-1", "0.2"]] * 12


def et(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=ET).astimezone(UTC)


def start(day: date) -> datetime:
    session = market_session(day)
    assert session is not None
    return trading_day(session).starts_at


def dated(nets_by_day: dict[date, list[str]], *, identity: str, current: str) -> IdentityEvidence:
    """Evidence whose real trades are on the given sessions (the size writer counts
    the new ones since a size event), in the shape of the promoter tests' own."""
    days = sorted(nets_by_day)
    trades = tuple(
        _trade(day, f"{identity}-{day.isoformat()}-{number}", net, identity=identity)
        for day in days
        for number, net in enumerate(nets_by_day[day])
    )
    return IdentityEvidence(
        identity=identity, current=Decimal(current), evidence_identity="e" * 64, window=tuple(days),
        trades=trades,
        shadow={trade.decision_id: ShadowOutcome(True, trade.gross_r + Decimal("0.03")) for trade in trades},
        incidents=(), first_real_order=days[0], probation_sessions=len(days),
        size_history=_history(identity, days, current, 12),
    )


def verdicts(database: Path, plan: NightPlan, **kinds) -> PromotionProposals:
    """The promoter's real rule on synthetic evidence, at each identity's size in
    force; an identity without a readable size gets no verdict (as in the nightly
    job). A kind is a name, or a callable of the current size giving the evidence."""
    sizes, _unreadable = sizes_in_force(database, plan.session)
    items = []
    for name in SENDABLE_IDENTITIES:
        if name not in sizes:
            continue
        kind = kinds.get(name, "thin")
        current = str(sizes[name])
        if callable(kind):
            evidence = kind(current)
        elif kind == "strong":
            evidence = _evidence(STRONG, identity=name, current=current)
        elif kind == "losing":
            evidence = _evidence(LOSING, sessions=12, identity=name, current=current)
        elif kind == "losing_again":  # one more losing session: two more real trades
            evidence = _evidence(LOSING + [["-1", "0.2"]], sessions=13, identity=name, current=current)
        else:
            evidence = _evidence(THIN, sessions=3, identity=name, current=current)
        items.append(evaluate_identity(evidence, open_risk_term_deployed=True))
    return PromotionProposals(as_of=plan.night, identities=tuple(items))


@contextmanager
def writer_clock_at(moment: datetime) -> Iterator[None]:
    """Tests only: the writer's one clock (``_utc_now``, the real UTC time in
    production) starts at ``moment``, so a night of the past can be written.

    It ticks one microsecond per reading after the first, as a real clock
    never gives two transactions the same instant: proposals have no rowid
    since P3-C fix 1 (WITHOUT ROWID), so the store orders them by
    ``created_at`` (integration fix 2)."""
    real = size_writer._utc_now
    readings = itertools.count()
    size_writer._utc_now = lambda: moment + timedelta(microseconds=next(readings))
    try:
        yield
    finally:
        size_writer._utc_now = real


def run_night(database: Path, now: datetime, *, fence_store=None, **kinds: str):
    """The night the replay child writes at ``now`` (its plan and its clock)."""
    plan = plan_night(now)
    proposals = verdicts(database, plan, **kinds)
    with writer_clock_at(now):
        return write_night(database=database, proposals=proposals, plan=plan, fence_store=fence_store)


def rows(database: Path, sql: str, params: tuple[object, ...] = ()) -> list[tuple]:
    with closing(sqlite3.connect(database)) as db:
        return db.execute(sql, params).fetchall()


def counts(database: Path) -> dict[str, int]:
    return {table: rows(database, f"SELECT count(*) FROM {table}")[0][0] for table in size_schema.SIZE_STORE_TABLES}


def proposals_of(database: Path, identity: str) -> list[dict[str, object]]:
    found = rows(
        database,
        """SELECT p.proposal_id, p.as_of, p.kind, p.from_mult, p.to_mult, p.deadline_at, p.nights_since_last_step,
                  p.evidence_fingerprint, p.evidence_json, d.decision, d.actor
           FROM autotrader_size_proposals p LEFT JOIN autotrader_size_decisions d USING (proposal_id)
           WHERE p.identity = ? ORDER BY p.created_at, p.proposal_id""",
        (identity,),
    )
    keys = ("id", "as_of", "kind", "from", "to", "deadline", "nights", "fingerprint", "evidence", "decision", "actor")
    return [dict(zip(keys, row)) for row in found]


def events_of(database: Path, identity: str) -> list[tuple]:
    return rows(
        database,
        "SELECT from_mult, to_mult, effective_session, kind, account_mode, proposal_id FROM autotrader_size_events"
        " WHERE identity = ? ORDER BY id",
        (identity,),
    )


class _Records(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@pytest.fixture
def writer_log() -> Iterator[_Records]:
    """The size writer's own logger (``autotrader.shadow.sizes``)."""
    logger = logging.getLogger("autotrader.shadow.sizes")
    handler = _Records()
    level = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    try:
        yield handler
    finally:
        logger.removeHandler(handler)
        logger.setLevel(level)


def writer_criticals(log: _Records) -> list[str]:
    return [record.getMessage().split(":", 1)[0] for record in log.records if record.levelno == logging.CRITICAL]


@pytest.fixture
def database(tmp_path, monkeypatch) -> Path:
    """A migrated store that is also the fence store's database (cfg.DB_PATH)."""
    from config import cfg

    path = migrate_database(tmp_path / "sizes.db")
    monkeypatch.setattr(cfg, "DB_PATH", str(path))
    return path


# --- the night and the session it is for ------------------------------------------


def test_the_night_is_the_newest_closed_session_and_the_session_the_next_not_started():
    # The supervised replay child, 17:40 ET on a session day: tonight, for tomorrow.
    plan = plan_night(et(MONDAY, 17, 40))
    assert (plan.night, plan.session, plan.starts_at) == (MONDAY, TUESDAY, start(TUESDAY))
    assert plan.starts_at == datetime.combine(TUESDAY, time(7, 55), tzinfo=ET)
    # Friday night: the evidence is Friday's, the session Monday's.
    assert plan_night(et(FRIDAY, 17, 40)) == NightPlan(FRIDAY, NEXT_MONDAY, start(NEXT_MONDAY))
    # Before the Thanksgiving holiday: Wednesday's night is for Friday's half day.
    assert plan_night(et(date(2026, 11, 25), 18, 0)).session == date(2026, 11, 27)
    # A manual run during a session: the newest closed session is yesterday, and today's
    # session has started, so nothing written now can apply before tomorrow.
    assert plan_night(et(TUESDAY, 10, 0)) == NightPlan(MONDAY, WEDNESDAY, start(WEDNESDAY))
    # Early morning: today's session has not started (and is more than the margin away).
    assert plan_night(et(TUESDAY, 5, 0)) == NightPlan(MONDAY, TUESDAY, start(TUESDAY))
    # Inside the margin before a start: never a row that could land after it.
    assert WRITE_MARGIN == timedelta(minutes=30)
    assert plan_night(start(TUESDAY) - WRITE_MARGIN).session == WEDNESDAY
    assert plan_night(start(TUESDAY) - WRITE_MARGIN - timedelta(seconds=1)).session == TUESDAY
    with pytest.raises(ValueError):
        plan_night(datetime(2026, 10, 19, 17, 40))


# --- size_in_force: the reader of the writer's "current" ---------------------------


def test_size_in_force_is_what_the_session_start_reads(database, sizing_log):
    ledger = SizeLedger(database)
    assert {name: size_in_force(database, name, TUESDAY) for name in SENDABLE_IDENTITIES} == dict(
        identity_sizing.IDENTITY_SIZE_MULTIPLIER
    )
    ledger.step("VWAP_PB_V2", "0.5", "0.25", effective=TUESDAY, at=et(MONDAY, 20))
    # Written during Tuesday's session for Tuesday (a writer defect the store allows): not
    # Tuesday's size, as its start never saw it; Wednesday's, as written before its start.
    ledger.step("ORB_V2", "0.25", "0.5", effective=TUESDAY, at=et(TUESDAY, 9))
    # Written for Thursday during Wednesday's session (no clock guard: any time, read-only).
    ledger.step("ORB_V2", "0.5", "0.25", effective=THURSDAY, at=et(WEDNESDAY, 11))
    assert [size_in_force(database, "ORB_V2", day) for day in (MONDAY, TUESDAY, WEDNESDAY, THURSDAY)] == [
        Decimal("0.25"), Decimal("0.25"), Decimal("0.5"), Decimal("0.25"),
    ]
    vwap = [size_in_force(database, "VWAP_PB_V2", day) for day in (MONDAY, TUESDAY)]
    assert vwap == [Decimal("0.5"), Decimal("0.25")]
    assert criticals(sizing_log) == []  # the reader logs nothing
    for name in ("ORB_V2", "VWAP_PB_V2"):
        for day in (MONDAY, TUESDAY, WEDNESDAY, THURSDAY):
            after_start = start(day) + timedelta(hours=3)
            assert size_in_force(database, name, day) == session_identity_multiplier(
                name, sendable=True, session_date=day, database=database, now=after_start
            ), (name, day)
    # Only the session-start read logs the event it ignored (Tuesday's ORB read).
    assert criticals(sizing_log) == [identity_sizing.SIZE_EVENT_WRITTEN_MID_SESSION]


def test_size_in_force_fails_closed_with_the_session_starts_tokens(tmp_path, database, monkeypatch):
    def token(path: Path, name: str = "ORB_V2", day: date = TUESDAY) -> str:
        with pytest.raises(SizeNotInForce) as caught:
            size_in_force(path, name, day)
        return caught.value.token

    assert token(tmp_path / "missing.db") == IDENTITY_SIZE_STORE_UNREADABLE
    assert token(database, day=date(2026, 10, 24)) == IDENTITY_SIZE_STORE_UNREADABLE  # a Saturday
    assert token(database, name="ORB_V1") == identity_sizing.SENDABLE_WITHOUT_CEILING
    pending = tmp_path / "v17.db"
    from tests.schema_support import database_at

    database_at(pending, 17)
    assert token(pending) == IDENTITY_SIZE_STORE_UNREADABLE  # never the baseline
    # A chain that does not start at the reviewed baseline.
    SizeLedger(database).step("VWAP_PB_V2", "0.5", "0.25", effective=TUESDAY, at=et(MONDAY, 20))
    rebased = {**identity_sizing.IDENTITY_SIZE_MULTIPLIER, "VWAP_PB_V2": Decimal("1")}
    monkeypatch.setattr(identity_sizing, "IDENTITY_SIZE_MULTIPLIER", rebased)
    assert token(database, name="VWAP_PB_V2") == SIZE_EVENT_CHAIN_BROKEN
    monkeypatch.setattr(
        identity_sizing, "IDENTITY_SIZE_CEILING",
        {"paper": {**identity_sizing.IDENTITY_SIZE_CEILING["paper"], "MOMO_HOD_V2": Decimal("0.5")}},
    )
    assert token(database, name="MOMO_HOD_V2") == IDENTITY_SIZE_ABOVE_CEILING


# --- the plan's tests ---------------------------------------------------------------


def test_writer_idempotent_per_night(database):
    first = run_night(database, et(MONDAY, 17, 40), ORB_V2="strong", VWAP_PB_V2="losing")
    assert first.status == "written"
    assert sorted(first.evaluated) == sorted(SENDABLE_IDENTITIES) and first.already == ()
    assert [(change.identity, change.kind) for change in first.changes] == [
        ("ORB_V2", "proposal"), ("VWAP_PB_V2", "step_down"),
    ]
    written = counts(database)
    assert written == {
        "autotrader_size_proposals": 2, "autotrader_size_decisions": 1,
        "autotrader_size_events": 1, "autotrader_size_evaluations": 4,
    }
    snapshot = {table: rows(database, f"SELECT * FROM {table}") for table in size_schema.SIZE_STORE_TABLES}

    # A restarted replay the same night (18:10 ET), even with other verdicts: nothing new.
    again = run_night(database, et(MONDAY, 18, 10), ORB_V2="strong", GAP_GO_V2="strong", MOMO_HOD_V2="losing")
    assert sorted(again.already) == sorted(SENDABLE_IDENTITIES)
    assert (again.evaluated, again.changes, again.failed) == ((), (), {})
    assert {table: rows(database, f"SELECT * FROM {table}") for table in size_schema.SIZE_STORE_TABLES} == snapshot
    # The next morning's manual run reads the same newest closed night: still nothing.
    assert run_night(database, et(TUESDAY, 6, 0), ORB_V2="strong").already == tuple(SENDABLE_IDENTITIES)
    assert counts(database) == written
    # One evaluation per identity and night, by the database too.
    assert rows(database, "SELECT identity, as_of, status FROM autotrader_size_evaluations ORDER BY id") == [
        ("MOMO_HOD_V2", "2026-10-19", "at_ceiling"),
        ("ORB_V2", "2026-10-19", "proposed"),
        ("VWAP_PB_V2", "2026-10-19", "demotion_proposed"),
        ("GAP_GO_V2", "2026-10-19", "not_eligible"),
    ]


def test_step_up_stored_deadline_next_session_start(database):
    result = run_night(database, et(MONDAY, 17, 40), ORB_V2="strong")
    (proposal,) = proposals_of(database, "ORB_V2")
    assert (proposal["kind"], proposal["from"], proposal["to"], proposal["as_of"]) == (
        "step_up", "0.25", "0.5", "2026-10-19",
    )
    # The deadline is the next session's start (07:55 ET), the trading day's own.
    deadline = datetime.fromisoformat(str(proposal["deadline"]))
    assert deadline == start(TUESDAY) == datetime.combine(TUESDAY, time(7, 55), tzinfo=ET)
    assert str(proposal["deadline"]).endswith("+00:00")
    # The id: sha256(rule|identity|from|to|evidence_fingerprint|as_of)[:10].
    key = f"{RULE_VERSION}|ORB_V2|0.25|0.5|{proposal['fingerprint']}|2026-10-19"
    assert proposal["id"] == sha256(key.encode()).hexdigest()[:10]
    assert [change.proposal_id for change in result.changes] == [proposal["id"]]
    evidence = json.loads(str(proposal["evidence"]))
    assert evidence["evidence"]["trades"] == 30 and evidence["status"] == PROPOSED
    assert evidence["text"].startswith("30 trades, expectancy +0.50R (lower bound")
    facts = {key: evidence[key] for key in ("rule", "identity", "status", "current", "proposed", "evidence", "window")}
    assert proposal["fingerprint"] == sha256(
        json.dumps(facts, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    # Nothing decided, nothing applied: it waits for the owner.
    assert proposal["decision"] is None and events_of(database, "ORB_V2") == []
    assert size_in_force(database, "ORB_V2", TUESDAY) == Decimal("0.25")
    # Friday's proposal waits over the weekend: Monday's start.
    run_night(database, et(FRIDAY, 17, 40), GAP_GO_V2="strong")
    (gap,) = proposals_of(database, "GAP_GO_V2")
    assert datetime.fromisoformat(str(gap["deadline"])) == start(NEXT_MONDAY)
    assert (gap["from"], gap["to"]) == ("0.5", "1")


def test_demotion_event_effective_next_session(database, sizing_log):
    result = run_night(database, et(MONDAY, 17, 40), VWAP_PB_V2="losing")
    (proposal,) = proposals_of(database, "VWAP_PB_V2")
    assert (proposal["kind"], proposal["from"], proposal["to"], proposal["deadline"]) == (
        "step_down", "0.5", "0.25", None,
    )
    assert (proposal["decision"], proposal["actor"]) == ("auto_applied", "size_writer")
    assert events_of(database, "VWAP_PB_V2") == [("0.5", "0.25", "2026-10-20", "step_down", "paper", proposal["id"])]
    assert [(change.kind, change.to_mult) for change in result.changes] == [("step_down", "0.25")]
    # Today's session keeps its size; the next session starts at the new one.
    assert size_in_force(database, "VWAP_PB_V2", MONDAY) == Decimal("0.5")
    assert session_identity_multiplier(
        "VWAP_PB_V2", sendable=True, session_date=MONDAY, database=database, now=et(MONDAY, 17, 45)
    ) == Decimal("0.5")
    assert session_identity_multiplier(
        "VWAP_PB_V2", sendable=True, session_date=TUESDAY, database=database, now=et(TUESDAY, 9, 30)
    ) == Decimal("0.25")
    assert criticals(sizing_log) == []  # written before the start: never "mid-session"
    # Friday's step down takes effect on Monday, not on the weekend.
    run_night(database, et(FRIDAY, 17, 40), MOMO_HOD_V2="losing")
    assert [event[:3] for event in events_of(database, "MOMO_HOD_V2")] == [("1", "0.5", "2026-10-26")]


def test_a_refused_step_down_leaves_no_row_of_its_night(database):
    """Proposal, decision and event are one transaction: when the store refuses the
    event (an approval already moved the next session), nothing of the night stays."""
    ledger = SizeLedger(database)
    ledger.step("VWAP_PB_V2", "0.5", "1", effective=TUESDAY, at=et(MONDAY, 12))  # an owner-approved step up
    before = counts(database)

    result = run_night(database, et(MONDAY, 17, 40), VWAP_PB_V2="losing", ORB_V2="strong")

    assert result.failed == {"VWAP_PB_V2": size_schema.SIZE_EVENT_NOT_AFTER_PREVIOUS}
    assert [row["kind"] for row in proposals_of(database, "VWAP_PB_V2")] == ["step_up"]  # the ledger's own only
    assert rows(database, "SELECT count(*) FROM autotrader_size_evaluations WHERE identity = 'VWAP_PB_V2'") == [(0,)]
    assert size_in_force(database, "VWAP_PB_V2", TUESDAY) == Decimal("1")
    # The other identities' nights are written as usual.
    assert sorted(result.evaluated) == ["GAP_GO_V2", "MOMO_HOD_V2", "ORB_V2"]
    assert counts(database)["autotrader_size_proposals"] == before["autotrader_size_proposals"] + 1
    # The next night is not refused: the step down goes from the approved size.
    later = run_night(database, et(TUESDAY, 17, 40), VWAP_PB_V2="losing")
    assert later.failed == {} and events_of(database, "VWAP_PB_V2")[-1][:3] == ("1", "0.5", "2026-10-21")


class RecordingStore:
    """The real fence store, with every call the Sentinel's one path makes recorded."""

    def __init__(self) -> None:
        self.real = SQLitePaperKillStore()
        self.calls: list[tuple[str, str]] = []

    async def stop_identity(self, *, reason: str, identity: str, unless=None):
        self.calls.append((identity, reason))
        return await self.real.stop_identity(reason=reason, identity=identity, unless=unless)


def fence_row(database: Path, identity: str) -> tuple[int, str] | None:
    found = rows(
        database, "SELECT stopped, reason FROM autotrader_execution_fences WHERE name = ?", (f"paper:{identity}",)
    )
    return found[0] if found else None


def test_demotion_below_bottom_fences_via_fence_identity(database, monkeypatch):
    seen: list[tuple[str, str]] = []
    real = sentinel.fence_identity

    async def spy(store, *, identity, reason, resumed_on=None):
        seen.append((identity, reason))
        return await real(store, identity=identity, reason=reason, resumed_on=resumed_on)

    monkeypatch.setattr(sentinel, "fence_identity", spy)
    monkeypatch.setattr(sentinel, "SENTINEL_FENCES_LIVE", True)
    store = RecordingStore()

    result = run_night(database, et(MONDAY, 17, 40), fence_store=store, ORB_V2="losing")

    # Through sentinel.fence_identity, the one fence path, into the real store.
    assert [identity for identity, _reason in seen] == ["ORB_V2"] and len(store.calls) == 1
    reason = seen[0][1]
    assert reason.startswith("sentinel: demotion to shadow-only (night 2026-10-19: 24 trades, expectancy upper bound -")
    assert fence_row(database, "ORB_V2") == (1, reason)
    (proposal,) = proposals_of(database, "ORB_V2")
    assert (proposal["kind"], proposal["from"], proposal["to"], proposal["decision"]) == (
        "step_down", "0.25", "0", "fenced",
    )
    assert events_of(database, "ORB_V2") == []  # shadow-only is a fence, never a size
    assert size_in_force(database, "ORB_V2", TUESDAY) == Decimal("0.25")
    assert [(change.kind, change.to_mult) for change in result.changes] == [("fenced", "0")]

    # The owner resumes it; the next night has no new real trade: the resume holds.
    asyncio.run(SQLitePaperKillStore().resume(reason="owner: checked", identity="ORB_V2"))
    held = run_night(database, et(TUESDAY, 17, 40), fence_store=store, ORB_V2="losing")
    assert held.held == {"ORB_V2": OWNER_RESUME_HOLDS} and held.changes == ()
    assert len(seen) == 1 and fence_row(database, "ORB_V2")[0] == 0
    assert "ORB_V2" in held.evaluated and len(proposals_of(database, "ORB_V2")) == 1
    # It trades again and loses again: fenced again, through the same path.
    again = run_night(database, et(WEDNESDAY, 17, 40), fence_store=store, ORB_V2="losing_again")
    assert [change.kind for change in again.changes] == ["fenced"] and len(seen) == 2
    assert fence_row(database, "ORB_V2")[0] == 1


def test_a_demotion_fence_obeys_the_sentinels_switch_and_keeps_an_owners_halt(tmp_path, monkeypatch):
    from config import cfg

    # Log-only: fence_identity writes nothing; the decision says would_fence.
    quiet = migrate_database(tmp_path / "log-only.db")
    monkeypatch.setattr(cfg, "DB_PATH", str(quiet))
    monkeypatch.setattr(sentinel, "SENTINEL_FENCES_LIVE", False)
    result = run_night(
        quiet, et(MONDAY, 17, 40), fence_store=SQLitePaperKillStore(), GAP_GO_V2="losing", ORB_V2="losing"
    )
    assert fence_row(quiet, "ORB_V2") is None and fence_row(quiet, "GAP_GO_V2") is None
    assert [(change.identity, change.kind) for change in result.changes] == [
        ("ORB_V2", "would_fence"), ("GAP_GO_V2", "step_down"),
    ]
    assert proposals_of(quiet, "ORB_V2")[0]["decision"] == "would_fence"

    # Live, with the owner's /halt already standing: the owner's row and reason are kept.
    halted = migrate_database(tmp_path / "halted.db")
    monkeypatch.setattr(cfg, "DB_PATH", str(halted))
    monkeypatch.setattr(sentinel, "SENTINEL_FENCES_LIVE", True)
    asyncio.run(SQLitePaperKillStore().stop(reason="telegram /halt by owner", identity="ORB_V2"))
    run_night(halted, et(MONDAY, 17, 40), fence_store=SQLitePaperKillStore(), ORB_V2="losing")
    assert fence_row(halted, "ORB_V2") == (1, "telegram /halt by owner")
    assert proposals_of(halted, "ORB_V2")[0]["decision"] == "fenced"

    # No fence store for this database: nothing of that identity is written (retried next night).
    lost = migrate_database(tmp_path / "no-store.db")
    result = run_night(lost, et(MONDAY, 17, 40), fence_store=None, ORB_V2="losing")
    assert result.failed == {"ORB_V2": "no_fence_store"} and proposals_of(lost, "ORB_V2") == []
    assert "ORB_V2" not in result.evaluated


def test_writer_never_writes_step_up(database):
    """Night after night of eligible evidence, every step up stays a proposal: no
    approval, no event, no size above the baseline (the owner's phone, P3-D, only)."""
    for moment in (et(MONDAY, 17, 40), et(TUESDAY, 17, 40), et(WEDNESDAY, 17, 40)):
        run_night(database, moment, ORB_V2="strong", VWAP_PB_V2="strong", GAP_GO_V2="strong")
    assert rows(database, "SELECT count(*) FROM autotrader_size_events") == [(0,)]
    assert rows(database, "SELECT count(*) FROM autotrader_size_decisions WHERE decision = 'approved'") == [(0,)]
    assert {row[0] for row in rows(database, "SELECT kind FROM autotrader_size_proposals")} == {"step_up"}
    assert len(rows(database, "SELECT 1 FROM autotrader_size_proposals")) == 9
    for name in SENDABLE_IDENTITIES:
        for day in (TUESDAY, WEDNESDAY, THURSDAY):
            assert size_in_force(database, name, day) == identity_sizing.IDENTITY_SIZE_MULTIPLIER[name]
    # Mixed with a step down the same night: still only the step down is an event.
    run_night(database, et(THURSDAY, 17, 40), ORB_V2="strong", VWAP_PB_V2="losing")
    assert [row[0] for row in rows(database, "SELECT kind FROM autotrader_size_events")] == ["step_down"]


def test_the_writers_only_event_statement_is_a_paper_step_down():
    """Structure (AST): the writer's only size event statement inserts a PAPER step down."""
    source = Path(size_writer.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    strings = [node.value for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str)]
    joined = [ast.unparse(node) for node in ast.walk(tree) if isinstance(node, ast.JoinedStr)]
    inserts = [text for text in strings + joined if "INTO autotrader_size_events" in text.replace("\n", " ")]
    assert len(inserts) == 1, inserts
    assert size_writer._STEP_DOWN_EVENT == (
        f"INSERT INTO {size_schema.SIZE_EVENTS_TABLE}"
        " (identity, from_mult, to_mult, effective_session, kind, proposal_id, created_at, account_mode)"
        f" VALUES (?, ?, ?, ?, '{size_schema.STEP_DOWN}', ?, ?, '{size_schema.PAPER_ACCOUNT_MODE}')"
    )
    # Nothing in the writer can approve: no owner actor, no approved decision.
    assert size_schema.OWNER_TELEGRAM_ACTOR not in source and "'approved'" not in source and '"approved"' not in source
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    names |= {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    names |= {alias.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) for alias in node.names}
    assert "OWNER_TELEGRAM_ACTOR" not in names
    # The one function that executes it takes no kind.
    (function,) = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and "_STEP_DOWN_EVENT" in ast.unparse(node)
    ]
    assert function.name == "_insert_step_down"
    assert "kind" not in {argument.arg for argument in function.args.args + function.args.kwonlyargs}


def test_nights_evaluated_since_last_event(database):
    for moment in (et(MONDAY, 17, 40), et(TUESDAY, 17, 40), et(WEDNESDAY, 17, 40)):
        run_night(database, moment)
    # Thursday: VWAP steps down (its fourth night since no step at all); ORB is proposed.
    run_night(database, et(THURSDAY, 17, 40), VWAP_PB_V2="losing", ORB_V2="strong")
    assert proposals_of(database, "VWAP_PB_V2")[-1]["nights"] == 4
    assert proposals_of(database, "ORB_V2")[-1]["nights"] == 4
    # Friday: VWAP (now x0.25) is eligible: one night since its step, tonight.
    run_night(database, et(FRIDAY, 17, 40), VWAP_PB_V2="strong")
    friday = proposals_of(database, "VWAP_PB_V2")[-1]
    assert (friday["kind"], friday["from"], friday["to"], friday["nights"]) == ("step_up", "0.25", "0.5", 1)
    # Monday: two nights since the step; ORB, never stepped, six.
    run_night(database, et(NEXT_MONDAY, 17, 40), VWAP_PB_V2="strong", ORB_V2="strong")
    assert proposals_of(database, "VWAP_PB_V2")[-1]["nights"] == 2
    assert proposals_of(database, "ORB_V2")[-1]["nights"] == 6


def test_older_open_proposals_are_superseded_or_expired(database):
    # A late manual run on Tuesday morning (07:40 ET, inside the margin before 07:55):
    # Monday's night, for Wednesday. Its proposal is still open that evening.
    early = run_night(database, et(TUESDAY, 7, 40), ORB_V2="strong", GAP_GO_V2="strong")
    assert early.session == WEDNESDAY
    first_orb, first_gap = proposals_of(database, "ORB_V2")[0]["id"], proposals_of(database, "GAP_GO_V2")[0]["id"]

    # Tuesday night: newer evidence supersedes ORB's open proposal (and GAP's: not eligible now).
    evening = run_night(database, et(TUESDAY, 17, 40), ORB_V2="strong")
    assert sorted(evening.superseded) == sorted([first_orb, first_gap]) and evening.expired == ()
    assert [(row["decision"], row["actor"]) for row in proposals_of(database, "ORB_V2")] == [
        ("superseded", "size_writer"), (None, None),
    ]
    second_orb = proposals_of(database, "ORB_V2")[1]["id"]

    # Wednesday night, its deadline (Wednesday 07:55) passed: expired, also when the
    # identity is not evaluated tonight at all.
    plan = plan_night(et(WEDNESDAY, 17, 40))
    others = verdicts(database, plan)
    proposals = PromotionProposals(
        as_of=plan.night, identities=tuple(item for item in others.identities if item.identity != "ORB_V2")
    )
    with writer_clock_at(et(WEDNESDAY, 17, 40)):
        result = write_night(database=database, proposals=proposals, plan=plan, fence_store=None)
    assert result.expired == (second_orb,)
    assert proposals_of(database, "ORB_V2")[1]["decision"] == "expired"
    # Every proposal is decided once: the store's view has nothing pending left.
    assert rows(database, "SELECT count(*) FROM autotrader_size_proposal_state WHERE state = 'pending'") == [(0,)]


def test_an_unreadable_night_writes_nothing(tmp_path, database):
    plan = plan_night(et(MONDAY, 17, 40))
    unreadable = PromotionProposals(as_of=MONDAY, readable=False, note="identity_size_store_unreadable")
    with writer_clock_at(et(MONDAY, 17, 40)):
        result = write_night(database=database, proposals=unreadable, plan=plan, fence_store=None)
    assert (result.status, result.note) == ("not_written", "proposals not evaluated: identity_size_store_unreadable")
    assert set(counts(database).values()) == {0}
    # No database: never created.
    missing = tmp_path / "absent.db"
    proposals = verdicts(database, plan, ORB_V2="strong")
    with writer_clock_at(et(MONDAY, 17, 40)):
        absent = write_night(database=missing, proposals=proposals, plan=plan, fence_store=None)
    assert absent.status == "not_written"
    assert not missing.exists()
    # A verdict at a size that is not the one in force is refused, never written.
    SizeLedger(database).step("ORB_V2", "0.25", "0.5", effective=TUESDAY, at=et(MONDAY, 12))
    with writer_clock_at(et(MONDAY, 17, 40)):
        stale = write_night(database=database, proposals=proposals, plan=plan, fence_store=None)
    assert stale.failed == {"ORB_V2": "current_not_in_force"}
    assert [row["kind"] for row in proposals_of(database, "ORB_V2")] == ["step_up"]  # the ledger's approved one


# --- every timestamp is the writer's own clock (review N19a, open question 3) ---------


def test_every_row_carries_the_writers_own_utc_clock_read_in_its_transaction(database, monkeypatch):
    """The mid-session guard trusts ``created_at``: the writer takes no time from
    its caller, and stamps each transaction's rows with one reading of
    ``_utc_now``, the real UTC clock."""
    # The helper is the real clock, in UTC.
    before = datetime.now(UTC)
    reading = size_writer._utc_now()
    assert before <= reading <= datetime.now(UTC) and reading.utcoffset() == timedelta(0)
    assert "now" not in inspect.signature(write_night).parameters
    # A clock that moves one second per reading, an hour after the night was planned.
    planned = et(MONDAY, 17, 40)
    readings: list[datetime] = []

    def ticking() -> datetime:
        readings.append(et(MONDAY, 18, 40) + timedelta(seconds=len(readings)))
        return readings[-1]

    monkeypatch.setattr(sentinel, "SENTINEL_FENCES_LIVE", True)
    plan = plan_night(planned)
    proposals = verdicts(database, plan, ORB_V2="losing", VWAP_PB_V2="losing", GAP_GO_V2="strong")
    monkeypatch.setattr(size_writer, "_utc_now", ticking)
    write_night(database=database, proposals=proposals, plan=plan, fence_store=SQLitePaperKillStore())

    stamps = {
        "proposal": rows(database, "SELECT identity, created_at FROM autotrader_size_proposals"),
        "decision": rows(
            database,
            "SELECT p.identity, d.at FROM autotrader_size_decisions d"
            " JOIN autotrader_size_proposals p USING (proposal_id)",
        ),
        "event": rows(database, "SELECT identity, created_at FROM autotrader_size_events"),
        "evaluation": rows(database, "SELECT identity, created_at FROM autotrader_size_evaluations"),
    }
    issued = {moment.isoformat() for moment in readings}
    assert all(stamp in issued for table in stamps.values() for _name, stamp in table)
    assert planned.isoformat() not in {stamp for table in stamps.values() for _name, stamp in table}
    # One reading per identity's transaction: its proposal, decision, event and evaluation agree.
    by_identity: dict[str, set[str]] = {}
    for table in stamps.values():
        for name, stamp in table:
            by_identity.setdefault(name, set()).add(stamp)
    assert all(len(found) == 1 for found in by_identity.values()), by_identity
    assert len(set().union(*by_identity.values())) == 4  # four transactions, four readings
    assert {name for name, _stamp in stamps["event"]} == {"VWAP_PB_V2"}


def test_a_session_about_to_start_by_the_writers_clock_is_never_written(database):
    plan = plan_night(et(MONDAY, 17, 40))
    proposals = verdicts(database, plan, VWAP_PB_V2="losing", ORB_V2="strong")
    # Inside the margin before Tuesday's 07:55 ET start (a replay run late, or a clock
    # far ahead of the planner's): nothing at all, never a row Tuesday's start could miss.
    with writer_clock_at(start(TUESDAY) - WRITE_MARGIN):
        late = write_night(database=database, proposals=proposals, plan=plan, fence_store=None)
    assert (late.status, late.note) == ("not_written", size_writer.SESSION_STARTED)
    assert set(counts(database).values()) == {0}
    with writer_clock_at(start(TUESDAY) - WRITE_MARGIN - timedelta(seconds=1)):
        in_time = write_night(database=database, proposals=proposals, plan=plan, fence_store=None)
    assert in_time.status == "written" and len(in_time.changes) == 2
    assert events_of(database, "VWAP_PB_V2")[0][2] == "2026-10-20"


def test_the_writers_statements_are_plain_inserts_into_the_size_tables():
    """Append-only (review N19a MEDIUM-1): plain INSERTs only, written out literally."""
    assert size_writer._PROPOSAL.startswith(f"INSERT INTO {size_schema.SIZE_PROPOSALS_TABLE} (")
    assert size_writer._DECISION.startswith(f"INSERT INTO {size_schema.SIZE_DECISIONS_TABLE} (")
    assert size_writer._EVALUATION.startswith(f"INSERT INTO {size_schema.SIZE_EVALUATIONS_TABLE} (")
    assert size_writer._STEP_DOWN_EVENT.startswith(f"INSERT INTO {size_schema.SIZE_EVENTS_TABLE} (")
    source = Path(size_writer.__file__).read_text(encoding="utf-8")
    for forbidden in ("REPLACE", "OR IGNORE", "ON CONFLICT", "UPDATE ", "DELETE", "executemany", "executescript"):
        assert forbidden not in source.upper(), forbidden


# --- P3-E fix 1: no cascade on the same evidence; one identity never blocks the others -----

# Twelve bad sessions before the first night (two real trades each, net -0.8 R a day).
BAD_SESSIONS = (
    date(2026, 9, 30), date(2026, 10, 1), date(2026, 10, 2), date(2026, 10, 5), date(2026, 10, 6),
    date(2026, 10, 7), date(2026, 10, 8), date(2026, 10, 9), date(2026, 10, 12), date(2026, 10, 13),
    date(2026, 10, 14), date(2026, 10, 16),
)
BAD = {day: ["-1", "0.2"] for day in BAD_SESSIONS}


def momo(nets_by_day: dict[date, list[str]]):
    return lambda current: dated(nets_by_day, identity="MOMO_HOD_V2", current=current)


def test_no_cascade_on_the_same_evidence(database, writer_log, monkeypatch):
    """Owner/coordinator decision (P3-E fix 1, open question 1): after a size event the
    next step down waits for DEMOTION_MIN_NEW_TRADES new closed trades since it."""
    assert DEMOTION_MIN_NEW_TRADES == 5 and DEMOTION_WAITING == "demotion_waiting_new_evidence"
    # Three bad nights in a row on the same trades: exactly one step.
    nights = [run_night(database, et(day, 17, 40), MOMO_HOD_V2=momo(BAD)) for day in (MONDAY, TUESDAY, WEDNESDAY)]
    assert [event[:3] for event in events_of(database, "MOMO_HOD_V2")] == [("1", "0.5", "2026-10-20")]
    assert [change.kind for change in nights[0].changes] == ["step_down"]
    assert [(night.changes, dict(night.waiting)) for night in nights[1:]] == [
        ((), {"MOMO_HOD_V2": "0/5 new trades since the 2026-10-20 session"}),
    ] * 2
    assert len(proposals_of(database, "MOMO_HOD_V2")) == 1
    statuses = rows(
        database, "SELECT status FROM autotrader_size_evaluations WHERE identity = 'MOMO_HOD_V2' ORDER BY id"
    )
    assert [row[0] for row in statuses] == ["demotion_proposed", DEMOTION_WAITING, DEMOTION_WAITING]
    waits = [record.getMessage() for record in writer_log.records if record.getMessage().startswith(DEMOTION_WAITING)]
    assert len(waits) == 2 and all("MOMO_HOD_V2" in text for text in waits)
    # Four new losing trades since the step: still waiting.
    four = {**BAD, TUESDAY: ["-1", "-1"], WEDNESDAY: ["-1", "-1"]}
    thursday = run_night(database, et(THURSDAY, 17, 40), MOMO_HOD_V2=momo(four))
    assert thursday.changes == () and thursday.waiting == {"MOMO_HOD_V2": "4/5 new trades since the 2026-10-20 session"}
    # The fifth: the next step.
    five = {**four, THURSDAY: ["-1"]}
    friday = run_night(database, et(FRIDAY, 17, 40), MOMO_HOD_V2=momo(five))
    assert [(change.kind, change.to_mult) for change in friday.changes] == [("step_down", "0.25")]
    assert [event[:3] for event in events_of(database, "MOMO_HOD_V2")] == [
        ("1", "0.5", "2026-10-20"), ("0.5", "0.25", "2026-10-26"),
    ]
    # Below the bottom rung waits the same way: no fence on the evidence of the last step.
    monkeypatch.setattr(sentinel, "SENTINEL_FENCES_LIVE", True)
    store = RecordingStore()
    after = run_night(database, et(NEXT_MONDAY, 17, 40), fence_store=store, MOMO_HOD_V2=momo(five))
    assert store.calls == [] and fence_row(database, "MOMO_HOD_V2") is None
    assert after.changes == () and after.waiting == {"MOMO_HOD_V2": "0/5 new trades since the 2026-10-26 session"}


def test_one_identity_never_blocks_the_others(tmp_path, database, writer_log, monkeypatch):
    """P3-E fix 1 (open question 4): an identity whose size cannot be read, or whose
    night fails, is skipped with ONE CRITICAL size token and named in the night's
    summary; every other identity's evaluation, step down, proposal and fence is written."""
    # ORB_V2's history does not start at its reviewed baseline (x0.25): no size can be read.
    SizeLedger(database).step("ORB_V2", "0.5", "0.25", effective=MONDAY, at=et(date(2026, 10, 16), 20))
    result = run_night(database, et(MONDAY, 17, 40), VWAP_PB_V2="losing", GAP_GO_V2="strong")

    assert result.status == "written" and result.failed == {"ORB_V2": SIZE_EVENT_CHAIN_BROKEN}
    assert sorted(result.evaluated) == ["GAP_GO_V2", "MOMO_HOD_V2", "VWAP_PB_V2"]
    assert [(change.identity, change.kind) for change in result.changes] == [
        ("VWAP_PB_V2", "step_down"), ("GAP_GO_V2", "proposal"),
    ]
    assert writer_criticals(writer_log) == [SIZE_EVENT_CHAIN_BROKEN]
    assert "SKIPPED: ORB_V2 (size_event_chain_broken)" in result.summary()
    assert result.payload()["failed"] == {"ORB_V2": SIZE_EVENT_CHAIN_BROKEN}

    # A night that fails for one identity: the fence and the proposal of the others still land.
    from config import cfg

    other = migrate_database(tmp_path / "one-fails.db")
    monkeypatch.setattr(cfg, "DB_PATH", str(other))
    monkeypatch.setattr(sentinel, "SENTINEL_FENCES_LIVE", True)
    real = size_writer._nights_since_last_step

    def failing(db, identity, night):
        if identity == "VWAP_PB_V2":
            raise RuntimeError("disk full")
        return real(db, identity, night)

    monkeypatch.setattr(size_writer, "_nights_since_last_step", failing)
    writer_log.records.clear()
    mixed = run_night(
        other, et(MONDAY, 17, 40), fence_store=SQLitePaperKillStore(),
        ORB_V2="losing", VWAP_PB_V2="losing", GAP_GO_V2="strong",
    )
    assert mixed.failed == {"VWAP_PB_V2": "runtimeerror"}
    assert [(change.identity, change.kind) for change in mixed.changes] == [
        ("ORB_V2", "fenced"), ("GAP_GO_V2", "proposal"),
    ]
    assert fence_row(other, "ORB_V2")[0] == 1 and proposals_of(other, "VWAP_PB_V2") == []
    skipped = [token for token in writer_criticals(writer_log) if token != "size writer"]
    assert skipped == [identity_sizing.SIZE_WRITER_IDENTITY_SKIPPED]
    # The skipped identity is written the next night, once its trouble is gone.
    monkeypatch.setattr(size_writer, "_nights_since_last_step", real)
    assert "VWAP_PB_V2" in run_night(other, et(TUESDAY, 17, 40), VWAP_PB_V2="losing").evaluated


def test_the_nightly_job_judges_and_writes_every_identity_it_can_read(night, monkeypatch):
    from tests.test_nightly_evidence import TUESDAY as NIGHT_TUESDAY, _environment, _et
    from trading.research.replay import run_replay_cli

    database = night["database"]
    # ORB_V2's history does not start at its baseline: only ORB_V2 has no size.
    SizeLedger(database).step(
        "ORB_V2", "0.5", "0.25", effective=NIGHT_TUESDAY, at=_et(NIGHT_TUESDAY - timedelta(days=1), 20, 0)
    )
    monkeypatch.setattr(size_writer, "_utc_now", lambda: _et(NIGHT_TUESDAY, 17, 40))
    assert run_replay_cli([], environment=_environment(night, _et(NIGHT_TUESDAY, 17, 40))) == 0

    directory = night["root"] / "reports" / "2026-10-12"
    document = json.loads((directory / "proposals.json").read_text())
    assert document["readable"] is True
    assert [item["identity"] for item in document["identities"]] == ["MOMO_HOD_V2", "VWAP_PB_V2", "GAP_GO_V2"]
    sizes = json.loads((directory / "evidence.json").read_text())["sizes"]
    assert sizes["failed"] == {"ORB_V2": SIZE_EVENT_CHAIN_BROKEN}
    assert sorted(sizes["evaluated"]) == ["GAP_GO_V2", "MOMO_HOD_V2", "VWAP_PB_V2"]
    assert "SKIPPED: ORB_V2 (size_event_chain_broken)" in sizes["summary"]


# --- the nightly job: "current" from the store ---------------------------------------


def test_the_nightly_promoter_judges_the_size_in_force_from_the_store(night, monkeypatch):
    from tests.test_nightly_evidence import TUESDAY as NIGHT_TUESDAY, _environment, _et
    from trading.research.replay import run_replay_cli

    database = night["database"]
    # VWAP_PB_V2 stepped down to x0.25 from Tuesday (written Monday night).
    SizeLedger(database).step(
        "VWAP_PB_V2", "0.5", "0.25", effective=NIGHT_TUESDAY, at=_et(NIGHT_TUESDAY - timedelta(days=1), 20, 0)
    )

    monkeypatch.setattr(size_writer, "_utc_now", lambda: _et(NIGHT_TUESDAY, 17, 40))
    assert run_replay_cli([], environment=_environment(night, _et(NIGHT_TUESDAY, 17, 40))) == 0

    directory = night["root"] / "reports" / "2026-10-12"
    document = json.loads((directory / "proposals.json").read_text())
    current = {item["identity"]: item["current"] for item in document["identities"]}
    assert current == {"MOMO_HOD_V2": "1", "ORB_V2": "0.25", "VWAP_PB_V2": "0.25", "GAP_GO_V2": "0.5"}
    sizes = json.loads((directory / "evidence.json").read_text())["sizes"]
    assert (sizes["status"], sizes["night"], sizes["session"]) == ("written", "2026-10-13", "2026-10-14")
    assert sorted(sizes["evaluated"]) == sorted(SENDABLE_IDENTITIES) and sizes["failed"] == {}
    assert sizes["delivery"] == "not_attempted"  # no sender handed in: a test never reaches Telegram
    assert rows(database, "SELECT identity, status FROM autotrader_size_evaluations ORDER BY id") == [
        ("MOMO_HOD_V2", "at_ceiling"), ("ORB_V2", "not_eligible"), ("VWAP_PB_V2", "not_eligible"),
        ("GAP_GO_V2", "not_eligible"),
    ]


# --- the production path ---------------------------------------------------------------


async def test_a_step_down_written_tonight_sizes_the_next_session_through_the_repository_runner(
    tmp_path, monkeypatch, sizing_log
):
    """The writer runs the Friday night before the golden Monday: Friday's size is
    unchanged, and Monday's MOMO_HOD_V2 run, through ``run_shadow_session`` and the
    real pipeline and repository runner, sizes BF B at x0.5."""
    from tests.test_size_store import GOLDEN_BF_B_QUANTITY, _migrated, _production_momo_day, _stored_outcomes

    database = await _migrated(tmp_path, monkeypatch, "momo-step-down.db")
    friday_night = et(FRIDAY_BEFORE_GOLDEN, 17, 40)
    plan = plan_night(friday_night)
    assert (plan.night, plan.session) == (FRIDAY_BEFORE_GOLDEN, GOLDEN_MONDAY)
    proposals = verdicts(database, plan, MOMO_HOD_V2="losing")
    assert {item.identity: item.status for item in proposals.identities}["MOMO_HOD_V2"] == DEMOTION_PROPOSED

    monkeypatch.setattr(size_writer, "_utc_now", lambda: friday_night)
    result = await asyncio.to_thread(
        write_night, database=database, proposals=proposals, plan=plan, fence_store=None
    )

    assert [(change.identity, change.kind, change.to_mult) for change in result.changes] == [
        ("MOMO_HOD_V2", "step_down", "0.5")
    ]
    # Not today's: the Friday session (read after the write) keeps x1.
    assert session_identity_multiplier(
        "MOMO_HOD_V2", sendable=True, session_date=FRIDAY_BEFORE_GOLDEN, database=database,
        now=friday_night + timedelta(minutes=5),
    ) == Decimal("1")
    # The next session's: the golden Monday through the production path.
    decisions, pipeline = await _production_momo_day(database, monkeypatch)
    assert pipeline.identity_multiplier == Decimal("0.5")
    bf_b = decisions["BF B"]
    assert bf_b.outcome.value == "shadow_accepted"
    assert bf_b.risk is not None and bf_b.risk.intent is not None
    assert bf_b.risk.intent.quantity == math.floor(GOLDEN_BF_B_QUANTITY * Decimal("0.5")) == 1863
    assert _stored_outcomes(database)[bf_b.decision_key] == "shadow_accepted"
    assert criticals(sizing_log) == []

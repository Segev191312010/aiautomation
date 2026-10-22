"""The identity size store (migration 18) and its one read at session start (plan P3-C).

Owner decisions D2/D3 (2026-10-18): sizes live in the database and change only
at a session start; a step up needs the owner's Telegram approval, a step down
applies by itself; the ceiling is x1, PAPER only. The database refuses every
other write (append-only tables, one rung, owner-approved step ups, recorded
automatic step downs, one step per session, PAPER only); the read
(``identity_sizing.session_identity_multiplier``) fails closed with a named
CRITICAL and never falls back to a cached or default size (O13). The
production path is driven end to end: ``shadow_service.run_shadow_session``
reads the size and builds the real pipeline and repository runner, and the
golden 2026-10-12 MOMO_HOD_V2 day runs through them.
"""
from __future__ import annotations

import ast
from collections.abc import Iterator
from contextlib import closing
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
import functools
import logging
import math
from pathlib import Path
import re
import shutil
import sqlite3
from types import MappingProxyType, SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from tests.schema_support import apply_up_to, migrate_database
from trading.operations.db_admin import trading_day
from trading.risk import identity_sizing
from trading.risk.engine import IDENTITY_SIZING_MISSING
from trading.risk.identity_sizing import (
    ACCOUNT_MODE_WITHOUT_CEILING,
    CURRENT_ACCOUNT_MODE,
    IDENTITY_SIZE_ABOVE_CEILING,
    IDENTITY_SIZE_CEILING,
    IDENTITY_SIZE_MULTIPLIER,
    IDENTITY_SIZE_STORE_UNREADABLE,
    SENDABLE_WITHOUT_CEILING,
    SIZE_EVENT_CHAIN_BROKEN,
    SIZE_EVENT_WRITTEN_MID_SESSION,
    SIZE_LADDER,
    SIZE_READ_BEFORE_SESSION_START,
    session_identity_multiplier,
)
from trading.scheduler.sessions import market_session
from trading.sendable_identities import SENDABLE_IDENTITIES
from trading.storage import size_schema
from trading.storage.migrations import MIGRATIONS
from trading.storage.size_schema import (
    OWNER_TELEGRAM_ACTOR,
    SIZE_APPROVAL_AFTER_DEADLINE,
    SIZE_DECISION_NOT_FOR_PROPOSAL,
    SIZE_EVENT_NOT_AFTER_PREVIOUS,
    SIZE_STEP_DOWN_NEEDS_AUTO_APPLIED,
    SIZE_STEP_NOT_FROM_CURRENT,
    SIZE_STEP_UP_NEEDS_OWNER_APPROVAL,
    STEP_DOWN,
    STEP_UP,
)

ET = ZoneInfo("America/New_York")
UTC = timezone.utc
BACKEND = Path(__file__).resolve().parents[1]
MONDAY = date(2026, 10, 12)  # the golden MOMO_HOD_V2 day
TUESDAY = date(2026, 10, 13)
WEDNESDAY = date(2026, 10, 14)
SATURDAY = date(2026, 10, 17)
# The golden day's BF B quantity at x1 (665k net liq, 15 % notional at 26.77).
GOLDEN_BF_B_QUANTITY = 3726


def et(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=ET).astimezone(UTC)


def start(day: date) -> datetime:
    """The session's start as the read uses it (the supervisor's first child)."""
    session = market_session(day)
    assert session is not None
    return trading_day(session).starts_at


def night_before(day: date) -> datetime:
    """20:00 ET the evening before ``day``: when the nightly writer runs."""
    return et(day - timedelta(days=1), 20, 0)


def _stamp(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat()


# --- writing the store as the P3-D/P3-E writers will (test-only SQL) ---------------

PROPOSAL = """INSERT INTO autotrader_size_proposals
    (proposal_id, as_of, identity, kind, from_mult, to_mult, evidence_json, evidence_fingerprint,
     nights_since_last_step, deadline_at, rule_version, created_at)
    VALUES (?, ?, ?, ?, ?, ?, '{"trades": 30}', ?, 12, ?, 'promotion-proposals-v1', ?)"""
DECISION = "INSERT INTO autotrader_size_decisions (proposal_id, decision, actor, at) VALUES (?, ?, ?, ?)"
EVENT = """INSERT INTO autotrader_size_events
    (identity, from_mult, to_mult, effective_session, kind, proposal_id, created_at, account_mode)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)"""


def _kind(origin: str, target: str) -> str:
    return STEP_UP if Decimal(target) > Decimal(origin) else STEP_DOWN


class SizeLedger:
    """A proposal, its decision and its size event, each in its own committed write."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.serial = 0

    def connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path)
        db.execute("PRAGMA foreign_keys=ON")
        return db

    def write(self, sql: str, params: tuple[object, ...], *, ignore_checks: bool = False) -> int:
        with closing(self.connect()) as db:
            if ignore_checks:
                db.execute("PRAGMA ignore_check_constraints=ON")
            cursor = db.execute(sql, params)
            db.commit()
            return int(cursor.lastrowid or 0)

    def refused(self, sql: str, params: tuple[object, ...]) -> str:
        """The database's refusal text; nothing is written."""
        with pytest.raises(sqlite3.IntegrityError) as caught:
            self.write(sql, params)
        return str(caught.value)

    def propose(
        self, identity: str, origin: str, target: str, *, at: datetime, kind: str | None = None,
        ignore_checks: bool = False,
    ) -> str:
        kind = kind or _kind(origin, target)
        self.serial += 1
        proposal_id = f"{self.serial:010x}"
        deadline = _stamp(at + timedelta(hours=2)) if kind == STEP_UP else None
        self.write(
            PROPOSAL,
            (proposal_id, at.astimezone(ET).date().isoformat(), identity, kind, origin, target, "a" * 64,
             deadline, _stamp(at)),
            ignore_checks=ignore_checks,
        )
        return proposal_id

    def decide(self, proposal_id: str, decision: str, *, actor: str, at: datetime, ignore_checks: bool = False) -> None:
        self.write(DECISION, (proposal_id, decision, actor, _stamp(at)), ignore_checks=ignore_checks)

    def event_params(
        self, identity: str, origin: str, target: str, *, proposal_id: str, effective: date, at: datetime,
        kind: str | None = None, mode: str = "paper",
    ) -> tuple[object, ...]:
        return (identity, origin, target, effective.isoformat(), kind or _kind(origin, target), proposal_id,
                _stamp(at), mode)

    def event(self, *args: object, ignore_checks: bool = False, **kwargs: object) -> int:
        params = self.event_params(*args, **kwargs)  # type: ignore[arg-type]
        return self.write(EVENT, params, ignore_checks=ignore_checks)

    def step(self, identity: str, origin: str, target: str, *, effective: date, at: datetime) -> int:
        """One complete step: proposed at ``at``; decided and its event written an hour later."""
        kind = _kind(origin, target)
        proposal_id = self.propose(identity, origin, target, at=at)
        decided = at + timedelta(hours=1)
        if kind == STEP_UP:
            self.decide(proposal_id, "approved", actor=OWNER_TELEGRAM_ACTOR, at=decided)
        else:
            self.decide(proposal_id, "auto_applied", actor="size_writer", at=decided)
        return self.event(identity, origin, target, proposal_id=proposal_id, effective=effective, at=decided)


@pytest.fixture
def store(tmp_path) -> SizeLedger:
    return SizeLedger(migrate_database(tmp_path / "sizes.db"))


class _Collect(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@pytest.fixture
def sizing_log() -> Iterator[_Collect]:
    """The size read's own logger, whatever its parents' propagation is."""
    logger = logging.getLogger("autotrader.shadow.sizing")
    handler = _Collect()
    level = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    try:
        yield handler
    finally:
        logger.removeHandler(handler)
        logger.setLevel(level)


def criticals(log: _Collect) -> list[str]:
    """The token of every CRITICAL line, in order."""
    return [record.getMessage().split(":", 1)[0] for record in log.records if record.levelno == logging.CRITICAL]


def read(path: Path | str, name: str, day: date, *, now: datetime | None = None, sendable: bool = True):
    return session_identity_multiplier(
        name, sendable=sendable, session_date=day, database=path,
        now=start(day) + timedelta(hours=2) if now is None else now,
    )


# --- the pinned constants ----------------------------------------------------------


def test_v18_is_pinned_and_holds_the_ladder_the_code_uses(store):
    migration = MIGRATIONS[-1]
    assert (migration.version, migration.name) == (18, "identity-size-store")
    assert size_schema.SIZE_STORE_MIGRATION == 18
    # One ladder: the promoter's is the sizing module's, and the store's CHECKs hold its text.
    from trading.research import promotion_proposals

    assert promotion_proposals.SIZE_LADDER is SIZE_LADDER
    assert SIZE_LADDER == (Decimal("0.25"), Decimal("0.5"), Decimal("1"))
    assert size_schema.SIZE_RUNGS == tuple(str(rung) for rung in SIZE_LADDER)
    for statement in size_schema.SIZE_STORE_SCHEMA:
        for value in re.findall(r"(?:from_mult|to_mult) = '([0-9.]+)'", statement):
            assert value in (*size_schema.SIZE_RUNGS, size_schema.SHADOW_ONLY_MULT), value
    with closing(sqlite3.connect(store.path)) as db:
        names = {(kind, name) for kind, name in db.execute("SELECT type, name FROM sqlite_master")}
    for table in size_schema.SIZE_STORE_TABLES:
        assert ("table", table) in names
        assert ("trigger", f"{table}_no_update") in names and ("trigger", f"{table}_no_delete") in names
    assert ("view", size_schema.SIZE_PROPOSAL_STATE_VIEW) in names


def test_the_ceiling_and_the_account_mode_are_pinned():
    assert CURRENT_ACCOUNT_MODE == "paper" == size_schema.PAPER_ACCOUNT_MODE
    assert set(IDENTITY_SIZE_CEILING) == {"paper"}
    paper = IDENTITY_SIZE_CEILING["paper"]
    assert set(paper) == set(SENDABLE_IDENTITIES)
    assert all(type(value) is Decimal and value == Decimal("1") for value in paper.values())
    for table in (IDENTITY_SIZE_CEILING, paper):
        assert isinstance(table, MappingProxyType)
    with pytest.raises(TypeError):
        paper["ORB_V2"] = Decimal("2")  # type: ignore[index]
    # Every baseline sits on the ladder, at or under its ceiling.
    for name, baseline in IDENTITY_SIZE_MULTIPLIER.items():
        assert baseline in SIZE_LADDER and baseline <= paper[name], name
    assert OWNER_TELEGRAM_ACTOR == "owner_telegram"


# --- the database's own rules (migration 18) ----------------------------------------


def _isolated_event_refusal(
    store: SizeLedger, identity: str, origin: str, target: str, *, kind: str, effective: date, at: datetime,
    mode: str = "paper",
) -> str:
    """The events table's own refusal of one event, its triggers satisfied.

    BEFORE triggers run ahead of the CHECKs, so the event gets a matching
    proposal and decision (written past their own CHECKs) and only the
    event's constraints are left to refuse it.
    """
    proposal_id = store.propose(identity, origin, target, at=at, kind=kind, ignore_checks=True)
    if kind == STEP_UP:
        store.decide(proposal_id, "approved", actor=OWNER_TELEGRAM_ACTOR, at=at + timedelta(minutes=1))
    else:
        store.decide(proposal_id, "auto_applied", actor="size_writer", at=at + timedelta(minutes=1))
    return store.refused(EVENT, store.event_params(
        identity, origin, target, proposal_id=proposal_id, effective=effective, at=at, kind=kind, mode=mode,
    ))


def test_v18_append_only_ladder_ceiling_and_actor_checks(store):
    at = night_before(TUESDAY)
    up = store.propose("ORB_V2", "0.25", "0.5", at=at)
    store.decide(up, "approved", actor=OWNER_TELEGRAM_ACTOR, at=at + timedelta(minutes=30))
    store.event("ORB_V2", "0.25", "0.5", proposal_id=up, effective=TUESDAY, at=at + timedelta(minutes=30))
    store.write(
        "INSERT INTO autotrader_size_evaluations (identity, as_of, status, fingerprint, created_at)"
        " VALUES ('ORB_V2', '2026-10-12', 'proposed', ?, ?)",
        ("b" * 64, _stamp(at)),
    )

    # Append-only, all four tables: even an update that changes nothing.
    for table, column in (
        ("autotrader_size_proposals", "rule_version"),
        ("autotrader_size_decisions", "actor"),
        ("autotrader_size_events", "to_mult"),
        ("autotrader_size_evaluations", "status"),
    ):
        assert "append-only" in store.refused(f"UPDATE {table} SET {column} = {column}", ()), table
        assert "append-only" in store.refused(f"DELETE FROM {table}", ()), table

    # One rung: a proposal never skips a rung or leaves the ladder.
    for origin, target in (("0.25", "1"), ("1", "0.25"), ("1", "2"), ("0.5", "0.5"), ("0.5", "0")):
        params = ("b" * 10, "2026-10-12", "GAP_GO_V2", _kind(origin, target), origin, target, "a" * 64,
                  _stamp(at + timedelta(hours=1)) if _kind(origin, target) == STEP_UP else None, _stamp(at))
        assert "CHECK" in store.refused(PROPOSAL, params), (origin, target)
    # Nor does an event, even one its (forced) proposal and decision would allow; the
    # ceiling is the top rung, x1.
    for origin, target, kind in (
        ("0.25", "1", STEP_UP), ("1", "0.25", STEP_DOWN), ("1", "2", STEP_UP), ("1", "1.5", STEP_UP),
        ("0.5", "0.5", STEP_DOWN), ("0.5", "1", STEP_DOWN), ("0.5", "0.25", STEP_UP),
    ):
        refusal = _isolated_event_refusal(store, "MOMO_HOD_V2", origin, target, kind=kind, effective=WEDNESDAY, at=at)
        assert "CHECK" in refusal, (origin, target, kind)
    # PAPER only.
    refusal = _isolated_event_refusal(store, "VWAP_PB_V2", "0.5", "0.25", kind=STEP_DOWN, effective=WEDNESDAY,
                                      at=at, mode="live")
    assert "CHECK" in refusal

    # Actor: only the owner, from Telegram, approves a step up ...
    second = store.propose("VWAP_PB_V2", "0.5", "1", at=at)
    for actor in ("size_writer", "manager", "agent", "owner"):
        assert "CHECK" in store.refused(DECISION, (second, "approved", actor, _stamp(at + timedelta(minutes=5))))
    # ... a step up with no decision, or a rejected one, writes no event ...
    params = store.event_params("VWAP_PB_V2", "0.5", "1", proposal_id=second, effective=TUESDAY, at=at)
    assert SIZE_STEP_UP_NEEDS_OWNER_APPROVAL in store.refused(EVENT, params)
    store.decide(second, "rejected", actor=OWNER_TELEGRAM_ACTOR, at=at + timedelta(minutes=5))
    assert SIZE_STEP_UP_NEEDS_OWNER_APPROVAL in store.refused(EVENT, params)
    # ... the event trigger checks the actor itself, even past the decision's CHECK ...
    third = store.propose("GAP_GO_V2", "0.5", "1", at=at)
    store.decide(third, "approved", actor="agent", at=at + timedelta(minutes=5), ignore_checks=True)
    params = store.event_params("GAP_GO_V2", "0.5", "1", proposal_id=third, effective=TUESDAY, at=at)
    assert SIZE_STEP_UP_NEEDS_OWNER_APPROVAL in store.refused(EVENT, params)
    # ... and an approval raises only the step it approved (ORB's, not MOMO's).
    sneaky = store.event_params("MOMO_HOD_V2", "0.25", "0.5", proposal_id=up, effective=WEDNESDAY, at=at)
    assert SIZE_STEP_UP_NEEDS_OWNER_APPROVAL in store.refused(EVENT, sneaky)

    with closing(sqlite3.connect(store.path)) as db:
        events = db.execute("SELECT identity, from_mult, to_mult, kind FROM autotrader_size_events").fetchall()
    assert events == [("ORB_V2", "0.25", "0.5", STEP_UP)]


def test_v18_step_downs_chain_and_one_step_per_session(store):
    at = night_before(TUESDAY)
    # A step down needs its proposal's automatic decision.
    down = store.propose("MOMO_HOD_V2", "1", "0.5", at=at)
    params = store.event_params("MOMO_HOD_V2", "1", "0.5", proposal_id=down, effective=TUESDAY, at=at)
    assert SIZE_STEP_DOWN_NEEDS_AUTO_APPLIED in store.refused(EVENT, params)
    # Decisions fit their proposal's kind: no approval of a step down, no automatic step up,
    # no fence of a step that has a rung, no decision without a proposal.
    up = store.propose("ORB_V2", "0.25", "0.5", at=at)
    for proposal_id, decision in ((down, "approved"), (up, "auto_applied"), (down, "fenced"),
                                  (up, "would_fence"), ("f" * 10, "rejected")):
        refusal = store.refused(DECISION, (proposal_id, decision, OWNER_TELEGRAM_ACTOR, _stamp(at)))
        assert SIZE_DECISION_NOT_FOR_PROPOSAL in refusal, (proposal_id, decision)
    # The bottom rung steps down to shadow-only: a fence decision, never a size event.
    bottom = store.propose("ORB_V2", "0.25", "0", at=at)
    store.decide(bottom, "would_fence", actor="size_writer", at=at)
    assert SIZE_STEP_DOWN_NEEDS_AUTO_APPLIED in store.refused(EVENT, store.event_params(
        "ORB_V2", "0.25", "0", proposal_id=bottom, effective=TUESDAY, at=at, kind=STEP_DOWN))
    # One decision per proposal; an approval only before its deadline (two hours here).
    store.decide(down, "auto_applied", actor="size_writer", at=at)
    assert "UNIQUE" in store.refused(DECISION, (down, "auto_applied", "size_writer", _stamp(at)))
    assert SIZE_APPROVAL_AFTER_DEADLINE in store.refused(
        DECISION, (up, "approved", OWNER_TELEGRAM_ACTOR, _stamp(at + timedelta(hours=2)))
    )
    store.decide(up, "approved", actor=OWNER_TELEGRAM_ACTOR, at=at + timedelta(hours=2) - timedelta(seconds=1))

    # Never back-dated: an event takes effect on or after the day it was written.
    backdated = store.event_params("MOMO_HOD_V2", "1", "0.5", proposal_id=down, effective=MONDAY, at=at)
    assert "CHECK" in store.refused(EVENT, backdated)
    store.event("MOMO_HOD_V2", "1", "0.5", proposal_id=down, effective=TUESDAY, at=at)
    # The next step starts where the last one ended, at a later session.
    again = store.propose("MOMO_HOD_V2", "1", "0.5", at=at)
    store.decide(again, "auto_applied", actor="size_writer", at=at)
    assert SIZE_STEP_NOT_FROM_CURRENT in store.refused(EVENT, store.event_params(
        "MOMO_HOD_V2", "1", "0.5", proposal_id=again, effective=WEDNESDAY, at=at))
    lower = store.propose("MOMO_HOD_V2", "0.5", "0.25", at=at)
    store.decide(lower, "auto_applied", actor="size_writer", at=at)
    assert SIZE_EVENT_NOT_AFTER_PREVIOUS in store.refused(EVENT, store.event_params(
        "MOMO_HOD_V2", "0.5", "0.25", proposal_id=lower, effective=TUESDAY, at=at))
    store.event("MOMO_HOD_V2", "0.5", "0.25", proposal_id=lower, effective=WEDNESDAY, at=at)

    with closing(sqlite3.connect(store.path)) as db:
        # One event per proposal (a unique index; the chain trigger fires first in practice).
        indexes = db.execute("PRAGMA index_list(autotrader_size_events)").fetchall()
        unique_columns = [
            [row[2] for row in db.execute(f"PRAGMA index_info('{index[1]}')")]
            for index in indexes if index[2]
        ]
        assert ["proposal_id"] in unique_columns
        # The proposal state view: pending until decided.
        pending = store.propose("GAP_GO_V2", "0.5", "1", at=at)
        states = dict(db.execute("SELECT proposal_id, state FROM autotrader_size_proposal_state"))
        events = db.execute("SELECT from_mult, to_mult, effective_session FROM autotrader_size_events").fetchall()
    assert (states[pending], states[up], states[down], states[bottom]) == (
        "pending", "approved", "auto_applied", "would_fence"
    )
    assert events == [("1", "0.5", "2026-10-13"), ("0.5", "0.25", "2026-10-14")]


# --- the session-start read --------------------------------------------------------


def test_the_session_start_is_the_supervised_trading_day(store):
    assert start(TUESDAY) == et(TUESDAY, 7, 55)
    assert start(date(2026, 11, 27)) == et(date(2026, 11, 27), 7, 55)  # a half day starts as usual


def test_baseline_without_rows(store, sizing_log):
    for name in SENDABLE_IDENTITIES:
        assert read(store.path, name, TUESDAY) == IDENTITY_SIZE_MULTIPLIER[name], name
    assert read(store.path, "ORB_V2", TUESDAY) == Decimal("0.25")
    assert type(read(store.path, "ORB_V2", TUESDAY)) is Decimal
    # A shadow identity is x1 and reads nothing at all (no database needed).
    missing = store.path.parent / "absent.db"
    assert read(missing, "ORB_V1", TUESDAY, sendable=False) == Decimal("1")
    assert read(missing, "SH_MOMO_HOD_V3", SATURDAY, now=et(SATURDAY, 12, 0), sendable=False) == Decimal("1")
    assert not missing.exists()
    assert criticals(sizing_log) == []


def test_latest_effective_event(store, sizing_log):
    first = date(2026, 10, 6)
    second = date(2026, 10, 9)
    store.step("ORB_V2", "0.25", "0.5", effective=first, at=night_before(first))
    store.step("ORB_V2", "0.5", "0.25", effective=second, at=night_before(second))
    store.step("ORB_V2", "0.25", "0.5", effective=TUESDAY, at=night_before(TUESDAY))
    expected = {
        date(2026, 10, 5): Decimal("0.25"),  # before any event: the baseline
        first: Decimal("0.5"),  # effective that very session
        date(2026, 10, 8): Decimal("0.5"),
        second: Decimal("0.25"),
        MONDAY: Decimal("0.25"),
        TUESDAY: Decimal("0.5"),  # the latest event, effective on the session date itself
        WEDNESDAY: Decimal("0.5"),
    }
    assert {day: read(store.path, "ORB_V2", day) for day in expected} == expected
    # Other identities are untouched.
    assert read(store.path, "VWAP_PB_V2", TUESDAY) == Decimal("0.5")
    assert criticals(sizing_log) == []


def test_future_event_not_today(store, sizing_log):
    # Written during Tuesday's session for Wednesday: the nightly step down.
    store.step("GAP_GO_V2", "0.5", "0.25", effective=WEDNESDAY, at=et(TUESDAY, 17, 0))
    assert read(store.path, "GAP_GO_V2", TUESDAY, now=et(TUESDAY, 18, 0)) == Decimal("0.5")
    assert read(store.path, "GAP_GO_V2", WEDNESDAY) == Decimal("0.25")
    assert criticals(sizing_log) == []  # a future event is not a mid-session write


def test_mid_session_event_ignored_critical(store, sizing_log):
    # A step up recorded for Tuesday at 10:30 ET, after Tuesday's 07:55 start (a writer
    # defect: every writer targets a session not yet started).
    store.step("VWAP_PB_V2", "0.5", "1", effective=TUESDAY, at=et(TUESDAY, 9, 30))
    assert read(store.path, "VWAP_PB_V2", TUESDAY, now=et(TUESDAY, 11, 0)) == Decimal("0.5")
    assert criticals(sizing_log) == [SIZE_EVENT_WRITTEN_MID_SESSION]
    (critical,) = [record for record in sizing_log.records if record.levelno == logging.CRITICAL]
    message = critical.getMessage()
    assert "VWAP_PB_V2" in message and "ignored this session, the size stays x0.5" in message
    # The next session started after it was written: it is in force there, quietly.
    sizing_log.records.clear()
    assert read(store.path, "VWAP_PB_V2", WEDNESDAY) == Decimal("1")
    assert criticals(sizing_log) == []
    # The boundary: written exactly at the start is mid-session; a microsecond before is not.
    store.step("ORB_V2", "0.25", "0.5", effective=TUESDAY, at=start(TUESDAY) - timedelta(hours=1))
    store.step("GAP_GO_V2", "0.5", "0.25", effective=TUESDAY,
               at=start(TUESDAY) - timedelta(hours=1, microseconds=1))
    assert read(store.path, "ORB_V2", TUESDAY) == Decimal("0.25")
    assert read(store.path, "GAP_GO_V2", TUESDAY) == Decimal("0.25")
    assert criticals(sizing_log) == [SIZE_EVENT_WRITTEN_MID_SESSION]


def test_the_read_is_never_before_the_session_start(store, sizing_log):
    store.step("GAP_GO_V2", "0.5", "0.25", effective=TUESDAY, at=et(TUESDAY, 6, 0))
    # 07:54:59 ET: a step down written later could still take effect; no size yet.
    assert read(store.path, "GAP_GO_V2", TUESDAY, now=start(TUESDAY) - timedelta(seconds=1)) is None
    assert criticals(sizing_log) == [SIZE_READ_BEFORE_SESSION_START]
    assert read(store.path, "GAP_GO_V2", TUESDAY, now=start(TUESDAY)) == Decimal("0.25")
    # A date with no session has no start to read at.
    assert read(store.path, "GAP_GO_V2", SATURDAY, now=et(SATURDAY, 12, 0)) is None
    assert criticals(sizing_log)[-1] == IDENTITY_SIZE_STORE_UNREADABLE
    with pytest.raises(ValueError, match="timezone-aware"):
        read(store.path, "GAP_GO_V2", TUESDAY, now=datetime(2026, 10, 13, 12, 0))


def test_unreadable_store_fails_closed(tmp_path, store, sizing_log):
    garbage = tmp_path / "garbage.db"
    garbage.write_bytes(b"not a database" * 100)
    empty = tmp_path / "empty.db"
    with closing(sqlite3.connect(empty)) as db:
        db.execute("CREATE TABLE unrelated (x)")
        db.commit()
    dropped = tmp_path / "dropped.db"
    shutil.copy(store.path, dropped)
    with closing(sqlite3.connect(dropped)) as db:
        db.execute("DROP TABLE autotrader_size_events")
        db.commit()
    mismatched = tmp_path / "mismatched.db"
    shutil.copy(store.path, mismatched)
    with closing(sqlite3.connect(mismatched)) as db:
        db.execute("UPDATE autotrader_schema_migrations SET checksum = 'x' WHERE version = 18")
        db.commit()
    # The tables without their ledger row (created by hand, never by `db migrate`).
    unrecorded = tmp_path / "unrecorded.db"
    shutil.copy(store.path, unrecorded)
    with closing(sqlite3.connect(unrecorded)) as db:
        db.execute("DELETE FROM autotrader_schema_migrations WHERE version = 18")
        db.commit()
    malformed = SizeLedger(tmp_path / "malformed.db")
    shutil.copy(store.path, malformed.path)
    # A row off the ladder, written past the CHECKs (the triggers still pass it).
    proposal = malformed.propose("GAP_GO_V2", "0.5", "0.3", at=night_before(TUESDAY), kind=STEP_DOWN,
                                 ignore_checks=True)
    malformed.decide(proposal, "auto_applied", actor="size_writer", at=night_before(TUESDAY))
    malformed.event("GAP_GO_V2", "0.5", "0.3", proposal_id=proposal, effective=TUESDAY, at=night_before(TUESDAY),
                    kind=STEP_DOWN, ignore_checks=True)
    missing = tmp_path / "absent.db"

    for path in (missing, garbage, empty, dropped, mismatched, unrecorded, malformed.path, ":memory:"):
        sizing_log.records.clear()
        assert read(path, "GAP_GO_V2", TUESDAY) is None, path
        assert criticals(sizing_log) == [IDENTITY_SIZE_STORE_UNREADABLE], path
        # Never the baseline, and never another identity's luck: the shadow name alone is x1.
        assert read(path, "GAP_GO_V1", TUESDAY, sendable=False) == Decimal("1")
    assert not missing.exists()  # a read-only open never creates the database
    assert "schema_checksum_mismatch" in _detail(mismatched)
    assert "schema_migration_pending" in _detail(unrecorded)
    assert "does not parse" in _detail(malformed.path)


def _detail(path: Path) -> str:
    log = _Collect()
    logger = logging.getLogger("autotrader.shadow.sizing")
    logger.addHandler(log)
    try:
        read(path, "GAP_GO_V2", TUESDAY)
    finally:
        logger.removeHandler(log)
    return log.records[-1].getMessage()


def _ceiling(paper: dict[str, Decimal]) -> MappingProxyType:
    return MappingProxyType({"paper": MappingProxyType(paper)})


def test_other_mode_fails_closed(store, sizing_log, monkeypatch):
    monkeypatch.setattr(identity_sizing, "CURRENT_ACCOUNT_MODE", "live")
    for name in SENDABLE_IDENTITIES:
        assert read(store.path, name, TUESDAY) is None, name
    assert set(criticals(sizing_log)) == {ACCOUNT_MODE_WITHOUT_CEILING}
    assert read(store.path, "ORB_V1", TUESDAY, sendable=False) == Decimal("1")

    # The paper row without one identity: that identity alone has no size.
    monkeypatch.setattr(identity_sizing, "CURRENT_ACCOUNT_MODE", "paper")
    trimmed = {name: value for name, value in IDENTITY_SIZE_CEILING["paper"].items() if name != "ORB_V2"}
    monkeypatch.setattr(identity_sizing, "IDENTITY_SIZE_CEILING", _ceiling(trimmed))
    sizing_log.records.clear()
    assert read(store.path, "ORB_V2", TUESDAY) is None
    assert read(store.path, "MOMO_HOD_V2", TUESDAY) == Decimal("1")
    assert criticals(sizing_log) == [SENDABLE_WITHOUT_CEILING]


def test_a_size_above_its_ceiling_fails_closed(store, sizing_log, monkeypatch):
    store.step("ORB_V2", "0.25", "0.5", effective=TUESDAY, at=night_before(TUESDAY))
    lowered = {"MOMO_HOD_V2": Decimal("0.5"), "ORB_V2": Decimal("0.25"), "VWAP_PB_V2": Decimal("0.5"),
               "GAP_GO_V2": Decimal("0.5")}
    monkeypatch.setattr(identity_sizing, "IDENTITY_SIZE_CEILING", _ceiling(lowered))
    assert read(store.path, "MOMO_HOD_V2", TUESDAY) is None  # its baseline x1 is above x0.5
    assert read(store.path, "ORB_V2", TUESDAY) is None  # its event x0.5 is above x0.25
    assert read(store.path, "ORB_V2", MONDAY) == Decimal("0.25")  # before the event: at the ceiling
    assert read(store.path, "VWAP_PB_V2", TUESDAY) == Decimal("0.5")
    assert criticals(sizing_log) == [IDENTITY_SIZE_ABOVE_CEILING, IDENTITY_SIZE_ABOVE_CEILING]


def test_the_event_chain_starts_at_the_baseline(store, sizing_log, monkeypatch):
    # ORB_V2's baseline is x0.25: a first event from x0.5 is not its history.
    store.step("ORB_V2", "0.5", "0.25", effective=TUESDAY, at=night_before(TUESDAY))
    assert read(store.path, "ORB_V2", WEDNESDAY) is None
    assert read(store.path, "ORB_V2", MONDAY) is None  # the whole history is refused, not just its tail
    assert criticals(sizing_log) == [SIZE_EVENT_CHAIN_BROKEN, SIZE_EVENT_CHAIN_BROKEN]
    # A baseline changed under existing events breaks the chain too: no guessing.
    store.step("GAP_GO_V2", "0.5", "0.25", effective=TUESDAY, at=night_before(TUESDAY))
    assert read(store.path, "GAP_GO_V2", TUESDAY) == Decimal("0.25")
    rows = {**IDENTITY_SIZE_MULTIPLIER, "GAP_GO_V2": Decimal("1")}
    monkeypatch.setattr(identity_sizing, "IDENTITY_SIZE_MULTIPLIER", MappingProxyType(rows))
    assert read(store.path, "GAP_GO_V2", TUESDAY) is None
    assert criticals(sizing_log)[-1] == SIZE_EVENT_CHAIN_BROKEN


def test_a_sendable_name_without_a_baseline_row_is_none_never_one(store, sizing_log, monkeypatch):
    trimmed = {name: value for name, value in IDENTITY_SIZE_MULTIPLIER.items() if name != "ORB_V2"}
    monkeypatch.setattr(identity_sizing, "IDENTITY_SIZE_MULTIPLIER", MappingProxyType(trimmed))
    assert read(store.path, "ORB_V2", TUESDAY) is None
    # The pipeline reports it (identity_sizing_missing, once per run); the read adds nothing.
    assert criticals(sizing_log) == []


def test_the_size_read_keeps_no_cache():
    """O13: no memo, no module state, no global; every read opens the database afresh."""
    source = Path(identity_sizing.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        assert not isinstance(node, (ast.Global, ast.Nonlocal)), ast.dump(node)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for decorator in node.decorator_list:
                assert "cache" not in ast.unparse(decorator), node.name
    assert "functools" not in source and "lru_cache" not in source


async def test_v18_pending_blocks_entries_never_cached(tmp_path, monkeypatch, sizing_log):
    from tests.test_shadow_service import _pipeline_keywords, _size_store
    from trading.operations import shadow_service

    current = await _size_store(tmp_path, monkeypatch)
    pending = await apply_up_to(tmp_path / "v17.db", 17)
    assert read(current, "ORB_V2", TUESDAY) == Decimal("0.25")
    assert read(pending, "ORB_V2", TUESDAY) is None
    assert criticals(sizing_log) == [IDENTITY_SIZE_STORE_UNREADABLE]
    assert "schema_migration_pending" in sizing_log.records[-1].getMessage()
    # The same path, restored to a v17 backup (the database and its -wal/-shm moved
    # aside first): no size is remembered from the read before.
    for suffix in ("-wal", "-shm"):
        Path(f"{current}{suffix}").unlink(missing_ok=True)
    shutil.copy(pending, current)
    assert read(current, "ORB_V2", TUESDAY) is None
    assert criticals(sizing_log) == [IDENTITY_SIZE_STORE_UNREADABLE] * 2

    # Through the composition root (the child's own schema gate refuses first, exit 75;
    # see test_schema_gate): every sendable session's pipeline blocks, shadow ones keep x1.
    for key in sorted(shadow_service.STRATEGIES):
        keywords, _name = await _pipeline_keywords(monkeypatch, key)
        expected = None if key in shadow_service.SENDABLE_KEYS else Decimal("1")
        assert keywords["identity_multiplier"] == expected, key


async def test_child_restart_same_day_same_size(tmp_path, monkeypatch, sizing_log):
    from tests.test_shadow_service import TUESDAY as SERVICE_TUESDAY, _pipeline_keywords, _size_store

    assert SERVICE_TUESDAY == TUESDAY
    database = await _size_store(tmp_path, monkeypatch)
    ledger = SizeLedger(database)
    ledger.step("ORB_V2", "0.25", "0.5", effective=MONDAY, at=night_before(MONDAY))
    first, name = await _pipeline_keywords(monkeypatch, "orb-v2", at=et(TUESDAY, 9, 0))
    assert name == "ORB_V2" and first["identity_multiplier"] == Decimal("0.5")
    assert criticals(sizing_log) == []
    # Mid-session: a step written for today (a writer defect), and tonight's step down for tomorrow.
    ledger.step("ORB_V2", "0.5", "1", effective=TUESDAY, at=et(TUESDAY, 10, 0))
    ledger.step("ORB_V2", "1", "0.5", effective=WEDNESDAY, at=et(TUESDAY, 10, 30))
    restarted, _name = await _pipeline_keywords(monkeypatch, "orb-v2", at=et(TUESDAY, 11, 45))
    assert restarted["identity_multiplier"] == first["identity_multiplier"] == Decimal("0.5")
    assert criticals(sizing_log) == [SIZE_EVENT_WRITTEN_MID_SESSION]
    # Wednesday's start reads the history as it stands: today's step, then the step down.
    assert read(database, "ORB_V2", WEDNESDAY) == Decimal("0.5")
    assert criticals(sizing_log) == [SIZE_EVENT_WRITTEN_MID_SESSION]


async def test_the_read_waits_for_the_session_start(tmp_path, monkeypatch, sizing_log):
    """A run started at 04:00 ET reads its size after its wait for the 09:40 connect slot,
    never at 04:00, before the session's 07:55 start (a read then would be refused)."""
    from tests.test_shadow_service import _pipeline_keywords, _size_store

    database = await _size_store(tmp_path, monkeypatch)
    ledger = SizeLedger(database)
    ledger.step("ORB_V2", "0.25", "0.5", effective=MONDAY, at=night_before(MONDAY))
    ledger.step("ORB_V2", "0.5", "0.25", effective=TUESDAY, at=et(TUESDAY, 5, 0))
    keywords, name = await _pipeline_keywords(monkeypatch, "orb-v2", at=et(TUESDAY, 4, 0))
    assert name == "ORB_V2" and keywords["identity_multiplier"] == Decimal("0.25")
    assert criticals(sizing_log) == []


# --- the production path: run_shadow_session -> pipeline -> repository runner -------


def _metered_reviewer():
    """The golden day's scripted reviewer, with the usage the composition root logs."""
    from tests.test_setup_persistence import _Reviewer

    reviewer = _Reviewer()
    reviewer.usage = SimpleNamespace(calls=0, cost_usd=Decimal("0"))  # type: ignore[attr-defined]
    return reviewer


async def _production_momo_day(database: Path, monkeypatch):
    """The golden 2026-10-12 MOMO_HOD_V2 day through ``run_shadow_session``.

    The composition root reads the size and builds the real ``ShadowPipeline`` and
    ``RepositoryBackedShadowRunner``; only TWS, Claude and the poll loop are stand-ins
    (the loop replays the recorded day through the runner it was handed). The lease
    timing is stretched because the replay's clock jumps a whole bar per poll.
    """
    from tests.test_setup_persistence import SESSION, _et, _replay_momo_session
    from tests.test_shadow_service import runtime_config
    from trading.operations import shadow_service
    from trading.orchestration import RepositoryBackedShadowRunner, ShadowPipeline
    from trading.orchestration.session_runner import SessionReport

    assert SESSION == MONDAY
    state = {"now": _et(SESSION, 9, 0)}
    reviewer = _metered_reviewer()
    built: list[ShadowPipeline] = []
    captured: dict[str, object] = {}

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
            raise AssertionError("the replay brings its own portfolio")

    class FakeMarket:
        def __init__(self, **_kwargs: object) -> None:
            pass

        async def load_contract(self, con_id: int) -> None:
            pass

    class ReplayingSessionRunner:
        def __init__(self, *, runner: RepositoryBackedShadowRunner, **_kwargs: object) -> None:
            captured["runner"] = runner

        async def run(self, **kwargs: object) -> SessionReport:
            decisions, _ = await _replay_momo_session(
                database, monkeypatch, runner=captured["runner"], reviewer=reviewer, clock=state
            )
            captured["decisions"] = decisions
            return SessionReport(run_id=str(kwargs["run_id"]), session_date=SESSION)

    def pipeline(**kwargs: object) -> ShadowPipeline:
        built.append(ShadowPipeline(**kwargs))  # type: ignore[arg-type]
        return built[-1]

    async def strategy(**_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(strategy_id="f" * 32)

    async def sleep(seconds: float) -> None:
        state["now"] += timedelta(seconds=seconds)

    monkeypatch.setattr(shadow_service, "ensure_strategy", strategy)
    monkeypatch.setattr(shadow_service, "read_anthropic_api_key", lambda: "sk-test")
    monkeypatch.setattr(shadow_service, "build_anthropic_client", lambda **_kwargs: object())
    monkeypatch.setattr(shadow_service, "AnthropicMessagesTransport", lambda **_kwargs: object())
    monkeypatch.setattr(shadow_service, "StructuredAIReviewProvider", lambda **_kwargs: reviewer)
    monkeypatch.setattr(shadow_service, "TWSPaperActor", FakeActor)
    monkeypatch.setattr(shadow_service, "PaperIBKRMarketData", FakeMarket)
    monkeypatch.setattr(shadow_service, "ShadowPipeline", pipeline)
    monkeypatch.setattr(
        shadow_service,
        "RepositoryBackedShadowRunner",
        functools.partial(
            RepositoryBackedShadowRunner, lease_ttl=timedelta(hours=6), lease_renew_interval=timedelta(hours=1)
        ),
    )
    monkeypatch.setattr(shadow_service, "ShadowSessionRunner", ReplayingSessionRunner)
    monkeypatch.setattr(shadow_service, "source_sha", lambda: "abc1234")

    await shadow_service.run_shadow_session(
        config=runtime_config(),
        owner_id="demo",
        session=market_session(SESSION),
        clock=lambda: state["now"],
        sleep=sleep,
        strategy_name="momo",
        universe="watchlist",
    )
    (built_pipeline,) = built
    return captured["decisions"], built_pipeline


async def _migrated(tmp_path: Path, monkeypatch, name: str) -> Path:
    from config import cfg
    from db.core import init_db

    database = tmp_path / name
    monkeypatch.setattr(cfg, "DB_PATH", str(database))
    await init_db()
    return database


def _stored_outcomes(database: Path) -> dict[str, str]:
    with closing(sqlite3.connect(database)) as db:
        return dict(db.execute("SELECT decision_key, outcome FROM autotrader_candidate_decisions"))


async def test_unreadable_store_blocks_through_repository_runner(tmp_path, monkeypatch, sizing_log):
    database = await _migrated(tmp_path, monkeypatch, "momo-unreadable.db")
    with closing(sqlite3.connect(database)) as db:
        db.execute("DROP TABLE autotrader_size_events")  # the store cannot be read
        db.commit()

    decisions, pipeline = await _production_momo_day(database, monkeypatch)

    assert pipeline.identity_multiplier is None
    bf_b = decisions["BF B"]
    assert bf_b.outcome.value == "risk_blocked"
    assert bf_b.risk is not None and bf_b.risk.codes == (IDENTITY_SIZING_MISSING,)
    assert bf_b.risk.intent is None
    # The bridge sends shadow_accepted rows only: there is nothing for it to send.
    stored = _stored_outcomes(database)
    assert stored[bf_b.decision_key] == "risk_blocked" and "shadow_accepted" not in stored.values()
    assert criticals(sizing_log) == [IDENTITY_SIZE_STORE_UNREADABLE]


async def test_the_session_size_reaches_the_decision_through_repository_runner(tmp_path, monkeypatch, sizing_log):
    """The positive control of the test above: a step down in force sizes BF B at x0.5."""
    database = await _migrated(tmp_path, monkeypatch, "momo-stepped.db")
    SizeLedger(database).step("MOMO_HOD_V2", "1", "0.5", effective=MONDAY, at=night_before(date(2026, 10, 10)))

    decisions, pipeline = await _production_momo_day(database, monkeypatch)

    assert pipeline.identity_multiplier == Decimal("0.5")
    bf_b = decisions["BF B"]
    assert bf_b.outcome.value == "shadow_accepted"
    assert bf_b.risk is not None and bf_b.risk.intent is not None
    assert bf_b.risk.intent.quantity == math.floor(GOLDEN_BF_B_QUANTITY * Decimal("0.5")) == 1863
    assert _stored_outcomes(database)[bf_b.decision_key] == "shadow_accepted"
    assert criticals(sizing_log) == []


# --- D6: who may write a size event ------------------------------------------------

# Exactly two code paths may insert a size event (plan P3-C, D6; the AGENTS.md
# "Identity sizing" bullet once the owner approves it). Neither exists before
# P3-D/P3-E, so today nothing in production inserts one.
SIZE_EVENT_WRITERS = frozenset({
    "trading/operations/telegram_size_approval.py",  # P3-D: /approve then /confirm, step_up only
    "trading/research/size_writer.py",  # P3-E: the nightly automatic step_down
})
# Each writer is reached only from its own child: the nightly evidence of the
# replay child and the Telegram assistant. No other module, and no CLI verb of
# its own, imports one.
SIZE_WRITER_IMPORTERS = {
    "trading.research.size_writer": frozenset({"trading/research/nightly.py"}),
    "trading.operations.telegram_size_approval": frozenset({"trading/operations/telegram_bot.py"}),
}
_EXCLUDED_ROOTS = frozenset({"tests", ".venv", ".tmp", "node_modules", "__pycache__"})
_INSERT_INTO_EVENTS = re.compile(
    r"\b(?:INSERT|REPLACE)\b(?:\s+OR\s+[A-Z]+)?\s+INTO\s+(?:[\w\"`\[\]]+\.)?[\"`\[]?"
    + size_schema.SIZE_EVENTS_TABLE
    + r"\b",
    re.IGNORECASE,
)
_INSERT_INTO_ANY = re.compile(r"\b(?:INSERT|REPLACE)\b(?:\s+OR\s+[A-Z]+)?\s+INTO\b", re.IGNORECASE)


def _production_files(suffix: str) -> list[Path]:
    """Every backend file with ``suffix`` outside the tests, the venv and scratch dirs."""
    files: list[Path] = []
    for entry in sorted(BACKEND.iterdir()):
        if entry.name in _EXCLUDED_ROOTS or entry.name.startswith("."):
            continue
        if entry.is_file() and entry.suffix == suffix:
            files.append(entry)
        elif entry.is_dir():
            files.extend(
                path for path in sorted(entry.rglob(f"*{suffix}"))
                if not any(part in _EXCLUDED_ROOTS or part.startswith(".") for part in path.relative_to(BACKEND).parts)
            )
    return files


def _production_sources() -> list[Path]:
    return _production_files(".py")


def _docstrings(tree: ast.AST) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                ids.add(id(first.value))
    return ids


def _fold(node: ast.AST) -> str | None:
    """A string expression's text: constants, f-strings ("{}" for each value), and + of those."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(part.value if isinstance(part, ast.Constant) else "{}" for part in node.values)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _fold(node.left), _fold(node.right)
        if left is not None and right is not None:
            return left + right
    return None


def size_event_writes(source: str) -> list[str]:
    """Why ``source`` would insert a size event; empty when it cannot.

    Exact: a string (a constant, an f-string or a + of them) that inserts into
    the events table. Indirect: a module that names the table (its literal or
    ``SIZE_EVENTS_TABLE``) and holds any INSERT ... INTO string, as an
    f-string over that name would. Docstrings and trigger timing clauses
    (``BEFORE INSERT ON``) are not writes.
    """
    tree = ast.parse(source)
    skip = _docstrings(tree)
    texts = [
        text
        for node in ast.walk(tree)
        if id(node) not in skip and (text := _fold(node)) is not None
    ]
    reasons = [f"inserts: {text.strip()[:80]!r}" for text in texts if _INSERT_INTO_EVENTS.search(text)]
    names_table = any(size_schema.SIZE_EVENTS_TABLE in text for text in texts) or any(
        (isinstance(node, ast.Name) and node.id == "SIZE_EVENTS_TABLE")
        or (isinstance(node, ast.Attribute) and node.attr == "SIZE_EVENTS_TABLE")
        or (isinstance(node, ast.alias) and node.name == "SIZE_EVENTS_TABLE")
        for node in ast.walk(tree)
    )
    if names_table and not reasons:
        reasons += [f"names the table and inserts: {text.strip()[:80]!r}" for text in texts
                    if _INSERT_INTO_ANY.search(text)]
    return reasons


def _imports(tree: ast.AST, relative: str) -> set[str]:
    """Every module ``tree`` (the file at ``relative``) imports, relative imports resolved."""
    package = relative.removesuffix(".py").split("/")[:-1]
    named: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            base = package[: len(package) - node.level + 1] if node.level else []
            module = ".".join([*base, *([node.module] if node.module else [])])
            named.add(module)
            named.update(f"{module}.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Import):
            named.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            named.add(node.value)  # importlib.import_module("...")
    return named


def test_only_two_writers_insert_size_events():
    sources = _production_sources()
    relative = {path.relative_to(BACKEND).as_posix() for path in sources}
    # The scan covers the whole backend: the bridge, the CLI, the children, the API.
    assert {"scripts_local/paper_bridge.py", "trading/cli.py", "autotraderctl.py", "main.py",
            "trading/storage/size_schema.py", "trading/risk/identity_sizing.py"} <= relative
    writers = {
        path.relative_to(BACKEND).as_posix(): reasons
        for path in sources
        if (reasons := size_event_writes(path.read_text(encoding="utf-8")))
    }
    assert set(writers) <= SIZE_EVENT_WRITERS, writers
    # Before P3-D/P3-E nothing writes one at all: the store holds no event until they ship.
    present = {name for name in SIZE_EVENT_WRITERS if (BACKEND / name).exists()}
    assert set(writers) <= present
    # No SQL file outside the tests touches the table either.
    sql = _production_files(".sql")
    assert not [path for path in sql if size_schema.SIZE_EVENTS_TABLE in path.read_text(encoding="utf-8")]


@pytest.mark.parametrize(
    ("source", "writes"),
    [
        ('X = "INSERT INTO autotrader_size_events (identity) VALUES (?)"\n', True),
        ('X = """insert or ignore into\n  main."autotrader_size_events" values (?)"""\n', True),
        ('X = "REPLACE INTO autotrader_size_events VALUES (?)"\n', True),
        ('X = "INSERT INTO " + "autotrader_size_events VALUES (?)"\n', True),
        ('T = "autotrader_size_events"\nX = f"INSERT INTO {T} VALUES (?)"\n', True),
        ('from trading.storage.size_schema import SIZE_EVENTS_TABLE\nX = f"INSERT INTO {SIZE_EVENTS_TABLE} (x)"\n',
         True),
        ('from trading.storage import size_schema as s\nX = "INSERT INTO %s VALUES (?)" % s.SIZE_EVENTS_TABLE\n',
         True),
        ('X = "BEFORE INSERT ON autotrader_size_events BEGIN SELECT 1; END"\n', False),
        ('X = "SELECT to_mult FROM autotrader_size_events WHERE identity = ?"\n', False),
        ('"""Never insert a size event into autotrader_size_events here."""\n', False),
        ('X = "INSERT INTO autotrader_size_decisions VALUES (?)"\n', False),
    ],
)
def test_the_writer_scan_finds_every_shape_of_insert(source, writes):
    assert bool(size_event_writes(source)) is writes, size_event_writes(source)


def test_the_import_scan_resolves_relative_imports():
    tree = ast.parse("from . import size_writer\nfrom .size_writer import write\n")
    assert {"trading.research.size_writer", "trading.research.size_writer.write"} <= _imports(
        tree, "trading/research/nightly.py"
    )
    tree = ast.parse("from ..research import size_writer\n")
    assert "trading.research.size_writer" in _imports(tree, "trading/operations/telegram_bot.py")
    assert "trading.research.size_writer" in _imports(
        ast.parse("from .size_writer import x\n"), "trading/research/__init__.py"
    )


def test_no_cli_verb_writes_sizes():
    """A writer is reached only from its own child, never from a verb of its own."""
    offenders: list[str] = []
    for path in _production_sources():
        rel = path.relative_to(BACKEND).as_posix()
        imported = _imports(ast.parse(path.read_text(encoding="utf-8")), rel)
        for module, importers in SIZE_WRITER_IMPORTERS.items():
            if any(name == module or name.startswith(f"{module}.") for name in imported) and rel not in importers:
                offenders.append(f"{rel} imports {module}")
    assert not offenders, offenders
    for entry in ("trading/cli.py", "autotraderctl.py"):
        source = (BACKEND / entry).read_text(encoding="utf-8")
        assert not size_event_writes(source), entry
        assert not any(table in source for table in size_schema.SIZE_STORE_TABLES), entry

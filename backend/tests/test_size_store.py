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
        assert ("trigger", f"{table}_no_replace") in names  # N19a MEDIUM-1
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
    # (The no-replace trigger answers before the primary key does: fix 1, N19a MEDIUM-1.)
    assert "size decisions are append-only" in store.refused(
        DECISION, (down, "auto_applied", "size_writer", _stamp(at))
    )
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


# --- N19a fix 1: no REPLACE, no upsert, no silent IGNORE; exact rungs and stamps --------

EVALUATION = """INSERT INTO autotrader_size_evaluations (identity, as_of, status, fingerprint, created_at)
    VALUES (?, ?, ?, ?, ?)"""


def _rows(path: Path) -> dict[str, list[tuple[object, ...]]]:
    with closing(sqlite3.connect(path)) as db:
        return {table: sorted(db.execute(f"SELECT * FROM {table}").fetchall(), key=repr)
                for table in size_schema.SIZE_STORE_TABLES}


def _conflicting_inserts(path: Path, table: str) -> list[tuple[str, tuple[object, ...]]]:
    """Every way to insert ``table``'s first row again, by its key, with a conflict clause."""
    with closing(sqlite3.connect(path)) as db:
        columns = [row[1] for row in db.execute(f"PRAGMA table_info({table})")]
        row = db.execute(f"SELECT * FROM {table} LIMIT 1").fetchone()
    names, marks = ", ".join(columns), ", ".join("?" * len(columns))
    last = columns[-1]
    return [
        (f"INSERT OR REPLACE INTO {table} ({names}) VALUES ({marks})", row),
        (f"REPLACE INTO {table} ({names}) VALUES ({marks})", row),
        (f"INSERT OR IGNORE INTO {table} ({names}) VALUES ({marks})", row),
        (f"INSERT INTO {table} ({names}) VALUES ({marks}) ON CONFLICT DO NOTHING", row),
        (f"INSERT INTO {table} ({names}) VALUES ({marks}) ON CONFLICT DO UPDATE SET {last} = excluded.{last}", row),
    ]


def test_v18_refuses_replace_upsert_and_silent_ignore_on_every_table(store):
    """MEDIUM-1: REPLACE and upserts delete without the delete trigger; OR IGNORE must not drop in silence."""
    at = night_before(TUESDAY)
    up = store.propose("ORB_V2", "0.25", "0.5", at=at)
    store.decide(up, "approved", actor=OWNER_TELEGRAM_ACTOR, at=at + timedelta(minutes=30))
    store.event("ORB_V2", "0.25", "0.5", proposal_id=up, effective=TUESDAY, at=at + timedelta(minutes=30))
    store.write(EVALUATION, ("ORB_V2", "2026-10-12", "proposed", "b" * 64, _stamp(at)))
    before = _rows(store.path)
    for table in size_schema.SIZE_STORE_TABLES:
        for sql, row in _conflicting_inserts(store.path, table):
            refusal = store.refused(sql, row)
            if table != size_schema.SIZE_EVENTS_TABLE:  # an event's own chain triggers may answer first
                assert "append-only" in refusal, (table, sql, refusal)
    assert _rows(store.path) == before
    # The keyed tables have no implicit rowid to replace a row through.
    with closing(sqlite3.connect(store.path)) as db:
        without_rowid = {name for _schema, name, _type, _cols, wr, _strict in db.execute("PRAGMA table_list") if wr}
    assert {size_schema.SIZE_PROPOSALS_TABLE, size_schema.SIZE_DECISIONS_TABLE} <= without_rowid
    with pytest.raises(sqlite3.DatabaseError):
        store.write(f"INSERT OR REPLACE INTO {size_schema.SIZE_PROPOSALS_TABLE} (rowid, proposal_id) VALUES (1, 'x')",
                    ())
    # No row can sit at the id an auto-assigned insert shows its triggers (-1).
    momo = store.propose("MOMO_HOD_V2", "1", "0.5", at=at)
    store.decide(momo, "auto_applied", actor="size_writer", at=at)
    params = store.event_params("MOMO_HOD_V2", "1", "0.5", proposal_id=momo, effective=WEDNESDAY, at=at)
    assert "CHECK" in store.refused(EVENT.replace("(identity,", "(id, identity,").replace("VALUES (", "VALUES (-1, "),
                                    params)
    # Ordinary appends still work, after all those refusals.
    store.step("GAP_GO_V2", "0.5", "0.25", effective=WEDNESDAY, at=at)
    store.write(EVALUATION, ("ORB_V2", "2026-10-13", "held", "c" * 64, _stamp(at)))
    after = _rows(store.path)
    assert {table: len(rows) for table, rows in after.items()} == {
        size_schema.SIZE_PROPOSALS_TABLE: 3, size_schema.SIZE_DECISIONS_TABLE: 3,
        size_schema.SIZE_EVENTS_TABLE: 2, size_schema.SIZE_EVALUATIONS_TABLE: 2,
    }


def test_v18_review_repros_are_refused(store, sizing_log):
    """N19a's three reproductions on a migrated v18 database, each refused."""
    at = night_before(TUESDAY)
    decision = f"INSERT {{}} INTO {size_schema.SIZE_DECISIONS_TABLE} (proposal_id, decision, actor, at) VALUES (?, ?, ?, ?)"
    # [04]/[05] an owner rejection rewritten to an approval.
    vwap = store.propose("VWAP_PB_V2", "0.5", "1", at=at)
    store.decide(vwap, "rejected", actor=OWNER_TELEGRAM_ACTOR, at=at + timedelta(minutes=5))
    approve = (vwap, "approved", OWNER_TELEGRAM_ACTOR, _stamp(at + timedelta(minutes=10)))
    for sql in (decision.format("OR REPLACE"), decision.replace("INSERT {} INTO", "REPLACE INTO"),
                decision.format("") + " ON CONFLICT(proposal_id) DO UPDATE SET decision = excluded.decision"):
        assert "size decisions are append-only" in store.refused(sql, approve), sql
    with closing(sqlite3.connect(store.path)) as db:
        assert db.execute("SELECT state FROM autotrader_size_proposal_state WHERE proposal_id = ?",
                          (vwap,)).fetchone() == ("rejected",)
    params = store.event_params("VWAP_PB_V2", "0.5", "1", proposal_id=vwap, effective=TUESDAY, at=at)
    assert SIZE_STEP_UP_NEEDS_OWNER_APPROVAL in store.refused(EVENT, params)

    # [06] a MOMO step down erased: replaced, through its id, by an approved ORB step up.
    erased = store.step("MOMO_HOD_V2", "1", "0.5", effective=TUESDAY, at=at)
    orb = store.propose("ORB_V2", "0.25", "0.5", at=at)
    store.decide(orb, "approved", actor=OWNER_TELEGRAM_ACTOR, at=at + timedelta(minutes=5))
    columns = "(id, identity, from_mult, to_mult, effective_session, kind, proposal_id, created_at, account_mode)"
    row = (erased, "ORB_V2", "0.25", "0.5", "2026-10-14", STEP_UP, orb, _stamp(at + timedelta(minutes=6)), "paper")
    for verb in ("INSERT OR REPLACE", "REPLACE"):
        sql = f"{verb} INTO {size_schema.SIZE_EVENTS_TABLE} {columns} VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
        assert "size events are append-only" in store.refused(sql, row), verb
    assert read(store.path, "MOMO_HOD_V2", WEDNESDAY) == Decimal("0.5")  # never x1 again by deletion

    # [07] ORB's approval rewritten into a MOMO 0.5 -> 1 step up.
    rewrite = (orb, "2026-10-12", "MOMO_HOD_V2", STEP_UP, "0.5", "1", "a" * 64,
               _stamp(at + timedelta(hours=2)), _stamp(at))
    for verb in ("INSERT OR REPLACE", "REPLACE"):
        sql = PROPOSAL.replace("INSERT INTO", f"{verb} INTO")
        assert "size proposals are append-only" in store.refused(sql, rewrite), verb
    momo_up = store.event_params("MOMO_HOD_V2", "0.5", "1", proposal_id=orb, effective=WEDNESDAY, at=at)
    assert SIZE_STEP_UP_NEEDS_OWNER_APPROVAL in store.refused(EVENT, momo_up)

    # [09] an evaluation rewritten.
    store.write(EVALUATION, ("ORB_V2", "2026-10-12", "proposed", "b" * 64, _stamp(at)))
    sql = EVALUATION.replace("INSERT INTO", "INSERT OR REPLACE INTO")
    assert "size evaluations are append-only" in store.refused(sql, ("ORB_V2", "2026-10-12", "held", "c" * 64,
                                                                     _stamp(at)))
    assert criticals(sizing_log) == []


def test_v18_an_approval_raises_only_its_own_rung(store):
    """LOW-1: the step triggers match the proposal's from and to, not only its identity."""
    at = night_before(TUESDAY)
    store.step("ORB_V2", "0.25", "0.5", effective=TUESDAY, at=at)  # ORB_V2 now x0.5
    stale = store.propose("ORB_V2", "0.25", "0.5", at=at)  # an approved step, never used
    store.decide(stale, "approved", actor=OWNER_TELEGRAM_ACTOR, at=at + timedelta(minutes=5))
    higher = store.event_params("ORB_V2", "0.5", "1", proposal_id=stale, effective=WEDNESDAY,
                                at=at + timedelta(minutes=10))
    assert SIZE_STEP_UP_NEEDS_OWNER_APPROVAL in store.refused(EVENT, higher)
    # The step-down twin: an automatic 1 -> 0.5 never lowers 0.5 -> 0.25.
    store.step("MOMO_HOD_V2", "1", "0.5", effective=TUESDAY, at=at)
    old = store.propose("MOMO_HOD_V2", "1", "0.5", at=at)
    store.decide(old, "auto_applied", actor="size_writer", at=at)
    lower = store.event_params("MOMO_HOD_V2", "0.5", "0.25", proposal_id=old, effective=WEDNESDAY,
                               at=at + timedelta(minutes=10))
    assert SIZE_STEP_DOWN_NEEDS_AUTO_APPLIED in store.refused(EVENT, lower)


BAD_STAMPS = (
    "2026-02-30T10:00:00+00:00",  # no such day (SQLite would normalise it to March 2)
    "2026-10-22T24:00:00+00:00",  # hour 24
    "2026-10-22T10:60:00+00:00",
    "2026-10-22T10:00:00.1+00:00",  # not the six-digit fraction Python writes
    "1999-12-31T10:00:00+00:00",
    "0000-01-01T00:00:00+00:00",
    "2026-10-22T10:00:00Z",
    "2026-10-22T10:00:00-04:00",
    "2026-10-22 10:00:00+00:00",
)


def test_v18_refuses_stamps_and_dates_python_cannot_read(store):
    """LOW-6: no row the reader cannot parse, so no identity is ever blocked for good by one."""
    at = night_before(TUESDAY)
    good = store.propose("ORB_V2", "0.25", "0.5", at=at)
    store.decide(good, "approved", actor=OWNER_TELEGRAM_ACTOR, at=at + timedelta(minutes=5))
    for bad in BAD_STAMPS:
        proposal = ("e" * 10, "2026-10-12", "GAP_GO_V2", STEP_DOWN, "0.5", "0.25", "a" * 64, None, bad)
        assert "CHECK" in store.refused(PROPOSAL, proposal), bad
        deadline = ("e" * 10, "2026-10-12", "GAP_GO_V2", STEP_UP, "0.5", "1", "a" * 64, bad, _stamp(at))
        assert "CHECK" in store.refused(PROPOSAL, deadline), bad
        assert "CHECK" in store.refused(EVALUATION, ("GAP_GO_V2", "2026-10-12", "held", "b" * 64, bad)), bad
        event = ("ORB_V2", "0.25", "0.5", "2026-10-14", STEP_UP, good, bad, "paper")
        assert "CHECK" in store.refused(EVENT, event), bad
    vwap = store.propose("VWAP_PB_V2", "0.5", "1", at=at)
    for bad in BAD_STAMPS:
        assert "CHECK" in store.refused(DECISION, (vwap, "rejected", OWNER_TELEGRAM_ACTOR, bad)), bad
    for day in ("2026-02-30", "1999-12-31", "2026-10-17", "2026-10-18", "20261014"):
        event = ("ORB_V2", "0.25", "0.5", day, STEP_UP, good, _stamp(at), "paper")
        assert "CHECK" in store.refused(EVENT, event), day
    assert "CHECK" in store.refused(EVALUATION, ("GAP_GO_V2", "1999-12-31", "held", "b" * 64, _stamp(at)))
    # What the store accepts, Python reads: whole seconds and six-digit fractions.
    store.event("ORB_V2", "0.25", "0.5", proposal_id=good, effective=TUESDAY, at=at + timedelta(microseconds=7))
    assert read(store.path, "ORB_V2", TUESDAY) == Decimal("0.5")


def test_one_step_per_session_start_not_per_date(store, sizing_log):
    """LOW-5: two events with no session start between them never both apply at one start."""
    friday, saturday = date(2026, 10, 16), date(2026, 10, 17)
    vwap = store.propose("VWAP_PB_V2", "0.5", "1", at=et(friday, 20, 0))
    store.decide(vwap, "approved", actor=OWNER_TELEGRAM_ACTOR, at=et(friday, 20, 30))
    weekend = store.event_params("VWAP_PB_V2", "0.5", "1", proposal_id=vwap, effective=saturday,
                                 at=et(friday, 20, 30))
    assert "CHECK" in store.refused(EVENT, weekend)  # a weekend date is never a session
    # A holiday is a weekday: alone, its event takes effect at the next session's start ...
    thanksgiving, black_friday = date(2026, 11, 26), date(2026, 11, 27)
    store.step("GAP_GO_V2", "0.5", "0.25", effective=thanksgiving, at=night_before(thanksgiving))
    assert read(store.path, "GAP_GO_V2", date(2026, 11, 25)) == Decimal("0.5")
    assert read(store.path, "GAP_GO_V2", black_friday) == Decimal("0.25")
    assert criticals(sizing_log) == []
    # ... and a second event for that same start is two rungs at one start: refused.
    store.step("GAP_GO_V2", "0.25", "0.5", effective=black_friday, at=night_before(black_friday))
    assert read(store.path, "GAP_GO_V2", black_friday) is None
    assert read(store.path, "GAP_GO_V2", date(2026, 11, 30)) is None
    assert criticals(sizing_log) == [SIZE_EVENT_CHAIN_BROKEN] * 2
    assert "one step per session" in [r for r in sizing_log.records if r.levelno == logging.CRITICAL][-1].getMessage()


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


def test_the_reader_refuses_a_step_against_its_kind(store, sizing_log):
    """LOW-2: a raise labelled step_down, written past the CHECKs (any writer can switch them off)."""
    at = night_before(TUESDAY)
    raise_ = store.propose("VWAP_PB_V2", "0.5", "1", at=at, kind=STEP_DOWN, ignore_checks=True)
    store.decide(raise_, "auto_applied", actor="size_writer", at=at)
    store.event("VWAP_PB_V2", "0.5", "1", proposal_id=raise_, effective=TUESDAY, at=at, kind=STEP_DOWN,
                ignore_checks=True)
    assert read(store.path, "VWAP_PB_V2", TUESDAY) is None
    assert criticals(sizing_log) == [SIZE_EVENT_CHAIN_BROKEN]


def test_the_reader_refuses_events_out_of_session_order(store, sizing_log):
    """LOW-2: the reader keeps the order rule itself, without the database's trigger."""
    with closing(sqlite3.connect(store.path)) as db:
        db.execute("DROP TRIGGER autotrader_size_events_after_previous")  # a damaged store, not v18's rules
        db.commit()
    at = night_before(TUESDAY)
    store.step("GAP_GO_V2", "0.5", "0.25", effective=WEDNESDAY, at=at)
    store.step("GAP_GO_V2", "0.25", "0.5", effective=TUESDAY, at=at)  # one rung on, but an earlier session
    assert read(store.path, "GAP_GO_V2", WEDNESDAY) is None
    assert read(store.path, "GAP_GO_V2", TUESDAY) is None
    assert criticals(sizing_log) == [SIZE_EVENT_CHAIN_BROKEN] * 2


def _rollback_journal_copy(store: SizeLedger, tmp_path: Path) -> Path:
    """The store in rollback-journal mode, where a writer's lock makes a reader busy."""
    copy = tmp_path / "busy.db"
    shutil.copy(store.path, copy)
    with closing(sqlite3.connect(copy)) as db:
        assert db.execute("PRAGMA journal_mode=DELETE").fetchone() == ("delete",)
    return copy


def test_a_busy_store_is_read_again_then_fails_closed(tmp_path, store, sizing_log, monkeypatch):
    """Open question 2 (adopted): only a transient SQLite failure is retried, three tries, never an exit."""
    assert (identity_sizing.SIZE_READ_ATTEMPTS, identity_sizing.SIZE_READ_RETRY_SECONDS) == (3, 1.0)
    busy = _rollback_journal_copy(store, tmp_path)
    holder = sqlite3.connect(busy, isolation_level=None)
    monkeypatch.setattr(identity_sizing, "SCHEMA_READ_TIMEOUT_SECONDS", 0.05)
    pauses: list[float] = []

    def pause_and_release(seconds: float) -> None:
        pauses.append(seconds)
        holder.execute("COMMIT")  # the writer finishes while the reader waits

    monkeypatch.setattr(identity_sizing, "_pause", pause_and_release)
    holder.execute("BEGIN EXCLUSIVE")
    assert read(busy, "ORB_V2", TUESDAY) == Decimal("0.25")
    assert pauses == [1.0] and criticals(sizing_log) == []
    assert [r.levelno for r in sizing_log.records].count(logging.WARNING) == 1

    # Held throughout: three tries, then None with the CRITICAL (no exit, no exception).
    pauses.clear()
    monkeypatch.setattr(identity_sizing, "_pause", pauses.append)
    holder.execute("BEGIN EXCLUSIVE")
    try:
        assert read(busy, "ORB_V2", TUESDAY) is None
    finally:
        holder.execute("COMMIT")
        holder.close()
    assert pauses == [1.0, 1.0]
    assert criticals(sizing_log) == [IDENTITY_SIZE_STORE_UNREADABLE]
    assert "attempt 3" in [r for r in sizing_log.records if r.levelno == logging.CRITICAL][-1].getMessage()

    # A deterministic failure is never retried.
    pauses.clear()
    dropped = tmp_path / "dropped.db"
    shutil.copy(store.path, dropped)
    with closing(sqlite3.connect(dropped)) as db:
        db.execute("DROP TABLE autotrader_size_events")
        db.commit()
    assert read(dropped, "ORB_V2", TUESDAY) is None
    assert pauses == []


async def test_an_exception_from_the_size_read_never_becomes_a_size(tmp_path, monkeypatch):
    """LOW-3: the composition root lets a failing read fail the run; it never substitutes a size."""
    from tests.test_shadow_service import _pipeline_keywords, _size_store
    from trading.operations import shadow_service

    await _size_store(tmp_path, monkeypatch)

    def crashing(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("size read crashed")

    monkeypatch.setattr(shadow_service, "session_identity_multiplier", crashing)
    captured: dict[str, object] = {}
    with pytest.raises(RuntimeError, match="size read crashed"):
        await _pipeline_keywords(monkeypatch, "orb-v2", captured=captured)
    assert "pipeline" not in captured and captured["spec_name"] == "ORB_V2"


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
    # Mid-session: a step written for today (a writer defect), and a step down for Thursday.
    # (Rewritten explicitly by integration fix 2: a step down for Wednesday would share
    # Wednesday's start with today's late step, two rungs at one start, and is refused:
    # test_a_mid_session_event_and_the_next_sessions_never_share_a_start.)
    thursday = date(2026, 10, 15)
    ledger.step("ORB_V2", "0.5", "1", effective=TUESDAY, at=et(TUESDAY, 10, 0))
    ledger.step("ORB_V2", "1", "0.5", effective=thursday, at=et(TUESDAY, 10, 30))
    restarted, _name = await _pipeline_keywords(monkeypatch, "orb-v2", at=et(TUESDAY, 11, 45))
    assert restarted["identity_multiplier"] == first["identity_multiplier"] == Decimal("0.5")
    assert criticals(sizing_log) == [SIZE_EVENT_WRITTEN_MID_SESSION]
    # Wednesday's start takes today's late step; Thursday's the step down: one rung per start.
    assert read(database, "ORB_V2", WEDNESDAY) == Decimal("1")
    assert read(database, "ORB_V2", thursday) == Decimal("0.5")
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


# --- integration fix 2: one read rule, the calendar's edge, one step per start ------


def test_every_reader_shares_one_public_read_rule():
    """2a: the session start, size_in_force and the research readers all apply
    ``size_from_events``; no module reaches into identity_sizing's private names."""
    for relative in ("trading/research/size_history.py", "trading/operations/session_check.py",
                     "trading/research/size_writer.py", "trading/research/promotion_proposals.py",
                     "trading/operations/size_notify.py"):
        tree = ast.parse((BACKEND / relative).read_text(encoding="utf-8"))
        private = [node.attr for node in ast.walk(tree)
                   if isinstance(node, ast.Attribute) and node.attr.startswith("_")
                   and isinstance(node.value, ast.Name) and node.value.id == "identity_sizing"]
        imported = [alias.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
                    and node.module == "trading.risk.identity_sizing" for alias in node.names
                    if alias.name.startswith("_")]
        assert not private and not imported, (relative, private, imported)
    source = Path(identity_sizing.__file__).read_text(encoding="utf-8")
    for caller in ("def session_identity_multiplier(", "def size_in_force("):
        body = source[source.index(caller):]
        ends = [index for index in (body.find("\ndef ", 1), body.find("\n__all__")) if index > 0]
        body = body[: min(ends)]
        assert "size_from_events(" in body and "_chain_problem(" not in body, caller
    history = (BACKEND / "trading/research/size_history.py").read_text(encoding="utf-8")
    assert "identity_sizing.size_from_events(" in history and "_chain_problem" not in history


def test_a_date_the_calendar_cannot_place_is_refused_never_raised(store, sizing_log):
    """N2-LOW-1: an event outside the session calendar's span (written past the
    database's horizon CHECK) is a broken chain: None and one CRITICAL, never a raise."""
    from trading.research.size_history import size_in_force_from_events

    at = night_before(TUESDAY)
    far = date(2028, 6, 1)  # beyond the pinned calendar's last session
    pid = store.propose("GAP_GO_V2", "0.5", "0.25", at=at)
    store.decide(pid, "auto_applied", actor="size_writer", at=at)
    store.event("GAP_GO_V2", "0.5", "0.25", proposal_id=pid, effective=far, at=at, ignore_checks=True)
    assert read(store.path, "GAP_GO_V2", WEDNESDAY) is None
    assert criticals(sizing_log) == [SIZE_EVENT_CHAIN_BROKEN]
    assert "cannot be placed on the session calendar" in sizing_log.records[-1].getMessage()
    with pytest.raises(identity_sizing.SizeNotInForce) as refused:
        identity_sizing.size_in_force(store.path, "GAP_GO_V2", WEDNESDAY)
    assert refused.value.token == SIZE_EVENT_CHAIN_BROKEN
    events = identity_sizing.read_size_events(store.path, identity="GAP_GO_V2", account_mode="paper")
    research = size_in_force_from_events(events, identity="GAP_GO_V2", session_date=WEDNESDAY,
                                         starts_at=start(WEDNESDAY))
    assert research.factor is None and research.source.startswith(SIZE_EVENT_CHAIN_BROKEN)


def test_v18_refuses_an_event_beyond_the_horizon(store):
    """N2-LOW-1 at the database: effective at most 14 days after it was written (NEW.created_at only)."""
    assert size_schema.SIZE_EVENT_HORIZON_DAYS == 14
    at = night_before(TUESDAY)  # written 2026-10-13 00:00 UTC
    beyond = _isolated_event_refusal(store, "MOMO_HOD_V2", "1", "0.5", kind=STEP_DOWN,
                                     effective=date(2026, 10, 28), at=at)
    assert "CHECK" in beyond
    store.step("MOMO_HOD_V2", "1", "0.5", effective=date(2026, 10, 27), at=at - timedelta(hours=1))
    assert read(store.path, "MOMO_HOD_V2", date(2026, 10, 27)) == Decimal("0.5")


def test_a_mid_session_event_and_the_next_sessions_never_share_a_start(store, sizing_log):
    """N2-LOW-2 (the review's repro): ORB x0.25 -> x0.5 written Friday 10:30 ET for
    Friday (ignored Friday, so it takes effect Monday) and x0.5 -> x1 for Monday
    would both apply at Monday's one start: refused, never x1 by two rungs."""
    friday, monday = date(2026, 10, 16), date(2026, 10, 19)
    store.step("ORB_V2", "0.25", "0.5", effective=friday, at=et(friday, 9, 30))
    store.step("ORB_V2", "0.5", "1", effective=monday, at=et(friday, 19, 0))
    assert read(store.path, "ORB_V2", monday) is None
    assert criticals(sizing_log) == [SIZE_EVENT_CHAIN_BROKEN]
    assert "one step per session" in sizing_log.records[-1].getMessage()
    with pytest.raises(identity_sizing.SizeNotInForce):
        identity_sizing.size_in_force(store.path, "ORB_V2", monday)
    # Alone, the mid-session event takes effect at the next start, one rung.
    lone = SizeLedger(migrate_database(store.path.parent / "lone.db"))
    lone.step("ORB_V2", "0.25", "0.5", effective=friday, at=et(friday, 9, 30))
    assert read(lone.path, "ORB_V2", monday) == Decimal("0.5")
    # The boundary, as the session read counts it: written exactly at Friday's start is
    # after it (Monday's start, beside Monday's own step: refused); a microsecond
    # earlier is before it (Friday's start, then Monday's: one rung each).
    edge = SizeLedger(migrate_database(store.path.parent / "edge.db"))
    edge.step("ORB_V2", "0.25", "0.5", effective=friday, at=start(friday) - timedelta(hours=1))
    edge.step("ORB_V2", "0.5", "1", effective=monday, at=et(friday, 19, 0))
    assert read(edge.path, "ORB_V2", monday) is None
    early = SizeLedger(migrate_database(store.path.parent / "early.db"))
    early.step("ORB_V2", "0.25", "0.5", effective=friday, at=start(friday) - timedelta(hours=1, microseconds=1))
    early.step("ORB_V2", "0.5", "1", effective=monday, at=et(friday, 19, 0))
    assert read(early.path, "ORB_V2", friday) == Decimal("0.5")
    assert read(early.path, "ORB_V2", monday) == Decimal("1")


def test_replace_through_an_events_proposal_id_is_refused(store):
    """N2-LOW-3 F01: the events no-replace key covers proposal_id, not only id."""
    at = night_before(TUESDAY)
    store.step("MOMO_HOD_V2", "1", "0.5", effective=TUESDAY, at=at)
    store.step("MOMO_HOD_V2", "0.5", "1", effective=WEDNESDAY, at=at)
    with closing(sqlite3.connect(store.path)) as db:
        (first_proposal,) = db.execute("SELECT proposal_id FROM autotrader_size_events ORDER BY id LIMIT 1").fetchone()
    before = _rows(store.path)
    # A new id, the first event's proposal: every other rule passes (from x1, the size in force, at a later session).
    row = (99, "MOMO_HOD_V2", "1", "0.5", "2026-10-15", STEP_DOWN, first_proposal,
           _stamp(et(WEDNESDAY, 20, 0)), "paper")
    columns = "(id, identity, from_mult, to_mult, effective_session, kind, proposal_id, created_at, account_mode)"
    for verb in ("INSERT OR REPLACE", "REPLACE"):
        sql = f"{verb} INTO {size_schema.SIZE_EVENTS_TABLE} {columns} VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
        assert "size events are append-only" in store.refused(sql, row), verb
    assert _rows(store.path) == before


@pytest.mark.parametrize(
    ("code", "retried"),
    [
        (sqlite3.SQLITE_BUSY, True),
        (261, True),  # SQLITE_BUSY_RECOVERY
        (517, True),  # SQLITE_BUSY_SNAPSHOT (WAL)
        (262, True),  # SQLITE_LOCKED_SHAREDCACHE
        (266, True),  # SQLITE_IOERR_READ
        (3850, True),  # SQLITE_IOERR_LOCK
        (270, True),  # SQLITE_CANTOPEN_NOTEMPDIR
        (sqlite3.SQLITE_ERROR, False),
        (257, False),  # SQLITE_ERROR_MISSING_COLLSEQ
        (8, False),  # SQLITE_READONLY
    ],
)
def test_extended_sqlite_codes_map_to_their_primary_for_the_retry(store, monkeypatch, code, retried):
    """N2-LOW-3 F09: in WAL production the transient codes come extended."""
    real_connect = sqlite3.connect
    calls: list[int] = []
    pauses: list[float] = []

    def connect(*args: object, **kwargs: object):
        calls.append(1)
        if len(calls) == 1:
            failure = sqlite3.OperationalError(f"fixture: result code {code}")
            failure.sqlite_errorcode = code  # type: ignore[attr-defined]
            raise failure
        return real_connect(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(identity_sizing, "_pause", pauses.append)
    monkeypatch.setattr(identity_sizing.sqlite3, "connect", connect)
    if retried:
        assert identity_sizing.read_size_events(store.path, identity="ORB_V2", account_mode="paper") == ()
        assert (len(calls), pauses) == (2, [1.0])
    else:
        with pytest.raises(identity_sizing.SizeStoreUnreadable):
            identity_sizing.read_size_events(store.path, identity="ORB_V2", account_mode="paper")
        assert (len(calls), pauses) == (1, [])


async def test_the_writers_real_clock_stamps_pass_every_v18_rule_end_to_end(tmp_path, monkeypatch, sizing_log):
    """2f: P3-E's nightly writer with its real clock (no patch) on a v18 store: its rows
    pass the exact-UTC, weekday and horizon CHECKs, and the next session's composition
    root reads the step down into the pipeline."""
    import asyncio

    from tests.test_shadow_service import _pipeline_keywords, _size_store
    from tests.test_size_writer import verdicts
    from trading.research import size_writer

    database = await _size_store(tmp_path, monkeypatch)
    now = datetime.now(timezone.utc)
    plan = size_writer.plan_night(now)
    proposals = verdicts(database, plan, MOMO_HOD_V2="losing")
    result = await asyncio.to_thread(
        size_writer.write_night, database=database, proposals=proposals, plan=plan, fence_store=None
    )
    assert [(change.identity, change.kind, change.to_mult) for change in result.changes] == [
        ("MOMO_HOD_V2", "step_down", "0.5")
    ]
    with closing(sqlite3.connect(database)) as db:
        stamps = [row[0] for table in ("autotrader_size_proposals", "autotrader_size_events",
                                       "autotrader_size_evaluations")
                  for row in db.execute(f"SELECT created_at FROM {table}")]
        stamps += [row[0] for row in db.execute("SELECT at FROM autotrader_size_decisions")]
        ((effective,),) = db.execute("SELECT effective_session FROM autotrader_size_events").fetchall()
    for stamp in stamps:
        moment = datetime.fromisoformat(stamp)
        assert moment.utcoffset() == timedelta(0) and abs(moment - now) < timedelta(minutes=10), stamp
    assert date.fromisoformat(effective) == plan.session and market_session(plan.session) is not None
    keywords, name = await _pipeline_keywords(monkeypatch, "momo", day=plan.session)
    assert name == "MOMO_HOD_V2" and keywords["identity_multiplier"] == Decimal("0.5")
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
# "Identity sizing" bullet once the owner approves it). Each stamps every
# created_at and decision ``at`` it writes from one real
# ``datetime.now(timezone.utc)`` read inside its writing transaction, never a
# caller's time: the mid-session guard and the back-dating and horizon CHECKs
# trust that stamp (N19a OQ3, N19a-2 I-2). The store assigns every id.
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
# The tables a write to which changes a size: only the two writers insert into
# them (D6; N19a LOW-4 adds the decisions, where an approval lives).
SIZE_WRITE_TABLES = (size_schema.SIZE_EVENTS_TABLE, size_schema.SIZE_DECISIONS_TABLE)
# Modules that may name those tables together with any write word: the schema
# (DDL), the reader and the two writers. Another module that does is a writer
# until a review adds it here.
SIZE_WRITE_TABLE_NAMERS = frozenset({
    "trading/storage/size_schema.py", "trading/risk/identity_sizing.py", *SIZE_EVENT_WRITERS,
})
# The one module that may record an owner approval.
APPROVAL_WRITER = "trading/operations/telegram_size_approval.py"
_WRITE_TABLE_CONSTANTS = frozenset({"SIZE_EVENTS_TABLE", "SIZE_DECISIONS_TABLE", "SIZE_STORE_TABLES"})
_COMMENTS = re.compile(r"/\*.*?\*/|--[^\n]*", re.DOTALL)
_PERCENT_FIELD = re.compile(r"%(?:\([^)]*\))?[-#0 +]*\d*(?:\.\d+)?[sdifrxXeEgGc]")
_BRACE_FIELD = re.compile(r"\{[^{}]*\}")
_INSERT_INTO = re.compile(
    r"\b(?:INSERT|REPLACE)\b(?:\s+OR\s+\w+)?\s+INTO\s+(?:[\w\"`\[\]]+\.)?[\"`\[]?(?P<table>\w+)", re.IGNORECASE
)
_CONFLICT_CLAUSE = re.compile(r"\bINSERT\s+OR\s+(?:REPLACE|IGNORE)\b|\bREPLACE\s+INTO\b|\bON\s+CONFLICT\b", re.IGNORECASE)
_NAMES_WRITE_TABLE = re.compile(r"size_events|size_decisions|autotrader_size_\{\}", re.IGNORECASE)
_WRITE_WORD = re.compile(r"\b(?:INSERT|REPLACE|UPSERT)\b", re.IGNORECASE)
_WRITE_IDENTIFIER = re.compile(r"(?:^|_)(?:insert|replace|upsert|executescript|executemany)(?:_|$)", re.IGNORECASE)
_APPROVAL = re.compile(r"\bapproved\b|owner_telegram", re.IGNORECASE)
NIGHTLY_WRITER = "trading/research/size_writer.py"
# What each writer may insert (D2, D6; integration fix 2): the nightly writer
# records the automatic decisions and step-down events only; the owner's
# approval path records approvals, rejections and step-up events only.
WRITER_RIGHTS: dict[str, tuple[str, frozenset[str]]] = {
    NIGHTLY_WRITER: (STEP_DOWN, frozenset({"superseded", "expired", "auto_applied", "fenced", "would_fence"})),
    APPROVAL_WRITER: (STEP_UP, frozenset({"approved", "rejected"})),
}
# Words and constants a writer may not hold at all: the other writer's
# decisions that move a size or grant authority. (``expired`` and
# ``superseded`` stay usable by the approval path as refusal words for the
# owner; a literal one in its INSERT is still refused.)
_FOREIGN_WORDS = {
    NIGHTLY_WRITER: frozenset({"approved", "rejected", "owner_telegram"}),
    APPROVAL_WRITER: frozenset({"auto_applied", "fenced", "would_fence"}),
}
# Fragments of those words, for a writer that splits one across names the
# fold cannot follow (``A = "appro"; A + "ved"``; N19b-ES LOW-3).
_FOREIGN_FRAGMENTS = {
    NIGHTLY_WRITER: ("appro", "owner_tel", "reject"),
    APPROVAL_WRITER: ("auto_appl", "would_fen"),
}
# A statement on the events table a writer may hold: a SELECT, or an INSERT the
# rights check reads (its table written out). Anything else naming the table
# (a bare name to build a statement from) is refused (N19b-ES LOW-3).
_EVENTS_TABLE_TEXT = re.compile(r"size_events", re.IGNORECASE)
_READABLE_EVENTS_STATEMENT = re.compile(
    r"^(?:SELECT\b|INSERT\s+INTO\s+autotrader_size_events\b)", re.IGNORECASE
)
_RUNTIME_TABLE_INSERT = re.compile(r"\b(?:INSERT|REPLACE)\b(?:\s+OR\s+\w+)?\s+INTO\s+[^\w\s]", re.IGNORECASE)
_FOREIGN_CONSTANTS = {
    NIGHTLY_WRITER: frozenset({"OWNER_TELEGRAM_ACTOR", "STEP_UP_DECISIONS"}),
    APPROVAL_WRITER: frozenset({"STEP_DOWN_DECISIONS", "SHADOW_ONLY_DECISIONS"}),
}
_INSERT_STATEMENT = re.compile(
    r"\bINSERT\s+INTO\s+(?P<table>\w+)\s*(?:\((?P<columns>[^)]*)\))?\s*(?:VALUES\s*\((?P<values>[^)]*)\))?",
    re.IGNORECASE,
)


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


def _constant(node: ast.AST) -> object:
    """A literal operand (str, int, float, or a tuple of those), else ``_UNKNOWN``."""
    if isinstance(node, ast.Constant) and isinstance(node.value, (str, int, float)):
        return node.value
    if isinstance(node, ast.Tuple):
        values = tuple(_constant(item) for item in node.elts)
        return _UNKNOWN if _UNKNOWN in values else values
    return _UNKNOWN


_UNKNOWN = object()


# The ``str`` methods ``_fold`` evaluates on a folded base with literal
# arguments (N19b-ES LOW-3: ``"appro".strip() + "ved"`` is ``approved``).
_STR_METHODS = frozenset({
    "capitalize", "casefold", "center", "expandtabs", "ljust", "lower", "lstrip", "removeprefix", "removesuffix",
    "replace", "rjust", "rstrip", "strip", "swapcase", "title", "upper", "zfill",
})


def _fold(node: ast.AST) -> str | None:
    """A string expression's text, as far as the source fixes it ("{}" for the rest).

    Constants, f-strings, ``+``, ``*`` by a literal count, ``%`` formatting,
    ``.format``, ``.join``, ``str()``, a literal index or slice and the plain
    ``str`` methods (``_STR_METHODS``) with literal arguments: the ways a
    statement, a table name or a decision word can be split across literals
    (N19a LOW-4: ``"INSERT INTO autotrader_%s" % "size_events"`` folds whole;
    N19b-ES LOW-3: ``"appro".strip() + "ved"`` folds to ``approved``).
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
        for text_node, count_node in ((node.left, node.right), (node.right, node.left)):
            text, count = _fold(text_node), _constant(count_node)
            if text is not None and isinstance(count, int) and not isinstance(count, bool) and 0 <= count <= 64:
                return text * count
        return None
    if isinstance(node, ast.Subscript):
        base = _fold(node.value)
        if base is None:
            return None
        index = node.slice
        if isinstance(index, ast.Slice):
            bounds = [None if part is None else _constant(part) for part in (index.lower, index.upper, index.step)]
            if all(part is None or (isinstance(part, int) and not isinstance(part, bool)) for part in bounds):
                try:
                    return base[slice(*bounds)]
                except ValueError:
                    return base
            return base
        position = _constant(index)
        if isinstance(position, int) and not isinstance(position, bool):
            try:
                return base[position]
            except IndexError:
                return base
        return base
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "str" and len(node.args) == 1:
        return _fold(node.args[0])
    if isinstance(node, ast.JoinedStr):
        return "".join(part.value if isinstance(part, ast.Constant) else "{}" for part in node.values)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _fold(node.left), _fold(node.right)
        if left is None and right is None:
            return None
        return ("{}" if left is None else left) + ("{}" if right is None else right)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
        template = _fold(node.left)
        if template is None:
            return None
        operand = _constant(node.right)
        if operand is not _UNKNOWN:
            try:
                return template % operand
            except (TypeError, ValueError, KeyError):
                pass
        return _PERCENT_FIELD.sub("{}", template)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        base = _fold(node.func.value)
        if base is not None and node.func.attr == "format":
            arguments = [_constant(item) for item in node.args]
            keywords = {item.arg: _constant(item.value) for item in node.keywords if item.arg}
            if _UNKNOWN not in arguments and _UNKNOWN not in keywords.values() and len(keywords) == len(node.keywords):
                try:
                    return base.format(*arguments, **keywords)
                except (IndexError, KeyError, ValueError, AttributeError):
                    pass
            return _BRACE_FIELD.sub("{}", base)
        if base is not None and node.func.attr == "join" and len(node.args) == 1:
            items = node.args[0]
            if isinstance(items, (ast.List, ast.Tuple)):
                return base.join("{}" if (part := _fold(item)) is None else part for item in items.elts)
        if base is not None and node.func.attr in _STR_METHODS:
            arguments = [_constant(item) for item in node.args]
            if not node.keywords and _UNKNOWN not in arguments:
                try:
                    folded = getattr(base, node.func.attr)(*arguments)
                except (TypeError, ValueError):
                    folded = None
                if isinstance(folded, str):
                    return folded
            return base  # arguments not fixed by the source: the literal itself, unchanged
    return None


def _sql(text: str) -> str:
    """``text`` without SQL comments, its whitespace collapsed (``INSERT /* x */ INTO``)."""
    return " ".join(_COMMENTS.sub(" ", text).split())


def _identifiers(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.alias):
            names.add(node.name.rsplit(".", 1)[-1])
            if node.asname:
                names.add(node.asname)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
    return names


def size_store_violations(source: str, relative: str) -> list[str]:
    """Every way the module at ``relative`` could write a size or an approval outside D6.

    1. An INSERT/REPLACE INTO the events or decisions table, outside the two writers.
    2. Outside the reviewed namers: naming either table (its literal, a split of
       it, or its constant) together with any write word, in a string or an
       identifier (a generic ``insert(db, table, row)`` helper called with the
       table's name: N19a M18).
    3. A conflict clause (``INSERT OR REPLACE|IGNORE``, ``REPLACE INTO``,
       ``ON CONFLICT``) on a size table anywhere, and any conflict clause in a
       writer: they must check, then insert.
    4. An owner approval (``approved``, ``owner_telegram``) recorded anywhere but
       ``telegram_size_approval.py``.
    5. A writer beyond its rights (``WRITER_RIGHTS``): every INSERT into the
       events table names its kind, written out as the writer's own; every
       INSERT into the decisions table names its decision, a placeholder or
       one of the writer's own; and the writer holds none of the other's words.
    Docstrings, comments and trigger timing (``BEFORE INSERT ON``) are not writes.
    The scan is a tripwire, not a proof: code that builds its SQL at run time
    from data (an environment variable, ``chr`` codes) is beyond any static
    reading. The database's own triggers hold whatever writes.
    """
    tree = ast.parse(source)
    skip = _docstrings(tree)
    texts = [_sql(text) for node in ast.walk(tree) if id(node) not in skip and (text := _fold(node)) is not None]
    identifiers = _identifiers(tree)
    violations: list[str] = []
    inserts = sorted({
        match.group("table").lower()
        for text in texts for match in _INSERT_INTO.finditer(text)
        if match.group("table").lower() in SIZE_WRITE_TABLES
    })
    if inserts and relative not in SIZE_EVENT_WRITERS:
        violations.append(f"inserts into {', '.join(inserts)}")
    names_table = any(_NAMES_WRITE_TABLE.search(text) for text in texts) or bool(identifiers & _WRITE_TABLE_CONSTANTS)
    write_word = any(_WRITE_WORD.search(text) for text in texts) or any(
        _WRITE_IDENTIFIER.search(name) for name in identifiers
    )
    if names_table and write_word and relative not in SIZE_WRITE_TABLE_NAMERS:
        violations.append("names a size write table beside a write word, outside the reviewed modules")
    for text in texts:
        if _CONFLICT_CLAUSE.search(text) and ("autotrader_size_" in text.lower() or relative in SIZE_EVENT_WRITERS):
            violations.append(f"conflict clause on a size table: {text[:80]!r}")
    if relative in SIZE_WRITE_TABLE_NAMERS - {APPROVAL_WRITER, "trading/storage/size_schema.py"} and (
        any(_APPROVAL.search(text) for text in texts) or "OWNER_TELEGRAM_ACTOR" in identifiers
    ):
        violations.append("records an owner approval outside telegram_size_approval.py")
    if relative in WRITER_RIGHTS:
        violations += _beyond_rights(texts, identifiers, relative)
    return violations


def _beyond_rights(texts: list[str], identifiers: set[str], relative: str) -> list[str]:
    kind, decisions = WRITER_RIGHTS[relative]
    found: list[str] = []
    for text in texts:
        for match in _INSERT_STATEMENT.finditer(text):
            table = match.group("table").lower()
            if table not in SIZE_WRITE_TABLES:
                continue
            column, allowed = ("kind", {kind}) if table == size_schema.SIZE_EVENTS_TABLE else ("decision", decisions)
            columns = [item.strip().lower() for item in (match.group("columns") or "").split(",") if item.strip()]
            values = [item.strip() for item in match.group("values").split(",")] if match.group("values") else []
            if column not in columns or len(columns) != len(values):
                found.append(f"an INSERT INTO {table} whose {column} cannot be read: {text[:80]!r}")
                continue
            if table == size_schema.SIZE_EVENTS_TABLE and "id" in columns:
                found.append(f"an event's id is the store's to assign (N19a-2 I-1): {text[:80]!r}")
            value = values[columns.index(column)]
            if value == "?" and column == "kind":
                found.append(f"an event's kind must be written out as '{kind}': {text[:80]!r}")
            elif value != "?" and value.strip("'\"") not in allowed:
                found.append(f"inserts {column} {value}; may insert only {sorted(allowed)}")
    for word in sorted(_FOREIGN_WORDS[relative]):
        if any(text == word or f"'{word}'" in text for text in texts):
            found.append(f"holds {word!r}, the other writer's word")
    for fragment in _FOREIGN_FRAGMENTS[relative]:
        if any(fragment in text.lower() for text in texts):
            found.append(f"holds {fragment!r}, a piece of the other writer's words")
    for text in texts:
        if _RUNTIME_TABLE_INSERT.search(text):
            found.append(f"an INSERT whose table is not written out: {text[:80]!r}")
        if _EVENTS_TABLE_TEXT.search(text) and not _READABLE_EVENTS_STATEMENT.match(text):
            found.append(f"names the events table outside a SELECT or its own INSERT: {text[:80]!r}")
    for name in sorted(_FOREIGN_CONSTANTS[relative] & identifiers):
        found.append(f"names {name}, the other writer's constant")
    return found


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
    violations = {
        rel: found
        for path in sources
        if (found := size_store_violations(path.read_text(encoding="utf-8"), rel := path.relative_to(BACKEND).as_posix()))
    }
    assert not violations, violations
    # No SQL file outside the tests touches the write tables either.
    sql = _production_files(".sql")
    assert not [path for path in sql if _NAMES_WRITE_TABLE.search(path.read_text(encoding="utf-8"))]


ROGUE = "trading/research/rogue_sizes.py"
WRITER = "trading/research/size_writer.py"


@pytest.mark.parametrize(
    ("relative", "source", "flagged"),
    [
        (ROGUE, 'X = "INSERT INTO autotrader_size_events (identity) VALUES (?)"\n', True),
        (ROGUE, 'X = """insert or ignore into\n  main."autotrader_size_events" values (?)"""\n', True),
        (ROGUE, 'X = "REPLACE INTO autotrader_size_events VALUES (?)"\n', True),
        (ROGUE, 'X = "INSERT INTO " + "autotrader_size_events VALUES (?)"\n', True),
        (ROGUE, 'X = "INSERT INTO autotrader_%s (identity) VALUES (?)" % "size_events"\n', True),  # M16
        (ROGUE, 'X = "INSERT /* step */ INTO autotrader_size_events (identity) VALUES (?)"\n', True),  # M17
        (ROGUE, 'X = "INSERT -- why\\n INTO autotrader_size_events VALUES (?)"\n', True),
        (ROGUE, 'X = "INSERT\\n\\t INTO\\n autotrader_size_events VALUES (?)"\n', True),
        (ROGUE, 'X = "INSERT INTO autotrader_{} (x) VALUES (?)".format("size_events")\n', True),
        (ROGUE, 'X = " ".join(["INSERT", "INTO", "autotrader_size_events", "VALUES (?)"])\n', True),
        (ROGUE, 'db.executescript("BEGIN; INSERT INTO autotrader_size_decisions VALUES (1); COMMIT;")\n', True),
        (ROGUE, 'X = "INSERT INTO autotrader_size_decisions VALUES (?)"\n', True),
        (ROGUE, 'from trading.storage.rogue_helper import insert\n'
                'insert(db, "autotrader_size_events", {"identity": "ORB_V2"})\n', True),  # M18
        (ROGUE, 'T = "autotrader_size_" + kind\ndb.execute(f"INSERT INTO {T} VALUES (?)", row)\n', True),
        (ROGUE, 'from trading.storage.size_schema import SIZE_EVENTS_TABLE\nX = f"INSERT INTO {SIZE_EVENTS_TABLE} (x)"\n',
         True),
        (ROGUE, 'from trading.storage import size_schema as s\nX = "INSERT INTO %s VALUES (?)" % s.SIZE_EVENTS_TABLE\n',
         True),
        (ROGUE, 'def insert(db, table, row):\n    db.execute(f"INSERT INTO {table} VALUES (?)", row)\n', False),
        (ROGUE, 'X = "SELECT to_mult FROM autotrader_size_events WHERE identity = ?"\n', False),
        (ROGUE, '"""Never insert a size event into autotrader_size_events here."""\n', False),
        (ROGUE, 'X = "INSERT INTO autotrader_size_proposals VALUES (?)"\n', False),
        ("trading/storage/size_schema.py", 'X = "BEFORE INSERT ON autotrader_size_events BEGIN SELECT 1; END"\n', False),
        ("trading/risk/identity_sizing.py",
         'from trading.storage.size_schema import SIZE_EVENTS_TABLE\nX = f"SELECT * FROM {SIZE_EVENTS_TABLE}"\n', False),
        (WRITER, 'X = "INSERT INTO autotrader_size_events (identity) VALUES (?)"\n', True),  # its kind unwritten
        (WRITER, "X = \"INSERT INTO autotrader_size_decisions VALUES (?, 'approved', ?, ?)\"\n", True),
        (APPROVAL_WRITER,
         "X = \"INSERT INTO autotrader_size_decisions (proposal_id, decision, actor, at) VALUES (?, 'approved', ?, ?)\"\n",
         False),
        (WRITER, 'X = "INSERT OR IGNORE INTO autotrader_size_events (identity) VALUES (?)"\n', True),
        (WRITER, 'X = "INSERT INTO autotrader_size_events (identity) VALUES (?) ON CONFLICT DO NOTHING"\n', True),
        # Integration fix 2: each writer within its own rights.
        (WRITER, "X = \"INSERT INTO autotrader_size_events (identity, kind, proposal_id) VALUES (?, 'step_down', ?)\"\n",
         False),
        (WRITER, "X = \"INSERT INTO autotrader_size_events (identity, kind, proposal_id) VALUES (?, 'step_up', ?)\"\n",
         True),
        (WRITER, 'X = "INSERT INTO autotrader_size_events (identity, kind, proposal_id) VALUES (?, ?, ?)"\n', True),
        (WRITER, 'X = "INSERT INTO autotrader_size_events SELECT * FROM elsewhere"\n', True),
        (WRITER, 'X = "INSERT INTO autotrader_size_decisions (proposal_id, decision) VALUES (?, ?)"\nY = "expired"\n',
         False),
        (WRITER, "X = \"INSERT INTO autotrader_size_decisions (proposal_id, decision) VALUES (?, 'rejected')\"\n", True),
        (WRITER, 'X = "INSERT INTO autotrader_size_decisions (proposal_id, decision) VALUES (?, ?)"\nY = "rejected"\n',
         True),
        (WRITER, 'from trading.storage.size_schema import OWNER_TELEGRAM_ACTOR\n', True),
        (WRITER, "X = \"INSERT INTO autotrader_size_events (id, kind, proposal_id) VALUES (?, 'step_down', ?)\"\n",
         True),
        (APPROVAL_WRITER,
         "X = \"INSERT INTO autotrader_size_events (identity, kind, proposal_id) VALUES (?, 'step_up', ?)\"\n", False),
        (APPROVAL_WRITER,
         "X = \"INSERT INTO autotrader_size_events (identity, kind, proposal_id) VALUES (?, 'step_down', ?)\"\n", True),
        (APPROVAL_WRITER,
         "X = \"INSERT INTO autotrader_size_decisions (proposal_id, decision) VALUES (?, 'auto_applied')\"\n", True),
        (APPROVAL_WRITER,
         "X = \"INSERT INTO autotrader_size_decisions (proposal_id, decision) VALUES (?, 'rejected')\"\n", False),
        (APPROVAL_WRITER,
         "X = \"INSERT INTO autotrader_size_decisions (proposal_id, decision) VALUES (?, 'expired')\"\n", True),
        # Fix 3 (N19b-ES LOW-3): method calls on literals fold before matching; S1 is the reviewer's mutant.
        (WRITER, 'def _rogue(db, pid):\n    db.execute(_DECISION, (pid, "appro".strip() + "ved",'
                 ' "owner".strip() + "_telegram", "2026-10-20T00:00:00+00:00"))\n', True),  # S1
        (WRITER, 'X = "APPROVED".lower()\n', True),
        (WRITER, 'A = "appro"\nX = A + "ved"\n', True),  # a split the fold cannot follow: the fragment
        (WRITER, 'T = "autotrader_size_events".strip()\nX = "INSERT INTO " + T + " (kind) VALUES (\'step_up\')"\n', True),
        (WRITER, "from trading.storage.size_schema import SIZE_EVENTS_TABLE\n"
                 "X = f\"INSERT INTO {SIZE_EVENTS_TABLE} (identity, kind) VALUES (?, 'step_down')\"\n", True),
        (WRITER, 'X = "SELECT to_mult FROM autotrader_size_events WHERE identity = ?"\n', False),
        (WRITER, 'T = "autotrader_size_events".strip()\n', True),  # a bare name to build a statement from
        (ROGUE, 'X = "INSERT INTO autotrader_size_" + "EVENTS".lower() + " VALUES (?)"\n', True),
        (ROGUE, 'X = "INSERT INTO autotrader_size_events_x"[:34] + " VALUES (?)"\n', True),
        (ROGUE, 'X = "insert into autotrader_size_events values (?)".upper()\n', True),
        (APPROVAL_WRITER, 'X = "auto_" + "APPLIED".lower()\n', True),
    ],
)
def test_the_writer_scan_finds_every_shape_of_insert(relative, source, flagged):
    assert bool(size_store_violations(source, relative)) is flagged, size_store_violations(source, relative)


def test_the_fold_reads_method_calls_on_literals():
    """N19b-ES LOW-3: the fold evaluates what the source fixes, so a word or a table name split
    across literals and plain ``str`` methods is matched whole."""
    cases = {
        '"appro".strip() + "ved"': "approved",
        '"OWNER".lower() + "_telegram"': "owner_telegram",
        '"autotrader_size_events  ".rstrip()': "autotrader_size_events",
        '"xstep_up"[1:]': "step_up",
        '"approved"[0]': "a",
        '"ab" * 2': "abab",
        'str("approved")': "approved",
        '"x".replace("x", "approved")': "approved",
        '"approved".strip(chars)': "approved",  # an argument the source does not fix: the literal itself
        '"{}".format(name).upper()': "{}",
        'f"{table}".strip()': "{}",
    }
    for source, expected in cases.items():
        assert _fold(ast.parse(source, mode="eval").body) == expected, source
    assert _fold(ast.parse("name.strip()", mode="eval").body) is None


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
        assert not size_store_violations(source, entry), entry
        assert not any(table in source for table in size_schema.SIZE_STORE_TABLES), entry

"""The nightly size writer: the promoter's verdicts into the size store (plan P3-E).

Owner decision D2 (2026-10-18): a size goes UP only on the owner's phone
approval of one proposal, never by itself; a size goes DOWN by itself at the
next session and the owner is told. This module is the "by itself" half and
the proposal half; the approval is P3-D's ``/approve`` handler. AGENTS.md's
"Identity sizing" bullet (Appendix A, D6) names exactly these two writers of
``autotrader_size_events``, and a test allows no third.

It runs in the replay child's nightly evidence (``trading.research.nightly``,
its only importer), right after the promoter (``promotion_proposals.
collect_proposals``), which was handed ``current`` = the size in force for
the next session (``sizes_for_session``: ``identity_sizing.size_in_force``,
the same store the session start reads). For the night ``plan_night`` names
(the newest closed session, the store's ``as_of``) and the next session that
has not started (``WRITE_MARGIN`` ahead of the clock), per sendable identity:

* one evaluation row, ``UNIQUE(identity, as_of)``: the whole identity is
  written in ONE transaction, so a night is idempotent per identity (a
  restarted replay writes nothing twice and never half);
* every older step up still open is closed in the same transaction: expired
  when its deadline has passed, else superseded by tonight's evidence;
* PROPOSED (one rung up, never above the ceiling): a ``step_up`` proposal,
  id ``sha256(rule|identity|from|to|evidence_fingerprint|as_of)[:10]``,
  deadline = the next session's start (``db_admin.trading_day``: 07:55 ET).
  Nothing else: no decision, no event. The writer never builds a step up
  event (its only event statement is ``_STEP_DOWN_EVENT``, a test reads it);
* DEMOTION_PROPOSED to a rung: the proposal, its ``auto_applied`` decision
  and the ``step_down`` event effective the next session, in one transaction;
* DEMOTION_PROPOSED below the bottom rung (shadow-only): the fence goes
  through ``sentinel.fence_identity``, the single fence path (it obeys
  ``SENTINEL_FENCES_LIVE``; it writes only ``paper:<IDENTITY>`` stopped=1 and
  never resumes), then the proposal with its ``fenced`` or ``would_fence``
  decision. Resume stays the owner's, and an owner's resume holds: while the
  identity has no new real trade since its last demotion fence, the writer
  does not fence it again (the Sentinel's own streak rule: a resume holds
  until the next loss).

Every other verdict is an evaluation row only. A night whose proposals could
not be built, or whose store cannot be read, writes nothing.

Every timestamp the writer stores (a row's ``created_at``, a decision's
``at``) is the real current UTC time from ONE helper, ``_utc_now``, read
inside the writing transaction; no caller hands a time in. The session
start's mid-session guard trusts ``created_at`` (review N19a, open question
3), and before each transaction the writer refuses (``session_started``) a
night whose session starts within ``WRITE_MARGIN`` of that clock. Every row is
a plain INSERT (the store is append-only: superseding or expiring a proposal
is a new decision row, never a rewrite), each statement written out
literally. The owner hears about the night from one silent Telegram batch
(``trading.operations.size_notify``), never from here.
"""
from __future__ import annotations

import asyncio
from collections.abc import Mapping
from contextlib import closing
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_EVEN, Decimal
from hashlib import sha256
import json
import logging
from pathlib import Path
import re
import sqlite3
from urllib.parse import quote

from trading.operations import sentinel
from trading.operations.db_admin import trading_day
from trading.research.promotion_proposals import (
    DEMOTION_PROPOSED,
    PROPOSED,
    RULE_VERSION,
    IdentityProposal,
    PromotionProposals,
    step_down,
    step_up,
)
from trading.research.report_notify import latest_closed_session
from trading.risk.identity_sizing import (
    CURRENT_ACCOUNT_MODE,
    IDENTITY_SIZE_CEILING,
    SIZE_LADDER,
    size_in_force,
)
from trading.scheduler.sessions import next_market_session
from trading.sendable_identities import SENDABLE_IDENTITIES
from trading.storage.migrations import SCHEMA_READ_TIMEOUT_SECONDS
from trading.storage.size_schema import (
    SHADOW_ONLY_MULT,
    SIZE_DECISIONS_TABLE,
    SIZE_EVALUATIONS_TABLE,
    SIZE_EVENTS_TABLE,
    SIZE_PROPOSALS_TABLE,
    SIZE_RUNGS,
    STEP_DOWN,
    STEP_UP,
)

logger = logging.getLogger("autotrader.shadow.sizes")

UTC = timezone.utc
# The decisions' actor: the nightly writer itself (never the owner's token).
WRITER_ACTOR = "size_writer"
# A night is written for a session that starts at least this far ahead, so no
# row of tonight can land at or after that session's start (the session start
# would ignore it as written mid-session).
WRITE_MARGIN = timedelta(minutes=30)
WRITE_BUSY_TIMEOUT_SECONDS = 30.0

WRITTEN = "written"
NOT_WRITTEN = "not_written"
# Change kinds tonight (the batch's are size_notify's).
PROPOSAL = "proposal"
STEP_DOWN_CHANGE = "step_down"
# The decision a demotion fence records (size_schema.SHADOW_ONLY_DECISIONS).
FENCED_DECISION = "fenced"
WOULD_FENCE_DECISION = "would_fence"
# Named reasons an identity's night was not written (its row is retried next run).
CURRENT_NOT_IN_FORCE = "current_not_in_force"
PROPOSAL_NOT_ONE_RUNG = "proposal_not_one_rung"
PROPOSAL_ABOVE_CEILING = "proposal_above_ceiling"
NO_FENCE_STORE = "no_fence_store"
FENCE_FAILED = "fence_failed"
# An owner's resume of a demotion fence holds until the identity trades again.
OWNER_RESUME_HOLDS = "owner_resume_holds"

# The writer refuses a night whose session starts this soon by its own clock.
SESSION_STARTED = "session_started"

# Every statement the writer writes with, each a plain INSERT written out
# literally (no helper builds SQL; a test pins the tables equal to size_schema's
# constants). The writer's ONE size event is a PAPER step down, from the
# decision it just recorded (a test reads that no other event is ever built).
_STEP_DOWN_EVENT = (
    "INSERT INTO autotrader_size_events"
    " (identity, from_mult, to_mult, effective_session, kind, proposal_id, created_at, account_mode)"
    " VALUES (?, ?, ?, ?, 'step_down', ?, ?, 'paper')"
)
_PROPOSAL = (
    "INSERT INTO autotrader_size_proposals"
    " (proposal_id, as_of, identity, kind, from_mult, to_mult, evidence_json, evidence_fingerprint,"
    " nights_since_last_step, deadline_at, rule_version, created_at)"
    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)
_DECISION = "INSERT INTO autotrader_size_decisions (proposal_id, decision, actor, at) VALUES (?, ?, ?, ?)"
_EVALUATION = (
    "INSERT INTO autotrader_size_evaluations (identity, as_of, status, fingerprint, created_at)"
    " VALUES (?, ?, ?, ?, ?)"
)
_OPEN_STEP_UPS = f"""
    SELECT p.proposal_id, p.deadline_at FROM {SIZE_PROPOSALS_TABLE} p
    LEFT JOIN {SIZE_DECISIONS_TABLE} d ON d.proposal_id = p.proposal_id
    WHERE p.kind = '{STEP_UP}' AND d.proposal_id IS NULL AND (? IS NULL OR p.identity = ?) AND p.as_of < ?
    ORDER BY p.rowid
"""
_LAST_STEP_NIGHT = f"""
    SELECT p.as_of FROM {SIZE_EVENTS_TABLE} e
    JOIN {SIZE_PROPOSALS_TABLE} p ON p.proposal_id = e.proposal_id
    WHERE e.identity = ? AND e.account_mode = ?
    ORDER BY e.id DESC LIMIT 1
"""
_NIGHTS_EVALUATED = f"""
    SELECT count(*) FROM {SIZE_EVALUATIONS_TABLE}
    WHERE identity = ? AND as_of < ? AND (? IS NULL OR as_of > ?)
"""
_LAST_DEMOTION_FENCE = f"""
    SELECT p.evidence_json, d.decision FROM {SIZE_PROPOSALS_TABLE} p
    JOIN {SIZE_DECISIONS_TABLE} d ON d.proposal_id = p.proposal_id
    WHERE p.identity = ? AND p.kind = '{STEP_DOWN}' AND p.to_mult = '{SHADOW_ONLY_MULT}' AND p.as_of < ?
    ORDER BY p.rowid DESC LIMIT 1
"""


# --------------------------------------------------------------------------
# The night and its sizes
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class NightPlan:
    """``night``: the newest closed session (the evidence's last; the store's
    ``as_of``). ``session``: the next session that has not started yet, at
    least ``WRITE_MARGIN`` ahead; ``starts_at``: its start, which is a step
    up's deadline and the moment a step down takes effect."""

    night: date
    session: date
    starts_at: datetime


def plan_night(now: datetime) -> NightPlan:
    """The night to write and the session it is for, anchored to the XNYS
    calendar and the supervisor's plan (America/New_York), never a clock time."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    night = latest_closed_session(now)
    candidate = next_market_session(night)
    while True:
        starts_at = trading_day(candidate).starts_at
        if starts_at > now + WRITE_MARGIN:
            return NightPlan(night=night, session=candidate.session_date, starts_at=starts_at)
        candidate = next_market_session(candidate.session_date)


def sizes_for_session(database: str | Path, session: date) -> dict[str, Decimal]:
    """Each sendable identity's size in force for ``session``, from the store
    (``identity_sizing.size_in_force``); raises ``SizeNotInForce`` (fail-closed:
    the promoter gets no "current" it did not read)."""
    return {name: size_in_force(database, name, session) for name in SENDABLE_IDENTITIES}


# --------------------------------------------------------------------------
# The night's result
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SizeChange:
    """A row written tonight that the owner hears about."""

    identity: str
    proposal_id: str
    kind: str  # PROPOSAL, STEP_DOWN_CHANGE, FENCED_DECISION or WOULD_FENCE_DECISION
    from_mult: str
    to_mult: str

    def payload(self) -> dict[str, str]:
        return {
            "identity": self.identity,
            "proposal_id": self.proposal_id,
            "kind": self.kind,
            "from": self.from_mult,
            "to": self.to_mult,
        }


@dataclass(frozen=True, slots=True)
class SizeNight:
    """What the writer did for one night (``evidence.json["sizes"]``)."""

    status: str
    night: date | None = None
    session: date | None = None
    note: str = ""
    evaluated: tuple[str, ...] = ()
    already: tuple[str, ...] = ()
    changes: tuple[SizeChange, ...] = ()
    expired: tuple[str, ...] = ()
    superseded: tuple[str, ...] = ()
    held: Mapping[str, str] = field(default_factory=dict)
    failed: Mapping[str, str] = field(default_factory=dict)
    # The silent batch's outcome (report_notify's code); "not_attempted" without a sender.
    delivery: str = "not_attempted"

    def payload(self) -> dict[str, object]:
        return {
            "status": self.status,
            "night": None if self.night is None else self.night.isoformat(),
            "session": None if self.session is None else self.session.isoformat(),
            "note": self.note,
            "evaluated": list(self.evaluated),
            "already_evaluated": list(self.already),
            "changes": [change.payload() for change in self.changes],
            "expired": list(self.expired),
            "superseded": list(self.superseded),
            "held": dict(self.held),
            "failed": dict(self.failed),
            "delivery": self.delivery,
        }


def not_written(note: str, plan: NightPlan | None = None) -> SizeNight:
    return SizeNight(
        status=NOT_WRITTEN,
        night=None if plan is None else plan.night,
        session=None if plan is None else plan.session,
        note=note,
    )


# --------------------------------------------------------------------------
# Text of the store
# --------------------------------------------------------------------------


def _utc_now() -> datetime:
    """The ONE clock of every timestamp the writer stores: the real current UTC
    time, read inside each writing transaction (never a caller's time)."""
    return datetime.now(timezone.utc)


def _utc(moment: datetime) -> str:
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError("timestamps must be timezone-aware")
    return moment.astimezone(UTC).isoformat()


def _rung_text(value: Decimal) -> str:
    """A ladder rung as the store holds it (``size_schema.SIZE_RUNGS``)."""
    if value not in SIZE_LADDER:
        raise ValueError(f"x{value} is not a rung of the size ladder")
    return SIZE_RUNGS[SIZE_LADDER.index(value)]


def _evidence_text(item: IdentityProposal) -> str:
    """The promoter's own evidence wording: its proposal line after the step."""
    line = item.proposal_line() or ""
    return line.split("; ", 1)[1] if "; " in line else ""


def _evidence(item: IdentityProposal) -> tuple[str, str]:
    """``(evidence_json, evidence_fingerprint)`` of one identity's verdict."""
    payload = item.payload()
    facts = {
        "rule": RULE_VERSION,
        "identity": item.identity,
        "status": item.status,
        "current": payload["current"],
        "proposed": payload["proposed"],
        "evidence": payload["evidence"],
        "window": payload["window"],
    }
    canonical = json.dumps(facts, sort_keys=True, separators=(",", ":"))
    document = {**facts, "text": _evidence_text(item), "line": item.proposal_line()}
    return json.dumps(document, sort_keys=True), sha256(canonical.encode("utf-8")).hexdigest()


def proposal_id(*, identity: str, origin: str, target: str, fingerprint: str, night: date) -> str:
    """``sha256(rule|identity|from|to|evidence_fingerprint|as_of)[:10]``: the same
    verdict on the same night is the same id."""
    key = "|".join((RULE_VERSION, identity, origin, target, fingerprint, night.isoformat()))
    return sha256(key.encode("utf-8")).hexdigest()[:10]


def _fence_reason(item: IdentityProposal, night: date) -> str:
    upper = item.bound.upper
    bound = "n/a" if upper is None else f"{upper.quantize(Decimal('0.01'), rounding=ROUND_HALF_EVEN):+}R"
    return (
        f"{sentinel.REASON_PREFIX}demotion to shadow-only (night {night.isoformat()}:"
        f" {item.trades} trades, expectancy upper bound {bound})"
    )


# --------------------------------------------------------------------------
# The writes
# --------------------------------------------------------------------------

_TOKEN = re.compile(r"[a-z][a-z0-9_]{0,63}")


def _connect(path: Path) -> sqlite3.Connection:
    """The store, read-write but never created; transactions are explicit."""
    uri = f"file:{quote(str(path.resolve()))}?mode=rw"
    db = sqlite3.connect(uri, uri=True, timeout=WRITE_BUSY_TIMEOUT_SECONDS, isolation_level=None)
    db.execute("PRAGMA foreign_keys=ON")
    return db


def _evaluated(db: sqlite3.Connection, identity: str, night: date) -> bool:
    row = db.execute(
        f"SELECT 1 FROM {SIZE_EVALUATIONS_TABLE} WHERE identity = ? AND as_of = ?", (identity, night.isoformat())
    ).fetchone()
    return row is not None


def _nights_since_last_step(db: sqlite3.Connection, identity: str, night: date) -> int:
    """Nights evaluated since the identity's last size event's night, tonight included."""
    row = db.execute(_LAST_STEP_NIGHT, (identity, CURRENT_ACCOUNT_MODE)).fetchone()
    since = None if row is None else str(row[0])
    (count,) = db.execute(_NIGHTS_EVALUATED, (identity, night.isoformat(), since, since)).fetchone()
    return int(count) + 1


def _close_open_step_ups(
    db: sqlite3.Connection, *, identity: str | None, night: date, stamp: datetime
) -> tuple[list[str], list[str]]:
    """Every older step up nobody decided: expired once its deadline passed, else
    superseded (only for ``identity``'s own; ``None``: every identity, expiry only)."""
    expired: list[str] = []
    superseded: list[str] = []
    rows = db.execute(_OPEN_STEP_UPS, (identity, identity, night.isoformat())).fetchall()
    for pid, deadline in rows:
        overdue = datetime.fromisoformat(str(deadline)) <= stamp
        if not overdue and identity is None:
            continue
        decision = "expired" if overdue else "superseded"
        db.execute(_DECISION, (str(pid), decision, WRITER_ACTOR, _utc(stamp)))
        (expired if overdue else superseded).append(str(pid))
    return expired, superseded


def _insert_proposal(
    db: sqlite3.Connection,
    *,
    item: IdentityProposal,
    kind: str,
    origin: str,
    target: str,
    nights: int,
    deadline: datetime | None,
    night: date,
    stamp: datetime,
) -> str:
    evidence_json, fingerprint = _evidence(item)
    pid = proposal_id(identity=item.identity, origin=origin, target=target, fingerprint=fingerprint, night=night)
    db.execute(
        _PROPOSAL,
        (
            pid, night.isoformat(), item.identity, kind, origin, target, evidence_json, fingerprint, nights,
            None if deadline is None else _utc(deadline), RULE_VERSION, _utc(stamp),
        ),
    )
    return pid


def _insert_step_down(
    db: sqlite3.Connection, *, identity: str, origin: str, target: str, session: date, pid: str, stamp: datetime
) -> None:
    """The step down's event: effective the next session, never before."""
    db.execute(_STEP_DOWN_EVENT, (identity, origin, target, session.isoformat(), pid, _utc(stamp)))


def _previous_fence_trades(db: sqlite3.Connection, identity: str, night: date) -> tuple[str, int | None] | None:
    """The identity's last demotion fence before tonight: its decision and the trade count it saw."""
    row = db.execute(_LAST_DEMOTION_FENCE, (identity, night.isoformat())).fetchone()
    if row is None:
        return None
    try:
        trades = json.loads(str(row[0]))["evidence"]["trades"]
    except (ValueError, KeyError, TypeError):
        trades = None
    return str(row[1]), trades if isinstance(trades, int) else None


@dataclass(slots=True)
class _Tally:
    evaluated: list[str] = field(default_factory=list)
    already: list[str] = field(default_factory=list)
    changes: list[SizeChange] = field(default_factory=list)
    expired: list[str] = field(default_factory=list)
    superseded: list[str] = field(default_factory=list)
    held: dict[str, str] = field(default_factory=dict)
    failed: dict[str, str] = field(default_factory=dict)


def _failure_token(exc: BaseException) -> str:
    """A database refusal's own token (the triggers' RAISE texts are tokens)."""
    text = str(exc).strip().split(":", 1)[0].split(" ", 1)[0]
    return text if _TOKEN.fullmatch(text) else type(exc).__name__.lower()


def _session_started(plan: NightPlan, stamp: datetime) -> bool:
    """The plan's session starts within the margin of the writer's own clock:
    a row written now could land at or after that start (mid-session)."""
    return stamp + WRITE_MARGIN >= plan.starts_at


def _write_identity(
    db: sqlite3.Connection,
    item: IdentityProposal,
    *,
    plan: NightPlan,
    fence_outcome: str | None,
    tally: _Tally,
) -> None:
    """One identity's whole night in ONE transaction (or nothing of it), every
    row stamped with one reading of the writer's clock inside it."""
    name = item.identity
    db.execute("BEGIN IMMEDIATE")
    try:
        stamp = _utc_now()
        if _session_started(plan, stamp):
            db.execute("ROLLBACK")
            tally.failed[name] = SESSION_STARTED
            return
        if _evaluated(db, name, plan.night):
            db.execute("ROLLBACK")
            tally.already.append(name)
            return
        nights = _nights_since_last_step(db, name, plan.night)
        expired, superseded = _close_open_step_ups(db, identity=name, night=plan.night, stamp=stamp)
        current = _rung_text(item.current)
        change: SizeChange | None = None
        if item.status == PROPOSED:
            target = _rung_text(item.proposed) if item.proposed is not None else ""
            pid = _insert_proposal(
                db, item=item, kind=STEP_UP, origin=current, target=target, nights=nights,
                deadline=plan.starts_at, night=plan.night, stamp=stamp,
            )
            change = SizeChange(name, pid, PROPOSAL, current, target)
        elif item.status == DEMOTION_PROPOSED and item.proposed is not None:
            target = _rung_text(item.proposed)
            pid = _insert_proposal(
                db, item=item, kind=STEP_DOWN, origin=current, target=target, nights=nights,
                deadline=None, night=plan.night, stamp=stamp,
            )
            db.execute(_DECISION, (pid, "auto_applied", WRITER_ACTOR, _utc(stamp)))
            _insert_step_down(
                db, identity=name, origin=current, target=target, session=plan.session, pid=pid, stamp=stamp
            )
            change = SizeChange(name, pid, STEP_DOWN_CHANGE, current, target)
        elif item.status == DEMOTION_PROPOSED and fence_outcome is not None:
            pid = _insert_proposal(
                db, item=item, kind=STEP_DOWN, origin=current, target=SHADOW_ONLY_MULT, nights=nights,
                deadline=None, night=plan.night, stamp=stamp,
            )
            db.execute(_DECISION, (pid, fence_outcome, WRITER_ACTOR, _utc(stamp)))
            change = SizeChange(name, pid, fence_outcome, current, SHADOW_ONLY_MULT)
        _evidence_json, fingerprint = _evidence(item)
        db.execute(_EVALUATION, (name, plan.night.isoformat(), item.token or item.status, fingerprint, _utc(stamp)))
        db.execute("COMMIT")
    except BaseException:
        if db.in_transaction:
            db.execute("ROLLBACK")
        raise
    tally.evaluated.append(name)
    tally.expired += expired
    tally.superseded += superseded
    if change is not None:
        tally.changes.append(change)


def _check(item: IdentityProposal, sizes: Mapping[str, Decimal]) -> str | None:
    """Why the promoter's verdict cannot be written as it stands, else None."""
    if sizes.get(item.identity) != item.current:
        return CURRENT_NOT_IN_FORCE
    if item.status == PROPOSED:
        ceiling = IDENTITY_SIZE_CEILING.get(CURRENT_ACCOUNT_MODE, {}).get(item.identity)
        if item.proposed is None or item.proposed != step_up(item.current):
            return PROPOSAL_NOT_ONE_RUNG
        if ceiling is None or item.proposed > ceiling:
            return PROPOSAL_ABOVE_CEILING
    if item.status == DEMOTION_PROPOSED and item.proposed != step_down(item.current):
        return PROPOSAL_NOT_ONE_RUNG
    return None


def _demotion_fence(
    db: sqlite3.Connection,
    item: IdentityProposal,
    *,
    plan: NightPlan,
    fence_store: sentinel.FenceStore | None,
    tally: _Tally,
) -> str | None:
    """Below the bottom rung: fence through ``sentinel.fence_identity`` (outside
    any transaction of ours: the fence store writes the same database). The
    decision to record, or None when nothing is to be written tonight."""
    name = item.identity
    if _evaluated(db, name, plan.night):
        return None  # _write_identity records it as already evaluated
    expected = FENCED_DECISION if sentinel.SENTINEL_FENCES_LIVE else WOULD_FENCE_DECISION
    previous = _previous_fence_trades(db, name, plan.night)
    if previous is not None and previous == (expected, item.trades):
        # No new real trade since the last demotion fence: an owner's resume
        # holds (and a fence that still stands needs nothing).
        tally.held[name] = OWNER_RESUME_HOLDS
        return None
    if fence_store is None:
        tally.failed[name] = NO_FENCE_STORE
        return None
    if _session_started(plan, _utc_now()):
        tally.failed[name] = SESSION_STARTED
        return None
    try:
        outcome = asyncio.run(
            sentinel.fence_identity(fence_store, identity=name, reason=_fence_reason(item, plan.night))
        )
    except Exception as exc:  # noqa: BLE001 - named; retried by the next run (no row written)
        logger.exception("demotion fence for %s NOT written", name)
        tally.failed[name] = f"{FENCE_FAILED}_{type(exc).__name__.lower()}"[:64]
        return None
    if outcome in (sentinel.FENCED, sentinel.ALREADY_FENCED):
        logger.critical("size writer: %s demoted to shadow-only, fenced (%s)", name, outcome)
        return FENCED_DECISION
    if outcome == sentinel.WOULD_FENCE:
        return WOULD_FENCE_DECISION
    tally.failed[name] = f"{FENCE_FAILED}_{outcome}"[:64]
    return None


def write_night(
    *,
    database: str | Path,
    proposals: PromotionProposals,
    plan: NightPlan,
    fence_store: sentinel.FenceStore | None,
) -> SizeNight:
    """Write the night ``plan`` names from the promoter's ``proposals``; see the
    module docstring. It takes no time: every stamp is ``_utc_now``'s."""
    if not proposals.readable:
        return not_written(f"proposals not evaluated: {proposals.note}", plan)
    path = Path(database).expanduser()
    if not path.is_file():
        return not_written("no database", plan)
    try:
        sizes = sizes_for_session(path, plan.session)
    except Exception as exc:  # noqa: BLE001 - SizeNotInForce and its causes: nothing is written
        token = getattr(exc, "token", type(exc).__name__)
        logger.error("size writer: no size in force for %s (%s); nothing written", plan.session, exc)
        return not_written(str(token), plan)
    tally = _Tally()
    with closing(_connect(path)) as db:
        db.execute("BEGIN IMMEDIATE")
        try:
            stamp = _utc_now()
            if _session_started(plan, stamp):
                db.execute("ROLLBACK")
                logger.error(
                    "size writer: the %s session starts at %s, within %s of %s; nothing written",
                    plan.session, plan.starts_at.isoformat(), WRITE_MARGIN, stamp.isoformat(),
                )
                return not_written(SESSION_STARTED, plan)
            expired, _none = _close_open_step_ups(db, identity=None, night=plan.night, stamp=stamp)
            db.execute("COMMIT")
        except BaseException:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise
        tally.expired += expired
        for item in proposals.identities:
            if item.identity not in SENDABLE_IDENTITIES:
                continue
            problem = _check(item, sizes)
            if problem is not None:
                logger.error("size writer: %s not written: %s", item.identity, problem)
                tally.failed[item.identity] = problem
                continue
            fence_outcome = None
            if item.status == DEMOTION_PROPOSED and item.proposed is None:
                fence_outcome = _demotion_fence(db, item, plan=plan, fence_store=fence_store, tally=tally)
                if item.identity in tally.failed:
                    continue
            try:
                _write_identity(db, item, plan=plan, fence_outcome=fence_outcome, tally=tally)
            except sqlite3.Error as exc:
                logger.error("size writer: %s refused by the store: %s", item.identity, exc)
                tally.failed[item.identity] = _failure_token(exc)
    for change in tally.changes:
        logger.warning(
            "size writer: %s %s x%s -> %s (%s)", change.kind, change.identity, change.from_mult,
            change.to_mult, change.proposal_id,
        )
    return SizeNight(
        status=WRITTEN,
        night=plan.night,
        session=plan.session,
        evaluated=tuple(tally.evaluated),
        already=tuple(tally.already),
        changes=tuple(tally.changes),
        expired=tuple(tally.expired),
        superseded=tuple(tally.superseded),
        held=dict(tally.held),
        failed=dict(tally.failed),
    )


__all__ = [
    "NOT_WRITTEN",
    "OWNER_RESUME_HOLDS",
    "SESSION_STARTED",
    "PROPOSAL",
    "STEP_DOWN_CHANGE",
    "WRITER_ACTOR",
    "WRITE_MARGIN",
    "WRITTEN",
    "NightPlan",
    "SizeChange",
    "SizeNight",
    "not_written",
    "plan_night",
    "proposal_id",
    "sizes_for_session",
    "write_night",
]

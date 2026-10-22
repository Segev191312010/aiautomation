"""Per-identity size multipliers for the PAPER senders: the reviewed baseline,
the ladder, the ceiling, and the one read of the size store at session start.

Baseline (owner, 2026-10-14/15). Every sendable identity
(``trading.sendable_identities.SENDABLE_IDENTITIES``) has exactly one row in
``IDENTITY_SIZE_MULTIPLIER``. The factor scales the risk policy's approved
quantity to ``floor(policy quantity x multiplier)`` through the engine's
``size_multiplier`` (``trading.risk.engine``), the same hook the AI day
manager narrows through; the two factors multiply, each in (0, 1]. Baseline
values change only in a reviewed commit.

Size store (owner decision D2, 2026-10-18; migration 18,
``trading.storage.size_schema``). From its baseline an identity moves one
rung of ``SIZE_LADDER`` at a time, and only through rows in
``autotrader_size_events``: a step up after the owner's Telegram approval, a
step down by itself at the next session. ``session_identity_multiplier`` is
the one read: a strategy child calls it once, at its session's start, and
hands the result unchanged to its pipeline. The size is the identity's
latest event in force for that session (effective on or before it, written
before the session's trading day began), else the baseline, and never above
the account mode's ceiling (``IDENTITY_SIZE_CEILING``: x1, PAPER only, owner
decision D3).

Fail-closed rule: a sendable identity is never sized at 1 by default. Its
factor is ``None``, and the pipeline turns every one of its setups into a
terminal ``identity_sizing_missing`` block, when it has no baseline row; when
its account mode or the identity itself has no ceiling; when its size would
be above the ceiling; when the store cannot be read (migration 18 still
pending included); when its event chain does not start at its baseline; or
when the read comes before the session's start. Each case logs one CRITICAL
with its named token. Nothing ever falls back to a cached or remembered size
(O13): every read is a fresh read-only open of the database. A name that is
not sendable keeps 1 (shadow identities are sized exactly as before) and is
never read from the store. The launch preflight
(``shadow_service.registry_problems``) and tests pin the tables against
``SENDABLE_IDENTITIES``, so most ``None`` branches are tripwires, never
routine paths.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import logging
from pathlib import Path
import sqlite3
import time
from types import MappingProxyType
from urllib.parse import quote

from trading.risk.engine import NO_SCALING, require_size_multiplier
from trading.scheduler.sessions import MarketSession, market_session, next_market_session
from trading.storage import migrations as schema_migrations
from trading.storage.migrations import (
    SCHEMA_CHECKSUM_MISMATCH,
    SCHEMA_MIGRATION_PENDING,
    SCHEMA_READ_TIMEOUT_SECONDS,
)
from trading.storage.size_schema import (
    SIZE_EVENTS_TABLE,
    SIZE_STORE_MIGRATION,
    STEP_DOWN,
    STEP_UP,
)

logger = logging.getLogger("autotrader.shadow.sizing")

# One rung at a time, bottom to top, never above x1 (the owner-accepted rule,
# OPERATOR_PROMPT 8.3). The single implementation: the nightly promoter
# imports it and the size store's CHECKs hold the same rungs as text
# (``size_schema.SIZE_RUNGS``, pinned equal by a test).
SIZE_LADDER: tuple[Decimal, ...] = (Decimal("0.25"), Decimal("0.5"), Decimal("1"))
if (
    not SIZE_LADDER
    or any(type(rung) is not Decimal for rung in SIZE_LADDER)
    or list(SIZE_LADDER) != sorted(set(SIZE_LADDER))
    or SIZE_LADDER[-1] != NO_SCALING
):
    raise ValueError("SIZE_LADDER must be Decimals rising strictly to x1")
for _rung in SIZE_LADDER:
    require_size_multiplier(_rung)
del _rung

IDENTITY_SIZE_MULTIPLIER: Mapping[str, Decimal] = MappingProxyType(
    {
        "MOMO_HOD_V2": Decimal("1"),
        "ORB_V2": Decimal("0.25"),
        "VWAP_PB_V2": Decimal("0.5"),
        "GAP_GO_V2": Decimal("0.5"),
    }
)
for _name, _value in IDENTITY_SIZE_MULTIPLIER.items():
    # A row outside (0, 1], or not a Decimal, is refused at import: the table
    # is read at every session start and must never size anything up.
    require_size_multiplier(_value)
    if not isinstance(_value, Decimal):
        raise TypeError(f"identity multiplier for {_name} must be a Decimal")
del _name, _value

# The account mode every size read is for (O10): PAPER, a code constant. The
# bridge's own paper-account check stays the authority on the connected
# account; nothing here reads an account id.
CURRENT_ACCOUNT_MODE = "paper"
# The highest size per account mode and identity (owner decision D3: x1 for
# all four, PAPER only). A mode without a row, or a sendable identity missing
# from its mode's row, has no size at all. Written out name by name: a new
# sendable identity gets a ceiling only in a reviewed commit, never by default.
_PAPER_CEILING = Decimal("1")
IDENTITY_SIZE_CEILING: Mapping[str, Mapping[str, Decimal]] = MappingProxyType(
    {
        "paper": MappingProxyType(
            {
                "MOMO_HOD_V2": _PAPER_CEILING,
                "ORB_V2": _PAPER_CEILING,
                "VWAP_PB_V2": _PAPER_CEILING,
                "GAP_GO_V2": _PAPER_CEILING,
            }
        ),
    }
)
for _mode, _row in IDENTITY_SIZE_CEILING.items():
    # Frozen rows of Decimals in (0, 1]: a ceiling never permits a size up.
    if not isinstance(_row, MappingProxyType):
        raise TypeError(f"the {_mode} ceiling row must be read-only")
    for _name, _value in _row.items():
        if not isinstance(_value, Decimal):
            raise TypeError(f"the {_mode} ceiling for {_name} must be a Decimal")
        require_size_multiplier(_value)
del _mode, _row, _name, _value

# Named reasons a sendable identity has no size for a session (one CRITICAL
# each; its setups are then blocked as identity_sizing_missing).
ACCOUNT_MODE_WITHOUT_CEILING = "account_mode_without_ceiling"
SENDABLE_WITHOUT_CEILING = "sendable_without_ceiling"
IDENTITY_SIZE_STORE_UNREADABLE = "identity_size_store_unreadable"
IDENTITY_SIZE_ABOVE_CEILING = "identity_size_above_ceiling"
SIZE_EVENT_CHAIN_BROKEN = "size_event_chain_broken"
SIZE_READ_BEFORE_SESSION_START = "size_read_before_session_start"
# An event the session must not see (written after its start): ignored, one
# CRITICAL, and the size stays what the session started with.
SIZE_EVENT_WRITTEN_MID_SESSION = "size_event_written_mid_session"


def identity_multiplier(name: str, *, sendable: bool) -> Decimal | None:
    """The reviewed baseline for ``name``; ``None`` for a sendable name with no row.

    ``sendable`` is decided by the caller (``is_sendable_identity``), so this
    module never guesses from the name. A non-sendable name is 1: shadow
    identities keep the unscaled policy size. A sendable name without a row is
    ``None`` and must be refused by the caller, never defaulted to 1. A
    session's size comes from ``session_identity_multiplier``, which starts
    from this baseline.
    """
    if not sendable:
        return Decimal("1")
    value = IDENTITY_SIZE_MULTIPLIER.get(name.strip())
    if value is None:
        return None
    return require_size_multiplier(value)


# A busy, locked or failing database at the session start is read again,
# briefly, inside the child (N19a, open question 2): only these SQLite result
# codes. Every other failure is deterministic and gives None at once; nothing
# here ever exits the child (exit 75 belongs to the schema gate).
SIZE_READ_ATTEMPTS = 3
SIZE_READ_RETRY_SECONDS = 1.0
_TRANSIENT_SQLITE_CODES = frozenset(
    {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED, sqlite3.SQLITE_IOERR, sqlite3.SQLITE_CANTOPEN}
)
_pause = time.sleep


class SizeStoreUnreadable(RuntimeError):
    """The size store gave no complete, well-formed history: no size is read from it."""


@dataclass(frozen=True, slots=True)
class SizeEvent:
    """One row of ``autotrader_size_events``, parsed."""

    event_id: int
    identity: str
    from_mult: Decimal
    to_mult: Decimal
    effective_session: date
    kind: str
    created_at: datetime


_EVENTS = f"""
    SELECT id, identity, from_mult, to_mult, effective_session, kind, created_at
    FROM {SIZE_EVENTS_TABLE}
    WHERE identity = ? AND account_mode = ?
    ORDER BY id
"""


def read_size_events(
    database: str | Path, *, identity: str, account_mode: str
) -> tuple[SizeEvent, ...]:
    """Every size event of ``identity`` in ``account_mode``, oldest first.

    One read-only open (``mode=ro``: never creates, migrates or writes the
    database). Raises ``SizeStoreUnreadable``, never returns a partial
    history, when the database is missing or unreadable, migration 18 is not
    applied with this code's checksum, or a row does not parse.
    """
    path = Path(database).expanduser()
    if not path.is_file():
        raise SizeStoreUnreadable(f"no database at {path}")
    uri = f"file:{quote(str(path.resolve()))}?mode=ro"
    for attempt in range(1, SIZE_READ_ATTEMPTS + 1):
        try:
            with closing(sqlite3.connect(uri, uri=True, timeout=SCHEMA_READ_TIMEOUT_SECONDS)) as db:
                _require_size_store(db)
                rows = db.execute(_EVENTS, (identity, account_mode)).fetchall()
            break
        except sqlite3.OperationalError as exc:
            transient = (getattr(exc, "sqlite_errorcode", 0) & 0xFF) in _TRANSIENT_SQLITE_CODES
            if not transient or attempt == SIZE_READ_ATTEMPTS:
                raise SizeStoreUnreadable(f"{type(exc).__name__}: {exc} (attempt {attempt})") from exc
            logger.warning(
                "size store read %d/%d failed (%s); reading again in %.1f s",
                attempt, SIZE_READ_ATTEMPTS, exc, SIZE_READ_RETRY_SECONDS,
            )
            _pause(SIZE_READ_RETRY_SECONDS)
        except sqlite3.Error as exc:
            raise SizeStoreUnreadable(f"{type(exc).__name__}: {exc}") from exc
    return tuple(_event(row) for row in rows)


def _require_size_store(db: sqlite3.Connection) -> None:
    """Migration 18 is applied, and it is this code's migration 18 (O13: a
    pending migration is an unreadable store, never the baseline)."""
    expected = next(
        (item for item in schema_migrations.MIGRATIONS if item.version == SIZE_STORE_MIGRATION), None
    )
    if expected is None:
        raise SizeStoreUnreadable(f"this code registers no migration {SIZE_STORE_MIGRATION}")
    row = db.execute(
        "SELECT name, checksum FROM autotrader_schema_migrations WHERE version = ?",
        (SIZE_STORE_MIGRATION,),
    ).fetchone()
    if row is None:
        raise SizeStoreUnreadable(
            f"{SCHEMA_MIGRATION_PENDING}: migration {SIZE_STORE_MIGRATION} ({expected.name}) is not"
            " applied; run `autotraderctl db migrate` after the close"
        )
    if (str(row[0]), str(row[1])) != (expected.name, expected.checksum):
        raise SizeStoreUnreadable(
            f"{SCHEMA_CHECKSUM_MISMATCH}: migration {SIZE_STORE_MIGRATION} in the database is not this code's"
        )


def _rung(value: object) -> Decimal:
    rung = Decimal(str(value))
    if rung not in SIZE_LADDER:
        raise ValueError(f"{value!r} is not a rung of the size ladder")
    return rung


def _utc_time(value: object) -> datetime:
    moment = datetime.fromisoformat(str(value))
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError(f"{value!r} is not timezone-aware")
    return moment


def _event(row: Sequence[object]) -> SizeEvent:
    event_id, identity, origin, target, effective, kind, created = row
    try:
        return SizeEvent(
            event_id=int(str(event_id)),
            identity=str(identity),
            from_mult=_rung(origin),
            to_mult=_rung(target),
            effective_session=date.fromisoformat(str(effective)),
            kind=str(kind),
            created_at=_utc_time(created),
        )
    except (TypeError, ValueError, ArithmeticError) as exc:
        raise SizeStoreUnreadable(f"size event {event_id!r} does not parse: {exc}") from exc


def _session_start_of(day: date) -> date:
    """The session whose start an event effective on ``day`` takes effect at."""
    return day if market_session(day) is not None else next_market_session(day).session_date


def _chain_problem(events: Sequence[SizeEvent], baseline: Decimal) -> str | None:
    """Why ``events`` are not one history from ``baseline``, else None.

    The database checks each step against the previous event; only code
    knows the baseline the first step must start from, and the session
    calendar. Each event is one rung in its kind's direction, from the size
    before it, taking effect at a later session start than the one before it
    (an event dated on a holiday takes effect at the next session, so it may
    not share that start with the next event: N19a LOW-5).
    """
    size = baseline
    previous: SizeEvent | None = None
    previous_start: date | None = None
    for event in events:
        if event.from_mult != size:
            return f"event {event.event_id} steps from x{event.from_mult}, but the size before it is x{size}"
        direction = {STEP_UP: 1, STEP_DOWN: -1}.get(event.kind)
        if direction is None or SIZE_LADDER.index(event.to_mult) - SIZE_LADDER.index(event.from_mult) != direction:
            return f"event {event.event_id} ({event.kind} x{event.from_mult} -> x{event.to_mult}) is not one rung"
        starts = _session_start_of(event.effective_session)
        if previous is not None and previous_start is not None and starts <= previous_start:
            return (
                f"event {event.event_id} takes effect at the {starts.isoformat()} session start, not after"
                f" event {previous.event_id}'s ({previous_start.isoformat()}): one step per session"
            )
        size, previous, previous_start = event.to_mult, event, starts
    return None


def _trading_day_start(session: MarketSession) -> datetime:
    """The session's start: the supervisor's first child start (07:55 ET on
    every session), the same span ``db migrate`` refuses inside.

    Imported on use: the operations package plans the day, and the nightly
    promoter loads this module without it.
    """
    from trading.operations.db_admin import trading_day

    return trading_day(session).starts_at


def _refused(token: str, identity: str, session_date: date, detail: str) -> None:
    logger.critical(
        "%s: %s has no size for the %s session (%s); every setup of the run is blocked,"
        " never sized from a default or remembered value",
        token,
        identity,
        session_date.isoformat(),
        detail,
        stack_info=True,
    )
    return None


def session_identity_multiplier(
    name: str,
    *,
    sendable: bool,
    session_date: date,
    database: str | Path,
    now: datetime | None = None,
) -> Decimal | None:
    """The size factor ``name`` trades with in the session ``session_date``.

    Called once per run, at the session's start; the caller passes the result
    unchanged to its pipeline. A name that is not sendable is 1 and reads
    nothing. A sendable name reads its events from the store (``mode=ro``):
    the latest one effective on or before ``session_date`` and written before
    the session's trading day began is in force, else the baseline. An event
    effective in this session but written after its start is ignored with one
    CRITICAL (``size_event_written_mid_session``): sizes change only at a
    session start, so a restarted child keeps the size its first start read.
    Every other failure is ``None`` with one CRITICAL and its token (see the
    module docstring); ``now`` (default: the wall clock) must not be before the
    session's start, or an event written later tonight would be missed.
    """
    if not sendable:
        return NO_SCALING
    identity = name.strip()
    mode = CURRENT_ACCOUNT_MODE
    ceilings = IDENTITY_SIZE_CEILING.get(mode)
    if ceilings is None:
        detail = f"account mode {mode!r} has no ceiling row"
        return _refused(ACCOUNT_MODE_WITHOUT_CEILING, identity, session_date, detail)
    ceiling = ceilings.get(identity)
    if ceiling is None:
        detail = f"the {mode} ceiling row does not name it"
        return _refused(SENDABLE_WITHOUT_CEILING, identity, session_date, detail)
    baseline = identity_multiplier(identity, sendable=True)
    if baseline is None:
        # No reviewed baseline: the pipeline blocks every setup as
        # identity_sizing_missing and reports it once per run.
        return None
    session = market_session(session_date)
    if session is None:
        return _refused(
            IDENTITY_SIZE_STORE_UNREADABLE, identity, session_date, "not an XNYS session: it has no start to read at"
        )
    starts_at = _trading_day_start(session)
    observed = datetime.now(timezone.utc) if now is None else now
    if observed.tzinfo is None or observed.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    if observed < starts_at:
        return _refused(
            SIZE_READ_BEFORE_SESSION_START,
            identity,
            session_date,
            f"read at {observed.isoformat()}, before the session's start {starts_at.isoformat()}",
        )
    try:
        events = read_size_events(database, identity=identity, account_mode=mode)
    except SizeStoreUnreadable as exc:
        return _refused(IDENTITY_SIZE_STORE_UNREADABLE, identity, session_date, str(exc))
    problem = _chain_problem(events, baseline)
    if problem is not None:
        return _refused(SIZE_EVENT_CHAIN_BROKEN, identity, session_date, problem)
    size, source = baseline, "baseline: no size event in force"
    for event in events:
        if not event.effective_session <= session_date:
            continue  # it takes effect at a later session
        if event.created_at >= starts_at:
            logger.critical(
                "%s: %s size event %d (%s x%s -> x%s, effective %s) was written at %s, after the"
                " session's start %s; ignored this session, the size stays x%s",
                SIZE_EVENT_WRITTEN_MID_SESSION,
                identity,
                event.event_id,
                event.kind,
                event.from_mult,
                event.to_mult,
                event.effective_session.isoformat(),
                event.created_at.isoformat(),
                starts_at.isoformat(),
                size,
                stack_info=True,
            )
            continue
        size = event.to_mult
        source = f"size event {event.event_id}, {event.kind} effective {event.effective_session.isoformat()}"
    if size > ceiling:
        return _refused(
            IDENTITY_SIZE_ABOVE_CEILING, identity, session_date, f"x{size} is above the {mode} ceiling x{ceiling}"
        )
    logger.info("%s sizes x%s in the %s session (%s)", identity, size, session_date.isoformat(), source)
    return require_size_multiplier(size)


__all__ = [
    "ACCOUNT_MODE_WITHOUT_CEILING",
    "CURRENT_ACCOUNT_MODE",
    "IDENTITY_SIZE_ABOVE_CEILING",
    "IDENTITY_SIZE_CEILING",
    "IDENTITY_SIZE_MULTIPLIER",
    "IDENTITY_SIZE_STORE_UNREADABLE",
    "SENDABLE_WITHOUT_CEILING",
    "SIZE_EVENT_CHAIN_BROKEN",
    "SIZE_EVENT_WRITTEN_MID_SESSION",
    "SIZE_LADDER",
    "SIZE_READ_BEFORE_SESSION_START",
    "SizeEvent",
    "SizeStoreUnreadable",
    "identity_multiplier",
    "read_size_events",
    "session_identity_multiplier",
]

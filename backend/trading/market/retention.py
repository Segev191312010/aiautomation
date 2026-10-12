"""Screener snapshot retention: full sweeps for 10 days, hot rows to 120, then gone.

A ``screener.v1`` sweep stores every quoted instrument: about 150 KB, or
roughly 70 MB a trading day at one sweep a minute. Once a session is more
than ``FULL_RETENTION_DAYS`` old its snapshots are compacted in place to the
hot rows and the market context, keeping the sweep header (schema, session,
timestamps, counts) and recording what was dropped under ``compacted``.
Compacted snapshots are deleted once their session is more than
``COMPACT_RETENTION_DAYS`` old.

A compacted payload is deliberately no longer a valid ``ScreenerPayload``:
``quoted`` keeps the sweep's count while ``rows`` holds only the hot list,
and ``compacted`` is not a screener.v1 field. Strict readers therefore
refuse it instead of mistaking a hot list for a full sweep, while the
``market`` document is kept verbatim and stays readable. A payload this
module does not recognise is never rewritten and never deleted.

``VACUUM`` rewrites the whole database and makes every other writer wait
while it runs, so it runs at most once a week, on Sunday, only when enough
pages are free to be worth it and the disk can hold the copy, and never
inside 07:55-16:15 ET on a trading day.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
import json
import logging
from pathlib import Path
import shutil
import sqlite3
from typing import Any

from db.core import get_db, transaction
from trading.market.screener import SCHEMA_VERSION
from trading.market.universe import runtime_directory, write_private_text
from trading.scheduler.sessions import MARKET_TIMEZONE, market_session

logger = logging.getLogger("autotrader.shadow.screener")

FULL_RETENTION_DAYS = 10
COMPACT_RETENTION_DAYS = 120
COMPACTED_KEY = "compacted"
COMPACTION_SCHEMA = "screener_compaction.v1"
# The sweep header a compacted snapshot keeps besides its hot rows and market.
HEADER_KEYS = (
    "schema",
    "session_date",
    "observed_at",
    "sweep_seconds",
    "universe_size",
    "qualified",
    "quoted",
    "errors",
)
# Snapshots are rewritten in small transactions, so a writer in another
# process waits milliseconds rather than the length of a day's compaction.
COMPACT_BATCH_SIZE = 50
# The daily job falls due at 16:05 ET, after the screener window closes.
RETENTION_RUN_AFTER = time(16, 5)
VACUUM_WEEKDAY = 6  # Sunday (date.weekday())
VACUUM_MIN_INTERVAL = timedelta(days=7)
# VACUUM never runs inside this New York window on a trading day.
TRADING_DAY_QUIET_WINDOW = (time(7, 55), time(16, 15))
# Rewriting the file is only worth it when this share of its pages is free.
VACUUM_MIN_FREE_FRACTION = 0.10
# VACUUM writes a full copy of the database, and in WAL mode a log as large.
VACUUM_DISK_HEADROOM = 2.0
RETENTION_STATE_FILENAME = "screener_retention.json"
RETENTION_STATE_SCHEMA = "screener_retention_state.v1"

# Why VACUUM did not run; the same token goes to the report and the log.
VACUUM_NOT_REQUESTED = "not_requested"
VACUUM_NOT_VACUUM_DAY = "not_vacuum_day"
VACUUM_TRADING_HOURS = "trading_hours"
VACUUM_ALREADY_THIS_WEEK = "already_this_week"
VACUUM_COMPACTION_INCOMPLETE = "compaction_incomplete"
VACUUM_STATE_UNREADABLE = "state_unreadable"
VACUUM_STATE_UNWRITABLE = "state_unwritable"
VACUUM_NOT_WORTHWHILE = "not_worthwhile"
VACUUM_INSUFFICIENT_DISK = "insufficient_disk"
VACUUM_FAILED = "vacuum_failed"

Clock = Callable[[], datetime]

_OLD_SESSION_DATES = """SELECT DISTINCT session_date FROM autotrader_screener_snapshots
    WHERE session_date < ? ORDER BY session_date"""
_NEWEST_OF_SESSION = """SELECT payload_json FROM autotrader_screener_snapshots
    WHERE session_date = ? ORDER BY observed_at DESC, id DESC LIMIT 1"""
_SESSION_IDS = """SELECT id FROM autotrader_screener_snapshots
    WHERE session_date = ? ORDER BY observed_at, id"""
_PAYLOADS = """SELECT id, payload_json FROM autotrader_screener_snapshots
    WHERE id IN (SELECT value FROM json_each(?))"""
# The guard keeps a concurrent run from compacting a snapshot twice.
_REWRITE = """UPDATE autotrader_screener_snapshots SET payload_json = ?
    WHERE id = ? AND json_type(payload_json, '$.compacted') IS NULL"""
_DELETE_COMPACTED = """DELETE FROM autotrader_screener_snapshots
    WHERE session_date = ? AND json_type(payload_json, '$.compacted') = 'object'"""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _new_york(moment: datetime) -> datetime:
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError("retention times must be timezone-aware")
    return moment.astimezone(MARKET_TIMEZONE)


def _utc_text(moment: datetime) -> str:
    return _new_york(moment).astimezone(timezone.utc).isoformat(timespec="microseconds")


# --- schedule ------------------------------------------------------------------------


def retention_day(now: datetime) -> date:
    """The latest New York date whose 16:05 has passed: the job owed as of ``now``."""
    local = _new_york(now)
    if local.time() >= RETENTION_RUN_AFTER:
        return local.date()
    return local.date() - timedelta(days=1)


def next_retention_at(now: datetime) -> datetime:
    """The first 16:05 New York time strictly after ``now``."""
    local = _new_york(now)
    due = datetime.combine(local.date(), RETENTION_RUN_AFTER, tzinfo=MARKET_TIMEZONE)
    if due <= local:
        due = datetime.combine(
            local.date() + timedelta(days=1), RETENTION_RUN_AFTER, tzinfo=MARKET_TIMEZONE
        )
    return due


def retention_cutoffs(now: datetime) -> tuple[date, date]:
    """``(compact_before, delete_before)``: sessions strictly older are compacted / deleted."""
    today = _new_york(now).date()
    return (
        today - timedelta(days=FULL_RETENTION_DAYS),
        today - timedelta(days=COMPACT_RETENTION_DAYS),
    )


def in_trading_day_quiet_window(now: datetime) -> bool:
    """07:55-16:15 New York time on an XNYS session; an unreadable calendar counts as one."""
    local = _new_york(now)
    opens, closes = TRADING_DAY_QUIET_WINDOW
    if not opens <= local.time() < closes:
        return False
    try:
        return market_session(local.date()) is not None
    except (RuntimeError, ValueError):
        return True


def vacuum_block_reason(now: datetime, last_vacuum_on: date | None) -> str | None:
    """Why VACUUM must not run at ``now``, or None when it may."""
    local = _new_york(now)
    if local.weekday() != VACUUM_WEEKDAY:
        return VACUUM_NOT_VACUUM_DAY
    if in_trading_day_quiet_window(now):
        return VACUUM_TRADING_HOURS
    if last_vacuum_on is not None and local.date() - last_vacuum_on < VACUUM_MIN_INTERVAL:
        return VACUUM_ALREADY_THIS_WEEK
    return None


# --- compaction ------------------------------------------------------------------------


def is_compacted(document: object) -> bool:
    return isinstance(document, Mapping) and isinstance(document.get(COMPACTED_KEY), Mapping)


def compact_payload(document: Mapping[str, Any], *, compacted_at: datetime) -> dict[str, Any] | None:
    """The sweep reduced to its header, hot rows and market context.

    None when ``document`` is not a full screener.v1 sweep (another schema,
    no row list, or already compacted): such payloads are left as they are.
    """
    if document.get("schema") != SCHEMA_VERSION or COMPACTED_KEY in document:
        return None
    rows = document.get("rows")
    if not isinstance(rows, list):
        return None
    hot = [row for row in rows if isinstance(row, dict) and row.get("hot") is True]
    compacted: dict[str, Any] = {key: document[key] for key in HEADER_KEYS if key in document}
    compacted["rows"] = hot
    if document.get("market") is not None:
        compacted["market"] = document["market"]
    compacted[COMPACTED_KEY] = {
        "schema": COMPACTION_SCHEMA,
        "rows_kept": len(hot),
        "rows_dropped": len(rows) - len(hot),
        "compacted_at": _utc_text(compacted_at),
    }
    return compacted


def _load(text: object) -> dict[str, Any] | None:
    try:
        document = json.loads(str(text))
    except ValueError:
        return None
    return document if isinstance(document, dict) else None


@dataclass(slots=True)
class _Tally:
    compacted: int = 0
    skipped: int = 0
    rows_dropped: int = 0
    bytes_before: int = 0
    bytes_after: int = 0


async def _session_dates_before(cutoff: date) -> list[str]:
    async with get_db() as db:
        async with db.execute(_OLD_SESSION_DATES, (cutoff.isoformat(),)) as cursor:
            return [str(row[0]) for row in await cursor.fetchall()]


async def _compact_session(
    session_date: str,
    *,
    compacted_at: datetime,
    clock: Clock,
    deadline: datetime | None,
    tally: _Tally,
) -> bool:
    """Compact one session's snapshots oldest first; False when the deadline stopped it.

    Because snapshots are compacted in observation order, a compacted newest
    snapshot means the whole session is done and nothing else is read.
    """
    async with get_db() as db:
        async with db.execute(_NEWEST_OF_SESSION, (session_date,)) as cursor:
            newest = await cursor.fetchone()
        if newest is None or is_compacted(_load(newest[0])):
            return True
        async with db.execute(_SESSION_IDS, (session_date,)) as cursor:
            snapshot_ids = [str(row[0]) for row in await cursor.fetchall()]
    skipped_before = tally.skipped
    for start in range(0, len(snapshot_ids), COMPACT_BATCH_SIZE):
        if deadline is not None and clock() >= deadline:
            return False
        batch = snapshot_ids[start : start + COMPACT_BATCH_SIZE]
        async with get_db() as db:
            async with db.execute(_PAYLOADS, (json.dumps(batch),)) as cursor:
                stored = {str(row[0]): str(row[1]) for row in await cursor.fetchall()}
        rewrites: list[tuple[str, str, int, int, int]] = []
        for snapshot_id in batch:
            text = stored.get(snapshot_id)
            if text is None:
                continue
            document = _load(text)
            if is_compacted(document):
                continue
            compacted = (
                None if document is None else compact_payload(document, compacted_at=compacted_at)
            )
            if compacted is None:
                tally.skipped += 1
                continue
            new_text = json.dumps(compacted, separators=(",", ":"))
            rewrites.append(
                (
                    new_text,
                    snapshot_id,
                    int(compacted[COMPACTED_KEY]["rows_dropped"]),
                    len(text.encode("utf-8")),
                    len(new_text.encode("utf-8")),
                )
            )
        if not rewrites:
            continue
        async with transaction() as db:
            for new_text, snapshot_id, dropped, before, after in rewrites:
                cursor = await db.execute(_REWRITE, (new_text, snapshot_id))
                if cursor.rowcount == 1:
                    tally.compacted += 1
                    tally.rows_dropped += dropped
                    tally.bytes_before += before
                    tally.bytes_after += after
    if tally.skipped > skipped_before:
        logger.warning(
            "snapshot retention left %d unrecognised snapshots of %s untouched",
            tally.skipped - skipped_before,
            session_date,
        )
    return True


async def _delete_expired(delete_before: date) -> int:
    deleted = 0
    for session_date in await _session_dates_before(delete_before):
        async with transaction() as db:
            cursor = await db.execute(_DELETE_COMPACTED, (session_date,))
            deleted += max(cursor.rowcount, 0)
    return deleted


# --- VACUUM -------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _RetentionState:
    last_vacuum_on: date | None = None


def _read_state(path: Path) -> _RetentionState | None:
    """The persisted state; None when it exists but cannot be trusted."""
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return _RetentionState()
    except (OSError, UnicodeDecodeError):
        return None
    document = _load(raw)
    if document is None or document.get("schema") != RETENTION_STATE_SCHEMA:
        return None
    value = document.get("last_vacuum_on")
    if value is None:
        return _RetentionState()
    if not isinstance(value, str):
        return None
    try:
        return _RetentionState(last_vacuum_on=date.fromisoformat(value))
    except ValueError:
        return None


def _write_state(path: Path, *, last_vacuum_on: date | None, last_run_at: datetime) -> None:
    document = {
        "schema": RETENTION_STATE_SCHEMA,
        "last_vacuum_on": None if last_vacuum_on is None else last_vacuum_on.isoformat(),
        "last_run_at": _utc_text(last_run_at),
    }
    write_private_text(path, json.dumps(document, indent=1) + "\n")


async def _page_counts() -> tuple[int, int, int]:
    """``(page_size, page_count, freelist_count)`` of the configured database."""
    values: list[int] = []
    async with get_db() as db:
        for pragma in ("page_size", "page_count", "freelist_count"):
            async with db.execute(f"PRAGMA {pragma}") as cursor:
                row = await cursor.fetchone()
            values.append(int(row[0]) if row is not None else 0)
    return values[0], values[1], values[2]


async def _vacuum(
    state_path: Path, state: _RetentionState, *, clock: Clock, ran_at: datetime
) -> tuple[str | None, bool]:
    """VACUUM when worthwhile and still allowed right now.

    Returns ``(skip reason or None, whether this week's VACUUM was claimed)``.
    """
    page_size, page_count, free_pages = await _page_counts()
    if page_count <= 0 or free_pages / page_count < VACUUM_MIN_FREE_FRACTION:
        return VACUUM_NOT_WORTHWHILE, False
    if shutil.disk_usage(state_path.parent).free < VACUUM_DISK_HEADROOM * page_size * page_count:
        return VACUUM_INSUFFICIENT_DISK, False
    # Checked again on the live clock: compaction may have taken a while.
    moment = clock()
    reason = vacuum_block_reason(moment, state.last_vacuum_on)
    if reason is not None:
        return reason, False
    # Claimed before it runs, so a failed or concurrent VACUUM never repeats this week.
    try:
        _write_state(state_path, last_vacuum_on=_new_york(moment).date(), last_run_at=ran_at)
    except OSError as exc:
        logger.error("VACUUM skipped: retention state %s is not writable: %s", state_path, exc)
        return VACUUM_STATE_UNWRITABLE, False
    try:
        async with get_db() as db:
            await db.execute("VACUUM")
            await db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except sqlite3.Error as exc:
        logger.error("%s: %s", VACUUM_FAILED, exc)
        return VACUUM_FAILED, True
    return None, True


# --- the job ---------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RetentionReport:
    ran_at: datetime
    compact_before: date
    delete_before: date
    sessions_examined: int
    compacted: int
    skipped: int
    rows_dropped: int
    bytes_before: int
    bytes_after: int
    deleted: int
    complete: bool
    vacuumed: bool
    vacuum_skipped: str | None

    def summary(self) -> str:
        saved_mb = (self.bytes_before - self.bytes_after) / 1_048_576
        vacuum = "vacuumed" if self.vacuumed else f"no vacuum ({self.vacuum_skipped})"
        return (
            f"compacted {self.compacted} snapshots of sessions before "
            f"{self.compact_before.isoformat()} ({self.rows_dropped} rows, {saved_mb:.1f} MB), "
            f"deleted {self.deleted} before {self.delete_before.isoformat()}, "
            f"left {self.skipped} unrecognised, {vacuum}"
            + ("" if self.complete else ", compaction stopped at its deadline")
        )


async def compact_snapshots(
    now: datetime,
    *,
    clock: Clock | None = None,
    deadline: datetime | None = None,
    vacuum: bool = True,
) -> RetentionReport:
    """Compact, expire and (on Sunday) VACUUM the screener snapshots as of ``now``.

    Idempotent and resumable: compacted snapshots are recognised and skipped,
    and a ``deadline`` (checked between batches on ``clock``) stops
    compaction early, leaving the rest to the next run and skipping VACUUM.
    """
    current = clock or _utc_now
    compact_before, delete_before = retention_cutoffs(now)
    tally = _Tally()
    complete = True
    sessions = await _session_dates_before(compact_before)
    for session_date in sessions:
        if not await _compact_session(
            session_date, compacted_at=now, clock=current, deadline=deadline, tally=tally
        ):
            complete = False
            break
    deleted = await _delete_expired(delete_before)

    state_path = runtime_directory() / RETENTION_STATE_FILENAME
    state = _read_state(state_path)
    vacuum_skipped: str | None
    claimed = False
    if not vacuum:
        vacuum_skipped = VACUUM_NOT_REQUESTED
    elif not complete:
        vacuum_skipped = VACUUM_COMPACTION_INCOMPLETE
    elif state is None:
        logger.error("retention state %s is unreadable; VACUUM skipped this run", state_path)
        vacuum_skipped = VACUUM_STATE_UNREADABLE
    else:
        vacuum_skipped = vacuum_block_reason(now, state.last_vacuum_on)
        if vacuum_skipped is None:
            vacuum_skipped, claimed = await _vacuum(state_path, state, clock=current, ran_at=now)
    if not claimed:
        # An unreadable state is replaced; next week's VACUUM may then run again.
        try:
            _write_state(
                state_path,
                last_vacuum_on=None if state is None else state.last_vacuum_on,
                last_run_at=now,
            )
        except OSError as exc:
            logger.warning("retention state %s not written: %s", state_path, exc)
    return RetentionReport(
        ran_at=now,
        compact_before=compact_before,
        delete_before=delete_before,
        sessions_examined=len(sessions),
        compacted=tally.compacted,
        skipped=tally.skipped,
        rows_dropped=tally.rows_dropped,
        bytes_before=tally.bytes_before,
        bytes_after=tally.bytes_after,
        deleted=deleted,
        complete=complete,
        vacuumed=vacuum_skipped is None,
        vacuum_skipped=vacuum_skipped,
    )


__all__ = [
    "COMPACTED_KEY",
    "COMPACTION_SCHEMA",
    "COMPACT_BATCH_SIZE",
    "COMPACT_RETENTION_DAYS",
    "FULL_RETENTION_DAYS",
    "HEADER_KEYS",
    "RETENTION_RUN_AFTER",
    "RETENTION_STATE_FILENAME",
    "TRADING_DAY_QUIET_WINDOW",
    "VACUUM_WEEKDAY",
    "RetentionReport",
    "compact_payload",
    "compact_snapshots",
    "in_trading_day_quiet_window",
    "is_compacted",
    "next_retention_at",
    "retention_cutoffs",
    "retention_day",
    "vacuum_block_reason",
]

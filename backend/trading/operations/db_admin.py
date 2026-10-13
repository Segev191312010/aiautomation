"""``autotraderctl db migrate`` and ``db status``: the only way the schema changes.

No process migrates at start any more (``trading.operations.schema_gate``).
The owner migrates explicitly, after the close, with these guards in order:

1. an exclusive lock next to the database, so two migrations never overlap
   (``migrate_lock_busy``);
2. a read-only look at the ledger: nothing pending is a clean no-op; a
   checksum mismatch, an unknown version or a gap refuses without touching
   anything (``migrate_schema_problem``): restore the backup or deploy the
   matching code;
3. never inside the supervised trading day of a session (from the first
   child start to the last window end of the supervisor's own plan: 07:55 to
   18:30 ET, half days included) unless ``--during-session-i-know``
   (``migrate_refused_session_window``);
4. a unique, timestamped backup through SQLite's backup API, integrity-checked
   and compared with the ledger before any change; no backup, no migration
   (``migrate_backup_failed``);
5. the pending migrations in one transaction (``migrate_failed`` rolls back).

``db status`` is read-only and exits 0 when the schema is current, 75 when not.
"""
from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import fcntl
import os
from pathlib import Path
import sqlite3
import sys
from typing import TextIO
from urllib.parse import quote

import aiosqlite

from trading.operations.paths import FILE_MODE, ensure_private_directory
from trading.operations.schema_gate import EXIT_SCHEMA_NOT_READY
from trading.scheduler.sessions import MARKET_TIMEZONE, MarketSession, market_session
from trading.storage.migrations import MIGRATIONS, SchemaStatus, backup_database, inspect_schema

UTC = timezone.utc
EXIT_MIGRATED = 0
EXIT_MIGRATE_REFUSED = 1
SESSION_OVERRIDE_FLAG = "--during-session-i-know"
LOCK_SUFFIX = ".migrate.lock"
BACKUP_DIRECTORY_NAME = "backups"
PARTIAL_SUFFIX = ".partial"

MIGRATE_LOCK_BUSY = "migrate_lock_busy"
MIGRATE_SCHEMA_PROBLEM = "migrate_schema_problem"
MIGRATE_REFUSED_SESSION_WINDOW = "migrate_refused_session_window"
MIGRATE_BACKUP_FAILED = "migrate_backup_failed"
MIGRATE_FAILED = "migrate_failed"


class MigrateRefused(RuntimeError):
    """``db migrate`` stopped before (or rolled back) any schema change."""

    def __init__(self, token: str, detail: str) -> None:
        super().__init__(f"{token}: {detail}")
        self.token = token
        self.detail = detail


@dataclass(frozen=True, slots=True)
class TradingDay:
    """The span in which the supervisor runs children for one session."""

    session: MarketSession
    starts_at: datetime
    ends_at: datetime

    def describe(self) -> str:
        start = self.starts_at.astimezone(MARKET_TIMEZONE)
        end = self.ends_at.astimezone(MARKET_TIMEZONE)
        half = " (half day)" if self.session.half_day else ""
        return f"the {self.session.session_date.isoformat()} trading day{half}, {start:%H:%M}-{end:%H:%M} ET"


def trading_day(session: MarketSession) -> TradingDay:
    """From the session's first child start to its last window end.

    Taken from the supervisor's own plan, so it follows the calendar and any
    change to the children's windows. Strategies are planned up to the close,
    an upper bound of their real cutoffs: the span can only be wider than the
    supervised day, and nothing of the Claude or TWS stack is imported.
    """
    from trading.operations.supervisor import plan_session

    plans = plan_session(session, cutoff_for=lambda _name, _day: session.closes_at)
    return TradingDay(
        session=session,
        starts_at=min(plan.starts_at for plan in plans),
        ends_at=max(plan.ends_at for plan in plans),
    )


def trading_day_at(now: datetime) -> TradingDay | None:
    """The supervised span ``now`` falls inside, else None (closed day, before, after)."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    session = market_session(now.astimezone(MARKET_TIMEZONE).date())
    if session is None:
        return None
    day = trading_day(session)
    return day if day.starts_at <= now < day.ends_at else None


@contextmanager
def migration_lock(database: Path) -> Iterator[Path]:
    """Exclusive, non-blocking lock beside the database for one migration."""
    lock_path = database.with_name(database.name + LOCK_SUFFIX)
    descriptor = os.open(
        lock_path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW, FILE_MODE
    )
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            holder = os.pread(descriptor, 32, 0).decode("ascii", "replace").strip() or "unknown"
            raise MigrateRefused(
                MIGRATE_LOCK_BUSY, f"another `db migrate` holds {lock_path} (pid {holder})"
            ) from exc
        os.ftruncate(descriptor, 0)
        os.pwrite(descriptor, f"{os.getpid()}\n".encode("ascii"), 0)
        yield lock_path
    finally:
        os.close(descriptor)


def backup_path(database: Path, before: SchemaStatus, now: datetime) -> Path:
    """Unique per migration: UTC time to the microsecond and both versions."""
    stamp = now.astimezone(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    name = f"{database.stem}.{stamp}.v{before.applied_version}-to-v{before.code_version}.db"
    return database.parent / BACKUP_DIRECTORY_NAME / name


def _verify_backup(copy: Path, before: SchemaStatus) -> None:
    """The copy is a sound, single-file database holding the same ledger."""
    with closing(sqlite3.connect(str(copy))) as db:
        # A standalone file (no -wal beside it) that restores by copying alone.
        db.execute("PRAGMA journal_mode=DELETE").fetchone()
        result = db.execute("PRAGMA integrity_check").fetchone()
    if result is None or result[0] != "ok":
        raise RuntimeError(f"backup integrity check failed: {result}")
    copied = inspect_schema(copy)
    if copied.problems or copied.applied != before.applied:
        raise RuntimeError("backup ledger differs from the database being migrated")


async def make_backup(database: Path, before: SchemaStatus, now: datetime) -> Path:
    """A verified backup through SQLite's backup API, never overwriting one."""
    target = backup_path(database, before, now)
    partial = target.with_name(target.name + PARTIAL_SUFFIX)
    try:
        ensure_private_directory(target.parent)
        if target.exists() or partial.exists():
            raise FileExistsError(f"refusing to overwrite a backup: {target}")
        source_uri = f"file:{quote(str(database.resolve()))}?mode=ro"
        async with aiosqlite.connect(source_uri, uri=True) as source:
            await backup_database(source, partial)
        _verify_backup(partial, before)
        os.chmod(partial, FILE_MODE)
        # link + unlink: atomic, and fails instead of replacing an existing file.
        os.link(partial, target)
        partial.unlink()
    except Exception as exc:  # noqa: BLE001 - every failure refuses the migration
        try:
            partial.unlink(missing_ok=True)
        except OSError:
            pass
        raise MigrateRefused(
            MIGRATE_BACKUP_FAILED, f"{type(exc).__name__}: {exc}; nothing was migrated"
        ) from exc
    return target


async def _apply(database: Path) -> None:
    from config import cfg
    from db.core import init_db

    original = cfg.DB_PATH
    cfg.DB_PATH = str(database)
    try:
        await init_db()
    finally:
        cfg.DB_PATH = original


def _pending_names(status: SchemaStatus) -> str:
    names = {migration.version: migration.name for migration in MIGRATIONS}
    return ", ".join(f"{version} {names.get(version, '?')}" for version in status.pending)


async def _migrate(
    database: Path, *, now: datetime, allow_during_session: bool, out: TextIO
) -> int:
    before = inspect_schema(database)
    print(f"database  {database}", file=out)
    print(f"schema    v{before.applied_version} (this code knows v{before.code_version})", file=out)
    if before.problems:
        raise MigrateRefused(
            MIGRATE_SCHEMA_PROBLEM,
            f"{before.describe()}; nothing was changed. Deploy the matching code or restore"
            " the pre-migration backup",
        )
    if not before.pending:
        print("nothing to migrate: the schema is current", file=out)
        return EXIT_MIGRATED
    day = trading_day_at(now)
    if day is not None:
        if not allow_during_session:
            raise MigrateRefused(
                MIGRATE_REFUSED_SESSION_WINDOW,
                f"now is inside {day.describe()}, while the supervisor runs children; migrate"
                f" after {day.ends_at.astimezone(MARKET_TIMEZONE):%H:%M} ET, or pass"
                f" {SESSION_OVERRIDE_FLAG} if it cannot wait",
            )
        print(f"WARNING: migrating inside {day.describe()} ({SESSION_OVERRIDE_FLAG})", file=out)
    if before.exists:
        backup = await make_backup(database, before, now)
        print(f"backup    {backup}", file=out)
    else:
        backup = None
        database.parent.mkdir(parents=True, exist_ok=True)
        print("backup    none: a new database has nothing to back up", file=out)
    print(f"applying  {_pending_names(before)}", file=out)
    try:
        await _apply(database)
    except Exception as exc:  # noqa: BLE001 - reported with the backup's location
        kept = f"; backup: {backup}" if backup else ""
        raise MigrateRefused(
            MIGRATE_FAILED, f"{type(exc).__name__}: {exc}; the transaction was rolled back{kept}"
        ) from exc
    after = inspect_schema(database)
    if not after.current:
        raise MigrateRefused(MIGRATE_FAILED, f"schema not current after migrating: {after.describe()}")
    print(f"migrated  v{before.applied_version} -> v{after.applied_version}", file=out)
    return EXIT_MIGRATED


def run_migrate(
    database: Path,
    *,
    now: datetime | None = None,
    allow_during_session: bool = False,
    out: TextIO | None = None,
    err: TextIO | None = None,
) -> int:
    """``autotraderctl db migrate``: 0 when migrated (or nothing pending), 1 when refused."""
    stream = out or sys.stdout
    path = database.expanduser()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with migration_lock(path):
            return asyncio.run(
                _migrate(
                    path,
                    now=now or datetime.now(UTC),
                    allow_during_session=allow_during_session,
                    out=stream,
                )
            )
    except MigrateRefused as refused:
        stream.flush()  # what was checked prints before why it stopped
        print(f"error: {refused}", file=err or sys.stderr, flush=True)
        return EXIT_MIGRATE_REFUSED


def run_status(database: Path, *, now: datetime | None = None, out: TextIO | None = None) -> int:
    """``autotraderctl db status``: read-only; 0 when current, 75 when not."""
    stream = out or sys.stdout
    status = inspect_schema(database.expanduser())
    print(f"database  {status.database}", file=stream)
    print(f"state     {status.describe()}", file=stream)
    if status.pending:
        print(
            f"pending   {_pending_names(status)}: the screener, strategies and recorder refuse"
            " to start (exit 75) until `autotraderctl db migrate`",
            file=stream,
        )
    day = trading_day_at(now or datetime.now(UTC))
    if day is None:
        print("migrate   allowed now (outside every supervised trading day)", file=stream)
    else:
        print(
            f"migrate   refused now: inside {day.describe()} (override: {SESSION_OVERRIDE_FLAG})",
            file=stream,
        )
    return 0 if status.current else EXIT_SCHEMA_NOT_READY


__all__ = [
    "EXIT_MIGRATED",
    "EXIT_MIGRATE_REFUSED",
    "MigrateRefused",
    "SESSION_OVERRIDE_FLAG",
    "TradingDay",
    "backup_path",
    "make_backup",
    "migration_lock",
    "run_migrate",
    "run_status",
    "trading_day",
    "trading_day_at",
]

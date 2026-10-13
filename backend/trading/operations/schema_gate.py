"""Children never migrate: they refuse to start until the schema is the code's.

A migration applied implicitly at a child's start ran without a lock, a
backup or a time window, and two children starting at 07:55 could race it.
Now only ``autotraderctl db migrate`` changes the schema. Every process that
reads or writes the database checks the migration ledger first, read-only,
and refuses unless every migration the code knows is applied with its
recorded checksum and nothing else is:

* one CRITICAL line with the named token (``schema_migration_pending``, or
  ``schema_checksum_mismatch`` / ``schema_newer_than_code`` /
  ``schema_history_gap`` / ``schema_unreadable``);
* exit 75 (EX_TEMPFAIL). No supervised child lists 75 as final, so the
  supervisor keeps restarting it with backoff (at most every 5 minutes)
  and it recovers by itself once the owner has migrated.

Two processes keep running while a migration is merely pending, because
they use no table it adds: the notifier (read-only; it tells the phone once
per session) and the Telegram assistant, which needs only migrations up to
``TELEGRAM_ASSISTANT_REQUIRES`` and keeps the owner's /stop kill switch and
manual exits available. Both still stop on any other schema problem; the
alerts are in ``trading.operations.schema_alert``.
"""
from __future__ import annotations

import logging
from pathlib import Path
import sys
from typing import TextIO

from config import cfg
from trading.storage.migrations import (
    SCHEMA_MIGRATION_PENDING,
    SchemaStatus,
    inspect_schema,
)

# EX_TEMPFAIL: temporary, retried by the supervisor; never a final exit code.
EXIT_SCHEMA_NOT_READY = 75
MIGRATE_COMMAND = "autotraderctl db migrate"
# The newest migration the Telegram assistant's own tables come from: the
# manual-order ledger, Telegram updates, chat usage and the bridge heartbeat
# (12); the kill-switch fence and the decision and screener tables are older
# (a test pins every table its modules name). It starts while only newer
# migrations are pending, so /stop and manual exits never wait for one.
# NOTE for later phases: an assistant command that needs a table added by a
# newer migration must check the schema itself and refuse that one command
# gracefully while its migration is pending (a reply that names
# `autotraderctl db migrate`). Raising this number instead would take /stop
# down with every pending migration.
TELEGRAM_ASSISTANT_REQUIRES = 12

logger = logging.getLogger("autotrader.shadow.schema")


class SchemaNotReady(RuntimeError):
    """The database is not at exactly this code's schema; the process must not start."""

    def __init__(self, status: SchemaStatus, *, process: str) -> None:
        self.status = status
        self.process = process
        self.token = status.token or SCHEMA_MIGRATION_PENDING
        super().__init__(
            f"{self.token}: {process} refuses to start: {status.describe()} ({status.database})"
        )


def tolerates_pending(status: SchemaStatus, *, requires: int) -> bool:
    """Only migrations newer than ``requires`` are missing, and nothing else is wrong.

    Every migration up to ``requires`` is applied with its recorded checksum;
    no unknown version, gap or unreadable ledger.
    """
    return (
        status.exists
        and not status.problems
        and bool(status.pending)
        and min(status.pending) > requires
    )


def require_current_schema(
    *, process: str, database: str | Path | None = None, requires: int | None = None
) -> SchemaStatus:
    """Return the status when usable; otherwise log CRITICAL and raise ``SchemaNotReady``.

    Usable means current, or, for a process that names the newest migration
    it ``requires``, missing only newer migrations (one WARNING line; the
    caller still alerts). Read-only: the database is never created,
    migrated or written here.
    """
    status = inspect_schema(cfg.DB_PATH if database is None else database)
    if status.current:
        return status
    if requires is not None and tolerates_pending(status, requires=requires):
        logger.warning(
            "%s: %s starts anyway: it needs migrations up to %d only (%s)",
            SCHEMA_MIGRATION_PENDING,
            process,
            requires,
            status.describe(),
        )
        return status
    error = SchemaNotReady(status, process=process)
    logger.critical("%s; run `%s` after the close", error, MIGRATE_COMMAND, stack_info=True)
    raise error


def refusal_exit(error: SchemaNotReady, *, err: TextIO | None = None) -> int:
    """Report a refusal on stderr (the child's own log) and return exit 75."""
    print(
        f"error: {error}; run `{MIGRATE_COMMAND}` after the close",
        file=err or sys.stderr,
        flush=True,
    )
    return EXIT_SCHEMA_NOT_READY


__all__ = [
    "EXIT_SCHEMA_NOT_READY",
    "MIGRATE_COMMAND",
    "TELEGRAM_ASSISTANT_REQUIRES",
    "SchemaNotReady",
    "refusal_exit",
    "require_current_schema",
    "tolerates_pending",
]

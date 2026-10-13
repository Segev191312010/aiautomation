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

The notifier is the one exception: it only reads tables that exist before
any pending migration, keeps running, and tells the phone once per session
(``trading.operations.schema_alert``).
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


def require_current_schema(*, process: str, database: str | Path | None = None) -> SchemaStatus:
    """Return the status when current; otherwise log CRITICAL and raise ``SchemaNotReady``.

    Read-only: the database is never created, migrated or written here.
    """
    status = inspect_schema(cfg.DB_PATH if database is None else database)
    if status.current:
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
    "SchemaNotReady",
    "refusal_exit",
    "require_current_schema",
]

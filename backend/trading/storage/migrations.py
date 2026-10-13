"""Transactional, checksummed migrations for the Autotrader V1 SQLite domain.

Only ``autotraderctl db migrate`` applies migrations (with a lock and a
verified backup). Every other process reads the ledger with
:func:`inspect_schema` and refuses to start unless the schema is exactly the
code's: no process migrates implicitly at start.
"""
from __future__ import annotations

from collections.abc import Iterable
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import inspect
import json
from pathlib import Path
import sqlite3
from typing import Awaitable, Callable
from urllib.parse import quote
from uuid import uuid4

import aiosqlite

from trading.contracts import FixedUniverse, StrategySpec
from trading.storage.broker_inbox import BROKER_INBOX_SCHEMA, BROKER_SESSION_SCHEMA
from trading.storage.schema import (
    AUTHORITY_RECEIPT_ATTESTATION_SCHEMA_STATEMENTS,
    AUTHORITY_SCHEMA_STATEMENTS,
    EXECUTION_HARDENING_SCHEMA_STATEMENTS,
    EXECUTION_QUALIFIED_IDENTITY_SCHEMA_STATEMENTS,
    EXECUTION_SCHEMA_STATEMENTS,
    MIGRATION_LEDGER_STATEMENTS,
    RUN_SCHEMA_STATEMENTS,
    SHADOW_RUNTIME_SCHEMA_STATEMENTS,
    STRATEGY_SCHEMA_STATEMENTS,
)
from trading.storage.live_market import LIVE_MARKET_SCHEMA
from trading.storage.manager_schema import MANAGER_SETUPS_SCHEMA
from trading.storage.manual_orders_schema import MANUAL_ORDERS_SCHEMA


MigrationApply = Callable[[aiosqlite.Connection], Awaitable[None]]


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    signature: str
    apply: MigrationApply
    schema_payload: tuple[str, ...] = ()

    @property
    def checksum(self) -> str:
        try:
            implementation = inspect.getsource(self.apply)
        except (OSError, TypeError):
            implementation = getattr(self.apply, "__qualname__", repr(self.apply))
        payload = json.dumps(
            {
                "version": self.version,
                "name": self.name,
                "signature": self.signature,
                "schema": self.schema_payload,
                "implementation": implementation,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return sha256(payload.encode()).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _execute_all(db: aiosqlite.Connection, statements: tuple[str, ...]) -> None:
    for statement in statements:
        await db.execute(statement)


async def _table_exists(db: aiosqlite.Connection, table: str) -> bool:
    async with db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ) as cursor:
        return await cursor.fetchone() is not None


async def _archive_table(db: aiosqlite.Connection, table: str) -> None:
    if not await _table_exists(db, table):
        return
    async with db.execute(f'SELECT * FROM "{table}"') as cursor:
        columns = tuple(item[0] for item in cursor.description or ())
        rows = await cursor.fetchall()
    for index, row in enumerate(rows):
        payload = dict(zip(columns, row, strict=True))
        source_pk = str(payload.get("id", index))
        await db.execute(
            """
            INSERT OR IGNORE INTO autotrader_migration_archive
            (source_table, source_pk, payload_json, archived_at) VALUES (?, ?, ?, ?)
            """,
            (table, source_pk, json.dumps(payload, sort_keys=True, default=str), _now()),
        )


async def _retire_table(
    db: aiosqlite.Connection, table: str, retired_table: str
) -> None:
    if not await _table_exists(db, table):
        return
    if await _table_exists(db, retired_table):
        raise RuntimeError(f"retired table already exists: {retired_table}")
    await _archive_table(db, table)
    await db.execute(f'ALTER TABLE "{table}" RENAME TO "{retired_table}"')
    if await _table_exists(db, table) or not await _table_exists(db, retired_table):
        raise RuntimeError(f"failed to retire superseded table: {table}")


def _upgrade_legacy_spec(payload: dict[str, object]) -> StrategySpec:
    data = dict(payload)
    data.pop("id", None)
    data.pop("min_reward_to_risk", None)
    symbols = data.pop("symbols", None)
    if "universe" not in data:
        if not isinstance(symbols, list):
            raise ValueError("legacy strategy is missing symbols")
        data["universe"] = FixedUniverse(symbols=tuple(str(value) for value in symbols)).model_dump()
    return StrategySpec.model_validate(data)


async def _strategy_version_freeze(db: aiosqlite.Connection) -> None:
    await _execute_all(db, STRATEGY_SCHEMA_STATEMENTS)
    if await _table_exists(db, "autotrader_strategies"):
        async with db.execute(
            """
            SELECT id, owner_id, version, state, spec_json, created_at, updated_at
            FROM autotrader_strategies
            """
        ) as cursor:
            rows = await cursor.fetchall()
        for strategy_id, owner_id, version, state, spec_json, created_at, updated_at in rows:
            await db.execute(
                "INSERT OR IGNORE INTO autotrader_migration_archive VALUES (?, ?, ?, ?)",
                (
                    "autotrader_strategies",
                    str(strategy_id),
                    json.dumps(
                        {
                            "id": strategy_id,
                            "owner_id": owner_id,
                            "version": version,
                            "state": state,
                            "spec_json": spec_json,
                            "created_at": created_at,
                            "updated_at": updated_at,
                        },
                        sort_keys=True,
                    ),
                    _now(),
                ),
            )
            spec = _upgrade_legacy_spec(json.loads(str(spec_json)))
            content_hash = spec.fingerprint()
            safe_state = str(state)
            if safe_state not in {"draft", "backtesting", "shadow", "paused", "retired"}:
                safe_state = "paused"
            await db.execute(
                """
                INSERT OR IGNORE INTO autotrader_strategy_identities
                (id, owner_id, created_at, retired_at) VALUES (?, ?, ?, NULL)
                """,
                (strategy_id, owner_id, created_at),
            )
            await db.execute(
                """
                INSERT OR IGNORE INTO autotrader_strategy_versions
                (strategy_id, version, content_hash, spec_json, created_at, created_by)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (strategy_id, version, content_hash, json.dumps(spec.model_dump(mode="json"), sort_keys=True), created_at, owner_id),
            )
            await db.execute(
                """
                INSERT OR IGNORE INTO autotrader_strategy_lifecycle_v2
                (strategy_id, version, state, revision, updated_at) VALUES (?, ?, ?, 0, ?)
                """,
                (strategy_id, version, safe_state, updated_at),
            )
            await db.execute(
                """
                INSERT OR IGNORE INTO autotrader_strategy_events_v2
                (id, strategy_id, strategy_version, owner_id, event_type, from_state, to_state,
                 actor, reason, expected_revision, new_revision, created_at)
                VALUES (?, ?, ?, ?, 'migrated', ?, ?, ?, ?, NULL, 0, ?)
                """,
                (
                    str(uuid4()), strategy_id, version, owner_id, state, safe_state, owner_id,
                    "migrated from execution-disabled V1 baseline", updated_at,
                ),
            )

    for table, retired_table in (
        ("autotrader_strategies", "autotrader_retired_strategies"),
        ("autotrader_strategy_events", "autotrader_retired_strategy_events"),
        ("autotrader_decisions", "autotrader_retired_decisions"),
        ("autotrader_live_approvals", "autotrader_retired_live_approvals"),
    ):
        await _retire_table(db, table, retired_table)


async def _run_data_ledger(db: aiosqlite.Connection) -> None:
    await _execute_all(db, RUN_SCHEMA_STATEMENTS)


async def _authority_ledger(db: aiosqlite.Connection) -> None:
    await _execute_all(db, AUTHORITY_SCHEMA_STATEMENTS)


async def _authority_receipt_attestation_ledger(db: aiosqlite.Connection) -> None:
    await _execute_all(db, AUTHORITY_RECEIPT_ATTESTATION_SCHEMA_STATEMENTS)


async def _execution_ledger(db: aiosqlite.Connection) -> None:
    await _execute_all(db, EXECUTION_SCHEMA_STATEMENTS)


async def _table_columns(db: aiosqlite.Connection, table: str) -> set[str]:
    async with db.execute(f'PRAGMA table_info("{table}")') as cursor:
        rows = await cursor.fetchall()
    return {str(row[1]) for row in rows}


async def _execution_hardening_ledger(db: aiosqlite.Connection) -> None:
    group_columns = await _table_columns(db, "autotrader_bracket_groups")
    if "oca_group" not in group_columns:
        await db.execute("ALTER TABLE autotrader_bracket_groups ADD COLUMN oca_group TEXT NULL")
    leg_columns = await _table_columns(db, "autotrader_bracket_legs")
    if "parent_order_id" not in leg_columns:
        await db.execute(
            "ALTER TABLE autotrader_bracket_legs ADD COLUMN parent_order_id TEXT NULL"
        )
    await _execute_all(db, EXECUTION_HARDENING_SCHEMA_STATEMENTS)


async def _shadow_runtime_ledger(db: aiosqlite.Connection) -> None:
    ai_call_columns = await _table_columns(db, "autotrader_ai_calls")
    additions = (
        ("prompt_hash", "TEXT NULL"),
        ("pricing_hash", "TEXT NULL"),
        ("input_tokens", "INTEGER NULL CHECK(input_tokens IS NULL OR input_tokens >= 0)"),
        ("output_tokens", "INTEGER NULL CHECK(output_tokens IS NULL OR output_tokens >= 0)"),
        (
            "cached_input_tokens",
            "INTEGER NULL CHECK(cached_input_tokens IS NULL OR cached_input_tokens >= 0)",
        ),
    )
    for column, declaration in additions:
        if column not in ai_call_columns:
            await db.execute(
                f"ALTER TABLE autotrader_ai_calls ADD COLUMN {column} {declaration}"
            )
    await _execute_all(db, SHADOW_RUNTIME_SCHEMA_STATEMENTS)


async def _broker_inbox(db: aiosqlite.Connection) -> None:
    await _execute_all(db, BROKER_INBOX_SCHEMA)


async def _broker_sessions(db: aiosqlite.Connection) -> None:
    await _execute_all(db, BROKER_SESSION_SCHEMA)


async def _live_market_universe(db: aiosqlite.Connection) -> None:
    await _execute_all(db, LIVE_MARKET_SCHEMA)


async def _manual_orders_ledger(db: aiosqlite.Connection) -> None:
    await _execute_all(db, MANUAL_ORDERS_SCHEMA)


async def _manager_setups_ledger(db: aiosqlite.Connection) -> None:
    await _execute_all(db, MANAGER_SETUPS_SCHEMA)


async def _qualified_bracket_identity(db: aiosqlite.Connection) -> None:
    columns = await _table_columns(db, "autotrader_bracket_groups")
    additions = (
        ("con_id", "INTEGER NULL"),
        ("primary_exchange", "TEXT NULL"),
        ("currency", "TEXT NULL"),
        ("security_type", "TEXT NULL"),
    )
    for column, declaration in additions:
        if column not in columns:
            await db.execute(
                f"ALTER TABLE autotrader_bracket_groups ADD COLUMN {column} {declaration}"
            )
    await db.execute(
        """UPDATE autotrader_bracket_groups
        SET con_id=(SELECT candidate.con_id FROM autotrader_candidates candidate
                    WHERE candidate.id=autotrader_bracket_groups.candidate_id),
            primary_exchange=(SELECT json_extract(candidate.contract_json, '$.primary_exchange')
                              FROM autotrader_candidates candidate
                              WHERE candidate.id=autotrader_bracket_groups.candidate_id),
            currency=(SELECT json_extract(candidate.contract_json, '$.currency')
                      FROM autotrader_candidates candidate
                      WHERE candidate.id=autotrader_bracket_groups.candidate_id),
            security_type=(SELECT json_extract(candidate.contract_json, '$.security_type')
                           FROM autotrader_candidates candidate
                           WHERE candidate.id=autotrader_bracket_groups.candidate_id)
        WHERE con_id IS NULL"""
    )
    async with db.execute(
        """SELECT COUNT(*) FROM autotrader_bracket_groups
        WHERE con_id IS NULL OR con_id <= 0
           OR primary_exchange IS NULL OR trim(primary_exchange)=''
           OR currency IS NULL OR currency != 'USD'
           OR security_type IS NULL OR security_type != 'STK'"""
    ) as cursor:
        unresolved = int((await cursor.fetchone())[0])
    if unresolved:
        raise RuntimeError(
            "qualified bracket identity could not be recovered; PAPER reconciliation is blocked"
        )
    await _execute_all(db, EXECUTION_QUALIFIED_IDENTITY_SCHEMA_STATEMENTS)


MIGRATIONS = (
    Migration(
        1,
        "strategy-version-freeze",
        "v1-strategy-version-freeze-20260827",
        _strategy_version_freeze,
        MIGRATION_LEDGER_STATEMENTS + STRATEGY_SCHEMA_STATEMENTS,
    ),
    Migration(
        2,
        "run-data-decision-ledger",
        "v1-run-data-decision-ledger-20260827",
        _run_data_ledger,
        RUN_SCHEMA_STATEMENTS,
    ),
    Migration(
        3,
        "authority-risk-ledger",
        "v1-authority-risk-ledger-20260827",
        _authority_ledger,
        AUTHORITY_SCHEMA_STATEMENTS,
    ),
    Migration(
        4,
        "paper-execution-ledger",
        "v1-paper-execution-ledger-20260827",
        _execution_ledger,
        EXECUTION_SCHEMA_STATEMENTS,
    ),
    Migration(
        5,
        "authority-receipt-attestation-ledger",
        "v1-authority-receipt-attestation-ledger-20260827",
        _authority_receipt_attestation_ledger,
        AUTHORITY_RECEIPT_ATTESTATION_SCHEMA_STATEMENTS,
    ),
    Migration(
        6,
        "paper-execution-hardening",
        "v1-paper-execution-hardening-20260827",
        _execution_hardening_ledger,
        EXECUTION_HARDENING_SCHEMA_STATEMENTS,
    ),
    Migration(
        7,
        "shadow-runtime-ledger",
        "v1-shadow-runtime-ledger-20260831",
        _shadow_runtime_ledger,
        SHADOW_RUNTIME_SCHEMA_STATEMENTS,
    ),
    Migration(8, "broker-observation-inbox", "v1-broker-inbox-20260907", _broker_inbox, BROKER_INBOX_SCHEMA),
    Migration(9, "broker-session-ledger", "v1-broker-sessions-20260908", _broker_sessions, BROKER_SESSION_SCHEMA),
    Migration(
        10, "qualified-bracket-identity", "v1-qualified-bracket-identity-20260908",
        _qualified_bracket_identity, EXECUTION_QUALIFIED_IDENTITY_SCHEMA_STATEMENTS,
    ),
    Migration(
        11, "live-market-universe", "v1-live-market-universe-20261012",
        _live_market_universe, LIVE_MARKET_SCHEMA,
    ),
    Migration(
        12, "manual-orders-ledger", "v1-manual-orders-ledger-20261012",
        _manual_orders_ledger, MANUAL_ORDERS_SCHEMA,
    ),
    # Phase 0: only the formed setups the pipeline writes. The other day-
    # manager tables stay unregistered drafts in manager_schema.py until
    # their phase lands (14: phase 1, 15: phase 2), so nothing unsettled is
    # frozen. The unmerged C2 branch also numbered its ledger 13; it
    # renumbers when it merges after its own review.
    Migration(
        13, "manager-setups", "v1-manager-setups-20261013",
        _manager_setups_ledger, MANAGER_SETUPS_SCHEMA,
    ),
)


# Named reasons a database is not usable by this code. A child that finds one
# refuses to start; `autotraderctl db status` prints them.
SCHEMA_MIGRATION_PENDING = "schema_migration_pending"
SCHEMA_CHECKSUM_MISMATCH = "schema_checksum_mismatch"
SCHEMA_NEWER_THAN_CODE = "schema_newer_than_code"
SCHEMA_HISTORY_GAP = "schema_history_gap"
SCHEMA_UNREADABLE = "schema_unreadable"
# A read-only check never waits long: WAL readers are not blocked by writers.
SCHEMA_READ_TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True, slots=True)
class SchemaProblem:
    """A recorded history this code must not run on (named token + detail)."""

    token: str
    detail: str


@dataclass(frozen=True, slots=True)
class SchemaStatus:
    """A database's migration ledger compared with the code's migrations."""

    database: str
    exists: bool
    applied: tuple[int, ...]
    known: tuple[int, ...]
    problems: tuple[SchemaProblem, ...] = ()

    @property
    def pending(self) -> tuple[int, ...]:
        done = set(self.applied)
        return tuple(version for version in self.known if version not in done)

    @property
    def current(self) -> bool:
        """Exactly the code's schema: every known migration applied, nothing else."""
        return self.exists and not self.problems and not self.pending

    @property
    def token(self) -> str | None:
        if self.problems:
            return self.problems[0].token
        if self.pending:
            return SCHEMA_MIGRATION_PENDING
        return None

    @property
    def applied_version(self) -> int:
        return max(self.applied, default=0)

    @property
    def code_version(self) -> int:
        return max(self.known, default=0)

    def describe(self) -> str:
        """One line for logs, the terminal and the phone."""
        if self.current:
            return f"schema v{self.applied_version} is current"
        if self.problems:
            return "; ".join(f"{problem.token}: {problem.detail}" for problem in self.problems)
        state = f"schema v{self.applied_version}" if self.exists else "no database file"
        pending = ", ".join(str(version) for version in self.pending)
        return f"{state}, this code needs v{self.code_version} (pending: {pending})"


def _history_problems(
    rows: Iterable[tuple[object, object, object]],
) -> tuple[frozenset[int], tuple[SchemaProblem, ...]]:
    """Compare a ledger's ``(version, name, checksum)`` rows with ``MIGRATIONS``.

    The one comparison behind both :func:`apply_migrations` (which refuses on
    the first problem) and :func:`inspect_schema` (which reports them), so a
    child's start check and the migrate command cannot disagree.
    """
    expected = {migration.version: migration for migration in MIGRATIONS}
    applied: set[int] = set()
    problems: list[SchemaProblem] = []
    for version, name, checksum in rows:
        number = int(str(version))
        applied.add(number)
        migration = expected.get(number)
        if migration is None:
            problems.append(
                SchemaProblem(
                    SCHEMA_NEWER_THAN_CODE,
                    f"database migration {number} is newer than this application",
                )
            )
        elif str(name) != migration.name or str(checksum) != migration.checksum:
            problems.append(
                SchemaProblem(SCHEMA_CHECKSUM_MISMATCH, f"database migration {number} checksum mismatch")
            )
    if applied and applied != set(range(1, max(applied) + 1)):
        problems.append(
            SchemaProblem(SCHEMA_HISTORY_GAP, "database migration history contains a gap")
        )
    return frozenset(applied), tuple(problems)


def inspect_schema(database: str | Path) -> SchemaStatus:
    """Read a database's migration ledger through a read-only handle.

    Never creates, migrates or writes the database: a missing file reports
    every migration as pending, and an unreadable one as ``schema_unreadable``.
    """
    known = tuple(migration.version for migration in MIGRATIONS)
    if str(database) == ":memory:":
        return SchemaStatus(database=":memory:", exists=False, applied=(), known=known)
    path = Path(database).expanduser()
    if not path.is_file():
        return SchemaStatus(database=str(path), exists=False, applied=(), known=known)
    uri = f"file:{quote(str(path.resolve()))}?mode=ro"
    try:
        with closing(sqlite3.connect(uri, uri=True, timeout=SCHEMA_READ_TIMEOUT_SECONDS)) as db:
            ledger = db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='autotrader_schema_migrations'"
            ).fetchone()
            rows = (
                db.execute(
                    "SELECT version, name, checksum FROM autotrader_schema_migrations ORDER BY version"
                ).fetchall()
                if ledger is not None
                else []
            )
        applied, problems = _history_problems(rows)
    except (sqlite3.Error, ValueError, TypeError) as exc:
        # A refusal with a reason, never a crash: the child still exits 75.
        return SchemaStatus(
            database=str(path),
            exists=True,
            applied=(),
            known=known,
            problems=(
                SchemaProblem(SCHEMA_UNREADABLE, f"database unreadable: {type(exc).__name__}: {exc}"),
            ),
        )
    return SchemaStatus(
        database=str(path),
        exists=True,
        applied=tuple(sorted(applied)),
        known=known,
        problems=problems,
    )


async def backup_database(db: aiosqlite.Connection, destination: str | Path) -> Path:
    """Create a non-overwriting SQLite backup before applying migrations."""
    backup_path = Path(destination)
    if backup_path.exists():
        raise FileExistsError(f"refusing to overwrite migration backup: {backup_path}")
    if not backup_path.parent.is_dir():
        raise FileNotFoundError(f"migration backup directory does not exist: {backup_path.parent}")
    if db.in_transaction:
        raise RuntimeError("migration backup requires no active transaction")
    async with aiosqlite.connect(str(backup_path)) as backup:
        await db.backup(backup)
        await backup.commit()
    return backup_path


async def apply_migrations(
    db: aiosqlite.Connection, *, backup_path: str | Path | None = None
) -> None:
    """Apply every pending migration in one transaction.

    Called only by ``autotraderctl db migrate`` (through ``db.core.init_db``)
    and by tests; a source-level test keeps every runtime entry point away.
    """
    if backup_path is not None:
        await backup_database(db, backup_path)
    await db.execute("BEGIN IMMEDIATE")
    try:
        await _execute_all(db, MIGRATION_LEDGER_STATEMENTS)
        async with db.execute(
            "SELECT version, name, checksum FROM autotrader_schema_migrations ORDER BY version"
        ) as cursor:
            applied_rows = await cursor.fetchall()
        applied_versions, problems = _history_problems(applied_rows)
        if problems:
            raise RuntimeError(problems[0].detail)

        for migration in MIGRATIONS:
            if migration.version in applied_versions:
                continue
            await migration.apply(db)
            await db.execute(
                "INSERT INTO autotrader_schema_migrations VALUES (?, ?, ?, ?)",
                (migration.version, migration.name, migration.checksum, _now()),
            )
        async with db.execute("PRAGMA foreign_key_check") as cursor:
            violations = await cursor.fetchall()
        if violations:
            raise RuntimeError(f"foreign key violations after migration: {violations}")
        await db.commit()
    except BaseException:
        await db.rollback()
        raise

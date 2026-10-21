"""Migration 12 (manual-order ledger) applies cleanly and its checksum never drifts."""
from __future__ import annotations

from pathlib import Path
import sqlite3

import aiosqlite
import pytest

from config import cfg
from db.core import init_db
import trading.storage.migrations as migrations
from trading.storage.manager_schema import MANAGER_TABLES
from trading.storage.manual_orders_schema import MANUAL_ORDERS_SCHEMA


LIVE_DB = Path.home() / ".local" / "share" / "autotrader-v1" / "autotrader.db"
# Pinned because migration 12 is applied to the owner's live database: any edit
# to its schema or apply function would make every later start fail closed
# with "checksum mismatch". Change the schema only through a new migration.
MIGRATION_12_CHECKSUM = "e5e4c9c28b8fdeeb1511f9f35e807f8d13eb2a449e8bd86bd22c28305351611b"
NEW_TABLES = {
    "autotrader_manual_orders",
    "autotrader_telegram_updates",
    "autotrader_chat_usage",
    "autotrader_bridge_heartbeat",
}
NEW_TRIGGERS = {
    "autotrader_manual_orders_intent_immutable",
    "autotrader_manual_orders_status_monotonic",
    "autotrader_manual_orders_fills_monotonic",
    "autotrader_manual_orders_broker_identity_immutable",
    "autotrader_manual_orders_cancel_one_way",
    "autotrader_manual_orders_no_delete",
}
NEW_INDEXES = {"idx_autotrader_manual_orders_status"}


def _migration_12() -> migrations.Migration:
    return next(item for item in migrations.MIGRATIONS if item.version == 12)


def test_migration_12_identity_and_checksum_are_pinned() -> None:
    migration = _migration_12()
    assert (migration.version, migration.name, migration.signature) == (
        12, "manual-orders-ledger", "v1-manual-orders-ledger-20261012"
    )
    assert migration.schema_payload == MANUAL_ORDERS_SCHEMA
    assert migration.checksum == MIGRATION_12_CHECKSUM
    assert migration.checksum == _migration_12().checksum  # stable across reads
    assert [item.version for item in migrations.MIGRATIONS][:12] == list(range(1, 13))


async def _schema(path: Path) -> dict[str, set[str]]:
    async with aiosqlite.connect(path) as db:
        async with db.execute("SELECT type, name FROM sqlite_master") as cursor:
            rows = await cursor.fetchall()
    objects: dict[str, set[str]] = {}
    for kind, name in rows:
        objects.setdefault(str(kind), set()).add(str(name))
    return objects


async def _ledger(path: Path) -> list[tuple[int, str, str]]:
    async with aiosqlite.connect(path) as db:
        async with db.execute(
            "SELECT version, checksum, applied_at FROM autotrader_schema_migrations ORDER BY version"
        ) as cursor:
            return [(int(row[0]), str(row[1]), str(row[2])) for row in await cursor.fetchall()]


async def _row_counts(path: Path) -> dict[str, int]:
    async with aiosqlite.connect(path) as db:
        async with db.execute("SELECT name FROM sqlite_master WHERE type='table'") as cursor:
            tables = [str(row[0]) for row in await cursor.fetchall()]
        counts: dict[str, int] = {}
        for table in tables:
            async with db.execute(f'SELECT COUNT(*) FROM "{table}"') as cursor:
                row = await cursor.fetchone()
            counts[table] = int(row[0]) if row is not None else 0
    return counts


async def _integrity(path: Path) -> tuple[str, list[object]]:
    async with aiosqlite.connect(path) as db:
        async with db.execute("PRAGMA integrity_check") as cursor:
            integrity = await cursor.fetchone()
        async with db.execute("PRAGMA foreign_key_check") as cursor:
            violations = list(await cursor.fetchall())
    assert integrity is not None
    return str(integrity[0]), violations


def _assert_migration_12_present(schema: dict[str, set[str]]) -> None:
    assert NEW_TABLES <= schema.get("table", set())
    assert NEW_TRIGGERS <= schema.get("trigger", set())
    assert NEW_INDEXES <= schema.get("index", set())


@pytest.mark.anyio
async def test_fresh_database_gets_every_migration(tmp_path, monkeypatch) -> None:
    path = tmp_path / "fresh.db"
    monkeypatch.setattr(cfg, "DB_PATH", str(path))
    await init_db()
    first = await _ledger(path)
    await init_db()
    assert await _ledger(path) == first  # idempotent: nothing re-applied
    assert [version for version, _, _ in first] == [item.version for item in migrations.MIGRATIONS]
    assert [checksum for _, checksum, _ in first] == [item.checksum for item in migrations.MIGRATIONS]
    _assert_migration_12_present(await _schema(path))
    assert await _integrity(path) == ("ok", [])


@pytest.mark.anyio
async def test_migration_12_applies_on_top_of_a_version_11_database(tmp_path, monkeypatch) -> None:
    path = tmp_path / "v11.db"
    full = migrations.MIGRATIONS
    monkeypatch.setattr(migrations, "MIGRATIONS", full[:11])
    async with aiosqlite.connect(path) as db:
        await migrations.apply_migrations(db)
        await db.execute(
            "INSERT INTO autotrader_universe VALUES (?,?,?,?,?,?,?,?,?)",
            ("watchlist", "AAPL", "NASDAQ", "AAPL", 265598, "NASDAQ", "resolved", None, "t"),
        )
        await db.commit()
    assert [version for version, _, _ in await _ledger(path)] == list(range(1, 12))
    assert "autotrader_manual_orders" not in (await _schema(path)).get("table", set())
    monkeypatch.setattr(migrations, "MIGRATIONS", full[:12])
    backup = tmp_path / "before-migration-12.db"
    async with aiosqlite.connect(path) as db:
        await migrations.apply_migrations(db, backup_path=backup)
    ledger = await _ledger(path)
    assert [version for version, _, _ in ledger] == list(range(1, 13))
    assert ledger[-1][1] == MIGRATION_12_CHECKSUM
    _assert_migration_12_present(await _schema(path))
    assert (await _row_counts(path))["autotrader_universe"] == 1
    assert [version for version, _, _ in await _ledger(backup)] == list(range(1, 12))
    assert await _integrity(path) == ("ok", [])


def _snapshot_live_database(destination: Path) -> None:
    """Consistent read-only copy (the backup API includes WAL content; the live file is never written)."""
    try:
        source = sqlite3.connect(f"file:{LIVE_DB}?mode=ro", uri=True)
    except sqlite3.Error as exc:  # pragma: no cover - depends on the operator machine
        pytest.skip(f"live database unreadable: {exc}")
    try:
        target = sqlite3.connect(destination)
        try:
            source.backup(target)
        finally:
            target.close()
    except sqlite3.Error as exc:  # pragma: no cover - depends on the operator machine
        pytest.skip(f"live database unreadable: {exc}")
    finally:
        source.close()


@pytest.mark.skipif(not LIVE_DB.is_file(), reason="no live autotrader database on this machine")
@pytest.mark.anyio
async def test_every_migration_applies_cleanly_to_a_copy_of_the_live_database(tmp_path, monkeypatch) -> None:
    copy = tmp_path / "live-copy.db"
    _snapshot_live_database(copy)
    before = await _row_counts(copy)
    monkeypatch.setattr(cfg, "DB_PATH", str(copy))
    await init_db()
    ledger = await _ledger(copy)
    assert [version for version, _, _ in ledger] == [item.version for item in migrations.MIGRATIONS]
    assert [checksum for _, checksum, _ in ledger] == [item.checksum for item in migrations.MIGRATIONS]
    _assert_migration_12_present(await _schema(copy))
    after = await _row_counts(copy)
    assert after["autotrader_schema_migrations"] == len(migrations.MIGRATIONS)
    assert after["autotrader_schema_migrations"] >= before["autotrader_schema_migrations"]
    untouched = {
        table: count for table, count in before.items()
        if table not in ("users", "autotrader_schema_migrations")
    }
    assert {table: after[table] for table in untouched} == untouched  # existing data left as it was
    for table in (NEW_TABLES | set(MANAGER_TABLES)) - set(before):
        assert after[table] == 0
    await init_db()
    assert await _ledger(copy) == ledger
    assert await _integrity(copy) == ("ok", [])


# --- migration 17 (v7c, owner decision D5): a buy carries its protective stop -----------------------

V17_TRIGGERS = {
    "autotrader_manual_orders_buy_needs_stop",
    "autotrader_manual_orders_entry_stop_immutable",
}


def _migration_17() -> migrations.Migration:
    return next(item for item in migrations.MIGRATIONS if item.version == 17)


def test_migration_17_identity_and_payload() -> None:
    from trading.storage.manual_orders_schema import (
        MANUAL_BUY_NEEDS_STOP,
        MANUAL_ENTRY_STOP_MIGRATION,
        MANUAL_ENTRY_STOP_SCHEMA,
    )

    migration = _migration_17()
    assert (migration.version, migration.name, migration.signature) == (
        MANUAL_ENTRY_STOP_MIGRATION, "manual-entry-stop", "v1-manual-entry-stop-20261021"
    )
    assert migration.schema_payload == MANUAL_ENTRY_STOP_SCHEMA
    assert MANUAL_BUY_NEEDS_STOP == "manual_buy_needs_stop"
    # Migration 12's own schema is untouched: its reviewed intent trigger is never recreated.
    assert _migration_12().checksum == MIGRATION_12_CHECKSUM
    assert not any("DROP" in statement.upper() for statement in MANUAL_ENTRY_STOP_SCHEMA)


@pytest.mark.anyio
async def test_migration_17_applies_on_top_of_a_version_16_database_with_rows(tmp_path, monkeypatch) -> None:
    path = tmp_path / "v16.db"
    full = migrations.MIGRATIONS
    monkeypatch.setattr(migrations, "MIGRATIONS", full[:16])
    columns = ("id, created_at, updated_at, chat_id, message_id, update_id, source, raw_text, action, symbol,"
               " con_id, primary_exchange, quantity, order_type, limit_price, stop_price, status, expires_at,"
               " audit_json")
    marks = ", ".join("?" * 19)

    def row(digit: str, action: str, order_type: str, limit: str | None, stop: str | None) -> tuple[object, ...]:
        return (digit * 32, "t", "t", 7, int(digit, 16) + 1, 1, "command", "x", action, "AAPL", 1, "NASDAQ", 10,
                order_type, limit, stop, "filled", "t", "{}")

    async with aiosqlite.connect(path) as db:
        await migrations.apply_migrations(db)
        # The ledger as v16 holds it: buys without a stop, a /stop order.
        await db.execute(f"INSERT INTO autotrader_manual_orders ({columns}) VALUES ({marks})",
                         row("a", "BUY", "LMT", "180", None))
        await db.execute(f"INSERT INTO autotrader_manual_orders ({columns}) VALUES ({marks})",
                         row("b", "SELL_STOP", "STP", None, "170"))
        await db.commit()
    assert [version for version, _, _ in await _ledger(path)] == list(range(1, 17))
    monkeypatch.setattr(migrations, "MIGRATIONS", full)
    backup = tmp_path / "before-migration-17.db"
    async with aiosqlite.connect(path) as db:
        await migrations.apply_migrations(db, backup_path=backup)
    ledger = await _ledger(path)
    assert [version for version, _, _ in ledger] == [item.version for item in full]
    assert ledger[16][1] == _migration_17().checksum
    assert V17_TRIGGERS <= (await _schema(path))["trigger"]
    _assert_migration_12_present(await _schema(path))
    assert [version for version, _, _ in await _ledger(backup)] == list(range(1, 17))
    async with aiosqlite.connect(path) as db:
        async with db.execute("SELECT id, entry_stop_price FROM autotrader_manual_orders ORDER BY id") as cursor:
            assert [tuple(item) for item in await cursor.fetchall()] == [("a" * 32, None), ("b" * 32, None)]
        stop_columns = columns + ", entry_stop_price"
        stop_marks = marks + ", ?"
        # A buy without its stop, a stop on anything but a buy, a stop not below its limit: refused.
        for values, match in (
            (row("c", "BUY", "MKT", None, None) + (None,), "manual_buy_needs_stop"),
            (row("d", "BUY", "MKT", None, None) + ("0",), "CHECK"),
            (row("e", "SELL", "MKT", None, None) + ("170",), "CHECK"),
            (row("f", "BUY", "LMT", "180", None) + ("180",), "CHECK"),
        ):
            with pytest.raises(aiosqlite.IntegrityError, match=match):
                await db.execute(f"INSERT INTO autotrader_manual_orders ({stop_columns}) VALUES ({stop_marks})",
                                 values)
            await db.rollback()
        await db.execute(f"INSERT INTO autotrader_manual_orders ({stop_columns}) VALUES ({stop_marks})",
                         row("9", "BUY", "LMT", "180", None) + ("175",))
        await db.commit()
        # The stop is part of the immutable intent, also for the rows written before v17.
        for target in ("9" * 32, "a" * 32):
            with pytest.raises(aiosqlite.IntegrityError, match="immutable"):
                await db.execute("UPDATE autotrader_manual_orders SET entry_stop_price='150' WHERE id=?", (target,))
            await db.rollback()
        with pytest.raises(aiosqlite.IntegrityError, match="immutable"):
            await db.execute("UPDATE autotrader_manual_orders SET limit_price='181' WHERE id=?", ("9" * 32,))
        await db.rollback()
    assert await _integrity(path) == ("ok", [])

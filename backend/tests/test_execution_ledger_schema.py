"""The PAPER execution engine's ledger migration: pinned identity, clean application, its triggers.

The schema ships ahead of the engine: the migration and these tests are all
the main line carries, so the owner's database gets the (empty) tables through
``autotraderctl db migrate`` before any supervised drill. Its number lives
only in ``EXECUTION_LEDGER_MIGRATION``; every check here derives from it.

The triggers are the last line of defence: whatever the engine code does, the
ledger refuses to resend a decision, rewrite a placed order, decrease a fill,
undo a flatten attempt or rewrite an authority record.
"""
from __future__ import annotations

import asyncio
from contextlib import closing
from datetime import date, datetime, timezone
from pathlib import Path
import sqlite3
from typing import Any

import aiosqlite
import pytest

from config import cfg
from db.core import init_db
import trading.storage.migrations as migrations
from tests.fakes.decision_ledger import DecisionSeeder
from tests.schema_support import apply_up_to
from trading.storage.execution_ledger_schema import EXECUTION_LEDGER_MIGRATION, EXECUTION_LEDGER_SCHEMA


# Pinned with tests/test_migration_freeze.py: once applied to the owner's database, any edit to the
# schema or the apply function makes every later start refuse ("checksum mismatch"). The checksum
# covers the migration's number too.
LEDGER_MIGRATION_CHECKSUM = "01f8962c8d82e11d2617b6f0d4886e960e600c22abfb380ac71e8b6ee2fb7f8c"
ACCOUNT = "DU1234567"
SESSION = date(2026, 10, 13)
NOW = datetime(2026, 10, 13, 14, 0, tzinfo=timezone.utc)
NEW_TABLES = {
    "autotrader_exec_promotions", "autotrader_exec_outbox", "autotrader_exec_brackets", "autotrader_exec_legs",
    "autotrader_exec_fills", "autotrader_exec_events", "autotrader_exec_risk_reservations",
}


def _ledger_migration() -> migrations.Migration:
    return next(item for item in migrations.MIGRATIONS if item.name == "execution-engine-ledger")


def _all_versions() -> list[int]:
    return [item.version for item in migrations.MIGRATIONS]


def test_the_ledger_migration_identity_and_checksum_are_pinned() -> None:
    migration = _ledger_migration()
    assert (migration.version, migration.name, migration.signature) == (
        EXECUTION_LEDGER_MIGRATION, "execution-engine-ledger", "v1-execution-engine-ledger-20261013"
    )
    assert EXECUTION_LEDGER_MIGRATION == 14  # after the main line's 13 (manager setups)
    assert migration.schema_payload == EXECUTION_LEDGER_SCHEMA
    assert migration.checksum == LEDGER_MIGRATION_CHECKSUM
    assert _all_versions() == list(range(1, len(migrations.MIGRATIONS) + 1))


def test_the_ledger_migration_number_is_written_in_one_place_only() -> None:
    backend = Path(__file__).resolve().parents[1]
    source = (backend / "trading" / "storage" / "migrations.py").read_text()
    assert "EXECUTION_LEDGER_MIGRATION, \"execution-engine-ledger\"" in source


async def _objects(path: Path) -> dict[str, set[str]]:
    async with aiosqlite.connect(path) as db:
        async with db.execute("SELECT type, name FROM sqlite_master") as cursor:
            rows = await cursor.fetchall()
    found: dict[str, set[str]] = {}
    for kind, name in rows:
        found.setdefault(str(kind), set()).add(str(name))
    return found


async def _versions(path: Path) -> list[int]:
    async with aiosqlite.connect(path) as db:
        async with db.execute("SELECT version FROM autotrader_schema_migrations ORDER BY version") as cursor:
            return [int(row[0]) for row in await cursor.fetchall()]


async def test_a_fresh_database_gets_the_ledger_migration_once(tmp_path, monkeypatch) -> None:
    path = tmp_path / "fresh.db"
    monkeypatch.setattr(cfg, "DB_PATH", str(path))
    await init_db()
    await init_db()
    assert await _versions(path) == _all_versions()
    objects = await _objects(path)
    assert NEW_TABLES <= objects["table"]
    assert {name for name in objects["trigger"] if name.startswith("autotrader_exec_")} >= {
        "autotrader_exec_outbox_state_monotonic", "autotrader_exec_brackets_need_sending_outbox",
        "autotrader_exec_legs_intent_immutable", "autotrader_exec_fills_immutable",
        "autotrader_exec_brackets_flatten_one_way", "autotrader_exec_promotions_no_update",
    }


async def test_the_ledger_migration_applies_on_top_of_the_previous_version(tmp_path) -> None:
    path = await apply_up_to(tmp_path / "previous.db", EXECUTION_LEDGER_MIGRATION - 1)
    async with aiosqlite.connect(path) as db:
        await db.execute(
            "INSERT INTO autotrader_universe VALUES (?,?,?,?,?,?,?,?,?)",
            ("watchlist", "AAPL", "NASDAQ", "AAPL", 265598, "NASDAQ", "resolved", None, "t"),
        )
        await db.commit()
    assert "autotrader_exec_outbox" not in (await _objects(path))["table"]
    backup = tmp_path / "before-the-ledger-migration.db"
    async with aiosqlite.connect(path) as db:
        await migrations.apply_migrations(db, backup_path=backup)
    assert await _versions(path) == _all_versions()
    assert await _versions(backup) == list(range(1, EXECUTION_LEDGER_MIGRATION))
    assert NEW_TABLES <= (await _objects(path))["table"]
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM autotrader_universe").fetchone() == (1,)
        for table in NEW_TABLES:  # nothing writes them before the engine runs
            assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone() == (0,), table
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


# ---- invariants ----------------------------------------------------------------------------


@pytest.fixture
def ledger_path(tmp_path, monkeypatch) -> Path:
    path = tmp_path / "ledger.db"
    monkeypatch.setattr(cfg, "DB_PATH", str(path))
    asyncio.run(init_db())
    return path


def seed_decisions(count: int) -> list[str]:
    seeder = DecisionSeeder(SESSION)
    return [asyncio.run(seeder.accept(f"SYM{index}", created=NOW, con_id=1000 + index)) for index in range(count)]


def connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def insert_outbox(db: sqlite3.Connection, decision: str, *, state: str = "sending", bracket: str | None = "0" * 12,
                  code: str | None = None) -> None:
    db.execute(
        "INSERT INTO autotrader_exec_outbox (decision_id, book_id, account, session_date, strategy_name, symbol,"
        " state, reason_code, reason, bracket_id, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (decision, f"{ACCOUNT}:2026-10-13", ACCOUNT, "2026-10-13", "MOMO_HOD_V2", "AAPL", state, code, None,
         bracket, "t", "t"),
    )


def insert_bracket(db: sqlite3.Connection, decision: str, bracket: str = "0" * 12) -> None:
    db.execute(
        "INSERT INTO autotrader_exec_brackets (id, decision_id, book_id, account, client_id, session_date,"
        " strategy_name, symbol, con_id, primary_exchange, quantity, entry_price, stop_price, target_price, state,"
        " created_at, updated_at) VALUES (?,?,?,?,18,'2026-10-13','MOMO_HOD_V2','AAPL',265598,'NASDAQ',100,"
        " '50.00','49.50','51.00','sending','t','t')",
        (bracket, decision, f"{ACCOUNT}:2026-10-13", ACCOUNT),
    )


def insert_leg(db: sqlite3.Connection, bracket: str, leg: str, order_id: int, *, ref: str | None = None) -> None:
    shape = {
        "P": ("BUY", "LMT", "DAY", 0, None, "50.00"),
        "T": ("SELL", "LMT", "GTC", 0, 100, "51.00"),
        "S": ("SELL", "STP", "GTC", 1, 100, "49.50"),
        "F": ("SELL", "MKT", "DAY", 1, None, None),
    }[leg]
    action, order_type, tif, transmit, parent, price = shape
    db.execute(
        "INSERT INTO autotrader_exec_legs (id, bracket_id, leg, client_id, order_id, order_ref, action, order_type,"
        " tif, quantity, price, parent_order_id, transmit, state, created_at, updated_at)"
        " VALUES (?,?,?,18,?,?,?,?,?,100,?,?,?,'staged','t','t')",
        (f"{bracket}:{leg}", bracket, leg, order_id, ref or f"at1:{bracket}:{leg}", action, order_type, tif, price,
         parent, transmit),
    )


def refused(db: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> str:
    with pytest.raises(sqlite3.DatabaseError) as error:
        db.execute(sql, params)
    return str(error.value)


def test_the_outbox_holds_a_decision_once_and_never_moves_backwards(ledger_path) -> None:
    decision, other = seed_decisions(2)
    with closing(connect(ledger_path)) as db:
        assert "starts as sending or skipped" in refused(
            db, "INSERT INTO autotrader_exec_outbox VALUES (?,?,?,?,?,?,'sent',NULL,NULL,?,?,?)",
            (decision, f"{ACCOUNT}:2026-10-13", ACCOUNT, "2026-10-13", "MOMO_HOD_V2", "AAPL", "1" * 12, "t", "t"),
        )
        insert_outbox(db, decision)
        with pytest.raises(sqlite3.IntegrityError):
            insert_outbox(db, decision, bracket="2" * 12)  # a decision is considered once
        db.execute("UPDATE autotrader_exec_outbox SET state='sent' WHERE decision_id=?", (decision,))
        assert "sending -> sent|unknown" in refused(
            db, "UPDATE autotrader_exec_outbox SET state='sending' WHERE decision_id=?", (decision,))
        assert "identity is immutable" in refused(
            db, "UPDATE autotrader_exec_outbox SET bracket_id=? WHERE decision_id=?", ("3" * 12, decision))
        assert "append-only" in refused(db, "DELETE FROM autotrader_exec_outbox")
        with pytest.raises(sqlite3.IntegrityError):
            insert_outbox(db, "no-such-decision", bracket="4" * 12)  # only real ledger decisions
        insert_outbox(db, other, state="skipped", bracket=None, code="kill_switch")
        assert "sending -> sent|unknown" in refused(
            db, "UPDATE autotrader_exec_outbox SET state='sending' WHERE decision_id=?", (other,))


def test_a_bracket_needs_its_decision_claimed_as_sending_first(ledger_path) -> None:
    decision, other = seed_decisions(2)
    with closing(connect(ledger_path)) as db:
        assert "claimed as sending first" in refused(
            db, "INSERT INTO autotrader_exec_brackets (id, decision_id, book_id, account, client_id, session_date,"
            " strategy_name, symbol, con_id, primary_exchange, quantity, entry_price, stop_price, target_price,"
            " state, created_at, updated_at) VALUES ('000000000000',?,?,?,18,'2026-10-13','MOMO_HOD_V2','AAPL',"
            " 265598,'NASDAQ',100,'50.00','49.50','51.00','sending','t','t')",
            (decision, f"{ACCOUNT}:2026-10-13", ACCOUNT),
        )
        insert_outbox(db, other, state="skipped", bracket=None, code="kill_switch")
        with pytest.raises(sqlite3.DatabaseError):
            insert_bracket(db, other)  # a skipped decision never gets a bracket
        insert_outbox(db, decision)
        insert_bracket(db, decision)
        assert "intent is immutable" in refused(db, "UPDATE autotrader_exec_brackets SET quantity=200")
        assert "unsafe bracket state transition" in refused(db, "UPDATE autotrader_exec_brackets SET state='open'")
        db.execute("UPDATE autotrader_exec_brackets SET state='working'")
        # Filled and exited between two saves (the recorded bracket-fill sequence found this).
        db.execute("UPDATE autotrader_exec_brackets SET state='closed', entry_filled=100, exit_filled=100")
        db.execute("UPDATE autotrader_exec_brackets SET state='open'")  # the entry filled again after closing
        assert "unsafe bracket state transition" in refused(db, "UPDATE autotrader_exec_brackets SET state='working'")


def test_bracket_fills_never_decrease_and_a_flatten_is_never_undone(ledger_path) -> None:
    [decision] = seed_decisions(1)
    with closing(connect(ledger_path)) as db:
        insert_outbox(db, decision)
        insert_bracket(db, decision)
        db.execute("UPDATE autotrader_exec_brackets SET state='working'")
        db.execute("UPDATE autotrader_exec_brackets SET state='open', entry_filled=100, exit_filled=40")
        assert "never decrease" in refused(db, "UPDATE autotrader_exec_brackets SET entry_filled=60")
        assert "never decrease" in refused(db, "UPDATE autotrader_exec_brackets SET exit_filled=0")
        with pytest.raises(sqlite3.IntegrityError):  # entry fills never exceed the order
            db.execute("UPDATE autotrader_exec_brackets SET entry_filled=101")
        with pytest.raises(sqlite3.IntegrityError):  # a flatten state needs a recorded attempt
            db.execute("UPDATE autotrader_exec_brackets SET flatten_state='repricing'")
        db.execute("UPDATE autotrader_exec_brackets SET flatten_attempted=1, flatten_state='repricing'")
        assert "never undone" in refused(db, "UPDATE autotrader_exec_brackets SET flatten_attempted=0")
        assert "never undone" in refused(db, "UPDATE autotrader_exec_brackets SET flatten_state='none'")
        db.execute("UPDATE autotrader_exec_brackets SET flatten_state='cancelling'")
        assert "never undone" in refused(db, "UPDATE autotrader_exec_brackets SET flatten_state='repricing'")
        db.execute("UPDATE autotrader_exec_brackets SET flatten_state='selling'")
        assert "never undone" in refused(db, "UPDATE autotrader_exec_brackets SET flatten_state='cancelling'")
        db.execute("UPDATE autotrader_exec_brackets SET state='needs_attention'")
        assert "unsafe bracket state transition" in refused(db, "UPDATE autotrader_exec_brackets SET state='open'")
        db.execute("UPDATE autotrader_exec_brackets SET attention_cleared_at='t2'")
        assert "stays cleared" in refused(db, "UPDATE autotrader_exec_brackets SET attention_cleared_at=NULL")
        assert "append-only" in refused(db, "DELETE FROM autotrader_exec_brackets")


def test_every_leg_carries_its_at1_order_ref_and_its_placed_intent_is_immutable(ledger_path) -> None:
    [decision] = seed_decisions(1)
    bracket = "0" * 12
    with closing(connect(ledger_path)) as db:
        insert_outbox(db, decision)
        insert_bracket(db, decision)
        with pytest.raises(sqlite3.IntegrityError):
            insert_leg(db, bracket, "P", 100, ref="autotrader:" + bracket)  # the bridge's tag is not the engine's
        with pytest.raises(sqlite3.IntegrityError):  # only P, T, S and the fallback F exist
            db.execute(
                "INSERT INTO autotrader_exec_legs (id, bracket_id, leg, client_id, order_id, order_ref, action,"
                " order_type, tif, quantity, price, parent_order_id, transmit, state, created_at, updated_at)"
                " VALUES (?,?,'X',18,100,?,'BUY','LMT','DAY',100,'50.00',NULL,0,'staged','t','t')",
                (f"{bracket}:X", bracket, f"at1:{bracket}:X"),
            )
        insert_leg(db, bracket, "P", 100)
        insert_leg(db, bracket, "T", 101)
        insert_leg(db, bracket, "S", 102)
        insert_leg(db, bracket, "F", 103)
        with pytest.raises(sqlite3.IntegrityError):  # an order id is never reused by this client
            db.execute("INSERT INTO autotrader_exec_legs SELECT 'x:P', bracket_id, leg, client_id, order_id,"
                       " 'at1:x:P', action, order_type, tif, quantity, price, parent_order_id, transmit, state,"
                       " broker_status, perm_id, filled, working_quantity, working_price, created_at, updated_at"
                       " FROM autotrader_exec_legs WHERE leg='P'")
        for column, value in (("order_id", 999), ("quantity", 50), ("price", "49.00"), ("transmit", 0),
                              ("order_ref", "at1:111111111111:P")):
            assert "immutable" in refused(db, f"UPDATE autotrader_exec_legs SET {column}=? WHERE leg='S'", (value,))
        db.execute("UPDATE autotrader_exec_legs SET state='sent', filled=40, perm_id=7 WHERE leg='P'")
        assert "never re-staged" in refused(db, "UPDATE autotrader_exec_legs SET state='staged' WHERE leg='P'")
        assert "never decrease" in refused(db, "UPDATE autotrader_exec_legs SET filled=10 WHERE leg='P'")
        assert "never changes" in refused(db, "UPDATE autotrader_exec_legs SET perm_id=8 WHERE leg='P'")
        db.execute("UPDATE autotrader_exec_legs SET working_quantity=60, working_price='49.94' WHERE leg='T'")


def test_fills_are_keyed_by_exec_id_and_only_a_missing_commission_may_be_added(ledger_path) -> None:
    [decision] = seed_decisions(1)
    bracket = "0" * 12
    with closing(connect(ledger_path)) as db:
        insert_outbox(db, decision)
        insert_bracket(db, decision)
        insert_leg(db, bracket, "P", 100)
        fill = ("e.01", "e", 1, f"{bracket}:P", 100, "BOT", 100, "50.00", ACCOUNT, "20261013  10:00:05", None, None,
                "t")
        db.execute("INSERT INTO autotrader_exec_fills VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", fill)
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("INSERT INTO autotrader_exec_fills VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", fill)
        assert "only a missing commission" in refused(db, "UPDATE autotrader_exec_fills SET shares=50")
        db.execute("UPDATE autotrader_exec_fills SET commission='1.00', commission_currency='USD'")
        assert "only a missing commission" in refused(db, "UPDATE autotrader_exec_fills SET commission='0.50'")
        assert "append-only" in refused(db, "DELETE FROM autotrader_exec_fills")


def test_events_promotions_and_reservations_are_append_only(ledger_path) -> None:
    [decision] = seed_decisions(1)
    bracket = "0" * 12
    with closing(connect(ledger_path)) as db:
        db.execute("INSERT INTO autotrader_exec_events (book_id, kind, payload_json, created_at)"
                   " VALUES ('b', 'reconciled', '{}', 't')")
        assert "append-only" in refused(db, "UPDATE autotrader_exec_events SET kind='x'")
        assert "append-only" in refused(db, "DELETE FROM autotrader_exec_events")
        db.execute("INSERT INTO autotrader_exec_promotions (id, action, strategy_name, account, code_digest, actor,"
                   " reason, created_at) VALUES (?, 'grant', 'MOMO_HOD_V2', ?, ?, 'owner', 'go', 't')",
                   ("a" * 32, ACCOUNT, "b" * 64))
        assert "append-only" in refused(db, "UPDATE autotrader_exec_promotions SET action='revoke'")
        assert "append-only" in refused(db, "DELETE FROM autotrader_exec_promotions")
        for account, digest in (("U1234567", "b" * 64), (ACCOUNT, None)):  # live accounts; a grant without code
            with pytest.raises(sqlite3.IntegrityError):
                db.execute("INSERT INTO autotrader_exec_promotions (id, action, strategy_name, account, code_digest,"
                           " actor, reason, created_at) VALUES (?, 'grant', 'MOMO_HOD_V2', ?, ?, 'o', 'r', 't')",
                           ("c" * 32, account, digest))
        insert_outbox(db, decision)
        insert_bracket(db, decision)
        reservation = ("d" * 32, f"{ACCOUNT}:2026-10-13", ACCOUNT, "2026-10-13", decision, bracket, "AAPL", "50",
                       "5000", "1000000")
        assert "starts as reserved" in refused(
            db, "INSERT INTO autotrader_exec_risk_reservations VALUES (?,?,?,?,?,?,?,?,?,?,'consumed','t','t')",
            reservation)
        db.execute("INSERT INTO autotrader_exec_risk_reservations VALUES (?,?,?,?,?,?,?,?,?,?,'reserved','t',NULL)",
                   reservation)
        assert "reserved once" in refused(db, "UPDATE autotrader_exec_risk_reservations SET risk_amount='1'")
        db.execute("UPDATE autotrader_exec_risk_reservations SET state='consumed', settled_at='t2'")
        assert "reserved once" in refused(
            db, "UPDATE autotrader_exec_risk_reservations SET state='reserved', settled_at=NULL")

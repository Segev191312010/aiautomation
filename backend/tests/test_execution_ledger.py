"""Migration 13 (execution engine ledger): pinned identity, clean application, and its invariants.

The triggers are the last line of defence: whatever the engine code does, the
ledger refuses to resend a decision, rewrite a placed order, decrease a fill,
undo a flatten attempt or rewrite an authority record.
"""
from __future__ import annotations

import asyncio
from contextlib import closing
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
import sqlite3
from typing import Any

import aiosqlite
import pytest

from config import cfg
from db.core import init_db
import trading.storage.migrations as migrations
from tests.fakes.decision_ledger import DecisionSeeder
from trading.storage.execution_ledger import (
    BracketRecord,
    DecisionAlreadyClaimed,
    ExecutionLedger,
    FillRecord,
    LegRecord,
    RiskBookRefusal,
    book_id,
    order_ref,
    parse_order_ref,
)
from trading.storage.execution_ledger_schema import EXECUTION_LEDGER_SCHEMA


# Pinned: once migration 13 is applied to the owner's database, any edit to its schema or apply
# function would make every later start fail closed with "checksum mismatch".
MIGRATION_13_CHECKSUM = "48ec49e6a9b866bef124f6d004d6b72df2410d59912a0c3a64b0c418a59c4d02"
ACCOUNT = "DU1234567"
SESSION = date(2026, 10, 13)
NOW = datetime(2026, 10, 13, 14, 0, tzinfo=timezone.utc)
NEW_TABLES = {
    "autotrader_exec_promotions", "autotrader_exec_outbox", "autotrader_exec_brackets", "autotrader_exec_legs",
    "autotrader_exec_fills", "autotrader_exec_events", "autotrader_exec_risk_reservations",
}


def _migration_13() -> migrations.Migration:
    return next(item for item in migrations.MIGRATIONS if item.version == 13)


def test_migration_13_identity_and_checksum_are_pinned() -> None:
    migration = _migration_13()
    assert (migration.version, migration.name, migration.signature) == (
        13, "execution-engine-ledger", "v1-execution-engine-ledger-20261013"
    )
    assert migration.schema_payload == EXECUTION_LEDGER_SCHEMA
    assert migration.checksum == MIGRATION_13_CHECKSUM
    assert [item.version for item in migrations.MIGRATIONS] == list(range(1, 14))


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


async def test_a_fresh_database_gets_migration_13_once(tmp_path, monkeypatch) -> None:
    path = tmp_path / "fresh.db"
    monkeypatch.setattr(cfg, "DB_PATH", str(path))
    await init_db()
    await init_db()
    assert await _versions(path) == list(range(1, 14))
    objects = await _objects(path)
    assert NEW_TABLES <= objects["table"]
    assert {name for name in objects["trigger"] if name.startswith("autotrader_exec_")} >= {
        "autotrader_exec_outbox_state_monotonic", "autotrader_exec_brackets_need_sending_outbox",
        "autotrader_exec_legs_intent_immutable", "autotrader_exec_fills_immutable",
        "autotrader_exec_brackets_flatten_one_way", "autotrader_exec_promotions_no_update",
    }


async def test_migration_13_applies_on_top_of_a_version_12_database(tmp_path, monkeypatch) -> None:
    path = tmp_path / "v12.db"
    full = migrations.MIGRATIONS
    monkeypatch.setattr(migrations, "MIGRATIONS", full[:12])
    async with aiosqlite.connect(path) as db:
        await migrations.apply_migrations(db)
        await db.execute(
            "INSERT INTO autotrader_universe VALUES (?,?,?,?,?,?,?,?,?)",
            ("watchlist", "AAPL", "NASDAQ", "AAPL", 265598, "NASDAQ", "resolved", None, "t"),
        )
        await db.commit()
    assert "autotrader_exec_outbox" not in (await _objects(path))["table"]
    monkeypatch.setattr(migrations, "MIGRATIONS", full)
    backup = tmp_path / "before-migration-13.db"
    async with aiosqlite.connect(path) as db:
        await migrations.apply_migrations(db, backup_path=backup)
    assert await _versions(path) == list(range(1, 14))
    assert await _versions(backup) == list(range(1, 13))
    assert NEW_TABLES <= (await _objects(path))["table"]
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM autotrader_universe").fetchone() == (1,)
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


LIVE_DB = Path.home() / ".local" / "share" / "autotrader-v1" / "autotrader.db"


@pytest.mark.skipif(not LIVE_DB.is_file(), reason="no live autotrader database on this machine")
async def test_migration_13_applies_cleanly_to_a_read_only_copy_of_the_live_database(tmp_path, monkeypatch) -> None:
    from tests.test_manual_orders_migration import _row_counts, _snapshot_live_database

    copy = tmp_path / "live-copy.db"
    _snapshot_live_database(copy)  # the live file is opened read-only; only the copy is migrated
    before = await _row_counts(copy)
    monkeypatch.setattr(cfg, "DB_PATH", str(copy))
    await init_db()
    after = await _row_counts(copy)
    assert await _versions(copy) == list(range(1, 14))
    assert {table: after[table] for table in NEW_TABLES} == {table: 0 for table in NEW_TABLES}
    untouched = {table: count for table, count in before.items()
                 if table not in ("users", "autotrader_schema_migrations")}
    assert {table: after[table] for table in untouched} == untouched
    with closing(sqlite3.connect(copy)) as connection:
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
        fill = ("e.01", "e", 1, f"{bracket}:P", 100, "BOT", 100, "50.00", ACCOUNT, "20261013  10:00:05", None, None, "t")
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


# ---- the repository ----------------------------------------------------------------------------


def staged(decision: str, bracket: str, symbol: str, *, quantity: int = 100, first_id: int = 100,
           stop: str = "49.50") -> BracketRecord:
    def leg(code: str, order_id: int, action: str, order_type: str, tif: str, price: str, parent: int | None,
            transmit: bool) -> LegRecord:
        return LegRecord(id=f"{bracket}:{code}", bracket_id=bracket, leg=code, client_id=18,  # type: ignore[arg-type]
                         order_id=order_id, order_ref=order_ref(bracket, code), action=action,  # type: ignore[arg-type]
                         order_type=order_type, tif=tif, quantity=quantity, price=Decimal(price),  # type: ignore[arg-type]
                         parent_order_id=parent, transmit=transmit, state="staged", created_at=NOW)

    return BracketRecord(
        id=bracket, decision_id=decision, account=ACCOUNT, client_id=18, session_date=SESSION,
        strategy_name="MOMO_HOD_V2", symbol=symbol, con_id=abs(hash(symbol)) % 100000 + 1, primary_exchange="NASDAQ",
        quantity=quantity, entry_price=Decimal("50.00"), stop_price=Decimal(stop), target_price=Decimal("52.00"),
        setup_fingerprint=None, state="sending", created_at=NOW,
        legs={
            "P": leg("P", first_id, "BUY", "LMT", "DAY", "50.00", None, False),
            "T": leg("T", first_id + 1, "SELL", "LMT", "GTC", "52.00", first_id, False),
            "S": leg("S", first_id + 2, "SELL", "STP", "GTC", stop, first_id, True),
        },
    )


async def test_staging_rechecks_the_shared_book_atomically(ledger_path) -> None:
    first, second, third = await asyncio.to_thread(seed_decisions, 3)
    ledger = ExecutionLedger(clock=lambda: NOW)
    # 3000 x 1.00 = 3000 risk each against a 500000 book: 0.6% each would break the 0.5% new-trade cap,
    # so use 2500 x 1.00 = 0.5% each; together the three would be 1.5%, the fourth anything more.
    records = [staged(decision, f"{index:012x}", f"S{index}", quantity=2500, first_id=100 + 3 * index, stop="49.00")
               for index, decision in enumerate((first, second, third))]
    results = await asyncio.gather(
        *(ledger.stage_bracket(record, net_liquidation=Decimal("500000")) for record in records),
        return_exceptions=True,
    )
    assert all(isinstance(result, str) for result in results)  # serialized: 1.5% exactly fits
    with pytest.raises(DecisionAlreadyClaimed):
        await ledger.stage_bracket(staged(first, "f" * 12, "AGAIN", first_id=900), net_liquidation=Decimal("500000"))
    [fourth] = await asyncio.to_thread(seed_decisions_named, "S9")
    with pytest.raises(RiskBookRefusal) as refusal:
        await ledger.stage_bracket(staged(fourth, "e" * 12, "S9", quantity=10, first_id=950),
                                   net_liquidation=Decimal("500000"))
    assert refusal.value.check.code == "max_positions_reached"
    assert await ledger.outbox_row(fourth) is None  # a refused stage leaves nothing behind


def seed_decisions_named(symbol: str) -> list[str]:
    seeder = DecisionSeeder(SESSION)
    return [asyncio.run(seeder.accept(symbol, created=NOW, con_id=99_999, strategy="MOMO_HOD_V9"))]


async def test_fills_are_deduplicated_corrections_win_and_conflicts_are_reported(ledger_path) -> None:
    [decision] = await asyncio.to_thread(seed_decisions, 1)
    ledger = ExecutionLedger(clock=lambda: NOW)
    record = staged(decision, "a" * 12, "AAPL")
    await ledger.stage_bracket(record, net_liquidation=Decimal("1000000"))
    leg = f"{record.id}:P"

    def fill(exec_id: str, shares: int, *, commission: str | None = None) -> FillRecord:
        base, _, version = exec_id.rpartition(".")
        return FillRecord(exec_id=exec_id, exec_base=base, exec_version=int(version), leg_id=leg, order_id=100,
                          side="BOT", shares=shares, price=Decimal("50.00"), account=ACCOUNT, exec_time=None,
                          commission=None if commission is None else Decimal(commission),
                          commission_currency=None if commission is None else "USD")

    first = await ledger.record_fills([fill("e1.01", 60), fill("e2.01", 40)])
    again = await ledger.record_fills([fill("e1.01", 60, commission="1.00"), fill("e2.01", 41), fill("e1.02", 50)])
    assert first.inserted == ("e1.01", "e2.01")
    assert (again.inserted, again.commissions_added, again.conflicts) == (("e1.02",), ("e1.01",), ("e2.01",))
    totals = await ledger.fill_totals([leg])
    assert (totals[leg].shares, totals[leg].average) == (90, Decimal("50.0000"))  # e1.02 replaces e1.01
    assert len(await ledger.load_fills([leg])) == 3  # every report is kept for the audit


def test_order_refs_round_trip_and_reject_everything_else() -> None:
    assert order_ref("0123456789ab", "S") == "at1:0123456789ab:S"
    assert parse_order_ref("at1:0123456789ab:S") == ("0123456789ab", "S")
    for ref in ("manual:" + "a" * 32, "autotrader:abc", "at1:0123456789ab:X", "at1:XYZ:P", "", None, "at1::P"):
        assert parse_order_ref(ref) is None
    with pytest.raises(ValueError):
        order_ref("not-hex", "P")
    assert book_id(ACCOUNT, SESSION) == "DU1234567:2026-10-13"


def test_what_a_bracket_claims_from_the_shared_book() -> None:
    record = staged("d", "a" * 12, "AAPL")
    record.state = "working"
    assert (record.book_position().open_risk, record.book_position().holds_slot) == (Decimal("50.00"), True)
    record.state, record.entry_filled, record.avg_entry = "open", 60, Decimal("49.90")
    record.legs["P"].broker_status = "Cancelled"  # a dead parent no longer adds pending risk
    assert record.book_position().open_risk == Decimal("60") * Decimal("0.40")
    record.state = "needs_attention"
    assert record.book_position().holds_slot  # until reconciliation sees it flat at IBKR (M4)
    record.attention_cleared_at = NOW
    assert record.book_position() .holds_slot is False
    for state in ("closed", "cancelled", "unknown"):
        record.state, record.attention_cleared_at = state, None
        assert (record.book_position().open_risk, record.book_position().holds_slot) == (Decimal("0"), False)

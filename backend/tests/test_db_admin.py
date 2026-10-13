"""`autotraderctl db migrate`: locked, backed up, outside the trading day; `db status`: read-only."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
import io
import os
from pathlib import Path
import sqlite3
import stat

import pytest

from tests.schema_support import database_at, migrate_database
from trading.operations import db_admin
from trading.operations.db_admin import (
    EXIT_MIGRATE_REFUSED,
    MIGRATE_BACKUP_FAILED,
    MIGRATE_FAILED,
    MIGRATE_LOCK_BUSY,
    MIGRATE_REFUSED_SESSION_WINDOW,
    MIGRATE_SCHEMA_PROBLEM,
    backup_path,
    migration_lock,
    run_migrate,
    run_status,
    trading_day,
    trading_day_at,
)
from trading.operations.schema_gate import EXIT_SCHEMA_NOT_READY
from trading.operations.supervisor import plan_session
from trading.scheduler.sessions import MARKET_TIMEZONE, market_session
import trading.storage.migrations as migrations
from trading.storage.migrations import MIGRATIONS, Migration, inspect_schema

LATEST = MIGRATIONS[-1].version
PREVIOUS = LATEST - 1
TUESDAY = date(2026, 10, 13)
HALF_DAY = date(2026, 11, 27)


def _et(day: date, hour: int, minute: int, second: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute, second), tzinfo=MARKET_TIMEZONE).astimezone(
        timezone.utc
    )


AFTER_CLOSE = _et(TUESDAY, 19, 0)
MID_SESSION = _et(TUESDAY, 11, 0)


def _ledger(path: Path) -> list[int]:
    with sqlite3.connect(path) as db:
        return [row[0] for row in db.execute("SELECT version FROM autotrader_schema_migrations ORDER BY version")]


def _migrate(path: Path, now: datetime = AFTER_CLOSE, **kwargs) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = run_migrate(path, now=now, out=out, err=err, **kwargs)
    return code, out.getvalue(), err.getvalue()


# --- the trading day comes from the supervisor's own plan ----------------------------


@pytest.mark.parametrize("day", [TUESDAY, HALF_DAY])
def test_the_trading_day_spans_every_supervised_window(day):
    session = market_session(day)
    assert session is not None
    span = trading_day(session)

    assert span.starts_at == _et(day, 7, 55) and span.ends_at == _et(day, 18, 30)
    for plan in plan_session(session):  # the real strategy cutoffs
        assert span.starts_at <= plan.starts_at and plan.ends_at <= span.ends_at, plan.name
    assert session.half_day == (day == HALF_DAY)


@pytest.mark.parametrize(
    ("moment", "inside"),
    [
        (_et(TUESDAY, 7, 54, 59), False),
        (_et(TUESDAY, 7, 55), True),
        (_et(TUESDAY, 18, 29, 59), True),
        (_et(TUESDAY, 18, 30), False),
        (_et(HALF_DAY, 14, 0), True),  # the research children still run after a 13:00 close
        (_et(date(2026, 10, 17), 11, 0), False),  # Saturday
        (_et(date(2026, 11, 26), 11, 0), False),  # Thanksgiving
    ],
)
def test_migrations_are_refused_only_inside_a_trading_day(moment, inside):
    assert (trading_day_at(moment) is not None) == inside


# --- db migrate ----------------------------------------------------------------------


def test_migrate_backs_up_then_applies_and_prints_both_versions(tmp_path):
    path = database_at(tmp_path / "autotrader.db", PREVIOUS)
    with sqlite3.connect(path) as db:  # like the live database: WAL, with owner data
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("INSERT INTO autotrader_execution_fences VALUES ('paper', 1, 'owner stop', 't')")
    before = inspect_schema(path)

    code, out, err = _migrate(path)

    assert (code, err) == (0, "")
    assert f"schema    v{PREVIOUS} (this code knows v{LATEST})" in out
    assert f"applying  {LATEST} {MIGRATIONS[-1].name}" in out
    assert f"migrated  v{PREVIOUS} -> v{LATEST}" in out
    assert inspect_schema(path).current
    backup = backup_path(path, before, AFTER_CLOSE)
    assert f"backup    {backup}" in out
    assert backup.name == f"autotrader.20261013T230000.000000Z.v{PREVIOUS}-to-v{LATEST}.db"
    # The backup is the database as it was: same rows, the old ledger, sound.
    assert _ledger(backup) == list(range(1, LATEST))
    with sqlite3.connect(backup) as db:
        assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert db.execute("PRAGMA journal_mode").fetchone() == ("delete",)
        assert db.execute("SELECT reason FROM autotrader_execution_fences").fetchone() == ("owner stop",)
    assert stat.S_IMODE(backup.stat().st_mode) == 0o600
    assert stat.S_IMODE(backup.parent.stat().st_mode) == 0o700
    assert sorted(item.name for item in backup.parent.iterdir()) == [backup.name]  # no partial, no -wal


def test_every_migration_gets_its_own_backup(tmp_path, monkeypatch):
    path = database_at(tmp_path / "autotrader.db", PREVIOUS - 1)
    full = migrations.MIGRATIONS
    monkeypatch.setattr(migrations, "MIGRATIONS", full[:PREVIOUS])
    assert _migrate(path, AFTER_CLOSE)[0] == 0
    monkeypatch.setattr(migrations, "MIGRATIONS", full)
    assert _migrate(path, AFTER_CLOSE + timedelta(microseconds=1))[0] == 0

    backups = sorted((tmp_path / "backups").iterdir())
    assert len(backups) == 2 and backups[0] != backups[1]
    assert [_ledger(item)[-1] for item in backups] == [PREVIOUS - 1, PREVIOUS]


def test_nothing_pending_is_a_clean_no_op_without_a_backup(tmp_path):
    path = migrate_database(tmp_path / "autotrader.db")

    code, out, _err = _migrate(path, MID_SESSION)  # even inside the trading day

    assert code == 0 and "nothing to migrate" in out
    assert not (tmp_path / "backups").exists()


def test_migrate_refuses_inside_the_trading_day_and_changes_nothing(tmp_path):
    path = database_at(tmp_path / "autotrader.db", PREVIOUS)

    code, _out, err = _migrate(path, MID_SESSION)

    assert code == EXIT_MIGRATE_REFUSED
    assert MIGRATE_REFUSED_SESSION_WINDOW in err and "--during-session-i-know" in err
    assert "07:55-18:30 ET" in err
    assert _ledger(path) == list(range(1, LATEST))
    assert not (tmp_path / "backups").exists()


def test_the_owner_can_migrate_inside_the_trading_day_on_purpose(tmp_path):
    path = database_at(tmp_path / "autotrader.db", PREVIOUS)

    code, out, _err = _migrate(path, MID_SESSION, allow_during_session=True)

    assert code == 0 and "WARNING: migrating inside the 2026-10-13 trading day" in out
    assert inspect_schema(path).current and len(list((tmp_path / "backups").iterdir())) == 1


def test_a_mismatched_history_is_refused_before_any_backup(tmp_path):
    path = migrate_database(tmp_path / "autotrader.db")
    with sqlite3.connect(path) as db:
        db.execute("UPDATE autotrader_schema_migrations SET checksum='tampered' WHERE version=3")

    code, _out, err = _migrate(path)

    assert code == EXIT_MIGRATE_REFUSED and MIGRATE_SCHEMA_PROBLEM in err
    assert "schema_checksum_mismatch" in err
    assert not (tmp_path / "backups").exists()


def test_a_second_migration_is_refused_while_one_holds_the_lock(tmp_path):
    path = database_at(tmp_path / "autotrader.db", PREVIOUS)

    with migration_lock(path):
        code, _out, err = _migrate(path)

    assert code == EXIT_MIGRATE_REFUSED and MIGRATE_LOCK_BUSY in err
    assert f"pid {os.getpid()}" in err
    assert _ledger(path) == list(range(1, LATEST))


def test_no_backup_no_migration(tmp_path, monkeypatch):
    path = database_at(tmp_path / "autotrader.db", PREVIOUS)

    async def broken(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(db_admin, "backup_database", broken)
    code, _out, err = _migrate(path)

    assert code == EXIT_MIGRATE_REFUSED
    assert MIGRATE_BACKUP_FAILED in err and "disk full" in err and "nothing was migrated" in err
    assert _ledger(path) == list(range(1, LATEST))
    assert list((tmp_path / "backups").iterdir()) == []  # no partial left behind


def test_a_backup_is_never_overwritten(tmp_path):
    path = database_at(tmp_path / "autotrader.db", PREVIOUS)
    status = inspect_schema(path)
    existing = backup_path(path, status, AFTER_CLOSE)
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b"an earlier backup")

    code, _out, err = _migrate(path)

    assert code == EXIT_MIGRATE_REFUSED and MIGRATE_BACKUP_FAILED in err
    assert existing.read_bytes() == b"an earlier backup"
    assert _ledger(path) == list(range(1, LATEST))


def test_a_failing_migration_rolls_back_and_names_the_backup(tmp_path, monkeypatch):
    path = migrate_database(tmp_path / "autotrader.db")

    async def explode(db) -> None:
        await db.execute("CREATE TABLE half_done (id INTEGER)")
        raise RuntimeError("bad migration")

    broken = Migration(LATEST + 1, "broken", "v1-broken", explode, ())
    monkeypatch.setattr(migrations, "MIGRATIONS", (*MIGRATIONS, broken))
    code, out, err = _migrate(path)

    assert code == EXIT_MIGRATE_REFUSED
    assert MIGRATE_FAILED in err and "bad migration" in err and "rolled back" in err
    [backup] = (tmp_path / "backups").iterdir()
    assert str(backup) in err and f"backup    {backup}" in out
    assert _ledger(path) == list(range(1, LATEST + 1))
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT name FROM sqlite_master WHERE name='half_done'").fetchone() is None


def test_a_new_database_is_created_without_a_backup(tmp_path):
    path = tmp_path / "fresh" / "autotrader.db"

    code, out, _err = _migrate(path)

    assert code == 0 and "nothing to back up" in out
    assert inspect_schema(path).current


# --- db status -----------------------------------------------------------------------


def test_status_is_read_only_and_exits_75_until_migrated(tmp_path):
    path = database_at(tmp_path / "autotrader.db", PREVIOUS)
    out = io.StringIO()

    assert run_status(path, now=MID_SESSION, out=out) == EXIT_SCHEMA_NOT_READY
    report = out.getvalue()
    assert f"pending   {LATEST} {MIGRATIONS[-1].name}" in report
    assert "refused now: inside the 2026-10-13 trading day, 07:55-18:30 ET" in report
    assert _ledger(path) == list(range(1, LATEST))

    assert _migrate(path)[0] == 0
    out = io.StringIO()
    assert run_status(path, now=AFTER_CLOSE, out=out) == 0
    assert f"schema v{LATEST} is current" in out.getvalue()
    assert "allowed now" in out.getvalue()


def test_status_never_creates_a_database(tmp_path):
    path = tmp_path / "absent" / "autotrader.db"

    assert run_status(path, now=AFTER_CLOSE, out=io.StringIO()) == EXIT_SCHEMA_NOT_READY
    assert not path.exists() and not path.parent.exists()


def test_the_cli_routes_db_status_and_migrate(tmp_path, capsys):
    from trading import cli

    path = database_at(tmp_path / "autotrader.db", PREVIOUS)

    assert cli.main(["db", "status", "--db-path", str(path)]) == EXIT_SCHEMA_NOT_READY
    assert "pending" in capsys.readouterr().out
    # The override keeps this test independent of the time of day.
    assert cli.main(["--db-path", str(path), "db", "migrate", "--during-session-i-know"]) == 0
    assert f"-> v{LATEST}" in capsys.readouterr().out
    assert cli.main(["db", "status", "--db-path", str(path)]) == 0
    with pytest.raises(SystemExit) as refused:
        cli.main(["db", "drop"])
    assert refused.value.code == 2

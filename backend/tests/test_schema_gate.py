"""Children never migrate: they refuse to start (exit 75) until `db migrate` has run."""
from __future__ import annotations

import ast
from datetime import date, datetime, timedelta, timezone
import logging
from pathlib import Path
import sqlite3
import sys

import pytest

from tests.schema_support import database_at, migrate_database
from tests.test_supervisor import FakeWorld, Script, et, plans_for
from trading.operations import schema_gate
from trading.operations.schema_gate import (
    EXIT_SCHEMA_NOT_READY,
    SchemaNotReady,
    require_current_schema,
)
from trading.operations.supervisor import always_on_plans, plan_session
from trading.scheduler.sessions import market_session, next_market_session
from trading.storage.migrations import (
    MIGRATIONS,
    SCHEMA_CHECKSUM_MISMATCH,
    SCHEMA_HISTORY_GAP,
    SCHEMA_MIGRATION_PENDING,
    SCHEMA_NEWER_THAN_CODE,
    SCHEMA_UNREADABLE,
    inspect_schema,
)

BACKEND = Path(__file__).resolve().parents[1]
LATEST = MIGRATIONS[-1].version
PREVIOUS = LATEST - 1


def _ledger(path: Path) -> list[int]:
    with sqlite3.connect(path) as db:
        return [row[0] for row in db.execute("SELECT version FROM autotrader_schema_migrations ORDER BY version")]


def _snapshot(path: Path) -> tuple[list[int], list[str]]:
    with sqlite3.connect(path) as db:
        objects = sorted(row[0] for row in db.execute("SELECT name FROM sqlite_master"))
    return _ledger(path), objects


# --- read-only inspection -----------------------------------------------------------


def test_a_missing_database_is_pending_and_is_never_created(tmp_path):
    path = tmp_path / "absent" / "autotrader.db"

    status = inspect_schema(path)

    assert (status.exists, status.applied, status.current) == (False, (), False)
    assert status.pending == tuple(migration.version for migration in MIGRATIONS)
    assert status.token == SCHEMA_MIGRATION_PENDING
    assert not path.exists() and not path.parent.exists()


def test_a_current_database_reads_as_current(tmp_path):
    path = migrate_database(tmp_path / "current.db")

    status = inspect_schema(path)

    assert status.current and status.token is None and status.pending == ()
    assert status.applied_version == status.code_version == LATEST
    assert status.describe() == f"schema v{LATEST} is current"


def test_a_database_one_migration_behind_is_pending_and_left_untouched(tmp_path):
    path = database_at(tmp_path / "behind.db", PREVIOUS)
    before = _snapshot(path)

    status = inspect_schema(path)

    assert status.pending == (LATEST,) and status.token == SCHEMA_MIGRATION_PENDING
    assert not status.current
    assert f"pending: {LATEST}" in status.describe()
    assert _snapshot(path) == before


@pytest.mark.parametrize(
    ("tamper", "token"),
    [
        ("UPDATE autotrader_schema_migrations SET checksum='tampered' WHERE version=1", SCHEMA_CHECKSUM_MISMATCH),
        (
            f"INSERT INTO autotrader_schema_migrations VALUES ({LATEST + 1}, 'from-the-future', 'x', 't')",
            SCHEMA_NEWER_THAN_CODE,
        ),
        ("DELETE FROM autotrader_schema_migrations WHERE version=2", SCHEMA_HISTORY_GAP),
    ],
)
def test_a_history_this_code_must_not_run_on_is_a_named_problem(tmp_path, tamper, token):
    path = migrate_database(tmp_path / "tampered.db")
    with sqlite3.connect(path) as db:
        db.execute(tamper)

    status = inspect_schema(path)

    assert not status.current and status.token == token
    assert status.problems[0].token == token


def test_an_unreadable_file_is_a_named_problem(tmp_path):
    path = tmp_path / "garbage.db"
    path.write_bytes(b"this is not a sqlite database" * 100)

    status = inspect_schema(path)

    assert status.token == SCHEMA_UNREADABLE and not status.current


# --- the gate ------------------------------------------------------------------------


def test_the_gate_refuses_with_the_token_a_critical_line_and_never_writes(tmp_path, caplog):
    path = database_at(tmp_path / "behind.db", PREVIOUS)
    before = _snapshot(path)

    with caplog.at_level(logging.CRITICAL, logger="autotrader.shadow.schema"):
        with pytest.raises(SchemaNotReady) as refused:
            require_current_schema(process="screener run", database=path)

    assert refused.value.token == SCHEMA_MIGRATION_PENDING
    assert "screener run refuses to start" in str(refused.value)
    [record] = [item for item in caplog.records if item.name == "autotrader.shadow.schema"]
    assert record.levelno == logging.CRITICAL
    assert SCHEMA_MIGRATION_PENDING in record.getMessage()
    assert "autotraderctl db migrate" in record.getMessage()
    assert record.stack_info  # a tripwire: where the refusal happened
    assert _snapshot(path) == before


def test_the_gate_passes_a_current_database(tmp_path):
    path = migrate_database(tmp_path / "current.db")

    assert require_current_schema(process="screener run", database=path).current


def test_exit_75_is_temporary_for_every_supervised_child():
    """EX_TEMPFAIL: never a final code, so the supervisor keeps retrying."""
    assert EXIT_SCHEMA_NOT_READY == 75
    for day in (date(2026, 10, 13), date(2026, 11, 27)):
        session = market_session(day)
        assert session is not None
        for plan in plan_session(session):
            assert EXIT_SCHEMA_NOT_READY not in plan.final_codes, plan.name
    assert always_on_plans()  # restarted after any exit, whatever its code


@pytest.mark.anyio
async def test_the_supervisor_restarts_a_child_that_waits_for_a_migration():
    day = date(2026, 10, 13)
    session = market_session(day)
    assert session is not None
    refusal = Script(exit_after=timedelta(seconds=1), code=EXIT_SCHEMA_NOT_READY)
    world = FakeWorld(et(day, 7, 50), scripts={"screener": [refusal, refusal]})
    world.stop_at = et(day, 8, 1)
    supervisor = world.supervisor_for(planner=lambda _session: plans_for(session)[:1])

    await supervisor.run(session_date=day)

    exits = [line for line in world.child_lines("screener") if "CHILD_EXIT" in line]
    assert exits[0].endswith("CHILD_EXIT screener code=75 restart_in=15")
    assert exits[1].endswith("CHILD_EXIT screener code=75 restart_in=30")
    # Once migrated, the third start runs until the stop.
    assert len(world.launch_times("screener")) == 3


# --- every entry point that uses the database ---------------------------------------


@pytest.fixture
def pending_database(tmp_path, monkeypatch):
    """The live situation after a deploy without `db migrate`: one migration pending."""
    from config import cfg

    path = database_at(tmp_path / "pending.db", PREVIOUS)
    monkeypatch.setattr(cfg, "DB_PATH", str(path))
    return path


@pytest.fixture
def quiet_logs(monkeypatch):
    """Keep refusals off the real private log directory."""
    from trading import cli
    from trading.operations import logging as shadow_logging

    def quiet(**_kwargs):
        return logging.getLogger("test.schema.shadow")

    monkeypatch.setattr(shadow_logging, "configure_shadow_logging", quiet)
    monkeypatch.setattr(cli, "_interactive_screener_logger", lambda: logging.getLogger("test.schema"))
    monkeypatch.setattr(cli, "_interactive_research_logger", lambda: logging.getLogger("test.schema"))


def _refused(capsys, path: Path, before) -> None:
    error = capsys.readouterr().err
    assert SCHEMA_MIGRATION_PENDING in error and "autotraderctl db migrate" in error
    assert _snapshot(path) == before


@pytest.mark.parametrize(
    "argv",
    [
        ["screener", "run", "--once"],
        ["universe", "qualify", "--date", "2026-10-12"],
        ["research", "record"],
        ["research", "backfill", "--days", "3"],
    ],
)
def test_market_data_and_research_children_refuse_without_migrating(
    argv, pending_database, quiet_logs, capsys, monkeypatch
):
    from trading import cli
    from trading.operations import screener_service
    from trading.research import recorder

    async def never(*_args, **_kwargs):
        raise AssertionError("must not run while a migration is pending")

    monkeypatch.setattr(screener_service, "run_screener_once", never)
    monkeypatch.setattr(screener_service, "qualify_session", never)
    monkeypatch.setattr(recorder, "run_backfill_cli", never)
    before = _snapshot(pending_database)

    assert cli.main(argv) == EXIT_SCHEMA_NOT_READY

    _refused(capsys, pending_database, before)


def test_a_strategy_child_refuses_without_migrating(pending_database, quiet_logs, capsys, monkeypatch):
    from trading import cli
    from trading.operations import shadow_service

    async def never(**_kwargs):
        raise AssertionError("a strategy must not run while a migration is pending")

    monkeypatch.setattr(shadow_service, "run_shadow_day", never)
    session = next_market_session(datetime.now(timezone.utc)).session_date
    before = _snapshot(pending_database)

    code = cli.main(["shadow", "run", "--strategy", "momo", "--universe", "watchlist", "--date", session.isoformat()])

    assert code == EXIT_SCHEMA_NOT_READY
    _refused(capsys, pending_database, before)


def test_the_telegram_assistant_alerts_then_refuses_without_migrating(
    pending_database, capsys, monkeypatch
):
    from trading import cli
    from trading.operations import schema_alert

    alerts: list[object] = []

    async def fake_alert(status, **_kwargs) -> bool:
        alerts.append(status)
        return True

    class Assistant:
        async def run_telegram_assistant(self) -> int:
            raise AssertionError("the assistant must not serve while a migration is pending")

    monkeypatch.setattr(schema_alert, "send_schema_alert", fake_alert)
    monkeypatch.setitem(sys.modules, cli.TELEGRAM_BOT_MODULE, Assistant())
    before = _snapshot(pending_database)

    assert cli.main(["telegram", "run"]) == EXIT_SCHEMA_NOT_READY

    [status] = alerts
    assert status.pending == (LATEST,)
    _refused(capsys, pending_database, before)


def test_a_failing_alert_never_turns_the_refusal_into_a_crash(pending_database, capsys, monkeypatch):
    from trading import cli
    from trading.operations import schema_alert

    async def broken(status, **_kwargs) -> bool:
        raise RuntimeError("telegram down")

    monkeypatch.setattr(schema_alert, "send_schema_alert", broken)
    monkeypatch.setitem(sys.modules, cli.TELEGRAM_BOT_MODULE, object())

    assert cli.main(["telegram", "run"]) == EXIT_SCHEMA_NOT_READY
    assert SCHEMA_MIGRATION_PENDING in capsys.readouterr().err


@pytest.mark.anyio
async def test_the_api_refuses_to_start_on_a_pending_migration_and_starts_once_current(
    tmp_path, monkeypatch
):
    from config import cfg
    from db.core import init_db
    from main import app, lifespan
    from tests.schema_support import apply_up_to

    path = await apply_up_to(tmp_path / "api.db", PREVIOUS)
    monkeypatch.setattr(cfg, "DB_PATH", str(path))
    before = _snapshot(path)
    with pytest.raises(SchemaNotReady):
        async with lifespan(app):
            pass
    assert _snapshot(path) == before

    monkeypatch.setattr(cfg, "DB_PATH", str(tmp_path / "api-current.db"))
    await init_db()  # what `autotraderctl db migrate` does
    async with lifespan(app):
        pass


# --- the supervisor's dry run reports it loudly, read-only ---------------------------


def test_the_dry_run_reports_a_pending_migration_loudly_and_reads_only(tmp_path):
    from trading.operations.supervisor import dry_run_report

    pending = database_at(tmp_path / "pending.db", PREVIOUS)
    before = _snapshot(pending)

    report = dry_run_report(
        session_date=date(2026, 10, 13), clock_start=None, unsupervised=list, database=pending
    ).splitlines()

    assert report[0] == "DRY RUN: nothing is launched and no log is written."
    assert report[1].startswith(f"!!! DATABASE NOT READY ({SCHEMA_MIGRATION_PENDING}): schema v{PREVIOUS}")
    assert "until `autotraderctl db migrate`" in report[2]
    assert _snapshot(pending) == before

    current = migrate_database(tmp_path / "current.db")
    report = dry_run_report(session_date=date(2026, 10, 13), unsupervised=list, database=current)
    assert f"database     schema v{LATEST} current" in report
    assert "!!!" not in report

    missing = tmp_path / "missing" / "autotrader.db"
    report = dry_run_report(session_date=date(2026, 10, 13), unsupervised=list, database=missing)
    assert "!!! DATABASE NOT READY" in report and "no database file" in report
    assert not missing.parent.exists()


# --- no runtime path can migrate -----------------------------------------------------

# The only production modules allowed to reach the migration code: the
# migrations themselves, db.core (init_db) and the explicit `db migrate`.
MIGRATION_CALLERS = {
    "db/core.py",
    "trading/storage/migrations.py",
    "trading/operations/db_admin.py",
}
MIGRATION_ENTRY_POINTS = {"init_db", "apply_migrations"}


def _production_sources() -> list[Path]:
    roots = [BACKEND / "trading", BACKEND / "db", BACKEND / "routers", BACKEND / "scripts_local"]
    files = [path for root in roots for path in root.rglob("*.py")]
    files += [BACKEND / name for name in ("main.py", "auth.py", "config.py", "startup.py", "autotraderctl.py", "middleware.py")]
    return sorted(files)


def test_only_db_migrate_can_reach_the_migration_code():
    offenders: list[str] = []
    for path in _production_sources():
        relative = path.relative_to(BACKEND).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            named = None
            if isinstance(node, ast.Name):
                named = node.id
            elif isinstance(node, ast.Attribute):
                named = node.attr
            elif isinstance(node, ast.alias):
                named = node.name.rsplit(".", 1)[-1]
            if named in MIGRATION_ENTRY_POINTS and relative not in MIGRATION_CALLERS:
                offenders.append(f"{relative}:{getattr(node, 'lineno', '?')} {named}")
    assert not offenders, "a runtime path can migrate implicitly: " + ", ".join(offenders)


def test_the_gate_module_is_read_only():
    source = Path(schema_gate.__file__).read_text(encoding="utf-8")
    for forbidden in ("init_db", "apply_migrations", "get_db", "transaction("):
        assert forbidden not in source

"""autotraderctl execution: the explicit, recorded PAPER grant, and the engine's hand-started run.

``execution run`` is the supervised drill's entry point: every refusal comes
before TWS is contacted, and nothing schedules it (tests/test_execution_runner.py).
"""
from __future__ import annotations

import sqlite3

import pytest

from tests.schema_support import database_at, migrate_database
from trading.cli import main
from trading.execution.promotion import engine_code_digest
from trading.storage.execution_ledger_schema import EXECUTION_LEDGER_MIGRATION
from trading.storage.migrations import inspect_schema


ACCOUNT = "DU1234567"


def test_promote_needs_confirm_records_the_grant_and_status_shows_it(tmp_path, capsys) -> None:
    database = tmp_path / "authority.db"
    base = ["execution", "promote", "--db-path", str(database), "--strategy", "MOMO_HOD_V2", "--account", ACCOUNT,
            "--reason", "execution-safety review passed"]

    assert main(base) == 2
    assert "re-run with --confirm" in capsys.readouterr().err
    assert not database.exists()

    migrate_database(database)  # `autotraderctl db migrate` created it at the code's schema
    assert main([*base, "--confirm"]) == 0
    out = capsys.readouterr().out.splitlines()
    [backup] = list((tmp_path / "backups").iterdir())
    assert out[0] == f"database backed up to {backup}"
    assert backup.name.startswith("authority.before-execution-promote-") and backup.suffix == ".db"
    assert out[1].startswith("PAPER promotion recorded: MOMO_HOD_V2 on DU1234567 for engine code")
    with sqlite3.connect(database) as connection:
        rows = connection.execute("SELECT action, strategy_name, account, code_digest FROM autotrader_exec_promotions"
                                  ).fetchall()
    assert rows == [("grant", "MOMO_HOD_V2", ACCOUNT, engine_code_digest())]

    assert main(["execution", "status", "--db-path", str(database)]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out == [f"execution engine code: {engine_code_digest()}",
                   "execution engine: not wired; only `execution run --confirm` starts it, by hand (the PAPER"
                   " bridge is the scheduled order sender)",
                   "DU1234567: MOMO_HOD_V2"]

    assert main(["execution", "revoke", "--db-path", str(database), "--strategy", "MOMO_HOD_V2", "--account",
                 ACCOUNT, "--reason", "pause"]) == 0
    capsys.readouterr()
    assert main(["execution", "status", "--db-path", str(database)]) == 0
    assert capsys.readouterr().out.splitlines()[-1] == "DU1234567: no active promotion for this code"


def test_promote_backs_up_the_database_first_and_refuses_without_a_backup(tmp_path, capsys) -> None:
    database = tmp_path / "live.db"
    promote = ["execution", "promote", "--db-path", str(database), "--strategy", "MOMO_HOD_V2", "--account",
               ACCOUNT, "--reason", "review passed", "--confirm"]

    assert main(promote) == 78  # no database: a wrong --db-path never creates a fresh one with authority in it
    assert capsys.readouterr().err.startswith(f"error: no autotrader database at {database}")
    assert not database.exists()

    migrate_database(database)
    (tmp_path / "backups").write_text("not a directory")  # the backup cannot be written
    assert main(promote) == 1
    assert capsys.readouterr().err.startswith("error: database backup failed (FileExistsError")
    with sqlite3.connect(database) as connection:  # nothing was promoted
        assert connection.execute("SELECT COUNT(*) FROM autotrader_exec_promotions").fetchone() == (0,)

    (tmp_path / "backups").unlink()
    assert main(promote) == 0
    [backup] = list((tmp_path / "backups").iterdir())
    assert capsys.readouterr().out.splitlines()[0] == f"database backed up to {backup}"
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM autotrader_exec_promotions").fetchone() == (1,)
    with sqlite3.connect(backup) as connection:  # the database exactly as it was before the grant
        assert connection.execute("SELECT COUNT(*) FROM autotrader_exec_promotions").fetchone() == (0,)


@pytest.mark.parametrize("action", ["promote", "revoke"])
@pytest.mark.parametrize("version", [0, EXECUTION_LEDGER_MIGRATION - 1], ids=["empty", "before-the-ledger"])
def test_promote_and_revoke_never_migrate(tmp_path, capsys, action: str, version: int) -> None:
    database = tmp_path / "live.db"
    if version:
        database_at(database, version)  # the owner's database before `db migrate` knows the ledger
    else:
        sqlite3.connect(database).close()
    before = inspect_schema(database)
    command = ["execution", action, "--db-path", str(database), "--strategy", "MOMO_HOD_V2", "--account",
               ACCOUNT, "--reason", "drill"]

    assert main([*command, "--confirm"] if action == "promote" else command) == 78

    err = capsys.readouterr().err
    assert "the database schema is not this code's" in err and "run `autotraderctl db migrate`" in err
    assert f"once it knows migration {EXECUTION_LEDGER_MIGRATION}" in err
    after = inspect_schema(database)
    assert (after.applied, after.pending) == (before.applied, before.pending)  # nothing applied, nothing written
    assert not (tmp_path / "backups").exists()


def test_promote_refuses_shadow_only_strategies_and_live_accounts(tmp_path, capsys) -> None:
    database = tmp_path / "authority.db"
    sqlite3.connect(database).close()
    for strategy, account, code in (("ORB_V1", ACCOUNT, "strategy_not_promotable"),
                                    ("MOMO_HOD_V2", "U1234567", "not_paper_account")):
        assert main(["execution", "promote", "--db-path", str(database), "--strategy", strategy, "--account",
                     account, "--reason", "test", "--confirm"]) == 1
        assert capsys.readouterr().err.startswith(f"error: {code}")
    assert not (tmp_path / "backups").exists()  # refused before the database is backed up or migrated
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT name FROM sqlite_master").fetchall() == []


def test_status_is_read_only(tmp_path, capsys) -> None:
    missing = tmp_path / "absent.db"
    assert main(["execution", "status", "--db-path", str(missing)]) == 0
    assert capsys.readouterr().out.strip() == "no autotrader database: no PAPER promotion"
    assert not missing.exists()

    old = tmp_path / "old.db"
    sqlite3.connect(old).close()  # a database without the execution-ledger migration
    assert main(["execution", "status", "--db-path", str(old)]) == 0
    assert capsys.readouterr().out.strip() == (
        f"execution ledger not installed (migration {EXECUTION_LEDGER_MIGRATION} not applied): no PAPER promotion"
    )
    with sqlite3.connect(old) as connection:
        assert connection.execute("SELECT name FROM sqlite_master").fetchall() == []


@pytest.mark.parametrize("arguments", [["execution", "start"], ["execution", "order"], ["execution", "send"]])
def test_no_other_command_runs_the_engine_or_sends_an_order(arguments, capsys) -> None:
    with pytest.raises(SystemExit) as error:
        main(arguments)
    assert error.value.code == 2
    assert "invalid choice" in capsys.readouterr().err


def never_connects(config: object) -> object:
    raise AssertionError("the runner reached TWS")


def test_run_needs_confirm_and_refuses_before_tws(tmp_path, capsys, monkeypatch) -> None:
    import trading.execution.runner as runner

    monkeypatch.setattr(runner, "IbapiTransport", never_connects)
    database = tmp_path / "live.db"
    base = ["execution", "run", "--db-path", str(database)]

    assert main(base) == 2
    assert "re-run with --confirm" in capsys.readouterr().err

    assert main([*base, "--confirm"]) == 78  # no database
    assert f"refusing: no autotrader database at {database}; exit 78" in capsys.readouterr().out
    assert not database.exists()

    assert main([*base, "--confirm", "--date", "2999-01-04"]) == 78
    assert "is after today" in capsys.readouterr().out

    sqlite3.connect(database).close()
    assert main([*base, "--confirm"]) == 78  # pending migrations: the runner never migrates
    assert "the database schema is not this code's" in capsys.readouterr().out
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT name FROM sqlite_master").fetchall() == []


async def _migrated(path) -> None:
    from config import cfg
    from db.core import init_db

    original = cfg.DB_PATH
    cfg.DB_PATH = str(path)
    try:
        await init_db()
    finally:
        cfg.DB_PATH = original


def test_run_refuses_without_the_shadow_configuration_or_a_grant(tmp_path, capsys, monkeypatch) -> None:
    import asyncio

    import trading.execution.runner as runner
    from trading.operations.settings import ShadowSettingsError

    monkeypatch.setattr(runner, "IbapiTransport", never_connects)
    database = tmp_path / "live.db"
    asyncio.run(_migrated(database))
    run = ["execution", "run", "--db-path", str(database), "--confirm"]

    def no_configuration() -> runner.RunnerConfig:
        raise ShadowSettingsError("shadow configuration is unavailable at ~/.config/autotrader-v1/config.toml")

    monkeypatch.setattr(runner, "runner_config", no_configuration)
    assert main(run) == 78
    assert "refusing: shadow configuration is unavailable" in capsys.readouterr().out

    connected: list[object] = []
    monkeypatch.setattr(runner, "runner_config", lambda: runner.RunnerConfig(
        account=ACCOUNT, client_id=18, host="127.0.0.1", port=7497))
    monkeypatch.setattr(runner, "IbapiTransport", lambda config: _Unreached(config, connected))
    assert main(run) == 78  # no PAPER grant for this code: refused before TWS
    assert "refusing: promotion_missing" in capsys.readouterr().out
    assert connected == []


class _Unreached:
    """A transport whose connect must never run (the runner refuses first)."""

    def __init__(self, config: object, log: list[object]) -> None:
        from tests.fakes.fake_tws import FakeTws
        from trading.execution.tws_state import TwsMirror

        self.broker = FakeTws(wrapper=None)
        self.mirror = TwsMirror(18)
        self.log = log

    async def connect(self) -> None:
        self.log.append("connect")

    def disconnect(self) -> None:
        self.log.append("disconnect")

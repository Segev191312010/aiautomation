"""autotraderctl execution: the explicit, recorded PAPER grant, and nothing that runs the engine."""
from __future__ import annotations

import sqlite3

import pytest

from trading.cli import main
from trading.execution.promotion import engine_code_digest


ACCOUNT = "DU1234567"


def test_promote_needs_confirm_records_the_grant_and_status_shows_it(tmp_path, capsys) -> None:
    database = tmp_path / "authority.db"
    base = ["execution", "promote", "--db-path", str(database), "--strategy", "MOMO_HOD_V2", "--account", ACCOUNT,
            "--reason", "execution-safety review passed"]

    assert main(base) == 2
    assert "re-run with --confirm" in capsys.readouterr().err
    assert not database.exists()

    assert main([*base, "--confirm"]) == 0
    assert capsys.readouterr().out.startswith("PAPER promotion recorded: MOMO_HOD_V2 on DU1234567 for engine code")
    with sqlite3.connect(database) as connection:
        rows = connection.execute("SELECT action, strategy_name, account, code_digest FROM autotrader_exec_promotions"
                                  ).fetchall()
    assert rows == [("grant", "MOMO_HOD_V2", ACCOUNT, engine_code_digest())]

    assert main(["execution", "status", "--db-path", str(database)]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out == [f"execution engine code: {engine_code_digest()}",
                   "execution engine: built, not wired (the PAPER bridge is the only order sender)",
                   "DU1234567: MOMO_HOD_V2"]

    assert main(["execution", "revoke", "--db-path", str(database), "--strategy", "MOMO_HOD_V2", "--account",
                 ACCOUNT, "--reason", "pause"]) == 0
    capsys.readouterr()
    assert main(["execution", "status", "--db-path", str(database)]) == 0
    assert capsys.readouterr().out.splitlines()[-1] == "DU1234567: no active promotion for this code"


def test_promote_refuses_shadow_only_strategies_and_live_accounts(tmp_path, capsys) -> None:
    database = tmp_path / "authority.db"
    for strategy, account, code in (("ORB_V1", ACCOUNT, "strategy_not_promotable"),
                                    ("MOMO_HOD_V2", "U1234567", "not_paper_account")):
        assert main(["execution", "promote", "--db-path", str(database), "--strategy", strategy, "--account",
                     account, "--reason", "test", "--confirm"]) == 1
        assert capsys.readouterr().err.startswith(f"error: {code}")


def test_status_is_read_only(tmp_path, capsys) -> None:
    missing = tmp_path / "absent.db"
    assert main(["execution", "status", "--db-path", str(missing)]) == 0
    assert capsys.readouterr().out.strip() == "no autotrader database: no PAPER promotion"
    assert not missing.exists()

    old = tmp_path / "old.db"
    sqlite3.connect(old).close()  # a database without migration 13
    assert main(["execution", "status", "--db-path", str(old)]) == 0
    assert capsys.readouterr().out.strip() == (
        "execution ledger not installed (migration 13 not applied): no PAPER promotion"
    )
    with sqlite3.connect(old) as connection:
        assert connection.execute("SELECT name FROM sqlite_master").fetchall() == []


@pytest.mark.parametrize("arguments", [["execution", "start"], ["execution", "run"], ["execution", "order"],
                                       ["execution", "send"]])
def test_no_command_runs_the_engine_or_sends_an_order(arguments, capsys) -> None:
    with pytest.raises(SystemExit) as error:
        main(arguments)
    assert error.value.code == 2
    assert "invalid choice" in capsys.readouterr().err

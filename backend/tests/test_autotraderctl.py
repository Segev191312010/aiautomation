from __future__ import annotations

import sqlite3

import pytest

from trading.cli import main


def test_paper_status_initializes_only_requested_database(tmp_path, capsys):
    database_path = tmp_path / "isolated" / "paper.db"

    assert main(["paper", "status", "--db-path", str(database_path)]) == 0

    output = capsys.readouterr()
    assert output.out.strip() == "PAPER kill switch: INACTIVE"
    assert output.err == ""
    assert database_path.exists()


def test_paper_stop_is_durable_and_monotonic(tmp_path, capsys):
    database_path = tmp_path / "paper.db"

    assert (
        main(
            [
                "--db-path",
                str(database_path),
                "paper",
                "stop",
                "--reason",
                "manual safety stop",
            ]
        )
        == 0
    )
    assert capsys.readouterr().out.strip() == "PAPER kill switch: ACTIVE"

    assert main(["paper", "status", "--db-path", str(database_path)]) == 0
    assert capsys.readouterr().out.strip() == "PAPER kill switch: ACTIVE"

    with sqlite3.connect(database_path) as database:
        row = database.execute(
            "SELECT stopped, reason FROM autotrader_execution_fences WHERE name='paper'"
        ).fetchone()
    assert row == (1, "manual safety stop")


def test_stop_default_reason_does_not_print_or_require_secrets(tmp_path, capsys):
    database_path = tmp_path / "paper.db"

    assert main(["paper", "stop", "--db-path", str(database_path)]) == 0

    output = capsys.readouterr()
    assert output.out.strip() == "PAPER kill switch: ACTIVE"
    assert output.err == ""


@pytest.mark.parametrize(
    "arguments",
    [
        ["paper", "start"],
        ["paper", "promote"],
        ["paper", "order"],
    ],
)
def test_unsupported_authority_commands_are_not_exposed(arguments, capsys):
    with pytest.raises(SystemExit) as error:
        main(arguments)

    assert error.value.code == 2
    output = capsys.readouterr()
    assert "invalid choice" in output.err


def test_invalid_command_does_not_initialize_a_database(tmp_path, monkeypatch):
    database_path = tmp_path / "must-not-exist.db"
    monkeypatch.chdir(tmp_path)

    with pytest.raises(SystemExit):
        main(["paper", "start", "--db-path", str(database_path)])

    assert not database_path.exists()

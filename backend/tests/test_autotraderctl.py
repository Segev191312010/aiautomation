from __future__ import annotations

import sqlite3

import pytest

from tests.schema_support import database_at, migrate_database
from trading.cli import main
from trading.storage.migrations import MIGRATIONS


def test_paper_status_never_creates_or_migrates_a_database(tmp_path, capsys):
    database_path = tmp_path / "isolated" / "paper.db"

    assert main(["paper", "status", "--db-path", str(database_path)]) == 1

    output = capsys.readouterr()
    assert output.out == ""
    assert "autotraderctl db migrate" in output.err
    assert not database_path.exists()


def test_paper_status_reads_only_the_requested_database(tmp_path, capsys):
    database_path = migrate_database(tmp_path / "isolated" / "paper.db")

    assert main(["paper", "status", "--db-path", str(database_path)]) == 0

    output = capsys.readouterr()
    assert output.out.strip() == "PAPER kill switch: INACTIVE"
    assert output.err == ""


def test_the_kill_switch_works_while_a_migration_is_pending(tmp_path, capsys):
    # The owner can always stop new PAPER entries: the fence table predates
    # any pending migration, and paper control never waits for `db migrate`.
    previous = MIGRATIONS[-1].version - 1
    database_path = database_at(tmp_path / "pending.db", previous)

    assert main(["paper", "stop", "--db-path", str(database_path), "--reason", "pending"]) == 0
    assert capsys.readouterr().out.strip() == "PAPER kill switch: ACTIVE"
    with sqlite3.connect(database_path) as database:
        versions = [row[0] for row in database.execute("SELECT version FROM autotrader_schema_migrations")]
        stopped = database.execute(
            "SELECT stopped FROM autotrader_execution_fences WHERE name='paper'"
        ).fetchone()
    assert max(versions) == previous  # nothing was migrated
    assert stopped == (1,)


def test_paper_stop_is_durable_and_monotonic(tmp_path, capsys):
    database_path = migrate_database(tmp_path / "paper.db")

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
    database_path = migrate_database(tmp_path / "paper.db")

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


# --- per-identity fences (bridge v7) ------------------------------------------------


def test_paper_stop_with_identity_fences_only_that_identity(tmp_path, capsys):
    database_path = migrate_database(tmp_path / "paper.db")

    assert main(["paper", "stop", "--db-path", str(database_path), "--identity", "MOMO_HOD_V2",
                 "--reason", "first-day caution"]) == 0
    assert capsys.readouterr().out.strip() == "PAPER identity fence MOMO_HOD_V2: STOPPED"

    with sqlite3.connect(database_path) as database:
        rows = database.execute(
            "SELECT name, stopped, reason FROM autotrader_execution_fences ORDER BY name"
        ).fetchall()
    assert rows == [("paper:MOMO_HOD_V2", 1, "first-day caution")]  # no global row

    assert main(["paper", "status", "--db-path", str(database_path)]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "PAPER kill switch: INACTIVE",
        "identity MOMO_HOD_V2: STOPPED (first-day caution)",
    ]


def test_paper_resume_lifts_an_identity_fence_and_never_the_global_stop(tmp_path, capsys):
    database_path = migrate_database(tmp_path / "paper.db")
    assert main(["paper", "stop", "--db-path", str(database_path), "--identity", "MOMO_HOD_V2"]) == 0
    assert main(["paper", "stop", "--db-path", str(database_path)]) == 0
    capsys.readouterr()

    assert main(["paper", "resume", "--db-path", str(database_path), "--identity", "MOMO_HOD_V2",
                 "--reason", "fixed"]) == 0
    assert capsys.readouterr().out.strip() == "PAPER identity fence MOMO_HOD_V2: RESUMED"

    assert main(["paper", "status", "--db-path", str(database_path)]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "PAPER kill switch: ACTIVE",
        "identity MOMO_HOD_V2: RESUMED",
    ]
    with sqlite3.connect(database_path) as database:
        rows = database.execute(
            "SELECT name, stopped, reason FROM autotrader_execution_fences ORDER BY name"
        ).fetchall()
    assert rows == [("paper", 1, "operator stop via autotraderctl"), ("paper:MOMO_HOD_V2", 0, "fixed")]

    # The global switch has no resume.
    assert main(["paper", "resume", "--db-path", str(database_path)]) == 2
    assert "resume needs --identity" in capsys.readouterr().err
    assert main(["paper", "status", "--db-path", str(database_path)]) == 0
    assert capsys.readouterr().out.splitlines()[0] == "PAPER kill switch: ACTIVE"


def test_paper_resume_of_a_sentinel_fence_leaves_the_fences_date_in_its_reason(tmp_path, capsys):
    """N15a LOW-5: the trace the Sentinel reads from the table alone, so an owner's
    resume holds for the day even when the notifier's state file is lost."""
    from datetime import datetime, timezone

    from trading.operations.sentinel import owner_resumed_on
    from trading.scheduler.sessions import MARKET_TIMEZONE
    from trading.storage.runtime_fence import FenceRow

    database_path = migrate_database(tmp_path / "paper.db")
    assert main(["paper", "stop", "--db-path", str(database_path), "--identity", "ORB_V2",
                 "--reason", "sentinel: daily loss 1.30 R > 1.25 R (realized -$130.00)"]) == 0
    with sqlite3.connect(database_path) as database:
        (fenced_at,) = database.execute(
            "SELECT updated_at FROM autotrader_execution_fences WHERE name='paper:ORB_V2'"
        ).fetchone()
    day = datetime.fromisoformat(fenced_at).astimezone(MARKET_TIMEZONE).date()
    assert main(["paper", "resume", "--db-path", str(database_path), "--identity", "ORB_V2",
                 "--reason", "owner: checked"]) == 0
    assert capsys.readouterr().out.splitlines()[-1] == "PAPER identity fence ORB_V2: RESUMED"
    with sqlite3.connect(database_path) as database:
        stopped, reason, resumed_at = database.execute(
            "SELECT stopped, reason, updated_at FROM autotrader_execution_fences WHERE name='paper:ORB_V2'"
        ).fetchone()
    assert (stopped, reason) == (0, f"owner: checked (lifts sentinel fence of {day.isoformat()})")
    resumed_day = datetime.fromisoformat(resumed_at).astimezone(MARKET_TIMEZONE).date()
    assert owner_resumed_on(FenceRow(False, reason, resumed_at), day) is (resumed_day == day)
    assert datetime.fromisoformat(resumed_at).tzinfo == timezone.utc

    # Resuming a row that is not stopped, or an owner's own stop, keeps the reason as typed.
    assert main(["paper", "resume", "--db-path", str(database_path), "--identity", "ORB_V2",
                 "--reason", "again"]) == 0
    with sqlite3.connect(database_path) as database:
        assert database.execute(
            "SELECT reason FROM autotrader_execution_fences WHERE name='paper:ORB_V2'"
        ).fetchone() == ("again",)


@pytest.mark.parametrize("action", ["stop", "resume"])
@pytest.mark.parametrize("identity", ["AAPL", "ORB_V1", "momo_hod_v2", "AP_MOMO_HOD_V1"])
def test_paper_identity_commands_refuse_names_that_are_not_sendable(tmp_path, capsys, action, identity):
    database_path = migrate_database(tmp_path / "paper.db")

    assert main(["paper", action, "--db-path", str(database_path), "--identity", identity]) == 2
    assert "is not a sendable identity" in capsys.readouterr().err
    with sqlite3.connect(database_path) as database:
        assert database.execute("SELECT count(*) FROM autotrader_execution_fences").fetchone() == (0,)


def test_deploy_exposes_check_apply_and_undo_but_never_merge(capsys):
    from pathlib import Path

    from trading.cli import _parser

    arguments = _parser().parse_args(
        [
            "deploy", "check", "--candidate", "bridge-v7b", "--scope", "A6,A7",
            "--ledger", "docs/reviews/*ledger*.md", "--repo", "/repo", "--db-path", "/x.db",
            "--expect-parent", "88b963d", "--now", "2026-10-19T02:00:00+03:00", "--skip-clock",
        ]
    )
    assert (arguments.domain, arguments.action, arguments.candidate, arguments.scope) == (
        "deploy", "check", "bridge-v7b", "A6,A7",
    )
    assert (arguments.ledger, arguments.repo, arguments.db_path) == (
        "docs/reviews/*ledger*.md", Path("/repo"), Path("/x.db"),
    )
    assert (arguments.expect_parent, arguments.now, arguments.skip_clock) == (
        "88b963d", "2026-10-19T02:00:00+03:00", True,
    )
    # --repo has no default (review P1-A F-8: it defaulted to the running code's
    # checkout, the candidate itself when run from its worktree as documented).
    defaults = _parser().parse_args(["deploy", "check", "--candidate", "c", "--repo", "/repo"])
    assert (defaults.scope, defaults.ledger, defaults.expect_parent, defaults.now) == (
        None, None, None, None,
    )
    assert defaults.skip_clock is False and not hasattr(defaults, "db_path")
    with pytest.raises(SystemExit) as error:
        _parser().parse_args(["deploy", "check", "--candidate", "c"])
    assert error.value.code == 2
    assert "the following arguments are required: --repo" in capsys.readouterr().err

    # Ticket P2 (plan P2-1..P2-4, rewritten explicitly, not weakened): the write
    # half now exists as `deploy apply` and `deploy undo`, owner-run only (a TTY
    # and the typed short sha) and reviewed under scope P2. Both are valid verbs
    # whose required arguments are enforced; `merge` stays an invalid choice: the
    # fast-forward exists only inside `apply`, never as a verb of its own.
    for action, required in (
        (["apply"], "--candidate, --repo, --expect-parent, --expect-pin, --scope"),
        (["undo"], "--repo, --to, --reason"),
        (["check"], "--candidate, --repo"),
    ):
        with pytest.raises(SystemExit) as error:
            main(["deploy", *action])
        assert error.value.code == 2
        err = capsys.readouterr().err
        assert "invalid choice" not in err
        assert f"the following arguments are required: {required}" in err
    with pytest.raises(SystemExit) as error:
        main(["deploy", "merge"])
    assert error.value.code == 2
    err = capsys.readouterr().err
    assert "invalid choice: 'merge' (choose from 'check', 'apply', 'undo')" in err

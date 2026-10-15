"""``autotraderctl deploy check``: runbook 6.2 STEP 0 as code, read-only.

Every git scenario is a real repository built in ``tmp_path``; the clock probe
is always injected (an autouse fixture fails any test that would reach sntp).
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
import hashlib
import io
import os
from pathlib import Path
import re
import sqlite3
import subprocess

import pytest

from tests.schema_support import database_at, migrate_database
from trading.cli import main
from trading.operations import deploy_check
from trading.operations.deploy_check import (
    CLOCK,
    FENCE,
    FILES,
    OK,
    PARENT,
    PIN,
    REFUSE,
    SCHEMA,
    TOKENS,
    TREE_CLEAN,
    VERDICT,
    WARN,
    WINDOW,
    ClockUnavailable,
    DeployCheckRequest,
    Git,
)
from trading.storage.migrations import MIGRATIONS

ISRAEL = timezone(timedelta(hours=3))
# Sunday 2026-10-18 19:00 EDT: inside the window, outside every trading day.
INSIDE_WINDOW = datetime(2026, 10, 19, 2, 0, tzinfo=ISRAEL)
GOOD_SNTP = "+0.012345 +/- 0.023456 time.apple.com 17.253.4.253\n"
LEDGER = "docs/reviews/2026-10-15-ledger.md"
BRIDGE = "backend/scripts_local/paper_bridge.py"
SUPERVISOR = "backend/trading/operations/supervisor.py"
LINE_GRAMMAR = re.compile(r"^(OK [a-z_]+|WARN [a-z_]+: .+|REFUSE [a-z_]+: .+)$")
_GIT_ENV_DROP = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR")


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def refuse() -> str:
        raise AssertionError("a test reached the real sntp")

    monkeypatch.setattr(deploy_check, "system_sntp", refuse)


# --- a real repository ---------------------------------------------------------


def _git(repo: Path, *args: str) -> str:
    env = {key: value for key, value in os.environ.items() if key not in _GIT_ENV_DROP}
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    completed = subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "-c",
            "commit.gpgsign=false",
            *args,
        ],
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )
    return completed.stdout.strip()


@dataclass
class Repo:
    path: Path

    def write(self, relative: str, content: str | bytes) -> None:
        target = self.path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            target.write_bytes(content)
        else:
            target.write_text(content)

    def append(self, relative: str, line: str) -> None:
        target = self.path / relative
        target.write_text(target.read_text() + line + "\n")

    def commit(self, message: str) -> str:
        _git(self.path, "add", "-A")
        _git(self.path, "commit", "-q", "-m", message)
        return _git(self.path, "rev-parse", "HEAD")

    def git(self, *args: str) -> str:
        return _git(self.path, *args)


def _supervisor_text(pin: str) -> str:
    return f'"""supervisor"""\nREVIEWED_BRIDGE_SHA256 = "{pin}"\n__all__ = ["REVIEWED_BRIDGE_SHA256"]\n'


def _verdict(sha: str, scope: str = "A1,A2") -> str:
    return f"2026-10-15T20:00:00+03:00 | v9 {sha} verdict=GO scope={scope} | GO; no HIGH | docs/reviews/r.md"


@dataclass
class Scenario:
    repo: Repo
    base: str
    reviewed: str
    head: str


def _scenario(tmp_path: Path, *, pin_ok: bool = True, verdict_sha_length: int = 7) -> Scenario:
    """``main`` at ``base`` (checked out); ``cand`` = base -> reviewed backend commit
    -> one docs/reviews commit that adds the verdict line naming it."""
    repo = Repo(tmp_path / "repo")
    repo.path.mkdir(parents=True)
    _git(repo.path, "init", "-q", "-b", "main")
    bridge = "print('bridge v1')\n"
    repo.write(BRIDGE, bridge)
    repo.write(SUPERVISOR, _supervisor_text(hashlib.sha256(bridge.encode()).hexdigest()))
    repo.write(LEDGER, "# Ledger\n")
    repo.write("docs/reviews/2026-10-15-v9-review.md", "# Review\n")
    repo.write(".claude/settings.local.json", "{}\n")
    repo.write("README.md", "readme\n")
    base = repo.commit("base")
    repo.git("checkout", "-q", "-b", "cand")
    repo.write("backend/trading/feature.py", "FEATURE = 1\n")
    if not pin_ok:
        repo.write(BRIDGE, "print('bridge v2, not re-pinned')\n")
    reviewed = repo.commit("feature")
    repo.append(LEDGER, _verdict(reviewed[:verdict_sha_length]))
    head = repo.commit("docs(reviews): verdict")
    repo.git("checkout", "-q", "main")
    return Scenario(repo=repo, base=base, reviewed=reviewed, head=head)


def _on_candidate(scenario: Scenario, edit) -> str:
    """Add one commit on ``cand`` (edit(repo) writes it) and return to ``main``."""
    scenario.repo.git("checkout", "-q", "cand")
    edit(scenario.repo)
    sha = scenario.repo.commit("on top")
    scenario.repo.git("checkout", "-q", "main")
    return sha


def _verdict_result(scenario: Scenario, *, candidate: str = "cand", scope=(), ledger_glob=None):
    git = Git(scenario.repo.path)
    return deploy_check.check_verdict(
        git,
        candidate=candidate,
        head=deploy_check.resolve_candidate(git, candidate),
        ledger_glob=ledger_glob or deploy_check.DEFAULT_LEDGER_GLOB,
        scope=scope,
    )


@pytest.fixture
def scenario(tmp_path) -> Scenario:
    return _scenario(tmp_path)


@pytest.fixture
def database(tmp_path) -> Path:
    return migrate_database(tmp_path / "db" / "autotrader.db")


def _request(scenario: Scenario, database: Path, **changes) -> DeployCheckRequest:
    request = DeployCheckRequest(
        candidate="cand", repo=scenario.repo.path, db_path=database, now=INSIDE_WINDOW
    )
    return replace(request, **changes)


def _run(request: DeployCheckRequest, sntp=lambda: GOOD_SNTP) -> tuple[int, str]:
    out = io.StringIO()
    code = deploy_check.run_deploy_check(request, sntp=sntp, out=out)
    return code, out.getvalue()


def _statuses(output: str) -> dict[str, str]:
    found = {}
    for line in output.splitlines():
        match = re.match(r"^(OK|WARN|REFUSE) ([a-z_]+)", line)
        if match:
            found[match.group(2)] = match.group(1)
    return found


# --- the whole run ----------------------------------------------------------------


def test_a_clean_candidate_passes_every_check_and_exits_0(scenario, database):
    code, output = _run(_request(scenario, database))

    assert code == deploy_check.EXIT_OK
    lines = output.splitlines()
    assert lines[-1] == "deploy check: 9 ok, 0 warn, 0 refused"
    check_lines = [line for line in lines[1:-1] if not line.startswith("  ")]
    assert [line.split()[1].rstrip(":") for line in check_lines] == list(TOKENS)
    assert all(LINE_GRAMMAR.match(line) for line in check_lines)
    assert all(line == f"OK {token}" for line, token in zip(check_lines, TOKENS))


def _break_verdict(scenario, database, request):
    _on_candidate(scenario, lambda repo: repo.write("backend/trading/late.py", "LATE = 1\n"))
    return request


def _break_tree(scenario, database, request):
    scenario.repo.write("backend/stray.py", "x = 1\n")
    return request


def _break_parent(scenario, database, request):
    scenario.repo.write("backend/hotfix.py", "HOTFIX = 1\n")
    scenario.repo.commit("live-only hotfix")
    return request


def _break_schema(scenario, database, request):
    pending = database_at(database.parent / "pending.db", MIGRATIONS[-1].version - 1)
    return replace(request, db_path=pending)


def _break_fence(scenario, database, request):
    with sqlite3.connect(database) as db:
        db.execute(
            "INSERT INTO autotrader_execution_fences (name, stopped, reason, updated_at)"
            " VALUES ('paper', 1, 'test stop', '2026-10-15T00:00:00+00:00')"
        )
    return request


def _break_window(scenario, database, request):
    return replace(request, now=datetime(2026, 10, 19, 12, 0, tzinfo=timezone(timedelta(hours=-4))))


def _break_pin(scenario, database, request):
    # The reviewed commit itself carries an unpinned bridge: only the pin refuses.
    broken = _scenario(scenario.repo.path.parent / "unpinned", pin_ok=False)
    return replace(request, repo=broken.repo.path)


BREAKERS = {
    VERDICT: _break_verdict,
    TREE_CLEAN: _break_tree,
    PARENT: _break_parent,
    SCHEMA: _break_schema,
    FENCE: _break_fence,
    WINDOW: _break_window,
    PIN: _break_pin,
}


@pytest.mark.parametrize("token", sorted(BREAKERS))
def test_each_refusal_alone_exits_78(scenario, database, token):
    request = BREAKERS[token](scenario, database, _request(scenario, database))

    code, output = _run(request)

    assert code == deploy_check.EXIT_REFUSED
    refused = {name for name, status in _statuses(output).items() if status == REFUSE}
    assert refused == {token}
    assert output.splitlines()[-1] == "deploy check: 8 ok, 0 warn, 1 refused"


def test_a_bad_clock_alone_exits_78(scenario, database):
    code, output = _run(_request(scenario, database), sntp=lambda: "-3.250000 +/- 0.01 time.apple.com\n")

    assert code == deploy_check.EXIT_REFUSED
    assert "REFUSE clock: clock offset -3.250000 s exceeds +/-2 s (time.apple.com)" in output.splitlines()


def test_warnings_alone_still_exit_0(scenario, database):
    _on_candidate(scenario, lambda repo: repo.write("docs/reviews/2026-10-16-ledger.md", "# next\n"))
    scenario.repo.git("checkout", "-q", "cand")
    scenario.repo.write("tools/x.ts", "export {}\n")
    scenario.repo.commit("tools")
    scenario.repo.append(LEDGER, _verdict(scenario.repo.git("rev-parse", "--short=10", "HEAD")))
    scenario.repo.commit("docs(reviews): verdict 2")
    scenario.repo.git("checkout", "-q", "main")

    code, output = _run(_request(scenario, database, skip_clock=True))

    assert code == deploy_check.EXIT_OK
    assert _statuses(output)[FILES] == WARN
    assert _statuses(output)[CLOCK] == WARN
    assert "WARN clock: skipped (--skip-clock): offset not measured" in output
    assert output.splitlines()[-1] == "deploy check: 7 ok, 2 warn, 0 refused"


def test_an_unknown_candidate_refuses_every_git_check(scenario, database):
    code, output = _run(_request(scenario, database, candidate="no-such-branch"))

    assert code == deploy_check.EXIT_REFUSED
    statuses = _statuses(output)
    for token in (VERDICT, PARENT, FILES, PIN):
        assert statuses[token] == REFUSE
    assert f"REFUSE pin: candidate 'no-such-branch' is not a commit in {scenario.repo.path}" in output


def test_a_directory_inside_another_repository_is_not_that_repository(tmp_path, database):
    # --repo names the repository root: git must not walk up into an enclosing one.
    outer = _scenario(tmp_path).repo
    plain = outer.path / "plain"
    plain.mkdir()
    request = DeployCheckRequest(candidate="main", repo=plain, db_path=database, now=INSIDE_WINDOW)

    code, output = _run(request)

    assert code == deploy_check.EXIT_REFUSED
    statuses = _statuses(output)
    for token in (VERDICT, TREE_CLEAN, PARENT, FILES, PIN):
        assert statuses[token] == REFUSE
    assert "REFUSE tree_clean: git status failed: fatal: not a git repository" in output
    assert output.splitlines()[0].startswith("deploy check: candidate main = ? in ")


# --- read-only ----------------------------------------------------------------------


def _git_dir_snapshot(repo: Path) -> dict[str, tuple[int, int]]:
    return {
        str(path.relative_to(repo)): (path.stat().st_mtime_ns, path.stat().st_size)
        for path in (repo / ".git").rglob("*")
        if path.is_file()
    }


def test_a_run_leaves_the_repository_status_head_and_index_unchanged(scenario, database):
    repo = scenario.repo
    # A stat-dirty tracked file: plain `git status` would rewrite the index here.
    stale = repo.path / "README.md"
    os.utime(stale, (stale.stat().st_atime + 5, stale.stat().st_mtime + 5))
    repo.write(".claude/settings.local.json", '{"local": true}\n')
    before = (
        repo.git("rev-parse", "HEAD"),
        repo.git("--no-optional-locks", "status", "--porcelain"),
        _git_dir_snapshot(repo.path),
        (repo.path / ".git" / "index").read_bytes(),
    )

    code, output = _run(_request(scenario, database))

    after = (
        repo.git("rev-parse", "HEAD"),
        repo.git("--no-optional-locks", "status", "--porcelain"),
        _git_dir_snapshot(repo.path),
        (repo.path / ".git" / "index").read_bytes(),
    )
    assert code == deploy_check.EXIT_OK, output
    assert after == before


def test_every_git_call_passes_no_optional_locks_and_only_reads(scenario, database, monkeypatch):
    seen: list[list[str]] = []
    real_run = subprocess.run

    def spy(argv, *args, **kwargs):
        seen.append(list(argv))
        return real_run(argv, *args, **kwargs)

    monkeypatch.setattr(deploy_check.subprocess, "run", spy)

    code, _output = _run(_request(scenario, database, skip_clock=True))

    assert code == deploy_check.EXIT_OK
    assert seen and all(argv[0] == "git" for argv in seen)  # no sntp, no launchctl
    assert all(argv[1] == "--no-optional-locks" for argv in seen)
    subcommands = {argv[argv.index("core.fsmonitor=false") + 1] for argv in seen}
    assert subcommands <= {"rev-parse", "ls-tree", "show", "merge-base", "diff", "log", "status"}


def test_the_database_is_opened_read_only(scenario, database):
    os.chmod(database, 0o444)
    try:
        before = database.read_bytes()
        code, output = _run(_request(scenario, database))
        assert database.read_bytes() == before
    finally:
        os.chmod(database, 0o644)
    assert code == deploy_check.EXIT_OK, output


# --- verdict (I-11) -------------------------------------------------------------------


def test_a_verdict_plus_a_docs_reviews_only_commit_passes_and_prints_both(scenario):
    result = _verdict_result(scenario)

    assert result.status == OK
    assert result.notes[0] == f"{LEDGER}:2: {_verdict(scenario.reviewed[:7])}"
    assert result.notes[1] == (
        f"head {scenario.head[:12]} = verdict {scenario.reviewed[:12]} + 1 commit(s) touching only docs/reviews/:"
    )
    assert result.notes[2].startswith(f"+ {scenario.head[:7]}") and result.notes[2].endswith(
        "docs(reviews): verdict"
    )


def test_a_verdict_naming_the_head_itself_passes(scenario):
    # A commit cannot name itself in its own tree, so the exact match is served
    # by a ledger overlay; the check must then need no exception commits.
    class Overlay(Git):
        def blob(self, commit, path):
            if path == LEDGER:
                return f"# Ledger\n{_verdict(scenario.head)}\n".encode()
            return super().blob(commit, path)

    git = Overlay(scenario.repo.path)
    result = deploy_check.check_verdict(
        git, candidate="cand", head=scenario.head, ledger_glob=deploy_check.DEFAULT_LEDGER_GLOB, scope=()
    )

    assert result.status == OK
    assert result.notes == (f"{LEDGER}:2: {_verdict(scenario.head)}",)


def test_an_unreviewed_backend_commit_on_top_of_the_verdict_refuses(scenario):
    late = _on_candidate(scenario, lambda repo: repo.write("backend/trading/late.py", "LATE = 1\n"))

    result = _verdict_result(scenario)

    assert result.status == REFUSE
    assert f"names cand {late[:12]}" in result.detail
    assert any("change 1 path(s) outside docs/reviews/: backend/trading/late.py" in note for note in result.notes)


def test_an_unreviewed_backend_commit_under_the_ledger_commit_refuses(tmp_path):
    # verdict(R) .. backend commit .. the commit adding the line: still not reviewed.
    scenario = _scenario(tmp_path)
    scenario.repo.git("checkout", "-q", "cand")
    scenario.repo.write("backend/trading/sneak.py", "SNEAK = 1\n")
    scenario.repo.commit("sneak")
    scenario.repo.append(LEDGER, "2026-10-15T21:00:00+03:00 DECISION owner: nothing")
    scenario.repo.commit("docs(reviews): note")
    scenario.repo.git("checkout", "-q", "main")

    result = _verdict_result(scenario)

    assert result.status == REFUSE
    assert any("backend/trading/sneak.py" in note for note in result.notes)


def test_a_docs_change_outside_reviews_after_the_verdict_refuses(scenario):
    _on_candidate(scenario, lambda repo: repo.write("docs/OPERATOR_PROMPT.md", "changed\n"))

    result = _verdict_result(scenario)

    assert result.status == REFUSE
    assert any("docs/OPERATOR_PROMPT.md" in note for note in result.notes)


def test_a_backend_file_moved_into_reviews_still_refuses(scenario):
    def move(repo: Repo) -> None:
        repo.git("mv", "backend/trading/feature.py", "docs/reviews/feature.py")

    _on_candidate(scenario, move)

    result = _verdict_result(scenario)

    assert result.status == REFUSE
    assert any("backend/trading/feature.py" in note for note in result.notes)


def test_two_dated_ledgers_are_both_searched(tmp_path):
    scenario = _scenario(tmp_path)
    older = "docs/reviews/2026-10-14-ledger.md"
    scenario.repo.git("checkout", "-q", "cand")
    scenario.repo.write(older, "# Ledger 10-14\n")
    scenario.repo.commit("docs(reviews): older ledger")
    scenario.repo.git("checkout", "-q", "-b", "second", "cand")
    scenario.repo.write("backend/trading/second.py", "SECOND = 1\n")
    second = scenario.repo.commit("second feature")
    scenario.repo.append(older, _verdict(second[:9]))
    scenario.repo.commit("docs(reviews): verdict in the older ledger")
    scenario.repo.git("checkout", "-q", "main")

    first_result = _verdict_result(scenario)  # its verdict is in the 10-15 ledger
    second_result = _verdict_result(scenario, candidate="second")  # in the 10-14 ledger

    assert first_result.status == OK
    assert first_result.notes[0].startswith(f"{LEDGER}:2:")
    assert second_result.status == OK
    assert second_result.notes[0].startswith(f"{older}:2:")


def test_a_refusal_names_every_ledger_searched(tmp_path):
    scenario = _scenario(tmp_path)
    _on_candidate(scenario, lambda repo: repo.write("docs/reviews/2026-10-14-ledger.md", "# old\n"))
    _on_candidate(scenario, lambda repo: repo.write("backend/x.py", "X = 1\n"))

    result = _verdict_result(scenario)

    assert result.status == REFUSE
    assert "2 ledger(s): docs/reviews/2026-10-14-ledger.md, docs/reviews/2026-10-15-ledger.md" in result.detail


def test_a_verdict_in_a_review_report_is_not_a_ledger_line(tmp_path):
    scenario = _scenario(tmp_path)
    scenario.repo.git("checkout", "-q", "cand")
    scenario.repo.write("backend/trading/other.py", "OTHER = 1\n")
    other = scenario.repo.commit("other")
    scenario.repo.append("docs/reviews/2026-10-15-v9-review.md", _verdict(other[:7]))
    scenario.repo.commit("docs(reviews): report only")
    scenario.repo.git("checkout", "-q", "main")

    assert _verdict_result(scenario).status == REFUSE
    explicit = _verdict_result(scenario, ledger_glob="docs/reviews/*-review.md")
    assert explicit.status == OK


def test_a_nested_ledger_does_not_match_the_default_glob(tmp_path):
    scenario = _scenario(tmp_path)
    scenario.repo.git("checkout", "-q", "cand")
    scenario.repo.write("backend/trading/other.py", "OTHER = 1\n")
    other = scenario.repo.commit("other")
    scenario.repo.write("docs/reviews/archive/old-ledger.md", _verdict(other[:7]) + "\n")
    scenario.repo.commit("docs(reviews): nested")
    scenario.repo.git("checkout", "-q", "main")

    assert _verdict_result(scenario).status == REFUSE


def test_full_and_short_verdict_shas_are_accepted(tmp_path):
    full = _scenario(tmp_path / "full", verdict_sha_length=40)
    short = _scenario(tmp_path / "short", verdict_sha_length=7)

    assert _verdict_result(full).status == OK
    assert _verdict_result(short).status == OK


def test_scope_must_list_every_requested_slice(scenario):
    assert _verdict_result(scenario, scope=("A1",)).status == OK
    assert _verdict_result(scenario, scope=("A2", "A1")).status == OK
    refused = _verdict_result(scenario, scope=("A1", "A9"))
    assert refused.status == REFUSE
    assert "no `<sha> verdict=GO scope=A1,A9` line" in refused.detail


def test_a_verdict_without_scope_does_not_satisfy_a_scope(tmp_path):
    scenario = _scenario(tmp_path)
    scenario.repo.git("checkout", "-q", "cand")
    scenario.repo.write(LEDGER, f"# Ledger\n2026-10-15 | {scenario.reviewed[:7]} verdict=GO | GO\n")
    scenario.repo.commit("docs(reviews): unscoped")
    scenario.repo.git("checkout", "-q", "main")

    assert _verdict_result(scenario).status == OK
    assert _verdict_result(scenario, scope=("A1",)).status == REFUSE


@pytest.mark.parametrize(
    "line",
    [
        "R-A blocked until a line `{sha} verdict=GO scope=A1,A2` is recorded here",
        "{sha} verdict=NO-GO scope=A1,A2",
        "{sha} verdict=GO-PENDING scope=A1,A2",
        "{sha} verdict=go scope=A1,A2",
    ],
)
def test_lines_that_are_not_a_go_verdict_do_not_count(tmp_path, line):
    scenario = _scenario(tmp_path)
    scenario.repo.git("checkout", "-q", "cand")
    scenario.repo.write(LEDGER, "# Ledger\n" + line.format(sha=scenario.reviewed[:7]) + "\n")
    scenario.repo.commit("docs(reviews): not a verdict")
    scenario.repo.git("checkout", "-q", "main")

    assert _verdict_result(scenario).status == REFUSE


@pytest.mark.parametrize(
    "line",
    [
        # Review P1-A F-9: prose that mentions a verdict matched without --scope.
        "not yet: {sha} verdict=GO expected after fixes",
        "(see the review: {sha} verdict=GO)",
        "2026-10-16T01:00:00+03:00 | P1-A review | not yet: {sha} verdict=GO expected after fixes | pending",
        "2026-10-16T01:00:00+03:00 | not yet: {sha} verdict=GO | pending",
        "2026-10-16T01:00:00+03:00 | expected {sha} verdict=GO | pending",
        "2026-10-16T01:00:00+03:00 | waiting for `{sha} verdict=GO` | pending",
        "2026-10-16T01:00:00+03:00 | `{sha} verdict=GO` | quoted",
        "| {sha} verdict=GO | a table row, not a ledger line",
        "Ledger note | {sha} verdict=GO | the first field is not a time",
        "2026-10-16T01:00:00+03:00 DECISION owner: deploy once {sha} verdict=GO is recorded",
    ],
)
def test_a_verdict_counts_only_as_a_ledger_lines_check_field(tmp_path, line):
    scenario = _scenario(tmp_path)
    scenario.repo.git("checkout", "-q", "cand")
    scenario.repo.write(LEDGER, "# Ledger\n" + line.format(sha=scenario.reviewed[:7]) + "\n")
    scenario.repo.commit("docs(reviews): prose only")
    scenario.repo.git("checkout", "-q", "main")

    result = _verdict_result(scenario)  # no --scope: the case F-9 found open

    assert result.status == REFUSE
    assert "ledger grammar `<time> | [<label>] <sha> verdict=GO ... | ...`" in result.detail


@pytest.mark.parametrize(
    "line",
    [
        # The shapes of the real docs/reviews/2026-10-15-ledger.md.
        "05:15 | {sha} verdict=GO scope=A6,A7,A8,A10 | GO; no HIGH | docs/reviews/r.md",
        "05:43 | v7a {sha} verdict=GO bridge_sha=a63982cc01c3 | GO on the order path | docs/reviews/r.md",
        "14:42 | v7a.1 {sha} verdict=GO bridge_sha=9790eb3c1e81 | GO | docs/reviews/r.md",
        "2026-10-15T20:34:28+03:00 | v7a {sha} verdict=GO addendum: docs/tests only | (copied) | r.md",
        "2026-10-16T00:53:35+03:00 | P1-A {sha} verdict=GO scope=T-1,T-2,T-5,T-7 bridge_sha=7d313fd0 | N10 | r.md",
    ],
)
def test_the_real_ledger_verdict_shapes_still_match(tmp_path, line):
    scenario = _scenario(tmp_path)
    scenario.repo.git("checkout", "-q", "cand")
    scenario.repo.write(LEDGER, "# Ledger\n" + line.format(sha=scenario.reviewed[:7]) + "\n")
    scenario.repo.commit("docs(reviews): verdict")
    scenario.repo.git("checkout", "-q", "main")

    result = _verdict_result(scenario)

    assert result.status == OK
    assert result.notes[0] == f"{LEDGER}:2: {line.format(sha=scenario.reviewed[:7])}"


def test_a_scope_is_read_from_the_verdict_field_only(tmp_path):
    scenario = _scenario(tmp_path)
    scenario.repo.git("checkout", "-q", "cand")
    scenario.repo.write(
        LEDGER, f"# Ledger\n05:15 | v9 {scenario.reviewed[:7]} verdict=GO | GO; next: scope=A1,A2 | r.md\n"
    )
    scenario.repo.commit("docs(reviews): unscoped verdict, a scope in the result field")
    scenario.repo.git("checkout", "-q", "main")

    assert _verdict_result(scenario).status == OK
    assert _verdict_result(scenario, scope=("A1",)).status == REFUSE


def test_a_verdict_for_a_commit_off_the_candidate_line_refuses(tmp_path):
    scenario = _scenario(tmp_path)
    scenario.repo.git("checkout", "-q", "-b", "side", "main")
    scenario.repo.write("backend/trading/side.py", "SIDE = 1\n")
    side = scenario.repo.commit("side")
    scenario.repo.git("checkout", "-q", "cand")
    scenario.repo.write(LEDGER, f"# Ledger\n{_verdict(side[:7])}\n")
    scenario.repo.commit("docs(reviews): verdict for a side commit")
    scenario.repo.git("checkout", "-q", "main")

    result = _verdict_result(scenario)

    assert result.status == REFUSE
    assert any("is not an ancestor of" in note for note in result.notes)


def test_no_ledger_on_the_candidate_refuses(scenario):
    result = _verdict_result(scenario, ledger_glob="docs/reviews/*nothing*.md")

    assert result.status == REFUSE
    assert result.detail == "no ledger matches docs/reviews/*nothing*.md on cand"


# --- tree_clean / parent / files ------------------------------------------------------


def test_a_clean_tree_passes(scenario):
    result = deploy_check.check_tree_clean(Git(scenario.repo.path), benign_paths=deploy_check.DEFAULT_BENIGN_PATHS)

    assert result == deploy_check.CheckResult(TREE_CLEAN, OK)


def test_the_benign_settings_file_passes_and_is_shown(scenario):
    scenario.repo.write(".claude/settings.local.json", '{"local": true}\n')

    result = deploy_check.check_tree_clean(Git(scenario.repo.path), benign_paths=deploy_check.DEFAULT_BENIGN_PATHS)

    assert result.status == OK
    assert result.notes == ("benign:  M .claude/settings.local.json",)


@pytest.mark.parametrize(
    "dirty, entry",
    [
        (lambda repo: repo.write("backend/new.py", "x\n"), "?? backend/new.py"),
        (lambda repo: repo.write("README.md", "edited\n"), " M README.md"),
        (lambda repo: (repo.path / "README.md").unlink(), " D README.md"),
    ],
)
def test_a_dirty_tree_refuses(scenario, dirty, entry):
    dirty(scenario.repo)

    result = deploy_check.check_tree_clean(Git(scenario.repo.path), benign_paths=deploy_check.DEFAULT_BENIGN_PATHS)

    assert result.status == REFUSE
    assert entry in result.notes


def test_a_staged_rename_lists_both_paths(scenario):
    scenario.repo.git("mv", "README.md", "README2.md")

    result = deploy_check.check_tree_clean(Git(scenario.repo.path), benign_paths=("README.md",))

    assert result.status == REFUSE
    assert "R  README2.md" in result.notes and "benign: R  README.md" in result.notes


def test_the_benign_list_is_configurable(scenario):
    scenario.repo.write(".claude/settings.local.json", '{"local": true}\n')

    result = deploy_check.check_tree_clean(Git(scenario.repo.path), benign_paths=())

    assert result.status == REFUSE
    assert result.notes == (" M .claude/settings.local.json",)


def _parent(scenario: Scenario, expect_parent: str | None = None):
    git = Git(scenario.repo.path)
    return deploy_check.check_parent(
        git, candidate="cand", head=deploy_check.resolve_candidate(git, "cand"), expect_parent=expect_parent
    )


def test_parent_passes_on_a_fast_forward(scenario):
    result = _parent(scenario)

    assert result.status == OK
    assert result.notes == (f"HEAD {scenario.base[:12]} -> cand {scenario.head[:12]}: 2 commit(s) ahead",)


def test_parent_refuses_a_diverged_head(scenario):
    scenario.repo.write("backend/hotfix.py", "H = 1\n")
    hotfix = scenario.repo.commit("hotfix")

    result = _parent(scenario)

    assert result.status == REFUSE
    assert result.detail == f"HEAD {hotfix[:12]} is not an ancestor of cand {scenario.head[:12]}: no fast-forward"


def test_parent_refuses_the_candidate_checked_against_itself(scenario):
    """Review P1-A F-8: run from the candidate's worktree with --repo at itself,
    tree_clean, parent and files passed vacuously (0 commits ahead)."""
    scenario.repo.git("checkout", "-q", "cand")

    result = _parent(scenario)

    assert result.status == REFUSE
    assert result.detail == (
        f"HEAD of {scenario.repo.path} is the candidate cand {scenario.head[:12]} itself: --repo must be"
        " the live checkout the candidate is deployed into (nothing to fast-forward)"
    )


def test_expect_parent_accepts_short_and_full_shas(scenario):
    assert _parent(scenario, scenario.base[:7]).status == OK
    assert _parent(scenario, scenario.base).status == OK


def test_expect_parent_refuses_another_head(scenario):
    result = _parent(scenario, scenario.reviewed[:7])

    assert result.status == REFUSE
    assert result.detail.startswith(f"HEAD is {scenario.base[:12]}, expected parent {scenario.reviewed[:7]}")
    assert _parent(scenario, "deadbee").status == REFUSE


def test_files_warn_outside_the_deployable_paths(scenario):
    _on_candidate(scenario, lambda repo: (repo.write("tools/a.ts", "x\n"), repo.write("Makefile", "x\n"),
                                          repo.write("dashboard/src/a.ts", "x\n")))
    git = Git(scenario.repo.path)

    result = deploy_check.check_files(git, candidate="cand", head=deploy_check.resolve_candidate(git, "cand"))

    assert result.status == WARN
    assert result.detail.startswith("2 path(s) outside backend/, dashboard/, docs/, plans/, scripts/, AGENTS.md")
    assert result.notes[1:] == ("Makefile", "tools/a.ts")  # dashboard/ is deployable (904af38 ships dashboard code)


def test_files_pass_inside_the_deployable_paths(scenario):
    git = Git(scenario.repo.path)

    result = deploy_check.check_files(git, candidate="cand", head=scenario.head)

    assert result.status == OK
    assert result.notes == ("2 path(s) HEAD..cand: backend/ 1, docs/ 1",)


# --- schema / fence ---------------------------------------------------------------------


def test_schema_current_passes(database):
    result = deploy_check.check_schema(database, now=INSIDE_WINDOW)

    assert result.status == OK
    assert result.notes == (f"{database}: schema v{MIGRATIONS[-1].version} is current",)


def test_schema_pending_refuses_and_never_migrates(tmp_path):
    previous = MIGRATIONS[-1].version - 1
    pending = database_at(tmp_path / "pending.db", previous)

    result = deploy_check.check_schema(pending, now=INSIDE_WINDOW)

    assert result.status == REFUSE
    assert f"schema v{previous}" in result.detail
    with sqlite3.connect(pending) as db:
        assert db.execute("SELECT max(version) FROM autotrader_schema_migrations").fetchone()[0] == previous


def test_schema_and_fence_refuse_a_missing_database_without_creating_it(tmp_path):
    missing = tmp_path / "absent" / "autotrader.db"

    assert deploy_check.check_schema(missing, now=INSIDE_WINDOW).status == REFUSE
    assert deploy_check.check_fence(missing).status == REFUSE
    assert not missing.exists() and not missing.parent.exists()


def test_fence_inactive_passes_and_lists_identity_fences(database):
    with sqlite3.connect(database) as db:
        db.execute(
            "INSERT INTO autotrader_execution_fences (name, stopped, reason, updated_at)"
            " VALUES ('paper:ORB_V2', 1, 'owner halt', '2026-10-15T00:00:00+00:00')"
        )

    result = deploy_check.check_fence(database)

    assert result.status == OK
    assert result.notes == ("PAPER kill switch: INACTIVE", "identity ORB_V2 STOPPED (owner halt)")


def test_fence_active_refuses(database):
    _break_fence(None, database, None)

    result = deploy_check.check_fence(database)

    assert result.status == REFUSE
    assert result.detail.startswith(
        "the global PAPER kill switch is ACTIVE since 2026-10-15T00:00:00+00:00 (test stop)"
    )


def test_fence_unreadable_refuses(tmp_path):
    garbage = tmp_path / "garbage.db"
    garbage.write_bytes(b"not a database" * 100)

    result = deploy_check.check_fence(garbage)

    assert result.status == REFUSE
    assert result.detail.startswith(f"fences unreadable at {garbage}")


# --- clock -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "output, offset",
    [
        ("+0.012345 +/- 0.023456 time.apple.com 17.253.4.253\n", "+0.012345"),
        ("-1.999 +/- 0.1 time.apple.com 17.253.4.253\n", "-1.999"),
        (
            "2026-10-15 20:00:00.123456 (+0300) +0.001234 +/- 0.012345 time.apple.com 17.253.4.253 s1 no-leap\n",
            "+0.001234",
        ),
        ("sntp: no reply\n", None),
    ],
)
def test_sntp_output_is_parsed(output, offset):
    parsed = deploy_check.parse_sntp_offset(output)

    assert (None if parsed is None else f"{parsed:+}") == offset


@pytest.mark.parametrize(
    "output, status",
    [
        ("+2.000000 +/- 0.01 time.apple.com\n", OK),
        ("-2.000000 +/- 0.01 time.apple.com\n", OK),
        ("+2.000001 +/- 0.01 time.apple.com\n", REFUSE),
        ("garbage\n", REFUSE),
    ],
)
def test_the_clock_offset_limit_is_two_seconds(output, status):
    assert deploy_check.check_clock(skip=False, sntp=lambda: output).status == status


def test_an_unavailable_clock_refuses_and_skip_warns_without_probing():
    def unavailable() -> str:
        raise ClockUnavailable("sntp time.apple.com exited 1")

    refused = deploy_check.check_clock(skip=False, sntp=unavailable)
    skipped = deploy_check.check_clock(skip=True, sntp=lambda: pytest.fail("probed despite --skip-clock"))

    assert refused.status == REFUSE
    assert refused.detail == "sntp time.apple.com exited 1; pass --skip-clock to deploy without a reading"
    assert skipped == deploy_check.CheckResult(CLOCK, WARN, "skipped (--skip-clock): offset not measured")


# --- window (I-4) -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "instant, inside",
    [
        # EDT (UTC-4): 18:30 opens, 07:20 closes.
        ("2026-10-15T22:29:59+00:00", False),  # 18:29:59 EDT
        ("2026-10-15T22:30:00+00:00", True),  # 18:30:00 EDT
        ("2026-10-16T11:19:59+00:00", True),  # 07:19:59 EDT
        ("2026-10-16T11:20:00+00:00", False),  # 07:20:00 EDT
        ("2026-10-19T02:00:00+03:00", True),  # the acceptance instant, Israel time
        # 2026-11-01: EDT ends at 02:00 (clocks back to 01:00 EST, UTC-5).
        ("2026-10-31T11:30:00+00:00", False),  # 07:30 EDT
        ("2026-11-01T11:30:00+00:00", True),  # 06:30 EST: same UTC time, now inside
        ("2026-11-01T05:30:00+00:00", True),  # 01:30 EDT, the first 01:30
        ("2026-11-01T06:30:00+00:00", True),  # 01:30 EST, the repeated 01:30
        ("2026-11-01T12:19:59+00:00", True),  # 07:19:59 EST
        ("2026-11-01T12:20:00+00:00", False),  # 07:20:00 EST
        ("2026-11-01T23:29:59+00:00", False),  # 18:29:59 EST
        ("2026-11-01T23:30:00+00:00", True),  # 18:30:00 EST
        ("2026-11-01T22:30:00+00:00", False),  # 17:30 EST (18:30 under EDT)
    ],
)
def test_the_deploy_window_follows_new_york_time(instant, inside):
    now = datetime.fromisoformat(instant)

    assert deploy_check.in_deploy_window(now) is inside
    assert deploy_check.check_window(now).status == (OK if inside else REFUSE)


def test_the_window_refusal_names_the_et_time_and_the_opening():
    result = deploy_check.check_window(datetime.fromisoformat("2026-11-02T15:00:00+00:00"))

    assert result.detail == (
        "now 2026-11-02 10:00 EST is outside the deploy window 18:30 -> 07:20 ET (I-4); it opens at 18:30 ET"
    )


def test_a_naive_now_is_refused():
    with pytest.raises(ValueError):
        deploy_check.parse_now("2026-10-19T02:00:00")
    with pytest.raises(ValueError):
        deploy_check.in_deploy_window(datetime(2026, 10, 19, 2, 0))


# --- pin (I-5) ------------------------------------------------------------------------------


def _pin(repo: Repo, candidate: str = "cand"):
    git = Git(repo.path)
    return deploy_check.check_pin(git, candidate=candidate, head=deploy_check.resolve_candidate(git, candidate))


def test_the_pin_matches(scenario):
    result = _pin(scenario.repo)

    assert result.status == OK
    digest = hashlib.sha256(b"print('bridge v1')\n").hexdigest()
    assert result.notes == (f"bridge sha256 {digest[:12]} == REVIEWED_BRIDGE_SHA256",)


def test_a_pin_mismatch_refuses(tmp_path):
    broken = _scenario(tmp_path, pin_ok=False)

    result = _pin(broken.repo)

    assert result.status == REFUSE
    actual = hashlib.sha256(b"print('bridge v2, not re-pinned')\n").hexdigest()
    assert result.detail.startswith(f"cand:{BRIDGE} sha256 {actual[:12]} is not its REVIEWED_BRIDGE_SHA256")


@pytest.mark.parametrize(
    "supervisor",
    ['"""no pin here"""\n', _supervisor_text("a" * 64) + _supervisor_text("b" * 64)],
    ids=["missing", "two"],
)
def test_a_missing_or_ambiguous_pin_literal_refuses(scenario, supervisor):
    _on_candidate(scenario, lambda repo: repo.write(SUPERVISOR, supervisor))

    result = _pin(scenario.repo)

    assert result.status == REFUSE
    assert "REVIEWED_BRIDGE_SHA256 literal" in result.detail


def test_a_missing_bridge_refuses(scenario):
    _on_candidate(scenario, lambda repo: (repo.path / BRIDGE).unlink())

    assert _pin(scenario.repo).detail == f"cand has no {BRIDGE}"


# --- the command line -------------------------------------------------------------------------


def test_the_cli_runs_the_check(scenario, database, capsys):
    code = main(
        [
            "deploy",
            "check",
            "--candidate",
            "cand",
            "--repo",
            str(scenario.repo.path),
            "--db-path",
            str(database),
            "--scope",
            "A1",
            "--expect-parent",
            scenario.base[:7],
            "--now",
            "2026-10-19T02:00:00+03:00",
            "--skip-clock",
        ]
    )

    output = capsys.readouterr().out.splitlines()
    assert code == 0
    assert output[0] == (
        f"deploy check: candidate cand = {scenario.head[:12]} in {scenario.repo.path}"
        " at 2026-10-18 19:00 EDT scope=A1"
    )
    assert output[-1] == "deploy check: 8 ok, 1 warn, 0 refused"


@pytest.mark.parametrize(
    "extra",
    [
        ["--now", "2026-10-19T02:00:00"],
        ["--now", "tomorrow"],
        ["--expect-parent=--output=x"],
    ],
)
def test_the_cli_refuses_bad_arguments(tmp_path, capsys, extra):
    with pytest.raises(SystemExit) as error:
        main(["deploy", "check", "--candidate", "cand", "--repo", str(tmp_path), "--skip-clock", *extra])

    assert error.value.code == 2
    assert capsys.readouterr().out == ""


def test_the_cli_requires_the_repo(capsys):
    # Review P1-A F-8: the default was the running code's own checkout, which run
    # from the candidate's worktree is the candidate itself.
    with pytest.raises(SystemExit) as error:
        main(["deploy", "check", "--candidate", "cand", "--skip-clock"])

    assert error.value.code == 2
    assert "--repo" in capsys.readouterr().err
    assert not hasattr(deploy_check, "REPO_ROOT")


def test_the_cli_refuses_a_candidate_that_looks_like_an_option(tmp_path, capsys):
    with pytest.raises(SystemExit) as error:
        main(["deploy", "check", "--candidate=--upload-pack=x", "--repo", str(tmp_path), "--skip-clock"])

    assert error.value.code == 2
    assert "--candidate must be a git ref or sha" in capsys.readouterr().err

"""``autotraderctl deploy apply`` / ``deploy undo`` (P2-1..P2-4).

Every scenario is a real git repository in ``tmp_path``: the live checkout on
``main`` (PREV), the candidate on ``cand`` (a reviewed commit plus the
docs/reviews commit carrying its verdict line), and a detached worktree of the
candidate, the checkout the code "runs" from. Everything outside git is a
fake: the live checkout's own commands (``FakeLive``; its ``db migrate`` and
``db status`` are db_admin's real functions on a temporary database), launchctl
(``FakeLaunchctl`` of test_launchd), the process table, ``supervisor.lock`` and
``supervisor.log`` (``FakeRobot``, writing the supervisor's real line format),
the clock (``FakeClock``) and the terminal (``FakeTTY``). No test reaches
launchctl, ps, sntp, a network port or a real ~/Library path.
"""
from __future__ import annotations

import ast
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta, timezone
import fcntl
import hashlib
import heapq
import io
import itertools
import json
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys

import pytest

from tests.schema_support import database_at, migrate_database
from tests.test_deploy_check import GOOD_SNTP, LEDGER, Repo, _git
from tests.test_launchd import FakeLaunchctl
from trading.cli import main
from trading.operations import deploy_apply, deploy_check, launchd, supervisor
from trading.operations.db_admin import run_migrate, run_status
from trading.operations.deploy_apply import (
    DeployApplyRequest,
    DeployEnvironment,
    DeployFailure,
    DeployOperator,
    DeploysLog,
    DeploysLogError,
    DeployUndoRequest,
    DryRunCheck,
    DryRunExpectation,
    GitWriter,
)
from trading.operations.launchd import LaunchdManager
from trading.operations.logging import latest_rotation_boundary
from trading.operations.paths import UserPaths
from trading.scheduler.sessions import MARKET_TIMEZONE
from trading.sendable_identities import SENDABLE_IDENTITIES
from trading.storage.migrations import MIGRATIONS, inspect_schema

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="launchd and flock are macOS/POSIX")

ISRAEL = timezone(timedelta(hours=3))
# Saturday 2026-10-24 19:00 EDT: inside 18:40 -> 06:50 ET; the next session is Monday.
START = datetime(2026, 10, 25, 2, 0, tzinfo=ISRAEL)
NEXT_SESSION = date(2026, 10, 26)
LATEST = MIGRATIONS[-1].version
PREVIOUS = LATEST - 1
BRIDGE = deploy_check.BRIDGE_PATH
SUPERVISOR = deploy_check.SUPERVISOR_PATH
BRIDGE_V1 = "print('bridge v1')\n"
BRIDGE_V2 = "print('bridge v2')\n"
PIN_V1 = hashlib.sha256(BRIDGE_V1.encode()).hexdigest()
PIN_V2 = hashlib.sha256(BRIDGE_V2.encode()).hexdigest()
TOOL_FILE = "backend/trading/operations/deploy_check.py"
PLAN_TOKENS = (
    # Named in the plan (P2-1..P2-4) and kept verbatim.
    "not_interactive", "runs_from_live_checkout", "start_cutoff", "pin_verdict_missing", "pin_expected",
    "prev_sha_invalid", "deploy_lock_busy", "migrate_lock_busy", "deploy_tool_scope_missing",
    "confirm_mismatch", "head_mismatch", "bridge_sha_mismatch", "tree_dirty_after_ff", "dry_run_bang",
    "dry_run_warning", "dry_run_pin", "dry_run_fenced", "dry_run_sendable", "dry_run_registry",
    "dry_run_schema", "dry_run_session", "dry_run_heartbeat", "dry_run_budget", "stop_wait_timeout",
    "post_start_no_start", "post_start_session_wait", "post_start_launch_refused",
    "post_start_rotation_failed", "post_start_preflight_warning", "post_start_heartbeat_missing",
    "post_start_plist", "post_start_head", "post_start_timeout", "migration_unexpected",
    "verdict_migration_missing", "undo_reset_noop", "undo_prev_mismatch", "undo_needs_db_restore",
    "undo_not_flat",
)


# --- guards: nothing real outside git ------------------------------------------------------


@pytest.fixture(autouse=True)
def _nothing_live(monkeypatch):
    real_run = subprocess.run

    def guarded(argv, *args, **kwargs):
        first = str(list(argv)[0]) if not isinstance(argv, str) else argv.split()[0]
        if Path(first).name in {"launchctl", "ps", "sntp", "security", "pgrep"}:
            raise AssertionError(f"a test reached the real {first}")
        return real_run(argv, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", guarded)
    monkeypatch.setattr(deploy_check, "system_sntp", lambda: pytest.fail("a test reached the real sntp"))


# --- fakes ---------------------------------------------------------------------------------------


class FakeClock:
    """A wall clock that moves only when the code under test sleeps; events fire on the way."""

    def __init__(self, start: datetime) -> None:
        self.now = start
        self._events: list[tuple[datetime, int, Callable[[], None]]] = []
        self._order = itertools.count()
        self.slept = 0.0

    def __call__(self) -> datetime:
        return self.now

    def after(self, seconds: float, action: Callable[[], None]) -> None:
        heapq.heappush(self._events, (self.now + timedelta(seconds=seconds), next(self._order), action))

    def sleep(self, seconds: float) -> None:
        target = self.now + timedelta(seconds=seconds)
        while self._events and self._events[0][0] <= target:
            at, _order, action = heapq.heappop(self._events)
            self.now = max(self.now, at)
            action()
        self.now = target
        self.slept += seconds


class FakeTTY(io.StringIO):
    def __init__(self, typed: str = "", *, tty: bool = True) -> None:
        super().__init__(typed)
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


@dataclass
class StartShape:
    """What one supervisor start writes (the real order: preflight lines, START,
    always-on CHILD_STARTs, SESSION_WAIT, then a rotation's marker)."""

    session_date: date = NEXT_SESSION
    preflight: tuple[str, ...] = ()
    heartbeat_parent: int | None = None  # None: the new supervisor
    rotate: bool = False
    extra_rotated: int = 0
    plist_root: Path | None = None  # None: the live checkout
    mode: str = "daily"


class FakeRobot:
    """The supervisor as the deploy observes it: ps rows, supervisor.lock, supervisor.log."""

    def __init__(self, paths: UserPaths, repo: Path, clock: FakeClock) -> None:
        self.paths = paths
        self.repo = repo
        self.clock = clock
        self.log = paths.log_directory / supervisor.SUPERVISOR_LOG_NAME
        self.lock = paths.data_directory / supervisor.LOCK_FILE_NAME
        self.python = f"{repo}/backend/.venv/bin/python"
        self.control = f"{repo}/backend/autotraderctl.py"
        self.base_table: list[tuple[int, int, str]] = [
            (1, 0, "/sbin/launchd"),
            (os.getpid(), 1, f"/wt/backend/.venv/bin/python /wt/backend/autotraderctl.py deploy apply"),
            (77, 1, "/usr/bin/caffeinate -i -s"),
        ]
        self.children: list[tuple[int, int, str]] = []
        self.pids = itertools.count(4100)
        self.lock_fd: int | None = None
        self.table_reads = 0
        self.starts = 0

    def table(self) -> list[tuple[int, int, str]]:
        self.table_reads += 1
        return [*self.base_table, *self.children]

    def write(self, text: str, at: datetime | None = None, path: Path | None = None) -> str:
        line = f"{supervisor.iso_utc(at or self.clock())} {text}"
        target = path or self.log
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as stream:
            stream.write(line + "\n")
        return line

    @property
    def running(self) -> bool:
        return self.lock_fd is not None

    def hold_lock(self) -> None:
        self.lock.parent.mkdir(parents=True, exist_ok=True)
        self.lock_fd = os.open(self.lock, os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(self.lock_fd, fcntl.LOCK_EX)

    def release_lock(self) -> None:
        if self.lock_fd is not None:
            os.close(self.lock_fd)
            self.lock_fd = None

    def start(self, shape: StartShape | None = None, *, at: datetime | None = None) -> int:
        shape = shape or StartShape()
        self.starts += 1
        pid = next(self.pids)
        telegram, heartbeat = next(self.pids), next(self.pids)
        self.children = [
            (pid, 1, f"{self.python} {self.control} run"),
            (telegram, pid, f"{self.python} {self.control} telegram run"),
            (heartbeat, shape.heartbeat_parent or pid, f"{self.python} -m trading.operations.heartbeat"),
        ]
        self.hold_lock()
        moment = at or self.clock()
        for text in shape.preflight:
            self.write(text, moment)
        state = [
            self.write(f"SUPERVISOR_START pid={pid} mode={shape.mode}", moment),
            self.write(f"CHILD_START telegram pid={telegram}", moment),
            self.write(f"CHILD_START heartbeat pid={heartbeat}", moment),
            self.write(
                f"SESSION_WAIT date={shape.session_date.isoformat()}"
                f" until={shape.session_date.isoformat()}T11:55:00+00:00",
                moment,
            ),
        ]
        if shape.rotate:
            # LogRotation: rename, then the carried state lines and the marker in one write.
            day = latest_rotation_boundary(moment).date()
            self.log.rename(self.log.with_name(f"{self.log.name}.{day.isoformat()}"))
            self.log.write_text(
                "".join(f"{line}\n" for line in state)
                + f"{supervisor.iso_utc(moment)} LOG_ROTATED date={day.isoformat()} renamed=3 truncated=2"
                f" pruned=0 carried={len(state)}\n"
            )
            for extra in range(1, shape.extra_rotated + 1):
                # The next boundary's rotation (a start within 18:30-18:35 ET).
                self.write(
                    f"LOG_ROTATED date={day + timedelta(days=extra)} renamed=1 truncated=2 pruned=0 carried=4", moment
                )
        return pid

    def stop(self, reason: str = "SIGTERM") -> None:
        self.children = []
        self.write(f"SUPERVISOR_STOP reason={reason}")
        self.release_lock()


class FakeLive:
    """The live checkout's own commands, run as subprocesses by the code under test."""

    def __init__(self, world: "World") -> None:
        self.world = world
        self.calls: list[tuple[tuple[str, ...], Path, float]] = []
        self.stop_after = 3.0
        self.start_after = 2.0
        self.never_stop = False
        self.disable_code = 0
        # One entry per enable: how the supervisor starts (None: it never does).
        self.starts: list[StartShape | None] = []
        self.enable_code = 0
        self.migrate_result: tuple[int, str] | None = None
        self.status_code: int | None = None
        # Applied to the next dry run only (the undo's own dry run is clean).
        self.dry_run_edit: Callable[[str], str] | None = None
        self.dry_run_code = 0
        self.on_call: dict[str, Callable[[], None]] = {}

    def __call__(self, argv, *, cwd, timeout) -> deploy_apply.LiveResult:
        argv = tuple(argv)
        self.calls.append((argv, Path(cwd), timeout))
        args = argv[2:]
        verb = " ".join(args[:3]) if args[:2] == ("shadow", "agent") else " ".join(args[:2])
        hook = self.on_call.get(verb)
        if hook is not None:
            hook()
        world = self.world
        if verb == "shadow agent disable":
            world.launchctl.loaded = False
            if world.robot.running and not self.never_stop:
                world.clock.after(self.stop_after, world.robot.stop)
            if self.disable_code:
                return deploy_apply.LiveResult(argv, self.disable_code, "", "error: unable to control the LaunchAgent")
            return deploy_apply.LiveResult(argv, 0, "LaunchAgent disabled\n", "")
        if verb == "shadow agent enable":
            shape = self.starts.pop(0) if self.starts else StartShape()
            root = shape.plist_root if shape is not None and shape.plist_root else world.repo
            manager = LaunchdManager(user_paths=world.paths, repository_root=root)
            world.paths.launch_agents_directory.mkdir(parents=True, exist_ok=True)
            world.paths.launch_agent_file.write_bytes(manager.serialized_payload())
            world.launchctl.loaded = True
            if shape is not None:
                world.clock.after(self.start_after, lambda: world.robot.start(shape))
            if self.enable_code:
                return deploy_apply.LiveResult(argv, self.enable_code, "", "error: unable to enable the LaunchAgent")
            return deploy_apply.LiveResult(argv, 0, "LaunchAgent enabled\n", "")
        if verb == "run --dry-run":
            text = dry_run_text(world, date.fromisoformat(args[3]))
            if self.dry_run_edit is not None:
                text, self.dry_run_edit = self.dry_run_edit(text), None
            return deploy_apply.LiveResult(argv, self.dry_run_code, text, "")
        if verb == "db migrate" and self.migrate_result is not None:
            code, text = self.migrate_result
            return deploy_apply.LiveResult(argv, code, text, "error: migrate_failed: injected" if code else "")
        if verb == "db migrate":
            out, err = io.StringIO(), io.StringIO()
            code = run_migrate(Path(args[3]), now=world.clock(), out=out, err=err)
            return deploy_apply.LiveResult(argv, code, out.getvalue(), err.getvalue())
        if verb == "db status":
            out = io.StringIO()
            code = run_status(Path(args[3]), now=world.clock(), out=out)
            if self.status_code is not None:
                code = self.status_code
            return deploy_apply.LiveResult(argv, code, out.getvalue(), "")
        raise AssertionError(f"unexpected live command {argv}")

    @property
    def verbs(self) -> list[str]:
        return [" ".join(argv[2:5]) for argv, _cwd, _timeout in self.calls]


# The dry run's text, copied from the real `run --dry-run --date 2026-10-19` of
# the 2026-10-18 weekend rehearsal (L3) plus the heartbeat lines of runbook L3-8.
_DRY_RUN_HEAD = """DRY RUN: nothing is launched and no log is written.
session      {day} ({dow}), XNYS 09:30-16:00 ET
clock start  {day} 00:00 ET (simulated)
python       {python}
logs         {logs}/<child>.log; CHILD_* lines in {logs}/supervisor.log

child     window ET    final exit 0  backoff    command
screener  07:55-16:00  from 16:00    15-300s    backend/autotraderctl.py screener run --interval 60
notifier  07:55-16:15  from 16:05    15-300s    backend/autotraderctl.py notify run --date {day}
bridge    09:25-16:00  from 15:55    15-300s    backend/scripts_local/paper_bridge.py --date {day}
orb-v2    09:28-15:31  from 15:30    95-300s    backend/autotraderctl.py shadow run --strategy orb-v2 --universe scanner --date {day}
momo      09:55-15:01  from 15:00    95-300s    backend/autotraderctl.py shadow run --strategy momo --universe watchlist --date {day}
recorder  16:20-17:30  from 16:20    15-300s    backend/autotraderctl.py research record
replay    17:35-18:30  from 17:35    15-300s    backend/autotraderctl.py research replay
bridge exit 78 is a refusal: logged, never restarted
telegram  always-on    never         15-300s    backend/autotraderctl.py telegram run
telegram runs from supervisor start to supervisor stop, between sessions too; restarted after any exit (not shown in the timeline below)
heartbeat always-on    never         15-300s    -m trading.operations.heartbeat
heartbeat runs from supervisor start to supervisor stop, between sessions too; restarted after any exit (not shown in the timeline below)
restart cap  6 exits / 30 min; bridge and always-on children page only
heartbeat    Keychain item com.maccabi.autotrader-v1.heartbeat/heartbeat-url present (the child validates the URL)
rotation     daily at 18:35 ET between sessions into <name>.log.YYYY-MM-DD, 14 days kept; launchd.*.log and running always-on children's logs copied then truncated

simulated timeline (ET; pids are placeholders):
00:00:00  SUPERVISOR_START pid=62397 mode=once date={day}
00:00:00  SESSION_WAIT date={day} until={day}T11:55:00+00:00
07:55:00  SESSION_START date={day}
07:55:00  CHILD_START screener pid=1
16:00:00  SIGNAL screener SIGTERM reason=window_end
16:00:00  CHILD_EXIT screener code=0 restart_in=none
18:30:00  SESSION_END date={day}
18:30:00  SUPERVISOR_STOP reason=completed"""
_BRIDGE_LINES = """bridge       reviewed (sha256 {pin12})
bridge       INFO bridge sha256={pin}
bridge       INFO sendable identities = GAP_GO_V2, MOMO_HOD_V2, ORB_V2, VWAP_PB_V2
bridge       INFO caps GAP_GO_V2 positions=1 entries/day=1 last_entry=close-30m
bridge       INFO caps MOMO_HOD_V2 positions=3 entries/day=none last_entry=close-60m
bridge       INFO caps ORB_V2 positions=1 entries/day=1 last_entry=close-30m
bridge       INFO caps VWAP_PB_V2 positions=1 entries/day=2 last_entry=close-30m
bridge       {fenced}"""
_DRY_RUN_TAIL = """registry     sendable identities, size table and strategy registry agree
{schema}"""


def dry_run_text(world: "World", day: date) -> str:
    """What the live checkout's dry run prints, from the live files and database now."""
    repo = world.repo
    bridge = (repo / BRIDGE).read_bytes()
    actual = hashlib.sha256(bridge).hexdigest()
    pin = deploy_check.reviewed_pins((repo / SUPERVISOR).read_text())[deploy_check.PIN_NAME]
    status = inspect_schema(world.db)
    if actual == pin:
        with sqlite3.connect(world.db) as db:
            fenced = sorted(
                name.split(":", 1)[1]
                for (name,) in db.execute(
                    "SELECT name FROM autotrader_execution_fences WHERE name LIKE 'paper:%' AND stopped=1"
                )
                if name.split(":", 1)[1] in SENDABLE_IDENTITIES
            )
        level = "CRITICAL" if fenced else "INFO"
        bridge_lines = _BRIDGE_LINES.format(
            pin12=actual[:12], pin=actual, fenced=f"{level} fenced identities = {', '.join(fenced) or 'none'}"
        )
    else:
        bridge_lines = (
            f"WARNING: bridge sha256 {actual[:12]} is not the reviewed {pin[:12]}:"
            " the supervisor will refuse to start it (no orders)"
        )
    schema = (
        f"database     schema v{status.applied_version} current ({status.database})"
        if status.current
        else f"!!! DATABASE NOT READY ({status.token}): {status.describe()} ({status.database})"
    )
    head = _DRY_RUN_HEAD.format(
        day=day.isoformat(), dow=f"{day:%a}", python=world.robot.python, logs=world.paths.log_directory
    )
    return "\n".join([head, bridge_lines, _DRY_RUN_TAIL.format(schema=schema)]) + "\n"


# --- the world -------------------------------------------------------------------------------------


@dataclass
class Scenario:
    repo: Repo
    base: str
    reviewed: str
    head: str


def _verdict_line(sha: str, *, scope: str, bridge_sha: str | None, migration: int | None, label: str) -> str:
    tokens = f" bridge_sha={bridge_sha}" if bridge_sha else ""
    tokens += f" migration={migration}" if migration is not None else ""
    prefix = f"{label} " if label else ""
    return f"2026-10-24T20:00:00+03:00 | {prefix}{sha} verdict=GO scope={scope}{tokens} | GO; test | docs/reviews/r.md"


def _scenario(
    root: Path,
    *,
    scope: str = "P2",
    bridge_sha: str | None = PIN_V2,
    migration: int | None = None,
    tool_change: bool = False,
    label: str = "P2",
    candidate_pin: str = PIN_V2,
    base_pinned: bool = True,
) -> Scenario:
    repo = Repo(root / "live")
    repo.path.mkdir(parents=True)
    _git(repo.path, "init", "-q", "-b", "main")
    repo.write(".gitignore", "backend/.venv/\n")
    repo.write(BRIDGE, BRIDGE_V1)
    repo.write(
        SUPERVISOR,
        f'"""supervisor"""\nREVIEWED_BRIDGE_SHA256 = "{PIN_V1}"\n' if base_pinned else '"""no pin"""\n',
    )
    repo.write("backend/autotraderctl.py", "raise SystemExit(0)\n")
    repo.write(LEDGER, "# Ledger\n")
    repo.write("README.md", "readme\n")
    base = repo.commit("base")
    repo.git("checkout", "-q", "-b", "cand")
    repo.write(BRIDGE, BRIDGE_V2)
    repo.write(SUPERVISOR, f'"""supervisor"""\nREVIEWED_BRIDGE_SHA256 = "{candidate_pin}"\n')
    repo.write("backend/trading/feature.py", "FEATURE = 2\n")
    if tool_change:
        repo.write(TOOL_FILE, "# the deploy tool changes\n")
    reviewed = repo.commit("feature")
    repo.append(
        LEDGER, _verdict_line(reviewed[:7], scope=scope, bridge_sha=bridge_sha, migration=migration, label=label)
    )
    head = repo.commit("docs(reviews): verdict")
    repo.git("checkout", "-q", "main")
    python = repo.path / "backend" / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text("#!/bin/sh\nexit 99\n")
    python.chmod(0o755)
    return Scenario(repo=repo, base=base, reviewed=reviewed, head=head)


@dataclass
class World:
    root: Path
    scenario: Scenario
    paths: UserPaths
    db: Path
    clock: FakeClock
    launchctl: FakeLaunchctl
    robot: FakeRobot = field(init=False)
    live: FakeLive = field(init=False)
    out: io.StringIO = field(default_factory=io.StringIO)
    own_root: Path | None = None
    writer_factory: Callable[[Path], GitWriter] = GitWriter
    sntp: Callable[[], str] = lambda: GOOD_SNTP

    def __post_init__(self) -> None:
        self.robot = FakeRobot(self.paths, self.repo, self.clock)
        self.live = FakeLive(self)

    @property
    def repo(self) -> Path:
        return self.scenario.repo.path

    def worktree(self) -> Path:
        """A detached worktree at the candidate's current head (the running code)."""
        tree = self.root / "cand-wt"
        head = _git(self.repo, "rev-parse", "cand")
        if not tree.exists():
            _git(self.repo, "worktree", "add", "-q", "--detach", str(tree), head)
        else:
            _git(tree, "checkout", "-q", "--detach", head)
        return tree

    def env(self, typed: str | None = None, *, tty: bool = True) -> DeployEnvironment:
        return DeployEnvironment(
            user_paths=self.paths,
            db_path=self.db,
            stdin=FakeTTY(self.short_head + "\n" if typed is None else typed, tty=tty),
            out=self.out,
            clock=self.clock,
            sleep=self.clock.sleep,
            process_table=self.robot.table,
            live_runner=self.live,
            launchctl=self.launchctl,
            sntp=self.sntp,
            own_root=self.own_root or self.worktree(),
            writer_factory=self.writer_factory,
        )

    @property
    def head(self) -> str:
        return _git(self.repo, "rev-parse", "cand")

    @property
    def short_head(self) -> str:
        return self.head[:7]

    def live_head(self) -> str:
        return _git(self.repo, "rev-parse", "HEAD")

    def request(self, **changes) -> DeployApplyRequest:
        request = DeployApplyRequest(
            candidate="cand",
            repo=self.repo,
            expect_parent=self.scenario.base,
            expect_pin=PIN_V2,
            scope=("P2",),
        )
        return replace(request, **changes)

    def apply(self, typed: str | None = None, *, tty: bool = True, **changes) -> int:
        return deploy_apply.run_deploy_apply(self.request(**changes), self.env(typed, tty=tty))

    def undo(self, to: str, reason: str = "dry_run_bang", typed: str | None = None) -> int:
        environment = self.env(to[:7] + "\n" if typed is None else typed)
        return deploy_apply.run_deploy_undo(DeployUndoRequest(repo=self.repo, to=to, reason=reason), environment)

    @property
    def deploys_log(self) -> Path:
        return self.paths.log_directory / deploy_apply.DEPLOYS_LOG_NAME

    def deploys(self) -> list[str]:
        return self.deploys_log.read_text().splitlines() if self.deploys_log.exists() else []

    @property
    def output(self) -> str:
        return self.out.getvalue()

    def summary(self) -> str:
        return self.output.strip().splitlines()[-1]

    def operator(self) -> DeployOperator:
        return DeployOperator(self.env(), self.repo)


def _make_world(tmp_path: Path, **scenario_options) -> World:
    scenario = _scenario(tmp_path, **scenario_options)
    paths = UserPaths(home=tmp_path / "home")
    for directory in (paths.data_directory, paths.log_directory):
        directory.mkdir(parents=True, exist_ok=True)
    database = migrate_database(paths.database_file)
    clock = FakeClock(START)
    world = World(
        root=tmp_path, scenario=scenario, paths=paths, db=database, clock=clock, launchctl=FakeLaunchctl(loaded=True)
    )
    # Saturday's 18:35 ET rotation already ran: no rotation is due at a start now.
    boundary = latest_rotation_boundary(START).date()
    world.robot.write("LOG_ROTATED date=x", START - timedelta(hours=1),
                      path=world.robot.log.with_name(f"supervisor.log.{boundary.isoformat()}"))
    # The running supervisor, started two hours ago from the live checkout.
    world.robot.start(at=START - timedelta(hours=2))
    manager = LaunchdManager(user_paths=paths, repository_root=world.repo)
    paths.launch_agents_directory.mkdir(parents=True, exist_ok=True)
    paths.launch_agent_file.write_bytes(manager.serialized_payload())
    return world


@pytest.fixture
def world(tmp_path) -> World:
    made = _make_world(tmp_path)
    yield made
    made.robot.release_lock()


def _token_lines(world: World, token: str) -> None:
    """The one token appears identically in stdout, deploys.log reason= and the summary."""
    assert f"FAIL {token}:" in world.output
    assert any(f"reason={token}" in line for line in world.deploys())
    assert f"reason={token}" in world.summary()


def _refused(world: World) -> set[str]:
    summary = world.summary()
    assert summary.startswith(("deploy apply: REFUSED ", "deploy undo: REFUSED ")), summary
    return set(summary.split("REFUSED ", 1)[1].split(" ", 1)[0].split(","))


# --- P2-1: constants, tokens, module shape ----------------------------------------------------------


def test_deploy_apply_constants_are_pinned():
    assert deploy_apply.FIRST_START == time(18, 40)
    assert deploy_apply.LAST_START == time(6, 50)
    assert deploy_apply.STOP_WAIT_TIMEOUT_S == 75
    assert deploy_apply.POST_START_TIMEOUT_S == 60
    assert deploy_apply.POLL_S == 1.0
    assert deploy_apply.DEPLOYS_LOG_NAME == "deploys.log"
    assert deploy_apply.DEPLOY_LOCK_NAME == "deploy.lock"
    assert (
        deploy_apply.EXIT_OK,
        deploy_apply.EXIT_REFUSED,
        deploy_apply.EXIT_ROLLED_BACK,
        deploy_apply.EXIT_HALTED,
        deploy_apply.EXIT_UNDO_FAILED,
    ) == (0, 78, 3, 4, 5)
    assert deploy_apply.EXIT_REFUSED == deploy_check.EXIT_REFUSED
    assert deploy_apply.DEPLOY_TOOL_PATHS == (
        "backend/trading/operations/deploy_apply.py",
        "backend/trading/operations/deploy_check.py",
        "backend/trading/operations/launchd.py",
        "backend/trading/operations/db_admin.py",
    )
    # The supervisor's stop budget: children 20 s, launchd's ExitTimeOut 45 s.
    assert deploy_apply.STOP_WAIT_TIMEOUT_S > launchd.EXIT_TIMEOUT_SECONDS
    assert deploy_apply.LAST_START < time(7, 55) and deploy_apply.FIRST_START > time(18, 35)


def test_every_failure_token_is_one_literal_listed_once():
    tokens = deploy_apply.TOKENS
    assert len(tokens) == len(set(tokens))
    assert all(token == token.lower() and token.replace("_", "").isalnum() for token in tokens)
    assert set(PLAN_TOKENS) <= set(tokens)
    module_tokens = {
        value
        for name, value in vars(deploy_apply).items()
        if name.isupper() and isinstance(value, str) and value in tokens
    }
    assert module_tokens | set(deploy_apply.DEPLOY_CHECK_TOKENS) == set(tokens)


def test_module_has_no_hard_reset_literal():
    source = Path(deploy_apply.__file__).read_text()
    tree = ast.parse(source)
    strings = [node for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str)]
    assert not any("--hard" in node.value for node in strings)
    git_writes = {
        "merge", "reset", "checkout", "commit", "push", "pull", "rebase", "stash", "clean", "branch", "tag",
        "switch", "restore", "am", "cherry-pick", "revert", "fetch", "update-ref", "gc", "worktree", "apply",
    }
    writer = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "GitWriter")
    inside = {id(node) for node in ast.walk(writer)}
    found = {(node.value, id(node) in inside) for node in strings if node.value in git_writes}
    assert found == {("merge", True), ("reset", True)}
    methods = {node.name for node in writer.body if isinstance(node, ast.FunctionDef) and not node.name.startswith("_")}
    assert methods == {"merge_ff_only", "reset_keep"}
    # The only argv the writer builds: git -C <repo> -c core.fsmonitor=false <the two writes>.
    assert {"--ff-only", "--keep"} <= {node.value for node in strings if id(node) in inside}


def test_writer_argv_only_ff_only_and_reset_keep(world, monkeypatch):
    seen: list[list[str]] = []
    real_run = subprocess.run

    def spy(argv, *args, **kwargs):
        seen.append([str(part) for part in argv])
        return real_run(argv, *args, **kwargs)

    world.live.dry_run_edit = lambda text: text + "!!! injected\n"
    monkeypatch.setattr(subprocess, "run", spy)
    code = world.apply()
    monkeypatch.setattr(subprocess, "run", real_run)

    assert code == deploy_apply.EXIT_ROLLED_BACK, world.output
    # The code's own git calls (the test's helpers configure a user instead).
    git = [argv for argv in seen if argv[0] == "git" and "core.fsmonitor=false" in argv]
    read_only = {"rev-parse", "ls-tree", "show", "merge-base", "diff", "log", "status"}
    writes = []
    for argv in git:
        subcommand = argv[argv.index("core.fsmonitor=false") + 1]
        if subcommand not in read_only:
            writes.append(argv)
        else:
            assert argv[1] == "--no-optional-locks"
    assert writes == [
        ["git", "-C", str(world.repo), "-c", "core.fsmonitor=false", "merge", "--ff-only", world.head],
        ["git", "-C", str(world.repo), "-c", "core.fsmonitor=false", "reset", "--keep", world.scenario.base],
    ]
    for bad in ("cand", "HEAD", "--hard", world.head[:7], ""):
        with pytest.raises(ValueError):
            GitWriter(world.repo).merge_ff_only(bad)
        with pytest.raises(ValueError):
            GitWriter(world.repo).reset_keep(bad)


# --- P2-1: the whole run -------------------------------------------------------------------------------


def test_apply_happy_path_writes_prev_then_deploy_line_and_prints_ledger_line(world):
    code = world.apply()

    assert code == deploy_apply.EXIT_OK, world.output
    assert world.live_head() == world.head
    prev, deploy = world.deploys()
    assert prev == f"2026-10-25T02:00:00+03:00 P2 PREV_SHA={world.scenario.base} schema=v{LATEST}"
    assert deploy.split(" ", 1)[1] == (
        f"DEPLOY P2 {world.short_head} PREV_SHA={world.scenario.base} pin={PIN_V2[:12]} result=PASS schema=v{LATEST}"
    )
    # The 2026-10-18 shape (`DEPLOY <label> <short> PREV_SHA=<40> pin=<12> result=PASS`) plus schema=.
    assert deploy_apply._DEPLOY_PASS.search(deploy).group("prev") == world.scenario.base
    lines = world.output.splitlines()
    ledger = lines[-2]
    assert ledger.endswith(f" | {world.deploys_log}")
    assert (
        f" | DEPLOY P2 {world.short_head} PREV_SHA={world.scenario.base} pin={PIN_V2[:12]} result=PASS"
        f" | owner-run via autotraderctl deploy apply ({world.short_head}); 9 dry-run lines ok; post-checks ok | "
    ) in ledger
    assert world.summary() == f"deploy apply: PASS P2 {world.short_head} (exit 0)"
    assert world.live.verbs == ["shadow agent disable", "run --dry-run --date", "shadow agent enable"]
    assert world.launchctl.loaded and world.robot.running and world.robot.starts == 2
    for expected in (
        "check OK verdict",
        "PLAN (nothing has changed yet):",
        "OK stopped: no supervisor, no trading process, supervisor.lock free, SUPERVISOR_STOP reason=SIGTERM",
        f"OK fast-forward: HEAD {world.head}, bridge sha256 {PIN_V2[:12]}, tree only benign",
        f"  | bridge       reviewed (sha256 {PIN_V2[:12]})",
        "  | bridge       INFO fenced identities = none",
        f"  | {deploy_apply.heartbeat_ok_line()}",
        "OK dry run: 9 required lines, no !!!, no WARNING:",
        f"OK started: one supervisor, telegram and heartbeat under it, SESSION_WAIT date={NEXT_SESSION},"
        f" plist WorkingDirectory {world.repo}/backend, loaded, HEAD {world.head[:12]}",
    ):
        assert expected in lines, expected
    # The PASS line is written last, after every post-check.
    assert world.output.index("OK started:") < world.output.index("deploy apply: PASS")


def test_live_subprocesses_use_live_checkout_python_and_cwd(world, tmp_path, monkeypatch):
    assert world.apply() == deploy_apply.EXIT_OK, world.output
    python = str(world.repo / "backend" / ".venv" / "bin" / "python")
    control = str(world.repo / "backend" / "autotraderctl.py")
    assert [(argv[:2], cwd) for argv, cwd, _timeout in world.live.calls] == [
        ((python, control), world.repo / "backend")
    ] * 3
    assert [argv[2:] for argv, _cwd, _timeout in world.live.calls] == [
        ("shadow", "agent", "disable"),
        ("run", "--dry-run", "--date", NEXT_SESSION.isoformat()),
        ("shadow", "agent", "enable"),
    ]
    # The worktree's interpreter is never used: the code runs from cand-wt.
    assert all(str(world.root / "cand-wt") not in " ".join(argv) for argv, _c, _t in world.live.calls)
    recorded: list[tuple[tuple[str, ...], Path]] = []

    def runner(argv, *, cwd, timeout):
        recorded.append((tuple(argv), cwd))
        return deploy_apply.LiveResult(tuple(argv), 0, "", "")

    checkout = deploy_apply.LiveCheckout(world.repo, runner)
    checkout.migrate(world.db)
    checkout.status(world.db)
    assert recorded == [
        ((python, control, "db", "migrate", "--db-path", str(world.db)), world.repo / "backend"),
        ((python, control, "db", "status", "--db-path", str(world.db)), world.repo / "backend"),
    ]
    # The real runner: cwd is the live backend, PYTHONPATH never leaks in.
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "elsewhere"))
    result = deploy_apply.subprocess_runner(
        [sys.executable, "-c", "import os; print(os.getcwd()); print(os.environ.get('PYTHONPATH'))"],
        cwd=world.repo / "backend",
        timeout=30,
    )
    assert result.returncode == 0
    assert result.stdout.splitlines() == [str((world.repo / "backend").resolve()), "None"]


# --- P2-1: pre-checks ------------------------------------------------------------------------------------


def _hold(path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(descriptor, fcntl.LOCK_EX)
    return descriptor


def _on_candidate(world: World, edit: Callable[[Repo], None]) -> None:
    world.scenario.repo.git("checkout", "-q", "cand")
    edit(world.scenario.repo)
    world.scenario.repo.commit("on top")
    world.scenario.repo.git("checkout", "-q", "main")


def _rebuild(world: World, **options) -> World:
    """A world whose scenario is built with other options (same tmp root)."""
    world.robot.release_lock()
    root = world.root / "alt"
    root.mkdir()
    return _make_world(root, **options)


def _break_start_cutoff(world, held):
    world.clock.now = datetime(2026, 10, 25, 6, 50, tzinfo=MARKET_TIMEZONE)
    return world, {}, "start_cutoff"


def _break_verdict(world, held):
    _on_candidate(world, lambda repo: repo.write("backend/trading/late.py", "LATE = 1\n"))
    return world, {}, "deploy_check_verdict"


def _break_tree(world, held):
    world.scenario.repo.write("backend/stray.py", "x = 1\n")
    return world, {}, "deploy_check_tree_clean"


def _break_parent(world, held):
    return world, {"expect_parent": world.scenario.reviewed}, "deploy_check_parent"


def _database_at(world: World, version: int) -> None:
    for suffix in ("", "-wal", "-shm"):
        Path(f"{world.db}{suffix}").unlink(missing_ok=True)
    database_at(world.db, version)


def _break_schema(world, held):
    _database_at(world, PREVIOUS)
    return world, {}, "deploy_check_schema"


def _break_fence(world, held):
    with sqlite3.connect(world.db) as db:
        db.execute(
            "INSERT INTO autotrader_execution_fences (name, stopped, reason, updated_at)"
            " VALUES ('paper', 1, 'test stop', '2026-10-24T00:00:00+00:00')"
        )
    return world, {}, "deploy_check_fence"


def _break_clock(world, held):
    world.sntp = lambda: "-3.5 +/- 0.01 time.apple.com\n"
    return world, {}, "deploy_check_clock"


def _break_pin_blob(world, held):
    return _rebuild(world, candidate_pin=PIN_V1, bridge_sha=PIN_V2), {}, "deploy_check_pin"


def _break_migration_unexpected(world, held):
    return world, {"expect_migration": LATEST}, "migration_unexpected"


def _break_migration_missing(world, held):
    _database_at(world, PREVIOUS)
    return world, {"expect_migration": LATEST}, "verdict_migration_missing"


def _break_pin_verdict_missing(world, held):
    return _rebuild(world, bridge_sha=None), {}, "pin_verdict_missing"


def _break_pin_expected(world, held):
    return world, {"expect_pin": PIN_V1}, "pin_expected"


def _break_prev(world, held):
    held.append(("prev", None))
    return world, {}, "prev_sha_invalid"


def _break_tool_scope(world, held):
    return _rebuild(world, scope="A1", tool_change=True), {"scope": ("A1",)}, "deploy_tool_scope_missing"


def _break_fenced(world, held):
    with sqlite3.connect(world.db) as db:
        db.execute(
            "INSERT INTO autotrader_execution_fences (name, stopped, reason, updated_at)"
            " VALUES ('paper:ORB_V2', 1, 'owner halt', '2026-10-24T00:00:00+00:00')"
        )
    return world, {}, "fenced_unexpected"


def _break_migrate_lock(world, held):
    held.append(("fd", _hold(world.db.with_name(world.db.name + ".migrate.lock"))))
    return world, {}, "migrate_lock_busy"


def _break_deploys_log(world, held):
    target = world.root / "elsewhere.log"
    target.write_text("")
    world.deploys_log.symlink_to(target)
    return world, {}, "deploys_log_unusable"


def _break_deploy_lock(world, held):
    held.append(("fd", _hold(world.paths.data_directory / deploy_apply.DEPLOY_LOCK_NAME)))
    return world, {}, "deploy_lock_busy"


def _break_runs_from_live(world, held):
    world.own_root = world.repo
    return world, {}, "runs_from_live_checkout"


def _break_not_candidate(world, held):
    tree = world.root / "old-wt"
    _git(world.repo, "worktree", "add", "-q", "--detach", str(tree), world.scenario.reviewed)
    world.own_root = tree
    return world, {}, "not_candidate_worktree"


def _break_live_python(world, held):
    (world.repo / "backend" / ".venv" / "bin" / "python").unlink()
    return world, {}, "live_python_missing"


def _break_label(world, held):
    return _rebuild(world, label=""), {}, "label_missing"


def _break_fences_unreadable(world, held):
    for suffix in ("", "-wal", "-shm"):
        Path(f"{world.db}{suffix}").unlink(missing_ok=True)
    world.db.write_bytes(b"not a database" * 100)
    return world, {}, "fenced_unexpected"


def _break_live_pin(world, held):
    return _rebuild(world, base_pinned=False), {}, "prev_sha_invalid"


PRECHECK_BREAKERS = {
    "start_cutoff": _break_start_cutoff,
    "deploy_check_verdict": _break_verdict,
    "deploy_check_tree_clean": _break_tree,
    "deploy_check_parent": _break_parent,
    "deploy_check_schema": _break_schema,
    "deploy_check_fence": _break_fence,
    "deploy_check_clock": _break_clock,
    "deploy_check_pin": _break_pin_blob,
    "migration_unexpected": _break_migration_unexpected,
    "verdict_migration_missing": _break_migration_missing,
    "pin_verdict_missing": _break_pin_verdict_missing,
    "pin_expected": _break_pin_expected,
    "prev_sha_invalid": _break_prev,
    "deploy_tool_scope_missing": _break_tool_scope,
    "fenced_unexpected": _break_fenced,
    "migrate_lock_busy": _break_migrate_lock,
    "deploys_log_unusable": _break_deploys_log,
    "deploy_lock_busy": _break_deploy_lock,
    "runs_from_live_checkout": _break_runs_from_live,
    "not_candidate_worktree": _break_not_candidate,
    "live_python_missing": _break_live_python,
    "label_missing": _break_label,
    "fenced_unreadable": _break_fences_unreadable,
    "live_pin_unreadable": _break_live_pin,
}


@pytest.mark.parametrize("name", sorted(PRECHECK_BREAKERS))
def test_each_precheck_refusal_changes_nothing(world, monkeypatch, name):
    held: list[tuple[str, object]] = []
    target, changes, token = PRECHECK_BREAKERS[name](world, held)
    if ("prev", None) in held:
        monkeypatch.setattr(DeployOperator, "_live_head", lambda self: "")
    before = target.live_head()
    try:
        code = target.apply(**changes)
    finally:
        for kind, value in held:
            if kind == "fd":
                os.close(value)
        target.robot.release_lock()

    assert code == deploy_apply.EXIT_REFUSED, target.output
    assert token in _refused(target), target.output
    assert f"REFUSE {token}: " in target.output
    assert target.summary().endswith("(exit 78; nothing changed)")
    assert target.live_head() == before
    assert not target.deploys_log.exists() or target.deploys_log.is_symlink()
    if target.deploys_log.is_symlink():
        assert (target.root / "elsewhere.log").read_text() == ""
    assert target.live.calls == [] and target.launchctl.calls == [] and target.robot.table_reads == 0
    assert "PLAN (nothing has changed yet):" not in target.output


def test_not_a_tty_refuses(world):
    code = world.apply(tty=False)

    assert code == deploy_apply.EXIT_REFUSED
    assert _refused(world) == {"not_interactive"}
    assert "check " not in world.output  # refused before any pre-check ran
    assert world.live.calls == [] and not world.deploys_log.exists()


@pytest.mark.parametrize("typed", ["deadbee\n", "", "\n", "{full}\n", "{upper}\n"])
def test_wrong_typed_sha_refuses(world, typed):
    typed = typed.format(full=world.head, upper=world.short_head.upper())

    code = world.apply(typed=typed)

    assert code == deploy_apply.EXIT_REFUSED
    assert _refused(world) == {"confirm_mismatch"}
    assert "PLAN (nothing has changed yet):" in world.output
    assert world.live.calls == [] and not world.deploys_log.exists()
    assert world.live_head() == world.scenario.base


def test_runs_from_live_checkout_refuses(world):
    world.own_root = world.repo

    assert world.apply() == deploy_apply.EXIT_REFUSED
    assert "runs_from_live_checkout" in _refused(world)
    assert f"REFUSE runs_from_live_checkout: this code runs from {world.repo} itself" in world.output
    assert world.live.calls == []


def test_deploy_tool_change_requires_p2_scope(tmp_path):
    refused = _make_world(tmp_path / "a", scope="A1", tool_change=True)
    allowed = _make_world(tmp_path / "b", scope="A1,P2", tool_change=True)
    try:
        assert refused.apply(scope=("A1",)) == deploy_apply.EXIT_REFUSED
        assert _refused(refused) == {"deploy_tool_scope_missing"}
        assert TOOL_FILE in refused.output
        assert allowed.apply(scope=("A1", "P2")) == deploy_apply.EXIT_OK, allowed.output
    finally:
        refused.robot.release_lock()
        allowed.robot.release_lock()


def test_expect_pin_three_way(world, tmp_path):
    git = deploy_check.Git(world.repo)
    head = world.head

    def problems(expect, verdict_sha=PIN_V2):
        line = deploy_check.VerdictLine("x", 1, _verdict_line("abc1234", scope="P2", bridge_sha=verdict_sha,
                                                              migration=None, label="P2"), "abc1234")
        return deploy_apply.pin_problems(git, head, expect, [line])

    found, files = problems(PIN_V2)
    assert found == [] and files == {BRIDGE: PIN_V2}
    # --expect-pin differs from all three.
    found, _ = problems(PIN_V1)
    assert [token for token, _ in found] == ["pin_expected"] * 2
    assert "verdict bridge_sha=" in found[0][1] and "REVIEWED_BRIDGE_SHA256" in found[1][1]
    # The verdict's bridge_sha alone differs.
    found, _ = problems(PIN_V2, verdict_sha=PIN_V1)
    assert [token for token, _ in found] == ["pin_expected"] and "verdict bridge_sha=" in found[0][1]
    # The candidate's pin constant and the verdict agree; the blob does not.
    stale = _make_world(tmp_path / "stale", candidate_pin=PIN_V1, bridge_sha=PIN_V1)
    try:
        found, _ = deploy_apply.pin_problems(
            deploy_check.Git(stale.repo), stale.head, PIN_V1,
            [deploy_check.VerdictLine("x", 1, _verdict_line("abc1234", scope="P2", bridge_sha=PIN_V1,
                                                             migration=None, label="P2"), "abc1234")],
        )
        assert [token for token, _ in found] == ["pin_expected"]
        assert f"{BRIDGE} sha256 {PIN_V2} is not its pin {PIN_V1}" in found[0][1]
        assert stale.apply(expect_pin=PIN_V1) == deploy_apply.EXIT_REFUSED
        assert _refused(stale) == {"deploy_check_pin", "pin_expected"}
    finally:
        stale.robot.release_lock()
    # A future map pin (P3-B0) is verified file by file; an unknown shape refuses.
    assert deploy_apply.pinned_files(
        {"REVIEWED_BRIDGE_SHA256": PIN_V2, "REVIEWED_BRIDGE_IMPORTS_SHA256": {"config.py": PIN_V1}}
    ) == {BRIDGE: PIN_V2, "backend/config.py": PIN_V1}
    for bad in ({"REVIEWED_BRIDGE_SHA256": PIN_V2, "REVIEWED_X": "abc"}, {"REVIEWED_X": {"../x": PIN_V1}}, {}):
        with pytest.raises(ValueError):
            deploy_apply.pinned_files(bad)


def test_verdict_without_bridge_sha_refuses(tmp_path):
    world = _make_world(tmp_path, bridge_sha=None)
    try:
        assert world.apply() == deploy_apply.EXIT_REFUSED
    finally:
        world.robot.release_lock()
    assert _refused(world) == {"pin_verdict_missing"}
    assert "REFUSE pin_verdict_missing: the verdict line has no bridge_sha=" in world.output
    assert deploy_check.verdict_tokens(_verdict_line("abc1234", scope="P2", bridge_sha=None, migration=None,
                                                     label="P2")) == {}


def test_prev_sha_empty_short_or_not_ancestor_refuses_before_stop(world, monkeypatch):
    git = deploy_check.Git(world.repo)
    head = world.head
    repo = world.scenario.repo
    repo.git("checkout", "-q", "-b", "side", world.scenario.base)
    repo.write("backend/side.py", "SIDE = 1\n")
    side = repo.commit("side")
    repo.git("checkout", "-q", "main")
    tree = repo.git("rev-parse", "HEAD^{tree}")
    for bad in (None, "", " ", "abc1234", world.scenario.base[:39], world.scenario.base.upper(), "0" * 40, tree, side):
        assert deploy_apply.prev_sha_problem(git, bad, head) is not None, bad
    assert deploy_apply.prev_sha_problem(git, world.scenario.base, head) is None
    assert deploy_apply.prev_sha_problem(git, side) is None  # a commit; no candidate to compare with

    monkeypatch.setattr(DeployOperator, "_live_head", lambda self: "")
    code = world.apply()

    assert code == deploy_apply.EXIT_REFUSED
    assert "prev_sha_invalid" in _refused(world)
    assert "REFUSE prev_sha_invalid: PREV_SHA '' is not a 40-hex sha" in world.output
    assert world.live.calls == [] and not world.deploys_log.exists()


# --- P2-1: the window, three times ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "instant, inside",
    [
        # Saturday 2026-10-24 / Sunday 10-25, EDT (UTC-4): the weekend is no different.
        ("2026-10-25T10:49:59+00:00", True),  # Sun 06:49:59 EDT
        ("2026-10-25T10:50:00+00:00", False),  # Sun 06:50:00 EDT
        ("2026-10-24T22:39:59+00:00", False),  # Sat 18:39:59 EDT
        ("2026-10-24T22:40:00+00:00", True),  # Sat 18:40:00 EDT
        ("2026-10-24T22:35:00+00:00", False),  # Sat 18:35 EDT, the rotation
        ("2026-10-25T04:00:00+00:00", True),  # midnight EDT
        # A weekday: Monday 2026-10-26.
        ("2026-10-26T10:49:59+00:00", True),
        ("2026-10-26T10:50:00+00:00", False),
        ("2026-10-26T13:30:00+00:00", False),  # the open
        ("2026-10-26T22:40:00+00:00", True),
        # Sunday 2026-11-01, the US clock change (02:00 EDT -> 01:00 EST).
        ("2026-11-01T05:30:00+00:00", True),  # 01:30 EDT, the first 01:30
        ("2026-11-01T06:30:00+00:00", True),  # 01:30 EST, the repeated one
        ("2026-11-01T11:49:59+00:00", True),  # 06:49:59 EST
        ("2026-11-01T11:50:00+00:00", False),  # 06:50:00 EST
        ("2026-11-01T10:50:00+00:00", True),  # 05:50 EST (06:50 under EDT)
        ("2026-11-01T23:39:59+00:00", False),  # 18:39:59 EST
        ("2026-11-01T23:40:00+00:00", True),  # 18:40:00 EST
        ("2026-11-01T22:40:00+00:00", False),  # 17:40 EST (18:40 under EDT)
    ],
)
def test_start_cutoff_0650_and_first_start_1840_et(instant, inside):
    now = datetime.fromisoformat(instant)

    assert deploy_apply.in_start_window(now) is inside
    assert (deploy_apply.start_window_problem(now) is None) is inside
    with pytest.raises(ValueError):
        deploy_apply.in_start_window(datetime(2026, 10, 25, 2, 0))


def test_start_cutoff_rechecked_before_the_fast_forward(world):
    world.clock.now = datetime(2026, 10, 25, 6, 49, 0, tzinfo=MARKET_TIMEZONE)
    world.live.on_call["shadow agent disable"] = lambda: world.clock.sleep(61)

    code = world.apply()

    assert code == deploy_apply.EXIT_ROLLED_BACK, world.output
    assert "FAIL start_cutoff: before the fast-forward: now 2026-10-25 06:50" in world.output
    assert world.live_head() == world.scenario.base
    assert f"STEP git -C {world.repo} merge" not in world.output
    _token_lines(world, "start_cutoff")
    assert world.deploys()[-1].endswith(f"DEPLOY rollback to {world.scenario.base} result=ROLLBACK reason=start_cutoff")
    assert world.robot.running and world.launchctl.loaded


def test_start_cutoff_rechecked_before_the_start(world):
    world.clock.now = datetime(2026, 10, 25, 6, 49, 0, tzinfo=MARKET_TIMEZONE)
    world.live.on_call["run --dry-run"] = lambda: world.clock.sleep(60)

    code = world.apply()

    assert code == deploy_apply.EXIT_ROLLED_BACK, world.output
    assert "FAIL start_cutoff: before the start: now 2026-10-25 06:50" in world.output
    assert world.live_head() == world.scenario.base
    assert world.live.verbs[:3] == ["shadow agent disable", "run --dry-run --date", "shadow agent disable"]
    _token_lines(world, "start_cutoff")


# --- P2-1: the dry run, line by line -----------------------------------------------------------------------


def _template(world: World) -> str:
    return dry_run_text(world, NEXT_SESSION)


def _expect(world: World, **changes) -> DryRunExpectation:
    expectation = DryRunExpectation(
        session_date=NEXT_SESSION, pin=PIN_V1, schema_version=LATEST, database=str(world.db)
    )
    return replace(expectation, **changes)


DRY_RUN_VARIANTS = {
    "dry_run_exit": (None, {}, 1),
    "dry_run_bang": (lambda t: t + "!!! the supervisor will not start: fix the registry (nothing launches)\n", {}, 0),
    "dry_run_warning": (
        lambda t: t + "WARNING: running outside the supervisor (kill 600 before `run`): python autotraderctl.py"
        " screener run\n", {}, 0),
    "dry_run_pin": (lambda t: t.replace(f"(sha256 {PIN_V1[:12]})", f"(sha256 {PIN_V2[:12]})"), {}, 0),
    "dry_run_pin_info_missing": (lambda t: t.replace(f"bridge       INFO bridge sha256={PIN_V1}\n", ""), {}, 0),
    "dry_run_pin_twice": (lambda t: t + f"bridge       reviewed (sha256 {PIN_V1[:12]})\n", {}, 0),
    "dry_run_fenced": (lambda t: t.replace("INFO fenced identities = none", "CRITICAL fenced identities = ORB_V2"),
                       {}, 0),
    "dry_run_fenced_expected_missing": (None, {"fenced": ("ORB_V2",)}, 0),
    "dry_run_sendable": (lambda t: t.replace("GAP_GO_V2, MOMO_HOD_V2, ORB_V2, VWAP_PB_V2", "MOMO_HOD_V2"), {}, 0),
    "dry_run_registry": (lambda t: t.replace("registry     sendable identities, size table and strategy registry"
                                             " agree\n", ""), {}, 0),
    "dry_run_schema": (lambda t: t.replace(f"schema v{LATEST} current", f"schema v{PREVIOUS} current"), {}, 0),
    "dry_run_session": (lambda t: t.replace(f"SESSION_WAIT date={NEXT_SESSION}", "SESSION_WAIT date=2026-10-27"),
                        {}, 0),
    "dry_run_session_line": (lambda t: t.replace(f"session      {NEXT_SESSION}", "session      2026-10-27"), {}, 0),
    "dry_run_heartbeat": (
        lambda t: t.replace(
            "heartbeat    Keychain item com.maccabi.autotrader-v1.heartbeat/heartbeat-url present (the child"
            " validates the URL)",
            "heartbeat    not configured (no Keychain item com.maccabi.autotrader-v1.heartbeat/heartbeat-url)",
        ), {}, 0),
    "dry_run_budget": (None, {"budget": True}, 0),
}


@pytest.mark.parametrize("variant", sorted(DRY_RUN_VARIANTS))
def test_dry_run_check_each_bad_line_variant(world, variant):
    edit, changes, code = DRY_RUN_VARIANTS[variant]
    text = _template(world)
    assert DryRunCheck.parse(text, 0, _expect(world)).ok  # the unedited fixture passes
    if edit is not None:
        text = edit(text)
        assert text != _template(world), "the edit must change the fixture"

    check = DryRunCheck.parse(text, code, _expect(world, **changes))

    token = next(token for token in deploy_apply.TOKENS if variant.startswith(token))
    assert [found for found, _detail in check.failures] == [token], check.failures


def test_dry_run_fixture_has_the_production_shape(world, tmp_path):
    """LEARNED (2026-10-17): fixtures copy the real format. The real dry_run_report,
    with only the bridge subprocess faked by the real 2026-10-18 lines, passes the
    same checks with the same evidence lines as the fixture."""
    real_lines = _BRIDGE_LINES.format(
        pin12=supervisor.REVIEWED_BRIDGE_SHA256[:12],
        pin=supervisor.REVIEWED_BRIDGE_SHA256,
        fenced="INFO fenced identities = none",
    ).splitlines()

    def preflight(_session_date, **_kwargs):
        lines = [line.removeprefix("bridge       ") for line in real_lines[1:]]
        return 0, "".join(f"22:10:01 ET  {line}\n" for line in lines)

    report = supervisor.dry_run_report(
        session_date=NEXT_SESSION,
        now=datetime(2026, 10, 24, 23, 0, tzinfo=timezone.utc),
        user_paths=UserPaths(home=tmp_path / "dry-home"),
        unsupervised=list,
        database=world.db,
        registry=list,
        preflight=preflight,
        heartbeat_present=lambda: True,
    )
    expect = _expect(world, pin=supervisor.REVIEWED_BRIDGE_SHA256)
    real = DryRunCheck.parse(report, 0, expect)
    assert real.ok, real.failures
    fixture = DryRunCheck.parse(
        _template(world).replace(PIN_V1, expect.pin).replace(PIN_V1[:12], expect.pin[:12]), 0, expect
    )
    assert fixture.ok and fixture.evidence == real.evidence
    assert len(real.evidence) == 9


# --- P2-1: deploys.log --------------------------------------------------------------------------------------


def test_deploys_log_append_only_private_refuses_symlink(tmp_path, monkeypatch):
    path = tmp_path / "logs" / "deploys.log"
    path.parent.mkdir()
    path.write_text("2026-10-18T10:50:10+03:00 DEPLOY P1-B 9558442 PREV_SHA=x pin=y result=PASS\n")
    path.chmod(0o644)  # as `echo >>` left it
    writes, syncs = [], []
    real_write, real_fsync = os.write, os.fsync
    monkeypatch.setattr(os, "write", lambda fd, data: writes.append(data) or real_write(fd, data))
    monkeypatch.setattr(os, "fsync", lambda fd: syncs.append(fd) or real_fsync(fd))
    log = DeploysLog(path)

    log.append("one")
    log.append("two")

    assert path.read_text().splitlines()[1:] == ["one", "two"]
    assert path.read_text().startswith("2026-10-18T10:50:10+03:00 DEPLOY P1-B")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert writes == [b"one\n", b"two\n"] and len(syncs) == 2
    with pytest.raises(ValueError):
        log.append("a\nb")
    target = tmp_path / "target.log"
    target.write_text("keep\n")
    link = tmp_path / "logs" / "link.log"
    link.symlink_to(target)
    assert "symbolic link" in DeploysLog(link).problem()
    with pytest.raises(DeploysLogError):
        DeploysLog(link).append("x")
    with pytest.raises(DeploysLogError):
        DeploysLog(link).lines()
    assert target.read_text() == "keep\n"
    directory = tmp_path / "logs" / "dir.log"
    directory.mkdir()
    assert DeploysLog(directory).problem() is not None
    with pytest.raises(DeploysLogError):
        DeploysLog(directory).append("x")
    assert DeploysLog(tmp_path / "missing" / "deploys.log").problem() is not None
    assert DeploysLog(tmp_path / "logs" / "new.log").problem() is None


# --- P2-2: the stop waiter -------------------------------------------------------------------------------------


def _stopped_world(world: World) -> None:
    world.robot.stop()


@pytest.mark.parametrize(
    "condition, setup, phrase",
    # ids without spaces: the temporary repository path holds the test id, and a
    # path with a space cannot match the supervisor's own ps patterns.
    ids=["supervisor", "orphan", "bridge", "lock", "stop_line", "ps_failed"],
    argvalues=[
        ("supervisor", lambda w: w.robot.children.append((900, 1, f"{w.robot.python} {w.robot.control} run")),
         "supervisor still running: pid 900"),
        ("orphan", lambda w: w.robot.children.append((901, 1, f"{w.robot.python} -m trading.operations.heartbeat")),
         "trading process(es) still running: pid 901"),
        ("bridge", lambda w: w.robot.children.append((902, 1, f"{w.robot.python} scripts_local/paper_bridge.py")),
         "trading process(es) still running: pid 902"),
        ("lock", lambda w: w.robot.hold_lock(), "supervisor.lock is held"),
        ("stop line", None, "no SUPERVISOR_STOP reason=SIGTERM line since"),
        ("ps failed", lambda w: setattr(w.robot, "base_table", []), "the process table is empty"),
    ],
)
def test_stop_wait_needs_no_supervisor_no_orphans_free_lock_and_stop_line(world, condition, setup, phrase):
    operator = world.operator()
    world.robot.stop()  # stamped before `since`: an old stop line never counts
    world.clock.sleep(5)
    since = world.clock()
    if setup is not None:
        setup(world)
    if condition != "stop line":
        world.robot.write("SUPERVISOR_STOP reason=SIGTERM")

    with pytest.raises(DeployFailure) as failure:
        operator.wait_stopped(since, require_stop_line=True)

    assert failure.value.token == "stop_wait_timeout"
    assert phrase in failure.value.detail
    assert world.clock() >= since + timedelta(seconds=75)
    # Every condition met: done at once.
    world.robot.children = []
    world.robot.base_table = [(1, 0, "/sbin/launchd")]
    world.robot.release_lock()
    world.robot.write("SUPERVISOR_STOP reason=SIGTERM")
    operator.wait_stopped(since, require_stop_line=True)
    assert "OK stopped" in world.output


def test_stop_wait_reads_an_o_rdonly_lock_probe_without_creating_the_file(tmp_path):
    lock = tmp_path / "supervisor.lock"
    assert deploy_apply.lock_is_free(lock) is True and not lock.exists()
    descriptor = _hold(lock)
    try:
        lock.chmod(0o400)  # a read-only descriptor suffices for the probe
        assert deploy_apply.lock_is_free(lock) is False
    finally:
        os.close(descriptor)
    assert deploy_apply.lock_is_free(lock) is True


def test_stop_wait_timeout_halts_before_ff(world):
    world.live.never_stop = True

    code = world.apply()

    assert code == deploy_apply.EXIT_HALTED, world.output
    assert world.live_head() == world.scenario.base
    assert world.live.verbs == ["shadow agent disable"]
    _token_lines(world, "stop_wait_timeout")
    assert world.deploys()[-1].split(" ", 1)[1] == (
        f"DEPLOY P2 {world.short_head} PREV_SHA={world.scenario.base} pin={PIN_V2[:12]}"
        " result=HALTED reason=stop_wait_timeout"
    )
    supervisor_pid = world.robot.children[0][0]
    assert f"  pid {supervisor_pid} ppid 1: {world.robot.python} {world.robot.control} run" in world.output
    assert (
        f"  ( cd {world.repo}/backend && {world.repo}/backend/.venv/bin/python"
        f" {world.repo}/backend/autotraderctl.py shadow agent enable )"
    ) in world.output
    assert world.summary() == "deploy apply: HALTED reason=stop_wait_timeout (exit 4; the robot is disabled)"
    assert "STEP git -C" not in world.output


def test_disable_timeout_error_still_waits(world):
    world.live.disable_code = 1
    world.live.stop_after = 30.0

    code = world.apply()

    assert code == deploy_apply.EXIT_OK, world.output
    assert (
        "note: shadow agent disable exited 1 (error: unable to control the LaunchAgent);"
        " waiting on what is observed"
    ) in world.output
    assert world.live_head() == world.head


# --- P2-2: the start waiter ----------------------------------------------------------------------------------------


def _start_after(world: World, shape: StartShape | None = None, seconds: float = 2.0) -> datetime:
    """Stop the running robot, then, like an enable, start it ``seconds`` later."""
    world.robot.stop()
    world.clock.sleep(10)
    since = world.clock()
    world.paths.launch_agent_file.write_bytes(
        LaunchdManager(
            user_paths=world.paths, repository_root=(shape.plist_root if shape and shape.plist_root else world.repo)
        ).serialized_payload()
    )
    world.launchctl.loaded = True
    world.clock.after(seconds, lambda: world.robot.start(shape))
    return since


def _wait_started(world: World, since: datetime, operator: DeployOperator | None = None) -> None:
    (operator or world.operator()).wait_started(
        since, expected_head=world.live_head(), session_date=NEXT_SESSION
    )


def test_a_clean_start_passes_the_start_waiter(world):
    since = _start_after(world)

    _wait_started(world, since)

    assert "OK started: one supervisor" in world.output


def test_start_line_before_enable_time_is_not_accepted(world):
    # A supervisor (and its children) that started before the enable, still running:
    # its lines are older than `since`, so they prove nothing about this start.
    world.clock.sleep(30)
    since = world.clock()
    assert all(line.at < since for line in deploy_apply.supervisor_lines(world.paths.log_directory, START))

    with pytest.raises(DeployFailure) as failure:
        _wait_started(world, since)

    assert failure.value.token == "post_start_timeout"
    assert "no SUPERVISOR_START line" in failure.value.detail
    assert deploy_apply.supervisor_lines(world.paths.log_directory, since) == []


def test_the_previous_supervisors_stop_in_the_enable_second_is_not_the_new_ones(world):
    world.robot.stop()  # the same second as the enable: stamped == since
    since = world.clock()
    world.clock.after(2, lambda: world.robot.start())
    world.paths.launch_agent_file.write_bytes(
        LaunchdManager(user_paths=world.paths, repository_root=world.repo).serialized_payload()
    )

    _wait_started(world, since)

    assert "OK started" in world.output
    # A stop after the new start is the new supervisor's.
    since = world.clock()
    world.robot.stop()
    world.clock.after(2, lambda: world.robot.start())
    world.clock.after(3, lambda: world.robot.write("SUPERVISOR_STOP reason=SIGTERM"))
    with pytest.raises(DeployFailure) as failure:
        _wait_started(world, since)
    assert failure.value.token == "post_start_no_start" and "the supervisor stopped" in failure.value.detail


def test_preflight_warning_found_in_rotated_archive(world):
    """Rehearsal finding L3-A: written before the rotation, the warning is only in
    supervisor.log.<date>; the new file starts with the carried lines."""
    boundary = latest_rotation_boundary(START).date()
    world.robot.log.with_name(f"supervisor.log.{boundary.isoformat()}").unlink()
    since = _start_after(
        world, StartShape(rotate=True, preflight=("PREFLIGHT_WARNING heartbeat not configured (no Keychain item)",))
    )

    with pytest.raises(DeployFailure) as failure:
        _wait_started(world, since)

    assert failure.value.token == "post_start_preflight_warning"
    assert "PREFLIGHT_WARNING" not in world.robot.log.read_text()
    assert "LOG_ROTATED" in world.robot.log.read_text().splitlines()[-1]


def test_rotation_due_at_start_needs_its_log_rotated_line(world):
    boundary = latest_rotation_boundary(START).date()
    world.robot.log.with_name(f"supervisor.log.{boundary.isoformat()}").unlink()
    since = _start_after(world, StartShape(rotate=True))
    _wait_started(world, since)  # the carried lines and the archive count once
    assert "OK started" in world.output

    failed = _start_after(world, StartShape())
    world.robot.log.with_name(f"supervisor.log.{latest_rotation_boundary(failed).date()}").unlink()
    with pytest.raises(DeployFailure) as failure:
        _wait_started(world, failed)
    assert failure.value.token == "post_start_timeout"
    assert "LOG_ROTATED" in failure.value.detail


@pytest.mark.parametrize(
    "wall, rotated, accepted",
    [(time(18, 31), 1, True), (time(18, 31), 2, True), (time(18, 31), 3, False), (time(18, 40), 2, False)],
)
def test_two_log_rotated_lines_accepted_after_1830_start(world, wall, rotated, accepted):
    world.clock.now = datetime.combine(date(2026, 10, 26), wall, tzinfo=MARKET_TIMEZONE)
    for day in (date(2026, 10, 25), date(2026, 10, 26)):
        archive = world.robot.log.with_name(f"supervisor.log.{day.isoformat()}")
        archive.unlink(missing_ok=True)
    since = _start_after(world, StartShape(session_date=date(2026, 10, 27), rotate=True, extra_rotated=rotated - 1))
    operator = world.operator()
    run = lambda: operator.wait_started(since, expected_head=world.live_head(), session_date=date(2026, 10, 27))

    if accepted:
        run()
        assert "OK started" in world.output
    else:
        with pytest.raises(DeployFailure) as failure:
            run()
        assert failure.value.token == "post_start_rotation_failed"
        assert f"{rotated} LOG_ROTATED lines since the start" in failure.value.detail


@pytest.mark.parametrize(
    "line, token",
    ids=["launch_refused", "rotation_failed", "preflight_warning", "session_wait_date", "stopped", "restarted"],
    argvalues=[
        ("LAUNCH_REFUSED registry code=78 reason=size_table_mismatch=X", "post_start_launch_refused"),
        ("LOG_ROTATION_FAILED file=telegram.log error=PermissionError", "post_start_rotation_failed"),
        ("PREFLIGHT_WARNING unsupervised pid=600 (stop it: kill 600) cmd=python x", "post_start_preflight_warning"),
        ("SESSION_WAIT date=2026-10-27 until=2026-10-27T11:55:00+00:00", "post_start_session_wait"),
        ("SUPERVISOR_STOP reason=supervisor_error", "post_start_no_start"),
        ("SUPERVISOR_START pid=1 mode=daily", "post_start_no_start"),
    ],
)
def test_each_loud_start_line_has_its_token(world, line, token):
    since = _start_after(world)
    world.clock.after(3, lambda: world.robot.write(line))

    with pytest.raises(DeployFailure) as failure:
        _wait_started(world, since)

    assert failure.value.token == token


def test_heartbeat_child_must_descend_from_new_supervisor(world):
    old_supervisor = 3999
    world.robot.base_table.append((old_supervisor, 1, "/usr/bin/python3 /tmp/old/autotraderctl.py sleep"))
    since = _start_after(world, StartShape(heartbeat_parent=old_supervisor))

    with pytest.raises(DeployFailure) as failure:
        _wait_started(world, since)

    assert failure.value.token == "post_start_heartbeat_missing"
    assert "does not descend from the new supervisor" in failure.value.detail


def test_plist_wd_must_be_live_backend(world):
    since = _start_after(world, StartShape(plist_root=world.root / "cand-wt"))

    with pytest.raises(DeployFailure) as failure:
        _wait_started(world, since)

    assert failure.value.token == "post_start_plist"
    assert f"plist WorkingDirectory '{world.root}/cand-wt/backend' is not {world.repo}/backend" in failure.value.detail
    # A plist with the right paths but not loaded is refused too.
    since = _start_after(world)
    world.clock.after(1, lambda: setattr(world.launchctl, "loaded", False))
    with pytest.raises(DeployFailure) as failure:
        _wait_started(world, since)
    assert failure.value.token == "post_start_plist" and "not loaded" in failure.value.detail


def test_post_start_head_must_be_the_expected_head(world):
    since = _start_after(world)

    with pytest.raises(DeployFailure) as failure:
        world.operator().wait_started(since, expected_head=world.head, session_date=NEXT_SESSION)

    assert failure.value.token == "post_start_head"


# --- P2-3: expected-migration mode ------------------------------------------------------------------------------


def test_expect_migration_passes_only_exact_pending(tmp_path):
    previous = database_at(tmp_path / "previous.db", PREVIOUS)
    older = database_at(tmp_path / "older.db", PREVIOUS - 1)
    current = migrate_database(tmp_path / "current.db")
    now = datetime(2026, 10, 25, 2, 0, tzinfo=ISRAEL)

    ok = deploy_check.check_schema(previous, now=now, expect_migration=LATEST)
    assert ok.status == deploy_check.OK
    assert ok.notes == (f"{previous}: schema v{PREVIOUS}, this code needs v{LATEST} (pending: {LATEST});"
                        f" pending ({LATEST},) as expected",)
    for database, expected in ((older, LATEST), (current, LATEST), (older, PREVIOUS), (previous, PREVIOUS)):
        refused = deploy_check.check_schema(database, now=now, expect_migration=expected)
        assert refused.status == deploy_check.REFUSE
        assert refused.detail.startswith("migration_unexpected: ")
    missing = deploy_check.check_schema(tmp_path / "absent.db", now=now, expect_migration=LATEST)
    assert missing.status == deploy_check.REFUSE and not (tmp_path / "absent.db").exists()
    # Without the flag nothing may be pending, as before.
    assert deploy_check.check_schema(previous, now=now).status == deploy_check.REFUSE


def test_migration_without_verdict_token_refuses(tmp_path):
    for migration, status in ((None, "REFUSE"), (LATEST, "OK"), (LATEST + 1, "REFUSE")):
        world = _make_world(tmp_path / f"m{migration}", migration=migration)
        try:
            git = deploy_check.Git(world.repo)
            result, matched = deploy_check.match_verdict(
                git, candidate="cand", head=world.head, ledger_glob=deploy_check.DEFAULT_LEDGER_GLOB,
                scope=("P2",), expect_migration=LATEST,
            )
            assert result.status == status
            if status == "REFUSE":
                assert result.detail.startswith("verdict_migration_missing: ") and matched == ()
            else:
                assert [line.sha for line in matched] == [world.scenario.reviewed[:7]]
            # Without --expect-migration the token is not needed.
            plain, _ = deploy_check.match_verdict(
                git, candidate="cand", head=world.head, ledger_glob=deploy_check.DEFAULT_LEDGER_GLOB, scope=("P2",)
            )
            assert plain.status == "OK"
        finally:
            world.robot.release_lock()
    # The read-only CLI takes the flag too.
    world = _make_world(tmp_path / "cli")
    try:
        _database_at(world, PREVIOUS)
        out = io.StringIO()
        request = deploy_check.DeployCheckRequest(
            candidate="cand", repo=world.repo, db_path=world.db, scope=("P2",), now=START,
            expect_migration=LATEST,
        )
        assert deploy_check.run_deploy_check(request, sntp=lambda: GOOD_SNTP, out=out) == 78
        assert "REFUSE verdict: verdict_migration_missing: " in out.getvalue()
        assert "OK schema" in out.getvalue()
        assert f"expect-migration={LATEST}" in out.getvalue().splitlines()[0]
    finally:
        world.robot.release_lock()


def test_backup_line_parser_matches_db_admin_output(tmp_path):
    (tmp_path / "data").mkdir()
    database = database_at(tmp_path / "data" / "autotrader.db", PREVIOUS)
    out, err = io.StringIO(), io.StringIO()

    assert run_migrate(database, now=START, out=out, err=err) == 0

    backup, migrated = deploy_apply.parse_migrate_output(out.getvalue())
    assert migrated == (PREVIOUS, LATEST)
    assert backup is not None and Path(backup).is_file()
    assert Path(backup).parent == database.parent / "backups"
    assert f".v{PREVIOUS}-to-v{LATEST}.db" in backup
    assert deploy_apply.parse_migrate_output("nothing to migrate: the schema is current\n") == (None, None)


def _migration_world(tmp_path: Path) -> World:
    world = _make_world(tmp_path, migration=LATEST)
    _database_at(world, PREVIOUS)
    return world


def test_migration_deploy_passes_with_the_schema_on_both_lines(tmp_path):
    world = _migration_world(tmp_path)
    try:
        code = world.apply(expect_migration=LATEST)
    finally:
        world.robot.release_lock()

    assert code == deploy_apply.EXIT_OK, world.output
    prev, deploy = world.deploys()
    assert prev.endswith(f"PREV_SHA={world.scenario.base} schema=v{PREVIOUS}")
    assert deploy.endswith(f"result=PASS schema=v{LATEST}")
    assert world.live.verbs == [
        "shadow agent disable", "db migrate --db-path", "db status --db-path", "run --dry-run --date",
        "shadow agent enable",
    ]
    assert inspect_schema(world.db).current
    assert f"OK migrated v{PREVIOUS} -> v{LATEST}; backup " in world.output


def test_migrated_failure_halts_disabled_prints_restore_never_resets(tmp_path, monkeypatch):
    world = _migration_world(tmp_path)
    world.live.dry_run_edit = lambda text: text + "!!! injected after the migration\n"
    resets: list[str] = []
    monkeypatch.setattr(GitWriter, "reset_keep", lambda self, sha: resets.append(sha))
    try:
        code = world.apply(expect_migration=LATEST)
    finally:
        world.robot.release_lock()

    assert code == deploy_apply.EXIT_HALTED, world.output
    assert resets == [] and world.live_head() == world.head  # never reset after a migration
    assert not world.robot.running and not world.launchctl.loaded
    assert world.live.verbs == [
        "shadow agent disable", "db migrate --db-path", "db status --db-path", "run --dry-run --date",
        "shadow agent disable",
    ]
    _token_lines(world, "dry_run_bang")
    halted = world.deploys()[-1]
    backup = halted.split(" backup=", 1)[1]
    assert Path(backup).is_file() and f".v{PREVIOUS}-to-v{LATEST}.db" in backup
    assert halted.split(" ", 1)[1] == (
        f"DEPLOY P2 {world.short_head} PREV_SHA={world.scenario.base} pin={PIN_V2[:12]}"
        f" result=HALTED reason=dry_run_bang backup={backup}"
    )
    for expected in (
        "RESTORE (never run by this command); one line at a time, each only after the one before passed:",
        "  1. pgrep -fl autotraderctl.py || echo none",
        f"  2. [ -e {world.db} ] && mv {world.db} {world.db}.v{LATEST}-failed-",
        f"  3. cp {backup} {world.db}",
        f"  4. git -C {world.repo} reset --keep {world.scenario.base}",
        f"     Expect: `bridge       reviewed (sha256 {PIN_V1[:12]})` and `database     schema v{PREVIOUS} current`,"
        " no !!!",
    ):
        assert any(line.startswith(expected) for line in world.output.splitlines()), expected
    assert world.summary() == "deploy apply: HALTED reason=dry_run_bang (exit 4; the robot is disabled)"
    assert inspect_schema(world.db).current  # the restore is the owner's


# --- P2-4: undo --------------------------------------------------------------------------------------------------


def test_auto_undo_code_only_writes_rollback_and_reenables_with_old_pin(world):
    world.live.starts = [
        StartShape(preflight=("PREFLIGHT_WARNING unsupervised pid=600 (stop it: kill 600) cmd=python x",)),
        StartShape(),
    ]

    code = world.apply()

    assert code == deploy_apply.EXIT_ROLLED_BACK, world.output
    assert world.live_head() == world.scenario.base
    _token_lines(world, "post_start_preflight_warning")
    assert not any("result=PASS" in line for line in world.deploys())
    assert world.deploys()[-1].split(" ", 1)[1] == (
        f"DEPLOY rollback to {world.scenario.base} result=ROLLBACK reason=post_start_preflight_warning"
    )
    assert world.live.verbs == [
        "shadow agent disable", "run --dry-run --date", "shadow agent enable",
        "shadow agent disable", "run --dry-run --date", "shadow agent enable",
    ]
    assert f"  | bridge       reviewed (sha256 {PIN_V1[:12]})" in world.output.splitlines()
    assert world.robot.running and world.launchctl.loaded and world.robot.starts == 3
    assert world.summary() == (
        f"deploy apply: ROLLED BACK reason=post_start_preflight_warning (exit 3; {world.scenario.base[:7]} runs again)"
    )


def _bang_in_dry_run(world: World) -> None:
    world.live.dry_run_edit = lambda text: text + "!!! x\n"


@pytest.mark.parametrize("token, setup", [("dry_run_bang", "_bang_in_dry_run"), ("tree_dirty_after_ff", "_dirty_after_ff")])
def test_auto_undo_after_each_post_ff_failure(world, token, setup):
    globals()[setup](world)

    code = world.apply()

    assert code == deploy_apply.EXIT_ROLLED_BACK, world.output
    _token_lines(world, token)
    assert world.live_head() == world.scenario.base


def _dirty_after_ff(world: World) -> None:
    class DirtyWriter(GitWriter):
        def merge_ff_only(self, sha):
            super().merge_ff_only(sha)
            (world.repo / "backend" / "stray.txt").write_text("left behind\n")

        def reset_keep(self, sha):
            (world.repo / "backend" / "stray.txt").unlink(missing_ok=True)
            super().reset_keep(sha)

    world.writer_factory = DirtyWriter


def test_reset_noop_detected(world):
    class NoopReset(GitWriter):
        def reset_keep(self, sha):
            return None  # an empty PREV_SHA's silent no-op

    world.writer_factory = NoopReset
    world.live.dry_run_edit = lambda text: text + "!!! x\n"

    code = world.apply()

    assert code == deploy_apply.EXIT_UNDO_FAILED, world.output
    assert world.live_head() == world.head
    assert f"FAIL undo_reset_noop: HEAD is {world.head} after reset --keep {world.scenario.base}" in world.output
    assert world.deploys()[-1].split(" ", 1)[1] == (
        f"DEPLOY rollback to {world.scenario.base} result=UNDO_FAILED reason=undo_reset_noop"
    )
    assert not world.launchctl.loaded and not world.robot.running
    assert f"  git -C {world.repo} reset --keep {world.scenario.base}" in world.output.splitlines()
    assert world.summary() == (
        "deploy apply: UNDO FAILED reason=undo_reset_noop after dry_run_bang (exit 5; the robot is disabled)"
    )


def test_undo_failure_leaves_disabled_exit_5(world):
    # The deploy fails at its start; the undo's own start fails too.
    world.live.starts = [
        StartShape(preflight=("PREFLIGHT_WARNING heartbeat not configured",)),
        StartShape(preflight=("PREFLIGHT_WARNING heartbeat not configured",)),
    ]

    code = world.apply()

    assert code == deploy_apply.EXIT_UNDO_FAILED, world.output
    assert world.live_head() == world.scenario.base
    assert "FAIL undo_post_start: post_start_preflight_warning: " in world.output
    assert not world.launchctl.loaded and not world.robot.running
    assert world.live.verbs[-1] == "shadow agent disable"
    assert "MANUAL ROLLBACK (runbook 6.3), only when flat; run one line at a time:" in world.output
    assert (
        f"  echo \"$(date -Iseconds) DEPLOY rollback to {world.scenario.base} result=ROLLBACK"
        f" reason=post_start_preflight_warning\" >> {world.deploys_log}"
    ) in world.output.splitlines()
    assert world.deploys()[-1].endswith("result=UNDO_FAILED reason=undo_post_start")
    assert world.summary().startswith("deploy apply: UNDO FAILED reason=undo_post_start after post_start_preflight")


def _deployed(world: World, *, schema_before: int = LATEST, schema_after: int = LATEST) -> None:
    """The live checkout at the candidate after a deploy that wrote its two lines."""
    _git(world.repo, "merge", "-q", "--ff-only", world.head)
    world.deploys_log.write_text(
        f"2026-10-25T02:00:00+03:00 P2 PREV_SHA={world.scenario.base} schema=v{schema_before}\n"
        f"2026-10-25T02:03:00+03:00 DEPLOY P2 {world.short_head} PREV_SHA={world.scenario.base}"
        f" pin={PIN_V2[:12]} result=PASS schema=v{schema_after}\n"
    )


def test_manual_undo_rolls_back_to_the_prev_of_the_deploy_line(world):
    _deployed(world)

    code = world.undo(world.scenario.base, reason="owner_saw_errors")

    assert code == deploy_apply.EXIT_ROLLED_BACK, world.output
    assert world.live_head() == world.scenario.base
    assert world.deploys()[-1].split(" ", 1)[1] == (
        f"DEPLOY rollback to {world.scenario.base} result=ROLLBACK reason=owner_saw_errors"
    )
    assert world.summary() == "deploy undo: ROLLED BACK reason=owner_saw_errors (exit 3)"
    assert world.robot.running and world.launchctl.loaded


def test_undo_refuses_db_newer(world):
    _deployed(world, schema_before=PREVIOUS, schema_after=LATEST)
    backups = world.db.parent / "backups"
    backups.mkdir()
    backup = backups / f"autotrader.20261025T060000.000000Z.v{PREVIOUS}-to-v{LATEST}.db"
    backup.write_text("")

    code = world.undo(world.scenario.base)

    assert code == deploy_apply.EXIT_REFUSED
    assert _refused(world) == {"undo_needs_db_restore"}
    assert f"  3. cp {backup} {world.db}" in world.output.splitlines()
    assert world.live_head() == world.head and world.live.calls == []
    # A line written before P2 (no schema=) is never guessed.
    world.out.truncate(0)
    world.out.seek(0)
    world.deploys_log.write_text(
        f"2026-10-18T10:50:10+03:00 DEPLOY P1-B {world.short_head} PREV_SHA={world.scenario.base}"
        f" pin={PIN_V2[:12]} result=PASS\n"
    )
    assert world.undo(world.scenario.base) == deploy_apply.EXIT_REFUSED
    assert _refused(world) == {"undo_schema_unknown"}


def test_undo_refuses_not_flat(world):
    _deployed(world)
    state = world.paths.data_directory / "bridge"
    state.mkdir()
    (state / "2026-10-22.json").write_text(json.dumps({"submitted": {}}))
    (state / "2026-10-23.json").write_text(
        json.dumps({"submitted": {"d1": {"symbol": "AAPL", "status": "open", "filled": 100, "exit_filled": 0}}})
    )

    assert world.undo(world.scenario.base) == deploy_apply.EXIT_REFUSED
    assert _refused(world) == {"undo_not_flat"}
    assert "2026-10-23.json: AAPL open 100 sh" in world.output
    for submitted in (
        {"d1": {"symbol": "MSFT", "status": "sent", "filled": 0, "exit_filled": 0}},  # a working entry order
        {"d1": {"symbol": "MSFT", "status": "needs_attention", "filled": 50, "exit_filled": 40}},
        "not a map",
    ):
        (state / "2026-10-23.json").write_text(json.dumps({"submitted": submitted}))
        assert deploy_apply._not_flat(state) is not None, submitted
    (state / "2026-10-23.json").write_text(
        json.dumps({"submitted": {"d1": {"symbol": "AAPL", "status": "closed", "filled": 100, "exit_filled": 100}}})
    )
    assert deploy_apply._not_flat(state) is None


def test_undo_refuses_prev_mismatch(world):
    _deployed(world)

    assert world.undo(world.scenario.reviewed) == deploy_apply.EXIT_REFUSED
    assert _refused(world) == {"undo_prev_mismatch"}
    assert f"is not the PREV_SHA {world.scenario.base} of the DEPLOY line for HEAD" in world.output
    # No DEPLOY line for HEAD at all.
    world.deploys_log.write_text("")
    world.out.truncate(0)
    world.out.seek(0)
    assert world.undo(world.scenario.base) == deploy_apply.EXIT_REFUSED
    assert _refused(world) == {"undo_prev_mismatch"}
    assert world.live_head() == world.head and world.live.calls == []


def test_undo_refuses_outside_the_window_and_without_a_terminal(world):
    _deployed(world)
    world.clock.now = datetime(2026, 10, 26, 7, 0, tzinfo=MARKET_TIMEZONE)
    assert world.undo(world.scenario.base) == deploy_apply.EXIT_REFUSED
    assert _refused(world) == {"start_cutoff"}
    world.out.truncate(0)
    world.out.seek(0)
    environment = world.env(tty=False)
    request = DeployUndoRequest(repo=world.repo, to=world.scenario.base, reason="x_y")
    assert deploy_apply.run_deploy_undo(request, environment) == deploy_apply.EXIT_REFUSED
    assert _refused(world) == {"not_interactive"}


def test_the_deploy_line_parser_reads_every_written_shape():
    base = "a" * 40
    for line in (
        f"2026-10-18T10:50:10+03:00 DEPLOY P1-B 9558442 PREV_SHA={base} pin=7d313fd075d2 result=PASS",
        f"2026-10-16T01:48:33+03:00 DEPLOY R-B v7a 664c93b PREV_SHA={base} pin=9790eb3c1e81 result=PASS",
        f"2026-10-16T01:44:15+03:00 DEPLOY ops-p1b@9558442 PREV_SHA={base} pin=7d313fd075d2 result=PASS",
        f"2026-10-25T02:03:00+03:00 DEPLOY P2 9558442 PREV_SHA={base} pin=7d313fd075d2 result=PASS schema=v16",
    ):
        found = deploy_apply._DEPLOY_PASS.search(line)
        assert found and found.group("short") in {"9558442", "664c93b"} and found.group("prev") == base
    assert deploy_apply._DEPLOY_PASS.search(f"x DEPLOY rollback to {base} result=ROLLBACK reason=y") is None
    assert deploy_apply._DEPLOY_PASS.search(
        f"x DEPLOY P2 9558442 PREV_SHA={base} pin=7d313fd075d2 result=HALTED reason=y"
    ) is None
    assert deploy_apply._prev_schema([f"2026-10-25T02:00:00+03:00 P2 PREV_SHA={base} schema=v15"], base) == 15
    assert deploy_apply._prev_schema([f"2026-10-18T10:45:00+03:00 P1-B PREV_SHA={base}"], base) is None


# --- the command line ----------------------------------------------------------------------------------------------


def _apply_argv(**changes) -> list[str]:
    options = {
        "--candidate": "cand",
        "--repo": "/repo",
        "--expect-parent": "a" * 40,
        "--expect-pin": "b" * 64,
        "--scope": "P2",
    }
    options.update(changes)
    argv = ["deploy", "apply"]
    for key, value in options.items():
        if value is not None:
            argv += [key, value]
    return argv


@pytest.mark.parametrize(
    "changes",
    [
        {"--expect-parent": "a" * 7},
        {"--expect-parent": "A" * 40},
        {"--expect-pin": "b" * 63},
        {"--expect-pin": "B" * 64},
        {"--candidate": "--upload-pack=x"},
        {"--scope": " , "},
        {"--expect-fenced": "ORB_V1"},
        {"--label": "two words"},
        {"--expect-migration": "0"},
    ],
)
def test_the_cli_refuses_bad_apply_arguments(capsys, changes):
    with pytest.raises(SystemExit) as error:
        main(_apply_argv(**{key: value for key, value in changes.items()}))

    assert error.value.code == 2
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize(
    "argv",
    [
        ["deploy", "undo", "--repo", "/repo", "--to", "abc1234", "--reason", "x_y"],
        ["deploy", "undo", "--repo", "/repo", "--to", "a" * 40, "--reason", "two words"],
        ["deploy", "undo", "--repo", "/repo", "--to", "a" * 40, "--reason", "Upper"],
    ],
)
def test_the_cli_refuses_bad_undo_arguments(capsys, argv):
    with pytest.raises(SystemExit) as error:
        main(argv)

    assert error.value.code == 2
    assert capsys.readouterr().out == ""


def test_the_cli_apply_and_undo_refuse_without_a_terminal(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", FakeTTY("", tty=False))

    assert main(_apply_argv(**{"--repo": str(tmp_path), "--expect-fenced": "ORB_V2", "--label": "P2"})) == 78
    out = capsys.readouterr().out
    assert "REFUSE not_interactive: stdin is not a terminal: deploy apply is owner-run only" in out
    assert out.splitlines()[-1] == "deploy apply: REFUSED not_interactive (exit 78; nothing changed)"
    assert main(["deploy", "undo", "--repo", str(tmp_path), "--to", "a" * 40, "--reason", "x_y"]) == 78
    assert capsys.readouterr().out.splitlines()[-1] == "deploy undo: REFUSED not_interactive (exit 78; nothing changed)"


def test_the_cli_parses_apply_and_undo():
    from trading.cli import _parser

    arguments = _parser().parse_args(
        _apply_argv(**{"--expect-migration": "17", "--expect-fenced": "ORB_V2", "--label": "v7c", "--ledger": "g"})
        + ["--db-path", "/x.db"]
    )
    assert (arguments.action, arguments.candidate, arguments.expect_parent, arguments.expect_pin) == (
        "apply", "cand", "a" * 40, "b" * 64,
    )
    assert (arguments.expect_migration, arguments.expect_fenced, arguments.label, arguments.ledger) == (
        17, "ORB_V2", "v7c", "g",
    )
    assert arguments.db_path == Path("/x.db")
    undo = _parser().parse_args(["deploy", "undo", "--repo", "/r", "--to", "a" * 40, "--reason", "dry_run_bang"])
    assert (undo.action, undo.repo, undo.to, undo.reason) == ("undo", Path("/r"), "a" * 40, "dry_run_bang")
    check = _parser().parse_args(["deploy", "check", "--candidate", "c", "--repo", "/r", "--expect-migration", "17"])
    assert check.expect_migration == 17


# --- every other failure path, one token each ------------------------------------------------------


class _MergeFails(GitWriter):
    def merge_ff_only(self, sha):
        raise deploy_check.GitError("git merge failed: fatal: Not possible to fast-forward, aborting.")


def _merge_failed(world: World) -> None:
    world.writer_factory = _MergeFails


def _head_mismatch(world: World) -> None:
    reviewed = world.scenario.reviewed

    class WrongHead(GitWriter):
        def merge_ff_only(self, sha):
            super().merge_ff_only(reviewed)

    world.writer_factory = WrongHead


def _bridge_mismatch(world: World) -> None:
    bridge = world.repo / BRIDGE

    class EditedBridge(GitWriter):
        def merge_ff_only(self, sha):
            super().merge_ff_only(sha)
            bridge.write_text("print('edited after the fast-forward')\n")

        def reset_keep(self, sha):
            bridge.write_text(BRIDGE_V2)
            super().reset_keep(sha)

    world.writer_factory = EditedBridge


def _plist_elsewhere(world: World) -> None:
    world.live.starts = [StartShape(plist_root=world.root / "cand-wt"), StartShape()]


def _never_starts(world: World) -> None:
    world.live.starts = [None, StartShape()]


POST_FF_FAILURES = {
    "merge_failed": _merge_failed,
    "head_mismatch": _head_mismatch,
    "bridge_sha_mismatch": _bridge_mismatch,
    "tree_dirty_after_ff": _dirty_after_ff,
    "dry_run_bang": _bang_in_dry_run,
    "post_start_plist": _plist_elsewhere,
    "post_start_timeout": _never_starts,
}


@pytest.mark.parametrize("token", sorted(POST_FF_FAILURES))
def test_each_post_ff_failure_of_a_code_only_deploy_is_undone(world, token):
    POST_FF_FAILURES[token](world)

    code = world.apply()

    assert code == deploy_apply.EXIT_ROLLED_BACK, world.output
    _token_lines(world, token)
    assert world.live_head() == world.scenario.base
    assert world.robot.running and world.launchctl.loaded
    assert not any("result=PASS" in line for line in world.deploys())


@pytest.mark.parametrize(
    "token, setup",
    [
        ("migrate_failed", lambda w: setattr(w.live, "migrate_result", (1, "database  x\nschema    v15\n"))),
        ("migrate_unparsed", lambda w: setattr(w.live, "migrate_result", (0, "nothing to migrate\n"))),
        ("migrate_status_not_current", lambda w: setattr(w.live, "status_code", 75)),
    ],
)
def test_each_migrate_failure_halts_and_never_resets(tmp_path, token, setup):
    world = _migration_world(tmp_path)
    setup(world)
    try:
        code = world.apply(expect_migration=LATEST)
    finally:
        world.robot.release_lock()

    assert code == deploy_apply.EXIT_HALTED, world.output
    _token_lines(world, token)
    assert world.live_head() == world.head and not world.robot.running
    assert "RESTORE (never run by this command)" in world.output
    if token != "migrate_status_not_current":
        assert world.deploys()[-1].endswith(f"reason={token} backup=none")
        assert any("no backup line was read" in line for line in world.output.splitlines())


@pytest.mark.parametrize("error, token", [(KeyboardInterrupt, "interrupted"), (RuntimeError, "internal_error")])
def test_an_unexpected_error_halts_where_it_stood(world, error, token):
    def boom():
        raise error("injected")

    world.live.on_call["run --dry-run"] = boom

    code = world.apply()

    assert code == deploy_apply.EXIT_HALTED, world.output
    _token_lines(world, token)
    assert world.live_head() == world.head  # nothing more was run
    assert world.live.verbs == ["shadow agent disable", "run --dry-run --date"]
    assert "MANUAL ROLLBACK (runbook 6.3)" in world.output


def test_a_reset_keep_refusal_is_undo_reset_failed(world):
    feature = world.repo / "backend" / "trading" / "feature.py"
    world.live.on_call["run --dry-run"] = lambda: feature.write_text("FEATURE = 'edited by hand'\n")
    world.live.dry_run_edit = lambda text: text + "!!! x\n"

    code = world.apply()

    assert code == deploy_apply.EXIT_UNDO_FAILED, world.output
    assert "FAIL undo_reset_failed: git reset failed: " in world.output
    assert world.live_head() == world.head and feature.read_text() == "FEATURE = 'edited by hand'\n"
    assert world.deploys()[-1].endswith("result=UNDO_FAILED reason=undo_reset_failed")
    assert not world.robot.running and not world.launchctl.loaded


def test_an_undo_whose_stop_times_out_is_undo_stop_timeout(world):
    _deployed(world)
    world.live.never_stop = True

    code = world.undo(world.scenario.base)

    assert code == deploy_apply.EXIT_UNDO_FAILED, world.output
    assert "FAIL undo_stop_timeout: not stopped after 75 s" in world.output
    assert world.live_head() == world.head
    assert world.deploys()[-1].endswith("result=UNDO_FAILED reason=undo_stop_timeout")


def test_undo_to_an_invalid_prev_is_undo_prev_invalid(world):
    code = world.operator().undo_to("0" * 40, "x_y", PIN_V1, session_date=NEXT_SESSION)

    assert code == deploy_apply.EXIT_UNDO_FAILED
    assert "FAIL undo_prev_invalid: PREV_SHA 0000000000000000000000000000000000000000 is not a commit" in world.output
    assert world.live_head() == world.scenario.base


def test_an_enable_that_errors_is_noted_and_the_start_still_decides(world):
    world.live.enable_code = 1

    assert world.apply() == deploy_apply.EXIT_OK, world.output
    assert (
        "note: shadow agent enable exited 1 (error: unable to enable the LaunchAgent); waiting on what is observed"
    ) in world.output


def _plist_payload(world: World, **changes) -> bytes:
    payload = LaunchdManager(user_paths=world.paths, repository_root=world.repo).payload()
    payload.update(changes)
    import plistlib

    return plistlib.dumps(payload, fmt=plistlib.FMT_XML, sort_keys=True)


PLIST_PROBLEMS = {
    "missing": "is missing or not a regular file",
    "garbage": "unreadable",
    "arguments": "plist ProgramArguments[3:5]",
    "not_current": "is not the plist the live checkout writes",
    "print_fails": "launchctl print failed",
}


@pytest.mark.parametrize("case", sorted(PLIST_PROBLEMS))
def test_every_plist_problem_is_post_start_plist(world, case):
    since = _start_after(world)
    path = world.paths.launch_agent_file
    damages = {
        "missing": path.unlink,
        "garbage": lambda: path.write_bytes(b"not a plist"),
        "arguments": lambda: path.write_bytes(
            _plist_payload(
                world,
                ProgramArguments=["/usr/bin/caffeinate", "-i", "-s", "/x/python", "/x/autotraderctl.py", "run"],
            )
        ),
        "not_current": lambda: path.write_bytes(_plist_payload(world, ThrottleInterval=5)),
        "print_fails": lambda: None,
    }
    world.clock.after(1, damages[case])
    operator = world.operator()
    if case == "print_fails":

        def broken(command):
            raise OSError("launchctl unavailable")

        operator.env.launchctl = broken

    with pytest.raises(DeployFailure) as failure:
        operator.wait_started(since, expected_head=world.live_head(), session_date=NEXT_SESSION)

    assert failure.value.token == "post_start_plist"
    assert PLIST_PROBLEMS[case] in failure.value.detail


@pytest.mark.parametrize("case", ["another", "not_live", "once"])
def test_the_new_supervisor_must_be_the_only_one_and_the_live_checkouts(world, case):
    if case == "another":
        world.robot.base_table.append((950, 1, f"{world.robot.python} /other/backend/autotraderctl.py run"))
        since = _start_after(world)
        phrase = "another supervisor runs: pid 950"
    elif case == "not_live":
        world.robot.control = "/wt/backend/autotraderctl.py"
        since = _start_after(world)
        phrase = "is not the live checkout's supervisor"
    else:
        since = _start_after(world, StartShape(mode="once"))
        phrase = "not a daily start"

    with pytest.raises(DeployFailure) as failure:
        _wait_started(world, since)

    assert failure.value.token == "post_start_no_start"
    assert phrase in failure.value.detail


def test_a_child_that_never_appears_in_ps_times_out(world):
    since = _start_after(world)
    world.clock.after(3, lambda: world.robot.children.pop())  # the heartbeat dies

    with pytest.raises(DeployFailure) as failure:
        _wait_started(world, since)

    assert failure.value.token == "post_start_timeout"
    assert "heartbeat pid" in failure.value.detail and "in the process table" in failure.value.detail


def _undo_break_lock(world, held):
    held.append(_hold(world.paths.data_directory / deploy_apply.DEPLOY_LOCK_NAME))
    return "deploy_lock_busy", world.scenario.base


def _undo_break_live(world, held):
    world.own_root = world.repo
    return "runs_from_live_checkout", world.scenario.base


def _undo_break_prev(world, held):
    return "prev_sha_invalid", "0" * 40


def _undo_break_python(world, held):
    (world.repo / "backend" / ".venv" / "bin" / "python").unlink()
    return "live_python_missing", world.scenario.base


def _undo_break_log(world, held):
    target = world.root / "elsewhere.log"
    world.deploys_log.rename(target)
    world.deploys_log.symlink_to(target)
    return "deploys_log_unusable", world.scenario.base


def _undo_break_state(world, held):
    state = world.paths.data_directory / "bridge"
    state.mkdir()
    (world.root / "real.json").write_text(json.dumps({"submitted": {}}))
    (state / "2026-10-23.json").symlink_to(world.root / "real.json")
    return "undo_not_flat", world.scenario.base


UNDO_BREAKERS = {
    "deploy_lock_busy": _undo_break_lock,
    "runs_from_live_checkout": _undo_break_live,
    "prev_sha_invalid": _undo_break_prev,
    "live_python_missing": _undo_break_python,
    "deploys_log_unusable": _undo_break_log,
    "undo_not_flat": _undo_break_state,
}


@pytest.mark.parametrize("name", sorted(UNDO_BREAKERS))
def test_each_undo_refusal_changes_nothing(world, name):
    _deployed(world)
    held: list[int] = []
    token, to = UNDO_BREAKERS[name](world, held)
    try:
        code = world.undo(to)
    finally:
        for descriptor in held:
            os.close(descriptor)

    assert code == deploy_apply.EXIT_REFUSED, world.output
    assert token in _refused(world)
    assert world.live_head() == world.head and world.live.calls == [] and world.robot.table_reads == 0


def test_a_wrong_typed_sha_refuses_the_undo(world):
    _deployed(world)

    assert world.undo(world.scenario.base, typed=world.short_head + "\n") == deploy_apply.EXIT_REFUSED
    assert _refused(world) == {"confirm_mismatch"}
    assert world.live_head() == world.head and world.live.calls == []


def test_an_undo_whose_dry_run_fails_is_undo_dry_run(world):
    calls = itertools.count(1)

    def second_dry_run_bangs():
        if next(calls) == 2:  # the undo's own dry run, from the restored code
            world.live.dry_run_edit = lambda text: text + "!!! after the reset\n"

    world.live.starts = [StartShape(preflight=("PREFLIGHT_WARNING heartbeat not configured",)), StartShape()]
    world.live.on_call["run --dry-run"] = second_dry_run_bangs

    code = world.apply()

    assert code == deploy_apply.EXIT_UNDO_FAILED, world.output
    assert "FAIL undo_dry_run: dry_run_bang: 1 line(s) with !!!: !!! after the reset" in world.output
    assert world.live_head() == world.scenario.base  # reset, then stopped short of the start
    assert not world.robot.running and not world.launchctl.loaded
    assert world.live.verbs[-2:] == ["shadow agent disable", "run --dry-run --date"]
    assert world.deploys()[-1].endswith("result=UNDO_FAILED reason=undo_dry_run")


def test_an_unexpected_error_after_the_migration_prints_the_restore(tmp_path):
    world = _migration_world(tmp_path)

    def boom():
        raise RuntimeError("injected")

    world.live.on_call["run --dry-run"] = boom
    try:
        code = world.apply(expect_migration=LATEST)
    finally:
        world.robot.release_lock()

    assert code == deploy_apply.EXIT_HALTED, world.output
    _token_lines(world, "internal_error")
    assert "RESTORE (never run by this command)" in world.output
    assert world.deploys()[-1].split(" backup=", 1)[1].endswith(f".v{PREVIOUS}-to-v{LATEST}.db")

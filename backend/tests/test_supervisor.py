"""The trading-day supervisor: plan, restarts, window ends, shutdown, log contract."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
import io
import itertools
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
from zoneinfo import ZoneInfo

import pytest

from trading.operations import supervisor as supervisor_module
from trading.operations.paths import PathSecurityError, UserPaths
from trading.operations.supervisor import (
    BACKEND_DIR,
    STRATEGY_BACKOFF,
    VENV_PYTHON,
    ChildPlan,
    LaunchRefused,
    SubprocessLauncher,
    Supervisor,
    SupervisorAlreadyRunning,
    SupervisorLog,
    child_exit_line,
    child_start_line,
    dry_run_report,
    exit_status,
    open_private_append,
    plan_session,
    single_instance,
)
from trading.scheduler.lease import DEFAULT_LEASE_TTL
from trading.scheduler.sessions import market_session


@pytest.fixture(autouse=True)
def _two_strategy_schedule(request, monkeypatch):
    """Timeline tests below were written for the ORB+MOMO trading day; the
    shadow strategies added later (gap, vwap), the AI day manager's children
    (manager, momo-ap) and the after-close research children (recorder,
    replay) are covered by their own full_schedule tests."""
    if request.node.get_closest_marker("full_schedule"):
        return
    monkeypatch.setattr(
        supervisor_module, "STRATEGY_STARTS", {"orb": time(9, 40), "momo": time(9, 55)}
    )
    monkeypatch.setattr(
        supervisor_module, "STRATEGY_UNIVERSES", {"orb": "scanner", "momo": "watchlist"}
    )
    monkeypatch.setattr(supervisor_module, "RESEARCH_CHILDREN", {})
    monkeypatch.setattr(supervisor_module, "manager_plans", lambda *_args, **_kwargs: ())

ET = ZoneInfo("America/New_York")
UTC = timezone.utc
TUESDAY = date(2026, 10, 13)
PYTHON = Path("/repo/backend/.venv/bin/python")
BACKEND = Path("/repo/backend")
CHILD_START = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\+00:00 CHILD_START [a-z]+ pid=\d+$")
CHILD_EXIT = re.compile(
    r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\+00:00 CHILD_EXIT [a-z]+ code=\d+ restart_in=(\d+|none)$"
)


def et(day: date, hour: int, minute: int, second: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute, second), tzinfo=ET).astimezone(UTC)


def plans_for(session) -> tuple[ChildPlan, ...]:
    return plan_session(session, python=PYTHON, backend=BACKEND)


@dataclass(frozen=True)
class Script:
    """How one launch of a child behaves; the default runs until signalled."""

    exit_after: timedelta | None = None
    code: int = 0
    ignore_sigterm: bool = False
    launch_error: bool = False
    refused: bool = False


class FakeChild:
    def __init__(self, world: "FakeWorld", name: str, pid: int, script: Script) -> None:
        self.world = world
        self.name = name
        self.pid = pid
        self.script = script
        self.started = world.now
        self.returncode: int | None = None
        self.signals: list[tuple[datetime, signal.Signals]] = []
        self._exited = asyncio.Event()

    async def wait(self) -> int:
        await self._exited.wait()
        assert self.returncode is not None
        return self.returncode

    def send_signal(self, signum: int) -> None:
        self.signals.append((self.world.now, signal.Signals(signum)))
        if self.returncode is None and not (
            signum == signal.SIGTERM and self.script.ignore_sigterm
        ):
            self.finish(-signum)

    def finish(self, returncode: int) -> None:
        if self.returncode is None:
            self.returncode = returncode
            self._exited.set()


class FakeWorld:
    """Virtual clock plus scripted children; sleeping stops at the next scripted event."""

    def __init__(self, start: datetime, scripts: dict[str, list[Script]] | None = None) -> None:
        self.now = start
        self.scripts = scripts or {}
        self.launches: list[tuple[datetime, str]] = []
        self.children: list[FakeChild] = []
        self.lines: list[str] = []
        self.sleeps: list[float] = []
        self.stop_at: datetime | None = None
        self.supervisor: Supervisor | None = None
        self._pids = itertools.count(100)

    def clock(self) -> datetime:
        return self.now

    async def start(self, plan: ChildPlan) -> FakeChild:
        attempt = sum(1 for _, name in self.launches if name == plan.name)
        scripted = self.scripts.get(plan.name, [])
        script = scripted[attempt] if attempt < len(scripted) else Script()
        self.launches.append((self.now, plan.name))
        if script.launch_error:
            raise OSError("exec format error")
        if script.refused:
            raise LaunchRefused(78, "unreviewed sha256=1f856f7ee6be reviewed=000000000000")
        child = FakeChild(self, plan.name, next(self._pids), script)
        self.children.append(child)
        return child

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        target = self.now + timedelta(seconds=seconds)
        due = sorted(
            (
                (child.started + child.script.exit_after, index, child)
                for index, child in enumerate(self.children)
                if child.returncode is None and child.script.exit_after is not None
            ),
            key=lambda item: (item[0], item[1]),
        )
        if self.stop_at is not None and self.stop_at <= target and (
            not due or self.stop_at <= due[0][0]
        ):
            self.now = max(self.now, self.stop_at)
            self.stop_at = None
            assert self.supervisor is not None
            self.supervisor.request_stop(signal.SIGTERM)
        elif due and due[0][0] <= target:
            moment, _, child = due[0]
            self.now = max(self.now, moment)
            child.finish(child.script.code)
        else:
            self.now = target
        await asyncio.sleep(0)

    def supervisor_for(self, *, chunk: float = 3600.0, planner=plans_for) -> Supervisor:
        self.supervisor = Supervisor(
            launcher=self,
            emit=self.lines.append,
            planner=planner,
            clock=self.clock,
            sleep=self.sleep,
            sleep_chunk_seconds=chunk,
        )
        return self.supervisor

    def child_lines(self, name: str | None = None) -> list[str]:
        return [
            line
            for line in self.lines
            if " CHILD_" in line and (name is None or line.split(" ")[2] == name)
        ]

    def launch_times(self, name: str) -> list[datetime]:
        return [moment for moment, launched in self.launches if launched == name]

    def instances(self, name: str) -> list[FakeChild]:
        return [child for child in self.children if child.name == name]


def restart_values(world: FakeWorld, name: str) -> list[str]:
    return [line.rsplit("restart_in=", 1)[1] for line in world.child_lines(name) if "CHILD_EXIT" in line]


# --- the plan -------------------------------------------------------------


def window(plan: ChildPlan) -> tuple[str, str, str]:
    return tuple(  # type: ignore[return-value]
        moment.astimezone(ET).strftime("%H:%M")
        for moment in (plan.starts_at, plan.ends_at, plan.finishes_at)
    )


def test_plan_is_the_owner_timeline_with_explicit_dates():
    plans = {plan.name: plan for plan in plans_for(market_session(TUESDAY))}
    control = str(BACKEND / "autotraderctl.py")

    assert list(plans) == ["screener", "notifier", "bridge", "orb", "momo"]
    assert window(plans["screener"]) == ("07:55", "16:00", "16:00")
    assert window(plans["notifier"]) == ("07:55", "16:15", "16:05")
    assert window(plans["bridge"]) == ("09:25", "16:00", "15:55")
    assert window(plans["orb"]) == ("09:40", "12:01", "12:00")
    assert window(plans["momo"]) == ("09:55", "15:01", "15:00")
    assert plans["screener"].argv == (
        str(PYTHON), control, "screener", "run", "--interval", "60",
    )
    assert plans["notifier"].argv == (str(PYTHON), control, "notify", "run", "--date", "2026-10-13")
    assert plans["bridge"].argv == (
        str(PYTHON), str(BACKEND / "scripts_local" / "paper_bridge.py"), "--date", "2026-10-13",
    )
    assert plans["orb"].argv == (
        str(PYTHON), control, "shadow", "run", "--strategy", "orb",
        "--universe", "scanner", "--date", "2026-10-13",
    )
    assert plans["momo"].argv == (
        str(PYTHON), control, "shadow", "run", "--strategy", "momo",
        "--universe", "watchlist", "--date", "2026-10-13",
    )
    assert {name: plan.base_backoff.total_seconds() for name, plan in plans.items()} == {
        "screener": 15, "notifier": 15, "bridge": 15, "orb": 95, "momo": 95,
    }
    assert {name: set(plan.final_codes) for name, plan in plans.items()} == {
        "screener": set(), "notifier": set(), "bridge": {78}, "orb": set(), "momo": set(),
    }


def test_strategy_backoff_outlasts_the_shadow_run_lease():
    assert STRATEGY_BACKOFF >= timedelta(seconds=95)
    assert STRATEGY_BACKOFF > DEFAULT_LEASE_TTL


def test_default_interpreter_is_the_unresolved_venv_path():
    plans = plan_session(market_session(TUESDAY))

    assert VENV_PYTHON == BACKEND_DIR / ".venv" / "bin" / "python"
    assert {plan.argv[0] for plan in plans} == {str(VENV_PYTHON)}
    if VENV_PYTHON.is_symlink():
        # Resolving would name uv's base interpreter, which lacks the packages.
        assert str(VENV_PYTHON.resolve()) != plans[0].argv[0]


def test_half_day_strategy_windows_follow_their_own_cutoffs():
    plans = {plan.name: plan for plan in plans_for(market_session(date(2026, 11, 27)))}

    assert window(plans["momo"]) == ("09:55", "12:01", "12:00")
    assert window(plans["orb"]) == ("09:40", "12:01", "12:00")


def test_half_day_bridge_window_follows_the_early_close():
    plans = {plan.name: plan for plan in plans_for(market_session(date(2026, 11, 27)))}

    # paper_bridge.py stops at 12:55 on a 13:00 close; its 12:58 alert fits.
    assert window(plans["bridge"]) == ("09:25", "13:00", "12:55")


async def test_half_day_bridge_stop_at_its_own_close_is_not_restarted():
    half_day = date(2026, 11, 27)
    world = FakeWorld(
        et(half_day, 9, 20), {"bridge": [Script(exit_after=timedelta(hours=3, minutes=30))]}
    )

    def bridge_only(session):
        return [plan for plan in plans_for(session) if plan.name == "bridge"]

    assert await world.supervisor_for(planner=bridge_only).run(session_date=half_day) == 0

    # A fixed 15:55 finish read the 12:55 clean exit as a crash and restarted
    # the bridge every 15 s (repeating its "still open" alert) until 16:00.
    assert world.child_lines() == [
        "2026-11-27T14:25:00+00:00 CHILD_START bridge pid=100",
        "2026-11-27T17:55:00+00:00 CHILD_EXIT bridge code=0 restart_in=none",
    ]


async def test_a_refused_bridge_is_final_and_reported_with_its_reason():
    world = FakeWorld(et(TUESDAY, 9, 20), {"bridge": [Script(refused=True)]})

    def bridge_and_notifier(session):
        return [plan for plan in plans_for(session) if plan.name in ("bridge", "notifier")]

    world.stop_at = et(TUESDAY, 9, 40)
    await world.supervisor_for(planner=bridge_and_notifier).run(session_date=TUESDAY)

    # One attempt at 09:25, never retried: a restart cannot review the code.
    assert world.launch_times("bridge") == [et(TUESDAY, 9, 25)]
    assert [line.split(" ", 1)[1] for line in world.lines if "bridge" in line] == [
        "LAUNCH_REFUSED bridge code=78 reason=unreviewed sha256=1f856f7ee6be reviewed=000000000000",
        "CHILD_EXIT bridge code=78 restart_in=none",
    ]
    # The rest of the day is unaffected: the notifier runs until the stop.
    assert world.child_lines("notifier") == [
        "2026-10-13T13:20:00+00:00 CHILD_START notifier pid=100",
        "2026-10-13T13:40:00+00:00 CHILD_EXIT notifier code=143 restart_in=none",
    ]


def _script(tmp_path: Path, body: str = "print('bridge ran')") -> Path:
    script = tmp_path / "paper_bridge.py"
    script.write_text(body + "\n", encoding="utf-8")
    return script


def _pinned_plan(script: Path) -> ChildPlan:
    now = datetime.now(UTC)
    return ChildPlan(
        name="bridge",
        argv=(sys.executable, str(script), "--date", "2026-10-13"),
        starts_at=now,
        ends_at=now + timedelta(seconds=30),
        finishes_at=now + timedelta(seconds=30),
        base_backoff=timedelta(seconds=15),
        final_codes=frozenset({78}),
    )


async def test_the_launcher_starts_only_the_reviewed_bridge(tmp_path):
    script = _script(tmp_path)
    reviewed = supervisor_module.file_sha256(script)
    logs = tmp_path / "logs"
    launcher = SubprocessLauncher(logs, cwd=tmp_path, pinned={"bridge": reviewed})

    process = await launcher.start(_pinned_plan(script))
    assert await process.wait() == 0
    assert "bridge ran" in (logs / "bridge.log").read_text()

    # Edited after the review: refused before exec, at every (re)start.
    script.write_text("print('edited bridge ran')\n", encoding="utf-8")
    for _ in range(2):
        with pytest.raises(LaunchRefused) as refused:
            await launcher.start(_pinned_plan(script))
        assert refused.value.code == 78
        assert refused.value.reason.startswith("unreviewed sha256=")
        assert f"reviewed={reviewed[:12]}" in refused.value.reason
    assert "edited bridge ran" not in (logs / "bridge.log").read_text()

    script.unlink()
    with pytest.raises(LaunchRefused) as missing:
        await launcher.start(_pinned_plan(script))
    assert missing.value.reason == "unreadable_script error=FileNotFoundError"


async def test_unpinned_children_launch_whatever_their_script(tmp_path):
    script = _script(tmp_path)
    launcher = SubprocessLauncher(tmp_path / "logs", cwd=tmp_path, pinned={"other": "0" * 64})

    process = await launcher.start(_pinned_plan(script))

    assert await process.wait() == 0


def test_the_pinned_bridge_is_the_file_on_disk():
    # Moving the pin is a review decision: only the commit that records a GO in
    # docs/PAPER_EXECUTION_STATUS.md changes REVIEWED_BRIDGE_SHA256. A bridge
    # edit without a new review fails here, and the supervisor would refuse it.
    bridge = BACKEND_DIR / "scripts_local" / "paper_bridge.py"

    assert supervisor_module.file_sha256(bridge) == supervisor_module.REVIEWED_BRIDGE_SHA256


def test_the_dry_run_says_whether_the_bridge_is_the_reviewed_one(tmp_path, monkeypatch):
    script = tmp_path / "scripts_local" / "paper_bridge.py"
    script.parent.mkdir()
    script.write_text("print('bridge')\n", encoding="utf-8")
    digest = supervisor_module.file_sha256(script)

    monkeypatch.setattr(supervisor_module, "REVIEWED_BRIDGE_SHA256", digest)
    assert supervisor_module.bridge_review_line(tmp_path) == f"bridge       reviewed (sha256 {digest[:12]})"

    monkeypatch.setattr(supervisor_module, "REVIEWED_BRIDGE_SHA256", "f" * 64)
    line = supervisor_module.bridge_review_line(tmp_path)
    assert line.startswith(f"WARNING: bridge sha256 {digest[:12]} is not the reviewed ffffffffffff")
    assert "refuse to start it" in line

    script.unlink()
    assert "unreadable" in supervisor_module.bridge_review_line(tmp_path)


def test_strategy_without_an_entry_window_is_not_planned():
    session = market_session(TUESDAY)
    plans = plan_session(
        session, python=PYTHON, backend=BACKEND, cutoff_for=lambda name, day: et(day, 9, 30)
    )
    assert [plan.name for plan in plans] == ["screener", "notifier", "bridge"]


def test_child_plan_rejects_inverted_or_naive_windows():
    start = et(TUESDAY, 9, 0)
    with pytest.raises(ValueError):
        ChildPlan("x", ("python",), start, start, start, timedelta(seconds=1))
    with pytest.raises(ValueError):
        ChildPlan("x", ("python",), start, start + timedelta(minutes=1), start + timedelta(minutes=2), timedelta(seconds=1))
    with pytest.raises(ValueError):
        ChildPlan("x", ("python",), start.replace(tzinfo=None), start + timedelta(minutes=1), start, timedelta(seconds=1))


# --- log contract -----------------------------------------------------------


def test_child_lines_have_the_stable_notifier_format():
    moment = datetime(2026, 10, 13, 13, 55, tzinfo=UTC)

    assert child_start_line(moment, "momo", 4242) == "2026-10-13T13:55:00+00:00 CHILD_START momo pid=4242"
    assert (
        child_exit_line(moment, "momo", 1, timedelta(seconds=95))
        == "2026-10-13T13:55:00+00:00 CHILD_EXIT momo code=1 restart_in=95"
    )
    assert (
        child_exit_line(moment.astimezone(ET), "bridge", 0, None)
        == "2026-10-13T13:55:00+00:00 CHILD_EXIT bridge code=0 restart_in=none"
    )


def test_exit_status_follows_the_shell_signal_convention():
    assert [exit_status(code) for code in (0, 1, 75, -15, -9)] == [0, 1, 75, 143, 137]


# --- one session --------------------------------------------------------------


async def test_full_session_starts_and_stops_every_child_in_its_window():
    world = FakeWorld(et(TUESDAY, 7, 0))
    supervisor = world.supervisor_for()

    assert await supervisor.run(session_date=TUESDAY) == 0

    assert world.child_lines() == [
        "2026-10-13T11:55:00+00:00 CHILD_START screener pid=100",
        "2026-10-13T11:55:00+00:00 CHILD_START notifier pid=101",
        "2026-10-13T13:25:00+00:00 CHILD_START bridge pid=102",
        "2026-10-13T13:40:00+00:00 CHILD_START orb pid=103",
        "2026-10-13T13:55:00+00:00 CHILD_START momo pid=104",
        "2026-10-13T16:01:00+00:00 CHILD_EXIT orb code=143 restart_in=none",
        "2026-10-13T19:01:00+00:00 CHILD_EXIT momo code=143 restart_in=none",
        "2026-10-13T20:00:00+00:00 CHILD_EXIT screener code=143 restart_in=none",
        "2026-10-13T20:00:00+00:00 CHILD_EXIT bridge code=143 restart_in=none",
        "2026-10-13T20:15:00+00:00 CHILD_EXIT notifier code=143 restart_in=none",
    ]
    assert all(CHILD_START.match(line) or CHILD_EXIT.match(line) for line in world.child_lines())
    for child in world.children:
        assert [sig for _, sig in child.signals] == [signal.SIGTERM]
    assert world.lines[-1].endswith("SUPERVISOR_STOP reason=completed")


async def test_strategy_restarts_back_off_from_95_seconds_to_five_minutes():
    quick_failure = Script(exit_after=timedelta(seconds=10), code=1)
    world = FakeWorld(et(TUESDAY, 9, 50), {"momo": [quick_failure] * 4})

    await world.supervisor_for().run(session_date=TUESDAY)

    assert restart_values(world, "momo") == ["95", "190", "300", "300", "none"]
    assert [moment.astimezone(ET).strftime("%H:%M:%S") for moment in world.launch_times("momo")] == [
        "09:55:00", "09:56:45", "10:00:05", "10:05:15", "10:10:25",
    ]


async def test_service_restarts_back_off_from_15_seconds():
    disconnect = Script(exit_after=timedelta(seconds=1), code=75)
    world = FakeWorld(et(TUESDAY, 9, 20), {"bridge": [disconnect] * 6})

    await world.supervisor_for().run(session_date=TUESDAY)

    assert restart_values(world, "bridge") == ["15", "30", "60", "120", "240", "300", "none"]
    assert world.child_lines("bridge")[1].endswith("CHILD_EXIT bridge code=75 restart_in=15")


async def test_backoff_starts_over_after_a_healthy_run():
    world = FakeWorld(
        et(TUESDAY, 9, 20),
        {
            "bridge": [
                Script(exit_after=timedelta(seconds=1), code=75),
                Script(exit_after=timedelta(minutes=10), code=75),
                Script(exit_after=timedelta(seconds=1), code=75),
            ]
        },
    )

    await world.supervisor_for().run(session_date=TUESDAY)

    assert restart_values(world, "bridge")[:3] == ["15", "15", "30"]


async def test_clean_exit_after_the_planned_finish_is_final():
    world = FakeWorld(
        et(TUESDAY, 7, 0),
        {
            # The bridge stops itself at 15:55; the notifier after its 16:05 report.
            "bridge": [Script(exit_after=et(TUESDAY, 15, 55, 2) - et(TUESDAY, 9, 25))],
            "notifier": [Script(exit_after=et(TUESDAY, 16, 5, 30) - et(TUESDAY, 7, 55))],
        },
    )

    await world.supervisor_for().run(session_date=TUESDAY)

    assert world.child_lines("bridge")[-1] == "2026-10-13T19:55:02+00:00 CHILD_EXIT bridge code=0 restart_in=none"
    assert world.child_lines("notifier")[-1] == "2026-10-13T20:05:30+00:00 CHILD_EXIT notifier code=0 restart_in=none"
    assert len(world.launch_times("bridge")) == 1
    assert len(world.launch_times("notifier")) == 1
    assert world.instances("bridge")[0].signals == []


async def test_any_exit_before_the_planned_finish_is_restarted():
    world = FakeWorld(et(TUESDAY, 7, 0), {"screener": [Script(exit_after=timedelta(hours=1))]})

    await world.supervisor_for().run(session_date=TUESDAY)

    assert world.child_lines("screener")[1] == "2026-10-13T12:55:00+00:00 CHILD_EXIT screener code=0 restart_in=15"
    assert world.launch_times("screener")[1] == et(TUESDAY, 8, 55, 15)


async def test_restart_is_dropped_when_it_would_land_after_the_window_end():
    # ORB exits at its 12:00 cutoff; 95 s later its 12:01 window is closed.
    world = FakeWorld(
        et(TUESDAY, 7, 0),
        {"orb": [Script(exit_after=et(TUESDAY, 12, 0) - et(TUESDAY, 9, 40), code=1)]},
    )

    await world.supervisor_for().run(session_date=TUESDAY)

    assert world.child_lines("orb")[-1] == "2026-10-13T16:00:00+00:00 CHILD_EXIT orb code=1 restart_in=none"
    assert len(world.launch_times("orb")) == 1


async def test_window_end_escalates_to_sigkill_after_twenty_seconds():
    world = FakeWorld(et(TUESDAY, 7, 0), {"screener": [Script(ignore_sigterm=True)]})

    await world.supervisor_for().run(session_date=TUESDAY)

    screener = world.instances("screener")[0]
    assert screener.signals == [
        (et(TUESDAY, 16, 0), signal.SIGTERM),
        (et(TUESDAY, 16, 0, 20), signal.SIGKILL),
    ]
    assert world.child_lines("screener")[-1] == "2026-10-13T20:00:20+00:00 CHILD_EXIT screener code=137 restart_in=none"


async def test_a_bridge_refusal_is_final_and_reported():
    # 78: non-paper account, future date or unusable config; a restart cannot fix it.
    world = FakeWorld(et(TUESDAY, 9, 20), {"bridge": [Script(exit_after=timedelta(seconds=2), code=78)]})

    await world.supervisor_for().run(session_date=TUESDAY)

    assert world.child_lines("bridge") == [
        "2026-10-13T13:25:00+00:00 CHILD_START bridge pid=102",
        "2026-10-13T13:25:02+00:00 CHILD_EXIT bridge code=78 restart_in=none",
    ]
    assert len(world.launch_times("bridge")) == 1


async def test_bridge_alert_after_its_stop_time_is_not_cut_off():
    # Not flat at 15:55: the bridge alerts at 15:58 and then exits 0 by itself.
    world = FakeWorld(
        et(TUESDAY, 9, 20),
        {"bridge": [Script(exit_after=et(TUESDAY, 15, 58, 5) - et(TUESDAY, 9, 25))]},
    )

    await world.supervisor_for().run(session_date=TUESDAY)

    assert world.instances("bridge")[0].signals == []
    assert world.child_lines("bridge")[-1] == "2026-10-13T19:58:05+00:00 CHILD_EXIT bridge code=0 restart_in=none"


async def test_launch_failure_is_reported_as_code_127_and_retried():
    world = FakeWorld(et(TUESDAY, 9, 20), {"bridge": [Script(launch_error=True)]})

    await world.supervisor_for().run(session_date=TUESDAY)

    assert any(line.endswith("LAUNCH_FAILED bridge error=OSError") for line in world.lines)
    assert world.child_lines("bridge")[0] == "2026-10-13T13:25:00+00:00 CHILD_EXIT bridge code=127 restart_in=15"
    assert world.launch_times("bridge") == [et(TUESDAY, 9, 25), et(TUESDAY, 9, 25, 15)]


# --- shutdown -------------------------------------------------------------------


async def test_a_supervisor_failure_never_orphans_children():
    world = FakeWorld(et(TUESDAY, 9, 30))

    def emit(line: str) -> None:
        if "CHILD_START momo" in line:
            raise OSError("disk full")
        world.lines.append(line)

    supervisor = Supervisor(
        launcher=world, emit=emit, planner=plans_for, clock=world.clock, sleep=world.sleep,
        sleep_chunk_seconds=3600.0,
    )

    with pytest.raises(OSError, match="disk full"):
        await supervisor.run(session_date=TUESDAY)

    assert sorted(child.name for child in world.children) == [
        "bridge", "momo", "notifier", "orb", "screener",
    ]
    for child in world.children:
        assert child.returncode is not None
        assert [sig for _, sig in child.signals] == [signal.SIGTERM]
    assert world.lines[-1].endswith("SUPERVISOR_STOP reason=supervisor_error")


async def test_stop_terminates_every_child_kills_stragglers_and_returns_zero():
    world = FakeWorld(et(TUESDAY, 7, 0), {"bridge": [Script(ignore_sigterm=True)]})
    world.stop_at = et(TUESDAY, 10, 30)

    assert await world.supervisor_for().run() == 0

    stop = et(TUESDAY, 10, 30)
    assert sorted(child.name for child in world.children) == ["bridge", "momo", "notifier", "orb", "screener"]
    for child in world.children:
        expected = [(stop, signal.SIGTERM)]
        if child.name == "bridge":
            expected.append((stop + timedelta(seconds=20), signal.SIGKILL))
        assert child.signals == expected
    assert all(line.endswith("restart_in=none") for line in world.child_lines() if "CHILD_EXIT" in line)
    assert max(moment for moment, _ in world.launches) < stop
    assert world.child_lines("bridge")[-1].endswith("CHILD_EXIT bridge code=137 restart_in=none")
    assert world.lines[-1].endswith("SUPERVISOR_STOP reason=SIGTERM")


async def test_stop_cancels_a_pending_restart():
    world = FakeWorld(
        et(TUESDAY, 9, 30),
        {"orb": [Script(exit_after=et(TUESDAY, 10, 0) - et(TUESDAY, 9, 40), code=1)]},
    )
    world.stop_at = et(TUESDAY, 10, 0, 30)

    await world.supervisor_for().run()

    assert restart_values(world, "orb") == ["95"]
    assert len(world.launch_times("orb")) == 1


# --- days -------------------------------------------------------------------------


async def test_closed_days_sleep_until_the_next_session_opens():
    saturday_noon = datetime(2026, 10, 17, 12, 0, tzinfo=ET).astimezone(UTC)
    world = FakeWorld(saturday_noon)
    world.stop_at = et(date(2026, 10, 19), 8, 0)
    supervisor = world.supervisor_for(chunk=supervisor_module.MAX_SLEEP_CHUNK_SECONDS)

    await supervisor.run()

    assert any(
        line.endswith("SESSION_WAIT date=2026-10-19 until=2026-10-19T11:55:00+00:00")
        for line in world.lines
    )
    assert world.launch_times("screener") == [et(date(2026, 10, 19), 7, 55)]
    assert min(moment for moment, _ in world.launches) == et(date(2026, 10, 19), 7, 55)
    # Long waits are re-checked against wall-clock time (macOS sleep).
    assert max(world.sleeps) <= supervisor_module.MAX_SLEEP_CHUNK_SECONDS


async def test_daily_mode_keeps_running_into_the_next_session():
    world = FakeWorld(et(TUESDAY, 7, 0))
    world.stop_at = et(date(2026, 10, 14), 8, 0)

    await world.supervisor_for().run()

    events = [line.split(" ", 1)[1] for line in world.lines if "SESSION_" in line]
    assert events == [
        "SESSION_WAIT date=2026-10-13 until=2026-10-13T11:55:00+00:00",
        "SESSION_START date=2026-10-13",
        "SESSION_END date=2026-10-13",
        "SESSION_WAIT date=2026-10-14 until=2026-10-14T11:55:00+00:00",
        "SESSION_START date=2026-10-14",
        "SESSION_END date=2026-10-14",
    ]
    assert world.launch_times("screener") == [et(TUESDAY, 7, 55), et(date(2026, 10, 14), 7, 55)]


async def test_a_requested_session_that_is_over_launches_nothing():
    world = FakeWorld(et(TUESDAY, 16, 20))

    assert await world.supervisor_for().run(session_date=TUESDAY) == 0

    assert world.launches == []
    assert any(line.endswith("SESSION_OVER date=2026-10-13") for line in world.lines)


async def test_a_late_start_skips_windows_that_already_closed():
    world = FakeWorld(et(TUESDAY, 12, 30))

    await world.supervisor_for().run(session_date=TUESDAY)

    assert sorted(name for _, name in world.launches) == ["bridge", "momo", "notifier", "screener"]
    assert {moment for moment, _ in world.launches} == {et(TUESDAY, 12, 30)}


# --- real processes ---------------------------------------------------------------


def _plan(name: str, code: str, *, seconds: float = 30.0) -> ChildPlan:
    now = datetime.now(UTC)
    return ChildPlan(
        name=name,
        argv=(sys.executable, "-c", code),
        starts_at=now,
        ends_at=now + timedelta(seconds=seconds),
        finishes_at=now + timedelta(seconds=seconds),
        base_backoff=timedelta(seconds=30),
    )


async def test_launcher_appends_output_to_a_private_log_in_its_own_group(tmp_path):
    logs = tmp_path / "logs"
    launcher = SubprocessLauncher(logs, cwd=tmp_path)
    plan = _plan(
        "echo",
        "import os, sys; print('out'); print('err', file=sys.stderr); "
        "print('own group', os.getpgid(0) == os.getpid())",
    )

    for _ in range(2):
        process = await launcher.start(plan)
        assert await process.wait() == 0

    log = logs / "echo.log"
    text = log.read_text()
    assert text.count("supervisor starts echo") == 2
    assert text.count("out\n") == 2 and text.count("err\n") == 2
    assert "own group True" in text
    assert stat.S_IMODE(log.stat().st_mode) == 0o600
    assert stat.S_IMODE(logs.stat().st_mode) == 0o700


async def test_signals_reach_only_the_child_group(tmp_path):
    process = await SubprocessLauncher(tmp_path / "logs", cwd=tmp_path).start(
        _plan("sleeper", "import time; time.sleep(60)")
    )

    process.send_signal(signal.SIGTERM)

    assert await asyncio.wait_for(process.wait(), 10) == -signal.SIGTERM
    process.send_signal(signal.SIGTERM)  # after exit: a no-op, never another group


async def test_real_children_are_terminated_then_killed_at_window_end(tmp_path, monkeypatch):
    monkeypatch.setattr(supervisor_module, "TERMINATE_GRACE", timedelta(seconds=0.5))
    lines: list[str] = []
    plans = (
        _plan("sleeper", "import time; time.sleep(60)", seconds=1.0),
        _plan(
            "stubborn",
            "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)",
            seconds=1.0,
        ),
    )
    supervisor = Supervisor(
        launcher=SubprocessLauncher(tmp_path / "logs", cwd=tmp_path),
        emit=lines.append,
        sleep_chunk_seconds=0.05,
    )

    await asyncio.wait_for(supervisor.run_session(plans), 15)

    exits = [line.split(" ", 2)[2] for line in lines if "CHILD_EXIT" in line]
    assert sorted(exits) == [
        "sleeper code=143 restart_in=none",
        "stubborn code=137 restart_in=none",
    ]


def test_logs_refuse_symbolic_links(tmp_path):
    target = tmp_path / "target.log"
    target.write_text("")
    link = tmp_path / "bridge.log"
    link.symlink_to(target)

    with pytest.raises(PathSecurityError):
        open_private_append(link)


def test_supervisor_log_is_private_append_only_and_echoed(tmp_path):
    echo = io.StringIO()
    path = tmp_path / "logs" / "supervisor.log"
    for line in ("first", "second"):
        log = SupervisorLog(path, echo=echo)
        log(line)
        log.close()

    assert path.read_text() == "first\nsecond\n"
    assert echo.getvalue() == "first\nsecond\n"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_only_one_supervisor_may_run(tmp_path):
    lock = tmp_path / "data" / "supervisor.lock"
    with single_instance(lock):
        assert lock.read_text().strip() == str(os.getpid())
        with pytest.raises(SupervisorAlreadyRunning, match=str(os.getpid())):
            with single_instance(lock):
                pass
    with single_instance(lock):
        pass


def test_unsupervised_children_lists_trading_processes_but_not_itself(monkeypatch):
    calls: list[list[str]] = []

    def fake_run(command, **_kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=(
                "24401 .venv/bin/python -u autotraderctl.py screener run --interval 60\n"
                f"{os.getpid()} python autotraderctl.py shadow run\n"
            ),
            stderr="",
        )

    monkeypatch.setattr(supervisor_module.subprocess, "run", fake_run)

    assert supervisor_module.unsupervised_children() == [
        (24401, ".venv/bin/python -u autotraderctl.py screener run --interval 60")
    ]
    assert calls[0][:2] == ["/usr/bin/pgrep", "-fl"]


def test_unsupervised_children_tolerates_a_missing_pgrep(monkeypatch):
    def broken(*_args, **_kwargs):
        raise FileNotFoundError("pgrep")

    monkeypatch.setattr(supervisor_module.subprocess, "run", broken)
    assert supervisor_module.unsupervised_children() == []


def test_run_supervisor_refuses_a_closed_date_without_side_effects(tmp_path, capsys):
    paths = UserPaths(home=tmp_path)

    assert supervisor_module.run_supervisor(session_date=date(2026, 10, 17), user_paths=paths) == 1

    assert "not an XNYS trading session" in capsys.readouterr().err
    assert not paths.log_directory.exists()


# --- dry run ------------------------------------------------------------------------


def test_dry_run_simulates_the_day_and_writes_nothing(tmp_path):
    paths = UserPaths(home=tmp_path)

    report = dry_run_report(
        session_date=TUESDAY,
        now=et(date(2026, 10, 12), 22, 0),
        user_paths=paths,
        unsupervised=lambda: [(24401, ".venv/bin/python -u autotraderctl.py screener run")],
    )

    for expected in (
        "DRY RUN: nothing is launched and no log is written.",
        "clock start  2026-10-13 00:00 ET (simulated)",
        "07:55:00  CHILD_START screener pid=1",
        "09:25:00  CHILD_START bridge pid=3",
        "09:55:00  CHILD_START momo pid=5",
        "12:01:00  SIGNAL orb SIGTERM reason=window_end",
        "12:01:00  CHILD_EXIT orb code=0 restart_in=none",
        "16:15:00  SESSION_END date=2026-10-13",
    ):
        assert expected in report
    assert "backend/autotraderctl.py shadow run --strategy momo --universe watchlist --date 2026-10-13" in report
    assert "WARNING: running outside the supervisor (kill 24401 before `run`)" in report
    assert list(tmp_path.iterdir()) == []


def test_dry_run_clock_start_skips_windows_that_closed():
    report = dry_run_report(session_date=TUESDAY, clock_start=time(12, 30), unsupervised=list)

    assert "12:30:00  CHILD_START momo" in report
    assert "CHILD_START orb" not in report


def test_dry_run_rejects_a_closed_day():
    with pytest.raises(ValueError, match="not an XNYS trading session"):
        dry_run_report(session_date=date(2026, 10, 17))


# --- CLI: run and notify -------------------------------------------------------------


def test_cli_run_flags():
    from trading import cli

    parser = cli._parser()
    plain = parser.parse_args(["run"])
    assert (plain.dry_run, plain.date, plain.clock_start) == (False, None, None)
    dry = parser.parse_args(["run", "--dry-run", "--date", "2026-10-13", "--clock-start", "09:30"])
    assert (dry.dry_run, dry.date, dry.clock_start) == (True, TUESDAY, time(9, 30))
    for invalid in (["run", "--dry-run", "--clock-start", "9h30"], ["run", "--date", "13/10"]):
        with pytest.raises(SystemExit) as error:
            parser.parse_args(invalid)
        assert error.value.code == 2


@pytest.mark.parametrize(
    "arguments",
    [["run", "--clock-start", "10:00"], ["--db-path", "/tmp/other.db", "run"]],
)
def test_cli_run_rejects_options_it_would_silently_ignore(monkeypatch, arguments):
    from trading import cli

    monkeypatch.setattr(supervisor_module, "run_supervisor", lambda **_kwargs: pytest.fail("ran"))
    with pytest.raises(SystemExit) as error:
        cli.main(arguments)
    assert error.value.code == 2


def test_cli_run_starts_the_supervisor(monkeypatch):
    from trading import cli

    calls: list[dict[str, object]] = []

    def fake_run_supervisor(**kwargs: object) -> int:
        calls.append(kwargs)
        return 0

    monkeypatch.setattr(supervisor_module, "run_supervisor", fake_run_supervisor)

    assert cli.main(["run"]) == 0
    assert cli.main(["run", "--date", "2026-10-13"]) == 0
    assert calls == [{"session_date": None, "drill_date": None}, {"session_date": TUESDAY, "drill_date": None}]


def test_cli_dry_run_prints_the_timeline_and_launches_nothing(monkeypatch, capsys):
    from trading import cli

    monkeypatch.setattr(supervisor_module, "run_supervisor", lambda **_kwargs: pytest.fail("ran"))

    # A fixed simulated clock: on the session's own date the default clock
    # start is "now", which would make this test depend on the time of day.
    assert cli.main(["run", "--dry-run", "--date", "2026-10-13", "--clock-start", "07:00"]) == 0
    output = capsys.readouterr().out
    assert output.startswith("DRY RUN: nothing is launched")
    assert "07:55:00  CHILD_START screener pid=1" in output

    assert cli.main(["run", "--dry-run", "--date", "2026-10-17"]) == 1
    assert "not an XNYS trading session" in capsys.readouterr().err


class _NotifyStub:
    def __init__(self, *, failure: BaseException | None = None) -> None:
        self.calls: list[object] = []
        self.failure = failure

    async def run_notifier(self, *, session_date: date) -> int:
        self.calls.append(session_date)
        if self.failure is not None:
            raise self.failure
        return 0

    async def send_test_message(self) -> int:
        self.calls.append("test")
        return 0


def test_cli_notify_commands_reach_the_notify_service(monkeypatch):
    from trading import cli

    stub = _NotifyStub()
    monkeypatch.setitem(sys.modules, cli.NOTIFY_SERVICE_MODULE, stub)
    parser = cli._parser()
    assert parser.parse_args(["notify", "run", "--date", "2026-10-13"]).date == TUESDAY

    assert cli.main(["notify", "run", "--date", "2026-10-13"]) == 0
    assert cli.main(["notify", "test"]) == 0
    assert stub.calls == [TUESDAY, "test"]


def test_cli_notify_failures_never_print_the_exception_text(monkeypatch, capsys):
    from trading import cli

    secret = "123456:SECRET-bot-token"
    monkeypatch.setitem(sys.modules, cli.NOTIFY_SERVICE_MODULE, _NotifyStub(failure=RuntimeError(secret)))

    assert cli.main(["notify", "run", "--date", "2026-10-13"]) == 1
    error = capsys.readouterr().err
    assert "RuntimeError" in error and secret not in error


def test_cli_notify_reports_a_missing_service(monkeypatch, capsys):
    from trading import cli

    monkeypatch.setattr(cli, "NOTIFY_SERVICE_MODULE", "trading.operations.no_such_notify_service")

    assert cli.main(["notify", "test"]) == 1
    assert "notifier is unavailable" in capsys.readouterr().err


def test_sigterm_cancels_the_command_and_runs_its_cleanup():
    from trading import cli

    cleaned: list[str] = []

    async def command() -> int:
        try:
            os.kill(os.getpid(), signal.SIGTERM)
            await asyncio.sleep(30)
            return 0
        finally:
            cleaned.append("disconnected")

    # An orderly stop is a clean exit, so a window-end stop never alarms the phone.
    assert asyncio.run(cli._cancel_on_sigterm(command())) == cli.EXIT_STOPPED == 0
    assert cleaned == ["disconnected"]
    assert signal.getsignal(signal.SIGTERM) == signal.SIG_DFL


# --- always-on children (the Telegram assistant) ------------------------------------

TELEGRAM = supervisor_module.AlwaysOnPlan(
    name="telegram",
    argv=(str(PYTHON), str(BACKEND / "autotraderctl.py"), "telegram", "run"),
)
WEDNESDAY = date(2026, 10, 14)


def always_supervisor(
    world: FakeWorld, *, chunk: float = 3600.0, always=(TELEGRAM,), emit=None
) -> Supervisor:
    world.supervisor = Supervisor(
        launcher=world,
        emit=emit or world.lines.append,
        planner=plans_for,
        always=always,
        clock=world.clock,
        sleep=world.sleep,
        sleep_chunk_seconds=chunk,
    )
    return world.supervisor


def test_the_telegram_assistant_is_registered_as_an_always_on_child():
    (plan,) = supervisor_module.always_on_plans(python=PYTHON, backend=BACKEND)

    assert plan.name == supervisor_module.TELEGRAM_CHILD == "telegram"
    assert plan.argv == (str(PYTHON), str(BACKEND / "autotraderctl.py"), "telegram", "run")
    assert plan.base_backoff == timedelta(seconds=15)
    # Not part of any session's plan: it has no window.
    assert "telegram" not in {child.name for child in plans_for(market_session(TUESDAY))}


def test_always_on_plans_are_validated():
    with pytest.raises(ValueError, match="command and a positive backoff"):
        supervisor_module.AlwaysOnPlan(name="x", argv=())
    with pytest.raises(ValueError, match="positive backoff"):
        supervisor_module.AlwaysOnPlan(name="x", argv=("a",), base_backoff=timedelta(0))
    with pytest.raises(ValueError, match="distinct names"):
        Supervisor(launcher=FakeWorld(et(TUESDAY, 7, 0)), emit=print, always=(TELEGRAM, TELEGRAM))


async def test_always_on_child_starts_with_the_supervisor_and_runs_between_sessions():
    saturday_noon = datetime(2026, 10, 17, 12, 0, tzinfo=ET).astimezone(UTC)
    monday = date(2026, 10, 19)
    stop = et(monday, 8, 0)
    world = FakeWorld(saturday_noon)
    world.stop_at = stop

    assert await always_supervisor(world, chunk=supervisor_module.MAX_SLEEP_CHUNK_SECONDS).run() == 0

    # Started at once, alone, two days before the next session's children.
    assert world.launches[0] == (saturday_noon, "telegram")
    assert world.launch_times("telegram") == [saturday_noon]
    assert world.launch_times("screener") == [et(monday, 7, 55)]
    (telegram,) = world.instances("telegram")
    assert telegram.signals == [(stop, signal.SIGTERM)]
    lines = world.child_lines("telegram")
    assert lines[0].endswith("CHILD_START telegram pid=100") and CHILD_START.match(lines[0])
    assert lines[-1].endswith("CHILD_EXIT telegram code=143 restart_in=none") and CHILD_EXIT.match(lines[-1])
    assert world.lines[-1].endswith("SUPERVISOR_STOP reason=SIGTERM")


async def test_always_on_child_is_restarted_after_any_exit_with_backoff():
    start = et(TUESDAY, 18, 0)
    world = FakeWorld(
        start,
        {
            "telegram": [
                Script(exit_after=timedelta(minutes=1), code=0),
                Script(exit_after=timedelta(minutes=1), code=78),
                Script(exit_after=timedelta(seconds=10), code=1),
                Script(exit_after=timedelta(minutes=10), code=1),
            ]
        },
    )
    world.stop_at = et(TUESDAY, 19, 0)

    await always_supervisor(world).run()

    # A clean exit and a declared refusal are restarted too; the backoff
    # doubles from 15 s and starts over after a healthy (5 min+) run.
    assert restart_values(world, "telegram") == ["15", "30", "60", "15", "none"]
    assert world.launch_times("telegram") == [
        start,
        et(TUESDAY, 18, 1, 15),
        et(TUESDAY, 18, 2, 45),
        et(TUESDAY, 18, 3, 55),
        et(TUESDAY, 18, 14, 10),
    ]
    codes = [line.split("code=")[1].split(" ")[0] for line in world.child_lines("telegram") if "EXIT" in line]
    assert codes == ["0", "78", "1", "1", "143"]


async def test_session_window_ends_never_touch_the_always_on_child():
    world = FakeWorld(et(TUESDAY, 7, 0))
    stop = et(WEDNESDAY, 8, 0)
    world.stop_at = stop

    await always_supervisor(world).run()

    assert world.launch_times("telegram") == [et(TUESDAY, 7, 0)]
    assert world.launch_times("screener") == [et(TUESDAY, 7, 55), et(WEDNESDAY, 7, 55)]
    (telegram,) = world.instances("telegram")
    assert telegram.signals == [(stop, signal.SIGTERM)]
    assert all("SIGNAL telegram" not in line or "reason=shutdown" in line for line in world.lines)


async def test_a_single_session_run_stops_the_always_on_child_when_it_finishes():
    world = FakeWorld(et(TUESDAY, 15, 0))

    assert await always_supervisor(world).run(session_date=TUESDAY) == 0

    (telegram,) = world.instances("telegram")
    # The last session window (notifier) ends at 16:15; then the supervisor stops.
    assert telegram.signals == [(et(TUESDAY, 16, 15), signal.SIGTERM)]
    assert world.child_lines("telegram")[-1].endswith("CHILD_EXIT telegram code=143 restart_in=none")
    assert world.lines[-1].endswith("SUPERVISOR_STOP reason=completed")


async def test_an_always_on_child_that_ignores_sigterm_is_killed_after_the_grace():
    world = FakeWorld(et(TUESDAY, 18, 0), {"telegram": [Script(ignore_sigterm=True)]})
    stop = et(TUESDAY, 18, 30)
    world.stop_at = stop

    await always_supervisor(world).run()

    (telegram,) = world.instances("telegram")
    assert telegram.signals == [(stop, signal.SIGTERM), (stop + timedelta(seconds=20), signal.SIGKILL)]
    assert world.child_lines("telegram")[-1].endswith("CHILD_EXIT telegram code=137 restart_in=none")


async def test_an_always_on_launch_failure_is_retried_like_an_exit():
    world = FakeWorld(et(TUESDAY, 18, 0), {"telegram": [Script(launch_error=True)]})
    world.stop_at = et(TUESDAY, 18, 5)

    await always_supervisor(world).run()

    assert any("LAUNCH_FAILED telegram error=OSError" in line for line in world.lines)
    assert restart_values(world, "telegram")[0] == "15"
    assert world.launch_times("telegram") == [et(TUESDAY, 18, 0), et(TUESDAY, 18, 0, 15)]


async def test_a_supervisor_failure_never_orphans_the_always_on_child():
    world = FakeWorld(et(TUESDAY, 9, 30))

    def emit(line: str) -> None:
        if "CHILD_START momo" in line:
            raise OSError("disk full")
        world.lines.append(line)

    with pytest.raises(OSError, match="disk full"):
        await always_supervisor(world, emit=emit).run(session_date=TUESDAY)

    (telegram,) = world.instances("telegram")
    assert telegram.returncode is not None
    assert [sig for _, sig in telegram.signals] == [signal.SIGTERM]
    assert all(child.returncode is not None for child in world.children)
    assert world.lines[-1].endswith("SUPERVISOR_STOP reason=supervisor_error")


def test_run_supervisor_registers_the_always_on_children(tmp_path, monkeypatch):
    captured: dict[str, object] = {}

    class CapturingSupervisor:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

        def request_stop(self, signum: int | None = None) -> None:
            pass

        async def run(self, *, session_date: date | None = None) -> int:
            return 0

    monkeypatch.setattr(supervisor_module, "Supervisor", CapturingSupervisor)

    assert (
        supervisor_module.run_supervisor(
            user_paths=UserPaths(home=tmp_path), echo=io.StringIO(), unsupervised=list
        )
        == 0
    )
    assert [plan.name for plan in captured["always"]] == ["telegram"]


def test_dry_run_lists_the_always_on_child():
    report = dry_run_report(session_date=TUESDAY, unsupervised=list)

    assert "telegram  always-on" in report
    assert "backend/autotraderctl.py telegram run" in report
    assert "CHILD_START telegram" not in report  # the timeline shows session children only


def test_a_hand_started_assistant_is_reported_as_unsupervised():
    command = "/repo/backend/.venv/bin/python /repo/backend/autotraderctl.py telegram run"

    assert re.search(supervisor_module._UNSUPERVISED_PATTERN, command)


def test_single_instance_names_the_holder(tmp_path):
    lock = tmp_path / "data" / "telegram.lock"

    class Busy(supervisor_module.AlreadyRunning):
        pass

    with single_instance(lock, label="Telegram assistant", error=Busy):
        with pytest.raises(Busy, match=rf"another Telegram assistant is already running \(pid {os.getpid()}\)"):
            with single_instance(lock, label="Telegram assistant", error=Busy):
                pass


@pytest.mark.full_schedule
def test_default_plan_runs_the_shadow_strategies_and_the_learning_loop_in_their_own_windows():
    from trading.operations.supervisor import plan_session

    plans = {plan.name: plan for plan in plan_session(market_session(date(2026, 10, 13)))}
    assert list(plans) == [
        "screener", "notifier", "bridge", "orb-v2", "momo", "gap-v2", "vwap-v2", "manager",
        "momo-ap", "recorder", "replay",
    ]
    ny = ZoneInfo("America/New_York")
    # The all-session simulations replace ORB_V1, GAP_GO_V1 and VWAP_PB_V1: 09:28-15:31.
    for name in ("orb-v2", "gap-v2", "vwap-v2"):
        assert window(plans[name]) == ("09:28", "15:31", "15:30"), name
        assert plans[name].argv[4:6] == ("--strategy", name)
        assert plans[name].base_backoff == timedelta(seconds=95)
    assert window(plans["momo"]) == ("09:55", "15:01", "15:00")
    assert "all-gappers" in plans["gap-v2"].argv and "watchlist" in plans["vwap-v2"].argv
    assert "scanner" in plans["orb-v2"].argv
    assert not {"orb", "gap", "vwap"} & set(plans)
    # After the close: record the session that just closed, then replay the store.
    control = str(BACKEND_DIR / "autotraderctl.py")
    assert plans["recorder"].argv == (str(VENV_PYTHON), control, "research", "record")
    assert plans["replay"].argv == (str(VENV_PYTHON), control, "research", "replay")
    # The planned finish is the start: any clean exit is the end of the day.
    assert window(plans["recorder"]) == ("16:20", "17:30", "16:20")
    assert window(plans["replay"]) == ("17:35", "18:30", "17:35")
    for name in ("recorder", "replay"):
        assert plans[name].base_backoff == timedelta(seconds=15)
        assert plans[name].final_codes == frozenset()
    # The replay reads what the recorder stored, so their windows never overlap.
    assert plans["recorder"].ends_at < plans["replay"].starts_at


# --- the AI day manager's children (shadow) ---------------------------------------------


@pytest.mark.full_schedule
@pytest.mark.parametrize(
    ("day", "manager_window", "momo_ap_window"),
    [
        # Plans 09:20 .. 14:30; managed MOMO in MOMO's own 09:55-15:01 window.
        (date(2026, 10, 13), ("09:15", "14:40", "14:30"), ("09:55", "15:01", "15:00")),
        # Half day (13:00 close): plans until 11:30, MOMO's cutoff moves to 12:00.
        (date(2026, 11, 27), ("09:15", "11:40", "11:30"), ("09:55", "12:01", "12:00")),
    ],
    ids=["full-day", "half-day"],
)
def test_the_day_managers_children_run_in_shadow_in_their_own_windows(
    day, manager_window, momo_ap_window
):
    plans = {plan.name: plan for plan in plans_for(market_session(day))}
    control = str(BACKEND / "autotraderctl.py")

    assert window(plans["manager"]) == manager_window
    assert plans["manager"].argv == (str(PYTHON), control, "manager", "run", "--date", day.isoformat())
    assert plans["manager"].base_backoff == timedelta(seconds=15)
    assert window(plans["momo-ap"]) == momo_ap_window
    assert plans["momo-ap"].argv == (
        str(PYTHON), control, "shadow", "run", "--strategy", "momo-ap", "--universe", "watchlist",
        "--date", day.isoformat(),
    )
    assert plans["momo-ap"].base_backoff == plans["momo"].base_backoff
    assert window(plans["momo-ap"])[:2] == window(plans["momo"])[:2]
    # No child has a final exit code: a schema refusal (75) is always retried.
    assert plans["manager"].final_codes == plans["momo-ap"].final_codes == frozenset()


def test_the_manager_child_holds_no_tws_client_and_momo_ap_its_own():
    from trading.operations.screener_service import SCREENER_CLIENT_ID_OFFSET
    from trading.operations.shadow_service import STRATEGY_CLIENT_OFFSET

    from trading.operations.supervisor import MANAGED_STRATEGY_STARTS, STRATEGY_STARTS

    data = 17
    # Every child the supervisor schedules has a client id of its own (an
    # unscheduled V1 entry shares its V2 replacement's).
    scheduled = (*STRATEGY_STARTS, *MANAGED_STRATEGY_STARTS)
    ids = {name: data + STRATEGY_CLIENT_OFFSET[name] for name in scheduled}
    assert ids["momo-ap"] == 24 and ids["momo"] == 20
    assert len(set(ids.values())) == len(ids)
    assert data + SCREENER_CLIENT_ID_OFFSET not in ids.values()
    # The manager plans from the database and Claude only.
    assert "manager" not in STRATEGY_CLIENT_OFFSET
    source = (BACKEND_DIR / "trading" / "manager").glob("*.py")
    assert not any("TWSPaperActor" in path.read_text(encoding="utf-8") for path in source)


# --- the learning loop after the close (recorder, replay) ----------------------------

RESEARCH = ("recorder", "replay")


def research_plans(session) -> list[ChildPlan]:
    return [plan for plan in plans_for(session) if plan.name in RESEARCH]


@pytest.mark.full_schedule
def test_half_days_keep_the_learning_loop_windows():
    plans = {plan.name: plan for plan in plans_for(market_session(date(2026, 11, 27)))}

    assert window(plans["recorder"]) == ("16:20", "17:30", "16:20")
    assert window(plans["replay"]) == ("17:35", "18:30", "17:35")


@pytest.mark.full_schedule
async def test_research_children_run_once_and_a_clean_exit_is_final():
    world = FakeWorld(
        et(TUESDAY, 16, 0),
        {
            "recorder": [Script(exit_after=timedelta(minutes=30))],
            "replay": [Script(exit_after=timedelta(minutes=4))],
        },
    )

    assert await world.supervisor_for(planner=research_plans).run(session_date=TUESDAY) == 0

    assert world.child_lines() == [
        "2026-10-13T20:20:00+00:00 CHILD_START recorder pid=100",
        "2026-10-13T20:50:00+00:00 CHILD_EXIT recorder code=0 restart_in=none",
        "2026-10-13T21:35:00+00:00 CHILD_START replay pid=101",
        "2026-10-13T21:39:00+00:00 CHILD_EXIT replay code=0 restart_in=none",
    ]
    assert all(child.signals == [] for child in world.children)
    # The day ends when the replay is done, not at its 18:30 window end.
    assert world.lines[-2].endswith("SESSION_END date=2026-10-13")
    assert world.lines[-2].startswith("2026-10-13T21:39:00")


@pytest.mark.full_schedule
async def test_a_failed_recording_is_retried_with_the_service_backoff_inside_its_window():
    # IB Gateway logged off: every attempt fails after 10 s until 17:30.
    gateway_down = Script(exit_after=timedelta(seconds=10), code=1)
    world = FakeWorld(et(TUESDAY, 16, 0), {"recorder": [gateway_down] * 40})

    await world.supervisor_for(planner=research_plans).run(session_date=TUESDAY)

    restarts = restart_values(world, "recorder")
    assert restarts[:6] == ["15", "30", "60", "120", "240", "300"]
    assert set(restarts[6:-1]) == {"300"}
    # 17:25:35 + 300 s would land after the 17:30 window end: not restarted.
    assert restarts[-1] == "none"
    launches = world.launch_times("recorder")
    assert len(launches) == 17
    assert launches[-1] == et(TUESDAY, 17, 25, 25) and max(launches) < et(TUESDAY, 17, 30)
    # The replay still runs in its own window.
    assert world.launch_times("replay") == [et(TUESDAY, 17, 35)]


@pytest.mark.full_schedule
async def test_a_recording_still_running_at_17_30_is_stopped_and_the_replay_still_runs():
    world = FakeWorld(et(TUESDAY, 16, 0), {"replay": [Script(exit_after=timedelta(minutes=3))]})

    await world.supervisor_for(planner=research_plans).run(session_date=TUESDAY)

    (recorder,) = world.instances("recorder")
    assert recorder.signals == [(et(TUESDAY, 17, 30), signal.SIGTERM)]
    assert world.child_lines("recorder")[-1] == (
        "2026-10-13T21:30:00+00:00 CHILD_EXIT recorder code=143 restart_in=none"
    )
    assert world.launch_times("replay") == [et(TUESDAY, 17, 35)]


@pytest.mark.full_schedule
async def test_daily_mode_runs_the_learning_loop_once_per_session():
    # The replay usually finishes long before 18:30. The day must not be
    # re-entered then, which would relaunch the replay until its window closed.
    world = FakeWorld(
        et(TUESDAY, 16, 0),
        {
            "recorder": [Script(exit_after=timedelta(minutes=25))] * 2,
            "replay": [Script(exit_after=timedelta(minutes=4))] * 2,
        },
    )
    world.stop_at = et(date(2026, 10, 15), 7, 0)

    await world.supervisor_for(planner=research_plans).run()

    assert world.launches == [
        (et(TUESDAY, 16, 20), "recorder"),
        (et(TUESDAY, 17, 35), "replay"),
        (et(WEDNESDAY, 16, 20), "recorder"),
        (et(WEDNESDAY, 17, 35), "replay"),
    ]
    events = [line.split(" ", 1)[1] for line in world.lines if "SESSION_" in line]
    assert events == [
        "SESSION_WAIT date=2026-10-13 until=2026-10-13T20:20:00+00:00",
        "SESSION_START date=2026-10-13",
        "SESSION_END date=2026-10-13",
        "SESSION_WAIT date=2026-10-14 until=2026-10-14T20:20:00+00:00",
        "SESSION_START date=2026-10-14",
        "SESSION_END date=2026-10-14",
        "SESSION_WAIT date=2026-10-15 until=2026-10-15T20:20:00+00:00",
    ]


async def test_daily_mode_never_reenters_a_session_whose_children_all_finished():
    # The notifier exits 0 after its 16:05 report, ten minutes before its window
    # end; nothing may relaunch it for the rest of the day.
    world = FakeWorld(
        et(TUESDAY, 15, 0),
        {"notifier": [Script(exit_after=et(TUESDAY, 16, 5, 30) - et(TUESDAY, 15, 0))]},
    )
    world.stop_at = et(WEDNESDAY, 7, 0)

    await world.supervisor_for().run()

    assert world.launch_times("notifier") == [et(TUESDAY, 15, 0)]


@pytest.mark.full_schedule
async def test_a_supervisor_started_after_the_close_runs_only_the_learning_loop():
    world = FakeWorld(et(TUESDAY, 17, 0))

    await world.supervisor_for().run(session_date=TUESDAY)

    assert world.launches == [(et(TUESDAY, 17, 0), "recorder"), (et(TUESDAY, 17, 35), "replay")]


@pytest.mark.full_schedule
async def test_a_requested_session_after_the_replay_window_launches_nothing():
    world = FakeWorld(et(TUESDAY, 18, 30))

    assert await world.supervisor_for().run(session_date=TUESDAY) == 0

    assert world.launches == []
    assert any(line.endswith("SESSION_OVER date=2026-10-13") for line in world.lines)


@pytest.mark.full_schedule
def test_dry_run_shows_the_learning_loop():
    report = dry_run_report(session_date=TUESDAY, unsupervised=list)

    assert "recorder  16:20-17:30  from 16:20" in report
    assert "replay    17:35-18:30  from 17:35" in report
    assert "backend/autotraderctl.py research record" in report
    assert "backend/autotraderctl.py research replay" in report
    for expected in (
        "16:20:00  CHILD_START recorder",
        "17:30:00  SIGNAL recorder SIGTERM reason=window_end",
        "17:35:00  CHILD_START replay",
        "18:30:00  SESSION_END date=2026-10-13",
    ):
        assert expected in report


# --- drill mode: the engine's day without the bridge ---------------------------------------


@pytest.mark.parametrize("day", [date(2026, 10, 14), date(2026, 11, 27)], ids=["regular", "half-day"])
def test_without_the_flag_the_plan_and_the_dry_run_are_unchanged(day):
    session = market_session(day)
    assert session is not None
    assert plan_session(session, python=PYTHON, backend=BACKEND, drill_date=None) == plans_for(session)
    assert dry_run_report(session_date=day, unsupervised=list) == dry_run_report(
        session_date=day, unsupervised=list, drill_date=None
    )
    # Another day's drill never touches this session.
    other = dry_run_report(session_date=day, unsupervised=list, drill_date=date(2026, 10, 15))
    assert other == dry_run_report(session_date=day, unsupervised=list)


@pytest.mark.parametrize("day", [date(2026, 10, 14), date(2026, 11, 27)], ids=["regular", "half-day"])
def test_on_the_drill_date_only_the_bridge_is_not_planned(day):
    session = market_session(day)
    assert session is not None
    usual = {plan.name for plan in plans_for(session)}
    drill = plan_session(session, python=PYTHON, backend=BACKEND, drill_date=day)
    assert {plan.name for plan in drill} == usual - {supervisor_module.BRIDGE_CHILD}
    assert [plan.argv for plan in drill] == [plan.argv for plan in plans_for(session) if plan.name != supervisor_module.BRIDGE_CHILD]
    report = dry_run_report(session_date=day, unsupervised=list, drill_date=day)
    assert "DRILL MODE: bridge not planned" in report.splitlines()
    assert "CHILD_START bridge" not in report and "paper_bridge.py" not in report
    assert "CHILD_START screener" in report


def test_run_refuses_a_drill_without_a_grant_for_the_deployed_engine(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(supervisor_module.Supervisor, "run",
                        lambda self, session_date=None: pytest.fail("ran"))
    code = supervisor_module.run_supervisor(
        session_date=TUESDAY, user_paths=UserPaths(home=tmp_path), drill_date=TUESDAY,
        drill_authority=lambda: "no active PAPER grant for the deployed engine code",
    )
    assert code == 1
    assert "drill mode refused for 2026-10-13: no active PAPER grant" in capsys.readouterr().err
    assert list(tmp_path.iterdir()) == []  # nothing started, no log written

    code = supervisor_module.run_supervisor(session_date=TUESDAY, user_paths=UserPaths(home=tmp_path),
                                            drill_date=date(2026, 10, 17))
    assert code == 1 and "--drill-date 2026-10-17 is not an XNYS trading session" in capsys.readouterr().err


async def _granted_database(path: Path, *, grant: bool) -> Path:
    from config import cfg
    from db.core import init_db
    from trading.execution.promotion import grant_promotion

    original = cfg.DB_PATH
    cfg.DB_PATH = str(path)
    try:
        await init_db()
        if grant:
            await grant_promotion(strategy_name="MOMO_HOD_V2", account="DU1234567", actor="owner", reason="drill")
    finally:
        cfg.DB_PATH = original
    return path


def test_drill_authority_reads_the_grant_for_the_deployed_code_read_only(tmp_path):
    from trading.operations.supervisor import drill_authority_problem

    absent = tmp_path / "absent.db"
    assert (drill_authority_problem(absent, account="DU1234567") or "").startswith("no autotrader database")
    assert not absent.exists()
    ungranted = asyncio.run(_granted_database(tmp_path / "ungranted.db", grant=False))
    problem = drill_authority_problem(ungranted, account="DU1234567")
    assert problem is not None and "no active PAPER grant for the deployed engine code" in problem
    granted = asyncio.run(_granted_database(tmp_path / "granted.db", grant=True))
    assert drill_authority_problem(granted, account="DU1234567") is None
    assert drill_authority_problem(granted, account="DU7654321") is not None  # another account's grant is none


def test_drill_mode_is_logged_and_planned_without_the_bridge(tmp_path, monkeypatch):
    planned: list[tuple[str, ...]] = []

    async def record(self, session_date=None):
        session = market_session(session_date)
        planned.extend(plan.name for plan in self._planner(session))
        return 0

    monkeypatch.setattr(supervisor_module.Supervisor, "run", record)
    paths = UserPaths(home=tmp_path)
    echo = io.StringIO()
    code = supervisor_module.run_supervisor(session_date=TUESDAY, user_paths=paths, drill_date=TUESDAY, echo=echo,
                          drill_authority=lambda: None, unsupervised=list)
    assert code == 0
    assert supervisor_module.BRIDGE_CHILD not in planned and "screener" in planned
    assert any(line.endswith("DRILL_MODE date=2026-10-13 bridge=not-planned") for line in echo.getvalue().splitlines())


def test_cli_drill_date_is_an_explicit_flag_only(monkeypatch):
    from trading import cli

    parser = cli._parser()
    assert parser.parse_args(["run"]).drill_date is None
    assert parser.parse_args(["run", "--drill-date", "2026-10-14"]).drill_date == date(2026, 10, 14)
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(supervisor_module, "run_supervisor", lambda **kwargs: calls.append(kwargs) or 0)
    assert cli.main(["run", "--drill-date", "2026-10-14"]) == 0
    assert calls == [{"session_date": None, "drill_date": date(2026, 10, 14)}]
    backend = Path(__file__).resolve().parents[1]
    assert "drill" not in (backend / "trading/operations/settings.py").read_text().lower()  # no config key
    for module in ("trading/operations/supervisor.py", "trading/cli.py"):  # no environment variable
        assert re.search(r"(getenv|environ)[^\n]*drill", (backend / module).read_text(), re.I) is None

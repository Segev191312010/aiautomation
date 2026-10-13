"""One foreground process that runs every trading day's children by itself.

``autotraderctl run`` supervises, for each XNYS session, these child processes
inside America/New_York windows:

    screener  07:55-16:00  autotraderctl.py screener run --interval 60
    notifier  07:55-16:15  autotraderctl.py notify run --date D
    bridge    09:25-close  scripts_local/paper_bridge.py --date D   (16:00; 13:00 on a half day)
    orb       09:40-12:01  autotraderctl.py shadow run --strategy orb --universe scanner --date D
    momo      09:55-15:01  autotraderctl.py shadow run --strategy momo --universe watchlist --date D
    gap       09:28-10:31  autotraderctl.py shadow run --strategy gap --universe gappers --date D
    vwap      10:25-14:31  autotraderctl.py shadow run --strategy vwap --universe watchlist --date D
    recorder  16:20-17:30  autotraderctl.py research record
    replay    17:35-18:30  autotraderctl.py research replay

A strategy window ends one minute after that strategy's own entry cutoff, so a
half day's earlier MOMO cutoff moves its window in. Between sessions, and on
closed days, the process sleeps until the next session's 07:55 ET; it never
exits because a day ended (launchd's KeepAlive would only restart it).

The learning loop runs after the close: the recorder stores the session that
just closed in the research bar store (read-only TWS data client 28; it takes
no date because it always records the newest closed sessions, reaching back a
week so a missed night is filled in at no extra request), then the replay
walks every stored session through each strategy's rules, writes the reports
and, on the week's last session, sends their summary to Telegram. Both run
once: any clean exit is final, a failure is retried inside the window. Their
exits come after the notifier's 16:15 end, so they reach the phone only through
the weekly summary (which flags a session the recorder missed) and the
assistant's /status.

Always-on children run outside any session, for the supervisor's whole life:

    telegram  autotraderctl.py telegram run   (the owner's Telegram assistant)

They start when the supervisor starts, keep running between sessions and on
closed days, and stop only when the supervisor itself stops.

Restart policy. A child that exits before its window end is restarted after a
backoff: 95 s for strategies (longer than their 90 s shadow-run lease), 15 s
for the others, doubling to 5 minutes and starting over after a healthy run.
A clean exit (code 0) at or after a child's planned finish is its end of day
(the research children's planned finish is their start, so any clean exit),
and a refusal the child declares final (the bridge's 78) is never restarted;
nothing is (re)started at or after a window end. An always-on child is
restarted after any exit, whatever its code, with the same 15 s to 5 minute
backoff. At a window end, or when the supervisor receives SIGTERM, SIGINT or
SIGHUP, the child gets SIGTERM and, 20 s later, SIGKILL.

Log contract. ``supervisor.log`` lines

    <ISO UTC> CHILD_START <name> pid=<n>
    <ISO UTC> CHILD_EXIT <name> code=<n> restart_in=<seconds|none>

are tailed by the notifier; their format must not change. The children stop in
order on SIGTERM and exit 0; a child killed by signal N reports 128 + N (137
after SIGKILL); 127 means the child could not be launched. Each child's stdout
and stderr are appended to its own private ``<name>.log``.

The supervisor holds no broker connection and grants no authority; every child
enforces its own shadow or PAPER boundary.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
import fcntl
from functools import partial
import itertools
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
from typing import Protocol, TextIO

from trading.operations.paths import FILE_MODE, PathSecurityError, UserPaths, ensure_private_directory
from trading.scheduler.lease import DEFAULT_LEASE_TTL
from trading.scheduler.sessions import (
    MARKET_TIMEZONE,
    MarketSession,
    market_session,
    next_market_session,
)

UTC = timezone.utc
REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_DIR = REPO_ROOT / "backend"
# The venv's interpreter by its own path, never resolved: .venv/bin/python is
# a symlink to uv's base interpreter, and only the unresolved path loads the
# venv's packages. launchd's PATH has no venv, so nothing is looked up by name.
VENV_PYTHON = BACKEND_DIR / ".venv" / "bin" / "python"

SCREENER_WINDOW = (time(7, 55), time(16, 0))
NOTIFIER_WINDOW = (time(7, 55), time(16, 15))
# The notifier's 16:05 daily report; a clean exit after it is final.
NOTIFIER_FINISH = time(16, 5)
# paper_bridge.py exits 0 by itself five minutes before the close when flat,
# or just after its "still open" alert two minutes before it; the window must
# not cut that alert off. Both follow the session's own close: a half day's
# 13:00 close moves them to 12:55 and 13:00, where a fixed 15:55 finish would
# restart the bridge (and repeat its alert) every 15 s until 16:00.
BRIDGE_START = time(9, 25)
BRIDGE_FINISH_BEFORE_CLOSE = timedelta(minutes=5)
# EX_CONFIG: a refusal a restart cannot fix (non-paper account, future date,
# unusable config or state). Logged with restart_in=none so the phone alerts.
BRIDGE_REFUSED_CODE = 78
STRATEGY_STARTS = {
    "orb": time(9, 40),
    "momo": time(9, 55),
    "gap": time(9, 28),
    "vwap": time(10, 25),
}
STRATEGY_UNIVERSES = {"orb": "scanner", "momo": "watchlist", "gap": "gappers", "vwap": "watchlist"}
STRATEGY_WINDOW_TAIL = timedelta(minutes=1)
TELEGRAM_CHILD = "telegram"
# The learning loop, after the close: name -> ((start, end) ET, autotraderctl
# arguments). The recording (two paced requests per qualified contract, ~30
# minutes for ~400) must finish before the replay reads the store.
RECORDER_WINDOW = (time(16, 20), time(17, 30))
REPLAY_WINDOW = (time(17, 35), time(18, 30))
RESEARCH_CHILDREN: dict[str, tuple[tuple[time, time], tuple[str, ...]]] = {
    "recorder": (RECORDER_WINDOW, ("research", "record")),
    "replay": (REPLAY_WINDOW, ("research", "replay")),
}

# A restarted strategy must find its predecessor's shadow-run lease expired.
STRATEGY_BACKOFF = DEFAULT_LEASE_TTL + timedelta(seconds=5)
SERVICE_BACKOFF = timedelta(seconds=15)
MAX_BACKOFF = timedelta(minutes=5)
# A child that ran at least this long was healthy; its next failure backs off
# from the base delay again.
STABLE_RUN = timedelta(minutes=5)
TERMINATE_GRACE = timedelta(seconds=20)
# macOS stops the monotonic clock while asleep, so waits are cut into chunks
# and re-checked against wall-clock time.
MAX_SLEEP_CHUNK_SECONDS = 30.0
LAUNCH_FAILED_CODE = 127
SUPERVISOR_LOG_NAME = "supervisor.log"
LOCK_FILE_NAME = "supervisor.lock"
_SHUTDOWN_SIGNALS = (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)

Clock = Callable[[], datetime]
Sleeper = Callable[[float], Awaitable[None]]
Emit = Callable[[str], None]
StrategyCutoff = Callable[[str, date], datetime]


def _utc_now() -> datetime:
    return datetime.now(UTC)


def iso_utc(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat(timespec="seconds")


def child_start_line(moment: datetime, name: str, pid: int) -> str:
    return f"{iso_utc(moment)} CHILD_START {name} pid={pid}"


def child_exit_line(
    moment: datetime, name: str, code: int, restart_in: timedelta | None
) -> str:
    restart = "none" if restart_in is None else str(round(restart_in.total_seconds()))
    return f"{iso_utc(moment)} CHILD_EXIT {name} code={code} restart_in={restart}"


def exit_status(returncode: int) -> int:
    """Shell convention: a child killed by signal N reports 128 + N."""
    return 128 - returncode if returncode < 0 else returncode


@dataclass(frozen=True, slots=True)
class ChildPlan:
    """One child process for one session.

    ``ends_at`` is the window end: SIGTERM, then SIGKILL after the grace, and
    nothing (re)starts at or after it. A clean exit at or after ``finishes_at``
    is the child's planned end of day; an exit code in ``final_codes`` is a
    refusal that no restart can fix.
    """

    name: str
    argv: tuple[str, ...]
    starts_at: datetime
    ends_at: datetime
    finishes_at: datetime
    base_backoff: timedelta
    final_codes: frozenset[int] = frozenset()

    def __post_init__(self) -> None:
        for moment in (self.starts_at, self.ends_at, self.finishes_at):
            if moment.tzinfo is None or moment.utcoffset() is None:
                raise ValueError("child windows must be timezone-aware")
        if not self.starts_at < self.ends_at:
            raise ValueError(f"{self.name}: the window must start before it ends")
        if self.finishes_at > self.ends_at:
            raise ValueError(f"{self.name}: the planned finish must not follow the window end")
        if not self.argv or self.base_backoff <= timedelta(0):
            raise ValueError(f"{self.name}: a command and a positive backoff are required")


@dataclass(frozen=True, slots=True)
class AlwaysOnPlan:
    """A child that runs for the supervisor's whole life, outside any session.

    It has no window: it starts with the supervisor, is restarted after any
    exit (``base_backoff`` doubling to ``MAX_BACKOFF``), and is stopped only
    by the supervisor's own shutdown.
    """

    name: str
    argv: tuple[str, ...]
    base_backoff: timedelta = SERVICE_BACKOFF

    def __post_init__(self) -> None:
        if not self.argv or self.base_backoff <= timedelta(0):
            raise ValueError(f"{self.name}: a command and a positive backoff are required")


LaunchPlan = ChildPlan | AlwaysOnPlan


def always_on_plans(
    *, python: Path = VENV_PYTHON, backend: Path = BACKEND_DIR
) -> tuple[AlwaysOnPlan, ...]:
    """The children that run independently of trading sessions."""
    control = str(backend / "autotraderctl.py")
    return (AlwaysOnPlan(name=TELEGRAM_CHILD, argv=(str(python), control, "telegram", "run")),)


def strategy_cutoff(strategy_name: str, session_date: date) -> datetime:
    """The cutoff ``shadow run`` itself enforces, from the same strategy registry."""
    from trading.operations.shadow_service import strategy_entry_window

    return strategy_entry_window(strategy_name, session_date)[1]


def plan_session(
    session: MarketSession,
    *,
    python: Path = VENV_PYTHON,
    backend: Path = BACKEND_DIR,
    cutoff_for: StrategyCutoff = strategy_cutoff,
) -> tuple[ChildPlan, ...]:
    """The session's children in launch order, each with an explicit ``--date``."""
    day = session.session_date
    iso = day.isoformat()
    interpreter = str(python)
    control = str(backend / "autotraderctl.py")
    bridge = str(backend / "scripts_local" / "paper_bridge.py")

    def at(local: time) -> datetime:
        return datetime.combine(day, local, tzinfo=MARKET_TIMEZONE).astimezone(UTC)

    plans = [
        ChildPlan(
            name="screener",
            argv=(interpreter, control, "screener", "run", "--interval", "60"),
            starts_at=at(SCREENER_WINDOW[0]),
            ends_at=at(SCREENER_WINDOW[1]),
            finishes_at=at(SCREENER_WINDOW[1]),
            base_backoff=SERVICE_BACKOFF,
        ),
        ChildPlan(
            name="notifier",
            argv=(interpreter, control, "notify", "run", "--date", iso),
            starts_at=at(NOTIFIER_WINDOW[0]),
            ends_at=at(NOTIFIER_WINDOW[1]),
            finishes_at=at(NOTIFIER_FINISH),
            base_backoff=SERVICE_BACKOFF,
        ),
        ChildPlan(
            name="bridge",
            argv=(interpreter, bridge, "--date", iso),
            starts_at=at(BRIDGE_START),
            ends_at=session.closes_at.astimezone(UTC),
            finishes_at=(session.closes_at - BRIDGE_FINISH_BEFORE_CLOSE).astimezone(UTC),
            base_backoff=SERVICE_BACKOFF,
            final_codes=frozenset({BRIDGE_REFUSED_CODE}),
        ),
    ]
    for name, start in STRATEGY_STARTS.items():
        cutoff = cutoff_for(name, day).astimezone(UTC)
        if cutoff <= at(start):
            continue  # an early close leaves this strategy no entry window
        plans.append(
            ChildPlan(
                name=name,
                argv=(
                    interpreter,
                    control,
                    "shadow",
                    "run",
                    "--strategy",
                    name,
                    "--universe",
                    STRATEGY_UNIVERSES[name],
                    "--date",
                    iso,
                ),
                starts_at=at(start),
                ends_at=cutoff + STRATEGY_WINDOW_TAIL,
                finishes_at=cutoff,
                base_backoff=STRATEGY_BACKOFF,
            )
        )
    for name, ((start, end), command) in RESEARCH_CHILDREN.items():
        plans.append(
            ChildPlan(
                name=name,
                argv=(interpreter, control, *command),
                starts_at=at(start),
                ends_at=at(end),
                # Run once: a clean exit is done for the day whenever it comes;
                # a failure is retried with the service backoff inside the window.
                finishes_at=at(start),
                base_backoff=SERVICE_BACKOFF,
            )
        )
    return tuple(plans)


Planner = Callable[[MarketSession], Sequence[ChildPlan]]


def session_end(plans: Sequence[ChildPlan]) -> datetime:
    return max(plan.ends_at for plan in plans)


def upcoming_session(
    now: datetime, planner: Planner = plan_session
) -> tuple[MarketSession, tuple[ChildPlan, ...]]:
    """Today's session until its last window ends, else the next session."""
    local_day = now.astimezone(MARKET_TIMEZONE).date()
    session = market_session(local_day)
    if session is not None:
        plans = tuple(planner(session))
        if now < session_end(plans):
            return session, plans
    session = next_market_session(local_day)
    return session, tuple(planner(session))


class ChildProcess(Protocol):
    @property
    def pid(self) -> int: ...

    @property
    def returncode(self) -> int | None: ...

    async def wait(self) -> int: ...

    def send_signal(self, signum: int) -> None: ...


class Launcher(Protocol):
    async def start(self, plan: LaunchPlan) -> ChildProcess: ...


@dataclass(slots=True)
class _Child:
    plan: LaunchPlan
    backoff: timedelta
    next_start: datetime | None
    process: ChildProcess | None = None
    exited: asyncio.Future[int] | None = None
    started_at: datetime | None = None
    terminated_at: datetime | None = None
    killed: bool = False

    @property
    def running(self) -> bool:
        return self.process is not None

    @property
    def has_exited(self) -> bool:
        return self.process is not None and self.process.returncode is not None

    @property
    def window_end(self) -> datetime | None:
        """The session window end; an always-on child has none."""
        return self.plan.ends_at if isinstance(self.plan, ChildPlan) else None


class Supervisor:
    """Runs each session's children inside their windows, and the always-on
    children for its whole life; injectable clock and launcher."""

    def __init__(
        self,
        *,
        launcher: Launcher,
        emit: Emit,
        planner: Planner = plan_session,
        always: Sequence[AlwaysOnPlan] = (),
        clock: Clock = _utc_now,
        sleep: Sleeper = asyncio.sleep,
        sleep_chunk_seconds: float = MAX_SLEEP_CHUNK_SECONDS,
    ) -> None:
        if not sleep_chunk_seconds > 0:
            raise ValueError("sleep_chunk_seconds must be positive")
        names = [plan.name for plan in always]
        if len(set(names)) != len(names):
            raise ValueError("always-on children need distinct names")
        self._launcher = launcher
        self._emit = emit
        self._planner = planner
        self._clock = clock
        self._sleep = sleep
        self._chunk = sleep_chunk_seconds
        self._stop = asyncio.Event()
        self._stop_reason = "completed"
        # next_start is set when run() begins: they start with the supervisor.
        self._always = [
            _Child(plan=plan, backoff=plan.base_backoff, next_start=None) for plan in always
        ]
        self._retiring = False

    @property
    def stopping(self) -> bool:
        return self._stop.is_set()

    def request_stop(self, signum: int | None = None) -> None:
        """Begin an orderly shutdown; safe to call from a signal handler callback."""
        self._halt(signal.Signals(signum).name if signum is not None else "requested")

    def _halt(self, reason: str) -> None:
        if not self._stop.is_set():
            self._stop_reason = reason
            self._stop.set()

    async def run(self, *, session_date: date | None = None) -> int:
        """Supervise every session (or only ``session_date``); 0 after an orderly stop."""
        mode = "daily" if session_date is None else f"once date={session_date.isoformat()}"
        self._event(f"SUPERVISOR_START pid={os.getpid()} mode={mode}")
        try:
            started = self._clock()
            for child in self._always:
                child.next_start = started
            await self._tend_always(started)
            completed: date | None = None
            while not self.stopping:
                scheduled = self._next_session(session_date, after=completed)
                if scheduled is None:
                    assert session_date is not None
                    self._event(f"SESSION_OVER date={session_date.isoformat()}")
                    break
                session, plans = scheduled
                day = session.session_date.isoformat()
                opens = min(plan.starts_at for plan in plans)
                if self._clock() < opens:
                    self._event(f"SESSION_WAIT date={day} until={iso_utc(opens)}")
                    await self._wait_until(opens)
                    if self.stopping:
                        break
                self._event(f"SESSION_START date={day}")
                await self.run_session(plans)
                self._event(f"SESSION_END date={day}")
                completed = session.session_date
                if session_date is not None:
                    break
            await self._retire_always()
        except BaseException:
            self._halt("supervisor_error")
            await self._abandon(self._always)
            raise
        finally:
            self._event(f"SUPERVISOR_STOP reason={self._stop_reason}")
        return 0

    async def run_session(self, plans: Sequence[ChildPlan]) -> None:
        children = [
            _Child(plan=plan, backoff=plan.base_backoff, next_start=plan.starts_at)
            for plan in plans
        ]
        try:
            await self._supervise(children)
        finally:
            await self._abandon(children)

    def _next_session(
        self, requested: date | None, *, after: date | None = None
    ) -> tuple[MarketSession, tuple[ChildPlan, ...]] | None:
        now = self._clock()
        if requested is None:
            session, plans = upcoming_session(now, self._planner)
            if after is not None and session.session_date <= after:
                # Every child finished before the last window end (the replay
                # usually does): re-entering the day would relaunch each child
                # whose window is still open, so the next session follows.
                session = next_market_session(after)
                plans = tuple(self._planner(session))
            return session, plans
        session = market_session(requested)
        if session is None:
            raise ValueError(f"{requested.isoformat()} is not an XNYS trading session")
        plans = tuple(self._planner(session))
        return (session, plans) if now < session_end(plans) else None

    async def _supervise(self, children: list[_Child]) -> None:
        """Until every session child is done; the always-on children are tended
        alongside and keep running when this returns."""
        everyone = [*children, *self._always]
        while True:
            now = self._clock()
            for child in everyone:
                self._reap(child, now)
            if self.stopping:
                for child in everyone:
                    child.next_start = None
                    self._terminate(child, now, reason="shutdown")
            else:
                for child in everyone:
                    end = child.window_end
                    if child.running and end is not None and now >= end:
                        self._terminate(child, now, reason="window_end")
                    elif (
                        not child.running
                        and child.next_start is not None
                        and now >= child.next_start
                    ):
                        await self._start(child, now)
            if any(child.has_exited for child in everyone):
                continue  # reap at the moment the exit was observed
            if not any(child.running or child.next_start is not None for child in children):
                return
            await self._wait(self._next_deadline(everyone), everyone)

    async def _tend_always(self, now: datetime) -> None:
        """Reap exited always-on children and (re)start the ones that are due."""
        for child in self._always:
            self._reap(child, now)
        if self.stopping:
            return
        for child in self._always:
            if not child.running and child.next_start is not None and now >= child.next_start:
                await self._start(child, now)

    async def _retire_always(self) -> None:
        """The supervisor's own shutdown: the only way an always-on child stops."""
        self._retiring = True
        for child in self._always:
            child.next_start = None
        while True:
            now = self._clock()
            for child in self._always:
                self._reap(child, now)
            running = [child for child in self._always if child.running]
            if not running:
                return
            for child in running:
                self._terminate(child, now, reason="shutdown")
            if any(child.has_exited for child in running):
                continue
            await self._wait(self._next_deadline(running), running)

    def _reap(self, child: _Child, now: datetime) -> None:
        process = child.process
        if process is None or process.returncode is None:
            return
        code = exit_status(process.returncode)
        ran = now - child.started_at if child.started_at is not None else timedelta(0)
        if child.exited is not None and not child.exited.done():
            child.exited.cancel()
        child.process = None
        child.exited = None
        child.started_at = None
        child.terminated_at = None
        child.killed = False
        self._after_exit(child, code, now, ran)

    def _after_exit(self, child: _Child, code: int, now: datetime, ran: timedelta) -> None:
        restart_in = self._restart_delay(child, code, now, ran)
        self._emit(child_exit_line(now, child.plan.name, code, restart_in))
        if restart_in is None:
            child.next_start = None
            return
        child.next_start = now + restart_in
        child.backoff = min(restart_in * 2, MAX_BACKOFF)

    def _restart_delay(
        self, child: _Child, code: int, now: datetime, ran: timedelta
    ) -> timedelta | None:
        plan = child.plan
        if self.stopping:
            return None
        if isinstance(plan, AlwaysOnPlan):
            # Any exit, whatever its code; only the supervisor's shutdown ends it.
            if self._retiring:
                return None
            return plan.base_backoff if ran >= STABLE_RUN else child.backoff
        if now >= plan.ends_at or code in plan.final_codes:
            return None
        if code == 0 and now >= plan.finishes_at:
            return None
        delay = plan.base_backoff if ran >= STABLE_RUN else child.backoff
        if now + delay >= plan.ends_at:
            return None
        return delay

    async def _start(self, child: _Child, now: datetime) -> None:
        child.next_start = None
        end = child.window_end
        if end is not None and now >= end:
            return
        try:
            process = await self._launcher.start(child.plan)
        except Exception as exc:  # noqa: BLE001 - a failed launch is retried like an exit
            failed_at = self._clock()
            self._event(f"LAUNCH_FAILED {child.plan.name} error={type(exc).__name__}")
            self._after_exit(child, LAUNCH_FAILED_CODE, failed_at, timedelta(0))
            return
        started_at = self._clock()
        child.process = process
        child.started_at = started_at
        child.exited = asyncio.ensure_future(process.wait())
        self._emit(child_start_line(started_at, child.plan.name, process.pid))

    def _terminate(self, child: _Child, now: datetime, *, reason: str) -> None:
        if child.process is None or child.process.returncode is not None:
            return
        if child.terminated_at is None:
            child.terminated_at = now
            self._signal(child, signal.SIGTERM, reason)
        elif not child.killed and now >= child.terminated_at + TERMINATE_GRACE:
            child.killed = True
            self._signal(child, signal.SIGKILL, reason)

    def _signal(self, child: _Child, signum: signal.Signals, reason: str) -> None:
        assert child.process is not None
        # Signal first: a failing log write must never leave a child running.
        try:
            child.process.send_signal(signum)
        except OSError as exc:
            self._event(f"SIGNAL_FAILED {child.plan.name} {signum.name} error={type(exc).__name__}")
            return
        self._event(f"SIGNAL {child.plan.name} {signum.name} reason={reason}")

    @staticmethod
    def _next_deadline(children: Sequence[_Child]) -> datetime | None:
        moments: list[datetime] = []
        for child in children:
            if not child.running:
                if child.next_start is not None:
                    moments.append(child.next_start)
            elif child.terminated_at is None:
                if child.window_end is not None:
                    moments.append(child.window_end)
            elif not child.killed:
                moments.append(child.terminated_at + TERMINATE_GRACE)
        return min(moments, default=None)

    async def _wait(self, deadline: datetime | None, children: Sequence[_Child]) -> None:
        """Until the deadline (at most one chunk), a child exit, or a stop request."""
        seconds = self._chunk
        if deadline is not None:
            seconds = min(seconds, max(0.0, (deadline - self._clock()).total_seconds()))
        timer = asyncio.ensure_future(self._sleep(seconds))
        stop = None if self.stopping else asyncio.ensure_future(self._stop.wait())
        waiters: set[asyncio.Future[object]] = {timer}
        if stop is not None:
            waiters.add(stop)
        waiters.update(
            child.exited for child in children if child.exited is not None and not child.exited.done()
        )
        try:
            await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
        finally:
            timer.cancel()
            if stop is not None:
                stop.cancel()

    async def _wait_until(self, moment: datetime) -> None:
        """Between sessions: only the always-on children are tended."""
        while not self.stopping:
            now = self._clock()
            await self._tend_always(now)
            if self.stopping or now >= moment:
                return
            if any(child.has_exited for child in self._always):
                continue
            restarts = self._next_deadline(self._always)
            deadline = moment if restarts is None else min(moment, restarts)
            await self._wait(deadline, self._always)

    async def _abandon(self, children: Sequence[_Child]) -> None:
        """Error path: never leave a child running without its supervisor.

        Signals go straight to the processes and every log write is best
        effort, because the error being handled may be a failing log.
        """
        processes = [child.process for child in children if child.process is not None]
        if not processes:
            return
        self._halt("supervisor_error")
        for process in processes:
            _signal_quietly(process, signal.SIGTERM)
        try:
            await asyncio.wait_for(
                asyncio.gather(*(process.wait() for process in processes), return_exceptions=True),
                TERMINATE_GRACE.total_seconds(),
            )
        except asyncio.TimeoutError:
            for process in processes:
                _signal_quietly(process, signal.SIGKILL)
            await asyncio.gather(*(process.wait() for process in processes), return_exceptions=True)
        for child in children:
            try:
                self._reap(child, self._clock())
            except Exception:  # noqa: BLE001 - the children are already stopped
                pass

    def _event(self, message: str) -> None:
        self._emit(f"{iso_utc(self._clock())} {message}")


def _signal_quietly(process: ChildProcess, signum: int) -> None:
    if process.returncode is None:
        try:
            process.send_signal(signum)
        except OSError:
            pass


def open_private_append(path: Path) -> int:
    """Open a private (0600) regular file for appending; a symlink is refused."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_CLOEXEC | os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, FILE_MODE)
    except OSError as exc:
        if path.is_symlink():
            raise PathSecurityError("supervisor logs must not be symbolic links") from exc
        raise
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise PathSecurityError("supervisor logs must be regular files")
        os.fchmod(descriptor, FILE_MODE)
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


class _GroupLeader:
    """A spawned child. start_new_session made it the leader of its own process
    group, so signals reach that group only, never the supervisor's own group
    (where launchd's caffeinate helper lives)."""

    def __init__(self, process: asyncio.subprocess.Process) -> None:
        self._process = process

    @property
    def pid(self) -> int:
        return self._process.pid

    @property
    def returncode(self) -> int | None:
        return self._process.returncode

    async def wait(self) -> int:
        return await self._process.wait()

    def send_signal(self, signum: int) -> None:
        if self._process.returncode is not None:
            return
        try:
            os.killpg(self._process.pid, signum)
        except ProcessLookupError:
            pass
        except PermissionError:
            self._process.send_signal(signum)


class SubprocessLauncher:
    """Spawns each child in its own session, appending its output to ``<name>.log``."""

    def __init__(
        self,
        log_directory: Path,
        *,
        cwd: Path = BACKEND_DIR,
        environment: Mapping[str, str] | None = None,
        clock: Clock = _utc_now,
    ) -> None:
        self._log_directory = log_directory
        self._cwd = cwd
        self._environment = dict(os.environ if environment is None else environment)
        self._environment.update({"PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"})
        self._clock = clock

    def log_path(self, name: str) -> Path:
        return self._log_directory / f"{name}.log"

    async def start(self, plan: ChildPlan) -> ChildProcess:
        ensure_private_directory(self._log_directory)
        descriptor = open_private_append(self.log_path(plan.name))
        try:
            header = f"=== {iso_utc(self._clock())} supervisor starts {plan.name}: {' '.join(plan.argv)}\n"
            os.write(descriptor, header.encode("utf-8"))
            process = await asyncio.create_subprocess_exec(
                *plan.argv,
                stdin=subprocess.DEVNULL,
                stdout=descriptor,
                stderr=subprocess.STDOUT,
                cwd=str(self._cwd),
                env=self._environment,
                start_new_session=True,
            )
        finally:
            os.close(descriptor)
        return _GroupLeader(process)


class SupervisorLog:
    """Append-only private ``supervisor.log``, echoed to the terminal when attached."""

    def __init__(self, path: Path, *, echo: TextIO | None = None) -> None:
        ensure_private_directory(path.parent)
        self._stream = os.fdopen(open_private_append(path), "a", encoding="utf-8")
        self._echo = echo

    def __call__(self, line: str) -> None:
        self._stream.write(line + "\n")
        self._stream.flush()
        if self._echo is not None:
            try:
                print(line, file=self._echo, flush=True)
            except (OSError, ValueError):
                self._echo = None  # the terminal went away; the log file remains

    def close(self) -> None:
        self._stream.close()


class AlreadyRunning(RuntimeError):
    """Another process holds a single-instance lock."""


class SupervisorAlreadyRunning(AlreadyRunning):
    """Another supervisor holds the lock; two would launch duplicate children."""


@contextmanager
def single_instance(
    lock_path: Path,
    *,
    label: str = "supervisor",
    error: type[AlreadyRunning] = SupervisorAlreadyRunning,
) -> Iterator[None]:
    """Hold an exclusive lock for the process lifetime; the kernel drops it on exit."""
    ensure_private_directory(lock_path.parent)
    descriptor = os.open(
        lock_path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW, FILE_MODE
    )
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            holder = os.pread(descriptor, 32, 0).decode("ascii", "replace").strip() or "unknown"
            raise error(f"another {label} is already running (pid {holder})") from exc
        os.ftruncate(descriptor, 0)
        os.pwrite(descriptor, f"{os.getpid()}\n".encode("ascii"), 0)
        yield
    finally:
        os.close(descriptor)


def missing_entry_points(*, python: Path = VENV_PYTHON, backend: Path = BACKEND_DIR) -> list[Path]:
    candidates = (
        python,
        backend / "autotraderctl.py",
        backend / "scripts_local" / "paper_bridge.py",
    )
    return [path for path in candidates if not path.exists()]


# A hand-started screener idles overnight and resumes at 08:00 on its own; it
# would hold the supervised child's TWS client ID with no CHILD_EXIT to alert on.
# A hand-started Telegram assistant holds the single getUpdates consumer lock.
_UNSUPERVISED_PATTERN = (
    r"^[^ ]*python[^ ]* .*(autotraderctl\.py (screener|shadow|notify|telegram) run"
    r"|paper_bridge\.py)"
)


def unsupervised_children() -> list[tuple[int, str]]:
    """Trading processes already running outside this supervisor; reported, never killed."""
    try:
        result = subprocess.run(
            ["/usr/bin/pgrep", "-fl", _UNSUPERVISED_PATTERN],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    found: list[tuple[int, str]] = []
    for line in result.stdout.splitlines():
        pid, _, command = line.strip().partition(" ")
        if pid.isdigit() and int(pid) != os.getpid():
            found.append((int(pid), command))
    return found


async def _run_until_signalled(supervisor: Supervisor, session_date: date | None) -> int:
    loop = asyncio.get_running_loop()
    for signum in _SHUTDOWN_SIGNALS:
        loop.add_signal_handler(signum, supervisor.request_stop, signum)
    try:
        return await supervisor.run(session_date=session_date)
    finally:
        for signum in _SHUTDOWN_SIGNALS:
            loop.remove_signal_handler(signum)


def run_supervisor(
    *,
    session_date: date | None = None,
    user_paths: UserPaths | None = None,
    python: Path = VENV_PYTHON,
    backend: Path = BACKEND_DIR,
    echo: TextIO | None = None,
    unsupervised: Callable[[], list[tuple[int, str]]] = unsupervised_children,
) -> int:
    """``autotraderctl run``: supervise in the foreground until signalled."""
    paths = user_paths or UserPaths.current()
    if session_date is not None and market_session(session_date) is None:
        print(f"error: {session_date.isoformat()} is not an XNYS trading session", file=sys.stderr)
        return 1
    try:
        with single_instance(paths.data_directory / LOCK_FILE_NAME):
            log = SupervisorLog(
                paths.log_directory / SUPERVISOR_LOG_NAME,
                echo=sys.stdout if echo is None else echo,
            )
            try:
                for path in missing_entry_points(python=python, backend=backend):
                    log(f"{iso_utc(_utc_now())} PREFLIGHT_WARNING missing={path}")
                for pid, command in unsupervised():
                    log(
                        f"{iso_utc(_utc_now())} PREFLIGHT_WARNING unsupervised pid={pid} "
                        f"(stop it: kill {pid}) cmd={command}"
                    )
                supervisor = Supervisor(
                    launcher=SubprocessLauncher(paths.log_directory, cwd=backend),
                    emit=log,
                    planner=partial(plan_session, python=python, backend=backend),
                    always=always_on_plans(python=python, backend=backend),
                )
                return asyncio.run(_run_until_signalled(supervisor, session_date))
            finally:
                log.close()
    except SupervisorAlreadyRunning as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


class _SimulatedClock:
    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)
        await asyncio.sleep(0)


class _SimulatedChild:
    """Runs until signalled; like the real children, SIGTERM is an orderly exit 0."""

    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.returncode: int | None = None
        self._exited = asyncio.Event()

    async def wait(self) -> int:
        await self._exited.wait()
        assert self.returncode is not None
        return self.returncode

    def send_signal(self, signum: int) -> None:
        if self.returncode is None:
            self.returncode = 0 if signum == signal.SIGTERM else -signum
            self._exited.set()


class _SimulatedLauncher:
    def __init__(self) -> None:
        self._pids = itertools.count(1)

    async def start(self, plan: ChildPlan) -> ChildProcess:
        return _SimulatedChild(next(self._pids))


async def simulate_session(
    session: MarketSession, plans: Sequence[ChildPlan], *, start: datetime
) -> list[tuple[datetime, str]]:
    """Drive the real supervisor over one session on a virtual clock; nothing launches."""
    clock = _SimulatedClock(start)
    events: list[tuple[datetime, str]] = []
    supervisor = Supervisor(
        launcher=_SimulatedLauncher(),
        emit=lambda line: events.append((clock.now, line)),
        planner=lambda _session: plans,
        clock=clock,
        sleep=clock.sleep,
        sleep_chunk_seconds=timedelta(days=1).total_seconds(),
    )
    await supervisor.run(session_date=session.session_date)
    return events


def _display_command(argv: Sequence[str]) -> str:
    shown: list[str] = []
    for argument in argv[1:]:
        try:
            shown.append(str(Path(argument).relative_to(REPO_ROOT)))
        except ValueError:
            shown.append(argument)
    return " ".join(shown)


def _local(moment: datetime, pattern: str = "%H:%M") -> str:
    return moment.astimezone(MARKET_TIMEZONE).strftime(pattern)


def dry_run_report(
    *,
    session_date: date | None = None,
    clock_start: time | None = None,
    now: datetime | None = None,
    user_paths: UserPaths | None = None,
    python: Path = VENV_PYTHON,
    backend: Path = BACKEND_DIR,
    unsupervised: Callable[[], list[tuple[int, str]]] = unsupervised_children,
) -> str:
    """The planned child timeline for one session, simulated; launches and writes nothing."""
    current = now or _utc_now()
    planner = partial(plan_session, python=python, backend=backend)
    if session_date is None:
        session, plans = upcoming_session(current, planner)
    else:
        found = market_session(session_date)
        if found is None:
            raise ValueError(f"{session_date.isoformat()} is not an XNYS trading session")
        session, plans = found, tuple(planner(found))
    if clock_start is not None:
        start = datetime.combine(session.session_date, clock_start, tzinfo=MARKET_TIMEZONE)
    elif session.session_date == current.astimezone(MARKET_TIMEZONE).date():
        start = current
    else:
        start = datetime.combine(session.session_date, time(0, 0), tzinfo=MARKET_TIMEZONE)
    events = asyncio.run(simulate_session(session, plans, start=start.astimezone(UTC)))
    logs = (user_paths or UserPaths.current()).log_directory
    lines = [
        "DRY RUN: nothing is launched and no log is written.",
        f"session      {session.session_date.isoformat()} ({session.session_date:%a}), "
        f"XNYS {_local(session.opens_at)}-{_local(session.closes_at)} ET"
        + (" (half day)" if session.half_day else ""),
        f"clock start  {_local(start, '%Y-%m-%d %H:%M')} ET (simulated)",
        f"python       {python}",
        f"logs         {logs}/<child>.log; CHILD_* lines in {logs}/{SUPERVISOR_LOG_NAME}",
        "",
        f"{'child':<9} {'window ET':<12} {'final exit 0':<13} {'backoff':<10} command",
    ]
    for plan in plans:
        lines.append(
            f"{plan.name:<9} {_local(plan.starts_at)}-{_local(plan.ends_at):<6} "
            f"{'from ' + _local(plan.finishes_at):<13} "
            f"{f'{plan.base_backoff.seconds}-{MAX_BACKOFF.seconds}s':<10} "
            f"{_display_command(plan.argv)}"
        )
    for plan in plans:
        if plan.final_codes:
            codes = ", ".join(str(code) for code in sorted(plan.final_codes))
            lines.append(f"{plan.name} exit {codes} is a refusal: logged, never restarted")
    for always in always_on_plans(python=python, backend=backend):
        lines.append(
            f"{always.name:<9} {'always-on':<12} {'never':<13} "
            f"{f'{always.base_backoff.seconds}-{MAX_BACKOFF.seconds}s':<10} "
            f"{_display_command(always.argv)}"
        )
        lines.append(
            f"{always.name} runs from supervisor start to supervisor stop, between sessions"
            " too; restarted after any exit (not shown in the timeline below)"
        )
    lines += ["", "simulated timeline (ET; pids are placeholders):"]
    for moment, line in events:
        lines.append(f"{_local(moment, '%H:%M:%S')}  {line.split(' ', 1)[1]}")
    for path in missing_entry_points(python=python, backend=backend):
        lines.append(f"WARNING: missing {path}")
    for pid, command in unsupervised():
        lines.append(f"WARNING: running outside the supervisor (kill {pid} before `run`): {command}")
    return "\n".join(lines)


__all__ = [
    "BACKEND_DIR",
    "AlreadyRunning",
    "AlwaysOnPlan",
    "ChildPlan",
    "ChildProcess",
    "Launcher",
    "MAX_BACKOFF",
    "RECORDER_WINDOW",
    "REPLAY_WINDOW",
    "RESEARCH_CHILDREN",
    "SERVICE_BACKOFF",
    "STRATEGY_BACKOFF",
    "Supervisor",
    "SupervisorAlreadyRunning",
    "SupervisorLog",
    "SubprocessLauncher",
    "TELEGRAM_CHILD",
    "TERMINATE_GRACE",
    "VENV_PYTHON",
    "always_on_plans",
    "child_exit_line",
    "child_start_line",
    "dry_run_report",
    "exit_status",
    "missing_entry_points",
    "open_private_append",
    "plan_session",
    "run_supervisor",
    "simulate_session",
    "single_instance",
    "strategy_cutoff",
    "unsupervised_children",
    "upcoming_session",
]

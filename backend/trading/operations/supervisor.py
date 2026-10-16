"""One foreground process that runs every trading day's children by itself.

``autotraderctl run`` supervises, for each XNYS session, these child processes
inside America/New_York windows:

    screener  07:55-16:00  autotraderctl.py screener run --interval 60
    notifier  07:55-16:15  autotraderctl.py notify run --date D
    bridge    09:25-close  scripts_local/paper_bridge.py --date D   (16:00; 13:00 on a half day)
    orb-v2    09:28-15:31  autotraderctl.py shadow run --strategy orb-v2 --universe scanner --date D
    momo      09:55-15:01  autotraderctl.py shadow run --strategy momo --universe watchlist --date D
    gap-v2    09:28-15:31  autotraderctl.py shadow run --strategy gap-v2 --universe all-gappers --date D
    vwap-v2   09:28-15:31  autotraderctl.py shadow run --strategy vwap-v2 --universe watchlist --date D
    manager   09:15-14:40  autotraderctl.py manager run --date D   (day plans; shadow, no TWS)
    momo-ap   09:55-15:01  autotraderctl.py shadow run --strategy momo-ap --universe watchlist --date D
    recorder  16:20-17:30  autotraderctl.py research record
    replay    17:35-18:30  autotraderctl.py research replay

A strategy window ends one minute after that strategy's own entry cutoff, so a
half day's earlier cutoffs move their windows in (MOMO 12:00, the all-session
simulations 12:30). The all-session simulations send real PAPER orders only
when their names are sendable identities (bridge v7b); each sleeps until its
own data is due. The AI day
manager's children run in shadow whatever the autopilot switch says:
``manager`` writes the day plans (Claude and the database only, no TWS client)
from five minutes before its first slot to ten minutes after its last
(half-day aware), and ``momo-ap`` is managed MOMO (AP_MOMO_HOD_V1, never
sendable) in MOMO's own window.

Between sessions, and on closed days, the process sleeps until the next
session's 07:55 ET; it never exits because a day ended (launchd's KeepAlive
would only restart it).

The learning loop runs after the close: the recorder stores the session that
just closed in the research bar store (read-only TWS data client 28; it takes
no date because it always records the newest closed sessions, and reaches back
three weeks, so the first night fills the sample and a missed night is filled
in by the next), then the replay
walks every stored session through each strategy's rules, writes the reports,
runs the nightly evidence inside the same window (the signal-outcome tracker,
the live R ledger, the paper promotion rule, and the window research when it
can start by 18:00; ``trading.research.nightly``) and, on the week's last
session, sends their summary to Telegram. Both run
once: any clean exit is final, a failure is retried inside the window. Their
exits come after the notifier's 16:15 end, so they reach the phone only through
the weekly summary (which flags a session the recorder missed) and the
assistant's /status.

Always-on children run outside any session, for the supervisor's whole life:

    telegram   autotraderctl.py telegram run          (the owner's Telegram assistant)
    heartbeat  python -m trading.operations.heartbeat (the dead-man's switch's
               pings; scheduled only when its Keychain item exists)

They start when the supervisor starts, keep running between sessions and on
closed days, and stop only when the supervisor itself stops. The heartbeat's
URL is read from the Keychain at the supervisor's start (an item added later
takes effect at the next start) and by the child itself, never passed in argv.

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

Restart cap. Each child's restartable exits are counted in a rolling 30
minutes, but only while the IB Gateway's port (127.0.0.1:7497) answers: the
supervisor probes it when it reaps such an exit (one probe per loop pass,
shared by every exit and port re-check in that pass), and an exit with the
port closed is not counted, so a Gateway outage (2026-10-14: 20-25 exits per
child before the 11:29 ET login) never suspends anything and never costs a
trading day. Nor is a scheduled child's exit 75: for those children it is
only the schema gate's refusal (below), which the notifier's schema alert
already pages and which ends by itself after ``db migrate``. At 6 counted
exits a scheduled child other than the bridge is suspended: from then on it is
relaunched only as a probe every 5 minutes, and the suspension lifts when a
probe stays up 5 minutes, when the port was seen closed and opens again
(relaunched at once), at its window end or the session's end, or when the
supervisor stops (a new supervisor starts with empty ledgers). The bridge
(its positions are protected at IB) and the always-on children are never
suspended: they only page a restart storm and keep their backoff.

No child migrates the database. One that finds a migration pending exits 75
(``trading.operations.schema_gate``), which is never final: it is retried
like any failure and starts by itself once ``autotraderctl db migrate`` has
run. The dry run reads the schema (read-only) and says so on its second line.

Log contract. ``supervisor.log`` lines

    <ISO UTC> CHILD_START <name> pid=<n>
    <ISO UTC> CHILD_EXIT <name> code=<n> restart_in=<seconds|none>

are tailed by the notifier; their format must not change. The restart cap
adds, after the CHILD_EXIT line of the exit concerned and in this order,

    <ISO UTC> CHILD_RESUMED <name> reason=probe_healthy    (the exiting probe had run 5 min)
    <ISO UTC> PORT_PROBE_FAILED error=<exception class>
    <ISO UTC> CHILD_EXIT_UNCOUNTED <name> reason=port_closed|port_unknown|schema_not_ready
    <ISO UTC> CHILD_SUSPENDED <name> count=<n> window=30m
    <ISO UTC> CHILD_RESTART_STORM <name> count=<n> window=30m

and, on their own, ``CHILD_RESUMED <name>
reason=probe_healthy|port_open|window_boundary|supervisor_stop`` (a probe
still running at 5 minutes, a port re-check, a window or session end, the
supervisor's stop before its children are signalled) and
``PORT_PROBE_FAILED`` for a failed re-check. CHILD_SUSPENDED comes once per
suspension, CHILD_RESTART_STORM once per storm, CHILD_RESUMED only for a
suspended child; ``reason=port_unknown`` means the probe itself failed (a
``PORT_PROBE_FAILED`` line in the same loop pass says why, once per pass), so
the exit is not counted. The children stop in
order on SIGTERM and exit 0; a child killed by signal N reports 128 + N (137
after SIGKILL); 127 means the child could not be launched. Each child's stdout
and stderr are appended to its own private ``<name>.log``.

Log rotation (trading.operations.logging). Between sessions, at 18:35 ET each
day (after the replay window), ``supervisor.log``, every child's ``<name>.log``
and launchd's ``launchd.stdout.log``/``launchd.stderr.log`` rotate into
``<name>.log.YYYY-MM-DD``, kept 14 days. ``supervisor.log`` is renamed and
reopened, and the newest state lines (this run's SUPERVISOR_START, the newest
SESSION_* line, each child's newest CHILD_START or CHILD_EXIT) are copied,
verbatim with their original times, to the top of the new file, so a reader
of the new file alone (the Telegram /status, the heartbeat) still sees the
running supervisor. A child log nobody holds is renamed; a running always-on
child's log and launchd's files (held open by those processes) are copied then
truncated in place. A rotation missed while the supervisor was down is made
up between sessions at the next start; it is logged as

    <ISO UTC> LOG_ROTATED date=<YYYY-MM-DD> renamed=<n> truncated=<n> pruned=<n> carried=<n>
    <ISO UTC> LOG_ROTATION_FAILED file=<name> error=<type>   (one per failed file)

The supervisor holds no broker connection and grants no authority; every child
enforces its own shadow or PAPER boundary.
"""
from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable, Collection, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
import fcntl
from functools import partial
import hashlib
import itertools
import os
from pathlib import Path
import re
import signal
import socket
import stat
import subprocess
import sys
from typing import Protocol, TextIO

from trading.operations.heartbeat import (
    HEARTBEAT_CHILD,
    HEARTBEAT_MODULE,
    PresenceSource as HeartbeatPresenceSource,
    UrlSource as HeartbeatUrlSource,
    item_present as heartbeat_item_present,
    load_target as load_heartbeat_target,
    presence_detail as heartbeat_presence_detail,
    url_from_keychain as heartbeat_url_from_keychain,
)
from trading.operations.logging import (
    ROTATION_KEEP_DAYS,
    ROTATION_TIME,
    archive_path,
    latest_rotation_boundary,
    next_rotation_boundary,
    prune_archives,
    rotate_by_copy_truncate,
    rotate_by_rename,
)
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
# The notifier's own restart-cap lines are the Telegram child's to forward, at any
# hour: a suspended notifier cannot report its own suspension (N15b L-3).
NOTIFIER_CHILD = "notifier"
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
BRIDGE_CHILD = "bridge"
# trading.operations.schema_gate.EXIT_SCHEMA_NOT_READY (pinned by a test): a
# scheduled child's refusal to start on a schema that is not the code's. The
# bridge's own 75 ("TWS did not answer") is a different exit: it never reaches
# the rule that uses this, because the bridge is never suspended.
SCHEMA_REFUSAL_CODE = 75
# The registry preflight: the strategy registry, the sendable tuple and the
# identity size table must agree (shadow_service.registry_problems) or nothing
# launches. Logged as LAUNCH_REFUSED registry code=78 reason=<token>, the
# same line the notifier forwards for an unreviewed bridge.
REGISTRY_CHILD = "registry"
# Children run with the supervisor's environment plus these; the dry run's
# bridge preflight uses exactly the same environment, interpreter and cwd.
CHILD_ENVIRONMENT_EXTRA = {"PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}
BRIDGE_PREFLIGHT_FLAG = "--preflight"
BRIDGE_PREFLIGHT_TIMEOUT_S = 60.0
# The bridge writes notifier event lines, ``HH:MM:SS ET  <KIND> <text>``; the
# dry run relays the KIND and text and drops the clock, which is not the session's.
_BRIDGE_EVENT_CLOCK = re.compile(r"^\d{2}:\d{2}:\d{2} ET  (?=[A-Z]+ )")
# The bridge file the last independent execution-safety review passed (GO).
# The supervisor launches no other bridge: an edited, unreviewed bridge is
# refused (exit 78, phone alert) instead of sending orders unattended. Change
# this only in the commit that records that review in
# docs/PAPER_EXECUTION_STATUS.md; a test pins it to the file.
REVIEWED_BRIDGE_SHA256 = "7d313fd075d29e1e62c11eab52fd7832a0aaab21fd0b616c0ce2ee3f47962af4"
# The all-session simulations (orb-v2, gap-v2, vwap-v2) replace ORB_V1,
# GAP_GO_V1 and VWAP_PB_V1 here (owner decision "Both", 2026-10-13); the V1
# strategies stay registered for the CLI and the replay but are not scheduled.
STRATEGY_STARTS = {
    "orb-v2": time(9, 28),
    "momo": time(9, 55),
    "gap-v2": time(9, 28),
    "vwap-v2": time(9, 28),
}
STRATEGY_UNIVERSES = {
    "orb-v2": "scanner",
    "momo": "watchlist",
    "gap-v2": "all-gappers",
    "vwap-v2": "watchlist",
}
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
# The AI day manager (phase 2, shadow only). ``manager`` plans around its slots;
# the managed strategies run in their own entry windows like the others.
MANAGER_CHILD = "manager"
MANAGER_LEAD = timedelta(minutes=5)
MANAGER_TAIL = timedelta(minutes=10)
MANAGED_STRATEGY_STARTS = {"momo-ap": time(9, 55)}
MANAGED_STRATEGY_UNIVERSES = {"momo-ap": "watchlist"}

# A restarted strategy must find its predecessor's shadow-run lease expired.
STRATEGY_BACKOFF = DEFAULT_LEASE_TTL + timedelta(seconds=5)
SERVICE_BACKOFF = timedelta(seconds=15)
MAX_BACKOFF = timedelta(minutes=5)
# A child that ran at least this long was healthy; its next failure backs off
# from the base delay again.
STABLE_RUN = timedelta(minutes=5)
# The restart cap (T-5): RESTART_CAP counted exits within RESTART_WINDOW
# suspend a scheduled child (never the bridge, never an always-on child).
RESTART_CAP = 6
RESTART_WINDOW = timedelta(minutes=30)
# A suspended child is relaunched only as a probe this long after each exit; a
# probe that stays up PROBE_HEALTHY_RUN lifts the suspension.
SUSPENDED_PROBE_INTERVAL = timedelta(minutes=5)
PROBE_HEALTHY_RUN = timedelta(minutes=5)
# A suspended child whose last exit found the Gateway's port closed has it
# re-checked this often while it waits, and is relaunched at once when it opens.
PORT_RECHECK = timedelta(seconds=15)
# The IB Gateway's PAPER API port. A loopback connect is answered at once
# (accepted or refused); the timeout bounds the rare hung case (a frozen
# Gateway whose listen backlog is full). The probe is synchronous, so it
# blocks the whole loop (reaping, starts, window-end signals); hence at most
# one probe per loop pass, its result shared by every exit and port re-check
# in that pass. Worst case: one pass blocks PORT_PROBE_TIMEOUT_S (2 s) however
# many children exited at once (it was 2 s per exit and per suspended child,
# ~18 s for nine; review P1-A F-3). Passes that probe are those that reap a
# restartable exit of a scheduled child or find a port re-check due, and the
# re-checks of every suspended child waiting on the port fall into the same
# pass once one has probed, so a hung port costs at most 2 s per exit batch
# plus 2 s per PORT_RECHECK. The stop path never probes.
GATEWAY_HOST = "127.0.0.1"
GATEWAY_PORT = 7497
PORT_PROBE_TIMEOUT_S = 2.0
TERMINATE_GRACE = timedelta(seconds=20)
# macOS stops the monotonic clock while asleep, so waits are cut into chunks
# and re-checked against wall-clock time.
MAX_SLEEP_CHUNK_SECONDS = 30.0
LAUNCH_FAILED_CODE = 127
SUPERVISOR_LOG_NAME = "supervisor.log"
# Written by launchd itself (the supervisor's own stdout and stderr), so held
# open by launchd: rotated by copy-then-truncate, never renamed.
LAUNCHD_LOG_NAMES = ("launchd.stdout.log", "launchd.stderr.log")
LOCK_FILE_NAME = "supervisor.lock"
_SHUTDOWN_SIGNALS = (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)

Clock = Callable[[], datetime]
Sleeper = Callable[[float], Awaitable[None]]
Emit = Callable[[str], None]
StrategyCutoff = Callable[[str, date], datetime]
# True when the Gateway's API port accepts a TCP connection.
PortProbe = Callable[[], bool]


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


def gateway_port_open() -> bool:
    """Whether the IB Gateway's API port accepts a TCP connection (no API handshake).

    The repository's one port probe: the Telegram child awaits it through
    ``telegram_tools.gateway_port_open`` (in a worker thread) for /status and
    the overnight watch.
    """
    try:
        with socket.create_connection((GATEWAY_HOST, GATEWAY_PORT), timeout=PORT_PROBE_TIMEOUT_S):
            return True
    except OSError:
        return False


def _window_minutes() -> int:
    return round(RESTART_WINDOW.total_seconds() / 60)


def restart_cap_line() -> str:
    """The dry run's informational restart-cap line (never a ``!!!`` line)."""
    return (
        f"restart cap  {RESTART_CAP} exits / {_window_minutes()} min;"
        " bridge and always-on children page only"
    )


@dataclass(slots=True)
class RestartLedger:
    """One child's counted exits in the rolling window, and its cap state.

    ``suspended`` (scheduled children other than the bridge) and ``storming``
    (the bridge and the always-on children) are latches: each is announced
    once and cleared only by an explicit transition, never by the clock alone.
    """

    exits: deque[datetime] = field(default_factory=deque)
    suspended: bool = False
    storming: bool = False
    # Set while a suspended child waits after an exit that found the port
    # closed: when to look at the port again.
    port_recheck_at: datetime | None = None

    def record(self, moment: datetime) -> int:
        """Count one exit; the number of counted exits within the window ending now."""
        self.exits.append(moment)
        horizon = moment - RESTART_WINDOW
        while self.exits and self.exits[0] <= horizon:
            self.exits.popleft()
        return len(self.exits)

    def reset(self) -> None:
        self.exits.clear()
        self.suspended = False
        self.storming = False
        self.port_recheck_at = None


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
    *, python: Path = VENV_PYTHON, backend: Path = BACKEND_DIR, heartbeat: bool = False
) -> tuple[AlwaysOnPlan, ...]:
    """The children that run independently of trading sessions.

    ``heartbeat`` only when its URL is configured (``heartbeat.load_target``):
    without one the child could only exit 78 in a restart loop.
    """
    control = str(backend / "autotraderctl.py")
    plans = [AlwaysOnPlan(name=TELEGRAM_CHILD, argv=(str(python), control, "telegram", "run"))]
    if heartbeat:
        # No URL in the argv: argv is visible to ps and copied into the child
        # log's header line. The child reads the Keychain itself (cwd backend/).
        plans.append(AlwaysOnPlan(name=HEARTBEAT_CHILD, argv=(str(python), "-m", HEARTBEAT_MODULE)))
    return tuple(plans)


def strategy_cutoff(strategy_name: str, session_date: date) -> datetime:
    """The cutoff ``shadow run`` itself enforces, from the same strategy registry."""
    from trading.operations.shadow_service import strategy_entry_window

    return strategy_entry_window(strategy_name, session_date)[1]


def bridge_window(session: MarketSession) -> tuple[datetime, datetime]:
    """The bridge's window in UTC: ``BRIDGE_START`` to the session's own close
    (13:00 ET on a half day). The notifier stops re-asserting an IBKR loss the
    bridge reported once this window ends (review P1-A.1 L-2)."""
    starts_at = datetime.combine(session.session_date, BRIDGE_START, tzinfo=MARKET_TIMEZONE)
    return starts_at.astimezone(UTC), session.closes_at.astimezone(UTC)


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
    bridge_starts_at, bridge_ends_at = bridge_window(session)

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
            name=NOTIFIER_CHILD,
            argv=(interpreter, control, "notify", "run", "--date", iso),
            starts_at=at(NOTIFIER_WINDOW[0]),
            ends_at=at(NOTIFIER_WINDOW[1]),
            finishes_at=at(NOTIFIER_FINISH),
            base_backoff=SERVICE_BACKOFF,
        ),
        ChildPlan(
            name=BRIDGE_CHILD,
            argv=(interpreter, bridge, "--date", iso),
            starts_at=bridge_starts_at,
            ends_at=bridge_ends_at,
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
    plans.extend(
        manager_plans(session, python=python, backend=backend, cutoff_for=cutoff_for)
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


def manager_plans(
    session: MarketSession,
    *,
    python: Path = VENV_PYTHON,
    backend: Path = BACKEND_DIR,
    cutoff_for: StrategyCutoff = strategy_cutoff,
) -> tuple[ChildPlan, ...]:
    """The AI day manager's children: the planner and the managed strategies (shadow)."""
    from trading.manager.schedule import planning_window

    day = session.session_date
    iso = day.isoformat()
    interpreter = str(python)
    control = str(backend / "autotraderctl.py")
    plans: list[ChildPlan] = []
    first, last = planning_window(session)
    plans.append(
        ChildPlan(
            name=MANAGER_CHILD,
            argv=(interpreter, control, "manager", "run", "--date", iso),
            starts_at=first - MANAGER_LEAD,
            ends_at=last + MANAGER_TAIL,
            # It exits 0 by itself after its last slot (and that slot's retry).
            finishes_at=last,
            base_backoff=SERVICE_BACKOFF,
        )
    )
    for name, start in MANAGED_STRATEGY_STARTS.items():
        starts_at = datetime.combine(day, start, tzinfo=MARKET_TIMEZONE).astimezone(UTC)
        cutoff = cutoff_for(name, day).astimezone(UTC)
        if cutoff <= starts_at:
            continue
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
                    MANAGED_STRATEGY_UNIVERSES[name],
                    "--date",
                    iso,
                ),
                starts_at=starts_at,
                ends_at=cutoff + STRATEGY_WINDOW_TAIL,
                finishes_at=cutoff,
                base_backoff=STRATEGY_BACKOFF,
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


class LaunchRefused(Exception):
    """A launch the launcher declines before exec; ``code`` is reported as the exit."""

    def __init__(self, code: int, reason: str) -> None:
        super().__init__(reason)
        self.code = code
        self.reason = reason


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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
    # Per child and per session: a new session's children start with a fresh one.
    ledger: RestartLedger = field(default_factory=RestartLedger)

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

    @property
    def suspendable(self) -> bool:
        """The restart cap may suspend it: a scheduled child other than the bridge.

        The bridge is never suspended (its positions are protected at IB and
        the owner's phone orders go through it), nor is an always-on child
        (the owner's Telegram assistant): both only page a restart storm.
        """
        return isinstance(self.plan, ChildPlan) and self.plan.name != BRIDGE_CHILD


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
        port_probe: PortProbe | None = None,
        rotation: LogRotation | None = None,
    ) -> None:
        """``port_probe`` says whether the Gateway's port is open at an exit;
        ``run_supervisor`` passes ``gateway_port_open``. Without one (tests,
        the dry run) the port counts as open, so every restartable exit counts.
        ``rotation`` rotates the logs daily between sessions; without one
        (tests, the dry run) nothing is rotated.
        """
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
        self._port_probe = port_probe
        # The Gateway port's state as probed in the current loop pass (None: not
        # probed yet in this pass); see PORT_PROBE_TIMEOUT_S.
        self._pass_port: str | None = None
        self._rotation = rotation
        self._rotation_due: datetime | None = None
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
            if self._rotation is not None:
                self._rotation_due = self._rotation.due_at(started)
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
            if not self.stopping:
                # The session boundary: a child that ended its day suspended
                # before its own window end (no room for another probe) is
                # suspended no longer.
                for child in children:
                    self._end_ledger(child, "window_boundary")
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
            # A new loop pass: at most one Gateway port probe, shared by the pass.
            self._pass_port = None
            now = self._clock()
            for child in everyone:
                self._reap(child, now)
            if self.stopping:
                for child in everyone:
                    child.next_start = None
                    # Nothing restarts after a stop and the next supervisor
                    # starts with empty ledgers, so a suspension ends here, and
                    # says so before any child (the notifier among them) is
                    # signalled: the phone never keeps "suspended" for a child
                    # that runs again after a deploy (review P1-A F-4).
                    self._end_ledger(child, "supervisor_stop")
                for child in everyone:
                    self._terminate(child, now, reason="shutdown")
            else:
                for child in children:
                    self._review_ledger(child, now)
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
            deadline = self._next_deadline(everyone)
            ledger = self._ledger_deadline(children, now)
            if ledger is not None and (deadline is None or ledger < deadline):
                deadline = ledger
            await self._wait(deadline, everyone)

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
        # Every restart-cap line about this exit follows its CHILD_EXIT line.
        cap_lines: list[str] = []
        if child.ledger.suspended and ran >= PROBE_HEALTHY_RUN:
            # The probe outlived its deadline before the loop looked at it: the
            # ledger starts over before this exit is counted.
            cap_lines.append(self._lift(child, "probe_healthy"))
        restart_in = self._restart_delay(child, code, now, ran)
        if restart_in is not None:
            # Only an exit that would be restarted is counted: a window end, a
            # shutdown, a planned finish or a final code ends the child's day.
            restart_in, counted = self._count_exit(child, code, now, restart_in)
            cap_lines += counted
        self._emit(child_exit_line(now, child.plan.name, code, restart_in))
        for line in cap_lines:
            self._event(line)
        if restart_in is None:
            child.next_start = None
            return
        child.next_start = now + restart_in
        child.backoff = min(restart_in * 2, MAX_BACKOFF)

    def _count_exit(
        self, child: _Child, code: int, now: datetime, delay: timedelta
    ) -> tuple[timedelta | None, list[str]]:
        """The restart cap for one restartable exit: the delay it leaves and its lines."""
        name = child.plan.name
        ledger = child.ledger
        lines: list[str] = []
        uncounted: str | None = None
        if child.suspendable and code == SCHEMA_REFUSAL_CODE:
            # The schema gate's refusal (a pending migration or another schema
            # problem): every scheduled child loops on it until `db migrate`,
            # the notifier's schema alert already pages it, and the ordinary
            # backoff already caps the relaunches at one per 5 minutes. Counted,
            # it suspended every child after ~8 minutes and paged each one on
            # top of that alert (review P1-A F-11). No port question: not probed.
            uncounted = "schema_not_ready"
        elif isinstance(child.plan, ChildPlan):
            port, failed = self._probe_port()
            if failed is not None:
                lines.append(failed)
            if port != "open":
                # A closed Gateway is an outage, not a crashing child: never
                # counted, so an outage can never suspend anything.
                uncounted = f"port_{port}"
                if ledger.suspended:
                    ledger.port_recheck_at = now + PORT_RECHECK
        # Always-on children (telegram, and the heartbeat when its Keychain item
        # exists) do not need the Gateway: their exits always count and are
        # never probed (not suspendable, so an exit 75 of theirs is never read
        # as a schema refusal). Their storm reaches the phone only
        # while the notifier runs (07:55-16:15 ET on session days); a crash loop
        # overnight, during the Gateway's nightly restart, is in supervisor.log
        # only until the T-4 overnight watch forwards it (review P1-A F-2).
        if uncounted is not None:
            lines.append(f"CHILD_EXIT_UNCOUNTED {name} reason={uncounted}")
        else:
            # Exits caused by a competing IBKR login (the phone app or the web
            # takes the market data, errors 10197/162/165) happen with the port
            # OPEN, so they count and can suspend a strategy. Nothing here
            # watches that login: the 5-minute probe lifts such a suspension.
            # Once the owner logs out elsewhere, the next probe (at most 5
            # minutes later) gets its data and stays up, so the child is running
            # again within about 5 minutes; CHILD_RESUMED follows after its
            # 5 healthy minutes.
            ledger.port_recheck_at = None
            count = ledger.record(now)
            if count < RESTART_CAP:
                ledger.storming = False
            elif child.suspendable:
                if not ledger.suspended:
                    ledger.suspended = True
                    lines.append(
                        f"CHILD_SUSPENDED {name} count={count} window={_window_minutes()}m"
                    )
            elif not ledger.storming:
                ledger.storming = True
                lines.append(
                    f"CHILD_RESTART_STORM {name} count={count} window={_window_minutes()}m"
                )
        if ledger.suspended:
            plan = child.plan
            assert isinstance(plan, ChildPlan)
            delay = SUSPENDED_PROBE_INTERVAL
            if now + delay >= plan.ends_at:
                return None, lines
        return delay, lines

    def _probe_port(self) -> tuple[str, str | None]:
        """``(state, line)``: ``open``, ``closed`` or, when the probe itself fails,
        ``unknown`` with its ``PORT_PROBE_FAILED`` line (the caller places it).

        At most one probe per loop pass: later callers in the same pass share
        its result and get no line (see PORT_PROBE_TIMEOUT_S for the bound).
        """
        if self._port_probe is None:
            return "open", None
        if self._pass_port is not None:
            return self._pass_port, None
        failed: str | None = None
        try:
            state = "open" if self._port_probe() else "closed"
        except Exception as exc:  # noqa: BLE001 - a broken probe must not stop the supervisor
            failed = f"PORT_PROBE_FAILED error={type(exc).__name__}"
            # Uncounted, like a closed port: a broken probe can only disable
            # the cap (yesterday's behaviour), never suspend a child.
            state = "unknown"
        self._pass_port = state
        return state, failed

    def _review_ledger(self, child: _Child, now: datetime) -> None:
        """The suspension's timed transitions: window end, healthy probe, reopened port."""
        ledger = child.ledger
        end = child.window_end
        if end is not None and now >= end:
            self._end_ledger(child, "window_boundary")
            return
        if not ledger.suspended:
            return
        if child.running:
            if (
                child.terminated_at is None
                and child.started_at is not None
                and now >= child.started_at + PROBE_HEALTHY_RUN
            ):
                self._resume(child, "probe_healthy")
        elif (
            child.next_start is not None
            and ledger.port_recheck_at is not None
            # Due, or the port was already probed in this pass: the re-checks
            # of every child waiting on the port share one probe from then on.
            and (now >= ledger.port_recheck_at or self._pass_port is not None)
        ):
            port, failed = self._probe_port()
            if failed is not None:
                self._event(failed)
            if port == "open":
                self._resume(child, "port_open")
                child.next_start = now  # relaunched at once
            else:
                ledger.port_recheck_at = now + PORT_RECHECK

    def _lift(self, child: _Child, reason: str) -> str:
        """Lift a suspension (the ledger and the backoff start over); its line."""
        child.ledger.reset()
        child.backoff = child.plan.base_backoff
        return f"CHILD_RESUMED {child.plan.name} reason={reason}"

    def _resume(self, child: _Child, reason: str) -> None:
        self._event(self._lift(child, reason))

    def _end_ledger(self, child: _Child, reason: str) -> None:
        """The ledger starts over at a boundary; a suspended child says it is lifted."""
        if child.ledger.suspended:
            self._resume(child, reason)
        else:
            child.ledger.reset()

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
        except LaunchRefused as refused:
            # Reported as the child's own exit with that code, so a final code
            # (the bridge's 78) is never retried and the phone hears of it.
            refused_at = self._clock()
            self._event(
                f"LAUNCH_REFUSED {child.plan.name} code={refused.code} reason={refused.reason}"
            )
            self._after_exit(child, refused.code, refused_at, timedelta(0))
            return
        except Exception as exc:  # noqa: BLE001 - a failed launch is retried like an exit
            failed_at = self._clock()
            self._event(f"LAUNCH_FAILED {child.plan.name} error={type(exc).__name__}")
            self._after_exit(child, LAUNCH_FAILED_CODE, failed_at, timedelta(0))
            return
        started_at = self._clock()
        child.ledger.port_recheck_at = None
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

    @staticmethod
    def _ledger_deadline(children: Sequence[_Child], now: datetime) -> datetime | None:
        """The next timed transition of a suspension (healthy probe, port
        re-check, window end). ``_review_ledger`` has just handled every one
        that was due, so only future moments are offered: a transition that
        failed to advance can delay a line, never spin the loop."""
        moments: list[datetime] = []
        for child in children:
            ledger = child.ledger
            if not ledger.suspended:
                continue
            if child.running:
                if child.terminated_at is None and child.started_at is not None:
                    moments.append(child.started_at + PROBE_HEALTHY_RUN)
            elif child.next_start is not None and ledger.port_recheck_at is not None:
                moments.append(ledger.port_recheck_at)
            if child.window_end is not None:
                moments.append(child.window_end)
        return min((moment for moment in moments if moment > now), default=None)

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
        """Between sessions: only the always-on children are tended, and the
        logs rotate (never during a session, so a rotation never splits one)."""
        while not self.stopping:
            now = self._clock()
            await self._tend_always(now)
            if self.stopping or now >= moment:
                return
            self._rotate_logs_if_due(now)
            if any(child.has_exited for child in self._always):
                continue
            deadlines = [moment]
            restarts = self._next_deadline(self._always)
            if restarts is not None:
                deadlines.append(restarts)
            if self._rotation_due is not None:
                deadlines.append(self._rotation_due)
            await self._wait(min(deadlines), self._always)

    def _rotate_logs_if_due(self, now: datetime) -> None:
        rotation, due = self._rotation, self._rotation_due
        if rotation is None or due is None or now < due:
            return
        # The next boundary first: a failing rotation is retried at the next
        # boundary (or the next start), never in a loop.
        self._rotation_due = next_rotation_boundary(now)
        # A running always-on child holds its log open: copied then truncated.
        held = frozenset(child.plan.name for child in self._always if child.running)
        try:
            lines = rotation.rotate(now, held_open=held)
        except Exception as exc:  # noqa: BLE001 - a failed rotation must never stop the supervisor
            lines = [f"LOG_ROTATION_FAILED file=all error={type(exc).__name__}"]
        for line in lines:
            self._event(line)

    async def _abandon(self, children: Sequence[_Child]) -> None:
        """Error path: never leave a child running without its supervisor.

        Signals go straight to the processes and every log write is best
        effort, because the error being handled may be a failing log.
        """
        for child in children:
            if child.ledger.suspended:
                # As on an orderly stop: the next supervisor starts afresh.
                try:
                    self._resume(child, "supervisor_stop")
                except Exception:  # noqa: BLE001 - best effort, as every write here
                    pass
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
    """Spawns each child in its own session, appending its output to ``<name>.log``.

    ``pinned`` maps a child name to the sha256 its script (``argv[1]``) must
    have; any other content is refused before exec, at every (re)start.
    """

    def __init__(
        self,
        log_directory: Path,
        *,
        cwd: Path = BACKEND_DIR,
        environment: Mapping[str, str] | None = None,
        clock: Clock = _utc_now,
        pinned: Mapping[str, str] | None = None,
    ) -> None:
        self._log_directory = log_directory
        self._cwd = cwd
        self._environment = child_environment(environment)
        self._clock = clock
        self._pinned = dict(pinned or {})

    def log_path(self, name: str) -> Path:
        return self._log_directory / f"{name}.log"

    def _check_pinned(self, plan: ChildPlan) -> None:
        expected = self._pinned.get(plan.name)
        if expected is None:
            return
        try:
            actual = file_sha256(Path(plan.argv[1]))
        except (IndexError, OSError) as exc:
            raise LaunchRefused(
                BRIDGE_REFUSED_CODE, f"unreadable_script error={type(exc).__name__}"
            ) from exc
        if actual != expected:
            raise LaunchRefused(
                BRIDGE_REFUSED_CODE,
                f"unreviewed sha256={actual[:12]} reviewed={expected[:12]}",
            )

    async def start(self, plan: ChildPlan) -> ChildProcess:
        self._check_pinned(plan)
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


class _CarriedLines:
    """The newest lines a reader of a freshly rotated ``supervisor.log`` needs:
    this run's SUPERVISOR_START, the newest SESSION_* line, and each child's
    newest CHILD_START or CHILD_EXIT, in their original order."""

    def __init__(self) -> None:
        self._newest: dict[str, tuple[int, str]] = {}
        self._order = itertools.count()

    def observe(self, line: str) -> None:
        fields = line.split(" ", 3)
        if len(fields) < 2:
            return
        kind = fields[1]
        if kind == "SUPERVISOR_START":
            self._newest.clear()
            key = kind
        elif kind.startswith("SESSION_"):
            key = "SESSION_"
        elif kind in ("CHILD_START", "CHILD_EXIT") and len(fields) >= 3:
            key = f"CHILD {fields[2]}"
        else:
            return
        self._newest[key] = (next(self._order), line)

    def lines(self) -> list[str]:
        return [line for _order, line in sorted(self._newest.values())]


class SupervisorLog:
    """Append-only private ``supervisor.log``, echoed to the terminal when attached."""

    def __init__(self, path: Path, *, echo: TextIO | None = None) -> None:
        ensure_private_directory(path.parent)
        self.path = path
        self._stream = os.fdopen(open_private_append(path), "a", encoding="utf-8")
        self._echo = echo
        self._carried = _CarriedLines()

    def __call__(self, line: str) -> None:
        self._stream.write(line + "\n")
        self._stream.flush()
        self._carried.observe(line)
        if self._echo is not None:
            try:
                print(line, file=self._echo, flush=True)
            except (OSError, ValueError):
                self._echo = None  # the terminal went away; the log file remains

    def rotate(self, day: date, *, marker: Callable[[int], str] | None = None) -> int:
        """Rename the log to its archive for ``day``, reopen a new file at the same
        path, and copy the newest state lines into it; how many were copied.

        ``marker(carried)`` is the full ``LOG_ROTATED`` line: it is written with
        the carried lines in ONE write (N15b L-5), so a follower never reads the
        carried copies without the marker that tells it they are copies.

        If the new file cannot be opened, the old handle (now the archive's)
        stays in use, so no line is lost, and the error is raised.
        """
        self._stream.flush()
        rotate_by_rename(self.path, day, keep_empty=True)
        stream = os.fdopen(open_private_append(self.path), "a", encoding="utf-8")
        previous, self._stream = self._stream, stream
        previous.close()
        carried = self._carried.lines()
        block = [*carried, *([marker(len(carried))] if marker is not None else [])]
        if block:
            # The new stream's buffer is empty: one raw append holds the whole block.
            data = "".join(f"{line}\n" for line in block).encode("utf-8")
            descriptor = self._stream.fileno()
            written = 0
            while written < len(data):
                written += os.write(descriptor, data[written:])
        if marker is not None and self._echo is not None:
            try:
                print(block[-1], file=self._echo, flush=True)
            except (OSError, ValueError):
                self._echo = None
        return len(carried)

    def close(self) -> None:
        self._stream.close()


def rotated_child_names() -> tuple[str, ...]:
    """Every ``<name>.log`` the supervisor's launcher opens: each scheduled and
    each always-on child's (a test pins this to the plans)."""
    names = (
        "screener",
        NOTIFIER_CHILD,
        BRIDGE_CHILD,
        *STRATEGY_STARTS,
        MANAGER_CHILD,
        *MANAGED_STRATEGY_STARTS,
        *RESEARCH_CHILDREN,
        TELEGRAM_CHILD,
        HEARTBEAT_CHILD,
    )
    return tuple(dict.fromkeys(names))


def rotation_line() -> str:
    """The dry run's informational log-rotation line."""
    return (
        f"rotation     daily at {ROTATION_TIME:%H:%M} ET between sessions into"
        f" <name>.log.YYYY-MM-DD, {ROTATION_KEEP_DAYS} days kept;"
        " launchd.*.log and running always-on children's logs copied then truncated"
    )


class LogRotation:
    """The supervisor's daily log rotation (trading.operations.logging).

    Only ``supervisor.log``, the children's ``<name>.log`` and launchd's two
    files: ``shadow.log`` rotates by size on its own, and any other file in
    the directory (``deploys.log``) is never touched.
    """

    def __init__(
        self,
        directory: Path,
        log: SupervisorLog,
        *,
        children: Sequence[str] | None = None,
        keep_days: int = ROTATION_KEEP_DAYS,
    ) -> None:
        self._directory = directory
        self._log = log
        self._children = tuple(children) if children is not None else rotated_child_names()
        self._keep_days = keep_days

    def due_at(self, now: datetime) -> datetime:
        """The newest 18:35 ET boundary while its ``supervisor.log`` archive does
        not exist (a rotation missed while the supervisor was down is made up),
        else the next boundary."""
        boundary = latest_rotation_boundary(now)
        if archive_path(self._log.path, boundary.date()).exists():
            return next_rotation_boundary(now)
        return boundary

    def rotate(self, now: datetime, *, held_open: Collection[str] = ()) -> list[str]:
        """Rotate everything for the newest boundary's date; the lines still to log.

        ``held_open`` names the children running now (their files are copied
        then truncated). ``supervisor.log`` goes last: its archive marks the
        day as done, and the new file starts with the carried lines and the
        ``LOG_ROTATED`` marker, written together (N15b L-5). What is returned is
        for the caller to log after them: each ``LOG_ROTATION_FAILED``, and the
        marker itself only when ``supervisor.log`` could not be rotated.
        """
        day = latest_rotation_boundary(now).date()
        renamed = truncated = pruned = 0
        failures: list[str] = []

        def attempt(name: str, action: Callable[[], object]) -> object:
            try:
                return action()
            except Exception as exc:  # noqa: BLE001 - one bad file must not stop the others
                failures.append(f"LOG_ROTATION_FAILED file={name} error={type(exc).__name__}")
                return None

        for child in self._children:
            path = self._directory / f"{child}.log"
            if child in held_open:
                truncated += bool(attempt(path.name, partial(rotate_by_copy_truncate, path, day)))
            else:
                renamed += bool(attempt(path.name, partial(rotate_by_rename, path, day)))
        for name in LAUNCHD_LOG_NAMES:
            path = self._directory / name
            truncated += bool(attempt(name, partial(rotate_by_copy_truncate, path, day)))
        # Pruned before supervisor.log rotates: the marker written with the carried
        # lines then has its final counts.
        for name in (*(f"{child}.log" for child in self._children), *LAUNCHD_LOG_NAMES, SUPERVISOR_LOG_NAME):
            removed = attempt(
                name, partial(prune_archives, self._directory / name, day, keep_days=self._keep_days)
            )
            pruned += len(removed) if isinstance(removed, list) else 0

        def marker(renamed_files: int, carried_lines: int) -> str:
            return (
                f"LOG_ROTATED date={day.isoformat()} renamed={renamed_files} truncated={truncated}"
                f" pruned={pruned} carried={carried_lines}"
            )

        written = attempt(
            SUPERVISOR_LOG_NAME,
            partial(self._log.rotate, day, marker=lambda lines: f"{iso_utc(now)} {marker(renamed + 1, lines)}"),
        )
        if isinstance(written, int):
            return failures
        return [marker(renamed, 0), *failures]


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


def child_environment(environment: Mapping[str, str] | None = None) -> dict[str, str]:
    """The environment every child (and the bridge preflight) runs with."""
    result = dict(os.environ if environment is None else environment)
    result.update(CHILD_ENVIRONMENT_EXTRA)
    return result


def registry_refusals() -> list[str]:
    """``shadow_service.registry_problems()``, imported on use (the TWS and Claude stack)."""
    try:
        from trading.operations.shadow_service import registry_problems

        return registry_problems()
    except Exception as exc:  # noqa: BLE001 - a check that cannot run is a refusal, never a traceback
        return [f"registry_check_failed={type(exc).__name__}"]


def registry_lines(problems: Sequence[str]) -> tuple[str, ...]:
    """Dry-run lines for the registry preflight; loud when the launch would be refused."""
    if not problems:
        return ("registry     sendable identities, size table and strategy registry agree",)
    return tuple(
        f"!!! LAUNCH_REFUSED {REGISTRY_CHILD} code={BRIDGE_REFUSED_CODE} reason={problem}"
        for problem in problems
    ) + ("!!! the supervisor will not start: fix the registry (nothing launches)",)


def bridge_preflight_argv(
    session_date: date, *, python: Path = VENV_PYTHON, backend: Path = BACKEND_DIR
) -> tuple[str, ...]:
    """The bridge's real launch argv plus ``--preflight``: same interpreter, same script."""
    bridge = backend / "scripts_local" / "paper_bridge.py"
    return (str(python), str(bridge), "--date", session_date.isoformat(), BRIDGE_PREFLIGHT_FLAG)


def run_bridge_preflight(
    session_date: date, *, python: Path = VENV_PYTHON, backend: Path = BACKEND_DIR
) -> tuple[int, str]:
    """Run the bridge's own ``--preflight`` (no TWS) the way the supervisor would launch it.

    Returns ``(exit code, stdout)``; the lines are relayed verbatim by the dry
    run and a non-zero exit fails it. Env, interpreter and cwd are the launch's.
    """
    argv = bridge_preflight_argv(session_date, python=python, backend=backend)
    try:
        completed = subprocess.run(
            argv,
            cwd=str(backend),
            env=child_environment(),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=BRIDGE_PREFLIGHT_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired as exc:
        partial = exc.stdout or b""
        text = partial.decode("utf-8", "replace") if isinstance(partial, bytes) else str(partial)
        return 127, text + f"bridge preflight could not run (TimeoutExpired after {BRIDGE_PREFLIGHT_TIMEOUT_S:.0f} s)\n"
    except (OSError, subprocess.SubprocessError) as exc:
        return 127, f"bridge preflight could not run ({type(exc).__name__}: {exc})\n"
    output = completed.stdout
    if completed.returncode != 0 and completed.stderr.strip():
        output += completed.stderr
    return completed.returncode, output


BridgePreflight = Callable[..., tuple[int, str]]


def bridge_preflight_lines(
    session_date: date,
    *,
    python: Path = VENV_PYTHON,
    backend: Path = BACKEND_DIR,
    preflight: BridgePreflight | None = None,
) -> tuple[str, ...]:
    """The bridge preflight's output as dry-run lines, ``!!!`` on a non-zero exit."""
    runner = run_bridge_preflight if preflight is None else preflight
    code, output = runner(session_date, python=python, backend=backend)
    lines = tuple(
        f"bridge       {_BRIDGE_EVENT_CLOCK.sub('', line, count=1)}"
        for line in output.splitlines()
        if line.strip()
    )
    if code == 0:
        return lines
    return lines + (
        f"!!! BRIDGE PREFLIGHT FAILED (exit {code}): the bridge would refuse to start at"
        f" {_local(datetime.combine(session_date, BRIDGE_START, tzinfo=MARKET_TIMEZONE))} ET",
    )


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
# A hand-started heartbeat (other than a one-ping --once check) would keep the
# dead-man's switch quiet after the supervisor stops.
_UNSUPERVISED_PATTERN = (
    r"^[^ ]*python[^ ]* .*(autotraderctl\.py (screener|shadow|notify|telegram) run"
    r"|paper_bridge\.py"
    r"|-m trading\.operations\.heartbeat(?!.* --once))"
)
# A running supervisor (`autotraderctl run`, never its `--dry-run`): the
# children under it are its own, not strays.
_SUPERVISOR_PATTERN = r"^[^ ]*python[^ ]* .*autotraderctl\.py run(?: |$)(?!.*--dry-run)"

# One process: (pid, parent pid, full command line).
ProcessEntry = tuple[int, int, str]


def process_table() -> list[ProcessEntry]:
    """Every process with its parent, from ``ps``; empty when ``ps`` is unavailable."""
    try:
        result = subprocess.run(
            ["/bin/ps", "-axww", "-o", "pid=,ppid=,command="],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    table: list[ProcessEntry] = []
    for line in result.stdout.splitlines():
        parts = line.split(None, 2)
        if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit():
            table.append((int(parts[0]), int(parts[1]), parts[2]))
    return table


def unsupervised_children(
    table: Callable[[], Sequence[ProcessEntry]] = process_table,
) -> list[tuple[int, str]]:
    """Trading processes running outside any supervisor; reported, never killed.

    A child whose parent chain reaches a running ``autotraderctl run`` is that
    supervisor's own (the dry run used to warn about every supervised child,
    telling the owner to kill the live screener).
    """
    entries = list(table())
    parents = {pid: ppid for pid, ppid, _command in entries}
    supervisors = {
        pid for pid, _ppid, command in entries if re.search(_SUPERVISOR_PATTERN, command)
    }

    def supervised(pid: int) -> bool:
        seen: set[int] = set()
        while pid in parents and pid not in seen:
            seen.add(pid)
            pid = parents[pid]
            if pid in supervisors:
                return True
        return False

    found: list[tuple[int, str]] = []
    for pid, _ppid, command in entries:
        if pid == os.getpid() or not re.search(_UNSUPERVISED_PATTERN, command):
            continue
        if not supervised(pid):
            found.append((pid, command))
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
    heartbeat_url: HeartbeatUrlSource | None = None,
) -> int:
    """``autotraderctl run``: supervise in the foreground until signalled.

    ``heartbeat_url`` (tests) replaces the Keychain read of the dead-man's
    switch URL; the heartbeat child is scheduled only when it yields one.
    """
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
                problems = registry_refusals()
                for problem in problems:
                    log(
                        f"{iso_utc(_utc_now())} LAUNCH_REFUSED {REGISTRY_CHILD}"
                        f" code={BRIDGE_REFUSED_CODE} reason={problem}"
                    )
                if problems:
                    print(
                        "error: the strategy registry, the sendable identities and the size table"
                        " disagree; nothing was launched (supervisor.log)",
                        file=sys.stderr,
                    )
                    return BRIDGE_REFUSED_CODE
                for path in missing_entry_points(python=python, backend=backend):
                    log(f"{iso_utc(_utc_now())} PREFLIGHT_WARNING missing={path}")
                for pid, command in unsupervised():
                    log(
                        f"{iso_utc(_utc_now())} PREFLIGHT_WARNING unsupervised pid={pid} "
                        f"(stop it: kill {pid}) cmd={command}"
                    )
                heartbeat, heartbeat_detail = load_heartbeat_target(
                    heartbeat_url_from_keychain if heartbeat_url is None else heartbeat_url
                )
                if heartbeat is None:
                    log(f"{iso_utc(_utc_now())} PREFLIGHT_WARNING heartbeat {heartbeat_detail}")
                supervisor = Supervisor(
                    launcher=SubprocessLauncher(
                        paths.log_directory,
                        cwd=backend,
                        pinned={BRIDGE_CHILD: REVIEWED_BRIDGE_SHA256},
                    ),
                    emit=log,
                    planner=partial(plan_session, python=python, backend=backend),
                    always=always_on_plans(
                        python=python, backend=backend, heartbeat=heartbeat is not None
                    ),
                    port_probe=gateway_port_open,
                    rotation=LogRotation(paths.log_directory, log),
                )
                return asyncio.run(_run_until_signalled(supervisor, session_date))
            finally:
                log.close()
    except SupervisorAlreadyRunning as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def bridge_review_line(backend: Path = BACKEND_DIR) -> str:
    """Whether the bridge on disk is the reviewed one the supervisor will launch."""
    path = backend / "scripts_local" / "paper_bridge.py"
    try:
        actual = file_sha256(path)
    except OSError:
        return f"WARNING: bridge {path} unreadable: the supervisor will refuse to start it"
    if actual == REVIEWED_BRIDGE_SHA256:
        return f"bridge       reviewed (sha256 {actual[:12]})"
    return (
        f"WARNING: bridge sha256 {actual[:12]} is not the reviewed {REVIEWED_BRIDGE_SHA256[:12]}:"
        " the supervisor will refuse to start it (no orders)"
    )


def schema_lines(database: str | Path) -> tuple[str, ...]:
    """Read-only (the dry run never opens the database for writing): whether
    the children will find the schema they need, loudly when they will not."""
    from trading.storage.migrations import inspect_schema

    status = inspect_schema(database)
    if status.current:
        return (f"database     schema v{status.applied_version} current ({status.database})",)
    return (
        f"!!! DATABASE NOT READY ({status.token}): {status.describe()} ({status.database})",
        "!!! the screener, the strategies, the manager and the recorder will refuse to start"
        " (exit 75, retried with backoff) until `autotraderctl db migrate` (after the close)",
    )


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
    database: str | Path | None = None,
    registry: Callable[[], Sequence[str]] | None = None,
    preflight: BridgePreflight | None = None,
    heartbeat_present: HeartbeatPresenceSource | None = None,
) -> str:
    """The planned child timeline for one session, simulated; launches and writes nothing.

    The bridge's own ``--preflight`` is the one subprocess it runs (read-only,
    no TWS), after the pin check, with the launch's interpreter, env and cwd.
    ``heartbeat_present`` asks whether the dead-man's switch's Keychain item
    exists (``autotraderctl run --dry-run`` passes the presence lookup, which
    never reads the secret: agents run the dry run, N15b L-8); without one it
    is not checked. The child validates the URL itself.
    """
    if database is None:
        from config import cfg

        database = cfg.DB_PATH
    schema = schema_lines(database)
    registry_problems = tuple((registry_refusals if registry is None else registry)())
    registry_report = registry_lines(registry_problems)
    if heartbeat_present is None:
        heartbeat_scheduled = False
        heartbeat_detail = "not checked (no Keychain reader given)"
    else:
        heartbeat_scheduled, heartbeat_detail = heartbeat_presence_detail(heartbeat_present)
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
        *(line for line in schema if line.startswith("!!!")),
        *(line for line in registry_report if line.startswith("!!!")),
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
    for always in always_on_plans(python=python, backend=backend, heartbeat=heartbeat_scheduled):
        lines.append(
            f"{always.name:<9} {'always-on':<12} {'never':<13} "
            f"{f'{always.base_backoff.seconds}-{MAX_BACKOFF.seconds}s':<10} "
            f"{_display_command(always.argv)}"
        )
        lines.append(
            f"{always.name} runs from supervisor start to supervisor stop, between sessions"
            " too; restarted after any exit (not shown in the timeline below)"
        )
    lines.append(restart_cap_line())
    # Never a !!! line: without the switch the machine still trades; only the
    # owner's external page on a dead Mac is missing.
    lines.append(f"{HEARTBEAT_CHILD:<13}{heartbeat_detail}")
    lines.append(rotation_line())
    lines += ["", "simulated timeline (ET; pids are placeholders):"]
    for moment, line in events:
        lines.append(f"{_local(moment, '%H:%M:%S')}  {line.split(' ', 1)[1]}")
    for path in missing_entry_points(python=python, backend=backend):
        lines.append(f"WARNING: missing {path}")
    review = bridge_review_line(backend)
    lines.append(review)
    if review.startswith("bridge       reviewed"):
        # Only the reviewed bridge is asked; an unreviewed one is refused anyway.
        lines.extend(
            bridge_preflight_lines(
                session.session_date, python=python, backend=backend, preflight=preflight
            )
        )
    lines.extend(registry_report)
    lines.extend(schema)
    for pid, command in unsupervised():
        lines.append(f"WARNING: running outside the supervisor (kill {pid} before `run`): {command}")
    return "\n".join(lines)


__all__ = [
    "BACKEND_DIR",
    "AlreadyRunning",
    "AlwaysOnPlan",
    "BRIDGE_CHILD",
    "HEARTBEAT_CHILD",
    "LAUNCHD_LOG_NAMES",
    "LogRotation",
    "BRIDGE_PREFLIGHT_FLAG",
    "REGISTRY_CHILD",
    "ChildPlan",
    "ChildProcess",
    "LaunchRefused",
    "Launcher",
    "MAX_BACKOFF",
    "RECORDER_WINDOW",
    "REPLAY_WINDOW",
    "RESEARCH_CHILDREN",
    "RESTART_CAP",
    "RESTART_WINDOW",
    "RestartLedger",
    "REVIEWED_BRIDGE_SHA256",
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
    "bridge_window",
    "bridge_preflight_argv",
    "bridge_preflight_lines",
    "bridge_review_line",
    "child_environment",
    "child_exit_line",
    "child_start_line",
    "dry_run_report",
    "exit_status",
    "file_sha256",
    "gateway_port_open",
    "heartbeat_item_present",
    "heartbeat_url_from_keychain",
    "missing_entry_points",
    "open_private_append",
    "plan_session",
    "registry_lines",
    "registry_refusals",
    "restart_cap_line",
    "rotated_child_names",
    "rotation_line",
    "run_bridge_preflight",
    "run_supervisor",
    "schema_lines",
    "simulate_session",
    "single_instance",
    "strategy_cutoff",
    "unsupervised_children",
    "upcoming_session",
]

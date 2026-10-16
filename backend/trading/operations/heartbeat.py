"""The dead-man's switch client: tell an external monitor, every minute, that this Mac is alive.

A watchdog on this Mac cannot report this Mac dead (asleep, powered off,
offline, the supervisor gone). An external monitor (a hosted
healthchecks-style check the owner sets up, with its own grace period, paging
the owner's phone) pages when these pings STOP; this child only sends them.

``python -m trading.operations.heartbeat`` runs as the supervisor's always-on
child ``heartbeat``, scheduled only when the ping URL is in the Keychain
(``com.maccabi.autotrader-v1.heartbeat`` / ``heartbeat-url``). Every 60 s it
sends ``GET <url>?state=<idle|session|outage>`` (10 s timeout, standard
library only), the state read from the tail of ``supervisor.log``:

    outage   a ``CHILD_EXIT_UNCOUNTED <name> reason=port_closed`` line (a
             child exited while the IB Gateway's port was closed) in the
             last 10 minutes. The supervisor's other uncounted reasons are
             not: ``port_unknown`` (its probe itself failed, so the port
             is not known closed) and ``schema_not_ready`` (a pending
             migration, not the Gateway; the notifier pages it)
    session  the newest SESSION_*/SUPERVISOR_* line is SESSION_START
    idle     otherwise

The state is information for the monitor; it grants nothing. The URL is a
secret (whoever holds it can fake a heartbeat): it is never logged, never in
an argv, and an exception's text is never logged (only its type or the HTTP
status), because urllib's messages can quote the URL. A failed ping is logged
at most once per 10 minutes and the loop goes on: only the supervisor's
SIGTERM, or the supervisor's death, ends it.

The child exits when its parent supervisor dies: it runs in its own session,
so a SIGKILLed supervisor would otherwise leave it pinging, and a pinging
orphan is exactly the silence this switch exists to catch.

Power (owner, 2026-10-16, after the Mac went to clamshell sleep on battery
mid-session, the 15:45 ET flatten never ran and nothing paged). Sleep and
death are caught by the monitor alone: no pings, a page. Battery is caught
before the sleep: from 07:40 to 16:15 ET on a session day (the XNYS calendar),
when ``pmset -g batt`` says "Battery Power", the success ping is replaced by
the monitor's failure endpoint, ``<url>/fail?reason=battery`` (a page), at most
once per 15 minutes, each with one WARNING line; the check stays down until the
first ping back on AC power. Outside that window battery only adds
``&power=battery`` to the normal ping. An unreadable ``pmset`` or calendar
never stops a ping (no power information; any weekday counts as a session day).

Log lines (stdout, appended to ``heartbeat.log`` by the supervisor):

    <ISO UTC> HEARTBEAT_START every=60s timeout=10s host=<host>
    <ISO UTC> HEARTBEAT_OK state=<state>                (the first good ping)
    <ISO UTC> HEARTBEAT_STATE <old>-><new>
    <ISO UTC> HEARTBEAT_PING_FAILED error=<kind> state=<state>[ endpoint=fail] consecutive=<n> unlogged=<n>
    <ISO UTC> HEARTBEAT_RECOVERED after=<n> failed pings
    <ISO UTC> WARNING heartbeat: on battery during the session — paging via the monitor
    <ISO UTC> HEARTBEAT_POWER battery paging ended power=<ac|battery|unknown>
    <ISO UTC> HEARTBEAT_ORPHANED parent=<pid> gone
    <ISO UTC> HEARTBEAT_STOP reason=<signal|orphaned|once> ...
    <ISO UTC> HEARTBEAT_NOT_CONFIGURED <detail>         (exit 78)
"""
from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import threading
from typing import TextIO
import urllib.error
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

from trading.operations.keychain import (
    HEARTBEAT_KEYCHAIN_ACCOUNT,
    HEARTBEAT_KEYCHAIN_SERVICE,
    KeychainReadError,
    MacOSKeychainReader,
    heartbeat_item_exists,
    read_heartbeat_url,
)
from trading.operations.paths import UserPaths

UTC = timezone.utc
ET = ZoneInfo("America/New_York")
HEARTBEAT_CHILD = "heartbeat"
HEARTBEAT_MODULE = "trading.operations.heartbeat"
PING_INTERVAL = timedelta(seconds=60)
PING_TIMEOUT_S = 10.0
# At most one HEARTBEAT_PING_FAILED line per this long, whatever the failures.
ERROR_LOG_INTERVAL = timedelta(minutes=10)
# A closed-port exit this recent makes the state ``outage``.
OUTAGE_LOOKBACK = timedelta(minutes=10)
# Every reason of the supervisor's CHILD_EXIT_UNCOUNTED lines, in one of two
# sets (a test pins their union to the supervisor's log contract, so a new
# reason needs a decision here): only a port found closed is an outage.
OUTAGE_REASONS = frozenset({"port_closed"})
NOT_OUTAGE_REASONS = frozenset({"port_unknown", "schema_not_ready"})
STATES = ("idle", "session", "outage")
# EX_CONFIG: no (valid) URL in the Keychain. The supervisor schedules the child
# only when there is one, so this means it was removed while running.
NOT_CONFIGURED_CODE = 78
# ``--once`` only: the ping did not get through. EX_UNAVAILABLE, never 75: under
# backend/trading an exit 75 is the schema gate's refusal alone (P1-A.2's AST
# guard in test_supervisor.py), which the supervisor never counts.
PING_FAILED_CODE = 69
# The supervisor's own log (pinned equal to supervisor.SUPERVISOR_LOG_NAME by a
# test; not imported, so the child loads none of the supervisor's stack).
SUPERVISOR_LOG_NAME = "supervisor.log"
KEYCHAIN_ITEM = f"{HEARTBEAT_KEYCHAIN_SERVICE}/{HEARTBEAT_KEYCHAIN_ACCOUNT}"
USER_AGENT = "autotrader-v1-heartbeat"
# http is accepted only to a loopback host (tests); a real monitor is https,
# or the secret URL would cross the network in clear text.
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_MAX_RESPONSE_BYTES = 4096
_MAX_READ_BYTES = 1 << 20
_MAX_LINE_BYTES = 16 << 10
# The reader's fingerprint of the file it follows: its first bytes (a
# supervisor.log starts with a timestamped line, unique per file).
_HEAD_BYTES = 64
_STOP_SIGNALS = (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)
# The power rule: on battery inside this ET window of a session day, page.
POWER_WINDOW = (time(7, 40), time(16, 15))
# A healthchecks-style monitor's failure endpoint: <url>/fail marks the check down.
FAIL_SUFFIX = "/fail"
BATTERY_REASON = "battery"
BATTERY_PAGE_INTERVAL = timedelta(minutes=15)
BATTERY_WARNING = "heartbeat: on battery during the session — paging via the monitor"
PMSET_COMMAND = ("/usr/bin/pmset", "-g", "batt")
PMSET_TIMEOUT_S = 5.0
_POWER_SOURCE = re.compile(r"drawing from '(?P<source>[^']+)'")

# The ping URL, or None when none is configured.
UrlSource = Callable[[], str | None]
Clock = Callable[[], datetime]
# Waits up to the given seconds; True when a stop was requested meanwhile.
Wait = Callable[[float], bool]
Emit = Callable[[str], None]
# (request URL, timeout seconds): one GET; raises on any failure.
Sender = Callable[[str, float], None]
# ``pmset -g batt``'s output.
PmsetRunner = Callable[[], str]
# Whether a date is an XNYS session day.
SessionDay = Callable[[date], bool]


def _utc_now() -> datetime:
    return datetime.now(UTC)


def iso_utc(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat(timespec="seconds")


@dataclass(frozen=True, slots=True)
class PingTarget:
    """A validated ping URL; its repr shows the host only, never the URL."""

    url: str = field(repr=False)
    host: str

    @classmethod
    def parse(cls, url: str) -> PingTarget:
        """Refused (ValueError, a message without the URL): not https, or http to a
        non-loopback host, or no host."""
        try:
            parts = urllib.parse.urlsplit(url.strip())
            host = parts.hostname
            parts.port  # noqa: B018 - a malformed port raises here, not at the first ping
        except ValueError:
            raise ValueError("the heartbeat URL is malformed") from None
        if not host:
            raise ValueError("the heartbeat URL has no host")
        if parts.scheme != "https" and not (parts.scheme == "http" and host in _LOOPBACK_HOSTS):
            raise ValueError("the heartbeat URL is not https")
        return cls(url=url.strip(), host=host)


def url_from_keychain(reader: MacOSKeychainReader | None = None) -> str | None:
    """The ping URL from its Keychain item; None when there is none (or it cannot be read)."""
    try:
        return read_heartbeat_url(reader)
    except KeychainReadError:
        return None


# The dry run's question: is the item there? (never its secret)
PresenceSource = Callable[[], bool]


def item_present(reader: MacOSKeychainReader | None = None) -> bool:
    """The ping URL's Keychain item exists; the URL itself is not read."""
    return heartbeat_item_exists(reader)


def presence_detail(source: PresenceSource) -> tuple[bool, str]:
    """``(scheduled, detail)`` for the agent-run dry run (N15b L-8): the item's
    presence only. The child reads and validates the URL itself at start."""
    try:
        present = source()
    except Exception as exc:  # noqa: BLE001 - a failed lookup is reported, never raised
        return False, f"unknown (Keychain item {KEYCHAIN_ITEM} could not be checked: {type(exc).__name__})"
    if not present:
        return False, f"not configured (no Keychain item {KEYCHAIN_ITEM})"
    return True, f"Keychain item {KEYCHAIN_ITEM} present (the child validates the URL)"


def load_target(source: UrlSource) -> tuple[PingTarget | None, str]:
    """``(target, detail)``: the validated URL, or None, and a line that never shows the URL.

    The detail is the dry run's ``heartbeat`` line and the supervisor's
    ``PREFLIGHT_WARNING heartbeat`` line; a broken source is "not configured",
    never an exception.
    """
    try:
        url = source()
    except Exception as exc:  # noqa: BLE001 - an unreadable Keychain must not stop the supervisor
        return None, f"not configured (Keychain item {KEYCHAIN_ITEM} unreadable: {type(exc).__name__})"
    if url is None:
        return None, f"not configured (no Keychain item {KEYCHAIN_ITEM})"
    try:
        target = PingTarget.parse(url)
    except ValueError:
        return None, f"not configured (Keychain item {KEYCHAIN_ITEM} is not an https URL)"
    return target, f"every {round(PING_INTERVAL.total_seconds())} s to {target.host}"


def _with_query(url: str, extra: str, *, path_suffix: str = "") -> str:
    parts = urllib.parse.urlsplit(url)
    query = f"{parts.query}&{extra}" if parts.query else extra
    path = parts.path.rstrip("/") + path_suffix if path_suffix else parts.path
    return urllib.parse.urlunsplit(parts._replace(path=path, query=query))


def ping_url(url: str, state: str, *, power: str | None = None) -> str:
    """The success ping: ``url`` with ``state=<state>`` (and ``power=battery`` on
    battery) appended to its query, the rest kept byte for byte."""
    if state not in STATES:
        raise ValueError(f"unknown heartbeat state {state!r}")
    extra = f"state={state}" + ("&power=battery" if power == "battery" else "")
    return _with_query(url, extra)


def fail_url(url: str, reason: str = BATTERY_REASON) -> str:
    """The monitor's failure endpoint: ``<url>/fail?reason=<reason>`` (a page)."""
    return _with_query(url, f"reason={reason}", path_suffix=FAIL_SUFFIX)


def parse_power_source(output: str) -> str | None:
    """``battery``, ``ac`` or None (unknown) from ``pmset -g batt``'s first line,
    ``Now drawing from 'Battery Power'`` or ``... 'AC Power'``."""
    match = _POWER_SOURCE.search(output)
    if match is None:
        return None
    source = match.group("source").strip().lower()
    if source == "battery power":
        return "battery"
    if source == "ac power":
        return "ac"
    return None


def run_pmset() -> str:
    """``pmset -g batt``'s output; empty when it fails (read-only, no privileges)."""
    completed = subprocess.run(
        PMSET_COMMAND, capture_output=True, text=True, timeout=PMSET_TIMEOUT_S, check=False
    )
    return completed.stdout if completed.returncode == 0 else ""


def power_source(runner: PmsetRunner | None = None) -> str | None:
    """The Mac's power source; None when ``pmset`` cannot tell. Never raises."""
    try:
        return parse_power_source((runner or run_pmset)())
    except Exception:  # noqa: BLE001 - no power information never stops a ping
        return None


def is_session_day(day: date) -> bool:
    """An XNYS session day. The calendar loads on use (it is heavy); if it
    fails, every weekday counts, so a broken calendar never silences a page."""
    try:
        from trading.scheduler.sessions import market_session

        return market_session(day) is not None
    except Exception:  # noqa: BLE001 - fail toward paging
        return day.weekday() < 5


def in_power_window(now: datetime, session_day: SessionDay = is_session_day) -> bool:
    """07:40 <= ET clock < 16:15 on a session day."""
    local = now.astimezone(ET)
    start, end = POWER_WINDOW
    return start <= local.time() < end and session_day(local.date())


class PingFailed(Exception):
    """A ping without a 2xx/3xx answer; ``kind`` is safe to log (never the URL)."""

    def __init__(self, kind: str) -> None:
        super().__init__(kind)
        self.kind = kind


def failure_kind(exc: BaseException) -> str:
    """A loggable name for a ping failure: an HTTP status or an exception type."""
    if isinstance(exc, PingFailed):
        return exc.kind
    if isinstance(exc, urllib.error.HTTPError):
        return f"http_{exc.code}"
    if isinstance(exc, urllib.error.URLError):
        reason = exc.reason
        if isinstance(reason, TimeoutError):
            return "timeout"
        return f"network_{type(reason).__name__}" if isinstance(reason, BaseException) else "network"
    if isinstance(exc, TimeoutError):
        return "timeout"
    if isinstance(exc, ValueError):
        return "invalid_url"
    return type(exc).__name__


def send_ping(
    url: str,
    timeout: float = PING_TIMEOUT_S,
    *,
    opener: urllib.request.OpenerDirector | None = None,
) -> None:
    """One ``GET <url>`` (a ``ping_url`` or ``fail_url``); raises PingFailed (no URL
    in it) on any failure."""
    try:
        request = urllib.request.Request(url, method="GET", headers={"User-Agent": USER_AGENT})
        with (opener or urllib.request.build_opener()).open(request, timeout=timeout) as response:
            response.read(_MAX_RESPONSE_BYTES)
    except Exception as exc:  # noqa: BLE001 - every failure becomes a loggable kind
        raise PingFailed(failure_kind(exc)) from None


class SupervisorLogState:
    """The supervisor log's newest evidence, read incrementally, complete lines only.

    It follows the file across the daily rotation (trading.operations.logging):
    an inode change, a size below the offset read so far, or first bytes that
    are no longer the ones read (truncated, then regrown past the offset
    between two reads) start over at offset 0, and what was already learned
    stays (the supervisor also carries its newest SESSION_* line into the new
    file). An unreadable or missing log leaves the last known state. It never
    raises.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._identity: tuple[int, int] | None = None
        self._offset = 0
        self._partial = b""
        self._head = b""
        self._in_session = False
        self._port_closed_at: datetime | None = None

    def refresh(self) -> None:
        try:
            descriptor = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        except OSError:
            return
        try:
            with open(descriptor, "rb", closefd=True) as stream:
                status = os.fstat(stream.fileno())
                identity = (status.st_dev, status.st_ino)
                if (
                    identity != self._identity
                    or status.st_size < self._offset
                    or os.pread(stream.fileno(), len(self._head), 0) != self._head
                ):
                    self._identity = identity
                    self._offset = 0
                    self._partial = b""
                    self._head = b""
                stream.seek(self._offset)
                while chunk := stream.read(_MAX_READ_BYTES):
                    if len(self._head) < _HEAD_BYTES:
                        self._head += chunk[: _HEAD_BYTES - len(self._head)]
                    self._offset += len(chunk)
                    self._consume(chunk)
        except OSError:
            return

    def _consume(self, chunk: bytes) -> None:
        *complete, partial = (self._partial + chunk).split(b"\n")
        self._partial = partial if len(partial) <= _MAX_LINE_BYTES else b""
        for raw in complete:
            self.observe(raw.decode("utf-8", "replace"))

    def observe(self, line: str) -> None:
        """One supervisor log line: ``<ISO UTC> <KIND> ...``."""
        fields = line.strip().split(" ", 3)
        if len(fields) < 2:
            return
        kind = fields[1]
        if kind == "SESSION_START":
            self._in_session = True
        elif kind.startswith("SESSION_") or kind in ("SUPERVISOR_START", "SUPERVISOR_STOP"):
            # SESSION_END/WAIT/OVER, or a supervisor that (re)started or stopped.
            self._in_session = False
        elif (
            kind == "CHILD_EXIT_UNCOUNTED"
            and len(fields) == 4
            and any(f"reason={reason}" in fields[3].split() for reason in OUTAGE_REASONS)
        ):
            try:
                at = datetime.fromisoformat(fields[0])
            except ValueError:
                return
            if at.tzinfo is None or at.utcoffset() is None:
                return
            if self._port_closed_at is None or at > self._port_closed_at:
                self._port_closed_at = at

    def state(self, now: datetime) -> str:
        closed = self._port_closed_at
        if closed is not None and now - closed <= OUTAGE_LOOKBACK:
            return "outage"
        return "session" if self._in_session else "idle"

    def state_at(self, now: datetime) -> str:
        self.refresh()
        return self.state(now)


class HeartbeatClient:
    """Pings on a fixed 60 s cadence; injectable clock, wait, sender, state and power.

    ``ticks`` counts the cadence's slots; ``pings`` the requests sent (a slot
    on battery inside the power window between two pages sends none).
    """

    def __init__(
        self,
        target: PingTarget,
        *,
        state_of: Callable[[datetime], str],
        emit: Emit,
        wait: Wait,
        clock: Clock = _utc_now,
        sender: Sender = send_ping,
        interval: timedelta = PING_INTERVAL,
        timeout: float = PING_TIMEOUT_S,
        error_log_interval: timedelta = ERROR_LOG_INTERVAL,
        parent_alive: Callable[[], bool] = lambda: True,
        power: Callable[[], str | None] = lambda: None,
        power_window: Callable[[datetime], bool] = in_power_window,
        battery_page_interval: timedelta = BATTERY_PAGE_INTERVAL,
    ) -> None:
        if interval <= timedelta(0) or timeout <= 0:
            raise ValueError("a positive interval and timeout are required")
        self._target = target
        self._state_of = state_of
        self._emit = emit
        self._wait = wait
        self._clock = clock
        self._sender = sender
        self._interval = interval
        self._timeout = timeout
        self._error_log_interval = error_log_interval
        self._parent_alive = parent_alive
        self._power = power
        self._power_window = power_window
        self._battery_page_interval = battery_page_interval
        self.ticks = 0
        self.pings = 0
        self.failures = 0
        self.battery_pages = 0
        # Never reset when power returns: a flapping supply pages at most once per interval.
        self._last_battery_page: datetime | None = None
        self._battery_paging = False
        self._state: str | None = None
        self._ok_seen = False
        self._consecutive = 0
        self._unlogged = 0
        self._streak_logged = False
        self._last_failure_log: datetime | None = None

    def _event(self, now: datetime, message: str) -> None:
        self._emit(f"{iso_utc(now)} {message}")

    def _current_state(self, now: datetime) -> str:
        try:
            state = self._state_of(now)
        except Exception:  # noqa: BLE001 - the ping (liveness) matters more than its state
            return "idle"
        return state if state in STATES else "idle"

    def _current_power(self) -> str | None:
        try:
            power = self._power()
        except Exception:  # noqa: BLE001 - no power information never stops a ping
            return None
        return power if power in ("ac", "battery") else None

    def _in_window(self, now: datetime) -> bool:
        try:
            return bool(self._power_window(now))
        except Exception:  # noqa: BLE001 - fail toward paging: a window that cannot be read is open
            return True

    def ping(self, now: datetime) -> bool:
        """One cadence slot: the success ping, or on battery inside the power
        window the failure endpoint (at most once per 15 minutes). True when
        a request got through. Never raises."""
        self.ticks += 1
        state = self._current_state(now)
        if self._state is not None and state != self._state:
            self._event(now, f"HEARTBEAT_STATE {self._state}->{state}")
        self._state = state
        power = self._current_power()
        if power == "battery" and self._in_window(now):
            return self._page_battery(now, state)
        if self._battery_paging:
            self._battery_paging = False
            self._event(now, f"HEARTBEAT_POWER battery paging ended power={power or 'unknown'}")
        sent = self._send(now, ping_url(self._target.url, state, power=power), f"state={state}")
        if sent and not self._ok_seen:
            self._ok_seen = True
            self._event(now, f"HEARTBEAT_OK state={state}")
        return sent

    def _page_battery(self, now: datetime, state: str) -> bool:
        """The monitor's failure endpoint instead of the success ping: no success
        ping is sent meanwhile, so the check stays down until power returns."""
        last = self._last_battery_page
        if last is not None and now - last < self._battery_page_interval:
            return False
        self._battery_paging = True
        self._last_battery_page = now
        self.battery_pages += 1
        self._event(now, f"WARNING {BATTERY_WARNING}")
        return self._send(now, fail_url(self._target.url), f"state={state} endpoint=fail")

    def _send(self, now: datetime, url: str, label: str) -> bool:
        self.pings += 1
        try:
            self._sender(url, self._timeout)
        except Exception as exc:  # noqa: BLE001 - never the text: urllib's can quote the URL
            self._failed(now, failure_kind(exc), label)
            return False
        if self._consecutive and self._streak_logged:
            self._ok_seen = True
            self._event(now, f"HEARTBEAT_RECOVERED after={self._consecutive} failed pings")
        self._consecutive = 0
        self._streak_logged = False
        return True

    def _failed(self, now: datetime, kind: str, label: str) -> None:
        self.failures += 1
        self._consecutive += 1
        last = self._last_failure_log
        if last is not None and now - last < self._error_log_interval:
            self._unlogged += 1
            return
        self._event(
            now,
            f"HEARTBEAT_PING_FAILED error={kind} {label}"
            f" consecutive={self._consecutive} unlogged={self._unlogged}",
        )
        self._last_failure_log = now
        self._unlogged = 0
        self._streak_logged = True

    def run(self) -> str:
        """Ping every interval until a stop request or the parent's death; the reason."""
        start = self._clock()
        self._event(
            start,
            f"HEARTBEAT_START every={round(self._interval.total_seconds())}s"
            f" timeout={self._timeout:g}s host={self._target.host}",
        )
        next_at = start
        while True:
            if not self._parent_alive():
                return "orphaned"
            now = self._clock()
            if now >= next_at:
                self.ping(now)
                next_at += self._interval
                if next_at <= now:
                    # Behind (the Mac slept): the ping just sent restarts the cadence.
                    next_at = now + self._interval
            delay = max(0.0, (next_at - self._clock()).total_seconds())
            if self._wait(delay):
                return "signal"


def main(
    argv: Sequence[str] | None = None,
    *,
    source: UrlSource | None = None,
    user_paths: UserPaths | None = None,
    out: TextIO | None = None,
    pmset: PmsetRunner | None = None,
    session_day: SessionDay = is_session_day,
) -> int:
    """The ``heartbeat`` child: ping until SIGTERM; ``--once`` sends one ping and exits.

    ``source``, ``pmset`` and ``session_day`` replace the Keychain read, the
    ``pmset -g batt`` run and the XNYS calendar (tests).
    """
    parser = argparse.ArgumentParser(
        prog="python -m trading.operations.heartbeat",
        description="The dead-man's switch client (the supervisor's always-on heartbeat child).",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="send one ping now and exit (0 sent, 69 failed): checks the Keychain item and the monitor",
    )
    arguments = parser.parse_args(argv)
    stream = sys.stdout if out is None else out

    def emit(line: str) -> None:
        print(line, file=stream, flush=True)

    target, detail = load_target(url_from_keychain if source is None else source)
    if target is None:
        emit(f"{iso_utc(_utc_now())} HEARTBEAT_NOT_CONFIGURED {detail}")
        return NOT_CONFIGURED_CODE
    paths = user_paths or UserPaths.current()
    log_state = SupervisorLogState(paths.log_directory / SUPERVISOR_LOG_NAME)
    stop = threading.Event()
    parent = os.getppid()

    def parent_alive() -> bool:
        if os.getppid() == parent:
            return True
        emit(f"{iso_utc(_utc_now())} HEARTBEAT_ORPHANED parent={parent} gone")
        return False

    client = HeartbeatClient(
        target,
        state_of=log_state.state_at,
        emit=emit,
        wait=stop.wait,
        parent_alive=parent_alive,
        power=lambda: power_source(pmset),
        power_window=lambda now: in_power_window(now, session_day),
    )
    if arguments.once:
        sent = client.ping(_utc_now())
        emit(f"{iso_utc(_utc_now())} HEARTBEAT_STOP reason=once sent={'yes' if sent else 'no'}")
        return 0 if sent else PING_FAILED_CODE
    previous = {signum: signal.getsignal(signum) for signum in _STOP_SIGNALS}

    def request_stop(_signum: int, _frame: object) -> None:
        stop.set()

    try:
        for signum in _STOP_SIGNALS:
            signal.signal(signum, request_stop)
        reason = client.run()
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)
    emit(
        f"{iso_utc(_utc_now())} HEARTBEAT_STOP reason={reason}"
        f" pings={client.pings} failed={client.failures} battery_pages={client.battery_pages}"
    )
    return 0


__all__ = [
    "BATTERY_PAGE_INTERVAL",
    "BATTERY_WARNING",
    "ERROR_LOG_INTERVAL",
    "FAIL_SUFFIX",
    "HEARTBEAT_CHILD",
    "HEARTBEAT_MODULE",
    "KEYCHAIN_ITEM",
    "NOT_CONFIGURED_CODE",
    "NOT_OUTAGE_REASONS",
    "OUTAGE_REASONS",
    "OUTAGE_LOOKBACK",
    "PING_FAILED_CODE",
    "PING_INTERVAL",
    "PING_TIMEOUT_S",
    "POWER_WINDOW",
    "STATES",
    "HeartbeatClient",
    "PingFailed",
    "PingTarget",
    "SupervisorLogState",
    "fail_url",
    "failure_kind",
    "in_power_window",
    "is_session_day",
    "item_present",
    "load_target",
    "main",
    "presence_detail",
    "parse_power_source",
    "ping_url",
    "power_source",
    "run_pmset",
    "send_ping",
    "url_from_keychain",
]


if __name__ == "__main__":
    raise SystemExit(main())

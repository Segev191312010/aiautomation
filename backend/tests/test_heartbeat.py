"""The dead-man's switch client: cadence, state, power, failures, secrecy, lifecycle.

Every ping goes to a local fake HTTP server on 127.0.0.1 (an ephemeral port,
never the Gateway's), every URL is fake, the Keychain is never read (the
reader gets a fake command runner) and ``pmset`` is never run (fake output).
"""
from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import datetime, timedelta, timezone
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

import pytest

from trading.operations import heartbeat
from trading.operations.heartbeat import (
    HeartbeatClient,
    PingFailed,
    PingTarget,
    SupervisorLogState,
    fail_url,
    in_power_window,
    load_target,
    parse_power_source,
    ping_url,
    power_source,
    send_ping,
    url_from_keychain,
)
from trading.operations.keychain import MacOSKeychainReader
from trading.operations.paths import UserPaths

ET = ZoneInfo("America/New_York")
UTC = timezone.utc
# A fake secret: it must never reach a log line.
TOKEN = "f4ke-0000-uuid-heartbeat-secret"
# Direct connections only: a system proxy must not reroute the loopback fake.
DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))
# `pmset -g batt` as macOS prints it.
PMSET_BATTERY = (
    "Now drawing from 'Battery Power'\n"
    " -InternalBattery-0 (id=1234567)\t83%; discharging; 4:41 remaining present: true\n"
)
PMSET_AC = (
    "Now drawing from 'AC Power'\n"
    " -InternalBattery-0 (id=1234567)\t100%; charged; 0:00 remaining present: true\n"
)
TUESDAY = (2026, 10, 13)  # an XNYS session
SATURDAY = (2026, 10, 17)
# The real runner, before the guard below replaces it (its subprocess is faked where used).
REAL_RUN_PMSET = heartbeat.run_pmset


@pytest.fixture(autouse=True)
def _no_real_pmset(monkeypatch):
    """A test that would run the real `pmset` fails (the client catches
    Exception, not pytest's failure)."""

    def refuse() -> str:
        pytest.fail("a test ran the real pmset")

    monkeypatch.setattr(heartbeat, "run_pmset", refuse)


def et(year: int, month: int, day: int, hour: int, minute: int, second: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, second, tzinfo=ET).astimezone(UTC)


class FakeMonitor(ThreadingHTTPServer):
    """A healthchecks-style endpoint: records each request, answers ``status``."""

    daemon_threads = True

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _MonitorHandler)
        self.requests: list[str] = []
        self.status = 200
        self.delay = 0.0

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}"

    @property
    def states(self) -> list[str]:
        """Each request's ``state``, or ``fail:<reason>`` for the failure endpoint."""
        found: list[str] = []
        for path in self.requests:
            parts = urllib.parse.urlsplit(path)
            query = urllib.parse.parse_qs(parts.query)
            if parts.path.endswith("/fail"):
                found.append(f"fail:{query['reason'][-1]}")
            else:
                found.append(query["state"][-1] + ("+battery" if query.get("power") == ["battery"] else ""))
        return found

    def handle_error(self, request, client_address) -> None:  # noqa: ANN001
        pass  # a client that timed out and left is expected here


class _MonitorHandler(BaseHTTPRequestHandler):
    server: FakeMonitor

    def do_GET(self) -> None:  # noqa: N802 - the http.server hook name
        self.server.requests.append(self.path)
        if self.server.delay:
            time.sleep(self.server.delay)
        body = b"OK"
        self.send_response(self.server.status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass  # the server's own access log would print the secret path


@pytest.fixture
def monitor() -> Iterator[FakeMonitor]:
    server = FakeMonitor()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()


def secret_url(monitor: FakeMonitor) -> str:
    return f"{monitor.base}/ping/{TOKEN}"


class FakeTime:
    """A virtual clock; ``wait`` advances it, runs the actions it passes, and
    reports a stop once ``stop_when`` holds."""

    def __init__(self, start: datetime) -> None:
        self.now = start
        self.waits: list[float] = []
        self.actions: list[tuple[datetime, Callable[[], None]]] = []
        self.stop_when: Callable[[], bool] = lambda: False

    def clock(self) -> datetime:
        return self.now

    def at(self, moment: datetime, action: Callable[[], None]) -> None:
        self.actions.append((moment, action))
        self.actions.sort(key=lambda item: item[0])

    def wait(self, seconds: float) -> bool:
        self.waits.append(seconds)
        target = self.now + timedelta(seconds=seconds)
        while self.actions and self.actions[0][0] <= target:
            moment, action = self.actions.pop(0)
            self.now = max(self.now, moment)
            action()
        self.now = max(self.now, target)  # an action may have jumped ahead (a sleeping Mac)
        return self.stop_when()


def client_for(
    url: str,
    fake: FakeTime,
    *,
    state_of: Callable[[datetime], str] = lambda _now: "idle",
    lines: list[str],
    pings: int,
    timeout: float = heartbeat.PING_TIMEOUT_S,
    sender=None,
    parent_alive: Callable[[], bool] = lambda: True,
    power: Callable[[], str | None] = lambda: None,
) -> HeartbeatClient:
    """A client on the fake clock that stops after ``pings`` cadence slots."""
    client = HeartbeatClient(
        PingTarget.parse(url),
        state_of=state_of,
        emit=lines.append,
        wait=fake.wait,
        clock=fake.clock,
        sender=sender or partial(send_ping, opener=DIRECT),
        timeout=timeout,
        parent_alive=parent_alive,
        power=power,
    )
    fake.stop_when = lambda: client.ticks >= pings
    return client


def append(path: Path, line: str) -> Callable[[], None]:
    def write() -> None:
        with path.open("a", encoding="utf-8") as stream:
            stream.write(line + "\n")

    return write


def iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat(timespec="seconds")


# --- the contract constants ------------------------------------------------------------


def test_the_cadence_timeout_and_throttle_are_the_owners():
    assert heartbeat.PING_INTERVAL == timedelta(seconds=60)
    assert heartbeat.PING_TIMEOUT_S == 10.0
    assert heartbeat.ERROR_LOG_INTERVAL == timedelta(minutes=10)
    assert heartbeat.OUTAGE_LOOKBACK == timedelta(minutes=10)
    assert heartbeat.STATES == ("idle", "session", "outage")
    assert heartbeat.HEARTBEAT_CHILD == "heartbeat"


# --- cadence and state -----------------------------------------------------------------


def test_pings_every_60_seconds_with_the_state_read_from_the_supervisor_log(tmp_path, monitor):
    log = tmp_path / "supervisor.log"
    start = et(2026, 10, 13, 7, 53, 30)
    log.write_text(
        f"{iso(start - timedelta(hours=10))} SUPERVISOR_START pid=500 mode=daily\n"
        f"{iso(start - timedelta(hours=10))} SESSION_WAIT date=2026-10-13 until={iso(et(2026, 10, 13, 7, 55))}\n"
    )
    fake = FakeTime(start)
    fake.at(et(2026, 10, 13, 7, 55), append(log, f"{iso(et(2026, 10, 13, 7, 55))} SESSION_START date=2026-10-13"))
    closed = et(2026, 10, 13, 7, 57, 10)
    fake.at(closed, append(log, f"{iso(closed)} CHILD_EXIT screener code=75 restart_in=15"))
    fake.at(closed, append(log, f"{iso(closed)} CHILD_EXIT_UNCOUNTED screener reason=port_closed"))
    ended = et(2026, 10, 13, 8, 8, 45)
    fake.at(ended, append(log, f"{iso(ended)} SESSION_END date=2026-10-13"))
    sent: list[datetime] = []

    def timed_send(url: str, timeout: float) -> None:
        sent.append(fake.now)
        send_ping(url, timeout, opener=DIRECT)

    lines: list[str] = []
    state = SupervisorLogState(log)
    client = client_for(
        secret_url(monitor), fake, state_of=state.state_at, lines=lines, pings=17, sender=timed_send
    )

    assert client.run() == "signal"

    assert sent == [start + timedelta(seconds=60 * k) for k in range(17)]
    assert set(fake.waits) == {60.0}
    assert monitor.states == ["idle"] * 2 + ["session"] * 2 + ["outage"] * 10 + ["session"] * 2 + ["idle"]
    assert {urllib.parse.urlsplit(path).path for path in monitor.requests} == {f"/ping/{TOKEN}"}
    transitions = [line.split(" ", 1)[1] for line in lines if "HEARTBEAT_STATE" in line]
    assert transitions == [
        "HEARTBEAT_STATE idle->session",
        "HEARTBEAT_STATE session->outage",
        "HEARTBEAT_STATE outage->session",
        "HEARTBEAT_STATE session->idle",
    ]
    assert lines[0] == f"{iso(start)} HEARTBEAT_START every=60s timeout=10s host=127.0.0.1"
    assert lines[1] == f"{iso(start)} HEARTBEAT_OK state=idle"
    assert not any("FAILED" in line for line in lines)


def test_a_slow_ping_keeps_the_cadence_and_a_woken_mac_pings_at_once(monitor):
    start = et(2026, 10, 17, 12, 0)
    fake = FakeTime(start)
    sent: list[datetime] = []

    def send(url: str, timeout: float) -> None:
        sent.append(fake.now)
        if len(sent) == 2:
            fake.now += timedelta(seconds=9)  # a slow answer

    def sleep_two_hours() -> None:
        fake.now += timedelta(hours=2)

    # The Mac sleeps during the wait after the third ping.
    fake.at(start + timedelta(seconds=150), sleep_two_hours)
    client = client_for(secret_url(monitor), fake, lines=[], pings=5, sender=send)
    client.run()

    woke = start + timedelta(seconds=150, hours=2)
    assert sent == [
        start,
        start + timedelta(seconds=60),
        start + timedelta(seconds=120),  # the slow answer did not shift the cadence
        woke,  # at once on waking, then every 60 s from there
        woke + timedelta(seconds=60),
    ]


def test_outage_lasts_ten_minutes_after_the_newest_closed_port_exit(tmp_path):
    state = SupervisorLogState(tmp_path / "supervisor.log")
    closed = et(2026, 10, 13, 9, 30)
    for line in (
        f"{iso(et(2026, 10, 13, 7, 55))} SESSION_START date=2026-10-13",
        f"{iso(closed)} CHILD_EXIT_UNCOUNTED bridge reason=port_closed",
        # An unknown port (a failed probe) is not an outage, nor another reason.
        f"{iso(closed + timedelta(minutes=5))} CHILD_EXIT_UNCOUNTED momo reason=port_unknown",
        "not a supervisor line",
        "",
    ):
        state.observe(line)

    assert state.state(closed) == "outage"
    assert state.state(closed + timedelta(minutes=10)) == "outage"
    assert state.state(closed + timedelta(minutes=10, seconds=1)) == "session"
    state.observe(f"{iso(et(2026, 10, 13, 18, 30))} SESSION_END date=2026-10-13")
    assert state.state(et(2026, 10, 13, 18, 31)) == "idle"
    # A supervisor that restarted mid-session is idle until its SESSION_START.
    state.observe(f"{iso(et(2026, 10, 14, 9, 0))} SESSION_START date=2026-10-14")
    state.observe(f"{iso(et(2026, 10, 14, 9, 5))} SUPERVISOR_START pid=7 mode=daily")
    assert state.state(et(2026, 10, 14, 9, 6)) == "idle"


def test_the_state_follows_the_log_across_rename_and_truncation(tmp_path):
    log = tmp_path / "supervisor.log"
    log.write_text(f"{iso(et(2026, 10, 13, 7, 55))} SESSION_START date=2026-10-13\n")
    state = SupervisorLogState(log)
    now = et(2026, 10, 13, 18, 40)
    assert state.state_at(now) == "session"

    # Renamed away, a new file in its place (the supervisor's own rotation).
    log.rename(tmp_path / "supervisor.log.2026-10-13")
    log.write_text(f"{iso(et(2026, 10, 13, 18, 30))} SESSION_END date=2026-10-13\n")
    assert state.state_at(now) == "idle"

    # Truncated in place (copy-then-truncate) and regrown past the old offset
    # before the next read: the size alone cannot tell, the first bytes do.
    old_size = log.stat().st_size
    with log.open("r+b") as stream:
        stream.truncate(0)
    with log.open("a") as stream:
        stream.write(f"{iso(et(2026, 10, 14, 7, 55))} SESSION_START date=2026-10-14\n")
    assert log.stat().st_size > old_size
    assert state.state_at(et(2026, 10, 14, 8, 0)) == "session"

    # Truncated, a shorter file: the size shrinks below the offset.
    with log.open("r+b") as stream:
        stream.truncate(0)
    with log.open("a") as stream:
        stream.write(f"{iso(et(2026, 10, 14, 7, 56))} SESSION_END d\n")
    assert state.state_at(et(2026, 10, 14, 8, 0)) == "idle"
    with log.open("a") as stream:
        stream.write(f"{iso(et(2026, 10, 14, 7, 57))} SESSION_START date=2026-10-14\n")
    assert state.state_at(et(2026, 10, 14, 8, 0)) == "session"

    # A half-written line waits for its newline; a missing log keeps the state.
    with log.open("a") as stream:
        stream.write(f"{iso(et(2026, 10, 14, 18, 30))} SESSION_E")
    assert state.state_at(et(2026, 10, 14, 18, 31)) == "session"
    with log.open("a") as stream:
        stream.write("ND date=2026-10-14\n")
    assert state.state_at(et(2026, 10, 14, 18, 31)) == "idle"
    log.unlink()
    assert state.state_at(et(2026, 10, 14, 18, 32)) == "idle"


def test_a_broken_state_reader_still_pings(monitor):
    fake = FakeTime(et(2026, 10, 17, 12, 0))

    def broken(_now: datetime) -> str:
        raise RuntimeError("unreadable")

    client_for(secret_url(monitor), fake, state_of=broken, lines=[], pings=2).run()

    assert monitor.states == ["idle", "idle"]


# --- failures --------------------------------------------------------------------------


def test_http_500_is_logged_once_per_ten_minutes_and_the_pings_continue(monitor):
    start = et(2026, 10, 17, 12, 0)
    fake = FakeTime(start)
    monitor.status = 500
    fake.at(start + timedelta(minutes=24, seconds=30), lambda: setattr(monitor, "status", 200))
    lines: list[str] = []

    client = client_for(secret_url(monitor), fake, lines=lines, pings=27)
    assert client.run() == "signal"

    assert len(monitor.requests) == 27
    failed = [line for line in lines if "HEARTBEAT_PING_FAILED" in line]
    assert failed == [
        f"{iso(start)} HEARTBEAT_PING_FAILED error=http_500 state=idle consecutive=1 unlogged=0",
        f"{iso(start + timedelta(minutes=10))} HEARTBEAT_PING_FAILED error=http_500 state=idle consecutive=11 unlogged=9",
        f"{iso(start + timedelta(minutes=20))} HEARTBEAT_PING_FAILED error=http_500 state=idle consecutive=21 unlogged=9",
    ]
    assert [line for line in lines if "RECOVERED" in line] == [
        f"{iso(start + timedelta(minutes=25))} HEARTBEAT_RECOVERED after=25 failed pings"
    ]
    assert client.failures == 25
    assert not any("HEARTBEAT_OK" in line for line in lines)  # the recovery says it


def test_a_flapping_monitor_is_throttled_too(monitor):
    start = et(2026, 10, 17, 12, 0)
    fake = FakeTime(start)
    outcomes = iter([False, True] * 10)
    lines: list[str] = []

    def flapping(url: str, timeout: float) -> None:
        if not next(outcomes):
            raise PingFailed("http_503")

    client_for(secret_url(monitor), fake, lines=lines, pings=20, sender=flapping).run()

    # 20 minutes of fail/ok: one failure line and one recovery line per 10 minutes.
    assert [line.split(" ")[1] for line in lines if "FAILED" in line or "RECOVERED" in line] == [
        "HEARTBEAT_PING_FAILED",
        "HEARTBEAT_RECOVERED",
        "HEARTBEAT_PING_FAILED",
        "HEARTBEAT_RECOVERED",
    ]


def test_a_timeout_is_logged_once_and_the_pings_continue(monitor):
    monitor.delay = 1.0
    fake = FakeTime(et(2026, 10, 17, 12, 0))
    lines: list[str] = []

    client = client_for(secret_url(monitor), fake, lines=lines, pings=3, timeout=0.2)
    assert client.run() == "signal"

    assert len(monitor.requests) == 3
    failed = [line for line in lines if "HEARTBEAT_PING_FAILED" in line]
    assert len(failed) == 1 and "error=timeout" in failed[0]


def test_a_refused_connection_is_a_logged_failure_never_a_crash():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    fake = FakeTime(et(2026, 10, 17, 12, 0))
    lines: list[str] = []

    client_for(f"http://127.0.0.1:{port}/ping/{TOKEN}", fake, lines=lines, pings=2).run()

    failed = [line for line in lines if "HEARTBEAT_PING_FAILED" in line]
    assert len(failed) == 1 and "error=network_ConnectionRefusedError" in failed[0]


def test_failure_kinds_name_the_error_never_its_text():
    url = f"https://hc-ping.example/{TOKEN}"

    assert heartbeat.failure_kind(urllib.error.HTTPError(url, 404, "nope", {}, None)) == "http_404"
    assert heartbeat.failure_kind(urllib.error.URLError(TimeoutError("timed out"))) == "timeout"
    assert heartbeat.failure_kind(urllib.error.URLError(f"bad {url}")) == "network"
    assert heartbeat.failure_kind(TimeoutError()) == "timeout"
    assert heartbeat.failure_kind(ValueError(f"unknown url type: {url}")) == "invalid_url"
    assert heartbeat.failure_kind(RuntimeError(url)) == "RuntimeError"


# --- secrecy ----------------------------------------------------------------------------


def test_the_url_never_appears_in_any_log_line(tmp_path, monitor, capsys):
    url = f"{secret_url(monitor)}?also=s3cret-query"
    lines: list[str] = []
    paths = UserPaths(home=tmp_path)

    # A good ping, then a failing one, through the child's own entry point.
    out = io.StringIO()
    assert heartbeat.main(["--once"], source=lambda: url, user_paths=paths, out=out, pmset=lambda: PMSET_AC) == 0
    monitor.status = 500
    assert heartbeat.main(["--once"], source=lambda: url, user_paths=paths, out=out, pmset=lambda: PMSET_AC) == 75
    lines += out.getvalue().splitlines()

    # Senders whose exceptions quote the URL, and a recovery.
    fake = FakeTime(et(2026, 10, 17, 12, 0))
    errors = iter(
        [
            RuntimeError(f"failed to reach {url}"),
            urllib.error.HTTPError(url, 502, f"bad gateway {url}", {}, None),
            None,
        ]
    )

    def quoting(sent_url: str, timeout: float) -> None:
        error = next(errors)
        if error is not None:
            raise error

    client_for(url, fake, lines=lines, pings=3, sender=quoting).run()

    # The battery page, delivered and failing.
    paging = FakeTime(et(*TUESDAY, 10, 0))
    battery_errors = iter([RuntimeError(f"failed to reach {fail_url(url)}"), None])

    def battery_sender(sent_url: str, timeout: float) -> None:
        error = next(battery_errors, None)
        if error is not None:
            raise error

    client_for(url, paging, lines=lines, pings=16, sender=battery_sender, power=lambda: "battery").run()
    # The dry run's and the supervisor's lines, and a refused URL's.
    lines.append(load_target(lambda: url)[1])
    refused = io.StringIO()
    assert heartbeat.main([], source=lambda: f"http://hc-ping.example/{TOKEN}", out=refused) == 78
    lines += refused.getvalue().splitlines()

    for expected in (
        "HEARTBEAT_OK state=idle",
        "HEARTBEAT_PING_FAILED error=http_500",
        "HEARTBEAT_START every=60s timeout=10s host=127.0.0.1",
        "HEARTBEAT_PING_FAILED error=RuntimeError",
        "HEARTBEAT_RECOVERED after=2 failed pings",
        "every 60 s to 127.0.0.1",
        "HEARTBEAT_NOT_CONFIGURED not configured",
        "WARNING heartbeat: on battery during the session",
        "HEARTBEAT_PING_FAILED error=RuntimeError state=idle endpoint=fail",
    ):
        assert any(expected in line for line in lines), expected
    captured = capsys.readouterr()
    for text in (*lines, captured.out, captured.err, repr(PingTarget.parse(url))):
        assert TOKEN not in text
        assert "s3cret-query" not in text
    assert monitor.requests and all(TOKEN in path for path in monitor.requests)


def test_the_target_refuses_clear_text_and_keeps_the_query():
    assert PingTarget.parse("https://hc-ping.example/uuid").host == "hc-ping.example"
    assert PingTarget.parse("http://127.0.0.1:8080/uuid").host == "127.0.0.1"
    for bad in (
        "http://hc-ping.example/uuid",  # the secret in clear text over the network
        "ftp://hc-ping.example/uuid",
        "https:///uuid",
        "not a url",
        "https://hc-ping.example:99999/uuid",
    ):
        with pytest.raises(ValueError) as error:
            PingTarget.parse(bad)
        assert "uuid" not in str(error.value)
    assert ping_url("https://hc-ping.example/uuid", "session") == "https://hc-ping.example/uuid?state=session"
    assert (
        ping_url("https://hc-ping.example/uuid?rid=1", "outage")
        == "https://hc-ping.example/uuid?rid=1&state=outage"
    )
    with pytest.raises(ValueError):
        ping_url("https://hc-ping.example/uuid", "trading")
    assert (
        ping_url("https://hc-ping.example/uuid", "idle", power="battery")
        == "https://hc-ping.example/uuid?state=idle&power=battery"
    )
    assert ping_url("https://hc-ping.example/uuid", "idle", power="ac") == "https://hc-ping.example/uuid?state=idle"
    assert heartbeat.FAIL_SUFFIX == "/fail"
    assert fail_url("https://hc-ping.example/uuid") == "https://hc-ping.example/uuid/fail?reason=battery"
    assert fail_url("https://hc-ping.example/uuid/") == "https://hc-ping.example/uuid/fail?reason=battery"
    assert fail_url("https://hc-ping.example/uuid?rid=1") == "https://hc-ping.example/uuid/fail?rid=1&reason=battery"


def test_load_target_describes_the_keychain_item_without_the_url():
    item = "com.maccabi.autotrader-v1.heartbeat/heartbeat-url"

    assert load_target(lambda: None) == (None, f"not configured (no Keychain item {item})")
    target, detail = load_target(lambda: f"https://hc-ping.example/{TOKEN}")
    assert target is not None and detail == "every 60 s to hc-ping.example"
    assert load_target(lambda: f"http://hc-ping.example/{TOKEN}") == (
        None,
        f"not configured (Keychain item {item} is not an https URL)",
    )

    def broken() -> str:
        raise OSError("security binary missing")

    assert load_target(broken) == (None, f"not configured (Keychain item {item} unreadable: OSError)")


def test_the_url_is_read_from_its_own_keychain_item():
    commands: list[tuple[str, ...]] = []

    def found(command):
        commands.append(tuple(command))
        return subprocess.CompletedProcess(command, 0, stdout="https://hc-ping.example/uuid\n", stderr="")

    def missing(command):
        return subprocess.CompletedProcess(command, 44, stdout="", stderr="could not be found")

    assert url_from_keychain(MacOSKeychainReader(command_runner=found)) == "https://hc-ping.example/uuid"
    assert commands == [
        (
            "/usr/bin/security",
            "find-generic-password",
            "-a",
            "heartbeat-url",
            "-s",
            "com.maccabi.autotrader-v1.heartbeat",
            "-w",
        )
    ]
    assert url_from_keychain(MacOSKeychainReader(command_runner=missing)) is None


# --- lifecycle ---------------------------------------------------------------------------


def test_the_child_stops_pinging_when_its_parent_supervisor_dies(monitor):
    fake = FakeTime(et(2026, 10, 17, 12, 0))
    checks = iter([True, True, True, False])

    client = client_for(secret_url(monitor), fake, lines=[], pings=99, parent_alive=lambda: next(checks))

    assert client.run() == "orphaned"
    assert len(monitor.requests) == 3


def test_main_exits_when_orphaned_before_a_single_ping(tmp_path, monitor, monkeypatch):
    calls: list[int] = []

    def getppid() -> int:
        calls.append(1)
        return 4242 if len(calls) == 1 else 1  # recorded at start, then reparented

    monkeypatch.setattr(heartbeat.os, "getppid", getppid)
    out = io.StringIO()

    code = heartbeat.main(
        [], source=lambda: secret_url(monitor), user_paths=UserPaths(home=tmp_path), out=out, pmset=lambda: PMSET_AC
    )

    assert code == 0
    lines = out.getvalue().splitlines()
    assert "HEARTBEAT_START" in lines[0]
    assert lines[1].endswith("HEARTBEAT_ORPHANED parent=4242 gone")
    assert lines[2].endswith("HEARTBEAT_STOP reason=orphaned pings=0 failed=0 battery_pages=0")
    assert monitor.requests == []


def test_main_without_a_url_exits_78_and_sends_nothing(monitor):
    out = io.StringIO()

    assert heartbeat.main([], source=lambda: None, out=out) == 78

    assert out.getvalue().rstrip().endswith(
        "HEARTBEAT_NOT_CONFIGURED not configured (no Keychain item"
        " com.maccabi.autotrader-v1.heartbeat/heartbeat-url)"
    )
    assert monitor.requests == []


def test_main_pings_at_once_and_stops_cleanly_on_sigterm(tmp_path, monitor):
    (tmp_path / "Library" / "Logs" / "AutotraderV1").mkdir(parents=True)
    received: list[int] = []

    def harmless(signum: int, _frame: object) -> None:
        received.append(signum)  # only if main had not installed its own handler

    previous = signal.signal(signal.SIGTERM, harmless)
    timer = threading.Timer(0.5, os.kill, (os.getpid(), signal.SIGTERM))
    out = io.StringIO()
    try:
        timer.start()
        started = time.monotonic()
        code = heartbeat.main(
            [], source=lambda: secret_url(monitor), user_paths=UserPaths(home=tmp_path), out=out, pmset=lambda: PMSET_AC
        )
        elapsed = time.monotonic() - started
        restored = signal.getsignal(signal.SIGTERM)
    finally:
        timer.cancel()
        signal.signal(signal.SIGTERM, previous)

    assert code == 0
    assert elapsed < 10  # the 60 s wait ended at the signal
    assert received == []
    assert restored is harmless
    lines = out.getvalue().splitlines()
    assert "HEARTBEAT_START every=60s timeout=10s host=127.0.0.1" in lines[0]
    assert lines[-1].endswith("HEARTBEAT_STOP reason=signal pings=1 failed=0 battery_pages=0")
    assert monitor.states == ["idle"]


def test_the_module_runs_as_the_supervisors_argv():
    # `python -m trading.operations.heartbeat` from backend/ (the child's cwd);
    # --help reads no Keychain and sends nothing.
    backend = Path(heartbeat.__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, "-m", heartbeat.HEARTBEAT_MODULE, "--help"],
        cwd=backend,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "--once" in result.stdout


# --- power -------------------------------------------------------------------------------


def test_pmset_output_is_read_as_battery_ac_or_unknown():
    assert parse_power_source(PMSET_BATTERY) == "battery"
    assert parse_power_source(PMSET_AC) == "ac"
    assert parse_power_source("Now drawing from 'AC Power'\n") == "ac"  # a desktop Mac: no battery lines
    assert parse_power_source("Now drawing from 'UPS Power'\n") is None
    assert parse_power_source("") is None
    assert parse_power_source("pmset: unknown option") is None


def test_power_is_read_from_pmset_read_only_and_a_failure_is_unknown(monkeypatch):
    commands: list[tuple[str, ...]] = []

    def fake_run(command, **kwargs):
        commands.append(tuple(command))
        assert kwargs["timeout"] == heartbeat.PMSET_TIMEOUT_S
        return subprocess.CompletedProcess(command, 0, stdout=PMSET_BATTERY, stderr="")

    monkeypatch.setattr(heartbeat.subprocess, "run", fake_run)
    assert power_source(REAL_RUN_PMSET) == "battery"
    assert commands == [("/usr/bin/pmset", "-g", "batt")]

    def broken() -> str:
        raise subprocess.TimeoutExpired("pmset", 5)

    assert power_source(broken) is None
    assert power_source(lambda: "garbage") is None


def test_the_power_window_is_0740_to_1615_et_on_session_days_only():
    def at(day: tuple[int, int, int], hour: int, minute: int, second: int = 0) -> datetime:
        return et(*day, hour, minute, second)

    assert heartbeat.POWER_WINDOW == (heartbeat.time(7, 40), heartbeat.time(16, 15))
    assert not in_power_window(at(TUESDAY, 7, 39, 59))
    assert in_power_window(at(TUESDAY, 7, 40))
    assert in_power_window(at(TUESDAY, 15, 45))
    assert in_power_window(at(TUESDAY, 16, 14, 59))
    assert not in_power_window(at(TUESDAY, 16, 15))
    assert not in_power_window(at(SATURDAY, 10, 0))  # the XNYS calendar: closed
    assert not in_power_window(at((2026, 11, 26), 10, 0))  # Thanksgiving: closed
    assert in_power_window(at((2026, 11, 27), 10, 0))  # the half day after it: open
    # The day is New York's: 21:45 Israel on Tuesday is 15:45 ET (inside).
    israel = datetime(2026, 10, 13, 22, 45, tzinfo=ZoneInfo("Asia/Jerusalem"))
    assert in_power_window(israel)


def test_a_broken_calendar_counts_every_weekday_as_a_session(monkeypatch):
    from trading.scheduler import sessions

    def broken(_day):
        raise RuntimeError("exchange-calendars 4.11.2 is required")

    monkeypatch.setattr(sessions, "market_session", broken)

    assert heartbeat.is_session_day(datetime(2026, 11, 26).date())  # a Thursday, even a holiday
    assert not heartbeat.is_session_day(datetime(*SATURDAY).date())


def test_battery_in_the_session_window_pages_via_the_fail_endpoint_every_15_minutes(monitor):
    start = et(*TUESDAY, 7, 38)
    fake = FakeTime(start)
    supply = {"pmset": PMSET_BATTERY}
    fake.at(et(*TUESDAY, 8, 2, 30), lambda: supply.update(pmset=PMSET_AC))
    lines: list[str] = []
    client = HeartbeatClient(
        PingTarget.parse(secret_url(monitor)),
        state_of=lambda _now: "session",
        emit=lines.append,
        wait=fake.wait,
        clock=fake.clock,
        sender=partial(send_ping, opener=DIRECT),
        power=lambda: power_source(lambda: supply["pmset"]),
    )
    fake.stop_when = lambda: client.ticks >= 28  # 07:38 .. 08:05

    assert client.run() == "signal"

    assert monitor.states == [
        "session+battery",  # 07:38, before the window: tagged, not a page
        "session+battery",  # 07:39
        "fail:battery",  # 07:40: the page, instead of the success ping
        "fail:battery",  # 07:55: again after 15 minutes, nothing in between
        "session",  # 08:03, back on AC: the check comes back up
        "session",
        "session",
    ]
    assert [urllib.parse.urlsplit(path).path for path in monitor.requests if "fail" in path] == [
        f"/ping/{TOKEN}/fail"
    ] * 2
    warnings = [line for line in lines if " WARNING " in line]
    assert warnings == [
        f"{iso(et(*TUESDAY, 7, 40))} WARNING heartbeat: on battery during the session — paging via the monitor",
        f"{iso(et(*TUESDAY, 7, 55))} WARNING heartbeat: on battery during the session — paging via the monitor",
    ]
    assert f"{iso(et(*TUESDAY, 8, 3))} HEARTBEAT_POWER battery paging ended power=ac" in lines
    assert client.battery_pages == 2 and client.ticks == 28 and client.pings == 7


def test_battery_outside_the_window_or_on_a_closed_day_only_tags_the_ping(monitor):
    lines: list[str] = []
    for start in (et(*SATURDAY, 10, 0), et(*TUESDAY, 16, 15), et(*TUESDAY, 6, 0)):
        client_for(
            secret_url(monitor), FakeTime(start), lines=lines, pings=3, power=lambda: "battery"
        ).run()

    assert monitor.states == ["idle+battery"] * 9
    assert not any("WARNING" in line or "fail" in line for line in lines)


def test_a_flapping_power_supply_pages_at_most_once_per_15_minutes(monitor):
    fake = FakeTime(et(*TUESDAY, 10, 0))
    supply = iter(["battery", "ac"] * 20)
    lines: list[str] = []

    client = client_for(secret_url(monitor), fake, lines=lines, pings=40, power=lambda: next(supply))
    client.run()

    pages = [state for state in monitor.states if state.startswith("fail")]
    assert len(pages) == 3  # 10:00, 10:16, 10:32 (each the first battery slot 15+ minutes on)
    assert len([line for line in lines if " WARNING " in line]) == 3
    assert len([line for line in lines if "HEARTBEAT_POWER" in line]) == 3


def test_an_unreadable_power_source_never_stops_a_ping(monitor):
    fake = FakeTime(et(*TUESDAY, 10, 0))

    def broken() -> str | None:
        raise OSError("pmset missing")

    client_for(secret_url(monitor), fake, lines=[], pings=2, power=broken).run()

    assert monitor.states == ["idle", "idle"]


def test_main_wires_pmset_into_the_ping(tmp_path, monitor):
    out = io.StringIO()

    # Not a session day: battery only tags the ping, whatever the clock says.
    code = heartbeat.main(
        ["--once"],
        source=lambda: secret_url(monitor),
        user_paths=UserPaths(home=tmp_path),
        out=out,
        pmset=lambda: PMSET_BATTERY,
        session_day=lambda _day: False,
    )

    assert code == 0
    assert monitor.states == ["idle+battery"]

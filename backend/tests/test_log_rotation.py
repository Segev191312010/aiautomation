"""The daily log rotation: 18:35 ET, 14 days, rename vs copy-then-truncate, the
supervisor's own reopened log, and the DST change on 2026-11-01.

Everything runs in a temporary log directory on a fake clock; no real log is
read and no child is launched (the fake children hold their log descriptors
open with O_APPEND, like the real ones and like launchd).
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, time, timedelta, timezone
import itertools
import os
from pathlib import Path
import signal
import stat
from zoneinfo import ZoneInfo

import pytest

from trading.operations import supervisor as supervisor_module
from trading.operations.heartbeat import SupervisorLogState
from trading.operations.logging import (
    ROTATION_KEEP_DAYS,
    ROTATION_TIME,
    archive_path,
    latest_rotation_boundary,
    next_rotation_boundary,
    prune_archives,
    rotate_by_copy_truncate,
    rotate_by_rename,
    rotation_boundary,
)
from trading.operations.paths import PathSecurityError
from trading.operations.supervisor import (
    AlwaysOnPlan,
    ChildPlan,
    LogRotation,
    Supervisor,
    SupervisorLog,
    open_private_append,
)
from trading.operations.telegram_tools import last_child_events, supervisor_state
from trading.scheduler.sessions import market_session

ET = ZoneInfo("America/New_York")
UTC = timezone.utc
TUESDAY = date(2026, 10, 13)


def et(day: date, hour: int, minute: int, second: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute, second), tzinfo=ET).astimezone(UTC)


def mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def held_open(path: Path) -> int:
    """A writer that holds the file the way launchd and the children do."""
    return open_private_append(path)


# --- boundaries --------------------------------------------------------------------------


def test_the_rotation_is_daily_at_18_35_new_york_time():
    assert ROTATION_TIME == time(18, 35) and ROTATION_KEEP_DAYS == 14
    assert latest_rotation_boundary(et(TUESDAY, 18, 35)) == rotation_boundary(TUESDAY)
    assert latest_rotation_boundary(et(TUESDAY, 18, 34, 59)) == rotation_boundary(date(2026, 10, 12))
    assert next_rotation_boundary(et(TUESDAY, 18, 35)) == rotation_boundary(date(2026, 10, 14))
    assert next_rotation_boundary(et(TUESDAY, 9, 0)) == rotation_boundary(TUESDAY)
    assert rotation_boundary(TUESDAY).astimezone(UTC) == datetime(2026, 10, 13, 22, 35, tzinfo=UTC)
    with pytest.raises(ValueError):
        latest_rotation_boundary(datetime(2026, 10, 13, 18, 35))


def test_the_boundary_follows_new_york_across_the_2026_11_01_dst_change():
    saturday = rotation_boundary(date(2026, 10, 31))
    sunday = rotation_boundary(date(2026, 11, 1))

    assert saturday.astimezone(UTC) == datetime(2026, 10, 31, 22, 35, tzinfo=UTC)  # EDT
    assert sunday.astimezone(UTC) == datetime(2026, 11, 1, 23, 35, tzinfo=UTC)  # EST
    # Elapsed time in UTC (same-zone aware arithmetic is wall-clock in Python).
    assert sunday.astimezone(UTC) - saturday.astimezone(UTC) == timedelta(hours=25)
    assert next_rotation_boundary(saturday) == sunday
    # 24 hours after Saturday's rotation it is 17:35 EST: not yet Sunday's.
    assert latest_rotation_boundary(saturday.astimezone(UTC) + timedelta(hours=24)) == saturday
    # The night the clocks go back (01:00-02:00 happens twice) belongs to Saturday.
    assert latest_rotation_boundary(datetime(2026, 11, 1, 5, 30, tzinfo=UTC)) == saturday
    assert latest_rotation_boundary(datetime(2026, 11, 1, 6, 30, tzinfo=UTC)) == saturday


# --- the two ways --------------------------------------------------------------------------


def test_a_file_nobody_holds_is_renamed_and_a_new_empty_private_file_created(tmp_path):
    path = tmp_path / "bridge.log"
    path.write_text("the day's lines\n")
    path.chmod(0o600)
    before = path.stat().st_ino

    assert rotate_by_rename(path, TUESDAY) is True

    archive = tmp_path / "bridge.log.2026-10-13"
    assert archive.read_text() == "the day's lines\n"
    assert archive.stat().st_ino == before
    assert path.read_text() == "" and path.stat().st_ino != before
    assert mode(path) == 0o600 and mode(archive) == 0o600


def test_a_file_held_open_is_copied_then_truncated_in_place(tmp_path):
    path = tmp_path / "launchd.stdout.log"
    descriptor = held_open(path)
    try:
        os.write(descriptor, b"before rotation\n")
        before = path.stat().st_ino

        assert rotate_by_copy_truncate(path, TUESDAY) is True
        os.write(descriptor, b"after rotation\n")  # the holder writes on, unaware
    finally:
        os.close(descriptor)

    # Same inode, and the holder's next write landed at the new start (O_APPEND).
    assert path.stat().st_ino == before
    assert path.read_bytes() == b"after rotation\n"
    archive = tmp_path / "launchd.stdout.log.2026-10-13"
    assert archive.read_text() == "before rotation\n"
    assert mode(archive) == 0o600


def test_empty_and_missing_files_are_left_alone(tmp_path):
    empty = tmp_path / "manager.log"
    empty.write_text("")

    assert rotate_by_rename(tmp_path / "missing.log", TUESDAY) is False
    assert rotate_by_rename(empty, TUESDAY) is False
    assert rotate_by_copy_truncate(empty, TUESDAY) is False
    assert sorted(path.name for path in tmp_path.iterdir()) == ["manager.log"]
    # The supervisor's own log always rotates: its archive marks the day done.
    assert rotate_by_rename(empty, TUESDAY, keep_empty=True) is True
    assert (tmp_path / "manager.log.2026-10-13").exists()


def test_symbolic_links_are_refused(tmp_path):
    target = tmp_path / "elsewhere"
    target.write_text("not a log\n")
    link = tmp_path / "screener.log"
    link.symlink_to(target)

    for rotate in (rotate_by_rename, rotate_by_copy_truncate):
        with pytest.raises(PathSecurityError):
            rotate(link, TUESDAY)
    assert target.read_text() == "not a log\n"


def test_a_second_rotation_for_the_same_day_appends_to_its_archive(tmp_path):
    path = tmp_path / "replay.log"
    path.write_text("first\n")
    rotate_by_rename(path, TUESDAY)
    path.write_text("second\n")

    assert rotate_by_rename(path, TUESDAY) is True

    assert archive_path(path, TUESDAY).read_text() == "first\nsecond\n"
    assert path.read_text() == ""


# --- retention -----------------------------------------------------------------------------


def test_fourteen_days_of_archives_are_kept(tmp_path):
    day = date(2026, 10, 20)
    path = tmp_path / "supervisor.log"
    for back in range(0, 21):
        archive_path(path, day - timedelta(days=back)).write_text(f"{back}\n")
    untouched = [
        "deploys.log",
        "shadow.log",
        "shadow.log.1",
        "supervisor.log.bak",
        "supervisor.log.2026-13-01",
        "bridge.log.2026-09-01",
    ]
    for name in untouched:
        (tmp_path / name).write_text("keep\n")

    removed = prune_archives(path, day)

    kept = sorted(
        p.name for p in tmp_path.glob("supervisor.log.????-??-??") if p.name != "supervisor.log.2026-13-01"
    )
    assert kept == [f"supervisor.log.{(day - timedelta(days=back)).isoformat()}" for back in range(13, -1, -1)]
    assert len(kept) == 14
    assert archive_path(path, day - timedelta(days=13)).exists()  # the 14th day stays
    assert not archive_path(path, day - timedelta(days=14)).exists()  # the 15th goes
    assert len(removed) == 7
    for name in untouched:
        assert (tmp_path / name).read_text() == "keep\n"
    with pytest.raises(ValueError):
        prune_archives(path, day, keep_days=0)


# --- the supervisor's rotation ---------------------------------------------------------------


def test_one_rotation_renames_idle_logs_and_truncates_held_ones(tmp_path):
    log = SupervisorLog(tmp_path / "supervisor.log")
    log(f"{et(TUESDAY, 7, 0).isoformat()} SUPERVISOR_START pid=500 mode=daily")
    for name in ("bridge", "replay", "momo"):
        (tmp_path / f"{name}.log").write_text(f"{name} lines\n")
    telegram = held_open(tmp_path / "telegram.log")
    stdout = held_open(tmp_path / "launchd.stdout.log")
    stderr = held_open(tmp_path / "launchd.stderr.log")
    (tmp_path / "deploys.log").write_text("ledger line\n")
    (tmp_path / "shadow.log").write_text("size-rotated\n")
    archive_path(tmp_path / "bridge.log", TUESDAY - timedelta(days=14)).write_text("old\n")
    inodes = {name: (tmp_path / name).stat().st_ino for name in ("telegram.log", "launchd.stdout.log", "bridge.log")}
    try:
        for descriptor, text in ((telegram, b"telegram before\n"), (stdout, b"stdout before\n"), (stderr, b"")):
            os.write(descriptor, text)

        lines = LogRotation(tmp_path, log).rotate(et(TUESDAY, 18, 35), held_open={"telegram"})

        os.write(telegram, b"telegram after\n")
        os.write(stdout, b"stdout after\n")
    finally:
        for descriptor in (telegram, stdout, stderr):
            os.close(descriptor)
        log.close()

    assert lines == ["LOG_ROTATED date=2026-10-13 renamed=4 truncated=2 pruned=1 carried=1"]
    for name in ("bridge", "replay", "momo"):
        assert (tmp_path / f"{name}.log.2026-10-13").read_text() == f"{name} lines\n"
        assert (tmp_path / f"{name}.log").read_text() == ""
    assert (tmp_path / "bridge.log").stat().st_ino != inodes["bridge.log"]
    # Held open: same inode, the holder's later write at the new start.
    for name, before, after in (
        ("telegram.log", "telegram before\n", "telegram after\n"),
        ("launchd.stdout.log", "stdout before\n", "stdout after\n"),
    ):
        assert (tmp_path / name).stat().st_ino == inodes[name]
        assert (tmp_path / f"{name}.2026-10-13").read_text() == before
        assert (tmp_path / name).read_text() == after
    assert not (tmp_path / "launchd.stderr.log.2026-10-13").exists()  # empty: nothing to keep
    assert not archive_path(tmp_path / "bridge.log", TUESDAY - timedelta(days=14)).exists()
    assert (tmp_path / "deploys.log").read_text() == "ledger line\n"
    assert (tmp_path / "shadow.log").read_text() == "size-rotated\n"
    assert not list(tmp_path.glob("deploys.log.*")) and not list(tmp_path.glob("shadow.log.*"))


def test_a_rotation_failure_is_one_line_and_the_rest_still_rotates(tmp_path):
    log = SupervisorLog(tmp_path / "supervisor.log")
    (tmp_path / "elsewhere").write_text("x\n")
    (tmp_path / "screener.log").symlink_to(tmp_path / "elsewhere")
    (tmp_path / "bridge.log").write_text("bridge\n")
    try:
        lines = LogRotation(tmp_path, log).rotate(et(TUESDAY, 18, 35))
    finally:
        log.close()

    assert lines == [
        "LOG_ROTATED date=2026-10-13 renamed=2 truncated=0 pruned=0 carried=0",
        "LOG_ROTATION_FAILED file=screener.log error=PathSecurityError",
    ]
    assert (tmp_path / "bridge.log.2026-10-13").read_text() == "bridge\n"


def test_the_new_supervisor_log_carries_the_running_state(tmp_path):
    path = tmp_path / "supervisor.log"
    log = SupervisorLog(path)
    old = [
        "2026-10-12T22:00:00+00:00 SUPERVISOR_START pid=4321 mode=daily",
        "2026-10-12T22:00:00+00:00 CHILD_START telegram pid=4400",
        "2026-10-13T11:55:00+00:00 SESSION_START date=2026-10-13",
        "2026-10-13T11:55:00+00:00 CHILD_START screener pid=4500",
        "2026-10-13T20:00:00+00:00 CHILD_EXIT screener code=0 restart_in=none",
        "2026-10-13T21:35:00+00:00 CHILD_START replay pid=4600",
        "2026-10-13T22:10:00+00:00 CHILD_EXIT replay code=0 restart_in=none",
        "2026-10-13T22:10:00+00:00 SESSION_END date=2026-10-13",
        "2026-10-13T22:10:00+00:00 SESSION_WAIT date=2026-10-14 until=2026-10-14T11:55:00+00:00",
    ]
    for line in old:
        log(line)

    carried = log.rotate(TUESDAY)
    log("2026-10-13T22:35:00+00:00 LOG_ROTATED date=2026-10-13 renamed=1 truncated=0 pruned=0 carried=5")
    log("2026-10-13T23:00:00+00:00 CHILD_EXIT telegram code=1 restart_in=15")
    log.close()

    assert (tmp_path / "supervisor.log.2026-10-13").read_text().splitlines() == old
    new = path.read_text().splitlines()
    assert carried == 5
    assert new[:5] == [old[0], old[1], old[4], old[6], old[8]]  # verbatim, original order and times
    assert new[5].endswith("LOG_ROTATED date=2026-10-13 renamed=1 truncated=0 pruned=0 carried=5")
    assert new[6].endswith("CHILD_EXIT telegram code=1 restart_in=15")  # writing goes on, here
    assert mode(path) == 0o600
    # Its readers, from the new file alone: /status's supervisor and research
    # lines, and the heartbeat's state.
    state = supervisor_state(new, pid_alive=lambda pid: pid == 4321)
    assert state.seen and state.alive and state.pid == 4321
    assert state.started_at == datetime(2026, 10, 12, 22, 0, tzinfo=UTC)
    assert state.session == "wait date=2026-10-14 until=2026-10-14T11:55:00+00:00"
    assert {child.name for child in state.children} == {"telegram", "screener", "replay"}
    assert last_child_events(new, ("replay",))["replay"][0] == "exit"
    heartbeat_state = SupervisorLogState(path)
    assert heartbeat_state.state_at(datetime(2026, 10, 13, 23, 0, tzinfo=UTC)) == "idle"


# --- the supervisor on a fake clock ----------------------------------------------------------


class _Child:
    """Runs until signalled, holding its ``<name>.log`` open like a real child."""

    def __init__(self, pid: int, descriptor: int) -> None:
        self.pid = pid
        self.descriptor = descriptor
        self.returncode: int | None = None
        self._exited = asyncio.Event()

    async def wait(self) -> int:
        await self._exited.wait()
        assert self.returncode is not None
        return self.returncode

    def send_signal(self, signum: int) -> None:
        if self.returncode is None:
            self.returncode = 0 if signum == signal.SIGTERM else -signum
            os.close(self.descriptor)
            self._exited.set()


class _World:
    """A fake clock and launcher over a temporary log directory."""

    def __init__(self, directory: Path, start: datetime, stop: datetime) -> None:
        self.directory = directory
        self.now = start
        self.stop = stop
        self.supervisor: Supervisor | None = None
        self.children: dict[str, _Child] = {}
        self.actions: list[tuple[datetime, object]] = []
        self._pids = itertools.count(100)

    def clock(self) -> datetime:
        return self.now

    async def start(self, plan) -> _Child:  # noqa: ANN001
        descriptor = open_private_append(self.directory / f"{plan.name}.log")
        os.write(descriptor, f"=== {self.now.isoformat()} start {plan.name}\n".encode())
        child = _Child(next(self._pids), descriptor)
        self.children[plan.name] = child
        return child

    async def sleep(self, seconds: float) -> None:
        target = self.now + timedelta(seconds=seconds)
        for moment, action in sorted(self.actions, key=lambda item: item[0]):
            if self.now < moment <= min(target, self.stop):
                action()  # type: ignore[operator]
        if target >= self.stop:
            self.now = self.stop
            assert self.supervisor is not None
            self.supervisor.request_stop(signal.SIGTERM)
        else:
            self.now = target
        await asyncio.sleep(0)

    async def run(self, log: SupervisorLog) -> None:
        def screener_only(session):  # noqa: ANN001
            day = session.session_date
            return (
                ChildPlan(
                    name="screener",
                    argv=("python", "screener"),
                    starts_at=et(day, 7, 55),
                    ends_at=et(day, 16, 0),
                    finishes_at=et(day, 16, 0),
                    base_backoff=timedelta(seconds=15),
                ),
            )

        self.supervisor = Supervisor(
            launcher=self,
            emit=log,
            planner=screener_only,
            always=(AlwaysOnPlan(name="telegram", argv=("python", "telegram")),),
            clock=self.clock,
            sleep=self.sleep,
            sleep_chunk_seconds=3600.0,
            rotation=LogRotation(self.directory, log),
        )
        await self.supervisor.run()


def rotated_lines(path: Path) -> list[str]:
    return [line for line in path.read_text().splitlines() if " LOG_ROTAT" in line]


async def test_the_supervisor_rotates_at_18_35_and_keeps_writing_to_the_new_file(tmp_path):
    # Yesterday's rotation happened; one archive is 15 days old, one 14.
    for day in (date(2026, 10, 12), date(2026, 9, 29), date(2026, 9, 30)):
        archive_path(tmp_path / "supervisor.log", day).write_text("archived\n")
    world = _World(tmp_path, et(TUESDAY, 18, 0), stop=et(TUESDAY, 19, 0))
    launchd = held_open(tmp_path / "launchd.stdout.log")
    os.write(launchd, b"launchd before\n")
    world.actions.append(
        (et(TUESDAY, 18, 40), lambda: os.write(world.children["telegram"].descriptor, b"telegram after\n"))
    )
    world.actions.append((et(TUESDAY, 18, 41), lambda: os.write(launchd, b"launchd after\n")))
    log = SupervisorLog(tmp_path / "supervisor.log")
    try:
        await world.run(log)
    finally:
        log.close()
        os.close(launchd)

    archive = (tmp_path / "supervisor.log.2026-10-13").read_text()
    for expected in ("SUPERVISOR_START", "CHILD_START telegram", "SESSION_WAIT date=2026-10-14"):
        assert expected in archive
    assert "LOG_ROTATED" not in archive
    current = (tmp_path / "supervisor.log").read_text().splitlines()
    assert rotated_lines(tmp_path / "supervisor.log") == [
        "2026-10-13T22:35:00+00:00 LOG_ROTATED date=2026-10-13 renamed=1 truncated=2 pruned=1 carried=3"
    ]
    # The carried state first, then the rotation, then the supervisor writing on.
    assert [line.split(" ")[1] for line in current] == [
        "SUPERVISOR_START",
        "CHILD_START",
        "SESSION_WAIT",
        "LOG_ROTATED",
        "SIGNAL",
        "CHILD_EXIT",
        "SUPERVISOR_STOP",
    ]
    assert current[-1] == "2026-10-13T23:00:00+00:00 SUPERVISOR_STOP reason=SIGTERM"
    # The running assistant's log: copied, truncated, written on in place.
    assert (tmp_path / "telegram.log.2026-10-13").read_text().startswith("=== ")
    assert (tmp_path / "telegram.log").read_text() == "telegram after\n"
    assert (tmp_path / "launchd.stdout.log.2026-10-13").read_text() == "launchd before\n"
    assert (tmp_path / "launchd.stdout.log").read_text() == "launchd after\n"
    assert not (tmp_path / "supervisor.log.2026-09-29").exists()
    assert (tmp_path / "supervisor.log.2026-09-30").exists()


async def test_the_rotations_follow_new_york_across_the_dst_change(tmp_path):
    saturday = date(2026, 10, 31)
    archive_path(tmp_path / "supervisor.log", date(2026, 10, 30)).write_text("archived\n")
    world = _World(tmp_path, et(saturday, 18, 0), stop=et(date(2026, 11, 2), 7, 0))
    log = SupervisorLog(tmp_path / "supervisor.log")
    try:
        await world.run(log)
    finally:
        log.close()

    stamps = [
        line.split(" ")[0] + " " + line.split(" ")[2]
        for path in sorted(tmp_path.glob("supervisor.log*"))
        for line in rotated_lines(path)
    ]
    assert sorted(stamps) == [
        "2026-10-31T22:35:00+00:00 date=2026-10-31",  # 18:35 EDT
        "2026-11-01T23:35:00+00:00 date=2026-11-01",  # 18:35 EST, 25 hours later
    ]
    assert (tmp_path / "supervisor.log.2026-10-31").exists()
    assert (tmp_path / "supervisor.log.2026-11-01").exists()


async def test_a_missed_rotation_is_made_up_once_at_the_next_start(tmp_path):
    # The supervisor was down at Tuesday's 18:35 and starts at 20:00.
    first = _World(tmp_path, et(TUESDAY, 20, 0), stop=et(TUESDAY, 20, 30))
    log = SupervisorLog(tmp_path / "supervisor.log")
    try:
        await first.run(log)
    finally:
        log.close()
    second = _World(tmp_path, et(TUESDAY, 20, 30), stop=et(TUESDAY, 21, 0))
    log = SupervisorLog(tmp_path / "supervisor.log")
    try:
        await second.run(log)
    finally:
        log.close()

    every = [line for path in sorted(tmp_path.glob("supervisor.log*")) for line in rotated_lines(path)]
    assert every == ["2026-10-14T00:00:00+00:00 LOG_ROTATED date=2026-10-13 renamed=1 truncated=1 pruned=0 carried=3"]


async def test_a_rotation_due_during_a_session_waits_for_its_end(tmp_path):
    # Started mid-session with Monday's rotation missed: nothing rotates until
    # the session is over (the screener's window ends at 16:00), then twice.
    world = _World(tmp_path, et(TUESDAY, 9, 0), stop=et(TUESDAY, 19, 0))
    log = SupervisorLog(tmp_path / "supervisor.log")
    try:
        await world.run(log)
    finally:
        log.close()

    every = sorted(line for path in tmp_path.glob("supervisor.log*") for line in rotated_lines(path))
    assert [line.split(" ")[0] + " " + line.split(" ")[2] for line in every] == [
        "2026-10-13T20:00:00+00:00 date=2026-10-12",  # SESSION_END at 16:00 ET, then made up
        "2026-10-13T22:35:00+00:00 date=2026-10-13",
    ]
    monday = (tmp_path / "supervisor.log.2026-10-12").read_text()
    assert "SESSION_START date=2026-10-13" in monday and "SESSION_END date=2026-10-13" in monday


async def test_a_failing_rotation_is_retried_at_the_next_boundary_never_in_a_loop(tmp_path, monkeypatch):
    archive_path(tmp_path / "supervisor.log", date(2026, 10, 12)).write_text("archived\n")
    calls: list[datetime] = []

    def broken(self, now, *, held_open=()):  # noqa: ANN001
        calls.append(now)
        raise OSError("disk full")

    monkeypatch.setattr(LogRotation, "rotate", broken)
    world = _World(tmp_path, et(TUESDAY, 18, 0), stop=et(date(2026, 10, 14), 19, 0))
    log = SupervisorLog(tmp_path / "supervisor.log")
    try:
        await world.run(log)
    finally:
        log.close()

    assert calls == [et(TUESDAY, 18, 35), et(date(2026, 10, 14), 18, 35)]
    failures = [line for line in (tmp_path / "supervisor.log").read_text().splitlines() if "LOG_ROTATION_FAILED" in line]
    assert [line.split(" ", 1)[1] for line in failures] == ["LOG_ROTATION_FAILED file=all error=OSError"] * 2


def test_the_session_calendar_used_by_these_tests():
    assert market_session(TUESDAY) is not None
    assert market_session(date(2026, 10, 31)) is None and market_session(date(2026, 11, 1)) is None
    assert supervisor_module.SUPERVISOR_LOG_NAME == "supervisor.log"

"""The notifier's Mac power guard: a page when the Mac runs the session on battery.

Live 2026-10-23 the Mac ran on battery, hit 1 % and macOS forced a sleep
(22:07:37-22:51:19 Israel): the robot froze 43 minutes and its 15:45 ET
close-out ran 6 minutes late; nobody was told. No test here runs the real
``pmset``: every reading comes from a fake runner fed real-format output.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, time
import logging
from pathlib import Path
import re
import subprocess

import pytest

from tests.test_notify_service import (
    CHAT_ID,
    PMSET_PS_DESKTOP,
    SESSION,
    FakeTelegram,
    FakeTime,
    _environment,
    _et,
    _use_database,
)
from trading.operations import notify_service, telegram_tools
from trading.operations.notifier import save_chat_id, telegram_config_file
from trading.operations.notify_service import (
    MAC_ON_AC,
    MAC_ON_BATTERY,
    POWER_COMMAND,
    POWER_COMMAND_TIMEOUT_SECONDS,
    POWER_LOW_THRESHOLDS,
    POWER_POLL_SECONDS,
    POWER_UNREADABLE,
    NotifyEnvironment,
    NotifyState,
    PowerReading,
    _NotifierSession,
    back_on_ac_text,
    battery_low_text,
    battery_page_text,
    parse_power_status,
    run_notifier,
    state_file,
)
from trading.operations.paths import UserPaths

# `pmset -g ps` as macOS printed it on the owner's MacBook on AC power
# (2026-10-23 23:58 Israel; the battery's IOKit id replaced by a fake one), and
# the same lines on battery (the incident's 1 %, and a typical discharge).
PMSET_PS_AC = (
    "Now drawing from 'AC Power'\n"
    " -InternalBattery-0 (id=1234567)\t93%; charging; 0:26 remaining present: true\n"
)
PMSET_PS_CHARGED = (
    "Now drawing from 'AC Power'\n"
    " -InternalBattery-0 (id=1234567)\t100%; charged; 0:00 remaining present: true\n"
)
PMSET_PS_BATTERY_1 = (
    "Now drawing from 'Battery Power'\n"
    " -InternalBattery-0 (id=1234567)\t1%; discharging; (no estimate) present: true\n"
)


def on_battery(percent: int) -> str:
    return (
        "Now drawing from 'Battery Power'\n"
        f" -InternalBattery-0 (id=1234567)\t{percent}%; discharging; 4:12 remaining present: true\n"
    )


def on_ac(percent: int) -> str:
    return (
        "Now drawing from 'AC Power'\n"
        f" -InternalBattery-0 (id=1234567)\t{percent}%; charging; 1:05 remaining present: true\n"
    )


# The production shape of a laptop's `pmset -g ps` (the line captured above).
_LAPTOP_SHAPE = re.compile(
    r"Now drawing from '(?:AC|Battery) Power'\n"
    r" -InternalBattery-0 \(id=\d+\)\t\d{1,3}%; [a-z ]+; [^\n]+ present: true\n"
)


class FakePmset:
    """The power guard's one runner: records (argv, timeout) and the clock of each call."""

    def __init__(self, output: str | BaseException = PMSET_PS_AC, clock: FakeTime | None = None) -> None:
        self.output = output
        self.clock = clock
        self.calls: list[tuple[tuple[str, ...], float]] = []
        self.times: list[datetime] = []

    def __call__(self, argv, timeout_seconds):  # no default: a dropped timeout is a TypeError
        self.calls.append((tuple(argv), timeout_seconds))
        if self.clock is not None:
            self.times.append(self.clock.now)
        if isinstance(self.output, BaseException):
            raise self.output
        return self.output


class _FakeNotifier:
    def __init__(self) -> None:
        self.texts: list[str] = []

    def notify(self, text: str) -> asyncio.Future[bool]:
        self.texts.append(text)
        future: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        future.set_result(True)
        return future

    async def aclose(self, *, drain_timeout: float = 0) -> None:
        return None


@pytest.fixture
def records():
    """Every notifier log record, whatever another test did to propagation."""
    logger = logging.getLogger("autotrader.shadow.notify")
    captured: list[logging.LogRecord] = []

    class _Keep(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            captured.append(record)

    handler = _Keep(level=logging.DEBUG)
    saved = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    yield captured
    logger.removeHandler(handler)
    logger.setLevel(saved)


def _session(tmp_path: Path, pmset: FakePmset, *, clock: FakeTime | None = None, state: NotifyState | None = None):
    clock = clock or FakeTime(_et(10, 0))
    paths = UserPaths(home=tmp_path / "home")
    notifier = _FakeNotifier()
    session = _NotifierSession(
        session_date=SESSION,
        environment=_environment(paths, FakeTelegram(), clock, run_power_command=pmset),
        notifier=notifier,  # type: ignore[arg-type]
        state=state or NotifyState(path=tmp_path / "notify.json", session_date=SESSION),
    )
    return session, notifier


async def _polls(session: _NotifierSession, pmset: FakePmset, *outputs: str | BaseException) -> None:
    """One power check per output, as the cycle makes one per minute."""
    for output in outputs:
        pmset.output = output
        await session._check_power()


def _power_records(records: list[logging.LogRecord]) -> list[tuple[int, str]]:
    return [
        (record.levelno, record.getMessage())
        for record in records
        if record.getMessage().startswith((MAC_ON_BATTERY, MAC_ON_AC, POWER_UNREADABLE))
    ]


# --------------------------------------------------------------------------
# Parsing (real-format fixtures)
# --------------------------------------------------------------------------


def test_the_fixtures_have_the_real_pmset_shape():
    for fixture in (PMSET_PS_AC, PMSET_PS_CHARGED, PMSET_PS_BATTERY_1, on_battery(55), on_ac(40)):
        assert _LAPTOP_SHAPE.fullmatch(fixture), fixture
        assert "\t" in fixture and fixture.endswith("present: true\n")
    assert PMSET_PS_DESKTOP == "Now drawing from 'AC Power'\n"


def test_pmset_ps_is_read_as_the_source_and_the_internal_batterys_charge():
    assert parse_power_status(PMSET_PS_AC) == PowerReading(on_battery=False, percent=93)
    assert parse_power_status(PMSET_PS_CHARGED) == PowerReading(on_battery=False, percent=100)
    assert parse_power_status(PMSET_PS_BATTERY_1) == PowerReading(on_battery=True, percent=1)
    assert parse_power_status(on_battery(30)) == PowerReading(on_battery=True, percent=30)
    # A desktop Mac: AC Power and no battery line.
    assert parse_power_status(PMSET_PS_DESKTOP) == PowerReading(on_battery=False, percent=None)
    # On battery with a charge it cannot read: still on battery (it pages).
    assert parse_power_status("Now drawing from 'Battery Power'\n") == PowerReading(True, None)
    assert parse_power_status(on_battery(30).replace("30%", "300%")) == PowerReading(True, None)


@pytest.mark.parametrize(
    "output",
    [
        "",
        "pmset: unknown option\n",
        "Now drawing from 'UPS Power'\n -UPS (id=1)\t50%; discharging;\n",
        "Now drawing from 'Battery Power'\nNow drawing from 'AC Power'\n",
        "drawing from Battery Power 50%\n",
        "\x00\x01garbled",
    ],
)
def test_unparseable_pmset_output_is_no_reading(output):
    assert parse_power_status(output) is None


def test_the_guard_numbers_and_the_command_are_pinned():
    assert POWER_COMMAND == ("/usr/bin/pmset", "-g", "ps")
    assert POWER_COMMAND_TIMEOUT_SECONDS == 5.0
    assert POWER_POLL_SECONDS == 60.0
    assert POWER_LOW_THRESHOLDS == (30, 10)
    assert MAC_ON_BATTERY == "mac_on_battery"
    assert battery_page_text(42) == (
        "⚠️ Mac on battery (42 %) during the session — plug it in: at low battery macOS"
        " puts the Mac to sleep and the robot stops (as on 2026-10-23)"
    )
    assert back_on_ac_text(57) == "✅ Mac back on AC power (57 %)"


# --------------------------------------------------------------------------
# Episodes
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_the_switch_to_battery_pages_once_and_not_again_while_it_stays(tmp_path, records):
    pmset = FakePmset()
    session, notifier = _session(tmp_path, pmset)

    await _polls(session, pmset, PMSET_PS_AC, PMSET_PS_AC)
    assert notifier.texts == []
    await _polls(session, pmset, on_battery(80), on_battery(80), on_battery(79), on_battery(60), on_battery(31))

    assert notifier.texts == [battery_page_text(80)]
    assert "Mac on battery (80 %)" in notifier.texts[0] and "plug it in" in notifier.texts[0]
    assert _power_records(records) == [(logging.CRITICAL, f"{MAC_ON_BATTERY}: {battery_page_text(80)}")]
    assert any(record.getMessage() == f"alert queued: {MAC_ON_BATTERY}" for record in records)


@pytest.mark.anyio
async def test_low_battery_pages_at_30_and_at_10_once_each(tmp_path):
    pmset = FakePmset()
    session, notifier = _session(tmp_path, pmset)

    await _polls(session, pmset, on_battery(80), on_battery(50), on_battery(31))
    assert notifier.texts == [battery_page_text(80)]
    await _polls(session, pmset, on_battery(30))
    assert notifier.texts[1:] == [battery_low_text(30)]
    await _polls(session, pmset, on_battery(30), on_battery(29), on_battery(25), on_battery(11))
    assert len(notifier.texts) == 2
    await _polls(session, pmset, on_battery(10))
    assert notifier.texts[2:] == [battery_low_text(10)]
    await _polls(session, pmset, on_battery(10), on_battery(9), on_battery(5), PMSET_PS_BATTERY_1)

    assert notifier.texts == [battery_page_text(80), battery_low_text(30), battery_low_text(10)]
    assert "down to 30 %" in notifier.texts[1] and "plug it in now" in notifier.texts[1]


@pytest.mark.anyio
async def test_a_switch_at_a_low_charge_is_one_page_for_the_thresholds_it_has_reached(tmp_path):
    pmset = FakePmset()
    session, notifier = _session(tmp_path, pmset)
    await _polls(session, pmset, on_battery(25), on_battery(20), on_battery(11))
    assert notifier.texts == [battery_page_text(25)]
    await _polls(session, pmset, on_battery(10), on_battery(3))
    assert notifier.texts == [battery_page_text(25), battery_low_text(10)]

    pmset = FakePmset()
    session, notifier = _session(tmp_path / "second", pmset)
    await _polls(session, pmset, PMSET_PS_BATTERY_1, PMSET_PS_BATTERY_1)
    assert notifier.texts == [battery_page_text(1)]


@pytest.mark.anyio
async def test_a_drop_past_both_thresholds_between_two_reads_is_one_low_page(tmp_path):
    pmset = FakePmset()
    session, notifier = _session(tmp_path, pmset)
    await _polls(session, pmset, on_battery(80), on_battery(35), on_battery(8), on_battery(5))
    assert notifier.texts == [battery_page_text(80), battery_low_text(8)]


@pytest.mark.anyio
async def test_back_on_ac_is_one_notice_and_the_next_battery_episode_pages_again(tmp_path, records):
    pmset = FakePmset()
    session, notifier = _session(tmp_path, pmset)

    await _polls(session, pmset, on_battery(80), on_battery(30), on_ac(31), on_ac(32), PMSET_PS_CHARGED)
    assert notifier.texts == [battery_page_text(80), battery_low_text(30), back_on_ac_text(31)]
    assert (logging.INFO, f"{MAC_ON_AC}: {back_on_ac_text(31)}") in _power_records(records)
    assert not any(key.startswith(MAC_ON_BATTERY) for key in session._state.alerts)

    # A new episode: its switch pages, and both thresholds are armed again.
    await _polls(session, pmset, on_battery(60), on_battery(59), on_battery(30), on_battery(10), on_ac(11))
    assert notifier.texts[3:] == [
        battery_page_text(60),
        battery_low_text(30),
        battery_low_text(10),
        back_on_ac_text(11),
    ]


@pytest.mark.anyio
async def test_a_notifier_restart_keeps_the_episode(tmp_path):
    pmset = FakePmset()
    session, notifier = _session(tmp_path, pmset)
    await _polls(session, pmset, on_battery(80))
    session._checkpoint()
    assert notifier.texts == [battery_page_text(80)]

    state = NotifyState.load(tmp_path / "notify.json", SESSION)
    pmset = FakePmset()
    restarted, notifier = _session(tmp_path, pmset, state=state)
    await _polls(restarted, pmset, on_battery(79), on_battery(30), on_battery(29), on_ac(29))
    assert notifier.texts == [battery_low_text(30), back_on_ac_text(29)]


@pytest.mark.anyio
async def test_a_desktop_mac_never_pages(tmp_path, records):
    pmset = FakePmset(PMSET_PS_DESKTOP)
    session, notifier = _session(tmp_path, pmset)
    await _polls(session, pmset, *[PMSET_PS_DESKTOP] * 10)
    assert notifier.texts == []
    assert _power_records(records) == []
    assert session._state.alerts == {}


# --------------------------------------------------------------------------
# Failures: one WARNING line a day, never a page, never an exception
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_a_failed_or_timed_out_pmset_is_one_warning_a_day_and_never_escapes(tmp_path, records):
    pmset = FakePmset()
    session, notifier = _session(tmp_path, pmset)

    await _polls(
        session,
        pmset,
        subprocess.TimeoutExpired(list(POWER_COMMAND), POWER_COMMAND_TIMEOUT_SECONDS),
        OSError("pmset exited with 1"),
        FileNotFoundError("/usr/bin/pmset"),
        RuntimeError("anything"),
        subprocess.TimeoutExpired(list(POWER_COMMAND), POWER_COMMAND_TIMEOUT_SECONDS),
    )

    assert notifier.texts == []
    assert _power_records(records) == [
        (
            logging.WARNING,
            f"{POWER_UNREADABLE}: /usr/bin/pmset -g ps failed (TimeoutExpired);"
            " no battery page can come while it does (this line once a day)",
        )
    ]
    # Once a day, across a restart.
    session._checkpoint()
    pmset = FakePmset()
    restarted, notifier = _session(
        tmp_path, pmset, state=NotifyState.load(tmp_path / "notify.json", SESSION)
    )
    await _polls(restarted, pmset, OSError("pmset exited with 1"), "garbage")
    assert len(_power_records(records)) == 1
    # It is still watched: a later battery reading pages.
    await _polls(restarted, pmset, on_battery(70))
    assert notifier.texts == [battery_page_text(70)]


@pytest.mark.anyio
async def test_unparseable_output_never_pages(tmp_path, records):
    pmset = FakePmset()
    session, notifier = _session(tmp_path, pmset)
    await _polls(
        session,
        pmset,
        "",
        "pmset: unknown option\n",
        "Now drawing from 'UPS Power'\n -UPS (id=1)\t5%; discharging;\n",
        "\x00garbled",
    )
    assert notifier.texts == []
    warnings = _power_records(records)
    assert [level for level, _ in warnings] == [logging.WARNING]
    assert "printed no AC or battery power source" in warnings[0][1]


# --------------------------------------------------------------------------
# The runner: one function, the exact argv, a timeout, never a shell
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_the_runner_is_called_with_the_exact_argv_and_the_timeout(tmp_path):
    pmset = FakePmset(PMSET_PS_AC)
    session, _ = _session(tmp_path, pmset)
    await session._check_power()
    assert pmset.calls == [(("/usr/bin/pmset", "-g", "ps"), 5.0)]


def test_the_default_runner_runs_pmset_without_a_shell_and_with_the_timeout(tmp_path, monkeypatch):
    seen: list[tuple[tuple, dict]] = []

    def fake_run(*args, **kwargs):
        seen.append((args, kwargs))
        return subprocess.CompletedProcess(args[0], 0, stdout=PMSET_PS_AC, stderr="")

    monkeypatch.setattr(telegram_tools.subprocess, "run", fake_run)
    environment = NotifyEnvironment(paths=UserPaths(home=tmp_path / "home"))

    assert environment.run_power_command is telegram_tools.run_read_only_command
    assert environment.run_power_command(POWER_COMMAND, POWER_COMMAND_TIMEOUT_SECONDS) == PMSET_PS_AC
    (args, kwargs), = seen
    assert list(args[0]) == ["/usr/bin/pmset", "-g", "ps"]
    assert kwargs.get("shell", False) is False
    assert kwargs["timeout"] == 5.0
    assert kwargs["capture_output"] is True and kwargs["text"] is True

    def failed_run(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 1, stdout="", stderr="no")

    monkeypatch.setattr(telegram_tools.subprocess, "run", failed_run)
    with pytest.raises(OSError):
        environment.run_power_command(POWER_COMMAND, POWER_COMMAND_TIMEOUT_SECONDS)


# --------------------------------------------------------------------------
# Through run_notifier: only in the notifier's session window, every 60 s
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_the_guard_runs_only_in_the_notifiers_window_every_minute(tmp_path, monkeypatch, records):
    await _use_database(tmp_path, monkeypatch)
    monkeypatch.setattr(notify_service, "SESSION_END", time(8, 1))
    paths = UserPaths(home=tmp_path / "home")
    save_chat_id(telegram_config_file(paths), CHAT_ID)
    clock = FakeTime(_et(7, 52))
    pmset = FakePmset(on_battery(64), clock=clock)
    telegram = FakeTelegram()

    env = _environment(paths, telegram, clock, run_power_command=pmset)
    assert await run_notifier(session_date=SESSION, env=env) == 0

    assert pmset.times, "the guard never ran"
    assert all(_et(7, 55) <= moment < _et(8, 1) for moment in pmset.times), pmset.times
    assert pmset.times[0] < _et(7, 55, 5)
    gaps = [(later - earlier).total_seconds() for earlier, later in zip(pmset.times, pmset.times[1:])]
    assert gaps and all(POWER_POLL_SECONDS <= gap < POWER_POLL_SECONDS + 5 for gap in gaps), gaps
    assert len(pmset.times) == 6
    assert all(call == (POWER_COMMAND, POWER_COMMAND_TIMEOUT_SECONDS) for call in pmset.calls)
    battery_pages = [text for text in telegram.sent() if text.startswith("⚠️ Mac on battery")]
    assert battery_pages == [battery_page_text(64)]
    assert not any(record.getMessage() == "notifier cycle failed" for record in records)
    assert MAC_ON_BATTERY in NotifyState.load(state_file(paths, SESSION), SESSION).alerts

    # After the window nothing runs, not even a read.
    late = FakePmset(on_battery(50))
    after = _environment(paths, FakeTelegram(), FakeTime(_et(8, 1)), run_power_command=late)
    assert await run_notifier(session_date=SESSION, env=after) == 0
    assert late.calls == []


@pytest.mark.anyio
async def test_a_broken_pmset_never_costs_the_rest_of_the_cycle(tmp_path, monkeypatch, records):
    await _use_database(tmp_path, monkeypatch)
    monkeypatch.setattr(notify_service, "SESSION_END", time(10, 3))
    paths = UserPaths(home=tmp_path / "home")
    save_chat_id(telegram_config_file(paths), CHAT_ID)
    logs = paths.log_directory
    logs.mkdir(parents=True, exist_ok=True)
    bridge = logs / "bridge.log"
    bridge.write_text("")
    clock = FakeTime(_et(10, 0))
    clock.at(_et(10, 1), lambda: bridge.write_text("10:01:00 ET  FLAT: no open engine positions\n"))
    pmset = FakePmset(subprocess.TimeoutExpired(list(POWER_COMMAND), 5.0), clock=clock)
    telegram = FakeTelegram()

    env = _environment(paths, telegram, clock, run_power_command=pmset)
    assert await run_notifier(session_date=SESSION, env=env) == 0

    assert len(pmset.calls) >= 3
    assert "✅ FLAT: no open engine positions · 10:01:00 ET" in telegram.sent()
    assert not any(text.startswith("⚠️ Mac") for text in telegram.sent())
    assert not any(record.getMessage() == "notifier cycle failed" for record in records)
    assert [level for level, _ in _power_records(records)] == [logging.WARNING]

"""T-4: the Telegram child's watch outside the notifier's day.

The IB Gateway port (quiet hours, a morning digest, pages at the pre-session
leads, one recovery line, silence inside the notifier's window), the restart
cap's lines after hours (F-2: forwarded once, never an archive or a carried
line, never a line of the notifier's window), a competing login in the
recorder's log (page, re-page, end), and the screener's opt-in TWS status
lines. Every test drives ``OvernightWatch.run`` (the production loop) with a
fake clock, a fake port and real log files; nothing touches a socket."""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import date, datetime, time, timedelta, timezone
import os
from pathlib import Path

import pytest

from trading.operations import gateway_watch
from trading.operations.gateway_watch import (
    COMPETING_LOGIN_REPAGE,
    PORT_CHECK_INTERVAL,
    PRE_SESSION_LEADS,
    CompetingKind,
    CompetingLoginWatch,
    competing_login_window,
    in_notifier_window,
    is_quiet,
    pre_session_leads,
)
from trading.operations.hysteresis import CLOSE_AFTER, OPEN_AFTER, State
from trading.operations.overnight_watch import OvernightWatch, RestartCapGate
from trading.operations.supervisor import (
    NOTIFIER_WINDOW,
    RECORDER_WINDOW,
    REPLAY_WINDOW,
    SCREENER_WINDOW,
    LogRotation,
    SupervisorLog,
    iso_utc,
)
from trading.scheduler.sessions import MARKET_TIMEZONE

UTC = timezone.utc
MONDAY = date(2026, 10, 19)
SATURDAY = date(2026, 10, 17)
THANKSGIVING = date(2026, 11, 26)


def et(day: date, hour: int, minute: int = 0, second: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, second, tzinfo=MARKET_TIMEZONE).astimezone(UTC)


class Done(Exception):
    """The fake clock reached the test's end."""


class Time:
    """A wall clock that moves only when the watch sleeps; actions run at their moment."""

    def __init__(self, start: datetime, end: datetime) -> None:
        self.now = start
        self.end = end
        self._actions: list[tuple[datetime, Callable[[], object]]] = []

    def clock(self) -> datetime:
        return self.now

    def at(self, moment: datetime, action: Callable[[], object]) -> None:
        self._actions.append((moment, action))
        self._actions.sort(key=lambda item: item[0])

    async def sleep(self, seconds: float) -> None:
        target = self.now + timedelta(seconds=max(seconds, 0.0))
        while self._actions and self._actions[0][0] <= target:
            moment, action = self._actions.pop(0)
            self.now = max(self.now, moment)
            action()
        self.now = target
        if self.now >= self.end:
            raise Done
        await asyncio.sleep(0)


class Port:
    """The Gateway's API port: closed inside the given spans; every check recorded."""

    def __init__(self, time_: Time, closed: list[tuple[datetime, datetime]] | None = None) -> None:
        self.time = time_
        self.closed = list(closed or [])
        self.checks: list[datetime] = []

    async def __call__(self) -> bool:
        now = self.time.now
        self.checks.append(now)
        return not any(start <= now < end for start, end in self.closed)


def make_watch(tmp_path: Path, time_: Time, port: Port, **kwargs) -> tuple[OvernightWatch, dict]:
    logs = tmp_path / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    checkpoints: dict = kwargs.pop("checkpoints", {})
    watch = OvernightWatch(
        log_directory=logs,
        checkpoints=checkpoints,
        clock=time_.clock,
        sleep=time_.sleep,
        probe=port,
        **kwargs,
    )
    return watch, checkpoints


async def drive(watch: OvernightWatch, saves: list[int] | None = None) -> list[str]:
    sent: list[str] = []

    def save() -> None:
        if saves is not None:
            saves.append(1)

    with pytest.raises(Done):
        await watch.run(sent.append, save)
    return sent


def append(path: Path, *lines: str) -> Callable[[], None]:
    def action() -> None:
        with path.open("a", encoding="utf-8") as stream:
            stream.write("".join(f"{line}\n" for line in lines))

    return action


# --------------------------------------------------------------------------
# The schedule comes from the supervisor's windows and the session calendar
# --------------------------------------------------------------------------


def test_the_leads_and_windows_are_derived_from_the_supervisor_constants(monkeypatch):
    start = datetime.combine(MONDAY, SCREENER_WINDOW[0], tzinfo=MARKET_TIMEZONE)
    assert PRE_SESSION_LEADS == (timedelta(hours=2), timedelta(minutes=15))
    assert pre_session_leads(MONDAY) == tuple(sorted(start - lead for lead in PRE_SESSION_LEADS))
    assert [lead.strftime("%H:%M") for lead in pre_session_leads(MONDAY)] == ["05:55", "07:40"]
    assert pre_session_leads(SATURDAY) == () and pre_session_leads(THANKSGIVING) == ()
    # Quiet hours: REPLAY_WINDOW's end to the next session day's first lead.
    assert REPLAY_WINDOW[1] == time(18, 30)
    assert is_quiet(et(MONDAY, 5, 54, 59)) and not is_quiet(et(MONDAY, 5, 55))
    assert not is_quiet(et(MONDAY, 18, 29, 59)) and is_quiet(et(MONDAY, 18, 30))
    assert is_quiet(et(SATURDAY, 12, 0)) and is_quiet(et(THANKSGIVING, 6, 0))
    assert NOTIFIER_WINDOW == (time(7, 55), time(16, 15))
    assert in_notifier_window(et(MONDAY, 7, 55)) and not in_notifier_window(et(MONDAY, 16, 15))
    assert not in_notifier_window(et(SATURDAY, 10, 0))
    assert competing_login_window(MONDAY) == (
        datetime.combine(MONDAY, SCREENER_WINDOW[0], tzinfo=MARKET_TIMEZONE),
        datetime.combine(MONDAY, RECORDER_WINDOW[1], tzinfo=MARKET_TIMEZONE),
    )
    # No clock time is typed in the watch: moving SESSION_START moves the leads.
    monkeypatch.setattr(gateway_watch, "SCREENER_WINDOW", (time(8, 30), time(16, 0)))
    assert [lead.strftime("%H:%M") for lead in pre_session_leads(MONDAY)] == ["06:30", "08:15"]


# --------------------------------------------------------------------------
# The port
# --------------------------------------------------------------------------

CLOSED_FROM_0245 = [(et(MONDAY, 2, 45), et(MONDAY, 23, 0))]
DIGEST_0245 = "🌙 Overnight IB Gateway watch (quiet hours, not paged): port closed since 02:45 ET, still closed"
PAGE_0555 = (
    "⛔ IB Gateway closed since 02:45 ET (its API port does not answer) — log in to IB Gateway;"
    " session starts 07:55 ET (05:55 ET check)"
)
PAGE_0740 = PAGE_0555.replace("(05:55 ET check)", "(07:40 ET check)")


async def test_a_port_closed_at_0245_et_goes_to_the_digest_and_pages_nobody(tmp_path):
    clock = Time(et(MONDAY, 2, 30), et(MONDAY, 5, 54))
    port = Port(clock, CLOSED_FROM_0245)
    watch, _ = make_watch(tmp_path, clock, port)

    assert await drive(watch) == []
    # It did open (two failed checks five minutes apart), quietly.
    assert watch.port.hysteresis.is_open and watch.port.hysteresis.failing_since == et(MONDAY, 2, 45)
    assert port.checks[:4] == [et(MONDAY, 2, 30) + n * PORT_CHECK_INTERVAL for n in range(4)]
    assert all(b - a == PORT_CHECK_INTERVAL for a, b in zip(port.checks, port.checks[1:]))


async def test_still_closed_at_0555_et_the_digest_then_a_page_naming_the_deadline(tmp_path):
    clock = Time(et(MONDAY, 2, 30), et(MONDAY, 5, 56))
    watch, _ = make_watch(tmp_path, clock, Port(clock, CLOSED_FROM_0245))

    assert await drive(watch) == [DIGEST_0245, PAGE_0555]


async def test_still_closed_at_0740_et_it_pages_again(tmp_path):
    clock = Time(et(MONDAY, 2, 30), et(MONDAY, 7, 41))
    watch, _ = make_watch(tmp_path, clock, Port(clock, CLOSED_FROM_0245))

    assert await drive(watch) == [DIGEST_0245, PAGE_0555, PAGE_0740]


async def test_a_reopened_port_sends_one_recovery_line(tmp_path):
    clock = Time(et(MONDAY, 2, 30), et(MONDAY, 7, 30))
    port = Port(clock, [(et(MONDAY, 2, 45), et(MONDAY, 6, 10))])
    watch, _ = make_watch(tmp_path, clock, port)

    sent = await drive(watch)

    assert sent == [
        DIGEST_0245,
        PAGE_0555,
        "✅ IB Gateway port open again 06:10 ET (closed since 02:45 ET)",
    ]
    assert watch.port.hysteresis.state is State.CLEAR


async def test_an_outage_that_ends_in_the_quiet_hours_is_one_digest_entry(tmp_path):
    clock = Time(et(MONDAY, 2, 30), et(MONDAY, 5, 56))
    watch, _ = make_watch(tmp_path, clock, Port(clock, [(et(MONDAY, 2, 45), et(MONDAY, 3, 10))]))

    assert await drive(watch) == [
        "🌙 Overnight IB Gateway watch (quiet hours, not paged): port closed 02:45 ET–03:10 ET"
    ]


async def test_a_closure_spanning_the_lead_is_confirmed_before_it_pages(tmp_path):
    # A Gateway restart that happens to span 05:55: one failed check, reopened by
    # the confirmation one open_after later: nothing is paged.
    clock = Time(et(MONDAY, 5, 40), et(MONDAY, 6, 30))
    blip = Port(clock, [(et(MONDAY, 5, 54, 30), et(MONDAY, 5, 56, 30))])
    watch, _ = make_watch(tmp_path, clock, blip)
    assert await drive(watch) == []
    assert et(MONDAY, 5, 55) + OPEN_AFTER in blip.checks

    # A closure that lasts: paged at the confirmation, as the 05:55 check.
    clock = Time(et(MONDAY, 5, 40), et(MONDAY, 6, 0))
    watch, _ = make_watch(tmp_path / "lasting", clock, Port(clock, [(et(MONDAY, 5, 54, 30), et(MONDAY, 9, 0))]))
    assert await drive(watch) == [
        "⛔ IB Gateway closed since 05:55 ET (its API port does not answer) — log in to IB Gateway;"
        " session starts 07:55 ET (05:55 ET check)"
    ]


async def test_inside_the_notifier_window_the_watch_says_nothing_and_checks_nothing(tmp_path):
    clock = Time(et(MONDAY, 7, 50), et(MONDAY, 16, 21))
    port = Port(clock, [(et(MONDAY, 7, 45), et(MONDAY, 23, 0))])
    watch, _ = make_watch(tmp_path, clock, port)

    sent = await drive(watch)

    window_start, window_end = (et(MONDAY, 7, 55), et(MONDAY, 16, 15))
    assert not any(window_start <= moment < window_end for moment in port.checks)
    # BrokerWatch's day; the watch resumes at 16:15 with a fresh streak and pages
    # once it opens (the evening is not quiet hours: the recorder needs the Gateway).
    assert sent == [
        "⛔ IB Gateway closed since 16:15 ET (its API port does not answer) — log in to IB Gateway"
    ]


async def test_a_paged_outage_that_recovers_by_0755_gets_its_recovery_line_at_the_handover(tmp_path):
    # Started a minute after the 07:40 lead (a restart): the lead still runs, its
    # first failure is confirmed at 07:43, and the check at 07:53 finds the port
    # open again; the notifier's window starts before two clean minutes pass.
    clock = Time(et(MONDAY, 7, 41), et(MONDAY, 8, 30))
    port = Port(clock, [(et(MONDAY, 7, 40), et(MONDAY, 7, 50))])
    watch, _ = make_watch(tmp_path, clock, port)

    sent = await drive(watch)

    assert port.checks == [et(MONDAY, 7, 41), et(MONDAY, 7, 43), et(MONDAY, 7, 48), et(MONDAY, 7, 53)]
    assert sent == [
        "⛔ IB Gateway closed since 07:41 ET (its API port does not answer) — log in to IB Gateway;"
        " session starts 07:55 ET (07:40 ET check)",
        "✅ IB Gateway port open again 07:53 ET (closed since 07:41 ET)",
    ]


@pytest.mark.parametrize("day", [SATURDAY, THANKSGIVING])
async def test_a_weekend_or_holiday_gets_no_pre_session_page_and_no_check(tmp_path, day):
    checks: list[datetime] = []

    async def lead_check(now: datetime, lead: datetime) -> str | None:
        checks.append(lead)
        return "must not be sent"

    clock = Time(et(day, 4, 0), et(day, 10, 0))
    watch, _ = make_watch(tmp_path, clock, Port(clock, [(et(day, 4, 30), et(day, 23, 0))]), lead_check=lead_check)

    assert await drive(watch) == []
    assert checks == []
    assert watch.port.hysteresis.is_open  # held for the next session day's digest


# --------------------------------------------------------------------------
# F-2: the restart cap's lines after hours
# --------------------------------------------------------------------------


async def test_a_restart_cap_line_at_1800_et_is_forwarded_once_and_never_from_the_archive(tmp_path):
    """The real SupervisorLog and the real 18:35 rotation (T-3): the line of the
    notifier's window is never sent by the watch, the 18:00 suspension and the
    18:30 resumption once each, and after the rotation the archive is not
    read again (its lines are past the half hour, so a replay would page)."""
    logs = tmp_path / "logs"
    logs.mkdir()
    supervisor_log = SupervisorLog(logs / "supervisor.log")
    # Before the watch starts: never news (it follows the log from its end).
    supervisor_log(f"{iso_utc(et(MONDAY, 10, 0))} CHILD_SUSPENDED orb-v2 count=6 window=30m")
    clock = Time(et(MONDAY, 15, 50), et(MONDAY, 19, 20))
    port = Port(clock)

    def line(at: datetime, text: str) -> Callable[[], None]:
        return lambda: supervisor_log(f"{iso_utc(at)} {text}")

    clock.at(et(MONDAY, 15, 55), line(et(MONDAY, 15, 55), "CHILD_SUSPENDED momo count=6 window=30m"))
    clock.at(et(MONDAY, 18, 0), line(et(MONDAY, 18, 0), "CHILD_SUSPENDED replay count=6 window=30m"))
    clock.at(et(MONDAY, 18, 30), line(et(MONDAY, 18, 30), "CHILD_RESUMED replay reason=window_boundary"))

    def rotate() -> None:
        now = et(MONDAY, 18, 35)
        for marker in LogRotation(logs, supervisor_log).rotate(now, held_open={"telegram"}):
            supervisor_log(f"{iso_utc(now)} {marker}")

    clock.at(et(MONDAY, 18, 35), rotate)
    clock.at(et(MONDAY, 18, 50), line(et(MONDAY, 18, 50), "CHILD_RESTART_STORM telegram count=6 window=30m"))
    saves: list[int] = []
    watch, checkpoints = make_watch(tmp_path, clock, port)
    try:
        sent = await drive(watch, saves)
    finally:
        supervisor_log.close()

    assert (logs / "supervisor.log.2026-10-19").exists()
    assert sent == [
        "⛔ replay suspended after 6 restarts in 30 min (relaunch probe every 5 min) · 18:00 ET",
        "✅ replay done for today (window ended) · 18:30 ET",
        "⚠️ telegram restart storm: 6 in 30 min (positions protected at IB; backoff continues) · 18:50 ET",
    ]
    # The checkpoint follows the new file, and is saved as it moves.
    assert checkpoints["supervisor"]["ino"] == os.stat(logs / "supervisor.log").st_ino
    assert saves


async def test_the_rotations_carried_lines_are_never_forwarded(tmp_path):
    """log_tailer's one rule: lines dated before the LOG_ROTATED marker, written
    next to it, are copies of the archive's (here a cap line, to prove the rule
    and not only that T-3 happens to carry state lines alone)."""
    logs = tmp_path / "logs"
    logs.mkdir()
    path = logs / "supervisor.log"
    path.write_text(f"{iso_utc(et(MONDAY, 17, 0))} CHILD_START telegram pid=40\n")
    clock = Time(et(MONDAY, 18, 20), et(MONDAY, 18, 45))

    def rotate() -> None:
        path.rename(logs / "supervisor.log.2026-10-19")
        path.write_text(
            f"{iso_utc(et(MONDAY, 18, 0))} CHILD_SUSPENDED replay count=6 window=30m\n"
            f"{iso_utc(et(MONDAY, 18, 35))} LOG_ROTATED date=2026-10-19 renamed=1 truncated=0 pruned=0 carried=1\n"
            f"{iso_utc(et(MONDAY, 17, 59))} CHILD_RESUMED recorder reason=probe_healthy\n"
        )

    clock.at(et(MONDAY, 18, 35), rotate)
    watch, _ = make_watch(tmp_path, clock, Port(clock))

    sent = await drive(watch)

    # The suspension before the marker is carried (count 1); the line after it,
    # dated before it, is beyond the count: news, sent once.
    assert sent == ["✅ recorder resumed (probe_healthy) · 17:59 ET"]


async def test_a_restart_resumes_at_the_saved_checkpoint_and_resends_nothing(tmp_path):
    from trading.operations.telegram_bot import AssistantState

    logs = tmp_path / "logs"
    logs.mkdir()
    path = logs / "supervisor.log"
    path.write_text("")
    state = AssistantState.load(tmp_path / "telegram" / "state.json")
    clock = Time(et(MONDAY, 17, 0), et(MONDAY, 17, 10))
    clock.at(et(MONDAY, 17, 5), append(path, f"{iso_utc(et(MONDAY, 17, 5))} CHILD_SUSPENDED recorder count=6 window=30m"))
    watch, _ = make_watch(tmp_path, clock, Port(clock), checkpoints=state.tails)
    first = await drive(watch, [])
    state.save()
    assert first == ["⛔ recorder suspended after 6 restarts in 30 min (relaunch probe every 5 min) · 17:05 ET"]

    reloaded = AssistantState.load(tmp_path / "telegram" / "state.json")
    assert reloaded.tails["supervisor"]["offset"] == path.stat().st_size
    append(path, f"{iso_utc(et(MONDAY, 17, 12))} CHILD_RESUMED recorder reason=probe_healthy")()
    clock = Time(et(MONDAY, 17, 12), et(MONDAY, 17, 20))
    again, _ = make_watch(tmp_path, clock, Port(clock), checkpoints=reloaded.tails)
    assert await drive(again) == ["✅ recorder resumed (probe_healthy) · 17:12 ET"]


def test_the_after_hours_gate_is_the_notifiers_rule():
    gate = RestartCapGate()
    at = et(MONDAY, 18, 0)
    assert gate.allowed("child_suspended", "replay", at)
    assert not gate.allowed("child_suspended", "replay", at + timedelta(minutes=29))
    assert gate.allowed("child_resumed", "replay", at + timedelta(minutes=1))  # contradicts: always
    assert gate.allowed("child_restart_storm", "telegram", at)
    assert not gate.allowed("child_restart_storm", "telegram", at + timedelta(minutes=29))
    assert gate.allowed("child_restart_storm", "telegram", at + timedelta(minutes=30))


# --------------------------------------------------------------------------
# A competing login in the recorder's log (16:15-17:30 ET)
# --------------------------------------------------------------------------

RECORDER_LINE = (
    "{at} ERROR autotrader.shadow.recorder backfill stopped at AAPL: competing_session:"
    " TWS error 10197: No market data during competing live session"
)


async def test_a_competing_login_in_the_recorder_log_pages_repages_and_stops(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    recorder = logs / "recorder.log"
    recorder.write_text("")
    clock = Time(et(MONDAY, 16, 30), et(MONDAY, 17, 20))
    # The recorder logs it after every restart; here once a minute, 16:40-16:49.
    for minute in range(40, 50):
        moment = et(MONDAY, 16, minute)
        clock.at(moment, append(recorder, RECORDER_LINE.format(at=moment.astimezone(MARKET_TIMEZONE).isoformat())))
    watch, _ = make_watch(tmp_path, clock, Port(clock))

    sent = await drive(watch)

    assert sent == [
        "⚠️ IBKR login active elsewhere — log out of the IBKR app/website\n(seen in the recorder log)",
        "⚠️ IBKR login still active elsewhere since 16:40 ET — log out of the IBKR app/website\n"
        "(seen in the recorder log)",
    ]
    # No line for a whole re-page interval: over (closed two clean minutes later).
    assert watch.competing.hysteresis.state is State.CLEAR


async def test_a_competing_login_line_outside_the_recorder_window_is_not_the_watchs(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    recorder = logs / "recorder.log"
    recorder.write_text("")
    clock = Time(et(MONDAY, 17, 25), et(MONDAY, 18, 10))
    for moment in (et(MONDAY, 17, 31), et(MONDAY, 17, 45)):
        clock.at(moment, append(recorder, RECORDER_LINE.format(at=moment.isoformat())))
    watch, _ = make_watch(tmp_path, clock, Port(clock))

    assert await drive(watch) == []


def test_the_competing_login_watch_pages_at_once_repages_while_seen_and_ends_after_a_silence():
    watch = CompetingLoginWatch()
    start = et(MONDAY, 10, 0)
    opened = watch.seen(start, "screener")
    assert opened is not None and opened.kind is CompetingKind.OPENED and opened.since == start
    assert watch.seen(start, "screener") is None  # the same poll: not "still"
    assert watch.tick(start + COMPETING_LOGIN_REPAGE) is None  # nothing since the page
    assert watch.hysteresis.state is State.RECOVERING
    cleared = watch.tick(start + COMPETING_LOGIN_REPAGE + CLOSE_AFTER)
    assert cleared is not None and cleared.kind is CompetingKind.CLEARED

    again = CompetingLoginWatch()
    again.seen(start, "bridge")
    again.seen(start + timedelta(minutes=3), "screener")
    repage = again.tick(start + COMPETING_LOGIN_REPAGE)
    assert repage is not None and repage.kind is CompetingKind.REPAGE
    assert (repage.since, repage.source) == (start, "screener")


async def test_the_notifier_repages_a_competing_login_every_10_minutes_while_it_persists(tmp_path):
    from tests.test_notify_service import SESSION, FakeTelegram, FakeTime, _environment, _et
    from trading.operations.notify_service import NotifyState, _NotifierSession
    from trading.operations.paths import UserPaths

    class Notifier:
        def __init__(self) -> None:
            self.texts: list[str] = []

        def notify(self, text: str) -> asyncio.Future[bool]:
            self.texts.append(text)
            future: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
            future.set_result(True)
            return future

    clock = FakeTime(_et(10, 0))
    notifier = Notifier()
    session = _NotifierSession(
        session_date=SESSION,
        environment=_environment(UserPaths(home=tmp_path / "home"), FakeTelegram(), clock),
        notifier=notifier,  # type: ignore[arg-type]
        state=NotifyState(path=tmp_path / "notify.json", session_date=SESSION),
    )
    line = "ERROR IBKR login active elsewhere (phone/web) — log out there; retrying in 60 s (TWS error 10197: x)"
    for minute in range(0, 15):
        clock.now = _et(10, minute)
        if minute <= 12:
            session._handle_line("screener", line)
        session._follow_competing()
    for minute in range(15, 40):
        clock.now = _et(10, minute)
        session._follow_competing()

    assert notifier.texts == [
        "⚠️ IBKR login active elsewhere — log out of the IBKR app/website\n(seen in the screener log)",
        "⚠️ IBKR login still active elsewhere since 10:00 ET — log out of the IBKR app/website\n"
        "(seen in the screener log)",
        "⚠️ IBKR login still active elsewhere since 10:00 ET — log out of the IBKR app/website\n"
        "(seen in the screener log)",
    ]


# --------------------------------------------------------------------------
# The screener's opt-in TWS status lines (2103/2105/2157/2110)
# --------------------------------------------------------------------------

EVERY_WARNING = (2100, 2103, 2104, 2105, 2106, 2107, 2108, 2109, 2110, 2119, 2137, 2157, 2158, 2168)


async def test_the_actor_status_listener_hears_only_the_four_codes_and_changes_nothing_else():
    from tests.test_tws_actor import FakeTWSClient, config
    from trading.market.tws_actor import STATUS_LISTENER_CODES, TWSPaperActor

    assert STATUS_LISTENER_CODES == frozenset({2103, 2105, 2110, 2157})
    heard: list[tuple[int, str]] = []
    actor = TWSPaperActor(config(), client_factory=lambda callbacks: FakeTWSClient(callbacks))
    plain = TWSPaperActor(config(), client_factory=lambda callbacks: FakeTWSClient(callbacks))
    actor.set_status_listener(lambda code, message: heard.append((code, message)))
    for each in (actor, plain):
        await each.connect()
        for code in EVERY_WARNING:
            each._on_error(-1, code, f"farm message {code}")
        # A warning that names a request never fails it, listener or not.
        assert (await each.daily_trade_bars(101)).bars

    assert [code for code, _ in heard] == [2103, 2105, 2110, 2157]
    assert heard[2] == (2110, "farm message 2110")
    # Default behaviour for every other user: warnings stay out of last_error.
    assert actor.last_error is None and plain.last_error is None

    def broken(code: int, message: str) -> None:
        raise RuntimeError("listener bug")

    actor.set_status_listener(broken)
    actor._on_error(-1, 2110, "broken")  # ignored, as a failing competing listener is
    actor.set_status_listener(None)
    actor._on_error(-1, 2110, "nobody listens")
    assert len(heard) == 4


async def test_the_screener_logs_one_status_line_per_code_change_and_brokerwatch_reads_it(screener_logs, database):
    from tests.test_screener import FakeActor, FakeClock, at, runtime_config, seed_screener_universe
    from trading.market.tws_actor import TWSConnectionError
    from trading.operations import screener_service as service
    from trading.operations.notify_service import BrokerWatch, broker_evidence

    class StatusActor(FakeActor):
        def __init__(self) -> None:
            super().__init__(snapshot_failures=[TWSConnectionError("TWS connection closed")])
            self.listener = None

        def set_status_listener(self, listener) -> None:
            self.listener = listener

        async def connect(self) -> None:
            await super().connect()
            # What the real actor hands the listener on a broken Gateway (never 2104/2106/2158).
            for code in (2105, 2105, 2110):
                self.listener(code, f"HMDS data farm connection is broken:ushmds {code}")

    await seed_screener_universe()
    clock = FakeClock(at(10, 0))
    actor = StatusActor()
    await service.run_screener(
        runtime_config=runtime_config(), actor_factory=lambda config: actor,
        clock=clock, sleep=clock.sleep, monotonic=clock.monotonic, max_sweeps=1,
    )

    lines = [record.getMessage() for record in screener_logs if record.getMessage().startswith("TWS status")]
    # One line per change, and again after the reconnect (a new connection starts over).
    assert lines == [
        "TWS status 2105: HMDS data farm connection is broken:ushmds 2105",
        "TWS status 2110: HMDS data farm connection is broken:ushmds 2110",
        "TWS status 2105: HMDS data farm connection is broken:ushmds 2105",
        "TWS status 2110: HMDS data farm connection is broken:ushmds 2110",
    ]
    formatted = f"2026-10-13T10:00:01-0400 INFO autotrader.shadow.screener {lines[1]}"
    assert broker_evidence("screener", formatted) == "ibkr_status"
    assert broker_evidence("screener", formatted.replace("2110", "2104")) is None
    assert BrokerWatch is not None


def test_brokerwatch_counts_a_screener_2110_as_ibkr_side_never_re_asserted():
    from tests.test_notify_service import SESSION, _et
    from trading.operations.notify_service import IBKR_LINK_DOWN, BrokerWatch

    def status(code: int) -> str:
        return f"2026-10-13T08:00:01-0400 INFO autotrader.shadow.screener TWS status {code}: broken"

    # A connect's burst while the screener qualifies (no sweep for minutes) is one
    # failure signal and is never re-asserted: a slow healthy morning pages nothing.
    watch = BrokerWatch(SESSION)
    watch.session_started(_et(7, 55))
    for code in (2103, 2105, 2157):
        assert watch.observe("screener", status(code), _et(8, 0, 1)) is None
    for minute in range(1, 9):
        assert watch.check(_et(8, minute, 30)) is None
    assert not watch.unreachable
    watch.observe("screener", "sweep 2026-10-13: 35 ranked", _et(8, 9))
    assert watch.hysteresis.state is State.CLEAR

    # A 2110 that comes back after the reconnect: a second signal, two minutes on: open, IBKR-side.
    watch = BrokerWatch(SESSION)
    watch.session_started(_et(7, 55))
    watch.observe("screener", status(2110), _et(8, 0, 1))
    watch.observe("screener", status(2110), _et(8, 2, 30))
    assert watch.unreachable and watch.failure_kind == IBKR_LINK_DOWN
    assert watch.alert_text().startswith("⛔ IBKR connectivity lost since 08:00 ET")


from tests.test_screener import database, screener_logs  # noqa: E402,F401 - fixtures

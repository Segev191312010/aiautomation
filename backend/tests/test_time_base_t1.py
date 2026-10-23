"""The exchange calendar and the time base of the no-limit PAPER test L-T1 (owner plan v2, build item 6).

Every market-time rule is keyed to America/New_York and the XNYS calendar, never to the Mac's own clock:
the owner's Mac runs in Israel, at UTC+3 in summer and UTC+2 in winter, so ET+7 until 25 Oct 2026, ET+6
from 25 Oct to 1 Nov (Israel's clocks change first) and ET+7 again from 2 Nov, the first test session.
Each test sets the process's own time zone (``TZ``) to a fixed UTC+2 and UTC+3, to Israel and to New York,
and requires the same New York times from the bridge's session checkpoints, the supervisor's session and
child windows, the strategies' entry windows and the session checker. Fri 27 Nov 2026 closes at 13:00
(owner decision D6 changed: trade it normally): entries stop 12:30 ET (MOMO 12:00), the close-out runs
12:45 ET, every position is flat before 13:00 and the supervisor's children follow the half day.
"""
from __future__ import annotations

import ast
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime, timezone
import os
from pathlib import Path
import time as time_module

import pytest

from tests.test_session_check import pb
from trading.operations import session_check as sc
from trading.operations import supervisor
from trading.scheduler.sessions import MARKET_TIMEZONE, market_session

NY = MARKET_TIMEZONE
BACKEND = Path(__file__).resolve().parents[1]
# The Mac's clock: Israel in winter (UTC+2) and summer (UTC+3) as fixed offsets (POSIX "Etc/GMT-2" is
# UTC+2), Israel itself, and New York for reference.
ZONES = ("Etc/GMT-2", "Etc/GMT-3", "Asia/Jerusalem", "America/New_York")
SUMMER = date(2026, 10, 21)  # Israel UTC+3, New York UTC-4: ET+7
CHANGE_WEEK = date(2026, 10, 27)  # Israel UTC+2, New York UTC-4: ET+6 (25 Oct - 1 Nov 2026)
FIRST_TEST_SESSION = date(2026, 11, 2)  # Israel UTC+2, New York UTC-5: ET+7
HALF_DAY = date(2026, 11, 27)  # XNYS closes at 13:00


@contextmanager
def local_clock(zone: str) -> Iterator[None]:
    previous = os.environ.get("TZ")
    os.environ["TZ"] = zone
    time_module.tzset()
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = previous
        time_module.tzset()


def et(moment: datetime) -> str:
    return moment.astimezone(NY).strftime("%H:%M")


# --- the bridge -----------------------------------------------------------------------------------------

BRIDGE_CHECKPOINTS = ("last_entry", "flatten", "flatten_alert", "verify", "stop", "final_alert", "closes_at")
FULL_DAY_BRIDGE = ("15:30", "15:45", "15:46", "15:50", "15:55", "15:58", "16:00")
HALF_DAY_BRIDGE = ("12:30", "12:45", "12:46", "12:50", "12:55", "12:58", "13:00")


@pytest.mark.parametrize("zone", ZONES)
@pytest.mark.parametrize(("day", "expected"), [
    (SUMMER, FULL_DAY_BRIDGE), (CHANGE_WEEK, FULL_DAY_BRIDGE), (FIRST_TEST_SESSION, FULL_DAY_BRIDGE),
    (HALF_DAY, HALF_DAY_BRIDGE),
])
def test_the_bridges_session_checkpoints_are_new_york_times_on_any_local_clock(
    zone: str, day: date, expected: tuple[str, ...]
) -> None:
    with local_clock(zone):
        times = pb.session_times(day)
        assert tuple(et(getattr(times, name)) for name in BRIDGE_CHECKPOINTS) == expected
        if getattr(times, "opens_at", None) is not None:  # bridge v7c-t1: the outside-hours guard's open
            assert et(times.opens_at) == "09:30"
        # The same instants on the Mac's own clock: ET plus the zone's offset that day.
        local = times.flatten.astimezone()
        offset = local.utcoffset() - times.flatten.astimezone(NY).utcoffset()
        assert local.replace(tzinfo=None) - times.flatten.astimezone(NY).replace(tzinfo=None) == offset


@pytest.mark.parametrize(("day", "hours"), [(SUMMER, 7), (CHANGE_WEEK, 6), (FIRST_TEST_SESSION, 7)])
def test_israel_is_et_plus_6_in_the_change_week_and_et_plus_7_around_it(day: date, hours: int) -> None:
    with local_clock("Asia/Jerusalem"):
        flatten = pb.session_times(day).flatten
        assert flatten.astimezone().strftime("%H:%M") == f"{15 + hours}:45"


# --- the supervisor's session and child windows ----------------------------------------------------------


def plan_in_et(day: date) -> dict[str, tuple[str, str, str]]:
    session = market_session(day)
    assert session is not None
    return {plan.name: (et(plan.starts_at), et(plan.ends_at), et(plan.finishes_at))
            for plan in supervisor.plan_session(session)}


FULL_DAY_PLAN = {
    "screener": ("07:55", "16:00", "16:00"),
    "notifier": ("07:55", "16:15", "16:05"),
    "bridge": ("09:25", "16:00", "15:55"),
    "orb-v2": ("09:28", "15:31", "15:30"),
    "momo": ("09:55", "15:01", "15:00"),
    "gap-v2": ("09:28", "15:31", "15:30"),
    "vwap-v2": ("09:28", "15:31", "15:30"),
    "manager": ("09:15", "14:40", "14:30"),
    "momo-ap": ("09:55", "15:01", "15:00"),
    "recorder": ("16:20", "17:30", "16:20"),
    "replay": ("17:35", "18:30", "17:35"),
}
# Fri 27 Nov: the bridge and every child that can lead to an order follow the 13:00 close.
HALF_DAY_PLAN = {
    **FULL_DAY_PLAN,
    "bridge": ("09:25", "13:00", "12:55"),
    "orb-v2": ("09:28", "12:31", "12:30"),
    "momo": ("09:55", "12:01", "12:00"),
    "gap-v2": ("09:28", "12:31", "12:30"),
    "vwap-v2": ("09:28", "12:31", "12:30"),
    "manager": ("09:15", "11:40", "11:30"),
    "momo-ap": ("09:55", "12:01", "12:00"),
}
# Fixed New York clock times that do not follow the half day's close (report findings F-1 to F-3): none
# sends an order. The screener and the notifier run until 16:00 / 16:15 ET and the notifier's daily report
# is due 16:05 ET (supervisor.py SCREENER_WINDOW, NOTIFIER_WINDOW, NOTIFIER_FINISH); the recorder and the
# replay keep their evening windows (RECORDER_WINDOW, REPLAY_WINDOW).
NOT_CLOSE_RELATIVE = ("screener", "notifier", "recorder", "replay")


@pytest.mark.parametrize("zone", ZONES)
@pytest.mark.parametrize(("day", "expected"), [
    (SUMMER, FULL_DAY_PLAN), (CHANGE_WEEK, FULL_DAY_PLAN), (FIRST_TEST_SESSION, FULL_DAY_PLAN),
    (HALF_DAY, HALF_DAY_PLAN),
])
def test_the_supervisors_child_windows_are_new_york_times_on_any_local_clock(
    zone: str, day: date, expected: dict[str, tuple[str, str, str]]
) -> None:
    with local_clock(zone):
        assert plan_in_et(day) == expected


def test_on_the_half_day_only_the_windows_that_send_nothing_keep_their_clock_times() -> None:
    full, half = plan_in_et(FIRST_TEST_SESSION), plan_in_et(HALF_DAY)
    assert {name for name in full if full[name] != half[name]} == set(full) - set(NOT_CLOSE_RELATIVE)
    close = market_session(HALF_DAY).closes_at  # type: ignore[union-attr]
    for plan in supervisor.plan_session(market_session(HALF_DAY)):  # type: ignore[arg-type]
        if plan.name not in NOT_CLOSE_RELATIVE:
            assert plan.ends_at <= close, plan.name  # nothing that can lead to an order outlives the close


@pytest.mark.parametrize("zone", ZONES)
@pytest.mark.parametrize(("israel", "session_day"), [
    # 01:00 Israel on Tue 3 Nov is 18:00 ET on Mon 2 Nov: Monday's replay window (to 18:30 ET) still runs.
    (datetime(2026, 11, 3, 1, 0), FIRST_TEST_SESSION),
    # 02:00 Israel is 19:00 ET: Monday is over; Tuesday's session is next.
    (datetime(2026, 11, 3, 2, 0), date(2026, 11, 3)),
    # 23:00 Israel on Fri 27 Nov is 16:00 ET: the half day's evening windows still run.
    (datetime(2026, 11, 27, 23, 0), HALF_DAY),
])
def test_the_supervisor_picks_the_new_york_session_whatever_the_local_date(
    zone: str, israel: datetime, session_day: date
) -> None:
    from zoneinfo import ZoneInfo

    now = israel.replace(tzinfo=ZoneInfo("Asia/Jerusalem")).astimezone(timezone.utc)
    with local_clock(zone):
        session, plans = supervisor.upcoming_session(now)
    assert session.session_date == session_day
    assert {plan.name for plan in plans} == set(FULL_DAY_PLAN)


# --- the strategies' entry windows and the session checker ---------------------------------------------


@pytest.mark.parametrize("zone", ZONES)
def test_the_strategies_entry_cutoffs_follow_the_real_close_on_any_local_clock(zone: str) -> None:
    from trading.operations.shadow_service import strategy_entry_window

    with local_clock(zone):
        cutoffs = {name: (et(strategy_entry_window(name, FIRST_TEST_SESSION)[1]),
                          et(strategy_entry_window(name, HALF_DAY)[1]))
                   for name in (*supervisor.STRATEGY_STARTS, *supervisor.MANAGED_STRATEGY_STARTS)}
    assert cutoffs == {"orb-v2": ("15:30", "12:30"), "momo": ("15:00", "12:00"), "gap-v2": ("15:30", "12:30"),
                       "vwap-v2": ("15:30", "12:30"), "momo-ap": ("15:00", "12:00")}


@pytest.mark.parametrize("zone", ZONES)
def test_the_session_checkers_deadlines_and_flat_line_are_new_york_times(zone: str) -> None:
    with local_clock(zone):
        found = {}
        for day in (FIRST_TEST_SESSION, HALF_DAY):
            closes_at = market_session(day).closes_at.astimezone(NY)  # type: ignore[union-attr]
            found[day] = (
                {name: et(sc.entry_deadline(name, closes_at, sc.NO_LIMIT_TEST_IDENTITY_CAPS))
                 for name in sc.NO_LIMIT_TEST_IDENTITY_CAPS},
                et(closes_at - sc.FLAT_BY_BEFORE_CLOSE),
            )
    assert found == {
        FIRST_TEST_SESSION: ({"MOMO_HOD_V2": "15:00", "ORB_V2": "15:30", "VWAP_PB_V2": "15:30", "GAP_GO_V2": "15:30"},
                             "15:50"),
        HALF_DAY: ({"MOMO_HOD_V2": "12:00", "ORB_V2": "12:30", "VWAP_PB_V2": "12:30", "GAP_GO_V2": "12:30"},
                   "12:50"),
    }


def test_the_bridge_logs_new_york_clock_times() -> None:
    """bridge.log's clocks are New York's (paper_bridge.log: datetime.now(NY)): the checker reads them so."""
    source = (BACKEND / "scripts_local" / "paper_bridge.py").read_text(encoding="utf-8")
    assert 'line = f"{datetime.now(NY):%H:%M:%S} ET  {kind} {text}\\n"' in source
    assert pb.NY.key == "America/New_York" and sc.NY is NY


# --- no market-time rule reads the Mac's own clock -----------------------------------------------------------

# The order path and its schedule: the bridge, the calendar, the supervisor, the manager's slots, the checker.
ORDER_PATH = ("scripts_local/paper_bridge.py", "trading/scheduler/sessions.py", "trading/operations/supervisor.py",
              "trading/manager/schedule.py", "trading/operations/session_check.py")


def naive_clock_calls(source: str) -> list[str]:
    """Calls that read or build a time in the process's local zone: datetime.now() / date.today() /
    utcnow(), fromtimestamp() without a zone, astimezone() without one, time.localtime / mktime /
    strftime / ctime, and datetime.combine(day, clock) without tzinfo."""
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        name, owner = node.func.attr, node.func.value
        owner_name = owner.id if isinstance(owner, ast.Name) else owner.attr if isinstance(owner, ast.Attribute) else ""
        bare = not node.args and not node.keywords
        if owner_name in {"datetime", "date"} and (
            name in {"today", "utcnow", "utcfromtimestamp"} or (name == "now" and bare)
            or (name == "fromtimestamp" and len(node.args) < 2 and not node.keywords)
            or (name == "combine" and len(node.args) < 3 and not any(k.arg == "tzinfo" for k in node.keywords))
        ):
            found.append(f"{node.lineno}: {ast.unparse(node)}")
        elif name == "astimezone" and bare:
            found.append(f"{node.lineno}: {ast.unparse(node)}")
        elif owner_name == "time" and name in {"localtime", "mktime", "strftime", "ctime"}:
            found.append(f"{node.lineno}: {ast.unparse(node)}")
    return found


@pytest.mark.parametrize("relative", ORDER_PATH)
def test_no_order_path_module_reads_the_macs_local_clock(relative: str) -> None:
    assert naive_clock_calls((BACKEND / relative).read_text(encoding="utf-8")) == []


def test_the_naive_clock_scan_finds_each_kind_of_local_time() -> None:
    planted = "\n".join((
        "datetime.now()", "date.today()", "datetime.utcnow()", "datetime.fromtimestamp(0)", "moment.astimezone()",
        "time.localtime()", "datetime.combine(day, clock)",
        # never local: these pass
        "datetime.now(NY)", "datetime.now(timezone.utc)", "moment.astimezone(NY)",
        "datetime.combine(day, clock, tzinfo=NY)", "datetime.fromtimestamp(0, timezone.utc)", "time.monotonic()",
    ))
    assert [line.split(": ", 1)[1] for line in naive_clock_calls(planted)] == [
        "datetime.now()", "date.today()", "datetime.utcnow()", "datetime.fromtimestamp(0)", "moment.astimezone()",
        "time.localtime()", "datetime.combine(day, clock)"]

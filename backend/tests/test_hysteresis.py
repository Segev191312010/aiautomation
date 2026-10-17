"""The shared alert state machine (T-2 BrokerWatch, T-4 overnight Gateway watch)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from trading.operations import hysteresis
from trading.operations.hysteresis import CLOSE, OPEN, Event, Hysteresis, State

T0 = datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc)


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def run(steps, machine: Hysteresis | None = None) -> tuple[Hysteresis, list[tuple[float, Event]]]:
    """Feed ``(seconds, op)`` steps (op: F failure, S success, T tick); collect the events."""
    machine = machine or Hysteresis()
    events: list[tuple[float, Event]] = []
    for seconds, op in steps:
        call = {"F": machine.failure, "S": machine.success, "T": machine.tick}[op]
        event = call(at(seconds))
        if event is not None:
            events.append((seconds, event))
    return machine, events


def kinds(events) -> list[tuple[float, str]]:
    return [(seconds, event.kind.value) for seconds, event in events]


def test_the_constants_live_here_and_are_the_constructor_defaults():
    assert hysteresis.OPEN_AFTER_COUNT == 3
    assert hysteresis.OPEN_AFTER == timedelta(minutes=2)
    assert hysteresis.CLOSE_AFTER == timedelta(minutes=2)
    assert hysteresis.MAX_PAIRS_PER_WINDOW == 1
    assert hysteresis.PAIR_WINDOW == timedelta(minutes=30)
    assert hysteresis.SUSTAIN_LIMIT == timedelta(minutes=5)
    machine = Hysteresis()
    assert (
        machine.open_after_count,
        machine.open_after,
        machine.close_after,
        machine.max_pairs_per_window,
        machine.window,
        machine.sustain_limit,
    ) == (3, timedelta(minutes=2), timedelta(minutes=2), 1, timedelta(minutes=30), timedelta(minutes=5))
    assert machine.state is State.CLEAR and machine.flaps == 0 and not machine.announced


# (name, steps, expected events [(seconds, kind)], final state, flaps)
SCENARIOS = [
    (
        "three consecutive failures open at the third",
        [(0, "F"), (5, "F"), (15, "F")],
        [(15, "OPEN")],
        State.OPEN,
        0,
    ),
    (
        "two failures two minutes apart open at the second",
        [(0, "F"), (120, "F")],
        [(120, "OPEN")],
        State.OPEN,
        0,
    ),
    (
        "two failures 119 s apart do not open; a third does",
        [(0, "F"), (119, "F"), (119.5, "T"), (130, "F")],
        [(130, "OPEN")],
        State.OPEN,
        0,
    ),
    (
        "a success breaks the streak",
        [(0, "F"), (5, "F"), (10, "S"), (15, "F"), (20, "F")],
        [],
        State.FAILING,
        0,
    ),
    (
        "time alone never opens a lone failure (slow qualification after one blip)",
        [(0, "F"), (60, "T"), (600, "T"), (1680, "T")],
        [],
        State.FAILING,
        0,
    ),
    (
        "close only after two clean minutes",
        [(0, "F"), (5, "F"), (10, "F"), (100, "S"), (219, "T"), (220, "T"), (221, "T")],
        [(10, "OPEN"), (220, "CLOSE")],
        State.CLEAR,
        0,
    ),
    (
        "a later success closes when the clean stretch is long enough",
        [(0, "F"), (5, "F"), (10, "F"), (100, "S"), (190, "S"), (220, "S")],
        [(10, "OPEN"), (220, "CLOSE")],
        State.CLEAR,
        0,
    ),
    (
        "a failure inside the clean stretch is a flap; the next success restarts the clock",
        [(0, "F"), (5, "F"), (10, "F"), (100, "S"), (200, "F"), (210, "S"), (329, "T"), (330, "T")],
        [(10, "OPEN"), (330, "CLOSE")],
        State.CLEAR,
        1,
    ),
    (
        "one pair per 30 min: a re-open inside the window is held back and closes silently",
        [
            (0, "F"), (5, "F"), (10, "F"),  # OPEN at 10
            (60, "S"), (180, "T"),  # CLOSE
            (300, "F"), (305, "F"), (310, "F"),  # held back (flap)
            (400, "S"), (520, "T"),  # silent close
        ],
        [(10, "OPEN"), (180, "CLOSE")],
        State.CLEAR,
        1,
    ),
    (
        "a held-back open still failing when the window ends is announced then",
        [
            (0, "F"), (5, "F"), (10, "F"),
            (60, "S"), (180, "T"),
            (300, "F"), (305, "F"), (310, "F"),  # held back
            (1809, "T"), (1810, "T"), (1811, "T"),  # window from the OPEN at 10 ends at 1810
        ],
        [(10, "OPEN"), (180, "CLOSE"), (1810, "OPEN")],
        State.OPEN,
        1,
    ),
    (
        # Review P1-A F-1: a genuine outage right after an announced blip waited
        # for the window (to 1810) and its exits were folded meanwhile.
        "a held-back open is announced once its failures run unbroken for the sustain limit",
        [
            (0, "F"), (5, "F"), (10, "F"), (60, "S"), (180, "T"),  # a blip's pair
            (300, "F"), (370, "F"), (440, "F"),  # held back at 440
            (510, "F"), (580, "F"),  # 280 s into the run
            (599, "T"), (601, "T"),  # time alone never counts
            (650, "F"),  # 350 s >= 300 s: announced, the budget notwithstanding
            (720, "F"), (790, "S"), (910, "T"),
        ],
        [(10, "OPEN"), (180, "CLOSE"), (650, "OPEN"), (910, "CLOSE")],
        State.CLEAR,
        1,
    ),
    (
        "the sustain limit is reached exactly at five minutes of failures",
        [
            (0, "F"), (5, "F"), (10, "F"), (60, "S"), (180, "T"),
            (300, "F"), (301, "F"), (302, "F"),  # held back
            (599, "F"),  # 299 s
            (600, "F"),  # 300 s: announced
        ],
        [(10, "OPEN"), (180, "CLOSE"), (600, "OPEN")],
        State.OPEN,
        1,
    ),
    (
        "a flapping held-back open never reaches the sustain limit: a success breaks the run",
        [
            (0, "F"), (5, "F"), (10, "F"), (60, "S"), (180, "T"),
            (300, "F"), (305, "F"), (310, "F"),  # held back
            *[
                step
                for start in range(380, 1700, 140)
                for step in ((start, "S"), (start + 70, "F"), (start + 71, "T"))
            ],
            (1780, "S"), (1900, "T"),  # a silent close
        ],
        [(10, "OPEN"), (180, "CLOSE")],
        State.CLEAR,
        11,
    ),
    (
        "after the window an open is announced at once",
        [
            (0, "F"), (5, "F"), (10, "F"), (60, "S"), (180, "T"),
            (1900, "F"), (1905, "F"), (1910, "F"), (1960, "S"), (2080, "T"),
        ],
        [(10, "OPEN"), (180, "CLOSE"), (1910, "OPEN"), (2080, "CLOSE")],
        State.CLEAR,
        0,
    ),
    (
        "a failure after a clean stretch that was long enough closes first and starts a new streak",
        [(0, "F"), (5, "F"), (10, "F"), (100, "S"), (400, "F"), (405, "F")],
        [(10, "OPEN"), (400, "CLOSE")],
        State.FAILING,
        0,
    ),
    (
        "success while clear and tick while clear do nothing",
        [(0, "S"), (10, "T"), (20, "S")],
        [],
        State.CLEAR,
        0,
    ),
]


@pytest.mark.parametrize(
    "steps, expected, final_state, flaps",
    [scenario[1:] for scenario in SCENARIOS],
    ids=[scenario[0] for scenario in SCENARIOS],
)
def test_scenarios(steps, expected, final_state, flaps):
    machine, events = run(steps)
    assert kinds(events) == expected
    assert machine.state is final_state
    assert machine.flaps == flaps


def test_events_carry_the_streak_start_and_the_recovery_time():
    machine, events = run([(0, "F"), (70, "F"), (140, "F"), (300, "S"), (420, "T")])
    (_, opened), (_, closed) = events
    assert opened == Event(kind=OPEN, at=at(140), since=at(0))
    assert closed == Event(kind=CLOSE, at=at(420), since=at(0), recovered_at=at(300))
    assert machine.failing_since is None and machine.recovering_since is None


def test_failing_alternation_every_70_s_for_an_hour_is_one_pair_and_counted_flaps():
    # Open (three failures), then failure/success alternating every 70 s for an hour:
    # the clean stretch never lasts two minutes, so it stays open; each failure that
    # ends a recovery is a flap; one CLOSE two minutes after the last success.
    steps = [(0, "F"), (5, "F"), (10, "F")]
    second = 60
    while second + 70 <= 3660:
        steps += [(second, "S"), (second + 1, "T"), (second + 70, "F"), (second + 71, "T")]
        second += 140
    steps.append((second, "S"))  # the last signal is a success
    last_success = second
    steps.append((last_success + 120, "T"))
    machine, events = run(steps)
    assert kinds(events) == [(10, "OPEN"), (last_success + 120, "CLOSE")]
    failures_after_a_success = sum(1 for seconds, step in steps if step == "F" and seconds > 60)
    assert machine.flaps == failures_after_a_success > 20


def test_alternation_from_clear_never_opens():
    steps = []
    for index in range(52):
        steps.append((index * 70, "F" if index % 2 == 0 else "S"))
        steps.append((index * 70 + 35, "T"))
    machine, events = run(steps)
    assert events == [] and machine.flaps == 0


def test_since_backdates_the_streak_and_opens_on_the_time_rule():
    machine = Hysteresis()
    # No first sweep 30 minutes after the window opened: failing since the window open.
    event = machine.failure(at(1800), since=at(0))
    assert event == Event(kind=OPEN, at=at(1800), since=at(0))
    # A later since never moves the start forward.
    other = Hysteresis()
    other.failure(at(10))
    assert other.failure(at(20), since=at(15)) is None
    assert other.failing_since == at(10)
    # Once open, its announced start stays (the recovery line names the same time).
    opened, _ = run([(0, "F"), (1, "F"), (2, "F")])
    assert opened.failure(at(10), since=at(-600)) is None
    assert opened.failing_since == at(0)


def test_custom_budget_allows_more_pairs():
    machine = Hysteresis(max_pairs_per_window=2)
    steps = []
    for start in (0, 300, 600):
        steps += [(start, "F"), (start + 1, "F"), (start + 2, "F"), (start + 10, "S"), (start + 130, "T")]
    machine, events = run(steps, machine)
    assert [kind for _, kind in kinds(events)] == ["OPEN", "CLOSE", "OPEN", "CLOSE"]
    assert machine.flaps == 1


def test_open_after_count_of_one_and_a_deferred_open():
    machine = Hysteresis(open_after_count=1)
    assert machine.failure(at(0)).kind is OPEN
    machine.success(at(10))
    # The clean stretch had already closed it when the next failure arrives: CLOSE now,
    # and the new streak (already due) opens on the next call. The budget holds it back.
    assert machine.failure(at(200)).kind is CLOSE
    assert machine.state is State.FAILING
    assert machine.tick(at(201)) is None and machine.state is State.OPEN and machine.flaps == 1
    assert machine.tick(at(1800)).kind is OPEN


def test_reset_forgets_everything():
    machine, _ = run([(0, "F"), (1, "F"), (2, "F"), (10, "S"), (20, "F")])
    assert machine.flaps == 1 and machine.announced
    machine.reset()
    assert machine.state is State.CLEAR and machine.flaps == 0 and not machine.announced
    assert machine.failing_since is None and machine.recovering_since is None
    # The budget is forgotten too: a new open is announced at once.
    _, events = run([(30, "F"), (31, "F"), (32, "F")], machine)
    assert kinds(events) == [(32, "OPEN")]


def test_inputs_are_validated():
    naive = datetime(2026, 10, 13, 12, 0)
    machine = Hysteresis()
    for call in (machine.failure, machine.success, machine.tick):
        with pytest.raises(ValueError):
            call(naive)
    with pytest.raises(ValueError):
        machine.failure(at(0), since=naive)
    for kwargs in (
        {"open_after_count": 0},
        {"open_after_count": True},
        {"max_pairs_per_window": 0},
        {"open_after": timedelta(seconds=-1)},
        {"close_after": timedelta(seconds=-1)},
        {"window": timedelta(0)},
        {"sustain_limit": timedelta(0)},
        {"sustain_limit": 300},
    ):
        with pytest.raises(ValueError):
            Hysteresis(**kwargs)


# --------------------------------------------------------------------------
# close_after_count (P1-A.4; owner 2026-10-17, review N14 MEDIUM-1)
# --------------------------------------------------------------------------


def test_the_default_close_after_count_is_one_and_every_old_scenario_is_unchanged():
    """Plan HIGH-2: the overnight port watch and the competing-login watch build
    ``Hysteresis()`` too; a default above 1 would change them silently."""
    machine = Hysteresis()
    assert machine.close_after_count == 1
    for _name, steps, expected, final_state, flaps in SCENARIOS:
        default, events = run(steps)
        explicit, explicit_events = run(steps, Hysteresis(close_after_count=1))
        assert kinds(events) == kinds(explicit_events) == expected
        assert [event for _, event in events] == [event for _, event in explicit_events]
        assert default.state is explicit.state is final_state
        assert default.flaps == explicit.flaps == flaps
    # One success, then two clean minutes: closed, as before the parameter existed.
    _, events = run([(0, "F"), (5, "F"), (10, "F"), (100, "S"), (220, "T")])
    assert kinds(events) == [(10, "OPEN"), (220, "CLOSE")]


# (name, steps, expected events [(seconds, kind)], final state, flaps), with
# close_after_count=3: three successes in a row AND two clean minutes since the
# first of them; a failure before both restarts the count and the clock.
COUNT_SCENARIOS = [
    (
        "three successes over two minutes close at the third",
        [(0, "F"), (5, "F"), (10, "F"), (100, "S"), (165, "S"), (219, "T"), (230, "S")],
        [(10, "OPEN"), (230, "CLOSE")],
        State.CLEAR,
        0,
    ),
    (
        "three successes within seconds close only two minutes after the first",
        [(0, "F"), (5, "F"), (10, "F"), (100, "S"), (105, "S"), (110, "S"), (219, "T"), (220, "T")],
        [(10, "OPEN"), (220, "CLOSE")],
        State.CLEAR,
        0,
    ),
    (
        "a lone success never closes, however long the silence after it",
        [(0, "F"), (5, "F"), (10, "F"), (100, "S"), (220, "T"), (3700, "T")],
        [(10, "OPEN")],
        State.RECOVERING,
        0,
    ),
    (
        "two successes never close either",
        [(0, "F"), (5, "F"), (10, "F"), (100, "S"), (165, "S"), (3700, "T")],
        [(10, "OPEN")],
        State.RECOVERING,
        0,
    ),
    (
        "two successes then a failure after two minutes is a flap, not a close",
        [(0, "F"), (5, "F"), (10, "F"), (100, "S"), (165, "S"), (230, "F"), (231, "T")],
        [(10, "OPEN")],
        State.OPEN,
        1,
    ),
    (
        "one success then a failure 2 min 10 s later (N14: a lucky sweep in a refusal) is a flap",
        [(0, "F"), (5, "F"), (10, "F"), (100, "S"), (230, "F"), (250, "F")],
        [(10, "OPEN")],
        State.OPEN,
        1,
    ),
    (
        "a failure restarts the count: two, a failure, one more is one",
        [
            (0, "F"), (5, "F"), (10, "F"),
            (100, "S"), (165, "S"), (200, "F"),  # flap: the count starts again
            (300, "S"), (420, "T"), (1000, "T"),  # one success: still open
            (1065, "S"), (1130, "S"),  # the third since the failure, 830 s after the first
        ],
        [(10, "OPEN"), (1130, "CLOSE")],
        State.CLEAR,
        1,
    ),
    (
        "a failure after a clean stretch that met both closes first and starts a new streak",
        [(0, "F"), (5, "F"), (10, "F"), (100, "S"), (165, "S"), (230, "S"), (400, "F"), (405, "F")],
        [(10, "OPEN"), (230, "CLOSE")],
        State.FAILING,
        0,
    ),
    (
        "a success still breaks a streak that has not opened: one is enough",
        [(0, "F"), (5, "F"), (10, "S"), (600, "F"), (605, "F")],
        [],
        State.FAILING,
        0,
    ),
]


@pytest.mark.parametrize(
    "steps, expected, final_state, flaps",
    [scenario[1:] for scenario in COUNT_SCENARIOS],
    ids=[scenario[0] for scenario in COUNT_SCENARIOS],
)
def test_close_after_count_scenarios(steps, expected, final_state, flaps):
    machine, events = run(steps, Hysteresis(close_after_count=3))
    assert kinds(events) == expected
    assert machine.state is final_state
    assert machine.flaps == flaps


def test_a_counted_close_names_the_first_success_and_takes_effect_at_the_later_time():
    opened = [(0, "F"), (5, "F"), (10, "F")]
    # Three successes spread out: in effect at the third (230 > 100 + 120).
    _, events = run([*opened, (100, "S"), (165, "S"), (230, "S")], Hysteresis(close_after_count=3))
    assert events[-1][1] == Event(kind=CLOSE, at=at(230), since=at(0), recovered_at=at(100))
    # Three within seconds: in effect two minutes after the first, whenever the tick.
    _, events = run([*opened, (100, "S"), (101, "S"), (102, "S"), (300, "T")], Hysteresis(close_after_count=3))
    assert events[-1][1] == Event(kind=CLOSE, at=at(220), since=at(0), recovered_at=at(100))
    # The same pair budget: a re-open inside the window after a counted close is held back.
    machine, events = run(
        [
            (0, "F"), (5, "F"), (10, "F"), (100, "S"), (165, "S"), (230, "S"),
            (300, "F"), (305, "F"), (310, "F"),
        ],
        Hysteresis(close_after_count=3),
    )
    assert kinds(events) == [(10, "OPEN"), (230, "CLOSE")]
    assert machine.is_open and not machine.announced and machine.flaps == 1


def test_close_after_count_is_validated():
    for value in (0, -1, True, 1.5, "3"):
        with pytest.raises(ValueError):
            Hysteresis(close_after_count=value)
    assert Hysteresis(close_after_count=3).close_after_count == 3

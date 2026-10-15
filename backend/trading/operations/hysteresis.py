"""One alert state machine for every "is it down?" condition, so the numbers cannot drift.

A raw reachability signal is noisy. Live 2026-10-14 the screener logged 183
"TWS connection failed" lines, and a watcher that pages on every failure and
every recovery sends the owner a pair of messages per blip (review 904af38
F-1). ``Hysteresis`` turns failure/success signals for ONE cause into at most
one announced OPEN/CLOSE pair per window. It is pure: it never reads a clock
(every call takes ``now``, timezone-aware), it does no I/O, and it knows
nothing about logs, sockets or time of day. Quiet hours and lead-time pages
belong to the caller.

Users, with the default constants below and no other copy of them:
  * ``notify_service.BrokerWatch`` (T-2): the IB Gateway inside the notifier's
    07:55-16:15 ET day, fed by log lines (screener/bridge/children failures,
    screener sweeps) and ticked every notifier cycle;
  * the overnight Gateway watch (T-4): the same machine, fed by a 5-minute TCP
    check of the Gateway port. It instantiates ``Hysteresis()`` with these
    defaults; the constants live here and nowhere else.

Rules (defaults in brackets)
  * OPEN: at the failure signal that is the ``open_after_count``-th [3] in an
    unbroken streak, or that arrives at least ``open_after`` [2 min] after the
    streak's first failure, whichever comes first. A success breaks the
    streak. Time alone never opens: the decision is made on a failure signal
    (or on ``tick`` only when the last failure had already met the rule), so a
    lone failure followed by silence is not an outage. Live 2026-10-13: one
    connect failure, then an 8-minute universe qualification with no sweep.
  * CLOSE: once a success has been followed by ``close_after`` [2 min] with no
    failure; checked on the next ``success``/``tick`` (a ``failure`` that arrives
    after the clean stretch has already lasted that long closes first and
    starts a new streak). A failure inside the clean stretch is a flap: the
    condition stays open and the next success restarts the clock.
  * Budget: at most ``max_pairs_per_window`` [1] OPEN announcements in any
    ``window`` [30 min] (counted from each announced OPEN), hence at most that
    many OPEN/CLOSE pairs, with one exception below. An open beyond the budget
    is held back: no event, and if it closes before it is announced its close
    is silent too. A held-back open that is still failing once the budget
    allows is announced then (on the next ``failure`` or ``tick``).
  * Sustain limit: the budget holds an open back only while its failures are
    younger than ``sustain_limit`` [5 min]. Once failures have run unbroken
    that long (from the first failure after the last success to the latest
    failure signal; time alone still never counts), the open is announced
    whatever the budget, on that ``failure`` or the next ``tick``. A flap never
    lasts that long (a success breaks the run), so flaps stay one pair plus a
    count; a genuine outage right after an announced blip pages within the
    sustain limit plus one failure interval, not up to a window later (review
    P1-A F-1: 21 minutes of silence before 08:46 on the blip-then-outage replay).
  * ``flaps`` counts the condition flipping without a message: a recovery that
    did not hold for ``close_after``, and an open held back by the budget (also
    when the sustain limit or the window later announces it). It only grows
    (until ``reset``); callers report the difference (BrokerWatch: a line in
    the next digest).

API
  ``Hysteresis(open_after_count=3, open_after=2 min, close_after=2 min,
  max_pairs_per_window=1, window=30 min, sustain_limit=5 min)``
  ``failure(now, *, since=None) -> Event | None``: a failure signal; ``since``
      backdates a streak that has not opened yet when the caller knows the
      failure began earlier (BrokerWatch: no first sweep since the screener's
      08:00 window open).
  ``success(now) -> Event | None``: a success signal.
  ``tick(now) -> Event | None``: time passing with no signal; call it as often
      as the caller's resolution (BrokerWatch: every notifier cycle).
  ``state`` (``State``), ``is_open`` (OPEN or RECOVERING, announced or not),
  ``announced`` (an OPEN was emitted and its CLOSE not yet), ``failing_since``,
  ``recovering_since``, ``flaps``; ``reset()`` forgets everything.
  Each call returns at most one ``Event``: ``Event.kind`` is ``OPEN`` or
  ``CLOSE``; ``since`` is the streak's first failure; ``recovered_at`` (CLOSE
  only) is the first success of the clean stretch that closed it.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum

OPEN_AFTER_COUNT = 3
OPEN_AFTER = timedelta(minutes=2)
CLOSE_AFTER = timedelta(minutes=2)
MAX_PAIRS_PER_WINDOW = 1
PAIR_WINDOW = timedelta(minutes=30)
SUSTAIN_LIMIT = timedelta(minutes=5)


class State(Enum):
    CLEAR = "clear"  # no failure since the last close (or ever)
    FAILING = "failing"  # a failure streak that has not met the open rule
    OPEN = "open"  # open; announced or held back by the budget
    RECOVERING = "recovering"  # open, a success seen, waiting for close_after clean


class EventKind(Enum):
    OPEN = "OPEN"
    CLOSE = "CLOSE"


OPEN = EventKind.OPEN
CLOSE = EventKind.CLOSE


@dataclass(frozen=True, slots=True)
class Event:
    """An announced transition. ``at``: when it took effect (OPEN: the call's
    ``now``; CLOSE: ``recovered_at + close_after``). ``since``: the first failure
    of the streak that opened it. ``recovered_at``: CLOSE only, the first
    success of the clean stretch."""

    kind: EventKind
    at: datetime
    since: datetime
    recovered_at: datetime | None = None


def _aware(now: datetime, name: str = "now") -> datetime:
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError(f"{name} must be a timezone-aware datetime")
    return now


class Hysteresis:
    """Debounced OPEN/CLOSE for one cause; see the module docstring for the rules."""

    def __init__(
        self,
        *,
        open_after_count: int = OPEN_AFTER_COUNT,
        open_after: timedelta = OPEN_AFTER,
        close_after: timedelta = CLOSE_AFTER,
        max_pairs_per_window: int = MAX_PAIRS_PER_WINDOW,
        window: timedelta = PAIR_WINDOW,
        sustain_limit: timedelta = SUSTAIN_LIMIT,
    ) -> None:
        for name, count in (
            ("open_after_count", open_after_count),
            ("max_pairs_per_window", max_pairs_per_window),
        ):
            if isinstance(count, bool) or not isinstance(count, int) or count < 1:
                raise ValueError(f"{name} must be an int >= 1")
        for name, value in (("open_after", open_after), ("close_after", close_after)):
            if not isinstance(value, timedelta) or value < timedelta(0):
                raise ValueError(f"{name} must be a non-negative timedelta")
        for name, value in (("window", window), ("sustain_limit", sustain_limit)):
            if not isinstance(value, timedelta) or value <= timedelta(0):
                raise ValueError(f"{name} must be a positive timedelta")
        self.open_after_count = open_after_count
        self.open_after = open_after
        self.close_after = close_after
        self.max_pairs_per_window = max_pairs_per_window
        self.window = window
        self.sustain_limit = sustain_limit
        self.reset()

    def reset(self) -> None:
        self._state = State.CLEAR
        self._announced = False
        self._failures = 0
        self._failing_since: datetime | None = None
        self._last_failure_at: datetime | None = None
        # The first failure after the last success: where the sustain limit counts from.
        self._unbroken_since: datetime | None = None
        self._recovering_since: datetime | None = None
        self._opens: deque[datetime] = deque()
        self._flaps = 0

    # -- read-only view ---------------------------------------------------

    @property
    def state(self) -> State:
        return self._state

    @property
    def is_open(self) -> bool:
        return self._state in (State.OPEN, State.RECOVERING)

    @property
    def announced(self) -> bool:
        return self._announced

    @property
    def flaps(self) -> int:
        """Flips without a message since construction or ``reset``; only grows."""
        return self._flaps

    @property
    def failing_since(self) -> datetime | None:
        return self._failing_since

    @property
    def recovering_since(self) -> datetime | None:
        return self._recovering_since

    # -- signals ----------------------------------------------------------

    def failure(self, now: datetime, *, since: datetime | None = None) -> Event | None:
        _aware(now)
        if since is not None:
            _aware(since, "since")
        state = self._state
        if state is State.RECOVERING and self._clean_long_enough(now):
            # The clean stretch had already closed it; this failure starts a new
            # streak (counted, and opened on the next call if it already meets the rule).
            event = self._close()
            self._start_streak(now, since)
            return event
        if state is State.CLEAR:
            self._start_streak(now, since)
            return self._open_if_due(now)
        if state is State.FAILING:
            self._failures += 1
            self._last_failure_at = now
            self._backdate(since)
            return self._open_if_due(now)
        if state is State.RECOVERING:
            # The recovery did not hold: no message, one flap. The success broke
            # the run of failures, so the sustain limit counts from here.
            self._state = State.OPEN
            self._recovering_since = None
            self._unbroken_since = now
            self._flaps += 1
        self._last_failure_at = now
        # Open already: its start is the one announced (``since`` no longer moves it).
        return self._announce_if_allowed(now)

    def success(self, now: datetime) -> Event | None:
        _aware(now)
        if self._state is State.FAILING:
            self._clear()
            return None
        if self._state is State.OPEN:
            self._state = State.RECOVERING
            self._recovering_since = now
        if self._state is State.RECOVERING and self._clean_long_enough(now):
            return self._close()
        return None

    def tick(self, now: datetime) -> Event | None:
        _aware(now)
        if self._state is State.RECOVERING and self._clean_long_enough(now):
            return self._close()
        if self._state is State.OPEN:
            return self._announce_if_allowed(now)
        if self._state is State.FAILING:
            return self._open_if_due(now)
        return None

    # -- transitions --------------------------------------------------------

    def _start_streak(self, now: datetime, since: datetime | None) -> None:
        self._state = State.FAILING
        self._failures = 1
        self._failing_since = now
        self._unbroken_since = now
        self._last_failure_at = now
        self._backdate(since)

    def _backdate(self, since: datetime | None) -> None:
        if since is not None and self._failing_since is not None and since < self._failing_since:
            # Still FAILING (no success yet): the unbroken run starts there too.
            self._failing_since = since
            self._unbroken_since = since

    def _open_if_due(self, now: datetime) -> Event | None:
        assert self._failing_since is not None and self._last_failure_at is not None
        # Measured at the last failure signal, never at "now": time alone never opens.
        lasted = self._last_failure_at - self._failing_since
        if self._failures < self.open_after_count and lasted < self.open_after:
            return None
        self._state = State.OPEN
        event = self._announce_if_allowed(now)
        if event is None:
            self._flaps += 1  # held back by the budget
        return event

    def _announce_if_allowed(self, now: datetime) -> Event | None:
        if self._announced:
            return None
        while self._opens and now - self._opens[0] >= self.window:
            self._opens.popleft()
        if len(self._opens) >= self.max_pairs_per_window and not self._sustained():
            return None
        assert self._failing_since is not None
        self._opens.append(now)
        self._announced = True
        return Event(kind=OPEN, at=now, since=self._failing_since)

    def _sustained(self) -> bool:
        """Failures have run unbroken for ``sustain_limit``: an outage, not a flap.

        Measured at the latest failure signal, never at "now", so a held-back
        open whose failures stopped (the Gateway back, the first sweep still
        qualifying) is not announced by time alone.
        """
        return (
            self._state is State.OPEN
            and self._unbroken_since is not None
            and self._last_failure_at is not None
            and self._last_failure_at - self._unbroken_since >= self.sustain_limit
        )

    def _clean_long_enough(self, now: datetime) -> bool:
        assert self._recovering_since is not None
        return now - self._recovering_since >= self.close_after

    def _close(self) -> Event | None:
        assert self._failing_since is not None and self._recovering_since is not None
        event: Event | None = None
        if self._announced:
            event = Event(
                kind=CLOSE,
                at=self._recovering_since + self.close_after,
                since=self._failing_since,
                recovered_at=self._recovering_since,
            )
        self._clear()
        return event

    def _clear(self) -> None:
        self._state = State.CLEAR
        self._announced = False
        self._failures = 0
        self._failing_since = None
        self._last_failure_at = None
        self._unbroken_since = None
        self._recovering_since = None


__all__ = [
    "CLOSE",
    "CLOSE_AFTER",
    "Event",
    "EventKind",
    "Hysteresis",
    "MAX_PAIRS_PER_WINDOW",
    "OPEN",
    "OPEN_AFTER",
    "OPEN_AFTER_COUNT",
    "PAIR_WINDOW",
    "SUSTAIN_LIMIT",
    "State",
]

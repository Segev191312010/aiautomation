"""What the IB Gateway watch says outside the notifier's day (T-4), as pure decisions.

Inside ``NOTIFIER_WINDOW`` on session days (07:55-16:15 ET) the notifier's
``BrokerWatch`` owns the Gateway. Outside it, the always-on Telegram child
runs this watch (``overnight_watch``): a TCP check of the API port every
``PORT_CHECK_INTERVAL``, fed into the shared ``Hysteresis`` with its default
constants (the numbers BrokerWatch uses: three failed checks or two minutes
to open, two clean minutes to close, one pair per half hour), and its default
``close_after_count`` of 1: one good check starts the two clean minutes
(BrokerWatch alone asks for three successes, P1-A.4; plan HIGH-2). Nothing here
reads a clock, a socket or a file; every call takes ``now``.

Schedule, America/New_York, derived from the supervisor's windows and the
session calendar (no clock time is typed here):

  * pre-session leads: ``PRE_SESSION_LEADS`` before ``SCREENER_WINDOW[0]``
    (SESSION_START) on session days only: 05:55 and 07:40 ET;
  * the watch day: from a session day's first lead to ``RECORDER_WINDOW``'s
    end (17:30 ET; owner, 2026-10-17: the evening pages 16:15-17:30 ET, 23:15-
    00:30 Israel, then sleep). Everything else is quiet hours: 17:30 ET to the
    next session day's 05:55 ET, so a weekend or a holiday is quiet throughout;
  * the notifier's window: the watch is silent (``PortWatch.handover``);
  * the competing-login window: ``SCREENER_WINDOW[0]`` to ``RECORDER_WINDOW``'s
    end (07:55-17:30 ET) on session days; the notifier covers it to its own
    end, the Telegram child the rest (the recorder's log).

``PortWatch``:
  * quiet hours: no page; an outage that opens is a digest entry, and the
    digest is one line at the next lead (``digest_text``);
  * at each lead, the port closed: a page naming the deadline ("session
    starts 07:55 ET") and the check ("05:55 ET check"), every lead, already
    paged or not. A lead whose check is the streak's first failure is
    confirmed one ``open_after`` later (the hysteresis opens on the second
    failure two minutes into the streak), so a Gateway restart that happens to
    span the lead is not paged;
  * the rest of the watch day: a page when the outage opens;
  * one recovery line when a paged outage closes, whenever that is (it
    answers a page the owner already has);
  * entering the notifier's window: a paged outage whose last check succeeded,
    or whose port answers the handover's own check (N15b L-12), gets its
    recovery line; then everything is forgotten (BrokerWatch's turn).

An outage's start is never invented (N15b L-1). When the watch starts looking
(the first start, a restart, 16:15 after BrokerWatch) and its first check
already fails, the outage began at some unknown time before: "since before
HH:MM ET", unless the caller hands it a ``KnownOutage`` (the watch's own state
saved before a restart, or the notifier's announced outage at 16:15 when the
notifier was still checking it near its end, N15c LOW-1). A known
outage keeps its start, and if the phone was already paged it is not paged
again (a lead still pages, as always) and never goes into the digest as
"not paged"; its recovery line comes when the port answers.

``CompetingLoginWatch``: a login elsewhere (TWS 10197, or 162/165 "different
IP address") as one ``Hysteresis(open_after_count=1)``: the first line pages
at once (live 2026-10-13: an hour of refused data and no alert), and while
lines keep coming it is re-paged every ``COMPETING_LOGIN_REPAGE``. IBKR sends
nothing when the other login ends, but the screener logs the refusal at most
once a minute and the recorder after every restart, so a whole re-page
interval without a line is the end: one success signal, and the hysteresis
closes (silently) two clean minutes later; a line inside those two minutes is
a flap and the condition stays open. A new outage within the half hour after
the last one opened is held back unless its lines run five minutes (the
hysteresis's sustain limit).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from enum import Enum

from trading.operations.hysteresis import CLOSE, OPEN, Event, Hysteresis, State
from trading.operations.supervisor import (
    NOTIFIER_WINDOW,
    RECORDER_WINDOW,
    SCREENER_WINDOW,
)
from trading.scheduler.sessions import MARKET_TIMEZONE, market_session

# The TCP check's cadence outside the notifier's window (plan T-4).
PORT_CHECK_INTERVAL = timedelta(minutes=5)
# Before SESSION_START (SCREENER_WINDOW[0], 07:55 ET): 05:55 and 07:40 ET,
# 12:55 and 14:40 Israel (11:55 and 13:40 in the week Israel is ET+6).
PRE_SESSION_LEADS = (timedelta(hours=2), timedelta(minutes=15))
# A competing login is re-paged this often while its lines keep coming; the
# notifier's IBKR alert throttle is the same number (notify_service).
COMPETING_LOGIN_REPAGE = timedelta(minutes=10)


def _at(day: date, moment: time) -> datetime:
    return datetime.combine(day, moment, tzinfo=MARKET_TIMEZONE)


def et_day(now: datetime) -> date:
    return now.astimezone(MARKET_TIMEZONE).date()


def is_session_day(day: date) -> bool:
    return market_session(day) is not None


def session_start(day: date) -> datetime:
    """SESSION_START: the supervisor's first child window opens (the screener's)."""
    return _at(day, SCREENER_WINDOW[0])


def pre_session_leads(day: date) -> tuple[datetime, ...]:
    """The day's pre-session leads, earliest first; none on a closed day."""
    if not is_session_day(day):
        return ()
    start = session_start(day)
    return tuple(sorted(start - lead for lead in PRE_SESSION_LEADS))


def notifier_window(day: date) -> tuple[datetime, datetime] | None:
    if not is_session_day(day):
        return None
    return _at(day, NOTIFIER_WINDOW[0]), _at(day, NOTIFIER_WINDOW[1])


def in_notifier_window(now: datetime) -> bool:
    window = notifier_window(et_day(now))
    return window is not None and window[0] <= now < window[1]


def watch_day(day: date) -> tuple[datetime, datetime] | None:
    """From the first lead to the recorder window's end; None on a closed day."""
    leads = pre_session_leads(day)
    if not leads:
        return None
    return leads[0], _at(day, RECORDER_WINDOW[1])


def is_quiet(now: datetime) -> bool:
    """Quiet hours: outside the watch day (17:30 ET to the next session day's first lead)."""
    span = watch_day(et_day(now))
    return span is None or not span[0] <= now < span[1]


def competing_login_window(day: date) -> tuple[datetime, datetime] | None:
    """07:55-17:30 ET on session days: the session and the recorder."""
    if not is_session_day(day):
        return None
    return _at(day, SCREENER_WINDOW[0]), _at(day, RECORDER_WINDOW[1])


def after_notifier_competing_window(day: date) -> tuple[datetime, datetime] | None:
    """The Telegram child's share of the competing-login window: the notifier's end to the recorder's."""
    window = competing_login_window(day)
    if window is None:
        return None
    return _at(day, NOTIFIER_WINDOW[1]), window[1]


def clock_text(moment: datetime, now: datetime) -> str:
    """``HH:MM ET``, with the weekday when it is not ``now``'s ET date."""
    local = moment.astimezone(MARKET_TIMEZONE)
    if local.date() == et_day(now):
        return local.strftime("%H:%M ET")
    return local.strftime("%a %H:%M ET")


# -- the port ---------------------------------------------------------------


def since_text(since: datetime, now: datetime, *, before: bool = False) -> str:
    """``HH:MM ET``, or ``before HH:MM ET`` when the outage was already there at the first look."""
    return f"before {clock_text(since, now)}" if before else clock_text(since, now)


def port_closed_text(
    since: datetime, now: datetime, *, check: datetime | None = None, before: bool = False
) -> str:
    """The page: since when, what to do, the deadline before the session, and the lead's check."""
    text = (
        f"⛔ IB Gateway closed since {since_text(since, now, before=before)} (its API port does not answer)"
        " — log in to IB Gateway"
    )
    day = et_day(now)
    if is_session_day(day) and now < session_start(day):
        text += f"; session starts {session_start(day).strftime('%H:%M')} ET"
    if check is not None:
        text += f" ({check.astimezone(MARKET_TIMEZONE).strftime('%H:%M')} ET check)"
    return text


def port_reopened_text(reopened_at: datetime, since: datetime, now: datetime, *, before: bool = False) -> str:
    return (
        f"✅ IB Gateway port open again {clock_text(reopened_at, now)}"
        f" (closed since {since_text(since, now, before=before)})"
    )


@dataclass(frozen=True, slots=True)
class KnownOutage:
    """An outage the watch knows of from before it looked: its start (``before``:
    already there at that first look), and whether the phone was paged."""

    since: datetime
    paged: bool
    before: bool = False


@dataclass(slots=True)
class Outage:
    """One digest entry: an outage that opened in quiet hours."""

    since: datetime
    until: datetime | None = None
    before: bool = False


def digest_text(
    outages: list[Outage], flaps: int, now: datetime, *, reopening: datetime | None = None
) -> str:
    """One line for the quiet hours; ``reopening``: the open outage's last check succeeded then."""
    parts: list[str] = []
    for outage in outages:
        since = since_text(outage.since, now, before=outage.before)
        if outage.until is None and reopening is not None:
            parts.append(f"port closed since {since}, open again since {clock_text(reopening, now)}")
        elif outage.until is None:
            parts.append(f"port closed since {since}, still closed")
        elif outage.before:
            parts.append(f"port closed since {since}, open again {clock_text(outage.until, now)}")
        else:
            parts.append(f"port closed {since}–{clock_text(outage.until, now)}")
    if flaps:
        parts.append(f"{flaps} brief flap{'' if flaps == 1 else 's'} held back")
    return "🌙 Overnight IB Gateway watch (quiet hours, not paged): " + "; ".join(parts)


class PortWatch:
    """The port's outages outside the notifier's window; see the module docstring."""

    def __init__(self) -> None:
        self.hysteresis = Hysteresis()
        self.reset()

    def reset(self) -> None:
        self.hysteresis.reset()
        self._paged = False
        self._in_digest = False
        self._outage_since: datetime | None = None
        # The tracked outage was already there when the watch first looked.
        self._since_unknown = False
        self._lead_pending: datetime | None = None
        self._digest: list[Outage] = []
        self._quiet_flaps = 0
        # Evidence from before this watch looked (N15b L-1), until a check settles it.
        self._known: KnownOutage | None = None
        # Whether any check since the watch began looking found the port open.
        self._seen_open = False

    def carry(self, known: KnownOutage) -> None:
        """An outage known from before this watch looked (its own state before a
        restart, the notifier's at 16:15); ignored while one is tracked already."""
        if self._outage_since is None and not self.hysteresis.is_open:
            self._known = known

    def record(self) -> KnownOutage | None:
        """The outage to remember across a restart: the tracked one (paged or in the
        digest), else the evidence not settled yet; None when there is none."""
        if self._outage_since is not None and (self._paged or self._in_digest):
            return KnownOutage(since=self._outage_since, paged=self._paged, before=self._since_unknown)
        return self._known

    @property
    def owes_recovery(self) -> bool:
        """A paged outage whose last check still failed (a known one not settled yet
        included): the handover checks the port once more (N15b L-12)."""
        if self._known is not None:
            return self._known.paged
        return self._paged and self.hysteresis.recovering_since is None

    @property
    def lead_pending(self) -> datetime | None:
        """A lead whose check failed before the hysteresis opened: confirm it soon."""
        return self._lead_pending

    @property
    def paged(self) -> bool:
        return self._paged

    def check(self, now: datetime, port_open: bool) -> list[str]:
        """One TCP check outside the notifier's window; the messages it causes."""
        return self._signal(now, port_open)

    def tick(self, now: datetime) -> list[str]:
        recovering = self.hysteresis.recovering_since
        return self._apply(self.hysteresis.tick(now), now, recovering)

    def lead(self, now: datetime, port_open: bool, *, lead_at: datetime) -> list[str]:
        """A pre-session lead's check: the overnight digest first, then the page if closed."""
        messages = self._signal(now, port_open, lead_at=lead_at)
        if not port_open and not self._paged_by(messages):
            if self.hysteresis.is_open:
                messages.append(self._page(now, lead_at))
            elif self.hysteresis.state is State.FAILING:
                # The streak's first failure: confirmed one open_after later.
                self._lead_pending = lead_at
        digest = self.take_digest(now)
        return ([digest] if digest else []) + messages

    def take_digest(self, now: datetime) -> str | None:
        """The quiet hours' line, once; an outage still open is the pages' from here."""
        if not self._digest and not self._quiet_flaps:
            return None
        text = digest_text(
            self._digest, self._quiet_flaps, now, reopening=self.hysteresis.recovering_since
        )
        self._digest = []
        self._in_digest = False
        self._quiet_flaps = 0
        return text

    def handover(self, now: datetime, port_open: bool | None = None) -> list[str]:
        """Entering the notifier's window: anything left in the digest, the recovery
        line a paged outage still owes (its last check succeeded, or ``port_open``:
        the handover's own check, N15b L-12), then forget."""
        messages: list[str] = []
        digest = self.take_digest(now)
        if digest:
            messages.append(digest)
        recovering = self.hysteresis.recovering_since
        known = self._known
        if self._paged and self._outage_since is not None:
            reopened = recovering or (now if port_open else None)
            if reopened is not None:
                messages.append(
                    port_reopened_text(reopened, self._outage_since, now, before=self._since_unknown)
                )
        elif known is not None and known.paged and port_open:
            messages.append(port_reopened_text(now, known.since, now, before=known.before))
        self.reset()
        return messages

    # -- transitions --------------------------------------------------------

    def _signal(self, now: datetime, port_open: bool, *, lead_at: datetime | None = None) -> list[str]:
        recovering = self.hysteresis.recovering_since
        flaps = self.hysteresis.flaps
        event = self.hysteresis.success(now) if port_open else self.hysteresis.failure(now)
        if self.hysteresis.flaps > flaps and is_quiet(now):
            self._quiet_flaps += self.hysteresis.flaps - flaps
        messages = self._settle_known(now) if port_open else []
        if port_open:
            self._seen_open = True
        return messages + self._apply(event, now, recovering, lead_at=lead_at)

    def _settle_known(self, now: datetime) -> list[str]:
        """The port answers while a known outage is not settled: it is over."""
        known, self._known = self._known, None
        if known is None or self.hysteresis.is_open:
            return []
        if known.paged:
            return [port_reopened_text(now, known.since, now, before=known.before)]
        self._digest.append(Outage(known.since, until=now, before=known.before))
        return []

    def _apply(
        self,
        event: Event | None,
        now: datetime,
        recovering: datetime | None,
        *,
        lead_at: datetime | None = None,
    ) -> list[str]:
        messages: list[str] = []
        if event is not None and event.kind is OPEN:
            known, self._known = self._known, None
            check = lead_at or self._lead_pending
            if known is not None:
                # The evidence's start, never this watch's first failed check (N15b L-1).
                self._outage_since, self._since_unknown = known.since, known.before
            else:
                self._outage_since, self._since_unknown = event.since, not self._seen_open
            if known is not None and known.paged and check is None:
                self._paged = True  # the phone has this outage already: no second page
            elif check is None and is_quiet(now):
                self._digest.append(Outage(self._outage_since, before=self._since_unknown))
                self._in_digest = True
            else:
                messages.append(self._page(now, check))
        if self.hysteresis.state is State.CLEAR:
            self._lead_pending = None
            if self._paged or self._in_digest:
                # Closed: the hysteresis's CLOSE, or a held-back open (no event) that a lead paged.
                reopened = event.recovered_at if event is not None and event.kind is CLOSE else None
                reopened = reopened or recovering or now
                since = self._outage_since or reopened
                if self._paged:
                    messages.append(port_reopened_text(reopened, since, now, before=self._since_unknown))
                else:
                    for outage in self._digest:
                        if outage.until is None:
                            outage.until = reopened
                self._paged = self._in_digest = False
                self._outage_since = None
                self._since_unknown = False
        return messages

    def _page(self, now: datetime, check: datetime | None) -> str:
        since = self._outage_since or self.hysteresis.failing_since or now
        self._outage_since = since
        self._paged = True
        self._lead_pending = None
        return port_closed_text(since, now, check=check, before=self._since_unknown)

    @staticmethod
    def _paged_by(messages: list[str]) -> bool:
        return any(text.startswith("⛔ IB Gateway closed") for text in messages)


# -- a competing login --------------------------------------------------------


class CompetingKind(Enum):
    OPENED = "opened"  # page now
    REPAGE = "repage"  # page again: still seen
    CLEARED = "cleared"  # over (no message; the caller logs it)


@dataclass(frozen=True, slots=True)
class CompetingDecision:
    kind: CompetingKind
    since: datetime
    source: str


class CompetingLoginWatch:
    """One cause, "IBKR login active elsewhere"; see the module docstring."""

    def __init__(self, *, repage: timedelta = COMPETING_LOGIN_REPAGE) -> None:
        if repage <= timedelta(0):
            raise ValueError("repage must be a positive timedelta")
        self.repage = repage
        self.hysteresis = Hysteresis(open_after_count=1)
        self.reset()

    def reset(self) -> None:
        self.hysteresis.reset()
        self._paged_at: datetime | None = None
        self._last_seen: datetime | None = None
        self._since: datetime | None = None
        self._source = ""

    @property
    def paged_at(self) -> datetime | None:
        return self._paged_at

    def seen(self, now: datetime, source: str) -> CompetingDecision | None:
        """A competing-login line from ``source``'s log."""
        self._source = source
        self._last_seen = now
        return self._decide(self.hysteresis.failure(now), now)

    def heard(self, at: datetime) -> None:
        """The phone already heard this outage at ``at`` (a throttle the caller
        persists across restarts held this page back): re-pages count from there."""
        self._paged_at = at

    def tick(self, now: datetime) -> CompetingDecision | None:
        event = self.hysteresis.tick(now)
        if event is not None:
            return self._decide(event, now)
        if not self.hysteresis.announced or self._paged_at is None or self._since is None:
            return None
        if now - self._paged_at < self.repage:
            return None
        if self._last_seen is not None and self._last_seen > self._paged_at:
            self._paged_at = now
            return CompetingDecision(CompetingKind.REPAGE, self._since, self._source)
        if self.hysteresis.state is State.OPEN:
            # A whole re-page interval without a line: the other login has ended.
            self.hysteresis.success(now)
        return None

    def _decide(self, event: Event | None, now: datetime) -> CompetingDecision | None:
        if event is None:
            return None
        if event.kind is OPEN:
            self._paged_at = now
            self._since = event.since
            return CompetingDecision(CompetingKind.OPENED, event.since, self._source)
        since = self._since or event.since
        source = self._source
        self._paged_at = None
        self._since = None
        return CompetingDecision(CompetingKind.CLEARED, since, source)


__all__ = [
    "COMPETING_LOGIN_REPAGE",
    "CompetingDecision",
    "CompetingKind",
    "CompetingLoginWatch",
    "KnownOutage",
    "Outage",
    "PORT_CHECK_INTERVAL",
    "PRE_SESSION_LEADS",
    "PortWatch",
    "after_notifier_competing_window",
    "clock_text",
    "competing_login_window",
    "digest_text",
    "et_day",
    "in_notifier_window",
    "is_quiet",
    "is_session_day",
    "notifier_window",
    "port_closed_text",
    "port_reopened_text",
    "pre_session_leads",
    "session_start",
    "since_text",
    "watch_day",
]

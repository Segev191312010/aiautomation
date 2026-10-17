"""The Telegram child's watch outside the notifier's day (T-4): the loop and its I/O.

What to say is ``gateway_watch``'s (pure decisions, its docstring has the
schedule); this module probes, reads the logs and sends:

  * outside the notifier's window, every ``PORT_CHECK_INTERVAL``: the Gateway
    port probe (``telegram_tools.gateway_port_open``, the supervisor's one
    bounded connect run in a worker thread) into ``PortWatch``. At each
    pre-session lead a check on the lead itself (again one ``open_after``
    later when it is the streak's first failure), and then, with the port
    open, the lead hook (T-4b's real-data check). Inside the window nothing is
    probed or said: at its start ``PortWatch.handover``, after one more port
    check when a paged outage's last check still failed (N15b L-12). At 16:15,
    taking the Gateway over from the notifier, the watch starts from the
    notifier's announced outage of the day (``notify_service.
    notifier_gateway_outage``: its start, and whether it was paged), never
    from its own first failed check (N15b L-1), but only when the notifier
    was still checking it near its end (``gateway_outage_current``, N15c
    LOW-1): a stale one is ignored;
  * F-2, ``supervisor.log`` on any day: the restart cap's lines
    (``CHILD_SUSPENDED``, ``CHILD_RESTART_STORM``, ``CHILD_RESUMED``) dated
    outside the notifier's window, with notify_service's own texts and
    timing rule (each once per child per half hour; a state that contradicts
    the last one sent always goes), plus the line's ET time. A line dated
    inside the window is the notifier's: read and dropped, never sent twice,
    except the lines about the notifier itself, which are this watch's at any
    hour (a suspended or crash-looping notifier cannot report itself; the
    notifier never forwards them, N15b L-3).
    The rotation's marker and carried lines are dropped by ``log_tailer``'s
    one rule, and the archive is never read again;
  * ``recorder.log`` from the notifier's end to the recorder's (16:15-17:30
    ET on session days): its competing-login lines into
    ``CompetingLoginWatch`` (a page at once, a re-page every 10 minutes while
    they keep coming, over after a 10-minute silence).

A stop is never lost. ``TelegramAssistant.stop`` cancels this task and waits
for it; Python 3.11's ``asyncio.wait_for`` runs its awaitable in a task of its
own and drops a cancellation that lands just as that task finishes (gh-86296),
so the timeouts here are ``asyncio.timeout`` (the caller's own task), and the
loop checks for a pending cancellation before every sleep in case a library
swallowed one (2026-10-17, in the composition-root test, not live: the watch
paged on while stop() waited).

Both logs are followed by ``LogTailer``s whose checkpoints live in the
assistant's state file: a restart neither resends nor skips, and the first
start follows each log from its end. The port outage the watch tracks (its
start, whether it was paged) is kept there too (``memory``), so a restart
neither invents a new start nor files a paged outage as "not paged" (N15b
L-1). The digest's other entries and the competing-login state are in memory:
a restart forgets them (the next lead's own check still pages a closed port).
Nothing here can place, change or cancel an order; it sends Telegram text only.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, MutableMapping
from datetime import date, datetime, timezone
import logging
from pathlib import Path

from trading.operations.gateway_watch import (
    PORT_CHECK_INTERVAL,
    CompetingDecision,
    CompetingKind,
    CompetingLoginWatch,
    KnownOutage,
    PortWatch,
    after_notifier_competing_window,
    clock_text,
    et_day,
    in_notifier_window,
    notifier_window,
    pre_session_leads,
)
from trading.operations.hysteresis import State
from trading.operations.log_tailer import LogTailer, RotationCarried, line_time, parse_iso
from trading.operations.notify_service import (
    CHILD_EXIT_ALERT_INTERVAL,
    CHILD_RESUMED,
    CHILD_SUSPENDED,
    GATEWAY_DOWN,
    competing_login_repage_text,
    gateway_outage_current,
    ibkr_alert_text,
    ibkr_session_problem,
    notifier_gateway_outage,
    restart_cap_event,
)
from trading.operations.paths import UserPaths
from trading.operations.supervisor import NOTIFIER_CHILD, PORT_PROBE_TIMEOUT_S, SUPERVISOR_LOG_NAME

logger = logging.getLogger("autotrader.shadow.telegram")

SUPERVISOR_LOG = "supervisor"
RECORDER_LOG = "recorder"
RECORDER_LOG_NAME = "recorder.log"
# The loop's resolution: log lines, the competing-login re-page and the
# hysteresis's clean-stretch close are seen within this long.
WATCH_TICK_SECONDS = 30.0
MIN_WAIT_SECONDS = 1.0
# A lead missed by at most this much (the child restarted across it) still runs.
LEAD_GRACE = PORT_CHECK_INTERVAL
# The probe bounds itself (PORT_PROBE_TIMEOUT_S); this bounds the worker thread too.
PROBE_DEADLINE_S = 2 * PORT_PROBE_TIMEOUT_S
# What the watch keeps in the assistant's state file (``memory``): the port
# outage it tracks, and the session date it last took the Gateway over from
# the notifier (once a day, at the notifier's end).
OUTAGE_SINCE_KEY = "port_outage_since"
OUTAGE_PAGED_KEY = "port_outage_paged"
OUTAGE_BEFORE_KEY = "port_outage_before"
TOOK_OVER_KEY = "took_over"

Clock = Callable[[], datetime]
Sleeper = Callable[[float], Awaitable[None]]
Send = Callable[[str], None]
Save = Callable[[], None]
PortProbe = Callable[[], Awaitable[bool]]
# (now, the lead's scheduled time) -> the page to send, or None (healthy).
LeadCheck = Callable[[datetime, datetime], Awaitable[str | None]]
# The notifier's announced Gateway outage of a session date, as port evidence.
NotifierOutage = Callable[[date], KnownOutage | None]


def notifier_port_outage(paths: UserPaths) -> NotifierOutage:
    """The notifier's announced outage of a date, when it is the Gateway not
    answering (``GATEWAY_DOWN``): the port watch's evidence at 16:15. IBKR-side
    outages (data refused, the link lost) leave the port open: not evidence.

    Only a current outage counts (``gateway_outage_current``: stamped by a
    notifier still checking it near its end, N15c LOW-1). One a notifier left
    when it stopped early (a crash loop, a suspension) may have ended unseen;
    borrowing its "paged" would keep a new evening outage off the phone until
    05:55, so the evening starts from the watch's own checks instead."""

    def read(day: date) -> KnownOutage | None:
        outage = notifier_gateway_outage(paths, day)
        if outage is None or outage.kind != GATEWAY_DOWN:
            return None
        if not gateway_outage_current(outage, day):
            checked = outage.checked_at
            logger.warning(
                "overnight watch: the notifier's Gateway outage since %s was last checked %s, not near"
                " its end: stale, ignored",
                clock_text(outage.since, outage.since),
                "never" if checked is None else f"at {clock_text(checked, outage.since)}",
            )
            return None
        return KnownOutage(since=outage.since, paged=outage.paged)

    return read


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


async def _default_probe() -> bool:
    # Resolved at call time, like DataTools' /status probe, so a test can
    # replace telegram_tools.gateway_port_open for a whole module.
    from trading.operations import telegram_tools

    return await telegram_tools.gateway_port_open()


def _raise_if_cancelling() -> None:
    """A cancellation that something inside the step swallowed is still pending:
    honour it before sleeping again (``Task.cancelling`` counts it)."""
    task = asyncio.current_task()
    if task is not None and task.cancelling():
        raise asyncio.CancelledError


class RestartCapGate:
    """notify_service's rule for the restart cap's lines, in memory: each
    ``kind:child`` once per ``CHILD_EXIT_ALERT_INTERVAL``, except that a
    suspension or resumption that contradicts the last one sent always goes."""

    def __init__(self) -> None:
        self._told: dict[str, str] = {}
        self._last: dict[str, datetime] = {}

    def allowed(self, kind: str, name: str, now: datetime) -> bool:
        key = f"{kind}:{name}"
        if kind in (CHILD_SUSPENDED, CHILD_RESUMED) and self._told.get(name) != kind:
            self._told[name] = kind
            self._last[key] = now
            return True
        last = self._last.get(key)
        if last is not None and now - last < CHILD_EXIT_ALERT_INTERVAL:
            return False
        self._last[key] = now
        return True


class OvernightWatch:
    """The Telegram child's background task; every collaborator is injected."""

    def __init__(
        self,
        *,
        log_directory: Path,
        checkpoints: MutableMapping[str, dict[str, int]],
        clock: Clock = _utc_now,
        sleep: Sleeper = asyncio.sleep,
        probe: PortProbe | None = None,
        lead_check: LeadCheck | None = None,
        tick_seconds: float = WATCH_TICK_SECONDS,
        memory: MutableMapping[str, str] | None = None,
        notifier_outage: NotifierOutage | None = None,
    ) -> None:
        self._tailers = {
            SUPERVISOR_LOG: LogTailer(log_directory / SUPERVISOR_LOG_NAME),
            RECORDER_LOG: LogTailer(log_directory / RECORDER_LOG_NAME),
        }
        self._checkpoints = checkpoints
        self._clock = clock
        self._sleep = sleep
        self._probe_fn = probe or _default_probe
        self._lead_check = lead_check
        self._tick = tick_seconds
        self._memory: MutableMapping[str, str] = {} if memory is None else memory
        self._notifier_outage = notifier_outage
        self._rotation = RotationCarried()
        self.port = PortWatch()
        self.competing = CompetingLoginWatch()
        self._gate = RestartCapGate()
        self._next_check: datetime | None = None
        self._done_leads: set[datetime] = set()
        self._in_notifier_window = False
        self._started = False

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        """Resume each log at its checkpoint (the first start: from its end), and the
        port outage the watch was tracking before a restart."""
        if self._started:
            return
        self._started = True
        for name, tailer in self._tailers.items():
            tailer.start(self._checkpoints.get(name))
        since = parse_iso(self._memory.get(OUTAGE_SINCE_KEY))
        if since is not None:
            self.port.carry(
                KnownOutage(
                    since=since,
                    paged=self._memory.get(OUTAGE_PAGED_KEY) == "1",
                    before=self._memory.get(OUTAGE_BEFORE_KEY) == "1",
                )
            )

    def close(self) -> None:
        for tailer in self._tailers.values():
            tailer.close()

    async def run(self, send: Send, save: Save) -> None:
        self.start()
        logger.info("overnight watch started (Gateway port, restart cap, recorder competing login)")
        try:
            while True:
                try:
                    await self.step(send, save)
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001 - one bad step never stops the watch
                    logger.exception("overnight watch step failed")
                _raise_if_cancelling()
                await self._sleep(self._wait_seconds(self._clock()))
        finally:
            self.close()

    async def step(self, send: Send, save: Save) -> None:
        self.start()
        now = self._clock()
        remembered = dict(self._memory)
        self._follow_logs(now, send, save)
        self._follow_competing(now, send)
        try:
            await self._follow_port(now, send)
        finally:
            self._remember()
            if dict(self._memory) != remembered:
                save()

    def _remember(self) -> None:
        """The port outage the watch tracks now, in the assistant's state file."""
        record = self.port.record()
        wanted = (
            {}
            if record is None
            else {
                OUTAGE_SINCE_KEY: record.since.isoformat(),
                OUTAGE_PAGED_KEY: "1" if record.paged else "0",
                OUTAGE_BEFORE_KEY: "1" if record.before else "0",
            }
        )
        for key in (OUTAGE_SINCE_KEY, OUTAGE_PAGED_KEY, OUTAGE_BEFORE_KEY):
            if key in wanted:
                self._memory[key] = wanted[key]
            else:
                self._memory.pop(key, None)

    # -- logs ----------------------------------------------------------------

    def _follow_logs(self, now: datetime, send: Send, save: Save) -> None:
        for name, tailer in self._tailers.items():
            lines = tailer.poll()
            if name == SUPERVISOR_LOG:
                for line in self._rotation.fresh(lines):
                    self._supervisor_line(line, now, send)
            else:
                for line in lines:
                    self._recorder_line(line, now, send)
        changed = False
        for name, tailer in self._tailers.items():
            checkpoint = tailer.checkpoint()
            if self._checkpoints.get(name) != checkpoint:
                self._checkpoints[name] = checkpoint
                changed = True
        if changed:
            save()

    def _supervisor_line(self, line: str, now: datetime, send: Send) -> None:
        capped = restart_cap_event(line)
        if capped is None:
            return
        at = line_time(line) or now
        kind, name, text = capped
        if in_notifier_window(at) and name != NOTIFIER_CHILD:
            return  # the notifier's line: it was (or is being) sent from there
        if not self._gate.allowed(kind, name, now):
            return
        logger.info("overnight watch: forwarded %s %s", kind, name)
        send(f"{text} · {clock_text(at, now)}")

    def _recorder_line(self, line: str, now: datetime, send: Send) -> None:
        if ibkr_session_problem(line, include_connection_lost=False) != "competing_login":
            return
        if not self._in_competing_window(now):
            return
        self._competing_page(self.competing.seen(now, RECORDER_LOG), now, send)

    def _in_competing_window(self, now: datetime) -> bool:
        window = after_notifier_competing_window(et_day(now))
        return window is not None and window[0] <= now < window[1]

    def _follow_competing(self, now: datetime, send: Send) -> None:
        if not self._in_competing_window(now):
            if self.competing.paged_at is not None or self.competing.hysteresis.state is not State.CLEAR:
                logger.info("overnight watch: the recorder's window is over; competing-login watch reset")
                self.competing.reset()
            return
        self._competing_page(self.competing.tick(now), now, send)

    def _competing_page(self, decision: CompetingDecision | None, now: datetime, send: Send) -> None:
        if decision is None:
            return
        if decision.kind is CompetingKind.CLEARED:
            logger.info("overnight watch: competing login over (since %s)", clock_text(decision.since, now))
            return
        if decision.kind is CompetingKind.OPENED:
            text = ibkr_alert_text("competing_login", decision.source)
        else:
            text = competing_login_repage_text(decision.since, decision.source)
        logger.warning("overnight watch: competing login (%s), paged", decision.kind.value)
        send(text)

    # -- the port --------------------------------------------------------------

    async def _follow_port(self, now: datetime, send: Send) -> None:
        if in_notifier_window(now):
            if not self._in_notifier_window:
                self._in_notifier_window = True
                window = notifier_window(et_day(now))
                assert window is not None
                logger.info(
                    "overnight watch: BrokerWatch owns the Gateway until %s", clock_text(window[1], now)
                )
                # A paged outage whose last check still failed: one more check, so a
                # port that reopened since gets its recovery line (N15b L-12).
                port_open = await self._probe() if self.port.owes_recovery else None
                self._send_all(self.port.handover(now, port_open), send)
            self._next_check = None
            return
        self._in_notifier_window = False
        self._take_over(now)
        lead = self._due_lead(now)
        if lead is not None:
            port_open = await self._probe()
            if port_open is None:
                self._done_leads.discard(lead)  # retried at the next step, within LEAD_GRACE
            else:
                logger.info(
                    "overnight watch: %s ET lead, Gateway port %s",
                    clock_text(lead, now),
                    "open" if port_open else "closed",
                )
                self._send_all(self.port.lead(now, port_open, lead_at=lead), send)
                if port_open and self._lead_check is not None:
                    text = await self._lead_check(now, lead)
                    if text:
                        send(text)
                self._schedule_check(now)
        elif self._next_check is None or now >= self._next_check:
            port_open = await self._probe()
            if port_open is not None:
                self._send_all(self.port.check(now, port_open), send)
            self._schedule_check(now)
        self._send_all(self.port.tick(now), send)

    def _take_over(self, now: datetime) -> None:
        """Once a session day, after the notifier's window: its announced outage is
        the watch's evidence, so a closed port is never "closed since 16:15"."""
        day = et_day(now)
        window = notifier_window(day)
        if window is None or now < window[1] or self._memory.get(TOOK_OVER_KEY) == day.isoformat():
            return
        self._memory[TOOK_OVER_KEY] = day.isoformat()
        if self._notifier_outage is None or self.port.record() is not None:
            return
        try:
            known = self._notifier_outage(day)
        except Exception as exc:  # noqa: BLE001 - no evidence: "since before HH:MM" instead
            logger.warning("overnight watch: the notifier's Gateway state unreadable (%s)", type(exc).__name__)
            return
        if known is not None:
            logger.info(
                "overnight watch: the notifier's Gateway outage since %s (%s) carried over",
                clock_text(known.since, now), "paged" if known.paged else "not paged",
            )
            self.port.carry(known)

    def _schedule_check(self, now: datetime) -> None:
        # A lead's first failure is confirmed as soon as the hysteresis can open on it.
        wait = self.port.hysteresis.open_after if self.port.lead_pending else PORT_CHECK_INTERVAL
        self._next_check = now + wait

    def _due_lead(self, now: datetime) -> datetime | None:
        due: datetime | None = None
        for lead in pre_session_leads(et_day(now)):
            if lead in self._done_leads or lead > now:
                continue
            self._done_leads.add(lead)
            if now - lead <= LEAD_GRACE:
                due = lead
        return due

    async def _probe(self) -> bool | None:
        try:
            # Not wait_for: on 3.11 it can drop a stop that lands as the probe returns.
            async with asyncio.timeout(PROBE_DEADLINE_S):
                return bool(await self._probe_fn())
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - an unknown port is neither open nor closed
            logger.warning("overnight watch: port check failed (%s); skipped", type(exc).__name__)
            return None

    def _send_all(self, texts: list[str], send: Send) -> None:
        for text in texts:
            logger.info("overnight watch: %s", text.splitlines()[0])
            send(text)

    def _wait_seconds(self, now: datetime) -> float:
        wait = self._tick
        moments = [lead for lead in pre_session_leads(et_day(now)) if lead > now]
        if self._next_check is not None and self._next_check > now:
            moments.append(self._next_check)
        for moment in moments:
            wait = min(wait, (moment - now).total_seconds())
        return max(MIN_WAIT_SECONDS, wait)


__all__ = [
    "LEAD_GRACE",
    "OUTAGE_BEFORE_KEY",
    "OUTAGE_PAGED_KEY",
    "OUTAGE_SINCE_KEY",
    "TOOK_OVER_KEY",
    "LeadCheck",
    "NotifierOutage",
    "OvernightWatch",
    "PortProbe",
    "RestartCapGate",
    "WATCH_TICK_SECONDS",
    "notifier_port_outage",
]

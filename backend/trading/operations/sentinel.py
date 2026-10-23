"""The Sentinel: the risk-reducing auto-fence (T-9; OPERATOR_PROMPT Part 8.4).

AGENTS.md: AI may only narrow, and orders come only from deterministic code.
The Sentinel is not AI and not an order path. It is deterministic code with
the thresholds the owner accepted on 2026-10-15 (the constants below), and it
can do exactly one thing: STOP one sendable identity by writing the row
``paper:<IDENTITY>`` stopped=1 through ``SQLitePaperKillStore.stop``. It never
resumes a row (resume is the owner's: ``autotraderctl paper resume
--identity``), never writes the global ``paper`` row, and never sends,
modifies or cancels an order. The pinned bridge reads the identity row before
every entry (v7a), so a fence blocks new entries only: exits, stops, targets
and the 15:45 flatten keep working.

It reads STRUCTURED state only, never log lines, so a log-format change cannot
silently disable it:
  * today's bridge state ``<data>/bridge/<YYYY-MM-DD>.json`` (the pinned
    bridge's StateStore: brackets with ``strategy_name``, ``status``, ``qty``,
    ``entry``, ``stop``, ``realized_pnl``, ``sent_at``);
  * the research live ledger (``trading.research.live_ledger``) over every
    session file, for streaks across sessions;
  * the bridge heartbeat row (``autotrader_bridge_heartbeat``), for freshness
    only: the bridge rewrites its state file only when a bracket changes, so
    on a quiet day the file's mtime is no heartbeat, but the heartbeat row is
    written every bridge tick (every 2 s) while the manual-order desk runs.

Triggers (each fences the identity once per day; the first to fire wins):
  * daily loss: today's realized loss, in R, of one identity is more than
    ``max_entries_per_day x 1 R x 1.25`` for a capped identity (ORB_V2 and
    GAP_GO_V2: 1.25 R; VWAP_PB_V2: 2.5 R), or at least 2 R for MOMO_HOD_V2
    (no entries cap). Each bracket's realized P&L is divided by its own R,
    ``qty x (entry - stop)``, and the identity's brackets are summed (a win
    offsets a loss: the rule is about the day's realized loss).
  * losing streak: the identity's last 4 (or more) closed trades across
    sessions, in entry order (``sent_at``), all lost (net R after costs, as
    the live ledger computes it), and the latest of them closed today. A
    streak whose last loss is older does not fire again, so an owner's resume
    holds until the next loss.
  * a live/shadow decision mismatch: raised by the nightly replay job itself
    (``trading.research.nightly``; the notifier is not running then) with the
    same store, the same ``fence_identity`` and the reason grammar
    ``replay_mismatch_reason``.
  * NOT implemented: an unattributed fill (an execution whose orderRef starts
    ``autotrader:`` that matches no bracket). The bridge state records only
    each bracket's aggregated fills (``filled``/``avg_entry``/``exit_filled``/
    ``avg_exit`` for its own leg ids); executions, with their order ids and
    orderRefs, live only in the bridge process's memory and are never written
    to any structured state. It cannot be detected without reading log lines.

Log-only first (owner, 2026-10-17), live since P1-B.4: ``SENTINEL_FENCES_LIVE``
shipped False for the first session(s) (P1-B, installed 2026-10-18) and is True
since the owner, after reviewing the 🔎 lines, agreed through a reviewed
one-line change of that constant (never an environment variable or a setting).
The switch is read inside ``fence_identity``, the one write path, so both
writers obey it: the Sentinel's triggers and the nightly replay-mismatch hook.
Log-only (False), ``fence_identity`` never calls the store: it logs ``sentinel
would fence <ID>: <trigger>`` and returns ``WOULD_FENCE``, and the phone gets
``🔎 auto-fence (log-only): would stop <ID> — <trigger>`` once per identity per
day (its own memory key). Live (True), every row, reason and text is as it was
before the switch existed.

No-limit PAPER test L-T1 (owner, 2026-10-23; ``trading.risk.no_limit_test``):
for the test window the switch is False again (the Sentinel logs and pages
"would stop", it writes no row) and its caps mirror follows the bridge's
uncapped table (every identity ``None``: the daily-loss trigger is the
uncapped ``UNCAPPED_LOSS_R`` for all four). An import tripwire refuses either
value while it disagrees with the mode; ``deploy undo`` restores v7c's.

A Sentinel that cannot see is loud, and never fences: inside the bridge's own
window (09:25 ET to 5 minutes before the close) an unreadable state file, no
state file 5 minutes into the window, or no update (state file or heartbeat)
for 5 minutes sends ``sentinel blind since HH:MM ET`` at most once per 30
minutes, and no fence is written while blind. One "sees again" line follows
once it can read again.

Blindness is judged by the notifier that runs this code, on the same Mac as
the bridge: a Mac that sleeps (live 2026-10-15: lid closed on battery, no
bridge state written 14:47-16:12 ET) stops the notifier with the bridge, so
nothing here can page while the machine sleeps. The external dead-man's
switch (T-3) covers a sleeping or dead Mac. On wake, a check still inside the
window reports "blind since" the last update, and the 16:05 report's Close
line shows the bridge not flat at the 15:50 check and a flatten that ran late.

Its memory (what it fenced today, when it last said "blind") is a mapping the
caller persists per session date (the notifier's per-date state), so a
restart neither fences twice nor overrides an owner's resume the same day.
The fence table holds the same facts on its own (N15a LOW-5), so a lost or
unreadable state file cannot undo an owner's decision either:
  * the stop is one transaction (``SQLitePaperKillStore.stop_identity``): a row
    that is stopped already (an owner's /halt, an earlier fence) is kept with
    its reason, and an owner's stop or resume cannot land between the check
    and the write;
  * an owner's resume of a Sentinel fence stores the fence's ET date in its
    reason (``owner_resume_reason``, the CLI's ``paper resume``), and a row the
    owner resumed today from today's fence is never fenced again that day
    (``owner_resumed_on``).
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable, Mapping, MutableMapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from functools import partial
import json
import logging
import os
from pathlib import Path
import re
import stat
from types import MappingProxyType
from typing import Any, Protocol

from trading.research.live_ledger import STATE_SCHEMA_PREFIX, LiveLedger, LiveTrade, read_live_ledger
from trading.scheduler.sessions import MARKET_TIMEZONE, market_session
from trading.risk.no_limit_test import NO_LIMIT_TEST, NO_LIMIT_TEST_LABEL
from trading.sendable_identities import SENDABLE_IDENTITIES
from trading.storage.runtime_fence import FenceRow, KeepRow

logger = logging.getLogger("autotrader.shadow.sentinel")

# -- owner-set thresholds (accepted 2026-10-15: OPERATOR_PROMPT 8.4, plan Appendix C) --------
# A capped identity is fenced when today's realized loss exceeds
# max_entries_per_day x 1 R x CAPPED_LOSS_FACTOR (strictly more).
CAPPED_LOSS_FACTOR = Decimal("1.25")
# MOMO_HOD_V2 has no entries cap: fenced at UNCAPPED_LOSS_R or more.
UNCAPPED_LOSS_R = Decimal("2")
# Consecutive losing trades across sessions (owner, 2026-10-17: 4; it was 3).
LOSING_STREAK = 4
# Blindness: no evidence for this long inside the window; repeated at most this often.
BLIND_AFTER = timedelta(minutes=5)
BLIND_REPEAT = timedelta(minutes=30)
# A fence that could not be written is re-paged at most this often (retried every check).
FENCE_FAILED_REPEAT = timedelta(minutes=30)
# The bridge child's own window (supervisor BRIDGE_START, BRIDGE_FINISH_BEFORE_CLOSE;
# a test pins them equal): blindness is only judged while the bridge should be running.
WATCH_FROM = time(9, 25)
WATCH_UNTIL_BEFORE_CLOSE = timedelta(minutes=5)
# max_entries_per_day per sendable identity, as the pinned bridge's IDENTITY_CAPS
# has them (a test loads the bridge and pins the two equal; None = uncapped).
# No-limit test L-T1: the bridge's rows are uncapped (Caps(None, None, offset)),
# so every row here is None (v7c's were MOMO None, ORB 1, VWAP 2, GAP 1).
MAX_ENTRIES_PER_DAY: Mapping[str, int | None] = MappingProxyType(
    {"MOMO_HOD_V2": None, "ORB_V2": None, "VWAP_PB_V2": None, "GAP_GO_V2": None}
)
# A sendable identity without a row above (a test refuses that) is held to the
# tightest capped limit: risk-reducing, never wider.
_MISSING_CAPS_ENTRIES = 1
MAX_STATE_BYTES = 5 * 1024 * 1024

# -- the owner's switch (2026-10-17) -----------------------------------------------------
# It shipped False, log-only, for its first session(s) (P1-B, 2026-10-18 install:
# no fence row ever written; WOULD_FENCE and one 🔎 line per identity per day
# instead). It went live in P1-B.4, on the owner's decision after reviewing the
# 🔎 WOULD_FENCE lines: True writes the ``paper:<IDENTITY>`` stop row. It changes
# only through a reviewed one-line change of this constant, never otherwise.
# No-limit test L-T1 (owner, 2026-10-23): False again for the test window, the
# Sentinel logs only ("Sentinel (logs only)", plan v2 item 2); deploy undo
# restores v7c's True.
SENTINEL_FENCES_LIVE = False

# Mode on: log-only and uncapped; mode off: live with v7c's caps. A half-on build
# (one of the two values left behind) cannot start.
_UNCAPPED = all(entries is None for entries in MAX_ENTRIES_PER_DAY.values())
if not ((not SENTINEL_FENCES_LIVE and _UNCAPPED) if NO_LIMIT_TEST else (SENTINEL_FENCES_LIVE and not _UNCAPPED)):
    raise RuntimeError(
        f"no-limit test {NO_LIMIT_TEST_LABEL} is {'on' if NO_LIMIT_TEST else 'off'} but the Sentinel is"
        f" {'live' if SENTINEL_FENCES_LIVE else 'log-only'} with caps {dict(MAX_ENTRIES_PER_DAY)}"
    )

REASON_PREFIX = "sentinel: "
DAILY_LOSS = "daily loss"
LOSING_STREAK_TRIGGER = "losing streak"
REPLAY_MISMATCH = "replay mismatch"
# Memory keys (values: ISO timestamps).
FENCED_KEY = "sentinel_fence:{identity}"
FENCE_FAILED_KEY = "sentinel_fence_failed:{identity}"
# Log-only: the 🔎 line was sent for this identity today.
WOULD_FENCE_KEY = "sentinel_would_fence:{identity}"
BLIND_KEY = "sentinel_blind"
# Alert names for the caller's "alert queued" log line.
FENCE_ALERT = "sentinel_fence"
WOULD_FENCE_ALERT = "sentinel_would_fence"
BLIND_ALERT = "sentinel_blind"
SEES_AGAIN_ALERT = "sentinel_sees_again"

FENCED = "fenced"
ALREADY_FENCED = "already_fenced"
# The owner resumed today's Sentinel fence today: the row is kept, never fenced again.
OWNER_RESUMED = "owner_resumed"
# Log-only (SENTINEL_FENCES_LIVE is False): judged a fence, wrote nothing.
WOULD_FENCE = "would_fence"
# The trace an owner's resume of a Sentinel fence leaves in the row's reason.
LIFTED_SUFFIX = " (lifts sentinel fence of {day})"
_LIFTED = re.compile(r" \(lifts sentinel fence of (?P<day>\d{4}-\d{2}-\d{2})\)$")

_CENT = Decimal("0.01")
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


class FenceStore(Protocol):
    """The one call the Sentinel may make (``SQLitePaperKillStore`` satisfies it):
    stop one identity row, in one transaction, unless it is stopped already or
    ``unless(row)`` keeps it; None when it stopped it, else the row it kept."""

    async def stop_identity(
        self, *, reason: str, identity: str, unless: KeepRow | None = None
    ) -> FenceRow | None: ...


HeartbeatReader = Callable[[], Awaitable[datetime | None]]
LedgerReader = Callable[[Path], LiveLedger]


# --------------------------------------------------------------------------
# The one fence write (Sentinel and nightly replay)
# --------------------------------------------------------------------------


def replay_mismatch_reason(count: int) -> str:
    """The fence reason the nightly replay writes for ``count`` mismatched decisions."""
    return f"{REASON_PREFIX}{REPLAY_MISMATCH} {count} decisions"


def resume_command(identity: str) -> str:
    return f"autotraderctl paper resume --identity {identity}"


def owner_resume_reason(reason: str, lifted: FenceRow | None) -> str:
    """The reason an owner's resume stores. Lifting a Sentinel fence, it ends with
    that fence's ET date (``LIFTED_SUFFIX``): the trace ``owner_resumed_on`` reads."""
    if lifted is None or not lifted.stopped or not lifted.reason.startswith(REASON_PREFIX):
        return reason
    fenced_at = _parse_time(lifted.updated_at)
    if fenced_at is None:
        return reason
    return reason + LIFTED_SUFFIX.format(day=fenced_at.astimezone(MARKET_TIMEZONE).date().isoformat())


def owner_resumed_on(row: FenceRow, day: date) -> bool:
    """The row is the owner's resume, written on ``day`` (ET), of a Sentinel fence of ``day``."""
    if row.stopped:
        return False
    resumed_at = _parse_time(row.updated_at)
    if resumed_at is None or resumed_at.astimezone(MARKET_TIMEZONE).date() != day:
        return False
    found = _LIFTED.search(row.reason)
    return found is not None and found.group("day") == day.isoformat()


async def fence_identity(
    store: FenceStore, *, identity: str, reason: str, resumed_on: date | None = None
) -> str:
    """Stop one sendable identity's new entries: ``FENCED``; ``ALREADY_FENCED``
    when its row is stopped already (an owner's or an earlier reason is kept);
    ``OWNER_RESUMED`` when ``resumed_on`` is given and the owner resumed that
    day's Sentinel fence that day. Check and stop are one transaction.

    Log-only (``SENTINEL_FENCES_LIVE`` is False): ``WOULD_FENCE``, after one
    log line, and the store is never called, so no row can be written by
    either writer (the Sentinel, the nightly replay hook).

    Only an exact sendable identity reaches the store, so the global ``paper``
    row (``identity=None``) can never be written from here, and nothing here
    can resume.
    """
    if not isinstance(identity, str) or identity not in SENDABLE_IDENTITIES:
        raise ValueError(f"not a sendable identity: {identity!r}")
    if not reason.startswith(REASON_PREFIX):
        raise ValueError("an auto-fence reason starts with 'sentinel: '")
    if not SENTINEL_FENCES_LIVE:
        logger.warning("sentinel would fence %s: %s", identity, reason[len(REASON_PREFIX):])
        return WOULD_FENCE
    unless = None if resumed_on is None else partial(owner_resumed_on, day=resumed_on)
    kept = await store.stop_identity(reason=reason, identity=identity, unless=unless)
    if kept is None:
        return FENCED
    return ALREADY_FENCED if kept.stopped else OWNER_RESUMED


# --------------------------------------------------------------------------
# Small value helpers
# --------------------------------------------------------------------------


def _decimal(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() else None


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None and parsed.utcoffset() is not None else None


def _hhmm(moment: datetime) -> str:
    return moment.astimezone(MARKET_TIMEZONE).strftime("%H:%M")


def _money(value: Decimal) -> str:
    cents = value.quantize(_CENT)
    sign = "-" if cents < 0 else "+" if cents > 0 else ""
    return f"{sign}${abs(cents):,.2f}"


def _r(value: Decimal) -> str:
    return f"{value.quantize(_CENT)} R"


def _limit_text(value: Decimal) -> str:
    return format(value.normalize(), "f")


# --------------------------------------------------------------------------
# Today's bridge state
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BridgeStateReading:
    """Today's state file: its brackets (readable), a problem (unreadable), or
    neither (missing). ``signature`` changes whenever the bridge rewrites it."""

    submitted: Mapping[str, Mapping[str, Any]] | None = None
    modified_at: datetime | None = None
    problem: str | None = None
    signature: tuple[int, int] | None = None

    @property
    def readable(self) -> bool:
        return self.submitted is not None

    @property
    def missing(self) -> bool:
        return self.submitted is None and self.problem is None


def state_path(directory: Path, session_date: date) -> Path:
    return directory / f"{session_date.isoformat()}.json"


def read_bridge_state(path: Path, session_date: date) -> BridgeStateReading:
    """One bridge state file, read-only: a regular file (never through a link),
    under the size limit, the bridge's schema, this session."""
    try:
        if stat.S_ISLNK(path.lstat().st_mode):
            return BridgeStateReading(problem="bridge state is a symbolic link")
        with path.open("rb") as stream:
            status = os.fstat(stream.fileno())
            modified = datetime.fromtimestamp(status.st_mtime, timezone.utc)
            signature = (status.st_mtime_ns, status.st_size)
            if not stat.S_ISREG(status.st_mode):
                return BridgeStateReading(modified_at=modified, problem="bridge state is not a file")
            raw = stream.read(MAX_STATE_BYTES + 1)
    except FileNotFoundError:
        return BridgeStateReading()
    except OSError as exc:
        return BridgeStateReading(problem=f"bridge state unreadable ({type(exc).__name__})")

    def bad(why: str) -> BridgeStateReading:
        return BridgeStateReading(modified_at=modified, problem=why, signature=signature)

    if len(raw) > MAX_STATE_BYTES:
        return bad("bridge state too large")
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        return bad(f"bridge state unreadable ({type(exc).__name__})")
    if not isinstance(document, dict):
        return bad("bridge state is not a state document")
    schema = document.get("schema")
    if not isinstance(schema, str) or not schema.startswith(STATE_SCHEMA_PREFIX):
        return bad("bridge state has an unknown schema")
    if document.get("session_date", session_date.isoformat()) != session_date.isoformat():
        return bad("bridge state holds another session")
    submitted = document.get("submitted")
    if not isinstance(submitted, dict):
        return bad("bridge state has no submitted map")
    brackets = {str(key): item for key, item in submitted.items() if isinstance(item, dict)}
    return BridgeStateReading(
        submitted=MappingProxyType(brackets), modified_at=modified, signature=signature
    )


# --------------------------------------------------------------------------
# Triggers (pure)
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Trigger:
    identity: str
    name: str
    numbers: str

    @property
    def text(self) -> str:
        return f"{self.name} {self.numbers}"

    @property
    def reason(self) -> str:
        return f"{REASON_PREFIX}{self.text}"


def loss_limit(identity: str) -> tuple[Decimal, bool]:
    """``(limit in R, inclusive)``: a capped identity trips strictly above
    ``entries x 1.25``, the uncapped one at ``UNCAPPED_LOSS_R`` or more."""
    entries = MAX_ENTRIES_PER_DAY.get(identity, _MISSING_CAPS_ENTRIES)
    if entries is None:
        return UNCAPPED_LOSS_R, True
    return Decimal(entries) * CAPPED_LOSS_FACTOR, False


def _identity_of(item: Mapping[str, Any]) -> str:
    return str(item.get("strategy_name") or "").strip()


def _traded(item: Mapping[str, Any]) -> bool:
    return "qty" in item and not item.get("skipped") and item.get("status") != "skipped"


def bracket_r(item: Mapping[str, Any]) -> Decimal | None:
    """One bracket's R in dollars, ``qty x (entry - stop)``; None when not a long risk."""
    quantity = item.get("qty")
    if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
        return None
    entry, stop = _decimal(item.get("entry")), _decimal(item.get("stop"))
    if entry is None or stop is None or entry <= stop:
        return None
    return Decimal(quantity) * (entry - stop)


def daily_loss(
    identity: str, submitted: Mapping[str, Mapping[str, Any]]
) -> tuple[Decimal, Decimal] | None:
    """``(loss in R, realized $)`` today for ``identity``; None when there is
    realized P&L but no bracket of the identity has a usable R."""
    mine = [item for item in submitted.values() if _identity_of(item) == identity and _traded(item)]
    known = [value for value in (bracket_r(item) for item in mine) if value is not None]
    # A bracket without a usable R (never seen from this bridge) is measured
    # with the identity's largest R today: the more lenient unit, never a guess
    # that fences on a smaller one.
    fallback = max(known) if known else None
    total_r = Decimal("0")
    realized = Decimal("0")
    for item in mine:
        pnl = _decimal(item.get("realized_pnl"))
        if pnl is None or pnl == 0:
            continue
        unit = bracket_r(item) or fallback
        if unit is None:
            return None
        realized += pnl
        total_r += pnl / unit
    return -total_r, realized


def loss_trigger(identity: str, loss: Decimal, realized: Decimal) -> Trigger | None:
    """The daily-loss trigger for a measured loss (in R), else None."""
    limit, inclusive = loss_limit(identity)
    if not (loss >= limit if inclusive else loss > limit):
        return None
    sign = "≥" if inclusive else ">"
    return Trigger(
        identity=identity,
        name=DAILY_LOSS,
        numbers=f"{_r(loss)} {sign} {_limit_text(limit)} R (realized {_money(realized)})",
    )


def daily_loss_trigger(
    identity: str, submitted: Mapping[str, Mapping[str, Any]]
) -> Trigger | None:
    measured = daily_loss(identity, submitted)
    return None if measured is None else loss_trigger(identity, *measured)


def ordered_trades(
    trades: Iterable[LiveTrade],
    identity: str,
    sent_at: Mapping[tuple[date, str], datetime],
) -> list[LiveTrade]:
    """The identity's closed trades in entry order: session, then ``sent_at``."""

    def key(trade: LiveTrade) -> tuple[date, int, datetime, str]:
        sent = sent_at.get((trade.session_date, trade.decision_id))
        return (trade.session_date, 0 if sent is None else 1, sent or _EPOCH, trade.decision_id)

    return sorted((trade for trade in trades if trade.strategy == identity), key=key)


def losing_streak_trigger(
    identity: str, ordered: Sequence[LiveTrade], today: date
) -> Trigger | None:
    """The trailing run of losing trades, when it is at least ``LOSING_STREAK``
    long and its latest loss closed today."""
    tail: list[LiveTrade] = []
    for trade in reversed(ordered):
        if trade.net_r >= 0:
            break
        tail.append(trade)
    tail.reverse()
    if len(tail) < LOSING_STREAK or tail[-1].session_date != today:
        return None
    shown = ", ".join(_r(trade.net_r) for trade in tail[-5:])
    more = "…, " if len(tail) > 5 else ""
    return Trigger(
        identity=identity,
        name=LOSING_STREAK_TRIGGER,
        numbers=f"{len(tail)} losing trades in a row ({more}{shown} net)",
    )


def _sent_at_index(directory: Path, sessions: Iterable[date]) -> dict[tuple[date, str], datetime]:
    index: dict[tuple[date, str], datetime] = {}
    for day in sorted(set(sessions)):
        reading = read_bridge_state(state_path(directory, day), day)
        if reading.submitted is None:
            continue
        for decision_id, item in reading.submitted.items():
            sent = _parse_time(item.get("sent_at"))
            if sent is not None:
                index[(day, decision_id)] = sent
    return index


def streak_triggers(directory: Path, today: date, read_ledger: LedgerReader) -> tuple[Trigger, ...]:
    """Every sendable identity's losing-streak trigger, from the live ledger."""
    ledger = read_ledger(directory)
    if ledger.unreadable:
        logger.warning("sentinel: live ledger could not read %s", ", ".join(ledger.unreadable[:5]))
    mine = [trade for trade in ledger.trades if trade.strategy in SENDABLE_IDENTITIES]
    index = _sent_at_index(directory, (trade.session_date for trade in mine))
    found = []
    for identity in SENDABLE_IDENTITIES:
        trigger = losing_streak_trigger(identity, ordered_trades(mine, identity, index), today)
        if trigger is not None:
            found.append(trigger)
    return tuple(found)


# --------------------------------------------------------------------------
# Phone texts
# --------------------------------------------------------------------------


def fence_text(trigger: Trigger) -> str:
    return (
        f"⛔ auto-fence {trigger.identity}: {trigger.text} — new entries blocked;"
        f" resume: {resume_command(trigger.identity)}"
    )


def already_fenced_text(trigger: Trigger) -> str:
    return (
        f"⛔ auto-fence {trigger.identity}: {trigger.text} — new entries already blocked"
        f" (fenced earlier; its reason is kept); resume: {resume_command(trigger.identity)}"
    )


def would_fence_text(trigger: Trigger) -> str:
    return f"🔎 auto-fence (log-only): would stop {trigger.identity} — {trigger.text}"


def fence_failed_text(trigger: Trigger, problem: str) -> str:
    return (
        f"⚠️ auto-fence {trigger.identity} NOT written ({problem}): {trigger.text} — block it by hand:"
        f' autotraderctl paper stop --identity {trigger.identity} --reason "{trigger.reason}"'
    )


def blind_text(since: datetime, why: str) -> str:
    return f"⚠️ sentinel blind since {_hhmm(since)} ET — {why}; no auto-fence while it cannot see"


def sees_again_text(now: datetime) -> str:
    mode = "active" if SENTINEL_FENCES_LIVE else "log-only"
    return f"✅ sentinel sees the bridge state again ({_hhmm(now)} ET); auto-fence {mode}"


# --------------------------------------------------------------------------
# The Sentinel
# --------------------------------------------------------------------------


SentinelMessage = tuple[str, str]  # (alert name, phone text)


class Sentinel:
    """Run ``check(now)`` every few seconds during the session; send what it returns."""

    def __init__(
        self,
        *,
        session_date: date,
        state_directory: Path,
        memory: MutableMapping[str, str],
        store: FenceStore,
        read_heartbeat: HeartbeatReader,
        read_ledger: LedgerReader = read_live_ledger,
    ) -> None:
        self._session_date = session_date
        self._directory = state_directory
        self._path = state_path(state_directory, session_date)
        self._memory = memory
        self._store = store
        self._read_heartbeat = read_heartbeat
        self._read_ledger = read_ledger
        self._streaks: tuple[Trigger, ...] = ()
        self._streak_signature: tuple[int, int] | None = None
        self._unmeasurable: set[str] = set()
        # The state file as the last check read it (the caller's report reuses it).
        self.last_reading: BridgeStateReading | None = None
        session = market_session(session_date)
        self._window: tuple[datetime, datetime] | None = None
        if session is not None:
            start = datetime.combine(session_date, WATCH_FROM, tzinfo=MARKET_TIMEZONE)
            self._window = (start, session.closes_at - WATCH_UNTIL_BEFORE_CLOSE)

    async def check(self, now: datetime) -> list[SentinelMessage]:
        reading = await asyncio.to_thread(read_bridge_state, self._path, self._session_date)
        self.last_reading = reading
        inside = self._window is not None and self._window[0] <= now < self._window[1]
        blind = await self._blindness(now, reading) if inside else None
        if blind is not None:
            since, why = blind
            if self._due(BLIND_KEY, now, BLIND_REPEAT):
                logger.warning("sentinel blind since %s ET: %s", _hhmm(since), why)
                return [(BLIND_ALERT, blind_text(since, why))]
            return []
        if reading.submitted is None:
            return []  # nothing traded yet (before the bridge), or outside its window
        messages: list[SentinelMessage] = []
        # Only inside the window: after it nothing is judged, so nothing is "seen again".
        if inside and self._memory.pop(BLIND_KEY, None) is not None:
            logger.info("sentinel sees the bridge state again")
            messages.append((SEES_AGAIN_ALERT, sees_again_text(now)))
        for trigger in await self._triggers(reading):
            messages.extend(await self._fence(trigger, now))
        return messages

    # -- seeing -----------------------------------------------------------

    async def _blindness(
        self, now: datetime, reading: BridgeStateReading
    ) -> tuple[datetime, str] | None:
        """``(since, why)`` while the Sentinel cannot trust what it reads (inside the window)."""
        assert self._window is not None
        start = self._window[0]
        if reading.problem is not None:
            return max(reading.modified_at or start, start), reading.problem
        if reading.missing:
            if now - start < BLIND_AFTER:
                return None  # the bridge's first save follows its connect
            return start, "no bridge state file for today"
        heartbeat = await self._heartbeat()
        evidence = [moment for moment in (reading.modified_at, heartbeat) if moment is not None]
        last = max([start, *evidence])
        if now - last < BLIND_AFTER:
            return None
        minutes = int((now - last).total_seconds() // 60)
        return last, f"no bridge update for {minutes} min (state file and heartbeat)"

    async def _heartbeat(self) -> datetime | None:
        try:
            return await self._read_heartbeat()
        except Exception as exc:  # noqa: BLE001 - unknown freshness falls back to the file
            logger.warning("sentinel: bridge heartbeat unreadable (%s)", type(exc).__name__)
            return None

    # -- triggers -----------------------------------------------------------

    async def _triggers(self, reading: BridgeStateReading) -> list[Trigger]:
        assert reading.submitted is not None
        found: list[Trigger] = []
        for identity in SENDABLE_IDENTITIES:
            measured = daily_loss(identity, reading.submitted)
            if measured is None:
                if identity not in self._unmeasurable:
                    self._unmeasurable.add(identity)
                    logger.warning(
                        "sentinel: %s has realized P&L today but no bracket with a usable R", identity
                    )
                continue
            trigger = loss_trigger(identity, *measured)
            if trigger is not None:
                found.append(trigger)
        if reading.signature != self._streak_signature:
            # The ledger is re-read only when today's file changed: a new
            # closed trade is the only thing that can complete a streak today.
            try:
                self._streaks = await asyncio.to_thread(
                    streak_triggers, self._directory, self._session_date, self._read_ledger
                )
                self._streak_signature = reading.signature
            except Exception:  # noqa: BLE001 - the daily-loss rule still runs
                logger.exception("sentinel: live ledger failed; losing streaks not checked")
        found.extend(self._streaks)
        return found

    async def _fence(self, trigger: Trigger, now: datetime) -> list[SentinelMessage]:
        identity = trigger.identity
        done = FENCED_KEY.format(identity=identity)
        if done in self._memory:
            return []  # one fence per identity per day; an owner's resume holds
        would = WOULD_FENCE_KEY.format(identity=identity)
        if not SENTINEL_FENCES_LIVE and would in self._memory:
            return []  # log-only: one 🔎 line per identity per day, never one per check
        try:
            outcome = await fence_identity(
                self._store, identity=identity, reason=trigger.reason, resumed_on=self._session_date
            )
        except Exception as exc:  # noqa: BLE001 - paged, and retried at the next check
            logger.error("sentinel: fence %s NOT written (%s): %s", identity, type(exc).__name__, trigger.reason)
            if self._due(FENCE_FAILED_KEY.format(identity=identity), now, FENCE_FAILED_REPEAT):
                return [(FENCE_ALERT, fence_failed_text(trigger, type(exc).__name__))]
            return []
        if outcome == WOULD_FENCE:
            # Nothing was written: remembered under its own key, never as a fence.
            self._memory[would] = now.isoformat()
            return [(WOULD_FENCE_ALERT, would_fence_text(trigger))]
        self._memory[done] = now.isoformat()
        self._memory.pop(FENCE_FAILED_KEY.format(identity=identity), None)
        if outcome == OWNER_RESUMED:
            # The memory was lost (an unreadable state file); the table remembers.
            logger.warning("sentinel: %s not fenced again: the owner resumed today's fence; %s", identity, trigger.reason)
            return []
        if outcome == ALREADY_FENCED:
            logger.warning("sentinel: %s already fenced; %s", identity, trigger.reason)
            return [(FENCE_ALERT, already_fenced_text(trigger))]
        logger.critical("sentinel: fenced %s: %s", identity, trigger.reason)
        return [(FENCE_ALERT, fence_text(trigger))]

    def _due(self, key: str, now: datetime, interval: timedelta) -> bool:
        last = _parse_time(self._memory.get(key))
        if last is not None and now - last < interval:
            return False
        self._memory[key] = now.isoformat()
        return True


async def read_bridge_heartbeat_at() -> datetime | None:
    """The bridge heartbeat row's time, or None (never written, or no table yet)."""
    import aiosqlite

    from trading.execution.manual_orders import read_bridge_heartbeat

    try:
        heartbeat = await read_bridge_heartbeat()
    except aiosqlite.OperationalError:
        return None
    return None if heartbeat is None else heartbeat.updated_at


__all__ = [
    "ALREADY_FENCED",
    "BLIND_AFTER",
    "BLIND_ALERT",
    "BLIND_KEY",
    "BLIND_REPEAT",
    "CAPPED_LOSS_FACTOR",
    "DAILY_LOSS",
    "FENCED",
    "FENCE_ALERT",
    "LOSING_STREAK",
    "LOSING_STREAK_TRIGGER",
    "LIFTED_SUFFIX",
    "MAX_ENTRIES_PER_DAY",
    "OWNER_RESUMED",
    "REASON_PREFIX",
    "REPLAY_MISMATCH",
    "SENTINEL_FENCES_LIVE",
    "UNCAPPED_LOSS_R",
    "WATCH_FROM",
    "WATCH_UNTIL_BEFORE_CLOSE",
    "WOULD_FENCE",
    "WOULD_FENCE_ALERT",
    "WOULD_FENCE_KEY",
    "BridgeStateReading",
    "FenceStore",
    "HeartbeatReader",
    "Sentinel",
    "Trigger",
    "blind_text",
    "bracket_r",
    "daily_loss",
    "daily_loss_trigger",
    "fence_identity",
    "fence_text",
    "loss_limit",
    "loss_trigger",
    "losing_streak_trigger",
    "ordered_trades",
    "owner_resume_reason",
    "owner_resumed_on",
    "read_bridge_heartbeat_at",
    "read_bridge_state",
    "replay_mismatch_reason",
    "resume_command",
    "streak_triggers",
    "would_fence_text",
]

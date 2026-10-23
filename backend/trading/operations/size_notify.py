"""Size changes on the owner's phone (plan P3-E, owner decision D7).

The nightly size writer (``trading.research.size_writer``, in the replay
child) records each night's size proposals, automatic step-downs and
demotion fences in the size store (migration 18). This module turns them
into Telegram text, and it is all the two children share about it:

* the replay child sends ONE batched, silent message (``disable_notification``)
  with every step up waiting for the owner, every automatic step down and
  every demotion fence of the night, each proposal id at most once (the
  record ``telegram-proposals.json`` in the data directory, private, written
  atomically). The send itself is ``report_notify.notify_size_batch``, the
  replay child's one Telegram path;
* the Telegram child (``overnight_watch``) sends, for the session about to
  start, a NORMAL-sound reminder at the first pre-session lead (05:55 ET,
  ``gateway_watch.PRE_SESSION_LEADS``: two hours before the 07:55 ET
  deadline) when a proposal is still pending, and at the deadline a silent
  "expired, sizes unchanged" note naming each proposal nobody decided. Both
  only once phone approval exists: before it, "expired" is noise (P3-E fix 1).
  Step-downs need no answer: they are in the silent batch only.

Approval from the phone (``/approve``, the inline Approve/Reject buttons and
their ``callback_query`` handling) is P3-D and does not exist yet. Until it
ships, ``PHONE_APPROVAL_LIVE`` is False: the batch carries no buttons and no
"approve by" wording (it says the sizes stay unchanged and approval arrives
later), and no reminder or expiry note is sent, because nothing could be
done with either. P3-D flips the constant in its own reviewed change; a test pins it False until then.

Every schedule is anchored to America/New_York in code (the deadline is the
session's start, ``db_admin.trading_day``, 07:55 ET, the same instant as
``gateway_watch.session_start``); Israel time is only shown, computed with
``ZoneInfo("Asia/Jerusalem")`` at the moment of the deadline, never as a fixed
offset (the clock-change weeks: 25 Oct-1 Nov 2026, 14-26 Mar 2027, when Israel
is ET+6, not ET+7).

Everything here reads the size store read-only (``mode=ro``) and writes only
the delivery record; nothing here can change a size, a fence or an order.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, MutableMapping, Sequence
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import json
import logging
from pathlib import Path
import sqlite3
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

from trading.operations.gateway_watch import et_day, pre_session_leads, session_start
from trading.operations.paths import PathSecurityError, UserPaths, atomic_private_write
from trading.scheduler.sessions import MARKET_TIMEZONE
from trading.storage.migrations import SCHEMA_READ_TIMEOUT_SECONDS
from trading.storage.size_schema import (
    SHADOW_ONLY_MULT,
    SIZE_DECISIONS_TABLE,
    SIZE_EVENTS_TABLE,
    SIZE_PROPOSALS_TABLE,
    STEP_DOWN,
    STEP_UP,
)

logger = logging.getLogger("autotrader.shadow.sizes")

UTC = timezone.utc
# Plan P3-D (phone approval: /approve, the buttons, callback_query) is not
# built. Until its reviewed change flips this, nothing offers an approval.
PHONE_APPROVAL_LIVE = False
# Shown, never used for a schedule.
ISRAEL = ZoneInfo("Asia/Jerusalem")

PROPOSALS_STATE_FILENAME = "telegram-proposals.json"
STATE_SCHEMA = "autotrader-v1/size-proposals-delivered"
STATE_VERSION = 1
# Proposal ids remembered as delivered (newest kept): far more than a year of nights.
MAX_REMEMBERED = 2000
_STATE_MAX_BYTES = 256 * 1024

# The inline keyboard's callback data (P3-D answers it; at most 64 bytes).
APPROVE_CALLBACK = "size_approve:"
REJECT_CALLBACK = "size_reject:"

# Item kinds of the night's batch.
PROPOSAL = "proposal"
STEP_DOWN_ITEM = "step_down"
SHADOW_ONLY_ITEM = "shadow_only"
FENCED = "fenced"
WOULD_FENCE = "would_fence"

# The Telegram child's memory keys (the assistant's state file): the deadline
# (UTC ISO) whose reminder, and whose expiry note, were sent.
REMINDED_KEY = "size_proposals_reminded"
EXPIRED_KEY = "size_proposals_expired"
# The expiry note still goes after a Telegram child restart this soon after the deadline.
EXPIRY_NOTE_GRACE = timedelta(hours=1)

Deliver = Callable[[str, bool], object]  # (text, silent)


# --------------------------------------------------------------------------
# Text
# --------------------------------------------------------------------------


def deadline_text(moment: datetime) -> str:
    """``Mon 07:55 ET (14:55 Israel)``: the ET time, then the same instant in Israel."""
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError("the deadline must be timezone-aware")
    eastern = moment.astimezone(MARKET_TIMEZONE)
    israel = moment.astimezone(ISRAEL)
    shown = f"{israel:%H:%M}" if israel.date() == eastern.date() else f"{israel:%a %H:%M}"
    return f"{eastern:%a %H:%M} ET ({shown} Israel)"


def _times(text: str) -> str:
    return "shadow-only" if text == SHADOW_ONLY_MULT else f"×{text}"


def _nights(count: int) -> str:
    return f"evaluated {count} night{'' if count == 1 else 's'} since the last step"


@dataclass(frozen=True, slots=True)
class SizeItem:
    """One line of the night's batch, read back from the size store."""

    proposal_id: str
    identity: str
    kind: str  # PROPOSAL, STEP_DOWN_ITEM or SHADOW_ONLY_ITEM
    from_mult: str
    to_mult: str
    evidence: str
    nights: int
    deadline: datetime | None = None  # a proposal's
    effective_session: date | None = None  # a step down's
    decision: str | None = None  # a demotion fence's: fenced or would_fence

    def line(self) -> str:
        head = f"{self.identity} id {self.proposal_id}: {_times(self.from_mult)} → {_times(self.to_mult)}"
        evidence = f"; {self.evidence}" if self.evidence else ""
        if self.kind == PROPOSAL:
            due = "?" if self.deadline is None else deadline_text(self.deadline)
            if PHONE_APPROVAL_LIVE:
                ask = f"approve by {due}"
            else:
                ask = f"sizes stay unchanged: phone approval arrives later (this proposal expires {due})"
            return f"PROPOSAL {head}{evidence}; {_nights(self.nights)}; {ask}"
        if self.kind == STEP_DOWN_ITEM:
            session = "next" if self.effective_session is None else f"{self.effective_session:%a %Y-%m-%d}"
            return f"STEP DOWN {head} from the {session} session, automatic{evidence}; {_nights(self.nights)}"
        if self.decision == FENCED:
            done = (
                "fenced, its new entries are stopped (resume is yours:"
                f" autotraderctl paper resume --identity {self.identity})"
            )
        else:
            done = "would fence (the Sentinel is log-only): nothing written"
        return f"SHADOW-ONLY {head}: {done}{evidence}"


def batch_text(items: Sequence[SizeItem], *, night: date) -> str:
    """The night's one silent message: a title, then one line per item."""
    lines = [f"🌙 Sizes after the {night:%a %Y-%m-%d} session (silent; step-downs apply by themselves)"]
    lines += [item.line() for item in items]
    return "\n".join(lines)


def approval_markup(proposal_ids: Sequence[str]) -> dict[str, Any] | None:
    """The Approve/Reject inline keyboard, one row per proposal; None while
    phone approval does not exist (``PHONE_APPROVAL_LIVE``) or nothing waits."""
    if not PHONE_APPROVAL_LIVE or not proposal_ids:
        return None
    return {
        "inline_keyboard": [
            [
                {"text": f"Approve {pid}", "callback_data": f"{APPROVE_CALLBACK}{pid}"},
                {"text": f"Reject {pid}", "callback_data": f"{REJECT_CALLBACK}{pid}"},
            ]
            for pid in proposal_ids
        ]
    }


@dataclass(frozen=True, slots=True)
class PendingProposal:
    """A step up nobody decided yet."""

    proposal_id: str
    identity: str
    from_mult: str
    to_mult: str
    deadline: datetime

    def short(self) -> str:
        return f"{self.proposal_id} ({self.identity} {_times(self.from_mult)} → {_times(self.to_mult)})"


def reminder_text(pending: Sequence[PendingProposal], deadline: datetime) -> str:
    names = "; ".join(item.short() for item in pending)
    return (
        f"⏰ Size proposal{'s' if len(pending) > 1 else ''} waiting for your approval: {names}."
        f" Approve by {deadline_text(deadline)}, or the sizes stay unchanged."
    )


def expiry_text(expired: Sequence[PendingProposal], deadline: datetime) -> str:
    names = "; ".join(item.short() for item in expired)
    return f"⌛ Expired at {deadline_text(deadline)}, sizes unchanged: {names}"


# --------------------------------------------------------------------------
# The size store, read-only
# --------------------------------------------------------------------------


_NIGHT = f"""
    SELECT p.proposal_id, p.identity, p.kind, p.from_mult, p.to_mult, p.evidence_json,
           p.nights_since_last_step, p.deadline_at, d.decision, e.effective_session
    FROM {SIZE_PROPOSALS_TABLE} p
    LEFT JOIN {SIZE_DECISIONS_TABLE} d ON d.proposal_id = p.proposal_id
    LEFT JOIN {SIZE_EVENTS_TABLE} e ON e.proposal_id = p.proposal_id
    WHERE p.as_of = ?
    ORDER BY p.created_at, p.proposal_id
"""
_PENDING = f"""
    SELECT p.proposal_id, p.identity, p.from_mult, p.to_mult, p.deadline_at
    FROM {SIZE_PROPOSALS_TABLE} p
    LEFT JOIN {SIZE_DECISIONS_TABLE} d ON d.proposal_id = p.proposal_id
    WHERE p.kind = '{STEP_UP}' AND d.proposal_id IS NULL
    ORDER BY p.created_at, p.proposal_id
"""


def _connect_ro(database: Path) -> sqlite3.Connection:
    uri = f"file:{quote(str(database.resolve()))}?mode=ro"
    return sqlite3.connect(uri, uri=True, timeout=SCHEMA_READ_TIMEOUT_SECONDS)


def _has_store(db: sqlite3.Connection) -> bool:
    tables = {str(row[0]) for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    return {SIZE_PROPOSALS_TABLE, SIZE_DECISIONS_TABLE, SIZE_EVENTS_TABLE} <= tables


def _moment(value: object) -> datetime:
    moment = datetime.fromisoformat(str(value))
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError(f"{value!r} is not timezone-aware")
    return moment


def _evidence_text(raw: object) -> str:
    try:
        document = json.loads(str(raw))
    except ValueError:
        return ""
    text = document.get("text") if isinstance(document, dict) else None
    return text if isinstance(text, str) else ""


def night_items(database: str | Path, night: date) -> list[SizeItem]:
    """What the night ``night`` (the evaluated session) changed or proposed:
    every step up still pending, every applied step down, every demotion fence.
    A step up already decided (superseded, expired, ...) is no news. No
    database or no size store (migration 18 pending): nothing."""
    path = Path(database).expanduser()
    if not path.is_file():
        return []
    with closing(_connect_ro(path)) as db:
        if not _has_store(db):
            return []
        rows = db.execute(_NIGHT, (night.isoformat(),)).fetchall()
    items: list[SizeItem] = []
    for pid, identity, kind, origin, target, evidence, nights, deadline, decision, effective in rows:
        common = {
            "proposal_id": str(pid),
            "identity": str(identity),
            "from_mult": str(origin),
            "to_mult": str(target),
            "evidence": _evidence_text(evidence),
            "nights": int(nights),
        }
        if kind == STEP_UP and decision is None and deadline is not None:
            items.append(SizeItem(kind=PROPOSAL, deadline=_moment(deadline), **common))
        elif kind == STEP_DOWN and str(target) != SHADOW_ONLY_MULT and decision == "auto_applied" and effective:
            items.append(
                SizeItem(kind=STEP_DOWN_ITEM, effective_session=date.fromisoformat(str(effective)), **common)
            )
        elif kind == STEP_DOWN and str(target) == SHADOW_ONLY_MULT and decision in (FENCED, WOULD_FENCE):
            items.append(SizeItem(kind=SHADOW_ONLY_ITEM, decision=str(decision), **common))
    return items


def pending_proposals(database: str | Path, deadline: datetime) -> list[PendingProposal]:
    """The step ups whose deadline is ``deadline`` and that nobody decided yet.
    No database or no size store: none. Any other read error raises."""
    path = Path(database).expanduser()
    if not path.is_file():
        return []
    with closing(_connect_ro(path)) as db:
        if not _has_store(db):
            return []
        rows = db.execute(_PENDING).fetchall()
    pending: list[PendingProposal] = []
    for pid, identity, origin, target, due in rows:
        moment = _moment(due)
        if moment == deadline:
            pending.append(PendingProposal(str(pid), str(identity), str(origin), str(target), moment))
    return pending


# --------------------------------------------------------------------------
# The delivery record (the replay child's)
# --------------------------------------------------------------------------


def delivery_state_file(paths: UserPaths) -> Path:
    return paths.data_directory / PROPOSALS_STATE_FILENAME


def delivered_ids(path: Path) -> dict[str, str]:
    """Proposal id -> when its batch was delivered. An unreadable record reads as
    empty (logged): at worst one silent message twice, never a proposal lost."""
    if not path.exists() and not path.is_symlink():
        return {}
    try:
        if path.is_symlink() or not path.is_file():
            raise PathSecurityError("the size delivery record must be a regular file")
        with path.open("rb") as stream:
            raw = stream.read(_STATE_MAX_BYTES + 1)
        if len(raw) > _STATE_MAX_BYTES:
            raise ValueError("the size delivery record is larger than expected")
        data = json.loads(raw.decode("utf-8"))
    except (OSError, ValueError, PathSecurityError) as exc:
        logger.warning("ignoring %s: %s", path.name, type(exc).__name__)
        return {}
    sent = data.get("sent") if isinstance(data, dict) else None
    if (
        not isinstance(data, dict)
        or data.get("schema") != STATE_SCHEMA
        or data.get("version") != STATE_VERSION
        or not isinstance(sent, dict)
    ):
        logger.warning("ignoring %s: unexpected content", path.name)
        return {}
    return {str(key): str(value) for key, value in sent.items() if isinstance(value, str)}


def record_delivered(path: Path, proposal_ids: Iterable[str], at: datetime) -> None:
    """Add ``proposal_ids`` to the record (0600, written atomically), newest kept."""
    sent = delivered_ids(path)
    stamp = at.astimezone(UTC).isoformat()
    for pid in proposal_ids:
        sent[pid] = stamp
    if len(sent) > MAX_REMEMBERED:
        sent = dict(sorted(sent.items(), key=lambda item: item[1])[-MAX_REMEMBERED:])
    payload = {"schema": STATE_SCHEMA, "version": STATE_VERSION, "sent": sent}
    atomic_private_write(path, json.dumps(payload, sort_keys=True).encode("utf-8"))


def unsent_items(database: str | Path, night: date, record: Path) -> list[SizeItem]:
    """The night's items whose proposal id was never delivered."""
    sent = delivered_ids(record)
    return [item for item in night_items(database, night) if item.proposal_id not in sent]


# --------------------------------------------------------------------------
# The Telegram child's reminder and expiry note
# --------------------------------------------------------------------------


class ProposalWatch:
    """For the session about to start (ET-anchored, session days only): the
    reminder at the first pre-session lead and the expiry note at the deadline.

    ``deliver(text, silent)`` queues on the Telegram child's one sender; the
    memory is the overnight watch's (persisted in the assistant's state file),
    so a restart neither repeats nor drops a note.
    """

    def __init__(self, *, database: str | Path, deliver: Deliver) -> None:
        self._database = Path(database)
        self._deliver = deliver

    @staticmethod
    def schedule(now: datetime) -> tuple[datetime, datetime] | None:
        """``(reminder, deadline)`` of ``now``'s ET day; None on a closed day."""
        leads = pre_session_leads(et_day(now))
        if not leads:
            return None
        return leads[0], session_start(et_day(now))

    def moments(self, now: datetime) -> list[datetime]:
        """The instants the watch must wake for (its loop sleeps until the next)."""
        planned = self.schedule(now)
        return [] if planned is None else list(planned)

    def step(self, now: datetime, memory: MutableMapping[str, str]) -> None:
        planned = self.schedule(now)
        if planned is None:
            return
        reminder_at, deadline = planned
        stamp = deadline.astimezone(UTC).isoformat()
        if reminder_at <= now < deadline:
            if not PHONE_APPROVAL_LIVE or memory.get(REMINDED_KEY) == stamp:
                return
            pending = pending_proposals(self._database, deadline)
            if pending:
                logger.info("size proposals pending at the lead: %s", ", ".join(p.proposal_id for p in pending))
                self._deliver(reminder_text(pending, deadline), False)
                memory[REMINDED_KEY] = stamp
        elif deadline <= now < deadline + EXPIRY_NOTE_GRACE:
            # Before phone approval exists every proposal "expires": noise, not news.
            if not PHONE_APPROVAL_LIVE or memory.get(EXPIRED_KEY) == stamp:
                return
            expired = pending_proposals(self._database, deadline)
            if expired:
                logger.info("size proposals expired: %s", ", ".join(p.proposal_id for p in expired))
                self._deliver(expiry_text(expired, deadline), True)
            memory[EXPIRED_KEY] = stamp


def proposal_ids(items: Sequence[SizeItem]) -> list[str]:
    """The ids of the items that wait for the owner (the keyboard's rows)."""
    return [item.proposal_id for item in items if item.kind == PROPOSAL]


def describe(items: Sequence[SizeItem]) -> Mapping[str, int]:
    """Counts per kind, for a log line."""
    counts: dict[str, int] = {}
    for item in items:
        counts[item.kind] = counts.get(item.kind, 0) + 1
    return counts


__all__ = [
    "APPROVE_CALLBACK",
    "EXPIRED_KEY",
    "EXPIRY_NOTE_GRACE",
    "ISRAEL",
    "PHONE_APPROVAL_LIVE",
    "PROPOSAL",
    "PROPOSALS_STATE_FILENAME",
    "REJECT_CALLBACK",
    "REMINDED_KEY",
    "SHADOW_ONLY_ITEM",
    "STEP_DOWN_ITEM",
    "PendingProposal",
    "ProposalWatch",
    "SizeItem",
    "approval_markup",
    "batch_text",
    "deadline_text",
    "delivered_ids",
    "delivery_state_file",
    "describe",
    "expiry_text",
    "night_items",
    "pending_proposals",
    "proposal_ids",
    "record_delivered",
    "reminder_text",
    "unsent_items",
]

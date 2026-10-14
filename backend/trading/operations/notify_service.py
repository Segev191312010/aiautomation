"""Push the trading day's important events to the owner's phone via Telegram.

``run_notifier`` follows one session date until 16:15 ET and forwards:
  a. decisions Claude reviewed (a real setup), from the shadow database:
     real MOMO's (a sendable identity) at once, as before; managed MOMO's
     (AP_MOMO_HOD_V1) and the simulated strategies' in one digest every
     30 minutes (ET :00 and :30), flushed before the daily report;
  b. PAPER bridge order lines (SENT, FILL, EXIT, CLOSED, CANCEL, CANCELLED,
     FLATTEN, FLAT, CRITICAL, and loss-limit or kill-switch skips);
  c. supervisor child exits;
  d. competing IBKR logins and lost TWS connections seen in the data logs;
  e. the 16:05 ET daily report, closing with the last market context;
  f. market updates at 09:15, 12:00 and 15:30 ET and market regime changes,
     from the screener's market context;
  g. a database that needs migration, once per session (see below);
  h. the AI day manager (migration 16): a plan summary after the 09:20 and
     09:46 plans, and one alert per outage (no plan in force, a failed or
     timed-out plan, the plan budget spent). While migration 16 is pending
     these are skipped; everything else is unchanged;
  i. an unreachable IB Gateway (live 2026-10-14: off all session, no alert):
     one "IB Gateway unreachable since HH:MM ET" from the bridge's "TWS did
     not answer", the screener's "TWS connection failed", three strategy
     children's handshake failures, or no screener sweep within 20 minutes
     of SESSION_START; repeated at most every 30 minutes while it persists,
     and one "reconnected" line when a sweep returns.

It only reads the database, the logs, and the bridge state file: nothing here
can place, modify, or cancel an order. What was delivered, how far each log
was read, whether the report went out, and the regime last reported are kept
per date in a private state file, so a restart neither resends nor skips.

Unlike every other child it starts while a migration is pending: it never
writes the database and reads only tables that exist since migrations 2 and
11, so it keeps forwarding the bridge and the supervisor. It checks the
schema (read-only) at start and every five minutes; while the schema is not
current it sends one alert per session and drops the children's repeated
exit-75 restarts instead of sending one message per restart.
"""
from __future__ import annotations

import asyncio
from collections import Counter, defaultdict
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
import json
import logging
import os
from pathlib import Path
import re
import stat
import sys
import time as monotonic_time
from typing import TYPE_CHECKING, Any, BinaryIO, Protocol, TextIO

import aiosqlite
import httpx

from db.core import get_db
from trading.operations.keychain import (
    TELEGRAM_KEYCHAIN_ACCOUNT,
    TELEGRAM_KEYCHAIN_SERVICE,
    KeychainReadError,
    read_telegram_bot_token,
)
from trading.operations.logging import RedactingFormatter, configure_shadow_logging
from trading.operations.notifier import (
    TelegramAPIError,
    TelegramBotAPI,
    TelegramChat,
    TelegramConfigError,
    TelegramNotifier,
    discover_chat,
    is_valid_bot_token,
    load_chat_id,
    save_chat_id,
    telegram_config_file,
    token_secrets,
)
from trading.operations.paths import (
    PathSecurityError,
    UserPaths,
    atomic_private_write,
    ensure_private_file,
)
from trading.operations.schema_alert import schema_alert_text
from trading.operations.schema_gate import EXIT_SCHEMA_NOT_READY
from trading.scheduler.sessions import MARKET_TIMEZONE, market_session
from trading.sendable_identities import carries_sendable_prefix
from trading.storage.migrations import SchemaStatus, inspect_schema

if TYPE_CHECKING:
    from trading.market.context import MarketContext
    from trading.market.screener import HotListRow


logger = logging.getLogger("autotrader.shadow.notify")

SESSION_END = time(16, 15)
REPORT_AT = time(16, 5)
LOOP_TICK_SECONDS = 1.0
DECISION_POLL_SECONDS = 5.0
FILL_COALESCE_SECONDS = 10.0
IBKR_ALERT_INTERVAL = timedelta(minutes=10)
# A message the sender dropped (Telegram unreachable) is offered again after
# a cooldown, a bounded number of times.
REDELIVERY_COOLDOWN_SECONDS = 60.0
MAX_DELIVERY_ROUNDS = 3
RATIONALE_LIMIT = 600
DRAIN_TIMEOUT_SECONDS = 15.0
STATE_VERSION = 1
MAX_BRIDGE_STATE_BYTES = 5 * 1024 * 1024
LOCAL_PROBLEM_LOG_INTERVAL_SECONDS = 300.0
TEST_MESSAGE = "Autotrader connected ✅"
LOGIN_ALERT_TEXT = "⚠️ IBKR login active elsewhere — log out of the IBKR app/website"
MARKET_UPDATE_TIMES = (time(9, 15), time(12, 0), time(15, 30))
# A scheduled update waits this long for a fresh context (a screener restart
# takes minutes), then is skipped for the day rather than sent hours late.
MARKET_UPDATE_GRACE = timedelta(minutes=30)
MARKET_POLL_SECONDS = 30.0
# The same freshness MOMO's risk-off gate uses: older means "no context".
MARKET_CONTEXT_MAX_AGE = timedelta(minutes=5)
# "Market at close" is the day's last context however old: the screener
# stops at 16:00 and the report goes out at 16:05.
CLOSE_CONTEXT_MAX_AGE = timedelta(hours=24)
REGIME_ALERT_INTERVAL = timedelta(minutes=15)
# A migration that becomes pending (or a mismatched schema) reaches the phone
# within this long; the first check runs before the first log poll.
SCHEMA_CHECK_SECONDS = 300.0
SCHEMA_ALERT_KEY = "schema_alert"
# Pre-market regime swings only set the baseline that the 09:15 update reports.
REGIME_ALERTS_FROM = MARKET_UPDATE_TIMES[0]
HOT_NAMES_IN_UPDATE = 5
_REGIME_LABELS = {"risk_on": "RISK-ON", "neutral": "NEUTRAL", "risk_off": "RISK-OFF"}
_SERVICE_LOG_FILES = {
    "bridge": "bridge.log",
    "supervisor": "supervisor.log",
    "screener": "screener.log",
}


def log_files() -> dict[str, str]:
    """Every log the notifier follows: the services', and each supervised
    strategy child's own ``<name>.log`` (read from the supervisor's tables, so
    a renamed or added child is followed without an edit here)."""
    from trading.operations import supervisor

    children = (
        *supervisor.STRATEGY_STARTS,
        *supervisor.MANAGED_STRATEGY_STARTS,
        supervisor.MANAGER_CHILD,
    )
    return {**_SERVICE_LOG_FILES, **{name: f"{name}.log" for name in children}}


LOG_FILES = log_files()
# Shadow decisions (managed and simulated) reach the phone in one digest per
# half hour; only real MOMO's are sent one by one.
DIGEST_INTERVAL_MINUTES = 30
# A child that keeps exiting (a restart loop) reaches the phone once per half
# hour, not once per restart; the supervisor log keeps every line.
CHILD_EXIT_ALERT_INTERVAL = timedelta(minutes=30)
# The AI day manager's tripwires, as its children log them (CRITICAL lines in
# manager.log / momo-ap.log): each reaches the phone at most once per half hour.
MANAGER_TRIPWIRES = (
    "manager_widening_attempt",
    "manager_widening_detected",
    "candidate_filter_failed",
    "managed_size_failed",
    "managed_record_failed",
    "plan_store_failed",
    "spend_settle_failed",
    "ai_request_not_sent",
)
TRIPWIRE_ALERT_INTERVAL = timedelta(minutes=30)
# A plan lookup that fails in the managed child never shows in the plans table
# (the notifier's own outage alert reads that), so it is forwarded from the log.
_FALLBACK_PLAN_ERROR = re.compile(r"manager_fallback_neutral: plan_error\b")
DIGEST_LINES_PER_STRATEGY = 8
MANAGER_POLL_SECONDS = 30.0
PLAN_SUMMARY_SLOTS = ("09:20", "09:46")
BRIDGE_FORWARD_KINDS = frozenset(
    {"SENT", "FILL", "EXIT", "CLOSED", "CANCEL", "CANCELLED", "FLATTEN", "FLAT", "CRITICAL"}
)
# Outcomes that carry a Claude review, i.e. the strategy produced a real setup.
SIGNAL_OUTCOMES = (
    "shadow_accepted",
    "approved",
    "submitted",
    "risk_blocked",
    "ai_abstained",
    "ai_blocked",
)
ADD_TOKEN_COMMAND = (
    f"security add-generic-password -a {TELEGRAM_KEYCHAIN_ACCOUNT} "
    f"-s {TELEGRAM_KEYCHAIN_SERVICE} -w"
)
REPLACE_TOKEN_COMMAND = (
    f"security add-generic-password -U -a {TELEGRAM_KEYCHAIN_ACCOUNT} "
    f"-s {TELEGRAM_KEYCHAIN_SERVICE} -w"
)
SETUP_INSTRUCTIONS = f"""\
Telegram is not set up yet. One-time setup:
  1. In Telegram, open @BotFather, send /newbot and follow the prompts.
     BotFather replies with the bot token (it looks like 123456789:AAE...).
  2. Store the token in the macOS Keychain (paste it at the prompt; it is not shown):
       {ADD_TOKEN_COMMAND}
     To replace a token stored earlier:
       {REPLACE_TOKEN_COMMAND}
  3. Open your new bot in Telegram and press Start (this sends /start).
  4. Check the connection: autotraderctl notify test"""
START_INSTRUCTIONS = """\
  1. Open your bot in Telegram and press Start (or send /start).
  2. Run: autotraderctl notify test"""

CENT = Decimal("0.01")

Clock = Callable[[], datetime]
Sleeper = Callable[[float], Awaitable[None]]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class MarketContextReader(Protocol):
    async def __call__(
        self, session_date: date, *, now: datetime, max_age: timedelta
    ) -> MarketContext | None: ...


HotListReader = Callable[[date], Awaitable[Sequence["HotListRow"]]]


async def _latest_market_context(
    session_date: date, *, now: datetime, max_age: timedelta
) -> MarketContext | None:
    # Imported on use so the notifier keeps running (and reporting everything
    # else) even if the market modules fail to import.
    from trading.market.context import latest_market_context

    return await latest_market_context(session_date, now=now, max_age=max_age)


async def _latest_hot_list(session_date: date) -> Sequence[HotListRow]:
    from trading.market.screener import latest_hotlist

    return await latest_hotlist(session_date)


def _current_schema_status() -> SchemaStatus:
    """Read-only: the notifier never migrates, it only reports."""
    from config import cfg

    return inspect_schema(cfg.DB_PATH)


@dataclass(frozen=True, slots=True)
class NotifyEnvironment:
    """Everything the notifier touches outside the process; tests substitute each."""

    paths: UserPaths
    read_token: Callable[[], str] = read_telegram_bot_token
    transport: httpx.AsyncBaseTransport | None = None
    clock: Clock = _utc_now
    sleep: Sleeper = asyncio.sleep
    monotonic: Callable[[], float] = monotonic_time.monotonic
    out: TextIO | None = None
    configure_logging: bool = True
    read_market_context: MarketContextReader = _latest_market_context
    read_hot_list: HotListReader = _latest_hot_list
    read_schema_status: Callable[[], SchemaStatus] = _current_schema_status

    @classmethod
    def current(cls) -> NotifyEnvironment:
        return cls(paths=UserPaths.current())


def market_time(session_date: date, moment: time) -> datetime:
    return datetime.combine(session_date, moment, tzinfo=MARKET_TIMEZONE)


# --------------------------------------------------------------------------
# Small value helpers
# --------------------------------------------------------------------------


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def _et_clock(moment: datetime | None) -> str:
    if moment is None:
        return "time unknown"
    return moment.astimezone(MARKET_TIMEZONE).strftime("%H:%M:%S ET")


def _decimal(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() else None


def _int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    number = _decimal(value)
    if number is None or number != number.to_integral_value():
        return None
    return int(number)


def _price(value: Decimal | None) -> str:
    return "?" if value is None else format(value, "f")


def _money(value: Decimal) -> str:
    cents = value.quantize(CENT)
    sign = "-" if cents < 0 else "+" if cents > 0 else ""
    return f"{sign}${abs(cents):,.2f}"


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text.rfind(" ", 0, limit - 1)
    if cut < limit // 2:
        cut = limit - 1
    return text[:cut].rstrip() + "…"


def _json_object(value: object) -> dict[str, Any] | None:
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


# --------------------------------------------------------------------------
# Per-date delivery state
# --------------------------------------------------------------------------


def state_file(paths: UserPaths, session_date: date) -> Path:
    return paths.data_directory / "notify" / f"{session_date.isoformat()}.json"


@dataclass(slots=True)
class NotifyState:
    """What one session date has already delivered; survives restarts.

    ``delivered`` maps ``decision:<id>``, ``market:<HHMM>``, ``report`` and
    ``started`` to ``sent`` or ``dropped``; ``tails`` holds each log's file
    identity and the byte offset of its next unread line; ``market_regime`` is
    the regime the phone was last told about (or the silent baseline).
    """

    path: Path
    session_date: date
    delivered: dict[str, str] = field(default_factory=dict)
    tails: dict[str, dict[str, int]] = field(default_factory=dict)
    alerts: dict[str, str] = field(default_factory=dict)
    market_regime: str | None = None
    dirty: bool = False

    @classmethod
    def load(cls, path: Path, session_date: date) -> NotifyState:
        state = cls(path=path, session_date=session_date)
        target = path.expanduser()
        if not target.exists() and not target.is_symlink():
            return state
        try:
            ensure_private_file(target)
            raw = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, ValueError, PathSecurityError) as exc:
            logger.warning(
                "notifier state %s unreadable (%s); starting fresh", target.name, type(exc).__name__
            )
            return state
        if (
            not isinstance(raw, dict)
            or raw.get("version") != STATE_VERSION
            or raw.get("session_date") != session_date.isoformat()
        ):
            logger.warning("notifier state %s has an unexpected shape; starting fresh", target.name)
            return state
        delivered = raw.get("delivered")
        if isinstance(delivered, dict):
            state.delivered = {
                str(key): value for key, value in delivered.items() if value in {"sent", "dropped"}
            }
        tails = raw.get("tails")
        if isinstance(tails, dict):
            for name, checkpoint in tails.items():
                if isinstance(checkpoint, dict) and all(
                    _int(checkpoint.get(key)) is not None for key in ("dev", "ino", "offset")
                ):
                    state.tails[str(name)] = {
                        key: int(checkpoint[key]) for key in ("dev", "ino", "offset")
                    }
        alerts = raw.get("alerts")
        if isinstance(alerts, dict):
            state.alerts = {
                str(key): value for key, value in alerts.items() if isinstance(value, str)
            }
        regime = raw.get("market_regime")
        if isinstance(regime, str) and regime:
            state.market_regime = regime
        return state

    def save(self) -> None:
        payload = {
            "version": STATE_VERSION,
            "session_date": self.session_date.isoformat(),
            "delivered": self.delivered,
            "tails": self.tails,
            "alerts": self.alerts,
            "market_regime": self.market_regime,
        }
        encoded = json.dumps(payload, sort_keys=True, indent=1).encode("utf-8")
        atomic_private_write(self.path, encoded)
        self.dirty = False


# --------------------------------------------------------------------------
# Log following
# --------------------------------------------------------------------------


# Checkpoint for a log that did not exist: whatever appears later is all new.
MISSING_FILE_CHECKPOINT = {"dev": 0, "ino": 0, "offset": 0}


class LogTailer:
    """Follow one log by byte offset across rotation, truncation, and restarts.

    Only complete lines are returned. A file that exists at the first start of
    a session date is followed from its end (older lines belong to earlier
    runs); a file that appears later, or that replaced the one being
    followed, is read from its beginning.
    """

    def __init__(
        self,
        path: Path,
        *,
        max_read_bytes: int = 1 << 20,
        max_line_bytes: int = 16 << 10,
    ) -> None:
        self.path = path
        self._max_read = max_read_bytes
        self._max_line = max_line_bytes
        self._handle: BinaryIO | None = None
        self._identity: tuple[int, int] | None = None
        self._unreadable: tuple[int, int] | None = None
        self._offset = 0
        self._partial = b""

    def start(self, checkpoint: Mapping[str, int] | None = None) -> None:
        """Resume at ``checkpoint`` while it names this same file, else follow from the end."""
        if not self._open():
            return
        assert self._handle is not None
        size = os.fstat(self._handle.fileno()).st_size
        if checkpoint is None:
            offset = size
        elif (checkpoint.get("dev"), checkpoint.get("ino")) == self._identity and (
            0 <= checkpoint.get("offset", -1) <= size
        ):
            offset = checkpoint["offset"]
        else:
            # Rotated or truncated while the notifier was down: all of it is new.
            offset = 0
        self._handle.seek(offset)
        self._offset = offset

    def checkpoint(self) -> dict[str, int]:
        if self._identity is None or self._handle is None:
            return dict(MISSING_FILE_CHECKPOINT)
        return {
            "dev": self._identity[0],
            "ino": self._identity[1],
            "offset": self._offset - len(self._partial),
        }

    def poll(self) -> list[str]:
        lines: list[str] = []
        if self._handle is not None:
            # Drain the file being followed first: after a rotation this is
            # the tail end of the old file.
            lines.extend(self._drain())
        try:
            status = self.path.stat()
        except OSError:
            return lines
        if not stat.S_ISREG(status.st_mode):
            return lines
        identity = (status.st_dev, status.st_ino)
        if identity != self._identity:
            # First appearance or rotation: everything in the new file is new.
            if self._open(identity):
                lines.extend(self._drain())
        elif status.st_size < self._offset:
            assert self._handle is not None
            self._handle.seek(0)
            self._offset = 0
            self._partial = b""
            lines.extend(self._drain())
        return lines

    def close(self) -> None:
        if self._handle is not None:
            try:
                self._handle.close()
            except OSError:
                pass
        self._handle = None
        self._identity = None
        self._offset = 0
        self._partial = b""

    def _open(self, expected: tuple[int, int] | None = None) -> bool:
        self.close()
        try:
            handle = self.path.open("rb")
        except FileNotFoundError:
            return False
        except OSError as exc:
            # Retried every poll (it may be fixed), reported once per file.
            if expected is None or expected != self._unreadable:
                logger.warning("cannot read %s: %s", self.path.name, type(exc).__name__)
            self._unreadable = expected
            return False
        status = os.fstat(handle.fileno())
        if not stat.S_ISREG(status.st_mode):
            handle.close()
            return False
        self._handle = handle
        self._identity = (status.st_dev, status.st_ino)
        self._unreadable = None
        return True

    def _drain(self) -> list[str]:
        assert self._handle is not None
        try:
            chunk = self._handle.read(self._max_read)
        except OSError as exc:
            logger.warning("cannot read %s: %s", self.path.name, type(exc).__name__)
            return []
        if not chunk:
            return []
        self._offset += len(chunk)
        *complete, partial = (self._partial + chunk).split(b"\n")
        if len(partial) > self._max_line:
            # A runaway line is emitted rather than buffered without bound.
            complete.append(partial)
            partial = b""
        self._partial = partial
        lines: list[str] = []
        for raw in complete:
            text = raw.decode("utf-8", "replace").rstrip("\r")
            if text.strip():
                lines.append(text)
        return lines


# --------------------------------------------------------------------------
# Bridge, supervisor, and data-log lines
# --------------------------------------------------------------------------


_BRIDGE_LINE = re.compile(r"^(?P<clock>\d{1,2}:\d{2}:\d{2}) ET\s+(?P<body>\S.*)$")
_PNL = re.compile(r"P&L\s*:?\s*(?P<value>[-+]?\$?\s?\d[\d,]*(?:\.\d+)?)")
_KIND_EMOJI = {
    "SENT": "🟢",
    "FILL": "💵",
    "CLOSED": "🏁",
    "CANCEL": "↩️",
    "CANCELLED": "↩️",
    "FLATTEN": "🧹",
    "FLAT": "✅",
    "CRITICAL": "⚠️",
}
_CHILD_EXIT = re.compile(
    r"\bCHILD_EXIT\s+(?P<name>\S+)\s+code=(?P<code>[^\s,]+),?\s+restart_in=(?P<restart>[^\s,]+)"
)
_NO_RESTART = frozenset({"none", "never", "no", "null", "-"})
_LAUNCH_REFUSED = re.compile(
    r"\bLAUNCH_REFUSED\s+(?P<name>\S+)\s+code=(?P<code>\S+)\s+reason=(?P<reason>\S+)"
)
# The phrases IBKR (10197, and 162/165 "different IP address") and our own
# TWS actor use; a bare "competing" or a number elsewhere in a line is not one.
_COMPETING_LOGIN = re.compile(
    r"different ip address|competing (?:live )?session|TWSCompetingSessionError"
    r"|login active elsewhere|\b(?:error|code)\s*[=:]?\s*10197\b|\bTWS\s+-?\d+\s+10197\b",
    re.IGNORECASE,
)
_CONNECTION_LOST = re.compile(r"\bTWS connection lost\b", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class BridgeEvent:
    clock: str
    kind: str
    symbol: str | None
    body: str


def parse_bridge_line(line: str) -> BridgeEvent | None:
    """``HH:MM:SS ET  <KIND> <rest>``; anything else (tracebacks, banners) is None."""
    match = _BRIDGE_LINE.match(line.strip())
    if match is None:
        return None
    body = match.group("body").strip()
    head, _, rest = body.partition(" ")
    kind = head.rstrip(":")
    symbol: str | None = None
    if ":" in rest:
        subject = rest.split(":", 1)[0].strip()
        if kind == "CANCEL" and subject.startswith("entry "):
            subject = subject[len("entry ") :].strip()
        symbol = subject.split(" (", 1)[0].strip() or None
    return BridgeEvent(clock=match.group("clock"), kind=kind, symbol=symbol, body=body)


def _pnl_emoji(body: str) -> str:
    match = _PNL.search(body)
    value = None
    if match is not None:
        value = _decimal(match.group("value").replace(",", "").replace("$", "").replace(" ", ""))
    if value is None or value == 0:
        return "⚪"
    return "🔴" if value < 0 else "🟢"


def bridge_event_text(event: BridgeEvent) -> str | None:
    """The phone message for a bridge line, or None when it is not forwarded."""
    if event.kind == "SKIP":
        lowered = event.body.lower()
        if "daily loss" not in lowered and "kill switch" not in lowered:
            return None
        emoji = "⛔"
    elif event.kind not in BRIDGE_FORWARD_KINDS:
        return None
    elif event.kind == "EXIT":
        emoji = _pnl_emoji(event.body)
    else:
        emoji = _KIND_EMOJI[event.kind]
    return f"{emoji} {event.body} · {event.clock} ET"


def _seconds_text(value: str) -> str:
    number = _decimal(value.lower().removesuffix("s"))
    if number is None:
        return value
    if number == number.to_integral_value():
        return str(int(number))
    return format(number.normalize(), "f")


def supervisor_event_text(line: str) -> str | None:
    """``CHILD_EXIT <name> code=<n> restart_in=<x>``; a clean final exit is not news.

    ``LAUNCH_REFUSED`` (the supervisor declined to start a child, e.g. a bridge
    that is not the reviewed one) says why, since no orders follow from it.
    """
    refused = _LAUNCH_REFUSED.search(line)
    if refused is not None:
        name = refused.group("name")
        if refused.group("reason") == "unreviewed":
            return (
                f"⛔ {name} NOT started: its code is not the reviewed version, so no orders"
                " will be sent until it is reviewed (supervisor.log)"
            )
        return f"⛔ {name} NOT started: {refused.group('reason')} (supervisor.log)"
    match = _CHILD_EXIT.search(line)
    if match is None:
        return None
    name = match.group("name")
    code = match.group("code")
    restart = match.group("restart")
    if restart.lower() in _NO_RESTART:
        if code == "0":
            return None
        return f"⚙️ {name} exited (code {code}), not restarting"
    return f"⚙️ {name} exited (code {code}), restarting in {_seconds_text(restart)} s"


def supervisor_child_name(line: str) -> str | None:
    """The child a ``CHILD_EXIT`` or ``LAUNCH_REFUSED`` line is about."""
    match = _CHILD_EXIT.search(line) or _LAUNCH_REFUSED.search(line)
    return None if match is None else match.group("name")


def manager_tripwire(line: str) -> tuple[str, str] | None:
    """``(token, phone text)`` for a day-manager tripwire line, else None."""
    token = next((name for name in MANAGER_TRIPWIRES if name in line), None)
    if token is None and _FALLBACK_PLAN_ERROR.search(line):
        token = "manager_fallback_neutral"
    if token is None:
        return None
    return token, f"🚨 AI day manager tripwire: {token}\n{_clip(line.strip(), 300)}"


def child_exit_code(line: str) -> int | None:
    """The exit code of a ``CHILD_EXIT`` line, or None for any other line."""
    match = _CHILD_EXIT.search(line)
    if match is None:
        return None
    return _int(match.group("code"))


def ibkr_session_problem(line: str, *, include_connection_lost: bool = True) -> str | None:
    if _COMPETING_LOGIN.search(line):
        return "competing_login"
    if include_connection_lost and _CONNECTION_LOST.search(line):
        return "connection_lost"
    return None


def ibkr_alert_text(problem: str, source: str) -> str:
    if problem == "competing_login":
        return f"{LOGIN_ALERT_TEXT}\n(seen in the {source} log)"
    return (
        f"⚠️ TWS connection lost ({source}) — if you are logged in to the IBKR app/website, "
        "log out there; otherwise check that TWS is running and logged in"
    )


# -- IB Gateway reachability ------------------------------------------------
# Live 2026-10-14: the Gateway was off all session. The bridge exited 75 every
# 5 minutes ("TWS did not answer"), the screener logged "TWS connection failed"
# every 70 s with no sweep, every strategy child exit-1-looped, and nothing
# told the phone why. One alert names it, repeated at most every half hour
# while it persists, and one line says when a sweep returns.
BROKER_UNREACHABLE = "broker_unreachable"
BROKER_ALERT_INTERVAL = timedelta(minutes=30)
# The supervisor's bridge child and the bridge's own "ask again later" exit
# (`— exit 75` in its log); both pinned by tests.
BRIDGE_CHILD_NAME = "bridge"
BRIDGE_RESTART_CODE = 75
# The supervisor's SESSION_START (its first child window); pinned by a test.
SESSION_OPENS = time(7, 55)
# A healthy screener idles until 08:00 ET, connects, qualifies the universe
# (~8 minutes) and first sweeps at ~08:10 ET: its first sweep is due within
# 20 minutes of SESSION_START, not 5 (live 2026-10-13: 08:10 ET).
FIRST_SWEEP_GRACE = timedelta(minutes=20)
# Three children's handshake failures (one line each, at their exit) are the
# Gateway, not one child's bad luck.
HANDSHAKE_FAILURES_TO_ALERT = 3
_SESSION_START = re.compile(r"\bSESSION_START\s+date=(?P<date>\d{4}-\d{2}-\d{2})\b")
_BRIDGE_UNREACHABLE = re.compile(r"TWS did not answer|Couldn't connect to TWS", re.IGNORECASE)
_SCREENER_UNREACHABLE = re.compile(r"\bTWS connection failed\b", re.IGNORECASE)
# The child's own last line (`error: shadow run --strategy <name> failed: ...`),
# not the traceback line above it: one per exit.
_HANDSHAKE_FAILED = re.compile(r"^error: .*failed: TWSConnectionError: TWS PAPER handshake failed")
_SWEEP = re.compile(r"\bsweep (?P<date>\d{4}-\d{2}-\d{2}):")


def broker_evidence(source: str, line: str) -> str | None:
    """What one log line says about the Gateway: ``sweep`` (market data flows),
    ``bridge_refused``, ``screener_failed``, ``handshake_failed``, or None."""
    if source == "screener":
        if _SWEEP.search(line):
            return "sweep"
        if _SCREENER_UNREACHABLE.search(line):
            return "screener_failed"
        return None
    if source == "bridge":
        return "bridge_refused" if _BRIDGE_UNREACHABLE.search(line) else None
    if source == "supervisor":
        return None
    return "handshake_failed" if _HANDSHAKE_FAILED.match(line.strip()) else None


def _et_hhmm(moment: datetime) -> str:
    return moment.astimezone(MARKET_TIMEZONE).strftime("%H:%M")


@dataclass(slots=True)
class BrokerWatch:
    """The Gateway's reachability as the logs show it; the alert and the recovery line.

    In memory only: after a notifier restart during an outage the next
    evidence line reopens it (the 30-minute cadence lives in the persisted
    ``alerts``), so "since" is then the restart, not the first failure.
    """

    session_date: date
    opened_at: datetime | None = None
    last_sweep_at: datetime | None = None
    unreachable_since: datetime | None = None
    handshake_failures: int = 0
    # Who the outage took down: the bridge (its own refusal line) and the
    # children whose log showed the handshake failure. Their exits are the
    # outage, not news of their own; the next alert counts them.
    bridge_refused: bool = False
    handshake_children: set[str] = field(default_factory=set)
    restarting: dict[str, int] = field(default_factory=dict)

    @property
    def unreachable(self) -> bool:
        return self.unreachable_since is not None

    def session_started(self, now: datetime) -> None:
        if self.opened_at is None:
            self.opened_at = now

    def broker_exit(self, name: str, code: int | None) -> bool:
        """Whether a ``CHILD_EXIT`` is the outage (counted, not forwarded).

        Live 2026-10-14: ~12 "<child> exited (code N), restarting" lines an
        hour and never a plain "Gateway off". The bridge's exit 75 after its
        "TWS did not answer", and a child's exit 1 after its handshake
        failure, are folded into the broker alert; any other exit goes through.
        """
        if not self.unreachable:
            return False
        if name == BRIDGE_CHILD_NAME:
            caused = code == BRIDGE_RESTART_CODE and self.bridge_refused
        else:
            caused = code == 1 and name in self.handshake_children
        if caused:
            self.restarting[name] = self.restarting.get(name, 0) + 1
        return caused

    def alert_sent(self) -> None:
        self.restarting.clear()

    def observe(self, source: str, line: str, now: datetime) -> str | None:
        """Feed one log line; the recovery text when a sweep ends an outage, else None."""
        if source == "supervisor":
            started = _SESSION_START.search(line)
            if started is not None and started.group("date") == self.session_date.isoformat():
                self.session_started(now)
            return None
        evidence = broker_evidence(source, line)
        if evidence is None:
            return None
        if evidence == "sweep":
            self.last_sweep_at = now
            self.handshake_failures = 0
            self.bridge_refused = False
            self.handshake_children.clear()
            self.restarting.clear()
            since = self.unreachable_since
            if since is None:
                return None
            self.unreachable_since = None
            return (
                f"✅ IB Gateway reconnected {_et_hhmm(now)} ET — market data is back"
                f" (unreachable since {_et_hhmm(since)} ET)"
            )
        if evidence == "bridge_refused":
            self.bridge_refused = True
        if evidence == "handshake_failed":
            self.handshake_children.add(source)
            self.handshake_failures += 1
            if self.handshake_failures < HANDSHAKE_FAILURES_TO_ALERT:
                return None
        if self.unreachable_since is None:
            self.unreachable_since = now
        return None

    def check(self, now: datetime) -> None:
        """No sweep since the session opened: the Gateway never answered."""
        if (
            self.unreachable_since is None
            and self.opened_at is not None
            and self.last_sweep_at is None
            and now - self.opened_at >= FIRST_SWEEP_GRACE
        ):
            self.unreachable_since = self.opened_at

    def alert_text(self) -> str:
        assert self.unreachable_since is not None
        text = (
            f"⛔ IB Gateway unreachable since {_et_hhmm(self.unreachable_since)} ET — log in;"
            " the bot has no market data"
        )
        count = len(self.restarting)
        if count:
            text += f" ({count} {'child' if count == 1 else 'children'} restarting)"
        return text


@dataclass(slots=True)
class _PendingFill:
    first_seen: float
    text: str
    updates: int


class FillCoalescer:
    """Partial fills arrive in bursts: one message per symbol per window."""

    def __init__(self, window_seconds: float = FILL_COALESCE_SECONDS) -> None:
        self._window = window_seconds
        self._pending: dict[str, _PendingFill] = {}

    def add(self, symbol: str, text: str, now: float) -> None:
        pending = self._pending.get(symbol)
        if pending is None:
            self._pending[symbol] = _PendingFill(first_seen=now, text=text, updates=1)
            return
        # Bridge FILL lines are cumulative, so the newest one is the position.
        pending.text = text
        pending.updates += 1

    def due(self, now: float) -> list[str]:
        ready = [
            symbol
            for symbol, pending in self._pending.items()
            if now - pending.first_seen >= self._window
        ]
        return [self._render(self._pending.pop(symbol)) for symbol in ready]

    def drain(self) -> list[str]:
        texts = [self._render(pending) for pending in self._pending.values()]
        self._pending.clear()
        return texts

    @staticmethod
    def _render(pending: _PendingFill) -> str:
        if pending.updates == 1:
            return pending.text
        return f"{pending.text} ({pending.updates} fill updates)"


# --------------------------------------------------------------------------
# Decisions
# --------------------------------------------------------------------------


_STRATEGY_NAME_SQL = "COALESCE(json_extract(v.spec_json, '$.name'), r.strategy_id)"
_SESSION_DECISIONS_FROM = """
    FROM autotrader_candidate_decisions d
    JOIN autotrader_runs r ON r.id = d.run_id
    LEFT JOIN autotrader_strategy_versions v
      ON v.strategy_id = r.strategy_id AND v.version = r.strategy_version
"""
# Backtests replay old days into the same tables; only live runs are news.
_LIVE_RUN_FILTER = "r.session_date = ? AND r.mode IN ('shadow', 'paper')"

_OUTCOME_HEADLINES = {
    "shadow_accepted": ("🔔", "WOULD BUY"),
    "approved": ("🔔", "WOULD BUY"),
    "submitted": ("🔔", "BUY SUBMITTED"),
    "risk_blocked": ("🛑", "risk blocked"),
    "ai_abstained": ("🤔", "Claude abstained"),
    "ai_blocked": ("🚫", "AI blocked"),
}
_OUTCOME_ORDER = (
    "shadow_accepted",
    "approved",
    "submitted",
    "risk_blocked",
    "ai_abstained",
    "ai_blocked",
    "rule_rejected",
    "data_blocked",
)
_OUTCOME_LABELS = {
    "shadow_accepted": "would buy",
    "approved": "approved",
    "submitted": "submitted",
    "risk_blocked": "risk blocked",
    "ai_abstained": "Claude abstained",
    "ai_blocked": "AI blocked",
    "rule_rejected": "rule rejected",
    "data_blocked": "data blocked",
}


@dataclass(frozen=True, slots=True)
class SignalDecision:
    decision_id: str
    created_at: datetime | None
    strategy: str
    symbol: str
    outcome: str
    review: Mapping[str, Any]
    risk: Mapping[str, Any] | None


async def load_signal_decisions(session_date: date) -> list[SignalDecision]:
    """Today's decisions that reached Claude, oldest first."""
    placeholders = ",".join("?" for _ in SIGNAL_OUTCOMES)
    query = f"""
        SELECT d.id, d.outcome, d.payload_json, d.created_at, c.symbol, {_STRATEGY_NAME_SQL}
        {_SESSION_DECISIONS_FROM}
        JOIN autotrader_candidates c ON c.id = d.candidate_id
        WHERE {_LIVE_RUN_FILTER} AND d.outcome IN ({placeholders})
        ORDER BY d.created_at, d.id
    """
    async with get_db() as db:
        async with db.execute(query, (session_date.isoformat(), *SIGNAL_OUTCOMES)) as cursor:
            rows = await cursor.fetchall()
    decisions: list[SignalDecision] = []
    for decision_id, outcome, payload_json, created_at, symbol, strategy in rows:
        payload = _json_object(payload_json) or {}
        review = payload.get("ai_review")
        if not isinstance(review, dict):
            continue
        risk = payload.get("risk")
        decisions.append(
            SignalDecision(
                decision_id=str(decision_id),
                created_at=_parse_time(created_at),
                strategy=str(strategy or "unknown strategy"),
                symbol=str(symbol),
                outcome=str(outcome),
                review=review,
                risk=risk if isinstance(risk, dict) else None,
            )
        )
    return decisions


def is_real_decision(decision: SignalDecision) -> bool:
    """A decision the PAPER bridge may send (a MOMO_HOD name): at once, as before."""
    return carries_sendable_prefix(decision.strategy)


def digest_boundary(now: datetime) -> datetime:
    """The latest ET :00 or :30 at or before ``now``: one digest per half hour."""
    local = now.astimezone(MARKET_TIMEZONE)
    minute = local.minute - local.minute % DIGEST_INTERVAL_MINUTES
    return local.replace(minute=minute, second=0, microsecond=0)


async def load_managed_rows(session_date: date) -> dict[str, Mapping[str, Any]]:
    """Managed decisions' narrowing (migration 16), by decision id; empty without it."""
    try:
        from trading.manager.store import managed_decisions

        rows = await managed_decisions(session_date)
    except (aiosqlite.Error, OSError):
        return {}
    return {str(row["decision_id"]): row for row in rows}


def _digest_line(decision: SignalDecision, managed: Mapping[str, Any] | None) -> str:
    _emoji, headline = _OUTCOME_HEADLINES.get(decision.outcome, ("", decision.outcome))
    line = f"• {decision.symbol} {headline}"
    risk = decision.risk or {}
    intent = risk.get("intent")
    if isinstance(intent, Mapping):
        line += f" {intent.get('quantity')} @ {intent.get('entry_limit')}"
    elif decision.outcome == "risk_blocked":
        codes = risk.get("codes")
        if isinstance(codes, list) and codes:
            line += f" ({', '.join(str(code) for code in codes[:2])})"
    failure = decision.review.get("failure_reason")
    if failure:
        line += f" ({failure})"
    if managed is not None:
        if managed.get("mode") == "managed":
            line += (
                f" · plan {str(managed.get('plan_id') or '?').split('-', 1)[-1]}"
                f" · steps {managed.get('day_step')}×{managed.get('setup_step')}"
            )
            if managed.get("policy_qty") is not None:
                line += f" of {managed.get('policy_qty')}"
        else:
            line += f" · neutral ({managed.get('neutral_reason')})"
    return line


def format_digest(
    decisions: Sequence[SignalDecision],
    managed: Mapping[str, Mapping[str, Any]],
    at: datetime,
) -> str:
    """Shadow decisions since the last digest, grouped by strategy (plain text)."""
    by_strategy: dict[str, list[SignalDecision]] = defaultdict(list)
    for decision in decisions:
        by_strategy[decision.strategy].append(decision)
    lines = [f"🗂 Shadow digest {at.astimezone(MARKET_TIMEZONE):%H:%M} ET · {len(decisions)} decisions (no orders)"]
    for strategy in sorted(by_strategy):
        items = by_strategy[strategy]
        lines.append(f"{strategy}:")
        for decision in items[:DIGEST_LINES_PER_STRATEGY]:
            lines.append(_digest_line(decision, managed.get(decision.decision_id)))
        if len(items) > DIGEST_LINES_PER_STRATEGY:
            lines.append(f"• … {len(items) - DIGEST_LINES_PER_STRATEGY} more (/signals)")
    return "\n".join(lines)


def _setup_lines(intent: Mapping[str, Any]) -> list[str]:
    quantity = _int(intent.get("quantity"))
    entry = _decimal(intent.get("entry_limit"))
    stop = _decimal(intent.get("stop_price"))
    target = _decimal(intent.get("target_price"))
    lines = [
        f"Buy {quantity if quantity is not None else '?'} @ {_price(entry)}"
        f" · stop {_price(stop)} · target {_price(target)}"
    ]
    if quantity and entry is not None and stop is not None and target is not None and entry > stop:
        per_share = entry - stop
        risk = (per_share * quantity).quantize(CENT)
        reward = ((target - entry) / per_share).quantize(Decimal("0.1"))
        notional = (entry * quantity).quantize(CENT)
        lines.append(
            f"Risk ${risk:,.2f} ({_price(per_share)}/sh) · reward {reward}R"
            f" · notional ${notional:,.2f}"
        )
    return lines


def _confidence(value: object) -> str | None:
    number = _decimal(value)
    if number is None:
        return None
    return f"{(number * 100).quantize(Decimal('1'))}%"


def format_decision(decision: SignalDecision) -> str:
    """One plain-text message: what, which strategy, the setup, and Claude's reasoning."""
    emoji, headline = _OUTCOME_HEADLINES.get(decision.outcome, ("🔔", decision.outcome))
    lines = [f"{emoji} {decision.strategy} · {decision.symbol} · {headline}"]
    risk = decision.risk or {}
    intent = risk.get("intent")
    if isinstance(intent, Mapping):
        lines.extend(_setup_lines(intent))
    if decision.outcome == "risk_blocked":
        codes = risk.get("codes")
        codes = codes if isinstance(codes, list) else []
        names = [str(code) for code in codes if str(code).strip()]
        lines.append("Risk codes: " + (", ".join(names) or "none recorded"))
    review = decision.review
    failure = review.get("failure_reason")
    if decision.outcome == "ai_blocked" or failure:
        lines.append(f"Review failed: {failure or 'unknown reason'}")
    else:
        claude = f"Claude: {review.get('decision') or '?'}"
        confidence = _confidence(review.get("confidence"))
        if confidence:
            claude += f" · confidence {confidence}"
        lines.append(claude)
        rationale = str(review.get("rationale") or "").strip()
        if rationale:
            lines.append(_clip(rationale, RATIONALE_LIMIT))
    lines.append(f"{_et_clock(decision.created_at)} · decision {decision.decision_id[:8]}")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Market context
# --------------------------------------------------------------------------


def market_update_key(moment: time) -> str:
    return f"market:{moment:%H%M}"


def regime_label(regime: str) -> str:
    return _REGIME_LABELS.get(regime, regime.upper().replace("_", "-"))


def _pct(value: object) -> str:
    number = _decimal(value)
    if number is None:
        return "?"
    rounded = number.quantize(CENT)
    if rounded == 0:
        return "0.00%"
    return f"{'+' if rounded > 0 else ''}{rounded}%"


def _sector_line(label: str, sectors: object) -> str:
    items: list[str] = []
    for entry in sectors if isinstance(sectors, Sequence) else ():
        if isinstance(entry, Sequence) and not isinstance(entry, str) and len(entry) == 2:
            items.append(f"{entry[0]} {_pct(entry[1])}")
    return f"{label}: " + (", ".join(items) if items else "none")


def _hot_line(rows: Sequence[HotListRow] | None) -> str:
    if rows is None:
        return "Hot list: unavailable"
    names = [
        row.symbol if row.change_pct is None else f"{row.symbol} {_pct(row.change_pct)}"
        for row in rows[:HOT_NAMES_IN_UPDATE]
    ]
    return "Hot list: " + (", ".join(names) if names else "none")


def format_market_update(context: MarketContext, hot: Sequence[HotListRow] | None) -> str:
    """The scheduled snapshot: regime, summary, sectors, and the top of the hot list."""
    return "\n".join(
        [
            f"📈 Market · {_et_clock(context.observed_at)} · {regime_label(str(context.regime))}",
            str(context.summary).strip() or "no summary",
            _sector_line("Leading sectors", context.sector_leaders),
            _sector_line("Lagging sectors", context.sector_laggards),
            _hot_line(hot),
        ]
    )


def format_regime_change(context: MarketContext) -> str:
    regime = str(context.regime)
    # Mirrors MOMO's gate, which blocks entries only while the regime is risk_off.
    effect = "momentum entries paused" if regime == "risk_off" else "entries allowed"
    return "\n".join(
        [
            f"🧭 Market turned {regime_label(regime)} — {effect}",
            str(context.summary).strip() or "no summary",
            f"as of {_et_clock(context.observed_at)}",
        ]
    )


async def _close_context(
    session_date: date, reader: MarketContextReader, now: datetime
) -> MarketContext | None:
    try:
        return await reader(session_date, now=now, max_age=CLOSE_CONTEXT_MAX_AGE)
    except Exception as exc:  # noqa: BLE001 - the report never waits on market context
        logger.warning("market context for the daily report unavailable: %s", type(exc).__name__)
        return None


# --------------------------------------------------------------------------
# Daily report
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DailyReport:
    session_date: date
    outcomes: Mapping[str, Mapping[str, int]]
    ai_calls: int
    ai_cost_usd: Decimal
    trades: tuple[Mapping[str, Any], ...]
    trades_source: str | None
    trades_problem: str | None
    last_snapshot_at: datetime | None
    market_close_summary: str | None = None
    market_close_at: datetime | None = None


def bridge_state_files(paths: UserPaths, session_date: date) -> tuple[Path, Path]:
    """The per-date bridge state, then the legacy single file it replaced."""
    return (
        paths.data_directory / "bridge" / f"{session_date.isoformat()}.json",
        paths.data_directory / "paper_bridge_state.json",
    )


def load_bridge_trades(
    paths: UserPaths, session_date: date
) -> tuple[tuple[Mapping[str, Any], ...], str | None, str | None]:
    """``(trades, source file name, problem)`` from the bridge's own state file."""
    dated, legacy = bridge_state_files(paths, session_date)
    for path, legacy_file in ((dated, False), (legacy, True)):
        if not path.is_file():
            continue
        try:
            if path.stat().st_size > MAX_BRIDGE_STATE_BYTES:
                return (), path.name, "bridge state file is too large to read"
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            return (), path.name, f"bridge state unreadable ({type(exc).__name__})"
        submitted = data.get("submitted") if isinstance(data, dict) else None
        if not isinstance(submitted, dict):
            return (), path.name, "bridge state has no 'submitted' entries"
        trades = [
            {**item, "decision_id": str(key)}
            for key, item in submitted.items()
            if isinstance(item, dict)
        ]
        if legacy_file:
            # The legacy file spans days; keep the brackets sent on this date.
            trades = [
                trade
                for trade in trades
                if (sent := _parse_time(trade.get("sent_at"))) is not None
                and sent.astimezone(MARKET_TIMEZONE).date() == session_date
            ]
        return tuple(trades), path.name, None
    return (), None, None


async def collect_daily_report(
    session_date: date,
    paths: UserPaths,
    *,
    read_market_context: MarketContextReader = _latest_market_context,
    now: datetime | None = None,
) -> DailyReport:
    outcomes: dict[str, Counter[str]] = defaultdict(Counter)
    ai_calls = 0
    ai_cost = Decimal("0")
    query = f"""
        SELECT {_STRATEGY_NAME_SQL}, d.outcome, d.payload_json
        {_SESSION_DECISIONS_FROM}
        WHERE {_LIVE_RUN_FILTER}
    """
    async with get_db() as db:
        async with db.execute(query, (session_date.isoformat(),)) as cursor:
            rows = await cursor.fetchall()
        try:
            async with db.execute(
                "SELECT MAX(observed_at) FROM autotrader_screener_snapshots WHERE session_date=?",
                (session_date.isoformat(),),
            ) as cursor:
                snapshot_row = await cursor.fetchone()
        except aiosqlite.OperationalError:
            snapshot_row = None
    for strategy, outcome, payload_json in rows:
        outcomes[str(strategy or "unknown strategy")][str(outcome)] += 1
        review = (_json_object(payload_json) or {}).get("ai_review")
        if not isinstance(review, dict):
            continue
        cost = _decimal(review.get("cost_usd"))
        if cost is not None and cost >= 0:
            ai_cost += cost
        # Blocked reviews (timeout, budget, invalid output) never reached Claude.
        if review.get("model_id") not in (None, "unavailable"):
            ai_calls += 1
    trades, source, problem = load_bridge_trades(paths, session_date)
    close = await _close_context(session_date, read_market_context, now or _utc_now())
    return DailyReport(
        session_date=session_date,
        outcomes={strategy: dict(counts) for strategy, counts in outcomes.items()},
        ai_calls=ai_calls,
        ai_cost_usd=ai_cost,
        trades=trades,
        trades_source=source,
        trades_problem=problem,
        last_snapshot_at=_parse_time(snapshot_row[0]) if snapshot_row else None,
        market_close_summary=(str(close.summary).strip() or None) if close else None,
        market_close_at=close.observed_at if close else None,
    )


def _trade_lines(report: DailyReport) -> list[str]:
    lines = ["Trades (PAPER bridge)"]
    if report.trades_problem:
        return [*lines, f"• ⚠️ {report.trades_problem}"]
    if report.trades_source is None:
        return [*lines, "• no bridge state for today"]
    total = Decimal("0")
    skipped: list[str] = []
    shown = 0
    for trade in report.trades:
        status = str(trade.get("status") or "")
        symbol = str(trade.get("symbol") or "?")
        if status == "skipped" or trade.get("skipped") is True:
            reason = str(trade.get("skip_reason") or "").strip()
            if reason != "legacy entry":
                skipped.append(f"{symbol}: {reason}" if reason else symbol)
            continue
        strategy = trade.get("strategy")
        label = f"{symbol} ({strategy})" if strategy else symbol
        pnl = _decimal(trade.get("realized_pnl")) or Decimal("0")
        total += pnl
        quantity = _int(trade.get("qty"))
        filled = _int(trade.get("filled")) or 0
        exited = _int(trade.get("exit_filled")) or 0
        if status == "closed":
            shares = filled or quantity or 0
            lines.append(
                f"• {label}: {shares} sh {trade.get('avg_entry')} → {trade.get('avg_exit')}"
                f" ({trade.get('exit_reason') or 'exit'}) · P&L {_money(pnl)}"
            )
        elif status == "open":
            lines.append(
                f"• ⚠️ {label}: STILL OPEN {filled - exited} sh @ {trade.get('avg_entry')}"
                f" · realized {_money(pnl)}"
            )
        elif status == "cancelled":
            lines.append(f"• {label}: entry never filled (cancelled)")
        elif status == "sent":
            ordered = quantity if quantity else "?"
            lines.append(f"• ⚠️ {label}: entry still working ({filled}/{ordered} filled)")
        else:
            detail = status.replace("_", " ") if status else "unknown status"
            reason = str(trade.get("attention_reason") or "").strip()
            lines.append(f"• ⚠️ {label}: {detail}" + (f" — {reason}" if reason else ""))
        shown += 1
    if shown == 0:
        lines.append("• no orders sent")
    lines.append(f"Total realized P&L: {_money(total)}")
    if skipped:
        listed = "; ".join(skipped[:5]) + (" …" if len(skipped) > 5 else "")
        lines.append(f"Skipped by the bridge: {len(skipped)} ({listed})")
    return lines


def format_daily_report(report: DailyReport) -> str:
    lines = [f"📊 Daily report · {report.session_date:%a %d %b %Y}", "", "Decisions by strategy"]
    if not report.outcomes:
        lines.append("• none recorded")
    for strategy in sorted(report.outcomes):
        counts = report.outcomes[strategy]
        parts = [
            f"{counts[outcome]} {_OUTCOME_LABELS[outcome]}"
            for outcome in _OUTCOME_ORDER
            if counts.get(outcome)
        ]
        parts.extend(
            f"{count} {outcome}"
            for outcome, count in sorted(counts.items())
            if outcome not in _OUTCOME_LABELS and count
        )
        lines.append(f"• {strategy} ({sum(counts.values())}): " + ", ".join(parts))
    lines.append("")
    lines.extend(_trade_lines(report))
    lines.append("")
    plural = "" if report.ai_calls == 1 else "s"
    lines.append(
        f"AI: {report.ai_calls} Claude review{plural} · ${report.ai_cost_usd.quantize(CENT):,.2f}"
    )
    if report.market_close_summary:
        lines.append(
            f"Market at close: {report.market_close_summary}"
            f" ({_et_clock(report.market_close_at)})"
        )
    else:
        lines.append("Market at close: unavailable")
    snapshot = _et_clock(report.last_snapshot_at) if report.last_snapshot_at else "none today"
    lines.append(f"Screener: last snapshot {snapshot}")
    return "\n".join(lines)


def startup_text(session_date: date) -> str:
    return (
        f"🤖 Autotrader notifier on for {session_date:%a %d %b %Y}: signals, orders, exits,"
        " market updates and the 16:05 ET report will arrive here."
    )


# --------------------------------------------------------------------------
# The session loop
# --------------------------------------------------------------------------


@dataclass(slots=True)
class _Outbound:
    text: str
    key: str | None
    rounds: int = 1
    future: asyncio.Future[bool] | None = None
    retry_at: float = 0.0


class _NotifierSession:
    def __init__(
        self,
        *,
        session_date: date,
        environment: NotifyEnvironment,
        notifier: TelegramNotifier,
        state: NotifyState,
    ) -> None:
        self._session_date = session_date
        self._environment = environment
        self._notifier = notifier
        self._state = state
        self._end_at = market_time(session_date, SESSION_END)
        self._report_at = market_time(session_date, REPORT_AT)
        log_directory = environment.paths.log_directory
        self._tailers = {
            name: LogTailer(log_directory / filename) for name, filename in log_files().items()
        }
        self._fills = FillCoalescer()
        self._outstanding: list[_Outbound] = []
        self._retries: list[_Outbound] = []
        self._in_flight: set[str] = set()
        self._next_decision_poll = float("-inf")
        self._next_market_poll = float("-inf")
        self._report_retry_at = float("-inf")
        self._quiet_until: dict[str, float] = {}
        self._skipped_updates: set[str] = set()
        self._next_schema_check = float("-inf")
        self._schema_status: SchemaStatus | None = None
        self._digest: dict[str, SignalDecision] = {}
        self._digest_members: dict[str, list[str]] = {}
        self._next_manager_poll = float("-inf")
        self._broker = BrokerWatch(session_date)
        # Started after SESSION_START (a restart): the log line is behind the
        # checkpoint, so the session opened no later than now.
        if environment.clock() >= market_time(session_date, SESSION_OPENS):
            self._broker.session_started(environment.clock())

    async def run(self) -> None:
        for name, tailer in self._tailers.items():
            tailer.start(self._state.tails.get(name))
        self._checkpoint()
        if "started" not in self._state.delivered:
            self._send(startup_text(self._session_date), key="started")
        while self._environment.clock() < self._end_at:
            try:
                await self._cycle()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - one bad cycle never stops the notifier
                self._log_quietly("cycle", logging.ERROR, "notifier cycle failed", exc_info=True)
            await self._environment.sleep(LOOP_TICK_SECONDS)

    async def shutdown(self) -> None:
        """Flush held fills, the digest and redeliveries, drain the sender, persist state."""
        try:
            self._poll_logs()
        except Exception:  # noqa: BLE001 - shutdown always drains and saves
            logger.exception("final log poll failed")
        for text in self._fills.drain():
            self._send(text)
        try:
            await self._maybe_send_digest(final=True)
        except Exception:  # noqa: BLE001 - shutdown always drains and saves
            logger.exception("final digest failed")
        for outbound in self._retries:
            self._enqueue(outbound)
        self._retries.clear()
        await self._notifier.aclose(drain_timeout=DRAIN_TIMEOUT_SECONDS)
        self._collect_outcomes(final=True)
        self._checkpoint()
        for tailer in self._tailers.values():
            tailer.close()

    async def _cycle(self) -> None:
        # Before the logs: a pending migration's exit-75 restarts are folded
        # into one alert from the very first poll.
        if self._environment.monotonic() >= self._next_schema_check:
            self._next_schema_check = self._environment.monotonic() + SCHEMA_CHECK_SECONDS
            await self._check_schema()
        self._poll_logs()
        self._broker.check(self._environment.clock())
        if self._broker.unreachable and self._alert_allowed(BROKER_UNREACHABLE, BROKER_ALERT_INTERVAL):
            logger.warning("%s: %s", BROKER_UNREACHABLE, self._broker.alert_text())
            self._send_alert(BROKER_UNREACHABLE, self._broker.alert_text())
            self._broker.alert_sent()
        for text in self._fills.due(self._environment.monotonic()):
            self._send(text)
        if self._environment.monotonic() >= self._next_decision_poll:
            self._next_decision_poll = self._environment.monotonic() + DECISION_POLL_SECONDS
            await self._poll_decisions()
        await self._maybe_send_digest()
        if self._environment.monotonic() >= self._next_manager_poll:
            self._next_manager_poll = self._environment.monotonic() + MANAGER_POLL_SECONDS
            try:
                await self._poll_manager()
            except Exception:  # noqa: BLE001 - manager news never blocks the rest
                self._log_quietly("manager", logging.ERROR, "manager poll failed", exc_info=True)
        if self._environment.monotonic() >= self._next_market_poll:
            self._next_market_poll = self._environment.monotonic() + MARKET_POLL_SECONDS
            try:
                await self._poll_market()
            except Exception:  # noqa: BLE001 - market news never blocks the rest
                self._log_quietly("market", logging.ERROR, "market poll failed", exc_info=True)
        if self._environment.clock() >= self._report_at:
            await self._maybe_send_report()
        self._collect_outcomes()
        self._redeliver_due()
        self._checkpoint()

    # -- sources -----------------------------------------------------------

    def _poll_logs(self) -> None:
        for name, tailer in self._tailers.items():
            for line in tailer.poll():
                self._handle_line(name, line)

    async def _check_schema(self) -> None:
        """Read-only ledger check; never migrates. One alert per session date."""
        try:
            status = await asyncio.to_thread(self._environment.read_schema_status)
        except Exception as exc:  # noqa: BLE001 - the rest of the notifier carries on
            self._log_quietly("schema", logging.WARNING, "schema check failed: %s", type(exc).__name__)
            return
        self._schema_status = status
        if status.current:
            return
        self._log_quietly(
            "schema",
            logging.CRITICAL,
            "%s: %s; trading children refuse to start until `autotraderctl db migrate`",
            status.token,
            status.describe(),
        )
        if SCHEMA_ALERT_KEY in self._state.delivered or SCHEMA_ALERT_KEY in self._in_flight:
            return
        self._send_alert(SCHEMA_ALERT_KEY, schema_alert_text(status), key=SCHEMA_ALERT_KEY)

    def _schema_refusal(self, line: str) -> bool:
        """A child's exit 75 while the schema is not current: already in the alert."""
        status = self._schema_status
        return (
            status is not None
            and not status.current
            and child_exit_code(line) == EXIT_SCHEMA_NOT_READY
        )

    def _handle_line(self, source: str, line: str) -> None:
        recovered = self._broker.observe(source, line, self._environment.clock())
        if recovered is not None:
            logger.info("%s over: %s", BROKER_UNREACHABLE, recovered)
            self._send_alert("broker_reconnected", recovered)
            # The next outage alerts at once, not after the last alert's half hour.
            if self._state.alerts.pop(BROKER_UNREACHABLE, None) is not None:
                self._state.dirty = True
        if source == "supervisor":
            if self._schema_refusal(line):
                return
            name = supervisor_child_name(line) or "unknown"
            # An exit the outage caused is the broker alert's (its count), not a line of its own.
            if self._broker.broker_exit(name, child_exit_code(line)):
                return
            text = supervisor_event_text(line)
            if not text:
                return
            if self._alert_allowed(f"child_exit:{name}", CHILD_EXIT_ALERT_INTERVAL):
                self._send_alert(f"child_exit:{name}", text)
            return
        tripwire = manager_tripwire(line)
        if tripwire is not None:
            token, text = tripwire
            if self._alert_allowed(f"tripwire:{token}", TRIPWIRE_ALERT_INTERVAL):
                self._send_alert(f"tripwire:{token}", text)
        if source == "bridge":
            self._handle_bridge_line(line)
            # The bridge already reports its own disconnects as CRITICAL.
            problem = ibkr_session_problem(line, include_connection_lost=False)
        else:
            problem = ibkr_session_problem(line)
        if problem and self._alert_allowed("ibkr_session"):
            self._send_alert(f"ibkr_session:{problem}", ibkr_alert_text(problem, source))

    def _handle_bridge_line(self, line: str) -> None:
        event = parse_bridge_line(line)
        if event is None:
            return
        text = bridge_event_text(event)
        if text is None:
            return
        if event.kind == "FILL" and event.symbol:
            self._fills.add(event.symbol, text, self._environment.monotonic())
            return
        # A held fill always reaches the phone before the exit that follows it.
        for held in self._fills.drain():
            self._send(held)
        self._send(text)

    async def _poll_decisions(self) -> None:
        try:
            decisions = await load_signal_decisions(self._session_date)
        except (aiosqlite.Error, OSError) as exc:
            self._log_quietly("database", logging.WARNING, "decision poll failed: %s", exc)
            return
        for decision in decisions:
            key = f"decision:{decision.decision_id}"
            if key in self._state.delivered or key in self._in_flight:
                continue
            if not is_real_decision(decision):
                # Managed and simulated (shadow) decisions wait for the digest.
                self._digest.setdefault(key, decision)
                continue
            self._send(format_decision(decision), key=key)
            logger.info(
                "queued %s for %s (%s)", decision.outcome, decision.symbol, decision.strategy
            )

    async def _maybe_send_digest(self, *, final: bool = False) -> None:
        """One digest per ET half hour with the shadow decisions since the last."""
        if not self._digest:
            return
        now = self._environment.clock()
        # The decisions it carries, not the digest's key, keep a restart from
        # repeating one; a final flush (report, shutdown) has its own key.
        key = f"digest:final-{now:%H%M%S}" if final else f"digest:{digest_boundary(now):%H%M}"
        if key in self._state.delivered or key in self._in_flight:
            return
        pending = [
            (member, decision)
            for member, decision in self._digest.items()
            if member not in self._state.delivered and member not in self._in_flight
        ]
        self._digest.clear()
        if not pending:
            return
        managed = await load_managed_rows(self._session_date)
        text = format_digest([decision for _member, decision in pending], managed, now)
        members = [member for member, _decision in pending]
        self._digest_members[key] = members
        self._in_flight.update(members)
        self._send(text, key=key)
        logger.info("digest %s queued with %d shadow decisions", key, len(members))

    async def _poll_manager(self) -> None:
        """Plan summaries after the 09:20 and 09:46 plans; one alert per outage."""
        from trading.manager.report import format_plan, manager_outage, symbols_for
        from trading.manager.store import list_plans

        session = market_session(self._session_date)
        if session is None:
            return
        try:
            plans = await list_plans(self._session_date)
        except aiosqlite.OperationalError:
            # Migration 16 is pending: the schema alert already says so.
            return
        for slot in PLAN_SUMMARY_SLOTS:
            key = f"plan:{slot.replace(':', '')}"
            if key in self._state.delivered or key in self._in_flight:
                continue
            plan = next((item for item in plans if item.slot == slot and item.valid), None)
            if plan is None or plan.plan is None:
                continue
            symbols = await symbols_for([entry.con_id for entry in plan.plan.focus])
            self._send(format_plan(plan, symbols=symbols), key=key)
            logger.info("plan summary %s queued", plan.plan_id)
        outage = manager_outage(plans, session, self._environment.clock())
        if outage is not None:
            key, text = outage
            if key not in self._state.delivered and key not in self._in_flight:
                self._send(text, key=key)
                logger.warning("manager outage alert queued: %s", key)

    async def _poll_market(self) -> None:
        now = self._environment.clock()
        try:
            context = await self._environment.read_market_context(
                self._session_date, now=now, max_age=MARKET_CONTEXT_MAX_AGE
            )
        except Exception as exc:  # noqa: BLE001 - treated as no fresh context
            self._log_quietly(
                "market_context", logging.WARNING, "market context unavailable: %s",
                type(exc).__name__,
            )
            context = None
        if context is not None:
            self._follow_regime(context, now)
        for moment in MARKET_UPDATE_TIMES:
            key = market_update_key(moment)
            if (
                key in self._state.delivered
                or key in self._in_flight
                or key in self._skipped_updates
            ):
                continue
            due_at = market_time(self._session_date, moment)
            if now < due_at:
                continue
            if now >= due_at + MARKET_UPDATE_GRACE:
                self._skipped_updates.add(key)
                logger.warning(
                    "market update %s ET skipped for today: not sent by %s ET",
                    f"{moment:%H:%M}",
                    f"{(due_at + MARKET_UPDATE_GRACE).astimezone(MARKET_TIMEZONE):%H:%M}",
                )
                continue
            if context is None:
                continue  # waits for a fresh context until the grace runs out
            hot = await self._hot_list(now)
            self._send(format_market_update(context, hot), key=key)
            logger.info("market update %s ET queued", f"{moment:%H:%M}")

    def _follow_regime(self, context: MarketContext, now: datetime) -> None:
        """Alert when the regime differs from the one the phone last heard.

        Comparing with the last reported regime (not the previous context)
        means a flip that the rate limit held back is still reported once the
        limit allows, so the phone always converges on the current regime.
        """
        regime = str(context.regime)
        told = self._state.market_regime
        if regime == told:
            return
        if told is None or now < market_time(self._session_date, REGIME_ALERTS_FROM):
            self._state.market_regime = regime
            self._state.dirty = True
            return
        if not self._alert_allowed("market_regime", REGIME_ALERT_INTERVAL):
            return
        self._state.market_regime = regime
        self._state.dirty = True
        self._send(format_regime_change(context))
        logger.info("market regime %s -> %s alert queued", told, regime)

    async def _hot_list(self, now: datetime) -> Sequence[HotListRow] | None:
        """The newest sweep's hot list, or None when it is unreadable or stale."""
        try:
            rows = tuple(await self._environment.read_hot_list(self._session_date))
        except Exception as exc:  # noqa: BLE001 - the update goes out without it
            self._log_quietly(
                "hot_list", logging.WARNING, "hot list unavailable: %s", type(exc).__name__
            )
            return None
        if rows and now - rows[0].observed_at > MARKET_CONTEXT_MAX_AGE:
            return None
        return rows

    async def _maybe_send_report(self) -> None:
        if "report" in self._state.delivered or "report" in self._in_flight:
            return
        if self._environment.monotonic() < self._report_retry_at:
            return
        try:
            report = await collect_daily_report(
                self._session_date,
                self._environment.paths,
                read_market_context=self._environment.read_market_context,
                now=self._environment.clock(),
            )
        except (aiosqlite.Error, OSError) as exc:
            self._report_retry_at = self._environment.monotonic() + REDELIVERY_COOLDOWN_SECONDS
            logger.warning("daily report not built yet: %s", exc)
            return
        for held in self._fills.drain():
            self._send(held)
        await self._maybe_send_digest(final=True)
        self._send(format_daily_report(report), key="report")
        logger.info("daily report queued for %s", self._session_date.isoformat())

    def _alert_allowed(self, key: str, interval: timedelta = IBKR_ALERT_INTERVAL) -> bool:
        now = self._environment.clock()
        last = _parse_time(self._state.alerts.get(key))
        if last is not None and now - last < interval:
            return False
        self._state.alerts[key] = now.isoformat()
        self._state.dirty = True
        return True

    # -- delivery bookkeeping ---------------------------------------------

    def _send(self, text: str, *, key: str | None = None) -> None:
        if key is not None:
            self._in_flight.add(key)
        self._enqueue(_Outbound(text=text, key=key))

    def _send_alert(self, alert: str, text: str, *, key: str | None = None) -> None:
        """An alert or child-exit line; logged as queued so the day's checker can
        verify delivery (live 2026-10-14: the lines went out and the log said nothing)."""
        logger.info("alert queued: %s", alert)
        self._send(text, key=key)

    def _enqueue(self, outbound: _Outbound) -> None:
        outbound.future = self._notifier.notify(outbound.text)
        self._outstanding.append(outbound)

    def _collect_outcomes(self, *, final: bool = False) -> None:
        waiting: list[_Outbound] = []
        for outbound in self._outstanding:
            future = outbound.future
            if future is None or not future.done():
                waiting.append(outbound)
                continue
            if not future.cancelled() and future.result() is True:
                self._settle(outbound, "sent")
            elif final:
                # Left unsettled: a restart for this date before 16:15 retries
                # keyed messages (decisions, the report).
                if outbound.key is not None:
                    self._in_flight.discard(outbound.key)
                logger.error(
                    "notification undelivered at shutdown: %s", outbound.key or "log event"
                )
            elif outbound.rounds < MAX_DELIVERY_ROUNDS:
                outbound.rounds += 1
                outbound.retry_at = self._environment.monotonic() + REDELIVERY_COOLDOWN_SECONDS
                self._retries.append(outbound)
            else:
                logger.error(
                    "notification dropped after %d delivery rounds: %s",
                    MAX_DELIVERY_ROUNDS,
                    outbound.key or "log event",
                )
                self._settle(outbound, "dropped")
        self._outstanding = waiting

    def _settle(self, outbound: _Outbound, result: str) -> None:
        if outbound.key is None:
            return
        self._in_flight.discard(outbound.key)
        self._state.delivered[outbound.key] = result
        # A digest settles every decision it carried.
        for member in self._digest_members.pop(outbound.key, ()):
            self._in_flight.discard(member)
            self._state.delivered[member] = result
        self._state.dirty = True

    def _redeliver_due(self) -> None:
        now = self._environment.monotonic()
        due = [outbound for outbound in self._retries if outbound.retry_at <= now]
        if not due:
            return
        self._retries = [outbound for outbound in self._retries if outbound.retry_at > now]
        for outbound in due:
            self._enqueue(outbound)

    def _checkpoint(self) -> None:
        for name, tailer in self._tailers.items():
            checkpoint = tailer.checkpoint()
            if self._state.tails.get(name) != checkpoint:
                self._state.tails[name] = checkpoint
                self._state.dirty = True
        if not self._state.dirty:
            return
        try:
            self._state.save()
        except (OSError, PathSecurityError) as exc:
            self._log_quietly(
                "state", logging.ERROR, "cannot save notifier state: %s", type(exc).__name__
            )

    def _log_quietly(
        self, key: str, level: int, message: str, *args: object, exc_info: bool = False
    ) -> None:
        """Repeated local problems are logged at most every few minutes."""
        now = self._environment.monotonic()
        if now < self._quiet_until.get(key, float("-inf")):
            return
        self._quiet_until[key] = now + LOCAL_PROBLEM_LOG_INTERVAL_SECONDS
        logger.log(level, message, *args, exc_info=exc_info)


# --------------------------------------------------------------------------
# Entry points (wired by autotraderctl notify run / notify test)
# --------------------------------------------------------------------------


def _read_token(environment: NotifyEnvironment) -> str | None:
    try:
        return environment.read_token()
    except (KeychainReadError, ValueError, OSError):
        return None


def _configure_logging(
    environment: NotifyEnvironment, token: str | None, *, console_level: int
) -> None:
    """Private shadow log plus console, both redacting the token."""
    if not environment.configure_logging:
        return
    secrets = token_secrets(token) if token else ()
    shadow_logger = configure_shadow_logging(user_paths=environment.paths, secrets=secrets)
    console = logging.StreamHandler(sys.stderr)
    console.setLevel(console_level)
    console.setFormatter(RedactingFormatter(secrets=secrets))
    shadow_logger.addHandler(console)


async def resolve_chat(api: TelegramBotAPI, paths: UserPaths) -> TelegramChat | None:
    """The cached chat, else the newest private /start (which is then cached)."""
    config = telegram_config_file(paths)
    try:
        cached = load_chat_id(config)
    except TelegramConfigError as exc:
        logger.warning("ignoring %s: %s", config.name, exc)
        cached = None
    if cached is not None:
        return TelegramChat(chat_id=cached, label="your Telegram chat")
    chat = await discover_chat(api)
    if chat is not None:
        save_chat_id(config, chat.chat_id)
        logger.info("Telegram chat linked: %s", chat.label)
    return chat


async def send_test_message(*, env: NotifyEnvironment | None = None) -> int:
    """``autotraderctl notify test``: link the chat and send one message. Returns 0 or 1."""
    environment = env or NotifyEnvironment.current()
    out = environment.out or sys.stdout
    token = _read_token(environment)
    if token is None:
        print("No Telegram bot token is stored in the Keychain yet.\n", file=out)
        print(SETUP_INSTRUCTIONS, file=out)
        return 1
    if not is_valid_bot_token(token):
        print(
            "The Keychain item does not look like a Telegram bot token"
            " (expected digits, a colon, then letters, like 123456789:AAE...).\n",
            file=out,
        )
        print(SETUP_INSTRUCTIONS, file=out)
        return 1
    _configure_logging(environment, token, console_level=logging.WARNING)
    config_path = telegram_config_file(environment.paths)
    try:
        async with TelegramBotAPI(token, transport=environment.transport) as api:
            bot = await api.get_me()
            username = bot.get("username")
            bot_label = f"@{username}" if isinstance(username, str) and username else "your bot"
            chat = await resolve_chat(api, environment.paths)
            if chat is None:
                print(f"Bot {bot_label} works, but no /start from you has arrived yet.", file=out)
                print(START_INSTRUCTIONS, file=out)
                return 1
            notifier = TelegramNotifier(
                api,
                chat_id=chat.chat_id,
                sleep=environment.sleep,
                monotonic=environment.monotonic,
            )
            try:
                delivered = await notifier.notify(TEST_MESSAGE)
            finally:
                await notifier.aclose()
    except TelegramAPIError as exc:
        print(f"Telegram refused the request: {exc}", file=out)
        if exc.status in {401, 404}:
            print(
                "The bot token is wrong or was revoked. Copy the current token from @BotFather"
                f" and replace it:\n  {REPLACE_TOKEN_COMMAND}",
                file=out,
            )
        return 1
    except (TelegramConfigError, PathSecurityError, OSError) as exc:
        print(f"Telegram configuration problem: {exc}", file=out)
        return 1
    if delivered:
        print(f'Sent "{TEST_MESSAGE}" to {chat.label} via {bot_label}.', file=out)
        print("Phone notifications are ready.", file=out)
        return 0
    print(
        "The test message was not delivered (details above and in the shadow log)."
        f" If you changed bots or Telegram accounts, delete {config_path},"
        " send /start to the bot again, and rerun: autotraderctl notify test",
        file=out,
    )
    return 1


async def run_notifier(*, session_date: date, env: NotifyEnvironment | None = None) -> int:
    """``autotraderctl notify run --date D``: forward D's events until 16:15 ET.

    Returns 0 after a full session (or when D's session is already over) and
    1 when Telegram is not set up, so a supervisor restart retries later.
    """
    environment = env or NotifyEnvironment.current()
    out = environment.out or sys.stdout
    if environment.clock() >= market_time(session_date, SESSION_END):
        print(f"notifier: {session_date.isoformat()} ended at 16:15 ET; nothing to do", file=out)
        return 0
    token = _read_token(environment)
    if token is None or not is_valid_bot_token(token):
        _configure_logging(environment, None, console_level=logging.INFO)
        logger.error("no usable Telegram bot token in the Keychain; notifications are off")
        print(SETUP_INSTRUCTIONS, file=out)
        return 1
    _configure_logging(environment, token, console_level=logging.INFO)
    state = NotifyState.load(state_file(environment.paths, session_date), session_date)
    try:
        async with TelegramBotAPI(token, transport=environment.transport) as api:
            chat = await resolve_chat(api, environment.paths)
            if chat is None:
                logger.error(
                    "no Telegram chat linked: send /start to the bot, then run"
                    " 'autotraderctl notify test'"
                )
                return 1
            notifier = TelegramNotifier(
                api,
                chat_id=chat.chat_id,
                sleep=environment.sleep,
                monotonic=environment.monotonic,
            )
            session = _NotifierSession(
                session_date=session_date,
                environment=environment,
                notifier=notifier,
                state=state,
            )
            logger.info("notifier running for %s until 16:15 ET", session_date.isoformat())
            try:
                await session.run()
            finally:
                await session.shutdown()
    except TelegramAPIError as exc:
        logger.error("Telegram is unavailable: %s", exc)
        return 1
    except (TelegramConfigError, PathSecurityError) as exc:
        logger.error("Telegram configuration problem: %s", exc)
        return 1
    logger.info("notifier finished for %s", session_date.isoformat())
    return 0


__all__ = [
    "BRIDGE_FORWARD_KINDS",
    "LOGIN_ALERT_TEXT",
    "LOG_FILES",
    "log_files",
    "MARKET_CONTEXT_MAX_AGE",
    "MARKET_UPDATE_GRACE",
    "MARKET_UPDATE_TIMES",
    "MISSING_FILE_CHECKPOINT",
    "REGIME_ALERT_INTERVAL",
    "REPORT_AT",
    "SCHEMA_ALERT_KEY",
    "SCHEMA_CHECK_SECONDS",
    "SESSION_END",
    "SETUP_INSTRUCTIONS",
    "TEST_MESSAGE",
    "BridgeEvent",
    "DailyReport",
    "FillCoalescer",
    "LogTailer",
    "MarketContextReader",
    "NotifyEnvironment",
    "NotifyState",
    "SignalDecision",
    "bridge_event_text",
    "bridge_state_files",
    "child_exit_code",
    "manager_tripwire",
    "supervisor_child_name",
    "collect_daily_report",
    "format_daily_report",
    "format_decision",
    "format_market_update",
    "format_regime_change",
    "ibkr_alert_text",
    "ibkr_session_problem",
    "load_bridge_trades",
    "load_signal_decisions",
    "market_time",
    "market_update_key",
    "regime_label",
    "parse_bridge_line",
    "resolve_chat",
    "run_notifier",
    "send_test_message",
    "startup_text",
    "state_file",
    "supervisor_event_text",
]

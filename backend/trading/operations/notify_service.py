"""Push the trading day's important events to the owner's phone via Telegram.

``run_notifier`` follows one session date until 16:15 ET and forwards:
  a. decisions Claude reviewed (a real setup), from the shadow database:
     real MOMO's (a sendable identity) at once, as before; managed MOMO's
     (AP_MOMO_HOD_V1) and the simulated strategies' in one digest every
     30 minutes (ET :00 and :30), flushed before the daily report;
  b. PAPER bridge order lines (SENT, FILL, EXIT, CLOSED, CANCEL, CANCELLED,
     FLATTEN, FLAT, CRITICAL, and loss-limit or kill-switch skips);
  c. supervisor child exits, and the restart cap's suspended, resumed and
     restart-storm lines (each once per child per half hour; a suspension or
     resumption that contradicts the last one the phone heard always goes);
     outside this window the Telegram child forwards the restart cap's lines
     by the same rule (``trading.operations.overnight_watch``);
  d. competing IBKR logins and lost TWS connections seen in the data logs: a
     competing login through ``gateway_watch.CompetingLoginWatch`` (a page at
     the first line, re-paged every 10 minutes while its lines keep coming,
     over after a 10-minute silence; after 16:15 the Telegram child watches
     the recorder's log the same way), a lost connection at most every 10
     minutes;
  e. the 16:05 ET daily report, closing with the last market context;
  f. market updates at 09:15, 12:00 and 15:30 ET and market regime changes,
     from the screener's market context;
  g. a database that needs migration, once per session (see below);
  h. the AI day manager (migration 16): a plan summary after the 09:20 and
     09:46 plans, and one alert per outage (no plan in force, a failed or
     timed-out plan, the plan budget spent). While migration 16 is pending
     these are skipped; everything else is unchanged;
  i. an unreachable IB Gateway (live 2026-10-14: off all session, no alert):
     one "IB Gateway unreachable since HH:MM ET" once the bridge's "TWS did
     not answer", the screener's "TWS connection failed" and the strategy
     children's handshake failures make three in a row or a streak two
     minutes long, or when no screener sweep has come 30 minutes after the
     screener's 08:00 ET window opened; repeated at most every 30 minutes
     while the newest evidence is a failure (a lone success inside the
     outage stops no repeat), or worded "status unclear" once a success
     has had nothing after it for 10 minutes, and one "reconnected" line
     once three successes in a row (one per screener sweep) have come and
     two minutes have passed since the first of them without a failure; an
     outage's first page never says "restart IB Gateway now", only a
     repeat (below). At most one such pair
     per half hour (the shared ``trading.operations.hysteresis`` machine);
     what it holds back is a flap, counted in the next digest. An IBKR-side
     loss the bridge reports (TWS 1100/2110 not restored within two minutes,
     while the bridge runs inside its window) opens it too, worded "IBKR
     connectivity lost"; the screener's TWS 2110 and broken-farm codes
     (2103/2105/2157, its opt-in status lines) are failure signals worded the
     same way; so does IBKR refusing the screener's data with the
     Gateway connected (six daily-history timeouts in a row, or a failed
     sweep), worded "IBKR is refusing prices". Both IBKR-side wordings ask
     the owner to log out of the IBKR app and website first and to restart
     the IB Gateway if it lasts; once the outage has lasted 15 minutes they
     say "restart IB Gateway now", with the log-out step first when the
     outage's earlier pages said only "log in";
  j. the Sentinel (``trading.operations.sentinel``, T-9): from the bridge's
     structured state (today's state file, the live ledger, the heartbeat
     row; never a log line) it fences one sendable identity at the owner's
     daily-loss or losing-streak threshold, once per identity per day, with
     one "⛔ auto-fence" message (log-only until ``SENTINEL_FENCES_LIVE``: no
     row, one "🔎 auto-fence (log-only)" line per identity per day instead);
     while it cannot see the bridge state it sends "sentinel blind since
     HH:MM ET" every half hour and fences nothing;
  k. at start (07:55 ET), every identity whose fence is stopped (an earlier
     day's auto-fence, the nightly replay's mismatch fence, an owner's /halt);
  l. an OPS section in the 16:05 report: restarts per child, suspensions and
     restart storms, Gateway outage minutes and flaps, launch and preflight
     refusals, fenced identities, and trades and realized P&L per identity.
     An announced outage counts once, as an outage, whatever opened it (a
     failed connect, an IBKR-side loss, a data-refused run) and also when the
     pair budget held it back until the sustain limit or the window announced
     it; a flap is a flip the phone never heard as an outage;
  m. a sendable identity without a size (plan P3-E, from P3-C's open
     question 1): a strategy child's CRITICAL size line (``SIZE_TRIPWIRES``:
     the session-start size read's refusals, an event written mid-session,
     the pipeline's ``identity_sizing_missing`` block), with normal sound,
     each (token, identity) once per session date
     (``size_tripwire:<token>:<identity>`` in the per-date state), so the
     owner hears on the day which identities trade no setups, never only in
     a log.

It reads the database, the logs and the bridge state files, and writes one
kind of row: the Sentinel's ``paper:<IDENTITY>`` fence, stop-only. Nothing
here can place, modify, or cancel an order, resume a fence, or touch the
global kill switch. What was delivered, how far each log was read, whether
the report went out, the regime last reported, the Sentinel's fences and the
day's OPS counts are kept per date in a private state file, so a restart
neither resends nor skips.

Unlike every other child it starts while a migration is pending: it never
migrates, writes only the fence table (in every schema), and reads only
tables that exist since migrations 2 and 11, so it keeps forwarding the
bridge and the supervisor. It checks the schema (read-only) at start and
every five minutes; while the schema is not current it sends one alert per
session and drops the children's repeated exit-75 restarts instead of
sending one message per restart.

Every followed log survives the 18:35 ET rotation (a rename and a new file,
or a copy then a truncate): a new file identity, a file shorter than the read
offset, or one whose first 64 bytes changed is read again from its start, and
the archive is never replayed. The state lines the rotation carries into the
new supervisor.log (T-3 writes them just before its LOG_ROTATED marker; lines
after it, dated before it, count too) are neither forwarded again nor counted
twice in the OPS lines (one rule: ``rotation_carried``, shared with the
Telegram child's after-hours forwarder in ``trading.operations.log_tailer``).
"""
from __future__ import annotations

import asyncio
import bisect
from collections import Counter, defaultdict
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
import json
import logging
from pathlib import Path
import re
import sys
import time as monotonic_time
from typing import TYPE_CHECKING, Any, Protocol, TextIO

import aiosqlite
import httpx

from db.core import get_db
from trading.operations.gateway_watch import (
    COMPETING_LOGIN_REPAGE,
    CompetingDecision,
    CompetingKind,
    CompetingLoginWatch,
)
from trading.operations.hysteresis import OPEN as HYSTERESIS_OPEN
from trading.operations.hysteresis import Event as HysteresisEvent, Hysteresis
from trading.operations.keychain import (
    TELEGRAM_KEYCHAIN_ACCOUNT,
    TELEGRAM_KEYCHAIN_SERVICE,
    KeychainReadError,
    read_telegram_bot_token,
)
from trading.operations.log_tailer import (
    MISSING_FILE_CHECKPOINT,
    LogTailer,
    RotationCarried,
    line_time as _line_time,
    parse_iso as _parse_time,
    rotation_carried,
    without_carried,
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
from trading.operations.sentinel import (
    BLIND_ALERT,
    BLIND_KEY,
    BLIND_REPEAT,
    HeartbeatReader,
    Sentinel,
    blind_text,
    read_bridge_heartbeat_at,
    resume_command,
)
from trading.operations.supervisor import NOTIFIER_CHILD, bridge_window
from trading.operations.telegram_tools import child_starts_on, read_log_tail, restarts_text
from trading.risk.engine import IDENTITY_SIZING_MISSING
from trading.risk.identity_sizing import (
    ACCOUNT_MODE_WITHOUT_CEILING,
    IDENTITY_SIZE_ABOVE_CEILING,
    IDENTITY_SIZE_STORE_UNREADABLE,
    SENDABLE_WITHOUT_CEILING,
    SIZE_EVENT_CHAIN_BROKEN,
    SIZE_EVENT_WRITTEN_MID_SESSION,
    SIZE_READ_BEFORE_SESSION_START,
    SIZE_WRITER_IDENTITY_SKIPPED,
    SIZE_WRITER_NIGHT_FAILED,
)
from trading.research.live_ledger import bridge_state_directory
from trading.scheduler.sessions import MARKET_TIMEZONE, market_session
from trading.sendable_identities import SENDABLE_IDENTITIES, is_sendable_identity
from trading.storage.migrations import SchemaStatus, inspect_schema
from trading.storage.runtime_fence import SQLitePaperKillStore

if TYPE_CHECKING:
    from trading.market.context import MarketContext
    from trading.market.screener import HotListRow


logger = logging.getLogger("autotrader.shadow.notify")

SESSION_END = time(16, 15)
REPORT_AT = time(16, 5)
LOOP_TICK_SECONDS = 1.0
DECISION_POLL_SECONDS = 5.0
FILL_COALESCE_SECONDS = 10.0
# The IBKR alerts' throttle and a competing login's re-page: one number.
IBKR_ALERT_INTERVAL = COMPETING_LOGIN_REPAGE
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
# The Sentinel reads the bridge state this often (its blindness rule allows 5 minutes).
SENTINEL_CHECK_SECONDS = 30.0
# Fenced identities announced once per date, right after the startup line.
FENCES_AT_START_KEY = "fences_at_start"
# The 16:05 report's OPS lines read today's supervisor.log (rotated daily at
# 18:35 ET, so one day is far below this).
OPS_LOG_TAIL_BYTES = 4 << 20
OPS_ITEMS_SHOWN = 5
# The bridge's close checkpoints (paper_bridge FLATTEN_BEFORE_CLOSE and
# VERIFY_BEFORE_CLOSE; a test pins them): the flatten is due at 15:45 ET and the
# bridge checks it is flat at 15:50 ET (both earlier on a half day).
FLATTEN_BEFORE_CLOSE = timedelta(minutes=15)
VERIFY_BEFORE_CLOSE = timedelta(minutes=10)
# A flatten the state first shows this long after it was due ran late (live
# 2026-10-15: the Mac slept from 14:47 ET and the bridge flattened at 16:12).
FLATTEN_LATE_AFTER = timedelta(minutes=2)
# Observations of the bridge state kept in the per-date alerts map: what was
# held at the first look at/after the 15:50 check (before the close), and when
# the state first showed a flatten (the state file's write time).
HELD_AT_VERIFY_KEY = "ops_held_at_verify"
FLATTEN_SEEN_KEY = "ops_flatten_seen_at"
# What the notifier itself counts for the OPS lines (persisted per date).
GATEWAY_OUTAGES = "gateway_outages"
GATEWAY_UNREACHABLE_SECONDS = "gateway_unreachable_seconds"
# The part of the unreachable seconds that was IBKR refusing prices with the
# Gateway connected (an outage opened by data refused, N15b L-10).
GATEWAY_REFUSED_SECONDS = "gateway_refused_seconds"
GATEWAY_FLAPS = "gateway_flaps"
# The announced Gateway outage as the notifier last saw it (its first failure
# and the kind that opened it), kept in the per-date alerts map: the Telegram
# watch's evidence when it takes the Gateway over at 16:15 (N15b L-1). Paged:
# BROKER_UNREACHABLE's alert time is there too.
GATEWAY_OUTAGE_SINCE_KEY = "gateway_outage_since"
GATEWAY_OUTAGE_KIND_KEY = "gateway_outage_kind"
# The announced outage's latest check (BrokerWatch.checked_at), re-stamped at
# most once per GATEWAY_OUTAGE_STAMP_INTERVAL while the outage lasts (one state
# write a minute): how the Telegram watch tells a running notifier's outage
# from the one a notifier that stopped early left in its file (N15c LOW-1).
GATEWAY_OUTAGE_CHECKED_KEY = "gateway_outage_checked_at"
GATEWAY_OUTAGE_STAMP_INTERVAL = timedelta(minutes=1)
# The saved outage is current at the notifier's end (SESSION_END, the
# supervisor's NOTIFIER_WINDOW end; a test pins them equal) only when it was
# stamped at most this long before it: the stamp's cadence plus a slow last
# cycle. Stamped earlier, the notifier stopped early (a crash loop, a
# suspension, a hang) and its file says nothing about the evening.
GATEWAY_OUTAGE_FRESH_FOR = timedelta(minutes=5)
PREFLIGHT_REFUSED = "preflight_refused"
_PREFLIGHT_REFUSED = re.compile(r"\bPREFLIGHT_REFUSED\b")
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
# A sendable identity without a size for the session (plan P3-C/P3-E): the
# session-start read's CRITICAL tokens (trading.risk.identity_sizing), the
# event it ignored as written mid-session, and the pipeline's block of every
# setup of a run without a size; and the nightly size writer skipping one
# identity (P3-E fix 1) or failing for the whole night (fix 3). Forwarded
# each (token, identity) once per date: here from the strategy children's
# logs with normal sound; at night by the Telegram child's overnight watch
# from the replay child's log, silent (nothing to do before the session,
# whose start read pages again with sound if the problem lasts).
SIZE_TRIPWIRES = (
    IDENTITY_SIZE_STORE_UNREADABLE,
    IDENTITY_SIZE_ABOVE_CEILING,
    ACCOUNT_MODE_WITHOUT_CEILING,
    SIZE_EVENT_CHAIN_BROKEN,
    SIZE_EVENT_WRITTEN_MID_SESSION,
    SIZE_READ_BEFORE_SESSION_START,
    SENDABLE_WITHOUT_CEILING,
    IDENTITY_SIZING_MISSING,
    SIZE_WRITER_IDENTITY_SKIPPED,
    SIZE_WRITER_NIGHT_FAILED,
)
# Only the CRITICAL line itself (``<time> CRITICAL <logger> <token>: ...``),
# never a decision line that merely names the block as its risk code, and
# never a line of the stack the CRITICAL carries.
# Every size CRITICAL names its identity right after the token
# (``<token>: <IDENTITY> ...``); the night writer's whole-night failure names
# none. Pages are de-duplicated per (token, identity): two identities blocked
# for one reason are two pages, each once (N19b-OPS LOW-2).
_SIZE_TRIPWIRE = re.compile(
    r"\bCRITICAL\b.*?\b(?P<token>" + "|".join(re.escape(token) for token in SIZE_TRIPWIRES) + r"):"
    r"(?: (?P<identity>[A-Z][A-Z0-9_]{0,63})\b)?"
)
SIZE_TRIPWIRE_KEY = "size_tripwire:{token}:{identity}"
# The key part of a line that names no identity.
ALL_IDENTITIES = "*"
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
    # The Sentinel's only write path (stop one identity) and its freshness source.
    fence_store: SQLitePaperKillStore = field(default_factory=SQLitePaperKillStore)
    read_bridge_heartbeat: HeartbeatReader = read_bridge_heartbeat_at

    @classmethod
    def current(cls) -> NotifyEnvironment:
        return cls(paths=UserPaths.current())


def market_time(session_date: date, moment: time) -> datetime:
    return datetime.combine(session_date, moment, tzinfo=MARKET_TIMEZONE)


# --------------------------------------------------------------------------
# Small value helpers
# --------------------------------------------------------------------------


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


# The per-date state file is small; anything larger is not the notifier's.
MAX_STATE_FILE_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class GatewayOutage:
    """The notifier's announced Gateway outage for a date, as its state file has
    it; ``checked_at``: its latest stamped check (None in a file written before
    the stamp existed)."""

    since: datetime
    kind: str
    paged: bool
    checked_at: datetime | None = None


def gateway_outage_current(outage: GatewayOutage, session_date: date) -> bool:
    """Whether the saved outage is still the notifier's view at its end (N15c
    LOW-1): stamped on ``session_date`` (ET), no earlier than
    ``GATEWAY_OUTAGE_FRESH_FOR`` before ``SESSION_END``. A notifier that stopped
    early left an outage that may have ended since; a file without a stamp is
    never current."""
    checked = outage.checked_at
    if checked is None or checked.astimezone(MARKET_TIMEZONE).date() != session_date:
        return False
    return checked >= market_time(session_date, SESSION_END) - GATEWAY_OUTAGE_FRESH_FOR


def notifier_gateway_outage(paths: UserPaths, session_date: date) -> GatewayOutage | None:
    """The announced outage the notifier last saw on ``session_date`` (N15b L-1),
    read-only and never through a link; None when there is none, or when the
    file is missing, unreadable or not this date's. Whether it is still current
    is ``gateway_outage_current``'s question."""
    path = state_file(paths, session_date)
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_STATE_FILE_BYTES:
            return None
        raw = json.loads(path.read_bytes().decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    if not isinstance(raw, dict) or raw.get("session_date") != session_date.isoformat():
        return None
    alerts = raw.get("alerts")
    if not isinstance(alerts, dict):
        return None
    since = _parse_time(alerts.get(GATEWAY_OUTAGE_SINCE_KEY))
    kind = alerts.get(GATEWAY_OUTAGE_KIND_KEY)
    if since is None or not isinstance(kind, str):
        return None
    return GatewayOutage(
        since=since,
        kind=kind,
        paged=isinstance(alerts.get(BROKER_UNREACHABLE), str),
        checked_at=_parse_time(alerts.get(GATEWAY_OUTAGE_CHECKED_KEY)),
    )


@dataclass(slots=True)
class NotifyState:
    """What one session date has already delivered; survives restarts.

    ``delivered`` maps ``decision:<id>``, ``market:<HHMM>``, ``report`` and
    ``started`` to ``sent`` or ``dropped``; ``tails`` holds each log's file
    identity and the byte offset of its next unread line; ``market_regime`` is
    the regime the phone was last told about (or the silent baseline);
    ``alerts`` holds alert times, the Sentinel's memory among them;
    ``counters`` the OPS counts the notifier keeps itself (Gateway outages,
    unreachable seconds and flaps, bridge preflight refusals).
    """

    path: Path
    session_date: date
    delivered: dict[str, str] = field(default_factory=dict)
    tails: dict[str, dict[str, int]] = field(default_factory=dict)
    alerts: dict[str, str] = field(default_factory=dict)
    market_regime: str | None = None
    counters: dict[str, int] = field(default_factory=dict)
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
                    # The head fingerprint is optional (state files written before it).
                    state.tails[str(name)] = {
                        key: int(checkpoint[key])
                        for key in ("dev", "ino", "offset", "head", "head_len")
                        if _int(checkpoint.get(key)) is not None
                    }
        alerts = raw.get("alerts")
        if isinstance(alerts, dict):
            state.alerts = {
                str(key): value for key, value in alerts.items() if isinstance(value, str)
            }
        regime = raw.get("market_regime")
        if isinstance(regime, str) and regime:
            state.market_regime = regime
        counters = raw.get("counters")
        if isinstance(counters, dict):
            state.counters = {
                str(key): number
                for key, value in counters.items()
                if (number := _int(value)) is not None and number >= 0
            }
        return state

    def count(self, key: str, amount: int = 1) -> None:
        if amount:
            self.counters[key] = self.counters.get(key, 0) + amount
            self.dirty = True

    def save(self) -> None:
        payload = {
            "version": STATE_VERSION,
            "session_date": self.session_date.isoformat(),
            "delivered": self.delivered,
            "tails": self.tails,
            "alerts": self.alerts,
            "market_regime": self.market_regime,
            "counters": self.counters,
        }
        encoded = json.dumps(payload, sort_keys=True, indent=1).encode("utf-8")
        atomic_private_write(self.path, encoded)
        self.dirty = False


# --------------------------------------------------------------------------
# Log following
# --------------------------------------------------------------------------


# ``LogTailer`` and the rotation's carried-line rule: ``trading.operations.log_tailer``.


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
# The supervisor's restart cap (T-5), timestamp-prefixed like CHILD_EXIT:
#   <ISO UTC> CHILD_SUSPENDED <name> count=<n> window=30m
#   <ISO UTC> CHILD_RESUMED <name> reason=<port_open|probe_healthy|window_boundary|supervisor_stop>
#   <ISO UTC> CHILD_RESTART_STORM <name> count=<n> window=30m
CHILD_SUSPENDED = "child_suspended"
CHILD_RESUMED = "child_resumed"
CHILD_RESTART_STORM = "child_restart_storm"
_RESTART_CAP_LINES = {
    CHILD_SUSPENDED: re.compile(
        r"\bCHILD_SUSPENDED\s+(?P<name>\S+)\s+count=(?P<count>\d+)\s+window=(?P<window>\S+)"
    ),
    CHILD_RESUMED: re.compile(r"\bCHILD_RESUMED\s+(?P<name>\S+)\s+reason=(?P<reason>\S+)"),
    CHILD_RESTART_STORM: re.compile(
        r"\bCHILD_RESTART_STORM\s+(?P<name>\S+)\s+count=(?P<count>\d+)\s+window=(?P<window>\S+)"
    ),
}
# The two that say what state a child is in (the phone must not lag behind).
_CHILD_STATES = frozenset({CHILD_SUSPENDED, CHILD_RESUMED})
# The supervisor's CHILD_RESUMED reason at a child's window end or the session's.
WINDOW_BOUNDARY = "window_boundary"
# A line that names the event but not in that grammar is still forwarded (raw),
# never dropped: a format drift must not silence a suspension.
_RESTART_CAP_TOKENS = {
    kind: re.compile(rf"\b{kind.upper()}\b(?:\s+(?P<name>[^\s=]+)(?=\s|$))?")
    for kind in _RESTART_CAP_LINES
}
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
        # Owner-visible blocks only: the shared loss stop, the kill switch and, since
        # bridge v7a, an identity fence or a missing caps row ("identity_stopped:",
        # "identity fence unreadable", "identity_caps_missing:").
        if "daily loss" not in lowered and "kill switch" not in lowered and "identity" not in lowered:
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


def _window_text(value: str) -> str:
    match = re.fullmatch(r"(\d+)m", value)
    return f"{match.group(1)} min" if match else value


def restart_cap_event(line: str) -> tuple[str, str, str] | None:
    """``(kind, child, phone text)`` for a restart-cap line, else None."""
    for kind, token in _RESTART_CAP_TOKENS.items():
        found = token.search(line)
        if found is None:
            continue
        match = _RESTART_CAP_LINES[kind].search(line)
        if match is None:
            name = found.group("name") or "unknown"
            return kind, name, f"⚠️ supervisor: {_clip(line.strip(), 300)}"
        name = match.group("name")
        if kind == CHILD_SUSPENDED:
            text = (
                f"⛔ {name} suspended after {match.group('count')} restarts in"
                f" {_window_text(match.group('window'))} (relaunch probe every 5 min)"
            )
        elif kind == CHILD_RESUMED:
            reason = match.group("reason")
            if reason == WINDOW_BOUNDARY:
                # The suspension is cleared because the child's day is over, not
                # because it runs again (review P1-A F-5).
                text = f"✅ {name} done for today (window ended)"
            else:
                text = f"✅ {name} resumed ({reason})"
        else:
            text = (
                f"⚠️ {name} restart storm: {match.group('count')} in"
                f" {_window_text(match.group('window'))}"
                " (positions protected at IB; backoff continues)"
            )
        return kind, name, text
    return None


def supervisor_event_text(line: str) -> str | None:
    """``CHILD_EXIT <name> code=<n> restart_in=<x>``; a clean final exit is not news.

    ``LAUNCH_REFUSED`` (the supervisor declined to start a child, e.g. a bridge
    that is not the reviewed one) says why, since no orders follow from it; the
    restart cap's lines say what it did (``restart_cap_event``).
    """
    capped = restart_cap_event(line)
    if capped is not None:
        return capped[2]
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
    """The child a ``CHILD_EXIT``, ``LAUNCH_REFUSED`` or restart-cap line is about."""
    capped = restart_cap_event(line)
    if capped is not None:
        return capped[1]
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


def size_tripwire(line: str) -> tuple[str, str, str] | None:
    """``(token, identity, phone text)`` for a CRITICAL size line, else None;
    the identity is ``ALL_IDENTITIES`` for a line that names none."""
    match = _SIZE_TRIPWIRE.search(line)
    if match is None:
        return None
    token = match.group("token")
    identity = match.group("identity") or ALL_IDENTITIES
    return token, identity, f"🚨 Size tripwire: {token}\n{_clip(line.strip(), 300)}"


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


def competing_login_repage_text(since: datetime, source: str) -> str:
    """A competing login still seen ``COMPETING_LOGIN_REPAGE`` after the last page."""
    return (
        f"⚠️ IBKR login still active elsewhere since {_et_hhmm(since)} ET — log out of the"
        f" IBKR app/website\n(seen in the {source} log)"
    )


# -- IB Gateway reachability ------------------------------------------------
# Live 2026-10-14: the Gateway was off all session. The bridge exited 75 every
# 5 minutes ("TWS did not answer"), the screener logged "TWS connection failed"
# every 70 s with no sweep, every strategy child exit-1-looped, and nothing
# told the phone why. One alert names it, repeated at most every half hour
# while no sweep returns, and one line says when the sweeps are back for good.
BROKER_UNREACHABLE = "broker_unreachable"
BROKER_ALERT_INTERVAL = timedelta(minutes=30)
# The supervisor's bridge child and the bridge's own "ask again later" exit
# (`— exit 75` in its log); both pinned by tests.
BRIDGE_CHILD_NAME = "bridge"
BRIDGE_RESTART_CODE = 75
# The supervisor's SESSION_START (its first child window); pinned by a test.
SESSION_OPENS = time(7, 55)
# The screener's WINDOW_OPENS (pinned by a test): it idles until 08:00 ET, then
# connects and qualifies the whole universe before its first sweep. Live
# 2026-10-13: qualification 08:00:00-08:08:24 for 503 names, first sweep
# 08:10:02; a larger universe or IBKR pacing runs past 08:15 (review 904af38
# F-2), so the first sweep is due 30 minutes after the screener's window opens.
SCREENER_OPENS = time(8, 0)
FIRST_SWEEP_GRACE = timedelta(minutes=30)
# Open/close thresholds and the pair cap are the shared Hysteresis defaults
# (trading/operations/hysteresis.py, also the overnight watch's): no copy here,
# except the close count. An announced outage closes only once it has had
# CLOSE_AFTER_COUNT successes in a row (sweeps, TWS 1101/1102) AND the
# hysteresis's close_after (2 min) has passed since the first of them, with no
# failure between; any failure before that is a flap that restarts the count
# and the clock. Owner decision 2026-10-17
# ("clear only after several successes in a row spread over a few minutes,
# never after a single one"); review N14 MEDIUM-1: one lucky sweep (or a 1102)
# inside a refusal, then the 6th timeout of the next run two minutes later,
# closed the outage or silenced its repeats and its "restart now". A screener
# sweep is one success however many lines it logs (``_SWEEP``, review N14b
# MEDIUM-1), so with one sweep about every minute the outage closes at the
# third sweep, about two minutes after the first.
# The Hysteresis default stays 1 for its other users.
CLOSE_AFTER_COUNT = 3
# An announced outage that has had a success but neither a failure nor another
# success for this long is no recovery and no failure: its half-hour repeat
# comes again, worded "status unclear", instead of being held until the
# notifier's day ends (review N14b LOW-1: one sweep at 15:59:30, then the
# screener's 16:00 stop, left "log in" as the phone's last word with no repeat).
STATUS_UNCLEAR_AFTER = timedelta(minutes=10)
_SESSION_START = re.compile(r"\bSESSION_START\s+date=(?P<date>\d{4}-\d{2}-\d{2})\b")
# One line per failed bridge connect attempt: its closing CRITICAL. The
# "TWS -1 502 Couldn't connect" INFO line before it is the same attempt (live
# 2026-10-14: 28 of each), so it is not a second signal (review P1-A F-7).
_BRIDGE_UNREACHABLE = re.compile(r"\bTWS did not answer\b|\bTWS connect failed\b", re.IGNORECASE)
# IBKR's side lost with the Gateway's port open (live 2026-10-14 14:32-15:25 ET,
# review P1-A F-6): the bridge logs TWS 1100 ("connectivity between IBKR and
# Trader Workstation has been lost"), or 2110 when it connects while TWS is
# already cut off from the server, once; 1101/1102 once when it is restored.
_IBKR_LOST = re.compile(r"\bTWS\s+-?\d+\s+(?:1100|2110)\b")
_IBKR_RESTORED = re.compile(r"\bTWS\s+-?\d+\s+110[12]\b")
# What the alert asks of the owner follows the newest failure signal (review
# P1-A.1 L-3). A failed connect (the bridge's, the screener's, a child's
# handshake) or no first sweep: the Gateway does not answer, so "log in". TWS
# 1100/2110 (or its re-assertion) comes from a bridge connected to a logged-in
# Gateway whose own link to IBKR is down; data refused (below) from a
# connected Gateway that IBKR serves no data. Neither is fixed by logging in.
# Live 2026-10-16 (GW-HMDS-1016) it did not reconnect by itself for two hours:
# logging out of the IBKR app and website brought the orders back, and only an
# IB Gateway restart brought the price history back. So both IBKR-side
# wordings give those two steps in that order, and a repeat of the alert (never
# an outage's first page, review N14 LOW-1) once the outage has lasted
# RESTART_ADVICE_AFTER says "restart IB Gateway now". When that repeat is the
# outage's first IBKR-side page (its earlier pages said "log in"), it gives the
# log-out step first (review N14b LOW-3: the step that brought the orders back
# on 2026-10-16 was never sent for that outage).
GATEWAY_DOWN = "gateway_down"
IBKR_LINK_DOWN = "ibkr_link_down"
DATA_REFUSED = "data_refused"
RESTART_ADVICE_AFTER = timedelta(minutes=15)
_IBKR_SIDE_STEPS = (
    "1) log out of the IBKR app and website, 2) if it lasts, restart IB Gateway:"
    " File → Exit, reopen, log in"
)
_RESTART_NOW = "restart IB Gateway now: File → Exit, reopen, log in"
_LOG_OUT_THEN_RESTART_NOW = (
    "log out of the IBKR app and website first, then restart IB Gateway now:"
    " File → Exit, reopen, log in"
)
_SCREENER_UNREACHABLE = re.compile(r"\bTWS connection failed\b", re.IGNORECASE)
# Data refused with the Gateway connected (live 2026-10-16 08:00:20-09:51 ET):
# the screener connected, then every universe qualification timed out after
# the 20-second TWS request timeout, one line every ~20 s, with no sweep; its
# "qualified 50/503" progress lines kept coming (they count attempts, not
# successes). A successful qualification logs nothing, so a run is counted in
# time: REFUSAL_RUN timeouts, each within REFUSAL_GAP of the one before, with
# no sweep between. A gap that long means some 25 healthy one-second requests
# between two timeouts (live: 20-29 s within the refusal, 41 s across a
# screener restart); a sweep means data flowed. The REFUSAL_RUN-th timeout is
# one failure signal backdated to the run's first, and every later one in the
# run another. The day universe's own qualification line counts too: it runs
# only in the idle time between sweeps, so a sweep always resets its runs.
REFUSAL_RUN = 6
REFUSAL_GAP = timedelta(seconds=45)
_HISTORY_TIMEOUT = re.compile(
    r"\bqualification of .+? failed: error: TWS daily-history request timed out\b"
)
# A sweep whose snapshots all time out with the Gateway connected (live
# 2026-10-14 14:31-15:25 ET, every ~83 s; review P1-A.2 L-1): one failure each.
_SWEEP_FAILED = re.compile(r"\bscreener sweep failed: \d+ consecutive\b")
# The child's own last line (`error: shadow run --strategy <name> failed: ...`),
# not the traceback line above it: one per exit.
_HANDSHAKE_FAILED = re.compile(r"^error: .*failed: TWSConnectionError: TWS PAPER handshake failed")
# One success per screener sweep: its main line (`sweep D: qualified=N quoted=N
# errors=N hot=N seconds=S`), logged once per sweep when its ranking group is
# stored. Since 2026-10-14 every sweep also logs `sweep D: day universe
# quoted=N/M seconds=S` about 10 ms later (the day universe's own pass, on by
# default; screener.py). That line is the same sweep, never a second success:
# counted, it made "three successes in a row" two sweeps (review N14b MEDIUM-1).
_SWEEP = re.compile(r"\bsweep (?P<date>\d{4}-\d{2}-\d{2}): qualified=\d+ ")
# The screener's opt-in TWS status lines (screener_service.StatusCodeLog, T-4):
# ``TWS status <code>: <message>``, once per code change, for a broken farm
# (2103 market data, 2105 HMDS, 2157 sec-def) or a broken TWS-to-IBKR link
# (2110): IBKR's side down with the Gateway's port open, worded like the
# bridge's 1100/2110. Unlike the bridge's it is never re-asserted (no restore
# line follows it; the screener's next sweep is the success), and a connect's
# burst of codes is one failure signal (at most one per open_after), so a farm
# blip while the screener qualifies (no sweep for minutes) never pages alone.
_SCREENER_STATUS = re.compile(r"\bTWS status (?:2103|2105|2110|2157):")


def broker_evidence(source: str, line: str) -> str | None:
    """What one log line says about the Gateway: ``sweep`` (market data flows;
    once per sweep, its main line only), ``ibkr_restored`` (TWS 1101/1102),
    ``bridge_refused``, ``ibkr_lost`` (TWS 1100/2110), ``ibkr_status`` (the
    screener's TWS 2110 or broken farm), ``screener_failed``, ``history_timeout``
    (one daily-history timeout; ``BrokerWatch`` counts the run), ``sweep_failed``,
    ``handshake_failed``, or None."""
    if source == "screener":
        if _SWEEP.search(line):
            return "sweep"
        if _SCREENER_STATUS.search(line):
            return "ibkr_status"
        if _SCREENER_UNREACHABLE.search(line):
            return "screener_failed"
        if _HISTORY_TIMEOUT.search(line):
            return "history_timeout"
        if _SWEEP_FAILED.search(line):
            return "sweep_failed"
        return None
    if source == "bridge":
        if _BRIDGE_UNREACHABLE.search(line):
            return "bridge_refused"
        if _IBKR_LOST.search(line):
            return "ibkr_lost"
        if _IBKR_RESTORED.search(line):
            return "ibkr_restored"
        return None
    if source == "supervisor":
        return None
    return "handshake_failed" if _HANDSHAKE_FAILED.match(line.strip()) else None


def _et_hhmm(moment: datetime) -> str:
    return moment.astimezone(MARKET_TIMEZONE).strftime("%H:%M")


@dataclass(slots=True)
class BrokerWatch:
    """The Gateway's reachability as the logs show it; the alert and the recovery line.

    Every failure line (the bridge's failed connect attempt, the screener's
    failed connect, a child's failed handshake, the bridge's TWS 1100/2110
    "IBKR connectivity lost") is a failure signal, and every screener sweep
    (once, by its main line; review N14b MEDIUM-1) and the bridge's TWS
    1101/1102 "restored" a success, for one shared ``Hysteresis``: the outage
    opens at the third failure in a row or at a failure two minutes into the
    streak (a success breaks it), and closes once ``CLOSE_AFTER_COUNT`` (3)
    successes in a row have come and two minutes have passed since the first
    of them with no failure (a failure before that is a flap: the outage stays
    open, its half-hour repeats and their "restart now" go on, and the count
    starts again; review N14 MEDIUM-1). A success holds the next repeat back
    only while it is fresh: after ``STATUS_UNCLEAR_AFTER`` (10 min) with no
    failure and no further success the repeat is due again, worded "status
    unclear" (review N14b LOW-1); at most one
    alert/recovery pair per half hour, except that failures running unbroken
    for the sustain limit (5 min) always page. What it holds back is a flap,
    reported by ``take_flap_line`` in the next digest (live 2026-10-14: 183
    "TWS connection failed" lines; review 904af38 F-1).

    TWS says "lost" once and "restored" once, so a loss with no restore and no
    sweep is re-asserted as a failure every ``open_after`` (2 min): a loss that
    is not restored within two minutes opens the alert, one that is is a blip.
    Only while the bridge that said "lost" can still say "restored": the
    supervisor's exit line for the bridge, or the end of the bridge's window
    (``supervisor.bridge_window``), makes the loss unknown, not lost, and it is
    never re-asserted after that (review P1-A.1 L-2: 1100 at 15:59, restored at
    16:00:30 with nobody left to log it, paged "unreachable" at 16:01). A new
    bridge reports a loss that is still there itself (TWS 2110 on connect).

    The screener's own TWS status lines (2110, and the broken farms 2103,
    2105 and 2157) are failure signals worded IBKR-side too, one per
    ``open_after`` at most and never re-asserted (``_SCREENER_STATUS``).

    IBKR refusing the data with the Gateway connected (live 2026-10-16,
    GW-HMDS-1016) is a failure signal too: the screener's daily-history
    timeouts once ``REFUSAL_RUN`` of them have come each within
    ``REFUSAL_GAP`` of the one before with no sweep between (the run's first
    timeout is the streak's start), and every failed sweep. With the live
    20-second timeouts its alert opens at the run's seventh timeout, two
    minutes after the first (the hysteresis's ``open_after``), long before the
    first-sweep grace.

    The alert names the newest failure (review P1-A.1 L-3): a failed connect or
    no first sweep says "log in"; an IBKR-side loss (TWS 1100/2110) or data
    refused says the Gateway is connected and gives the two steps of
    GW-HMDS-1016 (log out of the IBKR app and website; if it lasts, restart IB
    Gateway); a repeat of the alert (every half-hour repeat: the first comes 30
    minutes in) once the outage has lasted ``RESTART_ADVICE_AFTER`` says
    "restart IB Gateway now" in the IBKR-side wordings, and an outage's first
    page never does (review N14 LOW-1: a backdated 08:30 first page said it).
    The first such "restart now" of an outage whose pages so far said only
    "log in" gives the log-out step first (review N14b LOW-3). The recovery
    line follows the kind that opened the outage: "IBKR is returning prices
    again" after data refused, "IB Gateway reconnected" otherwise (N15b L-10).

    In memory only: after a notifier restart during an outage the next
    evidence reopens it (the 30-minute cadence lives in the persisted
    ``alerts``), so "since" is then the restart's first failure.
    """

    session_date: date
    opened_at: datetime | None = None
    last_sweep_at: datetime | None = None
    # The latest success (a sweep or TWS 1101/1102): how long a hold has lasted.
    last_success_at: datetime | None = None
    # The first failure of the announced outage; None while none is announced.
    unreachable_since: datetime | None = None
    # Who the outage took down: the bridge (its own refusal line) and the
    # children whose log showed the handshake failure. Their exits are the
    # outage, not news of their own; the next alert counts them.
    bridge_refused: bool = False
    handshake_children: set[str] = field(default_factory=set)
    restarting: dict[str, int] = field(default_factory=dict)
    hysteresis: Hysteresis = field(
        default_factory=lambda: Hysteresis(close_after_count=CLOSE_AFTER_COUNT)
    )
    first_sweep_overdue: bool = False
    # The bridge's last TWS 1100/2110 (or its re-assertion) while no restore
    # and no sweep has come since; None when IBKR's side is not known lost.
    ibkr_lost_at: datetime | None = None
    # The newest failure signal's kind (GATEWAY_DOWN, IBKR_LINK_DOWN or
    # DATA_REFUSED): the alert's wording.
    failure_kind: str = GATEWAY_DOWN
    # The current run of daily-history timeouts: its first and latest timeout
    # and its length; reset by a sweep, or by a timeout more than REFUSAL_GAP
    # after the one before.
    refusal_started: datetime | None = None
    refusal_last_at: datetime | None = None
    refusal_count: int = 0
    # The newest data-refused failure signal since the last sweep (None after one).
    refused_at: datetime | None = None
    # The screener's last TWS status line counted as a failure signal.
    screener_status_at: datetime | None = None
    # The latest observe/check: how long the announced outage has lasted.
    checked_at: datetime | None = None
    alerted: bool = False
    swept_since_alert: bool = False
    # A page of this outage already gave the log-out step (an IBKR-side wording).
    told_log_out: bool = False
    flaps_seen: int = 0
    flaps_reported: int = 0
    # The flaps the digest has not reported yet, by time (oldest first), counted
    # by the OPS rule (N15b L-10): a held-back open waits in ``held_flap_at``
    # until it settles, a flap if it closes unannounced, gone if announced.
    flap_times: list[datetime] = field(default_factory=list)
    held_flap_at: datetime | None = None
    # Announced outages and their length (first failure to the first success
    # of the stretch that closed it), for the report's OPS lines; the part of
    # it that was data refused (an outage opened by DATA_REFUSED) besides.
    outages: int = 0
    unreachable_seconds: float = 0.0
    refused_seconds: float = 0.0
    # The kind that opened the announced outage: its recovery line's wording.
    outage_kind: str = GATEWAY_DOWN
    # The OPS lines count one outage once (P1-B): an open the pair budget held
    # back is a flap to the hysteresis even when the sustain limit or the window
    # announces it later (the digest's flap line keeps that). ``holding_back``:
    # the hysteresis is open and not announced after the latest signal;
    # ``flaps_announced``: held-back opens announced since.
    holding_back: bool = False
    flaps_announced: int = 0
    # The supervisor's bridge window end for this date; None when it is no session.
    bridge_ends_at: datetime | None = field(init=False, default=None)

    def __post_init__(self) -> None:
        session = market_session(self.session_date)
        if session is not None:
            self.bridge_ends_at = bridge_window(session)[1]

    @property
    def unreachable(self) -> bool:
        """An announced outage: the phone is told (or about to be)."""
        return self.unreachable_since is not None

    @property
    def alert_due(self) -> bool:
        """The alert, subject to the half-hour cadence: the outage's first, and a
        repeat unless a success has come since the last page and still holds
        (the hysteresis is recovering: no failure since). A failure after that
        success puts the outage back to OPEN, and the repeat, "restart now"
        included, is due again at its half hour (review N14 MEDIUM-1: one sweep
        or TWS 1102 inside an announced outage silenced its repeats for good).
        A success never resets the half-hour timer: only the recovery line does,
        and that needs ``CLOSE_AFTER_COUNT`` successes. A success that has had
        nothing after it for ``STATUS_UNCLEAR_AFTER`` holds nothing back (review
        N14b LOW-1): the repeat is due, worded "status unclear"."""
        holding = self.hysteresis.recovering_since is not None and not self.status_unclear
        return self.unreachable and not (self.alerted and self.swept_since_alert and holding)

    @property
    def status_unclear(self) -> bool:
        """An announced outage on hold (a success, no failure since) whose latest
        success is ``STATUS_UNCLEAR_AFTER`` old, as of the latest observe/check:
        neither recovered (fewer than ``CLOSE_AFTER_COUNT`` successes) nor known
        to fail (review N14b LOW-1)."""
        return (
            self.unreachable
            and self.hysteresis.recovering_since is not None
            and self.last_success_at is not None
            and self.checked_at is not None
            and self.checked_at - self.last_success_at >= STATUS_UNCLEAR_AFTER
        )

    @property
    def flaps_pending(self) -> bool:
        return bool(self.flap_times)

    @property
    def ops_flaps(self) -> int:
        """The flaps the report's OPS lines count: flips the phone never heard as
        an outage. The hysteresis's flaps less each held-back open announced later
        (an outage there, counted once as one) and less the open still held back
        (not settled yet: it ends as a flap or as an outage). Never decreases."""
        return self.hysteresis.flaps - self.flaps_announced - int(self.holding_back)

    def session_started(self, now: datetime) -> None:
        if self.opened_at is None:
            self.opened_at = now

    def first_sweep_due(self) -> datetime | None:
        """30 minutes after the screener's window opened (or the session, if later)."""
        if self.opened_at is None:
            return None
        start = max(self.opened_at, market_time(self.session_date, SCREENER_OPENS))
        return start + FIRST_SWEEP_GRACE

    def broker_exit(self, name: str, code: int | None) -> bool:
        """Whether a ``CHILD_EXIT`` is the outage (counted, not forwarded).

        Live 2026-10-14: ~12 "<child> exited (code N), restarting" lines an
        hour and never a plain "Gateway off". While the outage is announced,
        the bridge's exit 75 after its "TWS did not answer", and a child's
        exit 1 after its handshake failure, are folded into the broker alert;
        any other exit goes through. While the pair budget holds an outage
        back the phone has no alert to count them in, so they go through as
        before (review P1-A F-1).
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
        """The alert as ``alert_text`` worded it now went to the phone."""
        if self._wording()[1]:
            self.told_log_out = True
        self.restarting.clear()
        self.alerted = True
        self.swept_since_alert = False

    def folds_bridge_line(self, line: str) -> bool:
        """The bridge's "TWS did not answer" CRITICAL while the outage is announced:
        the alert's (its bridge exit is counted there), not a message of its own.
        Live 2026-10-14 it reached the phone on every attempt (review P1-A F-7)."""
        return self.unreachable and broker_evidence(BRIDGE_CHILD_NAME, line) == "bridge_refused"

    def observe(self, source: str, line: str, now: datetime) -> str | None:
        """Feed one log line; the recovery text when it ends the outage, else None."""
        self.checked_at = now
        if source == "supervisor":
            started = _SESSION_START.search(line)
            if started is not None and started.group("date") == self.session_date.isoformat():
                self.session_started(now)
            exited = _CHILD_EXIT.search(line)
            if exited is not None and exited.group("name") == BRIDGE_CHILD_NAME:
                # The client that said "lost" is gone and its successor never
                # hears the restore: unknown, not lost (review P1-A.1 L-2).
                self.ibkr_lost_at = None
            return None
        evidence = broker_evidence(source, line)
        if evidence is None:
            return None
        if evidence == "history_timeout":
            return self._history_timeout(now)
        if evidence == "ibkr_status":
            return self._screener_status(now)
        if evidence == "sweep":
            self.last_sweep_at = now
            # Data flowed: a refusal run, if any, is over.
            self.refusal_started = self.refusal_last_at = self.refused_at = None
            self.refusal_count = 0
        if evidence in {"sweep", "ibkr_restored"}:
            # Market data answered, or TWS says IBKR is back: who the outage
            # took down is forgotten (a later exit is its own news unless the
            # failure line comes again).
            self.last_success_at = now
            self.ibkr_lost_at = None
            self.bridge_refused = False
            self.handshake_children.clear()
            self.restarting.clear()
            if self.alerted:
                self.swept_since_alert = True
            return self._apply(self.hysteresis.success(now), now)
        if evidence == "bridge_refused":
            self.bridge_refused = True
        elif evidence == "handshake_failed":
            self.handshake_children.add(source)
        elif evidence == "ibkr_lost":
            self.ibkr_lost_at = now
        elif evidence == "sweep_failed":
            self.refused_at = now
        self.failure_kind = {
            "ibkr_lost": IBKR_LINK_DOWN,
            "sweep_failed": DATA_REFUSED,
        }.get(evidence, GATEWAY_DOWN)
        return self._apply(self.hysteresis.failure(now), now)

    def _history_timeout(self, now: datetime) -> str | None:
        """One daily-history timeout: a failure signal only inside a run of
        ``REFUSAL_RUN`` (each within ``REFUSAL_GAP`` of the one before, no sweep
        between); the first such signal backdated to the run's first timeout."""
        last = self.refusal_last_at
        if last is None or now - last > REFUSAL_GAP:
            self.refusal_started = now
            self.refusal_count = 0
        self.refusal_count += 1
        self.refusal_last_at = now
        if self.refusal_count < REFUSAL_RUN:
            return None
        self.refused_at = now
        self.failure_kind = DATA_REFUSED
        return self._apply(self.hysteresis.failure(now, since=self.refusal_started), now)

    def _screener_status(self, now: datetime) -> str | None:
        """The screener's TWS 2110 or broken farm: one failure signal, worded
        IBKR-side, at most one per ``open_after`` (a connect's burst of codes)."""
        last = self.screener_status_at
        if last is not None and now - last < self.hysteresis.open_after:
            return None
        self.screener_status_at = now
        self.failure_kind = IBKR_LINK_DOWN
        return self._apply(self.hysteresis.failure(now), now)

    def check(self, now: datetime) -> str | None:
        """Time passing: no first sweep in time (the Gateway never answered), an
        IBKR loss still not restored, the recovery line two clean minutes after
        the first of ``CLOSE_AFTER_COUNT`` successes in a row (once they have
        come), or a held-back outage announced once the pair cap or the
        sustain limit allows; the recovery text, else None."""
        self.checked_at = now
        due = self.first_sweep_due()
        if (
            due is not None
            and not self.first_sweep_overdue
            and self.last_sweep_at is None
            and now >= due
        ):
            self.first_sweep_overdue = True
            if self.ibkr_lost_at is None and self.refused_at is None:
                # A known IBKR-side loss or data refused explains the missing
                # sweep (the wording stays); otherwise the Gateway never answered.
                self.failure_kind = GATEWAY_DOWN
            # No market data since the screener's window opened: failing since then.
            event = self.hysteresis.failure(now, since=due - FIRST_SWEEP_GRACE)
            return self._apply(event, now)
        lost = self.ibkr_lost_at
        if lost is not None and (self.bridge_ends_at is None or now >= self.bridge_ends_at):
            # The bridge's window is over: no client is left to log the restore
            # and no sweep comes after the screener's own end. Unknown, not lost:
            # never re-asserted (review P1-A.1 L-2).
            self.ibkr_lost_at = None
        elif lost is not None and now - lost >= self.hysteresis.open_after:
            # TWS said "lost" once and has not said "restored", and no sweep came:
            # still lost, one more failure signal.
            self.ibkr_lost_at = now
            self.failure_kind = IBKR_LINK_DOWN
            return self._apply(self.hysteresis.failure(now), now)
        return self._apply(self.hysteresis.tick(now), now)

    def _apply(self, event: HysteresisEvent | None, now: datetime) -> str | None:
        flaps = self.hysteresis.flaps
        new = flaps - self.flaps_seen
        if new > 0:
            self.flaps_seen = flaps
            self.flap_times.extend([now] * new)
            logger.info("%s flap %d held back from the phone", BROKER_UNREACHABLE, flaps)
        holding = self.holding_back
        if event is not None and event.kind is HYSTERESIS_OPEN and holding:
            # Held back by the pair budget, announced now by the sustain limit or
            # the window: one outage, never also a flap (OPS lines and digest alike).
            self.flaps_announced += 1
            self.held_flap_at = None
        self.holding_back = self.hysteresis.is_open and not self.hysteresis.announced
        if self.holding_back and not holding and new > 0 and self.flap_times:
            # The open the budget just held back: not a flap yet, it may still be announced.
            self.held_flap_at = self.flap_times.pop()
        elif holding and not self.holding_back and self.held_flap_at is not None:
            # Closed without being announced: a flap after all.
            bisect.insort(self.flap_times, self.held_flap_at)
            self.held_flap_at = None
        if event is None:
            return None
        self.alerted = False
        self.swept_since_alert = False
        self.told_log_out = False
        if event.kind is HYSTERESIS_OPEN:
            self.unreachable_since = event.since
            self.outage_kind = self.failure_kind
            self.outages += 1
            return None
        self.unreachable_since = None
        assert event.recovered_at is not None
        lasted = max((event.recovered_at - event.since).total_seconds(), 0.0)
        self.unreachable_seconds += lasted
        if self.outage_kind == DATA_REFUSED:
            # The Gateway answered all along: IBKR refused the prices (N15b L-10).
            self.refused_seconds += lasted
            return (
                f"✅ IBKR is returning prices again {_et_hhmm(event.recovered_at)} ET — market data is back"
                f" (refusing prices since {_et_hhmm(event.since)} ET)"
            )
        return (
            f"✅ IB Gateway reconnected {_et_hhmm(event.recovered_at)} ET — market data is back"
            f" (unreachable since {_et_hhmm(event.since)} ET)"
        )

    def take_flap_line(self) -> str | None:
        """The digest's line for the settled flaps not reported yet, counted by the
        OPS lines' rule (``ops_flaps``): one announced outage is never also a flap."""
        if not self.flap_times:
            return None
        pending, since = len(self.flap_times), self.flap_times[0]
        self.flap_times.clear()
        self.flaps_reported += pending
        return f"⚡ IB Gateway flapped {pending}× since {_et_hhmm(since)} ET (held back from separate alerts)"

    def alert_text(self) -> str:
        """The alert for the newest failure kind, as of the latest observe/check."""
        text = self._wording()[0]
        count = len(self.restarting)
        if count:
            text += f" ({count} {'child' if count == 1 else 'children'} restarting)"
        return text

    def _wording(self) -> tuple[str, bool]:
        """The alert's text (without the restart count), and whether it gives the
        log-out step; one place, so ``alert_sent`` records what was sent."""
        assert self.unreachable_since is not None
        since = _et_hhmm(self.unreachable_since)
        if self.status_unclear:
            assert self.last_success_at is not None
            return (
                f"⛔ IB Gateway status unclear: no price data since {_et_hhmm(self.last_success_at)} ET"
                f" (down since {since} ET, not recovered) — check IB Gateway",
                False,
            )
        kind = self.failure_kind
        lasted = (
            self.checked_at - self.unreachable_since if self.checked_at is not None else timedelta(0)
        )
        # "Restart now" only on a repeat (``alerted``: this outage was paged), never
        # on its first page, whose start may be backdated to 08:00 (review N14 LOW-1).
        repeat = self.alerted
        if kind in (IBKR_LINK_DOWN, DATA_REFUSED) and repeat and lasted >= RESTART_ADVICE_AFTER:
            if self.told_log_out:
                return f"⛔ IBKR still refusing prices since {since} ET — {_RESTART_NOW}", False
            # The outage's first IBKR-side page: its earlier ones said "log in"
            # (review N14b LOW-3), so the log-out step comes first.
            return f"⛔ IBKR is refusing prices since {since} ET — {_LOG_OUT_THEN_RESTART_NOW}", True
        if kind == IBKR_LINK_DOWN:
            return (
                f"⛔ IBKR connectivity lost since {since} ET — the Gateway is still logged in;"
                f" {_IBKR_SIDE_STEPS}",
                True,
            )
        if kind == DATA_REFUSED:
            return f"⛔ IBKR is refusing prices since {since} ET (Gateway connected) — {_IBKR_SIDE_STEPS}", True
        return f"⛔ IB Gateway unreachable since {since} ET — log in; the bot has no market data", False


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
    """A decision the PAPER bridge may send (exactly a sendable identity): at once, as before."""
    return is_sendable_identity(decision.strategy)


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
    ops: OpsReport | None = None


@dataclass(frozen=True, slots=True)
class OpsCounters:
    """What the notifier counts itself during the day (persisted per date).

    ``gateway_unreachable_seconds`` includes an outage still open at the
    report (``gateway_down_since``). A report built outside the notifier
    (the phone's /report) has none: its Gateway line says so.
    """

    gateway_outages: int = 0
    gateway_unreachable_seconds: int = 0
    # The part of it that was IBKR refusing prices (the Gateway connected).
    gateway_refused_seconds: int = 0
    gateway_flaps: int = 0
    gateway_down_since: datetime | None = None
    # The outage open at the report was opened by data refused.
    gateway_down_refused: bool = False
    preflight_refused: int = 0
    # The bridge state as the notifier saw it during the day (HELD_AT_VERIFY_KEY,
    # FLATTEN_SEEN_KEY): None when it did not look then.
    held_at_verify: str | None = None
    flatten_seen_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class OpsReport:
    """The report's OPS lines: today's supervisor.log, the fence rows, the counters."""

    restarts: str
    capped: tuple[str, ...]
    refusals: tuple[str, ...]
    fenced: tuple[tuple[str, str], ...] | None
    fences_problem: str | None
    counters: OpsCounters | None
    close: str = ""


def held_positions(brackets: Iterable[Mapping[str, Any]]) -> list[str]:
    """``<symbol> <n> sh`` for every bracket the bridge itself calls not flat
    (open or needs attention, with entry shares not sold), by symbol."""
    held: list[str] = []
    for bracket in brackets:
        if bracket.get("status") not in {"open", "needs_attention"}:
            continue
        net = (_int(bracket.get("filled")) or 0) - (_int(bracket.get("exit_filled")) or 0)
        if net:
            held.append(f"{bracket.get('symbol') or '?'} {net} sh")
    return sorted(held)


def flatten_shown(brackets: Iterable[Mapping[str, Any]]) -> bool:
    """Whether the bridge has persisted a flatten attempt on any bracket."""
    return any(bracket.get("flatten_attempted") is True for bracket in brackets)


def close_line(
    session_date: date,
    brackets: Sequence[Mapping[str, Any]],
    *,
    now: datetime,
    held_at_verify: str | None = None,
    flatten_at: datetime | None = None,
    problem: str | None = None,
) -> str:
    """Was the bridge flat at its 15:50 check, and did the 15:45 flatten run on time?

    Nothing enters after the last entry (close - 30 min) and an entry lives
    10 minutes, so shares held at the report were held at the check too: the
    check's list is what the notifier saw then, else what is held now. A state
    file that cannot be read now (``problem``) never reads as flat.
    """
    session = market_session(session_date)
    if session is None:
        return "• Close: no session"
    due = session.closes_at - FLATTEN_BEFORE_CLOSE
    verify = session.closes_at - VERIFY_BEFORE_CLOSE
    if now < verify:
        return f"• Close: not checked yet (the bridge checks it is flat at {_et_hhmm(verify)} ET)"
    if problem is not None:
        text = f"• Close: ⚠️ unknown ({problem})"
        if held_at_verify is not None:
            text += f"; at {_et_hhmm(verify)} ET the state showed {held_at_verify or 'flat'}"
        return text
    held_now = held_positions(brackets)
    held = held_at_verify or ", ".join(held_now)
    if not held:
        when = verify if held_at_verify == "" else now
        return f"• Close: flat at {_et_hhmm(when)} ET"
    text = f"• Close: ⚠️ bridge not flat at {_et_hhmm(verify)} ET — {held}"
    if flatten_at is None:
        text += f"; flatten not run (due {_et_hhmm(due)} ET)"
    elif flatten_at > due + FLATTEN_LATE_AFTER:
        text += f"; flatten ran late at {_et_hhmm(flatten_at)} ET (due {_et_hhmm(due)} ET)"
    else:
        text += f"; flatten ran {_et_hhmm(flatten_at)} ET"
    if not held_now:
        text += f"; flat by {_et_hhmm(now)} ET"
    elif held_at_verify:
        text += f"; still open at {_et_hhmm(now)} ET: {', '.join(held_now)}"
    else:
        text += f"; still open at {_et_hhmm(now)} ET"
    return text


def supervisor_ops(lines: Sequence[str], day: date) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """``(suspensions and storms, launch refusals)`` on ``day`` (ET), oldest first."""
    capped: list[str] = []
    refusals: list[str] = []
    for line in lines:
        at_text, _, _rest = line.strip().partition(" ")
        at = _parse_time(at_text)
        if at is None or at.astimezone(MARKET_TIMEZONE).date() != day:
            continue
        clock = at.astimezone(MARKET_TIMEZONE).strftime("%H:%M ET")
        event = restart_cap_event(line)
        if event is not None:
            kind, name, _text = event
            if kind == CHILD_SUSPENDED:
                capped.append(f"{name} suspended {clock}")
            elif kind == CHILD_RESTART_STORM:
                capped.append(f"{name} restart storm {clock}")
            continue
        refused = _LAUNCH_REFUSED.search(line)
        if refused is not None:
            refusals.append(
                f"{refused.group('name')} LAUNCH_REFUSED {refused.group('reason')} {clock}"
            )
    return tuple(capped), tuple(refusals)


def _restarts_today(path: Path, day: date) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
    """Restart counts (the /status wording), suspensions/storms and refusals from
    supervisor.log, and from today's archive when the 18:35 rotation already ran."""
    if path.is_symlink() or not path.is_file():
        return "unknown (no supervisor.log)", (), ()
    current = read_log_tail(path, max_bytes=OPS_LOG_TAIL_BYTES)
    first_day = next(
        (
            moment.astimezone(MARKET_TIMEZONE).date()
            for line in current
            if (moment := _line_time(line)) is not None
        ),
        None,
    )
    partial = path.stat().st_size > OPS_LOG_TAIL_BYTES and first_day == day
    archive = path.with_name(f"{path.name}.{day.isoformat()}")
    archived = (
        read_log_tail(archive, max_bytes=OPS_LOG_TAIL_BYTES)
        if archive.is_file() and not archive.is_symlink()
        else []
    )
    lines = [*without_carried(archived), *without_carried(current)]
    capped, refusals = supervisor_ops(lines, day)
    return restarts_text(child_starts_on(lines, day), partial=partial), capped, refusals


def _state_written_at(paths: UserPaths, session_date: date) -> datetime | None:
    try:
        status = bridge_state_files(paths, session_date)[0].stat()
    except OSError:
        return None
    return datetime.fromtimestamp(status.st_mtime, timezone.utc)


async def collect_ops(
    session_date: date,
    paths: UserPaths,
    counters: OpsCounters | None,
    fence_store: SQLitePaperKillStore | None = None,
    *,
    trades: Sequence[Mapping[str, Any]] = (),
    trades_problem: str | None = None,
    now: datetime | None = None,
) -> OpsReport:
    try:
        restarts, capped, refusals = await asyncio.to_thread(
            _restarts_today, paths.log_directory / "supervisor.log", session_date
        )
    except OSError as exc:
        restarts, capped, refusals = f"unknown (supervisor.log {type(exc).__name__})", (), ()
    fenced: tuple[tuple[str, str], ...] | None = None
    problem: str | None = None
    try:
        rows = await (fence_store or SQLitePaperKillStore()).identity_rows()
        fenced = tuple((name, reason) for name, stopped, reason, _at in rows if stopped)
    except Exception as exc:  # noqa: BLE001 - the report never waits on the fence table
        problem = type(exc).__name__
    flatten_at = counters.flatten_seen_at if counters is not None else None
    if flatten_at is None and flatten_shown(trades):
        # Not seen during the day (the notifier was down): the file's last write, an upper bound.
        flatten_at = _state_written_at(paths, session_date)
    close = close_line(
        session_date,
        trades,
        now=now or _utc_now(),
        held_at_verify=counters.held_at_verify if counters is not None else None,
        flatten_at=flatten_at,
        problem=trades_problem,
    )
    return OpsReport(
        restarts=restarts,
        capped=capped,
        refusals=refusals,
        fenced=fenced,
        fences_problem=problem,
        counters=counters,
        close=close,
    )


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
    ops_counters: OpsCounters | None = None,
    fence_store: SQLitePaperKillStore | None = None,
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
    ops = await collect_ops(
        session_date,
        paths,
        ops_counters,
        fence_store,
        trades=trades,
        trades_problem=problem,
        now=now or _utc_now(),
    )
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
        ops=ops,
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


def _listed(items: Sequence[str]) -> str:
    if not items:
        return "none"
    shown = "; ".join(items[:OPS_ITEMS_SHOWN])
    return shown + (f"; … {len(items) - OPS_ITEMS_SHOWN} more" if len(items) > OPS_ITEMS_SHOWN else "")


def _plural(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


def _gateway_line(counters: OpsCounters | None) -> str:
    if counters is None:
        return "• Gateway: not counted here (the notifier's 16:05 report has it)"
    if not counters.gateway_outages and not counters.gateway_flaps:
        return "• Gateway: no outage, no flaps"
    refused = min(counters.gateway_refused_seconds, counters.gateway_unreachable_seconds)
    unreachable = -(-(counters.gateway_unreachable_seconds - refused) // 60)
    refusing = -(-refused // 60)
    parts = [_plural(counters.gateway_outages, "outage")]
    if unreachable or not refusing:
        parts.append(f"{unreachable} min unreachable")
    if refusing:
        # The Gateway answered: IBKR refused the prices (N15b L-10).
        parts.append(f"{refusing} min refusing prices")
    text = f"• Gateway: {', '.join(parts)}, {_plural(counters.gateway_flaps, 'flap')}"
    if counters.gateway_down_since is not None:
        state = "refusing prices" if counters.gateway_down_refused else "unreachable"
        text += f"; still {state} since {_et_hhmm(counters.gateway_down_since)} ET"
    return text


def _identity_lines(report: DailyReport) -> list[str]:
    """Trades (entries that filled) and realized P&L per identity, from the bridge state."""
    if report.trades_problem:
        return [f"• Per identity: unavailable ({report.trades_problem})"]
    totals: dict[str, tuple[int, Decimal]] = {
        name: (0, Decimal("0")) for name in SENDABLE_IDENTITIES
    }
    for trade in report.trades:
        if trade.get("status") == "skipped" or trade.get("skipped") is True:
            continue
        label = (
            str(trade.get("strategy_name") or "").strip()
            or str(trade.get("strategy") or "").strip()
            or "unknown"
        )
        count, pnl = totals.get(label, (0, Decimal("0")))
        filled = (_int(trade.get("filled")) or 0) > 0
        totals[label] = (count + int(filled), pnl + (_decimal(trade.get("realized_pnl")) or Decimal("0")))
    order = sorted(SENDABLE_IDENTITIES) + sorted(set(totals) - set(SENDABLE_IDENTITIES))
    return [
        f"• {name}: {_plural(totals[name][0], 'trade')} · P&L {_money(totals[name][1])}"
        for name in order
    ]


def _ops_lines(report: DailyReport) -> list[str]:
    ops = report.ops
    if ops is None:
        return []
    refusals = list(ops.refusals)
    if ops.counters is not None and ops.counters.preflight_refused:
        refusals.append(f"PREFLIGHT_REFUSED ×{ops.counters.preflight_refused} (bridge.log)")
    if ops.fenced is None:
        fenced = f"unknown ({ops.fences_problem})"
    else:
        fenced = _listed([f"{name} ({reason})" for name, reason in ops.fenced])
    return [
        "",
        "OPS",
        f"• Restarts today: {ops.restarts}",
        f"• Suspensions/storms: {_listed(ops.capped)}",
        _gateway_line(ops.counters),
        f"• Refusals: {_listed(refusals)}",
        f"• Fenced: {fenced}",
        *([ops.close] if ops.close else []),
        *_identity_lines(report),
    ]


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
    lines.extend(_ops_lines(report))
    return "\n".join(lines)


def startup_text(session_date: date) -> str:
    return (
        f"🤖 Autotrader notifier on for {session_date:%a %d %b %Y}: signals, orders, exits,"
        " market updates and the 16:05 ET report will arrive here."
    )


def fences_at_start_text(rows: Sequence[tuple[str, str, str]]) -> str:
    """Every stopped identity fence, ``(identity, reason, updated_at)``, at the notifier's start."""
    lines = ["⛔ Fenced at start — no new entries for:"]
    for identity, reason, updated in rows:
        moment = _parse_time(updated)
        since = (
            moment.astimezone(MARKET_TIMEZONE).strftime("%a %d %b %H:%M ET")
            if moment is not None
            else updated
        )
        lines.append(f"• {identity}: {reason} (since {since})")
    lines.append(f"Resume is the owner's: {resume_command('<IDENTITY>')}")
    return "\n".join(lines)


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
        # A login elsewhere: paged at its first line, re-paged while it lasts.
        self._competing = CompetingLoginWatch()
        # The broker counts already added to the persisted OPS counters.
        self._gateway_synced = (0, 0, 0, 0)
        # What the phone last heard per child from the restart cap (suspended or
        # resumed); rebuilt from the persisted alert times after a restart.
        self._child_told: dict[str, str] = {}
        # Its memory is the per-date alerts map: a restart neither fences twice
        # nor overrides an owner's resume the same day.
        self._sentinel = Sentinel(
            session_date=session_date,
            state_directory=bridge_state_directory(environment.paths),
            memory=state.alerts,
            store=environment.fence_store,
            read_heartbeat=environment.read_bridge_heartbeat,
        )
        self._next_sentinel_check = float("-inf")
        self._sentinel_failing_since: datetime | None = None
        # The start's fence announcement is retried while the fence table cannot be read.
        self._next_fence_announcement = float("-inf")
        # The latest LOG_ROTATED marker seen in supervisor.log, and how many of
        # the lines after it may still be carried copies (log_tailer's one rule).
        self._rotation = RotationCarried()
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
        await self._announce_fences()
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
        self._follow_broker()
        self._follow_competing()
        self._sync_gateway_counts()
        for text in self._fills.due(self._environment.monotonic()):
            self._send(text)
        if self._environment.monotonic() >= self._next_fence_announcement:
            await self._announce_fences()
        if self._environment.monotonic() >= self._next_sentinel_check:
            self._next_sentinel_check = self._environment.monotonic() + SENTINEL_CHECK_SECONDS
            await self._run_sentinel()
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
            lines = tailer.poll()
            # T-3 writes the carried state lines into the new supervisor.log just
            # before its LOG_ROTATED marker, in one instant, so they come in the
            # marker's poll: dropped here by the OPS lines' rule (P1-B; T-6 alone
            # dropped only lines after the marker and forwarded these again).
            markers = rotation_carried(lines) if name == "supervisor" else {}
            before = {index for carried, _after in markers.values() for index in carried}
            for index, line in enumerate(lines):
                if index in before:
                    continue
                self._handle_line(name, line)
                if index in markers:
                    # The marker's count covers these: fewer can follow it.
                    self._rotation.marker_counted(len(markers[index][0]))

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

    def _follow_broker(self) -> None:
        recovered = self._broker.check(self._environment.clock())
        if recovered is not None:
            self._broker_recovered(recovered)
        if self._broker.alert_due and self._alert_allowed(BROKER_UNREACHABLE, BROKER_ALERT_INTERVAL):
            logger.warning("%s: %s", BROKER_UNREACHABLE, self._broker.alert_text())
            self._send_alert(BROKER_UNREACHABLE, self._broker.alert_text())
            self._broker.alert_sent()

    def _sync_gateway_counts(self) -> None:
        """Add the broker's new outages, unreachable seconds and flaps to the day's
        counters; an outage counts once, as an outage (``BrokerWatch.ops_flaps``)."""
        broker = self._broker
        current = (
            broker.outages, int(broker.unreachable_seconds), int(broker.refused_seconds), broker.ops_flaps
        )
        for key, now, before in zip(
            (GATEWAY_OUTAGES, GATEWAY_UNREACHABLE_SECONDS, GATEWAY_REFUSED_SECONDS, GATEWAY_FLAPS),
            current,
            self._gateway_synced,
        ):
            self._state.count(key, now - before)
        self._gateway_synced = current
        self._sync_gateway_outage()

    def _sync_gateway_outage(self) -> None:
        """The announced outage's start and kind in the per-date alerts map (N15b
        L-1): after 16:15 the Telegram watch starts from them, never from its own
        first failed check. While it lasts its latest check is stamped there too,
        at least once per ``GATEWAY_OUTAGE_STAMP_INTERVAL``, so the watch can tell
        it from one a notifier that stopped early left behind (N15c LOW-1)."""
        alerts, since = self._state.alerts, self._broker.unreachable_since
        wanted = (
            {}
            if since is None
            else {GATEWAY_OUTAGE_SINCE_KEY: since.isoformat(), GATEWAY_OUTAGE_KIND_KEY: self._broker.outage_kind}
        )
        changed = False
        for key in (GATEWAY_OUTAGE_SINCE_KEY, GATEWAY_OUTAGE_KIND_KEY):
            if alerts.get(key) != wanted.get(key):
                if key in wanted:
                    alerts[key] = wanted[key]
                else:
                    alerts.pop(key, None)
                changed = True
        checked = self._broker.checked_at if since is not None else None
        stamped = _parse_time(alerts.get(GATEWAY_OUTAGE_CHECKED_KEY))
        if checked is None:
            if alerts.pop(GATEWAY_OUTAGE_CHECKED_KEY, None) is not None:
                changed = True
        elif (
            changed
            or stamped is None
            or not stamped <= checked < stamped + GATEWAY_OUTAGE_STAMP_INTERVAL
        ):
            alerts[GATEWAY_OUTAGE_CHECKED_KEY] = checked.isoformat()
            changed = True
        if changed:
            self._state.dirty = True

    def _ops_counters(self) -> OpsCounters:
        self._sync_gateway_counts()
        counters = self._state.counters
        seconds = counters.get(GATEWAY_UNREACHABLE_SECONDS, 0)
        refused = counters.get(GATEWAY_REFUSED_SECONDS, 0)
        since = self._broker.unreachable_since
        down_refused = since is not None and self._broker.outage_kind == DATA_REFUSED
        if since is not None:
            lasting = max(int((self._environment.clock() - since).total_seconds()), 0)
            seconds += lasting
            refused += lasting if down_refused else 0
        return OpsCounters(
            gateway_outages=counters.get(GATEWAY_OUTAGES, 0),
            gateway_unreachable_seconds=seconds,
            gateway_refused_seconds=refused,
            gateway_down_refused=down_refused,
            # An open still held back at the report is a flap as of now (persisted
            # only once it settles: closed unannounced, a flap; announced, an outage).
            gateway_flaps=counters.get(GATEWAY_FLAPS, 0) + int(self._broker.holding_back),
            gateway_down_since=since,
            preflight_refused=counters.get(PREFLIGHT_REFUSED, 0),
            held_at_verify=self._state.alerts.get(HELD_AT_VERIFY_KEY),
            flatten_seen_at=_parse_time(self._state.alerts.get(FLATTEN_SEEN_KEY)),
        )

    def _observe_close(self, now: datetime) -> None:
        """From the Sentinel's reading: when the state first showed a flatten, and
        what was held at the first look at/after the 15:50 check (before the close)."""
        reading = self._sentinel.last_reading
        if reading is None or reading.submitted is None:
            return
        alerts = self._state.alerts
        brackets = list(reading.submitted.values())
        if (
            FLATTEN_SEEN_KEY not in alerts
            and reading.modified_at is not None
            and flatten_shown(brackets)
        ):
            alerts[FLATTEN_SEEN_KEY] = reading.modified_at.isoformat()
            self._state.dirty = True
        session = market_session(self._session_date)
        if (
            session is not None
            and HELD_AT_VERIFY_KEY not in alerts
            and session.closes_at - VERIFY_BEFORE_CLOSE <= now < session.closes_at
        ):
            alerts[HELD_AT_VERIFY_KEY] = ", ".join(held_positions(brackets))
            self._state.dirty = True

    async def _run_sentinel(self) -> None:
        """The auto-fence check; a Sentinel that fails is as loud as a blind one."""
        now = self._environment.clock()
        before = dict(self._state.alerts)
        try:
            messages = await self._sentinel.check(now)
        except Exception as exc:  # noqa: BLE001 - paged as blindness, never fatal
            self._log_quietly("sentinel", logging.ERROR, "sentinel check failed", exc_info=True)
            if self._sentinel_failing_since is None:
                self._sentinel_failing_since = now
            messages = []
            if self._alert_allowed(BLIND_KEY, BLIND_REPEAT):
                why = f"its check failed ({type(exc).__name__})"
                messages.append((BLIND_ALERT, blind_text(self._sentinel_failing_since, why)))
        else:
            self._sentinel_failing_since = None
            self._observe_close(now)
        if self._state.alerts != before:
            self._state.dirty = True
        for alert, text in messages:
            self._send_alert(alert, text)

    async def _announce_fences(self) -> None:
        """Once per date: every stopped identity fence, right after the startup line
        (retried every minute while the fence table cannot be read)."""
        if FENCES_AT_START_KEY in self._state.delivered or FENCES_AT_START_KEY in self._in_flight:
            return
        try:
            rows = await self._environment.fence_store.identity_rows()
        except Exception as exc:  # noqa: BLE001 - retried; the bridge's connect line names unreadable fences
            self._next_fence_announcement = (
                self._environment.monotonic() + REDELIVERY_COOLDOWN_SECONDS
            )
            self._log_quietly(
                "fences", logging.WARNING, "fenced identities unreadable at start: %s",
                type(exc).__name__,
            )
            return
        stopped = [(name, reason, updated) for name, stopped, reason, updated in rows if stopped]
        if not stopped:
            self._state.delivered[FENCES_AT_START_KEY] = "sent"
            self._state.dirty = True
            return
        logger.warning("fenced at start: %s", ", ".join(name for name, _reason, _at in stopped))
        self._send_alert(
            FENCES_AT_START_KEY, fences_at_start_text(stopped), key=FENCES_AT_START_KEY
        )

    def _broker_recovered(self, text: str) -> None:
        logger.info("%s over: %s", BROKER_UNREACHABLE, text)
        # Once per announced outage: the hysteresis emits one close per open.
        self._send_alert("broker_reconnected", text)
        # The next announced outage alerts at once, not after the last alert's half hour.
        if self._state.alerts.pop(BROKER_UNREACHABLE, None) is not None:
            self._state.dirty = True

    def _carried(self, line: str) -> bool:
        """A supervisor line that is not news today: the rotation's marker, a line
        it carried into the new file, or one dated before this session."""
        return self._rotation.is_carried(line, stale=self._before_session)

    def _before_session(self, at: datetime) -> bool:
        return at.astimezone(MARKET_TIMEZONE).date() < self._session_date

    def _handle_line(self, source: str, line: str) -> None:
        if source == "supervisor" and self._carried(line):
            return
        recovered = self._broker.observe(source, line, self._environment.clock())
        if recovered is not None:
            self._broker_recovered(recovered)
        if source == "supervisor":
            if self._schema_refusal(line):
                return
            capped = restart_cap_event(line)
            if capped is not None:
                kind, name, text = capped
                # Lines about the notifier itself are the Telegram child's, at any
                # hour (N15b L-3): read late after its own restart, they would repeat.
                if name != NOTIFIER_CHILD and self._restart_cap_allowed(kind, name):
                    self._send_alert(f"{kind}:{name}", text)
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
        sized = size_tripwire(line)
        if sized is not None:
            self._size_tripwire(*sized)
        if source == "bridge":
            if _PREFLIGHT_REFUSED.search(line):
                self._state.count(PREFLIGHT_REFUSED)
            if not self._broker.folds_bridge_line(line):
                self._handle_bridge_line(line)
            # The bridge already reports its own disconnects as CRITICAL.
            problem = ibkr_session_problem(line, include_connection_lost=False)
        else:
            problem = ibkr_session_problem(line)
        if problem == "competing_login":
            self._competing_login(self._competing.seen(self._environment.clock(), source))
        elif problem and self._alert_allowed("ibkr_session"):
            self._send_alert(f"ibkr_session:{problem}", ibkr_alert_text(problem, source))

    def _size_tripwire(self, token: str, identity: str, text: str) -> None:
        """Once per (token, identity) and session date, normal sound: a problem that
        lasts pages again at the next session's start read. A lost delivery is retried."""
        key = SIZE_TRIPWIRE_KEY.format(token=token, identity=identity)
        if key in self._state.delivered or key in self._in_flight:
            return
        logger.warning("size tripwire %s: forwarded to the phone", token)
        self._send_alert(key, text, key=key)

    def _follow_competing(self) -> None:
        self._competing_login(self._competing.tick(self._environment.clock()))

    def _competing_login(self, decision: CompetingDecision | None) -> None:
        """A competing login's page or re-page, under the persisted IBKR alert
        throttle (one IBKR alert per 10 minutes, across restarts)."""
        if decision is None:
            return
        if decision.kind is CompetingKind.CLEARED:
            logger.info(
                "competing login over (no line for %d min; since %s ET)",
                round(COMPETING_LOGIN_REPAGE.total_seconds() / 60),
                _et_hhmm(decision.since),
            )
            return
        if not self._alert_allowed("ibkr_session"):
            # The phone heard an IBKR alert inside the interval (perhaps before a
            # restart): the next re-page counts from it.
            heard = _parse_time(self._state.alerts.get("ibkr_session"))
            if heard is not None:
                self._competing.heard(heard)
            return
        if decision.kind is CompetingKind.OPENED:
            text = ibkr_alert_text("competing_login", decision.source)
        else:
            text = competing_login_repage_text(decision.since, decision.source)
        self._send_alert("ibkr_session:competing_login", text)

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
        """One digest per ET half hour with the shadow decisions since the last,
        and a line for the Gateway's flaps since the last (flaps alone send one)."""
        if not self._digest and not self._broker.flaps_pending:
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
        flap_line = self._broker.take_flap_line()
        if not pending and flap_line is None:
            return
        managed = await load_managed_rows(self._session_date) if pending else {}
        text = format_digest([decision for _member, decision in pending], managed, now)
        if flap_line is not None:
            text += f"\n{flap_line}"
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
                ops_counters=self._ops_counters(),
                fence_store=self._environment.fence_store,
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

    def _restart_cap_allowed(self, kind: str, name: str) -> bool:
        """Each restart-cap line once per child per half hour; but a suspension or
        a resumption that contradicts what the phone last heard about that child
        always goes, so the phone never shows "resumed" for a suspended child."""
        key = f"{kind}:{name}"
        if kind in _CHILD_STATES:
            told = self._child_told.get(name)
            if told is None:
                told = self._persisted_child_state(name)
            if told != kind:
                self._child_told[name] = kind
                self._state.alerts[key] = self._environment.clock().isoformat()
                self._state.dirty = True
                return True
        return self._alert_allowed(key, CHILD_EXIT_ALERT_INTERVAL)

    def _persisted_child_state(self, name: str) -> str | None:
        """The later of the persisted suspended/resumed alert times (a restart)."""
        suspended = _parse_time(self._state.alerts.get(f"{CHILD_SUSPENDED}:{name}"))
        resumed = _parse_time(self._state.alerts.get(f"{CHILD_RESUMED}:{name}"))
        if suspended is not None and (resumed is None or suspended > resumed):
            return CHILD_SUSPENDED
        if resumed is not None and (suspended is None or resumed > suspended):
            return CHILD_RESUMED
        return None

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
    "FENCES_AT_START_KEY",
    "FillCoalescer",
    "LogTailer",
    "MarketContextReader",
    "NotifyEnvironment",
    "NotifyState",
    "OpsCounters",
    "OpsReport",
    "SENTINEL_CHECK_SECONDS",
    "SignalDecision",
    "bridge_event_text",
    "GATEWAY_OUTAGE_CHECKED_KEY",
    "GATEWAY_OUTAGE_FRESH_FOR",
    "GATEWAY_OUTAGE_STAMP_INTERVAL",
    "GatewayOutage",
    "bridge_state_files",
    "gateway_outage_current",
    "notifier_gateway_outage",
    "child_exit_code",
    "manager_tripwire",
    "size_tripwire",
    "supervisor_child_name",
    "collect_daily_report",
    "close_line",
    "collect_ops",
    "fences_at_start_text",
    "flatten_shown",
    "held_positions",
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
    "supervisor_ops",
    "rotation_carried",
    "without_carried",
]

"""Read-only check of one PAPER bridge session (W6.4, first v7b session 2026-10-19).

``autotraderctl session check --date D`` reads, for the New York session D,
only these inputs and never changes any of them:

* the ``bridge.log`` section of D: every bridge process the supervisor
  launched with ``--date D`` (its ``=== <ISO> supervisor starts bridge: ...``
  banner), from the ``bridge.log.YYYY-MM-DD`` archives and ``bridge.log``;
  a process without a banner counts when its own lines name D;
* ``supervisor.log`` and its archives, less the lines a rotation carried
  (``log_tailer.without_carried``): the bridge child's starts and exits on D;
* the bridge's own state ``<data>/bridge/D.json``;
* the database, opened ``file:...?mode=ro`` with ``query_only`` (never
  written, never migrated): the decisions exactly as the bridge reads them
  (``DECISIONS_SQL``, ``parse_intent``), the account snapshots a decision may
  have been sized from, and ``autotrader_execution_fences``.

``notifier.log`` (the Sentinel's would-fence lines) and the research bars
under ``<data>/research/bars/D/`` are read for rule 8 when they exist.

Nine rules, each PASS, WARN, FAIL or INFO with its evidence lines:

1. identities: every SENT names a sendable identity of that day's bridge and
   of ``SENDABLE_IDENTITIES``, its state bracket and its ledger decision carry
   the same name, and a shadow-only name appears only as a SKIP;
2. attribution and close-out: every FILL, EXIT and CLOSED line belongs to the
   SENT of its symbol (FILL lines name the identity; the state confirms the
   rest), nothing is "still open after flatten", a FLAT line comes at or after
   the flatten (close - 15 min), the last bridge process stopped with exit 0
   and the supervisor's last bridge exit was code 0;
3. caps: per identity, open brackets (SENT until CLOSED or CANCELLED) never
   exceed its position cap, entries (SENT attempts) never exceed its daily
   cap, and no entry comes at or after its last-entry time (the real XNYS
   close less its offset, half days included);
4. size: every SENT's shares equal its decision's intent quantity (and the
   state's), and that quantity is floor(policy quantity x identity row). The
   policy quantity is recomputed with ``trading.risk.engine.policy_quantity``
   at multiplier 1 only from recorded inputs (an account snapshot of the run
   whose portfolio fingerprint is the decision's, and the default risk policy
   by fingerprint); without them the rule says ``size check: inputs not
   recorded`` (WARN, never PASS). Either way it FAILs a quantity above the
   identity row's size even at the logged net liquidation +10 %, or above the
   bridge's order ceilings (25 % notional, 1 % risk of net liquidation);
5. daily loss: realized P&L >= -2 % of net liquidation, or no entry was sent
   once the realized loss had reached it;
6. fences: every fence row is listed; an identity (or the global switch)
   stopped during the day is a WARN, an entry sent while its fence was
   stopped is a FAIL;
7. day risk budget (bridge v7c, owner decisions D1 and D5): every accepted
   risk-increasing order, a bot SENT or an owner buy's MANUAL SENT, is
   matched to the ``v7c_budget_ok`` line its own bridge process logs right
   before it (a line never vouches across a restart),
   whose realized + open + new total must stay within 3 % of its net
   liquidation (one cent of the bridge's rounding allowed). A malformed budget
   line, a total above 3 %, a process that logged another cap or slippage
   factor, an entry of a v7c process without its line and a
   ``v7c_budget_invariant_broken`` page FAIL. An entry of a process that
   logged no day risk budget (a pre-v7c bridge) cannot be verified:
   ``day_risk_budget_unverified`` (WARN, never PASS). Over-budget, widened-stop
   and wrong-thread pages are WARNs; refusals are counted. A ``v7c_budget_ok``
   line is logged only when nothing would refuse, so one whose realized loss
   reached the -2 % stop FAILs (an owner buy past -2 %, owner decision D8);
8. no-limit test metrics (INFO, never a failure): the nightly report of the
   owner's plan v2, per sendable identity and in total (signals, AI verdicts,
   orders, positions at once, trades with R, latency, buying power, stop
   repairs, guards, Sentinel would-fence lines, loss-step pages, SPY and QQQ);
   ``--json`` prints the whole report for the replay engine, ``--owner`` the
   short owner text;
9. abort rules (plan v2 "Abort rules", "Decision step 1"): a robot position
   without a working stop for more than 60 s, a bot entry outside the XNYS
   session, a position open past the close less 10 minutes (15:50 ET; 12:50
   on a half day), a duplicate entry (same symbol and size within 5 s), and
   the same malfunction guard tripping twice in the day each FAIL.

No-limit PAPER test L-T1 (bridge v7c-t1, owner plan v2 of 2026-10-23): a
bridge process that logs the ``t1_mode`` line runs the test. Its caps rows
must be the test's (no position or entries/day cap, the same last-entry
offsets); rule 3 then FAILs only those rows or a missed last-entry time and
reports the counts; rule 5 is INFO (the -2 % stop is measured, never
blocking); rule 7 also takes the bridge's ``t1_over_v7c_limits`` line as a
bot entry's measurement and counts the entries over v7c's limits; rule 4
approves the test risk policy besides the default. A process without the
line is held to v7c exactly as before, so ``deploy undo`` needs no checker
change and a day that mixes the two modes FAILs.

Exit 0 when no rule FAILs (WARN and INFO allowed), 1 when one does, 2 when an
input is missing. Every line of the report is masked: an account id (``DU``
and five or more letters or digits) is printed as ``DU1234567``.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Iterator, Mapping, Sequence
from contextlib import closing
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
import json
from pathlib import Path
import re
import sqlite3
from types import MappingProxyType
from typing import Any, cast

from trading.contracts import AccountSnapshot, PortfolioSnapshot, RiskPolicy, RuleSetup
from trading.operations.log_tailer import line_time, without_carried
from trading.research.live_ledger import BRIDGE_STATE_DIRECTORY, STATE_SCHEMA_PREFIX
from trading.research.v7c_report import (
    DAILY_LOSS_LIMIT,
    DAY_RISK_BUDGET_PCT,
    STOP_SLIPPAGE_FACTOR,
    V7C_BUDGET_INVARIANT_BROKEN,
    V7C_BUDGET_OK,
    V7C_BUDGET_OVER,
    V7C_DAY_RISK_BUDGET,
    V7C_STOP_WIDENED,
    V7C_UNPROTECTED_POSITION,
    V7C_WRONG_THREAD,
    block_token,
    nearest_rank,
)
from trading.risk.engine import policy_quantity, scaled_quantity
from trading.risk.identity_sizing import IDENTITY_SIZE_MULTIPLIER
from trading.risk.portfolio import portfolio_from_account
from trading.scheduler.sessions import MARKET_TIMEZONE, market_session
from trading.sendable_identities import SENDABLE_IDENTITIES
from trading.storage.runtime_fence import GLOBAL_FENCE, IDENTITY_FENCE_PREFIX

try:  # The test risk policy of the no-limit test L-T1 (plan rev 1 T1-1, trading/risk/no_limit_test.py).
    from trading.risk.no_limit_test import NO_LIMIT_TEST_POLICY as _NO_LIMIT_TEST_POLICY
except ModuleNotFoundError as _absent:  # pragma: no cover - only a tree without T1-1: the default alone
    if _absent.name != "trading.risk.no_limit_test":
        raise
    _NO_LIMIT_TEST_POLICY = None

EXIT_PASSED = 0
EXIT_FAILED = 1
EXIT_INPUTS_MISSING = 2

PASS = "PASS"
WARN = "WARN"
FAIL = "FAIL"
INFO = "INFO"  # a rule that only reports: never a failure, never a warning

ACCOUNT_ID = re.compile(r"DU[A-Z0-9]{5,}")
MASKED_ACCOUNT_ID = "DU1234567"

NY = MARKET_TIMEZONE


@dataclass(frozen=True, slots=True)
class IdentityCaps:
    """One sendable identity's entry limits, as a bridge ``Caps`` row (None = no cap)."""

    max_positions: int | None
    max_entries_per_day: int | None
    last_entry_before_close: timedelta


# The v7c bridge's IDENTITY_CAPS (scripts_local/paper_bridge.py v7b/v7c; what
# ``deploy undo`` restores), row for row: a bridge process that did not log the
# no-limit test's mode line is held to these. The checker never imports the
# bridge, so a test loads it and pins the two equal (and the names equal to
# SENDABLE_IDENTITIES).
BRIDGE_IDENTITY_CAPS: Mapping[str, IdentityCaps] = MappingProxyType({
    "MOMO_HOD_V2": IdentityCaps(3, None, timedelta(minutes=60)),
    "ORB_V2": IdentityCaps(1, 1, timedelta(minutes=30)),
    "VWAP_PB_V2": IdentityCaps(1, 2, timedelta(minutes=30)),
    "GAP_GO_V2": IdentityCaps(1, 1, timedelta(minutes=30)),
})
# Bridge v7c-t1's rows while it runs the no-limit test L-T1: no position or
# entries/day cap, the same last-entry offsets (a test pins them to the bridge).
NO_LIMIT_TEST_IDENTITY_CAPS: Mapping[str, IdentityCaps] = MappingProxyType({
    name: IdentityCaps(None, None, caps.last_entry_before_close) for name, caps in BRIDGE_IDENTITY_CAPS.items()
})
# The test risk policy is approved besides the default: rule 4 recomputes a decision's
# size only under a policy it finds here by the decision's own fingerprint.
APPROVED_POLICIES: Mapping[str, RiskPolicy] = MappingProxyType({
    policy.fingerprint(): policy
    for policy in (RiskPolicy(), *(() if _NO_LIMIT_TEST_POLICY is None else (_NO_LIMIT_TEST_POLICY,)))
})

# Bridge v7c-t1's tokens (paper_bridge.py, plan v2 items 3-5; a test pins each to the
# bridge): every one starts its bridge.log line or SKIP reason.
NO_LIMIT_TEST_LABEL = "L-T1"
T1_MODE = "t1_mode"
T1_OVER_V7C_LIMITS = "t1_over_v7c_limits"
T1_ACCOUNT_VALUES = "t1_account_values"
T1_LOW_CUSHION = "t1_low_cushion"
T1_REALIZED_LOSS_STEP = "t1_realized_loss_step"
T1_BROKER_REJECTED = "t1_broker_rejected"
T1_STOP_REPAIR = "t1_stop_repair"
T1_UNPROTECTED_MANUAL = "t1_unprotected_manual"
T1_GUARD_ORDER_RATE = "t1_guard_order_rate"
T1_GUARD_REJECTIONS = "t1_guard_rejections"
T1_GUARD_SYMBOL_ENTRIES = "t1_guard_symbol_entries"
T1_GUARD_DUPLICATE = "t1_guard_duplicate"
T1_GUARD_OUTSIDE_HOURS = "t1_guard_outside_hours"
T1_GUARD_TOKENS = (T1_GUARD_ORDER_RATE, T1_GUARD_REJECTIONS, T1_GUARD_SYMBOL_ENTRIES, T1_GUARD_DUPLICATE,
                   T1_GUARD_OUTSIDE_HOURS)
# What a t1_over_v7c_limits line may say v7c would have refused (budget_check's Budget.over).
T1_OVER_TOKENS = frozenset({DAILY_LOSS_LIMIT, V7C_DAY_RISK_BUDGET, V7C_UNPROTECTED_POSITION})
# The abort rules (owner plan v2): a robot position may run without a working stop at
# most STOP_REPAIR_DEADLINE (paper_bridge.STOP_REPAIR_DEADLINE), the same symbol and size
# twice within GUARD_DUPLICATE_WINDOW is a double send (paper_bridge.GUARD_DUPLICATE_WINDOW),
# and every position is flat by the close less FLAT_BY_BEFORE_CLOSE (15:50 ET, 12:50 on a
# half day: paper_bridge.VERIFY_BEFORE_CLOSE, IBKR's Reg T end-of-day check).
STOP_REPAIR_DEADLINE = timedelta(seconds=60)
GUARD_DUPLICATE_WINDOW = timedelta(seconds=5)
FLAT_BY_BEFORE_CLOSE = timedelta(minutes=10)
GUARD_TRIPS_ABORT = 2  # "the same guard tripping twice in one day"
# IBKR's US-stock bar volume is in round lots (shadow_service.IBKR_API_VOLUME_MULTIPLIER; pinned).
BAR_VOLUME_MULTIPLIER = Decimal("100")
MARKET_SYMBOLS = ("SPY", "QQQ")
NOTIFIER_LOG = "notifier.log"
SENTINEL_WOULD_FENCE = "sentinel would fence "  # sentinel.fence_identity's log-only line
TOP_REASONS = 3
REASON_WIDTH = 80
MAX_BARS_FILE_BYTES = 2 * 1024 * 1024  # research.bars_store._MAX_FILE_BYTES
REPORT_SCHEMA = "autotrader-v1/session-check"
REPORT_SCHEMA_VERSION = 1
NO_LIMIT_MODE = "no_limit_test"  # the day's bridge processes logged the t1_mode line
V7C_MODE = "v7c"
METRICS_RULE = 8
ABORT_RULE = 9
# The bridge's session checkpoints and order ceilings (paper_bridge.py
# LAST_ENTRY_BEFORE_CLOSE, FLATTEN_BEFORE_CLOSE, MAX_*_PCT; pinned by a test).
SESSION_LAST_ENTRY_BEFORE_CLOSE = timedelta(minutes=30)
FLATTEN_BEFORE_CLOSE = timedelta(minutes=15)
MAX_DAILY_LOSS_PCT = Decimal("2")
MAX_ORDER_NOTIONAL_PCT = Decimal("25")
MAX_ORDER_RISK_PCT = Decimal("1")
SHADOW_ONLY_REASON = "shadow-only strategy"  # paper_bridge.py SHADOW_ONLY
# Rule 7: an accepted entry the log cannot verify (its process logged no day
# risk budget: a bridge older than v7c) is named, never passed.
DAY_RISK_BUDGET_UNVERIFIED = "day_risk_budget_unverified"
# The bridge logs each budget amount rounded to the cent: a total may read up
# to half a cent above what it compared, and three rounded parts may sum up
# to 1.5 cents away from their rounded total.
BUDGET_ROUNDING = Decimal("0.01")
BUDGET_SUM_TOLERANCE = Decimal("0.02")
HALF_CENT = Decimal("0.005")  # a logged cent amount is within this of what the bridge compared
# A decision is sized from the strategy's own account snapshot, not the
# bridge's: without a recorded snapshot the identity-row bound allows the
# logged net liquidation to have been this much higher (a x1 ORB is x4).
NET_LIQUIDATION_BAND = Decimal("0.10")
# paper_bridge.py DECISIONS_SQL verbatim (a test pins the two equal): the
# checker reads the decisions exactly as the bridge does.
DECISIONS_SQL = """
    SELECT d.id, d.run_id, c.symbol, c.con_id, c.contract_json, d.payload_json,
           d.created_at, v.spec_json
    FROM autotrader_candidate_decisions d
    JOIN autotrader_runs r ON r.id = d.run_id
    JOIN autotrader_candidates c ON c.id = d.candidate_id
    LEFT JOIN autotrader_strategy_versions v
      ON v.strategy_id = r.strategy_id AND v.version = r.strategy_version
    WHERE d.outcome = 'shadow_accepted' AND r.session_date = ?
    ORDER BY d.created_at, d.id
"""
ACCOUNT_SNAPSHOTS_SQL = (
    "SELECT payload_json FROM autotrader_account_snapshots WHERE run_id = ? ORDER BY observed_at, id"
)
FENCES_SQL = "SELECT name, stopped, reason, updated_at FROM autotrader_execution_fences ORDER BY name"

BRIDGE_LOG = "bridge.log"
SUPERVISOR_LOG = "supervisor.log"
MAX_LOG_BYTES = 64 * 1024 * 1024
MAX_STATE_BYTES = 5 * 1024 * 1024
# Phrases of the bridge's not-flat alerts (not_flat_message, report_state_after_close).
NOT_FLAT_PHRASES = ("still open after flatten", "still open per bridge state", "unreconciled bracket",
                    "exceed entry fills", "may be short")

_BANNER = re.compile(r"^=== (?P<at>\S+) supervisor starts bridge: (?P<argv>.*)$")
_DATE_ARGUMENT = re.compile(r"(?:^|\s)--date\s+(?P<day>\d{4}-\d{2}-\d{2})(?=\s|$)")
_EVENT = re.compile(r"^(?P<clock>\d{2}:\d{2}:\d{2}) ET\s+(?P<kind>[A-Z]+) (?P<text>.*)$")
_RUN_DAY = (
    re.compile(r"Managing brackets for (?P<day>\d{4}-\d{2}-\d{2}) until"),
    re.compile(r"/(?P<day>\d{4}-\d{2}-\d{2})\.json: \d+ brackets"),
    re.compile(r"\bfor (?P<day>\d{4}-\d{2}-\d{2}) \S+ nothing to manage"),
    re.compile(r"\bsession (?P<day>\d{4}-\d{2}-\d{2}) is over"),
)
_SHA = re.compile(r"^bridge sha256=(?P<sha>[0-9a-f]{64})")
_CONNECTED = re.compile(r"^PAPER bridge (?P<version>\S+) connected\b")
_STOPPED = re.compile(r"^bridge stopped \(exit (?P<code>-?\d+)\)")
_SENDABLE = re.compile(r"^sendable identities = (?P<names>.*)$")
_CAPS = re.compile(r"^caps (?P<name>\S+) positions=(?P<positions>\d+|none) entries/day=(?P<entries>\S+)"
                   r" last_entry=close-(?P<minutes>\d+)m$")
_FENCED = re.compile(r"^fenced identities = (?P<names>.*)$")
_NET_LIQ = re.compile(r"^net liquidation (?P<value>\d+(?:\.\d+)?); daily loss stop")
_SESSION_END = re.compile(r"^session end: realized P&L (?P<value>-?\d+(?:\.\d+)?)")
_SENT = re.compile(r"^(?P<symbol>.+?) \((?P<label>[^()]*)\): BUY (?P<qty>\d+) @ (?P<entry>\d+(?:\.\d+)?)"
                   r" \| stop (?P<stop>\d+(?:\.\d+)?) \| target (?P<target>\d+(?:\.\d+)?)"
                   r" \(orders (?P<parent>\d+)-(?P<last>\d+)\)$")
_FILL = re.compile(r"^(?P<symbol>.+?): (?P<filled>\d+)/(?P<qty>\d+) sh @ (?P<avg>\d+(?:\.\d+)?)"
                   r" \((?P<label>[^()]*)\)$")
_EXIT = re.compile(r"^(?P<symbol>.+?): (?P<qty>\d+) sh @ (?P<avg>\d+(?:\.\d+)?) via (?P<reason>\S+)"
                   r" \| P&L (?P<pnl>-?\d+(?:\.\d+)?)$")
_CLOSED = re.compile(r"^(?P<symbol>.+?): P&L (?P<pnl>-?\d+(?:\.\d+)?) \((?P<reason>[^()]*)\)$")
_CANCELLED = re.compile(r"^(?P<symbol>.+?): entry never filled$")
_SKIP = re.compile(r"^(?P<symbol>.+?)(?: \((?P<label>[^()]*)\))?: (?P<reason>.*)$")
_NOT_FOUND = re.compile(r"^(?P<symbol>.+?): bracket not found at IBKR")
_SUPERVISOR = re.compile(r"^(?P<at>\S+)\s+(?:!!!\s+)?(?P<event>[A-Z_]+)\s+bridge(?:\s+(?P<rest>.*))?$")
_CODE = re.compile(r"\bcode=(?P<code>\S+)")
_RESTART = re.compile(r"\brestart_in=(?P<restart>\S+)")
_ARCHIVE_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# Bridge v7c's day risk budget lines (paper_bridge.py budget_line, process_decision,
# ManualDesk.buy); a test feeds the bridge's own lines through these.
_BUDGET_RULE_START = "day risk budget "
_BUDGET_RULE = re.compile(r"^day risk budget (?P<pct>\d+(?:\.\d+)?)% of net liquidation, account level \(.*;"
                          r" stop slippage factor (?P<factor>\d+(?:\.\d+)?)\)$")
_BUDGET_OK = re.compile(
    r"^v7c_budget_ok (?P<symbol>.+?) \((?P<label>[^()]*)\): realized (?P<realized>\d+\.\d{2})"
    r" \+ open (?P<open>\d+\.\d{2}) \+ new (?P<new>\d+\.\d{2}) = (?P<total>\d+\.\d{2})"
    r" \((?P<percent>\d+\.\d{2})%\) <= (?P<cap>\d+(?:\.\d+)?)% of (?P<net_liq>\d+\.\d{2})"
    r" \(source=(?P<source>bot|manual)\)$"
)
_MANUAL_LABEL = re.compile(r"^manual #(?P<short>[0-9a-f]+)$")
# An owner buy as the bridge places it (v7c: with its attached stop); a status
# follow-up after a restart says "(orders [...])" and places nothing.
_MANUAL_BUY = re.compile(r"^SENT BUY .+ #(?P<short>[0-9a-f]+) \(order (?P<order>\d+)\)(?P<stop> with its stop .+)?$")
_MANUAL_REJECTED = re.compile(r"^REJECTED .* \((?P<code>[a-z0-9_]+)\)$")
_V7C_PAGE = re.compile(r"^(?P<token>v7c_[a-z_]+)\b")
_BUDGET_OK_START = re.compile(r"^v7c_budget_ok\b")
# Bridge v7c-t1's lines (paper_bridge.py process_decision, repair_stop, close_unprotected,
# entry_rejected, halt_identity, watch_account_values, page_loss_step; real lines feed these in
# a test once the bridge is merged).
_OVER_START = re.compile(r"^t1_over_v7c_limits\b")
_OVER = re.compile(
    r"^t1_over_v7c_limits (?P<symbol>.+?) \((?P<label>[^()]*)\): realized (?P<realized>\d+\.\d{2})"
    r" \+ open (?P<open>\d+\.\d{2}) \+ new (?P<new>\d+\.\d{2}) = (?P<total>\d+\.\d{2})"
    r" \((?P<percent>\d+\.\d{2})%\) vs (?P<cap>\d+(?:\.\d+)?)% of (?P<net_liq>\d+\.\d{2})"
    r" — would refuse: (?P<tokens>.*) \(source=(?P<source>bot|manual)\)$"
)
_REPAIR = re.compile(r"^t1_stop_repair (?P<symbol>.+?) \((?P<label>[^()]*)\): (?P<rest>.*)$")
_REPAIR_DEADLINE = re.compile(r"closed at market (?:if it is not working by|at) (?P<clock>\d{2}:\d{2}:\d{2}) ET")
_REPAIRED = re.compile(r"^repaired — stop order \d+ works at \S+(?: (?P<seconds>\d+)s after detection)?$")
_REPAIR_CLOSING = " — closing at market: "
_REJECTED_ENTRY = re.compile(r"^(?P<symbol>.+?): entry never filled — rejected by IBKR$")
_BROKER_REJECTED = re.compile(r"^t1_broker_rejected (?P<symbol>.+?) \((?P<identity>[^()]*)\): IBKR rejected the"
                              r" entry \(TWS (?P<code>-?\d+) ")
_HALT = re.compile(r"^t1_guard_rejections (?P<identity>\S+): .* — no new \S+ entries for the rest of")
_ACCOUNT_VALUE = re.compile(r"(?P<tag>[A-Za-z]+)=(?P<value>\S+)")
_LOSS_STEP = re.compile(r"^t1_realized_loss_step (?P<step>\d+)%:")
_ORDER_STATUS = re.compile(r"^order (?P<order>\d+): (?P<status>\S+) filled=")
# IBKR has the order: the first of these statuses for an entry's parent is its acknowledgement.
ACKNOWLEDGED_STATUSES = frozenset({"PreSubmitted", "Submitted", "Filled"})
EXPIRED_REASON = "decision expired"  # paper_bridge._expiry_problem


def mask_accounts(text: str) -> str:
    """Every account id (``DU`` + 5 or more letters or digits) as ``DU1234567``."""
    return ACCOUNT_ID.sub(MASKED_ACCOUNT_ID, text)


class InputsMissing(Exception):
    """An input the check needs is absent or unreadable: exit 2, no verdict."""


# --- reading: files ----------------------------------------------------------------


def _regular_file(path: Path) -> bool:
    return path.is_file() and not path.is_symlink()


def _read_bytes(path: Path, limit: int) -> bytes:
    """A regular file's bytes, opened for reading only, never through a symlink."""
    if not _regular_file(path):
        raise InputsMissing(f"{path.name} is not a regular file")
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise InputsMissing(f"{path.name} is larger than {limit} bytes")
    return raw


def log_files(directory: Path, name: str) -> list[Path]:
    """``<name>.YYYY-MM-DD`` archives oldest first, then ``<name>`` itself (regular files only)."""
    if not directory.is_dir():
        return []
    archives = sorted(
        path for path in directory.iterdir()
        if path.name.startswith(f"{name}.") and _ARCHIVE_DAY.match(path.name[len(name) + 1:])
        and _regular_file(path)
    )
    current = directory / name
    return [*archives, *([current] if _regular_file(current) else [])]


def _lines(path: Path) -> list[str]:
    return _read_bytes(path, MAX_LOG_BYTES).decode("utf-8", errors="replace").splitlines()


# --- reading: the bridge.log section ------------------------------------------------


@dataclass(slots=True)
class LogLine:
    source: str
    number: int
    run: int
    clock: time | None
    kind: str  # the event KIND; "" for a line that is not an event (a traceback)
    text: str

    @property
    def ref(self) -> str:
        return f"{self.source}:{self.number}"

    @property
    def stamp(self) -> str:
        return f"{self.clock:%H:%M:%S} ET" if self.clock is not None else "--:--:-- ET"

    def at(self, day: date) -> datetime | None:
        return None if self.clock is None else datetime.combine(day, self.clock, tzinfo=NY)

    def evidence(self) -> str:
        return f"{self.ref} {self.stamp} {self.kind} {self.text}".rstrip()


@dataclass(slots=True)
class BridgeRun:
    """One bridge process of the session: its banner (when supervised) and its lines."""

    index: int
    source: str
    first_line: int
    banner_at: datetime | None
    lines: list[LogLine] = field(default_factory=list)
    sha256: str = ""
    version: str = ""
    connected: bool = False
    stopped: LogLine | None = None
    stopped_code: int | None = None
    sendable: tuple[str, ...] | None = None
    caps: dict[str, IdentityCaps] = field(default_factory=dict)
    fenced: list[LogLine] = field(default_factory=list)
    net_liquidation: list[Decimal] = field(default_factory=list)
    session_end_pnl: Decimal | None = None
    no_limit: bool = False  # the process logged the no-limit test's mode line (t1_mode)


@dataclass(slots=True)
class BridgeSection:
    session_date: date
    runs: list[BridgeRun]
    files: list[str]

    def lines(self) -> Iterator[LogLine]:
        for run in self.runs:
            yield from run.lines


@dataclass(slots=True)
class _Block:
    day: date | None
    banner_at: datetime | None
    supervised: bool
    first_line: int
    raw: list[tuple[int, str]] = field(default_factory=list)
    started: bool = False


def _iso(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _block_day(block: _Block) -> date | None:
    for _, raw in block.raw:
        for pattern in _RUN_DAY:
            found = pattern.search(raw)
            if found is not None:
                try:
                    return date.fromisoformat(found.group("day"))
                except ValueError:
                    return None
    return None


def _blocks(lines: Sequence[str]) -> list[_Block]:
    """One block per bridge process: a supervisor banner, else a new ``bridge sha256=`` line."""
    blocks: list[_Block] = []
    current: _Block | None = None
    for number, raw in enumerate(lines, 1):
        banner = _BANNER.match(raw)
        if banner is not None:
            argument = _DATE_ARGUMENT.search(banner.group("argv"))
            try:
                day = date.fromisoformat(argument.group("day")) if argument else None
            except ValueError:
                day = None
            current = _Block(day=day, banner_at=_iso(banner.group("at")), supervised=True, first_line=number)
            blocks.append(current)
            continue
        event = _EVENT.match(raw)
        starts = event is not None and event.group("kind") == "INFO" and bool(_SHA.match(event.group("text")))
        if current is None or (starts and (current.started or not current.supervised)):
            current = _Block(day=None, banner_at=None, supervised=False, first_line=number)
            blocks.append(current)
        current.started = current.started or starts
        current.raw.append((number, raw))
    for block in blocks:
        if not block.supervised:
            block.day = _block_day(block)
    return blocks


def _decimal(value: object) -> Decimal | None:
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() else None


def _summarize(run: BridgeRun) -> None:
    for line in run.lines:
        text = line.text
        if line.kind == "INFO":
            if (found := _SHA.match(text)) is not None:
                run.sha256 = found.group("sha")
            elif (found := _CONNECTED.match(text)) is not None:
                run.version, run.connected = found.group("version"), True
            elif (found := _STOPPED.match(text)) is not None:
                run.stopped, run.stopped_code = line, int(found.group("code"))
            elif (found := _SENDABLE.match(text)) is not None:
                names = found.group("names").strip()
                run.sendable = () if names == "none" else tuple(name.strip() for name in names.split(","))
            elif (found := _CAPS.match(text)) is not None:
                positions, entries = found.group("positions"), found.group("entries")
                run.caps[found.group("name")] = IdentityCaps(
                    None if positions == "none" else int(positions), None if entries == "none" else int(entries),
                    timedelta(minutes=int(found.group("minutes"))),
                )
            elif text.startswith(f"{T1_MODE} "):
                run.no_limit = True
            elif (found := _NET_LIQ.match(text)) is not None:
                value = _decimal(found.group("value"))
                if value is not None and value > 0:
                    run.net_liquidation.append(value)
            elif (found := _SESSION_END.match(text)) is not None:
                run.session_end_pnl = _decimal(found.group("value"))
        if line.kind in {"INFO", "CRITICAL"} and _FENCED.match(text):
            run.fenced.append(line)


def read_bridge_section(logs: Path, day: date) -> BridgeSection:
    """Every bridge process of ``day`` in the bridge logs, in file order."""
    files = log_files(logs, BRIDGE_LOG)
    if not files:
        raise InputsMissing(f"no {BRIDGE_LOG} in {logs}")
    runs: list[BridgeRun] = []
    used: list[str] = []
    for path in files:
        for block in _blocks(_lines(path)):
            if block.day != day:
                continue
            run = BridgeRun(index=len(runs), source=path.name, first_line=block.first_line,
                            banner_at=block.banner_at)
            for number, raw in block.raw:
                event = _EVENT.match(raw)
                if event is None:
                    run.lines.append(LogLine(path.name, number, run.index, None, "", raw))
                    continue
                clock = datetime.strptime(event.group("clock"), "%H:%M:%S").time()
                run.lines.append(LogLine(path.name, number, run.index, clock, event.group("kind"),
                                         " ".join(event.group("text").split())))
            _summarize(run)
            runs.append(run)
            if path.name not in used:
                used.append(path.name)
    if not runs:
        raise InputsMissing(f"no {BRIDGE_LOG} section for {day.isoformat()} in {logs}")
    return BridgeSection(session_date=day, runs=runs, files=used)


# --- reading: supervisor.log ----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SupervisorEvent:
    at: datetime
    event: str  # CHILD_START, CHILD_EXIT, LAUNCH_REFUSED, SIGNAL, CHILD_RESTART_STORM, ...
    code: str | None
    restart: str | None
    ref: str
    text: str


def read_supervisor_bridge(logs: Path, day: date) -> tuple[list[SupervisorEvent], int]:
    """The bridge child's supervisor events dated ``day`` in New York; and the files read."""
    files = log_files(logs, SUPERVISOR_LOG)
    if not files:
        raise InputsMissing(f"no {SUPERVISOR_LOG} in {logs}")
    events: list[SupervisorEvent] = []
    for path in files:
        lines = _lines(path)
        # A carried copy's original is in the archive: count each line once (every
        # supervisor line carries its own time, so equal text means the same line).
        kept = Counter(without_carried(lines))
        for number, raw in enumerate(lines, 1):
            if kept[raw] <= 0:
                continue
            kept[raw] -= 1
            found = _SUPERVISOR.match(raw.strip())
            at = line_time(raw)
            if found is None or at is None or at.astimezone(NY).date() != day:
                continue
            rest = found.group("rest") or ""
            code = _CODE.search(rest)
            restart = _RESTART.search(rest)
            events.append(SupervisorEvent(
                at=at, event=found.group("event"), code=code.group("code") if code else None,
                restart=restart.group("restart") if restart else None, ref=f"{path.name}:{number}",
                text=raw.strip(),
            ))
    return events, len(files)


# --- reading: the bridge state --------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StateBracket:
    decision_id: str
    symbol: str
    strategy_name: str
    qty: int
    entry: Decimal | None
    stop: Decimal | None
    parent: int | None
    status: str
    skipped: bool
    skip_reason: str
    filled: int
    exit_filled: int
    realized_pnl: Decimal
    sent_at: datetime | None
    con_id: int = 0
    avg_entry: Decimal | None = None
    avg_exit: Decimal | None = None
    exit_reason: str = ""
    broker_rejection: str = ""  # bridge v7c-t1: "<TWS code> <message>" when IBKR rejected the entry

    @property
    def sent(self) -> bool:
        return self.parent is not None

    @property
    def net_held(self) -> int:
        return self.filled - self.exit_filled


def _int(value: object, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    number = _decimal(value)
    return int(number) if number is not None and number == number.to_integral_value() else default


def _bracket(decision_id: str, item: Mapping[str, Any]) -> StateBracket:
    parent = item.get("parent")
    return StateBracket(
        decision_id=decision_id,
        symbol=str(item.get("symbol") or "?"),
        strategy_name=str(item.get("strategy_name") or "").strip(),
        qty=_int(item.get("qty")),
        entry=_decimal(item.get("entry")),
        stop=_decimal(item.get("stop")),
        parent=parent if isinstance(parent, int) and not isinstance(parent, bool) else None,
        status=str(item.get("status") or ""),
        skipped=bool(item.get("skipped")),
        skip_reason=str(item.get("skip_reason") or ""),
        filled=_int(item.get("filled")),
        exit_filled=_int(item.get("exit_filled")),
        realized_pnl=_decimal(item.get("realized_pnl")) or Decimal("0"),
        sent_at=_iso(str(item.get("sent_at") or "")),
        con_id=_int(item.get("con_id")),
        avg_entry=_decimal(item.get("avg_entry")),
        avg_exit=_decimal(item.get("avg_exit")),
        exit_reason=str(item.get("exit_reason") or ""),
        broker_rejection=str(item.get("broker_rejection") or ""),
    )


def state_path(data: Path, day: date) -> Path:
    return data / BRIDGE_STATE_DIRECTORY / f"{day.isoformat()}.json"


def read_state(data: Path, day: date) -> dict[str, StateBracket]:
    """The bridge's state of ``day`` (never the dashboard mirror, never another date's file)."""
    path = state_path(data, day)
    if not path.exists():
        raise InputsMissing(f"no bridge state {BRIDGE_STATE_DIRECTORY}/{path.name} in {data}")
    try:
        document = json.loads(_read_bytes(path, MAX_STATE_BYTES).decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise InputsMissing(f"bridge state {path.name} is unreadable ({type(exc).__name__})") from exc
    schema = document.get("schema") if isinstance(document, dict) else None
    if not isinstance(schema, str) or not schema.startswith(STATE_SCHEMA_PREFIX):
        raise InputsMissing(f"bridge state {path.name} is not a bridge state document")
    if document.get("session_date") not in (None, day.isoformat()):
        raise InputsMissing(f"bridge state {path.name} holds session {document.get('session_date')}")
    submitted = document.get("submitted")
    if not isinstance(submitted, dict):
        raise InputsMissing(f"bridge state {path.name} has no submitted map")
    return {str(key): _bracket(str(key), item) for key, item in submitted.items() if isinstance(item, dict)}


# --- reading: the database (read-only) ---------------------------------------------------


def open_read_only(db: Path) -> sqlite3.Connection:
    """``file:<db>?mode=ro`` plus ``PRAGMA query_only``: no write or migration can pass."""
    path = db.expanduser()
    if not _regular_file(path.resolve()):
        raise InputsMissing(f"no database at {db}")
    try:
        connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=5)
        connection.execute("PRAGMA query_only = ON")
    except sqlite3.Error as exc:
        raise InputsMissing(f"database {db} unreadable ({type(exc).__name__}: {exc})") from exc
    return connection


@dataclass(frozen=True, slots=True)
class Intent:
    quantity: int
    entry: Decimal
    stop: Decimal
    target: Decimal


def parse_intent(payload_json: str) -> Intent | None:
    """The bridge's own reading of a decision's trade intent (``paper_bridge.parse_intent``;
    a test pins the two equal): ``risk.intent`` quantity, entry, stop and target."""
    payload = json.loads(payload_json)
    risk = payload.get("risk") if isinstance(payload, dict) else None
    intent = risk.get("intent") if isinstance(risk, dict) else None
    if not isinstance(intent, dict) or not intent:
        return None
    quantity = Decimal(str(intent["quantity"]))
    if not quantity.is_finite() or quantity != quantity.to_integral_value():
        raise ValueError("trade intent quantity is not a whole number")
    return Intent(
        quantity=int(quantity),
        entry=Decimal(str(intent["entry_limit"])),
        stop=Decimal(str(intent["stop_price"])),
        target=Decimal(str(intent["target_price"])),
    )


def strategy_name_from_spec(spec_json: object) -> str:
    """The ledger name the bridge gates on (``paper_bridge.strategy_name_from_spec``)."""
    if not isinstance(spec_json, str):
        return ""
    try:
        spec = json.loads(spec_json)
    except ValueError:
        return ""
    name = spec.get("name") if isinstance(spec, dict) else None
    return name.strip() if isinstance(name, str) else ""


@dataclass(frozen=True, slots=True)
class LedgerDecision:
    decision_id: str
    run_id: str
    symbol: str
    created_at: str
    strategy_name: str
    intent: Intent | None
    problem: str
    policy_fingerprint: str
    portfolio_fingerprint: str


def read_decisions(connection: sqlite3.Connection, day: date) -> dict[str, LedgerDecision]:
    """Every ``shadow_accepted`` decision of ``day``, read with the bridge's own query."""
    decisions: dict[str, LedgerDecision] = {}
    for row in connection.execute(DECISIONS_SQL, (day.isoformat(),)).fetchall():
        payload_json = str(row[5])
        intent: Intent | None = None
        problem = ""
        risk: Any = {}
        try:
            intent = parse_intent(payload_json)
            payload = json.loads(payload_json)
            risk = payload.get("risk") if isinstance(payload, dict) else {}
        except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
            problem = f"intent unreadable ({type(exc).__name__})"
        risk = risk if isinstance(risk, dict) else {}
        decisions[str(row[0])] = LedgerDecision(
            decision_id=str(row[0]), run_id=str(row[1]), symbol=str(row[2]), created_at=str(row[6]),
            strategy_name=strategy_name_from_spec(row[7]), intent=intent,
            problem=problem or ("" if intent is not None else "no trade intent"),
            policy_fingerprint=str(risk.get("policy_fingerprint") or ""),
            portfolio_fingerprint=str(risk.get("portfolio_fingerprint") or ""),
        )
    return decisions


@dataclass(frozen=True, slots=True)
class FenceRecord:
    name: str
    stopped: bool
    reason: str
    updated_at: datetime | None
    updated_text: str

    @property
    def identity(self) -> str | None:
        return self.name[len(IDENTITY_FENCE_PREFIX):] if self.name.startswith(IDENTITY_FENCE_PREFIX) else None

    @property
    def label(self) -> str:
        return "global PAPER stop" if self.name == GLOBAL_FENCE else self.name


def read_fences(connection: sqlite3.Connection) -> list[FenceRecord]:
    return [
        FenceRecord(name=str(name), stopped=bool(_int(stopped)), reason=str(reason),
                    updated_at=_iso(str(updated)), updated_text=str(updated))
        for name, stopped, reason, updated in connection.execute(FENCES_SQL).fetchall()
    ]


def _account_snapshots(connection: sqlite3.Connection, run_id: str) -> list[AccountSnapshot]:
    snapshots: list[AccountSnapshot] = []
    try:
        rows = connection.execute(ACCOUNT_SNAPSHOTS_SQL, (run_id,)).fetchall()
    except sqlite3.Error:
        return snapshots  # no snapshot table: the inputs are simply not recorded
    for (payload_json,) in rows:
        try:
            snapshots.append(AccountSnapshot.model_validate(json.loads(str(payload_json))))
        except ValueError:
            continue  # an unreadable snapshot proves nothing: the inputs stay unrecorded
    return snapshots


# --- the replay ---------------------------------------------------------------------


@dataclass(slots=True)
class Entry:
    """One SENT line and everything the section and the state say about it."""

    line: LogLine
    symbol: str
    identity: str
    qty: int
    entry: Decimal
    stop: Decimal
    target: Decimal
    parent: int
    net_liquidation: Decimal | None
    bracket: StateBracket | None = None
    decision: LedgerDecision | None = None
    fills: list[LogLine] = field(default_factory=list)
    exits: list[LogLine] = field(default_factory=list)
    realized: Decimal = Decimal("0")
    closed: LogLine | None = None
    cancelled: LogLine | None = None
    rejected: LogLine | None = None  # bridge v7c-t1: "entry never filled — rejected by IBKR"
    open: bool = True

    def sent_at(self, day: date) -> datetime | None:
        if self.bracket is not None and self.bracket.sent_at is not None:
            return self.bracket.sent_at.astimezone(NY)
        return self.line.at(day)

    @property
    def name(self) -> str:
        return f"{self.identity} {self.symbol} (orders {self.parent}-{self.parent + 2})"


@dataclass(slots=True)
class Skip:
    line: LogLine
    symbol: str
    label: str | None
    reason: str


@dataclass(slots=True)
class Timeline:
    entries: list[Entry] = field(default_factory=list)
    unattributed: list[tuple[LogLine, str]] = field(default_factory=list)
    attributed: dict[str, int] = field(default_factory=lambda: {"FILL": 0, "EXIT": 0, "CLOSED": 0})
    max_open: dict[str, tuple[int, LogLine]] = field(default_factory=dict)
    max_total: tuple[int, LogLine | None] = (0, None)
    skips: list[Skip] = field(default_factory=list)
    flats: list[LogLine] = field(default_factory=list)
    not_flat: list[LogLine] = field(default_factory=list)
    manual: int = 0
    loss_crossed: LogLine | None = None
    loss_violations: list[Entry] = field(default_factory=list)
    # At each SENT: (identity, its brackets open then, all brackets open then), the new one included:
    # what a position cap would have checked (rule 8's "positions open at once").
    open_samples: list[tuple[str, int, int]] = field(default_factory=list)

    @property
    def realized(self) -> Decimal:
        return sum((entry.realized for entry in self.entries), Decimal("0"))


def _loss_limit(net_liquidation: Decimal) -> Decimal:
    return net_liquidation * MAX_DAILY_LOSS_PCT / 100


def replay(section: BridgeSection, state: Mapping[str, StateBracket],
           decisions: Mapping[str, LedgerDecision]) -> Timeline:
    """Walk the section in order: SENT opens a bracket, CLOSED or CANCELLED ends it."""
    timeline = Timeline()
    by_parent = {bracket.parent: bracket for bracket in state.values() if bracket.parent is not None}
    latest: dict[str, Entry] = {}
    net_liquidation: Decimal | None = None
    run = -1
    for line in section.lines():
        if line.run != run:  # a new process knows no net liquidation until it logs one
            run, net_liquidation = line.run, None
        kind, text = line.kind, line.text
        if kind == "INFO":
            found = _NET_LIQ.match(text)
            if found is not None:
                net_liquidation = _decimal(found.group("value"))
            rejected = _REJECTED_ENTRY.match(text)
            owner = latest.get(rejected.group("symbol")) if rejected is not None else None
            if owner is not None and not owner.fills:  # IBKR rejected the entry: it never held a share
                owner.rejected, owner.open = line, False
            continue
        if kind == "MANUAL":
            timeline.manual += 1
            continue
        if kind == "SENT":
            sent = _SENT.match(text)
            if sent is None:
                timeline.unattributed.append((line, "SENT line not in the bridge's format"))
                continue
            entry = Entry(
                line=line, symbol=sent.group("symbol"), identity=sent.group("label").strip(),
                qty=int(sent.group("qty")), entry=Decimal(sent.group("entry")), stop=Decimal(sent.group("stop")),
                target=Decimal(sent.group("target")), parent=int(sent.group("parent")),
                net_liquidation=net_liquidation,
            )
            bracket = by_parent.get(entry.parent)
            if bracket is not None and bracket.symbol == entry.symbol:
                entry.bracket = bracket
                entry.decision = decisions.get(bracket.decision_id)
            if net_liquidation is not None and -timeline.realized >= _loss_limit(net_liquidation):
                timeline.loss_violations.append(entry)
            timeline.entries.append(entry)
            latest[entry.symbol] = entry
            mine, total = _count_open(timeline, entry)
            timeline.open_samples.append((entry.identity, mine, total))
            continue
        if kind in {"FILL", "EXIT", "CLOSED", "CANCELLED"}:
            pattern = {"FILL": _FILL, "EXIT": _EXIT, "CLOSED": _CLOSED, "CANCELLED": _CANCELLED}[kind]
            found = pattern.match(text)
            owner = latest.get(found.group("symbol")) if found is not None else None
            if found is None or owner is None:
                timeline.unattributed.append((line, f"{kind} without a SENT for its symbol"))
                continue
            if kind == "FILL":
                if found.group("label").strip() != owner.identity:
                    timeline.unattributed.append((line, f"FILL names {found.group('label')!r}, its SENT {owner.identity}"))
                    continue
                owner.fills.append(line)
                if not owner.open and owner.closed is None:  # a fill that raced the cancel holds shares
                    owner.open = True
                    _count_open(timeline, owner)
            elif kind == "EXIT":
                owner.exits.append(line)
                owner.realized = Decimal(found.group("pnl"))
                if (net_liquidation is not None and timeline.loss_crossed is None
                        and -timeline.realized >= _loss_limit(net_liquidation)):
                    timeline.loss_crossed = line
            elif kind == "CLOSED":
                owner.closed, owner.open = line, False
            else:
                owner.cancelled, owner.open = line, False
            if kind != "CANCELLED":
                timeline.attributed[kind] += 1
            continue
        if kind == "SKIP":
            found = _SKIP.match(text)
            if found is not None:
                timeline.skips.append(Skip(line, found.group("symbol"), found.group("label"), found.group("reason")))
            continue
        if kind == "FLAT":
            timeline.flats.append(line)
            continue
        if kind == "CRITICAL":
            if any(phrase in text for phrase in NOT_FLAT_PHRASES):
                timeline.not_flat.append(line)
            found = _NOT_FOUND.match(text)
            owner = latest.get(found.group("symbol")) if found is not None else None
            # needs_attention holds a slot only while it may hold shares (the bridge's holds_or_may_hold).
            if owner is not None and owner.bracket is not None and owner.bracket.filled == 0:
                owner.open = False
    return timeline


def _count_open(timeline: Timeline, entry: Entry) -> tuple[int, int]:
    mine = sum(1 for item in timeline.entries if item.open and item.identity == entry.identity)
    if mine > timeline.max_open.get(entry.identity, (0, entry.line))[0]:
        timeline.max_open[entry.identity] = (mine, entry.line)
    total = sum(1 for item in timeline.entries if item.open)
    if total > timeline.max_total[0]:
        timeline.max_total = (total, entry.line)
    return mine, total


# --- the report ---------------------------------------------------------------------


@dataclass(slots=True)
class RuleResult:
    number: int
    title: str
    status: str
    summary: str
    details: list[str] = field(default_factory=list)


@dataclass(slots=True)
class SessionReport:
    session_date: date
    inputs: list[str] = field(default_factory=list)
    rules: list[RuleResult] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    mode: str | None = None  # NO_LIMIT_MODE or V7C_MODE: what the day's bridge processes ran
    metrics: DayMetrics | None = None  # rule 8's numbers (the --json "metrics" object)

    @property
    def exit_code(self) -> int:
        if self.missing:
            return EXIT_INPUTS_MISSING
        return EXIT_FAILED if any(rule.status == FAIL for rule in self.rules) else EXIT_PASSED

    @property
    def verdict(self) -> str:
        if self.missing:
            return "INPUTS MISSING"
        return FAIL if self.exit_code == EXIT_FAILED else PASS

    def owner_text(self) -> str:
        """The owner's short text: one verdict line (clean, or what tripped), then the day in numbers."""
        day = self.session_date.isoformat()
        label = NO_LIMIT_TEST_LABEL if self.mode == NO_LIMIT_MODE else "v7c"
        if self.missing:
            return mask_accounts(f"{label} {day}: no verdict, an input is missing: {self.missing[0]}") + "\n"
        aborts = next((rule for rule in self.rules if rule.number == ABORT_RULE), None)
        failed = [rule for rule in self.rules if rule.status == FAIL and rule.number != ABORT_RULE]
        if aborts is not None and aborts.status == FAIL:
            head = (f"{label} {day}: ABORT RULE TRIPPED: {aborts.summary} (plan v2: a malfunction, deploy undo"
                    " tonight)")
        elif failed:
            head = f"{label} {day}: session check FAIL: rule {failed[0].number} {failed[0].title}: {failed[0].summary}"
        else:
            warned = sum(rule.status == WARN for rule in self.rules)
            head = (f"{label} {day}: clean, no abort rule tripped (session check PASS"
                    + (f", {warned} warning{'s' if warned != 1 else ''}" if warned else "") + ")")
        lines = [head, *(self.metrics.owner_lines() if self.metrics is not None else [])]
        return mask_accounts("\n".join(lines)) + "\n"

    def to_json(self) -> dict[str, Any]:
        """Everything the text report says, for the replay engine (versioned; nothing masked yet)."""
        return {
            "schema": REPORT_SCHEMA,
            "schema_version": REPORT_SCHEMA_VERSION,
            "session_date": self.session_date.isoformat(),
            "mode": self.mode,
            "verdict": self.verdict,
            "exit_code": self.exit_code,
            "rules": [{"number": rule.number, "title": rule.title, "status": rule.status, "summary": rule.summary,
                       "details": list(rule.details)} for rule in self.rules],
            "metrics": None if self.metrics is None else self.metrics.as_json(),
            "owner_text": self.owner_text().rstrip("\n"),
            "inputs": list(self.inputs),
            "missing": list(self.missing),
        }

    def render_json(self) -> str:
        """``to_json`` as text, every account id masked."""
        return mask_accounts(json.dumps(self.to_json(), indent=2, sort_keys=True, ensure_ascii=False)) + "\n"

    def render(self) -> str:
        day = self.session_date.isoformat()
        if self.missing:
            lines = [f"Session check {day}: INPUTS MISSING (exit {EXIT_INPUTS_MISSING}), no verdict"]
            lines += [f"  - {problem}" for problem in self.missing]
            lines += [f"  {item}" for item in self.inputs]
            return mask_accounts("\n".join(lines)) + "\n"
        failed = sum(rule.status == FAIL for rule in self.rules)
        warned = sum(rule.status == WARN for rule in self.rules)
        verdict = FAIL if failed else PASS
        notes = [f"{count} {word}{'s' if count != 1 else ''}"
                 for count, word in ((failed, "failure"), (warned, "warning")) if count]
        lines = [f"Session check {day}: {verdict}" + (f" ({', '.join(notes)})" if notes else "")
                 + f", exit {self.exit_code} (read-only)"]
        lines += [f"{rule.status}  rule {rule.number} {rule.title}: {rule.summary}" for rule in self.rules]
        lines += ["", "Inputs", *(f"  {item}" for item in self.inputs), "", "Details"]
        for rule in self.rules:
            lines.append(f"  rule {rule.number} {rule.title} ({rule.status})")
            lines += [f"    {detail}" for detail in rule.details] or ["    (nothing to add)"]
        return mask_accounts("\n".join(lines)) + "\n"


def _plain(value: Decimal) -> str:
    text = format(value.normalize(), "f")
    return "0" if Decimal(text) == 0 else text


def _factor(value: Decimal) -> str:
    return f"x{_plain(value)}"


def _percent(part: Decimal, whole: Decimal) -> str:
    return f"{(part * 100 / whole).quantize(Decimal('0.01'), rounding=ROUND_HALF_EVEN)}%"


def entry_deadline(identity: str, closes_at: datetime,
                   table: Mapping[str, IdentityCaps] = BRIDGE_IDENTITY_CAPS) -> datetime:
    """The bridge's last entry moment for ``identity``: its caps row's offset from the
    real close, never later than the session's own last entry (close - 30 min)."""
    session_last = closes_at - SESSION_LAST_ENTRY_BEFORE_CLOSE
    caps = table.get(identity)
    return session_last if caps is None else min(session_last, closes_at - caps.last_entry_before_close)


@dataclass(slots=True)
class _Context:
    day: date
    closes_at: datetime
    section: BridgeSection
    supervisor: list[SupervisorEvent]
    state: dict[str, StateBracket]
    decisions: dict[str, LedgerDecision]
    fences: list[FenceRecord]
    timeline: Timeline
    connection: sqlite3.Connection
    opens_at: datetime | None = None
    logs: Path | None = None
    data: Path | None = None

    @property
    def configured(self) -> list[BridgeRun]:
        """The processes that logged their caps rows (each connect does): the ones a mode applies to."""
        return [run for run in self.section.runs if run.caps]

    @property
    def no_limit(self) -> bool:
        """The day ran the no-limit test: a process logged its mode line."""
        return any(run.no_limit for run in self.section.runs)

    @property
    def mixed_modes(self) -> bool:
        return len({run.no_limit for run in self.configured}) > 1

    @property
    def caps_table(self) -> Mapping[str, IdentityCaps]:
        return NO_LIMIT_TEST_IDENTITY_CAPS if self.no_limit else BRIDGE_IDENTITY_CAPS

    @property
    def logged_sendable(self) -> set[str] | None:
        sets = [set(run.sendable) for run in self.section.runs if run.sendable is not None]
        return set().union(*sets) if sets else None

    @property
    def net_liquidations(self) -> list[Decimal]:
        return [value for run in self.section.runs for value in run.net_liquidation]


def _rule_identities(context: _Context) -> RuleResult:
    approved = set(SENDABLE_IDENTITIES)
    logged = context.logged_sendable
    in_force = approved if logged is None else logged
    failures: list[str] = []
    details: list[str] = []
    if logged is None:
        details.append("the bridge logged no 'sendable identities' line (older than v7a): checked against"
                       f" the approved set {', '.join(sorted(approved))}")
    else:
        details.append(f"bridge sendable identities this day: {', '.join(sorted(logged)) or 'none'}")
        if logged - approved:
            failures.append(f"the bridge's sendable set names {', '.join(sorted(logged - approved))},"
                            " outside SENDABLE_IDENTITIES")
    sent_by: dict[str, int] = {}
    for entry in context.timeline.entries:
        sent_by[entry.identity] = sent_by.get(entry.identity, 0) + 1
        where = entry.line.evidence()
        if entry.identity not in approved or entry.identity not in in_force:
            failures.append(f"SENT for a shadow-only name {entry.identity!r}: {where}")
        if entry.bracket is None:
            failures.append(f"SENT without a state bracket for orders {entry.parent}-{entry.parent + 2}: {where}")
            continue
        if entry.bracket.strategy_name != entry.identity:
            failures.append(f"state bracket {entry.bracket.decision_id[:12]} names"
                            f" {entry.bracket.strategy_name!r}, its SENT {entry.identity}: {where}")
        if entry.decision is None:
            failures.append(f"decision {entry.bracket.decision_id[:12]} is not a shadow_accepted decision of"
                            f" {context.day} in the ledger: {where}")
        elif entry.decision.strategy_name != entry.identity:
            failures.append(f"ledger decision {entry.decision.decision_id[:12]} names"
                            f" {entry.decision.strategy_name!r}, its SENT {entry.identity}: {where}")
        details.append(f"SENT {entry.name} at {entry.line.stamp}: state and ledger name"
                       f" {entry.bracket.strategy_name} (decision {entry.bracket.decision_id[:12]})")
    sent_parents = {entry.parent for entry in context.timeline.entries}
    for bracket in context.state.values():
        if bracket.sent and bracket.parent not in sent_parents:
            failures.append(f"state bracket {bracket.decision_id[:12]} ({bracket.strategy_name} {bracket.symbol},"
                            f" order {bracket.parent}) has no SENT line in the section")
        if bracket.sent and bracket.strategy_name not in approved:
            failures.append(f"state bracket {bracket.decision_id[:12]} {bracket.symbol} was sent for the"
                            f" shadow-only name {bracket.strategy_name!r}")
        if (logged is not None and not bracket.sent and bracket.strategy_name in in_force
                and bracket.skip_reason == SHADOW_ONLY_REASON):
            failures.append(f"state bracket {bracket.decision_id[:12]} {bracket.symbol}: sendable"
                            f" {bracket.strategy_name} skipped as shadow-only")
    shadow_skips: dict[str, int] = {}
    for skip in context.timeline.skips:
        if skip.label is None:
            continue
        if skip.label not in in_force:
            shadow_skips[skip.label] = shadow_skips.get(skip.label, 0) + 1
        elif logged is not None and skip.reason == SHADOW_ONLY_REASON:
            failures.append(f"sendable {skip.label} skipped as shadow-only: {skip.line.evidence()}")
    if shadow_skips:
        details.append("shadow-only names, SKIP lines only: "
                       + ", ".join(f"{name} x{count}" for name, count in sorted(shadow_skips.items())))
    sent_text = ", ".join(f"{name} x{count}" for name, count in sorted(sent_by.items())) or "none"
    if failures:
        return RuleResult(1, "identities", FAIL, f"{len(failures)} problem(s); first: {failures[0]}",
                          failures + details)
    shadow = sum(shadow_skips.values())
    return RuleResult(1, "identities", PASS,
                      f"{len(context.timeline.entries)} SENT, all sendable ({sent_text}); shadow-only names only"
                      f" as SKIP ({shadow} line{'s' if shadow != 1 else ''}); sendable set"
                      f" {', '.join(sorted(in_force)) or 'none'}", details)


def _rule_attribution(context: _Context) -> RuleResult:
    timeline = context.timeline
    failures = [f"unattributed: {reason}: {line.evidence()}" for line, reason in timeline.unattributed]
    details: list[str] = []
    for entry in timeline.entries:
        if entry.bracket is None:
            continue
        if entry.bracket.filled > 0 and not entry.fills:
            failures.append(f"{entry.name}: the state holds {entry.bracket.filled} filled shares, no FILL line")
        if entry.fills and entry.bracket.filled == 0:
            failures.append(f"{entry.name}: FILL lines but the state holds no filled shares")
        if entry.exits and entry.bracket.exit_filled == 0:
            failures.append(f"{entry.name}: EXIT lines but the state holds no exit fills")
        if entry.bracket.exit_filled > 0 and not entry.exits:
            failures.append(f"{entry.name}: the state holds {entry.bracket.exit_filled} exit fills, no EXIT line")
        details.append(f"{entry.name}: {len(entry.fills)} FILL, {len(entry.exits)} EXIT,"
                       f" {'CLOSED ' + entry.closed.stamp if entry.closed else 'CANCELLED' if entry.cancelled else 'not closed'};"
                       f" state {entry.bracket.status} {entry.bracket.filled}/{entry.bracket.qty} in,"
                       f" {entry.bracket.exit_filled} out")
    failures += [f"not flat: {line.evidence()}" for line in timeline.not_flat]
    for bracket in context.state.values():
        if bracket.status in {"open", "needs_attention"} and bracket.net_held != 0:
            failures.append(f"state: {bracket.symbol} ({bracket.strategy_name}) is {bracket.status} holding"
                            f" {bracket.net_held} sh at the end of the session")
    flatten_at = context.closes_at - FLATTEN_BEFORE_CLOSE
    flats = [line for line in timeline.flats if (at := line.at(context.day)) is not None and at >= flatten_at]
    if flats:
        details.append(f"FLAT after the {flatten_at:%H:%M} ET flatten: {flats[-1].evidence()}")
    else:
        failures.append(f"no FLAT line at or after the {flatten_at:%H:%M} ET flatten")
    stops = [run.stopped for run in context.section.runs if run.stopped is not None]
    last_code = next((run.stopped_code for run in reversed(context.section.runs) if run.stopped is not None), None)
    if last_code is None:
        failures.append("no 'bridge stopped (exit N)' line in the section")
    elif last_code != 0:
        failures.append(f"the last bridge process stopped with exit {last_code}: {stops[-1].evidence()}")
    else:
        details.append(f"last stop: {stops[-1].evidence()}")
    restarts = [run.stopped_code for run in context.section.runs[:-1] if run.stopped_code not in (None, 0)]
    if restarts:
        details.append(f"{len(restarts)} earlier bridge process(es) stopped for a supervised restart")
    starts = [event for event in context.supervisor if event.event == "CHILD_START"]
    exits = [event for event in context.supervisor if event.event == "CHILD_EXIT"]
    banners = sum(1 for run in context.section.runs if run.banner_at is not None)
    warnings: list[str] = []
    if len(starts) != banners:
        warnings.append(f"supervisor.log has {len(starts)} bridge starts on {context.day}, bridge.log"
                        f" {banners} supervisor banners")
    if exits and (exits[-1].code != "0" or (exits[-1].restart or "").lower() not in {"none", ""}):
        failures.append(f"supervisor: the bridge's last exit on {context.day} was not final code 0:"
                        f" {exits[-1].ref} {exits[-1].text}")
    elif exits:
        details.append(f"supervisor: {len(starts)} bridge starts, last exit {exits[-1].ref} {exits[-1].text}")
    else:
        warnings.append(f"supervisor.log has no bridge exit on {context.day}")
    failures += [f"supervisor refused to launch the bridge: {event.ref} {event.text}"
                 for event in context.supervisor if event.event == "LAUNCH_REFUSED"]
    if timeline.manual:
        details.append(f"{timeline.manual} MANUAL line(s): owner orders, outside the identity rules")
    counts = timeline.attributed
    summary = (f"{counts['FILL']} FILL, {counts['EXIT']} EXIT and {counts['CLOSED']} CLOSED lines attributed to"
               f" their SENT; {'FLAT ' + flats[-1].stamp if flats else 'no FLAT'}; bridge stopped"
               f" (exit {last_code if last_code is not None else '?'})")
    if failures:
        return RuleResult(2, "attribution and close-out", FAIL,
                          f"{len(failures)} problem(s); first: {failures[0]}", failures + warnings + details)
    status = WARN if warnings else PASS
    return RuleResult(2, "attribution and close-out", status, summary, warnings + details)


def _caps_text(caps: IdentityCaps) -> str:
    positions = "none" if caps.max_positions is None else str(caps.max_positions)
    entries = "none" if caps.max_entries_per_day is None else str(caps.max_entries_per_day)
    minutes = int(caps.last_entry_before_close.total_seconds() // 60)
    return f"positions={positions} entries/day={entries} last_entry=close-{minutes}m"


def _rule_caps(context: _Context) -> RuleResult:
    """v7c: every cap and every last-entry time. The no-limit test: the test's rows and every
    last-entry time; the counts are measured and reported, never a failure."""
    timeline = context.timeline
    failures: list[str] = []
    details: list[str] = []
    for run in context.section.runs:
        table = NO_LIMIT_TEST_IDENTITY_CAPS if run.no_limit else BRIDGE_IDENTITY_CAPS
        which = f"no-limit test {NO_LIMIT_TEST_LABEL}" if run.no_limit else "v7c"
        for name, caps in run.caps.items():
            approved = table.get(name)
            if approved != caps:
                failures.append(f"the bridge logged caps {name} {_caps_text(caps)}, the approved {which} row is"
                                f" {_caps_text(approved) if approved else 'missing'}: process at"
                                f" {run.source}:{run.first_line}")
    if context.mixed_modes:
        failures.append("one session ran both the v7c bridge and the no-limit test (t1_mode logged by "
                        + ", ".join(f"{run.source}:{run.first_line}" for run in context.configured if run.no_limit)
                        + " only)")
    no_limit = context.no_limit
    table = context.caps_table
    attempts: dict[str, list[Entry]] = {}
    for entry in timeline.entries:
        attempts.setdefault(entry.identity, []).append(entry)
    state_attempts: dict[str, int] = {}
    for bracket in context.state.values():
        if bracket.sent:
            state_attempts[bracket.strategy_name] = state_attempts.get(bracket.strategy_name, 0) + 1
    for identity in sorted(set(attempts) | set(state_attempts) | set(table)):
        caps = table.get(identity)
        entries = attempts.get(identity, [])
        if caps is None:
            if entries or state_attempts.get(identity):
                failures.append(f"{identity}: entries sent but it has no approved caps row")
            continue
        count = max(len(entries), state_attempts.get(identity, 0))
        deadline = entry_deadline(identity, context.closes_at, table)
        peak, peak_line = timeline.max_open.get(identity, (0, None))
        if caps.max_positions is not None and peak > caps.max_positions:
            failures.append(f"{identity}: {peak} brackets open at once, cap {caps.max_positions}:"
                            f" {peak_line.evidence() if peak_line else ''}")
        if caps.max_entries_per_day is not None and count > caps.max_entries_per_day:
            failures.append(f"{identity}: {count} entries sent, cap {caps.max_entries_per_day}/day"
                            f" (attempts count): {entries[-1].line.evidence() if entries else 'state only'}")
        for entry in entries:
            sent_at = entry.sent_at(context.day)
            if sent_at is None:
                failures.append(f"{entry.name}: no send time in the state or the log")
            elif sent_at >= deadline:
                failures.append(f"{entry.name}: sent {sent_at:%H:%M:%S} ET, at or after its last entry"
                                f" {deadline:%H:%M} ET: {entry.line.evidence()}")
        last = max((at for entry in entries if (at := entry.sent_at(context.day)) is not None), default=None)
        last_text = f"last entry {f'{last:%H:%M:%S} ET' if last else 'none'} (cutoff {deadline:%H:%M} ET)"
        if no_limit:
            details.append(f"{identity}: open at most {peak}, entries {count} (no cap: measured), {last_text}")
            continue
        limit = "none" if caps.max_entries_per_day is None else str(caps.max_entries_per_day)
        details.append(f"{identity}: open at most {peak}/{caps.max_positions}, entries {count}/{limit}, {last_text}")
    if no_limit:
        details.append(f"all identities: open at most {timeline.max_total[0]} (no account-wide cap)")
    else:
        total_cap = sum(caps.max_positions or 0 for caps in BRIDGE_IDENTITY_CAPS.values())
        if timeline.max_total[0] > total_cap:
            failures.append(f"{timeline.max_total[0]} brackets open at once across identities, cap {total_cap}")
        details.append(f"all identities: open at most {timeline.max_total[0]}/{total_cap}")
    if failures:
        return RuleResult(3, "caps", FAIL, f"{len(failures)} problem(s); first: {failures[0]}", failures + details)
    if no_limit:
        sent = [line for line in details if " entries 0 " not in line and not line.startswith("all identities")]
        return RuleResult(3, "caps", PASS, f"no-limit test {NO_LIMIT_TEST_LABEL}: the test's rows, every entry"
                          " before its last-entry time; " + ("; ".join(sent) or "no entries"), details)
    sent = [line for line in details if " entries 0/" not in line and not line.startswith("all identities")]
    return RuleResult(3, "caps", PASS, "; ".join(sent) or "no entries", details)


@dataclass(frozen=True, slots=True)
class _Prices:
    """The two setup prices ``policy_quantity`` reads (entry_limit, stop_price)."""

    entry_limit: Decimal
    stop_price: Decimal


def _policy_quantity(policy: RiskPolicy, portfolio: PortfolioSnapshot, entry: Decimal, stop: Decimal) -> int:
    return policy_quantity(policy=policy, portfolio=portfolio,
                           setup=cast(RuleSetup, _Prices(entry_limit=entry, stop_price=stop)))


def _recorded_portfolio(context: _Context, decision: LedgerDecision) -> PortfolioSnapshot | None:
    """The portfolio the decision was sized from, when an account snapshot of its run rebuilds it
    to the decision's own portfolio fingerprint (the session runner's view: no owned positions,
    this run's earlier accepted symbols pending)."""
    if not decision.portfolio_fingerprint:
        return None
    snapshots = _account_snapshots(context.connection, decision.run_id)
    if not snapshots:
        return None
    earlier: list[str] = []
    for other in context.decisions.values():
        if other.run_id == decision.run_id and (other.created_at, other.decision_id) < (
                decision.created_at, decision.decision_id) and other.symbol not in earlier:
            earlier.append(other.symbol)
    pending_sets = {tuple(sorted(earlier[:count])) for count in range(len(earlier) + 1)}
    for account in snapshots:
        for pending in sorted(pending_sets):
            portfolio = portfolio_from_account(account, owned_con_ids=frozenset(), pending_symbols=pending)
            if portfolio.fingerprint == decision.portfolio_fingerprint:
                return portfolio
    return None


@dataclass(slots=True)
class _Sized:
    """One SENT's size verdict: its failures, its evidence line, and what stayed unchecked."""

    failures: list[str] = field(default_factory=list)
    detail: str = ""
    exact: bool = False
    unchecked: list[str] = field(default_factory=list)


def _size_entry(context: _Context, entry: Entry) -> _Sized:
    sized = _Sized()
    decision, bracket = entry.decision, entry.bracket
    where = entry.line.evidence()
    if bracket is not None and bracket.qty != entry.qty:
        sized.failures.append(f"{entry.name}: SENT {entry.qty} sh, the state {bracket.qty}: {where}")
    if decision is None or decision.intent is None:
        problem = "no ledger decision" if decision is None else decision.problem
        sized.failures.append(f"{entry.name}: its shares cannot be traced to a decision intent ({problem}): {where}")
        return sized
    intent = decision.intent
    if (intent.quantity, intent.entry, intent.stop, intent.target) != (entry.qty, entry.entry, entry.stop,
                                                                       entry.target):
        sized.failures.append(f"{entry.name}: SENT {entry.qty} @ {entry.entry}/{entry.stop}/{entry.target}, its"
                              f" decision's intent {intent.quantity} @ {intent.entry}/{intent.stop}/{intent.target}")
    row = IDENTITY_SIZE_MULTIPLIER.get(entry.identity)
    if row is None:
        sized.failures.append(f"{entry.name}: {entry.identity} has no identity size row")
        return sized
    parts = [f"{intent.quantity} sh = SENT = intent" + (" = state" if bracket is not None else "")]
    net_liq = entry.net_liquidation
    if net_liq is None:
        sized.unchecked.append(f"{entry.name}: the bridge logged no net liquidation in this process:"
                               " order ceilings unchecked")
    else:
        notional, risk = entry.qty * entry.entry, entry.qty * (entry.entry - entry.stop)
        if notional > net_liq * MAX_ORDER_NOTIONAL_PCT / 100:
            sized.failures.append(f"{entry.name}: notional {notional} is above {MAX_ORDER_NOTIONAL_PCT}% of net"
                                  f" liquidation {net_liq}")
        if risk > net_liq * MAX_ORDER_RISK_PCT / 100:
            sized.failures.append(f"{entry.name}: risk {risk} is above {MAX_ORDER_RISK_PCT}% of net liquidation"
                                  f" {net_liq}")
        parts.append(f"notional {_percent(notional, net_liq)}, risk {_percent(risk, net_liq)} of net liquidation"
                     f" {net_liq} (ceilings {MAX_ORDER_NOTIONAL_PCT}%, {MAX_ORDER_RISK_PCT}%)")
    # The decision's own policy, when it is an approved one (the default, or the no-limit test's).
    approved = APPROVED_POLICIES.get(decision.policy_fingerprint)
    recorded = _recorded_portfolio(context, decision) if approved is not None else None
    if approved is not None and recorded is not None:
        policy = _policy_quantity(approved, recorded, entry.entry, entry.stop)
        expected = scaled_quantity(policy, row)
        sized.exact = expected == intent.quantity
        if not sized.exact:
            sized.failures.append(f"{entry.name}: {intent.quantity} sh, but floor(policy {policy} x {_factor(row)})"
                                  f" = {expected} at the recorded net liquidation {recorded.net_liquidation}")
        parts.insert(1, f"floor(policy {policy} x {_factor(row)}) = {expected} at the recorded net liquidation"
                        f" {recorded.net_liquidation}")
        sized.detail = f"{entry.name}: " + "; ".join(parts)
        return sized
    parts.insert(1, "inputs not recorded (no account snapshot of run"
                    f" {decision.run_id} rebuilds the decision's portfolio fingerprint"
                    + ("" if approved is not None else "; its policy is not an approved policy") + ")")
    logged = context.net_liquidations
    reference = net_liq or (max(logged) if logged else None)
    if reference is None or approved is None:
        sized.unchecked.append(f"{entry.name}: identity-row bound unchecked"
                               f" ({'no net liquidation logged' if reference is None else 'policy not approved'})")
        sized.detail = f"{entry.name}: " + "; ".join(parts)
        return sized
    at = entry.sent_at(context.day) or datetime.combine(context.day, time(12), tzinfo=NY)
    high = max(logged) * (1 + NET_LIQUIDATION_BAND)
    bound = scaled_quantity(
        _policy_quantity(approved, PortfolioSnapshot(observed_at=at, net_liquidation=high), entry.entry, entry.stop),
        row,
    )
    policy = _policy_quantity(approved, PortfolioSnapshot(observed_at=at, net_liquidation=reference), entry.entry,
                              entry.stop)
    implied = f"x{(Decimal(intent.quantity) / policy).quantize(Decimal('0.01'))}" if policy > 0 else "x?"
    band = f"+{_plain(NET_LIQUIDATION_BAND * 100)}%"
    if intent.quantity > bound:
        sized.failures.append(f"{entry.name}: {intent.quantity} sh is above its {_factor(row)} row: at most {bound}"
                              f" sh even at net liquidation {high.quantize(Decimal('0.01'))} (logged {band}); about"
                              f" {implied} of the policy size {policy} at {reference}")
    parts.insert(2, f"about {implied} of the policy size {policy} at the logged net liquidation {reference};"
                    f" row {_factor(row)} allows at most {bound} at {band}")
    sized.detail = f"{entry.name}: " + "; ".join(parts)
    return sized


def _rule_size(context: _Context) -> RuleResult:
    failures: list[str] = []
    unchecked: list[str] = []
    details = [f"the bridge's ceiling refused a decision: {skip.line.evidence()}"
               for skip in context.timeline.skips if "exceeds" in skip.reason and "net liquidation" in skip.reason]
    exact = 0
    for entry in context.timeline.entries:
        sized = _size_entry(context, entry)
        failures += sized.failures
        unchecked += sized.unchecked
        exact += sized.exact
        if sized.detail:
            details.append(sized.detail)
    entries = len(context.timeline.entries)
    if failures:
        return RuleResult(4, "size", FAIL, f"{len(failures)} problem(s); first: {failures[0]}",
                          failures + unchecked + details)
    if not entries:
        return RuleResult(4, "size", PASS, "no SENT: nothing was sized", details)
    if exact < entries:
        rest = f"; {len(unchecked)} check(s) could not run, see details" if unchecked else ""
        return RuleResult(4, "size", WARN,
                          f"size check: inputs not recorded for {entries - exact} of {entries} SENT; every SENT's"
                          " shares equal its decision's intent and stay inside the bridge's ceilings and below its"
                          f" identity row at the logged net liquidation{rest}", unchecked + details)
    if unchecked:
        return RuleResult(4, "size", WARN, f"{entries} SENT sized exactly; {len(unchecked)} check(s) could not run",
                          unchecked + details)
    return RuleResult(4, "size", PASS,
                      f"{entries} SENT = floor(policy quantity x identity row) from recorded inputs", details)


def _rule_daily_loss(context: _Context) -> RuleResult:
    timeline = context.timeline
    realized_state = sum((bracket.realized_pnl for bracket in context.state.values()), Decimal("0"))
    session_end = next((run.session_end_pnl for run in reversed(context.section.runs)
                        if run.session_end_pnl is not None), None)
    details = [f"realized P&L: state {realized_state}, EXIT lines {timeline.realized}, session end"
               f" {session_end if session_end is not None else 'not logged'}"]
    failures: list[str] = []
    if session_end is not None and session_end != realized_state:
        details.append("the session-end line and the state differ: the state is used")
    failures += [f"{entry.name} sent after the realized loss had reached {MAX_DAILY_LOSS_PCT}% of net"
                 f" liquidation: {entry.line.evidence()}" for entry in timeline.loss_violations]
    logged = context.net_liquidations
    blocked = [skip for skip in timeline.skips if "daily loss limit" in skip.reason]
    if blocked:
        details.append(f"{len(blocked)} SKIP line(s) for the daily loss limit, first {blocked[0].line.evidence()}")
    if not logged:
        if context.no_limit:
            return _daily_loss_measured(context, f"realized P&L {realized_state}; net liquidation never logged",
                                        details)
        if failures:
            return RuleResult(5, "daily loss", FAIL, failures[0], failures + details)
        status = PASS if not timeline.entries else WARN
        return RuleResult(5, "daily loss", status, f"realized P&L {realized_state}; net liquidation never logged"
                          + ("" if not timeline.entries else ": the -2% stop cannot be checked"), details)
    reference = logged[-1]
    limit = _loss_limit(reference)
    summary = (f"realized P&L {realized_state} = {_percent(realized_state, reference)} of net liquidation"
               f" {reference} (stop at -{MAX_DAILY_LOSS_PCT}% = -{limit.quantize(Decimal('0.01'))})")
    if context.no_limit:
        return _daily_loss_measured(context, summary, details)
    if failures:
        return RuleResult(5, "daily loss", FAIL, f"{len(failures)} problem(s); first: {failures[0]}", failures + details)
    if -realized_state >= limit:
        crossed = timeline.loss_crossed.evidence() if timeline.loss_crossed else "after the last EXIT"
        details.append(f"the loss reached the stop at {crossed}; no entry was sent after it")
        return RuleResult(5, "daily loss", PASS, summary + "; crossed, and no entry after it", details)
    return RuleResult(5, "daily loss", PASS, summary, details)


def _daily_loss_measured(context: _Context, summary: str, details: list[str]) -> RuleResult:
    """The no-limit test: the -2 % stop is measured and logged for bot entries, never a refusal (INFO).
    An owner buy past it is rule 7's failure (a v7c_budget_ok line at or past -2 %, decision D8)."""
    after = context.timeline.loss_violations
    details = [*details, *(f"{entry.name} sent after the bot's realized loss reached {MAX_DAILY_LOSS_PCT}% (measured):"
                           f" {entry.line.evidence()}" for entry in after)]
    measured = 0
    for line in context.section.lines():
        if line.kind == "INFO" and _OVER_START.match(line.text):
            found = _OVER.match(line.text)
            if found is not None and DAILY_LOSS_LIMIT in _over_tokens(found.group("tokens")):
                measured += 1
                details.append(f"measured past the account's -{MAX_DAILY_LOSS_PCT}%: {line.evidence()}")
        elif line.kind == "CRITICAL" and _LOSS_STEP.match(line.text):
            details.append(f"loss-step page: {line.evidence()}")
    steps = _loss_steps(context.section)
    return RuleResult(5, "daily loss", INFO,
                      f"no-limit test {NO_LIMIT_TEST_LABEL}, measured, never blocking: {summary}; {len(after)} bot"
                      f" entr{'y' if len(after) == 1 else 'ies'} sent after the bot's realized loss reached"
                      f" {MAX_DAILY_LOSS_PCT}%, {measured} measured past the account's -{MAX_DAILY_LOSS_PCT}%"
                      f" (t1_over_v7c_limits {DAILY_LOSS_LIMIT}); loss-step pages:"
                      f" {', '.join(f'{step}%' for step in steps) or 'none'}", details)


def _over_tokens(text: str) -> list[str]:
    return [token.strip() for token in text.split(",") if token.strip()]


def _loss_steps(section: BridgeSection) -> list[int]:
    """The whole percents the realized loss paged at (t1_realized_loss_step), each once."""
    steps: list[int] = []
    for line in section.lines():
        found = _LOSS_STEP.match(line.text) if line.kind == "CRITICAL" else None
        if found is not None and int(found.group("step")) not in steps:
            steps.append(int(found.group("step")))
    return steps


def _rule_fences(context: _Context) -> RuleResult:
    failures: list[str] = []
    warnings: list[str] = []
    details: list[str] = []
    session_end = context.closes_at
    session_start = datetime.combine(context.day, time(0), tzinfo=NY)
    for fence in context.fences:
        text = (f"{fence.label}: {'STOPPED' if fence.stopped else 'running'} since {fence.updated_text}"
                f" ({fence.reason})")
        details.append(f"fence row {text}")
        relevant = fence.name == GLOBAL_FENCE or fence.identity is not None
        if not relevant:
            continue
        updated = fence.updated_at
        if fence.stopped and (updated is None or updated <= session_end):
            warnings.append(f"fenced during the day: {text}")
            for entry in context.timeline.entries:
                if fence.identity not in (None, entry.identity):
                    continue
                sent_at = entry.sent_at(context.day)
                if sent_at is not None and (updated is None or sent_at >= updated):
                    failures.append(f"{entry.name} sent at {sent_at:%H:%M:%S} ET while {fence.label} was stopped"
                                    f" (since {fence.updated_text})")
        elif not fence.stopped and updated is not None and session_start <= updated <= session_end:
            warnings.append(f"resumed during the day (it was fenced before {updated.astimezone(NY):%H:%M} ET): {text}")
    for run in context.section.runs:
        for line in run.fenced:
            found = _FENCED.match(line.text)
            names = found.group("names").strip() if found else "?"
            if names != "none":
                warnings.append(f"the bridge started with fenced identities: {line.evidence()}")
    for skip in context.timeline.skips:
        if skip.reason.startswith("identity_stopped:") or "kill switch active" in skip.reason:
            warnings.append(f"an entry was refused by a fence: {skip.line.evidence()}")
    connects = sum(len(run.fenced) for run in context.section.runs)
    if failures:
        return RuleResult(6, "fences", FAIL, f"{len(failures)} problem(s); first: {failures[0]}",
                          failures + warnings + details)
    if warnings:
        return RuleResult(6, "fences", WARN, f"{len(warnings)} flag(s); first: {warnings[0]}", warnings + details)
    rows = f"{len(context.fences)} fence row(s), none stopped during the day" if context.fences else "no fence rows"
    return RuleResult(6, "fences", PASS, f"{rows}; {connects} connect(s) logged fenced identities = none", details)


@dataclass(frozen=True, slots=True)
class BudgetCheck:
    """One ``v7c_budget_ok`` line: the order it let through and the numbers it was let through at."""

    line: LogLine
    key: tuple[str, ...]  # ("bot", symbol, identity) or ("manual", short id)
    total: Decimal  # the larger of the logged total and its logged parts' sum (fail closed)
    net_liquidation: Decimal
    problems: tuple[str, ...] = ()

    @property
    def percent(self) -> Decimal:
        return self.total * 100 / self.net_liquidation


def parse_budget_ok(line: LogLine) -> BudgetCheck | str:
    """A budget line's numbers and what is wrong with them; a malformed line is its failure text."""
    found = _BUDGET_OK.match(line.text)
    if found is None:
        return f"malformed {V7C_BUDGET_OK} line: {line.evidence()}"
    label, source = found.group("label").strip(), found.group("source")
    manual = _MANUAL_LABEL.match(label)
    if (manual is not None) != (source == "manual"):
        return f"malformed {V7C_BUDGET_OK} line (label {label!r}, source={source}): {line.evidence()}"
    realized, open_risk, new, logged, net_liq, cap = (
        Decimal(found.group(name)) for name in ("realized", "open", "new", "total", "net_liq", "cap")
    )
    if net_liq <= 0:
        return f"malformed {V7C_BUDGET_OK} line (net liquidation {net_liq}): {line.evidence()}"
    parts = realized + open_risk + new
    if abs(parts - logged) > BUDGET_SUM_TOLERANCE:
        return (f"malformed {V7C_BUDGET_OK} line (realized + open + new = {parts}, logged total {logged}):"
                f" {line.evidence()}")
    key = ("manual", manual.group("short")) if manual is not None else ("bot", found.group("symbol"), label)
    total = max(parts, logged)
    problems: list[str] = []
    if cap != DAY_RISK_BUDGET_PCT:
        problems.append(f"the bridge let an order through at a {_plain(cap)}% cap, the approved day risk budget"
                        f" is {_plain(DAY_RISK_BUDGET_PCT)}%: {line.evidence()}")
    limit = net_liq * DAY_RISK_BUDGET_PCT / 100
    if total > limit + BUDGET_ROUNDING:
        problems.append(f"accepted at a day risk of {total} = {_percent(total, net_liq)} of net liquidation"
                        f" {net_liq}, above {_plain(DAY_RISK_BUDGET_PCT)}% ({limit}): {line.evidence()}")
    # The bridge logs v7c_budget_ok only when nothing would refuse, the -2 % stop included (v7c for
    # every order; in the no-limit test for owner buys, decision D8): past it, the line is a breach.
    if realized >= _loss_limit(net_liq) + HALF_CENT:
        problems.append(f"accepted with the account's realized loss {realized} = {_percent(realized, net_liq)} of net"
                        f" liquidation {net_liq}, at or past the -{MAX_DAILY_LOSS_PCT}% stop: {line.evidence()}")
    return BudgetCheck(line=line, key=key, total=total, net_liquidation=net_liq, problems=tuple(problems))


@dataclass(frozen=True, slots=True)
class OverCheck:
    """One ``t1_over_v7c_limits`` line: a bot entry the no-limit test sent over v7c's limits, measured."""

    line: LogLine
    key: tuple[str, ...]  # ("bot", symbol, identity): an owner buy is never measured-only (D8)
    total: Decimal
    net_liquidation: Decimal
    tokens: tuple[str, ...]  # what v7c would have refused it with
    problems: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()  # numbers and tokens that disagree (WARN)

    @property
    def percent(self) -> Decimal:
        return self.total * 100 / self.net_liquidation


def parse_over_limits(line: LogLine) -> OverCheck | str:
    """A measured-only line's numbers and tokens; a malformed line is its failure text."""
    found = _OVER.match(line.text)
    if found is None:
        return f"malformed {T1_OVER_V7C_LIMITS} line: {line.evidence()}"
    label, source = found.group("label").strip(), found.group("source")
    if source != "bot" or _MANUAL_LABEL.match(label) is not None:
        return (f"malformed {T1_OVER_V7C_LIMITS} line (label {label!r}, source={source}: an owner buy is never"
                f" measured-only, decision D8): {line.evidence()}")
    tokens = tuple(_over_tokens(found.group("tokens")))
    if not tokens or any(token not in T1_OVER_TOKENS for token in tokens):
        return f"malformed {T1_OVER_V7C_LIMITS} line (would refuse: {found.group('tokens')!r}): {line.evidence()}"
    realized, open_risk, new, logged, net_liq, cap = (
        Decimal(found.group(name)) for name in ("realized", "open", "new", "total", "net_liq", "cap")
    )
    if net_liq <= 0:
        return f"malformed {T1_OVER_V7C_LIMITS} line (net liquidation {net_liq}): {line.evidence()}"
    parts = realized + open_risk + new
    if abs(parts - logged) > BUDGET_SUM_TOLERANCE:
        return (f"malformed {T1_OVER_V7C_LIMITS} line (realized + open + new = {parts}, logged total {logged}):"
                f" {line.evidence()}")
    total = max(parts, logged)
    problems: list[str] = []
    if cap != DAY_RISK_BUDGET_PCT:
        problems.append(f"the bridge measured against a {_plain(cap)}% cap, the approved day risk budget is"
                        f" {_plain(DAY_RISK_BUDGET_PCT)}%: {line.evidence()}")
    notes: list[str] = []
    limit, loss_limit = net_liq * DAY_RISK_BUDGET_PCT / 100, _loss_limit(net_liq)
    if V7C_DAY_RISK_BUDGET not in tokens and total > limit + BUDGET_ROUNDING:
        notes.append(f"{T1_OVER_V7C_LIMITS} total {total} is above {_plain(DAY_RISK_BUDGET_PCT)}% but the line does"
                     f" not name {V7C_DAY_RISK_BUDGET}: {line.evidence()}")
    if V7C_DAY_RISK_BUDGET in tokens and total + BUDGET_ROUNDING < limit:
        notes.append(f"{T1_OVER_V7C_LIMITS} names {V7C_DAY_RISK_BUDGET} at a total {total} within"
                     f" {_plain(DAY_RISK_BUDGET_PCT)}%: {line.evidence()}")
    if DAILY_LOSS_LIMIT not in tokens and realized >= loss_limit + HALF_CENT:
        notes.append(f"{T1_OVER_V7C_LIMITS} realized {realized} is past -{MAX_DAILY_LOSS_PCT}% but the line does not"
                     f" name {DAILY_LOSS_LIMIT}: {line.evidence()}")
    if DAILY_LOSS_LIMIT in tokens and realized + HALF_CENT < loss_limit:
        notes.append(f"{T1_OVER_V7C_LIMITS} names {DAILY_LOSS_LIMIT} at a realized loss {realized} short of"
                     f" -{MAX_DAILY_LOSS_PCT}%: {line.evidence()}")
    return OverCheck(line=line, key=("bot", found.group("symbol"), label), total=total, net_liquidation=net_liq,
                     tokens=tokens, problems=tuple(problems), notes=tuple(notes))


def _rule_day_risk_budget(context: _Context) -> RuleResult:
    failures: list[str] = []
    warnings: list[str] = []
    details: list[str] = []
    approved = f"{_plain(DAY_RISK_BUDGET_PCT)}% (stop slippage factor {STOP_SLIPPAGE_FACTOR})"
    budget_runs: set[int] = set()
    for run in context.section.runs:
        for line in run.lines:
            if line.kind != "INFO" or not line.text.startswith(_BUDGET_RULE_START):
                continue
            found = _BUDGET_RULE.match(line.text)
            if found is None:
                failures.append(f"malformed day risk budget line: {line.evidence()}")
                continue
            budget_runs.add(run.index)
            if (Decimal(found.group("pct")), Decimal(found.group("factor"))) != (DAY_RISK_BUDGET_PCT,
                                                                                STOP_SLIPPAGE_FACTOR):
                failures.append(f"the bridge ran a day risk budget of {found.group('pct')}% (stop slippage factor"
                                f" {found.group('factor')}), the approved is {approved}: {line.evidence()}")
    # N18g-LOW-1: a budget line vouches only for an order of its own bridge process. The bridge
    # checks the budget immediately before it stores and places an order, on its loop, so an order
    # sent by a later process (after a restart) is preceded by that process's own line; a line
    # left by a process that stopped before its SENT never carries over. A process starts at a
    # supervisor banner or a ``bridge sha256=`` line (a new run of the section) and connects once
    # (``PAPER bridge … connected``); either one starts a new process here.
    # The no-limit test (L-T1): a bot entry's measurement is its v7c_budget_ok line, or its
    # t1_over_v7c_limits line when v7c would have refused it; either is its own process's, logged
    # right before the order is stored and placed. An owner buy keeps v7c's line only (D8).
    no_limit_runs = {run.index for run in context.section.runs if run.no_limit}
    pending: dict[tuple[tuple[int, int], tuple[str, ...]], list[BudgetCheck | OverCheck]] = {}
    connects: Counter[int] = Counter()
    process = (-1, 0)
    verified: list[tuple[str, BudgetCheck | OverCheck]] = []
    unverified: list[str] = []
    missing: list[str] = []
    refused: Counter[str] = Counter()

    def accepted(key: tuple[str, ...], name: str, line: LogLine) -> None:
        checks = pending.get((process, key))
        if checks:
            verified.append((name, checks.pop(0)))
        elif line.run in no_limit_runs and key[0] == "bot":
            missing.append(f"{name} sent without its measurement line ({V7C_BUDGET_OK} or {T1_OVER_V7C_LIMITS}),"
                           f" by a process that measures the day risk budget: {line.evidence()}")
            failures.append(missing[-1])
        elif line.run in budget_runs:
            missing.append(f"{name} sent without its {V7C_BUDGET_OK} line, by a process that enforces the day"
                           f" risk budget: {line.evidence()}")
            failures.append(missing[-1])
        else:
            unverified.append(f"{name}: no {V7C_BUDGET_OK} line (its process logged no day risk budget):"
                              f" {line.evidence()}")

    for line in context.section.lines():
        kind, text = line.kind, line.text
        if kind == "INFO" and _CONNECTED.match(text):
            connects[line.run] += 1
        process = (line.run, connects[line.run])
        if kind == "INFO" and _BUDGET_OK_START.match(text):
            check = parse_budget_ok(line)
            if isinstance(check, str):
                failures.append(check)
                continue
            failures += check.problems
            pending.setdefault((process, check.key), []).append(check)
        elif kind == "INFO" and _OVER_START.match(text):
            over = parse_over_limits(line)
            if isinstance(over, str):
                failures.append(over)
                continue
            if line.run not in no_limit_runs:
                failures.append(f"{T1_OVER_V7C_LIMITS} from a process that did not run the no-limit test (no"
                                f" {T1_MODE} line): it sent over v7c's limits: {line.evidence()}")
                continue
            failures += over.problems
            warnings += over.notes
            pending.setdefault((process, over.key), []).append(over)
        elif kind == "SENT":
            sent = _SENT.match(text)
            if sent is not None:  # an unreadable SENT is rule 2's failure
                identity, symbol = sent.group("label").strip(), sent.group("symbol")
                orders = f"{sent.group('parent')}-{sent.group('last')}"
                accepted(("bot", symbol, identity), f"{identity} {symbol} (orders {orders})", line)
        elif kind == "MANUAL":
            buy = _MANUAL_BUY.match(text)
            if buy is not None:
                short = buy.group("short")
                accepted(("manual", short), f"owner buy #{short} (order {buy.group('order')})", line)
                continue
            rejected = _MANUAL_REJECTED.match(text)
            if rejected is not None and block_token(rejected.group("code")) is not None:
                refused[f"manual {rejected.group('code')}"] += 1
        elif kind == "SKIP":
            skip = _SKIP.match(text)
            token = block_token(skip.group("reason")) if skip is not None else None
            if token is not None:
                refused[f"bot {token}"] += 1
        elif kind == "CRITICAL":
            page = _V7C_PAGE.match(text)
            token = page.group("token") if page is not None else ""
            if token == V7C_BUDGET_INVARIANT_BROKEN:
                failures.append(f"the bridge's budget tripwire fired: {line.evidence()}")
            elif token in {V7C_BUDGET_OVER, V7C_STOP_WIDENED, V7C_WRONG_THREAD}:
                warnings.append(f"{token} page: {line.evidence()}")
    for checks in pending.values():
        details += [f"budget checked, no order followed (stopped or disconnected): {check.line.evidence()}"
                    for check in checks]
    entries = len(verified) + len(unverified) + len(missing)
    by_source = Counter(check.key[0] for _, check in verified)
    refusals = ", ".join(f"{name} x{count}" for name, count in sorted(refused.items())) or "none"
    overs = [check for _, check in verified if isinstance(check, OverCheck)]
    over_tokens = Counter(token for check in overs for token in check.tokens)
    over_text = ", ".join(f"{token} x{count}" for token, count in sorted(over_tokens.items())) or "none"
    details.insert(0, f"refused risk-increasing orders: {refusals}")
    if context.no_limit:
        details.insert(1, f"bot entries sent over v7c's limits (measured, no-limit test {NO_LIMIT_TEST_LABEL}):"
                          f" {len(overs)}, would have refused: {over_text}")
    details += [f"{name}: {_percent(check.total, check.net_liquidation)} of net liquidation"
                f" {check.net_liquidation}"
                + (f", over v7c's limits ({', '.join(check.tokens)})" if isinstance(check, OverCheck) else "")
                + f": {check.line.evidence()}" for name, check in verified]
    if failures:
        return RuleResult(7, "day risk budget", FAIL, f"{len(failures)} problem(s); first: {failures[0]}",
                          failures + unverified + warnings + details)
    if unverified:
        return RuleResult(7, "day risk budget", WARN,
                          f"{DAY_RISK_BUDGET_UNVERIFIED}: {len(unverified)} of {entries} accepted"
                          f" entr{'y' if entries == 1 else 'ies'} without a {V7C_BUDGET_OK} line (a bridge older"
                          " than v7c logged no day risk budget); never a PASS", unverified + warnings + details)
    if not verified:
        summary = f"no risk-increasing order sent; refused: {refusals}"
    elif context.no_limit:
        name, top = max(verified, key=lambda item: item[1].percent)
        summary = (f"{len(verified)} accepted entr{'y' if len(verified) == 1 else 'ies'} ({by_source['bot']} bot,"
                   f" {by_source['manual']} manual), each measured by its own process:"
                   f" {len(verified) - len(overs)} within v7c's limits, {len(overs)} sent over them (measured,"
                   f" no-limit test {NO_LIMIT_TEST_LABEL}: {over_text}), highest"
                   f" {_percent(top.total, top.net_liquidation)} ({name}); refused: {refusals}")
    else:
        name, top = max(verified, key=lambda item: item[1].percent)
        summary = (f"{len(verified)} accepted entr{'y' if len(verified) == 1 else 'ies'} ({by_source['bot']} bot,"
                   f" {by_source['manual']} manual) within {_plain(DAY_RISK_BUDGET_PCT)}% of net liquidation,"
                   f" highest {_percent(top.total, top.net_liquidation)} ({name}); refused: {refusals}")
    if warnings:
        return RuleResult(7, "day risk budget", WARN, f"{len(warnings)} flag(s); first: {warnings[0]}",
                          [*warnings, summary, *details])
    return RuleResult(7, "day risk budget", PASS, summary, details)


# --- rule 9: the abort rules (owner plan v2) ------------------------------------------------


@dataclass(slots=True)
class RepairEpisode:
    """One robot position without a working stop, from the bridge's t1_stop_repair lines: from its
    detection to its stop working again (``repaired``) or the position flat (its CLOSED line)."""

    symbol: str
    label: str
    start: datetime  # detection: the first line's time, or its deadline less STOP_REPAIR_DEADLINE
    first: LogLine
    end: datetime | None = None
    last: LogLine | None = None
    outcome: str = "open"  # repaired | closed | open (never resolved in the log)
    reported: int | None = None  # the bridge's own "<n>s after detection"
    closing: bool = False  # the bridge closed it at market

    @property
    def seconds(self) -> int | None:
        """Whole seconds without a working stop (bridge.log clocks are whole seconds); None: unresolved."""
        if self.end is None:
            return None
        return max(int((self.end - self.start).total_seconds()), self.reported or 0)


def repair_episodes(section: BridgeSection, day: date) -> list[RepairEpisode]:
    episodes: list[RepairEpisode] = []
    unresolved: dict[str, RepairEpisode] = {}
    for line in section.lines():
        at = line.at(day)
        if at is None:
            continue
        if line.kind in {"INFO", "CRITICAL"} and (found := _REPAIR.match(line.text)) is not None:
            symbol, rest = found.group("symbol"), found.group("rest")
            episode = unresolved.get(symbol)
            if episode is None:
                episode = RepairEpisode(symbol=symbol, label=found.group("label").strip(), start=at, first=line)
                unresolved[symbol] = episode
                episodes.append(episode)
            deadline = _REPAIR_DEADLINE.search(rest)
            if deadline is not None:
                detected = datetime.combine(day, time.fromisoformat(deadline.group("clock")), tzinfo=NY)
                episode.start = min(episode.start, detected - STOP_REPAIR_DEADLINE)
            repaired = _REPAIRED.match(rest)
            if repaired is not None:
                episode.end, episode.last, episode.outcome = at, line, "repaired"
                episode.reported = int(repaired.group("seconds")) if repaired.group("seconds") else None
                del unresolved[symbol]
            elif _REPAIR_CLOSING in rest:
                episode.closing = True
        elif line.kind == "CLOSED" and (found := _CLOSED.match(line.text)) is not None:
            episode = unresolved.pop(found.group("symbol"), None)
            if episode is not None:  # flat: nothing left to protect
                episode.end, episode.last, episode.outcome = at, line, "closed"
    return episodes


@dataclass(frozen=True, slots=True)
class GuardTrip:
    token: str
    identity: str
    line: LogLine


def guard_trips(section: BridgeSection) -> list[GuardTrip]:
    """Each malfunction guard's trips: a refused entry (its SKIP line) or, for the broker-rejection
    guard, the identity's day halt (its CRITICAL page; a restart's INFO recount is the same halt)."""
    trips: list[GuardTrip] = []
    for line in section.lines():
        if line.kind == "SKIP" and (found := _SKIP.match(line.text)) is not None:
            token = found.group("reason").split(":", 1)[0].strip()
            if token in T1_GUARD_TOKENS and token != T1_GUARD_REJECTIONS:
                trips.append(GuardTrip(token, (found.group("label") or "?").strip(), line))
        elif line.kind == "CRITICAL" and (found := _HALT.match(line.text)) is not None:
            trips.append(GuardTrip(T1_GUARD_REJECTIONS, found.group("identity"), line))
    return trips


def _held_shares(entry: Entry) -> bool:
    return bool(entry.fills) or (entry.bracket is not None and entry.bracket.filled > 0)


def _rule_abort(context: _Context, episodes: Sequence[RepairEpisode], trips: Sequence[GuardTrip]) -> RuleResult:
    """Plan v2's malfunction abort rules, each a FAIL: deploy undo that night."""
    failures: list[str] = []
    details: list[str] = []
    day, entries = context.day, context.timeline.entries
    limit = int(STOP_REPAIR_DEADLINE.total_seconds())
    longest = 0
    for episode in episodes:
        seconds = episode.seconds
        where = f"{episode.symbol} ({episode.label})"
        if seconds is None:
            failures.append(f"robot position {where} without a working stop since {episode.start:%H:%M:%S} ET, never"
                            f" repaired or flat in the log: {episode.first.evidence()}")
            continue
        longest = max(longest, seconds)
        text = (f"robot position {where} without a working stop for {seconds}s ({episode.start:%H:%M:%S}-"
                f"{episode.end:%H:%M:%S} ET, {episode.outcome}{', closed at market' if episode.closing else ''})")
        if seconds > limit:
            failures.append(f"{text}, more than {limit}s: {episode.last.evidence() if episode.last else ''}")
        else:
            details.append(f"{text}: {episode.last.evidence() if episode.last else ''}")
    opens_at = context.opens_at or datetime.combine(day, time(9, 30), tzinfo=NY)
    closes_at = context.closes_at
    outside = 0
    for entry in entries:
        sent_at = entry.sent_at(day)
        if sent_at is not None and (sent_at < opens_at or sent_at >= closes_at):
            outside += 1
            failures.append(f"{entry.name} sent {sent_at:%H:%M:%S} ET, outside the XNYS session"
                            f" {opens_at:%H:%M}-{closes_at:%H:%M} ET: {entry.line.evidence()}")
    flat_by = closes_at - FLAT_BY_BEFORE_CLOSE
    last_flat: datetime | None = None
    for entry in entries:
        if not _held_shares(entry):
            continue
        flat_at = entry.closed.at(day) if entry.closed is not None else None
        if flat_at is None:
            failures.append(f"{entry.name} still holds shares at the end of the log (no CLOSED line), past"
                            f" {flat_by:%H:%M} ET: {entry.line.evidence()}")
            continue
        last_flat = flat_at if last_flat is None else max(last_flat, flat_at)
        if flat_at > flat_by:
            failures.append(f"{entry.name} open until {flat_at:%H:%M:%S} ET, past {flat_by:%H:%M} ET (the close less"
                            f" {int(FLAT_BY_BEFORE_CLOSE.total_seconds() // 60)} min):"
                            f" {entry.closed.evidence() if entry.closed else ''}")
    duplicates = 0
    for index, first in enumerate(entries):
        first_at = first.sent_at(day)
        for second in entries[index + 1:]:
            second_at = second.sent_at(day)
            if (first_at is None or second_at is None or (first.symbol, first.qty) != (second.symbol, second.qty)
                    or abs(second_at - first_at) >= GUARD_DUPLICATE_WINDOW):
                continue
            duplicates += 1
            failures.append(f"duplicate entry: {second.name} repeats {first.name}"
                            f" {abs(second_at - first_at).total_seconds():.1f}s later (BUY {second.qty}"
                            f" {second.symbol}, window {int(GUARD_DUPLICATE_WINDOW.total_seconds())}s):"
                            f" {second.line.evidence()}")
    counts = Counter(trip.token for trip in trips)
    for token, count in sorted(counts.items()):
        lines = [trip for trip in trips if trip.token == token]
        who = ", ".join(sorted({trip.identity for trip in lines}))
        text = f"guard {token} tripped {count} time{'s' if count != 1 else ''} today ({who})"
        if count >= GUARD_TRIPS_ABORT:
            failures.append(f"{text}: the same guard twice in one day: {lines[1].line.evidence()}")
        else:
            details.append(f"{text}: {lines[0].line.evidence()}")
    details.append("replay reproduction (orders replayed from the signal log): the replay engine's check, not this"
                   " checker's")
    if failures:
        return RuleResult(ABORT_RULE, "abort rules", FAIL, f"{len(failures)} problem(s); first: {failures[0]}",
                          failures + details)
    repaired = f"{len(episodes)} stop repair(s), longest {longest}s" if episodes else "no stop repair"
    flat = f"last position flat {last_flat:%H:%M:%S} ET" if last_flat is not None else "no position held"
    guards = ", ".join(f"{token} x{count}" for token, count in sorted(counts.items())) or "none"
    return RuleResult(ABORT_RULE, "abort rules", PASS,
                      f"none tripped: {repaired} (limit {limit}s), {outside} entries outside"
                      f" {opens_at:%H:%M}-{closes_at:%H:%M} ET, {flat} (limit {flat_by:%H:%M} ET),"
                      f" {duplicates} duplicates, guards tripped: {guards}", details)


# --- rule 8: the no-limit test's nightly numbers (INFO) ---------------------------------------


def _ratio(part: Decimal, whole: Decimal) -> Decimal:
    return (part * 100 / whole).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)


def _quantiles(values: Sequence[Decimal]) -> dict[str, str | int | None]:
    """p50, p95 (nearest rank: an observed value) and max of ``values`` as text, and how many."""
    ordered = sorted(values)
    p50, p95 = nearest_rank(ordered, Decimal("0.5")), nearest_rank(ordered, Decimal("0.95"))
    return {"p50": None if p50 is None else _plain(p50), "p95": None if p95 is None else _plain(p95),
            "max": None if not ordered else _plain(ordered[-1]), "n": len(ordered)}


def _count_quantiles(values: Sequence[int]) -> dict[str, int | None]:
    """``_quantiles`` of whole counts, as numbers."""
    ordered = sorted(Decimal(value) for value in values)
    p50, p95 = nearest_rank(ordered, Decimal("0.5")), nearest_rank(ordered, Decimal("0.95"))
    return {"p50": None if p50 is None else int(p50), "p95": None if p95 is None else int(p95),
            "max": None if not ordered else int(ordered[-1]), "n": len(ordered)}


@dataclass(slots=True)
class TradeRow:
    """One entry that held shares: what it bought, how it ended, and its R."""

    identity: str
    symbol: str
    orders: str
    sent: str
    closed: str | None
    qty: int
    filled: int
    entry: Decimal
    stop: Decimal
    avg_entry: Decimal | None
    avg_exit: Decimal | None
    exit_reason: str
    pnl: Decimal
    pct_day_volume: Decimal | None

    @property
    def r(self) -> Decimal | None:
        """Realized P&L over the planned risk of the shares bought: filled x (entry - stop)."""
        risk = Decimal(self.filled) * (self.entry - self.stop)
        return None if risk <= 0 else (self.pnl / risk).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)

    def as_json(self) -> dict[str, Any]:
        return {
            "identity": self.identity, "symbol": self.symbol, "orders": self.orders, "sent": self.sent,
            "closed": self.closed, "qty": self.qty, "filled": self.filled, "entry": str(self.entry),
            "stop": str(self.stop), "avg_entry": None if self.avg_entry is None else str(self.avg_entry),
            "avg_exit": None if self.avg_exit is None else str(self.avg_exit), "exit_reason": self.exit_reason,
            "pnl": str(self.pnl), "r": None if self.r is None else str(self.r),
            "pct_day_volume": None if self.pct_day_volume is None else str(self.pct_day_volume),
        }

    def text(self) -> str:
        volume = "" if self.pct_day_volume is None else f"; {self.pct_day_volume}% of the day's volume"
        r = "" if self.r is None else f" = {self.r}R"
        return (f"trade {self.identity} {self.symbol} (orders {self.orders}) sent {self.sent} ET:"
                f" {self.filled}/{self.qty} sh @ {self.avg_entry if self.avg_entry is not None else self.entry},"
                f" stop {self.stop}, out {self.avg_exit if self.avg_exit is not None else '-'}"
                f" ({self.exit_reason or 'open'}) at {self.closed or 'never'} ET: P&L {self.pnl}{r}{volume}")


@dataclass(slots=True)
class StrategyMetrics:
    """One sendable identity's day (or the total): the owner's plan v2 nightly report fields."""

    name: str
    signals: int = 0
    ai_approved: int = 0
    ai_rejected: int = 0
    reasons: Counter[str] = field(default_factory=Counter)
    ai_cost: Decimal = Decimal("0")
    accepted: int = 0
    risk_blocked: int = 0
    sent: int = 0
    filled: int = 0
    rejected: Counter[str] = field(default_factory=Counter)  # TWS code -> entries IBKR rejected
    expired: int = 0  # approvals that expired before their order left (paper_bridge _expiry_problem)
    open_samples: list[int] = field(default_factory=list)
    symbols: Counter[str] = field(default_factory=Counter)  # entries per symbol
    trades: list[TradeRow] = field(default_factory=list)
    ack_latency: list[Decimal] = field(default_factory=list)  # seconds, decision -> IBKR's first status
    sent_latency: list[Decimal] = field(default_factory=list)  # seconds, decision -> order placed
    guards: Counter[str] = field(default_factory=Counter)

    @property
    def reentries(self) -> int:
        return sum(count - 1 for count in self.symbols.values() if count > 1)

    @property
    def closed_trades(self) -> list[TradeRow]:
        return [trade for trade in self.trades if trade.closed is not None]

    @property
    def pnl(self) -> Decimal:
        return sum((trade.pnl for trade in self.trades), Decimal("0"))

    @property
    def mean_r(self) -> Decimal | None:
        rs = [trade.r for trade in self.closed_trades if trade.r is not None]
        return None if not rs else (sum(rs, Decimal("0")) / len(rs)).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)

    @property
    def win_rate(self) -> Decimal | None:
        closed = self.closed_trades
        return None if not closed else _ratio(Decimal(sum(trade.pnl > 0 for trade in closed)), Decimal(len(closed)))

    @property
    def max_drawdown(self) -> Decimal:
        """The deepest fall of the realized P&L curve (trades in close order) from its running peak."""
        peak = equity = drawdown = Decimal("0")
        for trade in sorted(self.closed_trades, key=lambda item: (item.closed or "", item.orders)):
            equity += trade.pnl
            peak = max(peak, equity)
            drawdown = max(drawdown, peak - equity)
        return drawdown

    def top_reasons(self) -> list[tuple[str, int]]:
        return sorted(self.reasons.items(), key=lambda item: (-item[1], item[0]))[:TOP_REASONS]

    def as_json(self) -> dict[str, Any]:
        return {
            "signals": self.signals,
            "ai": {"approved": self.ai_approved, "rejected": self.ai_rejected,
                   "top_rejection_reasons": [{"reason": reason, "count": count} for reason, count in self.top_reasons()],
                   "cost_usd": _plain(self.ai_cost)},
            "decisions": {"accepted": self.accepted, "risk_blocked": self.risk_blocked},
            "orders": {"sent": self.sent, "filled": self.filled, "rejected": dict(sorted(self.rejected.items())),
                       "expired_unsent": self.expired},
            "positions_at_once": _count_quantiles(self.open_samples),
            "entries": self.sent,
            "reentries": {"count": self.reentries,
                          "symbols": {symbol: count for symbol, count in sorted(self.symbols.items()) if count > 1}},
            "trades": [trade.as_json() for trade in self.trades],
            "mean_r": None if self.mean_r is None else str(self.mean_r),
            "win_rate_pct": None if self.win_rate is None else str(self.win_rate),
            "pnl": str(self.pnl),
            "max_drawdown": str(self.max_drawdown),
            "latency_s": {"signal_to_ack": _quantiles(self.ack_latency), "signal_to_sent": _quantiles(self.sent_latency)},
            "guards": dict(sorted(self.guards.items())),
        }

    def text(self) -> str:
        reasons = ", ".join(f'"{reason}" x{count}' for reason, count in self.top_reasons()) or "none"
        rejected = ", ".join(f"{code} x{count}" for code, count in sorted(self.rejected.items())) or "none"
        positions = _count_quantiles(self.open_samples)
        ack, sent = _quantiles(self.ack_latency), _quantiles(self.sent_latency)
        again = ", ".join(f"{symbol} x{count}" for symbol, count in sorted(self.symbols.items()) if count > 1)
        guards = ", ".join(f"{token} x{count}" for token, count in sorted(self.guards.items())) or "none"
        return (f"{self.name}: signals {self.signals}; AI {self.ai_approved} approved, {self.ai_rejected} rejected"
                f" (top: {reasons}), cost ${_plain(self.ai_cost)}; accepted {self.accepted}, risk-blocked"
                f" {self.risk_blocked}; orders sent {self.sent}, filled {self.filled}, rejected by IBKR {rejected},"
                f" expired unsent {self.expired}; positions at once p50 {positions['p50']} p95 {positions['p95']}"
                f" max {positions['max']}; entries {self.sent}, re-entries {self.reentries}"
                f"{f' ({again})' if again else ''}; trades {len(self.trades)} (closed {len(self.closed_trades)}),"
                f" win rate {self.win_rate if self.win_rate is not None else 'n/a'}%, mean R"
                f" {self.mean_r if self.mean_r is not None else 'n/a'}, P&L {self.pnl}, max drawdown"
                f" {self.max_drawdown}; latency signal->ack p50 {ack['p50']}s p95 {ack['p95']}s (signal->sent p50"
                f" {sent['p50']}s p95 {sent['p95']}s); guards {guards}")


@dataclass(slots=True)
class AccountMetrics:
    """The account-level part of the nightly report."""

    buying_power: dict[str, Any] = field(default_factory=dict)
    close_out: dict[str, Any] = field(default_factory=dict)
    stop_repairs: dict[str, Any] = field(default_factory=dict)
    guards: dict[str, int] = field(default_factory=dict)
    sentinel_would_fence: dict[str, Any] = field(default_factory=dict)
    loss_step_pages: list[int] = field(default_factory=list)
    low_cushion_pages: int = 0
    manual_unprotected_pages: int = 0
    market: dict[str, Any] = field(default_factory=dict)
    size_vs_displayed: dict[str, Any] = field(default_factory=dict)

    def as_json(self) -> dict[str, Any]:
        return {
            "buying_power": self.buying_power, "close_out": self.close_out, "stop_repairs": self.stop_repairs,
            "guards": dict(sorted(self.guards.items())), "sentinel_would_fence": self.sentinel_would_fence,
            "loss_step_pages": list(self.loss_step_pages), "low_cushion_pages": self.low_cushion_pages,
            "manual_unprotected_pages": self.manual_unprotected_pages, "market": self.market,
            "size_vs_displayed": self.size_vs_displayed,
        }

    def lines(self) -> list[str]:
        power = self.buying_power
        power_text = (f"peak buying power used {power['peak_used_pct']}% of available (min AvailableFunds"
                      f" {power['min_available_funds']}, min Cushion {power['min_cushion']}; {power['samples']}"
                      " readings)" if power.get("samples") else f"buying power: {power.get('note', 'n/a')}")
        close = self.close_out
        repairs = self.stop_repairs
        fence = self.sentinel_would_fence
        fence_text = (", ".join(f"{name} x{count}" for name, count in sorted(fence.get("identities", {}).items()))
                      or "none") if fence.get("status") == "read" else f"n/a ({fence.get('reason')})"
        market = "; ".join(
            f"{symbol} {value['return_pct']}% (range {value['range_pct']}%)" if value.get("status") == "read"
            else f"{symbol} n/a ({value.get('reason')})" for symbol, value in self.market.items())
        return [
            power_text,
            f"close-out: last position flat {close.get('last_flat') or 'none held'}"
            f" (limit {close.get('limit')} ET); still open at the end: {', '.join(close.get('open_at_end', [])) or 'none'}",
            f"stop repairs: {repairs.get('episodes', 0)} ({repairs.get('repaired', 0)} repaired,"
            f" {repairs.get('closed_at_market', 0)} closed at market), longest {repairs.get('longest_s')}s without a stop",
            "guards triggered: " + (", ".join(f"{token} x{count}" for token, count in sorted(self.guards.items()))
                                     or "none"),
            f"Sentinel would-fence: {fence_text}",
            f"loss-step pages: {', '.join(f'{step}%' for step in self.loss_step_pages) or 'none'}; low-cushion pages"
            f" {self.low_cushion_pages}; manual positions without a stop paged {self.manual_unprotected_pages}",
            f"market: {market}",
            f"order size vs displayed size: {self.size_vs_displayed.get('status')} ({self.size_vs_displayed.get('reason')})",
        ]


@dataclass(slots=True)
class DayMetrics:
    """Rule 8: per sendable identity, the total, and the account."""

    mode: str
    strategies: dict[str, StrategyMetrics]
    total: StrategyMetrics
    account: AccountMetrics

    def as_json(self) -> dict[str, Any]:
        return {"mode": self.mode, "strategies": {name: item.as_json() for name, item in self.strategies.items()},
                "total": self.total.as_json(), "account": self.account.as_json(),
                "definitions": dict(METRIC_DEFINITIONS)}

    def lines(self) -> list[str]:
        trades = [trade.text() for item in self.strategies.values() for trade in item.trades]
        return [*(item.text() for item in self.strategies.values()), self.total.text(), *self.account.lines(),
                *trades]

    def owner_lines(self) -> list[str]:
        total = self.total
        close = self.account.close_out
        head = (f"total: {total.signals} signals, {total.sent} sent, {total.filled} filled, {len(total.closed_trades)}"
                f" trades, win {total.win_rate if total.win_rate is not None else 'n/a'}%, mean R"
                f" {total.mean_r if total.mean_r is not None else 'n/a'}, P&L {total.pnl}; positions at once max"
                f" {max(total.open_samples, default=0)}; last flat {close.get('last_flat') or 'none held'}")
        rows = [f"{item.name}: {item.sent} sent, {len(item.closed_trades)} trades, P&L {item.pnl}, mean R"
                f" {item.mean_r if item.mean_r is not None else 'n/a'}" for item in self.strategies.values()
                if item.signals or item.sent]
        return [head, *rows]


# What each number of the JSON means (the replay engine and the 14 Nov decision read them).
METRIC_DEFINITIONS: Mapping[str, str] = MappingProxyType({
    "signals": "the day's decisions with a setup (every outcome but data_blocked and rule_rejected)",
    "positions_at_once": "brackets that hold or may hold shares at each SENT, the new one included (what a"
                         " position cap checks); the total counts every identity's",
    "quantiles": "nearest rank (an observed value)",
    "latency_s": "decision written -> order placed (the state's sent_at), and -> IBKR's first PreSubmitted,"
                 " Submitted or Filled status (bridge.log, whole-second clocks, never below 0)",
    "r": "realized P&L over filled shares x (SENT entry - SENT stop)",
    "max_drawdown": "the deepest fall of the realized P&L (trades in close order) from its running peak",
    "win_rate_pct": "closed trades with a positive P&L, of all closed trades",
    "pct_day_volume": "filled shares over the research bars' day volume (round lots x 100)",
})
NO_SETUP_OUTCOMES = frozenset({"data_blocked", "rule_rejected"})  # a candidate without a setup: no signal
DAY_DECISIONS_SQL = """
    SELECT d.outcome, d.payload_json, v.spec_json
    FROM autotrader_candidate_decisions d
    JOIN autotrader_runs r ON r.id = d.run_id
    LEFT JOIN autotrader_strategy_versions v
      ON v.strategy_id = r.strategy_id AND v.version = r.strategy_version
    WHERE r.session_date = ?
    ORDER BY d.created_at, d.id
"""


def _reason(text: object) -> str:
    words = " ".join(str(text or "").split())
    return words if len(words) <= REASON_WIDTH else words[:REASON_WIDTH - 3] + "..."


def _count_decisions(context: _Context, strategies: Mapping[str, StrategyMetrics]) -> None:
    """Signals and AI verdicts from the decision ledger (read-only): every decision with a setup."""
    for outcome, payload_json, spec_json in context.connection.execute(DAY_DECISIONS_SQL,
                                                                       (context.day.isoformat(),)).fetchall():
        item = strategies.get(strategy_name_from_spec(spec_json))
        if item is None or str(outcome) in NO_SETUP_OUTCOMES:
            continue
        item.signals += 1
        item.accepted += str(outcome) == "shadow_accepted"
        item.risk_blocked += str(outcome) == "risk_blocked"
        try:
            payload = json.loads(str(payload_json))
        except ValueError:
            payload = None
        review = payload.get("ai_review") if isinstance(payload, dict) else None
        review = review if isinstance(review, dict) else {}
        item.ai_cost += _decimal(review.get("cost_usd")) or Decimal("0")
        if review.get("decision") == "PROPOSE":
            item.ai_approved += 1
        else:
            item.ai_rejected += 1
            item.reasons[_reason(review.get("failure_reason") or review.get("rationale") or "no AI review recorded")] += 1


def _research_day(data: Path | None, day: date, con_id: int) -> Any:
    """The research recorder's regular-session bars of ``con_id`` on ``day``, or why there are none."""
    from trading.research.bars_store import BarsStoreError, ResearchPaths, day_from_payload

    if data is None or con_id <= 0:
        return "no contract id"
    path = ResearchPaths(root=data / "research").bars_file(day, con_id)
    if not _regular_file(path):
        return f"no research bars for {day} (the recorder runs 16:20-17:30 ET)"
    try:
        stored = day_from_payload(json.loads(_read_bytes(path, MAX_BARS_FILE_BYTES).decode("utf-8")),
                                  expected_date=day, expected_con_id=con_id)
    except (InputsMissing, BarsStoreError, UnicodeDecodeError, ValueError) as exc:
        return f"research bars unreadable ({type(exc).__name__})"
    return stored if stored.use_rth else "research bars filed in the wrong series (extended hours)"


def _market_day(data: Path | None, day: date) -> dict[str, Any]:
    """SPY and QQQ: the day's return and range, from the research bars when the recorder stored them."""
    from trading.research.bars_store import ResearchPaths

    found: dict[str, Any] = {}
    directory = ResearchPaths(root=data / "research").session_directory(day) if data is not None else None
    if directory is not None and directory.is_dir() and not directory.is_symlink():
        for path in sorted(directory.glob("*.json")):
            if not _regular_file(path):
                continue
            try:
                payload = json.loads(_read_bytes(path, MAX_BARS_FILE_BYTES).decode("utf-8"))
            except (InputsMissing, UnicodeDecodeError, ValueError):
                continue
            symbol = str(payload.get("symbol") or "").upper() if isinstance(payload, dict) else ""
            if symbol in MARKET_SYMBOLS and symbol not in found and path.stem.isdigit():
                stored = _research_day(data, day, int(path.stem))
                if not isinstance(stored, str):
                    found[symbol] = stored
    market: dict[str, Any] = {}
    for symbol in MARKET_SYMBOLS:
        stored = found.get(symbol)
        if stored is None:
            market[symbol] = {"status": "n/a", "reason": f"no research bars for {symbol} on {day} (the recorder"
                                                         " runs 16:20-17:30 ET and stores the day's contracts)"}
            continue
        base = stored.prior_close if stored.prior_close else stored.bars[0].open
        high, low = max(bar.high for bar in stored.bars), min(bar.low for bar in stored.bars)
        market[symbol] = {"status": "read", "return_pct": str(_ratio(stored.bars[-1].close - base, base)),
                          "range_pct": str(_ratio(high - low, base)),
                          "base": "prior close" if stored.prior_close else "first open"}
    return market


def _would_fence(logs: Path | None, day: date) -> dict[str, Any]:
    """The Sentinel's log-only lines (sentinel would fence <ID>: ...) dated ``day`` in New York."""
    if logs is None:
        return {"status": "n/a", "reason": "no log directory"}
    files = log_files(logs, NOTIFIER_LOG)
    if not files:
        return {"status": "n/a", "reason": f"no {NOTIFIER_LOG}"}
    seen: set[str] = set()
    counts: Counter[str] = Counter()
    for path in files:
        try:
            lines = _lines(path)
        except InputsMissing as exc:
            return {"status": "n/a", "reason": str(exc)}
        for raw in lines:
            index = raw.find(SENTINEL_WOULD_FENCE)
            at = line_time(raw) if index >= 0 else None
            if at is None or at.astimezone(NY).date() != day or raw in seen:
                continue  # a rotation's carried copy is byte-identical: counted once
            seen.add(raw)
            counts[raw[index + len(SENTINEL_WOULD_FENCE):].split(":", 1)[0].strip()] += 1
    return {"status": "read", "identities": dict(sorted(counts.items()))}


def _account_values(section: BridgeSection) -> dict[str, Any]:
    readings: list[dict[str, Decimal | None]] = []
    for line in section.lines():
        if line.kind == "INFO" and line.text.startswith(f"{T1_ACCOUNT_VALUES} "):
            readings.append({found.group("tag"): _decimal(found.group("value"))
                             for found in _ACCOUNT_VALUE.finditer(line.text[len(T1_ACCOUNT_VALUES) + 1:])})
    if not readings:
        return {"samples": 0, "peak_used_pct": None, "min_available_funds": None, "min_cushion": None,
                "note": f"n/a (no {T1_ACCOUNT_VALUES} line: the bridge logs account values in the no-limit test)"}
    used = [_ratio(gross, gross + power) for item in readings
            if (gross := item.get("GrossPositionValue")) is not None and (power := item.get("BuyingPower")) is not None
            and gross + power > 0]
    funds = [value for item in readings if (value := item.get("AvailableFunds")) is not None]
    cushion = [value for item in readings if (value := item.get("Cushion")) is not None]
    return {"samples": len(readings), "peak_used_pct": None if not used else str(max(used)),
            "min_available_funds": None if not funds else str(min(funds)),
            "min_cushion": None if not cushion else str(min(cushion)),
            "definition": "GrossPositionValue / (GrossPositionValue + BuyingPower)"}


def day_metrics(context: _Context, episodes: Sequence[RepairEpisode], trips: Sequence[GuardTrip]) -> DayMetrics:
    """Rule 8's numbers: every input read-only (bridge.log, the state, the ledger, notifier.log, research bars)."""
    day, timeline = context.day, context.timeline
    names = sorted(set(SENDABLE_IDENTITIES) | {entry.identity for entry in timeline.entries})
    strategies = {name: StrategyMetrics(name) for name in names}
    _count_decisions(context, strategies)
    acks: dict[int, LogLine] = {}
    rejected: list[tuple[str, str]] = []
    for line in context.section.lines():
        if line.kind == "INFO" and (found := _ORDER_STATUS.match(line.text)) is not None:
            if found.group("status") in ACKNOWLEDGED_STATUSES:
                acks.setdefault(int(found.group("order")), line)
        elif line.kind in {"INFO", "CRITICAL"} and (found := _BROKER_REJECTED.match(line.text)) is not None:
            rejected.append((found.group("identity").strip(), found.group("code")))
    for identity, code in rejected:
        if identity in strategies:
            strategies[identity].rejected[code] += 1
    for skip in timeline.skips:
        if skip.label is not None and skip.label.strip() in strategies and skip.reason.startswith(EXPIRED_REASON):
            strategies[skip.label.strip()].expired += 1
    for trip in trips:
        if trip.identity in strategies:
            strategies[trip.identity].guards[trip.token] += 1
    total_open: list[int] = []
    for entry, (_, mine, everyone) in zip(timeline.entries, timeline.open_samples):
        item = strategies[entry.identity]
        item.sent += 1
        item.symbols[entry.symbol] += 1
        item.open_samples.append(mine)
        total_open.append(everyone)
        decided = _iso(entry.decision.created_at) if entry.decision is not None else None
        sent_at = entry.sent_at(day)
        ack = acks.get(entry.parent)
        acked = ack.at(day) if ack is not None else None
        if decided is not None and sent_at is not None:
            item.sent_latency.append(_seconds(sent_at - decided))
        if decided is not None and acked is not None:
            item.ack_latency.append(_seconds(acked - decided))
        if not _held_shares(entry):
            continue
        item.filled += 1
        bracket = entry.bracket
        filled = bracket.filled if bracket is not None and bracket.filled > 0 else _last_filled(entry)
        stored = _research_day(context.data, day, bracket.con_id) if bracket is not None else "no state bracket"
        volume = None if isinstance(stored, str) else sum((bar.volume for bar in stored.bars), Decimal("0"))
        reason = bracket.exit_reason if bracket is not None and bracket.exit_reason else _closed_reason(entry)
        item.trades.append(TradeRow(
            identity=entry.identity, symbol=entry.symbol, orders=f"{entry.parent}-{entry.parent + 2}",
            sent=entry.line.stamp.removesuffix(" ET"), closed=entry.closed.stamp.removesuffix(" ET") if entry.closed else None,
            qty=entry.qty, filled=filled, entry=entry.entry, stop=entry.stop,
            avg_entry=bracket.avg_entry if bracket is not None else None,
            avg_exit=bracket.avg_exit if bracket is not None else None, exit_reason=reason,
            pnl=bracket.realized_pnl if bracket is not None else entry.realized,
            pct_day_volume=None if not volume else _ratio(Decimal(filled), volume * BAR_VOLUME_MULTIPLIER),
        ))
    total = StrategyMetrics("total")
    for item in strategies.values():
        total.signals += item.signals
        total.ai_approved += item.ai_approved
        total.ai_rejected += item.ai_rejected
        total.reasons.update(item.reasons)
        total.ai_cost += item.ai_cost
        total.accepted += item.accepted
        total.risk_blocked += item.risk_blocked
        total.sent += item.sent
        total.filled += item.filled
        total.rejected.update(item.rejected)
        total.expired += item.expired
        total.symbols.update(item.symbols)
        total.trades += item.trades
        total.ack_latency += item.ack_latency
        total.sent_latency += item.sent_latency
        total.guards.update(item.guards)
    total.open_samples = total_open
    total.trades.sort(key=lambda trade: (trade.sent, trade.orders))
    account = AccountMetrics()
    account.buying_power = _account_values(context.section)
    flat_by = context.closes_at - FLAT_BY_BEFORE_CLOSE
    held = [entry for entry in timeline.entries if _held_shares(entry)]
    flats = [at for entry in held if entry.closed is not None and (at := entry.closed.at(day)) is not None]
    account.close_out = {"last_flat": None if not flats else f"{max(flats):%H:%M:%S}", "limit": f"{flat_by:%H:%M}",
                         "open_at_end": [entry.name for entry in held if entry.closed is None]}
    resolved = [episode.seconds for episode in episodes if episode.seconds is not None]
    account.stop_repairs = {"episodes": len(episodes), "repaired": sum(e.outcome == "repaired" for e in episodes),
                            "closed_at_market": sum(e.closing for e in episodes),
                            "unresolved": sum(e.seconds is None for e in episodes),
                            "longest_s": max(resolved, default=0)}
    account.guards = dict(Counter(trip.token for trip in trips))
    account.sentinel_would_fence = _would_fence(context.logs, day)
    account.loss_step_pages = _loss_steps(context.section)
    account.low_cushion_pages = sum(1 for line in context.section.lines()
                                    if line.kind == "CRITICAL" and line.text.startswith(f"{T1_LOW_CUSHION}:"))
    account.manual_unprotected_pages = sum(1 for line in context.section.lines() if line.kind == "CRITICAL"
                                           and line.text.startswith(f"{T1_UNPROTECTED_MANUAL} "))
    account.market = _market_day(context.data, day)
    account.size_vs_displayed = {"status": "n/a", "reason": "the bridge logs no displayed (bid or ask) size at the"
                                                           " quote; % of the day's volume is per trade"}
    return DayMetrics(mode=NO_LIMIT_MODE if context.no_limit else V7C_MODE, strategies=strategies, total=total,
                      account=account)


def _seconds(delta: timedelta) -> Decimal:
    """Whole log clocks against precise times: never below zero, tenths of a second."""
    return max(Decimal("0"), Decimal(str(delta.total_seconds()))).quantize(Decimal("0.1"), rounding=ROUND_HALF_EVEN)


def _closed_reason(entry: Entry) -> str:
    found = _CLOSED.match(entry.closed.text) if entry.closed is not None else None
    return found.group("reason") if found is not None else ""


def _last_filled(entry: Entry) -> int:
    for line in reversed(entry.fills):
        found = _FILL.match(line.text)
        if found is not None:
            return int(found.group("filled"))
    return 0


def _rule_metrics(metrics: DayMetrics) -> RuleResult:
    total = metrics.total
    label = f"no-limit test {NO_LIMIT_TEST_LABEL}" if metrics.mode == NO_LIMIT_MODE else "v7c (baseline)"
    summary = (f"{label}: {total.signals} signals ({total.ai_approved} AI-approved), {total.sent} sent,"
               f" {total.filled} filled, {len(total.closed_trades)} trades, win rate"
               f" {total.win_rate if total.win_rate is not None else 'n/a'}%, mean R"
               f" {total.mean_r if total.mean_r is not None else 'n/a'}, P&L {total.pnl}; positions at once max"
               f" {max(total.open_samples, default=0)}; last flat"
               f" {metrics.account.close_out.get('last_flat') or 'none held'} ET")
    return RuleResult(METRICS_RULE, "no-limit test metrics", INFO, summary, metrics.lines())


def _inputs(context: _Context, logs: Path, data: Path, db: Path, supervisor_files: int) -> list[str]:
    section = context.section
    first, last = section.runs[0], section.runs[-1]
    versions = sorted({run.version for run in section.runs if run.version})
    shas = sorted({run.sha256[:12] for run in section.runs if run.sha256})
    logged = context.net_liquidations
    sent = sum(1 for bracket in context.state.values() if bracket.sent)
    return [
        f"bridge.log section {context.day}: {len(section.runs)} bridge process(es),"
        f" {first.source}:{first.first_line} to {last.source}:{last.lines[-1].number if last.lines else last.first_line}"
        f" ({', '.join(section.files)} in {logs})",
        f"bridge {', '.join(versions) or 'version not logged'}, sha256 {', '.join(shas) or 'not logged'};"
        f" net liquidation logged {', '.join(str(value) for value in logged) or 'never'}",
        f"supervisor.log: {len(context.supervisor)} bridge event(s) on {context.day} in {supervisor_files} file(s),"
        " rotation-carried lines skipped",
        f"bridge state {BRIDGE_STATE_DIRECTORY}/{context.day}.json: {len(context.state)} bracket(s), {sent} sent"
        f" ({data})",
        f"database {db} (read-only, mode=ro): {len(context.decisions)} shadow_accepted decision(s) on {context.day},"
        f" {len(context.fences)} fence row(s)",
        f"XNYS close {context.closes_at:%H:%M} ET{' (half day)' if context.closes_at.time() != time(16) else ''}",
        ("mode: no-limit test " + NO_LIMIT_TEST_LABEL + f" ({T1_MODE} logged by"
         f" {sum(run.no_limit for run in section.runs)} of {len(context.configured)} configured process(es))")
        if context.no_limit else "mode: v7c (no process logged the no-limit test's mode line)",
        f"rule 8 also reads {NOTIFIER_LOG}* (the Sentinel's would-fence lines) and research/bars/{context.day}"
        " (SPY, QQQ, day volumes) when present",
    ]


def check_session(day: date, *, logs: Path, data: Path, db: Path) -> SessionReport:
    """The nine rules for ``day``; nothing read is ever written."""
    report = SessionReport(session_date=day)
    session = market_session(day)
    if session is None:
        report.missing.append(f"{day} is not an XNYS session")
        return report
    try:
        section = read_bridge_section(logs, day)
        supervisor, supervisor_files = read_supervisor_bridge(logs, day)
        state = read_state(data, day)
        with closing(open_read_only(db)) as connection:
            try:
                decisions = read_decisions(connection, day)
                fences = read_fences(connection)
            except sqlite3.Error as exc:
                raise InputsMissing(f"database {db} lacks what the bridge reads ({type(exc).__name__}: {exc})") from exc
            context = _Context(
                day=day, closes_at=session.closes_at.astimezone(NY), section=section, supervisor=supervisor,
                state=state, decisions=decisions, fences=fences,
                timeline=replay(section, state, decisions), connection=connection,
                opens_at=session.opens_at.astimezone(NY), logs=logs, data=data,
            )
            episodes, trips = repair_episodes(section, day), guard_trips(section)
            rules = [
                _rule_identities(context), _rule_attribution(context), _rule_caps(context),
                _rule_size(context), _rule_daily_loss(context), _rule_fences(context),
                _rule_day_risk_budget(context),
            ]
            metrics: DayMetrics | None = None
            try:
                metrics = day_metrics(context, episodes, trips)
                rules.append(_rule_metrics(metrics))
            except sqlite3.Error as exc:
                raise InputsMissing(f"database {db} lacks the decision ledger ({type(exc).__name__}: {exc})") from exc
            except Exception as exc:  # noqa: BLE001 - rule 8 only reports: its failure never hides the other rules
                rules.append(RuleResult(METRICS_RULE, "no-limit test metrics", WARN,
                                        f"the metrics could not be computed ({type(exc).__name__}: {exc})"))
            rules.append(_rule_abort(context, episodes, trips))
            report.rules = rules
            report.mode = NO_LIMIT_MODE if context.no_limit else V7C_MODE
            report.metrics = metrics
            report.inputs = _inputs(context, logs, data, db, supervisor_files)
    except InputsMissing as exc:
        report.missing.append(str(exc))
        report.rules, report.metrics, report.mode = [], None, None
    except OSError as exc:  # a log, the state or the database could not be read
        report.missing.append(f"an input is unreadable ({type(exc).__name__}: {exc})")
        report.rules, report.metrics, report.mode = [], None, None
    return report


__all__ = [
    "APPROVED_POLICIES",
    "BRIDGE_IDENTITY_CAPS",
    "DAY_RISK_BUDGET_UNVERIFIED",
    "DECISIONS_SQL",
    "EXIT_FAILED",
    "EXIT_INPUTS_MISSING",
    "EXIT_PASSED",
    "FAIL",
    "INFO",
    "IdentityCaps",
    "BudgetCheck",
    "DayMetrics",
    "InputsMissing",
    "NO_LIMIT_TEST_IDENTITY_CAPS",
    "OverCheck",
    "PASS",
    "SessionReport",
    "WARN",
    "check_session",
    "day_metrics",
    "entry_deadline",
    "guard_trips",
    "mask_accounts",
    "open_read_only",
    "parse_budget_ok",
    "parse_intent",
    "parse_over_limits",
    "read_bridge_section",
    "repair_episodes",
    "strategy_name_from_spec",
]

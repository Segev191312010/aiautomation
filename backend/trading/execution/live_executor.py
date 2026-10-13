"""Reviewed PAPER execution engine (Milestone C2): BUILT, NOT WIRED.

This module is meant to replace ``backend/scripts_local/paper_bridge.py``.
Nothing starts it: no supervisor child, no LaunchAgent, no CLI command. The
bridge remains the only order sender until governance replaces it after an
independent execution-safety review, a supervised drill and a clean paper
week (AGENTS.md, docs/PAPER_EXECUTION_STATUS.md). :meth:`LiveExecutor.start`
refuses to run without an explicit promotion record for this exact code
(``trading/execution/promotion.py``).

One approved decision becomes one native IBKR bracket:

  outbox claim + bracket + legs + risk reservation, in ONE transaction
  -> parent BUY LMT DAY  transmit=False  orderRef at1:<bracket>:P
  -> target SELL LMT GTC transmit=False  orderRef at1:<bracket>:T
  -> stop SELL STP GTC   transmit=True   orderRef at1:<bracket>:S
  (IBKR OCA-links the children; the stop's transmit sends all three)

The decision id is the outbox key, so a decision is considered exactly once.
A crash never resends: a bracket still ``sending`` at a restart becomes
``sent`` when IBKR reports its legs and ``unknown`` (never resent) when IBKR
knows none of them.

Safety rules (the reviewed bridge's lessons are named in brackets):

* Paper only: every account TWS manages must start with ``DU`` and include the
  configured one, checked at start and on every reconnect; orders carry the
  account explicitly.
* Order ids are above TWS's next valid id, the ledger's highest id and every
  id TWS reports for this client (open orders, executions, unmanaged orders)
  [L-c]; ``reqIds`` is sent at start and on every reconnect.
* It never starts while the PAPER bridge runs (its process, or a bridge
  heartbeat under 60 s old), after the session's stop time, or for any code
  other than the promoted build. A slow TWS exits 75, anything unsafe 78.
* Entries: promoted MOMO_HOD strategies only, inside the strategy window and
  the risk approval's lifetime [L6], once per setup [L6], only in symbols the
  account does not hold [N1] and nobody has a working order in (owner TWS
  orders, ``manual:`` orders, unmanaged orders [L4]), never after a stop
  request [N6], and only within the shared risk book (0.5% new, 1.5% open, 2%
  daily, three positions, 25% notional [M3]). The kill switch, a reconciliation
  mismatch or configuration can block entries; nothing ever blocks an exit.
* Fills come from execDetails and commissionReport, deduplicated by execId (a
  correction replaces what it corrects) and scoped to this account and the
  engine's own orderRefs [L5]; they never decrease.
* Every exit quantity comes from ONE locked fill snapshot taken right before
  placeOrder, never exceeds the fills and is capped by the account position
  [H1, H2]; exit fills above entry fills block every exit and mark the bracket
  needs_attention [H1].
* A modify sends the full order, transmit=True, built by the same factory as
  the placement, copying only the OCA link from the order TWS reported [N3];
  it counts as done only when TWS echoes it and reports no error since; it is
  re-sent once per tick, at most three times, then CRITICAL [N2].
* A dead, partly filled parent has its exits resized to its fresh fill count
  [M2, N7]; every TWS error on an own order is CRITICAL unless informational
  [N2, L1, L8].
* Flatten at the real close minus 15 minutes (half-day aware): the target is
  repriced once to a marketable limit [H2]. Only when that cannot be done or
  is not confirmed: cancel target and stop (an Inactive leg too), CONFIRM both
  are Cancelled or Filled, then one MARKET sell. Both sell at most the ledger
  quantity capped by a fresh account position less every other working sell
  and less what other orders sold since the entry that the position does not
  show yet (completed orders of every client, the TWS GUI included; a Filled
  sell counts in full). No second sell ever follows a manual close [H2], and
  a restart never starts a flatten again [N5]. Without a fresh snapshot
  nothing is sold and the decision is taken again every tick: the flatten's
  start until 10 minutes before the close, the fallback's market sell until
  the close. Shares left without stop, target or working sell are flagged
  UNPROTECTED at once; a restart after the stop time finishes only a
  fallback whose cancels were already sent, before the close.
* An entry still working when its stop dies is cancelled: no share is bought
  without a stop.
* Reconciliation at start, on every reconnect, after TWS error 1101 (which
  also renews the position and account subscriptions) and every two minutes,
  scoped to the ``at1:`` prefix: a mismatch pauses new entries and raises
  CRITICAL; ``manual:`` orders and positions the engine did not open are left
  alone. A short position in a symbol the engine traded today, an entry whose
  stop died and a running bridge are mismatches too, and the engine never
  reports FLAT while the account is short such a symbol.
* Every stdout line is ``HH:MM:SS ET  <KIND> <text>`` (the notifier format).
"""
from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import date, datetime, time as clock_time, timedelta, timezone
from decimal import ROUND_CEILING, ROUND_DOWN, ROUND_FLOOR, Decimal, InvalidOperation
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
from typing import Any, Literal, Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import aiosqlite

from db.core import get_db
from trading.execution.promotion import (
    EngineAuthority,
    ExecutionAuthorityError,
    engine_code_digest,
    is_paper_account,
)
from trading.execution.tws_state import (
    DEAD_ORDER_STATUSES,
    LIVE_ORDER_STATUSES,
    PARENT_CANCELLED_STATUSES,
    WORKING_ORDER_STATUSES,
    ExecutionRow,
    TwsMirror,
    decimal_or_none,
    whole,
)
from trading.operations.paths import UserPaths
from trading.risk.book import NewTrade, evaluate_new_trade
from trading.storage.execution_ledger import (
    BRACKET_LEGS,
    EXIT_LEGS,
    BracketRecord,
    DecisionAlreadyClaimed,
    ExecutionLedger,
    FillRecord,
    LegCode,
    LegRecord,
    RiskBookRefusal,
    book_id,
    new_bracket_id,
    order_ref,
    parse_order_ref,
    risk_book_from,
)
from trading.storage.runtime_fence import SQLitePaperKillStore


ENGINE_VERSION = "c2.2"
NY = ZoneInfo("America/New_York")
UTC = timezone.utc

EXIT_OK = 0
EXIT_RESTART = 75  # EX_TEMPFAIL: a supervisor may restart the engine
EXIT_REFUSED = 78  # EX_CONFIG: unsafe or unauthorized; a restart cannot fix it

EVENT_KINDS = frozenset({
    "SENT", "FILL", "EXIT", "CLOSED", "CANCEL", "CANCELLED", "SKIP",
    "FLATTEN", "FLAT", "RESIZE", "CRITICAL", "INFO",
})
LEG_NAMES: dict[str, str] = {"P": "entry", "T": "target", "S": "stop", "F": "flatten sell"}
PROTECTIVE_LEGS: tuple[LegCode, LegCode] = ("T", "S")
# Only these end an order for good: an Inactive order can come back, so the fallback cancels it too.
FALLBACK_DEAD_STATUSES = frozenset({"Cancelled", "ApiCancelled", "Filled"})
# A slow TWS (no accounts or order id in time) is transient: exit 75 so a supervisor retries.
TRANSIENT_REFUSALS = frozenset({"accounts_unknown", "no_order_id"})
BRIDGE_HEARTBEAT_WINDOW = timedelta(seconds=60)  # a bridge heartbeat this recent means the bridge runs
# The bridge in any process's arguments (pgrep -f, extended regex): the supervisor's launch line
# (.../scripts_local/paper_bridge.py --date D), a hand start and the module form
# (python -m scripts_local.paper_bridge). Not test_paper_bridge.py, not paper_bridge_state.json.
BRIDGE_PROCESS_PATTERN = r"(^|[ /.])paper_bridge(\.py)?( |$)"
UNSHOWN_SELL_ALERT = timedelta(seconds=15)  # another order's sell the position still does not show [L-e]
# Completion times are TWS's (seconds, maybe another clock): a sell this close to the entry counts as after it.
FOREIGN_FILL_MARGIN = timedelta(seconds=60)
SNAPSHOT_MISSING = "TWS did not report a fresh snapshot"
EASTERN_ZONE_NAMES = frozenset({"EST", "EDT", "ET"})
_COMPLETED_TIME = re.compile(r"^(\d{8})[\s-]+(\d{2}):?(\d{2}):?(\d{2})(?:\s+(\S+))?")

DEFAULT_DECISION_TTL_S = 15  # trading/risk/engine.py: a risk approval lives 15 s
MAX_DECISION_AGE_S = 60  # backstop even when an intent claims a longer expiry
ENTRY_TTL = timedelta(minutes=10)
REGULAR_CLOSE = clock_time(16, 0)
LAST_ENTRY_BEFORE_CLOSE = timedelta(minutes=30)
FLATTEN_BEFORE_CLOSE = timedelta(minutes=15)
FLATTEN_ALERT_BEFORE_CLOSE = timedelta(minutes=14)
VERIFY_BEFORE_CLOSE = timedelta(minutes=10)
STOP_BEFORE_CLOSE = timedelta(minutes=5)
FINAL_ALERT_BEFORE_CLOSE = timedelta(minutes=2)
STRATEGY_CUTOFFS = {"momo": clock_time(15, 0), "orb": clock_time(12, 0)}
TICK = Decimal("0.01")
FLATTEN_DISCOUNT = Decimal("0.995")
LATE_FLATTEN_DISCOUNT = Decimal("0.98")  # the first and only flatten of a start after the stop time
SNAPSHOT_EXTRA_DISCOUNT = Decimal("0.99")
POSITION_GAP_GRACE = timedelta(seconds=15)  # a position update or OCA cancel may trail its fill
MODIFY_RETRIES = 3  # re-sends of a modification TWS has not echoed, one per tick
FLATTEN_CANCEL_TIMEOUT = timedelta(seconds=30)  # no fallback sell unless both cancels are confirmed
RECONCILE_INTERVAL = timedelta(minutes=2)
QUOTE_TIMEOUT_S = 4.0
SYNC_TIMEOUT_S = 10.0
LOOP_SECONDS = 2.0
EXEC_REQUEST_ID = 7101
SUMMARY_REQUEST_ID = 7102
QUOTE_REQUEST_BASE = 910000
PRICE_PLACES = Decimal("0.0001")
CENT = Decimal("0.01")
MANUAL_REF_PREFIX = "manual:"  # owner orders (trading/execution/manual_orders.py): never touched

RefusalCode = Literal[
    "not_paper_account", "accounts_unknown", "account_not_managed", "no_order_id", "market_closed",
    "bridge_running",
]
EventSink = Callable[[str], None]
_STDOUT_LOCK = threading.RLock()


def _loaded_engine_digest() -> str | None:
    try:
        return engine_code_digest()
    except ExecutionAuthorityError:
        return None


# The engine code as this process loaded it: start() refuses when the files changed since (C2-11).
LOADED_ENGINE_DIGEST = _loaded_engine_digest()


class ExecutionRefused(RuntimeError):
    """The engine refuses to run (or to keep running): nothing is sent."""

    def __init__(self, code: RefusalCode, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


def format_event_line(kind: str, message: str, moment: datetime) -> str:
    """One notifier event line: ``HH:MM:SS ET  <KIND> <text>``."""
    if kind not in EVENT_KINDS:
        kind, message = "INFO", f"{kind} {message}"
    text = " ".join(str(message).split())  # never let a TWS message break a line
    return f"{moment.astimezone(NY):%H:%M:%S} ET  {kind} {text}"


def stdout_sink(line: str) -> None:
    # One write per line: reader-thread callbacks must never splice a line.
    with _STDOUT_LOCK:
        sys.stdout.write(line + "\n")
        sys.stdout.flush()


def now_ny() -> datetime:
    return datetime.now(NY)


def floor_tick(price: Decimal) -> Decimal:
    return price.quantize(TICK, rounding=ROUND_DOWN)


def share_text(value: Decimal | int) -> str:
    number = Decimal(value)
    return str(int(number)) if number == number.to_integral_value() else str(number.normalize())


def strategy_family(name: str) -> str:
    upper = name.strip().upper()
    if upper.startswith("MOMO"):
        return "momo"
    if upper.startswith("ORB"):
        return "orb"
    return "other"


def parse_completed_time(value: object) -> datetime | None:
    """TWS's completedTime ('20261013 15:44:02 America/New_York'), or None when it cannot be placed in time.

    Only a named IANA zone or a US Eastern abbreviation is trusted: a time
    without a zone, or in a zone this machine cannot resolve, is unreadable and
    the caller assumes the worst.
    """
    match = _COMPLETED_TIME.match(value.strip()) if isinstance(value, str) else None
    if match is None:
        return None
    day, hour, minute, second, zone = match.groups()
    if not zone:
        return None
    if zone.upper() in EASTERN_ZONE_NAMES:
        tz = NY
    else:
        try:
            tz = ZoneInfo(zone)
        except (ZoneInfoNotFoundError, ValueError, OSError):
            return None
    try:
        return datetime.strptime(f"{day} {hour}{minute}{second}", "%Y%m%d %H%M%S").replace(tzinfo=tz)
    except ValueError:
        return None


def completed_sell_shares(status: str, total: Decimal, filled: Decimal | None) -> Decimal:
    """Shares a completed SELL may have sold (review R-2).

    Filled means the whole order executed, whatever filledQuantity says; a
    filledQuantity of 0 is unknown, never nothing sold (ibapi 9.81 decodes an
    empty field as 0.0): both count the order's whole size.
    """
    if status == "Filled" or not filled or filled <= 0:
        return max(total, filled or Decimal("0"))
    return filled


def running_bridge_processes() -> list[tuple[int, str]]:
    """Processes running the PAPER bridge script; raises when the process table cannot be read."""
    result = subprocess.run(["/usr/bin/pgrep", "-fl", BRIDGE_PROCESS_PATTERN], capture_output=True, text=True,
                            timeout=5, check=False)
    if result.returncode not in (0, 1):  # 1: nothing matched
        raise OSError(f"pgrep exited {result.returncode}")
    found: list[tuple[int, str]] = []
    for line in result.stdout.splitlines():
        pid, _, command = line.strip().partition(" ")
        if pid.isdigit() and int(pid) != os.getpid():
            found.append((int(pid), command))
    return found


def bridge_database() -> Path:
    """The database the PAPER bridge writes its heartbeat into, whatever database the engine uses.

    The bridge's ledger is the canonical one under the user's home
    (``paper_bridge.DB``); it writes no heartbeat into any other.
    """
    return UserPaths.current().database_file


async def latest_bridge_heartbeat(database: Path) -> datetime | None:
    """When the bridge last wrote its heartbeat (migration 12 table), read-only; None if it never did.

    A missing database or table means no bridge ever ran with it. Anything
    else unreadable raises: the interlock then counts the bridge as running.
    """
    if not database.is_file():
        return None
    async with aiosqlite.connect(f"{database.resolve().as_uri()}?mode=ro", uri=True) as db:
        async with db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='autotrader_bridge_heartbeat'"
        ) as cursor:
            if await cursor.fetchone() is None:
                return None
        async with db.execute("SELECT updated_at FROM autotrader_bridge_heartbeat WHERE id=1") as cursor:
            row = await cursor.fetchone()
    if row is None:
        return None
    stamp = datetime.fromisoformat(str(row[0]))
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError("bridge heartbeat time has no time zone")
    return stamp


def parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


# ---- session checkpoints ---------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SessionTimes:
    """Every session checkpoint, measured back from the real XNYS close."""

    closes_at: datetime
    last_entry: datetime
    flatten: datetime
    flatten_alert: datetime
    verify: datetime
    stop: datetime
    final_alert: datetime
    calendar_ok: bool = True

    @classmethod
    def closing_at(cls, closes_at: datetime, *, calendar_ok: bool = True) -> "SessionTimes":
        return cls(
            closes_at=closes_at,
            last_entry=closes_at - LAST_ENTRY_BEFORE_CLOSE,
            flatten=closes_at - FLATTEN_BEFORE_CLOSE,
            flatten_alert=closes_at - FLATTEN_ALERT_BEFORE_CLOSE,
            verify=closes_at - VERIFY_BEFORE_CLOSE,
            stop=closes_at - STOP_BEFORE_CLOSE,
            final_alert=closes_at - FINAL_ALERT_BEFORE_CLOSE,
            calendar_ok=calendar_ok,
        )


def session_times(session_date: date) -> SessionTimes:
    """The date's checkpoints from ``market_session`` (13:00 close on a half day).

    A closed date refuses the session. An unusable calendar falls back to the
    regular 16:00 close so open positions are still flattened, but blocks new
    entries (``calendar_ok`` is False).
    """
    regular = datetime.combine(session_date, REGULAR_CLOSE, tzinfo=NY)
    try:
        from trading.scheduler.sessions import market_session

        session = market_session(session_date)
    except Exception:
        return SessionTimes.closing_at(regular, calendar_ok=False)
    if session is None:
        raise ExecutionRefused("market_closed", f"{session_date} is not an XNYS session")
    return SessionTimes.closing_at(session.closes_at.astimezone(NY))


# ---- decisions ----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DecisionRow:
    decision_id: str
    run_id: str
    symbol: str
    con_id: int
    primary_exchange: str
    payload_json: str
    created_at: str
    strategy_name: str


@dataclass(frozen=True, slots=True)
class DecisionIntent:
    quantity: int
    entry: Decimal
    stop: Decimal
    target: Decimal
    expires_at: datetime | None
    setup_fingerprint: str


def parse_intent(payload_json: str) -> DecisionIntent | None:
    """The risk-approved trade intent of a decision payload; None when there is none."""
    payload = json.loads(payload_json)
    risk = payload.get("risk") if isinstance(payload, dict) else None
    intent = risk.get("intent") if isinstance(risk, dict) else None
    if not isinstance(intent, dict) or not intent:
        return None
    quantity = Decimal(str(intent["quantity"]))
    if not quantity.is_finite() or quantity != quantity.to_integral_value():
        raise ValueError("trade intent quantity is not a whole number")
    raw_expiry = intent.get("expires_at")
    expires_at = parse_time(raw_expiry)
    if raw_expiry is not None and expires_at is None:
        raise ValueError("trade intent expiry is unreadable")
    fingerprint = (
        intent.get("setup_fingerprint") or risk.get("setup_fingerprint") or payload.get("setup_fingerprint") or ""
    )
    return DecisionIntent(
        quantity=int(quantity), entry=Decimal(str(intent["entry_limit"])), stop=Decimal(str(intent["stop_price"])),
        target=Decimal(str(intent["target_price"])), expires_at=expires_at, setup_fingerprint=str(fingerprint),
    )


_DECISIONS_SQL = """
    SELECT d.id, d.run_id, c.symbol, c.con_id, c.contract_json, d.payload_json, d.created_at, v.spec_json
    FROM autotrader_candidate_decisions d
    JOIN autotrader_runs r ON r.id = d.run_id
    JOIN autotrader_candidates c ON c.id = d.candidate_id
    LEFT JOIN autotrader_strategy_versions v
      ON v.strategy_id = r.strategy_id AND v.version = r.strategy_version
    WHERE d.outcome = 'shadow_accepted' AND r.session_date = ?
    ORDER BY d.created_at, d.id
"""


def _json_text(value: object, key: str) -> str:
    try:
        document = json.loads(str(value))
    except (TypeError, ValueError):
        return ""
    found = document.get(key) if isinstance(document, dict) else None
    return found.strip() if isinstance(found, str) else ""


async def accepted_decisions(session_date: date) -> list[DecisionRow]:
    """Approved decisions of one New York session; the strategy name comes from its version, never a label."""
    async with get_db() as db:
        async with db.execute(_DECISIONS_SQL, (session_date.isoformat(),)) as cursor:
            rows = await cursor.fetchall()
    return [
        DecisionRow(
            decision_id=str(row[0]), run_id=str(row[1]), symbol=str(row[2]), con_id=int(row[3]),
            primary_exchange=_json_text(row[4], "primary_exchange"), payload_json=str(row[5]),
            created_at=str(row[6]), strategy_name=_json_text(row[7], "name"),
        )
        for row in rows
    ]


async def working_manual_symbols() -> frozenset[str]:
    """Symbols of owner manual orders that may still reach or sit at IBKR ("*" while a close-all works)."""
    from trading.execution import manual_orders

    symbols: set[str] = set()
    for row in await manual_orders.list_active_orders():
        if row.action == "CLOSE_ALL":
            symbols.add("*")
        elif row.symbol:
            symbols.add(manual_orders.normalize_symbol(row.symbol))
    return frozenset(symbols)


async def screener_quote(session_date: date, symbol: str, con_id: int) -> Decimal | None:
    """Newest same-session screener row for the symbol: the lower of bid and last."""
    async with get_db() as db:
        async with db.execute(
            "SELECT payload_json FROM autotrader_screener_snapshots WHERE session_date=?"
            " ORDER BY observed_at DESC, id DESC LIMIT 10",
            (session_date.isoformat(),),
        ) as cursor:
            snapshots = await cursor.fetchall()
    for (payload_json,) in snapshots:
        try:
            payload = json.loads(payload_json)
        except (TypeError, ValueError):
            continue
        rows = payload.get("rows") if isinstance(payload, dict) else None
        for row in rows if isinstance(rows, list) else ():
            if not isinstance(row, dict):
                continue
            if row.get("symbol") != symbol and not (con_id > 0 and row.get("con_id") == con_id):
                continue
            prices = [price for price in (decimal_or_none(row.get("bid")), decimal_or_none(row.get("last")))
                      if price is not None and price > 0]
            if prices:
                return min(prices)
    return None


# ---- exit planning (pure; ported verbatim from the reviewed bridge) -------------------


@dataclass(frozen=True, slots=True)
class FillSnapshot:
    """Everything an exit quantity depends on, read under one lock."""

    parent: int
    target: int
    stop: int
    recorded_exits: int  # the bracket's persisted exit fills (never decrease)
    position: Decimal | None  # account position in the contract; None = unknown


@dataclass(frozen=True, slots=True)
class ExitPlan:
    total: int = 0  # totalQuantity of the modification; IBKR reads it as the order total
    working: int = 0  # shares the modification leaves to sell: total - this leg's fills
    capped_from: int | None = None  # working before the account-position cap bound
    problem: str = ""


@dataclass(frozen=True, slots=True)
class ExitResult:
    outcome: str  # sent | unchanged | pending | nothing | blocked
    working: int = 0
    capped: bool = False


@dataclass(slots=True)
class PendingModify:
    """An exit modification TWS has not echoed back through openOrder yet."""

    bracket_id: str
    leg: LegCode
    purpose: str  # resize | flatten
    kind: str  # LMT | STP
    total: int
    price: Decimal
    sends: int = 1
    exhausted: bool = False
    reserved: int = 0  # shares of other sells the first send left alone; every re-send leaves them too
    restored: bool = False  # sent before a restart: confirmed by its price, never re-sent blindly


def plan_exit(snapshot: FillSnapshot, leg: LegCode) -> ExitPlan:
    """Total for a modification of one exit leg (the oversell rule).

    The target's total is entry fills - stop fills and the stop's is entry fills
    - target fills, so a leg's own fills never feed its total: a target fill
    that lands after the snapshot cannot inflate the target's total. Exit fills
    the snapshot cannot attribute to a leg are charged to the other leg, which
    can only make this leg's total too small.
    """
    own, other = (snapshot.target, snapshot.stop) if leg == "T" else (snapshot.stop, snapshot.target)
    exits = max(snapshot.recorded_exits, snapshot.target + snapshot.stop)
    if exits > snapshot.parent:
        return ExitPlan(problem=f"exit fills {exits} exceed entry fills {snapshot.parent}")
    unattributed = exits - snapshot.target - snapshot.stop
    total = snapshot.parent - other - unattributed
    if not own <= total <= snapshot.parent:  # tripwire: unreachable unless the rule above breaks
        return ExitPlan(problem=f"exit total {total} inconsistent with entry fills {snapshot.parent}")
    if snapshot.position is None:
        return ExitPlan(problem="account position unknown")
    working = total - own
    held = max(0, int(snapshot.position))  # a short or empty account has nothing to sell
    if working > held:
        return ExitPlan(total=own + held, working=held, capped_from=working)
    return ExitPlan(total=total, working=working)


# ---- IBKR order encoding ------------------------------------------------------------


def stock(con_id: int, primary_exchange: str) -> Any:
    from ibapi.contract import Contract

    contract = Contract()
    contract.conId = con_id
    contract.secType = "STK"
    contract.exchange = "SMART"
    contract.currency = "USD"
    contract.primaryExchange = primary_exchange
    return contract


def order(action: str, order_type: str, qty: int, account: str, ref: str, **fields: Any) -> Any:
    """The single order factory: placements and modifications are encoded alike [N3]."""
    from ibapi.order import Order

    result = Order()
    result.action = action
    result.orderType = order_type
    result.totalQuantity = qty
    result.account = account
    result.orderRef = ref
    result.eTradeOnly = False  # rejected by current TWS when left at the 9.81 default
    result.firmQuoteOnly = False
    for name, value in fields.items():
        setattr(result, name, value)
    return result


def leg_ticket(leg: LegRecord, account: str, *, quantity: int | None = None, price: Decimal | None = None,
               transmit: bool | None = None) -> Any:
    fields: dict[str, Any] = {"tif": leg.tif, "transmit": leg.transmit if transmit is None else transmit}
    if leg.parent_order_id is not None:
        fields["parentId"] = leg.parent_order_id
    value = leg.price if price is None else price
    if leg.order_type == "LMT" and value is not None:
        fields["lmtPrice"] = float(value)
    elif leg.order_type == "STP" and value is not None:
        fields["auxPrice"] = float(value)
    return order(leg.action, leg.order_type, leg.quantity if quantity is None else quantity, account,
                 leg.order_ref, **fields)


def execution_filter(client_id: int) -> Any:
    from ibapi.execution import ExecutionFilter

    execution_filter_ = ExecutionFilter()
    execution_filter_.clientId = client_id
    return execution_filter_


class BrokerPort(Protocol):
    """The ibapi ``EClient`` requests the engine sends (9.81 signatures)."""

    def isConnected(self) -> bool: ...
    def reqIds(self, numIds: int) -> None: ...
    def placeOrder(self, orderId: int, contract: Any, order: Any) -> None: ...
    def cancelOrder(self, orderId: int) -> None: ...
    def reqAllOpenOrders(self) -> None: ...
    def reqCompletedOrders(self, apiOnly: bool) -> None: ...
    def reqExecutions(self, reqId: int, execFilter: Any) -> None: ...
    def reqPositions(self) -> None: ...
    def cancelPositions(self) -> None: ...
    def reqAccountSummary(self, reqId: int, groupName: str, tags: str) -> None: ...
    def cancelAccountSummary(self, reqId: int) -> None: ...
    def reqMktData(self, reqId: int, contract: Any, genericTickList: str, snapshot: bool,
                   regulatorySnapshot: bool, mktDataOptions: list[Any]) -> None: ...
    def cancelMktData(self, reqId: int) -> None: ...
    def reqMarketDataType(self, marketDataType: int) -> None: ...


@dataclass(frozen=True, slots=True)
class SellFacts:
    """What one flatten may sell, read from one fresh TWS snapshot (review C2-1, C2-4)."""

    quantity: int  # min(held, position - reserved); never negative
    held: int  # the bracket's own shares per its fills
    position: Decimal  # the account position TWS reported after the order snapshots
    working: int  # open quantity of every other working sell in the contract
    unshown: int  # shares other orders sold since the entry that the position does not show yet

    @property
    def reserved(self) -> int:
        return self.working + self.unshown


@dataclass(frozen=True, slots=True)
class Finding:
    """One reconciliation mismatch; while any exists, new entries are paused."""

    code: str
    key: str
    message: str


# ---- the engine ------------------------------------------------------------------------


class LiveExecutor:
    """Manage native IBKR brackets for promoted strategies on one PAPER account."""

    def __init__(
        self,
        *,
        broker: BrokerPort,
        mirror: TwsMirror,
        ledger: ExecutionLedger,
        authority: EngineAuthority,
        account: str,
        client_id: int,
        session_date: date,
        clock: Callable[[], datetime] | None = None,
        times: SessionTimes | None = None,
        sink: EventSink | None = None,
        stop_event: threading.Event | None = None,
    ) -> None:
        if not is_paper_account(account):
            raise ExecutionRefused("not_paper_account", "the engine trades a DU paper account only")
        if authority.account != account or mirror.client_id != client_id:
            raise ExecutionRefused("account_not_managed", "authority, mirror and engine must share one identity")
        self.broker = broker
        self.mirror = mirror
        self.ledger = ledger
        self.authority = authority
        self.account = account
        self.client_id = client_id
        self.session_date = session_date
        self.book = book_id(account, session_date)
        self.clock = clock if clock is not None else now_ny
        self.sink = sink if sink is not None else stdout_sink
        self.stop_event = stop_event if stop_event is not None else threading.Event()
        self.times = times if times is not None else session_times(session_date)
        # Injectable inputs (tests replace them, as with the bridge).
        self.decisions: Callable[[], Awaitable[list[DecisionRow]]] = lambda: accepted_decisions(self.session_date)
        self.kill_switch_active: Callable[[], Awaitable[bool]] = SQLitePaperKillStore().is_stopped
        self.manual_symbols: Callable[[], Awaitable[frozenset[str]]] = working_manual_symbols
        self.fallback_quote: Callable[[BracketRecord], Awaitable[Decimal | None]] = (
            lambda bracket: screener_quote(self.session_date, bracket.symbol, bracket.con_id)
        )
        self.pause: Callable[[float], Awaitable[None]] = asyncio.sleep
        # The bridge interlock: two order senders on one account could each sell the same shares.
        self.bridge_processes: Callable[[], list[tuple[int, str]]] = running_bridge_processes
        self.bridge_database = bridge_database()
        self.bridge_heartbeat: Callable[[], Awaitable[datetime | None]] = self.read_bridge_heartbeat
        self.quote_timeout = QUOTE_TIMEOUT_S
        self.sync_timeout = SYNC_TIMEOUT_S
        self.loop_seconds = LOOP_SECONDS
        self.poll_seconds = 0.01
        # Ledger-backed state (loaded by start()).
        self.brackets: dict[str, BracketRecord] = {}
        self.processed: set[str] = set()
        self.leg_by_ref: dict[str, LegRecord] = {}
        self.leg_by_order: dict[int, LegRecord] = {}
        self.fills: dict[str, FillRecord] = {}  # execId base -> newest correction
        self.persisted: dict[str, bool] = {}  # execId -> stored (True once its commission is stored too)
        self.saved: dict[str, tuple[object, ...]] = {}
        self.unsettled: set[str] = set()  # sent brackets whose outbox row could not be closed yet
        # In-memory supervision state (re-derived after a restart).
        self.pending_modifies: dict[int, PendingModify] = {}
        self.error_marks: dict[int, int] = {}
        self.position_gaps: dict[str, datetime] = {}
        self.unshown_since: dict[str, datetime] = {}  # bracket -> first seen with sales the position lacks
        # Flattens whose first sell decision had no fresh snapshot: tried again every tick (R-1).
        self.flatten_waiting: dict[str, Decimal] = {}  # bracket -> discount
        # Perm ids of the orders in a bracket's contract TWS reported completed before its entry (R-3).
        self.entry_baselines: dict[str, frozenset[int]] = {}
        self.dangling_since: dict[int, datetime] = {}
        self.cancel_requested_at: dict[str, datetime] = {}
        self.unmanaged: dict[int, tuple[str, int]] = {}
        self.findings: dict[str, Finding] = {}
        self.alerted: set[tuple[str, ...]] = set()
        self.intake_failures: dict[str, int] = {}
        self.events: deque[tuple[str, dict[str, object], str | None, str | None]] = deque()
        self.last_reconcile: datetime | None = None
        self.flattened = False
        self.alerts_done: set[str] = set()
        self.alert_raised = False
        self.quote_req = QUOTE_REQUEST_BASE
        self.started = False
        self.mirror.alert = self.emit

    # ---- plumbing ---------------------------------------------------------------
    def now(self) -> datetime:
        return self.clock().astimezone(NY)

    def past(self, moment: datetime) -> bool:
        return self.now() >= moment

    def emit(self, kind: str, message: str) -> None:
        line = format_event_line(kind, message, self.now())
        self.sink(line)
        if kind == "CRITICAL":
            self.events.append(("alert", {"line": line}, None, None))

    def alert_once(self, key: tuple[str, ...], message: str, kind: str = "CRITICAL") -> None:
        if key not in self.alerted:
            self.alerted.add(key)
            self.emit(kind, message)

    def queue_event(self, kind: str, payload: dict[str, object], bracket: BracketRecord | None = None,
                    leg: LegRecord | None = None) -> None:
        self.events.append((kind, payload, None if bracket is None else bracket.id, None if leg is None else leg.id))

    async def flush_events(self) -> None:
        """The audit trail is best effort: it never delays order management."""
        while self.events:
            kind, payload, bracket_id, leg_id = self.events[0]
            try:
                await self.ledger.append_event(book=self.book, kind=kind, payload=payload, bracket_id=bracket_id,
                                               leg_id=leg_id)
            except Exception as exc:
                self.alert_once(("ledger", "events"), f"order events not saved to the execution ledger"
                                f" ({type(exc).__name__}: {exc}) — retried every tick; orders are still managed")
                return
            self.events.popleft()

    async def wait_for(self, event: threading.Event, timeout: float | None = None) -> bool:
        deadline = time.monotonic() + (self.sync_timeout if timeout is None else timeout)
        while not event.is_set():
            if time.monotonic() >= deadline:
                return False
            await asyncio.sleep(self.poll_seconds)
        return True

    @property
    def net_liq(self) -> Decimal | None:
        return self.mirror.net_liquidation(self.account)

    def entry_deadline(self, family: str) -> datetime:
        """A strategy's last entry moment; never later than 30 min before the real close."""
        cutoff = STRATEGY_CUTOFFS.get(family)
        last = self.times.last_entry
        return last if cutoff is None else min(last, datetime.combine(self.session_date, cutoff, tzinfo=NY))

    def account_position(self, con_id: int) -> Decimal | None:
        return self.mirror.account_position(self.account, con_id)

    # ---- ledger state --------------------------------------------------------------
    def index(self, bracket: BracketRecord) -> None:
        self.brackets[bracket.id] = bracket
        for leg in bracket.legs.values():
            self.leg_by_ref[leg.order_ref] = leg
            self.leg_by_order[leg.order_id] = leg
        with self.mirror.lock:  # rejections of restored legs must be CRITICAL too [L1]
            self.mirror.own_order_ids.update(leg.order_id for leg in bracket.legs.values())

    @staticmethod
    def signature(bracket: BracketRecord) -> tuple[object, ...]:
        legs = tuple(
            (leg.leg, leg.broker_status, leg.perm_id, leg.filled, leg.working_quantity, leg.working_price)
            for leg in sorted(bracket.legs.values(), key=lambda item: item.leg)
        )
        return (bracket.state, bracket.entry_filled, bracket.avg_entry, bracket.exit_filled, bracket.avg_exit,
                bracket.exit_reason, bracket.realized_pnl, bracket.commissions, bracket.parent_cancel_requested,
                bracket.resized_qty, bracket.flatten_attempted, bracket.flatten_state, bracket.flatten_price,
                bracket.attention_reason, bracket.attention_cleared_at, legs)

    async def load(self) -> None:
        self.brackets, self.leg_by_ref, self.leg_by_order = {}, {}, {}
        for bracket in await self.ledger.load_brackets(self.account, self.session_date):
            self.index(bracket)
            self.saved[bracket.id] = self.signature(bracket)
        self.processed = set(await self.ledger.processed_decision_ids(self.account, self.session_date))
        for event in await self.ledger.events(book=self.book):
            perms = event.payload.get("completed_before_entry") if event.kind == "claimed" else None
            if event.bracket_id in self.brackets and isinstance(perms, list):
                self.entry_baselines[event.bracket_id] = frozenset(
                    perm for perm in perms if isinstance(perm, int) and not isinstance(perm, bool) and perm > 0
                )
        legs = [leg.id for bracket in self.brackets.values() for leg in bracket.legs.values()]
        for fill in await self.ledger.load_fills(legs):
            current = self.fills.get(fill.exec_base)
            if current is None or fill.exec_version > current.exec_version:
                self.fills[fill.exec_base] = fill
            self.persisted[fill.exec_id] = fill.commission is not None
        for bracket in self.brackets.values():
            target = bracket.legs.get("T")
            if bracket.flatten_state == "repricing" and target is not None and bracket.flatten_price is not None:
                # A reprice sent before a restart keeps being confirmed (same order, same price).
                self.pending_modifies[target.order_id] = PendingModify(
                    bracket_id=bracket.id, leg="T", purpose="flatten", kind="LMT",
                    total=target.working_quantity or bracket.entry_filled, price=bracket.flatten_price,
                    restored=True,
                )

    async def persist(self, bracket: BracketRecord) -> bool:
        if bracket.state == "sending":
            return True  # only stage_bracket / mark_sent / mark_unknown write a sending bracket
        signature = self.signature(bracket)
        if self.saved.get(bracket.id) == signature:
            return True
        try:
            await self.ledger.save_bracket(bracket)
        except Exception as exc:
            self.alert_once(("ledger", "save", bracket.id), f"{bracket.symbol}: bracket state not saved to the"
                            f" execution ledger ({type(exc).__name__}: {exc}) — retried every tick")
            return False
        self.saved[bracket.id] = signature
        return True

    async def save(self) -> None:
        for bracket_id in list(self.unsettled):
            bracket = self.brackets[bracket_id]
            try:
                await self.ledger.mark_sent(bracket, payload={"settled_late": True})
            except Exception:
                continue
            self.unsettled.discard(bracket_id)
        for bracket in list(self.brackets.values()):
            await self.persist(bracket)
        await self.flush_events()

    # ---- start, reconnect, paper check ------------------------------------------------
    def require_paper_accounts(self) -> None:
        """Every managed account must be a paper (DU) account and ours must be among them."""
        with self.mirror.lock:
            accounts = list(self.mirror.accounts)
        if not accounts:
            raise ExecutionRefused("accounts_unknown", "TWS reported no managed accounts")
        foreign = sum(1 for account in accounts if not is_paper_account(account))
        if foreign:
            raise ExecutionRefused("not_paper_account", f"TWS manages {foreign} non-paper account(s)")
        if self.account not in accounts:
            raise ExecutionRefused("account_not_managed", "the configured paper account is not managed by this TWS")

    def require_reviewed_code(self) -> None:
        """A grant binds one engine build: this process must run exactly that code (review C2-11)."""
        on_disk = engine_code_digest()
        if LOADED_ENGINE_DIGEST != on_disk:
            raise ExecutionAuthorityError("code_mismatch", "the engine's source files changed after this process"
                                          " loaded them — restart it from the reviewed code")
        if self.authority.code_digest != on_disk:
            raise ExecutionAuthorityError("code_mismatch", f"the authority names engine code"
                                          f" {self.authority.code_digest[:12]}, this process runs {on_disk[:12]}")

    async def read_bridge_heartbeat(self) -> datetime | None:
        """The bridge's heartbeat from the bridge's own database (review R-5), never the engine's."""
        return await latest_bridge_heartbeat(self.bridge_database)

    async def bridge_problem(self) -> str | None:
        """Why the PAPER bridge may be running, or None; anything unreadable counts as running."""
        try:
            processes = self.bridge_processes()
        except Exception as exc:
            return f"the process table is unreadable ({type(exc).__name__}: {exc})"
        if processes:
            return f"the PAPER bridge is running (pid {processes[0][0]})"
        try:
            beat = await self.bridge_heartbeat()
        except Exception as exc:
            return f"the bridge heartbeat is unreadable ({type(exc).__name__}: {exc})"
        if beat is not None and self.now() - beat < BRIDGE_HEARTBEAT_WINDOW:
            age = max(0, int((self.now() - beat).total_seconds()))
            return f"the PAPER bridge wrote its heartbeat {age}s ago"
        return None

    async def start(self) -> None:
        """Authority for this code, no bridge, paper check, fresh order ids, then a full reconciliation."""
        active = await self.authority.require_start()
        self.require_reviewed_code()
        bridge = await self.bridge_problem()
        if bridge is not None:  # two order senders on one account could each sell the same shares
            raise ExecutionRefused("bridge_running", f"{bridge} — the engine never runs next to the bridge")
        if not await self.wait_for(self.mirror.accounts_event):
            raise ExecutionRefused("accounts_unknown", "TWS reported no managed accounts")
        self.require_paper_accounts()
        await self.refresh_order_ids()
        await self.load()
        self.request_net_liquidation()
        self.broker.reqMarketDataType(1)
        await self.reconcile(startup=True)
        await self.flush_events()
        self.started = True
        self.emit("INFO", f"execution engine {ENGINE_VERSION} started for {self.session_date}"
                  f" ({len(self.brackets)} brackets, promoted: {', '.join(sorted(active))})")
        if not self.times.calendar_ok:
            self.emit("CRITICAL", "market calendar unavailable — assuming the regular 16:00 close; new entries"
                      " blocked, open positions are still managed")

    async def handle_reconnect(self) -> None:
        """After TWS reconnects: paper check, reqIds and a full reconciliation before any entry."""
        self.started = False
        if not await self.wait_for(self.mirror.accounts_event):
            raise ExecutionRefused("accounts_unknown", "TWS reported no managed accounts after the reconnect")
        self.require_paper_accounts()
        await self.refresh_order_ids()
        self.request_net_liquidation()
        await self.reconcile(startup=True)
        await self.flush_events()
        self.started = True
        self.emit("INFO", "TWS reconnected: paper accounts verified, order ids refreshed, reconciliation done")

    async def refresh_order_ids(self) -> None:
        self.mirror.ready.clear()
        self.broker.reqIds(-1)
        if not await self.wait_for(self.mirror.ready):
            raise ExecutionRefused("no_order_id", "TWS did not report a next valid order id")

    def request_net_liquidation(self) -> None:
        self.mirror.summary_done.clear()
        self.broker.reqAccountSummary(SUMMARY_REQUEST_ID, "All", "NetLiquidation")

    async def allocate_order_ids(self, count: int) -> tuple[int, ...]:
        """Ids above TWS's next valid id, the ledger's highest and every id TWS reports for this client [L-c].

        TWS can reset its sequence while ids are still in use (an earlier
        session's GTC leg, an order of this client id the ledger does not know):
        a placeOrder on such an id would modify that order instead.
        """
        ledger_next = await self.ledger.max_order_id(self.client_id) + 1
        with self.mirror.lock:
            if self.mirror.next_id is None:
                raise ExecutionRefused("no_order_id", "TWS has not provided a valid order id")
            reported = self.mirror.next_id
            in_use = max([0, *self.mirror.open_orders, *self.mirror.order_status, *self.unmanaged,
                          *(row.order_id for row in self.mirror.execs.values())])
            first = max(reported, ledger_next, in_use + 1)
            self.mirror.next_id = first + count
            ids = tuple(range(first, first + count))
            self.mirror.own_order_ids.update(ids)
        if in_use >= max(reported, ledger_next):
            self.emit("INFO", f"next order id raised from {max(reported, ledger_next)} to {first}: ids up to"
                      f" {in_use} are in use at TWS")
        return ids

    # ---- fills ----------------------------------------------------------------------
    def match_execution(self, row: ExecutionRow) -> LegRecord | None:
        """The engine leg an execution belongs to [L5]; never the owner's or another account's fill."""
        if row.account and row.account != self.account:
            return None
        if row.order_ref:
            leg = self.leg_by_ref.get(row.order_ref)
            if leg is None:
                return None  # manual:, the owner's TWS orders, or a stray the reconciliation judges
            if row.order_id and row.order_id != leg.order_id:
                self.alert_once(("exec-order-mismatch", row.exec_id),
                                f"execution {row.exec_id} carries {row.order_ref} but order {row.order_id}, not"
                                f" {leg.order_id} — not counted; check TWS")
                return None
            return leg
        # A field TWS left empty cannot prove the execution is someone else's; the order id alone
        # may only claim it on or after the bracket's session (ids reset across TWS restarts).
        leg = self.leg_by_order.get(row.order_id)
        if leg is None:
            return None
        bracket = self.brackets[leg.bracket_id]
        if row.day is not None and row.day < bracket.session_date:
            return None
        return leg

    async def sync_fills(self) -> None:
        """Mirror executions and commission reports into memory and the ledger (deduplicated by execId)."""
        pending: list[FillRecord] = []
        for row in self.mirror.executions():
            leg = self.match_execution(row)
            if leg is None:
                continue
            commission = self.mirror.commission_for(row.exec_id)
            record = FillRecord(
                exec_id=row.exec_id, exec_base=row.base, exec_version=row.version, leg_id=leg.id,
                order_id=row.order_id, side=row.side or None, shares=row.shares, price=row.price,
                account=row.account or None, exec_time=row.exec_time or None,
                commission=None if commission is None else commission[0],
                commission_currency=None if commission is None else commission[1],
            )
            current = self.fills.get(row.base)
            if current is None or record.exec_version > current.exec_version or (
                record.exec_version == current.exec_version and current.commission is None
                and record.commission is not None
            ):
                self.fills[row.base] = record
            stored = self.persisted.get(record.exec_id)
            if stored is None or (not stored and record.commission is not None):
                pending.append(record)
        if not pending:
            return
        try:
            result = await self.ledger.record_fills(pending)
        except Exception as exc:
            self.alert_once(("ledger", "fills"), f"fills not saved to the execution ledger ({type(exc).__name__}:"
                            f" {exc}) — retried every tick; orders are still managed")
            return
        conflicts = set(result.conflicts)
        for record in pending:
            if record.exec_id in conflicts:
                self.alert_once(("fill-conflict", record.exec_id), f"execution {record.exec_id} was reported"
                                " twice with different shares or price — the first report is kept; check TWS")
                self.persisted[record.exec_id] = True
            else:
                self.persisted[record.exec_id] = record.commission is not None
            if record.exec_id in result.inserted:
                self.queue_event("fill", {"exec_id": record.exec_id, "order_id": record.order_id,
                                          "shares": record.shares, "price": str(record.price)})

    def leg_fills(self, bracket: BracketRecord, code: LegCode) -> tuple[int, Decimal, Decimal]:
        """(shares, value, commission) of a leg's effective fills; never above what IBKR can fill."""
        leg = bracket.legs.get(code)
        if leg is None:
            return 0, Decimal("0"), Decimal("0")
        rows = [fill for fill in self.fills.values() if fill.leg_id == leg.id]
        shares = sum(fill.shares for fill in rows)
        value = sum((Decimal(fill.shares) * fill.price for fill in rows), Decimal("0"))
        commission = sum((fill.commission or Decimal("0") for fill in rows), Decimal("0"))
        cap = leg.quantity if code == "F" else bracket.quantity
        if shares > cap > 0:  # IBKR cannot fill more than the order's quantity [L5]
            self.alert_once(
                (bracket.id, "overfill", str(leg.order_id)),
                f"{bracket.symbol}: executions for order {leg.order_id} add up to {shares} sh, more than its"
                f" {cap} — counted as {cap}; check TWS",
            )
            value = value * cap / shares
            shares = cap
        return shares, value, commission

    def observe_legs(self) -> None:
        """Copy IBKR's view of every leg (status, permId, echoed size/price) onto the records."""
        with self.mirror.lock:
            statuses = dict(self.mirror.order_status)
            perms = dict(self.mirror.perm_ids)
            reported = dict(self.mirror.order_objects)
            completed = {row.order_ref: row.status for row in self.mirror.completed.values() if row.order_ref}
        for bracket in self.brackets.values():
            for leg in bracket.legs.values():
                status = statuses.get(leg.order_id) or completed.get(leg.order_ref)
                if status is not None and status != leg.broker_status:
                    self.queue_event("status", {"order_id": leg.order_id, "from": leg.broker_status, "to": status},
                                     bracket, leg)
                    leg.broker_status = status
                perm = perms.get(leg.order_id)
                if perm and leg.perm_id is None:
                    leg.perm_id = perm
                echo = reported.get(leg.order_id)
                if echo is not None:
                    leg.working_quantity = whole(getattr(echo, "totalQuantity", 0))
                    price = decimal_or_none(getattr(echo, "auxPrice" if leg.order_type == "STP" else "lmtPrice",
                                                    None))
                    leg.working_price = price if leg.order_type != "MKT" else None
                leg.filled = max(leg.filled, self.leg_fills(bracket, leg.leg)[0])

    # ---- bracket lifecycle (ported) ------------------------------------------------------
    def refresh(self, bracket: BracketRecord) -> None:
        if bracket.state in ("sending", "unknown"):
            return
        filled, value, entry_commission = self.leg_fills(bracket, "P")
        if filled > bracket.entry_filled:  # fills never decrease: executions can be missing after a restart
            bracket.entry_filled = filled
            bracket.avg_entry = (value / filled).quantize(PRICE_PLACES)
            self.emit("FILL", f"{bracket.symbol}: {filled}/{bracket.quantity} sh @ {bracket.avg_entry}"
                      f" ({bracket.strategy_name})")
        exit_qty, exit_value, exit_commission = 0, Decimal("0"), Decimal("0")
        exit_reason = bracket.exit_reason
        for code in EXIT_LEGS:
            quantity, leg_value, commission = self.leg_fills(bracket, code)
            exit_commission += commission
            if quantity > 0:
                exit_qty += quantity
                exit_value += leg_value
                repriced = bracket.flatten_state in ("repricing", "repriced") and code == "T"
                exit_reason = "flatten" if code == "F" or repriced else LEG_NAMES[code]
        # Commission reports may trail their fill (or vanish with a TWS restart): costs never decrease.
        bracket.commissions = max(bracket.commissions, entry_commission + exit_commission)
        exited = exit_qty > bracket.exit_filled
        if exited:
            bracket.exit_filled = exit_qty
            bracket.avg_exit = (exit_value / exit_qty).quantize(PRICE_PLACES)
            bracket.exit_reason = exit_reason
        if bracket.exit_filled > 0 and bracket.avg_exit is not None and bracket.avg_entry is not None:
            gross = (bracket.avg_exit - bracket.avg_entry) * bracket.exit_filled
            bracket.realized_pnl = (gross - bracket.commissions).quantize(CENT)  # feeds the daily budget
        if exited:
            self.emit("EXIT", f"{bracket.symbol}: {exit_qty} sh @ {bracket.avg_exit} via {bracket.exit_reason}"
                      f" | P&L {bracket.realized_pnl}")
        if bracket.state != "needs_attention":  # sticky: only the owner resolves it
            self.advance(bracket)

    def advance(self, bracket: BracketRecord) -> None:
        if bracket.exit_filled > bracket.entry_filled:
            self.mark_attention(
                bracket, f"exit fills {bracket.exit_filled} exceed entry fills {bracket.entry_filled}",
                message=f"{bracket.symbol}: exits {bracket.exit_filled} sh exceed entry fills"
                        f" {bracket.entry_filled} sh — the account may be short {-bracket.net_held} sh; act in TWS",
            )
            return
        parent_status = self.mirror.status(bracket.order_id("P"))
        if bracket.entry_filled > 0:
            if bracket.exit_filled >= bracket.entry_filled:
                if bracket.state != "closed":
                    bracket.state = "closed"
                    self.emit("CLOSED", f"{bracket.symbol}: P&L {bracket.realized_pnl} ({bracket.exit_reason})")
                self.cancel_working_entry(bracket, parent_status)
            elif bracket.state == "closed":
                bracket.state = "open"
                self.emit("CRITICAL", f"{bracket.symbol}: entry filled again after the bracket closed"
                          f" ({bracket.remaining} sh) — check TWS")
            elif bracket.state in ("working", "cancelled"):
                bracket.state = "open"  # a fill that raced the cancel is still a position
        elif parent_status in PARENT_CANCELLED_STATUSES and bracket.state == "working":
            bracket.state = "cancelled"
            self.emit("CANCELLED", f"{bracket.symbol}: entry never filled")

    def mark_attention(self, bracket: BracketRecord, reason: str, *, message: str | None = None) -> None:
        if bracket.state == "needs_attention":
            return
        bracket.state = "needs_attention"
        bracket.attention_reason = reason[:300]
        self.queue_event("needs_attention", {"reason": reason}, bracket)
        self.emit("CRITICAL", message or f"{bracket.symbol}: {reason} — act in TWS")

    def cancel_leg(self, bracket: BracketRecord, code: LegCode, why: str) -> bool:
        """Cancel one of this bracket's own legs; never an order the engine does not own."""
        leg = bracket.legs.get(code)
        if leg is None:
            return False
        with self.mirror.lock:
            reported_ref = self.mirror.order_refs.get(leg.order_id)
        if reported_ref and reported_ref != leg.order_ref:
            self.alert_once((bracket.id, code, "foreign-ref"), f"{bracket.symbol}: order {leg.order_id} at IBKR"
                            f" carries {reported_ref}, not {leg.order_ref} — not cancelled; act in TWS")
            return False
        self.broker.cancelOrder(leg.order_id)
        self.queue_event("cancel_sent", {"order_id": leg.order_id, "why": why}, bracket, leg)
        return True

    def cancel_working_entry(self, bracket: BracketRecord, parent_status: str | None) -> None:
        """Exits closed the position while part of the entry still works: stop it."""
        if (bracket.parent_cancel_requested or bracket.entry_filled >= bracket.quantity
                or parent_status not in LIVE_ORDER_STATUSES):
            return
        if self.cancel_leg(bracket, "P", "position closed while the entry was working"):
            bracket.parent_cancel_requested = True
            self.emit("CANCEL", f"entry {bracket.symbol}: position closed while the entry was working"
                      f" (filled {bracket.entry_filled}/{bracket.quantity})")

    def expire_entries(self) -> None:
        now = self.now()
        for bracket in list(self.brackets.values()):
            if bracket.state not in ("working", "open") or bracket.parent_cancel_requested:
                continue
            if bracket.entry_filled >= bracket.quantity or not self.mirror.live(bracket.order_id("P")):
                continue
            expired = now - bracket.created_at >= ENTRY_TTL
            closed_window = now >= self.entry_deadline(strategy_family(bracket.strategy_name))
            if (expired or closed_window) and self.cancel_leg(bracket, "P", "entry expired"):
                bracket.parent_cancel_requested = True
                self.emit("CANCEL", f"entry {bracket.symbol}: {'not filled in 10 min' if expired else 'window closed'}"
                          f" (filled {bracket.entry_filled}/{bracket.quantity})")

    def resend_lost_cancels(self) -> None:
        """9.81 drops a cancelOrder sent while disconnected without an error: send it again [L3]."""
        for bracket in self.brackets.values():
            if bracket.parent_cancel_requested and self.mirror.live(bracket.order_id("P")):
                bracket.parent_cancel_requested = False
                self.emit("INFO", f"entry {bracket.symbol}: still working at IBKR after an earlier cancel request —"
                          " the cancel will be re-sent")

    # ---- exit modifications (the only path that changes an exit leg) -------------------
    def exit_order(self, bracket: BracketRecord, code: LegCode, *, total: int, price: Decimal) -> Any | None:
        """The modification, encoded exactly like the placement TWS accepted [N3].

        Built by the same factory as the placement; from the order TWS last
        reported only the OCA link (ocaGroup, ocaType, parentId) is copied,
        after checking the order still has the shape the engine sent.
        """
        leg = bracket.legs[code]
        modified = leg_ticket(leg, self.account, quantity=total, price=price, transmit=True)
        with self.mirror.lock:
            cached = self.mirror.order_objects.get(leg.order_id)
        if cached is None:
            self.emit("INFO", f"{bracket.symbol}: no openOrder from TWS for {LEG_NAMES[code]} {leg.order_id} yet —"
                      " modification sent without its OCA fields")
            return modified
        action = str(getattr(cached, "action", "") or "")
        order_type = str(getattr(cached, "orderType", "") or "")
        account = str(getattr(cached, "account", "") or "")
        parent_id = whole(getattr(cached, "parentId", 0) or 0)
        ref = str(getattr(cached, "orderRef", "") or "")
        if (action != leg.action or order_type != leg.order_type or account not in {"", self.account}
                or parent_id not in {0, leg.parent_order_id} or ref not in {"", leg.order_ref}):
            self.alert_once(
                (bracket.id, code, "foreign-shape"),
                f"{bracket.symbol}: {LEG_NAMES[code]} {leg.order_id} at IBKR is {action or '?'} {order_type or '?'},"
                f" not the engine's {leg.action} {leg.order_type} — not modified; act in TWS",
            )
            return None
        modified.ocaGroup = str(getattr(cached, "ocaGroup", "") or "")
        modified.ocaType = whole(getattr(cached, "ocaType", 0) or 0)
        modified.parentId = parent_id or leg.parent_order_id
        return modified

    def shows_price(self, order_id: int, price: Decimal, kind: str) -> bool:
        """True once TWS has echoed this price for the order through openOrder."""
        with self.mirror.lock:
            cached = self.mirror.order_objects.get(order_id)
        if cached is None:
            return False
        try:
            shown = Decimal(str(getattr(cached, "lmtPrice" if kind == "LMT" else "auxPrice")))
        except (InvalidOperation, TypeError, ValueError):
            return False
        return shown.is_finite() and abs(shown - price) < TICK / 2

    def order_shows(self, order_id: int, total: int, price: Decimal, kind: str) -> bool:
        """True once TWS has echoed this quantity and price for the order through openOrder."""
        with self.mirror.lock:
            cached = self.mirror.order_objects.get(order_id)
        if cached is None or whole(getattr(cached, "totalQuantity", 0)) != total:
            return False
        return self.shows_price(order_id, price, kind)

    def accepted(self, order_id: int, total: int, price: Decimal, kind: str) -> bool:
        """TWS shows these values and reported no error for the order since we last modified it."""
        with self.mirror.lock:
            errors = self.mirror.order_errors.get(order_id, 0)
        if errors > self.error_marks.get(order_id, errors):
            return False  # rejected: an echo of either version proves nothing
        return self.order_shows(order_id, total, price, kind)

    def fill_snapshot(self, bracket: BracketRecord) -> FillSnapshot:
        """One locked read of fills, order statuses and the account position [H1]."""
        with self.mirror.lock:
            live_rows = list(self.mirror.execs.values())
            order_filled = dict(self.mirror.order_filled)
            position = self.mirror.account_position(self.account, bracket.con_id)

            def leg(code: LegCode) -> int:
                record = bracket.legs.get(code)
                if record is None:
                    return 0
                recorded = self.leg_fills(bracket, code)[0]
                arrived = sum(row.shares for row in live_rows if self.match_execution(row) is record)
                return min(bracket.quantity, max(recorded, arrived, order_filled.get(record.order_id, 0)))

            return FillSnapshot(
                parent=max(bracket.entry_filled, leg("P")), target=leg("T"), stop=leg("S"),
                recorded_exits=bracket.exit_filled, position=position,
            )

    def gap_age(self, bracket: BracketRecord) -> timedelta:
        """How long the account has held fewer shares than this bracket; every gap alert shares it."""
        now = self.now()
        return now - self.position_gaps.setdefault(bracket.id, now)

    def report_cap(self, bracket: BracketRecord, code: LegCode, order_id: int, purpose: str,
                   position: Decimal | None, plan: ExitPlan, reserved: int = 0) -> None:
        held = share_text(position or Decimal("0"))
        outcome = f"limited to {plan.working} sh" if plan.working > 0 else "not sent"
        if reserved > 0:  # not a trailing position update: other sells hold these shares now (C2-4)
            self.alert_once(
                (bracket.id, code, purpose, "reserved", str(plan.working)),
                f"{bracket.symbol}: account holds {held} sh, {reserved} of them reserved by other sells, but the"
                f" bracket expects {plan.capped_from} — {purpose} of {LEG_NAMES[code]} {order_id} {outcome}; act"
                " in TWS if they do not fill",
            )
            return
        message = (f"{bracket.symbol}: account holds {held} sh but the bracket expects {plan.capped_from} —"
                   f" {purpose} of {LEG_NAMES[code]} {order_id} {outcome}")
        key = (bracket.id, code, purpose, "capped", str(plan.working))
        if self.gap_age(bracket) >= POSITION_GAP_GRACE:
            self.alert_once(key, f"{message}; act in TWS")
        else:  # a position update may still be on its way: the cap applies, the alarm waits [N4]
            self.alert_once((*key, "early"), f"{message} — re-checked while the position updates", kind="INFO")

    def modify_exit(self, bracket: BracketRecord, code: LegCode, *, price: Decimal, purpose: str,
                    only_if_changed: bool = False, retry: bool = False, reserved: int = 0) -> ExitResult:
        leg = bracket.legs.get(code)
        if leg is None or code not in ("T", "S"):
            return ExitResult("blocked")
        order_id = leg.order_id
        snapshot = self.fill_snapshot(bracket)  # taken after every wait, right before placeOrder [H1]
        position = snapshot.position
        if reserved > 0 and position is not None:
            # Shares other sells may still take are not the engine's to sell (C2-4).
            snapshot = replace(snapshot, position=position - reserved)
        plan = plan_exit(snapshot, code)
        if plan.problem:
            self.alert_once(
                (bracket.id, code, purpose, "blocked", plan.problem),
                f"{bracket.symbol}: {purpose} of {LEG_NAMES[code]} {order_id} not sent — {plan.problem}; act in TWS",
            )
            return ExitResult("blocked")
        capped = plan.capped_from is not None
        if capped:
            self.report_cap(bracket, code, order_id, purpose, position, plan, reserved)
        if plan.working <= 0:
            return ExitResult("nothing", 0, capped)
        if only_if_changed:
            if self.accepted(order_id, plan.total, price, leg.order_type):
                self.pending_modifies.pop(order_id, None)
                return ExitResult("unchanged", plan.working, capped)
            pending = self.pending_modifies.get(order_id)
            if pending is not None and (pending.total, pending.price) == (plan.total, price):
                if pending.exhausted:
                    return ExitResult("blocked", plan.working, capped)
                if not retry:  # confirm_modifies owns re-sends, one per tick
                    return ExitResult("pending", plan.working, capped)
        modified = self.exit_order(bracket, code, total=plan.total, price=price)
        if modified is None:
            return ExitResult("blocked")
        with self.mirror.lock:
            self.error_marks[order_id] = self.mirror.order_errors.get(order_id, 0)
        self.broker.placeOrder(order_id, stock(bracket.con_id, bracket.primary_exchange), modified)
        previous = self.pending_modifies.get(order_id)
        same = previous is not None and (
            (previous.purpose, previous.total, previous.price) == (purpose, plan.total, price)
        )
        self.pending_modifies[order_id] = PendingModify(
            bracket_id=bracket.id, leg=code, purpose=purpose, kind=leg.order_type, total=plan.total, price=price,
            sends=previous.sends + 1 if same and previous is not None else 1, reserved=reserved,
        )
        self.queue_event("modify_sent", {"order_id": order_id, "purpose": purpose, "total": plan.total,
                                         "price": str(price)}, bracket, leg)
        return ExitResult("sent", plan.working, capped)

    def modify_settled(self, order_id: int) -> bool:
        """A modification counts as done only once TWS has echoed it [N2]."""
        pending = self.pending_modifies.get(order_id)
        if pending is None:
            return True
        confirmed = self.accepted(order_id, pending.total, pending.price, pending.kind)
        if not confirmed and pending.restored:
            # Sent before a restart, sized then (possibly less, for other sells): its price is the proof.
            confirmed = self.shows_price(order_id, pending.price, pending.kind)
        if confirmed:
            del self.pending_modifies[order_id]
            bracket = self.brackets.get(pending.bracket_id)
            if bracket is not None:
                self.queue_event("modify_confirmed", {"order_id": order_id, "purpose": pending.purpose}, bracket)
            return True
        return False

    def confirm_modifies(self) -> None:
        """Re-send a modification TWS has not echoed, once per tick, then CRITICAL [N2]."""
        for order_id, pending in list(self.pending_modifies.items()):
            bracket = self.brackets.get(pending.bracket_id)
            if (bracket is None or bracket.state != "open"
                    or self.mirror.status(order_id) in DEAD_ORDER_STATUSES):
                del self.pending_modifies[order_id]  # filled, cancelled or closed: nothing left to confirm
                continue
            if self.modify_settled(order_id) or pending.exhausted:
                continue
            name = LEG_NAMES[pending.leg]
            if pending.restored:
                # Never re-sent blindly: what it may sell was decided before the restart. The
                # flatten fallback takes over and decides afresh.
                pending.exhausted = True
                self.alert_once(
                    (bracket.id, pending.leg, pending.purpose, "unconfirmed"),
                    f"{bracket.symbol}: TWS does not show the {pending.purpose} of {name} {order_id} sent before"
                    f" the restart ({pending.price}) — not re-sent; act in TWS",
                )
                continue
            if pending.sends > MODIFY_RETRIES:
                pending.exhausted = True
                self.alert_once(
                    (bracket.id, pending.leg, pending.purpose, "unconfirmed"),
                    f"{bracket.symbol}: TWS did not confirm the {pending.purpose} of {name} {order_id}"
                    f" ({pending.total} sh @ {pending.price}) after {pending.sends} attempts — act in TWS",
                )
                continue
            self.emit("INFO", f"{bracket.symbol}: {pending.purpose} of {name} {order_id} not confirmed by TWS —"
                      f" sending again ({pending.sends + 1}/{MODIFY_RETRIES + 1})")
            result = self.modify_exit(bracket, pending.leg, price=pending.price, purpose=pending.purpose,
                                      only_if_changed=True, retry=True, reserved=pending.reserved)
            if result.outcome in {"nothing", "blocked"}:
                self.pending_modifies.pop(order_id, None)  # no longer needed; the alerts take over

    def leg_price(self, bracket: BracketRecord, code: LegCode) -> Decimal | None:
        if code == "S":
            return bracket.stop_price
        if bracket.flatten_state != "none":  # a target the flatten repriced keeps its flatten price
            return bracket.flatten_price
        return bracket.target_price

    def report_dead_leg(self, bracket: BracketRecord, code: LegCode) -> bool:
        """CRITICAL once for an exit leg that is no longer live; False while its fill is in flight."""
        order_id = bracket.order_id(code)
        status = self.mirror.status(order_id)
        if status == "Filled":
            return False  # its executions are on their way
        if bracket.net_held > 0:
            what = "unprotected" if code == "S" else "without a target"
            self.alert_once(
                (bracket.id, f"dead-{code}"),
                f"{bracket.symbol}: {LEG_NAMES[code]} order {order_id} is not live ({status or 'unknown'}) —"
                f" {bracket.net_held} sh {what}; act in TWS",
            )
        return True

    def parent_dead(self, bracket: BracketRecord) -> bool:
        status = self.mirror.status(bracket.order_id("P"))
        if status in PARENT_CANCELLED_STATUSES:
            return True
        # After a restart TWS no longer reports a cancelled order at all [N7].
        return bracket.parent_cancel_requested and status is None

    def resize_children(self, bracket: BracketRecord) -> None:
        """A dead, partly filled entry must not leave exits sized for the whole order [M2]."""
        if bracket.state != "open" or bracket.flatten_state not in ("none", "repricing", "repriced"):
            return
        if not self.parent_dead(bracket):
            return
        basis = self.fill_snapshot(bracket).parent  # prefers the dead parent's own filled count
        if not 0 < basis < bracket.quantity or bracket.resized_qty == basis:
            return
        sent = False
        settled = True
        for code in PROTECTIVE_LEGS:
            order_id = bracket.order_id(code)
            price = self.leg_price(bracket, code)
            if not self.mirror.live(order_id):
                settled = self.report_dead_leg(bracket, code) and settled
                continue
            if price is None or order_id is None:
                continue
            result = self.modify_exit(bracket, code, price=price, purpose="resize", only_if_changed=True)
            sent = sent or result.outcome == "sent"
            settled = (settled and result.outcome not in {"blocked", "pending"} and not result.capped
                       and self.modify_settled(order_id))
        if sent:
            self.emit("RESIZE", f"{bracket.symbol}: exits set to the {basis} filled shares")
        if settled:  # unconfirmed, capped or blocked: re-evaluated on the next tick
            bracket.resized_qty = basis

    # ---- flatten -----------------------------------------------------------------------
    async def bid(self, con_id: int, primary_exchange: str) -> Decimal | None:
        """The live real-time bid, else the last trade; delayed data never prices an order."""
        self.quote_req += 1
        request_id = self.quote_req
        self.broker.reqMktData(request_id, stock(con_id, primary_exchange), "", False, False, [])
        deadline = time.monotonic() + self.quote_timeout
        value: Decimal | None = None
        while True:
            with self.mirror.lock:
                quote = dict(self.mirror.quotes.get(request_id, {}))
            value = quote.get("bid") or quote.get("last")
            if value is not None or time.monotonic() >= deadline:
                break
            await asyncio.sleep(self.poll_seconds)
        self.broker.cancelMktData(request_id)
        return value

    async def flatten_quote(self, bracket: BracketRecord, discount: Decimal) -> tuple[Decimal, str] | None:
        bid = await self.bid(bracket.con_id, bracket.primary_exchange)
        if bid is not None:
            price, source = floor_tick(bid * discount), f"bid {bid}"
        else:
            try:
                fallback = await self.fallback_quote(bracket)
            except Exception as exc:
                self.emit("INFO", f"{bracket.symbol}: screener snapshot unreadable ({type(exc).__name__}: {exc})")
                fallback = None
            if fallback is None:
                return None
            price = floor_tick(fallback * discount * SNAPSHOT_EXTRA_DISCOUNT)
            source = f"no live bid; screener {fallback} less 1%"
        return (price, source) if price > 0 else None

    async def flatten_bracket(self, bracket: BracketRecord, discount: Decimal = FLATTEN_DISCOUNT) -> bool:
        """Reprice the bracket's own target once; the OCA link then cancels the stop [H2].

        The attempt is recorded before anything is sent and is never started
        again, by a later checkpoint or after a restart [N5]. Like the fallback,
        the reprice sells no more than a fresh account position less every
        other working sell and less what other orders sold that the position
        does not show yet (review C2-1, C2-4). Only when the reprice is
        impossible does the confirmed-cancel fallback take over.
        """
        if bracket.state != "open" or bracket.remaining <= 0 or bracket.flatten_attempted:
            return False
        bracket.flatten_attempted = True
        self.queue_event("flatten_attempt", {"discount": str(discount)}, bracket)
        await self.persist(bracket)  # an exit is never blocked by the ledger: a failure only alerts
        return await self.continue_flatten(bracket, discount)

    async def continue_flatten(self, bracket: BracketRecord, discount: Decimal) -> bool:
        """The flatten's sell decision, on a fresh snapshot.

        Without one nothing is sent and the decision waits: it is taken again
        every tick until 10 minutes before the close, each time on a fresh
        snapshot (review R-1). The wait lives in this process only: after a
        restart the recorded attempt is never started again [N5].
        """
        facts = await self.sellable(bracket)
        if bracket.state != "open":
            self.flatten_waiting.pop(bracket.id, None)
            bracket.flatten_state = "skipped"  # the fresh snapshot shows the fills that closed it
            await self.persist(bracket)
            return False
        if facts == SNAPSHOT_MISSING and not self.past(self.times.verify):
            self.flatten_waiting[bracket.id] = discount
            self.alert_once((bracket.id, "flatten", "snapshot-wait"), f"{bracket.symbol}: flatten waits for a fresh"
                            " TWS snapshot — nothing sent; tried again every tick until"
                            f" {self.times.verify:%H:%M} ET")
            return False
        self.flatten_waiting.pop(bracket.id, None)
        if isinstance(facts, str):
            bracket.flatten_state = "failed"  # in doubt: alert, never sell
            self.emit("CRITICAL", f"{bracket.symbol}: flatten not sent — {facts}; act in TWS")
            await self.persist(bracket)
            return False
        if facts.quantity <= 0 and facts.reserved > 0:
            self.nothing_to_sell(bracket, facts, stop_live=True)
            await self.persist(bracket)
            return False
        target = bracket.legs["T"]
        if not self.mirror.live(target.order_id):
            status = self.mirror.status(target.order_id) or "unknown"
            if status == "Filled":
                bracket.flatten_state = "skipped"
                self.emit("INFO", f"{bracket.symbol}: target {target.order_id} already filled; awaiting executions")
                return False
            return await self.start_fallback(bracket, reason=f"target {target.order_id} is not live ({status})",
                                             facts=facts)
        quote = await self.flatten_quote(bracket, discount)
        if quote is None:
            return await self.start_fallback(bracket, reason="no quote to reprice the target", facts=facts)
        price, source = quote
        result = self.modify_exit(bracket, "T", price=price, purpose="flatten", reserved=facts.reserved)
        if result.outcome == "sent":
            bracket.flatten_state, bracket.flatten_price = "repricing", price
            self.emit("FLATTEN", f"{bracket.symbol}: target repriced to {price} for {result.working} sh ({source})")
            await self.persist(bracket)
            return True
        # Capped to nothing (the owner already sold) or blocked (oversold, position unknown, a target
        # changed by hand in TWS): alerts only, never a second sell.
        bracket.flatten_state = "skipped" if result.outcome == "nothing" else "failed"
        await self.persist(bracket)
        return False

    async def sellable(self, bracket: BracketRecord) -> SellFacts | str:
        """What a flatten may sell, from one fresh TWS snapshot, or why it may sell nothing.

        Open orders, this client's executions, every client's completed orders
        and the positions are requested together; the position is read only
        once all of them arrived. The quantity is the ledger's shares, capped by
        that position less every other working sell [H2] and less the shares
        other orders sold that the position does not show yet: a filled sell is
        no longer a working order, and until TWS's position update shows it the
        same shares would be sold twice (review C2-1, the bridge's F2/R2).
        """
        if not await self.request_snapshots(positions=True):
            return SNAPSHOT_MISSING
        await self.sync_fills()
        self.observe_legs()
        self.refresh(bracket)
        snapshot = self.fill_snapshot(bracket)
        held = snapshot.parent - max(snapshot.recorded_exits, snapshot.target + snapshot.stop)
        if held < 0:
            return "exit fills exceed entry fills"
        if snapshot.position is None:
            return "account position unknown"
        working = self.other_working_sells(bracket)
        unshown = self.unshown_sales(bracket, held, snapshot.position)
        if unshown > 0:
            self.unshown_since.setdefault(bracket.id, self.now())
        quantity = min(held, max(0, int(snapshot.position) - working - unshown))
        return SellFacts(quantity=quantity, held=held, position=snapshot.position, working=working, unshown=unshown)

    def other_working_sells(self, bracket: BracketRecord) -> int:
        """Shares every other working SELL in the contract may still sell (owner, manual:, other apps)."""
        with self.mirror.lock:
            orders = list(self.mirror.account_orders.values())
        own = {leg.order_ref for leg in bracket.legs.values()}
        reserved = sum(
            (item.open_quantity for item in orders
             if item.con_id == bracket.con_id and item.action == "SELL" and item.working
             and item.account in {"", self.account} and item.order_ref not in own),
            Decimal("0"),
        )
        return int(reserved.to_integral_value(rounding=ROUND_CEILING))

    def foreign_fills(self, bracket: BracketRecord) -> tuple[int, int]:
        """``(sold, bought)``: shares other orders executed in the bracket's contract since its entry.

        Other orders: the owner's TWS orders, ``manual:`` orders, other apps. The
        engine's own legs, of any of today's brackets, are accounted by their
        executions instead (review R-3). The entry itself needed a flat account
        [N1], so only orders completed after it count: the bracket's baseline
        holds every order TWS reported completed before it; without one (an
        older ledger row) the completion time decides, a sell counting when it
        may have completed after the entry (an unreadable time counts), a buy
        only when it surely did.

        A Filled sell counts its whole size whatever filledQuantity says, and a
        filled quantity of 0 counts as unknown, never as nothing sold: ibapi
        9.81 decodes an empty field as 0.0 (review R-2). Every doubt leaves
        fewer shares to sell, never more.
        """
        baseline = self.entry_baselines.get(bracket.id)
        sold, bought = Decimal("0"), Decimal("0")
        with self.mirror.lock:
            completed = list(self.mirror.completed.values())
            working = list(self.mirror.account_orders.values())
        counted: set[int] = set()
        for row in completed:
            if (row.con_id != bracket.con_id or row.order_ref in self.leg_by_ref
                    or row.account not in {"", self.account}):
                continue
            if row.perm_id:
                counted.add(row.perm_id)
            if baseline is not None and row.perm_id:
                if row.perm_id in baseline:
                    continue  # completed before the entry: the flat position at the entry included it
                sold_after = bought_after = True
            else:
                when = parse_completed_time(row.completed_time)
                sold_after = when is None or when >= bracket.created_at - FOREIGN_FILL_MARGIN
                bought_after = when is not None and when >= bracket.created_at + FOREIGN_FILL_MARGIN
            if row.action == "SELL" and sold_after:
                sold += completed_sell_shares(row.status, row.total, row.filled)
            elif row.action == "BUY" and bought_after and row.filled:  # an unknown fill never adds shares
                bought += row.filled
        for item in working:
            if (item.con_id != bracket.con_id or item.order_ref in self.leg_by_ref
                    or item.account not in {"", self.account} or item.remaining is None
                    or (item.perm_id and item.perm_id in counted)):
                continue
            executed = max(Decimal("0"), item.total - item.remaining)
            if item.action == "SELL":
                sold += executed
            elif item.action == "BUY" and item.perm_id:
                bought += executed
        return (int(sold.to_integral_value(rounding=ROUND_CEILING)),
                int(bought.to_integral_value(rounding=ROUND_FLOOR)))

    def unshown_sales(self, bracket: BracketRecord, held: int, position: Decimal) -> int:
        """Shares other orders sold since the entry that the account position does not show yet.

        The entry needed a flat account [N1], so the true position is the
        bracket's own shares plus what other orders bought less what they sold;
        a position above that has not caught up with their sells.
        """
        sold, bought = self.foreign_fills(bracket)
        if sold <= 0:
            return 0
        expected = held + bought - sold
        return min(sold, max(0, int(position) - expected))

    def nothing_to_sell(self, bracket: BracketRecord, facts: SellFacts, *, stop_live: bool) -> None:
        bracket.flatten_state = "skipped"
        if facts.held <= 0:
            return
        protection = "its stop stays live" if stop_live else "its stop is cancelled"
        reasons = []
        if facts.working:
            reasons.append(f"{facts.working} reserved by working sells")
        if facts.unshown:
            reasons.append(f"{facts.unshown} sold by other orders that the position does not show yet")
        self.emit("CRITICAL", f"{bracket.symbol}: account holds {share_text(facts.position)} sh with"
                  f" {' and '.join(reasons) or 'nothing reserved'} — no sell for the {facts.held} sh the engine"
                  f" expects ({protection}); act in TWS if not flat")

    async def start_fallback(self, bracket: BracketRecord, *, reason: str, facts: SellFacts | None = None) -> bool:
        """Cancel target and stop; a market sell follows only once both cancels are confirmed."""
        if bracket.flatten_state not in ("none", "repricing"):
            return False
        # Never cancel the protective stop for a sell that could not be sent anyway.
        checked = facts if facts is not None else await self.sellable(bracket)
        if bracket.state != "open":
            bracket.flatten_state = "skipped"  # the fresh snapshot shows the fills that closed it
            await self.persist(bracket)
            return False
        if checked == SNAPSHOT_MISSING and not self.past(self.times.verify):
            # Nothing cancelled yet: asked again next tick, on a fresh snapshot (review R-1).
            self.alert_once((bracket.id, "fallback-start", "snapshot-wait"), f"{bracket.symbol}: flatten fallback"
                            f" waits for a fresh TWS snapshot ({reason}) — target and stop stay live; tried again"
                            f" every tick until {self.times.verify:%H:%M} ET")
            return False
        if isinstance(checked, str):
            bracket.flatten_state = "failed"
            self.emit("CRITICAL", f"{bracket.symbol}: flatten fallback not started ({reason}) — {checked};"
                      " act in TWS")
            await self.persist(bracket)
            return False
        if checked.quantity <= 0:
            self.nothing_to_sell(bracket, checked, stop_live=True)
            await self.persist(bracket)
            return False
        previous = bracket.flatten_state
        bracket.flatten_state = "cancelling"
        if not await self.persist(bracket):
            # Without a durable record a restart could not tell this flatten ever started:
            # keep the GTC stop at IBKR and leave the close to the owner.
            bracket.flatten_state = previous
            self.alert_once((bracket.id, "fallback", "unrecorded"), f"{bracket.symbol}: flatten fallback not"
                            f" started ({reason}) — the execution ledger is unavailable; act in TWS")
            return False
        self.pending_modifies.pop(bracket.order_id("T") or 0, None)
        self.cancel_requested_at[bracket.id] = self.now()
        self.emit("FLATTEN", f"{bracket.symbol}: {reason} — fallback: cancelling target and stop, then one market"
                  " sell once both cancels are confirmed")
        self.cancel_protective_legs(bracket, "flatten fallback")
        return True

    def cancel_protective_legs(self, bracket: BracketRecord, why: str) -> None:
        """Cancel every exit leg not yet Cancelled or Filled: an Inactive one can come back (review C2-5)."""
        for code in PROTECTIVE_LEGS:
            if not self.leg_dead(bracket, code):
                self.cancel_leg(bracket, code, why)

    def leg_status(self, bracket: BracketRecord, code: LegCode) -> str | None:
        """IBKR's status of a leg: its order status, else the completed order carrying its orderRef."""
        leg = bracket.legs.get(code)
        if leg is None:
            return None
        status = self.mirror.status(leg.order_id)
        if status is None:
            with self.mirror.lock:
                status = next((row.status for row in self.mirror.completed.values()
                               if row.order_ref == leg.order_ref), None)
        return status

    def leg_dead(self, bracket: BracketRecord, code: LegCode) -> bool:
        """Only Cancelled, ApiCancelled or Filled ends a leg for the fallback (review C2-5)."""
        if bracket.legs.get(code) is None:
            return True
        return self.leg_status(bracket, code) in FALLBACK_DEAD_STATUSES

    async def advance_fallbacks(self) -> None:
        for bracket in list(self.brackets.values()):
            try:
                await self.advance_fallback(bracket)
            except (ExecutionRefused, ExecutionAuthorityError):
                raise
            except Exception as exc:  # one bracket's failure never stops another's fallback [M1]
                self.alert_once((bracket.id, "fallback", "step", type(exc).__name__),
                                f"{bracket.symbol}: flatten fallback step failed ({type(exc).__name__}: {exc}) —"
                                " retried every tick; act in TWS if it persists")

    async def advance_fallback(self, bracket: BracketRecord) -> None:
        if bracket.flatten_state == "repricing":
            await self.follow_reprice(bracket)
            return
        if bracket.flatten_state != "cancelling":
            return
        if bracket.state != "open":
            bracket.flatten_state = "skipped"  # fills that raced the cancels closed it
            return
        if bracket.id not in self.cancel_requested_at:
            # First look in this run: a restart may have come before the cancels reached TWS.
            self.cancel_requested_at[bracket.id] = self.now()
            self.cancel_protective_legs(bracket, "flatten fallback, after a restart")
        if not all(self.leg_dead(bracket, code) for code in PROTECTIVE_LEGS):
            started = self.cancel_requested_at.setdefault(bracket.id, self.now())
            if self.now() - started >= FLATTEN_CANCEL_TIMEOUT:
                bracket.flatten_state = "failed"
                seconds = int(FLATTEN_CANCEL_TIMEOUT.total_seconds())
                self.emit("CRITICAL", f"{bracket.symbol}: TWS did not confirm cancelling the target and stop"
                          f" within {seconds}s — no market sell sent; act in TWS")
            return
        await self.send_fallback_sell(bracket)

    async def follow_reprice(self, bracket: BracketRecord) -> None:
        """A sent reprice ends confirmed, or falls back when TWS never confirmed it."""
        target = bracket.legs["T"]
        pending = self.pending_modifies.get(target.order_id)
        if pending is not None:
            if pending.exhausted and bracket.state == "open":
                await self.start_fallback(bracket, reason=f"TWS did not confirm the target reprice to {pending.price}")
            return
        status = self.mirror.status(target.order_id)
        if bracket.state == "open" and status not in WORKING_ORDER_STATUSES and status != "Filled":
            # Cancelled after an accepted reprice: someone else is acting on it; never a second sell.
            bracket.flatten_state = "failed"
            self.emit("CRITICAL", f"{bracket.symbol}: target {target.order_id} is no longer live"
                      f" ({status or 'unknown'}) after its flatten reprice — no further sell; act in TWS")
            return
        with self.mirror.lock:
            echo = self.mirror.order_objects.get(target.order_id)
        shown = decimal_or_none(getattr(echo, "lmtPrice", None)) if echo is not None else None
        repriced = (shown is not None and bracket.flatten_price is not None
                    and abs(shown - bracket.flatten_price) < TICK / 2)
        # A reprice dropped on the way (capped to nothing, or blocked with its own alert) is never retried.
        bracket.flatten_state = "repriced" if repriced or status == "Filled" or bracket.state != "open" else "failed"

    async def send_fallback_sell(self, bracket: BracketRecord) -> None:
        """One market sell of the ledger quantity, capped by a fresh position less every other sell."""
        # The most dangerous order the engine sends decides on a fresh snapshot of everything:
        # executions and completed orders (a fill that raced the cancels) and positions from scratch.
        facts = await self.sellable(bracket)
        if bracket.state != "open":
            bracket.flatten_state = "skipped"  # the cancels raced fills that closed it
            return
        if facts == SNAPSHOT_MISSING:
            self.retry_fallback_sell(bracket, facts)
            return
        if isinstance(facts, str):
            bracket.flatten_state = "failed"
            self.emit("CRITICAL", f"{bracket.symbol}: flatten fallback stopped — {facts}; no market sell sent;"
                      " act in TWS")
            return
        if facts.quantity <= 0:
            self.nothing_to_sell(bracket, facts, stop_live=False)
            return
        quantity, held, position = facts.quantity, facts.held, facts.position
        try:
            order_id = (await self.allocate_order_ids(1))[0]
        except Exception as exc:  # no order id from TWS, or an unreadable ledger [M1]
            self.retry_fallback_sell(bracket, f"no order id ({type(exc).__name__}: {exc})")
            return  # still "cancelling": the next tick takes a fresh snapshot and tries again
        leg = LegRecord(
            id=f"{bracket.id}:F", bracket_id=bracket.id, leg="F", client_id=self.client_id, order_id=order_id,
            order_ref=order_ref(bracket.id, "F"), action="SELL", order_type="MKT", tif="DAY", quantity=quantity,
            price=None, parent_order_id=None, transmit=True, state="staged", created_at=self.now(),
        )
        try:
            await self.ledger.add_flatten_leg(bracket, leg, payload={
                "held": held, "position": str(position), "reserved": facts.reserved, "quantity": quantity,
            })
        except Exception as exc:
            # Still "cancelling": nothing was sent, so a retry can never sell twice.
            self.retry_fallback_sell(bracket, f"not recorded in the execution ledger ({type(exc).__name__}: {exc})")
            return
        self.saved[bracket.id] = ()  # flatten state changed in the ledger: save the rest normally
        self.index(bracket)
        if self.stop_event.is_set() or not self.broker.isConnected():
            self.emit("CRITICAL", f"{bracket.symbol}: fallback market sell recorded but not sent (stopping or"
                      " disconnected) — never resent; act in TWS")
            return
        self.broker.placeOrder(order_id, stock(bracket.con_id, bracket.primary_exchange),
                               leg_ticket(leg, self.account))
        try:
            await self.ledger.settle_leg(leg, state="sent", book=self.book)
        except Exception:
            leg.state = "sent"
        self.emit("FLATTEN", f"{bracket.symbol}: fallback market sell of {quantity} sh (order {order_id}; ledger"
                  f" {held} sh, account {share_text(position)} sh)")

    def retry_fallback_sell(self, bracket: BracketRecord, why: str) -> None:
        """Target and stop are cancelled but the market sell could not go out: again next tick, until the close.

        Every try takes a fresh snapshot and nothing is sent twice (review R-1);
        at the close the fallback fails, CRITICAL.
        """
        close = self.times.closes_at
        if self.past(close):
            bracket.flatten_state = "failed"
            self.emit("CRITICAL", f"{bracket.symbol}: fallback market sell not sent by the {close:%H:%M} ET close"
                      f" ({why}) — {bracket.net_held} sh without target or stop; act in TWS")
            return
        self.alert_once((bracket.id, "fallback-sell", why.split(" (")[0]), f"{bracket.symbol}: fallback market"
                        f" sell not sent yet ({why}) — target and stop are cancelled; tried again every tick until"
                        f" {close:%H:%M} ET; act in TWS if it persists")

    async def retry_flattens(self) -> None:
        """Flattens still waiting for a fresh snapshot, each on a fresh one; at close - 10 min they fail."""
        for bracket_id, discount in list(self.flatten_waiting.items()):
            bracket = self.brackets[bracket_id]
            if self.past(self.times.verify):  # no try after the deadline, even one TWS would answer
                del self.flatten_waiting[bracket_id]
                bracket.flatten_state = "failed"
                self.emit("CRITICAL", f"{bracket.symbol}: flatten not sent — no fresh TWS snapshot by"
                          f" {self.times.verify:%H:%M} ET; act in TWS")
                await self.persist(bracket)
                continue
            try:
                await self.continue_flatten(bracket, discount)
            except Exception as exc:  # one bracket's failure never stops the others' flatten
                self.flatten_waiting.pop(bracket_id, None)
                self.emit("CRITICAL", f"{bracket.symbol}: flatten failed ({type(exc).__name__}: {exc}) — not"
                          " retried; act in TWS")

    async def flatten(self, discount: Decimal = FLATTEN_DISCOUNT) -> None:
        """One attempt per bracket, ever: flatten_attempted is persisted across restarts [N5]."""
        for bracket in list(self.brackets.values()):
            try:
                await self.flatten_bracket(bracket, discount)
            except Exception as exc:  # one bracket's failure never stops the others' flatten
                self.emit("CRITICAL", f"{bracket.symbol}: flatten failed ({type(exc).__name__}: {exc}) — not"
                          " retried; act in TWS")

    # ---- alerts and health -------------------------------------------------------------
    def positions_not_flat(self) -> list[BracketRecord]:
        return [
            bracket for bracket in self.brackets.values()
            if bracket.state in {"open", "needs_attention"} and bracket.net_held != 0
            and bracket.attention_cleared_at is None
        ]

    @staticmethod
    def not_flat_message(bracket: BracketRecord) -> str:
        if bracket.net_held < 0:
            return (f"{bracket.symbol}: exits exceed entry fills by {-bracket.net_held} sh — the account may be"
                    " short; act in TWS")
        if bracket.state == "open":
            return f"{bracket.symbol}: still open after flatten ({bracket.net_held} sh) — act in TWS"
        return f"{bracket.symbol}: unreconciled bracket, {bracket.net_held} sh per the ledger — act in TWS"

    def short_positions(self, *, unexplained: bool = False) -> list[tuple[int, str, Decimal]]:
        """``(conId, symbol, shares)`` of every short position in a symbol the engine traded today.

        ``unexplained`` leaves out contracts a not-flat bracket already reports
        as oversold, so the same short is not announced twice.
        """
        explained = {bracket.con_id for bracket in self.positions_not_flat() if bracket.net_held < 0}
        traded: dict[int, str] = {}
        for bracket in self.brackets.values():
            if bracket.entry_filled > 0 or bracket.state in ("open", "needs_attention", "unknown"):
                traded.setdefault(bracket.con_id, bracket.symbol)
        shorts: list[tuple[int, str, Decimal]] = []
        for con_id, symbol in traded.items():
            held = self.account_position(con_id)
            if held is not None and held < 0 and not (unexplained and con_id in explained):
                shorts.append((con_id, symbol, held))
        return shorts

    @staticmethod
    def short_message(symbol: str, held: Decimal) -> str:
        return f"{symbol}: account is short {share_text(-held)} sh in a symbol the engine traded today — act in TWS"

    def alert_not_flat(self) -> bool:
        """CRITICAL for every bracket not flat and every short in a symbol traded today (never FLAT then)."""
        messages = [self.short_message(symbol, held) for _, symbol, held in self.short_positions(unexplained=True)]
        messages += [self.not_flat_message(bracket) for bracket in self.positions_not_flat()]
        for message in messages:
            self.emit("CRITICAL", message)
        if messages:
            self.alert_raised = True
        return bool(messages)

    def verify_flat(self) -> None:
        """Alert only: a second automatic sell could race the owner's manual close [H2]."""
        if not self.alert_not_flat():
            self.emit("FLAT", "no open engine positions")

    def stop_died_under_entry(self, bracket: BracketRecord) -> str | None:
        """The stop's status when it died (rejected, cancelled, Inactive) while the entry still works."""
        if bracket.state not in ("working", "open") or bracket.parent_cancel_requested:
            return None
        status = self.mirror.status(bracket.order_id("S"))
        if status in PARENT_CANCELLED_STATUSES and self.mirror.live(bracket.order_id("P")):
            return status
        return None

    def contain_unprotected_entry(self, bracket: BracketRecord) -> None:
        """Shares bought after the stop died would have no stop at all: cancel the entry (review C2-6).

        The same containment as for a bracket cut off mid-send (resolve_inflight).
        """
        status = self.stop_died_under_entry(bracket)
        if status is None:
            return
        if self.cancel_leg(bracket, "P", "its stop is not live"):
            bracket.parent_cancel_requested = True
            self.emit("CRITICAL", f"entry {bracket.symbol}: stop {bracket.order_id('S')} is {status} while the entry"
                      f" works — entry cancelled (filled {bracket.entry_filled}/{bracket.quantity}); act in TWS")

    def check_unshown_sales(self, bracket: BracketRecord) -> None:
        """Shares other orders sold that the position still does not show stay reserved; CRITICAL after 15 s."""
        unshown, position = 0, None
        if bracket.state == "open":
            snapshot = self.fill_snapshot(bracket)
            held = snapshot.parent - max(snapshot.recorded_exits, snapshot.target + snapshot.stop)
            position = snapshot.position
            if position is not None and held > 0:
                unshown = self.unshown_sales(bracket, held, position)
        if unshown <= 0 or position is None:
            self.unshown_since.pop(bracket.id, None)
            return
        now = self.now()
        since = self.unshown_since.setdefault(bracket.id, now)
        if now - since >= UNSHOWN_SELL_ALERT:  # the bridge's L-e: the shares stay reserved, never released by time
            self.alert_once(
                (bracket.id, "unshown", str(unshown)),
                f"{bracket.symbol}: the account position ({share_text(position)} sh) still does not show {unshown} sh"
                f" other orders sold {int((now - since).total_seconds())}s ago — those shares stay reserved and the"
                " engine never sells them; check TWS",
            )

    def check_protection(self, bracket: BracketRecord) -> None:
        """Every tick: an entry whose stop died is cancelled; an open position without a live stop is CRITICAL [M2]."""
        self.contain_unprotected_entry(bracket)
        if bracket.state != "open" or bracket.net_held <= 0 or self.mirror.live(bracket.order_id("S")):
            return
        if bracket.flatten_state in ("cancelling", "selling"):
            self.check_fallback_protection(bracket)  # the fallback cancelled the stop on purpose
            return
        if self.mirror.status(bracket.order_id("T")) == "Filled":
            return  # the target's fill (and the OCA cancel of the stop) is still being reported
        self.report_dead_leg(bracket, "S")

    def check_fallback_protection(self, bracket: BracketRecord) -> None:
        """Shares with no stop, no target and no market sell working are UNPROTECTED: CRITICAL at once (R-1)."""
        if any(self.mirror.status(bracket.order_id(code)) in WORKING_ORDER_STATUSES for code in PROTECTIVE_LEGS):
            return  # a cancel not confirmed yet (PendingCancel): that leg may still fill
        sell = bracket.legs.get("F")
        if sell is not None:
            status = self.mirror.status(sell.order_id)
            if status is None or status in WORKING_ORDER_STATUSES or status == "Filled":
                return  # the market sell is on its way, working or filled
        self.flag_unprotected(bracket, "the flatten fallback cancelled target and stop and no market sell is"
                              " working")

    def flag_unprotected(self, bracket: BracketRecord, why: str) -> None:
        self.alert_once((bracket.id, "unprotected"), f"{bracket.symbol}: {bracket.net_held} sh UNPROTECTED — {why};"
                        " act in TWS")

    def report_dangling(self, bracket: BracketRecord, order_id: int) -> None:
        """A live exit on a finished bracket could sell shares the engine no longer holds [N2]."""
        self.unmanaged[order_id] = (bracket.symbol, bracket.con_id)  # blocks the symbol, takes a slot
        self.alert_once(
            (bracket.id, "dangling", str(order_id)),
            f"{bracket.symbol}: order {order_id} is still live at IBKR for a {bracket.state} bracket — check TWS",
        )

    def check_dangling_exits(self, bracket: BracketRecord) -> None:
        for code in PROTECTIVE_LEGS:
            order_id = bracket.order_id(code)
            if order_id is None:
                continue
            if bracket.state not in {"closed", "cancelled"} or not self.mirror.live(order_id):
                self.dangling_since.pop(order_id, None)
                continue
            now = self.now()
            if now - self.dangling_since.setdefault(order_id, now) >= POSITION_GAP_GRACE:  # OCA trails a fill
                self.report_dangling(bracket, order_id)

    def check_account_position(self, bracket: BracketRecord) -> None:
        """An account holding fewer shares than the bracket means its exits could oversell [H2]."""
        snapshot = self.fill_snapshot(bracket) if bracket.state == "open" else None
        expected = 0
        if snapshot is not None:
            expected = snapshot.parent - max(snapshot.recorded_exits, snapshot.target + snapshot.stop)
        if snapshot is None or snapshot.position is None or expected <= 0 or snapshot.position >= expected:
            self.position_gaps.pop(bracket.id, None)
            return
        if self.gap_age(bracket) >= POSITION_GAP_GRACE:
            self.alert_once(
                (bracket.id, "position-gap"),
                f"{bracket.symbol}: account holds {share_text(snapshot.position)} sh but the bracket expects"
                f" {expected} — its exit orders could sell shares the account no longer holds; check TWS",
            )

    # ---- reconciliation ------------------------------------------------------------------
    async def refresh_orders(self) -> bool:
        """A fresh snapshot of every client's working and completed orders (the owner's TWS orders included)."""
        mirror = self.mirror
        with mirror.lock:
            mirror.account_orders.clear()
        mirror.open_orders_done.clear()
        mirror.completed_done.clear()
        self.broker.reqAllOpenOrders()
        self.broker.reqCompletedOrders(False)
        deadline = time.monotonic() + self.sync_timeout
        for event in (mirror.open_orders_done, mirror.completed_done):
            if not await self.wait_for(event, max(0.0, deadline - time.monotonic())):
                return False
        return True

    async def request_snapshots(self, *, positions: bool) -> bool:
        mirror = self.mirror
        with mirror.lock:
            mirror.account_orders.clear()
        for event in (mirror.open_orders_done, mirror.exec_done, mirror.completed_done):
            event.clear()
        self.broker.reqAllOpenOrders()
        self.broker.reqExecutions(EXEC_REQUEST_ID, execution_filter(self.client_id))
        self.broker.reqCompletedOrders(False)  # every client's, the TWS GUI's included (review C2-1)
        if positions:
            # Re-subscribe from scratch: a contract TWS no longer reports must not keep a stale count.
            with mirror.lock:
                mirror.positions_done.clear()
                for key in [key for key in mirror.positions if key[0] == self.account]:
                    del mirror.positions[key]
            self.broker.cancelPositions()
            self.broker.reqPositions()  # stays subscribed: every exit is capped by it
        # One deadline for the whole snapshot: a silent TWS stalls a tick once, not once per request.
        deadline = time.monotonic() + self.sync_timeout
        for event in (mirror.open_orders_done, mirror.exec_done, mirror.completed_done, mirror.positions_done):
            if not await self.wait_for(event, max(0.0, deadline - time.monotonic())):
                return False
        return True

    def refs_known_at_ibkr(self) -> set[str]:
        with self.mirror.lock:
            refs = {row.order_ref for row in self.mirror.open_orders.values() if row.order_ref}
            refs |= {item.order_ref for item in self.mirror.account_orders.values() if item.order_ref}
            refs |= {row.order_ref for row in self.mirror.completed.values() if row.order_ref}
            refs |= {row.order_ref for row in self.mirror.execs.values() if row.order_ref}
        return refs

    async def resolve_inflight(self) -> list[Finding]:
        """Brackets and fallback sells being sent when the engine stopped: adopt or mark unknown, never resend."""
        findings: list[Finding] = []
        known = self.refs_known_at_ibkr()
        for bracket in [item for item in self.brackets.values() if item.state == "sending"]:
            present = [code for code in BRACKET_LEGS if bracket.legs[code].order_ref in known]
            if not present:
                await self.ledger.mark_unknown(bracket, reason="IBKR reports none of its legs after a restart")
                self.saved[bracket.id] = self.signature(bracket)
                message = (f"{bracket.symbol}: entry was being sent when the engine stopped and IBKR has no order"
                           " for it — marked unknown and never resent; check TWS")
                self.emit("CRITICAL", message)
                findings.append(Finding("inflight_unknown", f"inflight:{bracket.id}", message))
                continue
            await self.ledger.mark_sent(bracket, payload={"adopted_after_restart": present})
            self.saved[bracket.id] = self.signature(bracket)
            missing = [code for code in BRACKET_LEGS if code not in present]
            if not missing:
                self.emit("INFO", f"{bracket.symbol}: bracket sent before the restart is at IBKR — managed")
                continue
            self.mark_attention(bracket, f"legs {', '.join(missing)} not at IBKR after a restart mid-send")
            if self.mirror.status(bracket.order_id("P")) in WORKING_ORDER_STATUSES and "S" in missing:
                # Containment: an entry that could fill without its protective stop is cancelled.
                if self.cancel_leg(bracket, "P", "incomplete bracket after a restart"):
                    bracket.parent_cancel_requested = True
            findings.append(Finding("inflight_incomplete", f"inflight:{bracket.id}",
                                    f"{bracket.symbol}: bracket incomplete at IBKR (missing {', '.join(missing)})"))
        for bracket in self.brackets.values():
            leg = bracket.legs.get("F")
            if leg is None or leg.state != "staged":
                continue
            state: Literal["sent", "unknown"] = "sent" if leg.order_ref in known else "unknown"
            await self.ledger.settle_leg(leg, state=state, book=self.book)
            if state == "unknown":
                self.emit("CRITICAL", f"{bracket.symbol}: fallback market sell was being sent when the engine"
                          " stopped and IBKR has no order for it — never resent; act in TWS")
                findings.append(Finding("inflight_unknown", f"inflight:{leg.id}", "fallback sell unknown at IBKR"))
        return findings

    def expected_live_legs(self, bracket: BracketRecord) -> tuple[LegCode, ...]:
        if bracket.state == "working":
            return () if bracket.parent_cancel_requested else ("P", "T", "S")
        if bracket.state != "open" or bracket.flatten_state not in ("none", "repricing", "repriced"):
            return ()
        legs: list[LegCode] = ["T", "S"]
        if bracket.entry_filled < bracket.quantity and not bracket.parent_cancel_requested:
            legs.append("P")
        return tuple(legs)

    def track_unmanaged(self) -> None:
        """Live orders of this client that the ledger does not own block their symbol and take a slot [L4]."""
        with self.mirror.lock:
            rows = dict(self.mirror.open_orders)
            statuses = dict(self.mirror.order_status)
        for order_id, row in rows.items():
            if statuses.get(order_id, row.status) not in WORKING_ORDER_STATUSES:
                continue
            if order_id in self.leg_by_order or row.order_ref.startswith(MANUAL_REF_PREFIX):
                continue
            if order_id not in self.unmanaged:
                self.unmanaged[order_id] = (row.symbol or "?", row.con_id)
                with self.mirror.lock:
                    self.mirror.own_order_ids.add(order_id)
                self.emit("CRITICAL", f"order {order_id} ({row.symbol or '?'}) is live at IBKR but not in the"
                          f" execution ledger — {row.symbol or '?'} blocked for new entries; check TWS")

    def unmanaged_live(self) -> list[tuple[str, int]]:
        with self.mirror.lock:
            return [target for order_id, target in self.unmanaged.items()
                    if self.mirror.order_status.get(order_id) not in DEAD_ORDER_STATUSES]

    async def compare_with_ibkr(self, *, startup: bool = False) -> list[Finding]:
        findings: list[Finding] = []
        now = self.now()
        with self.mirror.lock:
            working = [item for item in self.mirror.account_orders.values() if item.working]
            executions = list(self.mirror.execs.values())
            completed_refs = {row.order_ref for row in self.mirror.completed.values() if row.order_ref}
        strays = [item.order_ref for item in working if parse_order_ref(item.order_ref) is not None
                  and item.order_ref not in self.leg_by_ref]
        strays += [row.order_ref for row in executions if parse_order_ref(row.order_ref) is not None
                   and row.order_ref not in self.leg_by_ref]
        known_elsewhere = await self.ledger.find_refs(strays) if strays else {}
        for item in working:
            if parse_order_ref(item.order_ref) is None:
                continue  # manual:, the owner's TWS orders, other apps: never the engine's business
            leg = self.leg_by_ref.get(item.order_ref)
            if leg is None:
                code = "stale_order" if item.order_ref in known_elsewhere else "unknown_order"
                self.unmanaged.setdefault(item.order_id, (item.symbol or "?", item.con_id))
                findings.append(Finding(code, f"{code}:{item.order_ref}",
                                        f"{item.symbol or '?'}: order {item.order_id} tagged {item.order_ref} is"
                                        " working at IBKR but is not a managed engine order — check TWS"))
                continue
            if item.client_id != self.client_id or item.order_id != leg.order_id or (
                item.account and item.account != self.account
            ):
                findings.append(Finding("identity_mismatch", f"identity:{item.order_ref}",
                                        f"{item.symbol or '?'}: {item.order_ref} is order {item.order_id} of client"
                                        f" {item.client_id} at IBKR, not order {leg.order_id} — check TWS"))
                continue
            bracket = self.brackets[leg.bracket_id]
            if bracket.state == "unknown":
                self.mark_attention(bracket, "IBKR reports an order of a bracket marked unknown")
                findings.append(Finding("unknown_bracket_live", f"unknown-live:{bracket.id}",
                                        f"{bracket.symbol}: IBKR reports {item.order_ref} of a bracket marked unknown"))
            elif startup and leg.leg in ("T", "S") and bracket.state in ("closed", "cancelled"):
                self.report_dangling(bracket, leg.order_id)  # finished before the restart: no grace
        for bracket in list(self.brackets.values()):
            expected = self.expected_live_legs(bracket)
            missing: list[LegCode] = []
            for code in expected:
                leg = bracket.legs[code]
                status = self.mirror.status(leg.order_id)
                if status in WORKING_ORDER_STATUSES or status in DEAD_ORDER_STATUSES:
                    continue  # dead legs are the protection checks' business
                if leg.order_ref in completed_refs:
                    continue
                missing.append(code)
                findings.append(Finding("missing_order", f"missing:{leg.id}",
                                        f"{bracket.symbol}: {LEG_NAMES[code]} order {leg.order_id} is not reported by"
                                        " IBKR — check TWS"))
            if expected and len(missing) == len(expected):
                # IBKR reports nothing of it: never managed or re-sent again; only the owner resolves it.
                self.mark_attention(bracket, "not found at IBKR",
                                    message=f"{bracket.symbol}: bracket not found at IBKR — check TWS")
        for row in executions:
            if parse_order_ref(row.order_ref) is None or row.order_ref in self.leg_by_ref:
                continue
            if row.order_ref not in known_elsewhere:
                findings.append(Finding("unknown_execution", f"unknown-exec:{row.base}",
                                        f"{row.symbol or '?'}: execution {row.exec_id} tagged {row.order_ref} is not in"
                                        " the execution ledger — check TWS"))
        expected: dict[int, tuple[str, int]] = {}
        for bracket in self.brackets.values():
            if bracket.state == "open" or (bracket.state == "needs_attention" and bracket.attention_cleared_at is None):
                symbol, shares = expected.get(bracket.con_id, (bracket.symbol, 0))
                expected[bracket.con_id] = (symbol, shares + bracket.net_held)
        for con_id, (symbol, shares) in expected.items():
            held = self.account_position(con_id)
            key = f"position:{con_id}"
            if held is None:
                findings.append(Finding("position_unknown", key, f"{symbol}: account position unknown"))
                continue
            if held == shares:
                self.position_gaps.pop(key, None)
                continue
            if now - self.position_gaps.setdefault(key, now) >= POSITION_GAP_GRACE:
                findings.append(Finding("position_mismatch", key,
                                        f"{symbol}: account holds {share_text(held)} sh, the execution ledger expects"
                                        f" {shares} — check TWS"))
        for con_id, symbol, held in self.short_positions():
            findings.append(Finding("short_position", f"short:{con_id}", self.short_message(symbol, held)))
        for bracket in self.brackets.values():
            status = self.stop_died_under_entry(bracket)
            if status is not None:
                findings.append(Finding("stop_dead", f"stop-dead:{bracket.id}",
                                        f"{bracket.symbol}: stop {bracket.order_id('S')} is {status} while the entry"
                                        " works — check TWS"))
        for bracket in self.brackets.values():
            if bracket.state != "needs_attention" or bracket.attention_cleared_at is not None:
                continue
            live_legs = any(self.mirror.status(leg.order_id) in WORKING_ORDER_STATUSES
                            for leg in bracket.legs.values())
            others = [item for item in self.brackets.values() if item is not bracket and item.con_id == bracket.con_id
                      and item.state in ("working", "open")]
            if not live_legs and not others and self.account_position(bracket.con_id) == 0:
                bracket.attention_cleared_at = now
                self.emit("INFO", f"{bracket.symbol}: needs-attention bracket is flat at IBKR — its slot is released")
                continue
            findings.append(Finding("attention_required", f"attention:{bracket.id}",
                                    f"{bracket.symbol}: bracket needs attention ({bracket.attention_reason}) — act in"
                                    " TWS"))
        self.track_unmanaged()
        return findings

    def apply_findings(self, findings: list[Finding]) -> None:
        """A mismatch pauses new entries until a later reconciliation is clean; exits never pause."""
        was_paused = bool(self.findings)
        for finding in findings:
            if finding.key not in self.findings:
                self.emit("CRITICAL", finding.message)
                self.queue_event("reconcile_mismatch", {"code": finding.code, "key": finding.key,
                                                        "message": finding.message})
        self.findings = {finding.key: finding for finding in findings}
        if self.findings and not was_paused:
            codes = sorted({finding.code for finding in findings})
            self.emit("CRITICAL", f"reconciliation mismatch ({', '.join(codes)}) — new entries paused;"
                      " exits continue")
            self.queue_event("entries_paused", {"codes": codes})
        elif not self.findings and was_paused:
            self.emit("INFO", "reconciliation clean — new entries resumed")
            self.queue_event("entries_resumed", {})

    async def reconcile(self, *, startup: bool = False, positions: bool | None = None) -> list[Finding]:
        """Compare the ledger with IBKR, scoped to the engine's at1: orders."""
        self.last_reconcile = self.now()
        if not await self.request_snapshots(positions=startup if positions is None else positions):
            findings = [Finding("snapshot_unavailable", "snapshot",
                                "TWS did not report open orders, executions, completed orders and positions in time")]
            self.apply_findings(findings)
            return findings
        findings: list[Finding] = await self.resolve_inflight()
        if startup:
            self.resend_lost_cancels()
        await self.sync_fills()
        self.observe_legs()
        for bracket in list(self.brackets.values()):
            self.refresh(bracket)
        findings += await self.compare_with_ibkr(startup=startup)
        bridge = await self.bridge_problem()
        if bridge is not None:
            findings.append(Finding("bridge_running", "bridge", f"{bridge} — the bridge and the engine must never"
                                    " send orders together; stop one of them"))
        self.apply_findings(findings)
        self.queue_event("reconciled", {"startup": startup, "findings": [item.code for item in findings]})
        await self.save()
        return findings

    def reconcile_due(self) -> bool:
        return self.last_reconcile is None or self.now() - self.last_reconcile >= RECONCILE_INTERVAL

    # ---- entries ------------------------------------------------------------------------
    def expiry_problem(self, intent: DecisionIntent, row: DecisionRow) -> str | None:
        created = parse_time(row.created_at)
        if created is None:
            return "decision timestamp unreadable"
        now = self.now()
        age = (now - created).total_seconds()
        # The risk approval expires with the intent; without one the engine's 15 s applies [L6].
        deadline = intent.expires_at or created + timedelta(seconds=DEFAULT_DECISION_TTL_S)
        if now >= deadline or age > MAX_DECISION_AGE_S:
            return f"decision expired ({age:.0f}s old)"
        return None

    async def symbol_problem(self, row: DecisionRow) -> tuple[str, str] | None:
        symbol, con_id = row.symbol, row.con_id
        # An unreconciled or unknown bracket may still hold shares, so its symbol stays blocked.
        if any(bracket.symbol == symbol and (bracket.state in ("sending", "working", "open", "unknown")
                                             or (bracket.state == "needs_attention"
                                                 and bracket.attention_cleared_at is None))
               for bracket in self.brackets.values()):
            return "symbol_has_bracket", "symbol already has an active bracket"
        if any(symbol == other or (con_id > 0 and con_id == other_con) for other, other_con in self.unmanaged_live()):
            return "symbol_unmanaged_order", "symbol has a live order at IBKR the engine does not manage"
        manual = await self.manual_symbols()
        if "*" in manual:
            return "owner_closing_all", "the owner's close-all is working"
        from trading.execution.manual_orders import normalize_symbol

        if normalize_symbol(symbol) in manual:
            return "symbol_manual_order", "symbol has a working manual order"
        if not await self.refresh_orders():  # the completed ones become the bracket's baseline (R-3)
            return "open_orders_unavailable", "TWS did not report its open and completed orders"
        with self.mirror.lock:
            busy = any(item.con_id == con_id and item.working and item.account in {"", self.account}
                       for item in self.mirror.account_orders.values())
        if busy:
            return "symbol_working_order", "symbol has a working order at IBKR (owner or another client)"
        # Exit caps read the whole account position, so the bot only enters a symbol the account does
        # not hold at all: the owner's own shares can then never be sold by a bracket [N1].
        held = self.account_position(con_id)
        if held is None:
            return "position_unknown", "account position unknown"
        if held != 0:
            return "symbol_held", f"account already holds {share_text(held)} — bot only trades symbols it alone holds"
        return None

    async def gate(self, row: DecisionRow) -> tuple[DecisionIntent | None, tuple[str, str] | None]:
        try:
            intent = parse_intent(row.payload_json)
        except Exception as exc:
            return None, ("decision_unreadable", f"decision unreadable ({type(exc).__name__})")
        refusal = self.authority.entry_refusal(row.strategy_name)
        if refusal is not None:
            return intent, refusal
        if intent is None:
            return None, ("no_trade_intent", "no trade intent")
        if (intent.quantity <= 0 or row.con_id <= 0 or not row.primary_exchange
                or not Decimal("0") < intent.stop < intent.entry < intent.target):
            return intent, ("invalid_trade_intent", "invalid trade intent")
        expired = self.expiry_problem(intent, row)
        if expired:
            return intent, ("decision_expired", expired)
        if not self.started:
            return intent, ("engine_not_reconciled", "engine not reconciled with IBKR yet")
        if not self.times.calendar_ok:
            return intent, ("calendar_unavailable", "market calendar unavailable — no new entries")
        if self.findings:
            return intent, ("entries_paused", "entries paused by a reconciliation mismatch")
        now = self.now()
        if now >= self.times.last_entry:
            return intent, ("after_last_entry", f"after {self.times.last_entry:%H:%M} — no new entries")
        family = strategy_family(row.strategy_name)
        if now >= self.entry_deadline(family):
            return intent, ("window_closed", f"{family} entry window closed")
        if intent.setup_fingerprint:
            for bracket in self.brackets.values():
                if bracket.setup_fingerprint == intent.setup_fingerprint:
                    return intent, ("setup_already_sent", f"setup already sent (decision {bracket.decision_id})")
        problem = await self.symbol_problem(row)
        if problem is not None:
            return intent, problem
        check = evaluate_new_trade(
            risk_book_from(self.brackets.values(), session_date=self.session_date, net_liquidation=self.net_liq,
                           unmanaged_symbols=frozenset(symbol for symbol, _ in self.unmanaged_live())),
            NewTrade(symbol=row.symbol, quantity=intent.quantity, entry=intent.entry, stop=intent.stop),
        )
        if not check.approved:
            detail = f"daily loss budget: {check.detail}" if check.code == "daily_risk_exceeded" else check.detail
            return intent, (str(check.code), detail)
        return intent, None

    async def skip(self, row: DecisionRow, code: str, reason: str, *, with_strategy: bool = True) -> None:
        recorded = await self.ledger.record_skip(
            decision_id=row.decision_id, account=self.account, session_date=self.session_date,
            strategy_name=row.strategy_name or "?", symbol=row.symbol, code=code, reason=reason,
        )
        self.processed.add(row.decision_id)
        if recorded:
            label = f" ({row.strategy_name})" if with_strategy and row.strategy_name else ""
            self.emit("SKIP", f"{row.symbol}{label}: {reason}")

    async def intake_decisions(self) -> None:
        rows = [row for row in await self.decisions() if row.decision_id not in self.processed]
        if not rows:
            return
        await self.authority.refresh()  # a revoke takes effect before the next entry
        for row in rows:
            if self.stop_event.is_set():
                return
            await self.process_decision(row)

    async def process_decision(self, row: DecisionRow) -> None:
        intent, refusal = await self.gate(row)
        if refusal is not None or intent is None:
            code, reason = refusal or ("no_trade_intent", "no trade intent")
            await self.skip(row, code, reason)
            return
        bid = await self.bid(row.con_id, row.primary_exchange)
        if bid is not None and bid <= intent.stop:
            await self.skip(row, "price_at_stop", f"price {bid} already at/below stop {intent.stop}")
            return
        expired = self.expiry_problem(intent, row)  # the quote wait can outlive the approval [L6]
        if expired:
            await self.skip(row, "decision_expired", expired)
            return
        # The fence is read last so it is as fresh as possible when orders leave.
        try:
            stopped = await self.kill_switch_active()
        except Exception as exc:
            await self.skip(row, "kill_switch_unreadable",
                            f"kill switch unreadable ({type(exc).__name__}) — fail closed", with_strategy=False)
            return
        if stopped:
            await self.skip(row, "kill_switch", "kill switch active", with_strategy=False)
            return
        await self.submit(row, intent)

    def new_bracket(self, row: DecisionRow, intent: DecisionIntent, ids: tuple[int, ...]) -> BracketRecord:
        bracket_id = new_bracket_id()
        now = self.now()
        parent_id, target_id, stop_id = ids

        def leg(code: LegCode, order_id: int, action: Literal["BUY", "SELL"], order_type: Literal["LMT", "STP"],
                tif: Literal["DAY", "GTC"], price: Decimal, parent: int | None, transmit: bool) -> LegRecord:
            return LegRecord(
                id=f"{bracket_id}:{code}", bracket_id=bracket_id, leg=code, client_id=self.client_id,
                order_id=order_id, order_ref=order_ref(bracket_id, code), action=action,
                order_type=order_type, tif=tif, quantity=intent.quantity, price=price,
                parent_order_id=parent, transmit=transmit, state="staged", created_at=now,
            )

        return BracketRecord(
            id=bracket_id, decision_id=row.decision_id, account=self.account, client_id=self.client_id,
            session_date=self.session_date, strategy_name=row.strategy_name, symbol=row.symbol, con_id=row.con_id,
            primary_exchange=row.primary_exchange, quantity=intent.quantity, entry_price=intent.entry,
            stop_price=intent.stop, target_price=intent.target, setup_fingerprint=intent.setup_fingerprint or None,
            state="sending", created_at=now,
            legs={
                "P": leg("P", parent_id, "BUY", "LMT", "DAY", intent.entry, None, False),
                "T": leg("T", target_id, "SELL", "LMT", "GTC", intent.target, parent_id, False),
                "S": leg("S", stop_id, "SELL", "STP", "GTC", intent.stop, parent_id, True),
            },
        )

    async def submit(self, row: DecisionRow, intent: DecisionIntent) -> None:
        if self.stop_event.is_set():  # a stop during the quote wait: never send after a stop [N6]
            self.emit("INFO", f"{row.symbol}: stop requested — entry not sent")
            return
        if not self.broker.isConnected():
            return  # left unprocessed; after the reconnect the decision is evaluated again (and expires)
        net_liq = self.net_liq
        if net_liq is None:
            await self.skip(row, "net_liquidation_unknown", "net liquidation unknown")
            return
        ids = await self.allocate_order_ids(3)
        bracket = self.new_bracket(row, intent, ids)
        with self.mirror.lock:  # the entry gate just read every completed order afresh (R-3)
            baseline = frozenset(item.perm_id for item in self.mirror.completed.values()
                                 if item.con_id == row.con_id and item.perm_id > 0)
        try:
            # Persisted (and the risk book re-checked atomically) before anything is sent.
            await self.ledger.stage_bracket(
                bracket, net_liquidation=net_liq,
                unmanaged_symbols=frozenset(symbol for symbol, _ in self.unmanaged_live()),
                payload={"strategy": row.strategy_name, "symbol": row.symbol,
                         "completed_before_entry": sorted(baseline)},
            )
        except RiskBookRefusal as exc:
            await self.skip(row, str(exc.check.code), exc.check.detail)
            return
        except DecisionAlreadyClaimed:
            self.processed.add(row.decision_id)
            return
        self.processed.add(row.decision_id)
        self.index(bracket)
        self.entry_baselines[bracket.id] = baseline
        self.saved[bracket.id] = self.signature(bracket)
        contract = stock(bracket.con_id, bracket.primary_exchange)
        try:
            for code in BRACKET_LEGS:
                leg = bracket.legs[code]
                self.broker.placeOrder(leg.order_id, contract, leg_ticket(leg, self.account))
        except Exception as exc:
            # In flight: reconciliation after the reconnect adopts it or marks it unknown, never resends.
            self.emit("CRITICAL", f"{bracket.symbol}: placing the bracket failed ({type(exc).__name__}: {exc}) —"
                      " left in flight, never resent; check TWS")
            self.apply_findings([*self.findings.values(), Finding(
                "send_failed", f"send-failed:{bracket.id}", f"{bracket.symbol}: bracket send interrupted")])
            return
        try:
            await self.ledger.mark_sent(bracket, payload={"order_ids": list(ids)})
        except Exception as exc:
            bracket.state = "working"
            for leg in bracket.legs.values():
                leg.state = "sent"
            self.unsettled.add(bracket.id)
            self.alert_once(("ledger", "sent", bracket.id), f"{bracket.symbol}: bracket sent but its outbox row"
                            f" could not be closed ({type(exc).__name__}: {exc}) — retried every tick")
        self.saved[bracket.id] = self.signature(bracket)
        self.emit("SENT", f"{bracket.symbol} ({bracket.strategy_name}): BUY {bracket.quantity} @ {bracket.entry_price}"
                  f" | stop {bracket.stop_price} | target {bracket.target_price} (orders {ids[0]}-{ids[-1]})")

    # ---- loop ------------------------------------------------------------------------------
    async def close_out(self) -> None:
        if not self.flattened and self.past(self.times.flatten):
            self.emit("INFO", f"{self.times.flatten:%H:%M} ET: flattening all engine positions")
            await self.flatten()
            self.flattened = True
        elif self.flatten_waiting:
            await self.retry_flattens()
        if self.flattened and "alert" not in self.alerts_done and self.past(self.times.flatten_alert):
            self.alerts_done.add("alert")
            self.alert_not_flat()
        if self.flattened and "verify" not in self.alerts_done and self.past(self.times.verify):
            self.alerts_done.add("verify")
            self.verify_flat()

    async def run_step(self, source: str, step: Callable[[], Awaitable[object]]) -> None:
        """A failing step (e.g. a locked ledger) is CRITICAL once per streak and never blocks a tick [M1]."""
        try:
            await step()
        except (ExecutionRefused, ExecutionAuthorityError):
            raise
        except Exception as exc:
            failures = self.intake_failures.get(source, 0)
            self.intake_failures[source] = failures + 1
            if failures == 0:
                self.emit("CRITICAL", f"{source} step failed ({type(exc).__name__}: {exc}) — no new entries from"
                          " it until it recovers; open brackets are still managed")
            return
        failures = self.intake_failures.pop(source, 0)
        if failures:
            self.emit("INFO", f"{source} step recovered after {failures} failed attempts")

    async def renew_subscriptions(self) -> None:
        """TWS 1101 (connectivity restored, data lost): positions and the account summary are requested again."""
        self.mirror.data_lost.clear()
        self.emit("INFO", "TWS lost its data subscriptions (1101): positions and net liquidation requested again;"
                  " reconciling")
        self.broker.cancelAccountSummary(SUMMARY_REQUEST_ID)
        self.request_net_liquidation()
        await self.reconcile(positions=True)  # re-subscribes positions from scratch (review C2-10)

    async def tick(self) -> None:
        # Every exit is capped by the positions: lost subscriptions are renewed before anything else.
        if self.mirror.data_lost.is_set():
            await self.run_step("reconciliation", self.renew_subscriptions)
        # Management first; reconciliation and intake last, so nothing they raise can delay a
        # cancel, a resize or the flatten.
        await self.run_step("fill", self.sync_fills)
        self.observe_legs()
        for bracket in list(self.brackets.values()):
            self.refresh(bracket)
        self.expire_entries()
        self.confirm_modifies()
        for bracket in list(self.brackets.values()):
            self.resize_children(bracket)
        await self.close_out()
        await self.advance_fallbacks()
        for bracket in list(self.brackets.values()):
            self.check_protection(bracket)
            self.check_account_position(bracket)
            self.check_unshown_sales(bracket)
            self.check_dangling_exits(bracket)
        await self.run_step("ledger", self.save)
        if self.reconcile_due():
            await self.run_step("reconciliation", self.reconcile)
        await self.run_step("decision", self.intake_decisions)
        await self.run_step("ledger", self.save)

    async def interrupted(self) -> int | None:
        if self.stop_event.is_set():
            self.emit("INFO", "stop requested — saving state")
            code = EXIT_OK
        elif not self.broker.isConnected():
            self.emit("CRITICAL", "TWS connection lost — engine stopping; open brackets keep their GTC stop and"
                      f" target at IBKR. Exit {EXIT_RESTART}.")
            code = EXIT_RESTART
        else:
            return None
        if self.past(self.times.flatten):
            self.alert_not_flat()  # after the flatten nothing else will watch these positions
        await self.run_step("ledger", self.save)
        return code

    async def finish(self) -> int:
        """At the stop checkpoint: no more management; stay for the final alert only while not flat."""
        await self.run_step("fill", self.sync_fills)
        for bracket in list(self.brackets.values()):
            self.refresh(bracket)
        if not self.flattened:  # started too late for the regular pass: the first and only attempt
            if self.past(self.times.closes_at):  # never a late flatten after the close (review C2-7)
                self.emit("INFO", f"past the {self.times.closes_at:%H:%M} ET close — no late flatten is sent")
            else:
                await self.flatten(LATE_FLATTEN_DISCOUNT)
            self.flattened = True
        await self.run_step("ledger", self.save)
        while self.positions_not_flat():
            code = await self.interrupted()
            if code is not None:
                return code
            # A fallback sell still to send is tried until the close (review R-1); nothing else waits.
            if self.past(self.times.final_alert) and not (self.fallback_sell_due() and not self.past(
                    self.times.closes_at)):
                self.fail_fallbacks_at_the_close()
                self.alert_not_flat()
                break
            await self.pause(self.loop_seconds)
            await self.run_step("fill", self.sync_fills)
            self.observe_legs()
            for bracket in list(self.brackets.values()):
                self.refresh(bracket)
            if self.flatten_waiting:
                await self.retry_flattens()
            await self.advance_fallbacks()
            for bracket in list(self.brackets.values()):
                self.check_protection(bracket)
            await self.run_step("ledger", self.save)
        else:
            if self.short_positions(unexplained=True):
                self.alert_not_flat()  # a short in a traded symbol is never reported FLAT
            elif self.alert_raised:
                self.emit("FLAT", "no open engine positions")
        realized = sum((bracket.realized_pnl for bracket in self.brackets.values()
                        if bracket.session_date == self.session_date), Decimal("0"))
        self.emit("INFO", f"session end: realized P&L {realized}")
        await self.run_step("ledger", self.save)
        return EXIT_OK

    def fallback_sell_due(self) -> bool:
        """A flatten fallback has cancelled (or is cancelling) target and stop and not sent its market sell."""
        return any(bracket.state == "open" and bracket.flatten_state == "cancelling"
                   for bracket in self.brackets.values())

    def fail_fallbacks_at_the_close(self) -> None:
        """Past the close a fallback sell still to send has failed: CRITICAL once each (review R-1)."""
        if not self.past(self.times.closes_at):
            return
        for bracket in self.brackets.values():
            if bracket.state == "open" and bracket.flatten_state == "cancelling":
                self.retry_fallback_sell(bracket, "the close came first")

    async def report_after_close(self) -> int:
        """Started after the stop time: nothing new is sent, not even a late flatten (review C2-7).

        Like the bridge's main(): the ledger is read and every bracket it still
        shows open is reported. Only a flatten fallback that was cancelling
        target and stop when the engine stopped is finished, before the close,
        after the full start checks; its shares are flagged unprotected at once
        (review R-4).
        """
        self.emit("INFO", f"started after {self.times.stop:%H:%M} ET for {self.session_date} — nothing new is"
                  " managed")
        try:
            await self.load()
        except Exception as exc:
            self.emit("CRITICAL", f"execution ledger unreadable ({type(exc).__name__}: {exc}) — check TWS")
            return EXIT_OK
        unfinished = [bracket for bracket in self.positions_not_flat()
                      if bracket.state == "open" and bracket.flatten_state == "cancelling"]
        for bracket in self.positions_not_flat():
            if bracket in unfinished:
                self.flag_unprotected(bracket, "the flatten fallback was cancelling target and stop when the engine"
                                      " stopped and its market sell was never sent")
            elif bracket.net_held < 0:
                self.emit("CRITICAL", self.not_flat_message(bracket))
            else:
                self.emit("CRITICAL", f"{bracket.symbol}: still open per the execution ledger ({bracket.net_held} sh)"
                          " — act in TWS")
        if not unfinished or self.past(self.times.closes_at):
            return EXIT_OK
        await self.start()  # authority, code, no bridge, paper accounts, order ids, reconciliation
        while self.fallback_sell_due() and not self.past(self.times.closes_at):
            code = await self.interrupted()
            if code is not None:
                return code
            await self.run_step("fill", self.sync_fills)
            self.observe_legs()
            for bracket in list(self.brackets.values()):
                self.refresh(bracket)
            for bracket in unfinished:  # nothing else is managed after the stop time
                if bracket.flatten_state == "cancelling":
                    try:
                        await self.advance_fallback(bracket)
                    except (ExecutionRefused, ExecutionAuthorityError):
                        raise
                    except Exception as exc:
                        self.alert_once((bracket.id, "fallback", "step", type(exc).__name__),
                                        f"{bracket.symbol}: flatten fallback step failed ({type(exc).__name__}: {exc})"
                                        " — retried every tick; act in TWS if it persists")
            await self.run_step("ledger", self.save)
            await self.pause(self.loop_seconds)
        self.fail_fallbacks_at_the_close()
        await self.run_step("ledger", self.save)
        return EXIT_OK

    async def run(self) -> int:
        """The session loop: a slow TWS or a lost connection returns EXIT_RESTART, refusals EXIT_REFUSED."""
        try:
            if self.past(self.times.stop):  # first, as the bridge's main(): no TWS state matters any more
                return await self.report_after_close()
            code = await self.interrupted()
            if code is not None:
                return code
            await self.start()
            while True:
                code = await self.interrupted()
                if code is not None:
                    return code
                if self.past(self.times.stop):
                    return await self.finish()
                await self.tick()
                await self.pause(self.loop_seconds)
        except ExecutionRefused as exc:
            if exc.code in TRANSIENT_REFUSALS:  # slow, not unsafe: a supervisor may retry (review C2-3)
                self.emit("CRITICAL", f"TWS not ready: {exc} — exit {EXIT_RESTART} for a supervised restart")
                return EXIT_RESTART
            self.emit("CRITICAL", f"refusing: {exc}")
            return EXIT_REFUSED
        except ExecutionAuthorityError as exc:
            self.emit("CRITICAL", f"refusing: {exc}")
            return EXIT_REFUSED


__all__ = [
    "ENGINE_VERSION",
    "EVENT_KINDS",
    "EXIT_OK",
    "EXIT_REFUSED",
    "EXIT_RESTART",
    "BrokerPort",
    "DecisionIntent",
    "DecisionRow",
    "ExecutionRefused",
    "ExitPlan",
    "Finding",
    "FillSnapshot",
    "LiveExecutor",
    "SellFacts",
    "SessionTimes",
    "accepted_decisions",
    "format_event_line",
    "leg_ticket",
    "order",
    "parse_intent",
    "plan_exit",
    "session_times",
    "stock",
]

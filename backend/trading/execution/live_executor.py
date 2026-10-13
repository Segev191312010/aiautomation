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
* Order ids are ``max(nextValidId, ledger max + 1)``; ``reqIds`` is sent at
  start and on every reconnect.
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
  is not confirmed: cancel target and stop, CONFIRM both cancels, then one
  MARKET sell of the ledger quantity capped by the live position less every
  other working sell. No second sell ever follows a manual close [H2], and a
  restart never starts a flatten again [N5].
* Reconciliation at start, on every reconnect and every two minutes, scoped
  to the ``at1:`` prefix: a mismatch pauses new entries and raises CRITICAL;
  ``manual:`` orders and positions the engine did not open are left alone.
* Every stdout line is ``HH:MM:SS ET  <KIND> <text>`` (the notifier format).
"""
from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, datetime, time as clock_time, timedelta, timezone
from decimal import ROUND_CEILING, ROUND_DOWN, Decimal, InvalidOperation
import json
import sys
import threading
import time
from typing import Any, Literal, Protocol
from zoneinfo import ZoneInfo

from db.core import get_db
from trading.execution.promotion import EngineAuthority, ExecutionAuthorityError, is_paper_account
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


ENGINE_VERSION = "c2.1"
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
]
EventSink = Callable[[str], None]
_STDOUT_LOCK = threading.RLock()


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
    def reqAccountSummary(self, reqId: int, groupName: str, tags: str) -> None: ...
    def cancelAccountSummary(self, reqId: int) -> None: ...
    def reqMktData(self, reqId: int, contract: Any, genericTickList: str, snapshot: bool,
                   regulatorySnapshot: bool, mktDataOptions: list[Any]) -> None: ...
    def cancelMktData(self, reqId: int) -> None: ...
    def reqMarketDataType(self, marketDataType: int) -> None: ...


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

    async def start(self) -> None:
        """Authority, paper check, fresh order ids, then a full reconciliation before any entry."""
        active = await self.authority.require_start()
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
        """``max(nextValidId, ledger max + 1)``: an id is never reused, even after TWS resets its sequence."""
        ledger_next = await self.ledger.max_order_id(self.client_id) + 1
        with self.mirror.lock:
            if self.mirror.next_id is None:
                raise ExecutionRefused("no_order_id", "TWS has not provided a valid order id")
            first = max(self.mirror.next_id, ledger_next)
            self.mirror.next_id = first + count
            ids = tuple(range(first, first + count))
            self.mirror.own_order_ids.update(ids)
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

    def order_shows(self, order_id: int, total: int, price: Decimal, kind: str) -> bool:
        """True once TWS has echoed this quantity and price for the order through openOrder."""
        with self.mirror.lock:
            cached = self.mirror.order_objects.get(order_id)
        if cached is None or whole(getattr(cached, "totalQuantity", 0)) != total:
            return False
        try:
            shown = Decimal(str(getattr(cached, "lmtPrice" if kind == "LMT" else "auxPrice")))
        except (InvalidOperation, TypeError, ValueError):
            return False
        return shown.is_finite() and abs(shown - price) < TICK / 2

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
                   snapshot: FillSnapshot, plan: ExitPlan) -> None:
        held = share_text(snapshot.position or Decimal("0"))
        outcome = f"limited to {plan.working} sh" if plan.working > 0 else "not sent"
        message = (f"{bracket.symbol}: account holds {held} sh but the bracket expects {plan.capped_from} —"
                   f" {purpose} of {LEG_NAMES[code]} {order_id} {outcome}")
        key = (bracket.id, code, purpose, "capped", str(plan.working))
        if self.gap_age(bracket) >= POSITION_GAP_GRACE:
            self.alert_once(key, f"{message}; act in TWS")
        else:  # a position update may still be on its way: the cap applies, the alarm waits [N4]
            self.alert_once((*key, "early"), f"{message} — re-checked while the position updates", kind="INFO")

    def modify_exit(self, bracket: BracketRecord, code: LegCode, *, price: Decimal, purpose: str,
                    only_if_changed: bool = False, retry: bool = False) -> ExitResult:
        leg = bracket.legs.get(code)
        if leg is None or code not in ("T", "S"):
            return ExitResult("blocked")
        order_id = leg.order_id
        snapshot = self.fill_snapshot(bracket)  # taken after every wait, right before placeOrder [H1]
        plan = plan_exit(snapshot, code)
        if plan.problem:
            self.alert_once(
                (bracket.id, code, purpose, "blocked", plan.problem),
                f"{bracket.symbol}: {purpose} of {LEG_NAMES[code]} {order_id} not sent — {plan.problem}; act in TWS",
            )
            return ExitResult("blocked")
        capped = plan.capped_from is not None
        if capped:
            self.report_cap(bracket, code, order_id, purpose, snapshot, plan)
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
        same = previous is not None and (previous.purpose, previous.total, previous.price) == (purpose, plan.total, price)
        self.pending_modifies[order_id] = PendingModify(
            bracket_id=bracket.id, leg=code, purpose=purpose, kind=leg.order_type, total=plan.total, price=price,
            sends=previous.sends + 1 if same and previous is not None else 1,
        )
        self.queue_event("modify_sent", {"order_id": order_id, "purpose": purpose, "total": plan.total,
                                         "price": str(price)}, bracket, leg)
        return ExitResult("sent", plan.working, capped)

    def modify_settled(self, order_id: int) -> bool:
        """A modification counts as done only once TWS has echoed it [N2]."""
        pending = self.pending_modifies.get(order_id)
        if pending is None:
            return True
        if self.accepted(order_id, pending.total, pending.price, pending.kind):
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
                                      only_if_changed=True, retry=True)
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
        for code in ("T", "S"):
            order_id = bracket.order_id(code)  # type: ignore[arg-type]
            price = self.leg_price(bracket, code)  # type: ignore[arg-type]
            if not self.mirror.live(order_id):
                settled = self.report_dead_leg(bracket, code) and settled  # type: ignore[arg-type]
                continue
            if price is None or order_id is None:
                continue
            result = self.modify_exit(bracket, code, price=price, purpose="resize", only_if_changed=True)  # type: ignore[arg-type]
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
        again, by a later checkpoint or after a restart [N5]. Only when the
        reprice is impossible does the confirmed-cancel fallback take over.
        """
        if bracket.state != "open" or bracket.remaining <= 0 or bracket.flatten_attempted:
            return False
        bracket.flatten_attempted = True
        self.queue_event("flatten_attempt", {"discount": str(discount)}, bracket)
        await self.persist(bracket)  # an exit is never blocked by the ledger: a failure only alerts
        target = bracket.legs["T"]
        if not self.mirror.live(target.order_id):
            status = self.mirror.status(target.order_id) or "unknown"
            if status == "Filled":
                bracket.flatten_state = "skipped"
                self.emit("INFO", f"{bracket.symbol}: target {target.order_id} already filled; awaiting executions")
                return False
            return await self.start_fallback(bracket, reason=f"target {target.order_id} is not live ({status})")
        quote = await self.flatten_quote(bracket, discount)
        if quote is None:
            return await self.start_fallback(bracket, reason="no quote to reprice the target")
        price, source = quote
        result = self.modify_exit(bracket, "T", price=price, purpose="flatten")
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

    async def sellable(self, bracket: BracketRecord) -> tuple[int, int, Decimal, int] | str:
        """``(quantity, held, position, reserved)`` a fallback may sell, or why it may sell nothing.

        The quantity is the ledger's shares, capped by the live account position
        less every other working sell in the contract (the owner's TWS orders,
        ``manual:`` orders): never a second sell next to a manual close [H2].
        """
        snapshot = self.fill_snapshot(bracket)
        held = snapshot.parent - max(snapshot.recorded_exits, snapshot.target + snapshot.stop)
        if held < 0:
            return "exit fills exceed entry fills"
        reserved = await self.other_working_sells(bracket)
        position = self.account_position(bracket.con_id)  # re-read after the open-orders wait
        if reserved is None:
            return "open orders unknown"
        if position is None:
            return "account position unknown"
        return min(held, max(0, int(position) - reserved)), held, position, reserved

    def nothing_to_sell(self, bracket: BracketRecord, held: int, position: Decimal, reserved: int,
                        *, stop_live: bool) -> None:
        bracket.flatten_state = "skipped"
        if held > 0:
            protection = "its stop stays live" if stop_live else "its stop is cancelled"
            self.emit("CRITICAL", f"{bracket.symbol}: account holds {share_text(position)} sh with {reserved}"
                      f" reserved by working sells — no market sell for the {held} sh the engine expects"
                      f" ({protection}); act in TWS if not flat")

    async def start_fallback(self, bracket: BracketRecord, *, reason: str) -> bool:
        """Cancel target and stop; a market sell follows only once both cancels are confirmed."""
        if bracket.flatten_state not in ("none", "repricing"):
            return False
        # Never cancel the protective stop for a sell that could not be sent anyway.
        facts = await self.sellable(bracket)
        if isinstance(facts, str):
            bracket.flatten_state = "failed"
            self.emit("CRITICAL", f"{bracket.symbol}: flatten fallback not started ({reason}) — {facts}; act in TWS")
            await self.persist(bracket)
            return False
        quantity, held, position, reserved = facts
        if quantity <= 0:
            self.nothing_to_sell(bracket, held, position, reserved, stop_live=True)
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
        for code in ("T", "S"):
            if self.mirror.status(bracket.order_id(code)) in WORKING_ORDER_STATUSES:  # type: ignore[arg-type]
                self.cancel_leg(bracket, code, "flatten fallback")  # type: ignore[arg-type]
        return True

    def leg_dead(self, bracket: BracketRecord, code: LegCode) -> bool:
        leg = bracket.legs.get(code)
        if leg is None:
            return True
        status = self.mirror.status(leg.order_id)
        if status is None:
            with self.mirror.lock:
                status = next((row.status for row in self.mirror.completed.values()
                               if row.order_ref == leg.order_ref), None)
        return status in DEAD_ORDER_STATUSES

    async def advance_fallbacks(self) -> None:
        for bracket in list(self.brackets.values()):
            if bracket.flatten_state == "repricing":
                await self.follow_reprice(bracket)
                continue
            if bracket.flatten_state != "cancelling":
                continue
            if bracket.state != "open":
                bracket.flatten_state = "skipped"  # fills that raced the cancels closed it
                continue
            if bracket.id not in self.cancel_requested_at:
                # First look in this run: a restart may have come before the cancels reached TWS.
                self.cancel_requested_at[bracket.id] = self.now()
                for code in ("T", "S"):
                    if self.mirror.status(bracket.order_id(code)) in WORKING_ORDER_STATUSES:  # type: ignore[arg-type]
                        self.cancel_leg(bracket, code, "flatten fallback, after a restart")  # type: ignore[arg-type]
            if not all(self.leg_dead(bracket, code) for code in ("T", "S")):
                started = self.cancel_requested_at.setdefault(bracket.id, self.now())
                if self.now() - started >= FLATTEN_CANCEL_TIMEOUT:
                    bracket.flatten_state = "failed"
                    self.emit("CRITICAL", f"{bracket.symbol}: TWS did not confirm cancelling the target and stop"
                              f" within {int(FLATTEN_CANCEL_TIMEOUT.total_seconds())}s — no market sell sent; act in TWS")
                continue
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
        repriced = shown is not None and bracket.flatten_price is not None and abs(shown - bracket.flatten_price) < TICK / 2
        # A reprice dropped on the way (capped to nothing, or blocked with its own alert) is never retried.
        bracket.flatten_state = "repriced" if repriced or status == "Filled" or bracket.state != "open" else "failed"

    async def other_working_sells(self, bracket: BracketRecord) -> int | None:
        """Shares every other working SELL in the contract may still sell (owner, manual:, other apps)."""
        if not await self.refresh_open_orders():
            return None
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

    async def send_fallback_sell(self, bracket: BracketRecord) -> None:
        """One market sell of the ledger quantity, capped by the live position less other working sells."""
        await self.sync_fills()
        self.refresh(bracket)
        if bracket.state != "open":
            bracket.flatten_state = "skipped"  # the cancels raced fills that closed it
            return
        facts = await self.sellable(bracket)  # one more look: the owner may have acted meanwhile
        if isinstance(facts, str):
            bracket.flatten_state = "failed"
            self.emit("CRITICAL", f"{bracket.symbol}: flatten fallback stopped — {facts}; no market sell sent;"
                      " act in TWS")
            return
        quantity, held, position, reserved = facts
        if quantity <= 0:
            self.nothing_to_sell(bracket, held, position, reserved, stop_live=False)
            return
        order_id = (await self.allocate_order_ids(1))[0]
        leg = LegRecord(
            id=f"{bracket.id}:F", bracket_id=bracket.id, leg="F", client_id=self.client_id, order_id=order_id,
            order_ref=order_ref(bracket.id, "F"), action="SELL", order_type="MKT", tif="DAY", quantity=quantity,
            price=None, parent_order_id=None, transmit=True, state="staged", created_at=self.now(),
        )
        try:
            await self.ledger.add_flatten_leg(bracket, leg, payload={
                "held": held, "position": str(position), "reserved": reserved, "quantity": quantity,
            })
        except Exception as exc:
            bracket.flatten_state = "failed"
            self.emit("CRITICAL", f"{bracket.symbol}: fallback market sell not recorded ({type(exc).__name__}:"
                      f" {exc}) — not sent; act in TWS")
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

    def alert_not_flat(self) -> bool:
        positions = self.positions_not_flat()
        for bracket in positions:
            self.emit("CRITICAL", self.not_flat_message(bracket))
        if positions:
            self.alert_raised = True
        return bool(positions)

    def verify_flat(self) -> None:
        """Alert only: a second automatic sell could race the owner's manual close [H2]."""
        if not self.alert_not_flat():
            self.emit("FLAT", "no open engine positions")

    def check_protection(self, bracket: BracketRecord) -> None:
        """Every tick: an open position without a live stop is CRITICAL once [M2]."""
        if bracket.state != "open" or bracket.net_held <= 0 or self.mirror.live(bracket.order_id("S")):
            return
        if bracket.flatten_state in ("cancelling", "selling"):
            return  # the fallback cancelled it on purpose; its own alerts cover the gap
        if self.mirror.status(bracket.order_id("T")) == "Filled":
            return  # the target's fill (and the OCA cancel of the stop) is still being reported
        self.report_dead_leg(bracket, "S")

    def report_dangling(self, bracket: BracketRecord, order_id: int) -> None:
        """A live exit on a finished bracket could sell shares the engine no longer holds [N2]."""
        self.unmanaged[order_id] = (bracket.symbol, bracket.con_id)  # blocks the symbol, takes a slot
        self.alert_once(
            (bracket.id, "dangling", str(order_id)),
            f"{bracket.symbol}: order {order_id} is still live at IBKR for a {bracket.state} bracket — check TWS",
        )

    def check_dangling_exits(self, bracket: BracketRecord) -> None:
        for code in ("T", "S"):
            order_id = bracket.order_id(code)  # type: ignore[arg-type]
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
    async def refresh_open_orders(self) -> bool:
        """A fresh snapshot of every client's working orders (the owner's TWS orders included)."""
        with self.mirror.lock:
            self.mirror.account_orders.clear()
        self.mirror.open_orders_done.clear()
        self.broker.reqAllOpenOrders()
        return await self.wait_for(self.mirror.open_orders_done)

    async def request_snapshots(self, *, positions: bool) -> bool:
        mirror = self.mirror
        with mirror.lock:
            mirror.account_orders.clear()
        for event in (mirror.open_orders_done, mirror.exec_done, mirror.completed_done):
            event.clear()
        self.broker.reqAllOpenOrders()
        self.broker.reqExecutions(EXEC_REQUEST_ID, execution_filter(self.client_id))
        self.broker.reqCompletedOrders(True)
        if positions:
            mirror.positions_done.clear()
            self.broker.reqPositions()  # stays subscribed: every exit is capped by it
        waits = (mirror.open_orders_done, mirror.exec_done, mirror.completed_done, mirror.positions_done)
        results = [await self.wait_for(event) for event in waits]
        return all(results)

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
                                        f"{item.symbol or '?'}: order {item.order_id} tagged {item.order_ref} is working"
                                        " at IBKR but is not a managed engine order — check TWS"))
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
            self.emit("CRITICAL", f"reconciliation mismatch ({', '.join(codes)}) — new entries paused; exits continue")
            self.queue_event("entries_paused", {"codes": codes})
        elif not self.findings and was_paused:
            self.emit("INFO", "reconciliation clean — new entries resumed")
            self.queue_event("entries_resumed", {})

    async def reconcile(self, *, startup: bool = False) -> list[Finding]:
        """Compare the ledger with IBKR, scoped to the engine's at1: orders."""
        self.last_reconcile = self.now()
        if not await self.request_snapshots(positions=startup):
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
        if not await self.refresh_open_orders():
            return "open_orders_unavailable", "TWS did not report its open orders"
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

        def leg(code: LegCode, order_id: int, action: str, order_type: str, tif: str, price: Decimal,
                parent: int | None, transmit: bool) -> LegRecord:
            return LegRecord(
                id=f"{bracket_id}:{code}", bracket_id=bracket_id, leg=code, client_id=self.client_id,
                order_id=order_id, order_ref=order_ref(bracket_id, code), action=action,  # type: ignore[arg-type]
                order_type=order_type, tif=tif, quantity=intent.quantity, price=price,  # type: ignore[arg-type]
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
        try:
            # Persisted (and the risk book re-checked atomically) before anything is sent.
            await self.ledger.stage_bracket(
                bracket, net_liquidation=net_liq,
                unmanaged_symbols=frozenset(symbol for symbol, _ in self.unmanaged_live()),
                payload={"strategy": row.strategy_name, "symbol": row.symbol},
            )
        except RiskBookRefusal as exc:
            await self.skip(row, str(exc.check.code), exc.check.detail)
            return
        except DecisionAlreadyClaimed:
            self.processed.add(row.decision_id)
            return
        self.processed.add(row.decision_id)
        self.index(bracket)
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

    async def tick(self) -> None:
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
            await self.flatten(LATE_FLATTEN_DISCOUNT)
            self.flattened = True
        await self.run_step("ledger", self.save)
        while self.positions_not_flat():
            code = await self.interrupted()
            if code is not None:
                return code
            if self.past(self.times.final_alert):
                self.alert_not_flat()
                break
            await self.pause(self.loop_seconds)
            await self.run_step("fill", self.sync_fills)
            self.observe_legs()
            for bracket in list(self.brackets.values()):
                self.refresh(bracket)
            await self.advance_fallbacks()
            await self.run_step("ledger", self.save)
        else:
            if self.alert_raised:
                self.emit("FLAT", "no open engine positions")
        realized = sum((bracket.realized_pnl for bracket in self.brackets.values()
                        if bracket.session_date == self.session_date), Decimal("0"))
        self.emit("INFO", f"session end: realized P&L {realized}")
        await self.run_step("ledger", self.save)
        return EXIT_OK

    async def run(self) -> int:
        """The session loop. Refusals return EXIT_REFUSED; a lost connection returns EXIT_RESTART."""
        try:
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
        except (ExecutionRefused, ExecutionAuthorityError) as exc:
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

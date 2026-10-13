"""Thread-safe mirror of the IBKR TWS API callbacks the execution engine reads.

The official ``ibapi`` reader thread calls these ``EWrapper`` methods; the
engine reads the mirror under one lock. Nothing here sends a request or
decides anything. The callback handling is ported from the reviewed PAPER
bridge (``scripts_local/paper_bridge.py`` v5) with its review lessons:

* another client's order never overwrites this client's order of the same id
  (TWS numbers every API client's orders from 1);
* fills never decrease, and an execution correction (same execId up to its
  final period, higher suffix) replaces the execution it corrects (L5);
* only this client's executions are mirrored, never the owner's TWS trades;
* any TWS error on one of the engine's own order ids is CRITICAL unless it is
  a known informational notice (N2, L1, L8).
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import threading
from typing import Any


LIVE_ORDER_STATUSES = frozenset({"ApiPending", "PendingSubmit", "PreSubmitted", "Submitted"})
WORKING_ORDER_STATUSES = LIVE_ORDER_STATUSES | {"PendingCancel"}  # may still fill
DEAD_ORDER_STATUSES = frozenset({"Cancelled", "ApiCancelled", "Inactive", "Filled"})
PARENT_CANCELLED_STATUSES = frozenset({"Cancelled", "ApiCancelled", "Inactive"})
# 202 cancel confirmed, 399 order warning, 2109/2148 order event warnings, 10311 direct routing.
ORDER_INFO_CODES = frozenset({202, 399, 2109, 2148, 10311})
# 1100 connectivity lost, 1300 socket port reset, 2110 TWS-to-server connectivity broken.
CRITICAL_TWS_CODES = frozenset({1100, 1300, 2110})
FARM_BROKEN_CODES = frozenset({2103, 2105, 2157})  # logged; the other 21xx farm notices are dropped
QUOTE_TICKS = {1: "bid", 2: "ask", 4: "last", 66: "bid", 67: "ask", 68: "last"}  # 66-68 are delayed
_UNSET_LIMIT = Decimal("1e12")  # ibapi 9.81 marks an unset double with 1.8e308

AlertSink = Callable[[str, str], None]


def decimal_or_none(value: object) -> Decimal | None:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return number if number.is_finite() and abs(number) < _UNSET_LIMIT else None


def whole(value: object) -> int:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return 0
    return int(number) if number.is_finite() else 0


def execution_key(exec_id: str) -> tuple[str, int]:
    """IBKR marks a correction by changing only the digits after the execId's final period."""
    base, dot, suffix = exec_id.rpartition(".")
    if dot and base and suffix.isdigit():
        return base, int(suffix)
    return exec_id, 0


def execution_day(value: object) -> date | None:
    """Trade date of an execution time such as '20261013  10:00:05' (TWS local time)."""
    text = str(value or "").strip()
    if len(text) < 8 or not text[:8].isdigit():
        return None
    try:
        return datetime.strptime(text[:8], "%Y%m%d").date()
    except ValueError:
        return None


@dataclass(frozen=True, slots=True)
class ExecutionRow:
    exec_id: str
    base: str
    version: int  # correction sequence: the digits after the execId's final period
    order_id: int
    perm_id: int
    client_id: int
    shares: int
    price: Decimal
    side: str
    account: str
    order_ref: str
    exec_time: str
    day: date | None
    con_id: int
    symbol: str


@dataclass(frozen=True, slots=True)
class OpenOrderRow:
    status: str
    symbol: str
    con_id: int
    order_ref: str


@dataclass(slots=True)
class AccountOrder:
    """One open order of any client (or TWS itself), from a reqAllOpenOrders snapshot."""

    client_id: int
    order_id: int
    perm_id: int
    account: str
    con_id: int
    symbol: str
    action: str
    order_type: str
    order_ref: str
    total: Decimal
    status: str
    remaining: Decimal | None = None

    @property
    def open_quantity(self) -> Decimal:
        return self.total if self.remaining is None else self.remaining

    @property
    def working(self) -> bool:
        return not self.status or self.status in WORKING_ORDER_STATUSES


@dataclass(frozen=True, slots=True)
class CompletedOrderRow:
    """A finished order TWS reports through reqCompletedOrders (any API client)."""

    order_id: int
    client_id: int
    perm_id: int
    order_ref: str
    status: str
    con_id: int
    symbol: str


class TwsMirror:
    """IBKR callback state shared between the API reader thread and the engine."""

    def __init__(self, client_id: int, *, alert: AlertSink | None = None) -> None:
        if client_id <= 0:
            raise ValueError("the engine needs a dedicated positive client id")
        self.client_id = client_id
        self.alert: AlertSink = alert or (lambda kind, text: None)
        self.lock = threading.RLock()
        self.next_id: int | None = None
        self.accounts: list[str] = []
        self.ready = threading.Event()
        self.accounts_event = threading.Event()
        self.order_status: dict[int, str] = {}
        self.order_filled: dict[int, int] = {}  # orderStatus cumulative filled, never decreasing
        self.open_orders: dict[int, OpenOrderRow] = {}
        self.order_objects: dict[int, Any] = {}  # the Order TWS last reported per own order id
        self.order_echoes: dict[int, int] = {}  # own order id -> openOrder reports received so far
        self.perm_ids: dict[int, int] = {}
        self.order_refs: dict[int, str] = {}
        self.open_orders_done = threading.Event()
        self.account_orders: dict[tuple[int, int, int], AccountOrder] = {}
        self.completed: dict[tuple[str, int], CompletedOrderRow] = {}  # (orderRef, permId) -> row
        self.completed_done = threading.Event()
        self.execs: dict[str, ExecutionRow] = {}  # execId without its correction suffix -> newest
        self.exec_done = threading.Event()
        self.commissions: dict[str, tuple[Decimal, str]] = {}  # execId -> (commission, currency)
        self.positions: dict[tuple[str, int], Decimal] = {}  # (account, conId) -> shares
        self.position_symbols: dict[tuple[str, int], str] = {}
        self.positions_done = threading.Event()
        self.net_liq_by_account: dict[str, Decimal] = {}
        self.summary_done = threading.Event()
        self.own_order_ids: set[int] = set()
        self.order_errors: dict[int, int] = {}  # own order id -> non-informational TWS errors so far
        self.error_details: dict[int, tuple[int, str]] = {}
        self.quotes: dict[int, dict[str, Decimal]] = {}
        self.delayed_quotes: set[int] = set()
        self.data_types: dict[int, int] = {}

    # ---- connection ------------------------------------------------------------
    def begin_connection(self) -> None:
        """Forget connection facts before a (re)connect: they must be reported afresh."""
        with self.lock:
            self.accounts = []
        self.accounts_event.clear()
        self.ready.clear()

    def nextValidId(self, orderId: int) -> None:
        with self.lock:
            self.next_id = max(self.next_id or 0, int(orderId))
        self.ready.set()

    def managedAccounts(self, accountsList: str) -> None:
        with self.lock:
            self.accounts = [account.strip() for account in str(accountsList).split(",") if account.strip()]
        self.accounts_event.set()

    def connectionClosed(self) -> None:
        self.alert("CRITICAL", "TWS connection closed")

    # ---- orders ----------------------------------------------------------------
    def orderStatus(self, orderId: int, status: str, filled: Any, remaining: Any,
                    avgFillPrice: Any, *args: Any) -> None:
        # 9.81 passes permId, parentId, lastFillPrice, clientId, whyHeld, mktCapPrice after these.
        perm_id = whole(args[0]) if args else 0
        client = whole(args[3]) if len(args) > 3 else self.client_id
        with self.lock:
            for key, account_order in self.account_orders.items():
                if (perm_id and account_order.perm_id == perm_id) or key == (client, int(orderId), 0):
                    account_order.status = str(status)
                    account_order.remaining = decimal_or_none(remaining)
            if client != self.client_id:
                return  # another client's order: its ids may collide with ours
            self.order_status[int(orderId)] = str(status)
            self.order_filled[int(orderId)] = max(self.order_filled.get(int(orderId), 0), whole(filled))
            if perm_id > 0:
                self.perm_ids.setdefault(int(orderId), perm_id)

    def openOrder(self, orderId: int, contract: Any, order: Any, orderState: Any) -> None:
        status = str(getattr(orderState, "status", "") or "")
        ref = str(getattr(order, "orderRef", "") or "") if order is not None else ""
        row = OpenOrderRow(status=status, symbol=str(getattr(contract, "symbol", "") or ""),
                           con_id=whole(getattr(contract, "conId", 0) or 0), order_ref=ref)
        client = self.client_id if order is None else whole(getattr(order, "clientId", 0))
        with self.lock:
            if order is not None:
                perm_id = whole(getattr(order, "permId", 0))
                self.account_orders[(client, int(orderId), perm_id)] = AccountOrder(
                    client_id=client, order_id=int(orderId), perm_id=perm_id,
                    account=str(getattr(order, "account", "") or ""), con_id=row.con_id, symbol=row.symbol,
                    action=str(getattr(order, "action", "") or ""),
                    order_type=str(getattr(order, "orderType", "") or ""), order_ref=ref,
                    total=decimal_or_none(getattr(order, "totalQuantity", 0)) or Decimal("0"), status=status,
                )
            if client != self.client_id:
                return  # another client's (or TWS's) order: never mixed into this client's order ids
            self.open_orders[int(orderId)] = row
            if order is not None:
                self.order_objects[int(orderId)] = order
                self.order_echoes[int(orderId)] = self.order_echoes.get(int(orderId), 0) + 1
                if ref:
                    self.order_refs[int(orderId)] = ref
                perm_id = whole(getattr(order, "permId", 0))
                if perm_id > 0:
                    self.perm_ids.setdefault(int(orderId), perm_id)
            if status:
                self.order_status.setdefault(int(orderId), status)

    def openOrderEnd(self) -> None:
        self.open_orders_done.set()

    def completedOrder(self, contract: Any, order: Any, orderState: Any) -> None:
        ref = str(getattr(order, "orderRef", "") or "")
        row = CompletedOrderRow(
            order_id=whole(getattr(order, "orderId", 0)), client_id=whole(getattr(order, "clientId", 0)),
            perm_id=whole(getattr(order, "permId", 0)), order_ref=ref,
            status=str(getattr(orderState, "status", "") or ""),
            con_id=whole(getattr(contract, "conId", 0) or 0), symbol=str(getattr(contract, "symbol", "") or ""),
        )
        with self.lock:
            self.completed[(ref, row.perm_id)] = row

    def completedOrdersEnd(self) -> None:
        self.completed_done.set()

    # ---- executions --------------------------------------------------------------
    def execDetails(self, reqId: int, contract: Any, execution: Any) -> None:
        # Only this client's orders: never the owner's manual TWS trades.
        if whole(getattr(execution, "clientId", -1)) != self.client_id:
            return
        exec_id = str(getattr(execution, "execId", "") or "")
        base, version = execution_key(exec_id)
        shares = decimal_or_none(getattr(execution, "shares", None))
        price = decimal_or_none(getattr(execution, "price", None))
        if not exec_id or shares is None or price is None or shares < 0 or shares != shares.to_integral_value():
            self.alert("INFO", f"unreadable execution {exec_id or '?'} ignored")
            return
        execution_time = str(getattr(execution, "time", "") or "")
        row = ExecutionRow(
            exec_id=exec_id, base=base, version=version, order_id=whole(getattr(execution, "orderId", 0)),
            perm_id=whole(getattr(execution, "permId", 0)), client_id=self.client_id, shares=int(shares),
            price=price, side=str(getattr(execution, "side", "") or ""),
            account=str(getattr(execution, "acctNumber", "") or ""),
            order_ref=str(getattr(execution, "orderRef", "") or ""), exec_time=execution_time,
            day=execution_day(execution_time), con_id=whole(getattr(contract, "conId", 0) or 0),
            symbol=str(getattr(contract, "symbol", "") or ""),
        )
        with self.lock:
            current = self.execs.get(base)
            if current is not None and current.version > version:
                return  # the original of an execution that was already corrected
            self.execs[base] = row
        if current is not None and version > current.version:
            self.alert("INFO", f"execution {exec_id} corrects order {row.order_id}: {current.shares} -> {row.shares} sh")

    def execDetailsEnd(self, reqId: int) -> None:
        self.exec_done.set()

    def commissionReport(self, commissionReport: Any) -> None:
        exec_id = str(getattr(commissionReport, "execId", "") or "")
        commission = decimal_or_none(getattr(commissionReport, "commission", None))
        if not exec_id or commission is None:
            return  # unset (1.8e308) or unreadable: the fill still counts, its cost stays unknown
        with self.lock:
            self.commissions[exec_id] = (commission, str(getattr(commissionReport, "currency", "") or ""))

    # ---- account -----------------------------------------------------------------
    def position(self, account: str, contract: Any, position: Any, avgCost: Any) -> None:
        shares = decimal_or_none(position)
        con_id = whole(getattr(contract, "conId", 0) or 0)
        if shares is None or con_id <= 0:
            self.alert("INFO", f"unreadable position {position!r} for {account} ignored")
            return
        with self.lock:
            self.positions[(str(account), con_id)] = shares
            self.position_symbols[(str(account), con_id)] = str(getattr(contract, "symbol", "") or "")

    def positionEnd(self) -> None:
        self.positions_done.set()

    def accountSummary(self, reqId: int, account: str, tag: str, value: str, currency: str) -> None:
        if tag != "NetLiquidation":
            return
        amount = decimal_or_none(value)
        if amount is not None and amount > 0:
            with self.lock:
                self.net_liq_by_account[str(account)] = amount

    def accountSummaryEnd(self, reqId: int) -> None:
        self.summary_done.set()

    # ---- market data ---------------------------------------------------------------
    def tickPrice(self, reqId: int, tickType: int, price: float, *args: Any) -> None:
        name = QUOTE_TICKS.get(int(tickType))
        value = decimal_or_none(price)
        if name is None or value is None or value <= 0:
            return
        with self.lock:
            if int(tickType) >= 66:  # delayed data never prices an order
                self.delayed_quotes.add(int(reqId))
                return
            self.quotes.setdefault(int(reqId), {})[name] = value

    def marketDataType(self, reqId: int, marketDataType: int) -> None:
        with self.lock:
            self.data_types[int(reqId)] = whole(marketDataType)

    # ---- errors ------------------------------------------------------------------
    def error(self, reqId: int, errorCode: Any, errorString: Any, *args: Any) -> None:
        try:
            number = int(errorCode)
        except (TypeError, ValueError):
            number = -1
        text = " ".join(str(errorString).split())
        with self.lock:
            ours = int(reqId) in self.own_order_ids
            if ours and number not in ORDER_INFO_CODES:
                self.order_errors[int(reqId)] = self.order_errors.get(int(reqId), 0) + 1
                self.error_details[int(reqId)] = (number, text)
        if ours:  # a rejected order, modify or cancel of ours is never routine
            self.alert("INFO" if number in ORDER_INFO_CODES else "CRITICAL", f"TWS {reqId} {errorCode} {text}")
            return
        if number in FARM_BROKEN_CODES:
            self.alert("INFO", f"TWS {reqId} {errorCode} {text}")
            return
        if 2100 <= number < 2200 and number not in CRITICAL_TWS_CODES:
            return  # data-farm "connection is OK" notices
        self.alert("CRITICAL" if number in CRITICAL_TWS_CODES else "INFO", f"TWS {reqId} {errorCode} {text}")

    # ---- reads (the engine holds no lock across an await) -------------------------
    def status(self, order_id: int | None) -> str | None:
        if order_id is None:
            return None
        with self.lock:
            return self.order_status.get(order_id)

    def live(self, order_id: int | None) -> bool:
        return self.status(order_id) in LIVE_ORDER_STATUSES

    def executions(self) -> list[ExecutionRow]:
        with self.lock:
            return list(self.execs.values())

    def commission_for(self, exec_id: str) -> tuple[Decimal, str] | None:
        with self.lock:
            return self.commissions.get(exec_id)

    def account_position(self, account: str, con_id: int) -> Decimal | None:
        """The account's position in a contract; None until TWS has reported positions."""
        with self.lock:
            if not self.positions_done.is_set():
                return None
            return self.positions.get((account, con_id), Decimal("0"))

    def net_liquidation(self, account: str) -> Decimal | None:
        with self.lock:
            return self.net_liq_by_account.get(account)


__all__ = [
    "DEAD_ORDER_STATUSES",
    "LIVE_ORDER_STATUSES",
    "ORDER_INFO_CODES",
    "PARENT_CANCELLED_STATUSES",
    "WORKING_ORDER_STATUSES",
    "AccountOrder",
    "AlertSink",
    "CompletedOrderRow",
    "ExecutionRow",
    "OpenOrderRow",
    "TwsMirror",
    "decimal_or_none",
    "execution_day",
    "execution_key",
    "whole",
]

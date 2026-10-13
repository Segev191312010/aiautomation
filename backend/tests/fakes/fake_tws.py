"""A synchronous TWS double that answers through the real ibapi 9.81 callbacks.

Every request the execution engine sends is answered at once by calling the
wrapper (``TwsMirror``) exactly as the ibapi reader thread would, with real
ibapi objects (``Contract``, ``Order``, ``OrderState``, ``Execution``,
``CommissionReport``) carrying 9.81's types (floats for shares and prices).
It models the TWS behaviour the engine relies on:

* connect answers ``managedAccounts`` and ``nextValidId``; ``reqIds`` answers
  ``nextValidId`` (which may restart low after a TWS restart);
* ``placeOrder`` with ``transmit=False`` stages an order; the first
  ``transmit=True`` order of the family transmits all of it. A bracket's
  children are ``PreSubmitted`` until the parent fills and are OCA-linked
  (TWS assigns ``ocaGroup`` and ``ocaType=3``);
* a ``placeOrder`` on a live id is a modification: echoed through
  ``openOrder`` (accept), refused with an error (reject), lost (silent), or
  echoed and then refused (echo_then_reject);
* ``cancelOrder`` answers ``Cancelled`` plus error 202, or leaves the order
  ``PendingCancel`` when cancels are not confirmed; unknown ids answer 10147;
* a fill sends ``execDetails`` (unsolicited, reqId -1), ``orderStatus``,
  ``commissionReport`` and, while subscribed, ``position``; a child fill
  reduces or cancels its OCA sibling;
* ``reqAllOpenOrders`` reports every client's working orders,
  ``reqCompletedOrders`` the finished ones, ``reqExecutions`` this client's
  executions with their commission reports.

Behaviour that has not been observed on a real TWS yet (the reporting of
untransmitted orders after a client crash) is marked as an assumption.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from ibapi.commission_report import CommissionReport
from ibapi.common import UNSET_DOUBLE
from ibapi.contract import Contract
from ibapi.execution import Execution
from ibapi.order import Order
from ibapi.order_state import OrderState


ACCOUNT = "DU1234567"
CLIENT_ID = 18
DEAD = frozenset({"Filled", "Cancelled", "ApiCancelled", "Inactive"})


def contract_for(con_id: int, symbol: str = "", primary_exchange: str = "NASDAQ") -> Contract:
    contract = Contract()
    contract.conId = con_id
    contract.symbol = symbol or f"SYM{con_id}"
    contract.secType = "STK"
    contract.exchange = "SMART"
    contract.currency = "USD"
    contract.primaryExchange = primary_exchange
    return contract


def order_state(status: str) -> OrderState:
    state = OrderState()
    state.status = status
    return state


@dataclass
class ServerOrder:
    """One order as TWS holds it."""

    client_id: int
    order_id: int
    perm_id: int
    contract: Contract
    order: Order
    status: str  # Staged (created, not transmitted) | PreSubmitted | Submitted | PendingCancel | Filled | Cancelled
    filled: int = 0
    value: Decimal = Decimal("0")
    oca_group: str = ""

    @property
    def total(self) -> int:
        return int(Decimal(str(self.order.totalQuantity)))

    @property
    def remaining(self) -> int:
        return max(0, self.total - self.filled)

    @property
    def average(self) -> float:
        return float(self.value / self.filled) if self.filled else 0.0

    @property
    def working(self) -> bool:
        return self.status not in DEAD


@dataclass
class FakeTws:
    """The server side of one TWS session; ``wrapper`` is the client's callback object."""

    wrapper: Any
    account: str = ACCOUNT
    accounts: str = ACCOUNT
    client_id: int = CLIENT_ID
    next_valid_id: int = 100
    net_liquidation: str = "1000000.00"
    quotes: dict[int, dict[str, float]] = field(default_factory=dict)  # conId -> bid/ask/last
    symbols: dict[int, str] = field(default_factory=dict)  # conId -> symbol TWS reports
    delayed: bool = False
    modify_mode: str = "accept"  # accept | reject | silent | echo_then_reject
    confirm_cancels: bool = True
    answer_snapshots: bool = True
    report_untransmitted: bool = False  # assumption to verify in the drill (see the module docstring)
    session_day: str = "20261013"
    connected: bool = False
    orders: dict[tuple[int, int], ServerOrder] = field(default_factory=dict)
    executions: list[tuple[Contract, Execution, CommissionReport | None]] = field(default_factory=list)
    positions: dict[int, Decimal] = field(default_factory=dict)
    position_contracts: dict[int, Contract] = field(default_factory=dict)
    placed: list[tuple[int, Order]] = field(default_factory=list)
    cancelled: list[int] = field(default_factory=list)
    requests: list[str] = field(default_factory=list)
    fail_place_after: int | None = None  # raise OSError once this many placeOrder calls went through
    position_subscribed: bool = False
    _perm: int = 990000
    _exec: int = 0
    _oca: int = 1528474519

    # ---- connection -------------------------------------------------------------
    def connect(self) -> None:
        if hasattr(self.wrapper, "begin_connection"):
            self.wrapper.begin_connection()
        self.connected = True
        self.position_subscribed = False
        self.wrapper.managedAccounts(self.accounts)
        self.wrapper.nextValidId(self.next_valid_id)

    def attach(self, wrapper: Any) -> None:
        """A restarted engine connects with a fresh wrapper to the same TWS."""
        self.wrapper = wrapper

    def isConnected(self) -> bool:
        return self.connected

    def disconnect(self) -> None:
        self.connected = False
        self.position_subscribed = False

    def restart_tws(self, *, next_valid_id: int | None = None) -> None:
        """TWS restarted: finished orders are only reported as completed; ids may start low."""
        self.connected = False
        self.position_subscribed = False
        if next_valid_id is not None:
            self.next_valid_id = next_valid_id

    # ---- requests -----------------------------------------------------------------
    def reqIds(self, numIds: int) -> None:
        self.requests.append("reqIds")
        self.wrapper.nextValidId(self.next_valid_id)

    def reqMarketDataType(self, marketDataType: int) -> None:
        self.requests.append("reqMarketDataType")

    def reqAccountSummary(self, reqId: int, groupName: str, tags: str) -> None:
        self.requests.append("reqAccountSummary")
        self.wrapper.accountSummary(reqId, self.account, "NetLiquidation", self.net_liquidation, "USD")
        self.wrapper.accountSummaryEnd(reqId)

    def cancelAccountSummary(self, reqId: int) -> None:
        self.requests.append("cancelAccountSummary")

    def reqPositions(self) -> None:
        self.requests.append("reqPositions")
        if not self.answer_snapshots:
            return
        self.position_subscribed = True
        for con_id, shares in self.positions.items():
            self.wrapper.position(self.account, self.position_contracts.get(con_id, contract_for(con_id)),
                                  float(shares), 50.0)
        self.wrapper.positionEnd()

    def cancelPositions(self) -> None:
        self.requests.append("cancelPositions")
        self.position_subscribed = False

    def reqAllOpenOrders(self) -> None:
        self.requests.append("reqAllOpenOrders")
        if not self.answer_snapshots:
            return
        for server in list(self.orders.values()):
            if not server.working:
                continue
            if server.status == "Staged" and not self.report_untransmitted:
                continue
            status = "PendingSubmit" if server.status == "Staged" else server.status
            self.wrapper.openOrder(server.order_id, server.contract, self.echo(server), order_state(status))
            self.wrapper.orderStatus(server.order_id, status, float(server.filled), float(server.remaining),
                                     server.average, server.perm_id, int(server.order.parentId), 0.0,
                                     server.client_id, "", 0.0)
        self.wrapper.openOrderEnd()

    def reqCompletedOrders(self, apiOnly: bool) -> None:
        self.requests.append("reqCompletedOrders")
        if not self.answer_snapshots:
            return
        for server in self.orders.values():
            if server.working:
                continue
            state = order_state(server.status)
            state.completedStatus = server.status
            self.wrapper.completedOrder(server.contract, self.echo(server), state)
        self.wrapper.completedOrdersEnd()

    def reqExecutions(self, reqId: int, execFilter: Any) -> None:
        self.requests.append("reqExecutions")
        if not self.answer_snapshots:
            return
        for contract, execution, report in self.executions:
            if execFilter.clientId and execution.clientId != execFilter.clientId:
                continue
            replay = copy.copy(execution)
            self.wrapper.execDetails(reqId, contract, replay)
            if report is not None:
                self.wrapper.commissionReport(report)
        self.wrapper.execDetailsEnd(reqId)

    def reqMktData(self, reqId: int, contract: Any, genericTickList: str, snapshot: bool,
                   regulatorySnapshot: bool, mktDataOptions: list[Any]) -> None:
        self.requests.append("reqMktData")
        quote = self.quotes.get(int(contract.conId), {})
        ticks = {"bid": 66, "ask": 67, "last": 68} if self.delayed else {"bid": 1, "ask": 2, "last": 4}
        for name, tick in ticks.items():
            if name in quote:
                self.wrapper.tickPrice(reqId, tick, quote[name], None)

    def cancelMktData(self, reqId: int) -> None:
        self.requests.append("cancelMktData")

    # ---- orders ----------------------------------------------------------------------
    def echo(self, server: ServerOrder) -> Order:
        """The Order as 9.81 decodes it from TWS: ids, OCA link and decoder defaults filled in."""
        reported = copy.copy(server.order)
        reported.orderId = server.order_id
        reported.clientId = server.client_id
        reported.permId = server.perm_id
        if server.oca_group:
            reported.ocaGroup, reported.ocaType = server.oca_group, 3
        # Fields 9.81 decodes differently from what the API sent: a modify must never copy them.
        reported.eTradeOnly = True
        reported.settlingFirm = "?"
        reported.openClose = "O"
        reported.totalQuantity = float(server.total)
        return reported

    def report(self, server: ServerOrder, status: str | None = None) -> None:
        status = status or server.status
        self.wrapper.openOrder(server.order_id, server.contract, self.echo(server), order_state(status))
        self.wrapper.orderStatus(server.order_id, status, float(server.filled), float(server.remaining),
                                 server.average, server.perm_id, int(server.order.parentId), 0.0,
                                 server.client_id, "", 0.0)

    def status_only(self, server: ServerOrder, *, remaining: int | None = None) -> None:
        self.wrapper.orderStatus(server.order_id, server.status, float(server.filled),
                                 float(server.remaining if remaining is None else remaining), server.average,
                                 server.perm_id, int(server.order.parentId), 0.0, server.client_id, "", 0.0)

    def family(self, parent_id: int) -> list[ServerOrder]:
        return [item for item in self.orders.values()
                if item.client_id == self.client_id and int(item.order.parentId) == parent_id]

    def placeOrder(self, orderId: int, contract: Any, order: Any) -> None:
        if not self.connected:
            self.wrapper.error(-1, 504, "Not connected")
            return
        if self.fail_place_after is not None and len(self.placed) >= self.fail_place_after:
            raise OSError("socket closed while sending")
        self.placed.append((orderId, copy.copy(order)))
        key = (self.client_id, orderId)
        existing = self.orders.get(key)
        if existing is not None:
            self.modify(existing, order)
            return
        if orderId < self.next_valid_id:
            self.wrapper.error(orderId, 103, "Duplicate order id")
            return
        if order.eTradeOnly or order.firmQuoteOnly:
            code = 10268 if order.eTradeOnly else 10269
            self.wrapper.error(orderId, code, "The order attribute is not supported.")
            return
        if int(order.parentId) and (self.client_id, int(order.parentId)) not in self.orders:
            self.wrapper.error(orderId, 10006, "Missing parent order")
            return
        self.next_valid_id = max(self.next_valid_id, orderId + 1)
        self._perm += 1
        reported_contract = copy.copy(contract)
        if not reported_contract.symbol:  # TWS reports the qualified contract back
            reported_contract.symbol = self.symbols.get(int(contract.conId), f"SYM{contract.conId}")
        server = ServerOrder(client_id=self.client_id, order_id=orderId, perm_id=self._perm,
                             contract=reported_contract, order=copy.copy(order), status="Staged")
        self.orders[key] = server
        if not order.transmit:
            return  # created at TWS, not sent: the family waits for its transmitting order
        parent_id = int(order.parentId) or orderId
        members = sorted({parent_id, *(item.order_id for item in self.family(parent_id)), orderId})
        self._oca += 1
        for member_id in members:
            member = self.orders[(self.client_id, member_id)]
            if member.status != "Staged":
                continue
            child = int(member.order.parentId) != 0
            if child:
                member.oca_group = str(self._oca)
            member.status = "PreSubmitted" if child or member.order.orderType == "STP" else "Submitted"
            self.report(member)

    def modify(self, server: ServerOrder, order: Any) -> None:
        if not server.working:
            self.wrapper.error(server.order_id, 104, "Cannot modify a filled order.")
            return
        if self.modify_mode == "silent":
            return
        if self.modify_mode == "reject":
            self.wrapper.error(server.order_id, 321, "Error validating request")
            return
        previous = server.order
        server.order = copy.copy(order)
        self.report(server)
        if self.modify_mode == "echo_then_reject":
            server.order = previous  # TWS keeps the old order after all
            self.wrapper.error(server.order_id, 321, "Error validating request")

    def cancelOrder(self, orderId: int) -> None:
        self.cancelled.append(orderId)
        server = self.orders.get((self.client_id, orderId))
        if server is None:
            self.wrapper.error(orderId, 10147, f"OrderId {orderId} that needs to be cancelled is not found.")
            return
        if not server.working:
            self.wrapper.error(orderId, 10148, f"OrderId {orderId} that needs to be cancelled cannot be"
                               f" cancelled, state: {server.status}.")
            return
        if not self.confirm_cancels:
            server.status = "PendingCancel"
            self.status_only(server)
            return
        self._cancel(server)
        if int(server.order.parentId) == 0 and server.filled == 0:
            for child in self.family(server.order_id):
                if child.working:
                    self._cancel(child)  # an unfilled parent takes its bracket with it

    def _cancel(self, server: ServerOrder) -> None:
        server.status = "Cancelled"
        self.status_only(server)
        self.wrapper.error(server.order_id, 202, "Order Canceled - reason:")

    def confirm_pending_cancels(self) -> None:
        for server in self.orders.values():
            if server.status == "PendingCancel":
                self._cancel(server)

    # ---- the market ------------------------------------------------------------------
    def fill(self, order_id: int, shares: int, price: float | str, *, commission: str | None = "1.00",
             time: str | None = None, client_id: int | None = None, exec_id: str | None = None,
             order_ref: str | None = None) -> Execution:
        """IBKR executes ``shares`` of an order: execDetails, orderStatus, commissionReport, position."""
        server = self.orders[(self.client_id if client_id is None else client_id, order_id)]
        price_dec = Decimal(str(price))
        server.filled += shares
        server.value += Decimal(shares) * price_dec
        if server.remaining == 0:
            server.status = "Filled"
        elif server.status == "PreSubmitted" and int(server.order.parentId) == 0:
            server.status = "Submitted"
        self._exec += 1
        execution = Execution()
        execution.execId = exec_id or f"0000e0d5.{self._exec:08x}.01.01"
        execution.time = time or f"{self.session_day}  10:00:{self._exec % 60:02d}"
        execution.acctNumber = self.account
        execution.exchange = "ISLAND"
        execution.side = "BOT" if server.order.action == "BUY" else "SLD"
        execution.shares = float(shares)
        execution.price = float(price_dec)
        execution.permId = server.perm_id
        execution.clientId = server.client_id
        execution.orderId = server.order_id
        execution.cumQty = float(server.filled)
        execution.avgPrice = server.average
        execution.orderRef = server.order.orderRef if order_ref is None else order_ref
        report = None
        if commission is not None:
            report = CommissionReport()
            report.execId = execution.execId
            report.commission = float(commission)
            report.currency = "USD"
            report.realizedPNL = UNSET_DOUBLE
        self.executions.append((server.contract, execution, report))
        self.wrapper.execDetails(-1, server.contract, copy.copy(execution))
        self.status_only(server)
        if report is not None:
            self.wrapper.commissionReport(report)
        self.move_position(int(server.contract.conId), shares if server.order.action == "BUY" else -shares,
                           server.contract)
        self.after_fill(server, shares)
        return execution

    def after_fill(self, server: ServerOrder, shares: int) -> None:
        parent_id = int(server.order.parentId)
        if parent_id == 0:
            # Children track the parent: activated, with the filled shares to protect.
            for child in self.family(server.order_id):
                if child.working:
                    child.status = "Submitted" if child.order.orderType == "LMT" else "PreSubmitted"
                    self.status_only(child, remaining=server.filled - child.filled)
            return
        for sibling in self.family(parent_id):  # OCA type 3: a fill reduces the other leg
            if sibling.order_id == server.order_id or not sibling.working:
                continue
            if server.status == "Filled":
                self._cancel(sibling)
            else:
                self.status_only(sibling, remaining=max(0, sibling.remaining - shares))

    def move_position(self, con_id: int, delta: int, contract: Contract | None = None) -> None:
        """The account's position changes (a fill, or the owner trading by hand in TWS)."""
        self.positions[con_id] = self.positions.get(con_id, Decimal("0")) + delta
        if contract is not None:
            self.position_contracts.setdefault(con_id, contract)
        if self.position_subscribed:
            self.wrapper.position(self.account, self.position_contracts.get(con_id, contract_for(con_id)),
                                  float(self.positions[con_id]), 50.0)

    def add_foreign_order(self, *, client_id: int, order_id: int, con_id: int, symbol: str, action: str,
                          quantity: int, order_type: str = "LMT", price: float = 10.0, order_ref: str = "",
                          status: str = "Submitted") -> ServerOrder:
        """An order of another client: the owner's TWS order (client 0), a manual order, another app."""
        placed = Order()
        placed.action, placed.orderType, placed.totalQuantity = action, order_type, quantity
        placed.account, placed.orderRef, placed.tif = self.account, order_ref, "DAY"
        if order_type == "LMT":
            placed.lmtPrice = price
        elif order_type == "STP":
            placed.auxPrice = price
        self._perm += 1
        server = ServerOrder(client_id=client_id, order_id=order_id, perm_id=self._perm,
                             contract=contract_for(con_id, symbol), order=placed, status=status)
        self.orders[(client_id, order_id)] = server
        return server

    def error(self, order_id: int, code: int, message: str) -> None:
        self.wrapper.error(order_id, code, message)


__all__ = ["ACCOUNT", "CLIENT_ID", "FakeTws", "ServerOrder", "contract_for", "order_state"]

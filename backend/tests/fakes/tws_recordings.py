"""Recorded-payload harness: TWS callback sequences as data, replayed into the engine's mirror.

Each recording is the sequence of ibapi 9.81 ``EWrapper`` callbacks TWS sends
for one scenario, with the field values TWS fills in (floats for quantities
and prices, ``permId``, the OCA group TWS assigns to bracket children,
``execId`` with its correction suffix, ``time`` in TWS local format, unset
doubles in commission reports). The replayer builds the real ibapi objects
(``Contract``, ``Order``, ``OrderState``, ``Execution``, ``CommissionReport``)
from each step and calls the mirror exactly as the ibapi decoder would.

Provenance: these sequences were written from the ibapi 9.81 callback
signatures and IBKR's documented order lifecycle; they were NOT captured from
a live TWS (this build never connects to one). The supervised drill should
capture the same five scenarios from the paper account and replace these
fixtures; the replayer accepts captured sequences unchanged. Points to verify
then are marked ``ASSUMPTION``.

Order ids, permIds and orderRefs are templated (``{P}``, ``{T}``, ``{S}``,
``{F}``, ``{ref_P}``, ...) because the engine assigns them at runtime.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ibapi.commission_report import CommissionReport
from ibapi.common import UNSET_DOUBLE
from ibapi.contract import Contract
from ibapi.execution import Execution
from ibapi.order import Order
from ibapi.order_state import OrderState


@dataclass(frozen=True)
class Step:
    callback: str
    fields: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Recording:
    name: str
    steps: tuple[Step, ...]
    notes: str = ""


def _order_echo(leg: str, status: str, **extra: Any) -> Step:
    return Step("openOrder", {"leg": leg, "status": status, **extra})


def _status(leg: str, status: str, filled: float, remaining: float, avg: float = 0.0, last: float = 0.0) -> Step:
    return Step("orderStatus", {"leg": leg, "status": status, "filled": filled, "remaining": remaining,
                                "avgFillPrice": avg, "lastFillPrice": last})


def _execution(leg: str, exec_id: str, shares: float, price: float, cum: float, side: str,
               time: str = "20261013  10:00:07") -> Step:
    return Step("execDetails", {"leg": leg, "execId": exec_id, "shares": shares, "price": price, "cumQty": cum,
                                "avgPrice": price, "side": side, "time": time})


def _commission(exec_id: str, commission: float) -> Step:
    return Step("commissionReport", {"execId": exec_id, "commission": commission})


def _position(shares: float, avg_cost: float = 50.0) -> Step:
    return Step("position", {"position": shares, "avgCost": avg_cost})


# A native bracket is reported leg by leg once the transmitting stop arrives; the
# children wait (PreSubmitted) until the parent fills, TWS assigns their OCA group.
_PLACEMENT = (
    _order_echo("P", "PreSubmitted"), _status("P", "PreSubmitted", 0.0, 100.0),
    _order_echo("T", "PreSubmitted"), _status("T", "PreSubmitted", 0.0, 100.0),
    _order_echo("S", "PreSubmitted"), _status("S", "PreSubmitted", 0.0, 100.0),
    _status("P", "Submitted", 0.0, 100.0),
)

BRACKET_FILL = Recording(
    name="bracket fill",
    notes="The parent fills in one execution; the target later fills in full and TWS cancels the OCA stop.",
    steps=(
        *_PLACEMENT,
        _status("P", "Filled", 100.0, 0.0, 50.0, 50.0),
        _execution("P", "0000e0d5.6705a1b2.01.01", 100.0, 50.0, 100.0, "BOT"),
        _order_echo("P", "Filled"),
        _commission("0000e0d5.6705a1b2.01.01", 1.0),
        _status("T", "Submitted", 0.0, 100.0),  # the children activate once the parent filled
        _position(100.0, 50.01),
        _status("T", "Filled", 100.0, 0.0, 51.0, 51.0),
        _execution("T", "0000e0d5.6705a1b3.01.01", 100.0, 51.0, 100.0, "SLD", "20261013  10:21:40"),
        _status("S", "Cancelled", 0.0, 100.0),  # OCA: the stop goes when the target fills
        _commission("0000e0d5.6705a1b3.01.01", 1.02),
        _position(0.0, 0.0),
    ),
)

PARTIAL_FILL_CHILDREN_TRACK = Recording(
    name="partial fill with children tracking it",
    notes=("The parent fills 40, then the rest; TWS reports the children's remaining quantity tracking the"
           " parent's filled shares (ASSUMPTION: exact child status text while the parent is partly filled)."),
    steps=(
        *_PLACEMENT,
        _status("P", "Submitted", 40.0, 60.0, 49.98, 49.98),
        _execution("P", "0000e0d5.6705b001.01.01", 40.0, 49.98, 40.0, "BOT"),
        _commission("0000e0d5.6705b001.01.01", 1.0),
        _status("T", "Submitted", 0.0, 40.0),
        _status("S", "PreSubmitted", 0.0, 40.0),
        _position(40.0, 49.98),
        _status("P", "Filled", 100.0, 0.0, 49.992, 50.0),
        _execution("P", "0000e0d5.6705b002.01.01", 60.0, 50.0, 100.0, "BOT", "20261013  10:00:31"),
        _commission("0000e0d5.6705b002.01.01", 0.35),
        _status("T", "Submitted", 0.0, 100.0),
        _status("S", "PreSubmitted", 0.0, 100.0),
        _position(100.0, 49.9952),
    ),
)

PARTIAL_THEN_CANCELLED = Recording(
    name="partial fill, then the parent is cancelled",
    notes="TWS keeps the children for the 40 filled shares; the engine must resize them to 40.",
    steps=(
        *_PLACEMENT,
        _status("P", "Submitted", 40.0, 60.0, 50.0, 50.0),
        _execution("P", "0000e0d5.6705c001.01.01", 40.0, 50.0, 40.0, "BOT"),
        _commission("0000e0d5.6705c001.01.01", 1.0),
        _status("T", "Submitted", 0.0, 40.0),
        _position(40.0, 50.0),
        _status("P", "Cancelled", 40.0, 0.0, 50.0, 50.0),
        Step("error", {"leg": "P", "code": 202, "message": "Order Canceled - reason:"}),
    ),
)

MODIFY_ECHO = Recording(
    name="modify echo",
    notes="TWS accepts a modification: openOrder reports the new quantity, then orderStatus.",
    steps=(
        _order_echo("T", "Submitted", totalQuantity=40.0),
        _status("T", "Submitted", 0.0, 40.0),
        _order_echo("S", "PreSubmitted", totalQuantity=40.0),
        _status("S", "PreSubmitted", 0.0, 40.0),
    ),
)

REJECTED_MODIFY = Recording(
    name="rejected modify",
    notes="TWS refuses a modification with error 321 and keeps the order as it was (no new openOrder).",
    steps=(
        Step("error", {"leg": "T", "code": 321,
                       "message": "Error validating request.-'bW' : cause - The order quantity cannot be changed"}),
        Step("error", {"leg": "S", "code": 321,
                       "message": "Error validating request.-'bW' : cause - The order quantity cannot be changed"}),
    ),
)

# After a restart mid-send (parent and target created with transmit=False, the
# transmitting stop never sent), the reconnect's reqAllOpenOrders either reports
# nothing or reports the two created legs. ASSUMPTION to verify in the drill:
# which of the two TWS does, and with which status.
RESTART_MID_SEND_NOTHING = Recording(
    name="restart mid-send: TWS reports nothing",
    steps=(Step("openOrderEnd"), Step("execDetailsEnd"), Step("completedOrdersEnd"), Step("positionEnd")),
)
RESTART_MID_SEND_CREATED_LEGS = Recording(
    name="restart mid-send: TWS reports the created, untransmitted legs",
    steps=(
        _order_echo("P", "PreSubmitted"), _status("P", "PreSubmitted", 0.0, 100.0),
        _order_echo("T", "PreSubmitted"), _status("T", "PreSubmitted", 0.0, 100.0),
        Step("openOrderEnd"), Step("execDetailsEnd"), Step("completedOrdersEnd"), Step("positionEnd"),
    ),
)

RECORDINGS = (BRACKET_FILL, PARTIAL_FILL_CHILDREN_TRACK, PARTIAL_THEN_CANCELLED, MODIFY_ECHO, REJECTED_MODIFY,
              RESTART_MID_SEND_NOTHING, RESTART_MID_SEND_CREATED_LEGS)


@dataclass
class Placed:
    """What the engine sent, so templated steps can be bound to its ids and refs."""

    orders: dict[str, Order]  # leg -> the Order as placed
    ids: dict[str, int]
    con_id: int
    symbol: str
    account: str
    client_id: int
    perm_base: int = 1_871_000_000


def replay(recording: Recording, mirror: Any, placed: Placed) -> None:
    """Call the mirror's ibapi callbacks for every step, with real ibapi objects."""
    contract = Contract()
    contract.conId, contract.symbol, contract.secType = placed.con_id, placed.symbol, "STK"
    contract.exchange, contract.primaryExchange, contract.currency = "SMART", "NASDAQ", "USD"
    for step in recording.steps:
        values = dict(step.fields)
        leg = values.pop("leg", None)
        order_id = placed.ids.get(leg, 0) if leg else 0
        perm_id = placed.perm_base + order_id
        if step.callback == "openOrder":
            reported = Order()
            source = placed.orders[leg]
            for name in ("action", "orderType", "totalQuantity", "lmtPrice", "auxPrice", "tif", "orderRef",
                         "account", "parentId", "transmit"):
                setattr(reported, name, getattr(source, name))
            reported.orderId, reported.clientId, reported.permId = order_id, placed.client_id, perm_id
            reported.totalQuantity = float(values.pop("totalQuantity", source.totalQuantity))
            if leg in ("T", "S"):
                reported.ocaGroup, reported.ocaType = f"{placed.perm_base + placed.ids['P']}", 3
            reported.eTradeOnly, reported.settlingFirm, reported.openClose = True, "?", "O"  # 9.81 decoder values
            state = OrderState()
            state.status = values["status"]
            mirror.openOrder(order_id, contract, reported, state)
        elif step.callback == "orderStatus":
            mirror.orderStatus(order_id, values["status"], values["filled"], values["remaining"],
                               values["avgFillPrice"], perm_id, placed.ids["P"] if leg in ("T", "S") else 0,
                               values["lastFillPrice"], placed.client_id, "", 0.0)
        elif step.callback == "execDetails":
            execution = Execution()
            execution.execId, execution.time = values["execId"], values["time"]
            execution.acctNumber, execution.exchange, execution.side = placed.account, "ISLAND", values["side"]
            execution.shares, execution.price = values["shares"], values["price"]
            execution.permId, execution.clientId, execution.orderId = perm_id, placed.client_id, order_id
            execution.cumQty, execution.avgPrice = values["cumQty"], values["avgPrice"]
            execution.orderRef = placed.orders[leg].orderRef
            mirror.execDetails(-1, contract, execution)
        elif step.callback == "commissionReport":
            report = CommissionReport()
            report.execId, report.commission, report.currency = values["execId"], values["commission"], "USD"
            report.realizedPNL = UNSET_DOUBLE
            mirror.commissionReport(report)
        elif step.callback == "position":
            mirror.position(placed.account, contract, values["position"], values["avgCost"])
        elif step.callback == "error":
            mirror.error(order_id, values["code"], values["message"])
        elif step.callback in ("openOrderEnd", "positionEnd", "completedOrdersEnd"):
            getattr(mirror, step.callback)()
        elif step.callback == "execDetailsEnd":
            mirror.execDetailsEnd(-1)
        else:  # pragma: no cover - a recording with an unknown callback is a broken fixture
            raise ValueError(f"unknown callback {step.callback}")


__all__ = [
    "BRACKET_FILL", "MODIFY_ECHO", "PARTIAL_FILL_CHILDREN_TRACK", "PARTIAL_THEN_CANCELLED", "RECORDINGS",
    "REJECTED_MODIFY", "RESTART_MID_SEND_CREATED_LEGS", "RESTART_MID_SEND_NOTHING", "Placed", "Recording", "Step",
    "replay",
]

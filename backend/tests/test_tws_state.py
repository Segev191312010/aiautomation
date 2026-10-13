"""The TWS callback mirror and the fake TWS against the real ibapi 9.81 interfaces.

The engine is only as good as its fake: these tests pin the mirror's callbacks
and the fake's requests to ibapi's own ``EWrapper`` and ``EClient``
signatures, so neither can drift from the API the real TWS speaks.
"""
from __future__ import annotations

import inspect
from decimal import Decimal
from types import SimpleNamespace

from ibapi.client import EClient
from ibapi.common import UNSET_DOUBLE
from ibapi.wrapper import EWrapper

from tests.fakes.fake_tws import FakeTws
from trading.execution.live_executor import BrokerPort
from trading.execution.tws_state import TwsMirror, execution_key


CALLBACKS = (
    "nextValidId", "managedAccounts", "connectionClosed", "orderStatus", "openOrder", "openOrderEnd",
    "completedOrder", "completedOrdersEnd", "execDetails", "execDetailsEnd", "commissionReport", "position",
    "positionEnd", "accountSummary", "accountSummaryEnd", "tickPrice", "marketDataType", "error",
)
REQUESTS = tuple(name for name in vars(BrokerPort) if not name.startswith("_"))


def test_every_mirror_callback_accepts_ibapis_own_call() -> None:
    for name in CALLBACKS:
        wrapper_parameters = list(inspect.signature(getattr(EWrapper, name)).parameters)[1:]
        mirror = inspect.signature(getattr(TwsMirror, name))
        mirror.bind(None, *wrapper_parameters)  # the decoder calls positionally, in this order


def test_the_engine_only_uses_eclient_requests_and_the_fake_answers_each_one() -> None:
    assert set(REQUESTS) >= {"placeOrder", "cancelOrder", "reqIds", "reqAllOpenOrders", "reqExecutions"}
    for name in REQUESTS:
        client = inspect.signature(getattr(EClient, name))
        port = inspect.signature(getattr(BrokerPort, name))
        assert list(client.parameters) == list(port.parameters), name
        fake = inspect.signature(getattr(FakeTws, name))
        assert list(fake.parameters) == list(client.parameters), name


def test_a_runner_can_combine_the_mirror_with_ibapi_without_shadowing_a_callback() -> None:
    class App(TwsMirror, EWrapper, EClient):  # how a future runner wires the mirror (not wired now)
        def __init__(self) -> None:
            EClient.__init__(self, self)
            TwsMirror.__init__(self, 18)

    app = App()
    for name in CALLBACKS:
        assert getattr(App, name) is getattr(TwsMirror, name), name
    app.nextValidId(42)
    app.managedAccounts("DU1234567")
    assert (app.next_id, app.accounts, app.isConnected()) == (42, ["DU1234567"], False)


def test_another_clients_order_with_our_order_id_never_overwrites_our_leg() -> None:
    mirror = TwsMirror(18)
    ours = SimpleNamespace(clientId=18, permId=7001, action="SELL", orderType="LMT", totalQuantity=100.0,
                           orderRef="at1:0123456789ab:T", account="DU1234567")
    mirror.openOrder(31, SimpleNamespace(symbol="HELD", conId=300), ours, SimpleNamespace(status="Submitted"))
    mirror.orderStatus(31, "Submitted", 0.0, 100.0, 0.0, 7001, 30, 0.0, 18, "", 0.0)
    theirs = SimpleNamespace(clientId=5, permId=990003, action="BUY", orderType="LMT", totalQuantity=7.0,
                             orderRef="other-app", account="DU1234567")
    mirror.openOrder(31, SimpleNamespace(symbol="ZZZ", conId=999), theirs, SimpleNamespace(status="Cancelled"))
    mirror.orderStatus(31, "Cancelled", 0.0, 7.0, 0.0, 990003, 0, 0.0, 5, "", 0.0)

    assert mirror.order_status[31] == "Submitted" and mirror.order_objects[31].action == "SELL"
    assert mirror.order_refs[31] == "at1:0123456789ab:T"
    assert {item.client_id for item in mirror.account_orders.values()} == {18, 5}


def test_only_this_clients_executions_are_mirrored_and_corrections_win() -> None:
    mirror = TwsMirror(18)
    lines: list[tuple[str, str]] = []
    mirror.alert = lambda kind, text: lines.append((kind, text))

    def execution(exec_id: str, shares: float, client: int = 18) -> None:
        mirror.execDetails(-1, SimpleNamespace(conId=300, symbol="HELD"), SimpleNamespace(
            execId=exec_id, orderId=30, shares=shares, price=50.0, clientId=client, acctNumber="DU1234567",
            orderRef="at1:0123456789ab:P", time="20261013  10:00:05", side="BOT", permId=1))

    execution("0000e0d5.6576fd38.01.01", 60.0)
    execution("0000e0d5.6576fd38.01.02", 40.0)
    execution("0000e0d5.6576fd38.01.01", 60.0)  # the original replayed after its correction
    execution("owner.01", 500.0, client=0)  # the owner's TWS trade
    execution("frac.01", 0.5)  # fractional shares are never counted
    [row] = mirror.executions()
    assert (row.exec_id, row.shares, row.day.isoformat()) == ("0000e0d5.6576fd38.01.02", 40, "2026-10-13")
    assert execution_key("0000e0d5.6576fd38.01.02") == ("0000e0d5.6576fd38.01", 2)
    assert lines == [("INFO", "execution 0000e0d5.6576fd38.01.02 corrects order 30: 60 -> 40 sh"),
                     ("INFO", "unreadable execution frac.01 ignored")]


def test_unset_values_and_delayed_ticks_never_count() -> None:
    mirror = TwsMirror(18)
    mirror.commissionReport(SimpleNamespace(execId="e.01", commission=UNSET_DOUBLE, currency="USD"))
    mirror.commissionReport(SimpleNamespace(execId="e.02", commission=1.25, currency="USD"))
    assert mirror.commissions == {"e.02": (Decimal("1.25"), "USD")}
    mirror.tickPrice(1, 66, 49.0, None)  # delayed bid: never prices an order
    mirror.tickPrice(2, 1, 50.1, None)
    mirror.tickPrice(2, 2, -1.0, None)  # TWS sends -1 when there is no ask
    assert mirror.quotes == {2: {"bid": Decimal("50.1")}} and mirror.delayed_quotes == {1}
    mirror.accountSummary(1, "DU1234567", "NetLiquidation", "", "USD")
    mirror.accountSummary(1, "DU1234567", "NetLiquidation", "1000000.00", "USD")
    assert mirror.net_liquidation("DU1234567") == Decimal("1000000.00")
    assert mirror.account_position("DU1234567", 300) is None  # positions not reported yet
    mirror.positionEnd()
    assert mirror.account_position("DU1234567", 300) == 0


def test_order_status_and_fills_never_go_backwards() -> None:
    mirror = TwsMirror(18)
    mirror.orderStatus(30, "Submitted", 60.0, 40.0, 50.0, 1, 0, 50.0, 18, "", 0.0)
    mirror.orderStatus(30, "Submitted", 40.0, 60.0, 50.0, 1, 0, 50.0, 18, "", 0.0)  # an out-of-order report
    assert mirror.order_filled[30] == 60
    mirror.own_order_ids.add(30)
    lines: list[tuple[str, str]] = []
    mirror.alert = lambda kind, text: lines.append((kind, text))
    mirror.error(30, 202, "Order Canceled - reason:")
    mirror.error(30, 201, "Order rejected")
    assert lines == [("INFO", "TWS 30 202 Order Canceled - reason:"), ("CRITICAL", "TWS 30 201 Order rejected")]
    assert mirror.order_errors == {30: 1}

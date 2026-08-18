#!/usr/bin/env python3
"""Deterministic, offline PAPER lifecycle drill harness.

The simulator exercises order lifecycle invariants without importing an IBKR
client, opening a socket, reading credentials, or changing application mode.
Its results are useful for regression testing only; they are not evidence of a
real IBKR PAPER session and never authorize LIVE trading.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class OrderStatus(str, Enum):
    NEW = "NEW"
    WORKING = "WORKING"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    DISCONNECTED = "DISCONNECTED"


@dataclass
class SimOrder:
    order_id: str
    symbol: str
    quantity: int
    filled: int = 0
    status: OrderStatus = OrderStatus.NEW
    limit_price: float | None = None
    replacement_count: int = 0

    @property
    def remaining(self) -> int:
        return self.quantity - self.filled


@dataclass
class SimBroker:
    """A deterministic broker double; it has no network-capable methods."""

    connected: bool = True
    orders: dict[str, SimOrder] = field(default_factory=dict)

    def submit(self, order: SimOrder) -> None:
        if not self.connected:
            raise ConnectionError("simulated broker is disconnected")
        order.status = OrderStatus.WORKING
        self.orders[order.order_id] = order

    def fill(self, order_id: str, quantity: int) -> None:
        if not self.connected:
            raise ConnectionError("simulated broker is disconnected")
        order = self.orders[order_id]
        if order.status in {OrderStatus.CANCELED, OrderStatus.FILLED}:
            raise ValueError("cannot fill terminal order")
        if quantity <= 0 or quantity > order.remaining:
            raise ValueError("fill quantity exceeds remaining order quantity")
        order.filled += quantity
        order.status = OrderStatus.FILLED if order.remaining == 0 else OrderStatus.PARTIALLY_FILLED

    def cancel(self, order_id: str) -> None:
        if not self.connected:
            raise ConnectionError("simulated broker is disconnected")
        order = self.orders[order_id]
        if order.status not in {OrderStatus.FILLED, OrderStatus.CANCELED}:
            order.status = OrderStatus.CANCELED

    def snapshot(self) -> dict[str, dict[str, Any]]:
        return {
            order_id: {
                "symbol": order.symbol,
                "quantity": order.quantity,
                "filled": order.filled,
                "remaining": order.remaining,
                "status": order.status.value,
                "limit_price": order.limit_price,
            }
            for order_id, order in sorted(self.orders.items())
        }


@dataclass
class LifecycleDrill:
    """Local state mirror and invariants for one deterministic drill run."""

    broker: SimBroker = field(default_factory=SimBroker)
    local_orders: dict[str, SimOrder] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)
    reconciled: bool = False
    mismatch: str | None = None

    def _record(self, event: str, **details: Any) -> None:
        self.events.append({"step": len(self.events) + 1, "event": event, **details})

    def create(self, order_id: str = "SIM-001", quantity: int = 10) -> SimOrder:
        order = SimOrder(order_id, "AAPL", quantity)
        self.local_orders[order_id] = order
        self._record("intent_created", order_id=order_id, quantity=quantity)
        return order

    def submit(self, order_id: str = "SIM-001") -> None:
        order = self.local_orders[order_id]
        self.broker.submit(order)
        self._record("submitted", order_id=order_id)

    def fill(self, quantity: int, order_id: str = "SIM-001") -> None:
        self.broker.fill(order_id, quantity)
        self._record("fill_received", order_id=order_id, quantity=quantity)

    def cancel_replace(self, new_price: float, order_id: str = "SIM-001") -> str:
        old = self.local_orders[order_id]
        self.broker.cancel(order_id)
        replacement_id = f"{order_id}-R{old.replacement_count + 1}"
        replacement = SimOrder(
            replacement_id,
            old.symbol,
            old.remaining,
            limit_price=new_price,
        )
        replacement.replacement_count = old.replacement_count + 1
        self.local_orders[replacement_id] = replacement
        self.broker.submit(replacement)
        self._record("cancel_replace", canceled=order_id, replacement=replacement_id, price=new_price)
        return replacement_id

    def disconnect(self) -> None:
        self.broker.connected = False
        self._record("disconnected")

    def reconnect(self) -> None:
        self.broker.connected = True
        self._record("reconnected")

    def restart(self) -> None:
        # A restart discards only process memory; broker truth is retained.
        self.local_orders = {}
        self.reconciled = False
        self._record("process_restarted")

    def reconcile(self) -> bool:
        broker_state = self.broker.snapshot()
        local_state = {
            order_id: {
                "symbol": order.symbol,
                "quantity": order.quantity,
                "filled": order.filled,
                "remaining": order.remaining,
                "status": order.status.value,
                "limit_price": order.limit_price,
            }
            for order_id, order in sorted(self.local_orders.items())
        }
        if broker_state != local_state:
            self.mismatch = "broker/local order state differs"
            self.reconciled = False
            self._record("reconciliation_mismatch", broker_orders=broker_state, local_orders=local_state)
            return False
        self.mismatch = None
        self.reconciled = True
        self._record("reconciled", order_count=len(broker_state))
        return True


def _result(name: str, drill: LifecycleDrill, passed: bool, **extra: Any) -> dict[str, Any]:
    return {
        "scenario": name,
        "passed": passed,
        "live_authorized": False,
        "broker": "deterministic-offline-double",
        "events": drill.events,
        "broker_orders": drill.broker.snapshot(),
        **extra,
    }


def scenario_normal_fill() -> dict[str, Any]:
    d = LifecycleDrill(); d.create(); d.submit(); d.fill(10)
    passed = d.local_orders["SIM-001"].status is OrderStatus.FILLED and d.reconcile()
    return _result("normal_fill", d, passed)


def scenario_partial_fill() -> dict[str, Any]:
    d = LifecycleDrill(); d.create(quantity=10); d.submit(); d.fill(4); d.fill(6)
    order = d.local_orders["SIM-001"]
    passed = order.filled == 10 and order.status is OrderStatus.FILLED and d.reconcile()
    return _result("partial_fill", d, passed, cumulative_filled=order.filled)


def scenario_cancel_replace() -> dict[str, Any]:
    d = LifecycleDrill(); d.create(); d.submit(); d.fill(3)
    replacement = d.cancel_replace(199.50); d.fill(7, replacement)
    passed = (
        d.broker.orders["SIM-001"].status is OrderStatus.CANCELED
        and d.broker.orders[replacement].status is OrderStatus.FILLED
        and d.broker.orders[replacement].quantity == 7
        and d.reconcile()
    )
    return _result("cancel_replace", d, passed, replacement_order_id=replacement)


def scenario_disconnect_reconnect() -> dict[str, Any]:
    d = LifecycleDrill(); d.create(); d.submit(); d.disconnect()
    rejected_while_offline = False
    try:
        d.fill(1)
    except ConnectionError:
        rejected_while_offline = True
    d.reconnect(); d.fill(10)
    passed = rejected_while_offline and d.reconcile()
    return _result("disconnect_reconnect", d, passed, rejected_while_offline=rejected_while_offline)


def scenario_restart_reconcile() -> dict[str, Any]:
    d = LifecycleDrill(); d.create(); d.submit(); d.fill(4); d.restart()
    missing_before_reconcile = not d.reconciled
    # Rehydrate local truth only after the broker snapshot is obtained.
    d.local_orders = {
        order_id: SimOrder(
            order_id,
            state["symbol"],
            state["quantity"],
            state["filled"],
            OrderStatus(state["status"]),
            state["limit_price"],
        )
        for order_id, state in d.broker.snapshot().items()
    }
    passed = missing_before_reconcile and d.reconcile()
    return _result("restart_reconcile", d, passed, readiness_before_reconcile=False)


def scenario_reconciliation_mismatch() -> dict[str, Any]:
    d = LifecycleDrill(); d.create(); d.submit(); d.fill(4)
    d.restart()
    # Deliberately provide stale local state; readiness must remain false.
    d.local_orders["SIM-001"] = SimOrder("SIM-001", "AAPL", 10, filled=0, status=OrderStatus.WORKING)
    passed = not d.reconcile() and d.mismatch is not None and not d.reconciled
    return _result("reconciliation_mismatch", d, passed, readiness=False)


SCENARIOS = {
    "normal_fill": scenario_normal_fill,
    "partial_fill": scenario_partial_fill,
    "cancel_replace": scenario_cancel_replace,
    "disconnect_reconnect": scenario_disconnect_reconnect,
    "restart_reconcile": scenario_restart_reconcile,
    "reconciliation_mismatch": scenario_reconciliation_mismatch,
}


def run(scenario: str = "all") -> dict[str, Any]:
    names = list(SCENARIOS) if scenario == "all" else [scenario]
    results = [SCENARIOS[name]() for name in names]
    return {
        "passed": all(result["passed"] for result in results),
        "live_authorized": False,
        "offline_only": True,
        "scenarios": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", choices=["all", *SCENARIOS], default="all")
    parser.add_argument("--json", action="store_true", help="emit machine-readable output")
    args = parser.parse_args()
    result = run(args.scenario)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

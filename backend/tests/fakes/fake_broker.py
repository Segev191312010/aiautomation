"""Deterministic PAPER broker harness for execution-safety tests.

This module deliberately has no IBKR or network dependency.  It models the
small broker boundary needed by the staged bracket executor: transmit order,
partial parent fills, OCA sibling cancellation, disconnects, and unknown
state.  It never retries or repairs an order.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal, Sequence

from trading.contracts import ReconciliationReport


LegType = Literal["parent", "target", "stop"]
OrderState = Literal[
    "staged",
    "accepted",
    "partial",
    "filled",
    "canceled",
    "rejected",
    "unknown",
]


class FakeBrokerError(RuntimeError):
    """Base class for deterministic fake-broker failures."""


class BrokerConfigurationError(FakeBrokerError, ValueError):
    """The adapter is not configured for the PAPER boundary."""


class BrokerSubmissionError(FakeBrokerError):
    """The fake broker rejected a staged submission."""


@dataclass(frozen=True, slots=True)
class PaperBrokerConfig:
    """Explicit PAPER connection identity; no live port is accepted."""

    host: str
    port: int
    account: str
    client_id: int

    def validate(self) -> None:
        if self.port != 7497:
            raise BrokerConfigurationError(
                "PAPER broker requires TWS port 7497; live ports are rejected"
            )
        if not self.host.strip():
            raise BrokerConfigurationError("PAPER broker host is required")
        if not self.account.strip():
            raise BrokerConfigurationError("PAPER account is required")
        if self.client_id <= 0:
            raise BrokerConfigurationError("a positive dedicated client_id is required")


@dataclass(frozen=True, slots=True)
class FakeOrder:
    """Immutable observation of one order known to the fake broker."""

    order_id: int
    order_ref: str
    role: LegType
    symbol: str
    security_type: Literal["STK"]
    side: Literal["BUY", "SELL"]
    quantity: int
    price: Decimal
    transmit: bool
    parent_order_id: int | None
    oca_group: str
    state: OrderState
    filled_quantity: int = 0
    protected_quantity: int = 0
    order_type: Literal["LMT"] = "LMT"


@dataclass(frozen=True, slots=True)
class SubmissionEvent:
    kind: Literal["submit", "cancel", "partial_fill", "fill", "oca_cancel"]
    order_id: int
    role: LegType
    quantity: int = 0
    transmit: bool | None = None
    observed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("broker event timestamps must be timezone-aware")
        object.__setattr__(self, "observed_at", self.observed_at.astimezone(timezone.utc))


@dataclass(frozen=True, slots=True)
class FakeBracketSubmission:
    state: Literal["accepted", "rejected", "manual_intervention"]
    orders: tuple[FakeOrder, ...]
    reason: str | None = None
    observed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("broker submission timestamps must be timezone-aware")
        object.__setattr__(self, "observed_at", self.observed_at.astimezone(timezone.utc))


@dataclass
class _MutableOrder:
    order_id: int
    order_ref: str
    role: LegType
    symbol: str
    security_type: Literal["STK"]
    side: Literal["BUY", "SELL"]
    quantity: int
    price: Decimal
    transmit: bool
    parent_order_id: int | None
    oca_group: str
    state: OrderState = "staged"
    filled_quantity: int = 0
    protected_quantity: int = 0
    order_type: Literal["LMT"] = "LMT"

    def snapshot(self) -> FakeOrder:
        return FakeOrder(
            order_id=self.order_id,
            order_ref=self.order_ref,
            role=self.role,
            symbol=self.symbol,
            security_type=self.security_type,
            side=self.side,
            quantity=self.quantity,
            price=self.price,
            transmit=self.transmit,
            parent_order_id=self.parent_order_id,
            oca_group=self.oca_group,
            state=self.state,
            filled_quantity=self.filled_quantity,
            protected_quantity=self.protected_quantity,
            order_type=self.order_type,
        )


class FakePaperBroker:
    """An in-memory, no-network PAPER broker with deliberately strict semantics."""

    def __init__(
        self,
        config: PaperBrokerConfig,
        *,
        fail_on_role: LegType | None = None,
        unknown_on_submit: bool = False,
    ) -> None:
        config.validate()
        self.config = config
        self.fail_on_role = fail_on_role
        self.unknown_on_submit = unknown_on_submit
        self._connected = True
        self._next_order_id = 1000
        self._orders: dict[int, _MutableOrder] = {}
        self._reserved_order_ids: set[int] = set()
        self._submitted_refs: set[str] = set()
        self._submission_attempts = 0
        self._events: list[SubmissionEvent] = []

    @property
    def orders(self) -> tuple[FakeOrder, ...]:
        return tuple(order.snapshot() for order in self._orders.values())

    @property
    def submission_log(self) -> tuple[SubmissionEvent, ...]:
        return tuple(self._events)

    @property
    def submission_attempts(self) -> int:
        return self._submission_attempts

    def reserve_order_ids(self, count: int) -> tuple[int, ...]:
        if count < 1:
            raise ValueError("count must be positive")
        reserved = tuple(range(self._next_order_id, self._next_order_id + count))
        self._next_order_id += count
        self._reserved_order_ids.update(reserved)
        return reserved

    async def submit_staged_bracket(
        self,
        orders: Sequence[FakeOrder],
        *,
        account: str,
        client_id: int,
    ) -> FakeBracketSubmission:
        """Accept only parent/target/stop in the broker transmit order.

        The caller must have persisted the order intents first.  This method
        does not retry after any failure; a caller must fence the group and
        reconcile it manually.
        """
        self._submission_attempts += 1
        self._require_identity(account=account, client_id=client_id)
        if not self._connected:
            raise BrokerSubmissionError("PAPER broker is disconnected")
        self._validate_sequence(orders)

        for order in orders:
            if order.order_id not in self._reserved_order_ids:
                raise BrokerSubmissionError("order ID was not reserved by this broker")
            if order.order_ref in self._submitted_refs:
                raise BrokerSubmissionError("duplicate order reference")
            self._reserved_order_ids.remove(order.order_id)
            self._submitted_refs.add(order.order_ref)
            mutable = _MutableOrder(
                order_id=order.order_id,
                order_ref=order.order_ref,
                role=order.role,
                symbol=order.symbol,
                security_type=order.security_type,
                side=order.side,
                quantity=order.quantity,
                price=order.price,
                transmit=order.transmit,
                parent_order_id=order.parent_order_id,
                oca_group=order.oca_group,
                state=order.state,
                filled_quantity=order.filled_quantity,
                protected_quantity=order.protected_quantity,
                order_type=order.order_type,
            )
            self._orders[order.order_id] = mutable
            self._events.append(
                SubmissionEvent(
                    kind="submit",
                    order_id=order.order_id,
                    role=order.role,
                    transmit=order.transmit,
                    observed_at=datetime.now(timezone.utc),
                )
            )
            if self.fail_on_role == order.role:
                mutable.state = "rejected"
                raise BrokerSubmissionError(f"fake broker rejected {order.role} leg")

        if self.unknown_on_submit:
            for order in orders:
                self._orders[order.order_id].state = "unknown"
            return FakeBracketSubmission(
                state="manual_intervention",
                orders=tuple(self._orders[order.order_id].snapshot() for order in orders),
                reason="broker returned unknown bracket state",
            )

        for order in orders:
            self._orders[order.order_id].state = "accepted"
        return FakeBracketSubmission(
            state="accepted",
            orders=tuple(self._orders[order.order_id].snapshot() for order in orders),
        )

    async def cancel_staged(self, order_ids: Sequence[int]) -> None:
        """Cancel known staged orders; this is cleanup, never repair/retry."""
        for order_id in order_ids:
            order = self._orders.get(order_id)
            if order is None or order.state in {"filled", "canceled"}:
                continue
            order.state = "canceled"
            self._events.append(
                SubmissionEvent(
                    kind="cancel",
                    order_id=order.order_id,
                    role=order.role,
                    observed_at=datetime.now(timezone.utc),
                )
            )

    async def fill_parent(self, order_id: int, quantity: int) -> tuple[SubmissionEvent, ...]:
        """Fill a parent, marking the exact filled quantity protected by both children."""
        parent = self._require_order(order_id, role="parent")
        if parent.state not in {"accepted", "partial"}:
            raise BrokerSubmissionError("parent is not fillable")
        remaining = parent.quantity - parent.filled_quantity
        if quantity <= 0 or quantity > remaining:
            raise BrokerSubmissionError("parent fill exceeds remaining quantity")
        parent.filled_quantity += quantity
        parent.state = "filled" if parent.filled_quantity == parent.quantity else "partial"
        event = SubmissionEvent(
            kind="partial_fill" if parent.state == "partial" else "fill",
            order_id=parent.order_id,
            role="parent",
            quantity=quantity,
            observed_at=datetime.now(timezone.utc),
        )
        self._events.append(event)
        for child in self._children(parent.order_id):
            child.protected_quantity = parent.filled_quantity
        return (event,)

    async def fill_exit(self, order_id: int) -> tuple[SubmissionEvent, ...]:
        """Fill one protective child and apply broker-native OCA cancellation."""
        exit_order = self._require_order(order_id, role=None)
        if exit_order.role not in {"stop", "target"}:
            raise BrokerSubmissionError("only protective children can be filled")
        if exit_order.state not in {"accepted", "partial"}:
            raise BrokerSubmissionError("protective child is not fillable")
        if exit_order.protected_quantity <= 0:
            raise BrokerSubmissionError("protective child has no filled parent quantity")
        exit_order.state = "filled"
        events = [
            SubmissionEvent(
                kind="fill",
                order_id=exit_order.order_id,
                role=exit_order.role,
                quantity=exit_order.protected_quantity,
                observed_at=datetime.now(timezone.utc),
            )
        ]
        for sibling in self._children(exit_order.parent_order_id):
            if sibling.order_id != exit_order.order_id and sibling.state == "accepted":
                sibling.state = "canceled"
                events.append(
                    SubmissionEvent(
                        kind="oca_cancel",
                        order_id=sibling.order_id,
                        role=sibling.role,
                        observed_at=datetime.now(timezone.utc),
                    )
                )
        self._events.extend(events)
        return tuple(events)

    def disconnect(self) -> None:
        self._connected = False

    def reconnect(self) -> None:
        self._connected = True

    def mark_unknown(self, order_id: int) -> None:
        self._require_order(order_id, role=None).state = "unknown"

    async def reconcile(self) -> ReconciliationReport:
        """Report broker state without modifying or repairing any order."""
        unknowns = tuple(
            f"order:{order.order_id}:unknown"
            for order in self._orders.values()
            if order.state == "unknown"
        )
        status: Literal["clear", "blocked"] = "blocked" if unknowns or not self._connected else "clear"
        if not self._connected:
            unknowns = (*unknowns, "broker:disconnected")
        return ReconciliationReport(
            paper_account=self.config.account,
            client_id=self.config.client_id,
            status=status,
            orders=tuple(
                {
                    "order_id": order.order_id,
                    "role": order.role,
                    "state": order.state,
                    "quantity": order.quantity,
                    "filled_quantity": order.filled_quantity,
                    "protected_quantity": order.protected_quantity,
                }
                for order in self._orders.values()
            ),
            unknowns=unknowns,
            observed_at=datetime.now(timezone.utc),
        )

    def _require_identity(self, *, account: str, client_id: int) -> None:
        if account != self.config.account:
            raise BrokerConfigurationError("broker account does not match PAPER identity")
        if client_id != self.config.client_id:
            raise BrokerConfigurationError("broker client_id does not match PAPER identity")

    @staticmethod
    def _validate_sequence(orders: Sequence[FakeOrder]) -> None:
        if len(orders) not in {2, 3}:
            raise BrokerSubmissionError("bracket must contain parent, stop, and optional target")
        roles = tuple(order.role for order in orders)
        if roles not in {("parent", "target", "stop"), ("parent", "stop")}:
            raise BrokerSubmissionError("bracket transmit order must end with stop")
        for index, order in enumerate(orders):
            if order.security_type != "STK" or order.order_type != "LMT":
                raise BrokerSubmissionError("fake PAPER broker accepts stock orders only")
            if order.side != ("BUY" if order.role == "parent" else "SELL"):
                raise BrokerSubmissionError("long bracket sides must be BUY parent and SELL children")
            if order.quantity <= 0 or not order.price.is_finite() or order.price <= 0:
                raise BrokerSubmissionError("order quantity and price must be finite and positive")
            if order.transmit != (index == len(orders) - 1):
                raise BrokerSubmissionError("only the final stop leg may transmit")
            if order.role == "parent" and order.parent_order_id is not None:
                raise BrokerSubmissionError("parent cannot reference a parent order")
            if order.role != "parent" and order.parent_order_id != orders[0].order_id:
                raise BrokerSubmissionError("child must reference the parent order")
            if order.symbol != orders[0].symbol:
                raise BrokerSubmissionError("bracket legs must use one symbol")
        if not orders[0].price > next(
            child.price for child in orders[1:] if child.role == "stop"
        ):
            raise BrokerSubmissionError("protective stop must be below the entry limit")
        targets = [child.price for child in orders[1:] if child.role == "target"]
        if targets and not orders[0].price < targets[0]:
            raise BrokerSubmissionError("target must be above the entry limit")
        if len({order.oca_group for order in orders[1:]}) != 1:
            raise BrokerSubmissionError("protective children must share one OCA group")

    def _require_order(self, order_id: int, *, role: LegType | None) -> _MutableOrder:
        order = self._orders.get(order_id)
        if order is None or (role is not None and order.role != role):
            raise BrokerSubmissionError("unknown broker order")
        return order

    def _children(self, parent_order_id: int | None) -> list[_MutableOrder]:
        if parent_order_id is None:
            return []
        return [
            order
            for order in self._orders.values()
            if order.parent_order_id == parent_order_id
        ]

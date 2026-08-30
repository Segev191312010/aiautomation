"""Lazy, fail-closed order transport for a local TWS PAPER session.

This module is an I/O boundary only.  It does not authorize PAPER execution,
start a connection during import or construction, schedule orders, or decide
when an order should be changed.  A caller must explicitly connect, reserve
IDs, and submit each staged order.
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import threading
from typing import Any, Protocol, TypeAlias

from trading.execution.ibkr_paper import BrokerLegReceipt


PAPER_TWS_HOST = "127.0.0.1"
PAPER_TWS_PORT = 7497


class TWSPaperTransportError(RuntimeError):
    """Base error for transport configuration, lifecycle, and broker I/O."""


class TWSConfigurationError(TWSPaperTransportError, ValueError):
    """The transport is not configured for the local PAPER boundary."""


class TWSHandshakeError(TWSPaperTransportError):
    """TWS did not complete the required identity handshake."""


class TWSSubmissionError(TWSPaperTransportError):
    """A staged order could not be submitted or acknowledged safely."""


@dataclass(frozen=True, slots=True)
class TWSPaperTransportConfig:
    """Explicit local endpoint and account identity for one TWS client."""

    account: str
    client_id: int
    host: str = PAPER_TWS_HOST
    port: int = PAPER_TWS_PORT
    handshake_timeout_seconds: float = 10.0
    acknowledgement_timeout_seconds: float = 10.0

    def validate(self) -> None:
        if self.host != PAPER_TWS_HOST:
            raise TWSConfigurationError(
                f"TWS PAPER transport requires host {PAPER_TWS_HOST}"
            )
        if self.port != PAPER_TWS_PORT:
            raise TWSConfigurationError("TWS PAPER transport requires port 7497")
        if not isinstance(self.account, str) or not self.account.strip():
            raise TWSConfigurationError("a PAPER account is required")
        if not isinstance(self.client_id, int) or isinstance(self.client_id, bool):
            raise TWSConfigurationError("client_id must be an integer")
        if self.client_id <= 0:
            raise TWSConfigurationError("a positive dedicated client_id is required")
        if self.handshake_timeout_seconds <= 0:
            raise TWSConfigurationError("handshake timeout must be positive")
        if self.acknowledgement_timeout_seconds <= 0:
            raise TWSConfigurationError("acknowledgement timeout must be positive")


@dataclass(frozen=True, slots=True)
class TWSOrderTransportIdentity:
    """Identity reported to an execution boundary after configuration checks."""

    host: str
    port: int
    account: str
    client_id: int

    def validate(self) -> None:
        TWSPaperTransportConfig(
            account=self.account,
            client_id=self.client_id,
            host=self.host,
            port=self.port,
        ).validate()


@dataclass(frozen=True, slots=True)
class OpenOrderEvent:
    """Typed observation of an IBKR ``openOrder`` callback."""

    order_id: int
    symbol: str
    security_type: str
    side: str
    order_type: str
    quantity: Decimal
    limit_price: Decimal | None
    auxiliary_price: Decimal | None
    parent_order_id: int | None
    transmit: bool
    oca_group: str | None
    account: str | None
    order_ref: str | None
    perm_id: str | None
    status: str | None
    received_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        _require_aware_timestamp(self.received_at)
        object.__setattr__(
            self, "received_at", self.received_at.astimezone(timezone.utc)
        )


@dataclass(frozen=True, slots=True)
class OrderStatusEvent:
    """Typed observation of an IBKR ``orderStatus`` callback."""

    order_id: int
    status: str
    filled: Decimal
    remaining: Decimal
    average_fill_price: Decimal
    perm_id: str | None
    parent_order_id: int | None
    last_fill_price: Decimal | None
    client_id: int | None
    received_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        _require_aware_timestamp(self.received_at)
        object.__setattr__(
            self, "received_at", self.received_at.astimezone(timezone.utc)
        )


@dataclass(frozen=True, slots=True)
class ExecutionEvent:
    """Typed observation of an IBKR ``execDetails`` callback."""

    request_id: int
    execution_id: str
    order_id: int
    perm_id: str | None
    account: str | None
    shares: Decimal
    price: Decimal
    side: str | None
    received_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        _require_aware_timestamp(self.received_at)
        object.__setattr__(
            self, "received_at", self.received_at.astimezone(timezone.utc)
        )


@dataclass(frozen=True, slots=True)
class BrokerErrorEvent:
    """Typed non-secret observation of a TWS error callback."""

    request_id: int
    code: int
    message: str
    received_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        _require_aware_timestamp(self.received_at)
        object.__setattr__(
            self, "received_at", self.received_at.astimezone(timezone.utc)
        )


@dataclass(frozen=True, slots=True)
class ConnectionClosedEvent:
    """Typed observation that the broker callback loop closed."""

    received_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        _require_aware_timestamp(self.received_at)
        object.__setattr__(
            self, "received_at", self.received_at.astimezone(timezone.utc)
        )


BrokerEvent: TypeAlias = (
    OpenOrderEvent
    | OrderStatusEvent
    | ExecutionEvent
    | BrokerErrorEvent
    | ConnectionClosedEvent
)


class TWSClient(Protocol):
    def connect(self, host: str, port: int, client_id: int) -> Any: ...

    def disconnect(self) -> Any: ...

    def run(self) -> Any: ...

    def placeOrder(self, order_id: int, contract: Any, order: Any) -> Any: ...


class _CallbackSink:
    """Translate official callback names into typed transport events."""

    def __init__(self, transport: "TWSPaperTransport") -> None:
        self._transport = transport

    def nextValidId(self, order_id: int) -> None:
        self._transport._on_next_valid_id(order_id)

    def managedAccounts(self, accounts: str) -> None:
        self._transport._on_managed_accounts(accounts)

    def openOrder(self, order_id: int, contract: Any, order: Any, order_state: Any) -> None:
        self._transport._on_open_order(order_id, contract, order, order_state)

    def orderStatus(
        self,
        order_id: int,
        status: str,
        filled: Any,
        remaining: Any,
        average_fill_price: Any,
        perm_id: Any,
        parent_id: Any,
        last_fill_price: Any,
        client_id: Any,
        *args: Any,
    ) -> None:
        self._transport._on_order_status(
            order_id,
            status,
            filled,
            remaining,
            average_fill_price,
            perm_id,
            parent_id,
            last_fill_price,
            client_id,
        )

    def execDetails(self, request_id: int, contract: Any, execution: Any) -> None:
        self._transport._on_execution(request_id, contract, execution)

    def error(self, request_id: int, code: int, message: str, *args: Any) -> None:
        self._transport._on_error(request_id, code, message)

    def connectionClosed(self) -> None:
        self._transport._on_connection_closed()

    next_valid_id = nextValidId
    managed_accounts = managedAccounts
    open_order = openOrder
    order_status = orderStatus
    exec_details = execDetails


def _official_client_factory(sink: _CallbackSink) -> TWSClient:
    """Instantiate official ``ibapi`` only after an explicit ``connect``."""
    from ibapi.client import EClient
    from ibapi.wrapper import EWrapper

    class Client(EWrapper, EClient):
        def __init__(self) -> None:
            EWrapper.__init__(self)
            EClient.__init__(self, self)
            self._sink = sink

        def nextValidId(self, value: int) -> None:
            self._sink.nextValidId(value)

        def managedAccounts(self, value: str) -> None:
            self._sink.managedAccounts(value)

        def openOrder(self, *args: Any) -> None:
            self._sink.openOrder(*args)

        def orderStatus(self, *args: Any) -> None:
            self._sink.orderStatus(*args)

        def execDetails(self, *args: Any) -> None:
            self._sink.execDetails(*args)

        def error(self, *args: Any) -> None:
            self._sink.error(*args)

        def connectionClosed(self) -> None:
            self._sink.connectionClosed()

    return Client()


def _official_contract_factory(order: Any) -> Any:
    from ibapi.contract import Contract

    contract = Contract()
    contract.symbol = str(order.symbol)
    contract.secType = "STK"
    contract.exchange = "SMART"
    contract.currency = "USD"
    return contract


def _official_order_factory(order: Any) -> Any:
    from ibapi.order import Order

    broker_order = Order()
    broker_order.action = str(order.side)
    broker_order.orderType = str(order.order_type)
    broker_order.totalQuantity = order.quantity
    if order.order_type == "LMT":
        broker_order.lmtPrice = float(order.price)
    else:
        broker_order.auxPrice = float(order.price)
    if order.parent_order_id is not None:
        broker_order.parentId = order.parent_order_id
    broker_order.transmit = bool(order.transmit)
    broker_order.orderRef = str(order.order_ref)
    broker_order.tif = "DAY"
    if order.oca_group:
        broker_order.ocaGroup = str(order.oca_group)
        broker_order.ocaType = 1
    return broker_order


@dataclass
class _PendingSubmission:
    future: asyncio.Future[BrokerLegReceipt]


class TWSPaperTransport:
    """Explicit local TWS PAPER order transport with no implicit I/O."""

    _ACCEPTED_STATUSES = frozenset(
        {"ApiPending", "PendingSubmit", "PreSubmitted", "Submitted", "Filled", "PartiallyFilled"}
    )
    _REJECTED_STATUSES = frozenset(
        {"ApiCancelled", "Cancelled", "Inactive", "Rejected"}
    )

    def __init__(
        self,
        config: TWSPaperTransportConfig,
        *,
        client_factory: Callable[[_CallbackSink], TWSClient] | None = None,
        contract_factory: Callable[[Any], Any] | None = None,
        order_factory: Callable[[Any], Any] | None = None,
    ) -> None:
        config.validate()
        self.config = config
        self._client_factory = client_factory or _official_client_factory
        self._contract_factory = contract_factory or _official_contract_factory
        self._order_factory = order_factory or _official_order_factory
        self._sink = _CallbackSink(self)
        self._client: TWSClient | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._run_thread: threading.Thread | None = None
        self._connected = False
        self._handshake_event: asyncio.Event | None = None
        self._accounts_event: asyncio.Event | None = None
        self._managed_accounts: frozenset[str] = frozenset()
        self._next_order_id: int | None = None
        self._reserved_order_ids: set[int] = set()
        self._submitted_order_ids: set[int] = set()
        self._submitted_order_refs: set[str] = set()
        self._pending: dict[int, _PendingSubmission] = {}
        self._events: list[BrokerEvent] = []
        self._state_lock = threading.RLock()

    @property
    def identity(self) -> TWSOrderTransportIdentity:
        return TWSOrderTransportIdentity(
            host=self.config.host,
            port=self.config.port,
            account=self.config.account,
            client_id=self.config.client_id,
        )

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def managed_accounts(self) -> frozenset[str]:
        return self._managed_accounts

    @property
    def events(self) -> tuple[BrokerEvent, ...]:
        with self._state_lock:
            return tuple(self._events)

    async def connect(self) -> None:
        """Open an explicit connection and verify next ID plus PAPER account."""
        self.config.validate()
        if self._connected:
            return
        self._loop = asyncio.get_running_loop()
        self._handshake_event = asyncio.Event()
        self._accounts_event = asyncio.Event()
        try:
            self._client = self._client_factory(self._sink)
            self._client.connect(self.config.host, self.config.port, self.config.client_id)
            run = getattr(self._client, "run", None)
            if callable(run):
                self._run_thread = threading.Thread(target=run, daemon=True)
                self._run_thread.start()
            await asyncio.wait_for(
                self._handshake_event.wait(), timeout=self.config.handshake_timeout_seconds
            )
            self._connected = True
            request_accounts = getattr(self._client, "reqManagedAccts", None)
            if not callable(request_accounts):
                raise TWSHandshakeError("TWS client cannot verify the PAPER account")
            request_accounts()
            await asyncio.wait_for(
                self._accounts_event.wait(), timeout=self.config.handshake_timeout_seconds
            )
            if self.config.account not in self._managed_accounts:
                raise TWSHandshakeError("configured PAPER account was not returned by TWS")
        except asyncio.TimeoutError as exc:
            await self.disconnect()
            raise TWSHandshakeError("TWS PAPER handshake timed out") from exc
        except Exception as exc:
            await self.disconnect()
            if isinstance(exc, TWSPaperTransportError):
                raise
            raise TWSHandshakeError("TWS PAPER handshake failed") from exc

    async def disconnect(self) -> None:
        """Close the explicit connection and invalidate unsubmitted IDs."""
        self._connected = False
        client, self._client = self._client, None
        if client is not None:
            try:
                client.disconnect()
            except Exception:
                pass
        self._next_order_id = None
        self._reserved_order_ids.clear()
        self._run_thread = None
        self._fail_pending(TWSPaperTransportError("TWS PAPER transport disconnected"))

    def reserve_order_ids(self, count: int) -> tuple[int, ...]:
        """Reserve contiguous IDs only after the TWS handshake completed."""
        if not isinstance(count, int) or isinstance(count, bool) or count < 1:
            raise ValueError("count must be a positive integer")
        self._require_ready()
        with self._state_lock:
            next_id = self._next_order_id
            if next_id is None:
                raise TWSHandshakeError("TWS has not supplied the next order ID")
            reserved = tuple(range(next_id, next_id + count))
            self._next_order_id = next_id + count
            self._reserved_order_ids.update(reserved)
            return reserved

    async def submit_order(
        self, order: Any, *, account: str, client_id: int
    ) -> BrokerLegReceipt:
        """Place one explicitly requested staged order and await broker status."""
        self._validate_identity(account=account, client_id=client_id)
        self._require_ready()
        self._validate_order(order)
        order_id = int(order.order_id)
        with self._state_lock:
            if order_id not in self._reserved_order_ids:
                raise TWSSubmissionError("order ID was not reserved after handshake")
            if order_id in self._submitted_order_ids:
                raise TWSSubmissionError("order ID has already been submitted")
            order_ref = str(order.order_ref)
            if order_ref in self._submitted_order_refs:
                raise TWSSubmissionError("order reference has already been submitted")
            if order.leg_type != "parent" and int(order.parent_order_id) not in self._submitted_order_ids:
                raise TWSSubmissionError("parent order must be submitted before a child")
            self._reserved_order_ids.remove(order_id)
            self._submitted_order_ids.add(order_id)
            self._submitted_order_refs.add(order_ref)
        loop = self._loop
        if loop is None:
            raise TWSHandshakeError("TWS event loop is not initialized")
        pending = _PendingSubmission(loop.create_future())
        self._pending[order_id] = pending
        try:
            contract = self._contract_factory(order)
            broker_order = self._order_factory(order)
            client = self._client
            if client is None:
                raise TWSHandshakeError("TWS client is disconnected")
            client.placeOrder(order_id, contract, broker_order)
            return await asyncio.wait_for(
                pending.future, timeout=self.config.acknowledgement_timeout_seconds
            )
        except asyncio.TimeoutError as exc:
            raise TWSSubmissionError(
                "broker acknowledgement timed out; order state requires reconciliation"
            ) from exc
        except TWSPaperTransportError:
            raise
        except Exception as exc:
            raise TWSSubmissionError("TWS placeOrder failed") from exc
        finally:
            self._pending.pop(order_id, None)

    async def submit_parent(self, order: Any, *, account: str, client_id: int) -> BrokerLegReceipt:
        self._require_leg(order, "parent")
        return await self.submit_order(order, account=account, client_id=client_id)

    async def submit_target(self, order: Any, *, account: str, client_id: int) -> BrokerLegReceipt:
        self._require_leg(order, "target")
        return await self.submit_order(order, account=account, client_id=client_id)

    async def submit_stop(self, order: Any, *, account: str, client_id: int) -> BrokerLegReceipt:
        self._require_leg(order, "stop")
        return await self.submit_order(order, account=account, client_id=client_id)

    async def cancel_order(self, order_id: int, *, account: str, client_id: int) -> None:
        """Cancel only an explicitly requested known order; never retry or repair."""
        self._validate_identity(account=account, client_id=client_id)
        self._require_ready()
        if order_id not in self._submitted_order_ids:
            raise TWSSubmissionError("only a submitted order can be explicitly canceled")
        client = self._client
        if client is None:
            raise TWSHandshakeError("TWS client is disconnected")
        cancel = getattr(client, "cancelOrder", None)
        if not callable(cancel):
            raise TWSSubmissionError("TWS client does not support order cancellation")
        cancel(order_id)

    def _require_ready(self) -> None:
        if not self._connected or self._client is None:
            raise TWSHandshakeError("connect() must complete before order transport use")
        if self._next_order_id is None:
            raise TWSHandshakeError("TWS handshake did not provide an order ID")

    def _validate_identity(self, *, account: str, client_id: int) -> None:
        if account != self.config.account or client_id != self.config.client_id:
            raise TWSSubmissionError("account or client identity does not match TWS PAPER transport")

    @classmethod
    def _require_leg(cls, order: Any, leg_type: str) -> None:
        if getattr(order, "leg_type", None) != leg_type:
            raise TWSSubmissionError(f"explicit {leg_type} submission received a different leg")

    @classmethod
    def _validate_order(cls, order: Any) -> None:
        leg_type = getattr(order, "leg_type", None)
        if leg_type not in {"parent", "target", "stop"}:
            raise TWSSubmissionError("order leg_type must be parent, target, or stop")
        order_id = getattr(order, "order_id", None)
        if not isinstance(order_id, int) or isinstance(order_id, bool) or order_id <= 0:
            raise TWSSubmissionError("order ID must be a positive integer")
        if not isinstance(getattr(order, "order_ref", None), str) or not order.order_ref.strip():
            raise TWSSubmissionError("order reference is required")
        if not isinstance(getattr(order, "symbol", None), str) or not order.symbol.strip():
            raise TWSSubmissionError("stock symbol is required")
        if getattr(order, "side", None) != ("BUY" if leg_type == "parent" else "SELL"):
            raise TWSSubmissionError("PAPER bracket side is invalid for this leg")
        expected_type = "LMT" if leg_type in {"parent", "target"} else "STP"
        if getattr(order, "order_type", None) != expected_type:
            raise TWSSubmissionError("PAPER bracket order type is invalid for this leg")
        quantity = _decimal(getattr(order, "quantity", None))
        if quantity is None or not quantity.is_finite() or quantity <= 0:
            raise TWSSubmissionError("order quantity must be finite and positive")
        price = _decimal(getattr(order, "price", None))
        if price is None or not price.is_finite() or price <= 0:
            raise TWSSubmissionError("order price must be finite and positive")
        if not isinstance(getattr(order, "transmit", None), bool):
            raise TWSSubmissionError("order transmit flag is required")
        parent_id = getattr(order, "parent_order_id", None)
        if leg_type == "parent":
            if parent_id is not None or order.transmit:
                raise TWSSubmissionError("parent must be non-transmitting and parentless")
            if getattr(order, "oca_group", None) is not None:
                raise TWSSubmissionError("parent cannot use the protective OCA group")
        else:
            if not isinstance(parent_id, int) or isinstance(parent_id, bool) or parent_id <= 0:
                raise TWSSubmissionError("child must reference a positive parent order ID")
            if not isinstance(getattr(order, "oca_group", None), str) or not order.oca_group.strip():
                raise TWSSubmissionError("protective child requires an OCA group")
            expected_transmit = leg_type == "stop"
            if order.transmit != expected_transmit:
                raise TWSSubmissionError(
                    "only the final protective stop may transmit"
                )

    def _on_next_valid_id(self, order_id: int) -> None:
        if not isinstance(order_id, int) or order_id < 0:
            return
        self._next_order_id = order_id
        self._signal_event(self._handshake_event)

    def _on_managed_accounts(self, accounts: str) -> None:
        self._managed_accounts = frozenset(
            value.strip() for value in str(accounts).split(",") if value.strip()
        )
        self._signal_event(self._accounts_event)

    def _on_open_order(self, order_id: int, contract: Any, order: Any, order_state: Any) -> None:
        status = _optional_text(getattr(order_state, "status", None))
        event = OpenOrderEvent(
            order_id=int(order_id),
            symbol=_optional_text(getattr(contract, "symbol", None)) or "",
            security_type=_optional_text(getattr(contract, "secType", None)) or "",
            side=_optional_text(getattr(order, "action", None)) or "",
            order_type=_optional_text(getattr(order, "orderType", None)) or "",
            quantity=_decimal(getattr(order, "totalQuantity", 0)) or Decimal("0"),
            limit_price=_decimal(getattr(order, "lmtPrice", None)),
            auxiliary_price=_decimal(getattr(order, "auxPrice", None)),
            parent_order_id=_optional_int(getattr(order, "parentId", None)),
            transmit=bool(getattr(order, "transmit", False)),
            oca_group=_optional_text(getattr(order, "ocaGroup", None)),
            account=_optional_text(getattr(order, "account", None)),
            order_ref=_optional_text(getattr(order, "orderRef", None)),
            perm_id=_optional_id(getattr(order, "permId", None)),
            status=status,
        )
        self._append_event(event)
        if status in self._ACCEPTED_STATUSES:
            self._resolve_submission(
                event.order_id,
                BrokerLegReceipt(
                    order_id=event.order_id,
                    accepted=True,
                    state="accepted",
                    perm_id=event.perm_id,
                ),
            )
        elif status in self._REJECTED_STATUSES:
            self._resolve_submission(
                event.order_id,
                BrokerLegReceipt(
                    order_id=event.order_id,
                    accepted=False,
                    state="rejected",
                    perm_id=event.perm_id,
                    reason=f"TWS order state: {status}",
                ),
            )

    def _on_order_status(
        self,
        order_id: int,
        status: str,
        filled: Any,
        remaining: Any,
        average_fill_price: Any,
        perm_id: Any,
        parent_id: Any,
        last_fill_price: Any,
        client_id: Any,
    ) -> None:
        event = OrderStatusEvent(
            order_id=int(order_id),
            status=str(status),
            filled=_decimal(filled) or Decimal("0"),
            remaining=_decimal(remaining) or Decimal("0"),
            average_fill_price=_decimal(average_fill_price) or Decimal("0"),
            perm_id=_optional_id(perm_id),
            parent_order_id=_optional_int(parent_id),
            last_fill_price=_decimal(last_fill_price),
            client_id=_optional_int(client_id),
        )
        self._append_event(event)
        if event.status in self._ACCEPTED_STATUSES:
            self._resolve_submission(
                event.order_id,
                BrokerLegReceipt(
                    order_id=event.order_id,
                    accepted=True,
                    state="accepted",
                    perm_id=event.perm_id,
                ),
            )
        elif event.status in self._REJECTED_STATUSES:
            self._resolve_submission(
                event.order_id,
                BrokerLegReceipt(
                    order_id=event.order_id,
                    accepted=False,
                    state="rejected",
                    perm_id=event.perm_id,
                    reason=f"TWS order status: {event.status}",
                ),
            )

    def _on_execution(self, request_id: int, contract: Any, execution: Any) -> None:
        event = ExecutionEvent(
            request_id=int(request_id),
            execution_id=_optional_text(getattr(execution, "execId", None)) or "",
            order_id=_optional_int(getattr(execution, "orderId", None)) or 0,
            perm_id=_optional_id(getattr(execution, "permId", None)),
            account=_optional_text(getattr(execution, "acctNumber", None)),
            shares=_decimal(getattr(execution, "shares", 0)) or Decimal("0"),
            price=_decimal(getattr(execution, "price", 0)) or Decimal("0"),
            side=_optional_text(getattr(execution, "side", None)),
        )
        self._append_event(event)

    def _on_error(self, request_id: int, code: int, message: str) -> None:
        event = BrokerErrorEvent(request_id=int(request_id), code=int(code), message=str(message))
        self._append_event(event)
        if request_id in self._pending:
            self._resolve_submission(
                request_id,
                BrokerLegReceipt(
                    order_id=request_id,
                    accepted=False,
                    state="rejected",
                    reason=f"TWS error {code}",
                ),
            )

    def _on_connection_closed(self) -> None:
        self._connected = False
        self._append_event(ConnectionClosedEvent())
        self._fail_pending(TWSPaperTransportError("TWS PAPER connection closed"))

    def _append_event(self, event: BrokerEvent) -> None:
        with self._state_lock:
            self._events.append(event)

    def _resolve_submission(self, order_id: int, receipt: BrokerLegReceipt) -> None:
        pending = self._pending.get(order_id)
        if pending is None:
            return
        self._set_future_result(pending.future, receipt)

    def _fail_pending(self, error: Exception) -> None:
        for pending in tuple(self._pending.values()):
            self._set_future_exception(pending.future, error)
        self._pending.clear()

    def _signal_event(self, event: asyncio.Event | None) -> None:
        loop = self._loop
        if event is None or loop is None:
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            event.set()
        else:
            loop.call_soon_threadsafe(event.set)

    def _set_future_result(self, future: asyncio.Future[Any], value: Any) -> None:
        loop = self._loop
        if loop is None:
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            if not future.done():
                future.set_result(value)
        else:
            loop.call_soon_threadsafe(self._resolve_result, future, value)

    @staticmethod
    def _resolve_result(future: asyncio.Future[Any], value: Any) -> None:
        if not future.done():
            future.set_result(value)

    def _set_future_exception(self, future: asyncio.Future[Any], error: Exception) -> None:
        loop = self._loop
        if loop is None:
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            if not future.done():
                future.set_exception(error)
        else:
            loop.call_soon_threadsafe(self._resolve_exception, future, error)

    @staticmethod
    def _resolve_exception(future: asyncio.Future[Any], error: Exception) -> None:
        if not future.done():
            future.set_exception(error)


def _require_aware_timestamp(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("broker event timestamps must be timezone-aware")


def _decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return result if result.is_finite() else None


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    result = str(value).strip()
    return result or None


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_id(value: Any) -> str | None:
    parsed = _optional_int(value)
    if parsed is not None and parsed > 0:
        return str(parsed)
    return _optional_text(value)


TWSOrderTransport = TWSPaperTransport
"""Backward-readable alias for callers naming the boundary by its role."""

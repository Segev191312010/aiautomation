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
from uuid import uuid4
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
    con_id: int
    symbol: str
    security_type: str
    primary_exchange: str
    currency: str
    side: str
    order_type: str
    quantity: Decimal
    limit_price: Decimal | None
    auxiliary_price: Decimal | None
    parent_order_id: int | None
    transmit: bool
    oca_group: str | None
    account: str | None
    client_id: int | None
    order_ref: str | None
    perm_id: str | None
    tif: str | None
    outside_rth: bool | None
    oca_type: int | None
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
    con_id: int
    symbol: str
    security_type: str
    currency: str
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
class PositionEvent:
    """Typed account position observed during reconciliation."""

    account: str
    con_id: int
    symbol: str
    security_type: str
    primary_exchange: str
    currency: str
    quantity: Decimal
    average_cost: Decimal
    received_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        _require_aware_timestamp(self.received_at)
        object.__setattr__(self, "received_at", self.received_at.astimezone(timezone.utc))


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
    | PositionEvent
    | BrokerErrorEvent
    | ConnectionClosedEvent
)


class BrokerObservationStore(Protocol):
    async def begin_session(
        self, *, session_id: str, owner_id: str, account: str, client_id: int,
    ) -> None: ...

    async def append(
        self, *, session_id: str, sequence: int, owner_id: str,
        account: str, client_id: int, event: BrokerEvent,
    ) -> None: ...

    async def close_session(
        self, *, session_id: str, owner_id: str, account: str, client_id: int,
        requires_reconciliation: bool,
    ) -> None: ...


class TWSClient(Protocol):
    def connect(self, host: str, port: int, client_id: int) -> Any: ...

    def disconnect(self) -> Any: ...

    def run(self) -> Any: ...

    def placeOrder(self, order_id: int, contract: Any, order: Any) -> Any: ...

    def reqAllOpenOrders(self) -> Any: ...

    def reqPositions(self) -> Any: ...

    def cancelPositions(self) -> Any: ...

    def reqExecutions(self, request_id: int, execution_filter: Any) -> Any: ...


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

    def openOrderEnd(self) -> None:
        self._transport._on_open_order_end()

    def position(self, account: str, contract: Any, position: Any, average_cost: Any) -> None:
        self._transport._on_position(account, contract, position, average_cost)

    def positionEnd(self) -> None:
        self._transport._on_position_end()

    def execDetailsEnd(self, request_id: int) -> None:
        self._transport._on_execution_end(request_id)

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

        def openOrderEnd(self) -> None:
            self._sink.openOrderEnd()

        def position(self, *args: Any) -> None:
            self._sink.position(*args)

        def positionEnd(self) -> None:
            self._sink.positionEnd()

        def execDetailsEnd(self, request_id: int) -> None:
            self._sink.execDetailsEnd(request_id)

        def error(self, *args: Any) -> None:
            self._sink.error(*args)

        def connectionClosed(self) -> None:
            self._sink.connectionClosed()

    return Client()


def _official_contract_factory(order: Any) -> Any:
    from ibapi.contract import Contract

    contract = Contract()
    contract.symbol = str(order.symbol)
    contract.conId = order.con_id
    contract.primaryExchange = order.primary_exchange
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


def _official_execution_filter_factory(account: str, client_id: int) -> Any:
    from ibapi.execution import ExecutionFilter

    execution_filter = ExecutionFilter()
    execution_filter.acctCode = account
    execution_filter.clientId = client_id
    return execution_filter


@dataclass
class _PendingSubmission:
    future: asyncio.Future[BrokerLegReceipt]
    expected_order: Any


@dataclass(frozen=True, slots=True)
class BrokerReconciliationSnapshot:
    open_orders: tuple[OpenOrderEvent, ...]
    positions: tuple[PositionEvent, ...]
    executions: tuple[ExecutionEvent, ...]
    observed_at: datetime


class TWSPaperTransport:
    """Explicit local TWS PAPER order transport with no implicit I/O."""

    _ACCEPTED_STATUSES = frozenset(
        {"PreSubmitted", "Submitted"}
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
        execution_filter_factory: Callable[[str, int], Any] | None = None,
        observation_store: BrokerObservationStore | None = None,
        owner_id: str | None = None,
    ) -> None:
        config.validate()
        if (observation_store is None) != (owner_id is None) or (owner_id is not None and not owner_id.strip()):
            raise TWSConfigurationError("observation store and owner must be configured together")
        self.config = config
        self._client_factory = client_factory or _official_client_factory
        self._contract_factory = contract_factory or _official_contract_factory
        self._order_factory = order_factory or _official_order_factory
        self._execution_filter_factory = execution_filter_factory or _official_execution_filter_factory
        self._sink = _CallbackSink(self)
        self._client: TWSClient | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._run_thread: threading.Thread | None = None
        self._connected = False
        self._execution_blocked = False
        self._handshake_event: asyncio.Event | None = None
        self._accounts_event: asyncio.Event | None = None
        self._open_orders_end: asyncio.Event | None = None
        self._positions_end: asyncio.Event | None = None
        self._executions_end: dict[int, asyncio.Event] = {}
        self._next_request_id = 1_000_000 + config.client_id * 1_000
        self._managed_accounts: frozenset[str] = frozenset()
        self._next_order_id: int | None = None
        self._reserved_order_ids: set[int] = set()
        self._submitted_order_ids: set[int] = set()
        self._submitted_order_refs: set[str] = set()
        self._pending: dict[int, _PendingSubmission] = {}
        self._expected_orders: dict[int, Any] = {}
        self._events: list[BrokerEvent] = []
        self._state_lock = threading.RLock()
        self._observation_session = str(uuid4())
        self._persisted_observations = 0
        self._observation_lock = asyncio.Lock()
        self._observation_owner: str | None = None
        self._observation_store = observation_store
        self._configured_owner = owner_id
        self._writer_task: asyncio.Task[None] | None = None
        self._writer_stop = asyncio.Event()
        self._session_started = False
        self._session_closed = False
        self._disconnecting = False
        self._snapshot_lock = asyncio.Lock()
        self._snapshot_poisoned = False

    async def _flush_observations(self) -> None:
        if self._observation_store is None or self._configured_owner is None:
            raise TWSSubmissionError("durable observation storage is required for submission")
        await self.persist_observations(self._observation_store, owner_id=self._configured_owner)

    async def _write_observations(self) -> None:
        try:
            while not self._writer_stop.is_set():
                await self._flush_observations()
                try:
                    await asyncio.wait_for(self._writer_stop.wait(), timeout=0.1)
                except asyncio.TimeoutError:
                    pass
            await self._flush_observations()
        except BaseException:
            self._block_execution()
            raise

    async def _start_observation_writer(self) -> None:
        if self._writer_task is not None:
            raise TWSSubmissionError("observation writer cannot be restarted")
        await self._flush_observations()
        self._writer_task = asyncio.create_task(self._write_observations())

    async def persist_observations(self, store: BrokerObservationStore, *, owner_id: str) -> int:
        """Persist buffered callbacks in order; failed writes never advance the cursor."""
        if not owner_id.strip():
            raise ValueError("observation owner is required")
        async with self._observation_lock:
            if self._observation_owner is not None and self._observation_owner != owner_id:
                self._block_execution()
                raise ValueError("observation session owner cannot change")
            self._observation_owner = owner_id
            observations = self.events
            try:
                for sequence in range(self._persisted_observations, len(observations)):
                    await store.append(
                        session_id=self._observation_session, sequence=sequence,
                        owner_id=owner_id, account=self.config.account,
                        client_id=self.config.client_id, event=observations[sequence],
                    )
                    self._persisted_observations = sequence + 1
            except BaseException:
                self._block_execution()
                raise
            return self._persisted_observations

    async def request_reconciliation_snapshot(self) -> BrokerReconciliationSnapshot:
        """Request complete read-only broker snapshots; never clear a fence."""
        async with self._snapshot_lock:
            return await self._request_reconciliation_snapshot()

    async def _request_reconciliation_snapshot(self) -> BrokerReconciliationSnapshot:
        if self._snapshot_poisoned:
            raise TWSSubmissionError(
                "reconciliation snapshot channel is incomplete; a fresh connection is required"
            )
        self._require_observation_ready()
        client = self._client
        if client is None:
            raise TWSHandshakeError("TWS client is disconnected")
        start = len(self.events)
        self._open_orders_end = asyncio.Event()
        self._positions_end = asyncio.Event()
        with self._state_lock:
            request_id = self._next_request_id
            self._next_request_id += 1
        executions_end = asyncio.Event()
        self._executions_end[request_id] = executions_end
        try:
            client.reqAllOpenOrders()
            client.reqPositions()
            client.reqExecutions(
                request_id,
                self._execution_filter_factory(self.config.account, self.config.client_id),
            )
            await asyncio.wait_for(
                asyncio.gather(
                    self._open_orders_end.wait(),
                    self._positions_end.wait(),
                    executions_end.wait(),
                ),
                timeout=self.config.handshake_timeout_seconds,
            )
        except BaseException as exc:
            self._snapshot_poisoned = True
            self._block_execution()
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise TWSSubmissionError("broker reconciliation snapshot failed") from exc
        finally:
            self._executions_end.pop(request_id, None)
            try:
                client.cancelPositions()
            except Exception:
                self._snapshot_poisoned = True
                self._block_execution()
        end = len(self.events)
        observations = self.events[start:end]
        await self._flush_observations()
        open_orders = tuple(event for event in observations if isinstance(event, OpenOrderEvent))
        positions = tuple(event for event in observations if isinstance(event, PositionEvent))
        executions = tuple(
            event for event in observations
            if isinstance(event, ExecutionEvent) and event.request_id == request_id
        )
        if any(event.account != self.config.account for event in (*open_orders, *positions, *executions)):
            self._block_execution()
            raise TWSSubmissionError("broker reconciliation returned an unexpected account")
        return BrokerReconciliationSnapshot(
            open_orders=open_orders,
            positions=positions,
            executions=executions,
            observed_at=datetime.now(timezone.utc),
        )

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
        if self._execution_blocked:
            raise TWSHandshakeError("transport requires independent reconciliation before replacement")
        if self._connected:
            return
        self._loop = asyncio.get_running_loop()
        self._handshake_event = asyncio.Event()
        self._accounts_event = asyncio.Event()
        try:
            if self._observation_store is not None and self._configured_owner is not None:
                await self._observation_store.begin_session(
                    session_id=self._observation_session,
                    owner_id=self._configured_owner,
                    account=self.config.account,
                    client_id=self.config.client_id,
                )
                self._session_started = True
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
            if self._observation_store is not None:
                await self._start_observation_writer()
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
        if self._session_closed and self._client is None:
            self._block_execution()
            return
        was_blocked = self._execution_blocked
        self._disconnecting = True
        self._connected = False
        client, self._client = self._client, None
        client_error: BaseException | None = None
        if client is not None:
            try:
                client.disconnect()
            except BaseException as exc:
                client_error = exc
                self._block_execution()
        thread = self._run_thread
        thread_quiesced = True
        if thread is not None and thread is not threading.current_thread():
            await asyncio.to_thread(thread.join, min(self.config.handshake_timeout_seconds, 2.0))
            thread_quiesced = not thread.is_alive()
            if not thread_quiesced:
                self._block_execution()
        self._next_order_id = None
        self._reserved_order_ids.clear()
        self._run_thread = None if thread_quiesced else thread
        self._fail_pending(TWSPaperTransportError("TWS PAPER transport disconnected"))
        self._writer_stop.set()
        writer_error: BaseException | None = None
        if self._writer_task is not None:
            try:
                await self._writer_task
            except BaseException as exc:
                writer_error = exc
                self._block_execution()
        elif self._session_started and self._observation_store is not None:
            try:
                await self._flush_observations()
            except BaseException as exc:
                writer_error = exc
                self._block_execution()
        requires_reconciliation = (
            bool(self._expected_orders)
            or was_blocked
            or self._execution_blocked
            or client_error is not None
            or writer_error is not None
            or not thread_quiesced
        )
        if (
            self._session_started
            and not self._session_closed
            and self._observation_store is not None
        ):
            try:
                await self._observation_store.close_session(
                    session_id=self._observation_session,
                    owner_id=self._configured_owner,
                    account=self.config.account,
                    client_id=self.config.client_id,
                    requires_reconciliation=requires_reconciliation,
                )
                self._session_closed = True
            except BaseException as exc:
                self._block_execution()
                writer_error = writer_error or exc
        self._block_execution()
        self._disconnecting = False
        if client_error is not None or writer_error is not None or not thread_quiesced:
            raise TWSPaperTransportError(
                "TWS shutdown was not clean; reconciliation is required"
            ) from (writer_error or client_error)

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
        await self._flush_observations()
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
        pending = _PendingSubmission(loop.create_future(), order)
        self._pending[order_id] = pending
        self._expected_orders[order_id] = order
        try:
            contract = self._contract_factory(order)
            broker_order = self._order_factory(order)
            broker_order.account = self.config.account
            client = self._client
            if client is None:
                raise TWSHandshakeError("TWS client is disconnected")
            with self._state_lock:
                if self._execution_blocked:
                    raise TWSSubmissionError("broker uncertainty blocks new orders")
                client.placeOrder(order_id, contract, broker_order)
            receipt = await asyncio.wait_for(
                pending.future, timeout=self.config.acknowledgement_timeout_seconds
            )
            await self._flush_observations()
            if self._execution_blocked:
                raise TWSSubmissionError("broker uncertainty blocks submission acknowledgement")
            return receipt
        except asyncio.TimeoutError as exc:
            self._block_execution()
            raise TWSSubmissionError(
                "broker acknowledgement timed out; order state requires reconciliation"
            ) from exc
        except TWSPaperTransportError:
            self._block_execution()
            raise
        except asyncio.CancelledError:
            self._block_execution()
            raise
        except Exception as exc:
            self._block_execution()
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
        if self._execution_blocked:
            raise TWSSubmissionError("broker uncertainty requires reconciliation; new orders blocked")
        self._require_observation_ready()

    def _require_observation_ready(self) -> None:
        if not self._connected or self._client is None:
            raise TWSHandshakeError("connect() must complete before order transport use")
        if self._next_order_id is None:
            raise TWSHandshakeError("TWS handshake did not provide an order ID")
        if self._writer_task is None or self._writer_task.done():
            raise TWSSubmissionError("active durable observation writer is required")

    def _validate_identity(self, *, account: str, client_id: int) -> None:
        if account != self.config.account or client_id != self.config.client_id:
            raise TWSSubmissionError("account or client identity does not match TWS PAPER transport")

    @classmethod
    def _require_leg(cls, order: Any, leg_type: str) -> None:
        if getattr(order, "leg_type", None) != leg_type:
            raise TWSSubmissionError(f"explicit {leg_type} submission received a different leg")

    @classmethod
    def _validate_order(cls, order: Any) -> None:
        con_id = getattr(order, "con_id", None)
        if not isinstance(con_id, int) or isinstance(con_id, bool) or con_id <= 0:
            raise TWSSubmissionError("qualified stock con_id is required")
        primary_exchange = getattr(order, "primary_exchange", None)
        if not isinstance(primary_exchange, str) or not primary_exchange.strip():
            raise TWSSubmissionError("qualified stock primary exchange is required")
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
            con_id=_optional_int(getattr(contract, "conId", None)) or 0,
            symbol=_optional_text(getattr(contract, "symbol", None)) or "",
            security_type=_optional_text(getattr(contract, "secType", None)) or "",
            primary_exchange=_optional_text(getattr(contract, "primaryExchange", None)) or "",
            currency=_optional_text(getattr(contract, "currency", None)) or "",
            side=_optional_text(getattr(order, "action", None)) or "",
            order_type=_optional_text(getattr(order, "orderType", None)) or "",
            quantity=_decimal(getattr(order, "totalQuantity", 0)) or Decimal("0"),
            limit_price=_decimal(getattr(order, "lmtPrice", None)),
            auxiliary_price=_decimal(getattr(order, "auxPrice", None)),
            parent_order_id=_optional_int(getattr(order, "parentId", None)),
            transmit=bool(getattr(order, "transmit", False)),
            oca_group=_optional_text(getattr(order, "ocaGroup", None)),
            account=_optional_text(getattr(order, "account", None)),
            client_id=_optional_int(getattr(order, "clientId", None)),
            order_ref=_optional_text(getattr(order, "orderRef", None)),
            perm_id=_optional_id(getattr(order, "permId", None)),
            tif=_optional_text(getattr(order, "tif", None)),
            outside_rth=(
                bool(getattr(order, "outsideRth"))
                if getattr(order, "outsideRth", None) is not None
                else None
            ),
            oca_type=_optional_int(getattr(order, "ocaType", None)),
            status=status,
        )
        self._append_event(event)
        pending = self._pending.get(event.order_id)
        if status in {"Filled", "PartiallyFilled"}:
            self._block_execution()
        expected = pending.expected_order if pending is not None else self._expected_orders.get(event.order_id)
        if expected is None:
            self._block_execution()
            return
        price = event.limit_price if expected.order_type == "LMT" else event.auxiliary_price
        if (
            event.account != self.config.account
            or event.order_ref != expected.order_ref
            or event.con_id != expected.con_id
            or event.primary_exchange != expected.primary_exchange
            or event.symbol != expected.symbol
            or event.security_type != "STK"
            or event.currency != "USD"
            or event.quantity != expected.quantity
            or event.side != expected.side
            or event.order_type != expected.order_type
            or price != expected.price
            or (event.parent_order_id or 0) != (expected.parent_order_id or 0)
            or event.oca_group != expected.oca_group
            or event.transmit != expected.transmit
            or event.tif != "DAY"
            or event.outside_rth is not False
            or event.client_id != self.config.client_id
            or (expected.oca_group is not None and event.oca_type != 1)
        ):
            self._block_execution()
            if pending is not None:
                self._set_future_exception(
                    pending.future, TWSSubmissionError("broker acknowledgement does not match staged intent")
                )
            return
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
            self._block_execution()
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
        if (
            event.order_id not in self._expected_orders
            or event.client_id != self.config.client_id
            or event.filled > 0
        ):
            self._block_execution()
        if event.status in self._REJECTED_STATUSES:
            self._block_execution()
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
        self._block_execution()
        event = ExecutionEvent(
            request_id=int(request_id),
            execution_id=_optional_text(getattr(execution, "execId", None)) or "",
            order_id=_optional_int(getattr(execution, "orderId", None)) or 0,
            con_id=_optional_int(getattr(contract, "conId", None)) or 0,
            symbol=_optional_text(getattr(contract, "symbol", None)) or "",
            security_type=_optional_text(getattr(contract, "secType", None)) or "",
            currency=_optional_text(getattr(contract, "currency", None)) or "",
            perm_id=_optional_id(getattr(execution, "permId", None)),
            account=_optional_text(getattr(execution, "acctNumber", None)),
            shares=_decimal(getattr(execution, "shares", 0)) or Decimal("0"),
            price=_decimal(getattr(execution, "price", 0)) or Decimal("0"),
            side=_optional_text(getattr(execution, "side", None)),
        )
        self._append_event(event)

    def _on_position(
        self, account: str, contract: Any, position: Any, average_cost: Any,
    ) -> None:
        event = PositionEvent(
            account=str(account),
            con_id=_optional_int(getattr(contract, "conId", None)) or 0,
            symbol=_optional_text(getattr(contract, "symbol", None)) or "",
            security_type=_optional_text(getattr(contract, "secType", None)) or "",
            primary_exchange=_optional_text(getattr(contract, "primaryExchange", None)) or "",
            currency=_optional_text(getattr(contract, "currency", None)) or "",
            quantity=_decimal(position) or Decimal("0"),
            average_cost=_decimal(average_cost) or Decimal("0"),
        )
        self._append_event(event)
        if (
            event.account != self.config.account
            or event.con_id <= 0
            or not event.symbol
            or not event.security_type
            or not event.currency
        ):
            self._block_execution()

    def _on_open_order_end(self) -> None:
        self._signal_event(self._open_orders_end)

    def _on_position_end(self) -> None:
        self._signal_event(self._positions_end)

    def _on_execution_end(self, request_id: int) -> None:
        self._signal_event(self._executions_end.get(int(request_id)))

    def _on_error(self, request_id: int, code: int, message: str) -> None:
        event = BrokerErrorEvent(request_id=int(request_id), code=int(code), message=str(message))
        self._append_event(event)
        if request_id in self._expected_orders or code in {1100, 1101, 1102, 1300, 502, 504}:
            self._block_execution()
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
        if not self._disconnecting:
            self._block_execution()
        self._append_event(ConnectionClosedEvent())
        self._fail_pending(TWSPaperTransportError("TWS PAPER connection closed"))

    def _append_event(self, event: BrokerEvent) -> None:
        with self._state_lock:
            self._events.append(event)

    def _block_execution(self) -> None:
        with self._state_lock:
            self._execution_blocked = True

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

"""Fail-closed IBKR PAPER bracket submission boundary.

This adapter has no connection lifecycle and no authority promotion logic.  A
caller must provide the previously-validated PAPER account, client ID, and an
execution gate for every submission.  Importing this module never opens a TWS
connection; the injected transport performs broker I/O only when ``submit`` is
called.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal, Protocol
from uuid import uuid4

from trading.contracts import TradeIntent
from trading.runtime import PaperRuntimeController


BracketGroupState = Literal[
    "staged", "submitted", "filled", "canceled", "failed", "manual_intervention"
]
BracketLegState = Literal[
    "staged", "submitted", "accepted", "filled", "canceled", "rejected", "unknown"
]
BracketLegType = Literal["parent", "stop", "target"]


class PaperExecutionError(RuntimeError):
    """A proposed PAPER bracket is not safe to submit."""


class ManualInterventionRequired(PaperExecutionError):
    """Broker or persistence state is uncertain; new entries must be fenced."""

    def __init__(self, group_id: str, reason: str) -> None:
        self.group_id = group_id
        self.reason = reason
        super().__init__(f"manual intervention required for {group_id}: {reason}")


@dataclass(frozen=True, slots=True)
class PaperSubmissionContext:
    """Durable ownership references required before an order can be submitted."""

    owner_id: str
    run_id: str
    candidate_id: str
    risk_reservation_id: str
    con_id: int
    currency: Literal["USD"]
    security_type: Literal["STK"]
    primary_exchange: str
    rth: bool
    min_tick: Decimal

    def validate(self) -> None:
        for field, value in (
            ("owner_id", self.owner_id),
            ("run_id", self.run_id),
            ("candidate_id", self.candidate_id),
            ("risk_reservation_id", self.risk_reservation_id),
        ):
            if not isinstance(value, str) or not value.strip():
                raise PaperExecutionError(f"{field} is required")
        if self.con_id <= 0:
            raise PaperExecutionError("qualified stock con_id is required")
        if self.currency != "USD" or self.security_type != "STK":
            raise PaperExecutionError("PAPER brackets require a qualified USD common stock")
        if not self.primary_exchange.strip():
            raise PaperExecutionError("qualified stock primary exchange is required")
        if not self.rth:
            raise PaperExecutionError("PAPER brackets are restricted to regular trading hours")
        if not self.min_tick.is_finite() or self.min_tick <= 0:
            raise PaperExecutionError("qualified stock min_tick must be finite and positive")


@dataclass(frozen=True, slots=True)
class StagedBrokerOrder:
    """One immutable IBKR order in the frozen parent/target/stop sequence."""

    order_id: int
    order_ref: str
    leg_type: BracketLegType
    symbol: str
    side: Literal["BUY", "SELL"]
    order_type: Literal["LMT", "STP"]
    quantity: int
    price: Decimal
    transmit: bool
    parent_order_id: int | None
    oca_group: str | None


@dataclass(frozen=True, slots=True)
class BrokerLegReceipt:
    """Non-sensitive acknowledgement for one broker order submission."""

    order_id: int
    accepted: bool
    state: BracketLegState
    perm_id: str | None = None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class BracketGroupRecord:
    """Persisted intent for one paper-only, long-stock bracket."""

    id: str
    context: PaperSubmissionContext
    execution_key: str
    account: str
    client_id: int
    symbol: str
    quantity: int
    entry_price: Decimal
    stop_price: Decimal
    target_price: Decimal
    oca_group: str
    state: BracketGroupState
    created_at: datetime
    updated_at: datetime
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class BracketLegRecord:
    """Persisted broker-leg intent, recorded before its corresponding submit."""

    id: str
    group_id: str
    leg_type: BracketLegType
    broker_order_id: int
    order_ref: str
    oca_group: str | None
    quantity: int
    price: Decimal
    transmit: bool
    state: BracketLegState
    perm_id: str | None = None


@dataclass(frozen=True, slots=True)
class PaperBracketResult:
    group: BracketGroupRecord
    legs: tuple[BracketLegRecord, ...]


class PaperBrokerTransport(Protocol):
    """The narrow I/O port implemented by the one official-TWS connection actor."""

    @property
    def identity(self) -> "PaperTransportIdentity":
        """Report the fixed local TWS PAPER identity used by this transport."""

    def reserve_order_ids(self, count: int) -> tuple[int, ...]:
        """Reserve contiguous order IDs after a healthy TWS handshake."""

    async def submit_order(
        self, order: StagedBrokerOrder, *, account: str, client_id: int
    ) -> BrokerLegReceipt:
        """Submit exactly one staged IBKR order without automatic retry."""

    async def cancel_order(self, order_id: int, *, account: str, client_id: int) -> None:
        """Cancel a known staged order as failure containment only."""


class BracketLedger(Protocol):
    async def validate_reservation(
        self, *, intent: TradeIntent, context: PaperSubmissionContext
    ) -> None:
        """Fail unless the active reservation binds this exact approved intent."""

    async def stage(
        self, group: BracketGroupRecord, legs: tuple[BracketLegRecord, ...]
    ) -> None:
        """Durably save group and every leg before any broker call."""

    async def update_group(
        self,
        group_id: str,
        *,
        owner_id: str,
        state: BracketGroupState,
        reason: str | None = None,
    ) -> None:
        """Persist a group state transition without sending a broker command."""

    async def update_leg(
        self,
        leg_id: str,
        *,
        owner_id: str,
        state: BracketLegState,
        perm_id: str | None = None,
    ) -> None:
        """Persist a broker acknowledgement without modifying an order."""

    async def execution_key_exists(self, execution_key: str) -> bool:
        """Return whether a durable bracket already consumed this risk approval."""

    async def fence(self, *, reason: str) -> None:
        """Persist a global execution fence before the caller permits new entries."""


@dataclass(frozen=True, slots=True)
class PaperTransportIdentity:
    host: str
    port: int
    account: str
    client_id: int

    def validate(self) -> None:
        if self.host != "127.0.0.1" or self.port != 7497:
            raise PaperExecutionError("executor requires the local TWS PAPER endpoint")
        if not self.account.strip() or self.client_id <= 0:
            raise PaperExecutionError("executor requires a configured PAPER account and client ID")


Clock = Callable[[], datetime]


class IBKRTwsPaperExecutor:
    """Submit a pre-approved long bracket to a caller-owned TWS PAPER transport.

    The executor is deliberately scheduler-free.  It neither decides when to
    enter nor changes, retries, repairs, replaces, or force-closes any exit.
    """

    def __init__(
        self,
        *,
        transport: PaperBrokerTransport,
        ledger: BracketLedger,
        runtime: PaperRuntimeController,
        clock: Clock | None = None,
    ) -> None:
        transport.identity.validate()
        identity = runtime.identity
        if (
            transport.identity.account != identity.paper_account
            or transport.identity.client_id != identity.client_id
        ):
            raise PaperExecutionError("TWS transport identity does not match the receipt-bound runtime")
        self._transport = transport
        self._ledger = ledger
        self._runtime = runtime
        self._paper_account = identity.paper_account
        self._client_id = identity.client_id
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    async def submit(
        self, *, intent: TradeIntent, context: PaperSubmissionContext
    ) -> PaperBracketResult:
        """Persist, submit once in IBKR sequence, and fence any uncertain result."""
        observed_at = self._now()
        context.validate()
        self._validate_intent(intent, observed_at=observed_at)
        if await self._ledger.execution_key_exists(intent.execution_key):
            raise PaperExecutionError("execution key has already been submitted")

        await self._runtime.require_paper_submission(intent=intent, context=context)
        await self._ledger.validate_reservation(intent=intent, context=context)
        group, legs = self._build_records(intent=intent, context=context, observed_at=observed_at)
        acknowledged: list[BracketLegRecord] = []
        try:
            await self._ledger.stage(group, legs)
        except Exception as exc:
            raise ManualInterventionRequired(
                group.id, "staged bracket was not durably persisted"
            ) from exc

        try:
            for leg in legs:
                receipt = await self._transport.submit_order(
                    self._to_broker_order(group=group, legs=legs, leg=leg),
                    account=self._paper_account,
                    client_id=self._client_id,
                )
                if receipt.order_id != leg.broker_order_id or not receipt.accepted:
                    raise PaperExecutionError(receipt.reason or "broker did not accept staged leg")
                accepted_leg = replace(leg, state=receipt.state, perm_id=receipt.perm_id)
                if accepted_leg.state != "accepted":
                    raise PaperExecutionError("broker returned an unsafe staged-leg state")
                await self._ledger.update_leg(
                    accepted_leg.id,
                    owner_id=context.owner_id,
                    state=accepted_leg.state,
                    perm_id=accepted_leg.perm_id,
                )
                acknowledged.append(accepted_leg)
        except BaseException as exc:
            await self._cancel_known_staged(legs, owner_id=context.owner_id)
            await self._fence(
                group.id,
                owner_id=context.owner_id,
                reason=f"broker submission uncertain: {type(exc).__name__}",
            )
            raise ManualInterventionRequired(
                group.id, "broker submission failed; no retry was attempted"
            ) from exc

        try:
            await self._ledger.update_group(
                group.id, owner_id=context.owner_id, state="submitted"
            )
        except Exception as exc:
            await self._fence(
                group.id,
                owner_id=context.owner_id,
                reason="broker accepted bracket but durable state update failed",
            )
            raise ManualInterventionRequired(
                group.id, "broker accepted bracket but durable state is uncertain"
            ) from exc

        return PaperBracketResult(
            group=replace(group, state="submitted", updated_at=self._now()),
            legs=tuple(acknowledged),
        )

    def _build_records(
        self,
        *,
        intent: TradeIntent,
        context: PaperSubmissionContext,
        observed_at: datetime,
    ) -> tuple[BracketGroupRecord, tuple[BracketLegRecord, ...]]:
        order_ids = self._transport.reserve_order_ids(3)
        if len(order_ids) != 3 or len(set(order_ids)) != 3 or any(order_id <= 0 for order_id in order_ids):
            raise PaperExecutionError("TWS actor failed to reserve three unique positive order IDs")
        group_id = str(uuid4())
        oca_group = f"autotrader-{group_id}"
        group = BracketGroupRecord(
            id=group_id,
            context=context,
            execution_key=intent.execution_key,
            account=self._paper_account,
            client_id=self._client_id,
            symbol=intent.symbol,
            quantity=intent.quantity,
            entry_price=intent.entry_limit,
            stop_price=intent.stop_price,
            target_price=intent.target_price,
            oca_group=oca_group,
            state="staged",
            created_at=observed_at,
            updated_at=observed_at,
        )
        parent_id, target_id, stop_id = order_ids
        legs = (
            BracketLegRecord(
                id=str(uuid4()), group_id=group_id, leg_type="parent", broker_order_id=parent_id,
                order_ref=f"{intent.execution_key}:parent", oca_group=None, quantity=intent.quantity,
                price=intent.entry_limit, transmit=False, state="staged",
            ),
            BracketLegRecord(
                id=str(uuid4()), group_id=group_id, leg_type="target", broker_order_id=target_id,
                order_ref=f"{intent.execution_key}:target", oca_group=oca_group, quantity=intent.quantity,
                price=intent.target_price, transmit=False, state="staged",
            ),
            BracketLegRecord(
                id=str(uuid4()), group_id=group_id, leg_type="stop", broker_order_id=stop_id,
                order_ref=f"{intent.execution_key}:stop", oca_group=oca_group, quantity=intent.quantity,
                price=intent.stop_price, transmit=True, state="staged",
            ),
        )
        self._validate_staged_records(group=group, legs=legs, min_tick=context.min_tick)
        return group, legs

    @staticmethod
    def _to_broker_order(
        *,
        group: BracketGroupRecord,
        legs: tuple[BracketLegRecord, ...],
        leg: BracketLegRecord,
    ) -> StagedBrokerOrder:
        parent_order_id = next(
            item.broker_order_id for item in legs if item.leg_type == "parent"
        )
        return StagedBrokerOrder(
            order_id=leg.broker_order_id,
            order_ref=leg.order_ref,
            leg_type=leg.leg_type,
            symbol=group.symbol,
            side="BUY" if leg.leg_type == "parent" else "SELL",
            order_type="LMT" if leg.leg_type in {"parent", "target"} else "STP",
            quantity=leg.quantity,
            price=leg.price,
            transmit=leg.transmit,
            parent_order_id=None if leg.leg_type == "parent" else parent_order_id,
            oca_group=leg.oca_group,
        )

    async def _cancel_known_staged(
        self, legs: Sequence[BracketLegRecord], *, owner_id: str
    ) -> None:
        for leg in reversed(legs):
            try:
                await self._transport.cancel_order(
                    leg.broker_order_id, account=self._paper_account, client_id=self._client_id
                )
                await self._ledger.update_leg(leg.id, owner_id=owner_id, state="canceled")
            except BaseException:
                continue

    async def _fence(self, group_id: str, *, owner_id: str, reason: str) -> None:
        self._runtime.block(reason=reason)
        try:
            await self._ledger.update_group(
                group_id,
                owner_id=owner_id,
                state="manual_intervention",
                reason=reason,
            )
            await self._ledger.fence(reason=reason)
        except Exception as exc:
            raise ManualInterventionRequired(
                group_id, "execution fence could not be persisted"
            ) from exc

    @staticmethod
    def _validate_intent(intent: TradeIntent, *, observed_at: datetime) -> None:
        if intent.side != "BUY":
            raise PaperExecutionError("only long BUY brackets are supported")
        if intent.expires_at <= observed_at:
            raise PaperExecutionError("risk approval has expired")
        for field, value in (
            ("entry_limit", intent.entry_limit),
            ("stop_price", intent.stop_price),
            ("target_price", intent.target_price),
        ):
            if not value.is_finite() or value <= 0:
                raise PaperExecutionError(f"{field} must be finite and positive")
        if not intent.stop_price < intent.entry_limit < intent.target_price:
            raise PaperExecutionError("bracket prices must satisfy stop < entry < target")

    @staticmethod
    def _validate_staged_records(
        *, group: BracketGroupRecord, legs: tuple[BracketLegRecord, ...], min_tick: Decimal
    ) -> None:
        if tuple(leg.leg_type for leg in legs) != ("parent", "target", "stop"):
            raise PaperExecutionError("bracket must submit parent, target, then protective stop")
        if tuple(leg.transmit for leg in legs) != (False, False, True):
            raise PaperExecutionError("only the final protective stop may transmit")
        if len({leg.broker_order_id for leg in legs}) != 3:
            raise PaperExecutionError("bracket order IDs must be unique")
        if legs[1].oca_group != group.oca_group or legs[2].oca_group != group.oca_group:
            raise PaperExecutionError("protective children must share the bracket OCA group")
        if legs[0].oca_group is not None:
            raise PaperExecutionError("parent order cannot be assigned to the exit OCA group")
        for value in (group.entry_price, group.stop_price, group.target_price):
            if value % min_tick != 0:
                raise PaperExecutionError("bracket prices must align to the qualified contract tick")

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("clock must return a timezone-aware datetime")
        return value.astimezone(timezone.utc)

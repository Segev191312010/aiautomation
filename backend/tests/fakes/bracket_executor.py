"""Staged, broker-native bracket execution over the fake PAPER boundary.

The executor owns validation and durable intent ordering only. Once the
broker accepts the staged bracket, this module never calculates, modifies,
cancels, retries, or repairs an exit.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from decimal import Decimal
import inspect
import re
from typing import Literal, Protocol
from uuid import uuid4

from trading.contracts import ReconciliationReport, TradeIntent

from .fake_broker import FakeBracketSubmission, FakeOrder, FakePaperBroker


OPTIONAL_TARGET_CONTRACT_MISMATCH = (
    "optional bracket targets are not representable by the frozen TradeIntent "
    "contract; target_price is currently required"
)

BracketState = Literal["staged", "submitted", "failed", "manual_intervention", "filled", "canceled"]
LegState = Literal["staged", "submitted", "accepted", "filled", "canceled", "rejected", "unknown"]


class BracketValidationError(ValueError):
    """A bracket violates the narrow long-stock limit contract."""


class ManualInterventionRequired(RuntimeError):
    """Execution state is not safely knowable and must not be auto-repaired."""

    def __init__(self, group_id: str, reason: str) -> None:
        self.group_id = group_id
        self.reason = reason
        super().__init__(f"manual intervention required for {group_id}: {reason}")


@dataclass(frozen=True, slots=True)
class BracketGroupRecord:
    id: str
    owner_id: str
    run_id: str
    candidate_id: str
    reservation_id: str
    execution_key: str
    account: str
    client_id: int
    symbol: str
    quantity: int
    entry_price: Decimal
    stop_price: Decimal
    target_price: Decimal | None
    oca_group: str
    state: BracketState
    created_at: datetime
    updated_at: datetime
    reason: str | None = None

    def __post_init__(self) -> None:
        for value in (self.created_at, self.updated_at):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("bracket timestamps must be timezone-aware")


@dataclass(frozen=True, slots=True)
class BracketLegRecord:
    id: str
    group_id: str
    leg_type: Literal["parent", "stop", "target"]
    broker_order_id: int
    order_ref: str
    oca_group: str
    quantity: int
    price: Decimal
    transmit: bool
    state: LegState


@dataclass(frozen=True, slots=True)
class BracketExecutionResult:
    group: BracketGroupRecord
    legs: tuple[BracketLegRecord, ...]
    broker: FakeBracketSubmission


class BracketPersistence(Protocol):
    async def persist(
        self, group: BracketGroupRecord, legs: tuple[BracketLegRecord, ...]
    ) -> None:
        """Persist the staged group and all legs before broker submission."""

    async def mark_state(
        self, group_id: str, state: BracketState, reason: str | None = None
    ) -> None:
        """Record a state transition without issuing a broker command."""


PersistCallback = Callable[
    [BracketGroupRecord, tuple[BracketLegRecord, ...]], Awaitable[None] | None
]


@dataclass
class InMemoryBracketPersistence:
    """Deterministic persistence double for unit and fault-harness tests."""

    groups: dict[str, BracketGroupRecord] = field(default_factory=dict)
    legs: dict[str, tuple[BracketLegRecord, ...]] = field(default_factory=dict)
    events: list[tuple[str, str, str | None]] = field(default_factory=list)
    fail_persist: bool = False
    fail_state: bool = False

    async def persist(
        self, group: BracketGroupRecord, legs: tuple[BracketLegRecord, ...]
    ) -> None:
        if self.fail_persist:
            raise OSError("persistence unavailable")
        if group.execution_key in {item.execution_key for item in self.groups.values()}:
            raise ValueError("execution key has already been persisted")
        self.groups[group.id] = group
        self.legs[group.id] = legs
        self.events.append(("persist", group.id, None))

    async def mark_state(
        self, group_id: str, state: BracketState, reason: str | None = None
    ) -> None:
        if self.fail_state:
            raise OSError("state persistence unavailable")
        group = self.groups.get(group_id)
        if group is not None:
            now = datetime.now(timezone.utc)
            self.groups[group_id] = replace(group, state=state, updated_at=now, reason=reason)
        self.events.append(("state", group_id, state))


class StagedBracketExecutor:
    """Submit one explicitly approved long-stock bracket, exactly once."""

    def __init__(
        self,
        *,
        broker: FakePaperBroker,
        persistence: BracketPersistence | None = None,
        persist_callback: PersistCallback | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.broker = broker
        self.persistence = persistence or InMemoryBracketPersistence()
        self.persist_callback = persist_callback
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._groups: dict[str, BracketGroupRecord] = {}
        self._execution_keys: set[str] = set()

    async def submit(
        self,
        intent: TradeIntent,
        *,
        owner_id: str,
        run_id: str,
        candidate_id: str,
        reservation_id: str,
        account: str | None = None,
        client_id: int | None = None,
        include_target: bool = True,
    ) -> BracketExecutionResult:
        """Persist then submit parent/target/stop; never retry a failure."""
        observed = self._now()
        if intent.execution_key in self._execution_keys:
            raise BracketValidationError("execution key has already been submitted")
        if not include_target:
            raise BracketValidationError(OPTIONAL_TARGET_CONTRACT_MISMATCH)
        self._validate_intent(
            intent,
            observed=observed,
            owner_id=owner_id,
            run_id=run_id,
            candidate_id=candidate_id,
            reservation_id=reservation_id,
            include_target=include_target,
        )
        resolved_account = self.broker.config.account if account is None else account
        resolved_client_id = self.broker.config.client_id if client_id is None else client_id
        if resolved_account != self.broker.config.account:
            raise BracketValidationError("account does not match the PAPER broker identity")
        if resolved_client_id != self.broker.config.client_id:
            raise BracketValidationError("client_id does not match the PAPER broker identity")

        group_id = str(uuid4())
        oca_group = f"OCA-{group_id}"
        order_ids = self.broker.reserve_order_ids(3 if include_target else 2)
        parent_id = order_ids[0]
        target_id = order_ids[1] if include_target else None
        stop_id = order_ids[-1]
        group = BracketGroupRecord(
            id=group_id,
            owner_id=owner_id,
            run_id=run_id,
            candidate_id=candidate_id,
            reservation_id=reservation_id,
            execution_key=intent.execution_key,
            account=resolved_account,
            client_id=resolved_client_id,
            symbol=intent.symbol,
            quantity=intent.quantity,
            entry_price=intent.entry_limit,
            stop_price=intent.stop_price,
            target_price=intent.target_price if include_target else None,
            oca_group=oca_group,
            state="staged",
            created_at=observed,
            updated_at=observed,
        )
        legs = self._build_legs(
            group=group, parent_id=parent_id, target_id=target_id, stop_id=stop_id
        )
        orders = self._build_orders(group=group, legs=legs)

        try:
            if self.persist_callback is not None:
                await _maybe_await(self.persist_callback(group, legs))
            await self.persistence.persist(group, legs)
        except Exception as exc:
            await self._mark_state_best_effort(
                group,
                state="manual_intervention",
                reason=f"staged bracket persistence failed: {type(exc).__name__}",
            )
            raise ManualInterventionRequired(
                group.id, "staged bracket was not durably persisted"
            ) from exc

        self._groups[group.id] = group
        self._execution_keys.add(intent.execution_key)
        try:
            broker_result = await self.broker.submit_staged_bracket(
                orders, account=resolved_account, client_id=resolved_client_id
            )
        except Exception as exc:
            await self.broker.cancel_staged(order_ids)
            await self._mark_state_best_effort(
                group,
                state="manual_intervention",
                reason=f"broker submission failed: {type(exc).__name__}",
            )
            raise ManualInterventionRequired(
                group.id, "broker submission failed; no retry was attempted"
            ) from exc

        if broker_result.state != "accepted":
            if broker_result.state == "rejected":
                await self.broker.cancel_staged(order_ids)
            await self._mark_state_best_effort(
                group,
                state="manual_intervention",
                reason=broker_result.reason or "broker returned an unsafe bracket state",
            )
            raise ManualInterventionRequired(
                group.id, broker_result.reason or "broker returned an unsafe bracket state"
            )

        submitted_group = replace(group, state="submitted", updated_at=self._now())
        self._groups[group.id] = submitted_group
        try:
            await self._mark_state(submitted_group, state="submitted")
        except Exception as exc:
            await self._mark_state_best_effort(
                submitted_group,
                state="manual_intervention",
                reason="broker accepted bracket but state update failed",
            )
            raise ManualInterventionRequired(
                group.id, "broker accepted bracket but durable state is uncertain"
            ) from exc
        return BracketExecutionResult(
            group=submitted_group,
            legs=tuple(replace(leg, state="accepted") for leg in legs),
            broker=broker_result,
        )

    async def reconcile_after_restart(self) -> ReconciliationReport:
        """Fence active groups on unknown state; never repair broker orders."""
        report = await self.broker.reconcile()
        if report.status == "blocked":
            for group in self._known_groups():
                if group.state == "submitted":
                    await self._mark_state_best_effort(
                        group,
                        state="manual_intervention",
                        reason="restart reconciliation reported unknown broker state",
                    )
        return report

    @property
    def groups(self) -> tuple[BracketGroupRecord, ...]:
        return tuple(self._groups.values())

    def _known_groups(self) -> tuple[BracketGroupRecord, ...]:
        """Recover submitted group observations from a restart-safe test store."""
        persisted = getattr(self.persistence, "groups", {})
        for group in persisted.values():
            if isinstance(group, BracketGroupRecord):
                self._groups.setdefault(group.id, group)
        return tuple(self._groups.values())

    def _build_legs(
        self, *, group: BracketGroupRecord, parent_id: int, target_id: int | None, stop_id: int
    ) -> tuple[BracketLegRecord, ...]:
        parent = BracketLegRecord(
            id=str(uuid4()), group_id=group.id, leg_type="parent",
            broker_order_id=parent_id, order_ref=f"{group.execution_key}:parent",
            oca_group="", quantity=group.quantity, price=group.entry_price,
            transmit=False, state="staged",
        )
        legs: list[BracketLegRecord] = [parent]
        if target_id is not None:
            legs.append(
                BracketLegRecord(
                    id=str(uuid4()), group_id=group.id, leg_type="target",
                    broker_order_id=target_id, order_ref=f"{group.execution_key}:target",
                    oca_group=group.oca_group, quantity=group.quantity,
                    price=group.target_price or group.entry_price,
                    transmit=False, state="staged",
                )
            )
        legs.append(
            BracketLegRecord(
                id=str(uuid4()), group_id=group.id, leg_type="stop",
                broker_order_id=stop_id, order_ref=f"{group.execution_key}:stop",
                oca_group=group.oca_group, quantity=group.quantity,
                price=group.stop_price, transmit=True, state="staged",
            )
        )
        return tuple(legs)

    @staticmethod
    def _build_orders(*, group: BracketGroupRecord, legs: tuple[BracketLegRecord, ...]) -> tuple[FakeOrder, ...]:
        parent_id = legs[0].broker_order_id
        return tuple(
            FakeOrder(
                order_id=leg.broker_order_id, order_ref=leg.order_ref,
                role=leg.leg_type, symbol=group.symbol, security_type="STK",
                side="BUY" if leg.leg_type == "parent" else "SELL",
                quantity=leg.quantity, price=leg.price, transmit=leg.transmit,
                parent_order_id=None if leg.leg_type == "parent" else parent_id,
                oca_group=leg.oca_group, state="staged", order_type="LMT",
            )
            for leg in legs
        )

    def _validate_intent(
        self,
        intent: TradeIntent,
        *,
        observed: datetime,
        owner_id: str,
        run_id: str,
        candidate_id: str,
        reservation_id: str,
        include_target: bool,
    ) -> None:
        for name, value in {
            "owner_id": owner_id, "run_id": run_id,
            "candidate_id": candidate_id, "reservation_id": reservation_id,
        }.items():
            if not isinstance(value, str) or not value.strip():
                raise BracketValidationError(f"{name} is required")
        if intent.side != "BUY":
            raise BracketValidationError("only long BUY brackets are supported")
        if not re.fullmatch(r"[A-Z0-9][A-Z0-9.-]{0,9}", intent.symbol):
            raise BracketValidationError("symbol must be a normalized stock symbol")
        if intent.expires_at <= observed:
            raise BracketValidationError("trade intent has expired")
        values = {
            "entry_limit": intent.entry_limit,
            "stop_price": intent.stop_price,
            "target_price": intent.target_price,
        }
        for name, value in values.items():
            if not value.is_finite() or value <= 0:
                raise BracketValidationError(f"{name} must be finite and positive")
        if not intent.stop_price < intent.entry_limit:
            raise BracketValidationError("stop must be below the entry limit")
        if include_target and not intent.entry_limit < intent.target_price:
            raise BracketValidationError("target must be above the entry limit")

    def _now(self) -> datetime:
        value = self.clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("clock must return a timezone-aware datetime")
        return value.astimezone(timezone.utc)

    async def _mark_state(
        self, group: BracketGroupRecord, *, state: BracketState, reason: str | None = None
    ) -> None:
        if group.id in self._groups:
            self._groups[group.id] = replace(
                self._groups[group.id],
                state=state,
                updated_at=self._now(),
                reason=reason,
            )
        method = getattr(self.persistence, "mark_state", None)
        if method is not None:
            await _maybe_await(method(group.id, state, reason))

    async def _mark_state_best_effort(
        self, group: BracketGroupRecord, *, state: BracketState, reason: str
    ) -> None:
        try:
            await self._mark_state(group, state=state, reason=reason)
        except Exception:
            return


async def _maybe_await(value: Awaitable[None] | None) -> None:
    if inspect.isawaitable(value):
        await value

"""Pure broker-state reconciliation for restart fencing.

The module deliberately has no broker client dependency.  It converts local
broker observations into immutable events and reports inconsistencies without
repairing, retrying, or changing an order.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from hashlib import sha256
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from trading.contracts import ReconciliationReport, require_aware


LegType = Literal["parent", "stop", "target"]
OrderState = Literal[
    "staged",
    "submitted",
    "accepted",
    "partial",
    "filled",
    "canceled",
    "rejected",
    "unknown",
]
FindingCode = Literal[
    "malformed_event",
    "unknown_state",
    "missing_order",
    "duplicate_event",
    "out_of_order",
    "partial_fill_unprotected",
    "oca_inconsistent",
    "unexpected_order",
]


class BrokerEventNormalizationError(ValueError):
    """Raised when a broker observation cannot become a typed event."""


class BrokerEvent(BaseModel):
    """Immutable normalized observation from a broker boundary."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: str = Field(min_length=1, max_length=256)
    group_id: str = Field(min_length=1, max_length=256)
    order_id: int = Field(gt=0)
    leg_type: LegType | None = None
    state: OrderState
    raw_state: str | None = Field(default=None, max_length=128)
    quantity: int = Field(gt=0)
    filled_quantity: int = Field(default=0, ge=0)
    oca_group: str | None = Field(default=None, max_length=256)
    event_time: datetime
    sequence: int | None = Field(default=None, ge=0)

    @field_validator("event_id", "group_id")
    @classmethod
    def validate_identifier(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("broker event identifiers must be non-empty")
        return value

    @field_validator("event_time")
    @classmethod
    def validate_event_time(cls, value: datetime) -> datetime:
        return require_aware(value)

    @field_validator("oca_group")
    @classmethod
    def validate_oca_group(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            return None
        return value

    @field_validator("filled_quantity")
    @classmethod
    def validate_filled_quantity(cls, value: int, info) -> int:
        quantity = info.data.get("quantity")
        if quantity is not None and value > quantity:
            raise ValueError("filled quantity cannot exceed order quantity")
        return value

    def fingerprint(self) -> str:
        payload = self.model_dump(mode="json")
        return sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()


class BracketExpectation(BaseModel):
    """The persisted order identity against which broker observations compare."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    group_id: str = Field(min_length=1, max_length=256)
    parent_order_id: int = Field(gt=0)
    stop_order_id: int = Field(gt=0)
    target_order_id: int | None = Field(default=None, gt=0)
    quantity: int = Field(gt=0)
    oca_group: str = Field(min_length=1, max_length=256)

    @field_validator("group_id", "oca_group")
    @classmethod
    def validate_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("bracket identifiers must be non-empty")
        return value

    @field_validator("stop_order_id", "target_order_id")
    @classmethod
    def validate_distinct_order_id(cls, value: int | None, info) -> int | None:
        if value is not None and value == info.data.get("parent_order_id"):
            raise ValueError("bracket leg order IDs must be distinct")
        return value

    @field_validator("target_order_id")
    @classmethod
    def validate_target_distinct_from_stop(cls, value: int | None, info) -> int | None:
        if value is not None and value == info.data.get("stop_order_id"):
            raise ValueError("bracket child order IDs must be distinct")
        return value

    def expected_legs(self) -> dict[int, LegType]:
        values: dict[int, LegType] = {
            self.parent_order_id: "parent",
            self.stop_order_id: "stop",
        }
        if self.target_order_id is not None:
            values[self.target_order_id] = "target"
        return values


class ReconciliationFinding(BaseModel):
    """A fixed, non-repairable inconsistency found during reconciliation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: FindingCode
    order_id: int | None = Field(default=None, gt=0)
    detail: str = Field(min_length=1, max_length=256)


class ReconciliationAnalysis(BaseModel):
    """Restart-safe result; blocked reports must fence new entries."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    group_id: str
    status: Literal["clear", "blocked"]
    findings: tuple[ReconciliationFinding, ...] = ()
    events: tuple[BrokerEvent, ...] = ()

    @property
    def blocking(self) -> bool:
        return self.status == "blocked"

    @property
    def should_fence(self) -> bool:
        return self.blocking

    @property
    def unknowns(self) -> tuple[str, ...]:
        return tuple(
            finding.detail
            for finding in self.findings
            if finding.code in {"unknown_state", "missing_order", "malformed_event"}
        )

    @property
    def mismatches(self) -> tuple[str, ...]:
        return tuple(
            finding.detail
            for finding in self.findings
            if finding.code
            in {
                "duplicate_event",
                "out_of_order",
                "partial_fill_unprotected",
                "oca_inconsistent",
                "unexpected_order",
            }
        )

    def as_contract_report(
        self, *, paper_account: str, client_id: int, observed_at: datetime | None = None
    ) -> ReconciliationReport:
        """Project the analysis into the shared persistence/report contract."""

        observed = require_aware(observed_at) if observed_at is not None else datetime.now(timezone.utc)
        orders = tuple(
            {
                "order_id": event.order_id,
                "leg_type": event.leg_type,
                "state": event.state,
                "quantity": event.quantity,
                "filled_quantity": event.filled_quantity,
                "oca_group": event.oca_group,
            }
            for event in self.events
        )
        return ReconciliationReport(
            paper_account=paper_account,
            client_id=client_id,
            status=self.status,
            orders=orders,
            unknowns=self.unknowns,
            mismatches=self.mismatches,
            observed_at=observed,
        )


_STATE_ALIASES: dict[str, OrderState] = {
    "staged": "staged",
    "presubmitted": "submitted",
    "submitted": "submitted",
    "open": "accepted",
    "accepted": "accepted",
    "partiallyfilled": "partial",
    "partial": "partial",
    "filled": "filled",
    "cancelled": "canceled",
    "canceled": "canceled",
    "apicancelled": "canceled",
    "rejected": "rejected",
    "inactive": "rejected",
    "unknown": "unknown",
}


def _value(raw: Mapping[str, object], *names: str) -> object | None:
    for name in names:
        if name in raw:
            return raw[name]
    return None


def _required_text(raw: Mapping[str, object], *names: str) -> str:
    value = _value(raw, *names)
    if not isinstance(value, str) or not value.strip():
        raise BrokerEventNormalizationError("broker event is missing required text")
    return value


def _required_int(raw: Mapping[str, object], *names: str) -> int:
    value = _value(raw, *names)
    try:
        if isinstance(value, bool) or value is None:
            raise TypeError
        return int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise BrokerEventNormalizationError("broker event is missing required integer") from exc


def _optional_int(raw: Mapping[str, object], *names: str) -> int | None:
    value = _value(raw, *names)
    if value is None:
        return None
    try:
        if isinstance(value, bool):
            raise TypeError
        return int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise BrokerEventNormalizationError("broker event integer is invalid") from exc


def _event_time(raw: Mapping[str, object]) -> datetime:
    value = _value(raw, "event_time", "observed_at", "timestamp", "time")
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise BrokerEventNormalizationError("broker event timestamp is invalid") from exc
    raise BrokerEventNormalizationError("broker event timestamp is required")


def normalize_broker_event(raw: Mapping[str, object]) -> BrokerEvent:
    """Normalize local broker-shaped fields without importing a broker SDK."""

    if not isinstance(raw, Mapping):
        raise BrokerEventNormalizationError("broker event must be a mapping")
    raw_state_value = _value(raw, "state", "status", "order_status", "orderStatus")
    if not isinstance(raw_state_value, str) or not raw_state_value.strip():
        raise BrokerEventNormalizationError("broker event state is required")
    normalized_key = "".join(character for character in raw_state_value.lower() if character.isalnum())
    state = _STATE_ALIASES.get(normalized_key, "unknown")
    quantity = _required_int(raw, "quantity", "total_quantity", "totalQuantity")
    filled_quantity = _optional_int(raw, "filled_quantity", "filledQuantity", "filled") or 0
    leg_value = _value(raw, "leg_type", "role", "leg")
    leg_type: LegType | None
    if leg_value is None:
        leg_type = None
    elif leg_value in {"parent", "stop", "target"}:
        leg_type = leg_value
    else:
        raise BrokerEventNormalizationError("broker event leg type is invalid")
    try:
        return BrokerEvent(
            event_id=_required_text(raw, "event_id", "eventId", "id"),
            group_id=_required_text(raw, "group_id", "groupId", "bracket_group_id"),
            order_id=_required_int(raw, "order_id", "orderId"),
            leg_type=leg_type,
            state=state,
            raw_state=raw_state_value,
            quantity=quantity,
            filled_quantity=filled_quantity,
            oca_group=_value(raw, "oca_group", "ocaGroup"),
            event_time=_event_time(raw),
            sequence=_optional_int(raw, "sequence", "seq"),
        )
    except (TypeError, ValueError) as exc:
        raise BrokerEventNormalizationError("broker event fields are invalid") from exc


_STATE_ORDER: dict[OrderState, int] = {
    "staged": 0,
    "submitted": 1,
    "accepted": 2,
    "partial": 3,
    "filled": 4,
    "canceled": 4,
    "rejected": 4,
    "unknown": 5,
}
_ACTIVE_PROTECTIVE_STATES = frozenset({"submitted", "accepted", "partial", "filled"})
_TERMINAL_PROTECTIVE_STATES = frozenset({"canceled", "rejected"})


def _finding(
    code: FindingCode, detail: str, *, order_id: int | None = None
) -> ReconciliationFinding:
    return ReconciliationFinding(code=code, order_id=order_id, detail=detail)


def analyze_reconciliation(
    expected: BracketExpectation,
    events: Iterable[BrokerEvent | Mapping[str, object]],
) -> ReconciliationAnalysis:
    """Analyze a snapshot/event stream and return a fail-closed report."""

    normalized: list[BrokerEvent] = []
    findings: list[ReconciliationFinding] = []
    for raw_event in events:
        if isinstance(raw_event, BrokerEvent):
            normalized.append(raw_event)
            continue
        try:
            normalized.append(normalize_broker_event(raw_event))
        except BrokerEventNormalizationError:
            findings.append(_finding("malformed_event", "broker event could not be normalized"))

    expected_legs = expected.expected_legs()
    event_ids: set[str] = set()
    fingerprints: set[str] = set()
    by_order: dict[int, list[BrokerEvent]] = defaultdict(list)
    for event in normalized:
        if event.group_id != expected.group_id:
            findings.append(
                _finding(
                    "unexpected_order",
                    "broker event belongs to an unexpected bracket group",
                    order_id=event.order_id,
                )
            )
        if event.order_id not in expected_legs:
            findings.append(
                _finding("unexpected_order", "broker event order is not in the bracket", order_id=event.order_id)
            )
        if event.event_id in event_ids or event.fingerprint() in fingerprints:
            findings.append(_finding("duplicate_event", "duplicate broker event observed", order_id=event.order_id))
        event_ids.add(event.event_id)
        fingerprints.add(event.fingerprint())
        by_order[event.order_id].append(event)
        if event.state == "unknown":
            findings.append(_finding("unknown_state", "broker reported an unknown order state", order_id=event.order_id))
        expected_leg = expected_legs.get(event.order_id)
        if expected_leg is not None and event.leg_type is not None and event.leg_type != expected_leg:
            findings.append(_finding("unexpected_order", "broker event leg does not match the bracket", order_id=event.order_id))
        if expected_leg in {"stop", "target"} and event.oca_group != expected.oca_group:
            findings.append(_finding("oca_inconsistent", "protective leg OCA group is inconsistent", order_id=event.order_id))

    for order_id, leg_type in expected_legs.items():
        if order_id not in by_order:
            findings.append(_finding("missing_order", f"bracket {leg_type} order state is missing", order_id=order_id))

    latest: dict[int, BrokerEvent] = {}
    for order_id, order_events in by_order.items():
        previous: BrokerEvent | None = None
        for event in order_events:
            if previous is not None:
                sequence_reversed = (
                    event.sequence is not None
                    and previous.sequence is not None
                    and event.sequence < previous.sequence
                )
                timestamp_reversed = event.event_time < previous.event_time
                state_reversed = _STATE_ORDER[event.state] < _STATE_ORDER[previous.state]
                terminal_reopened = previous.state in {
                    "filled",
                    "canceled",
                    "rejected",
                } and event.state != previous.state
                if sequence_reversed or timestamp_reversed or state_reversed or terminal_reopened:
                    findings.append(_finding("out_of_order", "broker order state arrived out of order", order_id=order_id))
            previous = event
        latest[order_id] = max(
            order_events,
            key=lambda event: (
                event.sequence if event.sequence is not None else -1,
                event.event_time,
            ),
        )

    parent = latest.get(expected.parent_order_id)
    filled_quantity = parent.filled_quantity if parent is not None else 0
    if parent is not None and parent.state == "filled" and filled_quantity <= 0:
        filled_quantity = expected.quantity if parent.state == "filled" else 0
    if filled_quantity > 0:
        for order_id, leg_type in expected_legs.items():
            if leg_type == "parent":
                continue
            protective = latest.get(order_id)
            if (
                protective is None
                or protective.state not in _ACTIVE_PROTECTIVE_STATES
                or protective.quantity < filled_quantity
            ):
                findings.append(
                    _finding(
                        "partial_fill_unprotected",
                        f"parent fill is not adequately protected by the {leg_type} leg",
                        order_id=order_id,
                    )
                )

    if expected.target_order_id is not None:
        stop = latest.get(expected.stop_order_id)
        target = latest.get(expected.target_order_id)
        if stop is not None and target is not None:
            stop_terminal = stop.state in _TERMINAL_PROTECTIVE_STATES
            target_terminal = target.state in _TERMINAL_PROTECTIVE_STATES
            stop_filled = stop.state == "filled"
            target_filled = target.state == "filled"
            if stop_terminal and not target_terminal and not target_filled:
                findings.append(_finding("oca_inconsistent", "stop sibling ended while target remains active", order_id=stop.order_id))
            if target_terminal and not stop_terminal and not stop_filled:
                findings.append(_finding("oca_inconsistent", "target sibling ended while stop remains active", order_id=target.order_id))

    return ReconciliationAnalysis(
        group_id=expected.group_id,
        status="blocked" if findings else "clear",
        findings=tuple(findings),
        events=tuple(normalized),
    )


reconcile = analyze_reconciliation
normalize_event = normalize_broker_event


__all__ = [
    "BrokerEvent",
    "BrokerEventNormalizationError",
    "BracketExpectation",
    "ReconciliationAnalysis",
    "ReconciliationFinding",
    "analyze_reconciliation",
    "normalize_broker_event",
    "normalize_event",
    "reconcile",
]

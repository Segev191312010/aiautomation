"""Position state transitions for Autotrader V1."""
from __future__ import annotations

from trading.contracts import PositionState


_ALLOWED_TRANSITIONS: dict[PositionState, frozenset[PositionState]] = {
    PositionState.FLAT: frozenset({PositionState.SIGNAL_FOUND}),
    PositionState.SIGNAL_FOUND: frozenset({PositionState.ANALYZING, PositionState.FLAT}),
    PositionState.ANALYZING: frozenset({PositionState.APPROVED, PositionState.FLAT}),
    PositionState.APPROVED: frozenset({PositionState.ENTRY_PENDING, PositionState.FLAT}),
    PositionState.ENTRY_PENDING: frozenset({PositionState.OPEN, PositionState.MANUAL_INTERVENTION, PositionState.FLAT}),
    PositionState.OPEN: frozenset({PositionState.EXIT_PENDING, PositionState.MANUAL_INTERVENTION}),
    PositionState.EXIT_PENDING: frozenset({PositionState.CLOSED, PositionState.MANUAL_INTERVENTION}),
    PositionState.CLOSED: frozenset({PositionState.FLAT}),
    PositionState.MANUAL_INTERVENTION: frozenset({PositionState.CLOSED, PositionState.FLAT}),
}


def can_transition(current: PositionState, target: PositionState) -> bool:
    return target in _ALLOWED_TRANSITIONS[current]


def require_transition(current: PositionState, target: PositionState) -> None:
    if not can_transition(current, target):
        raise ValueError(f"position transition {current.value} -> {target.value} is not allowed")

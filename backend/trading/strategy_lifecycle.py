"""Strategy promotion rules for Autotrader V1."""
from __future__ import annotations

from trading.contracts import StrategyRecord, StrategyState


_ALLOWED_TRANSITIONS: dict[StrategyState, frozenset[StrategyState]] = {
    StrategyState.DRAFT: frozenset({StrategyState.BACKTESTING, StrategyState.PAUSED, StrategyState.RETIRED}),
    StrategyState.BACKTESTING: frozenset({StrategyState.SHADOW, StrategyState.PAUSED, StrategyState.RETIRED}),
    StrategyState.SHADOW: frozenset({StrategyState.PAUSED, StrategyState.RETIRED}),
    StrategyState.PAPER: frozenset({StrategyState.PAUSED, StrategyState.RETIRED}),
    StrategyState.PAUSED: frozenset({StrategyState.RETIRED}),
    StrategyState.RETIRED: frozenset(),
}


def require_strategy_transition(
    *,
    record: StrategyRecord,
    target: StrategyState,
) -> None:
    if target not in _ALLOWED_TRANSITIONS[record.state]:
        raise ValueError(f"strategy transition {record.state.value} -> {target.value} is not allowed")

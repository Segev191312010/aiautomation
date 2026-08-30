"""Pure deterministic strategy scanner for validated market snapshots."""
from __future__ import annotations

from collections.abc import Iterable

from trading.contracts import FixedUniverse, MarketSnapshot, RuleCondition, StrategyRecord, StrategySignal


def _matches_condition(condition: RuleCondition, snapshot: MarketSnapshot) -> bool:
    current = snapshot.features.get(condition.feature)
    if current is None:
        return False
    if condition.operator == "gt":
        return current > condition.value
    if condition.operator == "gte":
        return current >= condition.value
    if condition.operator == "lt":
        return current < condition.value
    if condition.operator == "lte":
        return current <= condition.value
    previous = snapshot.previous_features.get(condition.feature)
    if previous is None:
        return False
    if condition.operator == "crosses_above":
        return previous <= condition.value < current
    return previous >= condition.value > current


def scan_strategy(
    *,
    record: StrategyRecord,
    snapshots: Iterable[MarketSnapshot],
) -> list[StrategySignal]:
    signals: list[StrategySignal] = []
    for snapshot in snapshots:
        if not snapshot.regular_session:
            continue
        if not isinstance(record.spec.universe, FixedUniverse):
            continue
        if snapshot.symbol not in record.spec.universe.symbols or snapshot.timeframe != record.spec.timeframe:
            continue
        matches = [_matches_condition(condition, snapshot) for condition in record.spec.conditions]
        matched = all(matches) if record.spec.condition_logic == "all" else any(matches)
        if not matched:
            continue
        signals.append(
            StrategySignal(
                strategy_id=record.strategy_id,
                strategy_version=record.version,
                symbol=snapshot.symbol,
                timeframe=snapshot.timeframe,
                snapshot_fingerprint=snapshot.fingerprint(),
                detected_at=snapshot.timestamp,
            )
        )
    return signals

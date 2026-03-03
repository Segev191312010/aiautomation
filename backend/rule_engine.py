"""
Rule evaluator.

Evaluates a list of Rule objects against OHLCV bar data and returns
which rules have their conditions met (and are not in cooldown).
"""
from __future__ import annotations
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional
import pandas as pd
from models import Rule, Condition
from indicators import calculate, detect_cross, resolve_value

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Single condition evaluation
# ---------------------------------------------------------------------------

def _evaluate_condition(cond: Condition, df: pd.DataFrame, cache: dict) -> bool:
    """
    Evaluate one condition against the last bar of df.

    Operators:
        crosses_above, crosses_below  — cross detection
        >, <, >=, <=, ==              — scalar or series comparison on last bar
    """
    op = cond.operator.lower().strip()

    try:
        # Compute the primary indicator series
        series_a = calculate(df, cond.indicator, cond.params)
        cache[f"{cond.indicator}_{cond.params}"] = series_a

        # Resolve the right-hand side (scalar or another series)
        rhs = resolve_value(cond.value, df, cache)

        if op == "crosses_above":
            if isinstance(rhs, pd.Series):
                return detect_cross(series_a, rhs) == "above"
            # Treat as: series_a crosses above scalar threshold
            scalar_series = pd.Series(float(rhs), index=series_a.index)
            return detect_cross(series_a, scalar_series) == "above"

        if op == "crosses_below":
            if isinstance(rhs, pd.Series):
                return detect_cross(series_a, rhs) == "below"
            scalar_series = pd.Series(float(rhs), index=series_a.index)
            return detect_cross(series_a, scalar_series) == "below"

        # Scalar comparison on last bar
        lhs_val = series_a.dropna().iloc[-1]
        if isinstance(rhs, pd.Series):
            rhs_val = rhs.dropna().iloc[-1]
        else:
            rhs_val = float(rhs)

        if op in (">", "gt"):
            return lhs_val > rhs_val
        if op in ("<", "lt"):
            return lhs_val < rhs_val
        if op in (">=", "gte"):
            return lhs_val >= rhs_val
        if op in ("<=", "lte"):
            return lhs_val <= rhs_val
        if op in ("==", "eq", "="):
            return abs(lhs_val - rhs_val) < 1e-9

        log.warning("Unknown operator '%s'", op)
        return False

    except Exception as exc:
        log.error("Error evaluating condition %s: %s", cond, exc)
        return False


# ---------------------------------------------------------------------------
# Single rule evaluation
# ---------------------------------------------------------------------------

def evaluate_rule(rule: Rule, df: pd.DataFrame) -> bool:
    """
    Evaluate all conditions of a rule against the provided OHLCV DataFrame.

    Returns True if the rule fires (all/any conditions met, depending on rule.logic),
    and the rule is not within its cooldown period.
    """
    if not rule.enabled:
        return False

    # Check cooldown
    if rule.last_triggered:
        last = datetime.fromisoformat(rule.last_triggered)
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        cooldown_end = last + timedelta(minutes=rule.cooldown_minutes)
        if datetime.now(timezone.utc) < cooldown_end:
            log.debug("Rule '%s' is in cooldown until %s", rule.name, cooldown_end)
            return False

    if df.empty or len(df) < 2:
        log.warning("Insufficient data for rule '%s'", rule.name)
        return False

    cache: dict = {}
    results = [_evaluate_condition(c, df, cache) for c in rule.conditions]

    if rule.logic == "AND":
        return all(results)
    return any(results)  # OR


# ---------------------------------------------------------------------------
# Evaluate all rules
# ---------------------------------------------------------------------------

def evaluate_conditions(
    conditions: list[Condition],
    df: pd.DataFrame,
    logic: str = "AND",
) -> bool:
    """
    Evaluate a list of conditions against a DataFrame slice.
    Used by backtester — no cooldown, no enabled check.

    Args:
        conditions: List of Condition objects.
        df:         DataFrame slice (e.g., df[:i+1] for bar-by-bar).
        logic:      "AND" or "OR".

    Returns:
        True if conditions are met per the logic operator.
    """
    if df.empty or len(df) < 2:
        return False
    cache: dict = {}
    results = [_evaluate_condition(c, df, cache) for c in conditions]
    if logic == "AND":
        return all(results)
    return any(results)  # OR


# ---------------------------------------------------------------------------
# Evaluate all rules
# ---------------------------------------------------------------------------

def evaluate_all(
    rules: list[Rule],
    bars_by_symbol: dict[str, pd.DataFrame],
) -> list[Rule]:
    """
    Evaluate all enabled rules and return those that fired.

    Args:
        rules:           All rules from the database.
        bars_by_symbol:  Dict mapping symbol → OHLCV DataFrame.

    Returns:
        List of rules that fired (conditions met, not in cooldown).
    """
    triggered: list[Rule] = []
    for rule in rules:
        if not rule.enabled:
            continue
        df = bars_by_symbol.get(rule.symbol.upper())
        if df is None or df.empty:
            log.warning("No bars available for symbol '%s' (rule: %s)", rule.symbol, rule.name)
            continue
        if evaluate_rule(rule, df):
            log.info("Rule TRIGGERED: '%s' on %s", rule.name, rule.symbol)
            triggered.append(rule)
    return triggered

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

# Global indicator cache — cleared once per bot cycle
_indicator_cache: dict[tuple, object] = {}


def clear_indicator_cache() -> None:
    """Call from bot_runner at the start of each cycle."""
    _indicator_cache.clear()


# ---------------------------------------------------------------------------
# Single condition evaluation
# ---------------------------------------------------------------------------

def _evaluate_condition(cond: Condition, df: pd.DataFrame, cache: dict, cache_scope: str = "") -> bool:
    """
    Evaluate one condition against the last bar of df.

    Operators:
        crosses_above, crosses_below  — cross detection
        >, <, >=, <=, ==              — scalar or series comparison on last bar
    """
    op = cond.operator.lower().strip()

    try:
        # Compute the primary indicator series (with deterministic cache key)
        last_time = str(df.index[-1]) if len(df) > 0 else ""
        normalized_params = tuple(sorted(cond.params.items())) if cond.params else ()
        cache_key = (cache_scope, len(df), last_time, cond.indicator, normalized_params)
        if cache_key in _indicator_cache:
            series_a = _indicator_cache[cache_key]
        else:
            series_a = calculate(df, cond.indicator, cond.params)
            _indicator_cache[cache_key] = series_a
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
    scope = rule.symbol.upper() if rule.symbol else f"universe:{rule.universe or 'custom'}"
    results = [_evaluate_condition(c, df, cache, cache_scope=scope) for c in rule.conditions]

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

def _check_symbol_cooldown(rule: Rule, symbol: str) -> bool:
    """
    Check if a specific symbol is within cooldown for a universe rule.

    Returns True if the symbol is still in cooldown (should NOT fire).
    """
    last_str = rule.symbol_cooldowns.get(symbol)
    if not last_str:
        return False
    try:
        last = datetime.fromisoformat(last_str)
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        cooldown_end = last + timedelta(minutes=rule.cooldown_minutes)
        return datetime.now(timezone.utc) < cooldown_end
    except (ValueError, TypeError):
        return False


def evaluate_all(
    rules: list[Rule],
    bars_by_symbol: dict[str, pd.DataFrame],
    universe_cache: dict[str, list[str]] | None = None,
) -> list[tuple[Rule, str]]:
    """
    Evaluate all enabled rules and return those that fired.

    For single-symbol rules: evaluates against rule.symbol.
    For universe rules: evaluates against every symbol in the universe.

    Args:
        rules:           All rules from the database.
        bars_by_symbol:  Dict mapping symbol → OHLCV DataFrame.
        universe_cache:  Pre-expanded universe symbol lists.

    Returns:
        List of (rule, symbol) tuples for each firing. For single-symbol
        rules, symbol == rule.symbol. For universe rules, symbol is the
        specific stock that triggered.
    """
    if universe_cache is None:
        universe_cache = {}

    triggered: list[tuple[Rule, str]] = []

    for rule in rules:
        if not rule.enabled:
            continue

        # ── Universe rule: evaluate against every symbol in the universe ──
        if rule.universe:
            universe_symbols = universe_cache.get(rule.universe, [])
            if not universe_symbols:
                log.warning("Universe '%s' is empty (rule: %s)", rule.universe, rule.name)
                continue

            fires_count = 0
            for sym in universe_symbols:
                sym_upper = sym.upper()
                df = bars_by_symbol.get(sym_upper)
                if df is None or df.empty:
                    continue

                # Per-symbol cooldown check
                if _check_symbol_cooldown(rule, sym_upper):
                    continue

                if _evaluate_conditions_for_rule(rule, df):
                    log.info(
                        "Universe rule TRIGGERED: '%s' on %s (universe=%s)",
                        rule.name, sym_upper, rule.universe,
                    )
                    triggered.append((rule, sym_upper))
                    fires_count += 1

            if fires_count > 0:
                log.info(
                    "Universe rule '%s' fired on %d / %d symbols",
                    rule.name, fires_count, len(universe_symbols),
                )
            continue

        # ── Single-symbol rule ───────────────────────────────────────────
        if not rule.symbol:
            log.warning("Rule '%s' has no symbol or universe, skipping", rule.name)
            continue
        df = bars_by_symbol.get(rule.symbol.upper())
        if df is None or df.empty:
            log.warning("No bars available for symbol '%s' (rule: %s)", rule.symbol, rule.name)
            continue
        if evaluate_rule(rule, df):
            log.info("Rule TRIGGERED: '%s' on %s", rule.name, rule.symbol)
            triggered.append((rule, rule.symbol))

    return triggered


def _evaluate_conditions_for_rule(rule: Rule, df: pd.DataFrame) -> bool:
    """
    Evaluate rule conditions against a DataFrame, without cooldown
    check (caller handles cooldown for universe rules).
    """
    if df.empty or len(df) < 2:
        return False
    cache: dict = {}
    scope = rule.symbol.upper() if rule.symbol else f"universe:{rule.universe or 'custom'}"
    results = [_evaluate_condition(c, df, cache, cache_scope=scope) for c in rule.conditions]
    if rule.logic == "AND":
        return all(results)
    return any(results)

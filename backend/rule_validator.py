"""Rule condition validator for the Rule Builder."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

VALID_INDICATORS = {"RSI", "SMA", "EMA", "MACD", "BBANDS", "ATR", "STOCH", "PRICE"}
VALID_OPERATORS = {"crosses_above", "crosses_below", ">", "<", ">=", "<=", "==", "gt", "lt", "gte", "lte", "eq"}


@dataclass
class ValidationError:
    field: str
    message: str
    suggestion: str = ""


@dataclass
class ValidationResult:
    valid: bool
    errors: list[ValidationError] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def validate_conditions(conditions: list[dict[str, Any]]) -> ValidationResult:
    """Validate a list of conditions without saving."""
    errors: list[ValidationError] = []
    warnings: list[str] = []

    if not conditions:
        errors.append(ValidationError("conditions", "At least one condition is required."))
        return ValidationResult(valid=False, errors=errors)

    for i, cond in enumerate(conditions):
        prefix = f"conditions[{i}]"

        # Indicator
        ind = cond.get("indicator", "")
        if ind not in VALID_INDICATORS:
            errors.append(ValidationError(
                f"{prefix}.indicator",
                f"Unknown indicator '{ind}'.",
                f"Valid: {', '.join(sorted(VALID_INDICATORS))}",
            ))

        # Operator
        op = cond.get("operator", "")
        if op not in VALID_OPERATORS:
            errors.append(ValidationError(
                f"{prefix}.operator",
                f"Unknown operator '{op}'.",
                f"Valid: {', '.join(sorted(VALID_OPERATORS))}",
            ))

        # Params
        params = cond.get("params", {})
        if ind in ("RSI", "SMA", "EMA", "ATR") and "period" in params:
            period = params["period"]
            if not isinstance(period, (int, float)) or period < 1:
                errors.append(ValidationError(
                    f"{prefix}.params.period",
                    f"Period must be >= 1, got {period}.",
                ))
            if period > 500:
                warnings.append(f"{prefix}: period={period} is very large — may need a lot of historical data.")

        if ind == "STOCH":
            for k in ("k_period", "d_period"):
                v = params.get(k, 14 if k == "k_period" else 3)
                if isinstance(v, (int, float)) and v < 1:
                    errors.append(ValidationError(f"{prefix}.params.{k}", f"{k} must be >= 1."))

        if ind == "MACD":
            fast = params.get("fast", 12)
            slow = params.get("slow", 26)
            if isinstance(fast, (int, float)) and isinstance(slow, (int, float)) and fast >= slow:
                errors.append(ValidationError(
                    f"{prefix}.params",
                    f"MACD fast ({fast}) must be less than slow ({slow}).",
                ))

        # Value
        value = cond.get("value")
        if value is None:
            errors.append(ValidationError(f"{prefix}.value", "Value is required."))
        elif isinstance(value, str):
            # Should reference another indicator like "SMA_200", "MACD_SIGNAL", "PRICE"
            valid_refs = {"PRICE", "SMA_", "EMA_", "BBANDS_UPPER_", "BBANDS_LOWER_", "BBANDS_MID_", "MACD_SIGNAL", "STOCH_D"}
            if not any(value.startswith(r) or value == r for r in valid_refs):
                warnings.append(f"{prefix}: value '{value}' may not be recognized.")

    # Check for contradictions
    rsi_ranges = []
    for cond in conditions:
        if cond.get("indicator") == "RSI":
            op = cond.get("operator", "")
            val = cond.get("value")
            if isinstance(val, (int, float)):
                if op in ("<", "lt"):
                    rsi_ranges.append(("lt", val))
                elif op in (">", "gt"):
                    rsi_ranges.append(("gt", val))
    for a in rsi_ranges:
        for b in rsi_ranges:
            if a[0] == "gt" and b[0] == "lt" and a[1] >= b[1]:
                errors.append(ValidationError(
                    "conditions",
                    f"Contradictory: RSI > {a[1]} AND RSI < {b[1]} can never be true.",
                    "Remove one of the conflicting RSI conditions.",
                ))

    return ValidationResult(valid=len(errors) == 0, errors=errors, warnings=warnings)

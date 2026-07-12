"""Fail-closed request and notional policy for manual orders."""
from __future__ import annotations

import math
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from config import cfg


StrictSymbol = Annotated[
    str,
    Field(
        strict=True,
        min_length=1,
        max_length=10,
        pattern=r"^[A-Z0-9][A-Z0-9.-]{0,9}$",
    ),
]
StrictQuantity = Annotated[int, Field(strict=True, ge=1, le=10_000)]
StrictPositivePrice = Annotated[
    float,
    Field(strict=True, gt=0, allow_inf_nan=False),
]


class ManualOrderPolicyError(ValueError):
    """Raised when an otherwise well-formed order exceeds manual policy."""


def validate_manual_order_notional(quantity: int, reference_price: float | None) -> float:
    """Validate unit notional against the configured absolute manual-order cap.

    This intentionally does not guess option or futures contract multipliers.
    Asset-specific multiplier policy remains a separate execution-boundary concern.
    """
    if isinstance(reference_price, bool) or not isinstance(reference_price, (int, float)):
        raise ManualOrderPolicyError("Manual order reference price must be numeric")

    price = float(reference_price)
    if not math.isfinite(price) or price <= 0:
        raise ManualOrderPolicyError("Manual order reference price must be finite and positive")

    max_notional = cfg.MANUAL_ORDER_MAX_NOTIONAL
    if not math.isfinite(max_notional) or max_notional <= 0:
        raise ManualOrderPolicyError("Manual order notional policy is misconfigured")

    notional = quantity * price
    if not math.isfinite(notional) or notional > max_notional:
        raise ManualOrderPolicyError(
            f"Manual order notional {notional:.2f} exceeds maximum {max_notional:.2f}"
        )
    return notional


class ManualOrderRequest(BaseModel):
    """Strict transport boundary for operator-submitted orders."""

    model_config = ConfigDict(extra="forbid")

    symbol: StrictSymbol
    action: Literal["BUY", "SELL"]
    quantity: StrictQuantity
    order_type: Literal["MKT", "LMT"] = "MKT"
    limit_price: StrictPositivePrice | None = None
    asset_type: Literal["STK", "OPT", "FUT"] = "STK"

    @field_validator("quantity")
    @classmethod
    def enforce_quantity_cap(cls, value: int) -> int:
        max_quantity = cfg.MANUAL_ORDER_MAX_QUANTITY
        if not 1 <= max_quantity <= 10_000:
            raise ValueError("Manual order quantity policy is misconfigured")
        if value > max_quantity:
            raise ValueError(f"Manual order quantity exceeds maximum {max_quantity}")
        return value

    @model_validator(mode="after")
    def enforce_order_type_policy(self) -> "ManualOrderRequest":
        if self.asset_type != "STK":
            raise ValueError(
                "Manual OPT/FUT orders are unavailable until multiplier-aware notional validation is implemented"
            )
        if self.order_type == "LMT":
            if self.limit_price is None:
                raise ValueError("limit_price is required for LMT orders")
            validate_manual_order_notional(self.quantity, self.limit_price)
        elif self.limit_price is not None:
            raise ValueError("limit_price must be omitted for MKT orders")
        return self

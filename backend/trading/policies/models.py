"""Frozen, versionable policies for the V1 pipeline."""
from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator

from trading.contracts import RiskPolicy, fingerprint_payload

PolicyType = Literal["candidate_selection", "opening_range_breakout", "risk", "execution"]


def _finite(value: Decimal, name: str) -> Decimal:
    if not value.is_finite():
        raise ValueError(f"{name} must be finite")
    return value


class CandidateSelectionPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scanner_policy_id: str = Field(min_length=1, max_length=128)
    scanner_policy_version: int = Field(ge=1)
    scanner_policy_hash: str = Field(min_length=64, max_length=64)
    maximum_candidates: int = Field(default=50, ge=1, le=50)
    currency: Literal["USD"] = "USD"
    security_type: Literal["STK"] = "STK"
    stock_type: Literal["COMMON"] = "COMMON"
    minimum_price: Decimal = Field(default=Decimal("10"), gt=0)
    maximum_price: Decimal = Field(default=Decimal("300"), gt=0)
    minimum_average_dollar_volume: Decimal = Field(default=Decimal("20000000"), gt=0)

    @field_validator("scanner_policy_hash")
    @classmethod
    def validate_hash(cls, value: str) -> str:
        if any(character not in "0123456789abcdef" for character in value):
            raise ValueError("scanner policy hash must be lowercase hexadecimal")
        return value

    @field_validator("minimum_price", "maximum_price", "minimum_average_dollar_volume")
    @classmethod
    def validate_finite_values(cls, value: Decimal, info: ValidationInfo) -> Decimal:
        return _finite(value, info.field_name)

    @model_validator(mode="after")
    def validate_price_range(self) -> "CandidateSelectionPolicy":
        if self.minimum_price >= self.maximum_price:
            raise ValueError("minimum price must be below maximum price")
        return self

    def fingerprint(self) -> str:
        return fingerprint_payload(self)


class OpeningRangeBreakoutPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    timezone: Literal["America/New_York"] = "America/New_York"
    timeframe: Literal["5m"] = "5m"
    opening_range_start: str = "09:30"
    opening_range_end: str = "09:45"
    entry_cutoff: str = "12:00"
    atr_period: int = Field(default=14, ge=1, le=100)
    stop_atr_multiple: Decimal = Field(default=Decimal("1"), gt=0)
    target_reward_risk: Decimal = Field(default=Decimal("2"), ge=Decimal("2"))
    max_spread_bps: Decimal = Field(default=Decimal("15"), gt=0)
    max_chase_bps: Decimal = Field(default=Decimal("20"), gt=0)
    quote_max_age_seconds: Decimal = Field(default=Decimal("2"), gt=0)

    @field_validator("stop_atr_multiple", "target_reward_risk", "max_spread_bps", "max_chase_bps", "quote_max_age_seconds")
    @classmethod
    def validate_finite_values(cls, value: Decimal, info: ValidationInfo) -> Decimal:
        return _finite(value, info.field_name)

    @model_validator(mode="after")
    def validate_v1_window(self) -> "OpeningRangeBreakoutPolicy":
        if self.opening_range_start != "09:30" or self.opening_range_end != "09:45":
            raise ValueError("V1 opening range must be 09:30 to 09:45")
        if self.entry_cutoff != "12:00":
            raise ValueError("V1 entry cutoff must be 12:00")
        return self

    def fingerprint(self) -> str:
        return fingerprint_payload(self)


class ExecutionPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    allowed_modes: tuple[Literal["backtest", "shadow"], ...] = ("backtest", "shadow")
    security_type: Literal["STK"] = "STK"
    side: Literal["BUY"] = "BUY"
    order_type: Literal["LMT"] = "LMT"
    long_only: Literal[True] = True
    required_protective_stop: Literal[True] = True
    optional_target: Literal[True] = True
    server_side_exit_engine: Literal[False] = False
    automatic_retry: Literal[False] = False

    @model_validator(mode="after")
    def validate_modes(self) -> "ExecutionPolicy":
        if not self.allowed_modes:
            raise ValueError("execution policy must allow at least one V1 mode")
        if any(mode not in {"backtest", "shadow"} for mode in self.allowed_modes):
            raise ValueError("execution policy cannot authorize PAPER or LIVE")
        return self

    def fingerprint(self) -> str:
        return fingerprint_payload(self)


PolicyValue = CandidateSelectionPolicy | OpeningRangeBreakoutPolicy | RiskPolicy | ExecutionPolicy


def policy_type_for(policy: PolicyValue) -> PolicyType:
    if isinstance(policy, CandidateSelectionPolicy):
        return "candidate_selection"
    if isinstance(policy, OpeningRangeBreakoutPolicy):
        return "opening_range_breakout"
    if isinstance(policy, RiskPolicy):
        return "risk"
    if isinstance(policy, ExecutionPolicy):
        return "execution"
    raise TypeError(f"unsupported policy value: {type(policy).__name__}")


def policy_from_payload(policy_type: PolicyType, payload: dict[str, object]) -> PolicyValue:
    model_types: dict[PolicyType, type[PolicyValue]] = {
        "candidate_selection": CandidateSelectionPolicy,
        "opening_range_breakout": OpeningRangeBreakoutPolicy,
        "risk": RiskPolicy,
        "execution": ExecutionPolicy,
    }
    return model_types[policy_type].model_validate(payload)

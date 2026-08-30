"""Canonical immutable contracts for Autotrader V1."""
from __future__ import annotations

import base64
import binascii
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
from hashlib import sha256
import json
from typing import Annotated, Any, Literal, Mapping, Self
from uuid import uuid4

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    field_validator,
    model_validator,
)


def fingerprint_payload(value: BaseModel | dict[str, object]) -> str:
    payload = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
    return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def require_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(timezone.utc)


class EngineMode(str, Enum):
    BACKTEST = "backtest"
    SHADOW = "shadow"
    PAPER = "paper"


class StrategyState(str, Enum):
    DRAFT = "draft"
    BACKTESTING = "backtesting"
    SHADOW = "shadow"
    PAPER = "paper"
    PAUSED = "paused"
    RETIRED = "retired"


class DecisionOutcome(str, Enum):
    DATA_BLOCKED = "data_blocked"
    RULE_REJECTED = "rule_rejected"
    AI_ABSTAINED = "ai_abstained"
    AI_BLOCKED = "ai_blocked"
    RISK_BLOCKED = "risk_blocked"
    SHADOW_ACCEPTED = "shadow_accepted"
    APPROVED = "approved"
    SUBMITTED = "submitted"


def _is_ed25519_signature(value: str) -> bool:
    if len(value) == 128:
        try:
            bytes.fromhex(value)
        except ValueError:
            return False
        return True
    if not 86 <= len(value) <= 88:
        return False
    try:
        padded = value + "=" * (-len(value) % 4)
        decoded = base64.b64decode(padded, altchars=b"-_", validate=True)
    except (ValueError, binascii.Error):
        return False
    return len(decoded) == 64


class PositionState(str, Enum):
    FLAT = "flat"
    SIGNAL_FOUND = "signal_found"
    ANALYZING = "analyzing"
    APPROVED = "approved"
    ENTRY_PENDING = "entry_pending"
    OPEN = "open"
    EXIT_PENDING = "exit_pending"
    CLOSED = "closed"
    MANUAL_INTERVENTION = "manual_intervention"


def normalize_symbols(values: tuple[str, ...]) -> tuple[str, ...]:
    normalized = tuple(value.strip().upper() for value in values)
    if any(not value or len(value) > 10 for value in normalized):
        raise ValueError("symbols must be non-empty US equity tickers")
    if len(set(normalized)) != len(normalized):
        raise ValueError("symbols must be unique")
    if any(not value.replace(".", "").replace("-", "").isalnum() for value in normalized):
        raise ValueError("symbols contain unsupported characters")
    return normalized


class FixedUniverse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["fixed"] = "fixed"
    symbols: tuple[str, ...] = Field(min_length=1, max_length=50)

    @field_validator("symbols")
    @classmethod
    def validate_symbols(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return normalize_symbols(values)


class ScannerUniverse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["scanner"] = "scanner"
    scanner_policy_id: str = Field(min_length=1, max_length=128)
    scanner_policy_version: int = Field(ge=1)
    scanner_policy_hash: str = Field(min_length=64, max_length=64)


UniverseSpec = Annotated[FixedUniverse | ScannerUniverse, Field(discriminator="kind")]
UNIVERSE_ADAPTER = TypeAdapter(UniverseSpec)
QUOTE_MAX_AGE_SECONDS = Decimal("2")


class RuleCondition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    feature: Literal["close", "vwap", "ema_fast", "ema_slow", "rvol", "volume"]
    operator: Literal["gt", "gte", "lt", "lte", "crosses_above", "crosses_below"]
    value: Decimal = Field(gt=Decimal("0"))
    lookback_bars: int = Field(default=1, ge=1, le=500)


class StrategySpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=3, max_length=120)
    universe: UniverseSpec
    timeframe: Literal["5m"] = "5m"
    conditions: tuple[RuleCondition, ...] = Field(min_length=1, max_length=12)
    condition_logic: Literal["all", "any"] = "all"
    ai_generated: bool = False
    model_version: str | None = Field(default=None, max_length=128)
    prompt_version: str | None = Field(default=None, max_length=128)
    rationale: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def require_ai_provenance(self) -> "StrategySpec":
        if self.ai_generated and (not self.model_version or not self.prompt_version):
            raise ValueError("AI-generated strategies require model and prompt versions")
        return self

    def fingerprint(self) -> str:
        return fingerprint_payload(self)


class StrategyVersion(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    strategy_id: str
    version: int = Field(ge=1)
    content_hash: str = Field(min_length=64, max_length=64)
    spec: StrategySpec
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("created_at")
    @classmethod
    def validate_created_at(cls, value: datetime) -> datetime:
        return require_aware(value)

    @model_validator(mode="after")
    def validate_content_hash(self) -> "StrategyVersion":
        if self.content_hash != self.spec.fingerprint():
            raise ValueError("strategy content_hash must match the immutable spec")
        return self


class StrategyRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    strategy_id: str
    owner_id: str
    version: int = Field(ge=1)
    content_hash: str = Field(min_length=64, max_length=64)
    spec: StrategySpec
    state: StrategyState = StrategyState.DRAFT
    revision: int = Field(default=0, ge=0)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("created_at", "updated_at")
    @classmethod
    def validate_timestamps(cls, value: datetime) -> datetime:
        return require_aware(value)

    @model_validator(mode="after")
    def validate_content_hash(self) -> "StrategyRecord":
        if self.content_hash != self.spec.fingerprint():
            raise ValueError("strategy content_hash must match the immutable spec")
        return self


class MarketSnapshot(BaseModel):
    """Compatibility snapshot used by the pure scanner until completed-bar ingestion lands."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    symbol: str
    timeframe: Literal["5m"] = "5m"
    timestamp: datetime
    regular_session: bool
    features: dict[str, Decimal] = Field(min_length=1)
    previous_features: dict[str, Decimal] = Field(default_factory=dict)
    source: Literal["ibkr", "historical"]

    @field_validator("symbol")
    @classmethod
    def validate_snapshot_symbol(cls, value: str) -> str:
        return normalize_symbols((value,))[0]

    @field_validator("timestamp")
    @classmethod
    def validate_snapshot_time(cls, value: datetime) -> datetime:
        return require_aware(value)

    @field_validator("features", "previous_features")
    @classmethod
    def validate_features(cls, value: dict[str, Decimal]) -> dict[str, Decimal]:
        allowed = {"close", "vwap", "ema_fast", "ema_slow", "rvol", "volume"}
        normalized = {key.strip().lower(): number for key, number in value.items()}
        unsupported = set(normalized) - allowed
        if unsupported:
            raise ValueError(f"unsupported market features: {sorted(unsupported)}")
        if any(not number.is_finite() for number in normalized.values()):
            raise ValueError("market features must be finite")
        return normalized

    def fingerprint(self) -> str:
        return fingerprint_payload(self)


class StrategySignal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    strategy_id: str
    strategy_version: int = Field(ge=1)
    symbol: str
    timeframe: Literal["5m"] = "5m"
    snapshot_fingerprint: str = Field(min_length=64, max_length=64)
    detected_at: datetime

    @field_validator("detected_at")
    @classmethod
    def validate_signal_time(cls, value: datetime) -> datetime:
        return require_aware(value)


class Run(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(default_factory=lambda: str(uuid4()))
    owner_id: str
    source_sha: str = Field(min_length=7, max_length=64)
    strategy_id: str
    strategy_version: int = Field(ge=1)
    policy_fingerprints: dict[str, str]
    session_date: date
    mode: EngineMode
    status: Literal["pending", "running", "completed", "failed"] = "pending"
    started_at: datetime | None = None
    closed_at: datetime | None = None

    @field_validator("started_at", "closed_at")
    @classmethod
    def validate_optional_timestamps(cls, value: datetime | None) -> datetime | None:
        return require_aware(value) if value is not None else None


class ScannerRun(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(default_factory=lambda: str(uuid4()))
    run_id: str
    parameters: dict[str, object]
    parameters_fingerprint: str = Field(min_length=64, max_length=64)
    status: Literal["pending", "completed", "failed"]
    started_at: datetime
    completed_at: datetime | None = None

    @field_validator("started_at", "completed_at")
    @classmethod
    def validate_scanner_timestamps(cls, value: datetime | None) -> datetime | None:
        return require_aware(value) if value is not None else None


class Candidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(default_factory=lambda: str(uuid4()))
    scanner_run_id: str
    con_id: int = Field(gt=0)
    symbol: str
    currency: Literal["USD"] = "USD"
    security_type: Literal["STK"] = "STK"
    stock_type: Literal["COMMON"] = "COMMON"
    primary_exchange: str = Field(min_length=1)
    rank: int = Field(ge=1, le=50)
    admission_status: Literal["admitted", "rejected"]
    rejection_reason: str | None = None

    @field_validator("symbol")
    @classmethod
    def validate_symbol(cls, value: str) -> str:
        return normalize_symbols((value,))[0]

    @model_validator(mode="after")
    def validate_admission_shape(self) -> "Candidate":
        has_reason = self.rejection_reason is not None and bool(self.rejection_reason.strip())
        if self.admission_status == "admitted" and self.rejection_reason is not None:
            raise ValueError("admitted candidates cannot have a rejection reason")
        if self.admission_status == "rejected" and not has_reason:
            raise ValueError("rejected candidates require a rejection reason")
        return self


class CompletedBar(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    con_id: int = Field(gt=0)
    interval_start_utc: datetime
    interval_end_utc: datetime
    timeframe: Literal["5m"] = "5m"
    open: Decimal = Field(gt=0)
    high: Decimal = Field(gt=0)
    low: Decimal = Field(gt=0)
    close: Decimal = Field(gt=0)
    volume: Decimal = Field(ge=0)
    rth: bool
    source: Literal["ibkr", "historical"]
    data_mode: Literal["live", "historical"]
    completed: Literal[True] = True

    @field_validator("interval_start_utc", "interval_end_utc")
    @classmethod
    def validate_bar_timestamps(cls, value: datetime) -> datetime:
        return require_aware(value)

    @model_validator(mode="after")
    def validate_bar(self) -> "CompletedBar":
        if self.interval_end_utc <= self.interval_start_utc:
            raise ValueError("bar interval must be positive")
        if self.interval_end_utc - self.interval_start_utc != timedelta(minutes=5):
            raise ValueError("completed bars must cover exactly five minutes")
        if self.high < max(self.open, self.close) or self.low > min(self.open, self.close):
            raise ValueError("OHLC range must contain open and close")
        if self.low > self.high:
            raise ValueError("bar low must not exceed high")
        return self

    def fingerprint(self) -> str:
        return fingerprint_payload(self)


class QuoteSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    con_id: int = Field(gt=0)
    bid: Decimal = Field(gt=0)
    ask: Decimal = Field(gt=0)
    observed_at: datetime
    min_tick: Decimal = Field(gt=0)

    @field_validator("observed_at")
    @classmethod
    def validate_observed_at(cls, value: datetime) -> datetime:
        return require_aware(value)

    @model_validator(mode="after")
    def validate_quote(self) -> "QuoteSnapshot":
        if self.ask < self.bid:
            raise ValueError("ask must not be below bid")
        return self

    def age_seconds(self, now: datetime | None = None) -> Decimal:
        """Return the non-negative age used by quote admission checks."""
        observed_now = (now or datetime.now(timezone.utc))
        observed_now = require_aware(observed_now)
        age = Decimal(str((observed_now - self.observed_at).total_seconds()))
        return age

    def is_fresh(
        self,
        *,
        now: datetime | None = None,
        max_age_seconds: Decimal = QUOTE_MAX_AGE_SECONDS,
    ) -> bool:
        """Quotes are fresh only when observed and no older than two seconds by default."""
        max_age = Decimal(str(max_age_seconds))
        if not max_age.is_finite() or max_age < 0:
            raise ValueError("max_age_seconds must be finite and non-negative")
        age = self.age_seconds(now)
        return Decimal("0") <= age <= max_age

    def require_fresh(
        self,
        *,
        now: datetime | None = None,
        max_age_seconds: Decimal = QUOTE_MAX_AGE_SECONDS,
    ) -> "QuoteSnapshot":
        if not self.is_fresh(now=now, max_age_seconds=max_age_seconds):
            raise ValueError("quote snapshot is stale or observed in the future")
        return self

    @property
    def spread_bps(self) -> Decimal:
        midpoint = (self.ask + self.bid) / Decimal("2")
        return (self.ask - self.bid) / midpoint * Decimal("10000")

    def fingerprint(self) -> str:
        return fingerprint_payload(self)


class RuleSetup(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    candidate_id: str
    strategy_id: str
    strategy_version: int = Field(ge=1)
    symbol: str
    opening_range_high: Decimal = Field(gt=0)
    opening_range_low: Decimal = Field(gt=0)
    atr: Decimal = Field(gt=0)
    entry_limit: Decimal = Field(gt=0)
    stop_price: Decimal = Field(gt=0)
    target_price: Decimal = Field(gt=0)
    bar_fingerprints: tuple[str, ...] = Field(min_length=1)
    quote_fingerprint: str = Field(min_length=64, max_length=64)
    detected_at: datetime

    @field_validator("symbol")
    @classmethod
    def validate_setup_symbol(cls, value: str) -> str:
        return normalize_symbols((value,))[0]

    @field_validator("detected_at")
    @classmethod
    def validate_detected_at(cls, value: datetime) -> datetime:
        return require_aware(value)

    @model_validator(mode="after")
    def validate_prices(self) -> "RuleSetup":
        if not self.stop_price < self.entry_limit < self.target_price:
            raise ValueError("rule setup must satisfy stop < entry < target")
        return self

    def fingerprint(self) -> str:
        return fingerprint_payload(self)


class AIReview(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    setup_fingerprint: str = Field(min_length=64, max_length=64)
    decision: Literal["PROPOSE", "ABSTAIN"]
    confidence: Decimal | None = Field(default=None, ge=0, le=1)
    rationale: str = Field(min_length=1, max_length=2000)
    model_id: str = Field(min_length=1, max_length=128)
    prompt_version: str = Field(min_length=1, max_length=128)
    response_hash: str = Field(min_length=64, max_length=64)
    latency_ms: int = Field(ge=0)
    cost_usd: Decimal = Field(ge=0)
    failure_reason: str | None = None

    @model_validator(mode="after")
    def validate_failure_shape(self) -> "AIReview":
        if self.failure_reason and self.decision != "ABSTAIN":
            raise ValueError("failed AI reviews must abstain")
        return self

    def fingerprint(self) -> str:
        return fingerprint_payload(self)


class PositionExposure(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    symbol: str
    quantity: int = Field(gt=0)
    market_value: Decimal = Field(ge=0)

    @field_validator("symbol")
    @classmethod
    def validate_symbol(cls, value: str) -> str:
        return normalize_symbols((value,))[0]


class PortfolioSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    fingerprint: str = ""
    observed_at: datetime
    net_liquidation: Decimal = Field(gt=0)
    realized_day_pnl: Decimal = Decimal("0")
    positions: tuple[PositionExposure, ...] = ()
    pending_symbols: tuple[str, ...] = ()
    reserved_risk: Decimal = Field(default=Decimal("0"), ge=0)
    reserved_notional: Decimal = Field(default=Decimal("0"), ge=0)

    @field_validator("observed_at")
    @classmethod
    def validate_portfolio_time(cls, value: datetime) -> datetime:
        return require_aware(value)

    @model_validator(mode="after")
    def validate_fingerprint(self) -> "PortfolioSnapshot":
        payload = self.model_dump(mode="json", exclude={"fingerprint"})
        expected = fingerprint_payload(payload)
        if self.fingerprint and self.fingerprint != expected:
            raise ValueError("portfolio fingerprint must match immutable snapshot fields")
        object.__setattr__(self, "fingerprint", expected)
        return self

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        values = self.model_dump(mode="python")
        updates = dict(update or {})
        values.update(updates)
        if "fingerprint" not in updates:
            values.pop("fingerprint", None)
        return type(self).model_validate(values)


class RiskPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = "moderate-pilot"
    version: int = Field(default=1, ge=1)
    max_open_positions: int = Field(default=3, ge=1, le=3)
    max_risk_per_trade_pct: Decimal = Field(default=Decimal("0.5"), gt=0, le=Decimal("0.5"))
    max_daily_loss_pct: Decimal = Field(default=Decimal("2"), gt=0, le=Decimal("2"))
    max_position_notional_pct: Decimal = Field(default=Decimal("15"), gt=0, le=Decimal("20"))
    max_total_notional_pct: Decimal = Field(default=Decimal("45"), gt=0, le=Decimal("45"))
    min_reward_to_risk: Decimal = Field(default=Decimal("2"), ge=Decimal("2"), le=Decimal("10"))

    def fingerprint(self) -> str:
        return fingerprint_payload(self)


class TradeIntent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    execution_key: str = Field(min_length=1, max_length=200)
    setup_fingerprint: str = Field(min_length=64, max_length=64)
    approval_token: str = Field(min_length=32, max_length=256)
    expires_at: datetime
    strategy_id: str
    strategy_version: int = Field(ge=1)
    symbol: str
    quantity: int = Field(gt=0)
    side: Literal["BUY"] = "BUY"
    entry_limit: Decimal = Field(gt=0)
    stop_price: Decimal = Field(gt=0)
    target_price: Decimal = Field(gt=0)

    @field_validator("symbol")
    @classmethod
    def validate_intent_symbol(cls, value: str) -> str:
        return normalize_symbols((value,))[0]

    @field_validator("expires_at")
    @classmethod
    def validate_expiry(cls, value: datetime) -> datetime:
        return require_aware(value)

    @model_validator(mode="after")
    def validate_long_bracket(self) -> "TradeIntent":
        if not self.stop_price < self.entry_limit < self.target_price:
            raise ValueError("trade intent must satisfy stop < entry < target")
        return self

    @property
    def risk_amount(self) -> Decimal:
        return (self.entry_limit - self.stop_price) * self.quantity

    @property
    def notional_amount(self) -> Decimal:
        return self.entry_limit * self.quantity

    @property
    def reward_to_risk(self) -> Decimal:
        return (self.target_price - self.entry_limit) / (self.entry_limit - self.stop_price)


class RiskApproval(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    approved: bool
    codes: tuple[str, ...] = ()
    intent: TradeIntent | None = None
    setup_fingerprint: str = Field(min_length=64, max_length=64)
    ai_review_fingerprint: str = Field(min_length=64, max_length=64)
    policy_fingerprint: str = Field(min_length=64, max_length=64)
    portfolio_fingerprint: str = Field(min_length=64, max_length=64)
    expires_at: datetime

    @field_validator("expires_at")
    @classmethod
    def validate_approval_expiry(cls, value: datetime) -> datetime:
        return require_aware(value)

    @model_validator(mode="after")
    def validate_approval(self) -> "RiskApproval":
        if any(not code.strip() for code in self.codes):
            raise ValueError("risk result codes must be non-empty")
        if self.approved != (self.intent is not None):
            raise ValueError("approved risk results require exactly one trade intent")
        if self.approved and self.codes:
            raise ValueError("approved risk results cannot contain blocking codes")
        if not self.approved and not self.codes:
            raise ValueError("blocked risk results require at least one code")
        if self.intent is not None:
            if self.intent.setup_fingerprint != self.setup_fingerprint:
                raise ValueError("trade intent must match the approved setup")
            if self.intent.expires_at != self.expires_at:
                raise ValueError("trade intent and risk approval must expire together")
        return self

    @property
    def outcome(self) -> Literal["approved", "blocked"]:
        return "approved" if self.approved else "blocked"


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(default_factory=lambda: str(uuid4()))
    run_id: str
    candidate_id: str
    strategy_id: str
    strategy_version: int = Field(ge=1)
    decision_key: str = Field(min_length=64, max_length=64)
    outcome: DecisionOutcome
    setup_fingerprint: str | None = Field(default=None, min_length=64, max_length=64)
    ai_review: AIReview | None = None
    risk: RiskApproval | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("created_at")
    @classmethod
    def validate_decision_time(cls, value: datetime) -> datetime:
        return require_aware(value)

    @model_validator(mode="after")
    def validate_outcome(self) -> "Decision":
        outcome = self.outcome
        review = self.ai_review
        risk = self.risk

        if self.setup_fingerprint is not None:
            if review is not None and review.setup_fingerprint != self.setup_fingerprint:
                raise ValueError("AI review must match the decision setup")
            if risk is not None and risk.setup_fingerprint != self.setup_fingerprint:
                raise ValueError("risk approval must match the decision setup")
        elif review is not None or risk is not None:
            raise ValueError("AI and risk records require a decision setup fingerprint")

        if risk is not None and review is not None:
            if risk.ai_review_fingerprint != review.fingerprint():
                raise ValueError("risk approval must match the decision AI review")
            if risk.intent is not None:
                if risk.intent.strategy_id != self.strategy_id:
                    raise ValueError("trade intent must match the decision strategy")
                if risk.intent.strategy_version != self.strategy_version:
                    raise ValueError("trade intent must match the decision strategy version")

        if outcome in {
            DecisionOutcome.AI_ABSTAINED,
            DecisionOutcome.AI_BLOCKED,
        }:
            if review is None or review.decision != "ABSTAIN" or risk is not None:
                raise ValueError("AI terminal outcomes require an abstaining review without risk")
            if outcome == DecisionOutcome.AI_BLOCKED and not review.failure_reason:
                raise ValueError("AI-blocked outcomes require a failure reason")
            if outcome == DecisionOutcome.AI_ABSTAINED and review.failure_reason:
                raise ValueError("AI-abstained outcomes cannot contain a failure reason")
        elif outcome in {
            DecisionOutcome.SHADOW_ACCEPTED,
            DecisionOutcome.APPROVED,
            DecisionOutcome.SUBMITTED,
        }:
            if review is None or review.decision != "PROPOSE":
                raise ValueError("approved outcomes require a proposing AI review")
            if risk is None or not risk.approved or risk.intent is None:
                raise ValueError("approved outcomes require an approved trade intent")
        elif outcome == DecisionOutcome.RISK_BLOCKED:
            if review is None or review.decision != "PROPOSE":
                raise ValueError("risk-blocked outcomes require a proposing AI review")
            if risk is None or risk.approved:
                raise ValueError("risk-blocked outcomes require a blocked risk approval")
        elif outcome in {DecisionOutcome.DATA_BLOCKED, DecisionOutcome.RULE_REJECTED}:
            if review is not None or risk is not None:
                raise ValueError("data or rule rejection cannot contain AI or risk decisions")

        return self


class AuthorityReceipt(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_by_alias=True,
        validate_by_name=True,
    )

    id: str = Field(default_factory=lambda: str(uuid4()))
    build_sha: str = Field(min_length=7, max_length=64)
    evidence_digest: str = Field(min_length=64, max_length=64)
    strategy_id: str
    strategy_version: int = Field(ge=1)
    policy_hashes: tuple[str, ...] = Field(min_length=1)
    paper_account: str = Field(min_length=1, max_length=64)
    client_id: int = Field(ge=0)
    expires_at: datetime
    owner_approval: str = Field(min_length=1)
    signature: str | None = None
    public_key_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        validation_alias=AliasChoices("public_key_id", "signing_key_id"),
    )
    issued_at: datetime | None = None
    expires_after_sessions: int = Field(
        default=5,
        ge=1,
        le=5,
        validation_alias=AliasChoices(
            "expires_after_sessions",
            "valid_for_sessions",
            "max_sessions",
            "session_expiry_sessions",
        ),
    )

    @field_validator("expires_at")
    @classmethod
    def validate_receipt_expiry(cls, value: datetime) -> datetime:
        return require_aware(value)

    @field_validator("issued_at")
    @classmethod
    def validate_receipt_issued_at(cls, value: datetime | None) -> datetime | None:
        return require_aware(value) if value is not None else None

    @field_validator("signature")
    @classmethod
    def validate_ed25519_signature(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if _is_ed25519_signature(value):
            return value
        raise ValueError("signature must encode a 64-byte Ed25519 signature")

    @model_validator(mode="after")
    def validate_signed_receipt(self) -> Self:
        signed_fields = (self.signature, self.public_key_id, self.issued_at)
        if any(value is not None for value in signed_fields) and not all(
            value is not None for value in signed_fields
        ):
            raise ValueError("signed authority receipts require signature, public key, and issued time")
        if self.issued_at is not None and self.issued_at > self.expires_at:
            raise ValueError("authority receipt issued time must precede expiry")
        return self

    @property
    def valid_for_sessions(self) -> int:
        """Compatibility name for the bounded session expiry metadata."""

        return self.expires_after_sessions

    @property
    def max_sessions(self) -> int:
        return self.expires_after_sessions


class ReconciliationReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(default_factory=lambda: str(uuid4()))
    paper_account: str
    client_id: int = Field(ge=0)
    status: Literal["clear", "blocked"]
    positions: tuple[dict[str, object], ...] = ()
    orders: tuple[dict[str, object], ...] = ()
    executions: tuple[dict[str, object], ...] = ()
    unknowns: tuple[str, ...] = ()
    mismatches: tuple[str, ...] = ()
    observed_at: datetime

    @field_validator("observed_at")
    @classmethod
    def validate_reconciliation_time(cls, value: datetime) -> datetime:
        return require_aware(value)

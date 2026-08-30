"""Schema and threshold validation for immutable V1 evidence bundles."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import re
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    ValidationInfo,
    field_validator,
    model_validator,
)

from trading.contracts import DecisionOutcome, require_aware

_SHA_PATTERN = re.compile(r"^[0-9a-f]{7,64}$")
_HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_TERMINAL_OUTCOMES = frozenset(outcome.value for outcome in DecisionOutcome)
MAX_AI_CALLS_PER_SESSION = 20
MAX_AI_COST_PER_SESSION = Decimal("5")


def _sha(value: str, *, full: bool = False) -> str:
    pattern = _HASH_PATTERN if full else _SHA_PATTERN
    if not pattern.fullmatch(value):
        raise ValueError("SHA values must be lowercase hexadecimal")
    return value


def _finite(value: Decimal, field_name: str) -> Decimal:
    if not value.is_finite():
        raise ValueError(f"{field_name} must be finite")
    return value


class DataProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_sha: str = Field(min_length=7, max_length=64)
    dataset_id: str = Field(min_length=1, max_length=200)
    dataset_fingerprint: str = Field(min_length=64, max_length=64)
    provider: str = Field(min_length=1, max_length=120)
    session_count: int = Field(ge=1)
    completed_bar_count: int = Field(ge=0)
    candidate_count: int = Field(ge=0)
    captured_at: datetime

    @field_validator("source_sha")
    @classmethod
    def validate_source_sha(cls, value: str) -> str:
        return _sha(value)

    @field_validator("dataset_fingerprint")
    @classmethod
    def validate_dataset_fingerprint(cls, value: str) -> str:
        return _sha(value, full=True)

    @field_validator("captured_at")
    @classmethod
    def validate_captured_at(cls, value: datetime) -> datetime:
        return require_aware(value)


class ReplayEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_sha: str = Field(min_length=7, max_length=64)
    dataset_fingerprint: str = Field(min_length=64, max_length=64)
    evaluated_sessions: int = Field(ge=1)
    candidate_count: int = Field(ge=0)
    terminal_decisions: int = Field(ge=0)
    replay_equality: bool
    replayed_decisions: int = Field(ge=0)

    @field_validator("source_sha")
    @classmethod
    def validate_source_sha(cls, value: str) -> str:
        return _sha(value)

    @field_validator("dataset_fingerprint")
    @classmethod
    def validate_dataset_fingerprint(cls, value: str) -> str:
        return _sha(value, full=True)

    @model_validator(mode="after")
    def validate_counts(self) -> "ReplayEvidence":
        if self.terminal_decisions > self.candidate_count:
            raise ValueError("replay terminal decisions cannot exceed candidates")
        if self.replayed_decisions > self.terminal_decisions:
            raise ValueError("replayed decisions cannot exceed terminal decisions")
        return self

    @property
    def decision_completeness(self) -> Decimal:
        if self.candidate_count == 0:
            return Decimal("0")
        return Decimal(self.replayed_decisions) / Decimal(self.candidate_count)


class DecisionEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate_count: int = Field(ge=1)
    terminal_decisions: int = Field(ge=0)
    outcome_counts: dict[str, int] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_outcomes(self) -> "DecisionEvidence":
        if set(self.outcome_counts) - _TERMINAL_OUTCOMES:
            raise ValueError("decision evidence contains an unknown terminal outcome")
        if any(count < 0 for count in self.outcome_counts.values()):
            raise ValueError("decision outcome counts cannot be negative")
        if sum(self.outcome_counts.values()) != self.terminal_decisions:
            raise ValueError("decision outcome counts must equal terminal decisions")
        if self.terminal_decisions > self.candidate_count:
            raise ValueError("terminal decisions cannot exceed candidates")
        return self

    @property
    def completeness(self) -> Decimal:
        return Decimal(self.terminal_decisions) / Decimal(self.candidate_count)


class AIUsageEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    call_count: int = Field(ge=0)
    total_cost_usd: Decimal = Field(ge=0)
    average_latency_ms: Decimal = Field(ge=0)
    p95_latency_ms: Decimal = Field(ge=0)
    max_session_calls: int = Field(ge=0)
    max_session_cost_usd: Decimal = Field(ge=0)

    @field_validator(
        "total_cost_usd",
        "average_latency_ms",
        "p95_latency_ms",
        "max_session_cost_usd",
    )
    @classmethod
    def validate_finite_values(cls, value: Decimal, info: ValidationInfo) -> Decimal:
        return _finite(value, info.field_name)

    @model_validator(mode="after")
    def validate_session_limits(self) -> "AIUsageEvidence":
        if self.max_session_calls > MAX_AI_CALLS_PER_SESSION:
            raise ValueError("AI session call limit exceeded")
        if self.max_session_cost_usd > MAX_AI_COST_PER_SESSION:
            raise ValueError("AI session cost limit exceeded")
        if self.call_count == 0 and self.total_cost_usd != 0:
            raise ValueError("AI cost requires at least one call")
        return self


class PerformanceEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    rth_sessions: int = Field(ge=0)
    modeled_fills: int = Field(ge=0)
    profit_factor: Decimal = Field(ge=0)
    max_drawdown_pct: Decimal = Field(ge=0)
    rule_only_expectancy_r: Decimal
    ai_expectancy_r: Decimal

    @field_validator(
        "profit_factor",
        "max_drawdown_pct",
        "rule_only_expectancy_r",
        "ai_expectancy_r",
    )
    @classmethod
    def validate_finite_values(cls, value: Decimal, info: ValidationInfo) -> Decimal:
        return _finite(value, info.field_name)


class ShadowGateThresholds(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    min_rth_sessions: int
    min_terminal_decisions: int
    min_modeled_fills: int
    min_profit_factor: Decimal
    max_drawdown_pct: Decimal
    min_ai_expectancy_ratio: Decimal
    require_replay_equality: bool
    min_decision_completeness: Decimal

    @model_validator(mode="after")
    def validate_declared_thresholds(self) -> "ShadowGateThresholds":
        expected = {
            "min_rth_sessions": 10,
            "min_terminal_decisions": 100,
            "min_modeled_fills": 30,
            "min_profit_factor": Decimal("1.20"),
            "max_drawdown_pct": Decimal("3"),
            "min_ai_expectancy_ratio": Decimal("0.95"),
            "require_replay_equality": True,
            "min_decision_completeness": Decimal("1"),
        }
        for field_name, expected_value in expected.items():
            if getattr(self, field_name) != expected_value:
                raise ValueError(f"shadow gate threshold {field_name} is not the declared V1 value")
        return self


class EvidenceBundle(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    source_sha: str = Field(min_length=7, max_length=64)
    generated_at: datetime
    strategy_id: str = Field(min_length=1, max_length=200)
    strategy_version: int = Field(ge=1)
    strategy_content_hash: str = Field(min_length=64, max_length=64)
    policy_hashes: tuple[str, ...] = Field(min_length=1)
    data_provenance: tuple[DataProvenance, ...] = Field(min_length=1)
    replay: ReplayEvidence
    decisions: DecisionEvidence
    ai_usage: AIUsageEvidence
    performance: PerformanceEvidence
    thresholds: ShadowGateThresholds

    @field_validator("source_sha")
    @classmethod
    def validate_source_sha(cls, value: str) -> str:
        return _sha(value)

    @field_validator("strategy_content_hash")
    @classmethod
    def validate_strategy_hash(cls, value: str) -> str:
        return _sha(value, full=True)

    @field_validator("policy_hashes")
    @classmethod
    def validate_policy_hashes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("policy hashes must be unique")
        return tuple(_sha(item, full=True) for item in value)

    @field_validator("generated_at")
    @classmethod
    def validate_generated_at(cls, value: datetime) -> datetime:
        return require_aware(value)

    @model_validator(mode="after")
    def validate_provenance(self) -> "EvidenceBundle":
        if self.replay.source_sha != self.source_sha:
            raise ValueError("replay source SHA must match bundle source SHA")
        if any(item.source_sha != self.source_sha for item in self.data_provenance):
            raise ValueError("data provenance source SHA must match bundle source SHA")
        if self.replay.dataset_fingerprint not in {
            item.dataset_fingerprint for item in self.data_provenance
        }:
            raise ValueError("replay dataset must be present in data provenance")
        if self.replay.candidate_count != self.decisions.candidate_count:
            raise ValueError("replay and decision candidate counts must match")
        if self.replay.terminal_decisions != self.decisions.terminal_decisions:
            raise ValueError("replay and decision terminal counts must match")
        if sum(item.session_count for item in self.data_provenance) < self.performance.rth_sessions:
            raise ValueError("data provenance does not cover performance sessions")
        return self

    def gate_checks(self) -> tuple["GateCheck", ...]:
        thresholds = self.thresholds
        performance = self.performance
        decisions = self.decisions
        replay = self.replay
        ai_ratio = (
            performance.ai_expectancy_r / performance.rule_only_expectancy_r
            if performance.rule_only_expectancy_r > 0
            else Decimal("0")
        )
        return (
            GateCheck(
                name="rth_sessions",
                passed=performance.rth_sessions >= thresholds.min_rth_sessions,
                observed=str(performance.rth_sessions),
                required=f">={thresholds.min_rth_sessions}",
            ),
            GateCheck(
                name="terminal_decisions",
                passed=decisions.terminal_decisions >= thresholds.min_terminal_decisions,
                observed=str(decisions.terminal_decisions),
                required=f">={thresholds.min_terminal_decisions}",
            ),
            GateCheck(
                name="modeled_fills",
                passed=performance.modeled_fills >= thresholds.min_modeled_fills,
                observed=str(performance.modeled_fills),
                required=f">={thresholds.min_modeled_fills}",
            ),
            GateCheck(
                name="positive_net_expectancy",
                passed=performance.ai_expectancy_r > 0,
                observed=str(performance.ai_expectancy_r),
                required=">0",
            ),
            GateCheck(
                name="profit_factor",
                passed=performance.profit_factor >= thresholds.min_profit_factor,
                observed=str(performance.profit_factor),
                required=f">={thresholds.min_profit_factor}",
            ),
            GateCheck(
                name="maximum_drawdown_pct",
                passed=performance.max_drawdown_pct <= thresholds.max_drawdown_pct,
                observed=str(performance.max_drawdown_pct),
                required=f"<={thresholds.max_drawdown_pct}",
            ),
            GateCheck(
                name="ai_expectancy_vs_rule_only",
                passed=ai_ratio >= thresholds.min_ai_expectancy_ratio,
                observed=str(ai_ratio),
                required=f">={thresholds.min_ai_expectancy_ratio}",
            ),
            GateCheck(
                name="replay_equality",
                passed=(not thresholds.require_replay_equality) or replay.replay_equality,
                observed=str(replay.replay_equality),
                required=str(thresholds.require_replay_equality),
            ),
            GateCheck(
                name="replay_decision_completeness",
                passed=replay.decision_completeness >= thresholds.min_decision_completeness,
                observed=str(replay.decision_completeness),
                required=f">={thresholds.min_decision_completeness}",
            ),
            GateCheck(
                name="decision_completeness",
                passed=decisions.completeness >= thresholds.min_decision_completeness,
                observed=str(decisions.completeness),
                required=f">={thresholds.min_decision_completeness}",
            ),
        )


@dataclass(frozen=True, slots=True)
class GateCheck:
    name: str
    passed: bool
    observed: str
    required: str


@dataclass(frozen=True, slots=True)
class EvidenceValidationReport:
    bundle: EvidenceBundle | None
    checks: tuple[GateCheck, ...] = ()
    errors: tuple[str, ...] = ()

    @property
    def passed(self) -> bool:
        return self.bundle is not None and not self.errors and all(
            check.passed for check in self.checks
        )


def _decode_payload(payload: str | bytes | Mapping[str, object]) -> Mapping[str, object]:
    if isinstance(payload, Mapping):
        return payload
    try:
        decoded = json.loads(payload)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("evidence bundle must be valid JSON") from exc
    if not isinstance(decoded, Mapping):
        raise ValueError("evidence bundle JSON must be an object")
    return decoded


def validate_evidence_bundle(
    payload: EvidenceBundle | str | bytes | Mapping[str, object],
) -> EvidenceValidationReport:
    try:
        bundle = payload if isinstance(payload, EvidenceBundle) else EvidenceBundle.model_validate(
            _decode_payload(payload)
        )
    except (ValidationError, ValueError, TypeError) as exc:
        return EvidenceValidationReport(bundle=None, errors=(str(exc),))
    return EvidenceValidationReport(bundle=bundle, checks=bundle.gate_checks())


def load_evidence_bundle(path: str | Path) -> EvidenceValidationReport:
    try:
        payload = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        return EvidenceValidationReport(bundle=None, errors=(str(exc),))
    return validate_evidence_bundle(payload)


__all__ = [
    "AIUsageEvidence",
    "DataProvenance",
    "DecisionEvidence",
    "EvidenceBundle",
    "EvidenceValidationReport",
    "GateCheck",
    "PerformanceEvidence",
    "ReplayEvidence",
    "ShadowGateThresholds",
    "load_evidence_bundle",
    "validate_evidence_bundle",
]

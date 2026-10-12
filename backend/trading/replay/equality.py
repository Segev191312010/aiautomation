"""Exact fingerprint equality for network-free deterministic shadow replay."""
from __future__ import annotations

from dataclasses import dataclass
import re

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from trading.contracts import fingerprint_payload


_SHA_PATTERN = re.compile(r"^[0-9a-f]{7,64}$")
_DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class ReplayFingerprintRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    identity: str = Field(min_length=1, max_length=512)
    fingerprint: str = Field(min_length=64, max_length=64)

    @field_validator("identity")
    @classmethod
    def validate_identity(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("replay fingerprint identity must be non-empty")
        return normalized

    @field_validator("fingerprint")
    @classmethod
    def validate_fingerprint(cls, value: str) -> str:
        if not _DIGEST_PATTERN.fullmatch(value):
            raise ValueError("replay fingerprints must be lowercase SHA-256 digests")
        return value


class ReplayFingerprintSet(BaseModel):
    """Recorded or replayed immutable outputs for one source and dataset."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_sha: str = Field(min_length=7, max_length=64)
    dataset_fingerprint: str = Field(min_length=64, max_length=64)
    decisions: tuple[ReplayFingerprintRecord, ...]
    setups: tuple[ReplayFingerprintRecord, ...]
    risks: tuple[ReplayFingerprintRecord, ...]
    fills: tuple[ReplayFingerprintRecord, ...]

    @field_validator("source_sha")
    @classmethod
    def validate_source_sha(cls, value: str) -> str:
        if not _SHA_PATTERN.fullmatch(value):
            raise ValueError("replay source SHA must be lowercase hexadecimal")
        return value

    @field_validator("dataset_fingerprint")
    @classmethod
    def validate_dataset_fingerprint(cls, value: str) -> str:
        if not _DIGEST_PATTERN.fullmatch(value):
            raise ValueError("replay dataset fingerprint must be a SHA-256 digest")
        return value

    @model_validator(mode="after")
    def validate_unique_identities(self) -> "ReplayFingerprintSet":
        for category in ("decisions", "setups", "risks", "fills"):
            records = getattr(self, category)
            identities = [record.identity for record in records]
            if len(identities) != len(set(identities)):
                raise ValueError(f"replay {category} identities must be unique")
        return self

    def fingerprint(self) -> str:
        return fingerprint_payload(self)


@dataclass(frozen=True, slots=True)
class ReplayMismatch:
    category: str
    identity: str
    reason: str


@dataclass(frozen=True, slots=True)
class ReplayEqualityReport:
    recorded_fingerprint: str
    replayed_fingerprint: str
    mismatches: tuple[ReplayMismatch, ...]

    @property
    def passed(self) -> bool:
        return not self.mismatches

    def fingerprint(self) -> str:
        return fingerprint_payload(
            {
                "recorded_fingerprint": self.recorded_fingerprint,
                "replayed_fingerprint": self.replayed_fingerprint,
                "mismatches": [
                    {
                        "category": mismatch.category,
                        "identity": mismatch.identity,
                        "reason": mismatch.reason,
                    }
                    for mismatch in self.mismatches
                ],
                "passed": self.passed,
            }
        )


def _compare_category(
    *,
    category: str,
    recorded: tuple[ReplayFingerprintRecord, ...],
    replayed: tuple[ReplayFingerprintRecord, ...],
) -> tuple[ReplayMismatch, ...]:
    recorded_map = {record.identity: record.fingerprint for record in recorded}
    replayed_map = {record.identity: record.fingerprint for record in replayed}
    mismatches: list[ReplayMismatch] = []
    for identity in sorted(recorded_map.keys() | replayed_map.keys()):
        if identity not in replayed_map:
            reason = "missing_from_replay"
        elif identity not in recorded_map:
            reason = "unexpected_in_replay"
        elif recorded_map[identity] != replayed_map[identity]:
            reason = "fingerprint_mismatch"
        else:
            continue
        mismatches.append(
            ReplayMismatch(category=category, identity=identity, reason=reason)
        )
    return tuple(mismatches)


def compare_replay_fingerprints(
    *,
    recorded: ReplayFingerprintSet,
    replayed: ReplayFingerprintSet,
) -> ReplayEqualityReport:
    """Require exact source, dataset, decision, setup, risk, and fill equality."""
    mismatches: list[ReplayMismatch] = []
    if recorded.source_sha != replayed.source_sha:
        mismatches.append(
            ReplayMismatch(category="metadata", identity="source_sha", reason="mismatch")
        )
    if recorded.dataset_fingerprint != replayed.dataset_fingerprint:
        mismatches.append(
            ReplayMismatch(
                category="metadata",
                identity="dataset_fingerprint",
                reason="mismatch",
            )
        )
    for category in ("decisions", "setups", "risks", "fills"):
        mismatches.extend(
            _compare_category(
                category=category,
                recorded=getattr(recorded, category),
                replayed=getattr(replayed, category),
            )
        )
    return ReplayEqualityReport(
        recorded_fingerprint=recorded.fingerprint(),
        replayed_fingerprint=replayed.fingerprint(),
        mismatches=tuple(mismatches),
    )


__all__ = [
    "ReplayEqualityReport",
    "ReplayFingerprintRecord",
    "ReplayFingerprintSet",
    "ReplayMismatch",
    "compare_replay_fingerprints",
]

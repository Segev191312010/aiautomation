from __future__ import annotations

import pytest
from pydantic import ValidationError

from trading.replay import (
    ReplayFingerprintRecord,
    ReplayFingerprintSet,
    compare_replay_fingerprints,
)


def _record(identity: str, character: str) -> ReplayFingerprintRecord:
    return ReplayFingerprintRecord(identity=identity, fingerprint=character * 64)


def _fingerprints() -> ReplayFingerprintSet:
    return ReplayFingerprintSet(
        source_sha="abcdef1",
        dataset_fingerprint="a" * 64,
        decisions=(_record("candidate-1", "b"),),
        setups=(_record("candidate-1", "c"),),
        risks=(_record("candidate-1", "d"),),
        fills=(_record("candidate-1:entry", "e"), _record("candidate-1:exit", "f")),
    )


def test_replay_requires_exact_fingerprint_equality() -> None:
    recorded = _fingerprints()
    replayed = ReplayFingerprintSet.model_validate(recorded.model_dump())
    report = compare_replay_fingerprints(recorded=recorded, replayed=replayed)
    assert report.passed
    assert report.mismatches == ()
    assert len(report.fingerprint()) == 64


def test_replay_reports_changed_missing_and_unexpected_records() -> None:
    recorded = _fingerprints()
    replayed = recorded.model_copy(
        update={
            "source_sha": "1234567",
            "decisions": (_record("candidate-1", "9"),),
            "setups": (),
            "fills": (*recorded.fills, _record("candidate-2:entry", "1")),
        }
    )
    report = compare_replay_fingerprints(recorded=recorded, replayed=replayed)
    assert not report.passed
    observed = {(item.category, item.identity, item.reason) for item in report.mismatches}
    assert ("metadata", "source_sha", "mismatch") in observed
    assert ("decisions", "candidate-1", "fingerprint_mismatch") in observed
    assert ("setups", "candidate-1", "missing_from_replay") in observed
    assert ("fills", "candidate-2:entry", "unexpected_in_replay") in observed


def test_replay_rejects_duplicate_identities_and_invalid_digests() -> None:
    record = _record("candidate-1", "a")
    with pytest.raises(ValidationError, match="unique"):
        ReplayFingerprintSet(
            source_sha="abcdef1",
            dataset_fingerprint="b" * 64,
            decisions=(record, record),
            setups=(),
            risks=(),
            fills=(),
        )
    with pytest.raises(ValidationError, match="SHA-256"):
        ReplayFingerprintRecord(identity="candidate-1", fingerprint="Z" * 64)

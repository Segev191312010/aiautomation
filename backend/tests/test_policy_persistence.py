from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from trading.contracts import RiskPolicy, fingerprint_payload
from trading.policies import (
    CandidateSelectionPolicy,
    ExecutionPolicy,
    OpeningRangeBreakoutPolicy,
    create_policy,
    get_policy,
    list_policy_versions,
    publish_policy,
)
from trading.policies.repository import PolicyVersion

HASH = "a" * 64


def _candidate_policy(*, maximum_candidates: int = 50) -> CandidateSelectionPolicy:
    return CandidateSelectionPolicy(
        scanner_policy_id="hot-by-volume",
        scanner_policy_version=1,
        scanner_policy_hash=HASH,
        maximum_candidates=maximum_candidates,
    )


def test_policies_are_frozen_and_keep_one_risk_authority():
    candidate = _candidate_policy()
    breakout = OpeningRangeBreakoutPolicy()
    execution = ExecutionPolicy()
    risk = RiskPolicy()

    assert candidate.fingerprint() == fingerprint_payload(candidate)
    assert breakout.fingerprint() == fingerprint_payload(breakout)
    assert execution.fingerprint() == fingerprint_payload(execution)
    assert risk.fingerprint() == fingerprint_payload(risk)
    assert risk.min_reward_to_risk == Decimal("2")
    assert "paper" not in execution.allowed_modes

    with pytest.raises(ValidationError):
        candidate.maximum_candidates = 10
    with pytest.raises(ValidationError, match="09:30"):
        OpeningRangeBreakoutPolicy(opening_range_start="09:35")
    with pytest.raises(ValidationError):
        ExecutionPolicy.model_validate({"allowed_modes": ["paper"]})


def test_policy_version_rejects_wrong_type_or_content_hash():
    policy = _candidate_policy()
    now = datetime.now(timezone.utc)

    with pytest.raises(ValidationError, match="content hash"):
        PolicyVersion(
            policy_id="candidate",
            version=1,
            owner_id="owner",
            policy_type="candidate_selection",
            content_hash=HASH,
            policy=policy,
            created_at=now,
        )
    with pytest.raises(ValidationError, match="policy type"):
        PolicyVersion(
            policy_id="candidate",
            version=1,
            owner_id="owner",
            policy_type="execution",
            content_hash=policy.fingerprint(),
            policy=policy,
            created_at=now,
        )


@pytest.mark.anyio
async def test_policy_repository_publishes_immutable_versions_with_cas(tmp_path, monkeypatch):
    from config import cfg
    from db.core import init_db

    monkeypatch.setattr(cfg, "DB_PATH", str(tmp_path / "policies.db"))
    await init_db()

    first = await create_policy(
        policy_id="candidate-selection",
        owner_id="owner-a",
        policy=_candidate_policy(),
    )
    second = await publish_policy(
        policy_id="candidate-selection",
        owner_id="owner-a",
        policy=_candidate_policy(maximum_candidates=25),
        base_version=1,
    )
    assert first.version == 1
    assert second.version == 2
    assert second.content_hash != first.content_hash

    latest = await get_policy(policy_id="candidate-selection", owner_id="owner-a")
    version_one = await get_policy(
        policy_id="candidate-selection",
        owner_id="owner-a",
        version=1,
    )
    versions = await list_policy_versions(
        policy_id="candidate-selection",
        owner_id="owner-a",
    )
    assert latest is not None and latest.version == 2
    assert version_one is not None and version_one.policy == first.policy
    assert [version.version for version in versions] == [1, 2]

    with pytest.raises(ValueError, match="stale"):
        await publish_policy(
            policy_id="candidate-selection",
            owner_id="owner-a",
            policy=_candidate_policy(maximum_candidates=20),
            base_version=1,
        )


@pytest.mark.anyio
async def test_policy_repository_preserves_type_and_database_immutability(tmp_path, monkeypatch):
    from config import cfg
    from db.core import init_db, get_db

    monkeypatch.setattr(cfg, "DB_PATH", str(tmp_path / "policies.db"))
    await init_db()
    execution = await create_policy(
        policy_id="execution",
        owner_id="owner-a",
        policy=ExecutionPolicy(),
    )
    loaded = await get_policy(policy_id="execution", owner_id="owner-a")
    assert loaded is not None
    assert isinstance(loaded.policy, ExecutionPolicy)
    assert loaded.content_hash == loaded.policy.fingerprint()

    with pytest.raises(ValueError, match="policy type"):
        await publish_policy(
            policy_id="execution",
            owner_id="owner-a",
            policy=_candidate_policy(),
            base_version=execution.version,
        )

    async with get_db() as database:
        with pytest.raises(Exception, match="immutable"):
            await database.execute(
                "UPDATE autotrader_policy_versions SET policy_json='{}' WHERE policy_id='execution'"
            )

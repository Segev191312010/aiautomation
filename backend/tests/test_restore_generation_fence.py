"""Restore-generation contract checks (design-only, fail-closed)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
DESIGN = ROOT / "docs/release-evidence/manifests/canary-fence-design-v1.json"


def _design() -> dict:
    return json.loads(DESIGN.read_text(encoding="utf-8"))


def test_generation_is_monotonic_and_external() -> None:
    design = _design()
    store = design["store"]
    fence = design["tables"]["restore_fence"]
    assert store["separate_from_trading_db"] is True
    assert store["independent_failure_domain"] is True
    assert "increase only" in fence["generation_rule"]


def test_restore_replay_is_recovery_only() -> None:
    restore = _design()["restore_protocol"]
    assert restore["raw_db_restore_authority"] is False
    assert "invalidate old authorizations" in restore["generation_mismatch"]
    assert "newly signed authorization" in restore["generation_mismatch"]


@pytest.mark.skip(
    reason=(
        "restore-replay execution requires the reviewed external fence and "
        "approved restore harness; no runtime authority is implemented"
    )
)
def test_restore_replay_cannot_consume_previous_authorization() -> None:
    """Placeholder for the mandatory adversarial restore-replay test."""
    raise AssertionError("authorized restore-generation harness is not present")


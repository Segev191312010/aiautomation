"""Offline C9 contract checks; no broker or fence-store authority.

The live conformance cases are intentionally gated until the signed C9 review
and an approved harness are supplied.  These checks only pin the design
contract, so they are safe to run in ordinary CI.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
DESIGN = ROOT / "docs/release-evidence/manifests/canary-fence-design-v1.json"


def _design() -> dict:
    return json.loads(DESIGN.read_text(encoding="utf-8"))


def test_design_is_explicitly_non_authorizing() -> None:
    design = _design()
    assert design["status"] == "DESIGN_ONLY_NO_RUNTIME_AUTHORITY"
    assert design["failure_rules"]["live_trading_authority_granted"] is False
    assert design["store"]["availability_policy"] == "unavailable_is_fail_closed"


def test_consume_contract_is_serializable_and_single_transition() -> None:
    consume = _design()["operations"]["consume"]
    assert consume["isolation"] == "SERIALIZABLE"
    assert "FOR UPDATE" in consume["lock"]
    assert consume["transition"] == "UNUSED -> CONSUMED_BY"
    assert "single transaction" in consume["commit"]
    assert "never mutates" in consume["idempotency"]


def test_restore_contract_invalidates_old_authorizations() -> None:
    design = _design()
    restore = design["restore_protocol"]
    order = restore["required_order"]
    assert order.index("bump_restore_generation") < order.index("replace_or_open_trading_db")
    assert "RECOVERY_ONLY" in restore["generation_mismatch"]
    assert "no ARM" in restore["fence_unreachable"]


@pytest.mark.skip(
    reason=(
        "K01-K17 requires signed C9 design review/owner acceptance and an "
        "approved persistent fake-broker + external-fence harness; this "
        "repository currently contains design-only evidence"
    )
)
def test_k01_k17_runtime_harness() -> None:
    """Placeholder for the authorized persistent harness, never a fake pass."""
    raise AssertionError("authorized C9 harness is not present")


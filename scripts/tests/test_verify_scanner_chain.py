import copy
import json
from pathlib import Path

import pytest

from scripts.verify_scanner_chain import (
    ScannerChainError,
    validate_canary,
    validate_chain_schema_contract,
    validate_evidence,
    validate_schema_contract,
    validate_hard_limits,
    validate_signed_bindings,
    validate_soak,
    verify_chain,
)


def policy():
    return {
        "schema_version": 1, "policy_id": "scanner-canary-v1-test", "owner": "risk-owner",
        "risk_operator": "risk-operator", "signed_at": "2026-07-20T00:00:00Z", "expires_at": "2026-07-21T00:00:00Z",
        "limits": {"symbols": ["AAPL"], "max_order_quantity": 1, "max_order_notional": 100,
                    "max_daily_notional": 100, "max_entry_orders": 1, "max_entry_adapter_calls": 1,
                    "max_cumulative_filled_quantity": 1, "max_cumulative_filled_notional": 100,
                    "max_open_positions": 1, "daily_loss_limit": 10, "no_short": True,
                    "cancel_timeout_seconds": 10, "observation_seconds": 60, "max_rollback_seconds": 30},
        "runtime": {"workers": 1, "sim_mode": True, "claude_worker": False, "claude_live": False,
                    "tv_write_route": False, "mcp_order_tool": False},
    }


def soak():
    return {"schema_version": 1, "protocol_id": "scanner-soak-v1", "eligible_sessions": 15,
            "minimum_eligible_decisions": 100, "max_failures": 0, "max_duplicates": 0,
            "max_expiries": 0, "max_orphans": 0, "max_reconciliation_mismatches": 0, "signed": True}


def schema():
    return json.loads(Path("docs/release-evidence/protocols/scanner-canary-policy-schema-v1.json").read_text())


def hard_limits():
    return json.loads(Path("docs/release-evidence/manifests/canary-hard-limits-v1.json").read_text())


def chain_schema():
    return json.loads(Path("docs/release-evidence/schemas/scanner-chain-v1.schema.json").read_text())


def write_pre_t_artifacts(root: Path) -> None:
    protocol_root = root / "docs/release-evidence/protocols"
    manifest_root = root / "docs/release-evidence/manifests"
    schema_root = root / "docs/release-evidence/schemas"
    protocol_root.mkdir(parents=True)
    manifest_root.mkdir(parents=True)
    schema_root.mkdir(parents=True)
    (protocol_root / "scanner-canary-policy-schema-v1.json").write_text(json.dumps(schema()))
    (manifest_root / "canary-hard-limits-v1.json").write_text(json.dumps(hard_limits()))
    (schema_root / "scanner-chain-v1.schema.json").write_text(json.dumps(chain_schema()))


def test_hard_limits_are_non_authorizing_and_immutable():
    validate_hard_limits(hard_limits())
    candidate = hard_limits()
    candidate["immutable"]["max_entry_orders"] = 2
    with pytest.raises(ScannerChainError, match="ceilings drifted"):
        validate_hard_limits(candidate)


def test_schema_contract_preserves_immutable_safety_envelope():
    validate_schema_contract(schema())


def test_chain_schema_preserves_closed_non_authorizing_envelope():
    validate_chain_schema_contract(chain_schema())


def test_pre_t_validates_only_frozen_schemas_and_hard_limits(tmp_path: Path):
    write_pre_t_artifacts(tmp_path)
    result = verify_chain(tmp_path, phase="pre-t")
    assert set(result) == {"schema_hash", "chain_schema_hash", "hard_limits_hash"}
    assert all(len(value) == 64 for value in result.values())


@pytest.mark.parametrize(
    "relative_path",
    [
        "docs/release-evidence/protocols/scanner-soak-v1.json",
        "docs/release-evidence/protocols/scanner-soak-v1.md",
        "docs/release-evidence/protocols/scanner-soak-v1.sig.json",
        "docs/release-evidence/protocols/scanner-canary-v1.json",
        "docs/release-evidence/protocols/scanner-canary-v1.md",
        "docs/release-evidence/protocols/scanner-canary-v1.sig.json",
    ],
)
def test_pre_t_rejects_reserved_post_t_instances(tmp_path: Path, relative_path: str):
    write_pre_t_artifacts(tmp_path)
    instance = tmp_path / relative_path
    instance.write_text("{}")
    with pytest.raises(ScannerChainError, match="post-T scanner instance"):
        verify_chain(tmp_path, phase="pre-t")


def test_pre_t_rejects_post_t_evidence_argument(tmp_path: Path):
    write_pre_t_artifacts(tmp_path)
    with pytest.raises(ScannerChainError, match="evidence is forbidden"):
        verify_chain(tmp_path, phase="pre-t", evidence={})


@pytest.mark.parametrize(
    "mutator,match",
    [
        (lambda value: value["properties"]["limits"]["properties"]["max_entry_orders"].update(const=2), "max_entry_orders"),
        (lambda value: value["properties"]["runtime"]["properties"]["workers"].update(const=2), "workers"),
        (lambda value: value["properties"]["runtime"]["properties"]["claude_live"].update(const=True), "claude_live"),
        (lambda value: value.update(additionalProperties=True), "closed object"),
    ],
)
def test_schema_contract_rejects_safety_weakening(mutator, match):
    candidate = schema()
    mutator(candidate)
    with pytest.raises(ScannerChainError, match=match):
        validate_schema_contract(candidate)


def test_valid_artifacts_hash():
    assert len(validate_canary(policy())) == 64
    assert len(validate_soak(soak())) == 64


@pytest.mark.parametrize("field,value", [("max_entry_orders", 2), ("max_entry_adapter_calls", 0), ("no_short", False)])
def test_non_overridable_limits_fail_closed(field, value):
    candidate = policy()
    target = candidate["limits"]
    target[field] = value
    with pytest.raises(ScannerChainError):
        validate_canary(candidate)


def test_evidence_requires_closed_single_entry():
    evidence = {"reserved_intents": 1, "entry_orders": 1, "entry_adapter_calls": 1,
                "unauthorized_orders": 0, "entry_authority": "OFF", "working_canary_orders": 0,
                "unresolved_interventions": 0, "final_exposure": "FLAT",
                "broker_entry_order_terminal": True, "execution_ids_unique": True}
    validate_evidence(evidence)
    bad = copy.deepcopy(evidence)
    bad["entry_orders"] = 2
    with pytest.raises(ScannerChainError):
        validate_evidence(bad)


@pytest.mark.parametrize("field", ["owner", "risk_operator"])
def test_policy_identity_must_be_non_empty(field):
    candidate = policy()
    candidate[field] = "   "
    with pytest.raises(ScannerChainError, match="non-empty identity"):
        validate_canary(candidate)


def test_policy_must_explicitly_remain_simulation_only():
    candidate = policy()
    candidate["runtime"]["sim_mode"] = False
    with pytest.raises(ScannerChainError, match="simulation-only"):
        validate_canary(candidate)


def test_unknown_phase_fails_closed(tmp_path: Path):
    with pytest.raises(ScannerChainError, match="unsupported scanner-chain phase"):
        verify_chain(tmp_path, phase="not-a-phase")


def test_signed_bindings_require_verified_signature_and_exact_hashes():
    from datetime import datetime, timezone
    from scripts.verify_scanner_chain import sha256_json

    candidate_policy = policy()
    candidate_soak = soak()
    candidate_soak.update({"owner": "soak-owner", "risk_operator": "soak-risk",
                           "signed_at": "2026-07-20T00:00:00Z", "expires_at": "2026-07-22T00:00:00Z"})
    kwargs = dict(schema=schema(), policy=candidate_policy, soak=candidate_soak,
                  schema_hash=sha256_json(schema()),
                  expected_policy_hash=sha256_json(candidate_policy),
                  expected_soak_hash=sha256_json(candidate_soak),
                  now=datetime(2026, 7, 20, 12, tzinfo=timezone.utc))
    with pytest.raises(ScannerChainError, match="signature is not verified"):
        validate_signed_bindings(**kwargs, signature_verified=False)
    validate_signed_bindings(**kwargs, signature_verified=True)
    kwargs["expected_policy_hash"] = "0" * 64
    with pytest.raises(ScannerChainError, match="CANARY_POLICY_HASH"):
        validate_signed_bindings(**kwargs, signature_verified=True)

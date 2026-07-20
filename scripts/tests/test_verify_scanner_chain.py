import copy
import json
from pathlib import Path

import pytest

from scripts.verify_scanner_chain import ScannerChainError, validate_canary, validate_evidence, validate_soak


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

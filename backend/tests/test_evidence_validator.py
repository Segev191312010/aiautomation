from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import json

from trading.evidence import EvidenceBundle, load_evidence_bundle, validate_evidence_bundle

SOURCE_SHA = "a" * 40
STRATEGY_HASH = "b" * 64
DATASET_HASH = "c" * 64


def _payload() -> dict[str, object]:
    return {
        "schema_version": 1,
        "source_sha": SOURCE_SHA,
        "generated_at": datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc).isoformat(),
        "strategy_id": "orb-v1",
        "strategy_version": 1,
        "strategy_content_hash": STRATEGY_HASH,
        "policy_hashes": ["d" * 64, "e" * 64],
        "data_provenance": [
            {
                "source_sha": SOURCE_SHA,
                "dataset_id": "paper-shadow-60-sessions",
                "dataset_fingerprint": DATASET_HASH,
                "provider": "ibkr",
                "session_count": 60,
                "completed_bar_count": 72000,
                "candidate_count": 100,
                "captured_at": datetime(
                    2026, 8, 27, 11, 0, tzinfo=timezone.utc
                ).isoformat(),
            }
        ],
        "replay": {
            "source_sha": SOURCE_SHA,
            "dataset_fingerprint": DATASET_HASH,
            "evaluated_sessions": 60,
            "candidate_count": 100,
            "terminal_decisions": 100,
            "replayed_decisions": 100,
            "replay_equality": True,
        },
        "decisions": {
            "candidate_count": 100,
            "terminal_decisions": 100,
            "outcome_counts": {
                "shadow_accepted": 30,
                "ai_abstained": 70,
            },
        },
        "ai_usage": {
            "call_count": 20,
            "total_cost_usd": "5",
            "average_latency_ms": "125",
            "p95_latency_ms": "250",
            "max_session_calls": 20,
            "max_session_cost_usd": "5",
        },
        "performance": {
            "rth_sessions": 10,
            "modeled_fills": 30,
            "profit_factor": "1.20",
            "max_drawdown_pct": "3",
            "rule_only_expectancy_r": "0.20",
            "ai_expectancy_r": "0.19",
        },
        "thresholds": {
            "min_rth_sessions": 10,
            "min_terminal_decisions": 100,
            "min_modeled_fills": 30,
            "min_profit_factor": "1.20",
            "max_drawdown_pct": "3",
            "min_ai_expectancy_ratio": "0.95",
            "require_replay_equality": True,
            "min_decision_completeness": "1",
        },
    }


def test_valid_bundle_passes_all_shadow_gate_checks():
    bundle = EvidenceBundle.model_validate(_payload())
    report = validate_evidence_bundle(bundle)

    assert report.passed
    assert not report.errors
    assert {check.name for check in report.checks} == {
        "rth_sessions",
        "terminal_decisions",
        "modeled_fills",
        "positive_net_expectancy",
        "profit_factor",
        "maximum_drawdown_pct",
        "ai_expectancy_vs_rule_only",
        "replay_equality",
        "replay_decision_completeness",
        "decision_completeness",
    }
    assert all(check.passed for check in report.checks)


def test_validator_rejects_unbound_or_mismatched_provenance():
    mismatched = _payload()
    mismatched["replay"] = {
        **mismatched["replay"],
        "source_sha": "f" * 40,
    }
    report = validate_evidence_bundle(mismatched)

    assert not report.passed
    assert any("source SHA" in error for error in report.errors)


def test_validator_rejects_lowered_declared_thresholds():
    lowered = _payload()
    lowered["thresholds"] = {
        **lowered["thresholds"],
        "min_terminal_decisions": 1,
    }
    report = validate_evidence_bundle(lowered)

    assert not report.passed
    assert any("min_terminal_decisions" in error for error in report.errors)


def test_validator_reports_failed_gate_metrics_without_unlocking_anything():
    failed = _payload()
    failed["performance"] = {
        **failed["performance"],
        "rth_sessions": 9,
        "modeled_fills": 29,
        "profit_factor": "1.1",
        "max_drawdown_pct": "3.1",
        "ai_expectancy_r": "0.10",
    }
    failed["replay"] = {
        **failed["replay"],
        "replay_equality": False,
        "terminal_decisions": 99,
        "replayed_decisions": 99,
    }
    failed["decisions"] = {
        **failed["decisions"],
        "terminal_decisions": 99,
        "outcome_counts": {"shadow_accepted": 29, "ai_abstained": 70},
    }
    report = validate_evidence_bundle(failed)

    assert not report.passed
    checks = {check.name: check.passed for check in report.checks}
    assert checks["rth_sessions"] is False
    assert checks["terminal_decisions"] is False
    assert checks["modeled_fills"] is False
    assert checks["profit_factor"] is False
    assert checks["maximum_drawdown_pct"] is False
    assert checks["replay_equality"] is False
    assert checks["replay_decision_completeness"] is False
    assert checks["decision_completeness"] is False


def test_validator_rejects_unknown_outcomes_and_ai_session_overages():
    unknown = _payload()
    unknown["decisions"] = {
        **unknown["decisions"],
        "outcome_counts": {"shadow_accepted": 30, "not_a_terminal_state": 70},
    }
    assert not validate_evidence_bundle(unknown).passed

    overage = _payload()
    overage["ai_usage"] = {
        **overage["ai_usage"],
        "max_session_calls": 21,
    }
    assert not validate_evidence_bundle(overage).passed


def test_validator_loads_json_file_and_preserves_decimal_metrics(tmp_path):
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps(_payload()), encoding="utf-8")

    report = load_evidence_bundle(path)

    assert report.passed
    assert report.bundle is not None
    assert report.bundle.performance.profit_factor == Decimal("1.20")

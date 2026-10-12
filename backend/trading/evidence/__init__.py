"""Machine-checkable evidence validation for Autotrader V1."""

from .modelled import (
    ModelledEntryResult,
    SHADOW_SLIPPAGE_BPS,
    ShadowFillModelError,
    model_entry_fill,
    model_session_close_fill,
    model_triggered_exit_fill,
    net_pnl,
    round_down_to_tick,
    round_up_to_tick,
    triggered_exit_reason,
)

from .validator import (
    AIUsageEvidence,
    DataProvenance,
    DecisionEvidence,
    EvidenceBundle,
    EvidenceValidationReport,
    GateCheck,
    PerformanceEvidence,
    ReplayEvidence,
    ShadowGateThresholds,
    load_evidence_bundle,
    validate_evidence_bundle,
)

__all__ = [
    "AIUsageEvidence",
    "DataProvenance",
    "DecisionEvidence",
    "EvidenceBundle",
    "EvidenceValidationReport",
    "GateCheck",
    "PerformanceEvidence",
    "ReplayEvidence",
    "ModelledEntryResult",
    "SHADOW_SLIPPAGE_BPS",
    "ShadowFillModelError",
    "ShadowGateThresholds",
    "load_evidence_bundle",
    "model_entry_fill",
    "model_session_close_fill",
    "model_triggered_exit_fill",
    "net_pnl",
    "round_down_to_tick",
    "round_up_to_tick",
    "triggered_exit_reason",
    "validate_evidence_bundle",
]

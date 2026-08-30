"""Read-only operator-console projections for Autotrader V1.

This module deliberately accepts already-authorized domain values and turns
them into immutable display models.  It performs no persistence, networking,
authority promotion, order submission, or policy mutation.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal, Sequence

from trading.authority import EngineAuthority
from trading.contracts import (
    AIReview,
    AuthorityReceipt,
    Candidate,
    Decision,
    PortfolioSnapshot,
    RiskApproval,
    RuleSetup,
    Run,
)
from trading.replay import ReplayResult
from trading.scheduler.lease import LeaseClaim


def _utc(value: datetime | None = None) -> datetime:
    observed = value or datetime.now(timezone.utc)
    if observed.tzinfo is None or observed.utcoffset() is None:
        raise ValueError("console timestamps must be timezone-aware")
    return observed.astimezone(timezone.utc)


@dataclass(frozen=True, slots=True)
class SchedulerHealthView:
    status: Literal["healthy", "degraded", "stopped"]
    lease_name: str | None
    holder_id: str | None
    fencing_token: int | None
    expires_at: datetime | None
    reason: str


@dataclass(frozen=True, slots=True)
class CandidateView:
    candidate_id: str
    scanner_run_id: str
    symbol: str
    con_id: int
    rank: int
    admission_status: str
    rejection_reason: str | None


@dataclass(frozen=True, slots=True)
class RuleSetupView:
    setup_fingerprint: str
    candidate_id: str
    symbol: str
    opening_range_high: Decimal
    opening_range_low: Decimal
    atr: Decimal
    entry_limit: Decimal
    stop_price: Decimal
    target_price: Decimal
    detected_at: datetime


@dataclass(frozen=True, slots=True)
class AIReviewView:
    setup_fingerprint: str
    decision: Literal["PROPOSE", "ABSTAIN"]
    confidence: Decimal | None
    model_id: str
    prompt_version: str
    latency_ms: int
    cost_usd: Decimal
    failure_reason: str | None


@dataclass(frozen=True, slots=True)
class RiskOutcomeView:
    approved: bool
    codes: tuple[str, ...]
    setup_fingerprint: str
    policy_fingerprint: str
    portfolio_fingerprint: str
    quantity: int | None
    risk_amount: Decimal | None
    notional_amount: Decimal | None
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class EvidenceProgressView:
    source_sha: str | None
    replay_fingerprint: str | None
    completed_sessions: int
    required_sessions: int
    terminal_decisions: int
    required_decisions: int
    modeled_fills: int
    required_fills: int
    status: Literal["not_started", "in_progress", "complete"]
    missing: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AuthorityStatusView:
    enabled_modes: tuple[str, ...]
    receipt_status: Literal["not_required", "missing", "valid", "expired", "build_mismatch"]
    build_sha: str | None
    receipt_expires_at: datetime | None
    paper_authorized: bool
    live_authorized: bool


@dataclass(frozen=True, slots=True)
class KillStateView:
    active: bool
    reason: str | None
    source: str


@dataclass(frozen=True, slots=True)
class ConsoleSnapshot:
    generated_at: datetime
    run: Run | None
    scheduler: SchedulerHealthView
    candidates: tuple[CandidateView, ...]
    rule_setup: RuleSetupView | None
    ai_review: AIReviewView | None
    risk: RiskOutcomeView | None
    evidence: EvidenceProgressView
    authority: AuthorityStatusView
    kill_state: KillStateView
    decision_outcomes: tuple[str, ...]


def project_scheduler_health(
    lease: LeaseClaim | None, *, now: datetime | None = None
) -> SchedulerHealthView:
    observed = _utc(now)
    if lease is None:
        return SchedulerHealthView(
            status="stopped",
            lease_name=None,
            holder_id=None,
            fencing_token=None,
            expires_at=None,
            reason="no scheduler lease",
        )
    if lease.expires_at <= observed:
        return SchedulerHealthView(
            status="degraded",
            lease_name=lease.name,
            holder_id=lease.holder_id,
            fencing_token=lease.fencing_token,
            expires_at=lease.expires_at,
            reason="scheduler lease expired",
        )
    return SchedulerHealthView(
        status="healthy",
        lease_name=lease.name,
        holder_id=lease.holder_id,
        fencing_token=lease.fencing_token,
        expires_at=lease.expires_at,
        reason="active scheduler lease",
    )


def project_candidate(candidate: Candidate) -> CandidateView:
    return CandidateView(
        candidate_id=candidate.id,
        scanner_run_id=candidate.scanner_run_id,
        symbol=candidate.symbol,
        con_id=candidate.con_id,
        rank=candidate.rank,
        admission_status=candidate.admission_status,
        rejection_reason=candidate.rejection_reason,
    )


def project_candidates(candidates: Sequence[Candidate]) -> tuple[CandidateView, ...]:
    return tuple(project_candidate(candidate) for candidate in candidates)


def project_rule_setup(setup: RuleSetup | None) -> RuleSetupView | None:
    if setup is None:
        return None
    return RuleSetupView(
        setup_fingerprint=setup.fingerprint(),
        candidate_id=setup.candidate_id,
        symbol=setup.symbol,
        opening_range_high=setup.opening_range_high,
        opening_range_low=setup.opening_range_low,
        atr=setup.atr,
        entry_limit=setup.entry_limit,
        stop_price=setup.stop_price,
        target_price=setup.target_price,
        detected_at=setup.detected_at,
    )


def project_ai_review(review: AIReview | None) -> AIReviewView | None:
    if review is None:
        return None
    return AIReviewView(
        setup_fingerprint=review.setup_fingerprint,
        decision=review.decision,
        confidence=review.confidence,
        model_id=review.model_id,
        prompt_version=review.prompt_version,
        latency_ms=review.latency_ms,
        cost_usd=review.cost_usd,
        failure_reason=review.failure_reason,
    )


def project_risk(approval: RiskApproval | None) -> RiskOutcomeView | None:
    if approval is None:
        return None
    intent = approval.intent
    return RiskOutcomeView(
        approved=approval.approved,
        codes=approval.codes,
        setup_fingerprint=approval.setup_fingerprint,
        policy_fingerprint=approval.policy_fingerprint,
        portfolio_fingerprint=approval.portfolio_fingerprint,
        quantity=intent.quantity if intent else None,
        risk_amount=intent.risk_amount if intent else None,
        notional_amount=intent.notional_amount if intent else None,
        expires_at=approval.expires_at,
    )


def project_evidence(
    *,
    replay: ReplayResult | None,
    completed_sessions: int = 0,
    required_sessions: int = 10,
    terminal_decisions: int = 0,
    required_decisions: int = 100,
    modeled_fills: int = 0,
    required_fills: int = 30,
) -> EvidenceProgressView:
    values = {
        "sessions": (completed_sessions, required_sessions),
        "terminal decisions": (terminal_decisions, required_decisions),
        "modeled fills": (modeled_fills, required_fills),
    }
    missing = tuple(
        f"{name}: {current}/{required}"
        for name, (current, required) in values.items()
        if current < required
    )
    status: Literal["not_started", "in_progress", "complete"]
    if not replay and completed_sessions == 0 and terminal_decisions == 0 and modeled_fills == 0:
        status = "not_started"
    elif missing:
        status = "in_progress"
    else:
        status = "complete"
    return EvidenceProgressView(
        source_sha=replay.source_sha if replay else None,
        replay_fingerprint=replay.dataset_fingerprint if replay else None,
        completed_sessions=completed_sessions,
        required_sessions=required_sessions,
        terminal_decisions=terminal_decisions,
        required_decisions=required_decisions,
        modeled_fills=modeled_fills,
        required_fills=required_fills,
        status=status,
        missing=missing,
    )


def project_authority(
    authority: EngineAuthority,
    receipt: AuthorityReceipt | None,
    *,
    expected_build_sha: str | None = None,
    now: datetime | None = None,
) -> AuthorityStatusView:
    observed = _utc(now)
    enabled_modes = tuple(sorted(mode.value for mode in authority.enabled_modes))
    if receipt is None:
        receipt_status: Literal["not_required", "missing", "valid", "expired", "build_mismatch"] = "missing"
        build_sha = None
        expires_at = None
    elif receipt.expires_at <= observed:
        receipt_status = "expired"
        build_sha = receipt.build_sha
        expires_at = receipt.expires_at
    elif expected_build_sha is not None and receipt.build_sha != expected_build_sha:
        receipt_status = "build_mismatch"
        build_sha = receipt.build_sha
        expires_at = receipt.expires_at
    else:
        receipt_status = "valid"
        build_sha = receipt.build_sha
        expires_at = receipt.expires_at
    paper_authorized = "paper" in enabled_modes and receipt_status == "valid"
    live_authorized = "live" in enabled_modes and receipt_status == "valid"
    return AuthorityStatusView(
        enabled_modes=enabled_modes,
        receipt_status=receipt_status,
        build_sha=build_sha,
        receipt_expires_at=expires_at,
        paper_authorized=paper_authorized,
        live_authorized=live_authorized,
    )


def project_kill_state(*, active: bool, reason: str | None, source: str) -> KillStateView:
    if not source.strip():
        raise ValueError("kill state source must be non-empty")
    return KillStateView(active=active, reason=reason, source=source)


def build_console_snapshot(
    *,
    run: Run | None = None,
    lease: LeaseClaim | None = None,
    candidates: Sequence[Candidate] = (),
    setup: RuleSetup | None = None,
    review: AIReview | None = None,
    risk: RiskApproval | None = None,
    replay: ReplayResult | None = None,
    completed_sessions: int = 0,
    required_sessions: int = 10,
    terminal_decisions: int = 0,
    required_decisions: int = 100,
    modeled_fills: int = 0,
    required_fills: int = 30,
    decisions: Sequence[Decision] = (),
    authority: EngineAuthority | None = None,
    receipt: AuthorityReceipt | None = None,
    expected_build_sha: str | None = None,
    kill_active: bool = False,
    kill_reason: str | None = None,
    kill_source: str = "operator",
    now: datetime | None = None,
) -> ConsoleSnapshot:
    observed = _utc(now)
    current_authority = authority or EngineAuthority()
    return ConsoleSnapshot(
        generated_at=observed,
        run=run,
        scheduler=project_scheduler_health(lease, now=observed),
        candidates=project_candidates(candidates),
        rule_setup=project_rule_setup(setup),
        ai_review=project_ai_review(review),
        risk=project_risk(risk),
        evidence=project_evidence(
            replay=replay,
            completed_sessions=completed_sessions,
            required_sessions=required_sessions,
            terminal_decisions=terminal_decisions,
            required_decisions=required_decisions,
            modeled_fills=modeled_fills,
            required_fills=required_fills,
        ),
        authority=project_authority(
            current_authority,
            receipt,
            expected_build_sha=expected_build_sha,
            now=observed,
        ),
        kill_state=project_kill_state(
            active=kill_active, reason=kill_reason, source=kill_source
        ),
        decision_outcomes=tuple(decision.outcome.value for decision in decisions),
    )


__all__ = [
    "AIReviewView",
    "AuthorityStatusView",
    "CandidateView",
    "ConsoleSnapshot",
    "EvidenceProgressView",
    "KillStateView",
    "RiskOutcomeView",
    "RuleSetupView",
    "SchedulerHealthView",
    "build_console_snapshot",
    "project_ai_review",
    "project_authority",
    "project_candidate",
    "project_candidates",
    "project_evidence",
    "project_kill_state",
    "project_risk",
    "project_rule_setup",
    "project_scheduler_health",
]

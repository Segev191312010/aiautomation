from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from trading.authority import EngineAuthority
from trading.console import (
    build_console_snapshot,
    project_authority,
    project_evidence,
    project_scheduler_health,
)
from trading.contracts import (
    AIReview,
    AuthorityReceipt,
    Candidate,
    Decision,
    DecisionOutcome,
    EngineMode,
    FixedUniverse,
    RiskApproval,
    RuleCondition,
    RuleSetup,
    Run,
    StrategySpec,
)
from trading.replay import ReplayResult
from trading.scheduler.lease import LeaseClaim


HASH = "a" * 64


def _setup() -> RuleSetup:
    return RuleSetup(
        run_id="run-1",
        candidate_id="candidate-1",
        strategy_id="strategy-1",
        strategy_version=1,
        symbol="AAPL",
        opening_range_high="100",
        opening_range_low="98",
        atr="1",
        entry_limit="100.10",
        stop_price="99.10",
        target_price="102.10",
        bar_fingerprints=(HASH,),
        quote_fingerprint=HASH,
        detected_at=datetime(2026, 8, 27, 14, 0, tzinfo=timezone.utc),
    )


def _review(setup: RuleSetup) -> AIReview:
    return AIReview(
        setup_fingerprint=setup.fingerprint(),
        decision="ABSTAIN",
        confidence="0.1",
        rationale="Console fixture abstention.",
        model_id="test-model",
        prompt_version="review-v1",
        response_hash=HASH,
        latency_ms=25,
        cost_usd="0.01",
    )


def _run() -> Run:
    return Run(
        id="run-1",
        owner_id="owner-1",
        source_sha="abcdef1",
        strategy_id="strategy-1",
        strategy_version=1,
        policy_fingerprints={"rules": HASH},
        session_date=date(2026, 8, 27),
        mode=EngineMode.SHADOW,
    )


@pytest.mark.anyio
async def test_console_snapshot_projects_all_required_read_only_surfaces():
    setup = _setup()
    candidate = Candidate(
        id="candidate-1",
        scanner_run_id="scanner-1",
        con_id=101,
        symbol="AAPL",
        primary_exchange="NASDAQ",
        rank=1,
        admission_status="admitted",
    )
    now = datetime(2026, 8, 27, 14, 0, tzinfo=timezone.utc)
    lease = LeaseClaim(
        name="shadow-runner",
        holder_id="worker-1",
        fencing_token=3,
        acquired_at=now - timedelta(minutes=1),
        expires_at=now + timedelta(minutes=1),
    )
    replay = ReplayResult(
        run_id="run-1",
        candidate_id="candidate-1",
        source_sha="abcdef1",
        bars=(),
        features=(),
        dataset_fingerprint=HASH,
    )
    risk = RiskApproval(
        approved=False,
        codes=("daily_loss_limit_reached",),
        setup_fingerprint=setup.fingerprint(),
        ai_review_fingerprint=_review(setup).fingerprint(),
        policy_fingerprint=HASH,
        portfolio_fingerprint=HASH,
        expires_at=now + timedelta(seconds=15),
    )
    decision = Decision(
        run_id="run-1",
        candidate_id="candidate-1",
        strategy_id="strategy-1",
        strategy_version=1,
        decision_key=HASH,
        outcome=DecisionOutcome.RULE_REJECTED,
    )
    snapshot = build_console_snapshot(
        run=_run(),
        lease=lease,
        candidates=(candidate,),
        setup=setup,
        review=_review(setup),
        risk=risk,
        replay=replay,
        completed_sessions=4,
        terminal_decisions=48,
        modeled_fills=12,
        decisions=(decision,),
        authority=EngineAuthority(),
        kill_active=True,
        kill_reason="operator stop",
        now=now,
    )
    assert snapshot.scheduler.status == "healthy"
    assert snapshot.candidates[0].symbol == "AAPL"
    assert snapshot.rule_setup is not None
    assert snapshot.ai_review is not None and snapshot.ai_review.decision == "ABSTAIN"
    assert snapshot.risk is not None and not snapshot.risk.approved
    assert snapshot.evidence.source_sha == "abcdef1"
    assert snapshot.evidence.status == "in_progress"
    assert snapshot.authority.enabled_modes == ("backtest", "shadow")
    assert not snapshot.authority.paper_authorized
    assert snapshot.kill_state.active
    assert snapshot.decision_outcomes == ("rule_rejected",)
    with pytest.raises(FrozenInstanceError):
        snapshot.kill_state.active = False


def test_console_reports_stopped_and_expired_scheduler_leases():
    now = datetime(2026, 8, 27, 14, 0, tzinfo=timezone.utc)
    assert project_scheduler_health(None, now=now).status == "stopped"
    expired = LeaseClaim(
        name="shadow-runner",
        holder_id="worker-1",
        fencing_token=1,
        acquired_at=now - timedelta(minutes=2),
        expires_at=now - timedelta(seconds=1),
    )
    view = project_scheduler_health(expired, now=now)
    assert view.status == "degraded"
    assert "expired" in view.reason


def test_console_authority_never_invents_paper_or_live_access():
    now = datetime(2026, 8, 27, 14, 0, tzinfo=timezone.utc)
    receipt = AuthorityReceipt(
        build_sha="abcdef1",
        evidence_digest=HASH,
        strategy_id="strategy-1",
        strategy_version=1,
        policy_hashes=(HASH,),
        paper_account="DU123",
        client_id=7,
        expires_at=now + timedelta(hours=1),
        owner_approval="owner-approved",
    )
    view = project_authority(EngineAuthority(), receipt, expected_build_sha="abcdef1", now=now)
    assert view.receipt_status == "valid"
    assert not view.paper_authorized
    assert not view.live_authorized
    mismatched = project_authority(
        EngineAuthority(), receipt, expected_build_sha="7654321", now=now
    )
    assert mismatched.receipt_status == "build_mismatch"


def test_evidence_projection_is_explicit_about_missing_gate_inputs():
    view = project_evidence(
        replay=None,
        completed_sessions=10,
        terminal_decisions=100,
        modeled_fills=30,
    )
    assert view.status == "complete"
    assert view.missing == ()
    assert not hasattr(view, "start")
    assert not hasattr(view, "promote")
    assert not hasattr(view, "submit_order")

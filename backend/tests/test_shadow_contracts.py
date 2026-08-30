from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from trading.contracts import (
    AccountSnapshot,
    CommissionPolicy,
    ModelledFill,
    QuoteSnapshot,
    RawAccountPosition,
    RawExecution,
    RawOpenOrder,
    RawScannerResult,
    RuleSetup,
    RuleSetupProvenance,
    ShadowReconciliationReport,
    ShadowRuntimeConfig,
    ShadowSessionReceipt,
    ShadowSessionRecord,
    ShadowSessionState,
)


NOW = datetime(2026, 8, 31, 14, 0, tzinfo=timezone.utc)
HASH = "a" * 64


def _runtime() -> ShadowRuntimeConfig:
    return ShadowRuntimeConfig(
        paper_account_id="DU1234567",
        client_id=17,
        model_id="review-model",
        prompt_id="orb-review-v1",
        calendar_id="XNYS",
        calendar_version="2026.1",
        commission_policy_id="ibkr-fixed",
        commission_policy_version=1,
    )


def test_shadow_runtime_contract_is_fixed_to_local_tws_and_bounded_ai() -> None:
    runtime = _runtime()
    assert runtime.tws_host == "127.0.0.1"
    assert runtime.tws_port == 7497
    assert runtime.max_candidates == runtime.max_ai_calls == 20
    assert runtime.max_ai_cost_usd == Decimal("5")
    assert len(runtime.fingerprint()) == 64

    for update in (
        {"tws_host": "localhost"},
        {"tws_port": 7496},
        {"max_candidates": 21},
        {"max_ai_calls": 21},
        {"max_ai_cost_usd": "5.01"},
    ):
        with pytest.raises(ValidationError):
            ShadowRuntimeConfig.model_validate({**runtime.model_dump(), **update})


def test_shadow_session_record_enforces_terminal_evidence_shape() -> None:
    active = ShadowSessionRecord(
        run_id="run-1",
        source_sha="abcdef1",
        session_date=date(2026, 8, 31),
        state=ShadowSessionState.EVALUATING,
        transitioned_at=NOW,
    )
    assert not active.evidence_eligible

    completed = active.model_copy(
        update={
            "state": ShadowSessionState.COMPLETED,
            "evidence_eligible": True,
            "closed_at": NOW + timedelta(hours=2),
        }
    )
    assert ShadowSessionRecord.model_validate(completed.model_dump()).evidence_eligible
    with pytest.raises(ValidationError, match="reason"):
        ShadowSessionRecord(
            source_sha="abcdef1",
            session_date=date(2026, 8, 31),
            state=ShadowSessionState.BLOCKED,
            transitioned_at=NOW,
            closed_at=NOW,
        )


def test_scanner_and_quote_preserve_raw_rank_and_side_provenance() -> None:
    result = RawScannerResult(
        scanner_run_id="scan-1",
        request_id=9,
        raw_rank=0,
        normalized_rank=1,
        con_id=123,
        symbol="aapl",
        security_type="stk",
        currency="usd",
        exchange="smart",
        primary_exchange="nasdaq",
        received_at=NOW,
    )
    assert result.symbol == "AAPL"
    with pytest.raises(ValidationError, match="raw rank plus one"):
        RawScannerResult.model_validate({**result.model_dump(), "normalized_rank": 2})

    quote = QuoteSnapshot(
        con_id=123,
        bid="100",
        ask="100.01",
        observed_at=NOW,
        bid_observed_at=NOW - timedelta(seconds=2),
        ask_observed_at=NOW - timedelta(seconds=1),
        min_tick="0.01",
        request_id=10,
    )
    assert quote.age_seconds(NOW) == Decimal("2.0")
    assert quote.source == "ibkr" and quote.data_mode == "live"


def test_account_snapshot_keeps_signed_decimal_broker_truth() -> None:
    position = RawAccountPosition(
        paper_account_id="DU1234567",
        con_id=123,
        symbol="AAPL",
        quantity="-2.5",
        market_price="100",
        market_value="-250",
        average_cost="101",
        unrealized_pnl="2.5",
        realized_pnl="-1.25",
        observed_at=NOW,
    )
    order = RawOpenOrder(
        paper_account_id="DU1234567",
        client_id=17,
        order_id=11,
        perm_id=21,
        con_id=123,
        symbol="AAPL",
        action="SELL",
        total_quantity="2.5",
        filled_quantity="1",
        remaining_quantity="1.5",
        order_type="LMT",
        limit_price="100",
        status="Submitted",
        observed_at=NOW,
    )
    execution = RawExecution(
        paper_account_id="DU1234567",
        client_id=17,
        execution_id="exec-1",
        order_id=11,
        perm_id=21,
        con_id=123,
        symbol="AAPL",
        side="SELL",
        quantity="1",
        price="100",
        cumulative_quantity="1",
        average_price="100",
        executed_at=NOW,
    )
    snapshot = AccountSnapshot(
        paper_account_id="DU1234567",
        client_id=17,
        observed_at=NOW,
        net_liquidation="100000",
        available_funds="90000",
        realized_day_pnl="-1.25",
        unrealized_pnl="2.5",
        positions=(position,),
        open_orders=(order,),
        executions=(execution,),
    )
    assert position.quantity == Decimal("-2.5")
    assert order.signed_total_quantity == Decimal("-2.5")
    assert execution.signed_quantity == Decimal("-1")
    assert len(snapshot.fingerprint) == 64

    clear = ShadowReconciliationReport(
        account_fingerprint=snapshot.fingerprint,
        status="clear",
        observed_at=NOW,
    )
    assert clear.status == "clear"
    with pytest.raises(ValidationError, match="findings"):
        ShadowReconciliationReport(
            account_fingerprint=snapshot.fingerprint,
            status="clear",
            unknown_order_ids=(11,),
            observed_at=NOW,
        )


def test_commission_and_modelled_fill_are_versioned_shadow_only_records() -> None:
    policy = CommissionPolicy(
        id="ibkr-fixed",
        version=1,
        effective_from=date(2026, 8, 31),
        commission_per_share_usd="0.005",
        minimum_commission_usd="1",
        maximum_commission_pct_of_trade_value="1",
        sell_fee_per_share_usd="0.0001",
    )
    assert policy.fee_for(side="BUY", quantity=100, price=Decimal("100")) == Decimal("1")
    assert policy.fee_for(side="SELL", quantity=100, price=Decimal("100")) == Decimal("1.0100")

    fill = ModelledFill(
        run_id="run-1",
        candidate_id="candidate-1",
        setup_fingerprint=HASH,
        quote_fingerprint=HASH,
        commission_policy_fingerprint=policy.fingerprint(),
        side="BUY",
        reason="entry",
        quantity=100,
        reference_price="100",
        modelled_price="100.05",
        commission_usd="1",
        filled_at=NOW,
    )
    assert fill.kind == "MODELLED"
    with pytest.raises(ValidationError, match="entry fills"):
        ModelledFill.model_validate({**fill.model_dump(), "side": "SELL"})


def test_rule_setup_causal_provenance_covers_every_consumed_bar() -> None:
    provenance = RuleSetupProvenance(
        opening_bar_fingerprints=("1" * 64, "2" * 64, "3" * 64),
        atr_bar_fingerprints=tuple(f"{value:x}" * 64 for value in range(1, 15)),
        breakout_bar_fingerprint="f" * 64,
        quote_fingerprint=HASH,
        candidate_input_fingerprint="b" * 64,
    )
    used = tuple(
        dict.fromkeys(
            (*provenance.opening_bar_fingerprints, *provenance.atr_bar_fingerprints, provenance.breakout_bar_fingerprint)
        )
    )
    setup = RuleSetup(
        run_id="run-1",
        candidate_id="candidate-1",
        strategy_id="ORB_V1",
        strategy_version=1,
        symbol="AAPL",
        opening_range_high="100",
        opening_range_low="98",
        atr="1",
        entry_limit="100.10",
        stop_price="99.10",
        target_price="102.10",
        bar_fingerprints=used,
        quote_fingerprint=HASH,
        causal_provenance=provenance,
        detected_at=NOW,
    )
    assert setup.causal_provenance is not None
    with pytest.raises(ValidationError, match="every causal bar"):
        RuleSetup.model_validate({**setup.model_dump(), "bar_fingerprints": ("1" * 64,)})


def test_session_receipt_requires_complete_terminal_decisions() -> None:
    receipt = ShadowSessionReceipt(
        run_id="run-1",
        source_sha="abcdef1",
        session_date=date(2026, 8, 31),
        state=ShadowSessionState.COMPLETED,
        evidence_eligible=True,
        strategy_id="ORB_V1",
        strategy_version=1,
        policy_fingerprints={"risk": HASH},
        runtime_config_fingerprint=_runtime().fingerprint(),
        commission_policy_fingerprint=HASH,
        calendar_id="XNYS",
        calendar_version="2026.1",
        scanner_result_count=50,
        admitted_candidate_count=20,
        terminal_decision_count=20,
        modelled_fill_count=4,
        decision_digest=HASH,
        replay_digest=HASH,
        data_provenance_digest=HASH,
        started_at=NOW,
        closed_at=NOW + timedelta(hours=6),
        created_at=NOW + timedelta(hours=6, minutes=1),
    )
    assert receipt.evidence_eligible
    with pytest.raises(ValidationError, match="terminal decision"):
        ShadowSessionReceipt.model_validate(
            {**receipt.model_dump(), "terminal_decision_count": 19}
        )

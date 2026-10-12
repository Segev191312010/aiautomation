from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from unittest.mock import AsyncMock

from trading.contracts import TradeIntent
from trading.execution.ibkr_paper import (
    BracketGroupRecord,
    BracketLegRecord,
    BrokerLegReceipt,
    IBKRTwsPaperExecutor,
    PaperExecutionError,
    PaperSubmissionContext,
    PaperTransportIdentity,
    StagedBrokerOrder,
    ManualInterventionRequired,
)
from trading.receipt_signing import Ed25519ReceiptSigner, Ed25519ReceiptVerifier
from trading.runtime import PaperPreflight, PaperRuntimeController, PaperRuntimeError, PaperRuntimeIdentity


@dataclass
class MemoryKillStore:
    stopped: bool = False

    async def is_stopped(self) -> bool:
        return self.stopped

    async def stop(self, *, reason: str) -> None:
        self.stopped = True


@dataclass
class RecordingLedger:
    staged: int = 0
    reservation_checks: int = 0

    async def validate_reservation(
        self, *, intent: TradeIntent, context: PaperSubmissionContext
    ) -> None:
        self.reservation_checks += 1

    async def stage(self, group: BracketGroupRecord, legs: tuple[BracketLegRecord, ...]) -> None:
        self.staged += 1

    async def update_group(
        self,
        group_id: str,
        *,
        owner_id: str,
        state: str,
        reason: str | None = None,
    ) -> None:
        return None

    async def update_leg(
        self,
        leg_id: str,
        *,
        owner_id: str,
        state: str,
        perm_id: str | None = None,
    ) -> None:
        return None

    async def execution_key_exists(self, execution_key: str) -> bool:
        return False

    async def fence(self, *, reason: str) -> None:
        return None


@dataclass
class RecordingTransport:
    identity: PaperTransportIdentity = field(
        default_factory=lambda: PaperTransportIdentity(
            host="127.0.0.1", port=7497, account="DU123", client_id=17
        )
    )
    reserve_calls: int = 0
    submissions: list[StagedBrokerOrder] = field(default_factory=list)

    def reserve_order_ids(self, count: int) -> tuple[int, ...]:
        self.reserve_calls += 1
        return (101, 102, 103)

    async def submit_order(
        self, order: StagedBrokerOrder, *, account: str, client_id: int
    ) -> BrokerLegReceipt:
        self.submissions.append(order)
        return BrokerLegReceipt(order_id=order.order_id, accepted=True, state="accepted")

    async def cancel_order(self, order_id: int, *, account: str, client_id: int) -> None:
        return None


def intent() -> TradeIntent:
    return TradeIntent(
        execution_key="paper-key",
        setup_fingerprint="a" * 64,
        approval_token="t" * 32,
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=60),
        strategy_id="ORB_V1",
        strategy_version=1,
        symbol="AAPL",
        quantity=10,
        entry_limit=Decimal("100.00"),
        stop_price=Decimal("98.00"),
        target_price=Decimal("104.00"),
    )


def context(**overrides: object) -> PaperSubmissionContext:
    values: dict[str, object] = {
        "owner_id": "owner-1",
        "run_id": "run-1",
        "candidate_id": "candidate-1",
        "risk_reservation_id": "reservation-1",
        "con_id": 101,
        "currency": "USD",
        "security_type": "STK",
        "primary_exchange": "NASDAQ",
        "rth": True,
        "min_tick": Decimal("0.01"),
    }
    values.update(overrides)
    return PaperSubmissionContext(**values)


def preflight() -> PaperPreflight:
    return PaperPreflight(
        tws_connected=True,
        paper_account_verified=True,
        client_id_verified=True,
        scanner_supported=True,
        real_time_data=True,
        data_line_capacity=True,
        reconciliation_clear=True,
        verified_at=datetime.now(timezone.utc),
    )


async def runtime() -> PaperRuntimeController:
    signer = Ed25519ReceiptSigner.generate()
    controller = PaperRuntimeController(
        identity=PaperRuntimeIdentity(
            build_sha="abcdef1",
            evidence_digest="b" * 64,
            strategy_id="ORB_V1",
            strategy_version=1,
            policy_hashes=("c" * 64,),
            paper_account="DU123",
            client_id=17,
        ),
        trusted_receipt_verifiers={"owner-key": Ed25519ReceiptVerifier(signer.public_key())},
        kill_store=MemoryKillStore(),
    )
    await controller.mark_paper_ready(
        preflight=preflight(), shadow_evidence_ready=True, manual_drill_ready=True
    )
    return controller


@pytest.mark.asyncio
async def test_executor_cannot_submit_until_a_future_phase_authority_gate_exists() -> None:
    transport = RecordingTransport()
    ledger = RecordingLedger()
    executor = IBKRTwsPaperExecutor(
        transport=transport, ledger=ledger, runtime=await runtime()
    )

    with pytest.raises(PaperRuntimeError, match="not active"):
        await executor.submit(intent=intent(), context=context())

    assert transport.reserve_calls == 0
    assert transport.submissions == []
    assert ledger.reservation_checks == 0
    assert ledger.staged == 0


@pytest.mark.asyncio
async def test_executor_rejects_invalid_or_unapproved_context_before_any_broker_io() -> None:
    transport = RecordingTransport()
    executor = IBKRTwsPaperExecutor(
        transport=transport, ledger=RecordingLedger(), runtime=await runtime()
    )

    with pytest.raises(PaperExecutionError, match="regular trading hours"):
        await executor.submit(intent=intent(), context=context(rth=False))
    with pytest.raises(PaperRuntimeError, match="not active"):
        await executor.submit(intent=intent(), context=context(min_tick=Decimal("0.03")))

    assert transport.reserve_calls == 0
    assert transport.submissions == []


@pytest.mark.asyncio
async def test_executor_rejects_any_transport_identity_other_than_local_paper() -> None:
    transport = RecordingTransport(
        identity=PaperTransportIdentity(host="127.0.0.1", port=7496, account="DU123", client_id=17)
    )

    with pytest.raises(PaperExecutionError, match="local TWS PAPER"):
        IBKRTwsPaperExecutor(
            transport=transport, ledger=RecordingLedger(), runtime=await runtime()
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("failed_leg", ["parent", "target", "stop"])
async def test_uncertain_submission_preserves_broker_orders(monkeypatch, failed_leg):
    controller = await runtime()
    monkeypatch.setattr(controller, "require_paper_submission", AsyncMock())
    transport = RecordingTransport()
    ledger = RecordingLedger()
    cancel = AsyncMock()
    monkeypatch.setattr(transport, "cancel_order", cancel)
    update_leg = AsyncMock()
    fence = AsyncMock()
    monkeypatch.setattr(ledger, "update_leg", update_leg)
    monkeypatch.setattr(ledger, "fence", fence)

    async def submit(order, **kwargs):
        transport.submissions.append(order)
        if order.leg_type == failed_leg:
            raise TimeoutError("unknown broker outcome")
        return BrokerLegReceipt(order_id=order.order_id, accepted=True, state="accepted")

    monkeypatch.setattr(transport, "submit_order", submit)
    executor = IBKRTwsPaperExecutor(transport=transport, ledger=ledger, runtime=controller)
    with pytest.raises(ManualInterventionRequired):
        await executor.submit(intent=intent(), context=context())
    cancel.assert_not_awaited()
    fence.assert_awaited_once()
    assert all(call.kwargs["state"] != "canceled" for call in update_leg.await_args_list)
    assert all(order.con_id == 101 and order.primary_exchange == "NASDAQ" for order in transport.submissions)


@pytest.mark.asyncio
async def test_global_fence_attempted_when_group_write_fails(monkeypatch):
    ledger = RecordingLedger()
    monkeypatch.setattr(ledger, "update_group", AsyncMock(side_effect=OSError("disk failure")))
    fence = AsyncMock()
    monkeypatch.setattr(ledger, "fence", fence)
    executor = IBKRTwsPaperExecutor(transport=RecordingTransport(), ledger=ledger, runtime=await runtime())
    with pytest.raises(ManualInterventionRequired):
        await executor._fence("group", owner_id="owner", reason="uncertain")
    fence.assert_awaited_once()


@pytest.mark.asyncio
async def test_qualified_contract_reaches_official_factory():
    from trading.execution.tws_paper_transport import _official_contract_factory

    executor = IBKRTwsPaperExecutor(transport=RecordingTransport(), ledger=RecordingLedger(), runtime=await runtime())
    group, legs = executor._build_records(intent=intent(), context=context(), observed_at=datetime.now(timezone.utc))
    order = executor._to_broker_order(group=group, legs=legs, leg=legs[0])
    contract = _official_contract_factory(order)
    assert contract.conId == 101
    assert contract.primaryExchange == "NASDAQ"
    assert contract.currency == "USD"


@pytest.mark.asyncio
async def test_cancellation_after_transmit_fences_without_removing_protection(monkeypatch):
    controller = await runtime()
    monkeypatch.setattr(controller, "require_paper_submission", AsyncMock())
    transport = RecordingTransport()
    cancel = AsyncMock()
    monkeypatch.setattr(transport, "cancel_order", cancel)
    ledger = RecordingLedger()
    fence = AsyncMock()
    monkeypatch.setattr(ledger, "fence", fence)

    async def update_group(group_id, **kwargs):
        if kwargs["state"] == "submitted":
            raise asyncio.CancelledError()

    monkeypatch.setattr(ledger, "update_group", update_group)
    executor = IBKRTwsPaperExecutor(transport=transport, ledger=ledger, runtime=controller)
    with pytest.raises(ManualInterventionRequired):
        await executor.submit(intent=intent(), context=context())
    assert [order.transmit for order in transport.submissions] == [False, False, True]
    fence.assert_awaited_once()
    cancel.assert_not_awaited()

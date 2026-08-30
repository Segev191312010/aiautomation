from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pytest

from trading.contracts import AuthorityReceipt, TradeIntent
from trading.receipt_signing import Ed25519ReceiptSigner, Ed25519ReceiptVerifier, sign_authority_receipt
from trading.runtime import (
    PaperPreflight,
    PaperRuntimeController,
    PaperRuntimeError,
    PaperRuntimeIdentity,
    PaperRuntimeState,
)


BUILD_SHA = "abcdef1"
DIGEST = "a" * 64
POLICY_HASH = "b" * 64


@dataclass
class MemoryKillStore:
    stopped: bool = False

    async def is_stopped(self) -> bool:
        return self.stopped

    async def stop(self, *, reason: str) -> None:
        assert reason
        self.stopped = True


def identity() -> PaperRuntimeIdentity:
    return PaperRuntimeIdentity(
        build_sha=BUILD_SHA,
        evidence_digest=DIGEST,
        strategy_id="ORB_V1",
        strategy_version=1,
        policy_hashes=(POLICY_HASH,),
        paper_account="DU123",
        client_id=17,
    )


def healthy_preflight(**overrides: object) -> PaperPreflight:
    values: dict[str, object] = {
        "tws_connected": True,
        "paper_account_verified": True,
        "client_id_verified": True,
        "scanner_supported": True,
        "real_time_data": True,
        "data_line_capacity": True,
        "reconciliation_clear": True,
        "verified_at": datetime.now(timezone.utc),
    }
    values.update(overrides)
    return PaperPreflight(**values)


def intent() -> TradeIntent:
    from decimal import Decimal

    return TradeIntent(
        execution_key="key-1",
        setup_fingerprint="c" * 64,
        approval_token="t" * 32,
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=30),
        strategy_id="ORB_V1",
        strategy_version=1,
        symbol="AAPL",
        quantity=1,
        entry_limit=Decimal("100"),
        stop_price=Decimal("98"),
        target_price=Decimal("104"),
    )


def signed_receipt(signer: Ed25519ReceiptSigner) -> AuthorityReceipt:
    return sign_authority_receipt(
        AuthorityReceipt(
            build_sha=BUILD_SHA,
            evidence_digest=DIGEST,
            strategy_id="ORB_V1",
            strategy_version=1,
            policy_hashes=(POLICY_HASH,),
            paper_account="DU123",
            client_id=17,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
            owner_approval="local owner approval",
        ),
        signer,
        public_key_id="local-owner-key",
        issued_at=datetime.now(timezone.utc),
    )


def controller(
    *, kill_store: MemoryKillStore | None = None
) -> tuple[PaperRuntimeController, Ed25519ReceiptSigner]:
    signer = Ed25519ReceiptSigner.generate()
    runtime = PaperRuntimeController(
        identity=identity(),
        trusted_receipt_verifiers={"local-owner-key": Ed25519ReceiptVerifier(signer.public_key())},
        kill_store=kill_store or MemoryKillStore(),
    )
    return runtime, signer


@pytest.mark.asyncio
async def test_shadow_readiness_does_not_require_or_load_a_paper_receipt() -> None:
    runtime, _ = controller()

    await runtime.mark_shadow_ready(preflight=healthy_preflight())

    assert runtime.state is PaperRuntimeState.SHADOW_READY
    assert runtime.receipt_validation is None


@pytest.mark.asyncio
async def test_paper_activation_requires_evidence_drill_and_remains_phase5_blocked() -> None:
    runtime, signer = controller()
    preflight = healthy_preflight()

    with pytest.raises(PaperRuntimeError, match="shadow and drill"):
        await runtime.activate(receipt=signed_receipt(signer), preflight=preflight)

    await runtime.mark_paper_ready(
        preflight=preflight, shadow_evidence_ready=True, manual_drill_ready=True
    )
    with pytest.raises(PaperRuntimeError, match="unavailable until its evidence gate"):
        await runtime.activate(receipt=signed_receipt(signer), preflight=preflight)
    with pytest.raises(PaperRuntimeError, match="not active"):
        await runtime.require_paper_submission(intent=intent(), context=object())

    assert runtime.state is PaperRuntimeState.BLOCKED


@pytest.mark.asyncio
async def test_untrusted_or_invalid_receipt_keeps_runtime_blocked() -> None:
    runtime, signer = controller()
    await runtime.mark_paper_ready(
        preflight=healthy_preflight(), shadow_evidence_ready=True, manual_drill_ready=True
    )
    receipt = signed_receipt(signer).model_copy(update={"client_id": 18})

    with pytest.raises(PaperRuntimeError, match="signature"):
        await runtime.activate(receipt=receipt, preflight=healthy_preflight())

    assert runtime.state is PaperRuntimeState.BLOCKED


@pytest.mark.asyncio
async def test_stop_is_monotonic_and_blocks_future_submission() -> None:
    kill_store = MemoryKillStore()
    runtime, _ = controller(kill_store=kill_store)
    preflight = healthy_preflight()
    await runtime.mark_paper_ready(
        preflight=preflight, shadow_evidence_ready=True, manual_drill_ready=True
    )
    await runtime.stop()
    with pytest.raises(PaperRuntimeError, match="not active"):
        await runtime.require_paper_submission(intent=intent(), context=object())

    assert kill_store.stopped is True
    assert runtime.state is PaperRuntimeState.BLOCKED


@pytest.mark.asyncio
async def test_unhealthy_preflight_cannot_enter_shadow_or_paper_state() -> None:
    runtime, _ = controller()

    with pytest.raises(PaperRuntimeError, match="preflight"):
        await runtime.mark_shadow_ready(
            preflight=healthy_preflight(real_time_data=False)
        )

    assert runtime.state is PaperRuntimeState.BLOCKED

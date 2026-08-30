from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
from subprocess import CompletedProcess

import pytest

from trading.receipt_signing import (
    Ed25519ReceiptSigner,
    Ed25519ReceiptVerifier,
    KeychainError,
    MacOSKeychainReceiptSigner,
    ReceiptSigningError,
    canonical_payload,
    sign_authority_receipt,
    verify_authority_receipt,
)
from trading.contracts import AuthorityReceipt


PAYLOAD = {
    "build_sha": "abcdef1",
    "evidence": {"digest": "a" * 64, "folds": [1, 2]},
    "owner": "local",
}


def test_canonical_payload_is_order_independent_and_compact():
    first = canonical_payload(
        {"owner": "local", "build_sha": "abcdef1", "evidence": {"b": 2, "a": 1}}
    )
    second = canonical_payload(
        {"evidence": {"a": 1, "b": 2}, "build_sha": "abcdef1", "owner": "local"}
    )

    assert first == second
    assert first == b'{"build_sha":"abcdef1","evidence":{"a":1,"b":2},"owner":"local"}'


def test_ed25519_sign_and_verify_reject_payload_mutation():
    signer = Ed25519ReceiptSigner.generate()
    signature = signer.sign(PAYLOAD)
    verifier = Ed25519ReceiptVerifier(signer.public_key())

    assert verifier.verify(PAYLOAD, signature)
    assert signer.verify(PAYLOAD, signature)
    assert not verifier.verify({**PAYLOAD, "owner": "other"}, signature)
    assert "=" not in signature


def test_authority_receipt_model_can_be_signed_without_changing_its_fields():
    receipt = AuthorityReceipt(
        build_sha="abcdef1",
        evidence_digest="a" * 64,
        strategy_id="strategy-1",
        strategy_version=1,
        policy_hashes=("b" * 64,),
        paper_account="DU123456",
        client_id=7,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        owner_approval="owner-approved",
    )
    signer = Ed25519ReceiptSigner.generate()

    signed_receipt = sign_authority_receipt(
        receipt,
        signer,
        public_key_id="key-1",
        issued_at=datetime.now(timezone.utc),
    )

    assert verify_authority_receipt(
        signed_receipt,
        Ed25519ReceiptVerifier(signer.public_key()),
    )


@pytest.mark.parametrize("signature", ["", "not-a-signature", "A" * 87])
def test_malformed_signature_fails_closed(signature: str):
    signer = Ed25519ReceiptSigner.generate()
    assert not signer.verify(PAYLOAD, signature)


def test_invalid_private_key_is_rejected():
    with pytest.raises(ReceiptSigningError):
        Ed25519ReceiptSigner(b"short")


def test_keychain_adapter_is_lazy_and_caches_key_without_printing():
    seed = bytes(range(32))
    encoded_seed = base64.urlsafe_b64encode(seed).rstrip(b"=").decode("ascii")
    calls: list[tuple[str, ...]] = []

    def runner(command):
        calls.append(tuple(command))
        return CompletedProcess(command, 0, stdout=encoded_seed, stderr="")

    adapter = MacOSKeychainReceiptSigner(
        service="com.example.autotrader.receipt",
        account="local-owner",
        command_runner=runner,
    )
    assert calls == []
    signature = adapter.sign(PAYLOAD)
    assert adapter.verify(PAYLOAD, signature)
    assert len(calls) == 1
    assert calls[0] == (
        "/usr/bin/security",
        "find-generic-password",
        "-a",
        "local-owner",
        "-s",
        "com.example.autotrader.receipt",
        "-w",
    )
    assert encoded_seed not in repr(adapter)


def test_keychain_adapter_does_not_expose_command_output_on_failure():
    secret = "sensitive-keychain-output"

    def runner(command):
        return CompletedProcess(command, 1, stdout=secret, stderr=secret)

    adapter = MacOSKeychainReceiptSigner(
        service="receipt",
        account="owner",
        command_runner=runner,
    )
    with pytest.raises(KeychainError) as error:
        adapter.public_key()
    assert secret not in str(error.value)


def test_keychain_hex_seed_is_supported():
    seed = bytes(range(32))

    def runner(command):
        return CompletedProcess(command, 0, stdout=seed.hex(), stderr="")

    adapter = MacOSKeychainReceiptSigner(
        service="receipt",
        account="owner",
        command_runner=runner,
    )
    signature = adapter.sign(PAYLOAD)
    assert adapter.verify(PAYLOAD, signature)

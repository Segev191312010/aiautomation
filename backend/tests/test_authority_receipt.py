from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from trading.authority import (
    AuthorityReceiptError,
    EngineAuthority,
    EngineAuthorityError,
)
from trading.contracts import AuthorityReceipt, EngineMode
from trading.receipt_signing import (
    Ed25519ReceiptSigner,
    Ed25519ReceiptVerifier,
    sign_authority_receipt,
)


HASH = "a" * 64
BUILD_SHA = "abcdef1234567"
NOW = datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)


def make_receipt(**overrides: object) -> AuthorityReceipt:
    values: dict[str, object] = {
        "build_sha": BUILD_SHA,
        "evidence_digest": HASH,
        "strategy_id": "orb-v1",
        "strategy_version": 3,
        "policy_hashes": (HASH,),
        "paper_account": "DU123456",
        "client_id": 17,
        "expires_at": NOW + timedelta(minutes=15),
        "owner_approval": "owner-approved",
    }
    values.update(overrides)
    return AuthorityReceipt(**values)


def test_valid_receipt_matches_immutable_runtime_identity():
    result = EngineAuthority().validate_paper_receipt(
        make_receipt(),
        expected_build_sha=BUILD_SHA,
        now=NOW,
        expected_strategy_id="orb-v1",
        expected_strategy_version=3,
        expected_policy_hashes=(HASH,),
        expected_paper_account="DU123456",
        expected_client_id=17,
    )

    assert result.valid
    assert result.status == "valid"
    assert "owner-approved" not in result.reason


def test_trusted_signature_is_required_when_runtime_provides_keyring():
    signer = Ed25519ReceiptSigner.generate()
    signed = sign_authority_receipt(
        make_receipt(), signer, public_key_id="local-owner-key", issued_at=NOW
    )
    keyring = {"local-owner-key": Ed25519ReceiptVerifier(signer.public_key())}

    result = EngineAuthority().validate_receipt(
        signed,
        expected_build_sha=BUILD_SHA,
        now=NOW,
        trusted_receipt_verifiers=keyring,
    )

    assert result.valid


def test_unsigned_or_tampered_receipt_is_rejected_when_signature_is_required():
    signer = Ed25519ReceiptSigner.generate()
    keyring = {"local-owner-key": Ed25519ReceiptVerifier(signer.public_key())}
    unsigned = EngineAuthority().validate_receipt(
        make_receipt(),
        expected_build_sha=BUILD_SHA,
        now=NOW,
        trusted_receipt_verifiers=keyring,
    )
    signed = sign_authority_receipt(
        make_receipt(), signer, public_key_id="local-owner-key", issued_at=NOW
    )
    tampered = signed.model_copy(update={"client_id": 18})
    invalid = EngineAuthority().validate_receipt(
        tampered,
        expected_build_sha=BUILD_SHA,
        now=NOW,
        trusted_receipt_verifiers=keyring,
    )

    assert unsigned.status == "signature_missing"
    assert invalid.status == "signature_invalid"


def test_receipt_model_is_immutable():
    receipt = make_receipt()

    with pytest.raises(ValidationError):
        receipt.build_sha = "b" * 13


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("build_sha", "Z" * 13, "receipt build SHA is invalid"),
        ("evidence_digest", "Z" * 64, "receipt evidence digest is invalid"),
        ("policy_hashes", ("Z" * 64,), "receipt policy hashes are invalid"),
        ("owner_approval", " ", "owner approval is required"),
    ],
)
def test_receipt_rejects_invalid_provenance_fields(field: str, value: object, reason: str):
    result = EngineAuthority().validate_receipt(
        make_receipt(**{field: value}),
        expected_build_sha=BUILD_SHA,
        now=NOW,
    )

    assert not result.valid
    assert result.status == "invalid"
    assert result.reason == reason


def test_receipt_rejects_missing_expired_and_build_mismatch():
    authority = EngineAuthority()

    assert authority.validate_receipt(None, expected_build_sha=BUILD_SHA, now=NOW).status == "missing"
    assert (
        authority.validate_receipt(
            make_receipt(), expected_build_sha="b" * 13, now=NOW
        ).status
        == "build_mismatch"
    )
    assert (
        authority.validate_receipt(
            make_receipt(expires_at=NOW), expected_build_sha=BUILD_SHA, now=NOW
        ).status
        == "expired"
    )


def test_receipt_rejects_evidence_digest_mismatch():
    result = EngineAuthority().validate_receipt(
        make_receipt(),
        expected_build_sha=BUILD_SHA,
        expected_evidence_digest="b" * 64,
        now=NOW,
    )

    assert result.status == "evidence_mismatch"
    assert not result.valid


@pytest.mark.parametrize(
    ("kwargs", "status"),
    [
        ({"expected_strategy_id": "other"}, "strategy_mismatch"),
        ({"expected_strategy_version": 4}, "strategy_mismatch"),
        ({"expected_policy_hashes": ("b" * 64,)}, "policy_mismatch"),
        ({"expected_paper_account": "DU999999"}, "account_mismatch"),
        ({"expected_client_id": 18}, "client_mismatch"),
    ],
)
def test_receipt_rejects_runtime_identity_mismatch(kwargs: dict[str, object], status: str):
    result = EngineAuthority().validate_receipt(
        make_receipt(), expected_build_sha=BUILD_SHA, now=NOW, **kwargs
    )

    assert result.status == status
    assert not result.valid


def test_valid_receipt_cannot_enable_paper_in_v1():
    authority = EngineAuthority()

    with pytest.raises(EngineAuthorityError, match="paper mode is unavailable"):
        authority.require_paper_receipt(
            make_receipt(), expected_build_sha=BUILD_SHA, now=NOW
        )
    with pytest.raises(EngineAuthorityError):
        authority.require_mode(EngineMode.PAPER)


def test_invalid_receipt_fails_without_exposing_approval_value():
    secret_approval = "owner-secret-approval-value"
    receipt = make_receipt(owner_approval=" ")

    with pytest.raises(AuthorityReceiptError) as error:
        EngineAuthority().require_paper_receipt(
            receipt, expected_build_sha=BUILD_SHA, now=NOW
        )

    assert secret_approval not in str(error.value)
    assert "owner approval is required" in str(error.value)

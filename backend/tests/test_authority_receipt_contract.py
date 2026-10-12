from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone

import aiosqlite
import pytest
from pydantic import ValidationError

from trading.contracts import AuthorityReceipt
from trading.storage.migrations import apply_migrations


HASH = "a" * 64
BUILD_SHA = "abcdef1234567"
NOW = datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)
HEX_SIGNATURE = "b" * 128
BASE64_SIGNATURE = base64.b64encode(bytes(64)).decode()


def make_receipt(**overrides: object) -> AuthorityReceipt:
    values: dict[str, object] = {
        "build_sha": BUILD_SHA,
        "evidence_digest": HASH,
        "strategy_id": "orb-v1",
        "strategy_version": 3,
        "policy_hashes": (HASH,),
        "paper_account": "DU123456",
        "client_id": 17,
        "expires_at": NOW + timedelta(days=5),
        "owner_approval": "owner-approved",
        "signature": HEX_SIGNATURE,
        "public_key_id": "ed25519-key-2026-08",
        "issued_at": NOW,
        "expires_after_sessions": 5,
    }
    values.update(overrides)
    return AuthorityReceipt(**values)


def test_signed_receipt_contains_immutable_ed25519_metadata():
    receipt = make_receipt()

    assert receipt.signature == HEX_SIGNATURE
    assert receipt.public_key_id == "ed25519-key-2026-08"
    assert receipt.issued_at == NOW
    assert receipt.expires_after_sessions == 5
    assert receipt.valid_for_sessions == 5
    assert receipt.max_sessions == 5

    with pytest.raises(ValidationError):
        receipt.public_key_id = "other-key"


def test_base64_ed25519_signature_and_session_alias_are_supported():
    values = {
        "build_sha": BUILD_SHA,
        "evidence_digest": HASH,
        "strategy_id": "orb-v1",
        "strategy_version": 3,
        "policy_hashes": (HASH,),
        "paper_account": "DU123456",
        "client_id": 17,
        "expires_at": NOW + timedelta(days=5),
        "owner_approval": "owner-approved",
        "signature": BASE64_SIGNATURE,
        "signing_key_id": "ed25519-key-2026-08",
        "issued_at": NOW,
        "valid_for_sessions": 5,
    }
    receipt = AuthorityReceipt(**values)

    assert receipt.signature == BASE64_SIGNATURE
    assert receipt.public_key_id == "ed25519-key-2026-08"
    assert receipt.expires_after_sessions == 5


@pytest.mark.parametrize(
    "signature",
    ["short", "a" * 127, "z" * 128, "!" * 86],
)
def test_signed_receipt_rejects_non_ed25519_signature_encoding(signature: str):
    with pytest.raises(ValidationError, match="64-byte Ed25519"):
        make_receipt(signature=signature)


def test_signed_fields_are_all_required_together_and_issue_before_expiry():
    with pytest.raises(ValidationError, match="require signature, public key, and issued time"):
        make_receipt(signature=None)
    with pytest.raises(ValidationError, match="precede expiry"):
        make_receipt(issued_at=NOW + timedelta(days=6))
    with pytest.raises(ValidationError):
        make_receipt(expires_after_sessions=6)


def test_legacy_unsigned_receipt_remains_readable_but_is_not_signed():
    receipt = make_receipt(signature=None, public_key_id=None, issued_at=None)

    assert receipt.signature is None
    assert receipt.public_key_id is None
    assert receipt.issued_at is None
    assert receipt.expires_after_sessions == 5


async def _connect(path) -> aiosqlite.Connection:
    database = await aiosqlite.connect(path)
    await database.execute("PRAGMA foreign_keys=ON")
    return database


@pytest.mark.anyio
async def test_attestation_migration_is_non_destructive_and_immutable(tmp_path):
    database = await _connect(tmp_path / "receipts.db")
    try:
        await apply_migrations(database)
        await database.execute(
            "INSERT INTO autotrader_strategy_identities VALUES ('strategy-1', 'owner-1', ?, NULL)",
            (NOW.isoformat(),),
        )
        await database.execute(
            """
            INSERT INTO autotrader_strategy_versions
            (strategy_id, version, content_hash, spec_json, created_at, created_by)
            VALUES ('strategy-1', 1, ?, '{}', ?, 'owner-1')
            """,
            (HASH, NOW.isoformat()),
        )
        await database.execute(
            """
            INSERT INTO autotrader_authority_receipts
            VALUES ('receipt-1', ?, 'abcdef1', ?, 'strategy-1', 1, ?, 'DU123456', 17, ?, '{}', NULL, ?)
            """,
            (
                HASH,
                HASH,
                '["' + HASH + '"]',
                (NOW + timedelta(days=5)).isoformat(),
                NOW.isoformat(),
            ),
        )
        await database.execute(
            """
            INSERT INTO autotrader_authority_receipt_attestations
            (receipt_id, signature, public_key_id, issued_at, expires_after_sessions)
            VALUES ('receipt-1', ?, 'ed25519-key-2026-08', ?, 5)
            """,
            (HEX_SIGNATURE, NOW.isoformat()),
        )
        await database.commit()

        async with database.execute(
            "SELECT COUNT(*) FROM autotrader_authority_receipts"
        ) as cursor:
            assert (await cursor.fetchone())[0] == 1
        async with database.execute(
            "SELECT signature, public_key_id, expires_after_sessions "
            "FROM autotrader_authority_receipt_attestations"
        ) as cursor:
            assert await cursor.fetchone() == (
                HEX_SIGNATURE,
                "ed25519-key-2026-08",
                5,
            )

        with pytest.raises(aiosqlite.IntegrityError, match="immutable"):
            await database.execute(
                "UPDATE autotrader_authority_receipt_attestations SET public_key_id='other' "
                "WHERE receipt_id='receipt-1'"
            )
        await database.rollback()
        with pytest.raises(aiosqlite.IntegrityError, match="immutable"):
            await database.execute(
                "DELETE FROM autotrader_authority_receipt_attestations WHERE receipt_id='receipt-1'"
            )
        await database.rollback()
    finally:
        await database.close()


@pytest.mark.anyio
async def test_attestation_migration_is_idempotent(tmp_path):
    database = await _connect(tmp_path / "idempotent.db")
    try:
        await apply_migrations(database)
        async with database.execute(
            "SELECT version, checksum, applied_at FROM autotrader_schema_migrations ORDER BY version"
        ) as cursor:
            first_run = await cursor.fetchall()
        await apply_migrations(database)
        async with database.execute(
            "SELECT version, checksum, applied_at FROM autotrader_schema_migrations ORDER BY version"
        ) as cursor:
            second_run = await cursor.fetchall()
        assert [row[0] for row in second_run] == list(range(1, 13))
        assert second_run == first_run
    finally:
        await database.close()

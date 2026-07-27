"""Durable cross-host execution lease primitives (Stage 9B Phase 1 SF1a)."""
from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

import pytest

from db.execution_lease import (
    LEASE_QUARANTINE_SECONDS,
    LEASE_TTL_SECONDS,
    Lease,
    acquire_execution_lease,
    get_current_lease,
    release_execution_lease,
    renew_execution_lease,
    validate_fencing_token,
)


from config import cfg


@pytest.fixture
def isolated_db(tmp_path):
    """Point DB_PATH at a fresh file for lease tests so the session lease does
    not interfere with assertions about lease ownership transitions.
    """
    db_file = tmp_path / "lease.db"
    original_cfg_db = cfg.DB_PATH
    cfg.DB_PATH = str(db_file)
    try:
        yield db_file
    finally:
        cfg.DB_PATH = original_cfg_db


@pytest.mark.anyio
async def test_acquire_execution_lease_creates_singleton_row(isolated_db):
    lease = await acquire_execution_lease(owner_id="test-owner")
    assert lease.owner_id == "test-owner"
    assert len(lease.fencing_token) == 32  # uuid4 hex

    current = await get_current_lease()
    assert current is not None
    assert current.fencing_token == lease.fencing_token
    assert current.version == 1

    await release_execution_lease(lease.fencing_token)


@pytest.mark.anyio
async def test_second_acquire_while_valid_is_rejected(isolated_db):
    lease_a = await acquire_execution_lease(owner_id="owner-a")
    try:
        with pytest.raises(RuntimeError, match="execution lease held by owner-a"):
            await acquire_execution_lease(owner_id="owner-b")
    finally:
        await release_execution_lease(lease_a.fencing_token)


@pytest.mark.anyio
async def test_validate_fencing_token_accepts_valid_token(isolated_db):
    lease = await acquire_execution_lease(owner_id="owner")
    try:
        validated = await validate_fencing_token(lease.fencing_token)
        assert isinstance(validated, Lease)
        assert validated.fencing_token == lease.fencing_token
    finally:
        await release_execution_lease(lease.fencing_token)


@pytest.mark.anyio
async def test_validate_fencing_token_rejects_stale_and_missing_tokens(isolated_db):
    lease = await acquire_execution_lease(owner_id="owner")
    try:
        assert await validate_fencing_token(None) is None
        assert await validate_fencing_token("") is None
        assert await validate_fencing_token("deadbeef") is None
    finally:
        await release_execution_lease(lease.fencing_token)


@pytest.mark.anyio
async def test_validate_fencing_token_rejects_expired_lease(isolated_db):
    lease = await acquire_execution_lease(
        owner_id="owner",
        ttl_seconds=1,
        heartbeat_seconds=1,
    )
    await asyncio.sleep(1.1)
    try:
        assert await validate_fencing_token(lease.fencing_token) is None
    finally:
        # Cleanup may fail if lease is already expired; ignore.
        await release_execution_lease(lease.fencing_token)


@pytest.mark.anyio
async def test_renew_execution_lease_extends_expiry(isolated_db):
    lease = await acquire_execution_lease(
        owner_id="owner",
        ttl_seconds=2,
        heartbeat_seconds=1,
    )
    original_expires = lease.expires_at
    await asyncio.sleep(0.5)
    renewed = await renew_execution_lease(lease.fencing_token, ttl_seconds=10)
    assert renewed is not None
    assert renewed.expires_at > original_expires
    assert renewed.fencing_token == lease.fencing_token
    await release_execution_lease(renewed.fencing_token)


@pytest.mark.anyio
async def test_renew_execution_lease_rejects_stale_token(isolated_db):
    lease = await acquire_execution_lease(owner_id="owner")
    # Simulate another owner taking over.
    await release_execution_lease(lease.fencing_token)
    lease_b = await acquire_execution_lease(owner_id="owner-b")
    try:
        assert await renew_execution_lease(lease.fencing_token, ttl_seconds=10) is None
    finally:
        await release_execution_lease(lease_b.fencing_token)


@pytest.mark.anyio
async def test_release_execution_lease_removes_row_and_returns_false_for_stale(isolated_db):
    lease = await acquire_execution_lease(owner_id="owner")
    assert await release_execution_lease(lease.fencing_token) is True
    assert await get_current_lease() is None
    # Releasing the same token again is a no-op.
    assert await release_execution_lease(lease.fencing_token) is False


@pytest.mark.anyio
async def test_quarantine_prevents_immediate_takeover(isolated_db):
    lease = await acquire_execution_lease(
        owner_id="owner-a",
        ttl_seconds=1,
        heartbeat_seconds=1,
        quarantine_seconds=2,
    )
    await asyncio.sleep(1.1)  # Let lease expire.
    try:
        with pytest.raises(RuntimeError, match="quarantine"):
            await acquire_execution_lease(owner_id="owner-b")
    finally:
        await release_execution_lease(lease.fencing_token)


@pytest.mark.anyio
async def test_quarantine_expires_and_new_owner_can_takeover(isolated_db):
    # Use an explicit short quarantine so the test completes quickly and
    # deterministically regardless of the production default.
    lease = await acquire_execution_lease(
        owner_id="owner-a",
        ttl_seconds=1,
        heartbeat_seconds=1,
        quarantine_seconds=1,
    )
    # Wait until the DB-visible lease has definitely expired and the
    # quarantine has cleared, then retry in a bounded loop to tolerate CI
    # scheduling drift.
    deadline = time.time() + 5.0
    lease_b: Lease | None = None
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            lease_b = await acquire_execution_lease(
                owner_id="owner-b",
                ttl_seconds=1,
                heartbeat_seconds=1,
                quarantine_seconds=1,
            )
            break
        except RuntimeError as exc:
            last_error = exc
            await asyncio.sleep(0.2)
    assert lease_b is not None, f"failed to take over after quarantine: {last_error}"
    assert lease_b.owner_id == "owner-b"
    assert lease_b.version > lease.version
    await release_execution_lease(lease_b.fencing_token)
    await release_execution_lease(lease.fencing_token)


@pytest.mark.anyio
async def test_default_ttl_and_heartbeat_are_reasonable(isolated_db):
    assert LEASE_TTL_SECONDS > 0
    assert LEASE_QUARANTINE_SECONDS >= 0
    lease = await acquire_execution_lease(owner_id="owner")
    now = time.time()
    try:
        assert lease.expires_at > now
        assert lease.expires_at <= now + LEASE_TTL_SECONDS + 1
    finally:
        await release_execution_lease(lease.fencing_token)

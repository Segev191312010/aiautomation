"""Execution lease + lifespan safety tests (Stage 9B Phase 1 SF1a)."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import startup
from auth import get_current_user
from config import cfg
from db.execution_lease import (
    acquire_execution_lease,
    get_current_lease,
    release_execution_lease,
)
import routers.status as status_router


@pytest.fixture
def isolated_status_app(tmp_path):
    """Minimal FastAPI app with the status router and a fresh execution lease.

    The session-scoped lease fixture in conftest.py publishes its token into
    startup._execution_lease, but it lives in the shared session DB.  Tests
    here need an isolated DB so we can verify takeover/heartbeat/release
    behavior without affecting the rest of the suite.
    """
    db_file = tmp_path / "lifespan_lease.db"
    original_cfg_db = cfg.DB_PATH
    cfg.DB_PATH = str(db_file)

    # Point startup helpers at the isolated DB path too.
    original_startup_lease = startup._execution_lease
    startup._execution_lease = None

    app = FastAPI()
    app.include_router(status_router.router)
    app.dependency_overrides[get_current_user] = lambda: {"id": "demo"}

    try:
        yield app
    finally:
        cfg.DB_PATH = original_cfg_db
        startup._execution_lease = original_startup_lease


@pytest.mark.anyio
async def test_lifespan_acquires_and_releases_execution_lease(isolated_status_app):
    app = isolated_status_app
    acquired = None

    @asynccontextmanager
    async def _lifespan(app: FastAPI):
        nonlocal acquired
        await startup.validate_startup()
        acquired = startup.get_execution_fencing_token()
        yield
        await startup.release_execution_lease_and_lock(db_path=cfg.DB_PATH)

    app.router.lifespan_context = _lifespan

    with TestClient(app):
        pass

    assert acquired is not None
    # After shutdown the lease row is gone.
    assert await get_current_lease() is None


@pytest.mark.anyio
async def test_lifespan_rejects_second_instance(isolated_status_app):
    app = isolated_status_app
    first_lease = await startup.acquire_execution_lease_and_lock(db_path=cfg.DB_PATH)
    try:
        # Direct call raises immediately because this process already holds a
        # lease. validate_startup() swallows RuntimeError and converts it to a
        # startup error, but the primitive itself rejects duplicate ownership.
        with pytest.raises(RuntimeError, match="already held"):
            await startup.acquire_execution_lease_and_lock(db_path=cfg.DB_PATH)
    finally:
        await startup.release_execution_lease_and_lock(db_path=cfg.DB_PATH)


@pytest.mark.anyio
async def test_validate_startup_idempotent_when_lease_already_held(isolated_status_app):
    app = isolated_status_app
    first = await startup.acquire_execution_lease_and_lock(db_path=cfg.DB_PATH)
    startup._execution_lease = first
    try:
        result = await startup.validate_startup()
        assert result["errors"] == []
        assert startup.get_execution_fencing_token() == first.fencing_token
    finally:
        await startup.release_execution_lease_and_lock(db_path=cfg.DB_PATH)


@pytest.mark.anyio
async def test_heartbeat_renewal_extends_expiry(isolated_status_app):
    app = isolated_status_app
    lease = await startup.acquire_execution_lease_and_lock(
        db_path=cfg.DB_PATH,
    )
    try:
        await asyncio.sleep(0.3)
        renewed = await startup.renew_execution_lease_heartbeat()
        assert renewed is not None
        assert renewed.expires_at > lease.expires_at
        assert renewed.fencing_token == lease.fencing_token
    finally:
        await startup.release_execution_lease_and_lock(db_path=cfg.DB_PATH)


@pytest.mark.anyio
async def test_heartbeat_rejected_after_external_takeover(isolated_status_app):
    app = isolated_status_app
    lease = await startup.acquire_execution_lease_and_lock(db_path=cfg.DB_PATH)
    try:
        # Another owner grabs the lease.
        await release_execution_lease(lease.fencing_token)
        other = await acquire_execution_lease(owner_id="other")
        try:
            renewed = await startup.renew_execution_lease_heartbeat()
            assert renewed is None
            assert startup._execution_lease is None
        finally:
            await release_execution_lease(other.fencing_token)
    finally:
        # If still held, release.  The external takeover branch already
        # released our token, so this may be a no-op.
        await startup.release_execution_lease_and_lock(db_path=cfg.DB_PATH)


def test_health_includes_execution_lease(isolated_status_app):
    app = isolated_status_app
    client = TestClient(app)

    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert "execution_lease" in body["checks"]
    lease_check = body["checks"]["execution_lease"]
    assert lease_check["status"] == "not_held"
    assert lease_check["owner"] is None
    assert lease_check["expires_at"] is None
    assert body["status"] == "degraded"

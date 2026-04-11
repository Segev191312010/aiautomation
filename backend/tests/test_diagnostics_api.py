"""
Diagnostics API contract tests.
"""
from __future__ import annotations

import os
import sys

import pytest
from httpx import ASGITransport, AsyncClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture
async def client(tmp_path):
    db_path = str(tmp_path / "diag_test.db")
    from config import cfg

    cfg.DB_PATH = db_path
    cfg.ENABLE_MARKET_DIAGNOSTICS = True
    import database

    database.DB_PATH = db_path
    from main import app, _diag_service
    from auth import create_token

    await database.init_db()
    await _diag_service.ensure_catalog_seeded()

    transport = ASGITransport(app=app)
    # Create auth headers for tests
    token = create_token("demo")
    auth_headers = {"Authorization": f"Bearer {token}"}
    async with AsyncClient(
        transport=transport, base_url="http://test", headers=auth_headers
    ) as c:
        yield c


@pytest.mark.asyncio
async def test_diagnostics_overview_contract(client, monkeypatch: pytest.MonkeyPatch):
    from main import _diag_service

    async def _fake_overview(lookback_days: int = 90):
        return {
            "as_of_ts": 1700000000,
            "composite_score": 62.5,
            "state": "YELLOW",
            "indicator_count": 8,
            "stale_count": 0,
            "warn_count": 1,
            "trend": [{"time": 1700000000, "value": 62.5}],
            "widgets": {},
        }

    monkeypatch.setattr(_diag_service, "get_overview", _fake_overview)
    resp = await client.get("/api/diagnostics/overview?lookback_days=90")
    assert resp.status_code == 200
    body = resp.json()
    assert body["state"] == "YELLOW"
    assert "trend" in body


@pytest.mark.asyncio
async def test_diagnostics_refresh_accepted(client, monkeypatch: pytest.MonkeyPatch):
    from main import _diag_service

    async def _fake_trigger(lock_holder: str = "manual", wait: bool = False):
        return {"status": "accepted", "run_id": 123}

    monkeypatch.setattr(_diag_service, "trigger_refresh", _fake_trigger)
    resp = await client.post("/api/diagnostics/refresh")
    assert resp.status_code == 202
    body = resp.json()
    assert body["run_id"] == 123


@pytest.mark.asyncio
async def test_diagnostics_refresh_conflict(client, monkeypatch: pytest.MonkeyPatch):
    from main import _diag_service

    async def _fake_trigger(lock_holder: str = "manual", wait: bool = False):
        return {
            "status": "conflict",
            "run_id": 77,
            "locked_by": "scheduler",
            "lock_expires_at": 1700000999,
        }

    monkeypatch.setattr(_diag_service, "trigger_refresh", _fake_trigger)
    resp = await client.post("/api/diagnostics/refresh")
    assert resp.status_code == 409
    body = resp.json()
    assert body["run_id"] == 77
    assert body["locked_by"] == "scheduler"


@pytest.mark.asyncio
async def test_data_health_includes_diagnostic_sources(client):
    resp = await client.get("/api/data/health")
    assert resp.status_code == 200
    body = resp.json()
    sources = body.get("sources", {})
    assert "diag_indicators" in sources
    assert "diag_market_map" in sources
    assert "diag_sector_projections" in sources
    assert "diag_news_cache" in sources
    assert "diag_refresh_jobs" in sources
    assert "diagnostics" in body

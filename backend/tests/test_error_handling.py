"""
Tests for API error handling — all errors return {error, detail} format.
Uses httpx + FastAPI TestClient approach.
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("DB_PATH", "test_trading.db")
os.environ.setdefault("SIM_MODE", "true")

import pytest
from httpx import AsyncClient, ASGITransport


async def issue_auth_headers(client: AsyncClient) -> dict[str, str]:
    from config import cfg
    headers = {}
    bootstrap_secret = getattr(cfg, "JWT_BOOTSTRAP_SECRET", None)
    if bootstrap_secret:
        headers["X-Bootstrap-Secret"] = bootstrap_secret
    resp = await client.post("/api/auth/token", headers=headers)
    assert resp.status_code == 200
    access_token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {access_token}"}


@pytest.fixture
async def client(tmp_path):
    db_path = str(tmp_path / "test.db")
    from config import cfg
    cfg.DB_PATH = db_path
    import database
    database.DB_PATH = db_path

    from main import app
    # Init DB for test
    await database.init_db()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_404_returns_json_format(client):
    resp = await client.get("/api/rules/nonexistent-id", headers=await issue_auth_headers(client))
    assert resp.status_code == 404
    body = resp.json()
    assert "error" in body
    assert "detail" in body


@pytest.mark.asyncio
async def test_status_endpoint(client):
    resp = await client.get("/api/status")
    assert resp.status_code == 200
    body = resp.json()
    assert "ibkr_connected" in body
    assert "sim_mode" in body


@pytest.mark.asyncio
async def test_auth_me_requires_bearer_token(client):
    resp = await client.get("/api/auth/me")
    assert resp.status_code == 401
    body = resp.json()
    assert body["detail"] == "Missing bearer token"


@pytest.mark.asyncio
async def test_auth_me_returns_demo_with_token(client):
    resp = await client.get("/api/auth/me", headers=await issue_auth_headers(client))
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == "demo"
    assert body["email"] == "demo@local"


@pytest.mark.asyncio
async def test_auth_token_endpoint(client):
    from config import cfg
    headers = {}
    bootstrap_secret = getattr(cfg, "JWT_BOOTSTRAP_SECRET", None)
    if bootstrap_secret:
        headers["X-Bootstrap-Secret"] = bootstrap_secret
    resp = await client.post("/api/auth/token", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert "access_token" in body
    assert body["token_type"] == "bearer"


@pytest.mark.asyncio
async def test_settings_get(client):
    resp = await client.get("/api/settings", headers=await issue_auth_headers(client))
    assert resp.status_code == 200
    body = resp.json()
    assert body["theme"] == "dark"
    assert "watchlist" in body


@pytest.mark.asyncio
async def test_settings_put_partial(client):
    resp = await client.put(
        "/api/settings",
        headers=await issue_auth_headers(client),
        json={"default_symbol": "TSLA"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["default_symbol"] == "TSLA"
    assert body["theme"] == "dark"  # untouched


@pytest.mark.asyncio
async def test_autopilot_route_requires_bearer_token(client):
    resp = await client.get("/api/autopilot/status")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_orders_route_requires_bearer_token(client):
    resp = await client.get("/api/orders")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_security_headers_are_mounted(client):
    resp = await client.get("/api/status")
    assert resp.status_code == 200
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["x-frame-options"] == "DENY"
    assert resp.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert resp.headers["cache-control"] == "no-store"
    csp = resp.headers["content-security-policy"]
    assert "default-src 'self'" in csp
    assert "script-src 'self'" in csp
    assert "style-src 'self'" in csp
    assert "img-src 'self' data:" in csp
    assert "connect-src 'self'" in csp
    assert "frame-src 'none'" in csp
    assert "frame-ancestors 'none'" in csp


@pytest.mark.asyncio
async def test_oneil_screener_is_explicitly_unavailable(client):
    resp = await client.get(
        "/api/swing/screener/oneil",
        headers=await issue_auth_headers(client),
    )
    assert resp.status_code == 501
    assert "fundamental data" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_validation_error_does_not_echo_session_capability(client, monkeypatch, caplog):
    from session_api import SESSION_BOOTSTRAP_TOKEN_ENV

    leaked_value = "leak-me"
    monkeypatch.setenv(SESSION_BOOTSTRAP_TOKEN_ENV, "configured-capability-123456")
    resp = await client.post(
        "/api/session/bootstrap",
        json={"launch_token": leaked_value},
    )

    assert resp.status_code == 422
    assert leaked_value not in resp.text
    assert leaked_value not in caplog.text

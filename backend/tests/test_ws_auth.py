"""
WebSocket authentication tests — verifies token validation on /ws and /ws/market-data.
Also verifies that backtest and screener routes require authentication.
"""
import os
import sys

import pytest
from httpx import ASGITransport, AsyncClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("DB_PATH", "test_trading.db")
os.environ.setdefault("SIM_MODE", "true")

from auth import create_token
from database import init_db
from main import app, _validate_ws_token, _check_ws_origin


@pytest.fixture(autouse=True)
async def _ensure_schema():
    await init_db()


# ── Unit tests for helpers ───────────────────────────────────────────────────

class _FakeWS:
    """Minimal WebSocket mock for unit-testing helper functions."""
    def __init__(self, query_params=None, headers=None):
        self.query_params = query_params or {}
        self.headers = headers or {}


def test_validate_ws_token_valid():
    token = create_token("demo")
    ws = _FakeWS(query_params={"token": token})
    assert _validate_ws_token(ws) == "demo"


def test_validate_ws_token_missing():
    ws = _FakeWS(query_params={})
    assert _validate_ws_token(ws) is None


def test_validate_ws_token_invalid():
    ws = _FakeWS(query_params={"token": "garbage.token.here"})
    assert _validate_ws_token(ws) is None


def test_check_ws_origin_allowed():
    ws = _FakeWS(headers={"origin": "http://localhost:5173"})
    assert _check_ws_origin(ws) is True


def test_check_ws_origin_blocked():
    ws = _FakeWS(headers={"origin": "http://evil.com"})
    assert _check_ws_origin(ws) is False


def test_check_ws_origin_no_header_rejected():
    """Non-browser clients (no origin header) should now be rejected."""
    ws = _FakeWS(headers={})
    assert _check_ws_origin(ws) is False


# ── HTTP route auth tests ────────────────────────────────────────────────────

@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_backtest_requires_auth(client):
    """GET /api/backtest/history without bearer token should return 401."""
    resp = await client.get("/api/backtest/history")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_screener_requires_auth(client):
    """GET /api/screener/presets without bearer token should return 401."""
    resp = await client.get("/api/screener/presets")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_backtest_with_auth(client):
    """GET /api/backtest/history with valid token should succeed."""
    from config import cfg
    headers = {}
    bootstrap_secret = getattr(cfg, "JWT_BOOTSTRAP_SECRET", None)
    if bootstrap_secret:
        headers["X-Bootstrap-Secret"] = bootstrap_secret
    token_resp = await client.post("/api/auth/token", headers=headers)
    token = token_resp.json()["access_token"]
    resp = await client.get(
        "/api/backtest/history",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_screener_presets_with_auth(client):
    """GET /api/screener/presets with valid token should succeed."""
    from config import cfg
    headers = {}
    bootstrap_secret = getattr(cfg, "JWT_BOOTSTRAP_SECRET", None)
    if bootstrap_secret:
        headers["X-Bootstrap-Secret"] = bootstrap_secret
    token_resp = await client.post("/api/auth/token", headers=headers)
    token = token_resp.json()["access_token"]
    resp = await client.get(
        "/api/screener/presets",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200

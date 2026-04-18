"""
CORS middleware smoke tests — verifies the allowlist is env-driven and
methods are explicit (no `["*"]` with credentials).
"""
import os
import sys

import pytest
from httpx import ASGITransport, AsyncClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("DB_PATH", "test_trading.db")
os.environ.setdefault("SIM_MODE", "true")

from main import app, _allowed_origins  # noqa: E402


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def test_dev_origins_are_allowed_when_env_unset(monkeypatch):
    monkeypatch.delenv("FRONTEND_ORIGIN", raising=False)
    origins = set(_allowed_origins())
    assert "http://localhost:5173" in origins
    assert "http://localhost:5174" in origins
    assert "http://127.0.0.1:5173" in origins


def test_env_origin_replaces_dev_defaults(monkeypatch):
    """FRONTEND_ORIGIN is authoritative when set — dev localhost is NOT trusted."""
    monkeypatch.setenv("FRONTEND_ORIGIN", "https://app.example.com,https://staging.example.com")
    origins = set(_allowed_origins())
    assert origins == {"https://app.example.com", "https://staging.example.com"}
    assert "http://localhost:5173" not in origins
    assert "http://127.0.0.1:5173" not in origins


def test_env_origin_whitespace_tolerated(monkeypatch):
    monkeypatch.setenv("FRONTEND_ORIGIN", "  https://app.example.com , , https://b.example.com ")
    origins = set(_allowed_origins())
    assert origins == {"https://app.example.com", "https://b.example.com"}


@pytest.mark.asyncio
async def test_preflight_with_allowed_origin_returns_cors_headers(client):
    """Browser preflight from localhost:5173 should echo ACAO + methods."""
    resp = await client.options(
        "/api/auth/status",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        },
    )
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") == "http://localhost:5173"
    # With credentials, the response must NOT be wildcard
    assert resp.headers.get("access-control-allow-origin") != "*"
    assert resp.headers.get("access-control-allow-credentials") == "true"
    methods = resp.headers.get("access-control-allow-methods", "")
    assert "GET" in methods
    assert "POST" in methods
    # HEAD must be present: FastAPI serves HEAD for every GET route
    assert "HEAD" in methods


@pytest.mark.asyncio
async def test_preflight_with_disallowed_origin_omits_cors_headers(client):
    resp = await client.options(
        "/api/auth/status",
        headers={
            "Origin": "http://evil.example.com",
            "Access-Control-Request-Method": "GET",
        },
    )
    # Starlette returns 400 or omits ACAO for disallowed origins
    assert resp.headers.get("access-control-allow-origin") != "http://evil.example.com"

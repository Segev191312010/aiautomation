from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from auth import get_current_user
from health import router as health_router
from models import User
from routers.status import router as status_router


async def _test_user() -> User:
    return User(
        id="demo",
        email="demo@local",
        created_at=datetime.now(timezone.utc).isoformat(),
        settings={},
    )


def _build_app(*, authed: bool = False) -> FastAPI:
    app = FastAPI()
    app.include_router(health_router)
    app.include_router(status_router)
    if authed:
        app.dependency_overrides[get_current_user] = _test_user
    return app


@pytest.mark.asyncio
async def test_basic_health_stays_public():
    transport = ASGITransport(app=_build_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/api/health/detailed", "/api/health/bot", "/api/health/deep"])
async def test_sensitive_health_routes_require_auth(path: str):
    transport = ASGITransport(app=_build_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(path)

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_detailed_health_works_with_auth():
    transport = ASGITransport(app=_build_app(authed=True))
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/health/detailed")

    assert response.status_code in (200, 503)
    payload = response.json()
    assert "runtime" in payload
    assert "pid" in payload["runtime"]
    assert "app" in payload
    assert "ibkr_port" in payload["app"]


@pytest.mark.asyncio
async def test_deep_health_works_at_dedicated_authed_path():
    transport = ASGITransport(app=_build_app(authed=True))
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/health/deep")

    assert response.status_code == 200
    payload = response.json()
    assert "checks" in payload
    assert "database" in payload["checks"]
    assert "ibkr" in payload["checks"]
    assert "bot" in payload["checks"]

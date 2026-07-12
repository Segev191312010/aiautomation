"""Phase B session bootstrap boundary regressions."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jose import jwt

from config import cfg
from session_api import (
    SESSION_BOOTSTRAP_TOKEN_ENV,
    SESSION_TOKEN_TTL_SECONDS,
    TRUST_PROXY_HEADERS_ENV,
    router,
)


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    return app


def _client(host: str = "127.0.0.1") -> TestClient:
    return TestClient(_app(), client=(host, 55000))


@pytest.fixture(autouse=True)
def _session_environment(monkeypatch):
    previous = (cfg.AUTOPILOT_MODE, cfg.SIM_MODE, cfg.IS_PAPER)
    monkeypatch.delenv(SESSION_BOOTSTRAP_TOKEN_ENV, raising=False)
    monkeypatch.delenv(TRUST_PROXY_HEADERS_ENV, raising=False)
    cfg.AUTOPILOT_MODE = "OFF"
    cfg.SIM_MODE = True
    cfg.IS_PAPER = True
    yield
    cfg.AUTOPILOT_MODE, cfg.SIM_MODE, cfg.IS_PAPER = previous


def test_safe_loopback_bootstrap_returns_short_lived_session():
    response = _client().post(
        "/api/session/bootstrap",
        json={},
        headers={"Origin": "http://127.0.0.1:5173"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["token_type"] == "bearer"
    assert payload["expires_in_seconds"] == SESSION_TOKEN_TTL_SECONDS
    assert response.headers["cache-control"] == "no-store"

    claims = jwt.decode(payload["access_token"], cfg.JWT_SECRET, algorithms=[cfg.JWT_ALGORITHM])
    assert claims["sub"] == "demo"
    expires_at = datetime.fromisoformat(payload["expires_at"].replace("Z", "+00:00"))
    remaining = (expires_at - datetime.now(timezone.utc)).total_seconds()
    assert SESSION_TOKEN_TTL_SECONDS - 10 <= remaining <= SESSION_TOKEN_TTL_SECONDS


def test_non_loopback_client_is_rejected():
    response = _client("203.0.113.25").post("/api/session/bootstrap", json={})

    assert response.status_code == 403


def test_compose_proxy_preserves_loopback_boundary(monkeypatch):
    monkeypatch.setenv(TRUST_PROXY_HEADERS_ENV, "true")
    response = _client("172.18.0.3").post(
        "/api/session/bootstrap",
        json={},
        headers={
            "Origin": "http://127.0.0.1:3000",
            "X-Forwarded-For": "172.18.0.1",
        },
    )

    assert response.status_code == 200


def test_proxy_header_is_ignored_unless_manifest_opt_in_is_enabled():
    response = _client("172.18.0.3").post(
        "/api/session/bootstrap",
        json={},
        headers={"X-Forwarded-For": "127.0.0.1"},
    )

    assert response.status_code == 403


def test_forwarded_address_chain_is_rejected(monkeypatch):
    monkeypatch.setenv(TRUST_PROXY_HEADERS_ENV, "true")
    response = _client("172.18.0.3").post(
        "/api/session/bootstrap",
        json={},
        headers={"X-Forwarded-For": "127.0.0.1, 203.0.113.25"},
    )

    assert response.status_code == 403


def test_public_direct_peer_cannot_spoof_trusted_proxy_header(monkeypatch):
    monkeypatch.setenv(TRUST_PROXY_HEADERS_ENV, "true")
    response = _client("203.0.113.25").post(
        "/api/session/bootstrap",
        json={},
        headers={"X-Forwarded-For": "127.0.0.1"},
    )

    assert response.status_code == 403


def test_non_loopback_browser_origin_is_rejected_without_launch_capability():
    response = _client().post(
        "/api/session/bootstrap",
        json={},
        headers={"Origin": "https://attacker.example"},
    )

    assert response.status_code == 403


def test_real_money_mode_requires_per_launch_capability():
    cfg.AUTOPILOT_MODE = "LIVE"
    cfg.SIM_MODE = False
    cfg.IS_PAPER = False

    response = _client().post("/api/session/bootstrap", json={})

    assert response.status_code == 503


def test_configured_per_launch_capability_is_required_and_allows_live(monkeypatch):
    capability = "per-launch-test-capability-32-bytes"
    monkeypatch.setenv(SESSION_BOOTSTRAP_TOKEN_ENV, capability)
    cfg.AUTOPILOT_MODE = "LIVE"
    cfg.SIM_MODE = False
    cfg.IS_PAPER = False
    client = _client()

    missing = client.post("/api/session/bootstrap", json={})
    wrong = client.post(
        "/api/session/bootstrap",
        json={"launch_token": "wrong-capability-value"},
    )
    accepted = client.post(
        "/api/session/bootstrap",
        json={"launch_token": capability},
        headers={"Origin": "null"},
    )

    assert missing.status_code == 401
    assert wrong.status_code == 401
    assert accepted.status_code == 200


def test_short_configured_launch_capability_fails_closed(monkeypatch):
    monkeypatch.setenv(SESSION_BOOTSTRAP_TOKEN_ENV, "too-short")

    response = _client().post("/api/session/bootstrap", json={})

    assert response.status_code == 503


def test_launch_capability_does_not_override_loopback_transport(monkeypatch):
    capability = "per-launch-test-capability-32-bytes"
    monkeypatch.setenv(SESSION_BOOTSTRAP_TOKEN_ENV, capability)

    response = _client("203.0.113.25").post(
        "/api/session/bootstrap",
        json={"launch_token": capability},
    )

    assert response.status_code == 403


def test_main_application_mounts_session_bootstrap_route():
    from main import app

    assert any(
        getattr(route, "path", None) == "/api/session/bootstrap"
        for route in app.routes
    )

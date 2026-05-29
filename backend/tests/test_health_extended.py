"""Tests for the extended health probes (ULTRAPLAN v4).

Exercises a minimal FastAPI app mounting only ``routers/health_extended.py``:

* GET /api/health/ibkr — requires auth; reports connection bool + a read-only
  heartbeat-age proxy. We mock the IBKR connection state (never touch a real
  socket).
* GET /api/health/database-integrity — requires auth; runs PRAGMA
  integrity_check / journal_mode against a real initialised temp DB.

Auth is satisfied by overriding the ``get_current_user`` dependency so the
tests stay focused on this lane and never mint real JWTs. A separate test
confirms the dependency is actually wired (401 without the override).
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import routers.health_extended as health_extended
from auth import get_current_user
from db.core import init_db


# ---------------------------------------------------------------------------
# App / fixtures
# ---------------------------------------------------------------------------

def _make_app(authed: bool = True) -> FastAPI:
    app = FastAPI()
    app.include_router(health_extended.router)
    if authed:
        # Bypass real bearer-token auth; the dependency contract is exercised
        # separately by test_ibkr_health_requires_auth.
        app.dependency_overrides[get_current_user] = lambda: {"id": "demo"}
    return app


@pytest.fixture
def client() -> TestClient:
    return TestClient(_make_app(authed=True))


class _FakeStats:
    def __init__(self, duration: float) -> None:
        self.duration = duration


class _FakeClient:
    def __init__(self, duration: float) -> None:
        self._duration = duration

    def connectionStats(self):
        return _FakeStats(self._duration)


class _FakeIB:
    def __init__(self, duration: float) -> None:
        self.client = _FakeClient(duration)


# ---------------------------------------------------------------------------
# /api/health/ibkr
# ---------------------------------------------------------------------------

def test_ibkr_health_connected(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(health_extended.ibkr, "is_connected", lambda: True)
    monkeypatch.setattr(
        type(health_extended.ibkr), "ib", property(lambda self: _FakeIB(42.5))
    )

    resp = client.get("/api/health/ibkr")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"connected": True, "last_heartbeat_age_s": 42.5}


def test_ibkr_health_disconnected(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(health_extended.ibkr, "is_connected", lambda: False)

    resp = client.get("/api/health/ibkr")
    assert resp.status_code == 200
    body = resp.json()
    assert body["connected"] is False
    # No live socket -> heartbeat age is null, never a stale duration.
    assert body["last_heartbeat_age_s"] is None


def test_ibkr_health_swallows_stats_errors(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A blow-up reading connectionStats must degrade to None, never 500."""

    class _Boom:
        def connectionStats(self):
            raise ConnectionError("not ready")

    class _BoomIB:
        client = _Boom()

    monkeypatch.setattr(health_extended.ibkr, "is_connected", lambda: True)
    monkeypatch.setattr(
        type(health_extended.ibkr), "ib", property(lambda self: _BoomIB())
    )

    resp = client.get("/api/health/ibkr")
    assert resp.status_code == 200
    body = resp.json()
    assert body["connected"] is True
    assert body["last_heartbeat_age_s"] is None


def test_ibkr_health_negative_duration_clamped(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(health_extended.ibkr, "is_connected", lambda: True)
    monkeypatch.setattr(
        type(health_extended.ibkr), "ib", property(lambda self: _FakeIB(-3.0))
    )

    resp = client.get("/api/health/ibkr")
    assert resp.status_code == 200
    assert resp.json()["last_heartbeat_age_s"] == 0.0


def test_ibkr_health_requires_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without the dependency override, the real bearer-token auth applies."""
    app = _make_app(authed=False)
    unauth_client = TestClient(app)

    # Even with a "connected" client, auth must gate the endpoint first.
    monkeypatch.setattr(health_extended.ibkr, "is_connected", lambda: True)

    resp = unauth_client.get("/api/health/ibkr")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# /api/health/database-integrity
# ---------------------------------------------------------------------------

@pytest.fixture
def initialized_db() -> None:
    # conftest points DB_PATH at a real temp file; create the schema so the
    # integrity check runs against a populated, WAL-mode database.
    asyncio.run(init_db())


def test_database_integrity_ok(client: TestClient, initialized_db: None) -> None:
    resp = client.get("/api/health/database-integrity")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"integrity", "journal_mode"}
    assert body["integrity"] == "ok"
    # get_db() forces WAL mode on every connection.
    assert str(body["journal_mode"]).lower() == "wal"


def test_database_integrity_requires_auth(initialized_db: None) -> None:
    app = _make_app(authed=False)
    unauth_client = TestClient(app)
    resp = unauth_client.get("/api/health/database-integrity")
    assert resp.status_code == 401

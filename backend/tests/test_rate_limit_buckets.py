"""General polling must not consume the login budget, and vice versa."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from middleware import RateLimitMiddleware


def _client(general: int, auth: int) -> TestClient:
    app = FastAPI()
    app.add_middleware(RateLimitMiddleware, general_limit=general, auth_limit=auth, window=60)

    @app.get("/api/live/status")
    def status() -> dict[str, bool]:
        return {"ok": True}

    @app.post("/api/auth/token")
    def token() -> dict[str, bool]:
        return {"ok": True}

    return TestClient(app)


def test_polling_does_not_exhaust_the_login_bucket():
    client = _client(general=50, auth=2)
    for _ in range(20):
        assert client.get("/api/live/status").status_code == 200
    assert client.post("/api/auth/token").status_code == 200
    assert client.post("/api/auth/token").status_code == 200
    assert client.post("/api/auth/token").status_code == 429


def test_login_attempts_do_not_consume_general_budget():
    client = _client(general=3, auth=10)
    for _ in range(5):
        client.post("/api/auth/token")
    assert client.get("/api/live/status").status_code == 200

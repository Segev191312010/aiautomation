from fastapi import FastAPI
from fastapi.testclient import TestClient

from middleware import RateLimitMiddleware


def test_session_bootstrap_uses_auth_rate_limit():
    app = FastAPI()
    app.add_middleware(RateLimitMiddleware, general_limit=100, auth_limit=1, window=60)

    @app.post("/api/session/bootstrap")
    async def bootstrap():
        return {"ok": True}

    with TestClient(app) as client:
        assert client.post("/api/session/bootstrap").status_code == 200
        limited = client.post("/api/session/bootstrap")

    assert limited.status_code == 429
    assert limited.headers["retry-after"]

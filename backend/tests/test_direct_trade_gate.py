"""
Batch 3 regression tests — direct-trades HTTP endpoint gate.

Pins three invariants:

1. Endpoint returns 503 when DIRECT_TRADE_INTENT_TOKEN is empty.
2. Endpoint returns 403 when the X-Intent-Token header is wrong / missing.
3. With a correct token, execute_direct_trade is called (the HTTP path
   never reaches the underlying call with skip_safety=True; safety stays
   in the path regardless of payload contents).

Also exercises the startup assertion in startup.validate_startup so an
ENV=live boot without a token refuses to start.
"""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_contracts import AIDirectTrade
from config import cfg
from models import User


@pytest.fixture
def fake_user() -> User:
    return User(id="u-1", email="u1@test", created_at="2026-05-14T00:00:00+00:00", settings={})


@pytest.fixture
def app_with_router(fake_user):
    """Build a FastAPI app with the autopilot router and a stubbed auth dep."""
    from autopilot_api import router as autopilot_router
    from auth import get_current_user

    app = FastAPI()
    app.include_router(autopilot_router)
    app.dependency_overrides[get_current_user] = lambda: fake_user
    return app


@pytest.fixture
def client(app_with_router):
    return TestClient(app_with_router)


def _payload() -> dict:
    trade = AIDirectTrade(
        symbol="AAPL",
        action="BUY",
        quantity=10,
        order_type="LMT",
        limit_price=100.0,
        stop_price=95.0,
        reason="test",
        confidence=0.8,
        invalidation="Break below 95",
    )
    return trade.model_dump()


# ---------------------------------------------------------------------------
# 1. 503 when token unset
# ---------------------------------------------------------------------------


def test_direct_trade_endpoint_503_when_token_empty(client):
    with patch.object(cfg, "DIRECT_TRADE_INTENT_TOKEN", ""):
        resp = client.post(
            "/api/autopilot/direct-trades/execute",
            json=_payload(),
        )
    assert resp.status_code == 503
    body = resp.json()
    detail = body.get("detail") or body  # FastAPI default vs custom shape
    assert "DIRECT_TRADE_INTENT_TOKEN unset" in str(detail) or "disabled" in str(detail).lower()


# ---------------------------------------------------------------------------
# 2. 403 on missing / wrong token
# ---------------------------------------------------------------------------


def test_direct_trade_endpoint_403_when_token_missing(client):
    with patch.object(cfg, "DIRECT_TRADE_INTENT_TOKEN", "s3cret-token"):
        resp = client.post(
            "/api/autopilot/direct-trades/execute",
            json=_payload(),
        )
    assert resp.status_code == 403
    assert "Invalid X-Intent-Token" in str(resp.json())


def test_direct_trade_endpoint_403_when_token_wrong(client):
    with patch.object(cfg, "DIRECT_TRADE_INTENT_TOKEN", "s3cret-token"):
        resp = client.post(
            "/api/autopilot/direct-trades/execute",
            json=_payload(),
            headers={"X-Intent-Token": "wrong"},
        )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# 3. 200 + execute_direct_trade called when token matches
# ---------------------------------------------------------------------------


def test_direct_trade_endpoint_invokes_underlying_when_token_matches(client, caplog):
    import logging

    fake_outcome = {"status": "ok", "trade": {"id": "t-1", "fill_price": 100.0}}

    with patch.object(cfg, "DIRECT_TRADE_INTENT_TOKEN", "s3cret-token"), \
         patch("autopilot_api.execute_direct_trade",
               new=AsyncMock(return_value=fake_outcome)) as mock_exec, \
         caplog.at_level(logging.WARNING):
        resp = client.post(
            "/api/autopilot/direct-trades/execute",
            json=_payload(),
            headers={"X-Intent-Token": "s3cret-token"},
        )

    assert resp.status_code == 200, resp.text
    assert resp.json() == fake_outcome
    mock_exec.assert_awaited_once()

    # The audit log must literally contain "skip_safety=False" so the trail
    # makes it unambiguous that HTTP cannot bypass safety.
    audit_logs = [r.getMessage() for r in caplog.records
                  if "direct_trade_http_invocation" in r.getMessage()]
    assert audit_logs, "must emit direct_trade_http_invocation audit log"
    assert "skip_safety=False" in audit_logs[0]


# ---------------------------------------------------------------------------
# 4. Startup assertion: ENV=live with empty token must refuse to boot
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_startup_refuses_to_boot_when_env_live_and_token_empty():
    """ENV=live + empty DIRECT_TRADE_INTENT_TOKEN -> startup errors out."""
    from startup import validate_startup

    with patch.dict(os.environ, {"ENV": "live", "WORKERS": "1"}, clear=False), \
         patch.object(cfg, "DIRECT_TRADE_INTENT_TOKEN", ""), \
         patch.object(cfg, "STRICT_CONFIG", False):
        with pytest.raises(SystemExit) as exc_info:
            await validate_startup()

    assert exc_info.value.code == 1


@pytest.mark.asyncio
async def test_startup_dev_env_does_not_require_token():
    """Dev/test ENV must NOT require the token (local soak works without it)."""
    from startup import validate_startup

    with patch.dict(os.environ, {"ENV": "dev"}, clear=False), \
         patch.object(cfg, "DIRECT_TRADE_INTENT_TOKEN", ""), \
         patch.object(cfg, "STRICT_CONFIG", False):
        result = await validate_startup()

    # The direct-trade gate error must NOT appear in dev
    assert not any("DIRECT_TRADE_INTENT_TOKEN" in e for e in result["errors"])

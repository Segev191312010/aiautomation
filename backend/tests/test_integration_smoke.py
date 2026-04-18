"""
Phase E4 — integration smoke tests.

Exercises two end-to-end flows:
  1. Order lifecycle via the SIM engine (place BUY → position exists →
     place SELL that closes the position → position list is empty).
     IBKR broker is stubbed entirely; SIM_MODE=true routes /api/orders/manual
     through the SimEngine.
  2. Watchlist persistence via the settings round trip: update watchlist,
     re-fetch, confirm the new list replaced the default.

These tests are deliberately broad (not unit-scoped) so a regression in
routing, auth, request parsing, or persistence surfaces here.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, patch

import aiosqlite
import pytest
from httpx import ASGITransport, AsyncClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("DB_PATH", "test_trading.db")
os.environ["SIM_MODE"] = "true"

from auth import seed_demo_user  # noqa: E402
from config import cfg  # noqa: E402
from database import init_db  # noqa: E402
from main import app  # noqa: E402
from simulation import sim_engine  # noqa: E402


@pytest.fixture
async def seeded_db(tmp_path):
    db_path = str(tmp_path / "integration.db")
    cfg.DB_PATH = db_path
    import database

    database.DB_PATH = db_path
    # Full core schema (users + trades + settings + ...)
    await init_db()
    # SimEngine caches its own db path; point it at the temp db and create
    # its tables.
    sim_engine._db = db_path
    await sim_engine.initialize()
    async with aiosqlite.connect(db_path) as db:
        await seed_demo_user(db)
    yield db_path


@pytest.fixture
async def authed_client(seeded_db):
    """Bootstrap token and return an httpx client with Authorization set."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        headers = {}
        bootstrap = getattr(cfg, "JWT_BOOTSTRAP_SECRET", None)
        if bootstrap:
            headers["X-Bootstrap-Secret"] = bootstrap
        resp = await client.post("/api/auth/token", headers=headers)
        token = resp.json()["access_token"]
        client.headers["Authorization"] = f"Bearer {token}"
        yield client


# ── Test 1: order lifecycle ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_order_lifecycle_buy_then_sell_round_trip(authed_client):
    """BUY opens a position, matching SELL closes it."""
    await sim_engine.reset()

    # Patch market-data lookup so the sim order placer has a fill price
    async def _mock_price(_symbol):
        return 100.0

    with patch("routers.orders.get_latest_price", new=AsyncMock(side_effect=_mock_price)):
        # BUY
        resp = await authed_client.post(
            "/api/orders/manual",
            json={"symbol": "AAPL", "action": "BUY", "quantity": 10},
        )
        assert resp.status_code == 201, resp.text
        assert resp.json().get("sim") is True

        # Confirm position opened
        resp = await authed_client.get("/api/simulation/positions")
        assert resp.status_code == 200
        positions = resp.json()
        assert len(positions) == 1
        assert positions[0]["symbol"] == "AAPL"
        assert positions[0]["qty"] == 10

        # SELL the full position
        resp = await authed_client.post(
            "/api/orders/manual",
            json={"symbol": "AAPL", "action": "SELL", "quantity": 10},
        )
        assert resp.status_code == 201, resp.text

        # Position should be closed
        resp = await authed_client.get("/api/simulation/positions")
        assert resp.status_code == 200
        assert resp.json() == []


@pytest.mark.asyncio
async def test_order_rejected_when_no_market_data(authed_client):
    """Without a price source the manual order path returns 503."""
    await sim_engine.reset()

    async def _none(_symbol):
        return None

    with patch("routers.orders.get_latest_price", new=AsyncMock(side_effect=_none)), patch(
        "yahoo_data.yf_quotes", new=AsyncMock(return_value=[]),
    ):
        resp = await authed_client.post(
            "/api/orders/manual",
            json={"symbol": "XYZZY", "action": "BUY", "quantity": 1},
        )
        assert resp.status_code == 503


# ── Test 2: watchlist persistence round trip ─────────────────────────────────


@pytest.mark.asyncio
async def test_watchlist_persists_across_fetches(authed_client):
    """Update watchlist via /api/settings, re-fetch, verify the new list stuck."""
    new_watchlist = ["AAPL", "TSLA", "NVDA"]

    resp = await authed_client.put("/api/settings", json={"watchlist": new_watchlist})
    assert resp.status_code == 200, resp.text
    assert resp.json()["watchlist"] == new_watchlist

    resp = await authed_client.get("/api/settings")
    assert resp.status_code == 200
    assert resp.json()["watchlist"] == new_watchlist


@pytest.mark.asyncio
async def test_partial_settings_update_does_not_clobber_other_keys(authed_client):
    """Updating default_symbol keeps the previously set watchlist intact."""
    await authed_client.put("/api/settings", json={"watchlist": ["SPY"]})
    await authed_client.put("/api/settings", json={"default_symbol": "QQQ"})

    resp = await authed_client.get("/api/settings")
    assert resp.status_code == 200
    body = resp.json()
    assert body["default_symbol"] == "QQQ"
    assert body["watchlist"] == ["SPY"]

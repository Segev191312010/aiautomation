"""Tests for GET /api/account/day-pnl (account_routes).

Seeds trades rows directly via get_db (no trade_service dependency) and asserts:
  * zeros when there are no trades today,
  * correct realized sum for today's fills,
  * ET-boundary selection (yesterday's ET fills are excluded; a UTC timestamp
    that is "today" in UTC but still "yesterday" in ET is also excluded).
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth import get_current_user
from db.core import get_db, init_db
from models import User
from routers.account_routes import _et_day_start_utc, router

_ET = ZoneInfo("America/New_York")


def _build_client() -> TestClient:
    """Minimal app with just the account router and auth overridden."""
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: User(
        id="demo", email="demo@example.com", created_at="2026-01-01T00:00:00+00:00"
    )
    return TestClient(app)


async def _clear_trades() -> None:
    async with get_db() as db:
        await db.execute("DELETE FROM trades")
        await db.commit()


async def _seed_trade(
    *,
    ts_utc: datetime,
    realized_pnl: float | None = None,
    metadata_pnl: float | None = None,
    user_id: str = "demo",
) -> None:
    """Insert a single trades row with the given UTC timestamp and P&L.

    Mirrors the real write path: top-level ``timestamp`` column holds the UTC
    ISO string and ``data`` is the Trade JSON blob (realized_pnl canonical,
    metadata.pnl legacy fallback).
    """
    tid = str(uuid.uuid4())
    iso = ts_utc.astimezone(timezone.utc).isoformat()
    data: dict = {
        "id": tid,
        "symbol": "AAPL",
        "action": "SELL",
        "timestamp": iso,
        "metadata": {},
    }
    if realized_pnl is not None:
        data["realized_pnl"] = realized_pnl
    if metadata_pnl is not None:
        data["metadata"]["pnl"] = metadata_pnl
    async with get_db() as db:
        await db.execute(
            "INSERT INTO trades (id, rule_id, symbol, action, timestamp, data, user_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (tid, "r1", "AAPL", "SELL", iso, json.dumps(data), user_id),
        )
        await db.commit()


@pytest.fixture
def client():
    import anyio

    anyio.run(init_db)
    anyio.run(_clear_trades)
    c = _build_client()
    yield c
    anyio.run(_clear_trades)


def test_zeros_when_no_trades(client):
    resp = client.get("/api/account/day-pnl")
    assert resp.status_code == 200
    body = resp.json()
    assert body["realized"] == 0.0
    assert body["unrealized"] == 0.0
    assert body["total"] == 0.0
    assert body["count_trades_today"] == 0


def test_realized_sum_for_today(client):
    import anyio

    # Two fills today (one canonical, one legacy metadata.pnl), one yesterday.
    day_start = _et_day_start_utc()
    today_a = day_start + timedelta(hours=1)
    today_b = day_start + timedelta(hours=2)
    yesterday = day_start - timedelta(hours=3)

    anyio.run(lambda: _seed_trade(ts_utc=today_a, realized_pnl=125.50))
    anyio.run(lambda: _seed_trade(ts_utc=today_b, metadata_pnl=-25.25))
    anyio.run(lambda: _seed_trade(ts_utc=yesterday, realized_pnl=999.99))

    body = client.get("/api/account/day-pnl").json()
    # 125.50 + (-25.25) = 100.25; yesterday excluded.
    assert body["realized"] == 100.25
    assert body["unrealized"] == 0.0
    assert body["total"] == 100.25
    assert body["count_trades_today"] == 2


def test_et_boundary_excludes_pre_et_midnight(client):
    """A fill that is 'today' in UTC but still 'yesterday' in ET is excluded."""
    import anyio

    now_et = datetime.now(timezone.utc).astimezone(_ET)
    et_midnight = now_et.replace(hour=0, minute=0, second=0, microsecond=0)

    # One minute BEFORE ET midnight — belongs to the previous ET trading day.
    before_et = (et_midnight - timedelta(minutes=1)).astimezone(timezone.utc)
    # One minute AFTER ET midnight — belongs to today's ET trading day.
    after_et = (et_midnight + timedelta(minutes=1)).astimezone(timezone.utc)

    anyio.run(lambda: _seed_trade(ts_utc=before_et, realized_pnl=50.0))
    anyio.run(lambda: _seed_trade(ts_utc=after_et, realized_pnl=70.0))

    body = client.get("/api/account/day-pnl").json()
    assert body["count_trades_today"] == 1
    assert body["realized"] == 70.0


def test_et_day_start_is_utc_aware_and_dst_correct():
    """The ET boundary resolves the correct UTC offset across DST."""
    # Winter (EST, -05:00): 2026-01-15 ET midnight == 05:00 UTC.
    jan = datetime(2026, 1, 15, 18, 0, tzinfo=timezone.utc)
    start = _et_day_start_utc(jan)
    assert start.tzinfo == timezone.utc
    assert start == datetime(2026, 1, 15, 5, 0, tzinfo=timezone.utc)

    # Summer (EDT, -04:00): 2026-07-15 ET midnight == 04:00 UTC.
    jul = datetime(2026, 7, 15, 18, 0, tzinfo=timezone.utc)
    start = _et_day_start_utc(jul)
    assert start == datetime(2026, 7, 15, 4, 0, tzinfo=timezone.utc)

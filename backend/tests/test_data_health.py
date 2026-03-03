"""
Tests for data freshness monitor and data health endpoint.
"""
from __future__ import annotations

import os
import sys
import time

import pytest
from httpx import ASGITransport, AsyncClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("DB_PATH", "test_trading.db")
os.environ.setdefault("SIM_MODE", "true")
os.environ.setdefault("MOCK_MODE", "true")

from data_health import DataFreshnessMonitor


def test_data_freshness_status_transitions():
    monitor = DataFreshnessMonitor({"quotes": 0.1})

    snap = monitor.snapshot()
    assert snap["sources"]["quotes"]["status"] == "unknown"

    monitor.record_success("quotes", count=3, duration_ms=12.5)
    snap = monitor.snapshot()
    assert snap["sources"]["quotes"]["status"] == "fresh"
    assert snap["sources"]["quotes"]["last_count"] == 3

    time.sleep(0.15)
    snap = monitor.snapshot()
    assert snap["sources"]["quotes"]["status"] == "stale"

    time.sleep(0.22)
    snap = monitor.snapshot()
    assert snap["sources"]["quotes"]["status"] == "critical"


def test_data_freshness_failure_escalation():
    monitor = DataFreshnessMonitor({"ws": 5.0})
    monitor.record_success("ws")
    for _ in range(5):
        monitor.record_failure("ws", "upstream timeout")
    snap = monitor.snapshot()
    assert snap["sources"]["ws"]["status"] == "critical"
    assert snap["sources"]["ws"]["consecutive_failures"] == 5


@pytest.fixture
async def client():
    db_path = os.path.join(os.path.dirname(__file__), "_test_data_health.db")
    try:
        os.remove(db_path)
    except FileNotFoundError:
        pass

    os.environ["MARKET_HEARTBEAT_ENABLED"] = "false"
    from config import cfg
    cfg.DB_PATH = db_path
    import database
    database.DB_PATH = db_path
    from main import app

    await database.init_db()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    try:
        os.remove(db_path)
    except FileNotFoundError:
        pass


@pytest.mark.asyncio
async def test_data_health_endpoint_shape(client):
    resp = await client.get("/api/data/health")
    assert resp.status_code == 200
    body = resp.json()
    assert "timestamp" in body
    assert "overall_status" in body
    assert "sources" in body
    assert "watchlist_quotes" in body["sources"]
    assert "ws_quotes" in body["sources"]
    assert "ws_ibkr_quotes" in body["sources"]
    assert "ws_yahoo_quotes" in body["sources"]
    assert "yahoo_bars" in body["sources"]
    assert "heartbeat_quotes" in body["sources"]
    assert "streaming" in body
    assert "push_interval_s" in body["streaming"]

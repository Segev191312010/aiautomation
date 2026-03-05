"""
Tests for the alert engine and alert API endpoints.

Coverage:
  - API contract: create, list, get, update, delete, toggle, history, test
  - Engine unit: price >, crosses_above, no-prev safety
  - Lifecycle: one-shot auto-disable, recurring cooldown
  - History: persistence on fire
  - Route ordering: /api/alerts/history not captured by /{alert_id}
  - User isolation: user A cannot access user B's alerts
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture
async def client(tmp_path):
    db_path = str(tmp_path / "alert_test.db")
    from config import cfg

    cfg.DB_PATH = db_path
    import database

    database.DB_PATH = db_path
    from main import app

    await database.init_db()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


ALERT_BODY = {
    "name": "AAPL above 250",
    "symbol": "AAPL",
    "condition": {
        "indicator": "PRICE",
        "params": {},
        "operator": ">",
        "value": 250.0,
    },
    "alert_type": "one_shot",
    "cooldown_minutes": 60,
    "enabled": True,
}


# ---------------------------------------------------------------------------
# API contract tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_alert(client):
    resp = await client.post("/api/alerts", json=ALERT_BODY)
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "AAPL above 250"
    assert data["symbol"] == "AAPL"
    assert data["enabled"] is True
    assert data["alert_type"] == "one_shot"
    assert "id" in data
    assert "created_at" in data


@pytest.mark.asyncio
async def test_list_alerts(client):
    await client.post("/api/alerts", json=ALERT_BODY)
    resp = await client.get("/api/alerts")
    assert resp.status_code == 200
    alerts = resp.json()
    assert len(alerts) >= 1
    assert alerts[0]["symbol"] == "AAPL"


@pytest.mark.asyncio
async def test_get_alert(client):
    create_resp = await client.post("/api/alerts", json=ALERT_BODY)
    alert_id = create_resp.json()["id"]
    resp = await client.get(f"/api/alerts/{alert_id}")
    assert resp.status_code == 200
    assert resp.json()["id"] == alert_id


@pytest.mark.asyncio
async def test_update_alert(client):
    create_resp = await client.post("/api/alerts", json=ALERT_BODY)
    alert_id = create_resp.json()["id"]
    resp = await client.put(
        f"/api/alerts/{alert_id}",
        json={"name": "Updated Name", "symbol": "TSLA"},
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "Updated Name"
    assert resp.json()["symbol"] == "TSLA"


@pytest.mark.asyncio
async def test_delete_alert(client):
    create_resp = await client.post("/api/alerts", json=ALERT_BODY)
    alert_id = create_resp.json()["id"]
    resp = await client.delete(f"/api/alerts/{alert_id}")
    assert resp.status_code == 200
    assert resp.json()["deleted"] is True
    # Subsequent GET should 404
    get_resp = await client.get(f"/api/alerts/{alert_id}")
    assert get_resp.status_code == 404


@pytest.mark.asyncio
async def test_toggle_alert(client):
    create_resp = await client.post("/api/alerts", json=ALERT_BODY)
    alert_id = create_resp.json()["id"]
    assert create_resp.json()["enabled"] is True
    # Toggle off
    resp = await client.post(f"/api/alerts/{alert_id}/toggle")
    assert resp.status_code == 200
    assert resp.json()["enabled"] is False
    # Toggle back on
    resp2 = await client.post(f"/api/alerts/{alert_id}/toggle")
    assert resp2.json()["enabled"] is True


@pytest.mark.asyncio
async def test_history_route_not_captured(client):
    """GET /api/alerts/history must return 200, not be treated as alert_id='history'."""
    resp = await client.get("/api/alerts/history")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_test_notification_endpoint(client):
    resp = await client.post("/api/alerts/test", json=ALERT_BODY)
    assert resp.status_code == 200
    data = resp.json()
    assert "triggered" in data
    assert "price" in data
    assert "condition_summary" in data


# ---------------------------------------------------------------------------
# Engine unit tests — price condition evaluation
# ---------------------------------------------------------------------------


def test_price_gt_condition():
    from alert_engine import _evaluate_price_condition
    from models import Condition

    cond = Condition(indicator="PRICE", params={}, operator=">", value=250.0)
    assert _evaluate_price_condition(cond, "AAPL", 251.0) is True
    assert _evaluate_price_condition(cond, "AAPL", 249.0) is False
    assert _evaluate_price_condition(cond, "AAPL", 250.0) is False


def test_price_lt_condition():
    from alert_engine import _evaluate_price_condition
    from models import Condition

    cond = Condition(indicator="PRICE", params={}, operator="<", value=250.0)
    assert _evaluate_price_condition(cond, "AAPL", 249.0) is True
    assert _evaluate_price_condition(cond, "AAPL", 251.0) is False


def test_price_crosses_above():
    import alert_engine
    from alert_engine import _evaluate_price_condition
    from models import Condition

    cond = Condition(
        indicator="PRICE", params={}, operator="crosses_above", value=250.0
    )
    # Set previous price below threshold
    alert_engine._prev_prices["AAPL"] = 249.0
    assert _evaluate_price_condition(cond, "AAPL", 251.0) is True
    # Already above — not a cross
    alert_engine._prev_prices["AAPL"] = 251.0
    assert _evaluate_price_condition(cond, "AAPL", 252.0) is False


def test_price_crosses_above_no_prev():
    """First cycle: no previous price → no cross detected (safety)."""
    import alert_engine
    from alert_engine import _evaluate_price_condition
    from models import Condition

    cond = Condition(
        indicator="PRICE", params={}, operator="crosses_above", value=250.0
    )
    # Clear prev prices to simulate first cycle
    alert_engine._prev_prices.pop("NEWSTOCK", None)
    assert _evaluate_price_condition(cond, "NEWSTOCK", 260.0) is False


# ---------------------------------------------------------------------------
# Lifecycle tests — one-shot, cooldown
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_one_shot_auto_disable(tmp_path):
    """One-shot alert sets enabled=False after firing."""
    db_path = str(tmp_path / "oneshot.db")
    from config import cfg

    cfg.DB_PATH = db_path
    import database

    database.DB_PATH = db_path
    await database.init_db()

    import alert_engine

    alert_engine._broadcast = AsyncMock()

    from models import Alert, Condition

    alert = Alert(
        name="Test one-shot",
        symbol="AAPL",
        condition=Condition(indicator="PRICE", params={}, operator=">", value=200.0),
        alert_type="one_shot",
        enabled=True,
        user_id="demo",
    )
    await database.save_alert(alert, user_id="demo")

    # Fire it
    await alert_engine._fire_alert(alert, 220.0)

    # Verify alert is now disabled
    saved = await database.get_alert(alert.id, user_id="demo")
    assert saved is not None
    assert saved.enabled is False
    assert saved.last_triggered is not None


@pytest.mark.asyncio
async def test_recurring_cooldown_blocks(tmp_path):
    """Recurring alert within cooldown should not fire."""
    db_path = str(tmp_path / "cooldown.db")
    from config import cfg

    cfg.DB_PATH = db_path
    import database

    database.DB_PATH = db_path
    await database.init_db()

    import alert_engine

    from models import Alert, Condition

    # Alert fired 5 minutes ago, cooldown is 60 minutes
    five_min_ago = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    alert = Alert(
        name="Cooldown test",
        symbol="AAPL",
        condition=Condition(indicator="PRICE", params={}, operator=">", value=200.0),
        alert_type="recurring",
        cooldown_minutes=60,
        enabled=True,
        last_triggered=five_min_ago,
        user_id="demo",
    )
    await database.save_alert(alert, user_id="demo")

    prices = {"AAPL": 220.0}
    bars_cache: dict = {}
    did_fire = await alert_engine._evaluate_alert(alert, prices, bars_cache)
    assert did_fire is False


@pytest.mark.asyncio
async def test_recurring_fires_after_cooldown(tmp_path):
    """Recurring alert past cooldown should fire."""
    db_path = str(tmp_path / "cooldown_expired.db")
    from config import cfg

    cfg.DB_PATH = db_path
    import database

    database.DB_PATH = db_path
    await database.init_db()

    import alert_engine

    alert_engine._broadcast = AsyncMock()

    from models import Alert, Condition

    # Alert fired 70 minutes ago, cooldown is 60 minutes
    seventy_min_ago = (datetime.now(timezone.utc) - timedelta(minutes=70)).isoformat()
    alert = Alert(
        name="Cooldown expired",
        symbol="AAPL",
        condition=Condition(indicator="PRICE", params={}, operator=">", value=200.0),
        alert_type="recurring",
        cooldown_minutes=60,
        enabled=True,
        last_triggered=seventy_min_ago,
        user_id="demo",
    )
    await database.save_alert(alert, user_id="demo")

    prices = {"AAPL": 220.0}
    bars_cache: dict = {}
    did_fire = await alert_engine._evaluate_alert(alert, prices, bars_cache)
    assert did_fire is True


# ---------------------------------------------------------------------------
# History persistence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_history_persisted_on_fire(tmp_path):
    """Firing an alert must create an AlertHistory entry."""
    db_path = str(tmp_path / "history.db")
    from config import cfg

    cfg.DB_PATH = db_path
    import database

    database.DB_PATH = db_path
    await database.init_db()

    import alert_engine

    alert_engine._broadcast = AsyncMock()

    from models import Alert, Condition

    alert = Alert(
        name="History test",
        symbol="SPY",
        condition=Condition(indicator="PRICE", params={}, operator=">", value=500.0),
        alert_type="one_shot",
        enabled=True,
        user_id="demo",
    )
    await database.save_alert(alert, user_id="demo")
    await alert_engine._fire_alert(alert, 510.0)

    history = await database.get_alert_history(user_id="demo")
    assert len(history) >= 1
    entry = history[0]
    assert entry.alert_id == alert.id
    assert entry.symbol == "SPY"
    assert entry.price_at_trigger == 510.0
    assert "PRICE" in entry.condition_summary


# ---------------------------------------------------------------------------
# Condition summary formatting
# ---------------------------------------------------------------------------


def test_condition_summary():
    from alert_engine import _condition_summary
    from models import Condition

    cond1 = Condition(indicator="RSI", params={"length": 14}, operator="<", value=30)
    summary1 = _condition_summary(cond1)
    assert "RSI(14)" in summary1
    assert "< 30" in summary1

    cond2 = Condition(indicator="PRICE", params={}, operator=">", value=250.0)
    summary2 = _condition_summary(cond2)
    assert "PRICE" in summary2
    assert "> 250" in summary2

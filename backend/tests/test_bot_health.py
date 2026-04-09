from __future__ import annotations

from datetime import datetime, timedelta, timezone
import time

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

import bot_runner
import bot_health
from config import cfg
from health import router as health_router


@pytest.fixture(autouse=True)
def restore_health_state():
    previous = {
        "enabled": cfg.ENABLE_BOT_HEALTH_MONITORING,
        "running": bot_runner._running,
        "cycle_count": bot_health._cycle_count_today,
        "last_cycle_completed_at": bot_health._last_cycle_completed_at,
        "last_signal_symbol": bot_health._last_signal_symbol,
        "last_error_message": bot_health._last_error_message,
        "error_timestamps": list(bot_health._error_timestamps),
        "degraded_timestamps": list(bot_health._degraded_timestamps),
    }
    try:
        yield
    finally:
        cfg.ENABLE_BOT_HEALTH_MONITORING = previous["enabled"]
        bot_runner._running = previous["running"]
        bot_health._cycle_count_today = previous["cycle_count"]
        bot_health._last_cycle_completed_at = previous["last_cycle_completed_at"]
        bot_health._last_signal_symbol = previous["last_signal_symbol"]
        bot_health._last_error_message = previous["last_error_message"]
        bot_health._error_timestamps[:] = previous["error_timestamps"]
        bot_health._degraded_timestamps[:] = previous["degraded_timestamps"]


def test_get_bot_health_reports_stale_warning():
    cfg.ENABLE_BOT_HEALTH_MONITORING = True
    bot_runner._running = True
    bot_health._cycle_count_today = 7
    bot_health._last_signal_symbol = "AAPL"
    bot_health._last_error_message = "timeout"
    stale_threshold = max(cfg.BOT_INTERVAL_SECONDS * 2, 60)
    bot_health._last_cycle_completed_at = (
        datetime.now(timezone.utc) - timedelta(seconds=stale_threshold + 5)
    ).isoformat()
    now_ts = time.time()
    bot_health._error_timestamps[:] = [now_ts - 10, now_ts - 5]
    bot_health._degraded_timestamps[:] = [now_ts - 3]

    health = bot_runner.get_bot_health()

    assert health["is_running"] is True
    assert health["stale_warning"] is True
    assert health["total_cycles_today"] == 7
    assert health["last_signal_symbol"] == "AAPL"
    assert health["error_count_24h"] == 2
    assert health["degraded_mode_count_24h"] == 1


@pytest.mark.asyncio
async def test_bot_health_endpoint_shape():
    cfg.ENABLE_BOT_HEALTH_MONITORING = True
    bot_runner._running = True
    bot_health._cycle_count_today = 4
    bot_health._last_cycle_completed_at = datetime.now(timezone.utc).isoformat()
    bot_health._last_signal_symbol = "MSFT"
    bot_health._error_timestamps[:] = []
    bot_health._degraded_timestamps[:] = []

    app = FastAPI()
    app.include_router(health_router)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/health/bot")

    assert response.status_code == 200
    payload = response.json()
    assert payload["monitoring_enabled"] is True
    assert payload["is_running"] is True
    assert payload["total_cycles_today"] == 4
    assert payload["last_signal_symbol"] == "MSFT"
    assert "minutes_since_last_cycle" in payload

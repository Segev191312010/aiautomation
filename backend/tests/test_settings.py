"""
Tests for settings module — get/update, JSON deep merge.
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("DB_PATH", "test_trading.db")
os.environ.setdefault("SIM_MODE", "true")
os.environ.setdefault("MOCK_MODE", "true")

import pytest
import aiosqlite
from config import cfg
from auth import seed_demo_user
from settings import get_settings, update_settings, DEFAULT_SETTINGS, _deep_merge


@pytest.fixture(autouse=True)
async def setup_db(tmp_path):
    db_path = str(tmp_path / "test.db")
    cfg.DB_PATH = db_path
    import database
    database.DB_PATH = db_path
    async with aiosqlite.connect(db_path) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY, email TEXT UNIQUE,
                password_hash TEXT, created_at TEXT, settings TEXT DEFAULT '{}'
            )
        """)
        await db.commit()
        await seed_demo_user(db)
    yield


@pytest.mark.asyncio
async def test_get_settings_returns_defaults():
    settings = await get_settings("demo")
    assert settings["theme"] == "dark"
    assert settings["default_symbol"] == "SPY"
    assert "watchlist" in settings
    assert isinstance(settings["watchlist"], list)


@pytest.mark.asyncio
async def test_update_settings_partial_merge():
    updated = await update_settings("demo", {"default_symbol": "AAPL"})
    assert updated["default_symbol"] == "AAPL"
    # Other defaults should still be present
    assert updated["theme"] == "dark"
    assert updated["bot_interval"] == 60


@pytest.mark.asyncio
async def test_update_settings_watchlist():
    updated = await update_settings("demo", {"watchlist": ["SPY", "QQQ"]})
    assert updated["watchlist"] == ["SPY", "QQQ"]


def test_deep_merge_nested():
    base = {"a": {"b": 1, "c": 2}, "d": 3}
    overlay = {"a": {"b": 10}, "e": 5}
    result = _deep_merge(base, overlay)
    assert result == {"a": {"b": 10, "c": 2}, "d": 3, "e": 5}


def test_deep_merge_does_not_mutate_base():
    base = {"a": 1}
    overlay = {"b": 2}
    result = _deep_merge(base, overlay)
    assert "b" not in base
    assert result == {"a": 1, "b": 2}

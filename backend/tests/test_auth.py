"""
Tests for auth module — token creation, verification, demo user seeding.
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("DB_PATH", "test_trading.db")
os.environ.setdefault("SIM_MODE", "true")
os.environ.setdefault("MOCK_MODE", "true")

import pytest
import aiosqlite
from config import cfg
from auth import create_token, verify_token, seed_demo_user, get_user, DEMO_USER_ID


@pytest.fixture(autouse=True)
async def setup_db(tmp_path):
    db_path = str(tmp_path / "test.db")
    cfg.DB_PATH = db_path
    # Import database to use its DB_PATH
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
    # cleanup happens via tmp_path


@pytest.mark.asyncio
async def test_create_and_verify_token():
    token = create_token("demo")
    assert isinstance(token, str)
    assert len(token) > 20
    user_id = verify_token(token)
    assert user_id == "demo"


@pytest.mark.asyncio
async def test_verify_invalid_token():
    result = verify_token("invalid.token.value")
    assert result is None


@pytest.mark.asyncio
async def test_demo_user_seeded():
    user = await get_user(DEMO_USER_ID)
    assert user is not None
    assert user.id == "demo"
    assert user.email == "demo@local"


@pytest.mark.asyncio
async def test_seed_demo_user_idempotent(tmp_path):
    """Seeding twice should not raise or duplicate."""
    db_path = cfg.DB_PATH
    async with aiosqlite.connect(db_path) as db:
        await seed_demo_user(db)
    user = await get_user(DEMO_USER_ID)
    assert user is not None

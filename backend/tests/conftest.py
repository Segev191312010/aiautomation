"""
Shared test fixtures.
"""
import os
import sys
import tempfile
import pytest

# Ensure backend package is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Use a real temp file for the test DB.
# SQLite ":memory:" gives each aiosqlite.connect() its own independent
# database, so init_db() creates tables in one connection while CRUD
# functions open a second (empty) connection — tables not found.
_TEST_DB = os.path.join(tempfile.gettempdir(), "test_trading_platform.db")
if os.path.exists(_TEST_DB):
    os.unlink(_TEST_DB)
os.environ["DB_PATH"] = _TEST_DB
os.environ["SIM_MODE"] = "true"


@pytest.fixture
def anyio_backend():
    return "asyncio"

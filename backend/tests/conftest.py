"""
Shared test fixtures.
"""
import os
import shutil
import sys
import pytest

# Ensure backend package is importable
_BACKEND_DIR = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, _BACKEND_DIR)

# Repo-local temp root avoids Windows permission issues with the global
# temp directory that pytest uses by default.
_LOCAL_TMP = os.path.join(_BACKEND_DIR, ".tmp", "pytest")
os.makedirs(_LOCAL_TMP, exist_ok=True)

# Use a real temp file for the test DB.
# SQLite ":memory:" gives each aiosqlite.connect() its own independent
# database, so init_db() creates tables in one connection while CRUD
# functions open a second (empty) connection — tables not found.
_TEST_DB = os.path.join(_LOCAL_TMP, "test_trading_platform.db")
if os.path.exists(_TEST_DB):
    os.unlink(_TEST_DB)
os.environ["DB_PATH"] = _TEST_DB
os.environ["SIM_MODE"] = "true"


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def tmp_path(request, tmp_path_factory):
    """Repo-local tmp_path that avoids Windows permission errors."""
    p = os.path.join(_LOCAL_TMP, request.node.name)
    os.makedirs(p, exist_ok=True)
    yield type(tmp_path_factory.getbasetemp())(p)
    # Best-effort cleanup; Windows may hold file locks
    shutil.rmtree(p, ignore_errors=True)


@pytest.fixture(autouse=True)
def _restore_db_path():
    """Restore database.DB_PATH after tests that override it."""
    import database
    from config import cfg
    original_db = database.DB_PATH
    original_cfg = cfg.DB_PATH
    yield
    database.DB_PATH = original_db
    cfg.DB_PATH = original_cfg

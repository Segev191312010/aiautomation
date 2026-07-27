"""
Shared test fixtures.
"""
import asyncio
import os
import shutil
import sys
from pathlib import Path

import pytest

# eventkit (dependency of ib_insync) calls asyncio.get_event_loop() at import
# time.  Python 3.12+ no longer creates an implicit event loop in the main
# thread, so pytest collection fails on any module importing ib_insync.  Create
# a default loop here, before collection imports those modules.
try:
    asyncio.get_running_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

# Ensure backend package is importable
_BACKEND_DIR = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, _BACKEND_DIR)

# Repo-local temp root avoids Windows permission issues with the global
# temp directory that pytest uses by default.  Never touch getbasetemp()
# or tempfile.gettempdir() — both can hit unwritable global paths on
# Windows cold starts.
_LOCAL_TMP = os.path.join(os.path.abspath(_BACKEND_DIR), ".tmp", "pytest")
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

# Relaxed rate limits for tests — avoid 429s when multiple test files
# hit /api/auth/token in the same test session.
os.environ.setdefault("TEST_RATE_LIMIT_GENERAL", "10000")
os.environ.setdefault("TEST_RATE_LIMIT_AUTH", "10000")

# Set JWT_BOOTSTRAP_SECRET for tests so /api/auth/token works
os.environ.setdefault("JWT_BOOTSTRAP_SECRET", "test-bootstrap-secret")


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(scope="session", autouse=True)
async def _test_execution_lease():
    """Hold a durable execution lease for the whole test session.

    Phase 1 (ADR 0006) requires a valid fencing token at every broker
    mutation path.  Tests exercise those paths without a lifespan, so we
    acquire a single lease here and publish it into startup's process-global
    state.  validate_startup() detects the existing lease and skips
    re-acquisition, and order_executor / safety_kernel paths see a valid token.
    """
    import startup
    from db.execution_lease import acquire_execution_lease, release_execution_lease

    # _TEST_DB is recreated at module import time, so no stale lease exists.
    lease = await acquire_execution_lease(owner_id="pytest-session")
    startup._execution_lease = lease
    yield
    startup._execution_lease = None
    await release_execution_lease(lease.fencing_token)


@pytest.fixture
def tmp_path(request):
    """Repo-local tmp_path that never touches the global temp root.

    Uses pathlib.Path directly instead of tmp_path_factory.getbasetemp()
    which would create dirs inside the unwritable global temp on Windows.
    """
    p = Path(_LOCAL_TMP) / request.node.name
    p.mkdir(parents=True, exist_ok=True)
    yield p
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

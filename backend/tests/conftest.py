"""
Shared test fixtures.
"""
import os
import tempfile
import shutil
import sys
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import pytest

# Ensure backend package is importable
_BACKEND_DIR = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, _BACKEND_DIR)

# Repo-local temp root avoids Windows permission issues with the global
# temp directory that pytest uses by default.  Never touch getbasetemp()
# or tempfile.gettempdir() — both can hit unwritable global paths on
# Windows cold starts.
_LOCAL_TMP = os.path.join(
    os.path.abspath(_BACKEND_DIR),
    ".tmp",
    "pytest",
    f"{os.getpid()}-{uuid4().hex}",
)
os.makedirs(_LOCAL_TMP, exist_ok=True)

# Use one real SQLite file per pytest process because each aiosqlite connection
# gets a distinct in-memory database.  A run-unique root prevents a crashed or
# concurrently running suite from leaking state into another process.
_TEST_DB = os.path.join(_LOCAL_TMP, "test_trading_platform.db")
os.environ["DB_PATH"] = _TEST_DB
# Nothing under test may read the operator's real home (the live config, the live
# database, LaunchAgents): every Path.home() in the suite resolves to a scratch dir.
_TEST_HOME = tempfile.mkdtemp(prefix="autotrader-test-home-")
os.environ["HOME"] = _TEST_HOME

# Relaxed rate limits for tests — avoid 429s when multiple test files
# hit /api/auth/token in the same test session.
os.environ.setdefault("TEST_RATE_LIMIT_GENERAL", "10000")
os.environ.setdefault("TEST_RATE_LIMIT_AUTH", "10000")

# Set JWT_BOOTSTRAP_SECRET for tests so /api/auth/token works
os.environ.setdefault("JWT_BOOTSTRAP_SECRET", "test-bootstrap-secret")


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def tmp_path(request):
    """Repo-local tmp_path that never touches the global temp root.

    Uses pathlib.Path directly instead of tmp_path_factory.getbasetemp()
    which would create dirs inside the unwritable global temp on Windows.
    """
    node_digest = sha256(request.node.nodeid.encode("utf-8")).hexdigest()[:12]
    p = Path(_LOCAL_TMP) / f"{request.node.name}-{node_digest}"
    p.mkdir(parents=True, exist_ok=True)
    yield p
    # Best-effort cleanup; Windows may hold file locks
    shutil.rmtree(p, ignore_errors=True)


# The live IB Gateway's PAPER API port. A /status, a supervisor exit or a
# deploy check under test must use a fake; reaching the real Gateway from a
# test is a defect even when nothing is listening (review P1-A F-11).
LIVE_GATEWAY_PORT = 7497


class GatewayPortGuard:
    """Records every refused connect to the live Gateway port during one test."""

    def __init__(self) -> None:
        self.attempts: list[object] = []


def _address_port(address: object) -> object:
    if isinstance(address, tuple) and len(address) >= 2:
        return address[1]
    return None


@pytest.fixture(autouse=True)
def no_live_gateway_port(monkeypatch) -> GatewayPortGuard:
    """Suite-wide: a connect to port 7497 is refused (as if closed) and fails the
    test at teardown, whatever module made it and whatever caught the error."""
    import socket

    guard = GatewayPortGuard()
    connect = socket.socket.connect
    connect_ex = socket.socket.connect_ex

    def guarded_connect(self, address):
        if _address_port(address) == LIVE_GATEWAY_PORT:
            guard.attempts.append(address)
            raise ConnectionRefusedError(61, "test guard: the live Gateway port 7497 is off limits")
        return connect(self, address)

    def guarded_connect_ex(self, address):
        if _address_port(address) == LIVE_GATEWAY_PORT:
            guard.attempts.append(address)
            return 61
        return connect_ex(self, address)

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", guarded_connect_ex)
    yield guard
    assert not guard.attempts, f"a test tried to connect to the live Gateway port: {guard.attempts}"


@pytest.fixture(autouse=True)
def _restore_db_path():
    """Restore the configured database path after each test."""
    from config import cfg
    original_cfg = cfg.DB_PATH
    yield
    cfg.DB_PATH = original_cfg

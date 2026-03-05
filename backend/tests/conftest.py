"""
Shared test fixtures.
"""
import os
import sys
import pytest

# Ensure backend package is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Use an in-memory / temp DB for tests
os.environ["DB_PATH"] = ":memory:"
os.environ["SIM_MODE"] = "true"


@pytest.fixture
def anyio_backend():
    return "asyncio"

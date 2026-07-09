"""App-level runtime lock startup/shutdown regressions."""
from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient

from runtime_lock import RuntimeLock, RuntimeLockError


def test_app_startup_acquires_and_shutdown_releases_runtime_lock(tmp_path, monkeypatch):
    import main
    from config import cfg

    lock_path = tmp_path / "runtime.lock"
    monkeypatch.setattr(cfg, "RUNTIME_LOCK_PATH", str(lock_path), raising=False)

    @asynccontextmanager
    async def no_side_effect_lifespan(_app):
        assert lock_path.exists()
        yield

    monkeypatch.setattr(main, "_run_lifespan", no_side_effect_lifespan)

    with TestClient(main.app):
        metadata = json.loads(lock_path.read_text(encoding="utf-8"))
        assert metadata["pid"] == os.getpid()

    assert not lock_path.exists()


def test_duplicate_runtime_refuses_startup_before_side_effects(tmp_path, monkeypatch):
    import main
    from config import cfg

    lock_path = tmp_path / "runtime.lock"
    monkeypatch.setattr(cfg, "RUNTIME_LOCK_PATH", str(lock_path), raising=False)

    holder = RuntimeLock(lock_path)
    holder.acquire()
    side_effect_started = False

    @asynccontextmanager
    async def fail_if_called(_app):
        nonlocal side_effect_started
        side_effect_started = True
        raise AssertionError("stateful startup should not run when runtime lock is held")
        yield

    monkeypatch.setattr(main, "_run_lifespan", fail_if_called)

    try:
        with pytest.raises(RuntimeLockError, match="already running"):
            with TestClient(main.app):
                pass
        assert side_effect_started is False
    finally:
        holder.release()


def test_stale_runtime_lock_is_reclaimed_then_released(tmp_path, monkeypatch):
    import main
    from config import cfg

    lock_path = tmp_path / "runtime.lock"
    lock_path.write_text(
        json.dumps(
            {
                "hostname": "old-host",
                "pid": 0,
                "started_at_utc": "2026-07-09T00:00:00+00:00",
                "token": "old-token",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(cfg, "RUNTIME_LOCK_PATH", str(lock_path), raising=False)

    @asynccontextmanager
    async def no_side_effect_lifespan(_app):
        metadata = json.loads(lock_path.read_text(encoding="utf-8"))
        assert metadata["pid"] == os.getpid()
        assert metadata["token"] != "old-token"
        yield

    monkeypatch.setattr(main, "_run_lifespan", no_side_effect_lifespan)

    with TestClient(main.app):
        assert lock_path.exists()

    assert not lock_path.exists()

"""Runtime lock unit regressions for the stateful backend."""
from __future__ import annotations

import json
import os

import pytest

from runtime_lock import RuntimeLock, RuntimeLockError, resolve_runtime_lock_path


def test_acquire_creates_lock_file_with_expected_metadata(tmp_path):
    lock_path = tmp_path / "runtime.lock"
    lock = RuntimeLock(lock_path, mode="test")

    lock.acquire()

    metadata = json.loads(lock_path.read_text(encoding="utf-8"))
    assert metadata["pid"] == os.getpid()
    assert metadata["mode"] == "test"
    assert metadata["hostname"]
    assert metadata["started_at_utc"]
    assert metadata["executable"]
    assert metadata["cwd"]
    assert metadata["lock_version"] == 1

    lock.release()


def test_release_removes_lock_when_owned_by_current_process(tmp_path):
    lock_path = tmp_path / "runtime.lock"
    lock = RuntimeLock(lock_path, mode="test")

    lock.acquire()
    lock.release()

    assert not lock_path.exists()


def test_release_is_safe_when_already_released(tmp_path):
    lock_path = tmp_path / "runtime.lock"
    lock = RuntimeLock(lock_path, mode="test")

    lock.release()
    lock.acquire()
    lock.release()
    lock.release()

    assert not lock_path.exists()


def test_second_acquire_fails_when_first_owner_is_live(tmp_path):
    lock_path = tmp_path / "runtime.lock"
    first = RuntimeLock(lock_path, mode="first", pid_checker=lambda _pid: True)
    second = RuntimeLock(lock_path, mode="second", pid_checker=lambda _pid: True)

    first.acquire()
    try:
        with pytest.raises(RuntimeLockError, match="already running"):
            second.acquire()
    finally:
        first.release()


def test_stale_lock_is_reclaimed_when_pid_is_dead(tmp_path, caplog):
    lock_path = tmp_path / "runtime.lock"
    lock_path.write_text(
        json.dumps(
            {
                "hostname": "old-host",
                "pid": 999999,
                "started_at_utc": "2026-07-09T00:00:00+00:00",
                "token": "old-token",
            }
        ),
        encoding="utf-8",
    )

    lock = RuntimeLock(lock_path, mode="replacement", pid_checker=lambda _pid: False)
    lock.acquire()

    metadata = json.loads(lock_path.read_text(encoding="utf-8"))
    assert metadata["pid"] == os.getpid()
    assert metadata["mode"] == "replacement"
    assert metadata["token"] != "old-token"
    assert "runtime_lock_stale_recovered" in caplog.text

    lock.release()


def test_live_lock_is_not_reclaimed(tmp_path):
    lock_path = tmp_path / "runtime.lock"
    lock_path.write_text(
        json.dumps(
            {
                "hostname": "live-host",
                "pid": 12345,
                "started_at_utc": "2026-07-09T00:00:00+00:00",
                "token": "live-token",
            }
        ),
        encoding="utf-8",
    )
    lock = RuntimeLock(lock_path, mode="test", pid_checker=lambda _pid: True)

    with pytest.raises(RuntimeLockError, match="already running"):
        lock.acquire()

    metadata = json.loads(lock_path.read_text(encoding="utf-8"))
    assert metadata["token"] == "live-token"


def test_malformed_lock_file_fails_closed(tmp_path):
    lock_path = tmp_path / "runtime.lock"
    lock_path.write_text("not-json", encoding="utf-8")
    lock = RuntimeLock(lock_path, mode="test")

    with pytest.raises(RuntimeLockError, match="already running"):
        lock.acquire()


def test_release_does_not_delete_lock_owned_by_another_runtime(tmp_path):
    lock_path = tmp_path / "runtime.lock"
    lock = RuntimeLock(lock_path, mode="test")

    lock.acquire()
    lock_path.write_text(
        json.dumps(
            {
                "hostname": "other-host",
                "pid": 12345,
                "started_at_utc": "2026-07-09T00:00:00+00:00",
                "token": "other-token",
            }
        ),
        encoding="utf-8",
    )
    lock.release()

    assert lock_path.exists()
    assert json.loads(lock_path.read_text(encoding="utf-8"))["token"] == "other-token"


def test_relative_configured_path_resolves_from_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    assert resolve_runtime_lock_path("locks/runtime.lock") == tmp_path / "locks" / "runtime.lock"

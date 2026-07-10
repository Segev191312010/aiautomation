"""Runtime lock regressions for the stateful backend."""
from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

import runtime_lock as runtime_lock_module
from runtime_lock import (
    RuntimeLock,
    RuntimeLockError,
    default_runtime_lock_path,
    resolve_runtime_lock_path,
)


BACKEND_ROOT = Path(__file__).resolve().parents[1]
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

LOCK_HELPER = r"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[1])
from runtime_lock import RuntimeLock, RuntimeLockError

lock_path = Path(sys.argv[2])
action = sys.argv[3]
ready_path = Path(sys.argv[4])
release_path = Path(sys.argv[5])

if action == "app-contender":
    from contextlib import asynccontextmanager

    os.environ.update(
        {
            "AUTOPILOT_MODE": "OFF",
            "IBKR_PORT": "7497",
            "IS_PAPER": "true",
            "RUNTIME_LOCK_PATH": str(lock_path),
            "SIM_MODE": "true",
        }
    )
    import main
    from fastapi.testclient import TestClient

    @asynccontextmanager
    async def side_effect_marker(_app):
        ready_path.write_text("entered", encoding="utf-8")
        yield

    main._run_lifespan = side_effect_marker
    try:
        with TestClient(main.app):
            pass
    except RuntimeLockError as exc:
        print(json.dumps({"status": "conflict", "error": str(exc)}), flush=True)
        raise SystemExit(23)
    print(json.dumps({"status": "unexpected-start"}), flush=True)
    raise SystemExit(0)

lock = RuntimeLock(lock_path, mode=action)

try:
    lock.acquire()
except RuntimeLockError as exc:
    print(json.dumps({"status": "conflict", "error": str(exc)}), flush=True)
    raise SystemExit(23)

ready_path.write_text(json.dumps({"status": "acquired", "pid": os.getpid()}), encoding="utf-8")
if action == "crash":
    os._exit(0)
if action == "once":
    lock.release()
    raise SystemExit(0)

try:
    sys.stdin.readline()
finally:
    lock.release()
"""


def _popen_helper(
    lock_path: Path,
    action: str,
    ready_path: Path,
    release_path: Path,
) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [
            sys.executable,
            "-c",
            LOCK_HELPER,
            str(BACKEND_ROOT),
            str(lock_path),
            action,
            str(ready_path),
            str(release_path),
        ],
        cwd=BACKEND_ROOT,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        shell=False,
        creationflags=CREATE_NO_WINDOW,
    )


def _wait_for_file(path: Path, process: subprocess.Popen[str], timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            raise AssertionError(
                f"lock helper exited before readiness: rc={process.returncode} "
                f"stdout={stdout!r} stderr={stderr!r}"
            )
        time.sleep(0.01)
    raise AssertionError(f"timed out waiting for {path}")


def _stop_holder(process: subprocess.Popen[str]) -> tuple[str, str]:
    if process.stdin is not None:
        process.stdin.write("release\n")
        process.stdin.flush()
    return process.communicate(timeout=10)


def _write_released_v2_metadata(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "lock_version": 2,
                "pid": 0,
                "state": "released",
                "token": "released-token",
            }
        ),
        encoding="utf-8",
    )


def test_default_path_is_stable_across_working_directories(tmp_path, monkeypatch):
    first_cwd = tmp_path / "first"
    second_cwd = tmp_path / "second"
    first_cwd.mkdir()
    second_cwd.mkdir()

    monkeypatch.chdir(first_cwd)
    first = default_runtime_lock_path()
    monkeypatch.chdir(second_cwd)
    second = default_runtime_lock_path()

    assert first == second
    assert first.name == "tradebot-runtime.lock"
    assert first_cwd not in first.parents
    assert second_cwd not in second.parents


def test_acquire_writes_readable_v2_metadata_while_held(tmp_path):
    lock_path = tmp_path / "runtime.lock"
    lock = RuntimeLock(lock_path, mode="test")

    lock.acquire()
    try:
        metadata = json.loads(lock_path.read_text(encoding="utf-8"))
        assert metadata["pid"] == os.getpid()
        assert metadata["mode"] == "test"
        assert metadata["state"] == "owned"
        assert metadata["hostname"]
        assert metadata["started_at_utc"]
        assert metadata["executable"]
        assert metadata["cwd"]
        assert metadata["lock_version"] == 2
    finally:
        lock.release()


def test_release_persists_metadata_and_allows_reacquire(tmp_path):
    lock_path = tmp_path / "runtime.lock"
    first = RuntimeLock(lock_path, mode="first")
    second = RuntimeLock(lock_path, mode="second")

    first.acquire()
    first.release()

    released = json.loads(lock_path.read_text(encoding="utf-8"))
    assert released["state"] == "released"
    assert released["released_at_utc"]

    second.acquire()
    try:
        metadata = json.loads(lock_path.read_text(encoding="utf-8"))
        assert metadata["mode"] == "second"
        assert metadata["token"] == second._token
    finally:
        second.release()


def test_release_is_idempotent(tmp_path):
    lock_path = tmp_path / "runtime.lock"
    lock = RuntimeLock(lock_path, mode="test")

    lock.release()
    lock.acquire()
    lock.release()
    lock.release()

    probe = RuntimeLock(lock_path, mode="probe")
    probe.acquire()
    probe.release()


def test_second_acquire_fails_with_owner_details(tmp_path, caplog):
    lock_path = tmp_path / "runtime.lock"
    first = RuntimeLock(lock_path, mode="first")
    second = RuntimeLock(lock_path, mode="second")

    first.acquire()
    try:
        with pytest.raises(RuntimeLockError) as exc_info:
            second.acquire()
        message = str(exc_info.value)
        assert "already running" in message
        assert str(lock_path) in message
        assert f"pid={os.getpid()}" in message
        assert "hostname=" in message
        assert "started_at_utc=" in message
        assert "runtime_lock_conflict" in caplog.text
    finally:
        first.release()


def test_dead_legacy_v1_metadata_migrates_to_v2(tmp_path, caplog):
    lock_path = tmp_path / "runtime.lock"
    lock_path.write_text(
        json.dumps(
            {
                "lock_version": 1,
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
    try:
        metadata = json.loads(lock_path.read_text(encoding="utf-8"))
        assert metadata["lock_version"] == 2
        assert metadata["mode"] == "replacement"
        assert metadata["token"] != "old-token"
        assert "runtime_lock_stale_recovered" in caplog.text
    finally:
        lock.release()


def test_live_legacy_v1_metadata_fails_closed_during_upgrade(tmp_path):
    lock_path = tmp_path / "runtime.lock"
    lock_path.write_text(
        json.dumps(
            {
                "lock_version": 1,
                "hostname": "legacy-host",
                "pid": 12345,
                "started_at_utc": "2026-07-09T00:00:00+00:00",
                "token": "legacy-token",
            }
        ),
        encoding="utf-8",
    )

    lock = RuntimeLock(lock_path, mode="test", pid_checker=lambda _pid: True)
    with pytest.raises(RuntimeLockError, match="already running"):
        lock.acquire()

    assert json.loads(lock_path.read_text(encoding="utf-8"))["token"] == "legacy-token"


def test_unlocked_v2_metadata_does_not_grant_ownership(tmp_path):
    lock_path = tmp_path / "runtime.lock"
    lock_path.write_text(
        json.dumps(
            {
                "lock_version": 2,
                "hostname": "old-host",
                "pid": os.getpid(),
                "state": "owned",
                "started_at_utc": "2026-07-09T00:00:00+00:00",
                "token": "old-token",
            }
        ),
        encoding="utf-8",
    )

    lock = RuntimeLock(lock_path, mode="new-owner", pid_checker=lambda _pid: True)
    lock.acquire()
    try:
        metadata = json.loads(lock_path.read_text(encoding="utf-8"))
        assert metadata["mode"] == "new-owner"
        assert metadata["token"] != "old-token"
    finally:
        lock.release()


def test_v2_pid_diagnostic_cannot_override_os_lock_authority(tmp_path):
    lock_path = tmp_path / "runtime.lock"
    lock_path.write_text(
        json.dumps(
            {
                "lock_version": 2,
                "pid": 12345,
                "state": "owned",
                "token": "previous-owner",
            }
        ),
        encoding="utf-8",
    )

    def fail_pid_check(_pid: int) -> bool:
        raise AssertionError("v2 ownership must not use PID diagnostics")

    lock = RuntimeLock(lock_path, mode="replacement", pid_checker=fail_pid_check)
    lock.acquire()
    lock.release()


@pytest.mark.parametrize(
    "payload",
    ["not-json", "[]", "null", '"text"', '{"lock_version": 1}'],
    ids=["invalid-json", "array", "null", "string", "partial-v1-object"],
)
def test_unknown_pre_v2_metadata_fails_closed_without_overwrite(tmp_path, payload):
    lock_path = tmp_path / "runtime.lock"
    lock_path.write_text(payload, encoding="utf-8")

    with pytest.raises(RuntimeLockError, match="already running") as exc_info:
        RuntimeLock(lock_path, mode="replacement").acquire()

    assert "only after verifying no backend owns this scope" in str(exc_info.value)
    assert lock_path.read_text(encoding="utf-8") == payload


def test_interrupted_v2_metadata_requires_verified_operator_recovery(tmp_path):
    lock_path = tmp_path / "runtime.lock"
    partial_payload = '{"lock_version": 2, "state": "owned"'
    lock_path.write_text(partial_payload, encoding="utf-8")

    with pytest.raises(RuntimeLockError) as exc_info:
        RuntimeLock(lock_path, mode="replacement").acquire()

    error = str(exc_info.value)
    assert "already running" in error
    assert "Stop all pre-v2 TradeBot runtimes" in error
    assert "only after verifying no backend owns this scope" in error
    assert lock_path.read_text(encoding="utf-8") == partial_payload

    # This unlink represents the documented operator step after the process
    # inventory has proved that neither a v1 nor v2 runtime remains active.
    lock_path.unlink()
    replacement = RuntimeLock(lock_path, mode="after-verified-cleanup")
    replacement.acquire()
    replacement.release()


def test_held_os_lock_refuses_startup_even_with_malformed_metadata(tmp_path):
    lock_path = tmp_path / "runtime.lock"
    holder = RuntimeLock(lock_path, mode="holder")
    holder.acquire()
    lock_path.write_text("[]", encoding="utf-8")

    try:
        with pytest.raises(RuntimeLockError, match="already running"):
            RuntimeLock(lock_path, mode="contender").acquire()
    finally:
        holder.release()

    assert holder._acquired is False
    assert holder._fd is None
    _write_released_v2_metadata(lock_path)
    probe = RuntimeLock(lock_path, mode="probe")
    probe.acquire()
    probe.release()


def test_concurrent_stale_contenders_have_exactly_one_winner(
    tmp_path,
    monkeypatch,
    caplog,
):
    lock_path = tmp_path / "runtime.lock"
    lock_path.write_text(
        json.dumps({"lock_version": 1, "pid": 0, "token": "stale"}),
        encoding="utf-8",
    )
    real_lock = runtime_lock_module._lock_fd_nonblocking
    claim_barrier = threading.Barrier(2)
    release_winner = threading.Event()
    outcomes: queue.Queue[tuple[str, str, str]] = queue.Queue()

    def synchronized_lock(fd: int) -> None:
        claim_barrier.wait(timeout=5)
        real_lock(fd)

    monkeypatch.setattr(runtime_lock_module, "_lock_fd_nonblocking", synchronized_lock)

    def contend(name: str) -> None:
        lock = RuntimeLock(lock_path, mode=name, pid_checker=lambda _pid: False)
        try:
            lock.acquire()
            outcomes.put(("acquired", name, lock._token))
            assert release_winner.wait(timeout=5)
        except RuntimeLockError as exc:
            outcomes.put(("conflict", name, str(exc)))
        finally:
            lock.release()

    threads = [threading.Thread(target=contend, args=(name,)) for name in ("one", "two")]
    for thread in threads:
        thread.start()

    results = [outcomes.get(timeout=5), outcomes.get(timeout=5)]
    assert sorted(result[0] for result in results) == ["acquired", "conflict"]
    winner = next(result for result in results if result[0] == "acquired")
    metadata = json.loads(lock_path.read_text(encoding="utf-8"))
    assert metadata["mode"] == winner[1]
    assert metadata["token"] == winner[2]
    assert "runtime_lock_conflict" in caplog.text

    release_winner.set()
    for thread in threads:
        thread.join(timeout=5)
        assert not thread.is_alive()

    monkeypatch.setattr(runtime_lock_module, "_lock_fd_nonblocking", real_lock)
    probe = RuntimeLock(lock_path, mode="probe")
    probe.acquire()
    probe.release()


def test_metadata_write_failure_releases_os_lock(tmp_path, monkeypatch):
    lock_path = tmp_path / "runtime.lock"
    _write_released_v2_metadata(lock_path)
    original_writer = RuntimeLock._write_metadata_fd

    def fail_write(_fd: int, _metadata: dict[str, object]) -> None:
        raise OSError("injected write failure")

    monkeypatch.setattr(RuntimeLock, "_write_metadata_fd", staticmethod(fail_write))
    with pytest.raises(RuntimeLockError, match="could not initialize"):
        RuntimeLock(lock_path).acquire()

    monkeypatch.setattr(RuntimeLock, "_write_metadata_fd", staticmethod(original_writer))
    probe = RuntimeLock(lock_path, mode="probe")
    probe.acquire()
    probe.release()


def test_diagnostic_failure_releases_os_lock(tmp_path):
    lock_path = tmp_path / "runtime.lock"
    lock_path.write_text(
        json.dumps({"lock_version": 1, "pid": 12345, "token": "legacy"}),
        encoding="utf-8",
    )

    def fail_pid_check(_pid: int) -> bool:
        raise OSError("injected PID diagnostic failure")

    with pytest.raises(RuntimeLockError, match="could not initialize"):
        RuntimeLock(lock_path, pid_checker=fail_pid_check).acquire()

    probe = RuntimeLock(lock_path, mode="probe", pid_checker=lambda _pid: False)
    probe.acquire()
    probe.release()


def test_real_subprocess_collision_and_reacquire(tmp_path):
    lock_path = tmp_path / "runtime.lock"
    ready_path = tmp_path / "holder-ready.json"
    release_path = tmp_path / "release"
    holder = _popen_helper(lock_path, "hold", ready_path, release_path)

    try:
        _wait_for_file(ready_path, holder)
        owner_metadata = json.loads(lock_path.read_text(encoding="utf-8"))

        side_effect_marker = tmp_path / "contender-side-effect"
        contender = _popen_helper(
            lock_path,
            "app-contender",
            side_effect_marker,
            release_path,
        )
        stdout, stderr = contender.communicate(timeout=10)

        assert contender.returncode == 23, stderr
        conflict = json.loads(stdout.strip())
        assert conflict["status"] == "conflict"
        assert str(lock_path) in conflict["error"]
        assert f"pid={owner_metadata['pid']}" in conflict["error"]
        assert not side_effect_marker.exists()
        assert holder.poll() is None

        holder_stdout, holder_stderr = _stop_holder(holder)
        assert holder.returncode == 0, (holder_stdout, holder_stderr)

        probe = RuntimeLock(lock_path, mode="after-subprocess")
        probe.acquire()
        probe.release()
    finally:
        if holder.poll() is None:
            holder.terminate()
            try:
                holder.wait(timeout=5)
            except subprocess.TimeoutExpired:
                holder.kill()
                holder.wait(timeout=5)


def test_process_death_releases_os_lock(tmp_path):
    lock_path = tmp_path / "runtime.lock"
    ready_path = tmp_path / "crash-ready.json"
    release_path = tmp_path / "unused-release"
    process = _popen_helper(lock_path, "crash", ready_path, release_path)

    _wait_for_file(ready_path, process)
    stdout, stderr = process.communicate(timeout=10)
    assert process.returncode == 0
    assert stdout == "", stderr
    crashed_metadata = json.loads(lock_path.read_text(encoding="utf-8"))

    replacement = RuntimeLock(lock_path, mode="after-crash", pid_checker=lambda _pid: False)
    replacement.acquire()
    try:
        metadata = json.loads(lock_path.read_text(encoding="utf-8"))
        assert metadata["token"] != crashed_metadata["token"]
        assert metadata["mode"] == "after-crash"
    finally:
        replacement.release()


def test_relative_configured_path_resolves_from_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    assert resolve_runtime_lock_path("locks/runtime.lock") == tmp_path / "locks" / "runtime.lock"

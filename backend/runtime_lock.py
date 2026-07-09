"""Machine-local runtime lock for the stateful trading backend."""
from __future__ import annotations

import ctypes
import json
import logging
import os
import socket
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


log = logging.getLogger(__name__)

LOCK_FILENAME = "tradebot-runtime.lock"
REPO_ROOT = Path(__file__).resolve().parents[1]


class RuntimeLockError(RuntimeError):
    """Raised when another active backend runtime already owns the lock."""


def default_runtime_lock_path() -> Path:
    """Return a deterministic writable lock path for this runtime.

    Docker Compose sets RUNTIME_LOCK_PATH to a host-shared bind mount. Bare
    containers use /data when available. Local development uses the repo-level
    .runtime directory so separate terminals coordinate on one lock.
    """
    docker_data = Path("/data")
    if docker_data.is_dir():
        return docker_data / LOCK_FILENAME
    return REPO_ROOT / ".runtime" / LOCK_FILENAME


def resolve_runtime_lock_path(configured_path: str | os.PathLike[str] | None = None) -> Path:
    if configured_path:
        path = Path(configured_path).expanduser()
        return path if path.is_absolute() else (Path.cwd() / path).resolve()
    return default_runtime_lock_path()


def _pid_is_running_windows(pid: int) -> bool:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    process_query_limited_information = 0x1000
    still_active = 259

    handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
    if not handle:
        error = ctypes.get_last_error()
        # ERROR_INVALID_PARAMETER means there is no such process. Other errors
        # are treated as live so duplicate-runtime checks fail closed.
        return error != 87

    try:
        exit_code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return True
        return exit_code.value == still_active
    finally:
        kernel32.CloseHandle(handle)


def pid_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True

    if os.name == "nt":
        return _pid_is_running_windows(pid)

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


@dataclass
class RuntimeLock:
    path: Path
    mode: str = "unknown"
    pid_checker: Callable[[int], bool] = pid_is_running

    def __post_init__(self) -> None:
        self.path = Path(self.path)
        self._token = uuid.uuid4().hex
        self._acquired = False

    @classmethod
    def from_config(cls, cfg: Any) -> "RuntimeLock":
        mode = (
            f"autopilot={getattr(cfg, 'AUTOPILOT_MODE', 'UNKNOWN')};"
            f"paper={getattr(cfg, 'IS_PAPER', 'UNKNOWN')};"
            f"sim={getattr(cfg, 'SIM_MODE', 'UNKNOWN')}"
        )
        return cls(
            path=resolve_runtime_lock_path(getattr(cfg, "RUNTIME_LOCK_PATH", "")),
            mode=mode,
        )

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        metadata = self._metadata()

        for _attempt in range(2):
            try:
                fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                existing = self._read_existing_metadata()
                stale_reason = self._stale_reason(existing)
                if stale_reason:
                    log.warning(
                        "event=runtime_lock_stale_recovered path=%s reason=%s metadata=%s",
                        self.path,
                        stale_reason,
                        existing,
                    )
                    self.path.unlink()
                    continue
                log.error(
                    "event=runtime_lock_conflict path=%s metadata=%s",
                    self.path,
                    existing,
                )
                raise RuntimeLockError(self._duplicate_message(existing)) from None

            try:
                with os.fdopen(fd, "w", encoding="utf-8") as lock_file:
                    json.dump(metadata, lock_file, indent=2, sort_keys=True)
                    lock_file.write("\n")
            except Exception:
                self._safe_unlink()
                raise

            self._acquired = True
            log.info(
                "event=runtime_lock_acquired path=%s pid=%s hostname=%s",
                self.path,
                metadata["pid"],
                metadata["hostname"],
            )
            return

        raise RuntimeLockError(f"TradeBot backend could not acquire runtime lock at {self.path}")

    def release(self) -> None:
        if not self._acquired:
            return

        metadata = self._read_existing_metadata()
        if metadata.get("token") == self._token and metadata.get("pid") == os.getpid():
            self._safe_unlink()
            log.info("event=runtime_lock_released path=%s pid=%s", self.path, os.getpid())
        else:
            log.warning(
                "event=runtime_lock_release_skipped path=%s metadata=%s",
                self.path,
                metadata,
            )
        self._acquired = False

    def _metadata(self) -> dict[str, Any]:
        return {
            "cwd": str(Path.cwd()),
            "executable": sys.executable,
            "hostname": socket.gethostname(),
            "lock_version": 1,
            "mode": self.mode,
            "pid": os.getpid(),
            "started_at_utc": datetime.now(timezone.utc).isoformat(),
            "token": self._token,
        }

    def _read_existing_metadata(self) -> dict[str, Any]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            return {"read_error": str(exc)}

    def _stale_reason(self, metadata: dict[str, Any]) -> str | None:
        pid = metadata.get("pid")
        if not isinstance(pid, int):
            return None
        if self.pid_checker(pid):
            return None
        return f"recorded pid {pid} is not running"

    def _duplicate_message(self, metadata: dict[str, Any]) -> str:
        pid = metadata.get("pid", "unknown")
        hostname = metadata.get("hostname", "unknown")
        started_at = metadata.get("started_at_utc", "unknown")
        return (
            "TradeBot backend already running; refusing second runtime "
            f"(lock={self.path}, pid={pid}, hostname={hostname}, started_at_utc={started_at})"
        )

    def _safe_unlink(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass

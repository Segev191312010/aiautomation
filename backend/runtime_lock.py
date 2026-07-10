"""OS-held single-runtime lock for the stateful trading backend."""
from __future__ import annotations

import ctypes
import errno
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

if os.name == "nt":
    import msvcrt
else:  # pragma: no cover - exercised by Linux CI
    import fcntl


log = logging.getLogger(__name__)

LOCK_FILENAME = "tradebot-runtime.lock"
APP_RUNTIME_DIRNAME = "TradeBot"
# Windows byte-range locks also block reads that overlap the locked byte. Keep
# the guard well beyond the small JSON metadata payload so operators and losing
# contenders can still read ownership details while the guard remains held.
LOCK_BYTE_OFFSET = 1 << 20


class RuntimeLockError(RuntimeError):
    """Raised when another active backend runtime already owns the lock."""


def default_runtime_lock_path() -> Path:
    """Return a stable per-user path shared by local checkouts.

    Docker images create ``/data`` and use that persistent volume. Docker
    Compose supplies its own shared path. Native launches use per-user
    operating-system state, so separate native clones and worktrees for that
    user still contend on the same lock.
    """
    docker_data = Path("/data")
    if docker_data.is_dir():
        return docker_data / LOCK_FILENAME

    if os.name == "nt":
        base = os.getenv("LOCALAPPDATA")
        state_root = Path(base) if base else Path.home() / "AppData" / "Local"
    else:
        base = os.getenv("XDG_RUNTIME_DIR") or os.getenv("XDG_STATE_HOME")
        state_root = Path(base) if base else Path.home() / ".local" / "state"

    return state_root / APP_RUNTIME_DIRNAME / "runtime" / LOCK_FILENAME


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


def _lock_fd_nonblocking(fd: int) -> None:
    """Acquire an exclusive OS lock or raise when another owner holds it."""
    os.lseek(fd, LOCK_BYTE_OFFSET, os.SEEK_SET)
    try:
        if os.name == "nt":
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:  # pragma: no cover - exercised by Linux CI
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        if exc.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
            raise RuntimeLockError("runtime lock is already owned") from exc
        raise


def _unlock_fd(fd: int) -> None:
    os.lseek(fd, LOCK_BYTE_OFFSET, os.SEEK_SET)
    if os.name == "nt":
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:  # pragma: no cover - exercised by Linux CI
        fcntl.flock(fd, fcntl.LOCK_UN)


@dataclass
class RuntimeLock:
    path: Path
    mode: str = "unknown"
    pid_checker: Callable[[int], bool] = pid_is_running

    def __post_init__(self) -> None:
        self.path = Path(self.path)
        self._token = uuid.uuid4().hex
        self._acquired = False
        self._fd: int | None = None

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
        if self._acquired or self._fd is not None:
            raise RuntimeLockError(f"Runtime lock instance already owns {self.path}")

        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            raise RuntimeLockError(
                f"TradeBot backend could not prepare runtime lock directory "
                f"{self.path.parent}: {exc}"
            ) from exc
        metadata = self._metadata()
        flags = (
            os.O_CREAT
            | os.O_RDWR
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_CLOEXEC", 0)
        )
        fd: int | None = None
        created = False
        try:
            try:
                fd = os.open(str(self.path), flags | os.O_EXCL, 0o600)
                created = True
            except FileExistsError:
                fd = os.open(str(self.path), flags, 0o600)
            os.set_inheritable(fd, False)
        except Exception as exc:
            if fd is not None:
                os.close(fd)
            raise RuntimeLockError(
                f"TradeBot backend could not open runtime lock at {self.path}: {exc}"
            ) from exc

        try:
            _lock_fd_nonblocking(fd)
        except RuntimeLockError:
            existing = self._read_metadata_fd(fd)
            os.close(fd)
            log.error(
                "event=runtime_lock_conflict path=%s metadata=%s",
                self.path,
                existing,
            )
            raise RuntimeLockError(self._duplicate_message(existing)) from None
        except Exception:
            os.close(fd)
            raise RuntimeLockError(
                f"TradeBot backend could not acquire OS runtime lock at {self.path}"
            ) from None

        try:
            existing = self._read_metadata_fd(fd)
            legacy_conflict = self._legacy_conflict_reason(existing, created=created)
            if legacy_conflict:
                log.error(
                    "event=runtime_lock_legacy_conflict path=%s reason=%s metadata=%s",
                    self.path,
                    legacy_conflict,
                    existing,
                )
                raise RuntimeLockError(self._duplicate_message(existing))

            stale_reason = self._stale_reason(existing)
            if stale_reason:
                log.warning(
                    "event=runtime_lock_stale_recovered path=%s reason=%s metadata=%s",
                    self.path,
                    stale_reason,
                    existing,
                )
            elif existing.get("read_error"):
                log.warning(
                    "event=runtime_lock_metadata_recovered path=%s metadata=%s",
                    self.path,
                    existing,
                )
            self._write_metadata_fd(fd, metadata)
        except Exception as exc:
            try:
                _unlock_fd(fd)
            finally:
                os.close(fd)
            if isinstance(exc, RuntimeLockError):
                raise
            raise RuntimeLockError(
                f"TradeBot backend could not initialize runtime lock at {self.path}: {exc}"
            ) from exc

        self._fd = fd
        self._acquired = True
        log.info(
            "event=runtime_lock_acquired path=%s pid=%s hostname=%s",
            self.path,
            metadata["pid"],
            metadata["hostname"],
        )

    def release(self) -> None:
        if not self._acquired or self._fd is None:
            return

        fd = self._fd
        try:
            metadata = self._read_metadata_fd(fd)
            metadata_matches = (
                metadata.get("token") == self._token
                and metadata.get("pid") == os.getpid()
            )
            if metadata_matches:
                released_metadata = dict(metadata)
                released_metadata["state"] = "released"
                released_metadata["released_at_utc"] = datetime.now(timezone.utc).isoformat()
                self._write_metadata_fd(fd, released_metadata)
            else:
                log.warning(
                    "event=runtime_lock_release_metadata_mismatch path=%s metadata=%s",
                    self.path,
                    metadata,
                )
        except Exception:  # noqa: BLE001
            log.exception(
                "event=runtime_lock_release_metadata_failed path=%s",
                self.path,
            )
        finally:
            try:
                _unlock_fd(fd)
            except Exception:  # noqa: BLE001
                log.exception("event=runtime_lock_unlock_failed path=%s", self.path)
            try:
                os.close(fd)
            except Exception:  # noqa: BLE001
                log.exception("event=runtime_lock_close_failed path=%s", self.path)
            self._fd = None
            self._acquired = False

        log.info("event=runtime_lock_released path=%s pid=%s", self.path, os.getpid())

    def _metadata(self) -> dict[str, Any]:
        return {
            "cwd": str(Path.cwd()),
            "executable": sys.executable,
            "hostname": socket.gethostname(),
            "lock_version": 2,
            "mode": self.mode,
            "pid": os.getpid(),
            "state": "owned",
            "started_at_utc": datetime.now(timezone.utc).isoformat(),
            "token": self._token,
        }

    def _read_metadata_fd(self, fd: int) -> dict[str, Any]:
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            chunks: list[bytes] = []
            while True:
                chunk = os.read(fd, 4096)
                if not chunk:
                    break
                chunks.append(chunk)
            if not chunks:
                return {}
            decoded = json.loads(b"".join(chunks).decode("utf-8"))
            if not isinstance(decoded, dict):
                return {
                    "read_error": (
                        "runtime lock metadata must be a JSON object, got "
                        f"{type(decoded).__name__}"
                    )
                }
            return decoded
        except Exception as exc:  # noqa: BLE001
            return {"read_error": str(exc)}

    @staticmethod
    def _write_metadata_fd(fd: int, metadata: dict[str, Any]) -> None:
        payload = (json.dumps(metadata, indent=2, sort_keys=True) + "\n").encode("utf-8")
        if len(payload) >= LOCK_BYTE_OFFSET:
            raise ValueError("runtime lock metadata exceeds reserved guard offset")
        os.lseek(fd, 0, os.SEEK_SET)
        written = 0
        while written < len(payload):
            chunk_size = os.write(fd, payload[written:])
            if chunk_size <= 0:
                raise OSError("runtime lock metadata write made no progress")
            written += chunk_size
        os.ftruncate(fd, len(payload))
        os.fsync(fd)

    def _legacy_conflict_reason(
        self,
        metadata: dict[str, Any],
        *,
        created: bool,
    ) -> str | None:
        if created:
            return None
        version = metadata.get("lock_version")
        if isinstance(version, int) and version >= 2:
            return None
        pid = metadata.get("pid")
        if not isinstance(pid, int):
            return "existing lock has unknown or partially-written pre-v2 metadata"
        if self.pid_checker(pid):
            return f"legacy v1 owner pid {pid} is still running"
        return None

    def _stale_reason(self, metadata: dict[str, Any]) -> str | None:
        if metadata.get("state") == "released":
            return None
        version = metadata.get("lock_version")
        if isinstance(version, int) and version >= 2:
            return "previous v2 owner no longer holds the OS lock"
        pid = metadata.get("pid")
        if not isinstance(pid, int):
            return None
        # A pre-v2 PID was already checked by _legacy_conflict_reason. Do not
        # perform a second liveness probe after the OS lock has established
        # v2 ownership authority.
        return f"legacy v1 owner pid {pid} is not running"

    def _duplicate_message(self, metadata: dict[str, Any]) -> str:
        pid = metadata.get("pid", "unknown")
        hostname = metadata.get("hostname", "unknown")
        started_at = metadata.get("started_at_utc", "unknown")
        message = (
            "TradeBot backend already running; refusing second runtime "
            f"(lock={self.path}, pid={pid}, hostname={hostname}, started_at_utc={started_at})"
        )
        version = metadata.get("lock_version")
        unknown_pre_v2 = (
            not isinstance(version, int)
            or (version < 2 and not isinstance(metadata.get("pid"), int))
        )
        if metadata.get("read_error") or unknown_pre_v2:
            message += (
                ". Lock metadata is unknown or incomplete. Stop all pre-v2 "
                "TradeBot runtimes; only after verifying no backend owns this "
                "scope, remove the lock file and restart"
            )
        return message

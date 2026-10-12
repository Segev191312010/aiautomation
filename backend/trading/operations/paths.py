"""Canonical private paths for the host-native shadow runtime."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from uuid import uuid4


DIRECTORY_MODE = 0o700
FILE_MODE = 0o600
LAUNCH_AGENT_LABEL = "com.maccabi.autotrader-v1.shadow"


class PathSecurityError(RuntimeError):
    """A runtime path cannot be made private without following a symlink."""


def ensure_private_directory(path: Path) -> Path:
    expanded = path.expanduser()
    if expanded.is_symlink():
        raise PathSecurityError("private runtime directories must not be symbolic links")
    expanded.mkdir(parents=True, exist_ok=True, mode=DIRECTORY_MODE)
    if not expanded.is_dir():
        raise PathSecurityError("private runtime path is not a directory")
    expanded.chmod(DIRECTORY_MODE)
    return expanded


def ensure_private_file(path: Path) -> Path:
    expanded = path.expanduser()
    if expanded.is_symlink() or (expanded.exists() and not expanded.is_file()):
        raise PathSecurityError("private runtime files must be regular files")
    if expanded.exists():
        expanded.chmod(FILE_MODE)
    return expanded


def atomic_private_write(path: Path, payload: bytes) -> Path:
    target = path.expanduser()
    ensure_private_directory(target.parent)
    if target.is_symlink() or (target.exists() and not target.is_file()):
        raise PathSecurityError("private runtime files must not be symbolic links")
    temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, FILE_MODE)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        target.chmod(FILE_MODE)
    except BaseException:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return target


@dataclass(frozen=True, slots=True)
class UserPaths:
    """One canonical path set; callers cannot redirect shadow state into the repo."""

    home: Path

    @classmethod
    def current(cls) -> "UserPaths":
        return cls(home=Path.home())

    @property
    def config_directory(self) -> Path:
        return self.home / ".config" / "autotrader-v1"

    @property
    def config_file(self) -> Path:
        return self.config_directory / "config.toml"

    @property
    def data_directory(self) -> Path:
        return self.home / ".local" / "share" / "autotrader-v1"

    @property
    def database_file(self) -> Path:
        return self.data_directory / "autotrader.db"

    @property
    def evidence_directory(self) -> Path:
        return self.data_directory / "evidence"

    @property
    def log_directory(self) -> Path:
        return self.home / "Library" / "Logs" / "AutotraderV1"

    @property
    def shadow_log_file(self) -> Path:
        return self.log_directory / "shadow.log"

    @property
    def launchd_stdout_file(self) -> Path:
        return self.log_directory / "launchd.stdout.log"

    @property
    def launchd_stderr_file(self) -> Path:
        return self.log_directory / "launchd.stderr.log"

    @property
    def launch_agents_directory(self) -> Path:
        return self.home / "Library" / "LaunchAgents"

    @property
    def launch_agent_file(self) -> Path:
        return self.launch_agents_directory / f"{LAUNCH_AGENT_LABEL}.plist"

    def ensure_runtime_layout(self) -> None:
        for directory in (
            self.config_directory,
            self.data_directory,
            self.evidence_directory,
            self.log_directory,
            self.launch_agents_directory,
        ):
            ensure_private_directory(directory)
        for file_path in (
            self.config_file,
            self.database_file,
            self.shadow_log_file,
            self.launchd_stdout_file,
            self.launchd_stderr_file,
            self.launch_agent_file,
        ):
            ensure_private_file(file_path)


__all__ = [
    "DIRECTORY_MODE",
    "FILE_MODE",
    "LAUNCH_AGENT_LABEL",
    "PathSecurityError",
    "UserPaths",
    "atomic_private_write",
    "ensure_private_directory",
    "ensure_private_file",
]

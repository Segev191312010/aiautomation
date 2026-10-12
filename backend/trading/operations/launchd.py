"""Generate and control the LaunchAgent that keeps the trading supervisor running.

``install`` only writes the plist; ``enable`` registers and loads it; ``disable``
unregisters and unloads it.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
import os
from pathlib import Path
import plistlib
import subprocess
from typing import Protocol

from trading.operations.paths import (
    LAUNCH_AGENT_LABEL,
    PathSecurityError,
    UserPaths,
    atomic_private_write,
)

# caffeinate execs the supervisor in place (same PID) while a helper process holds the
# sleep assertions until it exits, so launchd's SIGTERM and the exit status reach Python
# directly. -i blocks idle sleep; -s blocks system sleep while on AC power.
CAFFEINATE = "/usr/bin/caffeinate"
CAFFEINATE_FLAGS = ("-i", "-s")
SUPERVISOR_COMMAND = ("run",)
# KeepAlive restarts the supervisor after any exit; this caps it at one spawn per 30 s
# so a start that cannot succeed does not spin.
THROTTLE_INTERVAL_SECONDS = 30
# The supervisor gives children 20 s after SIGTERM before SIGKILL; launchd's
# default 20 s exit timeout would kill the supervisor mid-shutdown.
EXIT_TIMEOUT_SECONDS = 45


class LaunchdInstallError(RuntimeError):
    """The LaunchAgent could not be written or controlled; never carries subprocess output."""


class LaunchdCommandRunner(Protocol):
    def __call__(self, command: Sequence[str]) -> subprocess.CompletedProcess[str]: ...


def _run(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )


@dataclass(slots=True)
class LaunchdManager:
    user_paths: UserPaths
    repository_root: Path
    command_runner: LaunchdCommandRunner = _run

    def __post_init__(self) -> None:
        # launchd needs absolute paths; a relative root would bake the installer's
        # current directory into the plist.
        if not self.repository_root.is_absolute():
            raise ValueError("repository_root must be an absolute path")

    @classmethod
    def current(cls, *, user_paths: UserPaths | None = None) -> "LaunchdManager":
        return cls(
            user_paths=user_paths or UserPaths.current(),
            repository_root=Path(__file__).resolve().parents[3],
        )

    @property
    def backend_directory(self) -> Path:
        return self.repository_root / "backend"

    @property
    def virtualenv_directory(self) -> Path:
        return self.backend_directory / ".venv"

    @property
    def python_executable(self) -> Path:
        # Deliberately unresolved: bin/python is a symlink to uv's base interpreter, and
        # Python finds pyvenv.cfg (and the project's packages) only through the venv path.
        return self.virtualenv_directory / "bin" / "python"

    @property
    def entrypoint(self) -> Path:
        return self.backend_directory / "autotraderctl.py"

    def program_arguments(self) -> list[str]:
        return [
            CAFFEINATE,
            *CAFFEINATE_FLAGS,
            str(self.python_executable),
            str(self.entrypoint),
            *SUPERVISOR_COMMAND,
        ]

    def payload(self) -> dict[str, object]:
        return {
            "Label": LAUNCH_AGENT_LABEL,
            "ProgramArguments": self.program_arguments(),
            "WorkingDirectory": str(self.backend_directory),
            # launchd starts agents with an almost empty environment; a login start must
            # behave like a terminal start (UTF-8 I/O, unbuffered logs, system tools only).
            "EnvironmentVariables": {
                "PYTHONUNBUFFERED": "1",
                "LANG": "en_US.UTF-8",
                "PYTHONIOENCODING": "utf-8",
                "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
            },
            "ProcessType": "Standard",
            "RunAtLoad": True,
            "KeepAlive": True,
            "ThrottleInterval": THROTTLE_INTERVAL_SECONDS,
            "ExitTimeOut": EXIT_TIMEOUT_SECONDS,
            "StandardOutPath": str(self.user_paths.launchd_stdout_file),
            "StandardErrorPath": str(self.user_paths.launchd_stderr_file),
        }

    def serialized_payload(self) -> bytes:
        return plistlib.dumps(self.payload(), fmt=plistlib.FMT_XML, sort_keys=True)

    def check_runtime(self) -> None:
        """Refuse to hand launchd a command that can only crash-loop under KeepAlive."""

        if not (self.virtualenv_directory / "pyvenv.cfg").is_file():
            raise LaunchdInstallError(f"{self.virtualenv_directory} is not a virtual environment")
        if not os.access(self.python_executable, os.X_OK):
            raise LaunchdInstallError(f"{self.python_executable} is missing or not executable")
        if not self.entrypoint.is_file():
            raise LaunchdInstallError(f"{self.entrypoint} is missing")

    def write(self) -> Path:
        self.check_runtime()
        try:
            self.user_paths.ensure_runtime_layout()
            return atomic_private_write(self.user_paths.launch_agent_file, self.serialized_payload())
        except (OSError, PathSecurityError) as exc:
            raise LaunchdInstallError(f"unable to write the LaunchAgent plist: {exc}") from exc

    def install(self) -> Path:
        """Write the plist without loading or enabling the service."""

        return self.write()

    @property
    def domain(self) -> str:
        return f"gui/{os.getuid()}"

    @property
    def service(self) -> str:
        return f"{self.domain}/{LAUNCH_AGENT_LABEL}"

    def is_installed(self) -> bool:
        path = self.user_paths.launch_agent_file
        return path.is_file() and not path.is_symlink()

    def is_current(self) -> bool:
        """True when the installed plist is byte-identical to the payload this code writes."""

        if not self.is_installed():
            return False
        try:
            return self.user_paths.launch_agent_file.read_bytes() == self.serialized_payload()
        except OSError:
            return False

    def is_loaded(self) -> bool:
        result = self._command(("/bin/launchctl", "print", self.service))
        return result.returncode == 0

    def enable(self) -> None:
        if not self.is_installed():
            raise LaunchdInstallError("the LaunchAgent must be installed first")
        self.check_runtime()
        rewritten = not self.is_current()
        if rewritten:
            # Never bootstrap a stale definition, such as one naming the base interpreter.
            self.write()
        enabled = self._command(("/bin/launchctl", "enable", self.service))
        if enabled.returncode != 0:
            raise LaunchdInstallError("unable to enable the LaunchAgent")
        if self.is_loaded():
            if rewritten:
                # launchd keeps running the definition it loaded. Restarting a live
                # supervisor is the operator's call, so report it instead of bouncing it.
                raise LaunchdInstallError(
                    "the LaunchAgent is loaded from an outdated plist; "
                    "disable and enable it again to load the new one"
                )
            return
        bootstrapped = self._command(
            (
                "/bin/launchctl",
                "bootstrap",
                self.domain,
                str(self.user_paths.launch_agent_file),
            )
        )
        if bootstrapped.returncode == 0:
            return
        self._command(("/bin/launchctl", "disable", self.service))
        raise LaunchdInstallError("unable to load the LaunchAgent")

    def disable(self) -> None:
        disabled = self._command(("/bin/launchctl", "disable", self.service))
        if disabled.returncode != 0:
            raise LaunchdInstallError("unable to disable the LaunchAgent")
        if not self.is_loaded():
            return
        unloaded = self._command(("/bin/launchctl", "bootout", self.service))
        if unloaded.returncode != 0:
            raise LaunchdInstallError("unable to unload the LaunchAgent")

    def _command(self, command: Sequence[str]) -> subprocess.CompletedProcess[str]:
        try:
            return self.command_runner(command)
        except (OSError, subprocess.SubprocessError) as exc:
            raise LaunchdInstallError("unable to control the LaunchAgent") from exc


__all__ = ["LaunchdCommandRunner", "LaunchdInstallError", "LaunchdManager"]

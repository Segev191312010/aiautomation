"""Generate and install the disabled-by-default host shadow LaunchAgent."""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
import os
from pathlib import Path
import plistlib
import subprocess
import sys
from typing import Protocol

from trading.operations.paths import LAUNCH_AGENT_LABEL, UserPaths, atomic_private_write


class LaunchdInstallError(RuntimeError):
    """The LaunchAgent could not be controlled without exposing subprocess output."""


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
    python_executable: Path = Path(sys.executable)
    command_runner: LaunchdCommandRunner = _run

    @classmethod
    def current(cls, *, user_paths: UserPaths | None = None) -> "LaunchdManager":
        return cls(
            user_paths=user_paths or UserPaths.current(),
            repository_root=Path(__file__).resolve().parents[3],
        )

    def payload(self) -> dict[str, object]:
        entrypoint = self.repository_root / "backend" / "autotraderctl.py"
        return {
            "Label": LAUNCH_AGENT_LABEL,
            "ProgramArguments": [
                str(self.python_executable.resolve()),
                str(entrypoint.resolve()),
                "shadow",
                "daemon",
            ],
            "WorkingDirectory": str(self.repository_root.resolve()),
            "RunAtLoad": True,
            "KeepAlive": {"SuccessfulExit": False},
            "ProcessType": "Background",
            "ThrottleInterval": 10,
            "EnvironmentVariables": {"PYTHONUNBUFFERED": "1"},
            "StandardOutPath": str(self.user_paths.launchd_stdout_file),
            "StandardErrorPath": str(self.user_paths.launchd_stderr_file),
        }

    def write(self) -> Path:
        self.user_paths.ensure_runtime_layout()
        serialized = plistlib.dumps(self.payload(), fmt=plistlib.FMT_XML, sort_keys=True)
        return atomic_private_write(self.user_paths.launch_agent_file, serialized)

    def install(self) -> Path:
        """Install the plist without loading or enabling the service."""

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

    def is_loaded(self) -> bool:
        result = self._command(("/bin/launchctl", "print", self.service))
        return result.returncode == 0

    def enable(self) -> None:
        if not self.is_installed():
            raise LaunchdInstallError("the shadow LaunchAgent must be installed first")
        enabled = self._command(("/bin/launchctl", "enable", self.service))
        if enabled.returncode != 0:
            raise LaunchdInstallError("unable to enable the shadow LaunchAgent")
        if self.is_loaded():
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
        raise LaunchdInstallError("unable to load the shadow LaunchAgent")

    def disable(self) -> None:
        disabled = self._command(("/bin/launchctl", "disable", self.service))
        if disabled.returncode != 0:
            raise LaunchdInstallError("unable to disable the shadow LaunchAgent")
        if not self.is_loaded():
            return
        unloaded = self._command(("/bin/launchctl", "bootout", self.service))
        if unloaded.returncode != 0:
            raise LaunchdInstallError("unable to unload the shadow LaunchAgent")

    def _command(self, command: Sequence[str]) -> subprocess.CompletedProcess[str]:
        try:
            return self.command_runner(command)
        except (OSError, subprocess.SubprocessError) as exc:
            raise LaunchdInstallError("unable to control the shadow LaunchAgent") from exc


__all__ = ["LaunchdCommandRunner", "LaunchdInstallError", "LaunchdManager"]

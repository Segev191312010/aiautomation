"""Secret-safe macOS Keychain access for the Anthropic setup-review key."""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
import subprocess
from typing import Protocol


ANTHROPIC_KEYCHAIN_SERVICE = "com.maccabi.autotrader-v1.anthropic"
ANTHROPIC_KEYCHAIN_ACCOUNT = "anthropic-api-key"


class KeychainReadError(RuntimeError):
    """A Keychain secret could not be loaded without exposing its value."""


class KeychainCommandRunner(Protocol):
    def __call__(self, command: Sequence[str]) -> subprocess.CompletedProcess[str]: ...


def _run(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )


@dataclass(slots=True)
class MacOSKeychainReader:
    command_runner: KeychainCommandRunner = _run

    def read(self, *, service: str, account: str) -> str:
        if not service.strip() or not account.strip():
            raise ValueError("Keychain service and account are required")
        command = (
            "/usr/bin/security",
            "find-generic-password",
            "-a",
            account,
            "-s",
            service,
            "-w",
        )
        try:
            result = self.command_runner(command)
        except (OSError, subprocess.SubprocessError) as exc:
            raise KeychainReadError("unable to read the requested Keychain secret") from exc
        if result.returncode != 0 or not isinstance(result.stdout, str):
            raise KeychainReadError("unable to read the requested Keychain secret")
        secret = result.stdout.strip()
        if not secret or len(secret) > 8192 or any(character.isspace() for character in secret):
            raise KeychainReadError("the requested Keychain secret is invalid")
        return secret


def read_anthropic_api_key(reader: MacOSKeychainReader | None = None) -> str:
    return (reader or MacOSKeychainReader()).read(
        service=ANTHROPIC_KEYCHAIN_SERVICE,
        account=ANTHROPIC_KEYCHAIN_ACCOUNT,
    )


__all__ = [
    "KeychainCommandRunner",
    "KeychainReadError",
    "MacOSKeychainReader",
    "ANTHROPIC_KEYCHAIN_ACCOUNT",
    "ANTHROPIC_KEYCHAIN_SERVICE",
    "read_anthropic_api_key",
]

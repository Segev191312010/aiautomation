"""Secret-safe macOS Keychain access for the Anthropic review key, the Telegram
bot token and the dead-man's switch ping URL."""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
import subprocess
from typing import Protocol


ANTHROPIC_KEYCHAIN_SERVICE = "com.maccabi.autotrader-v1.anthropic"
ANTHROPIC_KEYCHAIN_ACCOUNT = "anthropic-api-key"
# Stored with /usr/bin/security add-generic-password, so the same binary can
# read it back under launchd without an interactive Keychain prompt.
TELEGRAM_KEYCHAIN_SERVICE = "com.maccabi.autotrader-v1.telegram"
TELEGRAM_KEYCHAIN_ACCOUNT = "telegram-bot-token"
# The external dead-man's switch ping URL (trading.operations.heartbeat). It
# grants no authority, but anyone holding it can fake a heartbeat, so it is a
# secret: never an environment variable, the config file, an argv or a log line.
HEARTBEAT_KEYCHAIN_SERVICE = "com.maccabi.autotrader-v1.heartbeat"
HEARTBEAT_KEYCHAIN_ACCOUNT = "heartbeat-url"


# /usr/bin/security's exit code for errSecItemNotFound.
ITEM_NOT_FOUND_CODE = 44


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

    def exists(self, *, service: str, account: str) -> bool:
        """Whether the item exists, its secret never read: ``find-generic-password``
        without ``-w`` (or ``-g``) prints the item's attributes only, and they are
        discarded. Exit 44 is ``errSecItemNotFound``; anything else raises."""
        if not service.strip() or not account.strip():
            raise ValueError("Keychain service and account are required")
        command = ("/usr/bin/security", "find-generic-password", "-a", account, "-s", service)
        try:
            result = self.command_runner(command)
        except (OSError, subprocess.SubprocessError) as exc:
            raise KeychainReadError("unable to look up the requested Keychain item") from exc
        if result.returncode == 0:
            return True
        if result.returncode == ITEM_NOT_FOUND_CODE:
            return False
        raise KeychainReadError("unable to look up the requested Keychain item")


def read_anthropic_api_key(reader: MacOSKeychainReader | None = None) -> str:
    return (reader or MacOSKeychainReader()).read(
        service=ANTHROPIC_KEYCHAIN_SERVICE,
        account=ANTHROPIC_KEYCHAIN_ACCOUNT,
    )


def read_telegram_bot_token(reader: MacOSKeychainReader | None = None) -> str:
    return (reader or MacOSKeychainReader()).read(
        service=TELEGRAM_KEYCHAIN_SERVICE,
        account=TELEGRAM_KEYCHAIN_ACCOUNT,
    )


def read_heartbeat_url(reader: MacOSKeychainReader | None = None) -> str:
    return (reader or MacOSKeychainReader()).read(
        service=HEARTBEAT_KEYCHAIN_SERVICE,
        account=HEARTBEAT_KEYCHAIN_ACCOUNT,
    )


def heartbeat_item_exists(reader: MacOSKeychainReader | None = None) -> bool:
    """The ping URL's item is there; its secret is never read (the agent-run dry run)."""
    return (reader or MacOSKeychainReader()).exists(
        service=HEARTBEAT_KEYCHAIN_SERVICE,
        account=HEARTBEAT_KEYCHAIN_ACCOUNT,
    )


__all__ = [
    "KeychainCommandRunner",
    "KeychainReadError",
    "MacOSKeychainReader",
    "ANTHROPIC_KEYCHAIN_ACCOUNT",
    "ANTHROPIC_KEYCHAIN_SERVICE",
    "HEARTBEAT_KEYCHAIN_ACCOUNT",
    "HEARTBEAT_KEYCHAIN_SERVICE",
    "ITEM_NOT_FOUND_CODE",
    "TELEGRAM_KEYCHAIN_ACCOUNT",
    "TELEGRAM_KEYCHAIN_SERVICE",
    "heartbeat_item_exists",
    "read_anthropic_api_key",
    "read_heartbeat_url",
    "read_telegram_bot_token",
]

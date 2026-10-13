"""Tell the owner's phone when the database needs migration; no process migrates.

While the schema is not current every child that uses the database refuses
to start (exit 75) and the supervisor retries it. Two processes can still
reach Telegram and say so, each at most once per day:

* the notifier (07:55-16:15 ET on trading days) keeps running, because it
  only reads tables older than any pending migration; it sends the alert
  once per session date (its own delivery state) and folds the children's
  repeated exit-75 restarts into it instead of one message per restart;
* the always-on Telegram assistant refuses at any hour (e.g. a supervisor
  restarted after an evening deploy without the migration); before it exits
  it sends the alert directly, claimed once per ET day with an exclusive
  marker file, so its restarts every few minutes never repeat it.

The alert never migrates anything: it names the command for the owner.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timezone
import logging
import os
from pathlib import Path

import httpx

from trading.operations.keychain import KeychainReadError, read_telegram_bot_token
from trading.operations.notifier import (
    TelegramAPIError,
    TelegramBotAPI,
    TelegramConfigError,
    is_valid_bot_token,
    load_chat_id,
    telegram_config_file,
)
from trading.operations.paths import (
    FILE_MODE,
    PathSecurityError,
    UserPaths,
    ensure_private_directory,
)
from trading.operations.schema_gate import MIGRATE_COMMAND
from trading.scheduler.sessions import MARKET_TIMEZONE
from trading.storage.migrations import SchemaStatus

logger = logging.getLogger("autotrader.shadow.schema")

ALERT_DIRECTORY_NAME = "alerts"
SESSION_OVERRIDE_FLAG = "--during-session-i-know"


def schema_alert_text(status: SchemaStatus) -> str:
    """Plain text (no Markdown) for the phone."""
    if status.problems:
        return (
            f"⛔ Database schema does not match this code ({status.describe()}). Every trading"
            " child refuses to start (exit 75) and nothing was migrated. Deploy the matching code"
            " or restore the pre-migration backup; details: autotraderctl db status"
        )
    return (
        f"⛔ Database needs migration — run `{MIGRATE_COMMAND}` after the close."
        f" {status.describe()}. Until then the screener, strategies and this Telegram assistant"
        " refuse to start (exit 75) and retry every few minutes; the bridge still manages open"
        f" positions and `autotraderctl paper stop` still works. Inside the session: `{MIGRATE_COMMAND}"
        f" {SESSION_OVERRIDE_FLAG}`."
    )


def alert_marker(paths: UserPaths, day: date) -> Path:
    return paths.data_directory / ALERT_DIRECTORY_NAME / f"schema-{day.isoformat()}.sent"


def claim_daily_alert(paths: UserPaths, day: date) -> bool:
    """Atomically claim ``day``'s alert: True for exactly one caller (O_EXCL)."""
    directory = ensure_private_directory(paths.data_directory / ALERT_DIRECTORY_NAME)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
    try:
        descriptor = os.open(directory / alert_marker(paths, day).name, flags, FILE_MODE)
    except FileExistsError:
        return False
    os.close(descriptor)
    return True


def release_daily_alert(paths: UserPaths, day: date) -> None:
    """After a failed send, so a later refusal the same day can try again."""
    alert_marker(paths, day).unlink(missing_ok=True)


async def send_schema_alert(
    status: SchemaStatus,
    *,
    paths: UserPaths | None = None,
    now: datetime | None = None,
    read_token: Callable[[], str] = read_telegram_bot_token,
    transport: httpx.AsyncBaseTransport | None = None,
) -> bool:
    """Send the alert once per ET day to the pinned owner chat; never raises.

    Returns True only when this call delivered it. Used by the refusing
    Telegram assistant, which has no running notifier queue to go through.
    """
    if status.current:
        return False
    target = paths or UserPaths.current()
    day = (now or datetime.now(timezone.utc)).astimezone(MARKET_TIMEZONE).date()
    try:
        if not claim_daily_alert(target, day):
            return False
    except (OSError, PathSecurityError) as exc:
        logger.error("schema alert not sent: cannot claim today's alert (%s)", type(exc).__name__)
        return False
    try:
        token = read_token()
        chat_id = load_chat_id(telegram_config_file(target))
        if not is_valid_bot_token(token) or chat_id is None:
            raise TelegramConfigError("no usable bot token or pinned owner chat")
        async with TelegramBotAPI(token, transport=transport) as api:
            await api.send_message(chat_id, schema_alert_text(status))
    except (KeychainReadError, TelegramAPIError, TelegramConfigError, PathSecurityError, OSError, ValueError) as exc:
        # The type only: a message could carry the bot token.
        logger.error("schema alert not delivered (%s); the next refusal retries", type(exc).__name__)
        release_daily_alert(target, day)
        return False
    logger.critical("schema alert sent to the owner's phone: %s", status.describe())
    return True


__all__ = [
    "ALERT_DIRECTORY_NAME",
    "SESSION_OVERRIDE_FLAG",
    "alert_marker",
    "claim_daily_alert",
    "release_daily_alert",
    "schema_alert_text",
    "send_schema_alert",
]

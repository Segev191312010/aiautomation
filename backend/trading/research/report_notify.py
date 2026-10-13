"""Weekly Telegram summary of the nightly research replay.

The supervisor replays the research store every trading evening; the owner's
phone hears about it once a week. After a successful replay the plain-text
summary goes to the pinned owner chat as ONE message:

    weekly  after the close of the week's last XNYS session (Friday, or the
            session before a Friday holiday), at most once per ISO week, so a
            supervisor restart that reruns the replay never sends it twice;
    now     on demand (``autotraderctl research replay --notify``).

Telegram that is not set up (no usable bot token in the Keychain, no pinned
owner chat) is skipped silently: the replay's own report files are the record.
The chat id comes only from telegram.json, as the assistant pins it, never from
getUpdates: the always-on assistant is the single getUpdates consumer, and a
second one would knock it off. The bot token exists only inside
TelegramBotAPI's request path; nothing here prints or logs it, and failures are
reported by exception type alone.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, datetime, timezone
import json
import logging
from pathlib import Path
import time as monotonic_time
from typing import Literal

import httpx

from trading.operations.keychain import KeychainReadError, read_telegram_bot_token
from trading.operations.notifier import (
    TelegramBotAPI,
    TelegramConfigError,
    TelegramNotifier,
    is_valid_bot_token,
    load_chat_id,
    normalize_text,
    split_message,
    telegram_config_file,
)
from trading.operations.paths import PathSecurityError, UserPaths, atomic_private_write
from trading.research.bars_store import NEW_YORK, ResearchPaths, previous_session
from trading.scheduler.sessions import market_session, next_market_session

logger = logging.getLogger("autotrader.shadow.research")

NotifyMode = Literal["never", "weekly", "now"]
NOTIFY_MODES: tuple[NotifyMode, ...] = ("never", "weekly", "now")

# Outcomes, named so callers branch on codes rather than prose.
SENT = "sent"
NOT_REQUESTED = "not_requested"
NOT_DUE = "not_due"
ALREADY_SENT = "already_sent"
NOT_CONFIGURED = "not_configured"
FAILED = "failed"
OUTCOME_TEXT = {
    SENT: "sent",
    NOT_REQUESTED: "not requested",
    NOT_DUE: "not the week's last session",
    ALREADY_SENT: "this week's summary was already sent",
    NOT_CONFIGURED: (
        "Telegram is not set up (bot token in the Keychain and a pinned chat:"
        " autotraderctl notify test)"
    ),
    FAILED: "Telegram did not accept it (details in the shadow log)",
}

WEEKLY_STATE_FILENAME = "telegram-weekly.json"
WEEKLY_STATE_SCHEMA = "autotrader-v1/research-weekly-summary"
WEEKLY_STATE_VERSION = 1
_STATE_MAX_BYTES = 4096

Clock = Callable[[], datetime]
Sleeper = Callable[[float], Awaitable[None]]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class ReportNotifyEnvironment:
    """Everything the summary touches outside the process; tests substitute each."""

    paths: UserPaths
    read_token: Callable[[], str] = read_telegram_bot_token
    transport: httpx.AsyncBaseTransport | None = None
    clock: Clock = _utc_now
    sleep: Sleeper = asyncio.sleep
    monotonic: Callable[[], float] = monotonic_time.monotonic

    @classmethod
    def current(cls) -> ReportNotifyEnvironment:
        return cls(paths=UserPaths.current())


def weekly_summary_due(day: date) -> bool:
    """True on the week's last XNYS session: Friday, or the one before a Friday holiday."""
    if market_session(day) is None:
        return False
    following = next_market_session(day).session_date
    return following.isocalendar()[:2] != day.isocalendar()[:2]


def iso_week(day: date) -> str:
    year, week, _ = day.isocalendar()
    return f"{year}-W{week:02d}"


def latest_closed_session(now: datetime) -> date:
    """The newest XNYS session that has closed by ``now``."""
    day = now.astimezone(NEW_YORK).date()
    session = market_session(day)
    if session is not None and session.closes_at <= now:
        return day
    return previous_session(day)


def summary_message(
    summary: str, *, now: datetime, newest_session: date | None, weekly: bool
) -> str:
    """One plain-text message: a title, a gap warning when tonight's session is
    missing from the store, then the replay summary, cut to one message."""
    today = now.astimezone(NEW_YORK).date()
    title = "Weekly research summary" if weekly else "Research summary"
    lines = [f"📊 {title} · {today:%a %Y-%m-%d}"]
    expected = latest_closed_session(now)
    if newest_session is None or newest_session < expected:
        stored = "no session" if newest_session is None else newest_session.isoformat()
        # The supervisor's notifier stops at 16:15, so a failed recorder is
        # otherwise never reported to the phone.
        lines.append(
            f"⚠️ The research store ends at {stored}: {expected.isoformat()} was not"
            " recorded (see recorder.log)."
        )
    lines += ["", summary]
    text = normalize_text("\n".join(lines))
    return split_message(text, max_parts=1)[0]


def weekly_state_file(paths: UserPaths) -> Path:
    return ResearchPaths.for_user(paths).reports_directory / WEEKLY_STATE_FILENAME


def _sent_week(path: Path) -> str | None:
    """The ISO week of the last weekly summary sent; unreadable state reads as none.

    Failing open costs at most one duplicate message; failing closed could
    silence every later week.
    """
    if not path.exists() and not path.is_symlink():
        return None
    try:
        if path.is_symlink() or not path.is_file():
            raise PathSecurityError("the weekly summary state must be a regular file")
        with path.open("rb") as stream:
            raw = stream.read(_STATE_MAX_BYTES + 1)
        if len(raw) > _STATE_MAX_BYTES:
            raise ValueError("the weekly summary state is larger than expected")
        data = json.loads(raw.decode("utf-8"))
    except (OSError, ValueError, PathSecurityError) as exc:
        logger.warning("ignoring %s: %s", path.name, type(exc).__name__)
        return None
    if (
        not isinstance(data, dict)
        or data.get("schema") != WEEKLY_STATE_SCHEMA
        or data.get("version") != WEEKLY_STATE_VERSION
        or not isinstance(data.get("week"), str)
    ):
        logger.warning("ignoring %s: unexpected content", path.name)
        return None
    return str(data["week"])


def _record_sent_week(paths: UserPaths, day: date, now: datetime) -> None:
    research = ResearchPaths.for_user(paths)
    payload = {
        "schema": WEEKLY_STATE_SCHEMA,
        "version": WEEKLY_STATE_VERSION,
        "week": iso_week(day),
        "session_date": day.isoformat(),
        "sent_at": now.astimezone(timezone.utc).isoformat(),
    }
    try:
        research.ensure_private(research.reports_directory)
        atomic_private_write(weekly_state_file(paths), json.dumps(payload).encode("utf-8"))
    except (OSError, PathSecurityError, ValueError) as exc:
        logger.warning("weekly summary state not saved: %s", type(exc).__name__)


def _usable_token(environment: ReportNotifyEnvironment) -> str | None:
    try:
        token = environment.read_token()
    except (KeychainReadError, ValueError, OSError):
        return None
    return token if is_valid_bot_token(token) else None


def _pinned_chat(paths: UserPaths) -> int | None:
    try:
        return load_chat_id(telegram_config_file(paths))
    except (TelegramConfigError, PathSecurityError, OSError) as exc:
        logger.warning("Telegram chat configuration unusable: %s", type(exc).__name__)
        return None


async def deliver_summary(
    summary: str,
    *,
    mode: NotifyMode,
    newest_session: date | None,
    env: ReportNotifyEnvironment | None = None,
) -> str:
    """Send ``summary`` as one Telegram message when ``mode`` says so; returns an outcome."""
    if mode not in NOTIFY_MODES:
        raise ValueError(f"unknown notify mode {mode!r}")
    if mode == "never":
        return NOT_REQUESTED
    environment = env or ReportNotifyEnvironment.current()
    now = environment.clock()
    today = now.astimezone(NEW_YORK).date()
    weekly = mode == "weekly"
    if weekly:
        # After the close only: a morning run must not use up the week's
        # message before its last session is in the store.
        if not weekly_summary_due(today) or latest_closed_session(now) != today:
            return NOT_DUE
        if _sent_week(weekly_state_file(environment.paths)) == iso_week(today):
            logger.info("research summary for %s was already sent", iso_week(today))
            return ALREADY_SENT
    token = _usable_token(environment)
    chat_id = None if token is None else _pinned_chat(environment.paths)
    if token is None or chat_id is None:
        logger.info("Telegram is not set up; research summary not sent")
        return NOT_CONFIGURED
    message = summary_message(summary, now=now, newest_session=newest_session, weekly=weekly)
    try:
        async with TelegramBotAPI(token, transport=environment.transport) as api:
            notifier = TelegramNotifier(
                api,
                chat_id=chat_id,
                sleep=environment.sleep,
                monotonic=environment.monotonic,
            )
            try:
                delivered = await notifier.notify(message)
            finally:
                await notifier.aclose()
    except Exception as exc:  # noqa: BLE001 - by type only: no text that could carry the token
        logger.warning("research summary not sent: %s", type(exc).__name__)
        return FAILED
    if not delivered:
        logger.warning("research summary not delivered")
        return FAILED
    if weekly:
        _record_sent_week(environment.paths, today, now)
    logger.info("research summary sent to Telegram")
    return SENT


def notify_summary(
    summary: str,
    *,
    mode: NotifyMode,
    newest_session: date | None,
    env: ReportNotifyEnvironment | None = None,
) -> str:
    """Synchronous entry for the replay CLI. A Telegram problem never raises."""
    if mode not in NOTIFY_MODES:
        raise ValueError(f"unknown notify mode {mode!r}")
    try:
        return asyncio.run(
            deliver_summary(summary, mode=mode, newest_session=newest_session, env=env)
        )
    except Exception as exc:  # noqa: BLE001 - by type only: no text that could carry the token
        logger.warning("research summary not sent: %s", type(exc).__name__)
        return FAILED


__all__ = [
    "ALREADY_SENT",
    "FAILED",
    "NOTIFY_MODES",
    "NOT_CONFIGURED",
    "NOT_DUE",
    "NOT_REQUESTED",
    "OUTCOME_TEXT",
    "SENT",
    "WEEKLY_STATE_FILENAME",
    "NotifyMode",
    "ReportNotifyEnvironment",
    "deliver_summary",
    "iso_week",
    "latest_closed_session",
    "notify_summary",
    "summary_message",
    "weekly_state_file",
    "weekly_summary_due",
]

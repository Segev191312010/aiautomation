"""Weekly Telegram summary of the nightly research replay and its evidence.

The supervisor replays the research store every trading evening; the owner's
phone hears about it once a week. The summary has these sections, most
important first, because one Telegram message is cut at its end:

1. evidence: each strategy ELIGIBLE or not under the paper promotion rule,
   and why (the rule only reports; nothing is granted), with the AI
   do-no-harm verdict and the signal-outcome tracker's night, then the
   promoter's proposals (PROPOSAL / HELD / DEMOTION PROPOSAL lines) and each
   sendable identity's size and probation;
2. window research: the replay's swept windows and its trades by signal
   hour, plus the live tracked setups by hour;
3. live R: every real PAPER bracket in R after commissions;
4. v7c: the orders bridge v7c refused tonight and this week, bot and manual,
   with what they would have done in R, the stop slippage per identity and
   P&L by source (``trading/research/v7c_report.py``); a night without that
   data says "no v7c data", never nothing;
5. phase-3 readiness: managed MOMO (shadow) against MOMO_HOD_V2 on the same
   setups, and the do-no-harm verdict (``trading/research/overlay.py``);
6. the replay's own per-strategy summary.

After a successful replay the plain-text summary goes to the pinned owner
chat as ONE message:

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
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_EVEN, Decimal
import json
import logging
from pathlib import Path
import time as monotonic_time
from typing import TYPE_CHECKING, Literal

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

if TYPE_CHECKING:
    from trading.research.nightly import NightlyEvidence
    from trading.research.outcomes import TrackerReport
    from trading.research.paper_promotion import PromotionReport

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


def _r(value: Decimal | None) -> str:
    if value is None:
        return "n/a"
    return f"{value.quantize(Decimal('0.01'), rounding=ROUND_HALF_EVEN):+}R"


def _reason_text(report: PromotionReport, reason: str) -> str | None:
    """One failed check as the numbers behind it; ``None`` when there is no number yet.

    A bound needs two days of trades and a profit factor one trade: before
    that the check fails without a value worth a phone line (evidence.json
    keeps every reason).
    """
    from trading.research import paper_promotion as rule

    live, replay = report.live, report.replay
    texts: dict[str, str | None] = {
        rule.LIVE_SESSIONS_BELOW_MINIMUM: f"live sessions {report.live_sessions}/{rule.MIN_SESSIONS}",
        rule.LIVE_SETUPS_BELOW_MINIMUM: f"live setups {live.trades}/{rule.MIN_LIVE_SETUPS}",
        rule.LIVE_LOWER_BOUND_NOT_POSITIVE: _maybe("live bound", live.bound.lower),
        rule.LIVE_PROFIT_FACTOR_BELOW_MINIMUM: _factor("live", live.profit_factor, live.trades),
        rule.REPLAY_UNAVAILABLE: "no replay"
        + (f" ({report.replay_note.split(':')[0]})" if report.replay_note else ""),
    }
    if replay is not None:
        texts.update(
            {
                rule.REPLAY_SESSIONS_BELOW_MINIMUM: f"replay sessions {replay.sessions}/{rule.MIN_SESSIONS}",
                rule.REPLAY_TRADES_BELOW_MINIMUM: f"replay trades {replay.trades}/{rule.MIN_REPLAY_TRADES}",
                rule.REPLAY_LOWER_BOUND_NOT_POSITIVE: _maybe("replay bound", replay.bound.lower),
                rule.REPLAY_PROFIT_FACTOR_BELOW_MINIMUM: _factor(
                    "replay", replay.profit_factor, replay.trades
                ),
                rule.REPLAY_DRAWDOWN_ABOVE_MAXIMUM: (
                    f"replay drawdown {_r(replay.max_drawdown_r).lstrip('+')}"
                    f" > {rule.MAX_REPLAY_DRAWDOWN_R}R"
                ),
            }
        )
    return texts.get(reason, reason)


def _maybe(label: str, value: Decimal | None) -> str | None:
    return None if value is None else f"{label} {_r(value)}"


def _factor(sample: str, value: Decimal | None, trades: int) -> str | None:
    if not trades or value is None:
        return None
    return f"{sample} PF {value.quantize(Decimal('0.01'), rounding=ROUND_HALF_EVEN)}"


_AI_TEXT = {
    "advisory": "AI advisory (its picks cost R)",
    "active": "AI review keeps its say",
    "insufficient_data": "AI: too little data",
}


def evidence_lines(
    reports: Sequence[PromotionReport], *, tracker: TrackerReport | None = None
) -> list[str]:
    """Per strategy: ELIGIBLE or not under the paper promotion rule, and why."""
    from trading.research.paper_promotion import AI_INSUFFICIENT, RULE_VERSION

    lines = [f"Paper promotion ({RULE_VERSION}; reports only, nothing is granted)"]
    for report in reports:
        if report.eligible:
            verdict = "ELIGIBLE for the owner's review"
        else:
            texts = [text for reason in report.reasons if (text := _reason_text(report, reason))]
            verdict = "not eligible: " + ", ".join(texts or report.reasons)
        ai = report.ai
        ai_text = _AI_TEXT.get(ai.status, ai.status)
        if ai.status != AI_INSUFFICIENT:
            ai_text += (
                f" (picks - all {_r(ai.difference.estimate)}, upper bound {_r(ai.difference.upper)})"
            )
        lines.append(f"{report.strategy}: {verdict}; {ai_text}")
    if tracker is not None:
        lines.append(tracker.summary_line())
    return lines


def window_research_lines(evidence: NightlyEvidence) -> list[str]:
    """The replay's swept windows and hours, then the live tracked setups by hour."""
    from trading.research.windows import window_lines

    lines = ["Window research (replay, rules-only, net R after costs; * = outside the V1 window)"]
    if evidence.windows_note:
        lines.append(evidence.windows_note)
    for family in evidence.windows:
        lines.extend(window_lines(family))
    if evidence.live_hours:
        lines.append("Live tracked setups by signal hour (net R)")
        for strategy, hours in evidence.live_hours.items():
            lines.append(
                f"  {strategy}: "
                + " · ".join(f"{row.hour:02d}h {row.trades} {_r(row.expectancy_r)}" for row in hours)
            )
    return lines


def live_r_lines(evidence: NightlyEvidence) -> list[str]:
    from trading.research.live_ledger import ledger_lines

    return ["Live R (PAPER bridge, after commissions)"] + [
        f"  {line}" for line in ledger_lines(evidence.live_r, week_start=evidence.week_start)
    ]


def v7c_lines(evidence: NightlyEvidence) -> list[str]:
    """Refused orders (tonight, this week), stop slippage and P&L by source; never empty."""
    from trading.research.v7c_report import report_lines

    return report_lines(evidence.v7c)


def phase3_lines(evidence: NightlyEvidence) -> list[str]:
    """Phase-3 readiness: managed MOMO against MOMO_HOD_V2 on the same setups."""
    from trading.research.overlay import readiness_lines

    return [] if evidence.phase3 is None else readiness_lines(evidence.phase3)


def evidence_text(evidence: NightlyEvidence) -> str:
    """The evidence, window research, live R, v7c and phase-3 sections as one plain text.

    The phase-3 section is appended after the others (absent when the
    evidence carries no scorecard), so the earlier sections never change.
    """
    first = evidence_lines(evidence.promotion, tracker=evidence.tracker)
    if evidence.proposals is not None:
        first += evidence.proposals.lines()
    sections = [
        "\n".join(first),
        "\n".join(window_research_lines(evidence)),
        "\n".join(live_r_lines(evidence)),
        "\n".join(v7c_lines(evidence)),
    ]
    phase3 = phase3_lines(evidence)
    if phase3:
        sections.append("\n".join(phase3))
    return "\n\n".join(sections)


def compose_summary(replay: str, evidence: NightlyEvidence | None = None) -> str:
    """The weekly text: evidence, window research and live R ahead of the replay."""
    if evidence is None:
        return replay
    return f"{evidence_text(evidence)}\n\n{replay}"


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
    "compose_summary",
    "deliver_summary",
    "evidence_lines",
    "evidence_text",
    "iso_week",
    "latest_closed_session",
    "live_r_lines",
    "notify_summary",
    "phase3_lines",
    "summary_message",
    "v7c_lines",
    "weekly_state_file",
    "weekly_summary_due",
    "window_research_lines",
]

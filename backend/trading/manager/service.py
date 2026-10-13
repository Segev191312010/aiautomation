"""Entry points: the ``manager`` child and the owner's ``autotraderctl manager`` commands.

``manager run`` writes the session's day plans and exits after the last
slot. It always runs in shadow: it holds no TWS client and no order path,
and writes only the manager's own tables. ``manager on|off|status`` is the
CLI half of the deterministic switch (Telegram's ``/autopilot`` is the
other); while migration 16 is pending they refuse with a reply naming
``autotraderctl db migrate``.
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import date, datetime, timezone
import logging
import sys
from typing import Any, TextIO

from trading.manager.constants import PLAN_TIMEOUT_SECONDS
from trading.scheduler.sessions import MARKET_TIMEZONE, MarketSession, market_session

logger = logging.getLogger("autotrader.manager.service")

MIGRATE_HINT = "run `autotraderctl db migrate` after the close"
CLI_ACTOR = "owner (autotraderctl)"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def session_for(requested: date | None, *, now: datetime) -> MarketSession | None:
    day = requested or now.astimezone(MARKET_TIMEZONE).date()
    return market_session(day)


async def run_manager(
    *,
    session: MarketSession,
    client_factory: Callable[[], Any],
    clock: Callable[[], datetime] = _utc_now,
    sleep: Callable[[float], Any] = asyncio.sleep,
) -> dict[str, object]:
    """The child's work: every remaining slot of ``session``."""
    from trading.manager.planner import AnthropicPlanTransport, run_manager_day

    transport = AnthropicPlanTransport(client=client_factory())
    report = await run_manager_day(session=session, clock=clock, sleep=sleep, transport=transport)
    logger.info("manager day finished: %s", report.summary())
    return report.summary()


def anthropic_client_factory() -> Any:
    """The official client with the Keychain key; read only when the child plans."""
    from trading.intelligence.anthropic_transport import build_anthropic_client
    from trading.operations.keychain import read_anthropic_api_key

    return build_anthropic_client(
        api_key=read_anthropic_api_key(), timeout_seconds=PLAN_TIMEOUT_SECONDS
    )


async def switch_command(
    action: str,
    *,
    database: str,
    reason: str | None = None,
    now: datetime | None = None,
    out: TextIO | None = None,
    err: TextIO | None = None,
) -> int:
    """``autotraderctl manager on|off|status``."""
    from trading.manager.report import format_status, read_status
    from trading.manager.store import manager_tables_ready
    from trading.manager.switch import set_switch

    stream = out or sys.stdout
    errors = err or sys.stderr
    ready, problem = manager_tables_ready(database)
    if not ready:
        print(f"error: autopilot is unavailable: {problem}; {MIGRATE_HINT}", file=errors)
        return 1
    moment = now or _utc_now()
    if action in ("on", "off"):
        await set_switch(
            action,  # type: ignore[arg-type]
            source="cli",
            actor=CLI_ACTOR,
            reason=reason or f"autopilot {action} via autotraderctl",
            now=moment,
        )
    status = await read_status(moment.astimezone(MARKET_TIMEZONE).date(), moment)
    print(format_status(status), file=stream)
    return 0


__all__ = ["anthropic_client_factory", "run_manager", "session_for", "switch_command"]

"""The Telegram half of the autopilot switch: ``/autopilot on|off|status`` and ``/plan``.

Reached only from the assistant's deterministic command routing
(``telegram_bot.TelegramAssistant._route``); the assistant's Claude has no
tool that reaches this module. The assistant itself still needs only
migrations up to 12, so ``/stop`` never waits for migration 16: while it is
pending these two commands refuse with a reply naming
``autotraderctl db migrate`` and nothing else changes.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone

from trading.manager.store import manager_tables_ready
from trading.scheduler.sessions import MARKET_TIMEZONE
from trading.storage.manager_schema import MANAGER_PHASE2_MIGRATION

TELEGRAM_ACTOR = "owner (telegram)"
AUTOPILOT_USAGE = "Usage: /autopilot on · /autopilot off · /autopilot status"
NOT_READY_TEXT = (
    "⚠️ Autopilot isn't available yet: the database needs migration "
    f"{MANAGER_PHASE2_MIGRATION}. On the Mac, after the close, run: autotraderctl db migrate. "
    "Everything else (/stop, orders, /status) keeps working."
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _default_database() -> str:
    from config import cfg

    return str(cfg.DB_PATH)


class ManagerDesk:
    """The deterministic handlers behind ``/autopilot`` and ``/plan``."""

    def __init__(
        self,
        *,
        database: Callable[[], str] = _default_database,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._database = database
        self._clock = clock

    def _ready(self) -> bool:
        ready, _problem = manager_tables_ready(self._database())
        return ready

    async def autopilot(self, args: str) -> str:
        action = args.strip().lower() or "status"
        if action not in {"on", "off", "status"}:
            return AUTOPILOT_USAGE
        if not self._ready():
            return NOT_READY_TEXT
        from trading.manager.report import format_status, read_status
        from trading.manager.switch import set_switch

        now = self._clock()
        if action in {"on", "off"}:
            await set_switch(
                action,  # type: ignore[arg-type]
                source="telegram",
                actor=TELEGRAM_ACTOR,
                reason=f"/autopilot {action}",
                now=now,
            )
        status = await read_status(now.astimezone(MARKET_TIMEZONE).date(), now)
        return format_status(status)

    async def plan(self) -> str:
        if not self._ready():
            return NOT_READY_TEXT
        from trading.manager.report import format_current_plan, read_status, symbols_for

        now = self._clock()
        status = await read_status(now.astimezone(MARKET_TIMEZONE).date(), now)
        current = status.current
        symbols = await symbols_for(
            [entry.con_id for entry in current.plan.focus] if current and current.plan else []
        )
        return format_current_plan(status, symbols=symbols)


__all__ = ["AUTOPILOT_USAGE", "ManagerDesk", "NOT_READY_TEXT"]

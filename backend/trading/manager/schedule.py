"""When the manager plans: 09:20, 09:46, then hourly 10:30-14:30 ET, half-day aware.

Pure calendar arithmetic (no database, no clock), shared by the manager
child and the supervisor's window for it.
"""
from __future__ import annotations

from datetime import datetime, time, timezone

from trading.manager.constants import LAST_PLAN_BEFORE_CLOSE, PLAN_TIMES
from trading.scheduler.sessions import MARKET_TIMEZONE, MarketSession

UTC = timezone.utc


def plan_times(session: MarketSession) -> tuple[datetime, ...]:
    """The session's plan slots in UTC; a half day drops those too close to its close."""
    latest = session.closes_at - LAST_PLAN_BEFORE_CLOSE
    slots = (
        datetime.combine(session.session_date, local, tzinfo=MARKET_TIMEZONE)
        for local in PLAN_TIMES
    )
    return tuple(slot.astimezone(UTC) for slot in slots if slot <= latest)


def slot_label(moment: datetime) -> str:
    """``HH:MM`` in New York time: the plan row's ``slot``."""
    return f"{moment.astimezone(MARKET_TIMEZONE):%H:%M}"


def slot_time(session: MarketSession, label: str) -> datetime:
    hour, minute = (int(part) for part in label.split(":"))
    return datetime.combine(session.session_date, time(hour, minute), tzinfo=MARKET_TIMEZONE).astimezone(UTC)


def current_slot(session: MarketSession, now: datetime) -> datetime | None:
    """The newest slot at or before ``now``, or None before the first."""
    due = [slot for slot in plan_times(session) if slot <= now]
    return due[-1] if due else None


def next_slot(session: MarketSession, after: datetime) -> datetime | None:
    return next((slot for slot in plan_times(session) if slot > after), None)


def planning_window(session: MarketSession) -> tuple[datetime, datetime]:
    """``(first slot, last slot)`` in UTC; the supervisor runs the child around it."""
    slots = plan_times(session)
    if not slots:
        raise ValueError(f"{session.session_date.isoformat()} has no plan slot")
    return slots[0], slots[-1]


__all__ = [
    "current_slot",
    "next_slot",
    "plan_times",
    "planning_window",
    "slot_label",
    "slot_time",
]

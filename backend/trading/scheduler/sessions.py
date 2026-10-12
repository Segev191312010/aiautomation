"""Pinned XNYS cash-equity session timing without network dependencies."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from functools import lru_cache
from importlib.metadata import PackageNotFoundError, version
from zoneinfo import ZoneInfo

import exchange_calendars as exchange_calendars


MARKET_TIMEZONE = ZoneInfo("America/New_York")
UTC = timezone.utc
MARKET_CALENDAR_ID = "XNYS"
MARKET_CALENDAR_VERSION = "4.11.2"
_OPEN = time(9, 30)
_SCAN_OFFSET = timedelta(minutes=15, seconds=5)
_FIRST_SIGNAL_OFFSET = timedelta(minutes=20)
_ENTRY_CUTOFF = time(12, 0)


@lru_cache(maxsize=1)
def _calendar():
    try:
        installed = version("exchange-calendars")
    except PackageNotFoundError as exc:
        raise RuntimeError("the pinned XNYS calendar dependency is unavailable") from exc
    if installed != MARKET_CALENDAR_VERSION:
        raise RuntimeError(
            f"exchange-calendars {MARKET_CALENDAR_VERSION} is required; found {installed}"
        )
    calendar = exchange_calendars.get_calendar(MARKET_CALENDAR_ID)
    if calendar.name != MARKET_CALENDAR_ID or str(calendar.tz) != str(MARKET_TIMEZONE):
        raise RuntimeError("the pinned market calendar identity is invalid")
    return calendar


def is_market_holiday(session_date: date) -> bool:
    """Return whether a date is a weekend or scheduled full market holiday."""
    return not bool(_calendar().is_session(session_date.isoformat()))


def _aware_at(session_date: date, local_time: time) -> datetime:
    return datetime.combine(session_date, local_time, tzinfo=MARKET_TIMEZONE)


@dataclass(frozen=True)
class MarketSession:
    """The fixed schedule checkpoints consumed by the future orchestrator."""

    session_date: date
    opens_at: datetime
    opening_range_ends_at: datetime
    scanner_at: datetime
    first_signal_at: datetime
    entry_cutoff_at: datetime
    closes_at: datetime
    half_day: bool

    def __post_init__(self) -> None:
        fields = (
            self.opens_at,
            self.opening_range_ends_at,
            self.scanner_at,
            self.first_signal_at,
            self.entry_cutoff_at,
            self.closes_at,
        )
        if any(value.tzinfo is None or value.utcoffset() is None for value in fields):
            raise ValueError("market session timestamps must be timezone-aware")
        if tuple(sorted(fields)) != fields:
            raise ValueError("market session checkpoints must be ordered")

    @property
    def is_opening_range(self) -> tuple[datetime, datetime]:
        return self.opens_at, self.opening_range_ends_at


def market_session(session_date: date) -> MarketSession | None:
    """Build a NYSE regular session, or ``None`` for a closed date."""
    if is_market_holiday(session_date):
        return None
    calendar = _calendar()
    label = session_date.isoformat()
    opens_at_utc = calendar.session_open(label).to_pydatetime().astimezone(UTC)
    closes_at_utc = calendar.session_close(label).to_pydatetime().astimezone(UTC)
    opens_at = opens_at_utc.astimezone(MARKET_TIMEZONE)
    closes_at = closes_at_utc.astimezone(MARKET_TIMEZONE)
    if opens_at.timetz().replace(tzinfo=None) != _OPEN:
        raise RuntimeError("XNYS returned an unexpected regular-session open")
    opening_range_ends_at = opens_at + timedelta(minutes=15)
    regular_close = _aware_at(session_date, time(16, 0))
    half_day = closes_at < regular_close
    return MarketSession(
        session_date=session_date,
        opens_at=opens_at,
        opening_range_ends_at=opening_range_ends_at,
        scanner_at=opens_at + _SCAN_OFFSET,
        first_signal_at=opens_at + _FIRST_SIGNAL_OFFSET,
        entry_cutoff_at=_aware_at(session_date, _ENTRY_CUTOFF),
        closes_at=closes_at,
        half_day=half_day,
    )


def _local_date(value: date | datetime) -> date:
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("scheduler timestamps must be timezone-aware")
        return value.astimezone(MARKET_TIMEZONE).date()
    return value


def next_market_session(value: date | datetime) -> MarketSession:
    """Return the first open session strictly after the supplied date/time."""
    candidate = _local_date(value) + timedelta(days=1)
    while (session := market_session(candidate)) is None:
        candidate += timedelta(days=1)
    return session


__all__ = [
    "MARKET_CALENDAR_ID",
    "MARKET_CALENDAR_VERSION",
    "MARKET_TIMEZONE",
    "MarketSession",
    "is_market_holiday",
    "market_session",
    "next_market_session",
]

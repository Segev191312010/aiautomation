"""Timezone-aware US cash-equity session timing without network dependencies."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo


MARKET_TIMEZONE = ZoneInfo("America/New_York")
UTC = timezone.utc
_OPEN = time(9, 30)
_SCAN_OFFSET = timedelta(minutes=15, seconds=5)
_FIRST_SIGNAL_OFFSET = timedelta(minutes=20)
_ENTRY_CUTOFF = time(12, 0)
_REGULAR_CLOSE = time(16, 0)
_HALF_DAY_CLOSE = time(13, 0)


def _observed_fixed_holiday(year: int, month: int, day: int) -> date:
    holiday = date(year, month, day)
    if holiday.weekday() == 5:
        return holiday - timedelta(days=1)
    if holiday.weekday() == 6:
        return holiday + timedelta(days=1)
    return holiday


def _nth_weekday(year: int, month: int, weekday: int, ordinal: int) -> date:
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + 7 * (ordinal - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    if month == 12:
        next_month = date(year + 1, 1, 1)
    else:
        next_month = date(year, month + 1, 1)
    last = next_month - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def _easter_sunday(year: int) -> date:
    """Return Gregorian Easter using the Anonymous Gregorian algorithm."""
    cycle, century = year % 19, year // 100
    year_in_century = year % 100
    leap_correction, solar_correction = divmod(century, 4)
    century_correction = (century + 8) // 25
    century_epact = (century - century_correction + 1) // 3
    epact = (19 * cycle + century - leap_correction - century_epact + 15) % 30
    weekday = (32 + 2 * solar_correction + 2 * (year_in_century // 4) - epact - year_in_century % 4) % 7
    correction = (cycle + 11 * epact + 22 * weekday) // 451
    month = (epact + weekday - 7 * correction + 114) // 31
    day = (epact + weekday - 7 * correction + 114) % 31 + 1
    return date(year, month, day)


@lru_cache(maxsize=32)
def _market_holidays(year: int) -> frozenset[date]:
    holidays = {
        _observed_fixed_holiday(year, 1, 1),
        _nth_weekday(year, 1, 0, 3),
        _nth_weekday(year, 2, 0, 3),
        _easter_sunday(year) - timedelta(days=2),
        _last_weekday(year, 5, 0),
        _observed_fixed_holiday(year, 6, 19),
        _observed_fixed_holiday(year, 7, 4),
        _nth_weekday(year, 9, 0, 1),
        _nth_weekday(year, 11, 3, 4),
        _observed_fixed_holiday(year, 12, 25),
    }
    # Juneteenth became an NYSE holiday in 2022.
    if year < 2022:
        holidays.discard(_observed_fixed_holiday(year, 6, 19))
    return frozenset(holidays)


@lru_cache(maxsize=32)
def _half_days(year: int) -> frozenset[date]:
    thanksgiving = _nth_weekday(year, 11, 3, 4)
    day_after_thanksgiving = thanksgiving + timedelta(days=1)
    christmas_eve = date(year, 12, 24)
    july_third = date(year, 7, 3)
    candidates = {day_after_thanksgiving, christmas_eve, july_third}
    return frozenset(
        day
        for day in candidates
        if day.weekday() < 5 and day not in _market_holidays(year)
    )


def is_market_holiday(session_date: date) -> bool:
    """Return whether a date is a weekend or scheduled full market holiday."""
    return session_date.weekday() >= 5 or session_date in _market_holidays(session_date.year)


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
    half_day = session_date in _half_days(session_date.year)
    opens_at = _aware_at(session_date, _OPEN)
    opening_range_ends_at = opens_at + timedelta(minutes=15)
    return MarketSession(
        session_date=session_date,
        opens_at=opens_at,
        opening_range_ends_at=opening_range_ends_at,
        scanner_at=opens_at + _SCAN_OFFSET,
        first_signal_at=opens_at + _FIRST_SIGNAL_OFFSET,
        entry_cutoff_at=_aware_at(session_date, _ENTRY_CUTOFF),
        closes_at=_aware_at(session_date, _HALF_DAY_CLOSE if half_day else _REGULAR_CLOSE),
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

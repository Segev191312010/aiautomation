"""
Scoring and freshness helpers for diagnostics.
"""
from __future__ import annotations

from datetime import datetime, timezone
import math
from zoneinfo import ZoneInfo


def clamp(value: float, low: float, high: float) -> float:
    return min(high, max(low, value))


def safe_mean_std(values: list[float]) -> tuple[float, float]:
    if not values:
        return (0.0, 0.0)
    mean = sum(values) / len(values)
    if len(values) < 2:
        return (mean, 0.0)
    variance = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
    return (mean, math.sqrt(max(0.0, variance)))


def score_from_z(value: float, mean: float, stddev: float) -> float:
    if stddev <= 1e-9:
        return 50.0
    z = (value - mean) / stddev
    return clamp(50.0 + (z * 25.0), 0.0, 100.0)


def state_from_score(score: float | None) -> str | None:
    if score is None:
        return None
    if score >= 70.0:
        return "GREEN"
    if score >= 40.0:
        return "YELLOW"
    return "RED"


def business_days_between(start_date: datetime, end_date: datetime, tz_name: str = "America/New_York") -> int:
    tz = ZoneInfo(tz_name)
    s = start_date.astimezone(tz).date()
    e = end_date.astimezone(tz).date()
    if e <= s:
        return 0
    days = 0
    cursor = s
    while cursor < e:
        cursor = cursor.fromordinal(cursor.toordinal() + 1)
        if cursor.weekday() < 5:
            days += 1
    return days


def freshness_from_intraday_age(age_s: float | None, warn_s: float | None, critical_s: float | None) -> tuple[str, str | None]:
    if age_s is None:
        return ("stale", "missing_data")
    if warn_s is None or critical_s is None:
        return ("ok", None)
    if age_s <= warn_s:
        return ("ok", None)
    if age_s <= critical_s:
        return ("warn", "stale_intraday")
    return ("stale", "stale_intraday")


def freshness_from_business_lag(
    last_value_dt: datetime | None,
    expected_lag_business_days: int,
    now_dt: datetime | None = None,
    tz_name: str = "America/New_York",
) -> tuple[str, str | None, int | None]:
    now = now_dt or datetime.now(timezone.utc)
    if last_value_dt is None:
        return ("stale", "missing_data", None)
    lag = business_days_between(last_value_dt, now, tz_name=tz_name)
    if lag <= max(0, expected_lag_business_days):
        return ("ok", None, lag)
    if lag <= max(0, expected_lag_business_days) + 1:
        return ("warn", "awaiting_source_publish", lag)
    return ("stale", "missing_data", lag)

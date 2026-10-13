"""File store of real five-minute RTH TRADES bars for strategy research.

One JSON file per contract and session:
``~/.local/share/autotrader-v1/research/bars/<YYYY-MM-DD>/<con_id>.json``.

Directories are 0700 and files 0600, and every write is atomic, so a crash
never leaves a half-written day that a resumed backfill would mistake for a
stored one. Reads fail closed: a file whose identity, schema, or bars do not
validate raises ``BarsStoreError`` instead of feeding a replay.
"""
from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import re
from typing import Any
from zoneinfo import ZoneInfo

from trading.contracts import CompletedBar
from trading.operations.paths import UserPaths, atomic_private_write, ensure_private_directory
from trading.scheduler.sessions import market_session

NEW_YORK = ZoneInfo("America/New_York")
BAR_INTERVAL = timedelta(minutes=5)
SCHEMA = "autotrader-v1/research-bars"
SCHEMA_VERSION = 1
# CompletedBar.volume carries TWS's raw unit; rules convert with their own multiplier.
VOLUME_UNIT = "raw TWS API units (round lots of 100 shares)"
DEFAULT_SOURCE = "ibkr_paper_tws_read_only"
_CON_ID_FILE = re.compile(r"^([1-9][0-9]{0,11})\.json$")
_MAX_FILE_BYTES = 2 * 1024 * 1024


class BarsStoreError(ValueError):
    """A stored research day is missing, unsafe, or fails validation."""


def previous_session(session_date: date) -> date:
    """The XNYS session immediately before ``session_date``."""
    day = session_date - timedelta(days=1)
    # Weekends and holidays never span more than a few days in a row.
    for _ in range(14):
        if market_session(day) is not None:
            return day
        day -= timedelta(days=1)
    raise BarsStoreError("no XNYS session found in the two weeks before the date")


@dataclass(frozen=True, slots=True)
class ResearchPaths:
    """Private research layout under the runtime data directory."""

    root: Path

    @classmethod
    def default(cls) -> "ResearchPaths":
        return cls.for_user(UserPaths.current())

    @classmethod
    def for_user(cls, paths: UserPaths) -> "ResearchPaths":
        return cls(root=paths.data_directory / "research")

    @property
    def bars_directory(self) -> Path:
        return self.root / "bars"

    @property
    def reports_directory(self) -> Path:
        return self.root / "reports"

    def session_directory(self, session_date: date) -> Path:
        return self.bars_directory / session_date.isoformat()

    def bars_file(self, session_date: date, con_id: int) -> Path:
        _require_con_id(con_id)
        return self.session_directory(session_date) / f"{con_id}.json"

    def ensure_private(self, directory: Path) -> Path:
        """Create ``directory`` with every level from ``root`` down private (0700).

        ``mkdir(parents=True)`` ignores the mode for intermediate levels, so
        each level is made private explicitly.
        """
        target = directory.expanduser()
        root = self.root.expanduser()
        if target != root and root not in target.parents:
            raise BarsStoreError("research directories must stay under the research root")
        ensure_private_directory(root)
        current = root
        for part in target.relative_to(root).parts:
            current = current / part
            ensure_private_directory(current)
        return target


def _require_con_id(con_id: int) -> int:
    if isinstance(con_id, bool) or not isinstance(con_id, int) or con_id <= 0:
        raise BarsStoreError("con_id must be a positive integer")
    return con_id


def _decimal(value: Any, field_name: str) -> Decimal:
    if isinstance(value, bool) or isinstance(value, float):
        # Floats would silently round prices; the store holds exact decimal text.
        raise BarsStoreError(f"{field_name} must be decimal text")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise BarsStoreError(f"{field_name} must be a decimal") from exc
    if not result.is_finite():
        raise BarsStoreError(f"{field_name} must be finite")
    return result


def _aware(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise BarsStoreError(f"{field_name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _timestamp(value: Any, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise BarsStoreError(f"{field_name} must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise BarsStoreError(f"{field_name} must be an ISO timestamp") from exc
    return _aware(parsed, field_name)


@dataclass(frozen=True, slots=True)
class StoredBar:
    """One completed five-minute RTH TRADES bar exactly as TWS reported it."""

    interval_start_utc: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    rth: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "interval_start_utc", _aware(self.interval_start_utc, "interval_start_utc")
        )
        for name in ("open", "high", "low", "close", "volume"):
            object.__setattr__(self, name, _decimal(getattr(self, name), name))
        if min(self.open, self.high, self.low, self.close) <= 0:
            raise BarsStoreError("bar prices must be positive")
        if self.volume < 0:
            raise BarsStoreError("bar volume must not be negative")
        if self.high < max(self.open, self.close) or self.low > min(self.open, self.close):
            raise BarsStoreError("bar range must contain its open and close")
        if not isinstance(self.rth, bool):
            raise BarsStoreError("bar rth flag must be a boolean")

    @property
    def interval_end_utc(self) -> datetime:
        return self.interval_start_utc + BAR_INTERVAL

    def completed_bar(self, con_id: int) -> CompletedBar:
        return CompletedBar(
            con_id=con_id,
            interval_start_utc=self.interval_start_utc,
            interval_end_utc=self.interval_end_utc,
            open=self.open,
            high=self.high,
            low=self.low,
            close=self.close,
            volume=self.volume,
            rth=self.rth,
            source="ibkr",
            data_mode="historical",
        )


@dataclass(frozen=True, slots=True)
class StoredDay:
    """One contract's regular session: its bars plus the prior-session context.

    ``prior_close`` and ``avg_dollar_volume`` (20 prior sessions, in dollars)
    are what live qualification and MOMO's relative volume use; either is
    ``None`` when the daily history could not supply it.
    """

    session_date: date
    con_id: int
    symbol: str
    bars: tuple[StoredBar, ...]
    prior_close: Decimal | None = None
    avg_dollar_volume: Decimal | None = None
    primary_exchange: str | None = None
    fetched_at: datetime | None = None
    source: str = DEFAULT_SOURCE

    def __post_init__(self) -> None:
        _require_con_id(self.con_id)
        symbol = self.symbol.strip().upper() if isinstance(self.symbol, str) else ""
        if not symbol or len(symbol) > 16:
            raise BarsStoreError("symbol must be a non-empty ticker")
        object.__setattr__(self, "symbol", symbol)
        if not self.bars:
            raise BarsStoreError("a stored day needs at least one bar")
        bars = tuple(self.bars)
        object.__setattr__(self, "bars", bars)
        previous: StoredBar | None = None
        for bar in bars:
            if not isinstance(bar, StoredBar):
                raise BarsStoreError("bars must be StoredBar values")
            local = bar.interval_start_utc.astimezone(NEW_YORK)
            if local.date() != self.session_date:
                raise BarsStoreError("every bar must belong to the stored session date")
            if local.minute % 5 or local.second or local.microsecond:
                raise BarsStoreError("bars must start on a five-minute boundary")
            if previous is not None and bar.interval_start_utc < previous.interval_end_utc:
                raise BarsStoreError("bars must be ordered without duplicates or overlaps")
            previous = bar
        if self.prior_close is not None:
            prior_close = _decimal(self.prior_close, "prior_close")
            if prior_close <= 0:
                raise BarsStoreError("prior_close must be positive")
            object.__setattr__(self, "prior_close", prior_close)
        if self.avg_dollar_volume is not None:
            average = _decimal(self.avg_dollar_volume, "avg_dollar_volume")
            if average < 0:
                raise BarsStoreError("avg_dollar_volume must not be negative")
            object.__setattr__(self, "avg_dollar_volume", average)
        if self.fetched_at is not None:
            object.__setattr__(self, "fetched_at", _aware(self.fetched_at, "fetched_at"))
        if not isinstance(self.source, str) or not self.source.strip():
            raise BarsStoreError("source must be named")

    def completed_bars(self) -> tuple[CompletedBar, ...]:
        return tuple(bar.completed_bar(self.con_id) for bar in self.bars)


def day_to_payload(day: StoredDay) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "version": SCHEMA_VERSION,
        "session_date": day.session_date.isoformat(),
        "con_id": day.con_id,
        "symbol": day.symbol,
        "primary_exchange": day.primary_exchange,
        "prior_close": None if day.prior_close is None else str(day.prior_close),
        "avg_dollar_volume": (
            None if day.avg_dollar_volume is None else str(day.avg_dollar_volume)
        ),
        "request": {"bar_size": "5 mins", "what_to_show": "TRADES", "use_rth": 1},
        "volume_unit": VOLUME_UNIT,
        "source": day.source,
        "fetched_at": None if day.fetched_at is None else day.fetched_at.isoformat(),
        "bars": [
            {
                "interval_start_utc": bar.interval_start_utc.isoformat(),
                "interval_end_utc": bar.interval_end_utc.isoformat(),
                "open": str(bar.open),
                "high": str(bar.high),
                "low": str(bar.low),
                "close": str(bar.close),
                "volume": str(bar.volume),
                "rth": bar.rth,
            }
            for bar in day.bars
        ],
    }


def _optional_decimal(payload: Mapping[str, Any], key: str) -> Decimal | None:
    value = payload.get(key)
    return None if value is None else _decimal(value, key)


def day_from_payload(
    payload: Any,
    *,
    expected_date: date | None = None,
    expected_con_id: int | None = None,
) -> StoredDay:
    """Validate one stored day; identity must match where it was filed."""
    if not isinstance(payload, Mapping):
        raise BarsStoreError("stored day must be a JSON object")
    if payload.get("schema") != SCHEMA or payload.get("version") != SCHEMA_VERSION:
        raise BarsStoreError("stored day has an unsupported schema")
    try:
        session_date = date.fromisoformat(str(payload.get("session_date")))
    except ValueError as exc:
        raise BarsStoreError("stored day has an invalid session date") from exc
    con_id = payload.get("con_id")
    if isinstance(con_id, bool) or not isinstance(con_id, int):
        raise BarsStoreError("stored day has an invalid con_id")
    if expected_date is not None and session_date != expected_date:
        raise BarsStoreError("stored day was filed under another session date")
    if expected_con_id is not None and con_id != expected_con_id:
        raise BarsStoreError("stored day was filed under another con_id")
    raw_bars = payload.get("bars")
    if not isinstance(raw_bars, list):
        raise BarsStoreError("stored day bars must be a list")
    bars: list[StoredBar] = []
    for raw in raw_bars:
        if not isinstance(raw, Mapping):
            raise BarsStoreError("stored bar must be a JSON object")
        start = _timestamp(raw.get("interval_start_utc"), "interval_start_utc")
        end = _timestamp(raw.get("interval_end_utc"), "interval_end_utc")
        if end - start != BAR_INTERVAL:
            raise BarsStoreError("stored bars must cover exactly five minutes")
        bars.append(
            StoredBar(
                interval_start_utc=start,
                open=_decimal(raw.get("open"), "open"),
                high=_decimal(raw.get("high"), "high"),
                low=_decimal(raw.get("low"), "low"),
                close=_decimal(raw.get("close"), "close"),
                volume=_decimal(raw.get("volume"), "volume"),
                rth=raw.get("rth"),
            )
        )
    fetched_raw = payload.get("fetched_at")
    primary = payload.get("primary_exchange")
    if primary is not None and not isinstance(primary, str):
        raise BarsStoreError("primary_exchange must be text")
    symbol = payload.get("symbol")
    if not isinstance(symbol, str):
        raise BarsStoreError("stored day has no symbol")
    return StoredDay(
        session_date=session_date,
        con_id=con_id,
        symbol=symbol,
        bars=tuple(bars),
        prior_close=_optional_decimal(payload, "prior_close"),
        avg_dollar_volume=_optional_decimal(payload, "avg_dollar_volume"),
        primary_exchange=primary,
        fetched_at=None if fetched_raw is None else _timestamp(fetched_raw, "fetched_at"),
        source=str(payload.get("source") or ""),
    )


class BarsStore:
    """Read, write, and list stored research days. Construction touches nothing."""

    def __init__(self, paths: ResearchPaths | None = None) -> None:
        self._paths = paths or ResearchPaths.default()

    @property
    def paths(self) -> ResearchPaths:
        return self._paths

    def path_for(self, session_date: date, con_id: int) -> Path:
        return self._paths.bars_file(session_date, con_id)

    def has_day(self, session_date: date, con_id: int) -> bool:
        path = self.path_for(session_date, con_id)
        return path.is_file() and not path.is_symlink()

    def write_day(self, day: StoredDay, *, overwrite: bool = False) -> Path:
        """Persist one day atomically; an existing day is kept unless ``overwrite``."""
        path = self.path_for(day.session_date, day.con_id)
        if path.exists() and not overwrite:
            raise BarsStoreError(
                f"{day.session_date.isoformat()}/{day.con_id} is already stored"
            )
        self._paths.ensure_private(path.parent)
        encoded = json.dumps(day_to_payload(day), separators=(",", ":"), sort_keys=True)
        return atomic_private_write(path, encoded.encode("utf-8"))

    def read_day(self, session_date: date, con_id: int) -> StoredDay:
        path = self.path_for(session_date, con_id)
        if path.is_symlink() or not path.is_file():
            raise BarsStoreError(f"{session_date.isoformat()}/{con_id} is not stored")
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise BarsStoreError(f"{session_date.isoformat()}/{con_id} is unreadable") from exc
        if len(raw) > _MAX_FILE_BYTES:
            raise BarsStoreError(f"{session_date.isoformat()}/{con_id} exceeds the size limit")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise BarsStoreError(f"{session_date.isoformat()}/{con_id} is not valid JSON") from exc
        return day_from_payload(payload, expected_date=session_date, expected_con_id=con_id)

    def get_day(self, session_date: date, con_id: int) -> StoredDay | None:
        """The stored day, or ``None`` when nothing is filed for it."""
        if not self.has_day(session_date, con_id):
            return None
        return self.read_day(session_date, con_id)

    def _session_dates(self) -> list[date]:
        directory = self._paths.bars_directory
        if directory.is_symlink() or not directory.is_dir():
            return []
        sessions: list[date] = []
        for entry in directory.iterdir():
            if entry.is_symlink() or not entry.is_dir():
                continue
            try:
                session_date = date.fromisoformat(entry.name)
            except ValueError:
                continue
            if entry.name == session_date.isoformat():
                sessions.append(session_date)
        return sorted(sessions)

    def list_sessions(self) -> tuple[date, ...]:
        """Session dates with at least one stored contract, oldest first."""
        return tuple(day for day in self._session_dates() if self.list_con_ids(day))

    def list_con_ids(self, session_date: date) -> tuple[int, ...]:
        directory = self._paths.session_directory(session_date)
        if directory.is_symlink() or not directory.is_dir():
            return ()
        con_ids: list[int] = []
        for entry in directory.iterdir():
            match = _CON_ID_FILE.match(entry.name)
            if match is not None and entry.is_file() and not entry.is_symlink():
                con_ids.append(int(match.group(1)))
        return tuple(sorted(con_ids))

    def stored_sessions(self, con_id: int) -> frozenset[date]:
        _require_con_id(con_id)
        return frozenset(
            session_date
            for session_date in self._session_dates()
            if self.has_day(session_date, con_id)
        )

    def iter_days(self, session_date: date) -> Iterator[StoredDay]:
        for con_id in self.list_con_ids(session_date):
            yield self.read_day(session_date, con_id)


__all__ = [
    "BAR_INTERVAL",
    "DEFAULT_SOURCE",
    "SCHEMA",
    "SCHEMA_VERSION",
    "VOLUME_UNIT",
    "BarsStore",
    "BarsStoreError",
    "ResearchPaths",
    "StoredBar",
    "StoredDay",
    "day_from_payload",
    "day_to_payload",
    "previous_session",
]

"""Read-only IBKR backfill of five-minute RTH bars into the research store.

One intraday request per contract covers every missing session at once
(``"<n> D"``, five-minute RTH TRADES bars), then one daily request supplies
each session's prior close and 20-session average dollar volume exactly as
live qualification computes them. Requests are paced, a pacing violation
backs off and retries, and a competing login or lost connection stops the run
cleanly: every day already written stays written, and the next run skips it.
Only sessions that have closed are stored, so a stored day is never partial.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from functools import partial
import logging
from pathlib import Path
import re
import sqlite3
import sys
import time
from typing import Protocol, TypeVar
from urllib.parse import quote

from trading.market.ibkr_boundary import (
    BarBatch,
    DailyBarBatch,
    IBKRTradeBar,
    MarketDataQualityError,
    liquidity_profile,
)
from trading.market.tws_actor import (
    TWSActorConfig,
    TWSActorError,
    TWSCompetingSessionError,
    TWSConnectionError,
    TWSPaperActor,
    TWSRequestError,
    _PendingBars,
    is_competing_session_report,
)
from trading.market.universe import RequestPacer
from trading.operations.paths import PathSecurityError
from trading.operations.shadow_service import IBKR_API_VOLUME_MULTIPLIER
from trading.research.bars_store import (
    BarsStore,
    BarsStoreError,
    StoredBar,
    StoredDay,
    previous_session,
)
from trading.scheduler.sessions import MARKET_TIMEZONE, market_session

logger = logging.getLogger("autotrader.shadow.research")

# A dedicated read-only data client: TWS accepts one connection per client ID,
# and 17-21 are taken by the shadow sessions, screener, bridge and assistant.
RESEARCH_CLIENT_ID = 28
DEFAULT_PACE_SECONDS = 2.0
# Five-minute RTH bars: 60 sessions is ~4,700 bars, inside one IBKR response.
MAX_BACKFILL_DAYS = 60
# A multi-week request takes longer than the live two-session request.
HISTORY_REQUEST_TIMEOUT_SECONDS = 60.0
PACING_BACKOFF_SECONDS = 15.0
MAX_REQUEST_ATTEMPTS = 4
# A run of failures across contracts means TWS, not the contracts, is broken.
MAX_CONSECUTIVE_FAILURES = 10
INTRADAY_BAR_SIZE = "5 mins"
_DURATION = re.compile(r"^([1-9][0-9]{0,2}) D$")
_ADV_QUANTUM = Decimal("0.01")

STOP_COMPETING_SESSION = "competing_session"
STOP_DISCONNECTED = "disconnected"
STOP_PACING = "pacing"
STOP_REPEATED_FAILURES = "repeated_failures"
STOP_STORE_UNWRITABLE = "store_unwritable"

Clock = Callable[[], datetime]
Sleeper = Callable[[float], Awaitable[None]]
_Batch = TypeVar("_Batch", BarBatch, DailyBarBatch)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def validate_duration(duration: str) -> int:
    """Return the session count of an ``"<n> D"`` duration, 1..MAX_BACKFILL_DAYS."""
    match = _DURATION.fullmatch(duration) if isinstance(duration, str) else None
    if match is None or not 1 <= int(match.group(1)) <= MAX_BACKFILL_DAYS:
        raise TWSRequestError(f"duration must be '<n> D' with 1 <= n <= {MAX_BACKFILL_DAYS}")
    return int(match.group(1))


class ResearchTWSActor(TWSPaperActor):
    """The PAPER data actor plus intraday history of a caller-chosen duration.

    ``completed_trade_bars`` is pinned to the two sessions the live rules
    need. ``intraday_trade_bars`` sends the identical request (same gate,
    request IDs, callbacks and timeout handling) with only the duration
    changed; a test pins the two requests to each other.
    """

    async def intraday_trade_bars(self, con_id: int, *, duration: str) -> BarBatch:
        if con_id <= 0:
            raise TWSRequestError("con_id must be positive")
        validate_duration(duration)
        self._require_connected()
        async with self._request_gate:
            request_id = await self._allocate_request_id()
            pending = _PendingBars(asyncio.get_running_loop().create_future(), con_id)
            self._pending_bars[request_id] = pending
            try:
                contract = self._contract_factory(con_id)
                method = getattr(self._client, "reqHistoricalData")
                method(
                    request_id,
                    contract,
                    "",
                    duration,
                    INTRADAY_BAR_SIZE,
                    "TRADES",
                    1,
                    2,
                    False,
                    [],
                )
                return await asyncio.wait_for(
                    pending.future, timeout=self.config.request_timeout_seconds
                )
            except asyncio.TimeoutError as exc:
                self._cancel_historical(request_id)
                raise TWSRequestError("TWS historical-data request timed out") from exc
            finally:
                self._pending_bars.pop(request_id, None)


class HistorySource(Protocol):
    async def intraday_trade_bars(self, con_id: int, *, duration: str) -> BarBatch: ...

    async def daily_trade_bars(self, con_id: int) -> DailyBarBatch: ...


@dataclass(frozen=True, slots=True)
class Instrument:
    con_id: int
    symbol: str
    primary_exchange: str | None = None

    def __post_init__(self) -> None:
        if isinstance(self.con_id, bool) or not isinstance(self.con_id, int) or self.con_id <= 0:
            raise ValueError("con_id must be a positive integer")
        if not isinstance(self.symbol, str) or not self.symbol.strip():
            raise ValueError("symbol must be non-empty")


@dataclass(frozen=True, slots=True)
class BackfillReport:
    sessions: tuple[date, ...]
    instruments: int
    already_stored: int = 0
    requested: int = 0
    days_written: int = 0
    symbols_written: int = 0
    # (symbol, session) pairs IBKR returned no bars for, e.g. before a listing.
    missing_days: tuple[tuple[str, str], ...] = ()
    failures: tuple[tuple[str, str], ...] = ()
    stopped: str | None = None
    remaining: int = 0

    @property
    def completed(self) -> bool:
        return self.stopped is None


class _Stop(Exception):
    """End the run: the condition says nothing about the contract itself."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason


class _InstrumentFailure(Exception):
    """This contract's request failed; the run continues with the next one."""


def completed_sessions(now: datetime, days: int) -> tuple[date, ...]:
    """The newest ``days`` XNYS sessions that have closed by ``now``, oldest first."""
    if days < 1:
        raise ValueError("days must be positive")
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    now_utc = now.astimezone(timezone.utc)
    day = now_utc.astimezone(MARKET_TIMEZONE).date()
    sessions: list[date] = []
    # Holidays and weekends never span more than a few days in a row.
    for _ in range(days * 2 + 14):
        session = market_session(day)
        if session is not None and session.closes_at <= now_utc:
            sessions.append(day)
            if len(sessions) == days:
                break
        day -= timedelta(days=1)
    return tuple(reversed(sessions))


def _session_in_progress(now: datetime) -> bool:
    session = market_session(now.astimezone(MARKET_TIMEZONE).date())
    return session is not None and session.opens_at <= now < session.closes_at


def request_days(sessions: Sequence[date], missing: Sequence[date], now: datetime) -> int:
    """Sessions one request must span to reach the oldest missing one.

    IBKR counts today's session in a duration once it has opened, so an
    in-progress session (never stored) adds one.
    """
    oldest = min(missing)
    count = sum(1 for session in sessions if session >= oldest)
    return count + (1 if _session_in_progress(now) else 0)


def split_sessions(bars: Iterable[IBKRTradeBar]) -> dict[date, list[IBKRTradeBar]]:
    by_session: dict[date, list[IBKRTradeBar]] = {}
    for bar in bars:
        session_date = bar.interval_start_utc.astimezone(MARKET_TIMEZONE).date()
        by_session.setdefault(session_date, []).append(bar)
    for session_bars in by_session.values():
        session_bars.sort(key=lambda bar: bar.interval_start_utc)
    return by_session


def stored_bars(raw: Sequence[IBKRTradeBar], *, con_id: int) -> tuple[StoredBar, ...]:
    """Only completed live TRADES bars of the requested contract are stored."""
    bars: list[StoredBar] = []
    for bar in raw:
        if bar.con_id != con_id:
            raise BarsStoreError("history returned bars of another contract")
        if bar.bar_type != "TRADES" or bar.delayed or bar.frozen or not bar.complete:
            raise BarsStoreError("only completed real-time TRADES bars are stored")
        if bar.interval_end_utc - bar.interval_start_utc != timedelta(minutes=5):
            raise BarsStoreError("only five-minute bars are stored")
        bars.append(
            StoredBar(
                interval_start_utc=bar.interval_start_utc,
                open=bar.open,
                high=bar.high,
                low=bar.low,
                close=bar.close,
                volume=bar.volume,
                rth=bar.rth,
            )
        )
    return tuple(bars)


def daily_context(
    batch: DailyBarBatch, session_date: date
) -> tuple[Decimal | None, Decimal | None]:
    """``(prior_close, avg_dollar_volume)`` for one session, as live qualification sees them.

    Both come from ``liquidity_profile`` (the prior 20 completed sessions,
    volume converted from TWS lots to shares). With too little history the
    average is unknown, and the prior close is kept only when it is the
    immediately preceding session's.
    """
    try:
        profile = liquidity_profile(
            batch, session_date=session_date, volume_multiplier=IBKR_API_VOLUME_MULTIPLIER
        )
    except MarketDataQualityError:
        prior = sorted(
            (bar for bar in batch.bars if bar.session_date < session_date),
            key=lambda bar: bar.session_date,
        )
        if prior and prior[-1].session_date == previous_session(session_date):
            close = prior[-1].close
            return (close if close.is_finite() and close > 0 else None), None
        return None, None
    average = profile.average_dollar_volume.quantize(_ADV_QUANTUM, rounding=ROUND_HALF_UP)
    return profile.reference_price, average


def is_pacing_violation(batch: BarBatch | DailyBarBatch) -> bool:
    """IBKR reuses code 162 for every HMDS error; only its pacing text is pacing."""
    return batch.pacing_failed and "pacing" in (batch.error or "").lower()


async def _fetch(
    request: Callable[[], Awaitable[_Batch]],
    *,
    pacer: RequestPacer,
    max_attempts: int,
    backoff_seconds: float,
) -> _Batch:
    for attempt in range(1, max_attempts + 1):
        await pacer.wait()
        try:
            batch = await request()
        except TWSCompetingSessionError as exc:
            raise _Stop(STOP_COMPETING_SESSION, str(exc)) from exc
        except TWSConnectionError as exc:
            raise _Stop(STOP_DISCONNECTED, str(exc)) from exc
        except TWSRequestError as exc:
            # Timeouts are transient; a contract that keeps timing out is skipped.
            if attempt == max_attempts:
                raise _InstrumentFailure(str(exc)) from exc
            pacer.defer(backoff_seconds * attempt)
            continue
        if is_competing_session_report(batch.error):
            raise _Stop(STOP_COMPETING_SESSION, str(batch.error))
        if not batch.connected:
            raise _Stop(STOP_DISCONNECTED, str(batch.error or "TWS connection lost"))
        if is_pacing_violation(batch):
            if attempt == max_attempts:
                raise _Stop(STOP_PACING, str(batch.error))
            delay = backoff_seconds * 2 ** (attempt - 1)
            logger.warning("IBKR pacing violation; backing off %.0f s", delay)
            pacer.defer(delay)
            continue
        if batch.error or batch.pacing_failed:
            raise _InstrumentFailure(str(batch.error or "IBKR history request failed"))
        return batch
    raise _InstrumentFailure("IBKR history request failed")  # pragma: no cover - loop returns


def _instruments(
    con_ids_with_symbols: Mapping[int, str] | Iterable[tuple[int, str] | Instrument],
    primary_exchanges: Mapping[int, str] | None,
) -> list[Instrument]:
    items: Iterable[tuple[int, str] | Instrument]
    if isinstance(con_ids_with_symbols, Mapping):
        items = con_ids_with_symbols.items()
    else:
        items = con_ids_with_symbols
    exchanges = primary_exchanges or {}
    unique: dict[int, Instrument] = {}
    for item in items:
        instrument = (
            item
            if isinstance(item, Instrument)
            else Instrument(con_id=item[0], symbol=item[1], primary_exchange=exchanges.get(item[0]))
        )
        unique.setdefault(instrument.con_id, instrument)
    return list(unique.values())


async def backfill(
    actor: HistorySource,
    *,
    con_ids_with_symbols: Mapping[int, str] | Iterable[tuple[int, str] | Instrument],
    days: int,
    pace_seconds: float,
    store: BarsStore | None = None,
    primary_exchanges: Mapping[int, str] | None = None,
    clock: Clock = _utc_now,
    sleep: Sleeper = asyncio.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    max_attempts: int = MAX_REQUEST_ATTEMPTS,
    pacing_backoff_seconds: float = PACING_BACKOFF_SECONDS,
    max_consecutive_failures: int = MAX_CONSECUTIVE_FAILURES,
) -> BackfillReport:
    """Store the newest ``days`` closed sessions for each contract; resumable.

    Sessions already stored are skipped (a fully stored contract sends no
    request), request starts are at least ``pace_seconds`` apart, and the run
    stops early, with everything written so far kept, on a competing login,
    a lost connection, persistent pacing, or repeated contract failures.
    """
    if not 1 <= days <= MAX_BACKFILL_DAYS:
        raise ValueError(f"days must be between 1 and {MAX_BACKFILL_DAYS}")
    if pace_seconds < 0 or max_attempts < 1 or max_consecutive_failures < 1:
        raise ValueError("pacing and retry limits must be positive")
    store = store or BarsStore()
    instruments = _instruments(con_ids_with_symbols, primary_exchanges)
    sessions = completed_sessions(clock(), days)
    pacer = RequestPacer(pace_seconds, clock=monotonic, sleep=sleep)
    already_stored = requested = days_written = symbols_written = 0
    missing_days: list[tuple[str, str]] = []
    failures: list[tuple[str, str]] = []
    consecutive_failures = 0
    stopped: str | None = None
    remaining = 0

    for index, instrument in enumerate(instruments):
        missing = [day for day in sessions if not store.has_day(day, instrument.con_id)]
        if not missing:
            already_stored += 1
            continue
        requested += 1
        now = clock()
        duration = f"{request_days(sessions, missing, now)} D"
        written = 0
        try:
            intraday = await _fetch(
                partial(actor.intraday_trade_bars, instrument.con_id, duration=duration),
                pacer=pacer,
                max_attempts=max_attempts,
                backoff_seconds=pacing_backoff_seconds,
            )
            # The in-progress session is never in ``missing``; forming bars never count.
            by_session = split_sessions(
                bar for bar in intraday.bars if bar.interval_end_utc <= clock()
            )
            writable = {day: by_session[day] for day in missing if day in by_session}
            missing_days.extend(
                (instrument.symbol, day.isoformat()) for day in missing if day not in by_session
            )
            if writable:
                daily = await _fetch(
                    partial(actor.daily_trade_bars, instrument.con_id),
                    pacer=pacer,
                    max_attempts=max_attempts,
                    backoff_seconds=pacing_backoff_seconds,
                )
                if any(bar.con_id != instrument.con_id for bar in daily.bars):
                    raise _InstrumentFailure("daily history returned another contract")
                fetched_at = clock()
                for day, raw_bars in sorted(writable.items()):
                    prior_close, average = daily_context(daily, day)
                    try:
                        store.write_day(
                            StoredDay(
                                session_date=day,
                                con_id=instrument.con_id,
                                symbol=instrument.symbol,
                                bars=stored_bars(raw_bars, con_id=instrument.con_id),
                                prior_close=prior_close,
                                avg_dollar_volume=average,
                                primary_exchange=instrument.primary_exchange,
                                fetched_at=fetched_at,
                            )
                        )
                    except BarsStoreError as exc:
                        failures.append((instrument.symbol, f"{day.isoformat()}: {exc}"))
                        continue
                    except (OSError, PathSecurityError) as exc:
                        # A full disk or an unsafe path fails every later write too.
                        raise _Stop(STOP_STORE_UNWRITABLE, str(exc)) from exc
                    written += 1
        except _Stop as stop:
            stopped = stop.reason
            remaining = len(instruments) - index
            # Days this contract wrote before the stop stay stored and counted.
            days_written += written
            symbols_written += 1 if written else 0
            failures.append((instrument.symbol, str(stop)))
            logger.error("backfill stopped at %s: %s", instrument.symbol, stop)
            break
        except _InstrumentFailure as exc:
            failures.append((instrument.symbol, str(exc)))
            consecutive_failures += 1
            logger.warning("backfill of %s failed: %s", instrument.symbol, exc)
            if consecutive_failures >= max_consecutive_failures:
                stopped = STOP_REPEATED_FAILURES
                remaining = len(instruments) - index - 1
                logger.error("backfill stopped after %d failures in a row", consecutive_failures)
                break
            continue
        consecutive_failures = 0
        days_written += written
        symbols_written += 1 if written else 0
        if requested % 25 == 0:
            logger.info(
                "backfill %d/%d contracts, %d days written",
                index + 1,
                len(instruments),
                days_written,
            )
    return BackfillReport(
        sessions=sessions,
        instruments=len(instruments),
        already_stored=already_stored,
        requested=requested,
        days_written=days_written,
        symbols_written=symbols_written,
        missing_days=tuple(missing_days),
        failures=tuple(failures),
        stopped=stopped,
        remaining=remaining,
    )


async def record_today(
    actor: HistorySource,
    *,
    con_ids_with_symbols: Mapping[int, str] | Iterable[tuple[int, str] | Instrument],
    pace_seconds: float = DEFAULT_PACE_SECONDS,
    **options: object,
) -> BackfillReport:
    """The nightly recording: the newest closed session only."""
    return await backfill(
        actor,
        con_ids_with_symbols=con_ids_with_symbols,
        days=1,
        pace_seconds=pace_seconds,
        **options,  # type: ignore[arg-type]
    )


class UniverseUnavailableError(RuntimeError):
    """No qualified universe could be read from the runtime database."""


def load_qualified_universe(db_path: str | Path) -> tuple[date, tuple[Instrument, ...]]:
    """The newest session's qualified contracts, read through a read-only handle."""
    path = Path(db_path).expanduser()
    if not path.is_file():
        raise UniverseUnavailableError(f"runtime database not found at {path}")
    uri = f"file:{quote(str(path))}?mode=ro"
    try:
        with closing(sqlite3.connect(uri, uri=True)) as db:
            row = db.execute(
                "SELECT MAX(session_date) FROM autotrader_universe_daily WHERE qualified = 1"
            ).fetchone()
            if row is None or row[0] is None:
                raise UniverseUnavailableError("no qualified universe has been recorded")
            session_date = date.fromisoformat(str(row[0]))
            rows = db.execute(
                """SELECT d.con_id, d.symbol,
                          (SELECT u.primary_exchange FROM autotrader_universe u
                            WHERE u.con_id = d.con_id AND u.primary_exchange IS NOT NULL
                            ORDER BY u.source LIMIT 1)
                     FROM autotrader_universe_daily d
                    WHERE d.session_date = ? AND d.qualified = 1
                    ORDER BY d.symbol, d.con_id""",
                (session_date.isoformat(),),
            ).fetchall()
    except sqlite3.Error as exc:
        raise UniverseUnavailableError(f"qualified universe unreadable: {exc}") from exc
    instruments = tuple(
        Instrument(con_id=int(con_id), symbol=str(symbol), primary_exchange=exchange)
        for con_id, symbol, exchange in rows
    )
    return session_date, instruments


def format_backfill_report(report: BackfillReport, *, universe_date: date | None = None) -> str:
    sessions = report.sessions
    span = f"{sessions[0].isoformat()}..{sessions[-1].isoformat()}" if sessions else "none"
    lines = [
        f"Research backfill: {len(sessions)} sessions {span}",
        f"  universe: {report.instruments} contracts"
        + (f" (qualified {universe_date.isoformat()})" if universe_date else ""),
        f"  already stored: {report.already_stored}  requested: {report.requested}",
        f"  written: {report.days_written} days for {report.symbols_written} contracts",
        f"  sessions IBKR had no bars for: {len(report.missing_days)}",
        f"  failures: {len(report.failures)}",
    ]
    lines.extend(f"    {symbol}: {reason}" for symbol, reason in report.failures[:10])
    if report.stopped:
        lines.append(
            f"  STOPPED early ({report.stopped}); {report.remaining} contracts not attempted."
            " Re-run to resume: stored days are skipped."
        )
    else:
        lines.append("  completed")
    return "\n".join(lines)


async def run_backfill_cli(days: int) -> int:
    """``autotraderctl research backfill --days N``: read-only, client id 28."""
    from config import cfg
    from trading.operations.settings import ShadowSettingsError, load_shadow_runtime_config

    if not 1 <= days <= MAX_BACKFILL_DAYS:
        print(f"error: --days must be between 1 and {MAX_BACKFILL_DAYS}", file=sys.stderr)
        return 2
    try:
        config = load_shadow_runtime_config()
        universe_date, instruments = load_qualified_universe(cfg.DB_PATH)
    except (ShadowSettingsError, UniverseUnavailableError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    actor = ResearchTWSActor(
        TWSActorConfig(
            account=config.paper_account_id,
            client_id=RESEARCH_CLIENT_ID,
            request_timeout_seconds=HISTORY_REQUEST_TIMEOUT_SECONDS,
        )
    )
    # Two paced requests per contract that still needs a session.
    minutes = len(instruments) * 2 * DEFAULT_PACE_SECONDS / 60
    print(
        f"research backfill: {len(instruments)} contracts qualified"
        f" {universe_date.isoformat()}, {days} sessions each;"
        f" at most ~{minutes:.0f} min at {DEFAULT_PACE_SECONDS:.0f} s spacing"
        " (stored sessions are skipped)",
        flush=True,
    )
    try:
        await actor.connect()
        if not actor.managed_accounts or any(
            not account.startswith("DU") for account in actor.managed_accounts
        ):
            print("error: refusing a TWS login with a non-paper account", file=sys.stderr)
            return 1
        report = await backfill(
            actor,
            con_ids_with_symbols=instruments,
            days=days,
            pace_seconds=DEFAULT_PACE_SECONDS,
        )
    except TWSActorError as exc:
        print(f"error: TWS PAPER data unavailable: {exc}", file=sys.stderr)
        return 1
    finally:
        await actor.disconnect()
    print(format_backfill_report(report, universe_date=universe_date))
    return 0 if report.completed else 1


__all__ = [
    "DEFAULT_PACE_SECONDS",
    "HISTORY_REQUEST_TIMEOUT_SECONDS",
    "MAX_BACKFILL_DAYS",
    "RESEARCH_CLIENT_ID",
    "BackfillReport",
    "HistorySource",
    "Instrument",
    "ResearchTWSActor",
    "UniverseUnavailableError",
    "backfill",
    "completed_sessions",
    "daily_context",
    "format_backfill_report",
    "is_pacing_violation",
    "load_qualified_universe",
    "record_today",
    "request_days",
    "run_backfill_cli",
    "split_sessions",
    "stored_bars",
    "validate_duration",
]

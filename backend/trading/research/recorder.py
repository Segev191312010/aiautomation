"""Read-only IBKR backfill of five-minute RTH bars into the research store.

One intraday request per contract covers every missing session at once
(``"<n> D"``, five-minute RTH TRADES bars), then one daily request supplies
each session's prior close and 20-session average dollar volume exactly as
live qualification computes them. Requests are paced, a pacing violation
backs off and retries, and a competing login or lost connection stops the run
cleanly: every day already written stays written, and the next run skips it.
Only sessions that have closed are stored, so a stored day is never partial.

The nightly run (``research record``, 16:20-17:30 ET):

1. first records extended-hours bars (04:00 ET through the close) of the
   closed session's gap candidates (watchlist and scanner gappers, at most
   ``MAX_GAP_CANDIDATES``) for GAP_GO's replay: IBKR's all-hours request
   covers only the current day, so this is the one step a later night can
   never refill;
2. records the RTH bars of every qualified contract of that session: the
   watchlist first, then the day universe by scan rank, then any other
   qualified scan name, capped at ``RECORDER_MAX_CONTRACTS`` and stopped at
   ``regular_hours_deadline`` (5 minutes before the window's end) with a
   "STOPPED early" line and its counts; the next night's lookback takes the
   rest;
3. only while ``EVENING_QUALIFICATION_ENABLED`` (off: review R6), qualifies
   the next session's watchlist; otherwise the screener does it at 08:00
   exactly as deployed.

A manual ``research backfill`` runs the same steps (without a deadline
outside the window); each is idempotent.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime, time as dt_time, timedelta, timezone
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
    is_competing_session_report,
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
)
from trading.market.universe import (
    EVENING_QUALIFICATION_ENABLED,
    DailyHistorySource,
    QualificationReport,
    RequestPacer,
    qualify_universe,
)
from trading.operations.paths import PathSecurityError
from trading.operations.shadow_service import IBKR_API_VOLUME_MULTIPLIER
from trading.research.bars_store import (
    EXTENDED_HOURS_START,
    BarsStore,
    BarsStoreError,
    StoredBar,
    StoredDay,
    previous_session,
)
from trading.scheduler.sessions import MARKET_TIMEZONE, market_session, next_market_session

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
# A planned stop: the regular-hours pass reached its deadline before the
# window's end. The missing days are taken by the next night's lookback.
STOP_DEADLINE = "deadline"
# Stops after which no further TWS step of the night is attempted.
CONNECTION_STOPS = frozenset({STOP_COMPETING_SESSION, STOP_DISCONNECTED})

# --- the nightly budget -------------------------------------------------------------
# The recorder's supervised window, 16:20-17:30 ET (supervisor.RECORDER_WINDOW;
# a test pins the two).
RECORDER_WINDOW = (dt_time(16, 20), dt_time(17, 30))
RECORDER_WINDOW_SECONDS = 70 * 60
# The regular-hours pass starts no contract later than this before the
# window's end, so the supervisor's SIGTERM never cuts a night short.
RECORDER_MARGIN_SECONDS = 5 * 60
# Contracts recorded per night, at most: the watchlist (~400 qualified of
# ~500), the day universe (<= 150) and any other qualified scan name, in that
# priority. A first night for a contract fetches all 21 sessions in the same
# two requests, only with a longer reply.
RECORDER_MAX_CONTRACTS = 600
MAX_GAP_CANDIDATES = 60
# Watchlist size the evening qualification is planned for (the owner's ~503).
WATCHLIST_PLANNING_SIZE = 520
QUALIFY_PACE_SECONDS = 1.0
# Worst-typical wall time per item: the pacing plus a slow reply.
RECORD_SECONDS_PER_CONTRACT = 2 * DEFAULT_PACE_SECONDS + 1.0
# All-hours bars plus, since this step runs first, the daily context request.
EXTENDED_SECONDS_PER_CONTRACT = 2 * DEFAULT_PACE_SECONDS + 1.0
QUALIFY_SECONDS_PER_CONTRACT = QUALIFY_PACE_SECONDS + 0.2
# Gap candidates: GAP_GO's own minimum gap, read from the sweeps of the first
# hour after the open (the official open makes gap_pct final by then).
GAP_CANDIDATE_MIN_PCT = Decimal("3")
GAP_CANDIDATE_WINDOW = timedelta(minutes=60)


def nightly_budget_seconds(contracts: int, gap_candidates: int, watchlist: int) -> float:
    """Worst-typical seconds for one night: extended bars, RTH recording, qualification.

    ``watchlist`` is 0 while evening qualification is off (the default).
    """
    return (
        gap_candidates * EXTENDED_SECONDS_PER_CONTRACT
        + contracts * RECORD_SECONDS_PER_CONTRACT
        + watchlist * QUALIFY_SECONDS_PER_CONTRACT
    )


def regular_hours_deadline(started: datetime) -> datetime | None:
    """When the regular-hours pass starts its last contract, or None.

    Inside the supervised window the pass stops ``RECORDER_MARGIN_SECONDS``
    before the window's end, less the evening qualification's budget when
    that step is on. A run started outside the window (an owner's manual
    backfill) has no deadline.
    """
    if started.tzinfo is None or started.utcoffset() is None:
        raise ValueError("started must be timezone-aware")
    local = started.astimezone(MARKET_TIMEZONE)
    opens, closes = RECORDER_WINDOW
    if not opens <= local.time() < closes:
        return None
    end = datetime.combine(local.date(), closes, tzinfo=MARKET_TIMEZONE)
    reserve = timedelta(seconds=RECORDER_MARGIN_SECONDS)
    if EVENING_QUALIFICATION_ENABLED:
        reserve += timedelta(seconds=WATCHLIST_PLANNING_SIZE * QUALIFY_SECONDS_PER_CONTRACT)
    return (end - reserve).astimezone(timezone.utc)

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


class ExtendedHistorySource(Protocol):
    async def extended_trade_bars(self, con_id: int) -> BarBatch: ...

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
    deadline: datetime | None = None,
) -> BackfillReport:
    """Store the newest ``days`` closed sessions for each contract; resumable.

    Sessions already stored are skipped (a fully stored contract sends no
    request), request starts are at least ``pace_seconds`` apart, and the run
    stops early, with everything written so far kept, on a competing login,
    a lost connection, persistent pacing, or repeated contract failures. With
    ``deadline`` no contract starts at or after it (``STOP_DEADLINE``).
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
        if deadline is not None and clock() >= deadline:
            stopped = STOP_DEADLINE
            remaining = len(instruments) - index
            logger.warning(
                "backfill STOPPED early at its %s ET deadline: %d days written for %d contracts,"
                " %d contracts not attempted (the next night's lookback takes them)",
                deadline.astimezone(MARKET_TIMEZONE).strftime("%H:%M"),
                days_written,
                symbols_written,
                remaining,
            )
            break
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


def _read_only_database(db_path: str | Path) -> sqlite3.Connection:
    path = Path(db_path).expanduser()
    if not path.is_file():
        raise UniverseUnavailableError(f"runtime database not found at {path}")
    return sqlite3.connect(f"file:{quote(str(path))}?mode=ro", uri=True)


def _has_table(db: sqlite3.Connection, table: str) -> bool:
    return (
        db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
        is not None
    )


# Tier 0: the owner's watchlist; 1: the day universe by its best scan rank;
# 2: any other qualified contract (the hot-35's scan additions). The day
# universe keeps its qualifications out of autotrader_universe_daily, so its
# names are read from its own table too.
_UNIVERSE_SQL = """
    SELECT con_id, symbol, exchange, tier, day_rank FROM (
    SELECT d.con_id AS con_id, d.symbol AS symbol,
           COALESCE((SELECT u.primary_exchange FROM autotrader_universe u
                      WHERE u.con_id = d.con_id AND u.primary_exchange IS NOT NULL
                      ORDER BY u.source LIMIT 1), {day_exchange}) AS exchange,
           CASE WHEN EXISTS (SELECT 1 FROM autotrader_universe u WHERE u.con_id = d.con_id) THEN 0
                WHEN {day_member} THEN 1 ELSE 2 END AS tier,
           {day_rank} AS day_rank
      FROM autotrader_universe_daily d
      {day_join}
     WHERE d.session_date = ? AND d.qualified = 1
    {day_only}
    ) ORDER BY tier, day_rank IS NULL, day_rank, symbol, con_id
"""
_DAY_ONLY_SQL = """
    UNION ALL
    SELECT du.con_id, du.symbol, du.primary_exchange, 1, du.best_rank
      FROM autotrader_manager_day_universe du
     WHERE du.session_date = ? AND du.qualified = 1
       AND NOT EXISTS (SELECT 1 FROM autotrader_universe_daily d
                        WHERE d.session_date = du.session_date AND d.con_id = du.con_id
                          AND d.qualified = 1)
"""


def load_qualified_universe(
    db_path: str | Path,
    *,
    now: datetime | None = None,
    max_contracts: int | None = None,
) -> tuple[date, tuple[Instrument, ...]]:
    """The newest qualified session's contracts, read through a read-only handle.

    With ``now`` the session is the newest one that has closed by then, so a
    next session already qualified in the evening is never mistaken for the
    one to record. Contracts come in priority order (watchlist, day universe
    by scan rank, other scan names) and at most ``max_contracts`` of them.
    """
    newest_closed = completed_sessions(now, 1)[-1] if now is not None else None
    try:
        with closing(_read_only_database(db_path)) as db:
            query = "SELECT MAX(session_date) FROM autotrader_universe_daily WHERE qualified = 1"
            params: tuple[object, ...] = ()
            if newest_closed is not None:
                query += " AND session_date <= ?"
                params = (newest_closed.isoformat(),)
            row = db.execute(query, params).fetchone()
            if row is None or row[0] is None:
                raise UniverseUnavailableError("no qualified universe has been recorded")
            session_date = date.fromisoformat(str(row[0]))
            if _has_table(db, "autotrader_manager_day_universe"):
                sql = _UNIVERSE_SQL.format(
                    day_exchange="du.primary_exchange",
                    day_member="du.con_id IS NOT NULL",
                    day_rank="du.best_rank",
                    day_join=(
                        "LEFT JOIN autotrader_manager_day_universe du"
                        " ON du.session_date = d.session_date AND du.con_id = d.con_id"
                        " AND du.qualified = 1"
                    ),
                    day_only=_DAY_ONLY_SQL,
                )
                universe_params: tuple[str, ...] = (session_date.isoformat(),) * 2
            else:
                sql = _UNIVERSE_SQL.format(
                    day_exchange="NULL", day_member="0", day_rank="NULL", day_join="", day_only=""
                )
                universe_params = (session_date.isoformat(),)
            rows = db.execute(sql, universe_params).fetchall()
    except sqlite3.Error as exc:
        raise UniverseUnavailableError(f"qualified universe unreadable: {exc}") from exc
    instruments = tuple(
        Instrument(con_id=int(con_id), symbol=str(symbol), primary_exchange=exchange)
        for con_id, symbol, exchange, _tier, _rank in rows
    )
    if max_contracts is not None and len(instruments) > max_contracts:
        logger.warning(
            "recording %d of %d qualified contracts (the nightly cap); the rest are scan names",
            max_contracts,
            len(instruments),
        )
        instruments = instruments[:max_contracts]
    return session_date, instruments


def _utc_text(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="microseconds")


# Rows and day-universe rows of the sweeps in the first hour after the open.
_GAP_ROWS_SQL = """
    SELECT CAST(json_extract(r.value, '$.con_id') AS INTEGER),
           json_extract(r.value, '$.symbol'),
           json_extract(r.value, '$.primary_exchange'),
           CAST(json_extract(r.value, '$.gap_pct') AS REAL)
      FROM autotrader_screener_snapshots s, json_each(s.payload_json, '{path}') r
     WHERE s.session_date = ? AND s.observed_at >= ? AND s.observed_at <= ?
       AND json_extract(r.value, '$.gap_pct') IS NOT NULL
"""


def load_gap_candidates(
    db_path: str | Path,
    session_date: date,
    *,
    min_gap_pct: Decimal = GAP_CANDIDATE_MIN_PCT,
    limit: int = MAX_GAP_CANDIDATES,
) -> tuple[Instrument, ...]:
    """The session's gappers: watchlist rows, scan rows and day-universe rows.

    Every contract whose official gap (``gap_pct``) reached ``min_gap_pct`` in
    a sweep of the first hour after the open, largest gap first, at most
    ``limit``. Read through a read-only handle; no sweep means no candidates.
    """
    session = market_session(session_date)
    if session is None:
        return ()
    bounds = (
        session_date.isoformat(),
        _utc_text(session.opens_at),
        _utc_text(session.opens_at + GAP_CANDIDATE_WINDOW),
    )
    best: dict[int, tuple[float, str, str | None]] = {}
    try:
        with closing(_read_only_database(db_path)) as db:
            for path in ("$.rows", "$.day_universe.rows"):
                for con_id, symbol, exchange, gap in db.execute(
                    _GAP_ROWS_SQL.format(path=path), bounds
                ).fetchall():
                    if con_id is None or int(con_id) <= 0 or not symbol or gap is None:
                        continue
                    current = best.get(int(con_id))
                    if current is None or float(gap) > current[0]:
                        best[int(con_id)] = (float(gap), str(symbol), exchange)
    except sqlite3.Error as exc:
        raise UniverseUnavailableError(f"screener snapshots unreadable: {exc}") from exc
    ordered = sorted(
        (
            (gap, symbol, con_id, exchange)
            for con_id, (gap, symbol, exchange) in best.items()
            if Decimal(str(gap)) >= min_gap_pct
        ),
        key=lambda item: (-item[0], item[1], item[2]),
    )
    return tuple(
        Instrument(con_id=con_id, symbol=symbol, primary_exchange=exchange or None)
        for _gap, symbol, con_id, exchange in ordered[:limit]
    )


# --- extended-hours bars for GAP_GO's replay ---------------------------------------------


@dataclass(frozen=True, slots=True)
class ExtendedReport:
    session_date: date
    candidates: int
    already_stored: int = 0
    written: int = 0
    failures: tuple[tuple[str, str], ...] = ()
    skipped: str | None = None
    stopped: str | None = None


def extended_session_bars(
    raw: Sequence[IBKRTradeBar], *, session_date: date, now: datetime
) -> list[IBKRTradeBar]:
    """The session day's completed bars from 04:00 ET through the close."""
    session = market_session(session_date)
    if session is None:
        return []
    closes_at = session.closes_at.astimezone(timezone.utc)
    kept: list[IBKRTradeBar] = []
    for bar in raw:
        local = bar.interval_start_utc.astimezone(MARKET_TIMEZONE)
        if local.date() != session_date or local.time() < EXTENDED_HOURS_START:
            continue
        if bar.interval_end_utc > closes_at or bar.interval_end_utc > now:
            continue
        kept.append(bar)
    return sorted(kept, key=lambda bar: bar.interval_start_utc)


async def record_extended(
    actor: ExtendedHistorySource,
    *,
    session_date: date,
    instruments: Sequence[Instrument],
    pace_seconds: float = DEFAULT_PACE_SECONDS,
    store: BarsStore | None = None,
    clock: Clock = _utc_now,
    sleep: Sleeper = asyncio.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    max_attempts: int = MAX_REQUEST_ATTEMPTS,
    pacing_backoff_seconds: float = PACING_BACKOFF_SECONDS,
) -> ExtendedReport:
    """Store each gap candidate's pre-market and regular session of ``session_date``.

    IBKR's all-hours request (``extended_trade_bars``, "1 D") returns only
    the current day, so the step runs on the evening of the session itself
    (first of the night: it can never be refilled later) and is skipped
    (named) otherwise. The prior close and average dollar volume come from
    the same session's regular-hours day when stored, else from one daily
    request, exactly as the regular-hours recording computes them.
    """
    store = store or BarsStore()
    now = clock()
    session = market_session(session_date)
    if session is None or now < session.closes_at:
        return ExtendedReport(session_date, len(instruments), skipped="session_not_closed")
    if now.astimezone(MARKET_TIMEZONE).date() != session_date:
        return ExtendedReport(session_date, len(instruments), skipped="not_the_session_day")
    pacer = RequestPacer(pace_seconds, clock=monotonic, sleep=sleep)
    already = written = 0
    failures: list[tuple[str, str]] = []
    stopped: str | None = None
    for instrument in instruments:
        if store.has_day(session_date, instrument.con_id, extended=True):
            already += 1
            continue
        try:
            batch = await _fetch(
                partial(actor.extended_trade_bars, instrument.con_id),
                pacer=pacer,
                max_attempts=max_attempts,
                backoff_seconds=pacing_backoff_seconds,
            )
            bars = extended_session_bars(batch.bars, session_date=session_date, now=clock())
            if not bars:
                failures.append((instrument.symbol, "no extended-hours bars"))
                continue
            regular = store.get_day(session_date, instrument.con_id)
            if regular is not None:
                prior_close, average = regular.prior_close, regular.avg_dollar_volume
            else:
                daily = await _fetch(
                    partial(actor.daily_trade_bars, instrument.con_id),
                    pacer=pacer,
                    max_attempts=max_attempts,
                    backoff_seconds=pacing_backoff_seconds,
                )
                if any(bar.con_id != instrument.con_id for bar in daily.bars):
                    raise _InstrumentFailure("daily history returned another contract")
                prior_close, average = daily_context(daily, session_date)
            store.write_day(
                StoredDay(
                    session_date=session_date,
                    con_id=instrument.con_id,
                    symbol=instrument.symbol,
                    bars=stored_bars(bars, con_id=instrument.con_id),
                    prior_close=prior_close,
                    avg_dollar_volume=average,
                    primary_exchange=instrument.primary_exchange
                    or (None if regular is None else regular.primary_exchange),
                    fetched_at=clock(),
                    use_rth=False,
                )
            )
            written += 1
        except _Stop as stop:
            stopped = stop.reason
            failures.append((instrument.symbol, str(stop)))
            logger.error("extended-hours recording stopped at %s: %s", instrument.symbol, stop)
            break
        except _InstrumentFailure as exc:
            failures.append((instrument.symbol, str(exc)))
        except BarsStoreError as exc:
            failures.append((instrument.symbol, str(exc)))
        except (OSError, PathSecurityError) as exc:
            stopped = STOP_STORE_UNWRITABLE
            failures.append((instrument.symbol, str(exc)))
            break
    return ExtendedReport(
        session_date=session_date,
        candidates=len(instruments),
        already_stored=already,
        written=written,
        failures=tuple(failures),
        stopped=stopped,
    )


# --- the next session's watchlist qualification ----------------------------------------------


def next_session_after_close(now: datetime) -> date:
    """The session after the newest one that has closed by ``now``."""
    return next_market_session(completed_sessions(now, 1)[-1]).session_date


async def qualify_next_session(
    history: DailyHistorySource,
    *,
    now: datetime,
    pace_seconds: float = QUALIFY_PACE_SECONDS,
    clock: Clock = _utc_now,
    pacer: RequestPacer | None = None,
) -> QualificationReport:
    """Qualify the next session's watchlist tonight, exactly as 08:00 would.

    Resumable and idempotent (final rows are skipped); the session calendar
    picks the date, so a Friday evening qualifies Monday (or the day after a
    holiday). A split effective at that open is caught by the first sweep's
    prior-close re-check.
    """
    return await qualify_universe(
        history,
        session_date=next_session_after_close(now),
        volume_multiplier=IBKR_API_VOLUME_MULTIPLIER,
        clock=clock,
        pacer=pacer or RequestPacer(pace_seconds),
    )


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


def format_extended_report(report: ExtendedReport) -> str:
    head = f"Extended-hours bars {report.session_date.isoformat()}: {report.candidates} gap candidates"
    if report.skipped:
        return f"{head}; skipped ({report.skipped})"
    lines = [
        head,
        f"  already stored: {report.already_stored}  written: {report.written}"
        f"  failures: {len(report.failures)}",
    ]
    lines.extend(f"    {symbol}: {reason}" for symbol, reason in report.failures[:10])
    lines.append(f"  STOPPED early ({report.stopped})" if report.stopped else "  completed")
    return "\n".join(lines)


def format_qualification_report(report: QualificationReport) -> str:
    return (
        f"Next session {report.session_date.isoformat()} qualified tonight:"
        f" {report.qualified} of {report.instruments} instruments"
        f" ({report.requested} requested, {len(report.errors)} transient errors,"
        " retried at 08:00)"
    )


async def run_backfill_cli(days: int, *, clock: Clock = _utc_now) -> int:
    """``autotraderctl research backfill --days N`` / ``record``: read-only TWS, client id 28.

    The gap candidates' extended-hours bars come first (they can never be
    refilled), then the regular-hours pass up to ``regular_hours_deadline``,
    then, only while ``EVENING_QUALIFICATION_ENABLED``, the next session's
    watchlist qualification. Nothing runs after a competing login or a lost
    connection. A pass stopped at its deadline is a planned stop (exit 0).
    """
    from config import cfg
    from trading.operations.settings import ShadowSettingsError, load_shadow_runtime_config

    if not 1 <= days <= MAX_BACKFILL_DAYS:
        print(f"error: --days must be between 1 and {MAX_BACKFILL_DAYS}", file=sys.stderr)
        return 2
    started = clock()
    try:
        config = load_shadow_runtime_config()
        universe_date, instruments = load_qualified_universe(
            cfg.DB_PATH, now=started, max_contracts=RECORDER_MAX_CONTRACTS
        )
        closed = completed_sessions(started, 1)[-1]
        gappers = load_gap_candidates(cfg.DB_PATH, closed)
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
    qualify_tonight = EVENING_QUALIFICATION_ENABLED
    deadline = regular_hours_deadline(started)
    minutes = (
        nightly_budget_seconds(
            len(instruments), len(gappers), WATCHLIST_PLANNING_SIZE if qualify_tonight else 0
        )
        / 60
    )
    print(
        f"research backfill: {len(gappers)} gap candidates of {closed.isoformat()} first, then"
        f" {len(instruments)} contracts qualified {universe_date.isoformat()}, {days} sessions"
        " each"
        + (
            f" until {deadline.astimezone(MARKET_TIMEZONE).strftime('%H:%M')} ET"
            if deadline is not None
            else ""
        )
        + ("; then the next session's qualification" if qualify_tonight else "")
        + f"; at most ~{minutes:.0f} min (stored sessions are skipped)",
        flush=True,
    )
    extended: ExtendedReport | None = None
    report: BackfillReport | None = None
    qualification: QualificationReport | None = None
    try:
        await actor.connect()
        if not actor.managed_accounts or any(
            not account.startswith("DU") for account in actor.managed_accounts
        ):
            print("error: refusing a TWS login with a non-paper account", file=sys.stderr)
            return 1
        extended = await record_extended(
            actor,
            session_date=closed,
            instruments=gappers,
            pace_seconds=DEFAULT_PACE_SECONDS,
            clock=clock,
        )
        if extended.stopped not in CONNECTION_STOPS:
            report = await backfill(
                actor,
                con_ids_with_symbols=instruments,
                days=days,
                pace_seconds=DEFAULT_PACE_SECONDS,
                clock=clock,
                deadline=deadline,
            )
        if qualify_tonight and report is not None and report.stopped not in CONNECTION_STOPS:
            try:
                qualification = await qualify_next_session(
                    actor, now=clock(), pace_seconds=QUALIFY_PACE_SECONDS, clock=clock
                )
            except TWSActorError as exc:
                print(f"next-session qualification stopped: {exc}", file=sys.stderr)
    except TWSActorError as exc:
        print(f"error: TWS PAPER data unavailable: {exc}", file=sys.stderr)
        return 1
    finally:
        await actor.disconnect()
    if extended is None:  # pragma: no cover - every path above sets it or returns
        return 1
    print(format_extended_report(extended))
    if report is not None:
        print(format_backfill_report(report, universe_date=universe_date))
    if qualification is not None:
        print(format_qualification_report(qualification))
    elif qualify_tonight:
        print("Next session not qualified tonight: the screener qualifies it at 08:00.")
    recorded = report is not None and (report.completed or report.stopped == STOP_DEADLINE)
    qualified = qualification is not None or not qualify_tonight
    return 0 if extended.stopped is None and recorded and qualified else 1


__all__ = [
    "DEFAULT_PACE_SECONDS",
    "GAP_CANDIDATE_MIN_PCT",
    "HISTORY_REQUEST_TIMEOUT_SECONDS",
    "MAX_BACKFILL_DAYS",
    "MAX_GAP_CANDIDATES",
    "RECORDER_MAX_CONTRACTS",
    "RECORDER_WINDOW",
    "RECORDER_WINDOW_SECONDS",
    "RESEARCH_CLIENT_ID",
    "STOP_DEADLINE",
    "BackfillReport",
    "ExtendedHistorySource",
    "ExtendedReport",
    "HistorySource",
    "Instrument",
    "ResearchTWSActor",
    "UniverseUnavailableError",
    "backfill",
    "completed_sessions",
    "daily_context",
    "extended_session_bars",
    "format_backfill_report",
    "format_extended_report",
    "format_qualification_report",
    "is_pacing_violation",
    "load_gap_candidates",
    "load_qualified_universe",
    "next_session_after_close",
    "nightly_budget_seconds",
    "qualify_next_session",
    "record_extended",
    "record_today",
    "regular_hours_deadline",
    "request_days",
    "run_backfill_cli",
    "split_sessions",
    "stored_bars",
    "validate_duration",
]

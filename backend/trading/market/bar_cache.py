"""Shared five-minute bar cache: one IBKR history request per bar and contract.

From the all-session simulations on, five strategy children poll the same
contracts every bar, each a few seconds behind the last (``STRATEGY_POLL_OFFSETS``
in ``trading.operations.shadow_service``), and send TWS identical historical
requests. IBKR refuses an identical request made within 15 s of another and
throttles the rest. The cache keeps, per ``(con_id, bar_size, bar_end)``, the
raw batch the first child fetched for the bar ending at ``bar_end``; the
children behind it read that batch instead of asking TWS again.

What the cache does not touch: validation. ``CachedBarsActor`` stands in for
the connection actor's ``completed_trade_bars`` only, so ``PaperIBKRMarketData``
still drops the forming bar, validates every bar and applies the caller's
own freshness limit (``PollSchedule.bar_max_age``) against the caller's own
clock. A cached bar too old for a reader's limit is refused exactly as a
freshly fetched one would be.

Fail closed, towards the broker: a batch is cached only when it is healthy
and holds the bar its key names; a cache failure of any kind (the table is
an unregistered draft, see ``MANAGER_PHASE3_DRAFT_DDL``) is a direct fetch,
announced once per run by ``BAR_CACHE_UNAVAILABLE``. ``BAR_CACHE_ENABLED``
is False and every registry entry opts out until the cache has its own
review. Nothing here submits orders.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import json
import logging
from zoneinfo import ZoneInfo

import aiosqlite

from trading.contracts import QuoteSnapshot
from trading.market.ibkr_boundary import (
    BarBatch,
    DailyBarBatch,
    IBKRConnectionActor,
    IBKRContractDetails,
    IBKRScannerResult,
    IBKRTradeBar,
    ScannerRequest,
)

logger = logging.getLogger("autotrader.shadow.bar_cache")

# Build only (2026-10-14): nothing reads through the cache until this is
# True and a registry entry opts in (shadow_service.STRATEGY_USES_BAR_CACHE).
BAR_CACHE_ENABLED = False
# Logged once per run when the cache cannot be read or written; the run
# fetches every bar directly from then on.
BAR_CACHE_UNAVAILABLE = "bar_cache_unavailable"
BAR_CACHE_TABLE = "autotrader_bar_cache"
BAR_SIZE_5M = "5m"
BAR_INTERVAL = timedelta(minutes=5)
# A claim older than IBKR's identical-request window (shadow_service's
# MIN_POLL_SEPARATION) protects nothing: the claimant's request, if it was
# ever sent, is no longer "within 15 s", so a waiter fetches for itself.
CLAIM_STALE_AFTER = timedelta(seconds=15)
# How often a waiter re-reads a claim it is waiting on.
CLAIM_POLL_SECONDS = 0.2
STATE_FETCHING = "fetching"
STATE_READY = "ready"
_NEW_YORK = ZoneInfo("America/New_York")

Fetch = Callable[[], Awaitable[BarBatch]]
Clock = Callable[[], datetime]
Sleeper = Callable[[float], Awaitable[None]]


def _aware_utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _timestamp(value: datetime) -> str:
    return _aware_utc(value, "timestamp").isoformat()


def completed_bar_end(now: datetime) -> datetime:
    """The end of the newest five-minute interval closed at ``now`` (UTC).

    A poll at ``bar close + offset`` asks for the batch whose newest completed
    bar ends at that close; this is the cache key's ``bar_end``.
    """
    observed = _aware_utc(now, "now")
    return observed.replace(minute=observed.minute - observed.minute % 5, second=0, microsecond=0)


def bar_to_payload(bar: IBKRTradeBar) -> dict[str, object]:
    """The raw bar as JSON data; decimals as text, so nothing is rounded."""
    return {
        "con_id": bar.con_id,
        "interval_start_utc": _timestamp(bar.interval_start_utc),
        "interval_end_utc": _timestamp(bar.interval_end_utc),
        "open": str(bar.open),
        "high": str(bar.high),
        "low": str(bar.low),
        "close": str(bar.close),
        "volume": str(bar.volume),
        "rth": bool(bar.rth),
        "bar_type": bar.bar_type,
        "delayed": bool(bar.delayed),
        "frozen": bool(bar.frozen),
        "complete": bool(bar.complete),
        "received_at": _timestamp(bar.received_at),
    }


def bar_from_payload(payload: dict[str, object]) -> IBKRTradeBar:
    return IBKRTradeBar(
        con_id=int(payload["con_id"]),  # type: ignore[arg-type]
        interval_start_utc=datetime.fromisoformat(str(payload["interval_start_utc"])),
        interval_end_utc=datetime.fromisoformat(str(payload["interval_end_utc"])),
        open=Decimal(str(payload["open"])),
        high=Decimal(str(payload["high"])),
        low=Decimal(str(payload["low"])),
        close=Decimal(str(payload["close"])),
        volume=Decimal(str(payload["volume"])),
        rth=bool(payload["rth"]),
        bar_type=str(payload["bar_type"]),  # type: ignore[arg-type]
        delayed=bool(payload["delayed"]),
        frozen=bool(payload["frozen"]),
        complete=bool(payload["complete"]),
        received_at=datetime.fromisoformat(str(payload["received_at"])),
    )


def cacheable_bars(
    batch: BarBatch, *, con_id: int, bar_end: datetime
) -> tuple[IBKRTradeBar, ...] | None:
    """The bars of ``batch`` worth caching under ``bar_end``, or None.

    Only a healthy batch (connected, no pacing or request error, no competing
    login) that holds the bar ending at ``bar_end`` is evidence for that key,
    and only its complete, live TRADES bars for ``con_id`` ending at or before
    ``bar_end`` are kept: the forming bar is never evidence and would differ
    between fetches. A batch IBKR has not finalized yet (no bar at ``bar_end``)
    is returned to its caller but not cached, so the next child's request can
    still populate the key.
    """
    if not batch.connected or batch.pacing_failed or batch.error or batch.competing_session:
        return None
    end = _aware_utc(bar_end, "bar_end")
    kept: list[IBKRTradeBar] = []
    for bar in batch.bars:
        if _aware_utc(bar.interval_end_utc, "interval_end_utc") > end:
            continue
        if bar.con_id != con_id or bar.bar_type != "TRADES":
            return None
        if bar.delayed or bar.frozen or not bar.complete:
            return None
        kept.append(bar)
    if not any(_aware_utc(bar.interval_end_utc, "interval_end_utc") == end for bar in kept):
        return None
    return tuple(kept)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class _Key:
    __slots__ = ("con_id", "bar_size", "session_date", "bar_end")

    def __init__(self, con_id: int, bar_size: str, session_date: date, bar_end: datetime) -> None:
        if con_id <= 0:
            raise ValueError("con_id must be positive")
        if bar_size != BAR_SIZE_5M:
            raise ValueError(f"only {BAR_SIZE_5M} bars are cached")
        end = _aware_utc(bar_end, "bar_end")
        if end != completed_bar_end(end):
            raise ValueError("bar_end must be a five-minute boundary")
        if end.astimezone(_NEW_YORK).date() != session_date:
            raise ValueError("bar_end does not fall on session_date")
        self.con_id = con_id
        self.bar_size = bar_size
        self.session_date = session_date
        self.bar_end = end

    @property
    def row_key(self) -> tuple[int, str, str]:
        return (self.con_id, self.bar_size, self.bar_end.isoformat())


class BarCache:
    """One child's handle on the shared cache table (``MANAGER_PHASE3_DRAFT_DDL``).

    ``writer`` names the child in the rows it claims. ``db_path`` is for tests
    (a scratch database); a run uses the configured database. After the first
    failed read or write the handle is unavailable for the rest of the run:
    every later ``get_or_fetch`` is a direct fetch and the failure was logged
    once as ``BAR_CACHE_UNAVAILABLE``.
    """

    def __init__(
        self,
        *,
        writer: str,
        clock: Clock = _utc_now,
        sleep: Sleeper = asyncio.sleep,
        db_path: str | None = None,
    ) -> None:
        if not writer.strip() or len(writer) > 64:
            raise ValueError("writer must name the child (1-64 characters)")
        self._writer = writer
        self._clock = clock
        self._sleep = sleep
        self._db_path = db_path
        self._available = True
        self._pruned_before: date | None = None

    @property
    def writer(self) -> str:
        return self._writer

    @property
    def available(self) -> bool:
        return self._available

    async def get_or_fetch(
        self,
        con_id: int,
        bar_size: str,
        session_date: date,
        bar_end: datetime,
        fetch: Fetch,
    ) -> BarBatch:
        """The batch for ``(con_id, bar_size, bar_end)``: cached, or fetched once.

        A hit is the batch the first child fetched (its forming bar dropped).
        A miss claims the key, fetches, caches a cacheable batch and returns
        the fetched batch exactly as ``fetch`` produced it. A child that finds
        another's claim waits for its bars rather than repeating the request;
        a claim that outlives IBKR's 15 s window is taken over.
        """
        key = _Key(con_id, bar_size, session_date, bar_end)
        if not self._available:
            return await fetch()
        try:
            await self._prune(session_date)
            claimed, row = await self._claim(key)
            if not claimed:
                bars = await self._await_claim(key, row)
                if bars is not None:
                    return BarBatch(bars=bars)
                # The claim went stale or was released: ours now, unless
                # another child got there first (then its bars, or a direct
                # fetch rather than a second wait).
                taken, row = await self._take_over(key, row)
                if not taken:
                    if row is not None and row[0] == STATE_READY:
                        return BarBatch(bars=_bars_of(row))
                    return await fetch()
        except Exception as exc:  # noqa: BLE001 - the cache is optional, the fetch is not
            self._unavailable("read", exc)
            return await fetch()
        try:
            batch = await fetch()
        except BaseException:
            await self._release_quietly(key)
            raise
        kept = cacheable_bars(batch, con_id=con_id, bar_end=key.bar_end)
        try:
            if kept is None:
                await self._release(key)
            else:
                await self._fill(key, kept)
        except Exception as exc:  # noqa: BLE001 - the batch is still the caller's
            self._unavailable("write", exc)
        return batch

    def _unavailable(self, operation: str, exc: BaseException) -> None:
        if self._available:
            logger.warning(
                "%s: %s failed (%s: %s); bars are fetched directly for the rest of the run",
                BAR_CACHE_UNAVAILABLE,
                operation,
                type(exc).__name__,
                exc,
            )
        self._available = False

    @asynccontextmanager
    async def _connection(self) -> AsyncIterator[aiosqlite.Connection]:
        if self._db_path is None:
            from db.core import get_db

            async with get_db() as db:
                yield db
            return
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute("PRAGMA busy_timeout=10000")
            yield db

    @asynccontextmanager
    async def _transaction(self) -> AsyncIterator[aiosqlite.Connection]:
        async with self._connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                yield db
                await db.commit()
            except BaseException:
                await db.rollback()
                raise

    async def _prune(self, session_date: date) -> None:
        """Rows of earlier sessions go the first time a run touches the cache."""
        if self._pruned_before == session_date:
            return
        async with self._transaction() as db:
            await db.execute(
                f"DELETE FROM {BAR_CACHE_TABLE} WHERE session_date < ?",
                (session_date.isoformat(),),
            )
        self._pruned_before = session_date

    async def _claim(self, key: _Key) -> tuple[bool, tuple | None]:
        """Insert our claim; or read the row that already holds the key."""
        async with self._transaction() as db:
            cursor = await db.execute(
                f"""INSERT OR IGNORE INTO {BAR_CACHE_TABLE}
                    (con_id, bar_size, bar_end_utc, session_date, state, writer, claimed_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (*key.row_key, key.session_date.isoformat(), STATE_FETCHING, self._writer,
                 _timestamp(self._clock())),
            )
            if cursor.rowcount == 1:
                return True, None
            return False, await self._read_on(db, key)

    async def _read_on(self, db: aiosqlite.Connection, key: _Key) -> tuple | None:
        async with db.execute(
            f"""SELECT state, writer, claimed_at, bars_json FROM {BAR_CACHE_TABLE}
                WHERE con_id = ? AND bar_size = ? AND bar_end_utc = ?""",
            key.row_key,
        ) as cursor:
            return await cursor.fetchone()

    async def _read(self, key: _Key) -> tuple | None:
        async with self._connection() as db:
            return await self._read_on(db, key)

    async def _await_claim(self, key: _Key, row: tuple | None) -> tuple[IBKRTradeBar, ...] | None:
        """Wait on another child's claim; its bars, or None once the claim is stale."""
        while True:
            if row is None:
                # The claimant released a batch it could not cache; claim anew.
                return None
            if row[0] == STATE_READY:
                return _bars_of(row)
            claimed_at = datetime.fromisoformat(str(row[2]))
            if self._clock() - claimed_at >= CLAIM_STALE_AFTER:
                return None
            await self._sleep(CLAIM_POLL_SECONDS)
            row = await self._read(key)

    async def _take_over(self, key: _Key, row: tuple | None) -> tuple[bool, tuple | None]:
        """Own a released or stale claim; else report the row as it is now."""
        async with self._transaction() as db:
            if row is None:
                cursor = await db.execute(
                    f"""INSERT OR IGNORE INTO {BAR_CACHE_TABLE}
                        (con_id, bar_size, bar_end_utc, session_date, state, writer, claimed_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (*key.row_key, key.session_date.isoformat(), STATE_FETCHING, self._writer,
                     _timestamp(self._clock())),
                )
            else:
                cursor = await db.execute(
                    f"""UPDATE {BAR_CACHE_TABLE} SET writer = ?, claimed_at = ?
                        WHERE con_id = ? AND bar_size = ? AND bar_end_utc = ?
                          AND state = ? AND claimed_at = ?""",
                    (self._writer, _timestamp(self._clock()), *key.row_key, STATE_FETCHING,
                     str(row[2])),
                )
            if cursor.rowcount == 1:
                return True, None
            return False, await self._read_on(db, key)

    async def _fill(self, key: _Key, bars: Sequence[IBKRTradeBar]) -> None:
        async with self._transaction() as db:
            await db.execute(
                f"""UPDATE {BAR_CACHE_TABLE}
                    SET state = ?, fetched_at = ?, bars_json = ?, bar_count = ?
                    WHERE con_id = ? AND bar_size = ? AND bar_end_utc = ? AND state = ?""",
                (
                    STATE_READY,
                    _timestamp(self._clock()),
                    json.dumps([bar_to_payload(bar) for bar in bars], separators=(",", ":")),
                    len(bars),
                    *key.row_key,
                    STATE_FETCHING,
                ),
            )

    async def _release(self, key: _Key) -> None:
        async with self._transaction() as db:
            await db.execute(
                f"""DELETE FROM {BAR_CACHE_TABLE}
                    WHERE con_id = ? AND bar_size = ? AND bar_end_utc = ?
                      AND state = ? AND writer = ?""",
                (*key.row_key, STATE_FETCHING, self._writer),
            )

    async def _release_quietly(self, key: _Key) -> None:
        try:
            await self._release(key)
        except Exception as exc:  # noqa: BLE001 - the fetch error is the one that matters
            self._unavailable("release", exc)


def _bars_of(row: tuple) -> tuple[IBKRTradeBar, ...]:
    payload = json.loads(str(row[3]))
    if not isinstance(payload, list):
        raise ValueError("cached bars are not a JSON array")
    return tuple(bar_from_payload(item) for item in payload)


class CachedBarsActor:
    """The connection actor with ``completed_trade_bars`` read through the cache.

    Every other request goes straight to the actor. The key's bar is the
    newest five-minute interval closed at the actor's clock when the request
    is made; the facade's own ``now`` and freshness limit then validate the
    result exactly as they would a direct reply.
    """

    def __init__(
        self,
        actor: IBKRConnectionActor,
        *,
        cache: BarCache,
        session_date: date,
        clock: Clock = _utc_now,
    ) -> None:
        self._actor = actor
        self._cache = cache
        self._session_date = session_date
        self._clock = clock

    @property
    def actor(self) -> IBKRConnectionActor:
        return self._actor

    @property
    def cache(self) -> BarCache:
        return self._cache

    async def completed_trade_bars(self, con_id: int) -> BarBatch:
        if con_id <= 0:
            # The actor refuses it as it always has.
            return await self._actor.completed_trade_bars(con_id)
        bar_end = completed_bar_end(self._clock())
        return await self._cache.get_or_fetch(
            con_id,
            BAR_SIZE_5M,
            self._session_date,
            bar_end,
            lambda: self._actor.completed_trade_bars(con_id),
        )

    async def supports_scanner(self, request: ScannerRequest) -> bool:
        return await self._actor.supports_scanner(request)

    async def scan(self, request: ScannerRequest) -> Sequence[IBKRScannerResult]:
        return await self._actor.scan(request)

    async def extended_trade_bars(self, con_id: int) -> BarBatch:
        return await self._actor.extended_trade_bars(con_id)

    async def contract_details(self, con_id: int) -> IBKRContractDetails:
        return await self._actor.contract_details(con_id)

    async def daily_trade_bars(self, con_id: int) -> DailyBarBatch:
        return await self._actor.daily_trade_bars(con_id)

    async def quote_snapshot(self, con_id: int, *, min_tick: Decimal) -> QuoteSnapshot:
        return await self._actor.quote_snapshot(con_id, min_tick=min_tick)


__all__ = [
    "BAR_CACHE_ENABLED",
    "BAR_CACHE_TABLE",
    "BAR_CACHE_UNAVAILABLE",
    "BAR_SIZE_5M",
    "CLAIM_POLL_SECONDS",
    "CLAIM_STALE_AFTER",
    "BarCache",
    "CachedBarsActor",
    "bar_from_payload",
    "bar_to_payload",
    "cacheable_bars",
    "completed_bar_end",
]

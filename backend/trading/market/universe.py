"""Owner watchlist universe: TradingView export to resolved IBKR contracts.

The module also owns each session's price/liquidity qualification of the
resolved contracts. Resolution and qualification fail closed: a symbol is
only tradable data once IBKR returned exactly one matching US stock, and a
contract is only screened once its prior-session history qualifies it.
"""
from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
import logging
import re
import time
from typing import Literal, Protocol

from db.core import get_db, transaction
from trading.market.ibkr_boundary import (
    LIQUIDITY_LOOKBACK_SESSIONS,
    DailyBarBatch,
    IBKRContractDetails,
    MarketDataQualityError,
    liquidity_profile,
)
from trading.market.tws_actor import (
    TWSCompetingSessionError,
    TWSConnectionError,
    TWSRequestError,
    is_competing_session_report,
)

logger = logging.getLogger("autotrader.shadow.screener")

WATCHLIST_SOURCE = "watchlist"
UniverseStatus = Literal["pending", "resolved", "unresolved"]
# TradingView listing prefix -> IBKR primaryExchange hint. CBOE-listed stocks
# trade on Cboe BZX, which IBKR names BATS. Unknown prefixes (OTC, foreign
# venues) are never resolved: a bare US lookup could match another company.
EXCHANGE_HINTS: dict[str, str] = {
    "NASDAQ": "NASDAQ",
    "NYSE": "NYSE",
    "AMEX": "AMEX",
    "NYSEARCA": "ARCA",
    "CBOE": "BATS",
    "BATS": "BATS",
}
_SYMBOL = re.compile(r"^[A-Z0-9]{1,10}(\.[A-Z0-9]{1,4})?$")
_SEPARATORS = re.compile(r"[,\r\n]+")
MAX_WATCHLIST_BYTES = 1024 * 1024

# Daily qualification mirrors the ORB admission band and liquidity floor.
MIN_PRICE = Decimal("10")
MAX_PRICE = Decimal("300")
MIN_AVG_DOLLAR_VOLUME = Decimal("20000000")
# Rows with this reason prefix failed for a transient reason (timeout, pacing,
# lost connection) and are retried by the next run; other outcomes are final
# for the session date.
ERROR_REASON_PREFIX = "error: "
PACING_BACKOFF_SECONDS = 15.0

Clock = Callable[[], datetime]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _decimal_text(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")


def _decimal_or_none(value: object) -> Decimal | None:
    return None if value is None else Decimal(str(value))


class WatchlistFormatError(ValueError):
    """The watchlist export cannot be read as TradingView symbols."""


class RequestPacer:
    """Spaces request starts at least ``min_interval`` seconds apart."""

    def __init__(
        self,
        min_interval: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if min_interval < 0:
            raise ValueError("min_interval must be non-negative")
        self._min_interval = min_interval
        self._clock = clock
        self._sleep = sleep
        self._next_at: float | None = None

    async def wait(self) -> None:
        if self._next_at is not None:
            delay = self._next_at - self._clock()
            if delay > 0:
                await self._sleep(delay)
        self._next_at = self._clock() + self._min_interval

    def defer(self, seconds: float) -> None:
        """Push the next request back, e.g. after IBKR reported pacing."""
        base = max(self._next_at or 0.0, self._clock())
        self._next_at = base + max(0.0, seconds)


# --- watchlist parsing -------------------------------------------------------


@dataclass(frozen=True, slots=True)
class WatchlistEntry:
    """One ``EXCHANGE:SYMBOL`` item exactly as TradingView exported it."""

    listing_exchange: str
    symbol: str

    @property
    def ibkr_symbol(self) -> str:
        return to_ibkr_symbol(self.symbol)

    @property
    def primary_exchange_hint(self) -> str | None:
        return EXCHANGE_HINTS.get(self.listing_exchange)

    @property
    def label(self) -> str:
        return f"{self.listing_exchange}:{self.symbol}" if self.listing_exchange else self.symbol


@dataclass(frozen=True, slots=True)
class ParsedWatchlist:
    entries: tuple[WatchlistEntry, ...]
    duplicates: tuple[str, ...]


def to_ibkr_symbol(symbol: str) -> str:
    """TradingView share classes use a dot (BRK.B); IBKR uses a space (BRK B)."""
    return symbol.strip().upper().replace(".", " ")


def parse_watchlist(text: str) -> ParsedWatchlist:
    """Split on commas and/or newlines; the first occurrence of a symbol wins."""
    entries: list[WatchlistEntry] = []
    duplicates: list[str] = []
    seen: set[str] = set()
    for token in _SEPARATORS.split(text):
        item = token.strip()
        if not item:
            continue
        exchange, _, symbol = item.rpartition(":")
        entry = WatchlistEntry(
            listing_exchange=exchange.strip().upper(), symbol=symbol.strip().upper()
        )
        if not entry.symbol:
            raise WatchlistFormatError(f"watchlist item {item!r} has no symbol")
        if entry.symbol in seen:
            duplicates.append(entry.label)
            continue
        seen.add(entry.symbol)
        entries.append(entry)
    if not entries:
        raise WatchlistFormatError("watchlist export contains no symbols")
    return ParsedWatchlist(entries=tuple(entries), duplicates=tuple(duplicates))


def entry_rejection(entry: WatchlistEntry) -> str | None:
    """Reason an entry must not be looked up at all, or None."""
    if not _SYMBOL.fullmatch(entry.symbol):
        return f"unsupported symbol format {entry.symbol!r}"
    if entry.listing_exchange and entry.listing_exchange not in EXCHANGE_HINTS:
        return f"unsupported listing exchange {entry.listing_exchange!r}"
    return None


# --- resolution ----------------------------------------------------------------


class SymbolResolver(Protocol):
    async def contract_details_for_symbol(
        self, symbol: str, primary_exchange: str | None
    ) -> IBKRContractDetails: ...


@dataclass(frozen=True, slots=True)
class UniverseRow:
    source: str
    symbol: str
    listing_exchange: str
    ibkr_symbol: str
    con_id: int | None
    primary_exchange: str | None
    status: UniverseStatus
    last_error: str | None
    updated_at: datetime

    def __post_init__(self) -> None:
        if self.status == "resolved" and (self.con_id is None or self.con_id <= 0):
            raise ValueError("a resolved universe row needs a positive conId")
        if self.status != "resolved" and self.con_id is not None:
            raise ValueError("only resolved universe rows carry a conId")


@dataclass(frozen=True, slots=True)
class Resolution:
    row: UniverseRow
    used_fallback: bool = False


def details_rejection(details: IBKRContractDetails, ibkr_symbol: str) -> str | None:
    """IBKR's answer must be the US stock that was asked for."""
    if details.con_id <= 0:
        return "IBKR returned an invalid conId"
    if details.currency.strip().upper() != "USD":
        return f"IBKR returned a {details.currency or 'non-USD'} contract"
    if details.security_type.strip().upper() != "STK":
        return f"IBKR returned security type {details.security_type!r}"
    if details.symbol.strip().upper() != ibkr_symbol:
        return f"IBKR returned symbol {details.symbol!r}"
    return None


async def resolve_entry(
    resolver: SymbolResolver,
    entry: WatchlistEntry,
    *,
    now: datetime,
    source: str = WATCHLIST_SOURCE,
    pacer: RequestPacer | None = None,
) -> Resolution:
    """Hinted lookup first, then one retry without the exchange hint."""

    def row(
        status: UniverseStatus,
        *,
        con_id: int | None = None,
        primary_exchange: str | None = None,
        error: str | None = None,
    ) -> UniverseRow:
        return UniverseRow(
            source=source,
            symbol=entry.symbol,
            listing_exchange=entry.listing_exchange,
            ibkr_symbol=entry.ibkr_symbol,
            con_id=con_id,
            primary_exchange=primary_exchange,
            status=status,
            last_error=error,
            updated_at=now,
        )

    rejection = entry_rejection(entry)
    if rejection is not None:
        return Resolution(row("unresolved", error=rejection))
    hint = entry.primary_exchange_hint
    attempts: tuple[str | None, ...] = (hint, None) if hint else (None,)
    failures: list[str] = []
    for attempt in attempts:
        if pacer is not None:
            await pacer.wait()
        label = attempt or "no hint"
        try:
            details = await resolver.contract_details_for_symbol(entry.ibkr_symbol, attempt)
        except (TWSConnectionError, TWSCompetingSessionError):
            raise
        except TWSRequestError as exc:
            failures.append(f"{label}: {exc}")
            continue
        problem = details_rejection(details, entry.ibkr_symbol)
        if problem is not None:
            failures.append(f"{label}: {problem}")
            continue
        return Resolution(
            row(
                "resolved",
                con_id=details.con_id,
                primary_exchange=details.primary_exchange.strip().upper() or None,
            ),
            used_fallback=attempt is None and hint is not None,
        )
    return Resolution(row("unresolved", error="; ".join(failures)))


@dataclass(frozen=True, slots=True)
class ImportReport:
    parsed: int
    duplicates: tuple[str, ...]
    resolved: int
    unresolved: tuple[UniverseRow, ...]
    used_fallback: tuple[str, ...]
    removed: tuple[str, ...]


async def import_watchlist(
    resolver: SymbolResolver,
    text: str,
    *,
    clock: Clock = _utc_now,
    pacer: RequestPacer | None = None,
    source: str = WATCHLIST_SOURCE,
) -> ImportReport:
    """Resolve every entry, then replace the source's rows in one transaction.

    The stored universe always equals the latest export: symbols missing from
    it are removed, and a failed import leaves the previous universe intact.
    """
    parsed = parse_watchlist(text)
    resolutions: list[Resolution] = []
    for index, entry in enumerate(parsed.entries, start=1):
        resolution = await resolve_entry(
            resolver, entry, now=clock(), source=source, pacer=pacer
        )
        resolutions.append(resolution)
        if resolution.row.status != "resolved":
            logger.warning("unresolved %s: %s", entry.label, resolution.row.last_error)
        if index % 100 == 0:
            logger.info("resolved %d/%d watchlist symbols", index, len(parsed.entries))
    rows = tuple(resolution.row for resolution in resolutions)
    removed = await replace_universe_source(source, rows)
    return ImportReport(
        parsed=len(parsed.entries),
        duplicates=parsed.duplicates,
        resolved=sum(1 for row in rows if row.status == "resolved"),
        unresolved=tuple(row for row in rows if row.status != "resolved"),
        used_fallback=tuple(
            resolution.row.symbol for resolution in resolutions if resolution.used_fallback
        ),
        removed=removed,
    )


# --- universe persistence ------------------------------------------------------

_UNIVERSE_UPSERT = """
INSERT INTO autotrader_universe (
    source, symbol, listing_exchange, ibkr_symbol, con_id, primary_exchange,
    status, last_error, updated_at
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(source, symbol) DO UPDATE SET
    listing_exchange = excluded.listing_exchange,
    ibkr_symbol = excluded.ibkr_symbol,
    con_id = excluded.con_id,
    primary_exchange = excluded.primary_exchange,
    status = excluded.status,
    last_error = excluded.last_error,
    updated_at = excluded.updated_at
"""


def _universe_params(row: UniverseRow) -> tuple[object, ...]:
    return (
        row.source,
        row.symbol,
        row.listing_exchange,
        row.ibkr_symbol,
        row.con_id,
        row.primary_exchange,
        row.status,
        row.last_error,
        _timestamp(row.updated_at),
    )


async def replace_universe_source(source: str, rows: Sequence[UniverseRow]) -> tuple[str, ...]:
    """Upsert ``rows`` and delete the source's other symbols; returns removed symbols."""
    if any(row.source != source for row in rows):
        raise ValueError("every row must belong to the replaced source")
    keep = {row.symbol for row in rows}
    async with transaction() as db:
        async with db.execute(
            "SELECT symbol FROM autotrader_universe WHERE source = ?", (source,)
        ) as cursor:
            existing = {str(item[0]) for item in await cursor.fetchall()}
        removed = tuple(sorted(existing - keep))
        for row in rows:
            await db.execute(_UNIVERSE_UPSERT, _universe_params(row))
        for symbol in removed:
            await db.execute(
                "DELETE FROM autotrader_universe WHERE source = ? AND symbol = ?",
                (source, symbol),
            )
    return removed


def _universe_row(raw: Sequence[object]) -> UniverseRow:
    return UniverseRow(
        source=str(raw[0]),
        symbol=str(raw[1]),
        listing_exchange=str(raw[2]),
        ibkr_symbol=str(raw[3]),
        con_id=int(str(raw[4])) if raw[4] is not None else None,
        primary_exchange=str(raw[5]) if raw[5] is not None else None,
        status=_status(raw[6]),
        last_error=str(raw[7]) if raw[7] is not None else None,
        updated_at=datetime.fromisoformat(str(raw[8])),
    )


def _status(value: object) -> UniverseStatus:
    text = str(value)
    if text == "resolved":
        return "resolved"
    if text == "unresolved":
        return "unresolved"
    if text == "pending":
        return "pending"
    raise ValueError(f"unknown universe status {text!r}")


async def load_universe(source: str | None = None) -> tuple[UniverseRow, ...]:
    query = (
        "SELECT source, symbol, listing_exchange, ibkr_symbol, con_id, primary_exchange,"
        " status, last_error, updated_at FROM autotrader_universe"
    )
    params: tuple[object, ...] = ()
    if source is not None:
        query += " WHERE source = ?"
        params = (source,)
    query += " ORDER BY source, symbol"
    async with get_db() as db:
        async with db.execute(query, params) as cursor:
            return tuple(_universe_row(raw) for raw in await cursor.fetchall())


def resolved_instruments(rows: Iterable[UniverseRow]) -> tuple[UniverseRow, ...]:
    """One row per conId; the owner's watchlist wins over other sources."""
    ordered = sorted(
        (row for row in rows if row.status == "resolved"),
        key=lambda row: (row.source != WATCHLIST_SOURCE, row.source, row.symbol),
    )
    unique: dict[int, UniverseRow] = {}
    for row in ordered:
        if row.con_id is not None:
            unique.setdefault(row.con_id, row)
    return tuple(unique.values())


# --- daily qualification ---------------------------------------------------------


class DailyHistorySource(Protocol):
    async def daily_trade_bars(self, con_id: int) -> DailyBarBatch: ...


@dataclass(frozen=True, slots=True)
class DailyQualification:
    session_date: date
    con_id: int
    symbol: str
    prior_close: Decimal | None
    avg_daily_volume_shares: Decimal | None
    avg_dollar_volume: Decimal | None
    daily_atr: Decimal | None
    qualified: bool
    reason: str | None
    observed_at: datetime

    def __post_init__(self) -> None:
        if self.con_id <= 0:
            raise ValueError("con_id must be positive")
        if self.qualified and (
            self.reason is not None or self.prior_close is None or self.avg_dollar_volume is None
        ):
            raise ValueError("a qualified row needs metrics and no rejection reason")
        if not self.qualified and not self.reason:
            raise ValueError("an unqualified row needs a reason")

    @property
    def transient(self) -> bool:
        return bool(self.reason) and str(self.reason).startswith(ERROR_REASON_PREFIX)


def _window(batch: DailyBarBatch, *, session_date: date, lookback: int) -> list[Decimal]:
    prior = sorted(
        (bar for bar in batch.bars if bar.session_date < session_date),
        key=lambda bar: bar.session_date,
    )
    return [bar.volume for bar in prior[-lookback:]]


def evaluate_daily_history(
    batch: DailyBarBatch,
    *,
    con_id: int,
    symbol: str,
    session_date: date,
    volume_multiplier: Decimal,
    observed_at: datetime,
    lookback: int = LIQUIDITY_LOOKBACK_SESSIONS,
) -> DailyQualification:
    """Prior close and 20-session averages; the session's own bar never counts."""

    def outcome(
        reason: str | None,
        *,
        prior_close: Decimal | None = None,
        avg_shares: Decimal | None = None,
        avg_dollars: Decimal | None = None,
    ) -> DailyQualification:
        return DailyQualification(
            session_date=session_date,
            con_id=con_id,
            symbol=symbol,
            prior_close=prior_close,
            avg_daily_volume_shares=avg_shares,
            avg_dollar_volume=avg_dollars,
            daily_atr=None,
            qualified=reason is None,
            reason=reason,
            observed_at=observed_at,
        )

    if not batch.connected:
        return outcome(ERROR_REASON_PREFIX + "TWS connection unavailable")
    if batch.error or batch.pacing_failed:
        return outcome(ERROR_REASON_PREFIX + (batch.error or "IBKR pacing limit"))
    if any(bar.con_id != con_id for bar in batch.bars):
        return outcome(ERROR_REASON_PREFIX + "daily history returned for a different contract")
    try:
        profile = liquidity_profile(
            batch,
            session_date=session_date,
            volume_multiplier=volume_multiplier,
            lookback=lookback,
        )
    except MarketDataQualityError as exc:
        return outcome(str(exc))
    volumes = _window(batch, session_date=session_date, lookback=lookback)
    avg_shares = sum(volumes, Decimal("0")) * volume_multiplier / Decimal(len(volumes))
    avg_dollars = profile.average_dollar_volume
    reason: str | None = None
    if not MIN_PRICE <= profile.reference_price <= MAX_PRICE:
        reason = "price_out_of_range"
    elif avg_dollars < MIN_AVG_DOLLAR_VOLUME:
        reason = "dollar_volume_too_low"
    return outcome(
        reason,
        prior_close=profile.reference_price,
        avg_shares=avg_shares.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
        avg_dollars=avg_dollars.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
    )


_DAILY_UPSERT = """
INSERT INTO autotrader_universe_daily (
    session_date, con_id, symbol, prior_close, avg_daily_volume_shares,
    avg_dollar_volume, daily_atr, qualified, reason, observed_at
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(session_date, con_id) DO UPDATE SET
    symbol = excluded.symbol,
    prior_close = excluded.prior_close,
    avg_daily_volume_shares = excluded.avg_daily_volume_shares,
    avg_dollar_volume = excluded.avg_dollar_volume,
    daily_atr = excluded.daily_atr,
    qualified = excluded.qualified,
    reason = excluded.reason,
    observed_at = excluded.observed_at
"""


async def upsert_daily(row: DailyQualification) -> None:
    async with transaction() as db:
        await db.execute(
            _DAILY_UPSERT,
            (
                row.session_date.isoformat(),
                row.con_id,
                row.symbol,
                _decimal_text(row.prior_close),
                _decimal_text(row.avg_daily_volume_shares),
                _decimal_text(row.avg_dollar_volume),
                _decimal_text(row.daily_atr),
                1 if row.qualified else 0,
                row.reason,
                _timestamp(row.observed_at),
            ),
        )


async def load_daily(session_date: date) -> tuple[DailyQualification, ...]:
    async with get_db() as db:
        async with db.execute(
            """SELECT session_date, con_id, symbol, prior_close, avg_daily_volume_shares,
                      avg_dollar_volume, daily_atr, qualified, reason, observed_at
               FROM autotrader_universe_daily WHERE session_date = ? ORDER BY symbol""",
            (session_date.isoformat(),),
        ) as cursor:
            raw_rows = await cursor.fetchall()
    return tuple(
        DailyQualification(
            session_date=date.fromisoformat(str(raw[0])),
            con_id=int(str(raw[1])),
            symbol=str(raw[2]),
            prior_close=_decimal_or_none(raw[3]),
            avg_daily_volume_shares=_decimal_or_none(raw[4]),
            avg_dollar_volume=_decimal_or_none(raw[5]),
            daily_atr=_decimal_or_none(raw[6]),
            qualified=bool(raw[7]),
            reason=str(raw[8]) if raw[8] is not None else None,
            observed_at=datetime.fromisoformat(str(raw[9])),
        )
        for raw in raw_rows
    )


@dataclass(frozen=True, slots=True)
class QualificationReport:
    session_date: date
    instruments: int
    skipped: int
    requested: int
    qualified: int
    rejected: tuple[tuple[str, int], ...]
    errors: tuple[tuple[str, str], ...]


def summarize_daily(
    session_date: date,
    rows: Sequence[DailyQualification],
    *,
    instruments: int,
    skipped: int,
    requested: int,
) -> QualificationReport:
    final_rejections = Counter(
        str(row.reason) for row in rows if not row.qualified and not row.transient
    )
    return QualificationReport(
        session_date=session_date,
        instruments=instruments,
        skipped=skipped,
        requested=requested,
        qualified=sum(1 for row in rows if row.qualified),
        rejected=tuple(sorted(final_rejections.items(), key=lambda item: (-item[1], item[0]))),
        errors=tuple((row.symbol, str(row.reason)) for row in rows if row.transient),
    )


async def qualify_contract(
    history: DailyHistorySource,
    *,
    con_id: int,
    symbol: str,
    session_date: date,
    volume_multiplier: Decimal,
    clock: Clock,
    pacer: RequestPacer,
) -> DailyQualification:
    """Request one contract's daily history, evaluate it, and persist the outcome.

    A competing login or lost connection raises instead of recording a
    failure, because neither says anything about the contract itself.
    """
    await pacer.wait()
    try:
        batch = await history.daily_trade_bars(con_id)
    except (TWSConnectionError, TWSCompetingSessionError):
        raise
    except TWSRequestError as exc:
        result = DailyQualification(
            session_date=session_date,
            con_id=con_id,
            symbol=symbol,
            prior_close=None,
            avg_daily_volume_shares=None,
            avg_dollar_volume=None,
            daily_atr=None,
            qualified=False,
            reason=ERROR_REASON_PREFIX + str(exc),
            observed_at=clock(),
        )
    else:
        if is_competing_session_report(batch.error):
            raise TWSCompetingSessionError(str(batch.error))
        if not batch.connected:
            raise TWSConnectionError("TWS connection lost during qualification")
        if batch.pacing_failed:
            pacer.defer(PACING_BACKOFF_SECONDS)
        result = evaluate_daily_history(
            batch,
            con_id=con_id,
            symbol=symbol,
            session_date=session_date,
            volume_multiplier=volume_multiplier,
            observed_at=clock(),
        )
    await upsert_daily(result)
    if result.transient:
        logger.warning("qualification of %s failed: %s", symbol, result.reason)
    return result


async def qualify_universe(
    history: DailyHistorySource,
    *,
    session_date: date,
    volume_multiplier: Decimal,
    clock: Clock = _utc_now,
    pacer: RequestPacer | None = None,
) -> QualificationReport:
    """Qualify every resolved conId for ``session_date``; resumable.

    Contracts with a final outcome for the date are skipped, so an
    interrupted run continues where it stopped and transient failures are
    retried. A competing login or lost connection aborts the run.
    """
    instruments = resolved_instruments(await load_universe())
    universe_ids = {row.con_id for row in instruments}
    final = {row.con_id for row in await load_daily(session_date) if not row.transient}
    pending = [row for row in instruments if row.con_id not in final]
    pacer = pacer or RequestPacer(1.0)
    if pending:
        logger.info(
            "qualifying %d of %d instruments for %s",
            len(pending),
            len(instruments),
            session_date.isoformat(),
        )
    for index, row in enumerate(pending, start=1):
        if row.con_id is None:
            continue
        await qualify_contract(
            history,
            con_id=row.con_id,
            symbol=row.symbol,
            session_date=session_date,
            volume_multiplier=volume_multiplier,
            clock=clock,
            pacer=pacer,
        )
        if index % 50 == 0:
            logger.info("qualified %d/%d instruments", index, len(pending))
    # Rows for scan-sourced contracts share the table but not this report.
    rows = [row for row in await load_daily(session_date) if row.con_id in universe_ids]
    return summarize_daily(
        session_date,
        rows,
        instruments=len(instruments),
        skipped=len(instruments) - len(pending),
        requested=len(pending),
    )


__all__ = [
    "ERROR_REASON_PREFIX",
    "EXCHANGE_HINTS",
    "MAX_PRICE",
    "MAX_WATCHLIST_BYTES",
    "MIN_AVG_DOLLAR_VOLUME",
    "MIN_PRICE",
    "WATCHLIST_SOURCE",
    "DailyQualification",
    "ImportReport",
    "ParsedWatchlist",
    "QualificationReport",
    "RequestPacer",
    "Resolution",
    "UniverseRow",
    "WatchlistEntry",
    "WatchlistFormatError",
    "details_rejection",
    "entry_rejection",
    "evaluate_daily_history",
    "import_watchlist",
    "load_daily",
    "load_universe",
    "parse_watchlist",
    "qualify_contract",
    "qualify_universe",
    "replace_universe_source",
    "resolve_entry",
    "resolved_instruments",
    "summarize_daily",
    "to_ibkr_symbol",
    "upsert_daily",
]

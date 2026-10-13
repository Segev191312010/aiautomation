"""Live screener: deterministic ranking of the qualified universe.

A sweep takes one short-lived streaming snapshot per qualified instrument,
ranks the movers, and appends one ``screener.v1`` payload. The dashboard and
strategies read only persisted payloads; ranking is pure so identical inputs
always produce the identical hot list. With the context ETFs configured the
payload also carries the sweep's ``market_context.v1`` document.

The hot-35 ranking group is the qualified watchlist plus at most 15 scan
additions, exactly as before the day manager. Two kinds of names are quoted
after that group, in their own pass, and never ranked with it:

* the day universe (``trading.market.day_universe``), ranked in its own list
  under ``day_universe``; the manager and the simulations read it, the real
  MOMO never does;
* symbols the order bridge may hold (the session's accepted decisions of a
  sendable strategy name) that left the ranking group: they trail ``rows``
  unranked (score 0, never hot) so the bridge's flatten fallback always finds
  a fresh ``rows[].{symbol,con_id,bid,last}`` quote.
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Callable, Mapping, Sequence, Set
from dataclasses import dataclass, field
from datetime import date, datetime, time as dt_time, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
import json
import logging
import time
from typing import Annotated, Any, Literal, Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from db.core import get_db, transaction
from trading.market.context import (
    CONTEXT_SYMBOL_SET,
    CONTEXT_SYMBOLS,
    MarketContext,
    build_market_context,
)
from trading.market.day_universe import DAY_UNIVERSE_MAX, load_day_universe_members
from trading.market.ibkr_boundary import IBKRScannerResult
from trading.market.snapshots import MarketSnapshot
from trading.market.tws_actor import (
    TWSCompetingSessionError,
    TWSConnectionError,
    TWSRequestError,
)
from trading.market.universe import (
    WATCHLIST_SOURCE,
    DailyHistorySource,
    RequestPacer,
    SymbolResolver,
    details_rejection,
    load_daily,
    qualify_contract,
)
from trading.scheduler.sessions import MARKET_TIMEZONE, MarketSession, market_session
from trading.sendable_identities import carries_sendable_prefix

logger = logging.getLogger("autotrader.shadow.screener")

SCHEMA_VERSION = "screener.v1"
HOT_LIST_SIZE = 35
CHANGE_WEIGHT = Decimal("0.5")
RVOL_WEIGHT = Decimal("0.3")
HIGH_WEIGHT = Decimal("0.2")
REGULAR_SESSION_MINUTES = Decimal("390")
MIN_ELAPSED_FRACTION = Decimal("0.05")
# Live 2026-10-12 (TWS with ibapi 9.81): streaming tick 8 arrives in shares --
# TWS warned "Trimmed value 7729883.797717 to 7729883" for AAPL -- while
# historical bars arrive in round lots. Applying the bars' x100 here would
# overstate intraday volume, rvol and dollar volume a hundredfold.
TICK_VOLUME_MULTIPLIER = Decimal("1")
# Snapshots are taken in chunks so a dead feed is noticed between chunks;
# within a chunk the actor bounds concurrent lines and message rate.
SNAPSHOT_CHUNK_SIZE = 50
# A run of failures means the feed itself is down; stop instead of spending
# minutes timing out on every remaining instrument.
MAX_CONSECUTIVE_SNAPSHOT_ERRORS = 25
SOURCES = ("watchlist", "ibkr_scan")
# The hot-35 ranking group's scans and additions: the real MOMO's inputs.
SCAN_CODES = ("HOT_BY_VOLUME", "TOP_PERC_GAIN", "MOST_ACTIVE")
MAX_SCAN_ADDITIONS = 15
# Daily-history requests are paced at one per second, so qualifying scan
# symbols is bounded per sweep; qualified rows are reused all session.
MAX_SCAN_QUALIFICATIONS_PER_SWEEP = 15
# Held and day-universe rows are quoted, never ranked with the hot-35 group.
UNRANKED_SCORE = Decimal("0")
# The first sweep compares each evening-qualified prior close with TWS's own
# close tick; a larger relative gap (a split, a correction) re-qualifies the
# contract with the morning's history, as an 08:00 qualification would.
PRIOR_CLOSE_RECHECK_TOLERANCE = Decimal("0.02")
# A context ETF that failed to resolve is retried this long after the
# failure, so one transient error cannot leave the regime without SPY/QQQ
# for the life of the process; resolved conIds are never re-requested.
CONTEXT_RESOLVE_RETRY = timedelta(minutes=15)
_ONE = Decimal("1")
_HUNDRED = Decimal("100")

Clock = Callable[[], datetime]
ScreenerSource = Literal["watchlist", "ibkr_scan"]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_iso(value: datetime) -> str:
    """Fixed-precision UTC text so stored timestamps sort chronologically."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


class ScreenerError(RuntimeError):
    """A sweep could not produce a trustworthy snapshot."""


# --- metrics -----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ScreenerInstrument:
    """A qualified universe row with its prior-session volume baseline."""

    symbol: str
    con_id: int
    primary_exchange: str | None
    source: ScreenerSource
    avg_daily_volume_shares: Decimal | None


@dataclass(frozen=True, slots=True)
class ScreenerMetrics:
    price: Decimal
    change_pct: Decimal
    gap_pct: Decimal | None
    volume_shares: Decimal | None
    rvol: Decimal | None
    dollar_volume: Decimal | None
    pct_from_high: Decimal | None


def elapsed_fraction(now: datetime, session: MarketSession | None) -> Decimal:
    """Share of a 390-minute session elapsed, floored at 5% (pre-open: 5%)."""
    if session is None or now < session.opens_at:
        return MIN_ELAPSED_FRACTION
    end = min(now, session.closes_at)
    minutes = Decimal(str((end - session.opens_at).total_seconds())) / Decimal("60")
    return min(_ONE, max(MIN_ELAPSED_FRACTION, minutes / REGULAR_SESSION_MINUTES))


def compute_metrics(
    snapshot: MarketSnapshot,
    *,
    avg_daily_volume_shares: Decimal | None,
    session: MarketSession | None,
    volume_multiplier: Decimal = TICK_VOLUME_MULTIPLIER,
) -> ScreenerMetrics:
    price = snapshot.reference_price
    if price is None:
        raise ScreenerError("snapshot has no reference price")
    prior = snapshot.prior_close
    pre_open = session is None or snapshot.observed_at < session.opens_at
    gap_pct = None
    if not pre_open and snapshot.open is not None:
        gap_pct = (snapshot.open - prior) / prior * _HUNDRED
    volume_shares = None
    if snapshot.volume_raw is not None:
        volume_shares = snapshot.volume_raw * volume_multiplier
    rvol = None
    if volume_shares is not None and avg_daily_volume_shares and avg_daily_volume_shares > 0:
        expected = avg_daily_volume_shares * elapsed_fraction(snapshot.observed_at, session)
        rvol = volume_shares / expected
    pct_from_high = None
    if snapshot.day_high is not None:
        pct_from_high = (price - snapshot.day_high) / snapshot.day_high * _HUNDRED
    return ScreenerMetrics(
        price=price,
        change_pct=(price - prior) / prior * _HUNDRED,
        gap_pct=gap_pct,
        volume_shares=volume_shares,
        rvol=rvol,
        dollar_volume=None if volume_shares is None else volume_shares * price,
        pct_from_high=pct_from_high,
    )


def percentile_ranks(values: Sequence[Decimal | None]) -> tuple[Decimal, ...]:
    """Mid-rank percentiles in [0, 1]; ties share a rank, unknown values rank 0."""
    present = sorted(value for value in values if value is not None)
    span = len(present) - 1
    ranks: list[Decimal] = []
    for value in values:
        if value is None:
            ranks.append(Decimal("0"))
            continue
        if span == 0:
            ranks.append(Decimal("0.5"))
            continue
        below = bisect_left(present, value)
        equal = bisect_right(present, value) - below
        ranks.append((Decimal(below) + Decimal(equal - 1) / 2) / Decimal(span))
    return tuple(ranks)


@dataclass(frozen=True, slots=True)
class Observation:
    instrument: ScreenerInstrument
    snapshot: MarketSnapshot
    metrics: ScreenerMetrics


@dataclass(frozen=True, slots=True)
class RankedObservation:
    observation: Observation
    score: Decimal
    hot_rank: int | None


def rank_observations(
    observations: Sequence[Observation], *, hot_list_size: int = HOT_LIST_SIZE
) -> tuple[RankedObservation, ...]:
    """Score = 0.5*pct(change) + 0.3*pct(rvol) + 0.2*pct(closeness to high).

    The hot list is the top ``hot_list_size`` rows by score among rows that
    are up on the day; ties break on change, then symbol, then conId.
    """
    change = percentile_ranks([item.metrics.change_pct for item in observations])
    rvol = percentile_ranks([item.metrics.rvol for item in observations])
    high = percentile_ranks([item.metrics.pct_from_high for item in observations])
    scored = [
        (item, CHANGE_WEIGHT * change[index] + RVOL_WEIGHT * rvol[index] + HIGH_WEIGHT * high[index])
        for index, item in enumerate(observations)
    ]
    scored.sort(
        key=lambda pair: (
            -pair[1],
            -pair[0].metrics.change_pct,
            pair[0].instrument.symbol,
            pair[0].instrument.con_id,
        )
    )
    ranked: list[RankedObservation] = []
    hot = 0
    for item, score in scored:
        hot_rank = None
        if item.metrics.change_pct > 0 and hot < hot_list_size:
            hot += 1
            hot_rank = hot
        ranked.append(RankedObservation(observation=item, score=score, hot_rank=hot_rank))
    return tuple(ranked)


def rank_day_universe(observations: Sequence[Observation]) -> tuple[RankedObservation, ...]:
    """The day universe's own ranking: the hot-35 score, over its names only.

    Same weights and tie-break as ``rank_observations`` but its own
    percentiles, so the two lists never influence each other; every row is
    ranked (no gain required). ``hot_rank`` carries the list position.
    """
    change = percentile_ranks([item.metrics.change_pct for item in observations])
    rvol = percentile_ranks([item.metrics.rvol for item in observations])
    high = percentile_ranks([item.metrics.pct_from_high for item in observations])
    scored = sorted(
        (
            (item, CHANGE_WEIGHT * change[index] + RVOL_WEIGHT * rvol[index] + HIGH_WEIGHT * high[index])
            for index, item in enumerate(observations)
        ),
        key=lambda pair: (
            -pair[1],
            -pair[0].metrics.change_pct,
            pair[0].instrument.symbol,
            pair[0].instrument.con_id,
        ),
    )
    return tuple(
        RankedObservation(observation=item, score=score, hot_rank=position)
        for position, (item, score) in enumerate(scored, start=1)
    )


# --- screener.v1 payload --------------------------------------------------------

DecimalText = Annotated[str, StringConstraints(pattern=r"^-?\d+(\.\d+)?$")]


class ScreenerRow(BaseModel):
    """One ranked instrument; Decimals are strings, unknown values are null."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    symbol: str = Field(min_length=1, max_length=16)
    con_id: int = Field(gt=0)
    primary_exchange: str | None
    source: ScreenerSource
    last: DecimalText | None
    bid: DecimalText | None
    ask: DecimalText | None
    prior_close: DecimalText | None
    open: DecimalText | None
    day_high: DecimalText | None
    day_low: DecimalText | None
    change_pct: DecimalText | None
    gap_pct: DecimalText | None
    volume_shares: DecimalText | None
    rvol: DecimalText | None
    dollar_volume: DecimalText | None
    pct_from_high: DecimalText | None
    score: DecimalText
    hot: bool
    hot_rank: int | None = Field(ge=1)

    @model_validator(mode="after")
    def validate_hot_rank(self) -> ScreenerRow:
        if self.hot != (self.hot_rank is not None):
            raise ValueError("hot rows and only hot rows carry a hot_rank")
        return self


class DayUniverseRow(BaseModel):
    """One day-universe name in its own ranking (``rank`` 1..n by its own score)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    rank: int = Field(ge=1)
    symbol: str = Field(min_length=1, max_length=16)
    con_id: int = Field(gt=0)
    primary_exchange: str | None
    last: DecimalText | None
    bid: DecimalText | None
    ask: DecimalText | None
    prior_close: DecimalText | None
    open: DecimalText | None
    day_high: DecimalText | None
    day_low: DecimalText | None
    change_pct: DecimalText | None
    gap_pct: DecimalText | None
    volume_shares: DecimalText | None
    rvol: DecimalText | None
    dollar_volume: DecimalText | None
    pct_from_high: DecimalText | None
    score: DecimalText


class DayUniverseSection(BaseModel):
    """The sweep's quotes of the day universe; never part of ``rows`` or the hot list.

    ``size`` counts the session's qualified names at the sweep; each is either
    quoted (a row) or an error. ``complete`` is false when the pass stopped
    early (a failing feed), so unattempted names count as errors.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    size: int = Field(ge=0, le=DAY_UNIVERSE_MAX)
    quoted: int = Field(ge=0)
    errors: int = Field(ge=0)
    complete: bool
    seconds: float = Field(ge=0)
    rows: tuple[DayUniverseRow, ...]

    @model_validator(mode="after")
    def validate_section(self) -> DayUniverseSection:
        if self.quoted != len(self.rows):
            raise ValueError("day universe: quoted must equal the number of rows")
        if self.size != self.quoted + self.errors:
            raise ValueError("day universe: every name is either quoted or an error")
        scores = [Decimal(row.score) for row in self.rows]
        if scores != sorted(scores, reverse=True):
            raise ValueError("day universe rows must be sorted by score, highest first")
        if [row.rank for row in self.rows] != list(range(1, len(self.rows) + 1)):
            raise ValueError("day universe ranks must run 1..n in score order")
        if len({row.con_id for row in self.rows}) != len(self.rows):
            raise ValueError("a conId appears at most once in the day universe")
        return self


class ScreenerPayload(BaseModel):
    """The persisted sweep contract consumed by the dashboard and strategies."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    schema_id: Literal["screener.v1"] = Field(alias="schema")
    session_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    observed_at: str
    sweep_seconds: float = Field(ge=0)
    universe_size: int = Field(ge=0)
    qualified: int = Field(ge=0)
    quoted: int = Field(ge=0)
    errors: int = Field(ge=0)
    rows: tuple[ScreenerRow, ...]
    # The sweep's market_context.v1 document, absent on sweeps without one.
    # Its own reader (trading.market.context) validates it, so a context
    # problem can never make the hot list unreadable.
    market: dict[str, Any] | None = None
    # The day universe's own list, absent on sweeps without it. Strategies
    # reading the hot list and the breadth never look here.
    day_universe: DayUniverseSection | None = None

    @model_validator(mode="after")
    def validate_sweep(self) -> ScreenerPayload:
        date.fromisoformat(self.session_date)
        observed = datetime.fromisoformat(self.observed_at)
        if observed.tzinfo is None or observed.utcoffset() is None:
            raise ValueError("observed_at must be timezone-aware")
        if self.quoted != len(self.rows):
            raise ValueError("quoted must equal the number of rows")
        if self.qualified != self.quoted + self.errors:
            raise ValueError("every qualified instrument is either quoted or an error")
        scores = [Decimal(row.score) for row in self.rows]
        if scores != sorted(scores, reverse=True):
            raise ValueError("rows must be sorted by score, highest first")
        hot_ranks = [row.hot_rank for row in self.rows if row.hot_rank is not None]
        if hot_ranks != list(range(1, len(hot_ranks) + 1)) or len(hot_ranks) > HOT_LIST_SIZE:
            raise ValueError("hot ranks must run 1..n in score order")
        if len({row.con_id for row in self.rows}) != len(self.rows):
            raise ValueError("a conId appears at most once per sweep")
        return self

    def to_json(self) -> str:
        # A sweep without market context or day universe serializes
        # byte-for-byte as before either existed.
        exclude = {name for name in ("market", "day_universe") if getattr(self, name) is None}
        return json.dumps(
            self.model_dump(mode="json", by_alias=True, exclude=exclude or None),
            separators=(",", ":"),
        )


def _fixed(value: Decimal | None, places: str) -> str | None:
    if value is None:
        return None
    quantized = value.quantize(Decimal(places), rounding=ROUND_HALF_UP)
    # A tiny negative rounds to "-0.00"; zero carries no sign.
    return format(quantized.copy_abs() if quantized.is_zero() else quantized, "f")


def price_text(value: Decimal | None) -> str | None:
    """At least cents, at most four places (sub-penny prints keep their digits)."""
    if value is None:
        return None
    whole, _, fraction = format(
        value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP), "f"
    ).partition(".")
    return f"{whole}.{fraction.rstrip('0').ljust(2, '0')}"


def payload_row(ranked: RankedObservation) -> ScreenerRow:
    instrument = ranked.observation.instrument
    snapshot = ranked.observation.snapshot
    metrics = ranked.observation.metrics
    # Before the open TWS's open tick still describes the prior session, and
    # gap_pct is null exactly then.
    session_open = snapshot.open if metrics.gap_pct is not None else None
    return ScreenerRow(
        symbol=instrument.symbol,
        con_id=instrument.con_id,
        primary_exchange=instrument.primary_exchange,
        source=instrument.source,
        last=price_text(snapshot.last),
        bid=price_text(snapshot.bid),
        ask=price_text(snapshot.ask),
        prior_close=price_text(snapshot.prior_close),
        open=price_text(session_open),
        day_high=price_text(snapshot.day_high),
        day_low=price_text(snapshot.day_low),
        change_pct=_fixed(metrics.change_pct, "0.01"),
        gap_pct=_fixed(metrics.gap_pct, "0.01"),
        volume_shares=_fixed(metrics.volume_shares, "1"),
        rvol=_fixed(metrics.rvol, "0.01"),
        dollar_volume=_fixed(metrics.dollar_volume, "0.01"),
        pct_from_high=_fixed(metrics.pct_from_high, "0.01"),
        score=_fixed(ranked.score, "0.0001") or "0",
        hot=ranked.hot_rank is not None,
        hot_rank=ranked.hot_rank,
    )


def day_universe_row(ranked: RankedObservation) -> DayUniverseRow:
    """A day-universe row; ``hot_rank`` of ``rank_day_universe`` is its rank."""
    row = payload_row(
        RankedObservation(observation=ranked.observation, score=ranked.score, hot_rank=None)
    )
    if ranked.hot_rank is None:
        raise ScreenerError("a day-universe row needs its rank")
    values = row.model_dump(exclude={"source", "hot", "hot_rank"})
    return DayUniverseRow(rank=ranked.hot_rank, **values)


def unranked(observations: Sequence[Observation]) -> tuple[RankedObservation, ...]:
    """Quoted-only rows (held symbols outside the ranking group), by symbol."""
    ordered = sorted(observations, key=lambda item: (item.instrument.symbol, item.instrument.con_id))
    return tuple(
        RankedObservation(observation=item, score=UNRANKED_SCORE, hot_rank=None) for item in ordered
    )


def build_payload(
    *,
    session_date: date,
    observed_at: datetime,
    sweep_seconds: float,
    universe_size: int,
    qualified: int,
    errors: int,
    ranked: Sequence[RankedObservation],
    trailing: Sequence[RankedObservation] = (),
    day_universe: DayUniverseSection | None = None,
) -> ScreenerPayload:
    """``rows`` are ``ranked`` (the hot-35 group, as ranked) then ``trailing``."""
    rows = [*ranked, *trailing]
    document: dict[str, Any] = {
        "schema": SCHEMA_VERSION,
        "session_date": session_date.isoformat(),
        "observed_at": utc_iso(observed_at),
        "sweep_seconds": round(max(0.0, sweep_seconds), 1),
        "universe_size": universe_size,
        "qualified": qualified,
        "quoted": len(rows),
        "errors": errors,
        "rows": [payload_row(item) for item in rows],
    }
    if day_universe is not None:
        document["day_universe"] = day_universe
    return ScreenerPayload.model_validate(document)


def with_market_context(payload: ScreenerPayload, context: MarketContext) -> ScreenerPayload:
    """The payload with its own sweep's market context under ``market``."""
    if (
        context.session_date.isoformat() != payload.session_date
        or utc_iso(context.observed_at) != payload.observed_at
    ):
        raise ScreenerError("market context belongs to a different sweep")
    document = payload.model_dump(mode="json", by_alias=True)
    document["market"] = context.to_payload()
    return ScreenerPayload.model_validate(document)


# --- persistence ------------------------------------------------------------------


async def persist_snapshot(payload: ScreenerPayload) -> str:
    """Append one sweep; snapshots are never updated in place."""
    snapshot_id = uuid4().hex
    async with transaction() as db:
        await db.execute(
            """INSERT INTO autotrader_screener_snapshots
               (id, session_date, observed_at, payload_json) VALUES (?, ?, ?, ?)""",
            (snapshot_id, payload.session_date, payload.observed_at, payload.to_json()),
        )
    return snapshot_id


async def latest_snapshot(session_date: date) -> ScreenerPayload | None:
    async with get_db() as db:
        async with db.execute(
            """SELECT payload_json FROM autotrader_screener_snapshots
               WHERE session_date = ? ORDER BY observed_at DESC, id DESC LIMIT 1""",
            (session_date.isoformat(),),
        ) as cursor:
            raw = await cursor.fetchone()
    if raw is None:
        return None
    payload = ScreenerPayload.model_validate_json(str(raw[0]))
    if payload.session_date != session_date.isoformat():
        raise ScreenerError("stored screener snapshot belongs to a different session")
    return payload


@dataclass(frozen=True, slots=True)
class HotListRow:
    """One hot-list entry for strategies; ``observed_at`` is the sweep start."""

    session_date: date
    observed_at: datetime
    hot_rank: int
    symbol: str
    con_id: int
    primary_exchange: str | None
    source: ScreenerSource
    score: Decimal
    last: Decimal | None
    prior_close: Decimal | None
    open: Decimal | None
    day_high: Decimal | None
    day_low: Decimal | None
    change_pct: Decimal | None
    gap_pct: Decimal | None
    volume_shares: Decimal | None
    rvol: Decimal | None
    dollar_volume: Decimal | None
    pct_from_high: Decimal | None


def _decimal(value: str | None) -> Decimal | None:
    return None if value is None else Decimal(value)


def hotlist_rows(payload: ScreenerPayload) -> tuple[HotListRow, ...]:
    session_date = date.fromisoformat(payload.session_date)
    observed_at = datetime.fromisoformat(payload.observed_at)
    rows = sorted(
        (row for row in payload.rows if row.hot_rank is not None),
        key=lambda row: row.hot_rank or 0,
    )
    return tuple(
        HotListRow(
            session_date=session_date,
            observed_at=observed_at,
            hot_rank=row.hot_rank or 0,
            symbol=row.symbol,
            con_id=row.con_id,
            primary_exchange=row.primary_exchange,
            source=row.source,
            score=Decimal(row.score),
            last=_decimal(row.last),
            prior_close=_decimal(row.prior_close),
            open=_decimal(row.open),
            day_high=_decimal(row.day_high),
            day_low=_decimal(row.day_low),
            change_pct=_decimal(row.change_pct),
            gap_pct=_decimal(row.gap_pct),
            volume_shares=_decimal(row.volume_shares),
            rvol=_decimal(row.rvol),
            dollar_volume=_decimal(row.dollar_volume),
            pct_from_high=_decimal(row.pct_from_high),
        )
        for row in rows
    )


async def latest_hotlist(session_date: date) -> tuple[HotListRow, ...]:
    """Hot list of the newest sweep for the session, best first; empty if none.

    Callers own freshness: compare ``observed_at`` with their own limit.
    """
    payload = await latest_snapshot(session_date)
    return () if payload is None else hotlist_rows(payload)


async def latest_day_universe(
    session_date: date,
) -> tuple[datetime, DayUniverseSection] | None:
    """The newest sweep's day-universe list with that sweep's start, or None.

    For the day manager and the simulations only; callers own freshness.
    """
    payload = await latest_snapshot(session_date)
    if payload is None or payload.day_universe is None:
        return None
    return datetime.fromisoformat(payload.observed_at), payload.day_universe


async def load_screener_universe(
    session_date: date,
) -> tuple[int, tuple[ScreenerInstrument, ...]]:
    """Universe size and the instruments qualified for ``session_date``."""
    async with get_db() as db:
        async with db.execute("SELECT count(*) FROM autotrader_universe") as cursor:
            count_row = await cursor.fetchone()
        async with db.execute(
            """SELECT u.source, u.symbol, u.con_id, u.primary_exchange,
                      d.avg_daily_volume_shares
               FROM autotrader_universe u
               JOIN autotrader_universe_daily d
                 ON d.con_id = u.con_id AND d.session_date = ?
               WHERE u.status = 'resolved' AND d.qualified = 1""",
            (session_date.isoformat(),),
        ) as cursor:
            raw_rows = await cursor.fetchall()
    universe_size = int(count_row[0]) if count_row is not None else 0
    candidates = sorted(
        (
            ScreenerInstrument(
                symbol=str(raw[1]),
                con_id=int(str(raw[2])),
                primary_exchange=str(raw[3]) if raw[3] is not None else None,
                source=_source(raw[0]),
                avg_daily_volume_shares=None if raw[4] is None else Decimal(str(raw[4])),
            )
            for raw in raw_rows
        ),
        key=lambda item: (item.source != WATCHLIST_SOURCE, item.symbol, item.con_id),
    )
    unique: dict[int, ScreenerInstrument] = {}
    for instrument in candidates:
        unique.setdefault(instrument.con_id, instrument)
    return universe_size, tuple(sorted(unique.values(), key=lambda item: item.symbol))


def _source(value: object) -> ScreenerSource:
    text = str(value)
    if text == "watchlist":
        return "watchlist"
    if text == "ibkr_scan":
        return "ibkr_scan"
    raise ScreenerError(f"unknown universe source {text!r}")


# --- IBKR scan merge -------------------------------------------------------------


class ScanSource(DailyHistorySource, Protocol):
    async def screener_scan(self, scan_code: str) -> Sequence[IBKRScannerResult]: ...


@dataclass(frozen=True, slots=True)
class ScanMerge:
    """Adds liquid IBKR scan movers that are not in the owner's universe."""

    source: ScanSource
    volume_multiplier: Decimal
    history_pacer: RequestPacer
    max_additions: int = MAX_SCAN_ADDITIONS
    max_qualifications: int = MAX_SCAN_QUALIFICATIONS_PER_SWEEP


def scan_candidates(
    scans: Sequence[Sequence[IBKRScannerResult]], *, exclude: Set[int]
) -> tuple[IBKRScannerResult, ...]:
    """Unique usable rows, best rank first, earlier scan winning rank ties."""
    best: dict[int, tuple[int, int, IBKRScannerResult]] = {}
    for scan_index, results in enumerate(scans):
        for result in results:
            if result.con_id <= 0 or result.con_id in exclude or not result.symbol.strip():
                continue
            if result.currency.strip().upper() != "USD":
                continue
            if result.security_type.strip().upper() != "STK":
                continue
            current = best.get(result.con_id)
            if current is None or (result.rank, scan_index) < current[:2]:
                best[result.con_id] = (result.rank, scan_index, result)
    ordered = sorted(best.values(), key=lambda item: (item[0], item[1], item[2].con_id))
    return tuple(item[2] for item in ordered)


ScanResults = Mapping[str, Sequence[IBKRScannerResult]]


async def run_scans(source: ScanSource, codes: Sequence[str]) -> dict[str, tuple[IBKRScannerResult, ...]]:
    """Each scan once, in ``codes`` order; a failed scan is logged and left out."""
    results: dict[str, tuple[IBKRScannerResult, ...]] = {}
    for code in codes:
        try:
            results[code] = tuple(await source.screener_scan(code))
        except (TWSCompetingSessionError, TWSConnectionError):
            raise
        except TWSRequestError as exc:
            logger.warning("IBKR scan %s failed: %s", code, exc)
    return results


async def scan_additions(
    merge: ScanMerge,
    *,
    session_date: date,
    exclude: Set[int],
    clock: Clock = _utc_now,
    scans: ScanResults | None = None,
) -> tuple[ScreenerInstrument, ...]:
    """Qualified scan symbols outside ``exclude``, at most ``merge.max_additions``.

    Only the ``SCAN_CODES`` results count, in that order, whatever else
    ``scans`` holds: these additions are part of the hot-35 ranking group.
    """
    if scans is None:
        scans = await run_scans(merge.source, SCAN_CODES)
    ranking_scans = [scans[code] for code in SCAN_CODES if code in scans]
    daily = {row.con_id: row for row in await load_daily(session_date)}
    additions: list[ScreenerInstrument] = []
    qualified_now = 0
    for candidate in scan_candidates(ranking_scans, exclude=exclude):
        if len(additions) >= merge.max_additions:
            break
        row = daily.get(candidate.con_id)
        if row is None or row.transient:
            if qualified_now >= merge.max_qualifications:
                continue
            qualified_now += 1
            row = await qualify_contract(
                merge.source,
                con_id=candidate.con_id,
                symbol=candidate.symbol.strip(),
                session_date=session_date,
                volume_multiplier=merge.volume_multiplier,
                clock=clock,
                pacer=merge.history_pacer,
            )
        if row.qualified:
            additions.append(
                ScreenerInstrument(
                    symbol=candidate.symbol.strip(),
                    con_id=candidate.con_id,
                    primary_exchange=candidate.primary_exchange.strip().upper() or None,
                    source="ibkr_scan",
                    avg_daily_volume_shares=row.avg_daily_volume_shares,
                )
            )
    return tuple(additions)


async def universe_con_ids() -> frozenset[int]:
    """Every conId already in the stored universe, qualified today or not."""
    async with get_db() as db:
        async with db.execute(
            "SELECT con_id FROM autotrader_universe WHERE con_id IS NOT NULL"
        ) as cursor:
            return frozenset(int(str(raw[0])) for raw in await cursor.fetchall())


# --- symbols the order bridge may hold --------------------------------------------------

# The pinned bridge's own predicate (paper_bridge.DECISIONS_SQL): accepted
# decisions of the session; the strategy name comes from the run's version.
_ACCEPTED_DECISIONS = """
    SELECT c.con_id, c.symbol,
           CASE WHEN json_valid(c.contract_json)
                THEN json_extract(c.contract_json, '$.primary_exchange') END,
           v.spec_json, d2.avg_daily_volume_shares
    FROM autotrader_candidate_decisions d
    JOIN autotrader_runs r ON r.id = d.run_id
    JOIN autotrader_candidates c ON c.id = d.candidate_id
    LEFT JOIN autotrader_strategy_versions v
      ON v.strategy_id = r.strategy_id AND v.version = r.strategy_version
    LEFT JOIN autotrader_universe_daily d2
      ON d2.session_date = r.session_date AND d2.con_id = c.con_id
    WHERE d.outcome = 'shadow_accepted' AND r.session_date = ?
"""


def _strategy_name(spec_json: object) -> str:
    if not isinstance(spec_json, str):
        return ""
    try:
        spec = json.loads(spec_json)
    except ValueError:
        return ""
    name = spec.get("name") if isinstance(spec, dict) else None
    return name if isinstance(name, str) else ""


async def held_instruments(session_date: date) -> tuple[ScreenerInstrument, ...]:
    """Contracts the order bridge may hold: the session's sendable accepted decisions.

    A superset of the bridge's positions (it may never have sent one), read
    with the bridge's own predicate, so its flatten fallback always finds a
    screener quote for every bracket. Quoted only, never ranked; their rows
    say ``ibkr_scan`` so they can never count toward the watchlist breadth.
    """
    async with get_db() as db:
        async with db.execute(_ACCEPTED_DECISIONS, (session_date.isoformat(),)) as cursor:
            raw_rows = await cursor.fetchall()
    held: dict[int, ScreenerInstrument] = {}
    for con_id, symbol, exchange, spec_json, average in raw_rows:
        if not carries_sendable_prefix(_strategy_name(spec_json)):
            continue
        identifier = int(str(con_id))
        if identifier <= 0 or not 1 <= len(str(symbol or "").strip()) <= 16:
            continue
        held.setdefault(
            identifier,
            ScreenerInstrument(
                symbol=str(symbol).strip(),
                con_id=identifier,
                primary_exchange=str(exchange).strip().upper() or None if exchange else None,
                source="ibkr_scan",
                avg_daily_volume_shares=None if average is None else Decimal(str(average)),
            ),
        )
    return tuple(sorted(held.values(), key=lambda item: (item.symbol, item.con_id)))


# --- market context ETFs -------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ContextInstrument:
    symbol: str
    con_id: int


class ContextETFs:
    """The market-context ETFs' conIds, resolved once and cached in memory.

    One instance lives as long as its screener process. A symbol that fails
    to resolve is left out and retried ``retry_after`` later; a competing
    login or lost connection propagates like any other sweep request.
    """

    def __init__(
        self,
        resolver: SymbolResolver,
        *,
        clock: Clock = _utc_now,
        retry_after: timedelta = CONTEXT_RESOLVE_RETRY,
    ) -> None:
        if retry_after <= timedelta(0):
            raise ValueError("retry_after must be positive")
        self._resolver = resolver
        self._clock = clock
        self._retry_after = retry_after
        self._con_ids: dict[str, int] = {}
        self._failed_at: dict[str, datetime] = {}

    @property
    def con_ids(self) -> frozenset[int]:
        return frozenset(self._con_ids.values())

    def instruments(self) -> tuple[ContextInstrument, ...]:
        """Resolved ETFs in ``CONTEXT_SYMBOLS`` order."""
        return tuple(
            ContextInstrument(symbol=symbol, con_id=self._con_ids[symbol])
            for symbol, _ in CONTEXT_SYMBOLS
            if symbol in self._con_ids
        )

    async def resolve(self) -> tuple[ContextInstrument, ...]:
        """Resolve every ETF not yet cached and not inside its retry wait."""
        failures: list[str] = []
        resolved_now = 0
        for symbol, exchange in CONTEXT_SYMBOLS:
            if symbol in self._con_ids:
                continue
            now = self._clock()
            failed_at = self._failed_at.get(symbol)
            if failed_at is not None and now - failed_at < self._retry_after:
                continue
            problem: str | None
            try:
                details = await self._resolver.contract_details_for_symbol(symbol, exchange)
            except (TWSCompetingSessionError, TWSConnectionError):
                raise
            except TWSRequestError as exc:
                problem = str(exc)
            else:
                problem = details_rejection(details, symbol)
                if problem is None and details.con_id in self._con_ids.values():
                    problem = f"conId {details.con_id} already belongs to another context ETF"
                if problem is None:
                    self._con_ids[symbol] = details.con_id
                    self._failed_at.pop(symbol, None)
                    resolved_now += 1
                    continue
            self._failed_at[symbol] = now
            failures.append(f"{symbol}@{exchange}: {problem}")
        if resolved_now:
            logger.info(
                "context ETFs resolved: %d/%d", len(self._con_ids), len(CONTEXT_SYMBOLS)
            )
        if failures:
            logger.warning(
                "context ETFs unresolved, continuing without them (retry in %d min): %s",
                int(self._retry_after.total_seconds() // 60),
                "; ".join(failures),
            )
        return self.instruments()


def without_context_etfs(
    instruments: Sequence[ScreenerInstrument], con_ids: Set[int]
) -> tuple[ScreenerInstrument, ...]:
    """Context ETFs are market inputs, never screener rows or hot-list entries.

    Matching the symbol as well keeps an ETF out even when its conId could
    not be resolved this process.
    """
    return tuple(
        item
        for item in instruments
        if item.con_id not in con_ids and item.symbol not in CONTEXT_SYMBOL_SET
    )


async def snapshot_context(
    source: SnapshotSource, instruments: Sequence[ContextInstrument]
) -> dict[str, MarketSnapshot]:
    """One snapshot batch over the context ETFs; a failed ETF is left out.

    A competing login or lost connection raises, as for the universe.
    """
    if not instruments:
        return {}
    results = await source.market_snapshots([item.con_id for item in instruments])
    if len(results) != len(instruments):
        raise ScreenerError("context snapshot batch returned a different number of results")
    snapshots: dict[str, MarketSnapshot] = {}
    failures: list[str] = []
    for instrument, result in zip(instruments, results):
        if isinstance(result, (TWSCompetingSessionError, TWSConnectionError)):
            raise result
        if isinstance(result, Exception):
            failures.append(f"{instrument.symbol}: {result}")
        elif result.con_id != instrument.con_id:
            failures.append(f"{instrument.symbol}: snapshot returned for a different contract")
        else:
            snapshots[instrument.symbol] = result
    if failures:
        logger.warning("context ETF snapshot errors: %s", "; ".join(failures))
    return snapshots


# --- sweep --------------------------------------------------------------------------


class SnapshotSource(Protocol):
    async def market_snapshots(
        self, con_ids: Sequence[int], *, timeout_seconds: float = 3.0
    ) -> Sequence[MarketSnapshot | TWSRequestError]: ...


@dataclass(frozen=True, slots=True)
class SweepResult:
    snapshot_id: str
    payload: ScreenerPayload
    error_samples: tuple[str, ...]
    market_context: MarketContext | None = None
    # The sweep's own scan results (code -> rows), for the day universe's
    # admission after the sweep: no scan is requested twice.
    scans: Mapping[str, tuple[IBKRScannerResult, ...]] = field(default_factory=dict)


def _observe(
    instrument: ScreenerInstrument,
    result: MarketSnapshot | Exception,
    session: MarketSession | None,
) -> Observation:
    """One snapshot result as an observation; a request or data problem raises."""
    if isinstance(result, Exception):
        raise result
    if result.con_id != instrument.con_id:
        raise TWSRequestError("snapshot returned for a different contract")
    metrics = compute_metrics(
        result,
        avg_daily_volume_shares=instrument.avg_daily_volume_shares,
        session=session,
    )
    return Observation(instrument=instrument, snapshot=result, metrics=metrics)


class _ExtrasStopped(Exception):
    """The quote-only pass hit a run of failures; the ranking group stands."""


@dataclass(slots=True)
class _ExtrasPass:
    observed: dict[int, Observation] = field(default_factory=dict)
    complete: bool = True
    seconds: float = 0.0
    interrupted: BaseException | None = None


async def _quote_extras(
    source: SnapshotSource,
    extras: Sequence[ScreenerInstrument],
    *,
    session: MarketSession | None,
    monotonic: Callable[[], float],
) -> _ExtrasPass:
    """Quote day-universe and held names after the ranking group, in their own pass.

    Nothing here can change or abort the ranking group's sweep: a run of
    failures only ends this pass, and a competing login or lost connection
    is kept for the caller to raise once the ranking group is persisted.
    """
    outcome = _ExtrasPass()
    started = monotonic()
    consecutive = 0
    try:
        for offset in range(0, len(extras), SNAPSHOT_CHUNK_SIZE):
            chunk = extras[offset : offset + SNAPSHOT_CHUNK_SIZE]
            results = await source.market_snapshots([item.con_id for item in chunk])
            if len(results) != len(chunk):
                raise _ExtrasStopped("snapshot batch returned a different number of results")
            for instrument, result in zip(chunk, results):
                try:
                    outcome.observed[instrument.con_id] = _observe(instrument, result, session)
                except (TWSCompetingSessionError, TWSConnectionError):
                    raise
                except (TWSRequestError, ScreenerError) as exc:
                    logger.debug("extra snapshot failed for %s: %s", instrument.symbol, exc)
                    consecutive += 1
                    if consecutive >= MAX_CONSECUTIVE_SNAPSHOT_ERRORS:
                        raise _ExtrasStopped(f"{consecutive} consecutive failures; last: {exc}") from exc
                    continue
                consecutive = 0
    except _ExtrasStopped as exc:
        outcome.complete = False
        logger.error("day-universe and held quotes stopped early: %s", exc)
    except (TWSCompetingSessionError, TWSConnectionError) as exc:
        outcome.complete = False
        outcome.interrupted = exc
    outcome.seconds = round(max(0.0, monotonic() - started), 1)
    return outcome


class RecheckSource(SnapshotSource, DailyHistorySource, Protocol):
    """Quotes and daily history: what the prior-close re-check needs."""


@dataclass(frozen=True, slots=True)
class PriorCloseRecheck:
    """The first sweep's comparison of evening qualifications with TWS's close."""

    checked: int
    requalified: tuple[str, ...] = ()
    still_different: tuple[str, ...] = ()

    def summary(self) -> str:
        text = f"prior-close re-check: {self.checked} evening rows, {len(self.requalified)} re-qualified"
        if self.requalified:
            text += f" ({', '.join(self.requalified)})"
        if self.still_different:
            text += f"; still different after re-qualifying: {', '.join(self.still_different)}"
        return text


def _relative_gap(quoted: Decimal, stored: Decimal) -> Decimal:
    return abs(quoted - stored) / stored


async def recheck_prior_closes(
    source: RecheckSource,
    *,
    session_date: date,
    payload: ScreenerPayload,
    volume_multiplier: Decimal,
    pacer: RequestPacer,
    clock: Clock = _utc_now,
    tolerance: Decimal = PRIOR_CLOSE_RECHECK_TOLERANCE,
) -> PriorCloseRecheck:
    """Re-qualify evening-qualified contracts whose prior close TWS now reports differently.

    Qualification runs the evening before the session (after the recorder),
    when a split taking effect at the next open is not yet in the history.
    The session's first sweep therefore compares every row qualified before
    07:00 ET of the session with TWS's close tick: the sweep's own quote, or
    one extra snapshot batch for rows the sweep does not quote (e.g. a $350
    stock rejected as out of range that splits 2:1). A relative gap above
    ``tolerance`` re-qualifies the contract exactly as an 08:00
    qualification would. A competing login or lost connection raises.
    """
    cutoff = datetime.combine(session_date, dt_time(7, 0), tzinfo=MARKET_TIMEZONE)
    evening = [
        row
        for row in await load_daily(session_date)
        if row.prior_close is not None and row.prior_close > 0 and row.observed_at < cutoff
    ]
    if not evening:
        return PriorCloseRecheck(checked=0)
    quoted: dict[int, Decimal] = {
        row.con_id: Decimal(row.prior_close) for row in payload.rows if row.prior_close is not None
    }
    unquoted = [row for row in evening if row.con_id not in quoted]
    for offset in range(0, len(unquoted), SNAPSHOT_CHUNK_SIZE):
        chunk = unquoted[offset : offset + SNAPSHOT_CHUNK_SIZE]
        results = await source.market_snapshots([row.con_id for row in chunk])
        for row, result in zip(chunk, results):
            if isinstance(result, (TWSCompetingSessionError, TWSConnectionError)):
                raise result
            if isinstance(result, MarketSnapshot) and result.con_id == row.con_id:
                quoted[row.con_id] = result.prior_close
    requalified: list[str] = []
    still_different: list[str] = []
    for row in evening:
        stored = row.prior_close
        reported = quoted.get(row.con_id)
        if stored is None or reported is None or _relative_gap(reported, stored) <= tolerance:
            continue
        logger.warning(
            "prior close of %s changed overnight (%s stored, %s reported); re-qualifying",
            row.symbol,
            stored,
            reported,
        )
        fresh = await qualify_contract(
            source,
            con_id=row.con_id,
            symbol=row.symbol,
            session_date=session_date,
            volume_multiplier=volume_multiplier,
            clock=clock,
            pacer=pacer,
        )
        requalified.append(row.symbol)
        if fresh.prior_close is None or _relative_gap(reported, fresh.prior_close) > tolerance:
            still_different.append(row.symbol)
    return PriorCloseRecheck(
        checked=len(evening),
        requalified=tuple(requalified),
        still_different=tuple(still_different),
    )


async def day_universe_instruments(
    session_date: date, context_ids: Set[int]
) -> tuple[ScreenerInstrument, ...]:
    """The session's qualified day-universe names as quotable instruments."""
    members = await load_day_universe_members(session_date)
    return without_context_etfs(
        tuple(
            ScreenerInstrument(
                symbol=member.symbol,
                con_id=member.con_id,
                primary_exchange=member.primary_exchange,
                source="ibkr_scan",
                avg_daily_volume_shares=member.avg_daily_volume_shares,
            )
            for member in members[:DAY_UNIVERSE_MAX]
        ),
        context_ids,
    )


async def run_sweep(
    source: SnapshotSource,
    *,
    session_date: date,
    clock: Clock = _utc_now,
    monotonic: Callable[[], float] = time.monotonic,
    scan_merge: ScanMerge | None = None,
    context_etfs: ContextETFs | None = None,
    day_universe: bool = False,
    held: bool = True,
) -> SweepResult:
    """Snapshot every qualified instrument once, rank, and persist the sweep.

    With ``scan_merge`` the sweep also ranks up to 15 qualified IBKR scan
    movers outside the stored universe. With ``context_etfs`` it snapshots
    the context ETFs first, in their own batch, and stores the market
    context under ``market``; those ETFs never become rows. A competing
    login or lost connection during that ranking group aborts without
    persisting: a partial sweep would rank a different universe than the
    one that qualified.

    Afterwards, in a second pass that can neither change nor abort the
    ranking group, it quotes the day universe (``day_universe``: its own
    list under ``day_universe``) and, with ``held``, the symbols the order
    bridge may hold that left the ranking group (trailing, unranked rows).
    A competing login or lost connection in that pass still persists the
    ranking group, then raises.
    """
    universe_size, instruments = await load_screener_universe(session_date)
    context_instruments: tuple[ContextInstrument, ...] = ()
    if context_etfs is not None:
        context_instruments = await context_etfs.resolve()
    context_ids = frozenset(item.con_id for item in context_instruments)
    instruments = without_context_etfs(instruments, context_ids)
    if not instruments:
        logger.warning(
            "no qualified instruments for %s; run `autotraderctl universe qualify`",
            session_date.isoformat(),
        )
    session = market_session(session_date)
    observed_at = clock()
    started = monotonic()
    etf_snapshots: dict[str, MarketSnapshot] | None = None
    if context_etfs is not None:
        etf_snapshots = await snapshot_context(source, context_instruments)
    scans: dict[str, tuple[IBKRScannerResult, ...]] = {}
    if scan_merge is not None:
        scans = await run_scans(scan_merge.source, SCAN_CODES)
        additions = without_context_etfs(
            await scan_additions(
                scan_merge,
                session_date=session_date,
                exclude=await universe_con_ids() | context_ids,
                clock=clock,
                scans=scans,
            ),
            context_ids,
        )
        instruments = instruments + additions
        universe_size += len(additions)
    # --- the hot-35 ranking group: exactly as before the day universe ---
    observations: list[Observation] = []
    errors: list[str] = []
    consecutive = 0
    for offset in range(0, len(instruments), SNAPSHOT_CHUNK_SIZE):
        chunk = instruments[offset : offset + SNAPSHOT_CHUNK_SIZE]
        results = await source.market_snapshots([item.con_id for item in chunk])
        if len(results) != len(chunk):
            raise ScreenerError("snapshot batch returned a different number of results")
        for instrument, result in zip(chunk, results):
            try:
                observation = _observe(instrument, result, session)
            except (TWSCompetingSessionError, TWSConnectionError):
                raise
            except (TWSRequestError, ScreenerError) as exc:
                errors.append(f"{instrument.symbol}: {exc}")
                logger.debug("snapshot failed for %s: %s", instrument.symbol, exc)
                consecutive += 1
                if consecutive >= MAX_CONSECUTIVE_SNAPSHOT_ERRORS:
                    raise ScreenerError(
                        f"{consecutive} consecutive snapshot failures; last: {exc}"
                    ) from exc
                continue
            consecutive = 0
            observations.append(observation)
    ranked = rank_observations(observations)
    # --- quote-only names: never ranked with the group above ---
    ranking_ids = {item.con_id for item in instruments}
    members = await day_universe_instruments(session_date, context_ids) if day_universe else ()
    held_names = (
        without_context_etfs(await held_instruments(session_date), context_ids) if held else ()
    )
    extras: dict[int, ScreenerInstrument] = {}
    for item in (*members, *held_names):
        if item.con_id not in ranking_ids:
            extras.setdefault(item.con_id, item)
    extra_pass = await _quote_extras(
        source, tuple(extras.values()), session=session, monotonic=monotonic
    )
    observed = {item.instrument.con_id: item for item in observations}
    observed.update(extra_pass.observed)
    held_only = [item for item in held_names if item.con_id not in ranking_ids]
    trailing = unranked([observed[item.con_id] for item in held_only if item.con_id in observed])
    section: DayUniverseSection | None = None
    if day_universe:
        quoted_members = [observed[item.con_id] for item in members if item.con_id in observed]
        section = DayUniverseSection(
            size=len(members),
            quoted=len(quoted_members),
            errors=len(members) - len(quoted_members),
            complete=extra_pass.complete,
            seconds=extra_pass.seconds,
            rows=tuple(day_universe_row(item) for item in rank_day_universe(quoted_members)),
        )
    payload = build_payload(
        session_date=session_date,
        observed_at=observed_at,
        sweep_seconds=monotonic() - started,
        universe_size=universe_size,
        qualified=len(instruments) + len(held_only),
        errors=len(errors) + len(held_only) - len(trailing),
        ranked=ranked,
        trailing=trailing,
        day_universe=section,
    )
    context: MarketContext | None = None
    if etf_snapshots is not None:
        context = build_market_context(
            session_date=session_date,
            observed_at=observed_at,
            etf_snapshots=etf_snapshots,
            watchlist_rows=[row.model_dump() for row in payload.rows],
        )
        payload = with_market_context(payload, context)
    snapshot_id = await persist_snapshot(payload)
    logger.info(
        "sweep %s: qualified=%d quoted=%d errors=%d hot=%d seconds=%.1f",
        session_date.isoformat(),
        payload.qualified,
        payload.quoted,
        payload.errors,
        sum(1 for row in payload.rows if row.hot),
        payload.sweep_seconds,
    )
    if section is not None or trailing:
        logger.info(
            "sweep %s: day universe quoted=%d/%d%s held-only=%d extra seconds=%.1f",
            session_date.isoformat(),
            0 if section is None else section.quoted,
            0 if section is None else section.size,
            "" if extra_pass.complete else " (incomplete)",
            len(trailing),
            extra_pass.seconds,
        )
    if context is not None:
        logger.info("market %s", context.summary)
    if errors:
        logger.warning("sweep snapshot errors (first 5): %s", "; ".join(errors[:5]))
    if extra_pass.interrupted is not None:
        # The ranking group is persisted; the connection problem still surfaces.
        raise extra_pass.interrupted
    return SweepResult(
        snapshot_id=snapshot_id,
        payload=payload,
        error_samples=tuple(errors[:10]),
        market_context=context,
        scans=scans,
    )


__all__ = [
    "CONTEXT_RESOLVE_RETRY",
    "HOT_LIST_SIZE",
    "PRIOR_CLOSE_RECHECK_TOLERANCE",
    "SCAN_CODES",
    "SCHEMA_VERSION",
    "TICK_VOLUME_MULTIPLIER",
    "UNRANKED_SCORE",
    "ContextETFs",
    "ContextInstrument",
    "DayUniverseRow",
    "DayUniverseSection",
    "HotListRow",
    "Observation",
    "PriorCloseRecheck",
    "RankedObservation",
    "RecheckSource",
    "ScanMerge",
    "ScanResults",
    "ScanSource",
    "ScreenerError",
    "ScreenerInstrument",
    "ScreenerMetrics",
    "ScreenerPayload",
    "ScreenerRow",
    "SweepResult",
    "build_payload",
    "compute_metrics",
    "day_universe_instruments",
    "day_universe_row",
    "elapsed_fraction",
    "held_instruments",
    "hotlist_rows",
    "latest_day_universe",
    "latest_hotlist",
    "latest_snapshot",
    "load_screener_universe",
    "percentile_ranks",
    "persist_snapshot",
    "price_text",
    "rank_day_universe",
    "rank_observations",
    "recheck_prior_closes",
    "run_scans",
    "run_sweep",
    "scan_additions",
    "scan_candidates",
    "snapshot_context",
    "universe_con_ids",
    "unranked",
    "utc_iso",
    "with_market_context",
    "without_context_etfs",
]

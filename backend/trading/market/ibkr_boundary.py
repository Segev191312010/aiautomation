"""IBKR market-data boundary for the evidence-gated V1 engine.

This module intentionally contains no IBKR client import and opens no network
connection.  A single connection actor owns that integration elsewhere; this
boundary validates its outputs before they reach scanner or rule code.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from functools import lru_cache
from hashlib import sha256
import json
from typing import Literal, Protocol
from zoneinfo import ZoneInfo

from trading.contracts import Candidate, CompletedBar, QuoteSnapshot


PAPER_TWS_PORT = 7497
MAX_ADMITTED_CANDIDATES = 50
DEFAULT_BAR_MAX_AGE = timedelta(seconds=30)
# Outside the regular session a thin name may not trade for minutes, so the
# newest extended-hours bar may be this old and still be current evidence.
EXTENDED_HOURS_BAR_MAX_AGE = timedelta(minutes=15)
LIQUIDITY_LOOKBACK_SESSIONS = 20
# Most recent prior session may be at most this old (covers long holiday weekends).
MAX_DAILY_HISTORY_GAP = timedelta(days=5)
_NEW_YORK = ZoneInfo("America/New_York")
_BAR_INTERVAL = timedelta(minutes=5)


class MarketDataBoundaryError(ValueError):
    """Raised when market data cannot be admitted to the V1 pipeline."""


class ConnectionConfigurationError(MarketDataBoundaryError):
    """Raised when the adapter is not pointed at the PAPER TWS port."""


class ScannerSupportError(MarketDataBoundaryError):
    """Raised when TWS does not support the frozen scanner request."""


class MarketDataQualityError(MarketDataBoundaryError):
    """Raised when data is delayed, incomplete, stale, or otherwise unsafe."""


class ScannerDataError(MarketDataBoundaryError):
    """Raised when scanner identity or ranking cannot be trusted."""


@dataclass(frozen=True)
class ScannerRequest:
    """The only scanner request admitted by the V1 data boundary."""

    instrument: Literal["STK"] = "STK"
    location: Literal["STK.US.MAJOR"] = "STK.US.MAJOR"
    scan_code: Literal["HOT_BY_VOLUME"] = "HOT_BY_VOLUME"
    stock_type_filter: Literal["COMMON"] = "COMMON"
    number_of_rows: int = MAX_ADMITTED_CANDIDATES
    # Server-side price band matching ORB's $10-$300 rule. Without it the
    # 2026-10-12 live scan returned 25 of 26 admitted rows under $10.
    above_price: Decimal = Decimal("10")
    below_price: Decimal = Decimal("300")

    def __post_init__(self) -> None:
        if self.number_of_rows != MAX_ADMITTED_CANDIDATES:
            raise ValueError("the V1 scanner request must ask for exactly 50 rows")
        if (self.above_price, self.below_price) != (Decimal("10"), Decimal("300")):
            raise ValueError("the V1 scanner price band is fixed at $10-$300")


DEFAULT_SCANNER_REQUEST = ScannerRequest()


@dataclass(frozen=True)
class IBKRScannerResult:
    """Scanner identity returned by the connection actor.

    ``rank`` is one-based; ``raw_rank`` keeps IBKR's zero-based source rank when
    the actor supplied it. Missing identity fields arrive as empty strings so
    the boundary can reject the row instead of the whole scan.
    """

    con_id: int
    symbol: str
    currency: str
    security_type: str
    stock_type: str
    primary_exchange: str
    rank: int
    raw_rank: int | None = None
    exchange: str = ""


@dataclass(frozen=True)
class IBKRContractDetails:
    """Qualified contract facts needed before a scanner row becomes a candidate."""

    con_id: int
    symbol: str
    currency: str
    security_type: str
    stock_type: str
    primary_exchange: str
    min_tick: Decimal


@dataclass(frozen=True)
class ScannerRecord:
    """One scanner result retained for persistence and admission auditing."""

    source: IBKRScannerResult
    candidate: Candidate | None
    admitted: bool
    rejection_reason: str | None = None


@dataclass(frozen=True)
class ScannerSnapshot:
    """All raw-ranked results plus the bounded admitted candidate set."""

    request: ScannerRequest
    records: tuple[ScannerRecord, ...]
    admitted_candidates: tuple[Candidate, ...]

    def __post_init__(self) -> None:
        if len(self.admitted_candidates) > MAX_ADMITTED_CANDIDATES:
            raise ValueError("scanner admitted more than 50 candidates")

    @property
    def raw_results(self) -> tuple[IBKRScannerResult, ...]:
        return tuple(record.source for record in self.records)


@dataclass(frozen=True)
class IBKRTradeBar:
    """Raw completed-bar event from the one connection actor."""

    con_id: int
    interval_start_utc: datetime
    interval_end_utc: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    rth: bool
    bar_type: Literal["TRADES"] = "TRADES"
    delayed: bool = False
    frozen: bool = False
    complete: bool = True
    received_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class BarBatch:
    """Data and transport health returned by the connection actor."""

    bars: tuple[IBKRTradeBar, ...]
    connected: bool = True
    pacing_failed: bool = False
    error: str | None = None


@dataclass(frozen=True)
class IBKRDailyBar:
    """One regular-session daily TRADES bar; ``volume`` is in the API's raw unit."""

    con_id: int
    session_date: date
    close: Decimal
    volume: Decimal


@dataclass(frozen=True)
class DailyBarBatch:
    bars: tuple[IBKRDailyBar, ...]
    connected: bool = True
    pacing_failed: bool = False
    error: str | None = None


@dataclass(frozen=True)
class LiquidityProfile:
    """Prior-session price and 20-session average dollar volume for qualification."""

    con_id: int
    reference_price: Decimal
    average_dollar_volume: Decimal
    sessions_used: int
    last_session_date: date


class IBKRConnectionActor(Protocol):
    """The sole integration seam for request IDs, callbacks, and reconnects."""

    async def supports_scanner(self, request: ScannerRequest) -> bool:
        """Return whether TWS supports the frozen request at this session."""

    async def scan(self, request: ScannerRequest) -> Sequence[IBKRScannerResult]:
        """Return source-ranked scanner results without changing the request."""

    async def completed_trade_bars(self, con_id: int) -> BarBatch:
        """Return only the recent five-minute bar batch for one contract."""

    async def extended_trade_bars(self, con_id: int) -> BarBatch:
        """Return today's five-minute bars including pre-market and after-hours.

        Each bar's ``rth`` flag comes from the XNYS calendar, so pre-market
        bars are distinguishable from regular-session bars.
        """

    async def contract_details(self, con_id: int) -> IBKRContractDetails:
        """Return qualified contract details for one conId."""

    async def daily_trade_bars(self, con_id: int) -> DailyBarBatch:
        """Return recent regular-session daily TRADES bars for one contract."""

    async def quote_snapshot(self, con_id: int, *, min_tick: Decimal) -> QuoteSnapshot:
        """Return a confirmed real-time bid/ask for one contract."""


def _require_aware(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise MarketDataQualityError(f"{field_name} must be timezone-aware")
    return value.astimezone(timezone.utc)


@lru_cache(maxsize=64)
def regular_session_bounds(session_date: date) -> tuple[datetime, datetime] | None:
    """XNYS regular-session ``(open, close)`` in UTC; ``None`` on a closed date.

    The close is the scheduled one, so a half day ends at 13:00 ET.
    """
    from trading.scheduler.sessions import market_session

    session = market_session(session_date)
    if session is None:
        return None
    return session.opens_at.astimezone(timezone.utc), session.closes_at.astimezone(timezone.utc)


def is_regular_session_bar(start: datetime, end: datetime) -> bool:
    """Whether ``[start, end)`` lies wholly inside its New York date's regular session.

    Derived from the pinned calendar rather than trusted from the request, so
    pre-market, after-hours, holiday and post-half-day bars are never RTH.
    """
    start_utc = _require_aware(start, "interval_start_utc")
    end_utc = _require_aware(end, "interval_end_utc")
    bounds = regular_session_bounds(start_utc.astimezone(_NEW_YORK).date())
    if bounds is None:
        return False
    opens_at, closes_at = bounds
    return opens_at <= start_utc and end_utc <= closes_at


def extended_hours_bar_max_age(observed_at: datetime, *, regular_max_age: timedelta) -> timedelta:
    """Freshness allowance for the newest bar of an extended-hours batch.

    The strict regular-session rule applies once the session's first bar has
    completed and until the close; before that (pre-market, including the
    still-forming 09:30 bar) and after it, the newest bar may be minutes old.
    """
    observed = _require_aware(observed_at, "now")
    bounds = regular_session_bounds(observed.astimezone(_NEW_YORK).date())
    if bounds is not None:
        opens_at, closes_at = bounds
        if opens_at + _BAR_INTERVAL <= observed <= closes_at:
            return regular_max_age
    return max(regular_max_age, EXTENDED_HOURS_BAR_MAX_AGE)


def _scanner_identity_rejection(result: IBKRScannerResult) -> str | None:
    """Return a stable rejection code for one unusable row, or None."""
    if result.con_id <= 0:
        return "invalid_con_id"
    if not result.symbol.strip():
        return "empty_symbol"
    if result.currency.strip().upper() != "USD":
        return "not_usd_contract"
    if result.security_type.strip().upper() != "STK":
        return "not_stock_contract"
    if result.stock_type.strip().upper() != "COMMON":
        return "not_common_stock"
    if not result.primary_exchange.strip():
        return "missing_primary_exchange"
    return None


def qualify_scanner_results(
    results: Sequence[IBKRScannerResult],
    *,
    scanner_run_id: str,
    request: ScannerRequest = DEFAULT_SCANNER_REQUEST,
    unqualified_con_ids: frozenset[int] = frozenset(),
) -> ScannerSnapshot:
    """Retain every source result while admitting at most 50 valid candidates."""
    if not scanner_run_id.strip():
        raise ScannerDataError("scanner_run_id must be non-empty")
    if request != DEFAULT_SCANNER_REQUEST:
        raise ScannerDataError("scanner request is not the frozen V1 request")

    ordered = sorted(results, key=lambda result: (result.rank, result.con_id))
    seen_con_ids: set[int] = set()
    seen_ranks: set[int] = set()
    records: list[ScannerRecord] = []
    admitted: list[Candidate] = []

    for result in ordered:
        # Rank identity is the integrity of the whole scan: a broken ranking
        # cannot be repaired row by row, so it still fails the snapshot.
        if result.rank < 1:
            raise ScannerDataError("scanner rank must be positive")
        if result.raw_rank is not None and result.rank != result.raw_rank + 1:
            raise ScannerDataError("scanner rank must equal the raw IBKR rank plus one")
        if result.rank in seen_ranks:
            raise ScannerDataError(f"duplicate scanner rank: {result.rank}")
        seen_ranks.add(result.rank)

        if result.con_id > 0 and result.con_id in unqualified_con_ids:
            rejection: str | None = "contract_details_unavailable"
        else:
            rejection = _scanner_identity_rejection(result)
        if rejection is None and result.con_id in seen_con_ids:
            rejection = "duplicate_con_id"
        if rejection is not None:
            records.append(
                ScannerRecord(
                    source=result,
                    candidate=None,
                    admitted=False,
                    rejection_reason=rejection,
                )
            )
            continue
        seen_con_ids.add(result.con_id)

        if result.rank > MAX_ADMITTED_CANDIDATES or len(admitted) >= MAX_ADMITTED_CANDIDATES:
            records.append(
                ScannerRecord(
                    source=result,
                    candidate=None,
                    admitted=False,
                    rejection_reason="admission_cap_reached",
                )
            )
            continue

        candidate = Candidate(
            scanner_run_id=scanner_run_id,
            con_id=result.con_id,
            symbol=result.symbol,
            currency="USD",
            security_type="STK",
            stock_type="COMMON",
            primary_exchange=result.primary_exchange.strip().upper(),
            rank=result.rank,
            admission_status="admitted",
        )
        admitted.append(candidate)
        records.append(ScannerRecord(source=result, candidate=candidate, admitted=True))

    return ScannerSnapshot(
        request=request,
        records=tuple(records),
        admitted_candidates=tuple(admitted),
    )


def _bar_fingerprint(bar: IBKRTradeBar) -> str:
    payload = {
        "con_id": bar.con_id,
        "interval_start_utc": _require_aware(bar.interval_start_utc, "interval_start_utc").isoformat(),
        "interval_end_utc": _require_aware(bar.interval_end_utc, "interval_end_utc").isoformat(),
        "open": str(bar.open),
        "high": str(bar.high),
        "low": str(bar.low),
        "close": str(bar.close),
        "volume": str(bar.volume),
        "rth": bar.rth,
    }
    return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def validate_completed_trade_bars(
    batch: BarBatch,
    *,
    now: datetime,
    max_age: timedelta = DEFAULT_BAR_MAX_AGE,
) -> tuple[CompletedBar, ...]:
    """Convert a healthy live TRADES batch into strictly completed five-minute bars."""
    observed_at = _require_aware(now, "now")
    if max_age <= timedelta(0):
        raise ValueError("max_age must be positive")
    if not batch.connected:
        raise MarketDataQualityError("IBKR market-data connection is unavailable")
    if batch.pacing_failed:
        raise MarketDataQualityError("IBKR market-data pacing limit blocked the request")
    if batch.error:
        raise MarketDataQualityError("IBKR market-data request failed")

    validated: list[CompletedBar] = []
    fingerprints: set[str] = set()
    intervals: set[tuple[int, datetime]] = set()
    latest_end: datetime | None = None
    for raw in batch.bars:
        start = _require_aware(raw.interval_start_utc, "interval_start_utc")
        end = _require_aware(raw.interval_end_utc, "interval_end_utc")
        received_at = _require_aware(raw.received_at, "received_at")
        if raw.bar_type != "TRADES":
            raise MarketDataQualityError("only completed TRADES bars are admissible")
        if raw.delayed:
            raise MarketDataQualityError("delayed market data cannot feed a candidate")
        if raw.frozen:
            raise MarketDataQualityError("frozen market data cannot feed a candidate")
        if not raw.complete:
            raise MarketDataQualityError("incomplete bars cannot feed a candidate")
        if end > observed_at:
            # IBKR includes the still-forming interval when the request ends "now";
            # it is not completed evidence and is dropped, never admitted.
            continue
        latest_end = end if latest_end is None or end > latest_end else latest_end
        if received_at > observed_at + timedelta(seconds=1):
            raise MarketDataQualityError("bar receipt timestamp is in the future")
        if raw.con_id <= 0:
            raise MarketDataQualityError("bar has an invalid conId")

        fingerprint = _bar_fingerprint(raw)
        interval_identity = (raw.con_id, start)
        if fingerprint in fingerprints or interval_identity in intervals:
            raise MarketDataQualityError("duplicate market bar cannot feed a candidate")
        fingerprints.add(fingerprint)
        intervals.add(interval_identity)

        try:
            validated.append(
                CompletedBar(
                    con_id=raw.con_id,
                    interval_start_utc=start,
                    interval_end_utc=end,
                    open=raw.open,
                    high=raw.high,
                    low=raw.low,
                    close=raw.close,
                    volume=raw.volume,
                    rth=raw.rth,
                    source="ibkr",
                    data_mode="live",
                    completed=True,
                )
            )
        except ValueError as exc:
            raise MarketDataQualityError("bar failed the five-minute OHLC validation") from exc
    # Older bars are legitimate history (ATR, opening range); freshness is a
    # property of the newest completed interval only.
    if latest_end is not None and observed_at - latest_end > max_age:
        raise MarketDataQualityError("stale market data cannot feed a candidate")
    return tuple(validated)


def liquidity_profile(
    batch: DailyBarBatch,
    *,
    session_date: date,
    volume_multiplier: Decimal,
    lookback: int = LIQUIDITY_LOOKBACK_SESSIONS,
) -> LiquidityProfile:
    """Average dollar volume over the prior ``lookback`` completed sessions.

    Today's daily bar is still forming during the session and is excluded.
    """
    if not volume_multiplier.is_finite() or volume_multiplier <= 0:
        raise ValueError("volume_multiplier must be finite and positive")
    if not batch.connected:
        raise MarketDataQualityError("IBKR market-data connection is unavailable")
    if batch.pacing_failed:
        raise MarketDataQualityError("IBKR market-data pacing limit blocked the request")
    if batch.error:
        raise MarketDataQualityError("IBKR market-data request failed")
    con_ids = {bar.con_id for bar in batch.bars}
    if len(con_ids) > 1:
        raise MarketDataQualityError("daily history contains multiple contracts")
    prior = sorted(
        (bar for bar in batch.bars if bar.session_date < session_date),
        key=lambda bar: bar.session_date,
    )
    if len({bar.session_date for bar in prior}) != len(prior):
        raise MarketDataQualityError("duplicate daily bar cannot feed a candidate")
    if len(prior) < lookback:
        raise MarketDataQualityError("insufficient_daily_history")
    window = prior[-lookback:]
    if any(not bar.close.is_finite() or bar.close <= 0 for bar in window):
        raise MarketDataQualityError("daily bar has a non-positive close")
    if any(not bar.volume.is_finite() or bar.volume < 0 for bar in window):
        raise MarketDataQualityError("daily bar has an invalid volume")
    last = window[-1]
    if session_date - last.session_date > MAX_DAILY_HISTORY_GAP:
        raise MarketDataQualityError("stale daily history cannot feed a candidate")
    total = sum((bar.close * bar.volume * volume_multiplier for bar in window), Decimal("0"))
    return LiquidityProfile(
        con_id=last.con_id,
        reference_price=last.close,
        average_dollar_volume=total / Decimal(lookback),
        sessions_used=lookback,
        last_session_date=last.session_date,
    )


@dataclass
class PaperIBKRMarketData:
    """Validated PAPER data facade backed by one caller-owned connection actor."""

    actor: IBKRConnectionActor
    host: str
    port: int = PAPER_TWS_PORT
    bar_max_age: timedelta = DEFAULT_BAR_MAX_AGE
    # Raw API volume unit for US stocks; confirmed against TWS by the probe.
    volume_multiplier: Decimal = Decimal("1")
    _scanner_ready: bool = field(default=False, init=False, repr=False)
    _contract_details: dict[int, IBKRContractDetails] = field(
        default_factory=dict, init=False, repr=False
    )

    def __post_init__(self) -> None:
        if not self.host.strip():
            raise ConnectionConfigurationError("IBKR host must be non-empty")
        if self.port != PAPER_TWS_PORT:
            raise ConnectionConfigurationError(
                f"only TWS PAPER port {PAPER_TWS_PORT} is supported by the V1 data boundary"
            )
        if self.bar_max_age <= timedelta(0):
            raise ConnectionConfigurationError("bar_max_age must be positive")
        if not self.volume_multiplier.is_finite() or self.volume_multiplier <= 0:
            raise ConnectionConfigurationError("volume_multiplier must be positive")

    async def initialize(self) -> None:
        """Verify scanner support before allowing a session to request data."""
        supported = await self.actor.supports_scanner(DEFAULT_SCANNER_REQUEST)
        if not supported:
            raise ScannerSupportError("TWS does not support the frozen HOT_BY_VOLUME scanner request")
        self._scanner_ready = True

    async def scanner_snapshot(self, *, scanner_run_id: str) -> ScannerSnapshot:
        if not self._scanner_ready:
            await self.initialize()
        results = await self.actor.scan(DEFAULT_SCANNER_REQUEST)
        qualified: list[IBKRScannerResult] = []
        unqualified: set[int] = set()
        for result in results:
            enriched = await self._qualify_contract(result)
            if enriched is None:
                unqualified.add(result.con_id)
                qualified.append(result)
            else:
                qualified.append(enriched)
        return qualify_scanner_results(
            qualified,
            scanner_run_id=scanner_run_id,
            unqualified_con_ids=frozenset(unqualified),
        )

    async def load_contract(self, con_id: int) -> IBKRContractDetails:
        """Qualify one known conId (e.g. from the watchlist universe) for quotes."""
        cached = self._contract_details.get(con_id)
        if cached is not None:
            return cached
        details = await self.actor.contract_details(con_id)
        if details.con_id != con_id or not details.min_tick.is_finite() or details.min_tick <= 0:
            raise MarketDataQualityError("contract details do not match the requested conId")
        self._contract_details[con_id] = details
        return details

    def min_tick(self, con_id: int) -> Decimal:
        """Minimum price increment from the contract details of a scanned row."""
        details = self._contract_details.get(con_id)
        if details is None:
            raise MarketDataQualityError("contract details were not loaded for this conId")
        return details.min_tick

    async def _qualify_contract(self, result: IBKRScannerResult) -> IBKRScannerResult | None:
        """Fill exchange and stock type from contract details; never invent identity.

        Scanner rows frequently omit ``primaryExchange``. The frozen request's
        COMMON filter stands when TWS omits ``stockType`` in both places; any
        explicit non-COMMON type still rejects the row downstream.
        """
        if result.con_id <= 0:
            return result
        try:
            details = await self.actor.contract_details(result.con_id)
        except Exception:
            return None
        if (
            details.con_id != result.con_id
            or not details.min_tick.is_finite()
            or details.min_tick <= 0
        ):
            return None
        self._contract_details[result.con_id] = details
        stock_type = (
            details.stock_type.strip()
            or result.stock_type.strip()
            or DEFAULT_SCANNER_REQUEST.stock_type_filter
        )
        return replace(
            result,
            stock_type=stock_type,
            primary_exchange=details.primary_exchange.strip() or result.primary_exchange.strip(),
            currency=details.currency.strip() or result.currency,
            security_type=details.security_type.strip() or result.security_type,
        )

    async def completed_bars(
        self,
        *,
        con_id: int,
        now: datetime,
    ) -> tuple[CompletedBar, ...]:
        if con_id <= 0:
            raise MarketDataQualityError("conId must be positive")
        batch = await self.actor.completed_trade_bars(con_id)
        # ``now`` is taken before the request; a slow reply (seen live with 35
        # sequential requests) delivers bars after it. The observation moment
        # is when the batch arrived, so receipt time is never "in the future".
        received = [_require_aware(bar.received_at, "received_at") for bar in batch.bars]
        observed_at = max([_require_aware(now, "now"), *received])
        return validate_completed_trade_bars(batch, now=observed_at, max_age=self.bar_max_age)

    async def extended_bars(
        self,
        *,
        con_id: int,
        now: datetime,
    ) -> tuple[CompletedBar, ...]:
        """Today's completed bars including pre-market, validated like ``completed_bars``.

        Observed at receipt and with the forming bar dropped; only the
        freshness allowance differs (``extended_hours_bar_max_age``).
        """
        if con_id <= 0:
            raise MarketDataQualityError("conId must be positive")
        batch = await self.actor.extended_trade_bars(con_id)
        if any(bar.con_id != con_id for bar in batch.bars):
            raise MarketDataQualityError("extended-hours bars returned for a different contract")
        received = [_require_aware(bar.received_at, "received_at") for bar in batch.bars]
        observed_at = max([_require_aware(now, "now"), *received])
        max_age = extended_hours_bar_max_age(observed_at, regular_max_age=self.bar_max_age)
        return validate_completed_trade_bars(batch, now=observed_at, max_age=max_age)

    async def quote(self, *, con_id: int) -> QuoteSnapshot:
        """Real-time quote priced on the contract's own tick grid."""
        if con_id <= 0:
            raise MarketDataQualityError("conId must be positive")
        quote = await self.actor.quote_snapshot(con_id, min_tick=self.min_tick(con_id))
        if quote.con_id != con_id:
            raise MarketDataQualityError("quote returned for a different contract")
        return quote

    async def liquidity(self, *, con_id: int, session_date: date) -> LiquidityProfile:
        if con_id <= 0:
            raise MarketDataQualityError("conId must be positive")
        batch = await self.actor.daily_trade_bars(con_id)
        if any(bar.con_id != con_id for bar in batch.bars):
            raise MarketDataQualityError("daily history returned for a different contract")
        return liquidity_profile(
            batch, session_date=session_date, volume_multiplier=self.volume_multiplier
        )

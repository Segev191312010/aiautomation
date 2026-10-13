"""The day universe: IBKR scan names for the day manager and the simulations only.

Every US common stock the screener's scans surface is admitted once per
session: IBKR contract details first (stock type, industry, category), then
the same daily price/liquidity qualification as the watchlist. Admitted
names live in ``autotrader_manager_day_universe`` for that session only (at
most ``DAY_UNIVERSE_MAX``), the screener quotes every one of them every
sweep, and ranks them in their own list under the payload's
``day_universe`` key.

Principle 1 (the real MOMO's inputs do not change): day-universe names are
never part of the hot-35 ranking group or the watchlist breadth. The stock
type rejections recorded here never reach ``autotrader_universe_daily``,
which the hot-35's own scan additions read.

Admission spends paced IBKR requests, so the screener runs it after a sweep
was persisted, in the idle rest of the sweep interval, and it stops before
the next sweep is due: no sweep is ever longer for it.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence, Set
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
import json
import logging
import re
import time
from typing import Protocol

from db.core import get_db, transaction
from trading.market.ibkr_boundary import IBKRContractDetails, IBKRScannerResult
from trading.market.tws_actor import (
    TWSCompetingSessionError,
    TWSConnectionError,
    TWSRequestError,
)
from trading.market.universe import (
    ContractFacts,
    DailyHistorySource,
    RequestPacer,
    load_daily,
    qualify_contract,
    record_contract_facts,
    stock_type_rejection,
)
from trading.scheduler.sessions import market_session

logger = logging.getLogger("autotrader.shadow.screener")

DAY_UNIVERSE_MAX = 150
# Before the regular open at most this many names are admitted, so the
# session's own movers still find room after the pre-market gappers.
DAY_UNIVERSE_PREMARKET_MAX = 60
# New names one sweep's idle time may admit (each costs one contract-details
# request and, unless already qualified today, one paced history request).
SCAN_ADDITIONS_PER_SWEEP = 40
# One admission at worst-typical cost: details (~0.1 s) plus a history request
# behind the 1 req/s pacer. A name is only started with this much time left.
ADMISSION_SECONDS_PER_NAME = 1.5
# Admission stops this long before the next sweep is due.
ADMISSION_MARGIN_SECONDS = 5.0
# Reasons are snake_case tokens (the table enforces it).
REASON_CONTRACT_MISMATCH = "contract_details_mismatch"
# The screener row contract's symbol limit (ScreenerRow, DayUniverseRow).
MAX_SYMBOL_LENGTH = 16
_REASON_LIMIT = 64
_NON_TOKEN = re.compile(r"[^a-z0-9]+")

Clock = Callable[[], datetime]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


def reason_token(reason: str | None) -> str:
    """A qualification reason as the table's snake_case token.

    ``stock_type_ETF`` becomes ``stock_type_etf`` and a sentence such as
    ``stale daily history cannot feed a candidate`` its underscored form.
    """
    token = _NON_TOKEN.sub("_", (reason or "").lower()).strip("_")[:_REASON_LIMIT].strip("_")
    if not token or not token[0].isalpha():
        token = f"rejected_{token}"[:_REASON_LIMIT].strip("_")
    return token


def size_limit(session_date: date, now: datetime) -> int:
    """``DAY_UNIVERSE_PREMARKET_MAX`` before the session opens, else ``DAY_UNIVERSE_MAX``."""
    session = market_session(session_date)
    if session is not None and now < session.opens_at:
        return DAY_UNIVERSE_PREMARKET_MAX
    return DAY_UNIVERSE_MAX


# --- scan sightings ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ScanSighting:
    """One usable scan contract with its best rank and every scan that listed it."""

    con_id: int
    symbol: str
    primary_exchange: str | None
    best_rank: int
    scan_codes: tuple[str, ...]


def scan_sightings(
    scans: Mapping[str, Sequence[IBKRScannerResult]], *, exclude: Set[int]
) -> tuple[ScanSighting, ...]:
    """Usable rows (the screener's own filters), best rank first.

    Ties on rank go to the earlier scan, then the lower conId, exactly as the
    hot-35's ``scan_candidates`` orders them.
    """
    best: dict[int, tuple[int, int, IBKRScannerResult]] = {}
    codes: dict[int, set[str]] = {}
    for scan_index, (code, results) in enumerate(scans.items()):
        for result in results:
            if result.con_id <= 0 or result.con_id in exclude or not result.symbol.strip():
                continue
            if len(result.symbol.strip()) > MAX_SYMBOL_LENGTH:
                continue
            if result.currency.strip().upper() != "USD":
                continue
            if result.security_type.strip().upper() != "STK":
                continue
            codes.setdefault(result.con_id, set()).add(code)
            current = best.get(result.con_id)
            if current is None or (result.rank, scan_index) < current[:2]:
                best[result.con_id] = (result.rank, scan_index, result)
    ordered = sorted(best.values(), key=lambda item: (item[0], item[1], item[2].con_id))
    return tuple(
        ScanSighting(
            con_id=result.con_id,
            symbol=result.symbol.strip(),
            primary_exchange=result.primary_exchange.strip().upper()[:16] or None,
            best_rank=rank,
            scan_codes=tuple(sorted(codes[result.con_id])),
        )
        for rank, _index, result in ordered
    )


# --- persistence -------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DayUniverseName:
    """One stored name of a session's day universe."""

    session_date: date
    con_id: int
    symbol: str
    primary_exchange: str | None
    first_seen: datetime
    last_seen: datetime
    sources: tuple[str, ...]
    best_rank: int | None
    qualified: bool
    reason: str | None


@dataclass(frozen=True, slots=True)
class DayUniverseMember:
    """A qualified name with today's volume baseline, as the sweep quotes it."""

    con_id: int
    symbol: str
    primary_exchange: str | None
    avg_daily_volume_shares: Decimal | None
    best_rank: int | None


_SELECT_NAMES = """SELECT session_date, con_id, symbol, primary_exchange, first_seen, last_seen,
           sources_json, best_rank, qualified, reason
    FROM autotrader_manager_day_universe WHERE session_date = ?
    ORDER BY best_rank IS NULL, best_rank, symbol, con_id"""

_INSERT_NAME = """INSERT INTO autotrader_manager_day_universe (
        session_date, con_id, symbol, primary_exchange, first_seen, last_seen,
        sources_json, best_rank, qualified, reason
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(session_date, con_id) DO NOTHING"""

# last_seen only moves forward (a trigger refuses anything else) and the
# best rank only improves.
_UPDATE_SIGHTING = """UPDATE autotrader_manager_day_universe
    SET last_seen = CASE WHEN julianday(?) > julianday(last_seen) THEN ? ELSE last_seen END,
        best_rank = CASE WHEN best_rank IS NULL OR ? < best_rank THEN ? ELSE best_rank END,
        sources_json = ?
    WHERE session_date = ? AND con_id = ?"""


def _sources(value: object) -> tuple[str, ...]:
    try:
        parsed = json.loads(str(value))
    except ValueError:
        return ()
    return tuple(str(item) for item in parsed) if isinstance(parsed, list) else ()


async def load_day_universe(session_date: date) -> tuple[DayUniverseName, ...]:
    """Every stored name of the session, qualified or not, best rank first."""
    async with get_db() as db:
        async with db.execute(_SELECT_NAMES, (session_date.isoformat(),)) as cursor:
            raw_rows = await cursor.fetchall()
    return tuple(
        DayUniverseName(
            session_date=date.fromisoformat(str(raw[0])),
            con_id=int(str(raw[1])),
            symbol=str(raw[2]),
            primary_exchange=None if raw[3] is None else str(raw[3]),
            first_seen=datetime.fromisoformat(str(raw[4])),
            last_seen=datetime.fromisoformat(str(raw[5])),
            sources=_sources(raw[6]),
            best_rank=None if raw[7] is None else int(str(raw[7])),
            qualified=bool(raw[8]),
            reason=None if raw[9] is None else str(raw[9]),
        )
        for raw in raw_rows
    )


async def load_day_universe_members(session_date: date) -> tuple[DayUniverseMember, ...]:
    """The session's qualified names whose daily qualification still stands."""
    async with get_db() as db:
        async with db.execute(
            """SELECT du.con_id, du.symbol, du.primary_exchange, d.avg_daily_volume_shares,
                      du.best_rank
               FROM autotrader_manager_day_universe du
               JOIN autotrader_universe_daily d
                 ON d.session_date = du.session_date AND d.con_id = du.con_id
               WHERE du.session_date = ? AND du.qualified = 1 AND d.qualified = 1
               ORDER BY du.best_rank IS NULL, du.best_rank, du.symbol, du.con_id""",
            (session_date.isoformat(),),
        ) as cursor:
            raw_rows = await cursor.fetchall()
    return tuple(
        DayUniverseMember(
            con_id=int(str(raw[0])),
            symbol=str(raw[1]),
            primary_exchange=None if raw[2] is None else str(raw[2]),
            avg_daily_volume_shares=None if raw[3] is None else Decimal(str(raw[3])),
            best_rank=None if raw[4] is None else int(str(raw[4])),
        )
        for raw in raw_rows
    )


async def _insert_name(
    session_date: date,
    sighting: ScanSighting,
    *,
    now: datetime,
    qualified: bool,
    reason: str | None,
    symbol: str | None = None,
    primary_exchange: str | None = None,
) -> None:
    stamp = _timestamp(now)
    async with transaction() as db:
        await db.execute(
            _INSERT_NAME,
            (
                session_date.isoformat(),
                sighting.con_id,
                (symbol or sighting.symbol)[:32],
                primary_exchange or sighting.primary_exchange,
                stamp,
                stamp,
                json.dumps(list(sighting.scan_codes)),
                sighting.best_rank,
                1 if qualified else 0,
                None if qualified else reason_token(reason),
            ),
        )


async def record_sightings(
    session_date: date,
    sightings: Iterable[ScanSighting],
    known: Mapping[int, DayUniverseName],
    *,
    now: datetime,
) -> int:
    """Refresh last_seen, best rank and scan codes of names already stored."""
    updates = [
        (sighting, known[sighting.con_id]) for sighting in sightings if sighting.con_id in known
    ]
    if not updates:
        return 0
    stamp = _timestamp(now)
    async with transaction() as db:
        for sighting, name in updates:
            sources = sorted({*name.sources, *sighting.scan_codes})
            await db.execute(
                _UPDATE_SIGHTING,
                (
                    stamp,
                    stamp,
                    sighting.best_rank,
                    sighting.best_rank,
                    json.dumps(sources),
                    session_date.isoformat(),
                    sighting.con_id,
                ),
            )
    return len(updates)


# --- admission ---------------------------------------------------------------------------------


class AdmissionSource(DailyHistorySource, Protocol):
    async def contract_details(self, con_id: int) -> IBKRContractDetails: ...


@dataclass(frozen=True, slots=True)
class AdmissionReport:
    seen: int = 0
    refreshed: int = 0
    admitted: int = 0
    rejected: int = 0
    deferred: int = 0
    failed: int = 0
    size: int = 0
    limit: int = DAY_UNIVERSE_MAX

    def summary(self) -> str:
        return (
            f"day universe {self.size}/{self.limit}: +{self.admitted} admitted,"
            f" {self.rejected} rejected, {self.deferred} deferred, {self.failed} failed"
            f" ({self.seen} sighted, {self.refreshed} refreshed)"
        )


def _details_problem(details: IBKRContractDetails, sighting: ScanSighting) -> str | None:
    if details.con_id != sighting.con_id:
        return REASON_CONTRACT_MISMATCH
    if details.currency.strip().upper() != "USD" or details.security_type.strip().upper() != "STK":
        return REASON_CONTRACT_MISMATCH
    if details.symbol.strip().upper() != sighting.symbol.strip().upper():
        return REASON_CONTRACT_MISMATCH
    return None


async def admit_scan_names(
    source: AdmissionSource,
    *,
    session_date: date,
    sightings: Sequence[ScanSighting],
    volume_multiplier: Decimal,
    history_pacer: RequestPacer,
    details_pacer: RequestPacer | None = None,
    clock: Clock = _utc_now,
    monotonic: Callable[[], float] = time.monotonic,
    deadline: float | None = None,
    max_admissions: int = SCAN_ADDITIONS_PER_SWEEP,
    max_size: int | None = None,
) -> AdmissionReport:
    """Refresh known names, then admit new ones best rank first.

    Stops at ``max_admissions`` new names, at the session's size limit, or
    when less than ``ADMISSION_SECONDS_PER_NAME`` remains before
    ``deadline`` (``monotonic`` seconds). A request error leaves the name for
    a later sighting; a competing login or lost connection raises, as in the
    sweep. Every outcome but a transient one is final for the session.
    """
    now = clock()
    stored = await load_day_universe(session_date)
    known = {name.con_id: name for name in stored}
    refreshed = await record_sightings(session_date, sightings, known, now=now)
    size = sum(1 for name in stored if name.qualified)
    limit = max_size if max_size is not None else size_limit(session_date, now)
    daily = {row.con_id: row for row in await load_daily(session_date)}
    started = admitted = rejected = deferred = failed = 0
    for sighting in sightings:
        if sighting.con_id in known:
            continue
        out_of_time = deadline is not None and monotonic() + ADMISSION_SECONDS_PER_NAME > deadline
        if started >= max_admissions or size >= limit or out_of_time:
            deferred += 1
            continue
        started += 1
        if details_pacer is not None:
            await details_pacer.wait()
        try:
            details = await source.contract_details(sighting.con_id)
        except (TWSCompetingSessionError, TWSConnectionError):
            raise
        except TWSRequestError as exc:
            failed += 1
            logger.warning("day universe: contract details of %s failed: %s", sighting.symbol, exc)
            continue
        problem = _details_problem(details, sighting)
        if problem is not None:
            await _insert_name(session_date, sighting, now=clock(), qualified=False, reason=problem)
            rejected += 1
            continue
        facts = ContractFacts.from_details(details, symbol=sighting.symbol)
        try:
            await record_contract_facts([facts], source="day_universe", now=clock())
        except Exception:  # noqa: BLE001 - metadata never blocks an admission
            logger.exception("day universe: contract facts of %s were not recorded", sighting.symbol)
        exchange = facts.primary_exchange or sighting.primary_exchange
        type_reason = stock_type_rejection(details.stock_type)
        if type_reason is not None:
            # Recorded here only: the hot-35's scan additions never see it.
            await _insert_name(
                session_date,
                sighting,
                now=clock(),
                qualified=False,
                reason=type_reason,
                primary_exchange=exchange,
            )
            rejected += 1
            continue
        row = daily.get(sighting.con_id)
        if row is None or row.transient:
            row = await qualify_contract(
                source,
                con_id=sighting.con_id,
                symbol=sighting.symbol,
                session_date=session_date,
                volume_multiplier=volume_multiplier,
                clock=clock,
                pacer=history_pacer,
            )
            daily[sighting.con_id] = row
        if row.transient:
            failed += 1
            continue
        await _insert_name(
            session_date,
            sighting,
            now=clock(),
            qualified=row.qualified,
            reason=row.reason,
            primary_exchange=exchange,
        )
        if row.qualified:
            size += 1
            admitted += 1
        else:
            rejected += 1
    return AdmissionReport(
        seen=len(sightings),
        refreshed=refreshed,
        admitted=admitted,
        rejected=rejected,
        deferred=deferred,
        failed=failed,
        size=size,
        limit=limit,
    )


__all__ = [
    "ADMISSION_MARGIN_SECONDS",
    "ADMISSION_SECONDS_PER_NAME",
    "DAY_UNIVERSE_MAX",
    "DAY_UNIVERSE_PREMARKET_MAX",
    "REASON_CONTRACT_MISMATCH",
    "SCAN_ADDITIONS_PER_SWEEP",
    "AdmissionReport",
    "AdmissionSource",
    "DayUniverseMember",
    "DayUniverseName",
    "ScanSighting",
    "admit_scan_names",
    "load_day_universe",
    "load_day_universe_members",
    "reason_token",
    "record_sightings",
    "scan_sightings",
    "size_limit",
]

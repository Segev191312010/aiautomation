"""The day universe: IBKR scan names for the day manager and the simulations only.

Every US common stock the screener's scans surface is admitted once per
session: IBKR contract details first (stock type, industry, category), then
the same daily price/liquidity qualification as the watchlist. Admitted
names live in ``autotrader_manager_day_universe`` for that session only (at
most ``DAY_UNIVERSE_MAX``), the screener quotes every one of them every
sweep, and ranks them in their own list under the payload's
``day_universe`` key.

Principle 1 (the real MOMO's inputs do not change, timing included):
day-universe names are never part of the hot-35 ranking group or the
watchlist breadth, and admission never writes ``autotrader_universe_daily``,
which the hot-35's own scan additions read: those qualify exactly as before,
at the same sweep. Admission keeps its own outcomes (``DayUniverseDaily``, a
private session-scoped file beside the database; the decision itself is the
day-universe row) and has its own request pacer. It may read a final row
the hot-35's own path wrote, to save a request, and never writes one.

Admission spends paced IBKR requests, so the screener runs it after a sweep
was persisted, in the idle rest of the sweep interval, and it stops before
the next sweep is due: no sweep is ever longer for it.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence, Set
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import logging
from pathlib import Path
import re
import time
from typing import Any, Protocol

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
    DailyQualification,
    RequestPacer,
    load_daily,
    record_contract_facts,
    request_qualification,
    runtime_directory,
    stock_type_rejection,
    write_private_text,
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
# The day universe's own daily qualifications, beside the database.
DAY_UNIVERSE_DAILY_FILENAME = "day_universe_daily.json"
DAY_UNIVERSE_DAILY_SCHEMA = "day_universe_daily.v1"
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


# --- the day universe's own daily qualifications ------------------------------------------------


def _decimal_text(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")


def _parse_decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("decimals are stored as text")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"{value!r} is not a decimal") from exc
    if not parsed.is_finite():
        raise ValueError(f"{value!r} is not finite")
    return parsed


def _qualification_document(row: DailyQualification) -> dict[str, Any]:
    return {
        "symbol": row.symbol,
        "prior_close": _decimal_text(row.prior_close),
        "avg_daily_volume_shares": _decimal_text(row.avg_daily_volume_shares),
        "avg_dollar_volume": _decimal_text(row.avg_dollar_volume),
        "daily_atr": _decimal_text(row.daily_atr),
        "qualified": row.qualified,
        "reason": row.reason,
        "observed_at": _timestamp(row.observed_at),
    }


def _qualification(session_date: date, con_id: int, document: object) -> DailyQualification:
    if not isinstance(document, dict):
        raise ValueError(f"conId {con_id}: not an object")
    qualified = document.get("qualified")
    reason = document.get("reason")
    symbol = document.get("symbol")
    observed = document.get("observed_at")
    if not isinstance(qualified, bool) or not isinstance(symbol, str) or not isinstance(observed, str):
        raise ValueError(f"conId {con_id}: malformed qualification")
    if reason is not None and not isinstance(reason, str):
        raise ValueError(f"conId {con_id}: malformed reason")
    observed_at = datetime.fromisoformat(observed)
    if observed_at.tzinfo is None:
        raise ValueError(f"conId {con_id}: naive timestamp")
    return DailyQualification(
        session_date=session_date,
        con_id=con_id,
        symbol=symbol,
        prior_close=_parse_decimal(document.get("prior_close")),
        avg_daily_volume_shares=_parse_decimal(document.get("avg_daily_volume_shares")),
        avg_dollar_volume=_parse_decimal(document.get("avg_dollar_volume")),
        daily_atr=_parse_decimal(document.get("daily_atr")),
        qualified=qualified,
        reason=reason,
        observed_at=observed_at,
    )


class DayUniverseDaily:
    """The day universe's daily qualifications, one private JSON file beside the database.

    Kept out of ``autotrader_universe_daily`` on purpose: that table feeds the
    hot-35's own scan additions, which must qualify exactly as before. Only
    the newest session is kept (TTL = session). An unreadable file reads as
    empty and is logged: the names keep their day-universe rows and are
    quoted without a volume baseline until re-qualified.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = path if path is not None else runtime_directory() / DAY_UNIVERSE_DAILY_FILENAME

    def _read(self) -> tuple[str | None, dict[int, object]]:
        try:
            document = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None, {}
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            logger.error("day universe qualifications in %s are unreadable: %s", self.path, exc)
            return None, {}
        if not isinstance(document, dict) or document.get("schema") != DAY_UNIVERSE_DAILY_SCHEMA:
            logger.error("day universe qualifications in %s have an unknown schema", self.path)
            return None, {}
        rows = document.get("rows")
        if not isinstance(rows, dict):
            return None, {}
        parsed: dict[int, object] = {}
        for key, value in rows.items():
            if isinstance(key, str) and key.isascii() and key.isdigit() and int(key) > 0:
                parsed[int(key)] = value
        session = document.get("session_date")
        return (session if isinstance(session, str) else None), parsed

    def load(self, session_date: date) -> dict[int, DailyQualification]:
        """The session's stored outcomes by conId; another session's read as none."""
        session, rows = self._read()
        if session != session_date.isoformat():
            return {}
        loaded: dict[int, DailyQualification] = {}
        for con_id, document in rows.items():
            try:
                loaded[con_id] = _qualification(session_date, con_id, document)
            except ValueError as exc:
                logger.error("day universe qualification dropped: %s", exc)
        return loaded

    def record(self, rows: Sequence[DailyQualification]) -> None:
        """Merge final outcomes of one session; a new session replaces the old one."""
        final = [row for row in rows if not row.transient]
        if not final:
            return
        session_date = final[0].session_date
        if any(row.session_date != session_date for row in final):
            raise ValueError("day universe qualifications are recorded one session at a time")
        merged = {con_id: _qualification_document(row) for con_id, row in self.load(session_date).items()}
        merged.update({row.con_id: _qualification_document(row) for row in final})
        document = {
            "schema": DAY_UNIVERSE_DAILY_SCHEMA,
            "session_date": session_date.isoformat(),
            "rows": {str(con_id): merged[con_id] for con_id in sorted(merged)},
        }
        write_private_text(self.path, json.dumps(document, indent=1, sort_keys=True) + "\n")


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
    """A qualified name with its own daily qualification, as the sweep quotes it.

    The metrics come from ``DayUniverseDaily``; they are unknown (None) when
    that file was lost, never borrowed from the hot-35's table.
    """

    con_id: int
    symbol: str
    primary_exchange: str | None
    avg_daily_volume_shares: Decimal | None
    best_rank: int | None
    prior_close: Decimal | None = None
    avg_dollar_volume: Decimal | None = None
    daily_atr: Decimal | None = None


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


async def load_day_universe_members(
    session_date: date, *, store: DayUniverseDaily | None = None
) -> tuple[DayUniverseMember, ...]:
    """The session's qualified names, best scan rank first, with their own metrics."""
    async with get_db() as db:
        async with db.execute(
            """SELECT con_id, symbol, primary_exchange, best_rank
               FROM autotrader_manager_day_universe
               WHERE session_date = ? AND qualified = 1
               ORDER BY best_rank IS NULL, best_rank, symbol, con_id""",
            (session_date.isoformat(),),
        ) as cursor:
            raw_rows = await cursor.fetchall()
    own = (store or DayUniverseDaily()).load(session_date)
    members: list[DayUniverseMember] = []
    for raw in raw_rows:
        con_id = int(str(raw[0]))
        metrics = own.get(con_id)
        members.append(
            DayUniverseMember(
                con_id=con_id,
                symbol=str(raw[1]),
                primary_exchange=None if raw[2] is None else str(raw[2]),
                avg_daily_volume_shares=None if metrics is None else metrics.avg_daily_volume_shares,
                best_rank=None if raw[3] is None else int(str(raw[3])),
                prior_close=None if metrics is None else metrics.prior_close,
                avg_dollar_volume=None if metrics is None else metrics.avg_dollar_volume,
                daily_atr=None if metrics is None else metrics.daily_atr,
            )
        )
    return tuple(members)


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
    store: DayUniverseDaily | None = None,
) -> AdmissionReport:
    """Refresh known names, then admit new ones best rank first.

    Stops at ``max_admissions`` new names, at the session's size limit, or
    when less than ``ADMISSION_SECONDS_PER_NAME`` remains before
    ``deadline`` (``monotonic`` seconds). A request error leaves the name for
    a later sighting; a competing login or lost connection raises, as in the
    sweep. Every outcome but a transient one is final for the session.

    ``history_pacer`` must be admission's own: the hot-35's scan additions
    keep their pacer, and so their request timing, to themselves. Outcomes
    go to ``store`` and the day-universe row, never to
    ``autotrader_universe_daily``.
    """
    now = clock()
    store = store or DayUniverseDaily()
    stored = await load_day_universe(session_date)
    known = {name.con_id: name for name in stored}
    refreshed = await record_sightings(session_date, sightings, known, now=now)
    size = sum(1 for name in stored if name.qualified)
    limit = max_size if max_size is not None else size_limit(session_date, now)
    own = store.load(session_date)
    # Read only: a final row the hot-35's path already wrote saves a request.
    shared = {row.con_id: row for row in await load_daily(session_date) if not row.transient}
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
        row = own.get(sighting.con_id) or shared.get(sighting.con_id)
        if row is None:
            row = await request_qualification(
                source,
                con_id=sighting.con_id,
                symbol=sighting.symbol,
                session_date=session_date,
                volume_multiplier=volume_multiplier,
                clock=clock,
                pacer=history_pacer,
            )
        if row.transient:
            failed += 1
            logger.warning("day universe: qualification of %s failed: %s", sighting.symbol, row.reason)
            continue
        # Its own outcome first, then the decision row that makes it a member.
        store.record([row])
        own[sighting.con_id] = row
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
    "DAY_UNIVERSE_DAILY_FILENAME",
    "DAY_UNIVERSE_DAILY_SCHEMA",
    "DAY_UNIVERSE_MAX",
    "DAY_UNIVERSE_PREMARKET_MAX",
    "REASON_CONTRACT_MISMATCH",
    "SCAN_ADDITIONS_PER_SWEEP",
    "AdmissionReport",
    "AdmissionSource",
    "DayUniverseDaily",
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

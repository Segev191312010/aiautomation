"""Candidates from the live screener's hot list instead of an IBKR scan.

The screener sweeps the owner's watchlist and ranks movers; a strategy run
takes the current hot list as its candidate set, with liquidity from that
day's universe qualification (no per-candidate daily-bar requests). A dynamic
run re-reads the hot list during the day (``hotlist_refresh``) so symbols that
heat up after its start are evaluated too.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import json

from db.core import get_db
from trading.contracts import Candidate, Run, ScannerRun, fingerprint_payload
from trading.market.ibkr_boundary import LiquidityProfile

MAX_CANDIDATES = 35
MAX_SNAPSHOT_AGE = timedelta(minutes=5)


class HotListUnavailable(RuntimeError):
    """No fresh screener snapshot exists for the session."""


@dataclass(frozen=True, slots=True)
class _Snapshot:
    id: str
    observed_at: datetime
    rows: list[dict]
    # conId -> (prior close, average dollar volume) of the day's qualified names.
    daily: dict[int, tuple[object, object]]


async def _fresh_snapshot(session_date: date, *, now: datetime, max_age: timedelta) -> _Snapshot:
    """The session's newest screener sweep, refused when missing or stale."""
    async with get_db() as db:
        async with db.execute(
            """SELECT id, observed_at, payload_json FROM autotrader_screener_snapshots
               WHERE session_date = ? ORDER BY observed_at DESC LIMIT 1""",
            (session_date.isoformat(),),
        ) as cursor:
            snapshot = await cursor.fetchone()
        if snapshot is None:
            raise HotListUnavailable("no screener snapshot for this session yet")
        snapshot_id, observed_raw, payload_raw = snapshot
        observed_at = datetime.fromisoformat(str(observed_raw)).astimezone(timezone.utc)
        if now - observed_at > max_age:
            raise HotListUnavailable(f"screener snapshot is {now - observed_at} old")
        async with db.execute(
            """SELECT con_id, prior_close, avg_dollar_volume FROM autotrader_universe_daily
               WHERE session_date = ? AND qualified = 1""",
            (session_date.isoformat(),),
        ) as cursor:
            daily = {int(con_id): (prior, adv) for con_id, prior, adv in await cursor.fetchall()}
    return _Snapshot(
        id=str(snapshot_id),
        observed_at=observed_at,
        rows=list(json.loads(str(payload_raw)).get("rows", [])),
        daily=daily,
    )


def _hot_rows(snapshot: _Snapshot) -> list[dict]:
    rows = [row for row in snapshot.rows if row.get("hot")]
    rows.sort(key=lambda row: int(row.get("hot_rank") or 10_000))
    return rows


def _admit(
    rows: Iterable[dict],
    *,
    run: Run,
    scanner_run_id: str,
    daily: Mapping[int, tuple[object, object]],
    max_candidates: int,
    exclude: frozenset[int] = frozenset(),
) -> tuple[tuple[Candidate, ...], dict[int, LiquidityProfile]]:
    """Ranked candidates for the qualified rows, in row order, with seeded liquidity.

    Ids are ``<run id>:<conId>``, so the same symbol is the same candidate in
    every selection of one run.
    """
    candidates: list[Candidate] = []
    liquidity: dict[int, LiquidityProfile] = {}
    for row in rows:
        con_id = int(row["con_id"])
        if con_id in exclude or con_id not in daily or len(candidates) >= max_candidates:
            continue
        prior_close, avg_dollar_volume = daily[con_id]
        if prior_close is None or avg_dollar_volume is None:
            continue
        candidates.append(
            Candidate(
                id=f"{run.id}:{con_id}",
                scanner_run_id=scanner_run_id,
                con_id=con_id,
                symbol=str(row["symbol"]),
                primary_exchange=str(row.get("primary_exchange") or "SMART"),
                rank=len(candidates) + 1,
                admission_status="admitted",
            )
        )
        liquidity[con_id] = LiquidityProfile(
            con_id=con_id,
            reference_price=Decimal(str(prior_close)),
            average_dollar_volume=Decimal(str(avg_dollar_volume)),
            sessions_used=20,
            last_session_date=run.session_date - timedelta(days=1),
        )
    return tuple(candidates), liquidity


def _hotlist_scanner_run_id(run: Run) -> str:
    return f"{run.id}-hotlist"


async def hotlist_candidates(
    run: Run,
    *,
    now: datetime,
    max_candidates: int = MAX_CANDIDATES,
    max_age: timedelta = MAX_SNAPSHOT_AGE,
) -> tuple[ScannerRun, tuple[Candidate, ...], dict[int, LiquidityProfile]]:
    """Return the run's candidate container, candidates and seeded liquidity."""
    snapshot = await _fresh_snapshot(run.session_date, now=now, max_age=max_age)
    rows = _hot_rows(snapshot)
    scanner_run_id = _hotlist_scanner_run_id(run)
    candidates, liquidity = _admit(
        rows,
        run=run,
        scanner_run_id=scanner_run_id,
        daily=snapshot.daily,
        max_candidates=max_candidates,
    )
    parameters: dict[str, object] = {
        "source": "screener_hotlist",
        "snapshot_id": snapshot.id,
        "snapshot_observed_at": snapshot.observed_at.isoformat(),
        "hot_rows": len(rows),
        "admitted": len(candidates),
    }
    scanner_run = ScannerRun(
        id=scanner_run_id,
        run_id=run.id,
        parameters=parameters,
        parameters_fingerprint=fingerprint_payload(parameters),
        status="completed",
        started_at=snapshot.observed_at,
        completed_at=now,
    )
    return scanner_run, candidates, liquidity


async def hotlist_refresh(
    run: Run,
    now: datetime,
    *,
    max_candidates: int = MAX_CANDIDATES,
    max_age: timedelta = MAX_SNAPSHOT_AGE,
) -> tuple[tuple[Candidate, ...], dict[int, LiquidityProfile]]:
    """Hot rows of the newest fresh sweep that are not yet candidates of ``run``.

    Same filters and order as ``hotlist_candidates`` and the same scanner
    run; ranks are provisional because the session admitting them assigns the
    run's next free ranks.
    """
    snapshot = await _fresh_snapshot(run.session_date, now=now, max_age=max_age)
    scanner_run_id = _hotlist_scanner_run_id(run)
    async with get_db() as db:
        async with db.execute(
            "SELECT con_id FROM autotrader_candidates WHERE scanner_run_id = ?",
            (scanner_run_id,),
        ) as cursor:
            known = frozenset(int(con_id) for (con_id,) in await cursor.fetchall())
    return _admit(
        _hot_rows(snapshot),
        run=run,
        scanner_run_id=scanner_run_id,
        daily=snapshot.daily,
        max_candidates=max_candidates,
        exclude=known,
    )


GAP_MIN_PCT = Decimal("3")


async def gap_candidates(
    run: Run,
    *,
    now: datetime,
    min_gap_pct: Decimal = GAP_MIN_PCT,
    max_candidates: int = MAX_CANDIDATES,
    max_age: timedelta = MAX_SNAPSHOT_AGE,
) -> tuple[ScannerRun, tuple[Candidate, ...], dict[int, LiquidityProfile]]:
    """Watchlist gappers from the newest sweep around the open.

    Before 09:30 the sweep has no official open, so the pre-market move
    (``change_pct`` vs prior close) stands in for the gap; the strategy itself
    re-checks the real gap from the first regular-session bar.
    """
    snapshot = await _fresh_snapshot(run.session_date, now=now, max_age=max_age)

    def gap_of(row: dict) -> Decimal | None:
        for key in ("gap_pct", "change_pct"):
            value = row.get(key)
            if value not in (None, ""):
                return Decimal(str(value))
        return None

    gappers = [
        (gap, row)
        for row in snapshot.rows
        if row.get("source") == "watchlist"
        and (gap := gap_of(row)) is not None
        and gap >= min_gap_pct
    ]
    gappers.sort(key=lambda item: (-item[0], str(item[1].get("symbol"))))
    scanner_run_id = f"{run.id}-gappers"
    candidates, liquidity = _admit(
        (row for _gap, row in gappers),
        run=run,
        scanner_run_id=scanner_run_id,
        daily=snapshot.daily,
        max_candidates=max_candidates,
    )
    parameters: dict[str, object] = {
        "source": "screener_gappers",
        "snapshot_id": snapshot.id,
        "snapshot_observed_at": snapshot.observed_at.isoformat(),
        "min_gap_pct": str(min_gap_pct),
        "gappers": len(gappers),
        "admitted": len(candidates),
    }
    scanner_run = ScannerRun(
        id=scanner_run_id,
        run_id=run.id,
        parameters=parameters,
        parameters_fingerprint=fingerprint_payload(parameters),
        status="completed",
        started_at=snapshot.observed_at,
        completed_at=now,
    )
    return scanner_run, candidates, liquidity


__all__ = ["HotListUnavailable", "gap_candidates", "hotlist_candidates", "hotlist_refresh"]

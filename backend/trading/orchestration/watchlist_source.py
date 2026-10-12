"""Candidates from the live screener's hot list instead of an IBKR scan.

The screener sweeps the owner's watchlist and ranks movers; a strategy run
takes the current hot list as its candidate set, with liquidity from that
day's universe qualification (no per-candidate daily-bar requests).
"""
from __future__ import annotations

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


async def hotlist_candidates(
    run: Run,
    *,
    now: datetime,
    max_candidates: int = MAX_CANDIDATES,
    max_age: timedelta = MAX_SNAPSHOT_AGE,
) -> tuple[ScannerRun, tuple[Candidate, ...], dict[int, LiquidityProfile]]:
    """Return the run's candidate container, candidates and seeded liquidity."""
    session_date = run.session_date
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
        rows = [row for row in json.loads(str(payload_raw)).get("rows", []) if row.get("hot")]
        rows.sort(key=lambda row: int(row.get("hot_rank") or 10_000))
        async with db.execute(
            """SELECT con_id, prior_close, avg_dollar_volume FROM autotrader_universe_daily
               WHERE session_date = ? AND qualified = 1""",
            (session_date.isoformat(),),
        ) as cursor:
            daily = {int(con_id): (prior, adv) for con_id, prior, adv in await cursor.fetchall()}

    scanner_run_id = f"{run.id}-hotlist"
    candidates: list[Candidate] = []
    liquidity: dict[int, LiquidityProfile] = {}
    for row in rows:
        con_id = int(row["con_id"])
        if con_id not in daily or len(candidates) >= max_candidates:
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
            last_session_date=session_date - timedelta(days=1),
        )
    parameters: dict[str, object] = {
        "source": "screener_hotlist",
        "snapshot_id": snapshot_id,
        "snapshot_observed_at": observed_at.isoformat(),
        "hot_rows": len(rows),
        "admitted": len(candidates),
    }
    scanner_run = ScannerRun(
        id=scanner_run_id,
        run_id=run.id,
        parameters=parameters,
        parameters_fingerprint=fingerprint_payload(parameters),
        status="completed",
        started_at=observed_at,
        completed_at=now,
    )
    return scanner_run, tuple(candidates), liquidity


__all__ = ["HotListUnavailable", "hotlist_candidates"]

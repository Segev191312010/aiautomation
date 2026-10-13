"""Read-only view of the AI day manager's day universe.

``autotrader_manager_day_universe`` holds the scanner movers kept for one
session (migration 15). It feeds the manager and the shadow simulations only;
it is never part of the hot-35 ranking or its breadth, so nothing a sendable
strategy trades reads it.

A database without the table (migration 15 not applied yet), or a read that
fails, reads as an empty day universe: a consumer such as GAP_GO_V2 then works
from the watchlist alone instead of failing its session.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date
import json
import logging
import sqlite3

from db.core import get_db

logger = logging.getLogger("autotrader.shadow.session")

DAY_UNIVERSE_TABLE = "autotrader_manager_day_universe"
# The IBKR gap-up scan codes (plan Design B). A source naming one marks a name
# the gap scans surfaced, even before the sweep has a row for it.
GAP_SCAN_CODES = ("HIGH_OPEN_GAP", "TOP_OPEN_PERC_GAIN")


@dataclass(frozen=True, slots=True)
class DayUniverseName:
    """One qualified name of the session's day universe."""

    con_id: int
    symbol: str
    sources: tuple[str, ...]
    best_rank: int | None

    @property
    def from_gap_scan(self) -> bool:
        return any(code in source.upper() for source in self.sources for code in GAP_SCAN_CODES)


DayUniverseReader = Callable[[date], Awaitable[tuple[DayUniverseName, ...]]]


def _sources(raw: object) -> tuple[str, ...]:
    try:
        value = json.loads(str(raw))
    except (TypeError, ValueError):
        return ()
    if not isinstance(value, list):
        return ()
    return tuple(str(item) for item in value if isinstance(item, str) and item)


async def qualified_day_universe(session_date: date) -> tuple[DayUniverseName, ...]:
    """The session's qualified day-universe names, best rank first; empty when absent."""
    try:
        async with get_db() as db:
            async with db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (DAY_UNIVERSE_TABLE,),
            ) as cursor:
                if await cursor.fetchone() is None:
                    return ()
            async with db.execute(
                f"""SELECT con_id, symbol, sources_json, best_rank FROM {DAY_UNIVERSE_TABLE}
                    WHERE session_date = ? AND qualified = 1
                    ORDER BY best_rank IS NULL, best_rank, symbol, con_id""",
                (session_date.isoformat(),),
            ) as cursor:
                rows = await cursor.fetchall()
    except sqlite3.Error as exc:
        logger.warning("day universe unreadable (%s); using none", type(exc).__name__)
        return ()
    names: list[DayUniverseName] = []
    for con_id, symbol, sources_json, best_rank in rows:
        try:
            names.append(
                DayUniverseName(
                    con_id=int(con_id),
                    symbol=str(symbol),
                    sources=_sources(sources_json),
                    best_rank=None if best_rank is None else int(best_rank),
                )
            )
        except (TypeError, ValueError):
            continue
    return tuple(names)


__all__ = [
    "DAY_UNIVERSE_TABLE",
    "GAP_SCAN_CODES",
    "DayUniverseName",
    "DayUniverseReader",
    "qualified_day_universe",
]

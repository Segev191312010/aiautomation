"""Account day-P&L route — GET /api/account/day-pnl.

Computes realized P&L on fills since the start of the current Eastern Time
trading day (DST-aware via zoneinfo), plus unrealized P&L from open positions
when a current-price source is readily readable.

This router queries the ``trades`` table directly (see db.core for schema)
rather than importing trade_service, to avoid coupling to a module that may be
edited concurrently. The ``trades.data`` column is a JSON blob; the canonical
realized-P&L field is ``realized_pnl`` with a legacy fallback to
``metadata.pnl`` (see db/trades.finalize_trade_outcome).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends

from auth import get_current_user
from db.core import get_db

log = logging.getLogger(__name__)

router = APIRouter(tags=["account"])

_ET = ZoneInfo("America/New_York")

# Unrealized P&L requires a current/last market price per open position. The
# open_positions table stores only entry data (entry_price, quantity, side) and
# no live mark, so there is no readily-readable source from the DB alone. We
# therefore report 0.0 and surface this caveat in the response note.
_UNREALIZED_NOTE = (
    "unrealized=0.0: no readily-readable current-price source; "
    "open_positions stores entry data only (no live mark)"
)


def _et_day_start_utc(now_utc: datetime | None = None) -> datetime:
    """Return the UTC instant of the most recent Eastern-Time midnight.

    DST-aware: the ET offset (EST -05:00 / EDT -04:00) is resolved by zoneinfo
    for the given calendar day, so the returned UTC boundary shifts correctly
    across DST transitions.
    """
    now_utc = now_utc or datetime.now(timezone.utc)
    now_et = now_utc.astimezone(_ET)
    midnight_et = now_et.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight_et.astimezone(timezone.utc)


def _parse_ts(value: str) -> datetime | None:
    """Parse a stored ISO timestamp into an aware UTC datetime, or None."""
    try:
        dt = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        # Stored timestamps are written as UTC; treat naive values as UTC.
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _row_realized_pnl(data_json: str) -> float:
    """Extract realized P&L from a trades.data JSON blob.

    Prefers the canonical ``realized_pnl`` field; falls back to legacy
    ``metadata.pnl``. Returns 0.0 when neither is present/numeric.
    """
    try:
        data = json.loads(data_json)
    except (TypeError, ValueError, json.JSONDecodeError):
        return 0.0
    val = data.get("realized_pnl")
    if val is None:
        meta = data.get("metadata")
        if isinstance(meta, dict):
            val = meta.get("pnl")
    try:
        return float(val) if val is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


@router.get("/api/account/day-pnl", dependencies=[Depends(get_current_user)])
async def get_day_pnl() -> dict:
    """Realized + unrealized P&L for the current ET trading day."""
    day_start_utc = _et_day_start_utc()

    realized = 0.0
    count_trades_today = 0

    async with get_db() as db:
        async with db.execute(
            "SELECT timestamp, data FROM trades ORDER BY timestamp DESC"
        ) as cur:
            rows = await cur.fetchall()

    for ts_raw, data_json in rows:
        ts = _parse_ts(ts_raw)
        if ts is None or ts < day_start_utc:
            continue
        count_trades_today += 1
        realized += _row_realized_pnl(data_json)

    realized = round(realized, 2)
    unrealized = 0.0
    total = round(realized + unrealized, 2)

    return {
        "realized": realized,
        "unrealized": unrealized,
        "total": total,
        "count_trades_today": count_trades_today,
        "note": _UNREALIZED_NOTE,
    }

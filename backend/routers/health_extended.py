"""Extended health probes — /api/health/ibkr, /api/health/database-integrity.

Authenticated, read-only diagnostics that complement the lightweight
``/api/health`` check in ``routers/status.py``. These endpoints surface lower
level connection/storage internals (IBKR socket liveness, SQLite integrity &
journal mode) for operators monitoring a live real-money deployment.

Everything here is strictly read-only:
* IBKR state is read via the existing :data:`ibkr` singleton's public
  ``is_connected()`` plus a guarded peek at ib_insync's connection stats —
  we never connect, disconnect, or mutate the client.
* The DB probe runs ``PRAGMA integrity_check`` / ``PRAGMA journal_mode`` inside
  the shared ``get_db()`` context manager.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends

from auth import get_current_user
from db.core import get_db
from ibkr_client import ibkr

log = logging.getLogger(__name__)

router = APIRouter(tags=["health"])


def _ibkr_heartbeat_age_s() -> Optional[float]:
    """Best-effort, read-only age (seconds) of the live IBKR connection.

    ib_insync does not maintain an explicit per-message heartbeat timestamp, so
    we use the connection's elapsed duration as a liveness proxy: a positive
    value means the socket has been continuously up for that many seconds.
    Returns ``None`` when not connected or if the stat is unavailable — this
    never raises and never triggers any network I/O.
    """
    try:
        if not ibkr.is_connected():
            return None
        client = getattr(ibkr.ib, "client", None)
        if client is None:
            return None
        # connectionStats() raises ConnectionError if not ready; the
        # is_connected() guard above makes that unlikely, but stay defensive.
        stats = client.connectionStats()
        duration = getattr(stats, "duration", None)
        if duration is None:
            return None
        return round(max(0.0, float(duration)), 3)
    except Exception as exc:  # pragma: no cover - defensive
        log.debug("IBKR heartbeat age unavailable: %s", exc)
        return None


@router.get("/api/health/ibkr", dependencies=[Depends(get_current_user)])
async def ibkr_health() -> dict:
    """Report IBKR socket connectivity and a heartbeat-age liveness proxy."""
    return {
        "connected": bool(ibkr.is_connected()),
        "last_heartbeat_age_s": _ibkr_heartbeat_age_s(),
    }


@router.get("/api/health/database-integrity", dependencies=[Depends(get_current_user)])
async def database_integrity() -> dict:
    """Run SQLite ``PRAGMA integrity_check`` and report the journal mode."""
    async with get_db() as db:
        async with db.execute("PRAGMA integrity_check") as cur:
            row = await cur.fetchone()
        integrity = row[0] if row else None

        async with db.execute("PRAGMA journal_mode") as cur:
            row = await cur.fetchone()
        journal_mode = row[0] if row else None

    return {"integrity": integrity, "journal_mode": journal_mode}

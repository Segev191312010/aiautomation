"""Direct AI candidates CRUD — persist queued candidates so they survive restart.

Queued direct AI candidates were previously held in an in-memory asyncio.Queue,
which meant a crash/restart dropped anything not yet executed. This module
persists them to SQLite keyed by ``id`` with a TTL stamped at queue time.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

import aiosqlite

from db.core import get_db

log = logging.getLogger(__name__)


async def queue_candidate(
    candidate_id: str,
    symbol: str,
    payload: dict,
    *,
    ttl_seconds: int = 900,
    user_id: str = "demo",
    db: aiosqlite.Connection | None = None,
) -> None:
    """Insert a queued candidate. Caller provides a stable id (UUID)."""
    queued_at = datetime.now(timezone.utc).isoformat()
    payload_json = json.dumps(payload, default=str)

    async def _execute(conn: aiosqlite.Connection) -> None:
        await conn.execute(
            "INSERT OR REPLACE INTO direct_candidates "
            "(id, user_id, symbol, payload, queued_at, ttl_seconds, status) "
            "VALUES (?, ?, ?, ?, ?, ?, 'queued')",
            (candidate_id, user_id, symbol.upper(), payload_json, queued_at, ttl_seconds),
        )

    if db is not None:
        await _execute(db)
    else:
        async with get_db() as conn:
            await _execute(conn)
            await conn.commit()


async def drain_candidates(
    max_age_seconds: int = 900,
    *,
    user_id: str = "demo",
) -> list[dict]:
    """Return non-expired queued candidates and mark them 'draining'.

    Candidates older than *max_age_seconds* are marked 'expired' and skipped.
    Callers that actually execute a candidate should then call
    :func:`mark_candidate_status` with ``applied`` or ``failed``.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=max_age_seconds)).isoformat()
    results: list[dict] = []
    async with get_db() as db:
        # Expire stale rows first
        await db.execute(
            "UPDATE direct_candidates SET status='expired' "
            "WHERE status='queued' AND user_id=? AND queued_at < ?",
            (user_id, cutoff),
        )
        async with db.execute(
            "SELECT id, symbol, payload, queued_at, ttl_seconds "
            "FROM direct_candidates "
            "WHERE status='queued' AND user_id=? AND queued_at >= ? "
            "ORDER BY queued_at ASC",
            (user_id, cutoff),
        ) as cur:
            rows = await cur.fetchall()

        ids: list[str] = []
        for row in rows:
            cand_id, symbol, payload_json, queued_at, ttl = row
            try:
                payload = json.loads(payload_json) if payload_json else {}
            except json.JSONDecodeError:
                log.warning("direct_candidates: corrupt payload for id=%s, skipping", cand_id)
                continue
            payload["_candidate_id"] = cand_id
            payload["queued_at"] = queued_at
            payload["ttl_seconds"] = ttl
            results.append(payload)
            ids.append(cand_id)

        if ids:
            placeholders = ",".join("?" for _ in ids)
            await db.execute(
                f"UPDATE direct_candidates SET status='draining' WHERE id IN ({placeholders})",
                ids,
            )
        await db.commit()
    return results


async def mark_candidate_status(
    candidate_id: str,
    status: str,
    *,
    db: aiosqlite.Connection | None = None,
) -> None:
    """Mark a queued candidate as applied / failed / expired.

    Allowed statuses: 'queued', 'draining', 'applied', 'failed', 'expired'.
    """
    if status not in {"queued", "draining", "applied", "failed", "expired"}:
        raise ValueError(f"invalid candidate status: {status}")

    async def _execute(conn: aiosqlite.Connection) -> None:
        await conn.execute(
            "UPDATE direct_candidates SET status=? WHERE id=?",
            (status, candidate_id),
        )

    if db is not None:
        await _execute(db)
    else:
        async with get_db() as conn:
            await _execute(conn)
            await conn.commit()


async def purge_expired_candidates(*, user_id: str | None = None) -> int:
    """One-shot purge at startup — mark all 'queued'/'draining' rows older than
    their TTL as expired, and delete rows in a terminal state older than 7 days.

    Returns number of rows expired.
    """
    now = datetime.now(timezone.utc)
    async with get_db() as db:
        # Expire by age vs ttl_seconds (stored per row)
        if user_id:
            args_prefix: tuple = (user_id,)
            user_clause = "AND user_id=?"
        else:
            args_prefix = ()
            user_clause = ""

        async with db.execute(
            f"SELECT id, queued_at, ttl_seconds FROM direct_candidates "
            f"WHERE status IN ('queued','draining') {user_clause}",
            args_prefix,
        ) as cur:
            rows = await cur.fetchall()

        expired_ids: list[str] = []
        for cand_id, queued_at_raw, ttl in rows:
            try:
                queued_at = datetime.fromisoformat(str(queued_at_raw).replace("Z", "+00:00"))
            except (TypeError, ValueError):
                expired_ids.append(cand_id)
                continue
            if queued_at.tzinfo is None:
                queued_at = queued_at.replace(tzinfo=timezone.utc)
            if (now - queued_at).total_seconds() > float(ttl or 900):
                expired_ids.append(cand_id)

        if expired_ids:
            placeholders = ",".join("?" for _ in expired_ids)
            await db.execute(
                f"UPDATE direct_candidates SET status='expired' WHERE id IN ({placeholders})",
                expired_ids,
            )

        # Garbage-collect terminal rows older than 7 days
        stale_cutoff = (now - timedelta(days=7)).isoformat()
        await db.execute(
            "DELETE FROM direct_candidates "
            "WHERE status IN ('applied','failed','expired') AND queued_at < ?",
            (stale_cutoff,),
        )
        await db.commit()
    if expired_ids:
        log.info("purge_expired_candidates: expired %d stale direct candidate(s)", len(expired_ids))
    return len(expired_ids)


async def get_candidate_status(candidate_id: str) -> str | None:
    """Return the current status of a candidate, or None if not found."""
    async with get_db() as db:
        async with db.execute(
            "SELECT status FROM direct_candidates WHERE id=?", (candidate_id,)
        ) as cur:
            row = await cur.fetchone()
    return row[0] if row else None

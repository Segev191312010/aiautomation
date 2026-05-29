"""Cross-process per-symbol order rate cap (SQLite-backed).

A sliding-window rate limiter that is safe across multiple worker processes
(WORKERS>1) and across coroutines within one process. It replaces the
in-process ``asyncio.Lock`` used by the order path so that an autopilot running
in LIVE mode under several Uvicorn workers cannot exceed the per-symbol order
rate cap.

The window is stored in the ``order_rate_window`` table. Each successfully
acquired slot inserts one row ``(symbol, ts_unix, worker_pid)``. Acquisition is
serialized by a single ``BEGIN IMMEDIATE`` write transaction (provided by
``db.core.transaction()``), so the evict -> count -> insert sequence is atomic
even when many workers race for the same symbol.

NOTE FOR ORCHESTRATOR
---------------------
This module self-creates its table/index lazily and idempotently via
``_ensure_schema()`` on the first ``try_acquire_order_slot`` call. The DDL is
intentionally kept here so the lane stays self-contained and does NOT edit
``db/core.py``. Consider folding the following into ``init_db()`` (and dropping
the lazy guard here) once integrating:

    CREATE TABLE IF NOT EXISTS order_rate_window (
        symbol     TEXT NOT NULL,
        ts_unix    INTEGER NOT NULL,
        worker_pid INTEGER NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_order_rate_symbol_ts
        ON order_rate_window(symbol, ts_unix);
"""
from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import time

from db.core import transaction

log = logging.getLogger(__name__)

# BEGIN IMMEDIATE takes a write lock and SQLite's busy_timeout retries most
# contention transparently. But concurrent first-touch DDL (CREATE TABLE/INDEX
# IF NOT EXISTS racing across worker connections on a cold DB) can surface a
# table-level SQLITE_LOCKED ("database is locked") that the busy handler does
# NOT retry. We retry such transient locks ourselves with a short backoff so a
# cold-start burst under WORKERS>1 still serializes cleanly.
_LOCK_RETRIES = 8
_LOCK_BACKOFF_SECONDS = 0.05

_CREATE_ORDER_RATE_WINDOW = """
CREATE TABLE IF NOT EXISTS order_rate_window (
    symbol     TEXT NOT NULL,
    ts_unix    INTEGER NOT NULL,
    worker_pid INTEGER NOT NULL
)
"""

_CREATE_ORDER_RATE_INDEX = """
CREATE INDEX IF NOT EXISTS idx_order_rate_symbol_ts
    ON order_rate_window(symbol, ts_unix)
"""


async def _ensure_schema(db) -> None:
    """Create the rate-window table and index if missing (idempotent).

    Runs inside the caller's open transaction/connection so the DDL shares the
    same write lock. ``CREATE TABLE/INDEX IF NOT EXISTS`` are cheap no-ops once
    the objects exist.
    """
    await db.execute(_CREATE_ORDER_RATE_WINDOW)
    await db.execute(_CREATE_ORDER_RATE_INDEX)


async def try_acquire_order_slot(
    symbol: str,
    max_per_minute: int,
    window_seconds: int = 60,
) -> bool:
    """Atomically try to reserve one order slot for ``symbol``.

    Within a single ``BEGIN IMMEDIATE`` write transaction:
      1. evict rows older than ``now - window_seconds`` (keeps the table bounded),
      2. count remaining rows for ``symbol`` inside the window,
      3. if count >= ``max_per_minute`` -> return ``False`` (cap reached),
         else INSERT ``(symbol, now, os.getpid())`` and return ``True``.

    Because ``transaction()`` takes an IMMEDIATE write lock, concurrent callers
    (other coroutines or other worker processes) are serialized: exactly
    ``max_per_minute`` acquisitions succeed per rolling window per symbol.

    A non-positive ``max_per_minute`` denies all orders (fail-closed): an order
    cap of zero means "no orders allowed".
    """
    # Fail closed: a cap of 0 (or negative) admits nothing.
    if max_per_minute <= 0:
        return False

    last_exc: sqlite3.OperationalError | None = None
    for attempt in range(_LOCK_RETRIES):
        try:
            return await _acquire_once(symbol, max_per_minute, window_seconds)
        except sqlite3.OperationalError as exc:
            # Only retry transient lock contention; re-raise real errors.
            if "locked" not in str(exc).lower():
                raise
            last_exc = exc
            await asyncio.sleep(_LOCK_BACKOFF_SECONDS * (attempt + 1))

    # Exhausted retries — surface the lock so the caller (order path) treats it
    # as a failure rather than silently admitting/denying an order on bad data.
    assert last_exc is not None
    raise last_exc


async def _acquire_once(
    symbol: str,
    max_per_minute: int,
    window_seconds: int,
) -> bool:
    """Single atomic evict -> count -> insert pass inside one write txn."""
    now = int(time.time())
    cutoff = now - window_seconds

    async with transaction() as db:
        await _ensure_schema(db)

        # 1. Evict expired rows globally so the table stays bounded over time.
        await db.execute(
            "DELETE FROM order_rate_window WHERE ts_unix <= ?",
            (cutoff,),
        )

        # 2. Count live rows for this symbol within the window.
        async with db.execute(
            "SELECT COUNT(*) FROM order_rate_window "
            "WHERE symbol = ? AND ts_unix > ?",
            (symbol, cutoff),
        ) as cur:
            row = await cur.fetchone()
        count = row[0] if row else 0

        # 3. Enforce the cap.
        if count >= max_per_minute:
            log.debug(
                "order rate cap hit for %s: %d/%d in %ds window",
                symbol, count, max_per_minute, window_seconds,
            )
            return False

        await db.execute(
            "INSERT INTO order_rate_window (symbol, ts_unix, worker_pid) "
            "VALUES (?, ?, ?)",
            (symbol, now, os.getpid()),
        )
        return True

"""W1: tv_idempotency migration — table exists, event_key is UNIQUE, index present.

The FK signal_id -> direct_candidates(id) is enforced (foreign_keys=ON in get_db),
so a parent direct_candidates row must exist before inserting the child.
"""
import sqlite3

import pytest

from db.core import get_db, init_db
from db.direct_candidates import queue_candidate


async def test_tv_idempotency_table_exists_after_init_db():
    await init_db()
    async with get_db() as db:
        async with db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='tv_idempotency'"
        ) as cur:
            row = await cur.fetchone()
    assert row is not None


async def test_unique_event_key_blocks_duplicate():
    await init_db()
    await queue_candidate("sig-idem-1", "AAPL", {"action": "buy"})  # parent (FK)
    async with get_db() as db:
        await db.execute(
            "INSERT INTO tv_idempotency (event_key, signal_id, created_at) VALUES (?,?,?)",
            ("ek-dup", "sig-idem-1", "2026-05-29T00:00:00+00:00"),
        )
        await db.commit()
        with pytest.raises(sqlite3.IntegrityError):
            await db.execute(
                "INSERT INTO tv_idempotency (event_key, signal_id, created_at) VALUES (?,?,?)",
                ("ek-dup", "sig-idem-1", "2026-05-29T00:00:01+00:00"),
            )
            await db.commit()


async def test_created_index_present():
    await init_db()
    async with get_db() as db:
        async with db.execute("PRAGMA index_list('tv_idempotency')") as cur:
            rows = await cur.fetchall()
    # index_list row layout: (seq, name, unique, origin, partial)
    names = [r[1] for r in rows]
    assert any("tv_idempotency_created" in n for n in names)

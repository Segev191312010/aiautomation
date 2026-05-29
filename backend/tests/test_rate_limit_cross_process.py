"""Tests for the SQLite cross-process per-symbol order rate cap.

Covers: first order, within-cap, cap-exceeded, window expiry frees a slot,
20 concurrent asyncio tasks -> exactly ``max_per_minute`` succeed, per-symbol
scoping, and that eviction keeps the table bounded.

Each test uses an isolated DB_PATH via monkeypatching ``cfg.DB_PATH`` (the
same pattern conftest uses for ``database.DB_PATH``). ``db.core.transaction()``
reads ``cfg.DB_PATH`` at call time, so the override fully isolates each test.
"""
from __future__ import annotations

import asyncio
import os
import time

import pytest

import config
from db.core import transaction
from db.rate_limits import try_acquire_order_slot


@pytest.fixture
def _isolated_db(tmp_path, monkeypatch):
    """Point cfg.DB_PATH at a fresh per-test SQLite file."""
    db_path = str(tmp_path / "rate_limits.db")
    if os.path.exists(db_path):
        os.unlink(db_path)
    monkeypatch.setattr(config.cfg, "DB_PATH", db_path)
    return db_path


async def _count_rows(symbol: str | None = None) -> int:
    async with transaction() as db:
        # The table is created lazily on first acquire; if no acquire with a
        # positive cap has run yet (e.g. zero-cap denial) it won't exist — and
        # that legitimately means zero rows.
        await db.execute(
            "CREATE TABLE IF NOT EXISTS order_rate_window "
            "(symbol TEXT NOT NULL, ts_unix INTEGER NOT NULL, worker_pid INTEGER NOT NULL)"
        )
        if symbol is None:
            sql, args = "SELECT COUNT(*) FROM order_rate_window", ()
        else:
            sql, args = (
                "SELECT COUNT(*) FROM order_rate_window WHERE symbol = ?",
                (symbol,),
            )
        async with db.execute(sql, args) as cur:
            row = await cur.fetchone()
    return row[0] if row else 0


async def _insert_stale_row(symbol: str, age_seconds: int) -> None:
    """Insert a row dated ``age_seconds`` in the past (bypasses the cap logic)."""
    ts = int(time.time()) - age_seconds
    async with transaction() as db:
        # Ensure the table exists first (mirrors module DDL).
        await db.execute(
            "CREATE TABLE IF NOT EXISTS order_rate_window "
            "(symbol TEXT NOT NULL, ts_unix INTEGER NOT NULL, worker_pid INTEGER NOT NULL)"
        )
        await db.execute(
            "INSERT INTO order_rate_window (symbol, ts_unix, worker_pid) VALUES (?, ?, ?)",
            (symbol, ts, os.getpid()),
        )


async def test_first_order_passes(_isolated_db):
    assert await try_acquire_order_slot("AAPL", max_per_minute=3) is True
    assert await _count_rows("AAPL") == 1


async def test_within_cap_passes(_isolated_db):
    assert await try_acquire_order_slot("AAPL", max_per_minute=3) is True
    assert await try_acquire_order_slot("AAPL", max_per_minute=3) is True
    assert await try_acquire_order_slot("AAPL", max_per_minute=3) is True
    assert await _count_rows("AAPL") == 3


async def test_cap_exceeded_returns_false(_isolated_db):
    for _ in range(3):
        assert await try_acquire_order_slot("AAPL", max_per_minute=3) is True
    # 4th within the same window is denied.
    assert await try_acquire_order_slot("AAPL", max_per_minute=3) is False
    # Still denied on repeat.
    assert await try_acquire_order_slot("AAPL", max_per_minute=3) is False
    # The denied attempts did NOT insert rows.
    assert await _count_rows("AAPL") == 3


async def test_zero_cap_denies_all(_isolated_db):
    assert await try_acquire_order_slot("AAPL", max_per_minute=0) is False
    assert await _count_rows("AAPL") == 0


async def test_window_expiry_frees_a_slot(_isolated_db):
    # Fill the cap with rows aged just past a 60s window.
    await _insert_stale_row("AAPL", age_seconds=61)
    await _insert_stale_row("AAPL", age_seconds=120)
    # Both are within the cap count window? No — they are expired, so the next
    # acquire should evict them and succeed.
    assert await try_acquire_order_slot("AAPL", max_per_minute=2, window_seconds=60) is True
    # Only the fresh row survives eviction.
    assert await _count_rows("AAPL") == 1


async def test_window_partial_expiry(_isolated_db):
    # One fresh, one stale within a cap of 2.
    assert await try_acquire_order_slot("AAPL", max_per_minute=2, window_seconds=60) is True
    await _insert_stale_row("AAPL", age_seconds=999)
    assert await _count_rows("AAPL") == 2
    # Stale row evicted -> count drops to 1 (the fresh one) -> under cap -> passes.
    assert await try_acquire_order_slot("AAPL", max_per_minute=2, window_seconds=60) is True
    assert await _count_rows("AAPL") == 2  # fresh + new; stale gone


async def test_concurrent_tasks_exactly_cap_succeed(_isolated_db):
    cap = 5
    n_tasks = 20

    results = await asyncio.gather(
        *(try_acquire_order_slot("AAPL", max_per_minute=cap) for _ in range(n_tasks))
    )

    succeeded = sum(1 for r in results if r is True)
    assert succeeded == cap, f"expected exactly {cap} winners, got {succeeded}"
    assert sum(1 for r in results if r is False) == n_tasks - cap
    # Exactly `cap` rows were inserted — no over-admission under contention.
    assert await _count_rows("AAPL") == cap


async def test_per_symbol_scoping(_isolated_db):
    # Exhaust AAPL's cap.
    for _ in range(2):
        assert await try_acquire_order_slot("AAPL", max_per_minute=2) is True
    assert await try_acquire_order_slot("AAPL", max_per_minute=2) is False
    # MSFT is unaffected by AAPL's saturated window.
    assert await try_acquire_order_slot("MSFT", max_per_minute=2) is True
    assert await try_acquire_order_slot("MSFT", max_per_minute=2) is True
    assert await try_acquire_order_slot("MSFT", max_per_minute=2) is False
    assert await _count_rows("AAPL") == 2
    assert await _count_rows("MSFT") == 2


async def test_eviction_keeps_table_bounded(_isolated_db):
    # Seed lots of stale rows across symbols that should all be evicted.
    for i in range(50):
        await _insert_stale_row("AAPL", age_seconds=120 + i)
        await _insert_stale_row("MSFT", age_seconds=120 + i)
    assert await _count_rows() == 100

    # A single acquire evicts ALL expired rows globally, leaving only the new one.
    assert await try_acquire_order_slot("AAPL", max_per_minute=10, window_seconds=60) is True
    assert await _count_rows() == 1
    assert await _count_rows("AAPL") == 1
    assert await _count_rows("MSFT") == 0

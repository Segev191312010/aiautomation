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
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

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


@pytest.mark.parametrize(
    "db_path",
    ["", " ", ":memory:", "file::memory:?cache=shared", "file:x?mode=memory"],
)
async def test_ephemeral_database_fails_closed(db_path, monkeypatch):
    monkeypatch.setattr(config.cfg, "DB_PATH", db_path)
    with pytest.raises(RuntimeError, match="durable SQLite file"):
        await try_acquire_order_slot("AAPL", max_per_minute=1)


async def test_invalid_window_and_symbol_fail_closed(_isolated_db):
    with pytest.raises(ValueError, match="window_seconds"):
        await try_acquire_order_slot("AAPL", max_per_minute=1, window_seconds=0)
    with pytest.raises(ValueError, match="symbol"):
        await try_acquire_order_slot(" ", max_per_minute=1)


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


def test_independent_processes_share_one_global_cap(_isolated_db, tmp_path):
    """Independent PIDs racing from one barrier admit exactly the global cap."""
    cap = 3
    child_count = 8
    start_path = tmp_path / "start"
    script = (
        "import asyncio, os, pathlib, sys, time\n"
        "import config\n"
        "from db.rate_limits import try_acquire_order_slot\n"
        "config.cfg.DB_PATH = sys.argv[1]\n"
        "barrier = pathlib.Path(sys.argv[2])\n"
        "deadline = time.monotonic() + 10\n"
        "while not barrier.exists():\n"
        "    if time.monotonic() > deadline: raise SystemExit(24)\n"
        "    time.sleep(0.01)\n"
        "ok = asyncio.run(try_acquire_order_slot('AAPL', int(sys.argv[3])))\n"
        "print(f'{os.getpid()}:{int(ok)}')\n"
    )
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", script, _isolated_db, str(start_path), str(cap)],
            cwd=Path(__file__).resolve().parents[1],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(child_count)
    ]
    start_path.touch()
    outputs = [process.communicate(timeout=20) for process in processes]

    assert all(process.returncode == 0 for process in processes), outputs
    parsed = [stdout.strip().split(":") for stdout, _stderr in outputs]
    assert len({pid for pid, _result in parsed}) == child_count
    assert sum(int(result) for _pid, result in parsed) == cap


async def test_real_write_lock_blocks_order_boundary_with_bounded_latency(
    _isolated_db,
    caplog,
):
    """Exercise actual SQLite contention through the production order wrapper."""
    import logging

    from order_executor import _check_and_record_rate_cap

    # Create the schema before taking the independent blocking transaction.
    assert await try_acquire_order_slot("SCHEMA", max_per_minute=10) is True
    blocker = sqlite3.connect(_isolated_db, timeout=0, isolation_level=None)
    blocker.execute("BEGIN IMMEDIATE")
    started = time.monotonic()
    try:
        with caplog.at_level(logging.CRITICAL):
            allowed = await _check_and_record_rate_cap("AAPL")
    finally:
        elapsed = time.monotonic() - started
        blocker.execute("ROLLBACK")
        blocker.close()

    assert allowed is False
    assert elapsed < 3.0, f"rate-cap failure took {elapsed:.2f}s"
    assert any(
        "order_rate_cap_unavailable" in record.getMessage()
        for record in caplog.records
    )


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

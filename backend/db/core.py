"""SQLite connection and schema initialization for Autotrader V1."""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite

from config import cfg
from trading.storage.migrations import apply_migrations


def _ensure_parent_directory() -> None:
    if cfg.DB_PATH == ":memory:":
        return
    Path(cfg.DB_PATH).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)


async def _configure(db: aiosqlite.Connection) -> None:
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA synchronous=FULL")
    await db.execute("PRAGMA busy_timeout=10000")
    await db.execute("PRAGMA foreign_keys=ON")


@asynccontextmanager
async def get_db():
    _ensure_parent_directory()
    async with aiosqlite.connect(cfg.DB_PATH) as db:
        await _configure(db)
        yield db


@asynccontextmanager
async def transaction():
    async with get_db() as db:
        await db.execute("BEGIN IMMEDIATE")
        try:
            yield db
            await db.commit()
        except BaseException:
            await db.rollback()
            raise


async def init_db() -> None:
    async with get_db() as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                email TEXT UNIQUE,
                password_hash TEXT,
                created_at TEXT,
                settings TEXT NOT NULL DEFAULT '{}'
            )
            """
        )
        await db.commit()
        await apply_migrations(db)

        from auth import seed_demo_user

        await seed_demo_user(db)

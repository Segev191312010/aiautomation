"""Test helpers for databases at a chosen schema version.

Runtime processes never migrate (they refuse with exit 75 until `autotraderctl
db migrate` has run), so a test that drives a child's command must create its
database at the code's schema first, the way the owner's migration does.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import aiosqlite

from config import cfg
import trading.storage.migrations as migrations


def migrate_database(path: Path | str) -> Path:
    """Create or upgrade ``path`` to the code's schema (users and demo user included).

    For synchronous tests; async tests await ``db.core.init_db()`` directly.
    """
    from db.core import init_db

    original = cfg.DB_PATH
    cfg.DB_PATH = str(path)
    try:
        asyncio.run(init_db())
    finally:
        cfg.DB_PATH = original
    return Path(path)


async def apply_up_to(path: Path | str, version: int) -> Path:
    """A database whose ledger stops at ``version``: the later migrations are pending."""
    full = migrations.MIGRATIONS
    migrations.MIGRATIONS = tuple(item for item in full if item.version <= version)
    try:
        async with aiosqlite.connect(str(path)) as db:
            await migrations.apply_migrations(db)
    finally:
        migrations.MIGRATIONS = full
    return Path(path)


def database_at(path: Path | str, version: int) -> Path:
    """Synchronous :func:`apply_up_to`."""
    return asyncio.run(apply_up_to(path, version))


__all__ = ["apply_up_to", "database_at", "migrate_database"]

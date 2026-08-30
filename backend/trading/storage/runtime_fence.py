"""Durable monotonic PAPER stop store."""
from __future__ import annotations

from datetime import datetime, timezone

from db.core import transaction


class SQLitePaperKillStore:
    """A stop-only store; clearing it requires an explicit future recovery workflow."""

    async def is_stopped(self) -> bool:
        async with transaction() as db:
            async with db.execute(
                "SELECT stopped FROM autotrader_execution_fences WHERE name='paper'"
            ) as cursor:
                row = await cursor.fetchone()
        return bool(row and int(row[0]))

    async def stop(self, *, reason: str) -> None:
        if not reason.strip():
            raise ValueError("operator stop reason is required")
        async with transaction() as db:
            await db.execute(
                """
                INSERT INTO autotrader_execution_fences (name, stopped, reason, updated_at)
                VALUES ('paper', 1, ?, ?)
                ON CONFLICT(name) DO UPDATE SET stopped=1, reason=excluded.reason,
                    updated_at=excluded.updated_at
                """,
                (reason, datetime.now(timezone.utc).isoformat()),
            )

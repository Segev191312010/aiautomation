"""Durable PAPER stop store: the global fence and one fence per sendable identity.

Rows of ``autotrader_execution_fences`` (``name TEXT PRIMARY KEY``; no CHECK
on ``name``, so no migration): ``paper`` is the global kill switch, stop-only;
``paper:<IDENTITY>`` fences one sendable identity's new entries and may be
resumed by the owner. The bridge reads the same rows with the same predicate
(``WHERE name=?``) right before every entry, fail-closed when unreadable.
"""
from __future__ import annotations

from datetime import datetime, timezone

from db.core import transaction

GLOBAL_FENCE = "paper"
IDENTITY_FENCE_PREFIX = "paper:"


def fence_name(identity: str | None = None) -> str:
    """``paper`` for the global switch, ``paper:<IDENTITY>`` for one identity."""
    if identity is None:
        return GLOBAL_FENCE
    name = identity.strip()
    if not name or any(character.isspace() for character in name):
        raise ValueError("identity must be a strategy name without spaces")
    return f"{IDENTITY_FENCE_PREFIX}{name}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SQLitePaperKillStore:
    """The global row is stop-only; an identity row is stop/resume."""

    async def is_stopped(self, *, identity: str | None = None) -> bool:
        async with transaction() as db:
            async with db.execute(
                "SELECT stopped FROM autotrader_execution_fences WHERE name=?",
                (fence_name(identity),),
            ) as cursor:
                row = await cursor.fetchone()
        return bool(row and int(row[0]))

    async def _write(self, *, name: str, stopped: int, reason: str) -> None:
        if not reason.strip():
            raise ValueError("operator reason is required")
        async with transaction() as db:
            await db.execute(
                """
                INSERT INTO autotrader_execution_fences (name, stopped, reason, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET stopped=excluded.stopped, reason=excluded.reason,
                    updated_at=excluded.updated_at
                """,
                (name, stopped, reason, _now()),
            )

    async def stop(self, *, reason: str, identity: str | None = None) -> None:
        await self._write(name=fence_name(identity), stopped=1, reason=reason)

    async def resume(self, *, reason: str, identity: str) -> None:
        """Lift one identity's fence. The global switch has no resume."""
        if identity is None or not str(identity).strip():
            raise ValueError("resume needs an identity; the global PAPER stop is never resumed")
        await self._write(name=fence_name(identity), stopped=0, reason=reason)

    async def identity_rows(self) -> list[tuple[str, bool, str, str]]:
        """``(identity, stopped, reason, updated_at)`` for every identity row."""
        async with transaction() as db:
            async with db.execute(
                "SELECT name, stopped, reason, updated_at FROM autotrader_execution_fences"
                " WHERE name LIKE ? ORDER BY name",
                (f"{IDENTITY_FENCE_PREFIX}%",),
            ) as cursor:
                rows = await cursor.fetchall()
        return [
            (str(name)[len(IDENTITY_FENCE_PREFIX):], bool(int(stopped)), str(reason), str(updated))
            for name, stopped, reason, updated in rows
        ]


__all__ = ["GLOBAL_FENCE", "IDENTITY_FENCE_PREFIX", "SQLitePaperKillStore", "fence_name"]

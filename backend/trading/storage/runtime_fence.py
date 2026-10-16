"""Durable PAPER stop store: the global fence and one fence per sendable identity.

Rows of ``autotrader_execution_fences`` (``name TEXT PRIMARY KEY``; no CHECK
on ``name``, so no migration): ``paper`` is the global kill switch, stop-only;
``paper:<IDENTITY>`` fences one sendable identity's new entries and may be
resumed by the owner. The bridge reads the same rows with the same predicate
(``WHERE name=?``) right before every entry, fail-closed when unreadable.

``stop_identity`` is the auto-fence's write (the Sentinel, the nightly replay):
it reads the row and stops it in ONE ``BEGIN IMMEDIATE`` transaction, so it
never overwrites a row that is stopped already (an owner's /halt, an earlier
fence) and an owner's stop or resume can never land between its check and its
write (N15a LOW-5).
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone

import aiosqlite

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


@dataclass(frozen=True, slots=True)
class FenceRow:
    """One fence row as stored: stopped or not, its reason, its UTC ISO time."""

    stopped: bool
    reason: str
    updated_at: str


# Decides, inside the write transaction, that an identity row must be kept as it is.
KeepRow = Callable[[FenceRow], bool]
# The reason a resume stores, from the row it lifts (None: no row yet).
ResumeReason = Callable[[FenceRow | None], str]

_UPSERT = """
    INSERT INTO autotrader_execution_fences (name, stopped, reason, updated_at)
    VALUES (?, ?, ?, ?)
    ON CONFLICT(name) DO UPDATE SET stopped=excluded.stopped, reason=excluded.reason,
        updated_at=excluded.updated_at
"""


async def _read_row(db: aiosqlite.Connection, name: str) -> FenceRow | None:
    async with db.execute(
        "SELECT stopped, reason, updated_at FROM autotrader_execution_fences WHERE name=?", (name,)
    ) as cursor:
        found = await cursor.fetchone()
    if found is None:
        return None
    return FenceRow(stopped=bool(int(found[0])), reason=str(found[1]), updated_at=str(found[2]))


async def _upsert(db: aiosqlite.Connection, *, name: str, stopped: int, reason: str) -> None:
    if not reason.strip():
        raise ValueError("operator reason is required")
    await db.execute(_UPSERT, (name, stopped, reason, _now()))


def _identity_name(identity: str | None, refusal: str) -> str:
    if identity is None or not str(identity).strip():
        raise ValueError(refusal)
    return fence_name(identity)


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
        async with transaction() as db:
            await _upsert(db, name=name, stopped=stopped, reason=reason)

    async def stop(self, *, reason: str, identity: str | None = None) -> None:
        await self._write(name=fence_name(identity), stopped=1, reason=reason)

    async def stop_identity(
        self, *, reason: str, identity: str, unless: KeepRow | None = None
    ) -> FenceRow | None:
        """Stop one identity's row unless it is stopped already or ``unless(row)``
        says to keep it: None when this call stopped it, else the row it kept,
        unchanged. Check and stop are one write transaction."""
        name = _identity_name(identity, "stop_identity needs an identity; it never writes the global row")
        if not reason.strip():
            raise ValueError("operator reason is required")
        async with transaction() as db:
            row = await _read_row(db, name)
            if row is not None and (row.stopped or (unless is not None and unless(row))):
                return row
            await _upsert(db, name=name, stopped=1, reason=reason)
        return None

    async def resume(
        self, *, reason: str, identity: str, annotate: ResumeReason | None = None
    ) -> None:
        """Lift one identity's fence. The global switch has no resume.

        ``annotate`` (the owner's CLI) builds the stored reason from the row it
        lifts, read in the same transaction: the trace that tells the Sentinel
        the owner lifted one of its fences (``sentinel.owner_resume_reason``).
        """
        name = _identity_name(identity, "resume needs an identity; the global PAPER stop is never resumed")
        if annotate is None:
            await self._write(name=name, stopped=0, reason=reason)
            return
        if not reason.strip():
            raise ValueError("operator reason is required")
        async with transaction() as db:
            await _upsert(db, name=name, stopped=0, reason=annotate(await _read_row(db, name)))

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


__all__ = [
    "GLOBAL_FENCE",
    "IDENTITY_FENCE_PREFIX",
    "FenceRow",
    "KeepRow",
    "ResumeReason",
    "SQLitePaperKillStore",
    "fence_name",
]

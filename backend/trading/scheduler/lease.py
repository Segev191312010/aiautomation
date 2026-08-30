"""Transactional durable leader leases; leases do not grant trade authority."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import aiosqlite


class LeaseLostError(RuntimeError):
    """Raised when a scheduler no longer owns its fencing token."""


@dataclass(frozen=True)
class LeaseClaim:
    name: str
    holder_id: str
    fencing_token: int
    acquired_at: datetime
    expires_at: datetime


def _utc(value: datetime | None) -> datetime:
    value = value or datetime.now(timezone.utc)
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("lease timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return _utc(value).isoformat()


def _parse(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return _utc(parsed)


def _validate_inputs(name: str, holder_id: str, ttl: timedelta) -> None:
    if not name.strip():
        raise ValueError("lease name must be non-empty")
    if not holder_id.strip():
        raise ValueError("lease holder_id must be non-empty")
    if ttl <= timedelta(0):
        raise ValueError("lease ttl must be positive")


async def get_lease(
    db: aiosqlite.Connection,
    *,
    name: str,
) -> LeaseClaim | None:
    """Read the current lease row without changing its fencing token."""
    async with db.execute(
        """
        SELECT name, holder_id, fencing_token, acquired_at, expires_at
        FROM autotrader_scheduler_leases WHERE name=?
        """,
        (name,),
    ) as cursor:
        row = await cursor.fetchone()
    if row is None:
        return None
    return LeaseClaim(
        name=str(row[0]),
        holder_id=str(row[1]),
        fencing_token=int(row[2]),
        acquired_at=_parse(str(row[3])),
        expires_at=_parse(str(row[4])),
    )


async def acquire_lease(
    db: aiosqlite.Connection,
    *,
    name: str,
    holder_id: str,
    ttl: timedelta,
    now: datetime | None = None,
) -> LeaseClaim | None:
    """Atomically claim an absent/expired lease and increment its fence token."""
    _validate_inputs(name, holder_id, ttl)
    observed_at = _utc(now)
    expires_at = observed_at + ttl
    await db.execute("BEGIN IMMEDIATE")
    try:
        current = await get_lease(db, name=name)
        if current is not None and current.expires_at > observed_at:
            await db.rollback()
            return None
        next_token = 1 if current is None else current.fencing_token + 1
        await db.execute(
            """
            INSERT INTO autotrader_scheduler_leases
                (name, holder_id, fencing_token, acquired_at, expires_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                holder_id=excluded.holder_id,
                fencing_token=excluded.fencing_token,
                acquired_at=excluded.acquired_at,
                expires_at=excluded.expires_at,
                updated_at=excluded.updated_at
            """,
            (name, holder_id, next_token, _iso(observed_at), _iso(expires_at), _iso(observed_at)),
        )
        await db.commit()
    except BaseException:
        await db.rollback()
        raise
    return LeaseClaim(name, holder_id, next_token, observed_at, expires_at)


async def renew_lease(
    db: aiosqlite.Connection,
    *,
    claim: LeaseClaim,
    ttl: timedelta,
    now: datetime | None = None,
) -> LeaseClaim | None:
    """Extend a lease only when holder and fencing token still match."""
    _validate_inputs(claim.name, claim.holder_id, ttl)
    observed_at = _utc(now)
    expires_at = observed_at + ttl
    await db.execute("BEGIN IMMEDIATE")
    try:
        cursor = await db.execute(
            """
            UPDATE autotrader_scheduler_leases
            SET expires_at=?, updated_at=?
            WHERE name=? AND holder_id=? AND fencing_token=? AND expires_at>?
            """,
            (
                _iso(expires_at),
                _iso(observed_at),
                claim.name,
                claim.holder_id,
                claim.fencing_token,
                _iso(observed_at),
            ),
        )
        await db.commit()
    except BaseException:
        await db.rollback()
        raise
    if cursor.rowcount != 1:
        return None
    return LeaseClaim(claim.name, claim.holder_id, claim.fencing_token, claim.acquired_at, expires_at)


async def release_lease(
    db: aiosqlite.Connection,
    *,
    claim: LeaseClaim,
    now: datetime | None = None,
) -> bool:
    """Expire a lease without deleting its row or resetting its fence token."""
    observed_at = _utc(now)
    await db.execute("BEGIN IMMEDIATE")
    try:
        cursor = await db.execute(
            """
            UPDATE autotrader_scheduler_leases
            SET expires_at=?, updated_at=?
            WHERE name=? AND holder_id=? AND fencing_token=?
            """,
            (_iso(observed_at), _iso(observed_at), claim.name, claim.holder_id, claim.fencing_token),
        )
        await db.commit()
    except BaseException:
        await db.rollback()
        raise
    return cursor.rowcount == 1


async def require_lease(
    db: aiosqlite.Connection,
    *,
    claim: LeaseClaim,
    now: datetime | None = None,
) -> None:
    """Fail closed unless the caller still owns the active fencing token."""
    observed_at = _utc(now)
    current = await get_lease(db, name=claim.name)
    if (
        current is None
        or current.holder_id != claim.holder_id
        or current.fencing_token != claim.fencing_token
        or current.expires_at <= observed_at
    ):
        raise LeaseLostError(f"scheduler lease lost: {claim.name}")

"""Durable cross-host execution lease and fencing token.

Stage 9A used a same-host ``fcntl`` file lock to prevent duplicate
broker/background lifecycles. Phase 1 (ADR 0006) replaces that with a
durable SQLite-backed lease so any process sharing the database can
detect the current execution owner and refuse stale mutations.

Design
------
- One row in ``execution_lease`` records the active owner.
- Lease has: owner_id, started_at, expires_at, fencing_token, version.
- Acquisition is atomic via INSERT/UPDATE guarded by version.
- Renewal extends ``expires_at`` as long as the caller holds the current
  fencing token.
- A lost lease is not reclaimable until a quarantine period passes.
- Fencing token must be supplied to all broker-mutation paths; a stale
  token causes rejection with a clear error.
"""
from __future__ import annotations

import logging
import os
import time
import uuid
from dataclasses import dataclass
from typing import Any

import aiosqlite

from config import cfg
from db.core import get_db, transaction

log = logging.getLogger(__name__)

# Tunable via env; kept conservative for safety.
LEASE_HEARTBEAT_SECONDS = int(os.getenv("EXECUTION_LEASE_HEARTBEAT_SECONDS", "5"))
LEASE_TTL_SECONDS = int(os.getenv("EXECUTION_LEASE_TTL_SECONDS", "15"))
LEASE_QUARANTINE_SECONDS = int(os.getenv("EXECUTION_LEASE_QUARANTINE_SECONDS", "30"))

LEASE_TABLE = """
CREATE TABLE IF NOT EXISTS execution_lease (
    singleton     INTEGER PRIMARY KEY DEFAULT 1 CHECK (singleton = 1),
    owner_id      TEXT NOT NULL,
    fencing_token TEXT NOT NULL,
    version       INTEGER NOT NULL,
    started_at    REAL NOT NULL,
    expires_at    REAL NOT NULL,
    last_seen_at  REAL NOT NULL
);
"""

_CREATE_INDEX = """
CREATE INDEX IF NOT EXISTS idx_execution_lease_expires
    ON execution_lease(expires_at);
"""


@dataclass(frozen=True)
class Lease:
    owner_id: str
    fencing_token: str
    version: int
    started_at: float
    expires_at: float
    last_seen_at: float

    def is_valid(self, now: float | None = None) -> bool:
        now = now or time.time()
        return now < self.expires_at

    def to_dict(self) -> dict[str, Any]:
        return {
            "owner_id": self.owner_id,
            "fencing_token": self.fencing_token,
            "version": self.version,
            "started_at": self.started_at,
            "expires_at": self.expires_at,
            "last_seen_at": self.last_seen_at,
        }


async def _ensure_schema(db: aiosqlite.Connection) -> None:
    await db.execute(LEASE_TABLE)
    await db.execute(_CREATE_INDEX)


def _generate_owner_id() -> str:
    """Stable for process lifetime but unique across hosts."""
    return f"{os.getpid()}@{uuid.getnode():012x}"


def _generate_token() -> str:
    return uuid.uuid4().hex


async def get_current_lease() -> Lease | None:
    """Return the active lease row, or None if table/row missing."""
    async with get_db() as db:
        await _ensure_schema(db)
        async with db.execute(
            "SELECT owner_id, fencing_token, version, started_at, expires_at, last_seen_at "
            "FROM execution_lease WHERE singleton = 1"
        ) as cur:
            row = await cur.fetchone()
            if row is None:
                return None
            return Lease(*row)


async def acquire_execution_lease(
    *,
    owner_id: str | None = None,
    heartbeat_seconds: int = LEASE_HEARTBEAT_SECONDS,
    ttl_seconds: int = LEASE_TTL_SECONDS,
    quarantine_seconds: int = LEASE_QUARANTINE_SECONDS,
) -> Lease:
    """Atomically become the execution owner.

    Raises RuntimeError if another valid owner holds the lease, or if the
    lease is in quarantine after a lost owner.
    """
    if ttl_seconds <= 0:
        raise ValueError("ttl_seconds must be positive")
    if heartbeat_seconds <= 0 or heartbeat_seconds > ttl_seconds:
        raise ValueError("heartbeat_seconds must be positive and <= ttl_seconds")
    if quarantine_seconds < 0:
        raise ValueError("quarantine_seconds must be non-negative")

    owner_id = owner_id or _generate_owner_id()
    token = _generate_token()
    now = time.time()
    expires = now + ttl_seconds
    version = 1

    async with transaction(busy_timeout_ms=250) as db:
        await _ensure_schema(db)
        async with db.execute(
            "SELECT owner_id, fencing_token, version, started_at, expires_at, last_seen_at "
            "FROM execution_lease WHERE singleton = 1"
        ) as cur:
            row = await cur.fetchone()

        if row is not None:
            existing = Lease(*row)
            if existing.is_valid(now):
                raise RuntimeError(
                    f"execution lease held by {existing.owner_id} until {existing.expires_at}"
                )
            # Expired/lost lease — enforce quarantine before takeover.
            quarantine_end = existing.expires_at + quarantine_seconds
            if now < quarantine_end:
                raise RuntimeError(
                    f"execution lease in quarantine until {quarantine_end}; "
                    f"previous owner was {existing.owner_id}"
                )
            version = existing.version + 1

        await db.execute(
            """
            INSERT INTO execution_lease (singleton, owner_id, fencing_token, version, started_at, expires_at, last_seen_at)
            VALUES (1, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(singleton) DO UPDATE SET
                owner_id = excluded.owner_id,
                fencing_token = excluded.fencing_token,
                version = excluded.version,
                started_at = excluded.started_at,
                expires_at = excluded.expires_at,
                last_seen_at = excluded.last_seen_at
            """,
            (owner_id, token, version, now, expires, now),
        )

    lease = Lease(owner_id, token, version, now, expires, now)
    log.info(
        "execution lease acquired: owner=%s version=%d expires=%.3f",
        owner_id, version, expires,
    )
    return lease


async def renew_execution_lease(
    fencing_token: str,
    *,
    ttl_seconds: int = LEASE_TTL_SECONDS,
) -> Lease | None:
    """Extend the lease if the token still matches.

    Returns the updated lease, or None if the token no longer owns the lease
    (ownership was lost or taken over). Raises ValueError on bad input.
    """
    if not fencing_token:
        raise ValueError("fencing_token required")
    if ttl_seconds <= 0:
        raise ValueError("ttl_seconds must be positive")

    now = time.time()
    expires = now + ttl_seconds

    async with transaction(busy_timeout_ms=250) as db:
        await _ensure_schema(db)
        async with db.execute(
            "SELECT owner_id, fencing_token, version, started_at, expires_at, last_seen_at "
            "FROM execution_lease WHERE singleton = 1"
        ) as cur:
            row = await cur.fetchone()
            if row is None:
                return None
            current = Lease(*row)
            if current.fencing_token != fencing_token:
                log.warning(
                    "lease renewal rejected: token mismatch (our token is stale); "
                    "current owner=%s",
                    current.owner_id,
                )
                return None
            if not current.is_valid(now):
                # Lease expired while we still hold the old token. Renewal
                # from a stale owner is not allowed; caller must re-acquire
                # after quarantine.
                log.warning("lease renewal rejected: lease already expired")
                return None

        await db.execute(
            """
            UPDATE execution_lease
            SET expires_at = ?, last_seen_at = ?
            WHERE singleton = 1 AND fencing_token = ?
            """,
            (expires, now, fencing_token),
        )
        if db.total_changes == 0:
            return None

    lease = Lease(current.owner_id, current.fencing_token, current.version, current.started_at, expires, now)
    log.debug("execution lease renewed: owner=%s expires=%.3f", lease.owner_id, expires)
    return lease


async def release_execution_lease(fencing_token: str) -> bool:
    """Release the lease if this token still owns it.

    Returns True if the lease row was removed, False otherwise.
    """
    if not fencing_token:
        raise ValueError("fencing_token required")

    async with transaction(busy_timeout_ms=250) as db:
        await _ensure_schema(db)
        await db.execute(
            "DELETE FROM execution_lease WHERE singleton = 1 AND fencing_token = ?",
            (fencing_token,),
        )
        removed = db.total_changes > 0

    if removed:
        log.info("execution lease released by token %s...", fencing_token[:8])
    return removed


async def validate_fencing_token(
    fencing_token: str | None,
    *,
    require_valid: bool = True,
) -> Lease | None:
    """Check that ``fencing_token`` matches the current lease.

    If ``require_valid`` is True (default), an expired lease is treated as
    invalid. Returns the current lease or None.
    """
    if not fencing_token:
        return None
    current = await get_current_lease()
    if current is None:
        return None
    if current.fencing_token != fencing_token:
        return None
    if require_valid and not current.is_valid():
        return None
    return current

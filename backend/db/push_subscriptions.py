"""Durable, user-owned browser PushSubscription persistence."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

from db.core import get_db, transaction


class PushSubscriptionOwnershipError(RuntimeError):
    """Raised when a browser endpoint is already owned by another user."""


class PushSubscriptionLimitError(RuntimeError):
    """Raised when a user has reached the configured endpoint limit."""


@dataclass(frozen=True)
class PushSubscriptionRecord:
    id: str
    user_id: str
    endpoint: str
    p256dh: str
    auth: str
    created_at: str
    updated_at: str
    last_success_at: str | None
    last_failure_at: str | None
    last_error: str | None
    failure_count: int

    def subscription_info(self) -> dict[str, object]:
        return {
            "endpoint": self.endpoint,
            "keys": {"p256dh": self.p256dh, "auth": self.auth},
        }


_SELECT_COLUMNS = (
    "id, user_id, endpoint, p256dh, auth, created_at, updated_at, "
    "last_success_at, last_failure_at, last_error, failure_count"
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _from_row(row: tuple[object, ...]) -> PushSubscriptionRecord:
    return PushSubscriptionRecord(
        id=str(row[0]),
        user_id=str(row[1]),
        endpoint=str(row[2]),
        p256dh=str(row[3]),
        auth=str(row[4]),
        created_at=str(row[5]),
        updated_at=str(row[6]),
        last_success_at=str(row[7]) if row[7] is not None else None,
        last_failure_at=str(row[8]) if row[8] is not None else None,
        last_error=str(row[9]) if row[9] is not None else None,
        failure_count=int(row[10]),
    )


async def list_push_subscriptions(user_id: str) -> list[PushSubscriptionRecord]:
    async with get_db() as db:
        async with db.execute(
            f"SELECT {_SELECT_COLUMNS} FROM push_subscriptions WHERE user_id=?",
            (user_id,),
        ) as cursor:
            rows = await cursor.fetchall()
    return [_from_row(row) for row in rows]


async def get_push_subscription(
    endpoint: str,
) -> PushSubscriptionRecord | None:
    async with get_db() as db:
        async with db.execute(
            f"SELECT {_SELECT_COLUMNS} FROM push_subscriptions WHERE endpoint=?",
            (endpoint,),
        ) as cursor:
            row = await cursor.fetchone()
    return _from_row(row) if row else None


async def upsert_push_subscription(
    *,
    user_id: str,
    endpoint: str,
    p256dh: str,
    auth: str,
    max_subscriptions: int = 10,
) -> tuple[PushSubscriptionRecord, bool]:
    """Create or refresh a subscription without transferring ownership."""
    if max_subscriptions < 1:
        raise ValueError("max_subscriptions must be positive")
    now = _utc_now()
    created = False
    async with transaction() as db:
        async with db.execute(
            "SELECT id, user_id FROM push_subscriptions WHERE endpoint=?",
            (endpoint,),
        ) as cursor:
            existing = await cursor.fetchone()
        if existing and str(existing[1]) != user_id:
            raise PushSubscriptionOwnershipError
        if existing:
            subscription_id = str(existing[0])
            await db.execute(
                "UPDATE push_subscriptions SET p256dh=?, auth=?, updated_at=?, "
                "last_error=NULL, failure_count=0 WHERE id=? AND user_id=?",
                (p256dh, auth, now, subscription_id, user_id),
            )
        else:
            async with db.execute(
                "SELECT COUNT(*) FROM push_subscriptions WHERE user_id=?", (user_id,)
            ) as cursor:
                count_row = await cursor.fetchone()
            if count_row and int(count_row[0]) >= max_subscriptions:
                raise PushSubscriptionLimitError
            created = True
            subscription_id = str(uuid4())
            await db.execute(
                "INSERT INTO push_subscriptions "
                "(id, user_id, endpoint, p256dh, auth, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (subscription_id, user_id, endpoint, p256dh, auth, now, now),
            )
        async with db.execute(
            f"SELECT {_SELECT_COLUMNS} FROM push_subscriptions WHERE id=?",
            (subscription_id,),
        ) as cursor:
            row = await cursor.fetchone()
    if row is None:
        raise RuntimeError("push subscription persistence failed")
    return _from_row(row), created


async def delete_push_subscription(*, user_id: str, endpoint: str) -> bool:
    async with transaction() as db:
        cursor = await db.execute(
            "DELETE FROM push_subscriptions WHERE user_id=? AND endpoint=?",
            (user_id, endpoint),
        )
        return cursor.rowcount > 0


async def delete_push_subscription_by_id(subscription_id: str) -> bool:
    async with transaction() as db:
        cursor = await db.execute(
            "DELETE FROM push_subscriptions WHERE id=?", (subscription_id,)
        )
        return cursor.rowcount > 0


async def record_push_success(subscription_id: str) -> None:
    now = _utc_now()
    async with transaction() as db:
        await db.execute(
            "UPDATE push_subscriptions SET last_success_at=?, last_error=NULL, "
            "failure_count=0 WHERE id=?",
            (now, subscription_id),
        )


async def record_push_failure(subscription_id: str, error_code: str) -> None:
    now = _utc_now()
    async with transaction() as db:
        await db.execute(
            "UPDATE push_subscriptions SET last_failure_at=?, last_error=?, "
            "failure_count=failure_count+1 WHERE id=?",
            (now, error_code[:80], subscription_id),
        )

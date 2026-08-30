"""Transactional persistence for immutable policy versions."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any

import aiosqlite
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from db.core import get_db, transaction
from trading.contracts import fingerprint_payload, require_aware
from trading.policies.models import (
    PolicyType,
    PolicyValue,
    policy_from_payload,
    policy_type_for,
)


class DuplicatePolicyError(ValueError):
    """Raised when an immutable policy version cannot be published."""


class PolicyRepository:
    async def create(
        self,
        *,
        policy_id: str,
        owner_id: str,
        policy: PolicyValue,
    ) -> "PolicyVersion":
        return await create_policy(policy_id=policy_id, owner_id=owner_id, policy=policy)

    async def publish(
        self,
        *,
        policy_id: str,
        owner_id: str,
        policy: PolicyValue,
        base_version: int | None = None,
    ) -> "PolicyVersion":
        return await publish_policy(
            policy_id=policy_id,
            owner_id=owner_id,
            policy=policy,
            base_version=base_version,
        )

    async def get(
        self,
        *,
        policy_id: str,
        owner_id: str,
        version: int | None = None,
    ) -> "PolicyVersion | None":
        return await get_policy(policy_id=policy_id, owner_id=owner_id, version=version)

    async def list_versions(self, *, policy_id: str, owner_id: str) -> list["PolicyVersion"]:
        return await list_policy_versions(policy_id=policy_id, owner_id=owner_id)


class PolicyVersion(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_id: str = Field(min_length=1, max_length=200)
    version: int = Field(ge=1)
    owner_id: str = Field(min_length=1, max_length=200)
    policy_type: PolicyType
    content_hash: str = Field(min_length=64, max_length=64)
    policy: PolicyValue
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def validate_created_at(cls, value: datetime) -> datetime:
        return require_aware(value)

    @model_validator(mode="after")
    def validate_content(self) -> "PolicyVersion":
        if policy_type_for(self.policy) != self.policy_type:
            raise ValueError("policy type does not match policy value")
        if self.content_hash != fingerprint_payload(self.policy):
            raise ValueError("policy content hash does not match immutable value")
        return self


def _dump(policy: PolicyValue) -> str:
    return json.dumps(policy.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))


def _record_from_row(row: tuple[Any, ...]) -> PolicyVersion:
    policy_id, version, owner_id, policy_type, content_hash, policy_json, created_at = row
    typed_policy = policy_from_payload(str(policy_type), json.loads(str(policy_json)))
    return PolicyVersion(
        policy_id=str(policy_id),
        version=int(version),
        owner_id=str(owner_id),
        policy_type=str(policy_type),
        content_hash=str(content_hash),
        policy=typed_policy,
        created_at=datetime.fromisoformat(str(created_at)),
    )


async def publish_policy(
    *,
    policy_id: str,
    owner_id: str,
    policy: PolicyValue,
    base_version: int | None = None,
) -> PolicyVersion:
    policy_type = policy_type_for(policy)
    content_hash = fingerprint_payload(policy)
    now = datetime.now(timezone.utc)
    async with transaction() as db:
        async with db.execute(
            "SELECT MAX(version), MIN(policy_type), MAX(policy_type) FROM autotrader_policy_versions WHERE policy_id=? AND owner_id=?",
            (policy_id, owner_id),
        ) as cursor:
            row = await cursor.fetchone()
        latest = int(row[0]) if row and row[0] is not None else None
        if row and row[1] is not None and (str(row[1]) != policy_type or str(row[2]) != policy_type):
            raise ValueError("policy id cannot change policy type")
        if latest is None:
            if base_version not in (None, 0):
                raise ValueError("initial policy base_version must be zero")
            version = 1
        else:
            if base_version is None or base_version != latest:
                raise ValueError("policy base_version is stale")
            version = latest + 1
        try:
            await db.execute(
                """
                INSERT INTO autotrader_policy_versions
                (policy_id, version, owner_id, policy_type, content_hash, policy_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    policy_id,
                    version,
                    owner_id,
                    policy_type,
                    content_hash,
                    _dump(policy),
                    now.isoformat(),
                ),
            )
        except aiosqlite.IntegrityError as exc:
            raise DuplicatePolicyError("policy version already exists or content is duplicated") from exc
    return PolicyVersion(
        policy_id=policy_id,
        version=version,
        owner_id=owner_id,
        policy_type=policy_type,
        content_hash=content_hash,
        policy=policy,
        created_at=now,
    )


async def create_policy(*, policy_id: str, owner_id: str, policy: PolicyValue) -> PolicyVersion:
    return await publish_policy(
        policy_id=policy_id,
        owner_id=owner_id,
        policy=policy,
        base_version=0,
    )


async def get_policy(
    *,
    policy_id: str,
    owner_id: str,
    version: int | None = None,
) -> PolicyVersion | None:
    async with get_db() as db:
        if version is None:
            async with db.execute(
                "SELECT MAX(version) FROM autotrader_policy_versions WHERE policy_id=? AND owner_id=?",
                (policy_id, owner_id),
            ) as cursor:
                row = await cursor.fetchone()
            if row is None or row[0] is None:
                return None
            version = int(row[0])
        async with db.execute(
            """
            SELECT policy_id, version, owner_id, policy_type, content_hash, policy_json, created_at
            FROM autotrader_policy_versions
            WHERE policy_id=? AND owner_id=? AND version=?
            """,
            (policy_id, owner_id, version),
        ) as cursor:
            row = await cursor.fetchone()
    return _record_from_row(row) if row else None


async def list_policy_versions(*, policy_id: str, owner_id: str) -> list[PolicyVersion]:
    async with get_db() as db:
        async with db.execute(
            """
            SELECT policy_id, version, owner_id, policy_type, content_hash, policy_json, created_at
            FROM autotrader_policy_versions
            WHERE policy_id=? AND owner_id=? ORDER BY version
            """,
            (policy_id, owner_id),
        ) as cursor:
            rows = await cursor.fetchall()
    return [_record_from_row(row) for row in rows]

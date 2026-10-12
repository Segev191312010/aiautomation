"""Durable operator control and optimistic shadow-session state."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timezone
import json

import aiosqlite

from db.core import get_db, transaction
from trading.contracts import ShadowSessionRecord


class ShadowControlConflict(RuntimeError):
    """Raised when a shadow control or session revision is stale."""


class ShadowSessionNotFound(LookupError):
    """Raised when a requested shadow session does not exist."""


@dataclass(frozen=True, slots=True)
class ShadowControlStatus:
    enabled: bool
    reason: str | None
    revision: int
    updated_at: datetime


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat()


def _payload(record: ShadowSessionRecord) -> str:
    return json.dumps(
        record.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
    )


def _status_from_row(row: tuple[object, ...]) -> ShadowControlStatus:
    return ShadowControlStatus(
        enabled=bool(row[0]),
        reason=str(row[1]) if row[1] is not None else None,
        revision=int(row[2]),
        updated_at=datetime.fromisoformat(str(row[3])),
    )


def _session_from_row(row: tuple[object, ...]) -> ShadowSessionRecord:
    record = ShadowSessionRecord.model_validate(json.loads(str(row[8])))
    columns = (
        str(row[0]),
        str(row[1]) if row[1] is not None else None,
        str(row[2]),
        str(row[3]),
        str(row[4]),
        int(row[5]),
        bool(row[6]),
        str(row[7]) if row[7] is not None else None,
    )
    expected = (
        record.id,
        record.run_id,
        record.source_sha,
        record.session_date.isoformat(),
        record.state.value,
        record.revision,
        record.evidence_eligible,
        record.block_reason,
    )
    if columns != expected:
        raise ValueError("persisted shadow session columns do not match its payload")
    return record


class SQLiteShadowControlStore:
    """SQLite-backed shadow enablement and single-session CAS store."""

    def __init__(self, *, clock: Callable[[], datetime] = _utc_now) -> None:
        self._clock = clock

    async def get_status(self) -> ShadowControlStatus:
        async with get_db() as db:
            async with db.execute(
                """
                SELECT enabled, reason, revision, updated_at
                FROM autotrader_shadow_control WHERE name='shadow'
                """
            ) as cursor:
                row = await cursor.fetchone()
        if row is None:
            raise ShadowSessionNotFound("shadow control has not been initialized")
        return _status_from_row(row)

    async def status(self) -> ShadowControlStatus:
        return await self.get_status()

    async def set_status(
        self,
        enabled: bool,
        reason: str | None = None,
        expected_revision: int | None = None,
    ) -> ShadowControlStatus:
        normalized_reason = reason.strip() if reason is not None else None
        if enabled and normalized_reason is not None:
            raise ValueError("enabled shadow control cannot carry a disable reason")
        if not enabled and not normalized_reason:
            raise ValueError("disabling shadow requires a non-empty reason")
        if expected_revision is not None and expected_revision < 0:
            raise ValueError("expected_revision must be non-negative")

        now = self._clock()
        observed_at = _timestamp(now)
        async with transaction() as db:
            async with db.execute(
                """
                SELECT enabled, reason, revision, updated_at
                FROM autotrader_shadow_control WHERE name='shadow'
                """
            ) as cursor:
                row = await cursor.fetchone()
            if row is None:
                raise ShadowSessionNotFound("shadow control has not been initialized")
            current = _status_from_row(row)
            revision = current.revision if expected_revision is None else expected_revision
            if revision != current.revision:
                raise ShadowControlConflict("shadow control revision is stale")
            if current.enabled == enabled and current.reason == normalized_reason:
                return current
            cursor = await db.execute(
                """
                UPDATE autotrader_shadow_control
                SET enabled=?, reason=?, revision=revision+1, updated_at=?
                WHERE name='shadow' AND revision=?
                """,
                (int(enabled), normalized_reason, observed_at, revision),
            )
            if cursor.rowcount != 1:
                raise ShadowControlConflict("shadow control revision is stale")
        return ShadowControlStatus(
            enabled=enabled,
            reason=normalized_reason,
            revision=revision + 1,
            updated_at=now.astimezone(timezone.utc),
        )

    async def enable(
        self, expected_revision: int | None = None
    ) -> ShadowControlStatus:
        return await self.set_status(True, expected_revision=expected_revision)

    async def disable(
        self,
        reason: str,
        expected_revision: int | None = None,
    ) -> ShadowControlStatus:
        return await self.set_status(
            False,
            reason=reason,
            expected_revision=expected_revision,
        )

    async def create_session(
        self, record: ShadowSessionRecord
    ) -> ShadowSessionRecord:
        if record.revision != 0:
            raise ValueError("new shadow sessions must start at revision zero")
        try:
            async with transaction() as db:
                await db.execute(
                    """
                    INSERT INTO autotrader_shadow_sessions
                    (id, run_id, source_sha, session_date, state, revision,
                     evidence_eligible, block_reason, transitioned_at, closed_at,
                     payload_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.id,
                        record.run_id,
                        record.source_sha,
                        record.session_date.isoformat(),
                        record.state.value,
                        record.revision,
                        int(record.evidence_eligible),
                        record.block_reason,
                        _timestamp(record.transitioned_at),
                        _timestamp(record.closed_at) if record.closed_at else None,
                        _payload(record),
                    ),
                )
        except aiosqlite.IntegrityError as exc:
            raise ShadowControlConflict(
                "a conflicting shadow session already exists"
            ) from exc
        return record

    async def get_session(
        self,
        session_id: str | None = None,
        *,
        source_sha: str | None = None,
        session_date: date | None = None,
    ) -> ShadowSessionRecord | None:
        filters: list[str] = []
        parameters: list[object] = []
        if session_id is not None:
            filters.append("id=?")
            parameters.append(session_id)
        if source_sha is not None:
            filters.append("source_sha=?")
            parameters.append(source_sha)
        if session_date is not None:
            filters.append("session_date=?")
            parameters.append(session_date.isoformat())
        where = f"WHERE {' AND '.join(filters)}" if filters else ""
        order = "ORDER BY transitioned_at DESC, id DESC LIMIT 1"
        async with get_db() as db:
            async with db.execute(
                f"""
                SELECT id, run_id, source_sha, session_date, state, revision,
                       evidence_eligible, block_reason, payload_json
                FROM autotrader_shadow_sessions {where} {order}
                """,
                tuple(parameters),
            ) as cursor:
                row = await cursor.fetchone()
        return _session_from_row(row) if row is not None else None

    async def compare_and_set_session(
        self,
        expected_revision: int,
        replacement: ShadowSessionRecord,
    ) -> ShadowSessionRecord:
        if expected_revision < 0:
            raise ValueError("expected_revision must be non-negative")
        if replacement.revision != expected_revision + 1:
            raise ValueError("replacement revision must advance exactly once")
        async with transaction() as db:
            cursor = await db.execute(
                """
                UPDATE autotrader_shadow_sessions
                SET run_id=?, source_sha=?, session_date=?, state=?, revision=?,
                    evidence_eligible=?, block_reason=?, transitioned_at=?, closed_at=?,
                    payload_json=?
                WHERE id=? AND revision=?
                """,
                (
                    replacement.run_id,
                    replacement.source_sha,
                    replacement.session_date.isoformat(),
                    replacement.state.value,
                    replacement.revision,
                    int(replacement.evidence_eligible),
                    replacement.block_reason,
                    _timestamp(replacement.transitioned_at),
                    _timestamp(replacement.closed_at)
                    if replacement.closed_at
                    else None,
                    _payload(replacement),
                    replacement.id,
                    expected_revision,
                ),
            )
            if cursor.rowcount != 1:
                raise ShadowControlConflict("shadow session revision is stale")
        return replacement

    async def try_compare_and_set_session(
        self,
        expected_revision: int,
        replacement: ShadowSessionRecord,
    ) -> bool:
        try:
            await self.compare_and_set_session(expected_revision, replacement)
        except ShadowControlConflict:
            return False
        return True


__all__ = [
    "SQLiteShadowControlStore",
    "ShadowControlConflict",
    "ShadowControlStatus",
    "ShadowSessionNotFound",
]

"""Transactional repositories for immutable Autotrader V1 strategy versions."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import uuid4

import aiosqlite

from db.core import get_db, transaction
from trading.contracts import Decision, StrategyRecord, StrategySpec, StrategyState
from trading.strategy_lifecycle import require_strategy_transition


def _timestamp() -> datetime:
    return datetime.now(timezone.utc)


def _dump(model: object) -> str:
    return json.dumps(model.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))


def _record_from_row(row: tuple[object, ...]) -> StrategyRecord:
    strategy_id, owner_id, version, state, revision, content_hash, spec_json, created_at, updated_at = row
    return StrategyRecord(
        strategy_id=str(strategy_id),
        owner_id=str(owner_id),
        version=int(version),
        state=StrategyState(str(state)),
        revision=int(revision),
        content_hash=str(content_hash),
        spec=StrategySpec.model_validate(json.loads(str(spec_json))),
        created_at=datetime.fromisoformat(str(created_at)),
        updated_at=datetime.fromisoformat(str(updated_at)),
    )


async def _load_record(
    db: aiosqlite.Connection,
    *,
    strategy_id: str,
    owner_id: str,
    version: int,
) -> StrategyRecord | None:
    async with db.execute(
        """
        SELECT i.id, i.owner_id, v.version, l.state, l.revision, v.content_hash,
               v.spec_json, v.created_at, l.updated_at
        FROM autotrader_strategy_identities i
        JOIN autotrader_strategy_versions v ON v.strategy_id=i.id
        JOIN autotrader_strategy_lifecycle_v2 l
          ON l.strategy_id=v.strategy_id AND l.version=v.version
        WHERE i.id=? AND i.owner_id=? AND v.version=?
        """,
        (strategy_id, owner_id, version),
    ) as cursor:
        row = await cursor.fetchone()
    return _record_from_row(row) if row else None


async def create_strategy(*, spec: StrategySpec, owner_id: str, actor: str) -> StrategyRecord:
    now = _timestamp()
    strategy_id = str(uuid4())
    content_hash = spec.fingerprint()
    async with transaction() as db:
        await db.execute(
            "INSERT INTO autotrader_strategy_identities VALUES (?, ?, ?, NULL)",
            (strategy_id, owner_id, now.isoformat()),
        )
        await db.execute(
            """
            INSERT INTO autotrader_strategy_versions
            (strategy_id, version, content_hash, spec_json, created_at, created_by)
            VALUES (?, 1, ?, ?, ?, ?)
            """,
            (strategy_id, content_hash, _dump(spec), now.isoformat(), actor),
        )
        await db.execute(
            "INSERT INTO autotrader_strategy_lifecycle_v2 VALUES (?, 1, 'draft', 0, ?)",
            (strategy_id, now.isoformat()),
        )
        await db.execute(
            """
            INSERT INTO autotrader_strategy_events_v2
            (id, strategy_id, strategy_version, owner_id, event_type, from_state, to_state,
             actor, reason, expected_revision, new_revision, created_at)
            VALUES (?, ?, 1, ?, 'created', NULL, 'draft', ?, 'strategy created', NULL, 0, ?)
            """,
            (str(uuid4()), strategy_id, owner_id, actor, now.isoformat()),
        )
    return StrategyRecord(
        strategy_id=strategy_id,
        owner_id=owner_id,
        version=1,
        content_hash=content_hash,
        spec=spec,
        created_at=now,
        updated_at=now,
    )


async def get_strategy(
    *, strategy_id: str, owner_id: str, version: int | None = None
) -> StrategyRecord | None:
    async with get_db() as db:
        if version is None:
            async with db.execute(
                "SELECT MAX(version) FROM autotrader_strategy_versions WHERE strategy_id=?",
                (strategy_id,),
            ) as cursor:
                row = await cursor.fetchone()
            if row is None or row[0] is None:
                return None
            version = int(row[0])
        return await _load_record(db, strategy_id=strategy_id, owner_id=owner_id, version=version)


async def list_strategies(*, owner_id: str) -> list[StrategyRecord]:
    async with get_db() as db:
        async with db.execute(
            """
            SELECT i.id, i.owner_id, v.version, l.state, l.revision, v.content_hash,
                   v.spec_json, v.created_at, l.updated_at
            FROM autotrader_strategy_identities i
            JOIN autotrader_strategy_versions v ON v.strategy_id=i.id
            JOIN autotrader_strategy_lifecycle_v2 l
              ON l.strategy_id=v.strategy_id AND l.version=v.version
            WHERE i.owner_id=? AND v.version=(
                SELECT MAX(v2.version) FROM autotrader_strategy_versions v2 WHERE v2.strategy_id=i.id
            )
            ORDER BY l.updated_at DESC
            """,
            (owner_id,),
        ) as cursor:
            rows = await cursor.fetchall()
    return [_record_from_row(row) for row in rows]


async def list_strategy_versions(*, strategy_id: str, owner_id: str) -> list[StrategyRecord]:
    async with get_db() as db:
        async with db.execute(
            """
            SELECT i.id, i.owner_id, v.version, l.state, l.revision, v.content_hash,
                   v.spec_json, v.created_at, l.updated_at
            FROM autotrader_strategy_identities i
            JOIN autotrader_strategy_versions v ON v.strategy_id=i.id
            JOIN autotrader_strategy_lifecycle_v2 l
              ON l.strategy_id=v.strategy_id AND l.version=v.version
            WHERE i.id=? AND i.owner_id=? ORDER BY v.version
            """,
            (strategy_id, owner_id),
        ) as cursor:
            rows = await cursor.fetchall()
    return [_record_from_row(row) for row in rows]


async def publish_strategy_version(
    *,
    strategy_id: str,
    owner_id: str,
    base_version: int,
    spec: StrategySpec,
    actor: str,
    reason: str,
) -> StrategyRecord:
    now = _timestamp()
    content_hash = spec.fingerprint()
    async with transaction() as db:
        async with db.execute(
            "SELECT owner_id FROM autotrader_strategy_identities WHERE id=?",
            (strategy_id,),
        ) as cursor:
            identity = await cursor.fetchone()
        if identity is None or str(identity[0]) != owner_id:
            raise LookupError("strategy not found")
        async with db.execute(
            "SELECT MAX(version) FROM autotrader_strategy_versions WHERE strategy_id=?",
            (strategy_id,),
        ) as cursor:
            latest = await cursor.fetchone()
        if latest is None or int(latest[0]) != base_version:
            raise ValueError("base_version is stale")
        new_version = base_version + 1
        try:
            await db.execute(
                """
                INSERT INTO autotrader_strategy_versions
                (strategy_id, version, content_hash, spec_json, created_at, created_by)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (strategy_id, new_version, content_hash, _dump(spec), now.isoformat(), actor),
            )
        except aiosqlite.IntegrityError as exc:
            raise ValueError("strategy version content must be new") from exc
        await db.execute(
            "INSERT INTO autotrader_strategy_lifecycle_v2 VALUES (?, ?, 'draft', 0, ?)",
            (strategy_id, new_version, now.isoformat()),
        )
        await db.execute(
            """
            INSERT INTO autotrader_strategy_events_v2
            (id, strategy_id, strategy_version, owner_id, event_type, from_state, to_state,
             actor, reason, expected_revision, new_revision, created_at)
            VALUES (?, ?, ?, ?, 'version_published', NULL, 'draft', ?, ?, NULL, 0, ?)
            """,
            (str(uuid4()), strategy_id, new_version, owner_id, actor, reason, now.isoformat()),
        )
    return StrategyRecord(
        strategy_id=strategy_id,
        owner_id=owner_id,
        version=new_version,
        content_hash=content_hash,
        spec=spec,
        created_at=now,
        updated_at=now,
    )


async def transition_strategy(
    *,
    strategy_id: str,
    version: int,
    owner_id: str,
    expected_revision: int,
    target: StrategyState,
    actor: str,
    reason: str,
) -> StrategyRecord:
    now = _timestamp()
    async with transaction() as db:
        record = await _load_record(
            db, strategy_id=strategy_id, owner_id=owner_id, version=version
        )
        if record is None:
            raise LookupError("strategy not found")
        if record.revision != expected_revision:
            raise ValueError("strategy revision is stale")
        require_strategy_transition(record=record, target=target)
        cursor = await db.execute(
            """
            UPDATE autotrader_strategy_lifecycle_v2
            SET state=?, revision=revision+1, updated_at=?
            WHERE strategy_id=? AND version=? AND revision=? AND state=?
            """,
            (
                target.value, now.isoformat(), strategy_id, version,
                expected_revision, record.state.value,
            ),
        )
        if cursor.rowcount != 1:
            raise ValueError("strategy transition conflict")
        await db.execute(
            """
            INSERT INTO autotrader_strategy_events_v2
            (id, strategy_id, strategy_version, owner_id, event_type, from_state, to_state,
             actor, reason, expected_revision, new_revision, created_at)
            VALUES (?, ?, ?, ?, 'state_transition', ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid4()), strategy_id, version, owner_id, record.state.value,
                target.value, actor, reason, expected_revision, expected_revision + 1,
                now.isoformat(),
            ),
        )
    return record.model_copy(
        update={"state": target, "revision": expected_revision + 1, "updated_at": now}
    )


async def record_decision(*, decision: Decision, owner_id: str) -> None:
    async with transaction() as db:
        async with db.execute(
            """
            SELECT owner_id, strategy_id, strategy_version
            FROM autotrader_runs WHERE id=?
            """,
            (decision.run_id,),
        ) as cursor:
            run = await cursor.fetchone()
        if run is None:
            raise ValueError("decision run does not exist")
        if str(run[0]) != owner_id:
            raise ValueError("decision owner does not own the run")
        if str(run[1]) != decision.strategy_id or int(run[2]) != decision.strategy_version:
            raise ValueError("decision strategy does not match the run")

        async with db.execute(
            """
            SELECT sr.run_id
            FROM autotrader_candidates c
            JOIN autotrader_scanner_runs sr ON sr.id=c.scanner_run_id
            WHERE c.id=?
            """,
            (decision.candidate_id,),
        ) as cursor:
            candidate = await cursor.fetchone()
        if candidate is None:
            raise ValueError("decision candidate does not exist")
        if str(candidate[0]) != decision.run_id:
            raise ValueError("decision candidate does not belong to the run")
        if decision.ai_review is not None:
            if decision.setup_fingerprint != decision.ai_review.setup_fingerprint:
                raise ValueError("decision setup does not match the AI review")
        if decision.risk is not None:
            if decision.setup_fingerprint != decision.risk.setup_fingerprint:
                raise ValueError("decision setup does not match the risk approval")
            if decision.risk.intent is not None:
                intent = decision.risk.intent
                if intent.strategy_id != decision.strategy_id:
                    raise ValueError("risk intent strategy does not match the decision")
        await db.execute(
            """
            INSERT INTO autotrader_candidate_decisions
            (id, run_id, candidate_id, strategy_id, strategy_version, decision_key, outcome,
             setup_fingerprint, ai_call_id, risk_reservation_id, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?)
            """,
            (
                decision.id, decision.run_id, decision.candidate_id, decision.strategy_id,
                decision.strategy_version, decision.decision_key, decision.outcome.value,
                decision.setup_fingerprint, _dump(decision), decision.created_at.isoformat(),
            ),
        )


async def list_decisions(
    *, owner_id: str, strategy_id: str | None = None, run_id: str | None = None, limit: int = 100
) -> list[dict]:
    safe_limit = min(max(limit, 1), 500)
    async with get_db() as db:
        async with db.execute(
            """
            SELECT d.payload_json
            FROM autotrader_candidate_decisions d
            JOIN autotrader_runs r ON r.id=d.run_id
            WHERE r.owner_id=?
              AND (? IS NULL OR d.strategy_id=?)
              AND (? IS NULL OR d.run_id=?)
            ORDER BY d.created_at DESC LIMIT ?
            """,
            (owner_id, strategy_id, strategy_id, run_id, run_id, safe_limit),
        ) as cursor:
            rows = await cursor.fetchall()
    return [json.loads(str(row[0])) for row in rows]

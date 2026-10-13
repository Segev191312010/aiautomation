"""Reads and writes of the manager's tables (migration 16); nothing else touches them.

Plans, verdicts and managed decisions are append-only (the schema aborts any
update or delete); the spend ledger reserves before a call and settles
after, like the assistant's chat budget, so a crash mid-call never loses
spend. Every timestamp is stored as ISO UTC text and every amount as plain
decimal text, as the schema requires.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
from typing import Any

from db.core import get_db, transaction
from trading.manager.constants import PLAN_VALID
from trading.manager.plan import DayPlan, plan_from_stored
from trading.storage.manager_schema import MANAGER_PHASE2_MIGRATION


def utc_text(moment: datetime) -> str:
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError("manager timestamps must be timezone-aware")
    return moment.astimezone(timezone.utc).isoformat()


def decimal_text(value: Decimal) -> str:
    if not value.is_finite() or value < 0:
        raise ValueError("manager amounts are finite and non-negative")
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".") or "0"
    return text


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _time(value: object) -> datetime | None:
    if value in (None, ""):
        return None
    return datetime.fromisoformat(str(value)).astimezone(timezone.utc)


def manager_tables_ready(database: str | Path) -> tuple[bool, str]:
    """``(ready, why not)``: migration 16 applied and the ledger otherwise sound.

    Read-only (``inspect_schema``); callers that are not children (the CLI's
    switch, the Telegram commands, the notifier) use it to refuse gracefully
    while the migration is pending.
    """
    from trading.storage.migrations import inspect_schema

    status = inspect_schema(database)
    if status.problems:
        return False, status.describe()
    if MANAGER_PHASE2_MIGRATION not in status.applied:
        return False, (
            f"the manager's tables arrive with migration {MANAGER_PHASE2_MIGRATION} "
            f"({status.describe()})"
        )
    return True, ""


# -- plans ---------------------------------------------------------------------------


def plan_id_for(session_date: date, slot: str, attempt: int) -> str:
    return f"{session_date:%Y%m%d}-{slot.replace(':', '')}-{attempt}"


@dataclass(frozen=True, slots=True)
class PlanRecord:
    """One plan attempt as written; ``plan`` is set exactly when the outcome is valid."""

    plan_id: str
    session_date: date
    slot: str
    created_at: datetime
    manager_version: str
    prompt_id: str
    model: str
    outcome: str
    candidates: tuple[int, ...]
    gates: Mapping[str, object]
    dropped: Sequence[Mapping[str, object]] = ()
    plan: DayPlan | None = None
    valid_until: datetime | None = None
    context_digest: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: Decimal = Decimal("0")
    latency_ms: int | None = None

    def __post_init__(self) -> None:
        if (self.outcome == PLAN_VALID) != (self.plan is not None):
            raise ValueError("a plan record carries a plan exactly when it is valid")


@dataclass(frozen=True, slots=True)
class StoredPlan:
    plan_id: str
    session_date: date
    slot: str
    created_at: datetime
    valid_until: datetime | None
    manager_version: str
    prompt_id: str
    model: str
    outcome: str
    plan: DayPlan | None
    candidates: tuple[int, ...]
    gates: Mapping[str, Any]
    dropped: tuple[Mapping[str, Any], ...]
    cost_usd: Decimal
    input_tokens: int | None
    output_tokens: int | None
    latency_ms: int | None

    @property
    def valid(self) -> bool:
        return self.outcome == PLAN_VALID and self.plan is not None

    def in_force(self, now: datetime) -> bool:
        return (
            self.valid
            and self.valid_until is not None
            and self.created_at <= now < self.valid_until
        )


_PLAN_COLUMNS = (
    "plan_id, session_date, slot, created_at, valid_until, manager_version, prompt_id, model, "
    "outcome, regime, strategies_json, focus_json, candidates_json, gates_json, dropped_json, "
    "context_digest, input_tokens, output_tokens, cost_usd, latency_ms"
)


async def insert_plan(record: PlanRecord) -> None:
    plan = record.plan
    values = (
        record.plan_id,
        record.session_date.isoformat(),
        record.slot,
        utc_text(record.created_at),
        utc_text(record.valid_until) if record.valid_until is not None else None,
        record.manager_version,
        record.prompt_id,
        record.model,
        record.outcome,
        plan.regime if plan is not None else None,
        _json(plan.strategies_payload()) if plan is not None else None,
        _json(plan.focus_payload()) if plan is not None else None,
        _json(list(record.candidates)),
        _json(dict(record.gates)),
        _json([dict(item) for item in record.dropped]),
        record.context_digest,
        record.input_tokens,
        record.output_tokens,
        decimal_text(record.cost_usd),
        record.latency_ms,
    )
    async with transaction() as db:
        await db.execute(
            f"INSERT INTO autotrader_manager_plans ({_PLAN_COLUMNS}) "
            f"VALUES ({', '.join('?' for _ in values)})",
            values,
        )


def _stored(row: Sequence[Any]) -> StoredPlan:
    (
        plan_id, session_date, slot, created_at, valid_until, version, prompt_id, model, outcome,
        regime, strategies_json, focus_json, candidates_json, gates_json, dropped_json,
        _digest, input_tokens, output_tokens, cost_usd, latency_ms,
    ) = row
    plan = None
    if outcome == PLAN_VALID:
        plan = plan_from_stored(
            regime=str(regime),
            strategies=json.loads(str(strategies_json)),
            focus=json.loads(str(focus_json)),
        )
    return StoredPlan(
        plan_id=str(plan_id),
        session_date=date.fromisoformat(str(session_date)),
        slot=str(slot),
        created_at=_time(created_at) or datetime.min.replace(tzinfo=timezone.utc),
        valid_until=_time(valid_until),
        manager_version=str(version),
        prompt_id=str(prompt_id),
        model=str(model),
        outcome=str(outcome),
        plan=plan,
        candidates=tuple(int(item) for item in json.loads(str(candidates_json))),
        gates=json.loads(str(gates_json)),
        dropped=tuple(json.loads(str(dropped_json))),
        cost_usd=Decimal(str(cost_usd)),
        input_tokens=None if input_tokens is None else int(input_tokens),
        output_tokens=None if output_tokens is None else int(output_tokens),
        latency_ms=None if latency_ms is None else int(latency_ms),
    )


async def list_plans(session_date: date) -> list[StoredPlan]:
    """Every attempt for the session, oldest first."""
    async with get_db() as db:
        async with db.execute(
            f"SELECT {_PLAN_COLUMNS} FROM autotrader_manager_plans "
            "WHERE session_date = ? ORDER BY created_at, plan_id",
            (session_date.isoformat(),),
        ) as cursor:
            rows = await cursor.fetchall()
    return [_stored(row) for row in rows]


async def newest_valid_plan(session_date: date, now: datetime) -> StoredPlan | None:
    """The newest valid plan written at or before ``now`` (it may have expired)."""
    async with get_db() as db:
        async with db.execute(
            f"SELECT {_PLAN_COLUMNS} FROM autotrader_manager_plans "
            "WHERE session_date = ? AND outcome = ? AND created_at <= ? "
            "ORDER BY created_at DESC, plan_id DESC LIMIT 1",
            (session_date.isoformat(), PLAN_VALID, utc_text(now)),
        ) as cursor:
            row = await cursor.fetchone()
    return None if row is None else _stored(row)


async def slot_attempts(session_date: date, slot: str) -> int:
    async with get_db() as db:
        async with db.execute(
            "SELECT count(*) FROM autotrader_manager_plans WHERE session_date = ? AND slot = ?",
            (session_date.isoformat(), slot),
        ) as cursor:
            row = await cursor.fetchone()
    return int(row[0]) if row else 0


async def slot_has_valid_plan(session_date: date, slot: str) -> bool:
    async with get_db() as db:
        async with db.execute(
            "SELECT 1 FROM autotrader_manager_plans WHERE session_date = ? AND slot = ? AND outcome = ?",
            (session_date.isoformat(), slot, PLAN_VALID),
        ) as cursor:
            return await cursor.fetchone() is not None


# -- verdicts and managed decisions -----------------------------------------------------


async def insert_verdict(
    *,
    run_id: str,
    setup_fingerprint: str,
    plan_id: str | None,
    mode: str,
    step: Decimal,
    note: str | None,
    created_at: datetime,
) -> None:
    async with transaction() as db:
        await db.execute(
            """INSERT INTO autotrader_manager_verdicts
               (run_id, setup_fingerprint, plan_id, mode, step, note, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(run_id, setup_fingerprint) DO NOTHING""",
            (run_id, setup_fingerprint, plan_id, mode, float(step), note, utc_text(created_at)),
        )


@dataclass(frozen=True, slots=True)
class ManagedDecisionRecord:
    decision_id: str
    run_id: str
    candidate_id: str
    con_id: int
    setup_fingerprint: str
    session_date: date
    outcome: str
    mode: str
    neutral_reason: str | None
    mark: str | None
    plan_id: str | None
    eligible: tuple[int, ...]
    focus: tuple[int, ...] | None
    gates: Mapping[str, object]
    day_step: Decimal
    setup_step: Decimal
    policy_qty: int | None
    final_qty: int | None
    created_at: datetime
    eligibility_id: str = field(default="")

    def __post_init__(self) -> None:
        if not self.eligibility_id:
            object.__setattr__(self, "eligibility_id", eligibility_id(self.eligible))


def eligibility_id(con_ids: Sequence[int]) -> str:
    """The eligibility snapshot's identity: a digest of its sorted contract set."""
    return sha256(_json(sorted(set(int(item) for item in con_ids))).encode("utf-8")).hexdigest()


async def insert_managed_decision(record: ManagedDecisionRecord) -> None:
    values = (
        record.decision_id,
        record.run_id,
        record.candidate_id,
        record.con_id,
        record.setup_fingerprint,
        record.session_date.isoformat(),
        record.outcome,
        record.mode,
        record.neutral_reason,
        record.mark,
        record.plan_id,
        record.eligibility_id,
        _json(sorted(set(record.eligible))),
        None if record.focus is None else _json(sorted(set(record.focus))),
        _json(dict(record.gates)),
        float(record.day_step),
        float(record.setup_step),
        record.policy_qty,
        record.final_qty,
        utc_text(record.created_at),
    )
    async with transaction() as db:
        await db.execute(
            """INSERT INTO autotrader_manager_decisions
               (decision_id, run_id, candidate_id, con_id, setup_fingerprint, session_date,
                outcome, mode, neutral_reason, mark, plan_id, eligibility_id, eligible_json,
                focus_json, gates_json, day_step, setup_step, policy_qty, final_qty, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            values,
        )


async def managed_decisions(session_date: date) -> list[dict[str, Any]]:
    async with get_db() as db:
        async with db.execute(
            """SELECT decision_id, run_id, candidate_id, con_id, outcome, mode, neutral_reason, mark,
                      plan_id, eligibility_id, eligible_json, focus_json, gates_json, day_step,
                      setup_step, policy_qty, final_qty, created_at
               FROM autotrader_manager_decisions WHERE session_date = ? ORDER BY id""",
            (session_date.isoformat(),),
        ) as cursor:
            columns = [item[0] for item in cursor.description or ()]
            rows = await cursor.fetchall()
    result = []
    for row in rows:
        item = dict(zip(columns, row, strict=True))
        for key in ("eligible_json", "focus_json", "gates_json"):
            item[key] = None if item[key] is None else json.loads(str(item[key]))
        result.append(item)
    return result


# -- spend -----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SpendRow:
    calls: int
    reserved_usd: Decimal
    spent_usd: Decimal

    @property
    def committed_usd(self) -> Decimal:
        return self.reserved_usd + self.spent_usd


async def reserve_spend(
    *, spend_date: date, purpose: str, budget_usd: Decimal, estimate_usd: Decimal, now: datetime
) -> bool:
    """Reserve ``estimate_usd`` before a call; False (nothing reserved) over budget."""
    if not estimate_usd.is_finite() or estimate_usd < 0:
        raise ValueError("a spend estimate is finite and non-negative")
    day = spend_date.isoformat()
    async with transaction() as db:
        await db.execute(
            """INSERT OR IGNORE INTO autotrader_manager_spend (spend_date, purpose, updated_at)
               VALUES (?, ?, ?)""",
            (day, purpose, utc_text(now)),
        )
        async with db.execute(
            "SELECT calls, reserved_usd, spent_usd FROM autotrader_manager_spend "
            "WHERE spend_date = ? AND purpose = ?",
            (day, purpose),
        ) as cursor:
            row = await cursor.fetchone()
        assert row is not None
        calls, reserved, spent = int(row[0]), Decimal(str(row[1])), Decimal(str(row[2]))
        if spent + reserved + estimate_usd > budget_usd:
            return False
        await db.execute(
            "UPDATE autotrader_manager_spend SET calls = ?, reserved_usd = ?, updated_at = ? "
            "WHERE spend_date = ? AND purpose = ?",
            (calls + 1, decimal_text(reserved + estimate_usd), utc_text(now), day, purpose),
        )
        return True


async def settle_spend(
    *, spend_date: date, purpose: str, estimate_usd: Decimal, actual_usd: Decimal, now: datetime
) -> None:
    """Release the reservation and record what the call actually cost."""
    day = spend_date.isoformat()
    async with transaction() as db:
        async with db.execute(
            "SELECT reserved_usd, spent_usd FROM autotrader_manager_spend "
            "WHERE spend_date = ? AND purpose = ?",
            (day, purpose),
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            raise LookupError("no spend reservation to settle")
        reserved = max(Decimal("0"), Decimal(str(row[0])) - estimate_usd)
        spent = Decimal(str(row[1])) + actual_usd
        await db.execute(
            "UPDATE autotrader_manager_spend SET reserved_usd = ?, spent_usd = ?, updated_at = ? "
            "WHERE spend_date = ? AND purpose = ?",
            (decimal_text(reserved), decimal_text(spent), utc_text(now), day, purpose),
        )


async def read_spend(*, spend_date: date, purpose: str) -> SpendRow:
    async with get_db() as db:
        async with db.execute(
            "SELECT calls, reserved_usd, spent_usd FROM autotrader_manager_spend "
            "WHERE spend_date = ? AND purpose = ?",
            (spend_date.isoformat(), purpose),
        ) as cursor:
            row = await cursor.fetchone()
    if row is None:
        return SpendRow(calls=0, reserved_usd=Decimal("0"), spent_usd=Decimal("0"))
    return SpendRow(calls=int(row[0]), reserved_usd=Decimal(str(row[1])), spent_usd=Decimal(str(row[2])))


__all__ = [
    "ManagedDecisionRecord",
    "PlanRecord",
    "SpendRow",
    "StoredPlan",
    "decimal_text",
    "eligibility_id",
    "insert_managed_decision",
    "insert_plan",
    "insert_verdict",
    "list_plans",
    "managed_decisions",
    "manager_tables_ready",
    "newest_valid_plan",
    "plan_id_for",
    "read_spend",
    "reserve_spend",
    "settle_spend",
    "slot_attempts",
    "slot_has_valid_plan",
    "utc_text",
]

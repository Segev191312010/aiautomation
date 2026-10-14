"""Read-only projections of the AI day manager for the local dashboard.

``GET /api/live/manager/{status,plan,plans,verdicts,spend}`` show the owner's
autopilot switch, the plan in force, today's plan history, the managed
reviewer's newest verdicts and the plan spend against its daily cap. Every
route only reads the manager's tables (migration 16), the day universe
(migration 15) and the notifier's per-date state file. There is no POST and
no route changes anything: the Telegram autopilot command and
``autotraderctl manager`` are the only control surfaces, so the panel these
feed has no buttons.

This module deliberately imports nothing from the manager package: a test
pins that no router can reach the switch or the planner, so the reads here
are plain SQL over the schema the manager's store writes, and the few
literals it shares with that package (the budget, the purpose, the outcome
and neutral tokens, the managed family) are pinned to it by a test.

While migration 16 is pending (or the ledger is otherwise not this code's)
every route answers 200 ``{"available": false, "reason": <schema token>}``
so the dashboard keeps polling and says why, instead of failing its poll.
Payloads carry no identifiers: no actor, account, chat or run ids.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import json
import logging
from pathlib import Path
from typing import Annotated, Any, Literal

import aiosqlite
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict

from auth import get_current_user
from config import cfg
from db.core import get_db
from routers.live_routes import (
    ClockDependency,
    _iso_utc as iso_utc,
    _parse_stored_time as parse_stored_time,
    _require_utc as require_utc,
    new_york_date,
)
from trading.operations.paths import UserPaths
from trading.storage.manager_schema import MANAGER_PHASE2_MIGRATION
from trading.storage.migrations import SCHEMA_MIGRATION_PENDING, inspect_schema

log = logging.getLogger(__name__)

# Literals shared with the manager package, pinned to it by a test.
MANAGER_DAILY_BUDGET_USD = Decimal("5")
PLAN_SPEND_PURPOSE = "plan"
PLAN_VALID = "valid"
PLAN_MISSING = "plan_missing"
PLAN_EXPIRED = "plan_expired"
MANAGED_FAMILY = "momo"

# The notifier keeps one private state file per session date here
# (trading.operations.notify_service.state_file); a test pins the layout.
NOTIFIER_STATE_DIRNAME = "notify"
# The notifier's rate-limit key for an IBKR session problem it alerted about:
# a competing login (the owner's phone or web login) or a lost TWS connection
# share this key, so the dashboard names both. A test pins the literal.
IBKR_SESSION_ALERT_KEY = "ibkr_session"
# The notifier repeats the alert every 10 minutes while the problem persists;
# an alert older than this means the logs went quiet and the panel stands down.
COMPETING_LOGIN_ACTIVE_WINDOW = timedelta(minutes=15)
VERDICTS_DEFAULT_LIMIT = 20
VERDICTS_MAX_LIMIT = 200


def notifier_state_directory() -> Path:
    """Directory of the notifier's per-date state files; tests override it."""
    return UserPaths.current().data_directory / NOTIFIER_STATE_DIRNAME


router = APIRouter(
    prefix="/api/live/manager",
    tags=["live"],
    dependencies=[Depends(get_current_user)],
)

NotifierDirectoryDependency = Annotated[Path, Depends(notifier_state_directory)]


# --------------------------------------------------------------------------
# Response models
# --------------------------------------------------------------------------


class _View(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ManagerUnavailable(_View):
    """The manager's tables are not readable by this code (migration pending or worse)."""

    available: Literal[False] = False
    reason: str
    detail: str


class SwitchView(_View):
    state: Literal["on", "off"]
    since: str | None
    source: Literal["cli", "telegram"] | None
    reason: str | None


class LatchView(_View):
    family: str
    mode: str
    managed: bool
    latched_at: str


class MigrationsView(_View):
    applied_version: int
    code_version: int
    current: bool


class PlanCountsView(_View):
    attempts: int
    valid: int
    in_force: str | None


class SpendSummary(_View):
    spend_date: date
    purpose: str
    calls: int
    spent_usd: str
    reserved_usd: str
    committed_usd: str
    budget_usd: str
    remaining_usd: str
    used_pct: float
    exhausted: bool


class SpendView(SpendSummary):
    available: Literal[True] = True


class DayUniverseView(_View):
    qualified: int
    seen: int


class CompetingLoginView(_View):
    active: bool
    last_alert_at: str | None


class ManagerStatusView(_View):
    available: Literal[True] = True
    server_time_utc: str
    session_date: date
    switch: SwitchView
    latch: LatchView | None
    migrations: MigrationsView
    plans: PlanCountsView
    spend: SpendSummary
    day_universe: DayUniverseView
    competing_login: CompetingLoginView


class StrategyStepView(_View):
    strategy: str
    step: str
    on: bool
    reason: str


class FocusEntryView(_View):
    con_id: int
    symbol: str | None
    strategies: list[str]
    note: str


class DroppedView(_View):
    kind: str
    token: str
    detail: str
    widening: bool


class PlanSummary(_View):
    """One plan attempt as the history lists it."""

    plan_id: str
    slot: str
    attempt: int
    created_at: str
    valid_until: str | None
    outcome: str
    valid: bool
    in_force: bool
    regime: str | None
    focus_count: int | None
    dropped_count: int
    latency_ms: int | None
    cost_usd: str
    input_tokens: int | None
    output_tokens: int | None
    model: str
    manager_version: str


class PlanView(PlanSummary):
    """A valid plan with its whole content."""

    strategies: list[StrategyStepView]
    focus: list[FocusEntryView]
    dropped: list[DroppedView]
    gates: dict[str, bool]


class CurrentPlanView(_View):
    available: Literal[True] = True
    session_date: date
    in_force: bool
    plan: PlanView | None
    last_attempt: PlanSummary | None
    neutral_reason: str | None


class PlansView(_View):
    available: Literal[True] = True
    session_date: date
    plans: list[PlanSummary]


class VerdictView(_View):
    id: int
    created_at: str
    mode: Literal["managed", "neutral"]
    step: str
    skipped: bool
    note: str | None
    plan_id: str | None
    symbol: str | None
    strategy: str | None
    detected_at: str | None
    entry_price: str | None
    stop_price: str | None
    target_price: str | None


class VerdictsView(_View):
    available: Literal[True] = True
    session_date: date
    verdicts: list[VerdictView]


# --------------------------------------------------------------------------
# Stored rows
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PlanRow:
    """One ``autotrader_manager_plans`` row, parsed but not re-validated."""

    plan_id: str
    slot: str
    created_at: datetime
    valid_until: datetime | None
    manager_version: str
    model: str
    outcome: str
    regime: str | None
    strategies: Mapping[str, Mapping[str, Any]]
    focus: Sequence[Mapping[str, Any]]
    gates: Mapping[str, Any]
    dropped: Sequence[Mapping[str, Any]]
    input_tokens: int | None
    output_tokens: int | None
    cost_usd: Decimal
    latency_ms: int | None

    @property
    def valid(self) -> bool:
        return self.outcome == PLAN_VALID

    def in_force(self, now: datetime) -> bool:
        return self.valid and self.valid_until is not None and self.created_at <= now < self.valid_until


@dataclass(frozen=True, slots=True)
class SpendRow:
    calls: int
    reserved_usd: Decimal
    spent_usd: Decimal

    @property
    def committed_usd(self) -> Decimal:
        return self.reserved_usd + self.spent_usd


# --------------------------------------------------------------------------
# Pure projections
# --------------------------------------------------------------------------


def decimal_text(value: Decimal) -> str:
    """Plain decimal text without exponent or trailing zeros ("0.5", "5")."""
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".") or "0"
    return text


def _decimal(value: object, default: Decimal = Decimal("0")) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return default
    return parsed if parsed.is_finite() else default


def _int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(str(value))
    except ValueError:
        return None


def _json(value: object, kind: type) -> Any:
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError):
        return kind()
    return parsed if isinstance(parsed, kind) else kind()


def schema_unavailable_reason(database: str | Path) -> tuple[str, str] | None:
    """``(token, detail)`` while this code must not read the manager's tables.

    Read-only (``inspect_schema``). Migration 16 applied is enough: a newer
    pending migration leaves these tables readable.
    """
    status = inspect_schema(database)
    if status.problems:
        return status.token or SCHEMA_MIGRATION_PENDING, status.describe()
    if MANAGER_PHASE2_MIGRATION not in status.applied:
        return SCHEMA_MIGRATION_PENDING, status.describe()
    return None


def _unavailable(reason: tuple[str, str]) -> ManagerUnavailable:
    return ManagerUnavailable(reason=reason[0], detail=reason[1])


def project_switch(row: Sequence[object] | None) -> SwitchView:
    """The newest switch event; no event means OFF (the schema's own view says so)."""
    if row is None:
        return SwitchView(state="off", since=None, source=None, reason=None)
    ts, action, source, reason = row
    moment = parse_stored_time(ts)
    return SwitchView(
        state="on" if action == "on" else "off",
        since=iso_utc(moment) if moment is not None else None,
        source=source if source in ("cli", "telegram") else None,  # type: ignore[arg-type]
        reason=None if reason is None else str(reason),
    )


def project_latch(row: Sequence[object] | None) -> LatchView | None:
    if row is None:
        return None
    mode, latched_at = row
    moment = parse_stored_time(latched_at)
    return LatchView(
        family=MANAGED_FAMILY,
        mode=str(mode),
        managed=mode == "managed",
        latched_at=iso_utc(moment) if moment is not None else str(latched_at),
    )


def project_spend(spend: SpendRow, *, spend_date: date) -> SpendSummary:
    budget = MANAGER_DAILY_BUDGET_USD
    committed = spend.committed_usd
    remaining = max(Decimal("0"), budget - committed)
    used = float((committed / budget * 100).quantize(Decimal("0.1"))) if budget > 0 else 0.0
    return SpendSummary(
        spend_date=spend_date,
        purpose=PLAN_SPEND_PURPOSE,
        calls=spend.calls,
        spent_usd=decimal_text(spend.spent_usd),
        reserved_usd=decimal_text(spend.reserved_usd),
        committed_usd=decimal_text(committed),
        budget_usd=decimal_text(budget),
        remaining_usd=decimal_text(remaining),
        used_pct=min(used, 100.0),
        exhausted=committed >= budget,
    )


def plan_row(row: Sequence[object]) -> PlanRow:
    (
        plan_id, slot, created_at, valid_until, version, model, outcome, regime,
        strategies_json, focus_json, gates_json, dropped_json,
        input_tokens, output_tokens, cost_usd, latency_ms,
    ) = row
    created = parse_stored_time(created_at)
    return PlanRow(
        plan_id=str(plan_id),
        slot=str(slot),
        created_at=created if created is not None else datetime.min.replace(tzinfo=timezone.utc),
        valid_until=parse_stored_time(valid_until) if valid_until is not None else None,
        manager_version=str(version),
        model=str(model),
        outcome=str(outcome),
        regime=None if regime is None else str(regime),
        strategies=_json(strategies_json, dict) if strategies_json is not None else {},
        focus=_json(focus_json, list) if focus_json is not None else [],
        gates=_json(gates_json, dict),
        dropped=_json(dropped_json, list),
        input_tokens=_int(input_tokens),
        output_tokens=_int(output_tokens),
        cost_usd=_decimal(cost_usd),
        latency_ms=_int(latency_ms),
    )


def plan_in_force(plans: Sequence[PlanRow], now: datetime) -> PlanRow | None:
    """The newest valid plan whose window holds ``now`` (plans are in creation order)."""
    current = [plan for plan in plans if plan.in_force(now)]
    return current[-1] if current else None


def attempt_numbers(plans: Sequence[PlanRow]) -> dict[str, int]:
    """Each plan's attempt number within its slot, in creation order."""
    seen: dict[str, int] = {}
    numbered: dict[str, int] = {}
    for plan in sorted(plans, key=lambda item: (item.created_at, item.plan_id)):
        seen[plan.slot] = seen.get(plan.slot, 0) + 1
        numbered[plan.plan_id] = seen[plan.slot]
    return numbered


def project_plan_summary(plan: PlanRow, *, attempt: int, in_force: bool) -> PlanSummary:
    """``in_force`` is the caller's: only the newest live plan is in force, not every live one."""
    return PlanSummary(
        plan_id=plan.plan_id,
        slot=plan.slot,
        attempt=attempt,
        created_at=iso_utc(plan.created_at),
        valid_until=iso_utc(plan.valid_until) if plan.valid_until is not None else None,
        outcome=plan.outcome,
        valid=plan.valid,
        in_force=in_force,
        regime=plan.regime if plan.valid else None,
        focus_count=len(plan.focus) if plan.valid else None,
        dropped_count=len(plan.dropped),
        latency_ms=plan.latency_ms,
        cost_usd=decimal_text(plan.cost_usd),
        input_tokens=plan.input_tokens,
        output_tokens=plan.output_tokens,
        model=plan.model,
        manager_version=plan.manager_version,
    )


def _strategy_steps(strategies: Mapping[str, Mapping[str, Any]]) -> list[StrategyStepView]:
    views = []
    for name, item in sorted(strategies.items()):
        if not isinstance(item, Mapping):
            continue
        step = _decimal(item.get("step"), Decimal("1"))
        views.append(
            StrategyStepView(
                strategy=str(name),
                step=decimal_text(step),
                on=step > 0,
                reason=str(item.get("reason", "")),
            )
        )
    return views


def _focus_entries(
    focus: Sequence[Mapping[str, Any]], symbols: Mapping[int, str]
) -> list[FocusEntryView]:
    views = []
    for item in focus:
        if not isinstance(item, Mapping):
            continue
        con_id = _int(item.get("con_id"))
        if con_id is None or con_id <= 0:
            continue
        names = item.get("strategies")
        views.append(
            FocusEntryView(
                con_id=con_id,
                symbol=symbols.get(con_id),
                strategies=[str(name) for name in names] if isinstance(names, list) else [],
                note=str(item.get("note", "")),
            )
        )
    return views


def _dropped(items: Iterable[Mapping[str, Any]]) -> list[DroppedView]:
    views = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        views.append(
            DroppedView(
                kind=str(item.get("kind", "")),
                token=str(item.get("token", "")),
                detail=str(item.get("detail", "")),
                widening=bool(item.get("widening", False)),
            )
        )
    return views


def project_plan(
    plan: PlanRow, *, attempt: int, in_force: bool, symbols: Mapping[int, str]
) -> PlanView:
    summary = project_plan_summary(plan, attempt=attempt, in_force=in_force)
    return PlanView(
        **summary.model_dump(),
        strategies=_strategy_steps(plan.strategies),
        focus=_focus_entries(plan.focus, symbols),
        dropped=_dropped(plan.dropped),
        gates={str(key): value for key, value in plan.gates.items() if isinstance(value, bool)},
    )


def neutral_reason(plans: Sequence[PlanRow], now: datetime) -> str | None:
    """Why managed strategies run neutral now, or None while a plan is in force."""
    if plan_in_force(plans, now) is not None:
        return None
    if any(plan.valid and plan.created_at <= now for plan in plans):
        return PLAN_EXPIRED
    return PLAN_MISSING


def competing_login_status(directory: Path, session_day: date, now: datetime) -> CompetingLoginView:
    """The notifier's newest IBKR session alert for the day, from its state file.

    Read-only; a missing, unreadable or foreign-shaped file reads as no alert.
    """
    inactive = CompetingLoginView(active=False, last_alert_at=None)
    path = directory / f"{session_day.isoformat()}.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return inactive
    if not isinstance(raw, dict) or raw.get("session_date") != session_day.isoformat():
        return inactive
    alerts = raw.get("alerts")
    stamp = alerts.get(IBKR_SESSION_ALERT_KEY) if isinstance(alerts, dict) else None
    seen = parse_stored_time(stamp) if isinstance(stamp, str) else None
    if seen is None:
        return inactive
    age = now - seen
    return CompetingLoginView(
        active=timedelta(0) <= age < COMPETING_LOGIN_ACTIVE_WINDOW,
        last_alert_at=iso_utc(seen),
    )


def _price(value: object) -> str | None:
    if value is None:
        return None
    parsed = _decimal(value, Decimal("NaN"))
    return str(value) if not parsed.is_finite() else decimal_text(parsed)


def project_verdict(row: Sequence[object]) -> VerdictView:
    (
        verdict_id, created_at, mode, step, note, plan_id,
        symbol, strategy, detected_at, entry, stop, target,
    ) = row
    step_value = _decimal(step, Decimal("1"))
    created = parse_stored_time(created_at)
    detected = parse_stored_time(detected_at) if detected_at is not None else None
    return VerdictView(
        id=int(str(verdict_id)),
        created_at=iso_utc(created) if created is not None else str(created_at),
        mode="managed" if mode == "managed" else "neutral",
        step=decimal_text(step_value),
        skipped=step_value == 0,
        note=None if note is None else str(note),
        plan_id=None if plan_id is None else str(plan_id),
        symbol=None if symbol is None else str(symbol),
        strategy=None if strategy is None else str(strategy),
        detected_at=iso_utc(detected) if detected is not None else None,
        entry_price=_price(entry),
        stop_price=_price(stop),
        target_price=_price(target),
    )


# --------------------------------------------------------------------------
# Queries (read-only; every polled one reads an index or a tiny table)
# --------------------------------------------------------------------------


_LATEST_SWITCH_EVENT = (
    "SELECT ts, action, source, reason FROM autotrader_manager_events ORDER BY id DESC LIMIT 1"
)
_DAY_LATCH = (
    "SELECT mode, latched_at FROM autotrader_manager_day_mode "
    "WHERE session_date = ? AND strategy_family = ?"
)
_PLAN_COLUMNS = (
    "plan_id, slot, created_at, valid_until, manager_version, model, outcome, regime, "
    "strategies_json, focus_json, gates_json, dropped_json, "
    "input_tokens, output_tokens, cost_usd, latency_ms"
)
# (session_date, created_at) is indexed.
_DAY_PLANS = (
    f"SELECT {_PLAN_COLUMNS} FROM autotrader_manager_plans "
    "WHERE session_date = ? ORDER BY created_at, plan_id"
)
_DAY_SPEND = (
    "SELECT calls, reserved_usd, spent_usd FROM autotrader_manager_spend "
    "WHERE spend_date = ? AND purpose = ?"
)
# (session_date, qualified, best_rank) is indexed: one range read per poll.
_DAY_UNIVERSE_COUNTS = (
    "SELECT COUNT(*), COALESCE(SUM(qualified), 0) FROM autotrader_manager_day_universe "
    "WHERE session_date = ?"
)
# Verdicts carry no session date: the run names it. The setup row is written
# right after the verdict, so it is joined loosely and may be absent for a
# moment (the symbol then reads null, never a wrong one).
_DAY_VERDICTS = """
    SELECT v.id, v.created_at, v.mode, v.step, v.note, v.plan_id,
           s.symbol, s.strategy_name, s.detected_at, s.entry_price, s.stop_price, s.target_price
    FROM autotrader_manager_verdicts v
    JOIN autotrader_runs r ON r.id = v.run_id
    LEFT JOIN autotrader_manager_setups s
      ON s.run_id = v.run_id AND s.setup_fingerprint = v.setup_fingerprint
    WHERE r.session_date = ?
    ORDER BY v.id DESC LIMIT ?
"""
# Display symbols for the focus, best source last: the session's own day
# universe wins over the watchlist, which wins over cached contract facts.
_SYMBOL_SOURCES = (
    "autotrader_manager_contract_info",
    "autotrader_universe",
    "autotrader_manager_day_universe",
)


async def _latest_switch(db: aiosqlite.Connection) -> Sequence[object] | None:
    async with db.execute(_LATEST_SWITCH_EVENT) as cursor:
        return await cursor.fetchone()


async def _latch(db: aiosqlite.Connection, session_day: date) -> Sequence[object] | None:
    async with db.execute(_DAY_LATCH, (session_day.isoformat(), MANAGED_FAMILY)) as cursor:
        return await cursor.fetchone()


async def _plans(db: aiosqlite.Connection, session_day: date) -> list[PlanRow]:
    async with db.execute(_DAY_PLANS, (session_day.isoformat(),)) as cursor:
        rows = await cursor.fetchall()
    return [plan_row(row) for row in rows]


async def _spend(db: aiosqlite.Connection, session_day: date) -> SpendRow:
    async with db.execute(_DAY_SPEND, (session_day.isoformat(), PLAN_SPEND_PURPOSE)) as cursor:
        row = await cursor.fetchone()
    if row is None:
        return SpendRow(calls=0, reserved_usd=Decimal("0"), spent_usd=Decimal("0"))
    return SpendRow(calls=_int(row[0]) or 0, reserved_usd=_decimal(row[1]), spent_usd=_decimal(row[2]))


async def _day_universe_counts(db: aiosqlite.Connection, session_day: date) -> DayUniverseView:
    async with db.execute(_DAY_UNIVERSE_COUNTS, (session_day.isoformat(),)) as cursor:
        row = await cursor.fetchone()
    seen, qualified = (int(row[0]), int(row[1])) if row else (0, 0)
    return DayUniverseView(qualified=qualified, seen=seen)


async def _symbols(db: aiosqlite.Connection, con_ids: Iterable[int]) -> dict[int, str]:
    wanted = sorted({int(item) for item in con_ids})
    if not wanted:
        return {}
    marks = ", ".join("?" for _ in wanted)
    symbols: dict[int, str] = {}
    for table in _SYMBOL_SOURCES:
        async with db.execute(
            f"SELECT con_id, MIN(symbol) FROM {table} WHERE con_id IN ({marks}) GROUP BY con_id",
            wanted,
        ) as cursor:
            for con_id, symbol in await cursor.fetchall():
                if symbol:
                    symbols[int(con_id)] = str(symbol)
    return symbols


def _focus_con_ids(plan: PlanRow) -> list[int]:
    ids = []
    for item in plan.focus:
        con_id = _int(item.get("con_id")) if isinstance(item, Mapping) else None
        if con_id is not None and con_id > 0:
            ids.append(con_id)
    return ids


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------


@router.get("/status")
async def get_manager_status(
    clock: ClockDependency, notifier_directory: NotifierDirectoryDependency
) -> ManagerStatusView | ManagerUnavailable:
    now = require_utc(clock())
    session_day = new_york_date(now)
    status = inspect_schema(cfg.DB_PATH)
    if status.problems or MANAGER_PHASE2_MIGRATION not in status.applied:
        return _unavailable((status.token or SCHEMA_MIGRATION_PENDING, status.describe()))
    async with get_db() as db:
        switch = await _latest_switch(db)
        latch = await _latch(db, session_day)
        plans = await _plans(db, session_day)
        spend = await _spend(db, session_day)
        universe = await _day_universe_counts(db, session_day)
    current = plan_in_force(plans, now)
    return ManagerStatusView(
        server_time_utc=iso_utc(now),
        session_date=session_day,
        switch=project_switch(switch),
        latch=project_latch(latch),
        migrations=MigrationsView(
            applied_version=status.applied_version,
            code_version=status.code_version,
            current=status.current,
        ),
        plans=PlanCountsView(
            attempts=len(plans),
            valid=sum(1 for plan in plans if plan.valid),
            in_force=current.plan_id if current is not None else None,
        ),
        spend=project_spend(spend, spend_date=session_day),
        day_universe=universe,
        competing_login=competing_login_status(notifier_directory, session_day, now),
    )


@router.get("/plan")
async def get_manager_plan(clock: ClockDependency) -> CurrentPlanView | ManagerUnavailable:
    """The plan in force now with its whole content, or why none is."""
    now = require_utc(clock())
    session_day = new_york_date(now)
    reason = schema_unavailable_reason(cfg.DB_PATH)
    if reason is not None:
        return _unavailable(reason)
    async with get_db() as db:
        plans = await _plans(db, session_day)
        current = plan_in_force(plans, now)
        symbols = await _symbols(db, _focus_con_ids(current)) if current is not None else {}
    attempts = attempt_numbers(plans)
    if current is None:
        written = [plan for plan in plans if plan.created_at <= now]
        last = written[-1] if written else None
        return CurrentPlanView(
            session_date=session_day,
            in_force=False,
            plan=None,
            last_attempt=(
                project_plan_summary(last, attempt=attempts[last.plan_id], in_force=False)
                if last is not None
                else None
            ),
            neutral_reason=neutral_reason(plans, now),
        )
    return CurrentPlanView(
        session_date=session_day,
        in_force=True,
        plan=project_plan(
            current, attempt=attempts[current.plan_id], in_force=True, symbols=symbols
        ),
        last_attempt=None,
        neutral_reason=None,
    )


@router.get("/plans")
async def get_manager_plans(clock: ClockDependency) -> PlansView | ManagerUnavailable:
    """Every plan attempt of the session, newest first, with its outcome token."""
    now = require_utc(clock())
    session_day = new_york_date(now)
    reason = schema_unavailable_reason(cfg.DB_PATH)
    if reason is not None:
        return _unavailable(reason)
    async with get_db() as db:
        plans = await _plans(db, session_day)
    attempts = attempt_numbers(plans)
    current = plan_in_force(plans, now)
    return PlansView(
        session_date=session_day,
        plans=[
            project_plan_summary(
                plan,
                attempt=attempts[plan.plan_id],
                in_force=current is not None and plan.plan_id == current.plan_id,
            )
            for plan in reversed(plans)
        ],
    )


@router.get("/verdicts")
async def get_manager_verdicts(
    clock: ClockDependency,
    limit: Annotated[int, Query(ge=1, le=VERDICTS_MAX_LIMIT)] = VERDICTS_DEFAULT_LIMIT,
) -> VerdictsView | ManagerUnavailable:
    """The managed reviewer's newest verdicts of the session with their setups."""
    now = require_utc(clock())
    session_day = new_york_date(now)
    reason = schema_unavailable_reason(cfg.DB_PATH)
    if reason is not None:
        return _unavailable(reason)
    async with get_db() as db:
        async with db.execute(_DAY_VERDICTS, (session_day.isoformat(), limit)) as cursor:
            rows = await cursor.fetchall()
    return VerdictsView(session_date=session_day, verdicts=[project_verdict(row) for row in rows])


@router.get("/spend")
async def get_manager_spend(clock: ClockDependency) -> SpendView | ManagerUnavailable:
    """Today's plan spend against the manager's fixed daily budget."""
    now = require_utc(clock())
    session_day = new_york_date(now)
    reason = schema_unavailable_reason(cfg.DB_PATH)
    if reason is not None:
        return _unavailable(reason)
    async with get_db() as db:
        spend = await _spend(db, session_day)
    return SpendView(**project_spend(spend, spend_date=session_day).model_dump())


__all__ = [
    "COMPETING_LOGIN_ACTIVE_WINDOW",
    "IBKR_SESSION_ALERT_KEY",
    "MANAGED_FAMILY",
    "MANAGER_DAILY_BUDGET_USD",
    "NOTIFIER_STATE_DIRNAME",
    "PLAN_EXPIRED",
    "PLAN_MISSING",
    "PLAN_SPEND_PURPOSE",
    "PLAN_VALID",
    "CompetingLoginView",
    "CurrentPlanView",
    "ManagerStatusView",
    "ManagerUnavailable",
    "PlanRow",
    "PlanSummary",
    "PlanView",
    "PlansView",
    "SpendRow",
    "SpendView",
    "VerdictView",
    "VerdictsView",
    "attempt_numbers",
    "competing_login_status",
    "decimal_text",
    "neutral_reason",
    "notifier_state_directory",
    "plan_in_force",
    "plan_row",
    "project_latch",
    "project_plan",
    "project_plan_summary",
    "project_spend",
    "project_switch",
    "project_verdict",
    "router",
    "schema_unavailable_reason",
]

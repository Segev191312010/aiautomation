"""The plan writer: one Claude call per slot, inside the spend ledger and a 90 s wrapper.

Each slot (``schedule.plan_times``) builds the context pack from the
database, reserves the call's worst case in the spend ledger, asks Claude
for a ``DayPlanOutput`` with ``messages.parse`` (effort medium), and passes
the answer through the narrow-only gate. Every attempt, valid or not, is
appended to ``autotrader_manager_plans`` with the manager version, the
prompt id, its cost and its outcome token. Over budget, no call is made and
no new plan is written: managed strategies run in neutral mode.

The manager child always runs in shadow, whatever the autopilot switch
says: its plans are evidence even while nothing follows them.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
import logging
import time as monotonic_time
from typing import Any, Protocol

from pydantic import ValidationError

from trading.intelligence.anthropic_transport import (
    CLAUDE_OPUS_5_5_PRICING,
    FALLBACK_CEILING_PRICING,
)
from trading.intelligence.manager_prompts import DAY_PLAN_PROMPT_ID, day_plan_prompt
from trading.intelligence.pricing import ModelPricing
from trading.manager.constants import (
    AI_MALFORMED,
    AI_REFUSAL,
    AI_TIMEOUT,
    AI_TRUNCATED,
    AI_UNAVAILABLE,
    BUDGET_EXHAUSTED,
    MANAGER_DAILY_BUDGET_USD,
    MANAGER_EFFORT,
    MANAGER_MODEL,
    MANAGER_VERSION,
    PLAN_MAX_TOKENS,
    PLAN_RETRY_AFTER,
    PLAN_SPEND_PURPOSE,
    PLAN_TIMEOUT_SECONDS,
    PLAN_TTL,
    PLAN_VALID,
)
from trading.manager.context import ContextPack, build_context
from trading.manager.plan import DayPlanOutput, NarrowResult, narrow_plan
from trading.manager.schedule import next_slot, plan_times, slot_label
from trading.manager.store import (
    PlanRecord,
    insert_plan,
    plan_id_for,
    reserve_spend,
    settle_spend,
    slot_attempts,
    slot_has_valid_plan,
)
from trading.scheduler.sessions import MarketSession

logger = logging.getLogger("autotrader.manager.planner")

FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_ATTEMPTS_PER_SLOT = 2
# Reservation: the input at about three characters a token plus the system
# prompt and request overhead, and the whole output budget (thinking
# included), at the primary model's prices.
ESTIMATE_CHARS_PER_TOKEN = 3
ESTIMATE_OVERHEAD_TOKENS = 600

Clock = Callable[[], datetime]
Sleeper = Callable[[float], Awaitable[None]]
ContextBuilder = Callable[..., Awaitable[ContextPack]]


@dataclass(frozen=True, slots=True)
class PlanResponse:
    """One answer: the parsed JSON object (or None), why the model stopped, what it cost."""

    answer: Mapping[str, Any] | None
    stop_reason: str | None
    served_model: str | None
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal


class PlanTransport(Protocol):
    async def plan(self, *, system: str, user: str) -> PlanResponse: ...


class PlanParseError(RuntimeError):
    """The answer did not parse as a DayPlanOutput; its cost is unknown."""


def _usage(usage: object, name: str) -> int:
    value = getattr(usage, name, None)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return value


class AnthropicPlanTransport:
    """``messages.parse(output_format=DayPlanOutput)`` on an injected official client."""

    def __init__(
        self,
        *,
        client: Any,
        model: str = MANAGER_MODEL,
        effort: str = MANAGER_EFFORT,
        max_tokens: int = PLAN_MAX_TOKENS,
        pricing: ModelPricing = CLAUDE_OPUS_5_5_PRICING,
        fallback_pricing: ModelPricing = FALLBACK_CEILING_PRICING,
    ) -> None:
        self._client = client
        self._model = model
        self._effort = effort
        self._max_tokens = max_tokens
        self._pricing = pricing
        self._fallback_pricing = fallback_pricing

    async def plan(self, *, system: str, user: str) -> PlanResponse:
        try:
            response = await self._client.messages.parse(
                model=self._model,
                max_tokens=self._max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_config={"effort": self._effort},
                output_format=DayPlanOutput,
                extra_headers={"anthropic-beta": FALLBACK_BETA},
                extra_body={"fallbacks": "default"},
            )
        except (ValidationError, ValueError) as exc:
            # The SDK parses before returning, so the usage is lost with it.
            raise PlanParseError(type(exc).__name__) from exc
        usage = getattr(response, "usage", None)
        input_tokens = _usage(usage, "input_tokens")
        output_tokens = _usage(usage, "output_tokens")
        cache_read = _usage(usage, "cache_read_input_tokens")
        cache_write = _usage(usage, "cache_creation_input_tokens")
        served = getattr(response, "model", None)
        pricing = self._pricing if served == self._model else self._fallback_pricing
        cost = pricing.cost(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_input_tokens=cache_read,
            cache_write_tokens=cache_write,
        )
        stop_reason = getattr(response, "stop_reason", None)
        parsed = getattr(response, "parsed_output", None) if stop_reason == "end_turn" else None
        answer: Mapping[str, Any] | None = None
        if isinstance(parsed, DayPlanOutput):
            # model_dump keeps extra keys, so an echoed order field is still seen.
            answer = parsed.model_dump()
        return PlanResponse(
            answer=answer,
            stop_reason=None if stop_reason is None else str(stop_reason),
            served_model=None if served is None else str(served),
            input_tokens=input_tokens + cache_read + cache_write,
            output_tokens=output_tokens,
            cost_usd=cost,
        )


def estimate_cost(
    system: str, user: str, *, pricing: ModelPricing = CLAUDE_OPUS_5_5_PRICING
) -> Decimal:
    input_tokens = (len(system) + len(user)) // ESTIMATE_CHARS_PER_TOKEN + ESTIMATE_OVERHEAD_TOKENS
    return pricing.cost(input_tokens=input_tokens, output_tokens=PLAN_MAX_TOKENS)


def running_strategies(session_date: date) -> tuple[str, ...]:
    """The plan's strategy keys: the family of each strategy child the supervisor
    schedules (registry entries it leaves out never run) with an entry window today."""
    from trading.operations.shadow_service import strategy_entry_window, strategy_family
    from trading.operations.supervisor import MANAGED_STRATEGY_STARTS, STRATEGY_STARTS

    running: list[str] = []
    for name in (*STRATEGY_STARTS, *MANAGED_STRATEGY_STARTS):
        start, cutoff = strategy_entry_window(name, session_date)
        family = strategy_family(name)
        if cutoff > start and family not in running:
            running.append(family)
    return tuple(running)


async def write_plan(
    *,
    session: MarketSession,
    slot_at: datetime,
    attempt: int,
    clock: Clock,
    transport: PlanTransport,
    running: Sequence[str],
    context_builder: ContextBuilder = build_context,
    budget_usd: Decimal = MANAGER_DAILY_BUDGET_USD,
    prompt_id: str = DAY_PLAN_PROMPT_ID,
    timeout_seconds: float = PLAN_TIMEOUT_SECONDS,
) -> PlanRecord:
    """One plan attempt for one slot; always appends exactly one plan row."""
    slot = slot_label(slot_at)
    plan_id = plan_id_for(session.session_date, slot, attempt)
    system = day_plan_prompt(prompt_id)
    context = await context_builder(session, now=clock(), slot=slot, running=tuple(running))
    user = context.encoded()
    estimate = estimate_cost(system, user)
    base = dict(
        plan_id=plan_id,
        session_date=session.session_date,
        slot=slot,
        manager_version=MANAGER_VERSION,
        prompt_id=prompt_id,
        model=MANAGER_MODEL,
        candidates=tuple(row.con_id for row in context.candidates),
        gates=context.gates.payload(),
        context_digest=context.digest(),
    )
    reserved = await reserve_spend(
        spend_date=session.session_date,
        purpose=PLAN_SPEND_PURPOSE,
        budget_usd=budget_usd,
        estimate_usd=estimate,
        now=clock(),
    )
    if not reserved:
        logger.critical(
            "%s: the manager's $%s for %s is spent; no new plans today (managed "
            "strategies stay neutral)",
            BUDGET_EXHAUSTED,
            budget_usd,
            session.session_date.isoformat(),
        )
        record = PlanRecord(created_at=clock(), outcome=BUDGET_EXHAUSTED, **base)
        await insert_plan(record)
        return record

    started = monotonic_time.monotonic()
    # Until the answer says otherwise the reservation is the cost: a timeout or
    # a parse failure may still be billed, so spend is never undercounted.
    cost = estimate
    response: PlanResponse | None = None
    narrowed: NarrowResult | None = None
    try:
        response = await asyncio.wait_for(
            transport.plan(system=system, user=user), timeout=timeout_seconds
        )
        cost = response.cost_usd
        if response.stop_reason == "refusal":
            outcome = AI_REFUSAL
        elif response.stop_reason == "max_tokens":
            outcome = AI_TRUNCATED
        elif response.answer is None:
            outcome = AI_MALFORMED
        else:
            narrowed = narrow_plan(
                response.answer,
                offered=context.offered,
                known=context.known,
                running=tuple(running),
                gates=context.gates,
            )
            outcome = narrowed.outcome
    except asyncio.TimeoutError:
        outcome = AI_TIMEOUT
    except PlanParseError:
        outcome = AI_MALFORMED
    except Exception as exc:  # noqa: BLE001 - any failure is a named outcome, never a crash
        logger.error("plan call failed: %s", type(exc).__name__)
        outcome = AI_UNAVAILABLE
    finally:
        await settle_spend(
            spend_date=session.session_date,
            purpose=PLAN_SPEND_PURPOSE,
            estimate_usd=estimate,
            actual_usd=cost,
            now=clock(),
        )
    latency_ms = max(0, int((monotonic_time.monotonic() - started) * 1000))
    created_at = clock()
    plan = narrowed.plan if narrowed is not None and narrowed.valid else None
    record = PlanRecord(
        created_at=created_at,
        outcome=outcome,
        plan=plan,
        valid_until=created_at + PLAN_TTL if plan is not None else None,
        dropped=tuple(narrowed.dropped_payload()) if narrowed is not None else (),
        input_tokens=response.input_tokens if response is not None else None,
        output_tokens=response.output_tokens if response is not None else None,
        cost_usd=cost,
        latency_ms=latency_ms,
        **base,
    )
    await insert_plan(record)
    if outcome == PLAN_VALID:
        logger.info(
            "plan %s valid: regime %s, focus %d, dropped %d, $%s",
            plan_id,
            plan.regime if plan else "?",
            len(plan.focus) if plan else 0,
            len(record.dropped),
            cost,
        )
    else:
        logger.error("plan %s failed: %s ($%s)", plan_id, outcome, cost)
    return record


@dataclass(slots=True)
class ManagerDayReport:
    session_date: date
    attempts: int = 0
    valid: int = 0
    outcomes: dict[str, int] | None = None

    def summary(self) -> dict[str, object]:
        return {
            "session_date": self.session_date.isoformat(),
            "attempts": self.attempts,
            "valid": self.valid,
            "outcomes": dict(self.outcomes or {}),
        }


async def run_manager_day(
    *,
    session: MarketSession,
    clock: Clock,
    sleep: Sleeper,
    transport: PlanTransport,
    running: Sequence[str] | None = None,
    context_builder: ContextBuilder = build_context,
) -> ManagerDayReport:
    """Plan every remaining slot of the session, then return.

    A restart skips slots that already have a valid plan or whose time is
    over (the next slot has come); a slot still current gets its plan at
    once. A failed attempt is retried once, three minutes later, before the
    next slot.
    """
    report = ManagerDayReport(session_date=session.session_date, outcomes={})
    names = tuple(running) if running is not None else running_strategies(session.session_date)
    for slot_at in plan_times(session):
        following = next_slot(session, slot_at) or (slot_at + PLAN_TTL)
        if clock() >= following:
            continue
        label = slot_label(slot_at)
        if await slot_has_valid_plan(session.session_date, label):
            continue
        await _sleep_until(slot_at, clock=clock, sleep=sleep)
        done = await slot_attempts(session.session_date, label)
        for attempt in range(done + 1, MAX_ATTEMPTS_PER_SLOT + 1):
            if attempt > 1:
                # The retry comes a few minutes after the failure, never at once.
                await sleep(PLAN_RETRY_AFTER.total_seconds())
                if clock() >= following:
                    break
            record = await write_plan(
                session=session,
                slot_at=slot_at,
                attempt=attempt,
                clock=clock,
                transport=transport,
                running=names,
                context_builder=context_builder,
            )
            report.attempts += 1
            assert report.outcomes is not None
            report.outcomes[record.outcome] = report.outcomes.get(record.outcome, 0) + 1
            if record.outcome == PLAN_VALID:
                report.valid += 1
                break
            if record.outcome == BUDGET_EXHAUSTED:
                break
    return report


async def _sleep_until(moment: datetime, *, clock: Clock, sleep: Sleeper) -> None:
    remaining = (moment.astimezone(timezone.utc) - clock()).total_seconds()
    if remaining > 0:
        await sleep(remaining)


__all__ = [
    "AnthropicPlanTransport",
    "MAX_ATTEMPTS_PER_SLOT",
    "ManagerDayReport",
    "PlanParseError",
    "PlanResponse",
    "PlanTransport",
    "estimate_cost",
    "run_manager_day",
    "running_strategies",
    "write_plan",
]

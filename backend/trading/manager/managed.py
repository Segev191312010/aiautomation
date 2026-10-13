"""Managed MOMO's controller: the plan in force, or neutral mode (Design E, Principle 3).

One ``ManagedStrategy`` serves one managed run. Once per poll, its entry gate
asks today's risk_off gate exactly as MOMO_HOD_V2 does (fail-open), then
refreshes the view:

* managed: the session latched ON, the switch is still on, and a valid plan
  is in force (written at most ``PLAN_TTL`` ago). The plan's day step for the
  family applies (0 pauses entries before any review), only the plan's focus
  names are assessed, the managed reviewer sees the plan, and a PROPOSE is
  sized at floor(policy x day step x setup step);
* neutral: anything else. The latch or the switch is off (by the owner's
  choice), or the plan is missing, expired or unreadable (a fallback, marked
  ``manager_fallback_neutral``, logged once per outage). Neutral mode is
  today's MOMO: every eligible candidate, V2's reviewer and prompt, steps 1.
  A plan failure never becomes an ABSTAIN.

The view only ever narrows what V2 does: the gate pauses whenever V2's
pauses, the focus is a subset of the run's eligible candidates (the hot-35
of each refresh), and the steps are at most 1. Each managed decision that
formed a setup is recorded with its eligibility snapshot, plan, gate states,
policy quantity and steps, and checked against those bounds (a CRITICAL
tripwire, then the database's own trigger).
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
import logging
from typing import Any

from trading.contracts import AIReview, Candidate, Decision, PortfolioSnapshot, RiskPolicy, RuleSetup
from trading.intelligence.managed_review import (
    NEUTRAL_VERDICT,
    ManagedReviewContext,
    SetupVerdict,
)
from trading.manager.constants import (
    FALLBACK_REASONS,
    MANAGER_FALLBACK_NEUTRAL,
    NEUTRAL_STEP,
    PLAN_ERROR,
    PLAN_EXPIRED,
    PLAN_MISSING,
    SWITCH_OFF,
    SWITCH_OFF_MIDDAY,
)
from trading.manager.store import (
    ManagedDecisionRecord,
    StoredPlan,
    insert_managed_decision,
    insert_verdict,
    newest_valid_plan,
)
from trading.manager.switch import DayLatch, SwitchState, latch_day_mode, read_switch
from trading.risk.engine import policy_quantity, scaled_quantity

logger = logging.getLogger("autotrader.manager.managed")

MANAGED = "managed"
NEUTRAL = "neutral"
MANAGER_WIDENING_DETECTED = "manager_widening_detected"

Clock = Callable[[], datetime]
EntryGate = Callable[[], Awaitable[tuple[bool, str]]]
PlanReader = Callable[[date, datetime], Awaitable[StoredPlan | None]]
SwitchReader = Callable[[], Awaitable[SwitchState]]


@dataclass(frozen=True, slots=True)
class ManagedView:
    """What managed MOMO follows for one poll."""

    mode: str
    observed_at: datetime
    neutral_reason: str | None = None
    plan: StoredPlan | None = None
    day_step: Decimal = NEUTRAL_STEP
    focus: frozenset[int] | None = None

    @property
    def neutral(self) -> bool:
        return self.mode == NEUTRAL

    @property
    def mark(self) -> str | None:
        """``manager_fallback_neutral`` when a plan failure (not the owner) chose neutral."""
        return MANAGER_FALLBACK_NEUTRAL if self.neutral_reason in FALLBACK_REASONS else None

    def describe(self) -> str:
        if self.neutral:
            return f"neutral ({self.neutral_reason})"
        assert self.plan is not None
        return (
            f"managed by plan {self.plan.plan_id}: step {self.day_step}, "
            f"focus {len(self.focus or ())}"
        )


def _neutral(reason: str, now: datetime) -> ManagedView:
    return ManagedView(mode=NEUTRAL, observed_at=now, neutral_reason=reason)


@dataclass(slots=True)
class _Reviewed:
    verdict: SetupVerdict
    context: ManagedReviewContext | None
    focus: frozenset[int] | None
    gates: dict[str, object] = field(default_factory=dict)


class ManagedStrategy:
    """Gate, focus filter, review context and sizing for one managed run."""

    def __init__(
        self,
        *,
        session_date: date,
        family: str,
        latch: DayLatch,
        clock: Clock,
        plan_reader: PlanReader = newest_valid_plan,
        switch_reader: SwitchReader = read_switch,
        decision_writer: Callable[[ManagedDecisionRecord], Awaitable[None]] = insert_managed_decision,
        verdict_writer: Callable[..., Awaitable[None]] = insert_verdict,
    ) -> None:
        self._session_date = session_date
        self._family = family
        self._latch = latch
        self._clock = clock
        self._plan_reader = plan_reader
        self._switch_reader = switch_reader
        self._decision_writer = decision_writer
        self._verdict_writer = verdict_writer
        self._view = _neutral(PLAN_MISSING, self._now())
        self._switched_off = False
        self._in_outage = False
        self._eligible: frozenset[int] = frozenset()
        self._con_ids: dict[str, int] = {}
        self._reviewed: dict[str, _Reviewed] = {}
        self._gates: dict[str, object] = {"risk_off": "unknown", "plan": "open"}

    @classmethod
    async def open(
        cls, *, session_date: date, family: str, clock: Clock, **kwargs: Any
    ) -> ManagedStrategy:
        """Latch the switch for the session (or reuse the latch) and build the controller."""
        latch = await latch_day_mode(session_date, family, now=clock().astimezone(timezone.utc))
        logger.info(
            "%s latch for %s: %s (%s, switch event %s)",
            family,
            session_date.isoformat(),
            latch.mode,
            "written now" if latch.created else "reused",
            latch.switch_event_id,
        )
        return cls(session_date=session_date, family=family, latch=latch, clock=clock, **kwargs)

    @property
    def latch(self) -> DayLatch:
        return self._latch

    @property
    def view(self) -> ManagedView:
        return self._view

    def _now(self) -> datetime:
        return self._clock().astimezone(timezone.utc)

    # -- the plan lookup (Principle 3) ------------------------------------------------

    async def refresh(self) -> ManagedView:
        now = self._now()
        try:
            view = await self._lookup(now)
        except Exception as exc:  # noqa: BLE001 - any plan failure is neutral mode
            logger.error("plan lookup failed (%s); neutral mode", type(exc).__name__)
            view = _neutral(PLAN_ERROR, now)
        self._set_view(view)
        return view

    async def _lookup(self, now: datetime) -> ManagedView:
        if not self._latch.managed:
            return _neutral(SWITCH_OFF, now)
        if self._switched_off:
            return _neutral(SWITCH_OFF_MIDDAY, now)
        switch = await self._switch_reader()
        if not switch.on:
            # A mid-day OFF holds for the rest of the session; the identity stays.
            self._switched_off = True
            logger.warning("autopilot switched off mid-session: %s neutral for the rest of today", self._family)
            return _neutral(SWITCH_OFF_MIDDAY, now)
        stored = await self._plan_reader(self._session_date, now)
        if stored is None or stored.plan is None:
            return _neutral(PLAN_MISSING, now)
        if not stored.in_force(now):
            return _neutral(PLAN_EXPIRED, now)
        plan = stored.plan
        if self._family not in plan.strategies:
            return _neutral(PLAN_MISSING, now)
        return ManagedView(
            mode=MANAGED,
            observed_at=now,
            plan=stored,
            day_step=plan.step_for(self._family),
            focus=plan.focus_for(self._family),
        )

    def _set_view(self, view: ManagedView) -> None:
        previous = self._view
        self._view = view
        if view.mark is not None and not self._in_outage:
            self._in_outage = True
            logger.critical(
                "%s: %s (%s); %s trades as its unmanaged self until a valid plan arrives",
                MANAGER_FALLBACK_NEUTRAL,
                view.neutral_reason,
                self._session_date.isoformat(),
                self._family,
            )
        elif view.mark is None and self._in_outage:
            self._in_outage = False
            logger.warning("manager recovered: %s", view.describe())
        elif previous.describe() != view.describe():
            logger.info("%s view: %s", self._family, view.describe())

    # -- the entry gate ---------------------------------------------------------------

    def entry_gate(self, risk_off_gate: EntryGate | None) -> EntryGate:
        """Today's risk_off gate (fail-open, as V2) AND the plan's day step.

        Every pause V2's gate makes is kept; the plan can only add one. A
        risk_off gate that raises still fails open, exactly as for V2, unless
        the plan itself pauses the family.
        """

        async def gate() -> tuple[bool, str]:
            failure: Exception | None = None
            reason = "no risk_off gate"
            if risk_off_gate is not None:
                try:
                    allowed, reason = await risk_off_gate()
                except Exception as exc:  # noqa: BLE001 - re-raised below unless the plan pauses
                    failure = exc
                    self._gates["risk_off"] = "failed_open"
                else:
                    self._gates["risk_off"] = "open" if allowed else "paused"
                    if not allowed:
                        await self.refresh()
                        self._gates["plan"] = "not_asked"
                        return False, reason
            view = await self.refresh()
            if not view.neutral and view.day_step == 0:
                self._gates["plan"] = "paused"
                assert view.plan is not None
                return False, f"manager: {self._family} off for the day (plan {view.plan.plan_id})"
            self._gates["plan"] = "open"
            if failure is not None:
                raise failure
            return True, f"{reason}; {view.describe()}"

        return gate

    # -- the focus filter -------------------------------------------------------------

    def admits(self, candidate: Candidate, eligible: tuple[Candidate, ...]) -> bool:
        """A deterministic skip: only the plan's focus names, inside the eligible set."""
        self._eligible = frozenset(item.con_id for item in eligible)
        self._con_ids = {item.id: item.con_id for item in eligible}
        view = self._view
        if view.neutral:
            return True
        return candidate.con_id in (view.focus or frozenset()) and candidate.con_id in self._eligible

    # -- the managed review -----------------------------------------------------------

    def review_context(self, setup: RuleSetup) -> ManagedReviewContext | None:
        """The plan's context for this setup, or None for neutral (V2's reviewer)."""
        view = self._view
        if view.neutral or view.plan is None or view.plan.plan is None:
            return None
        con_id = self._con_ids.get(setup.candidate_id)
        return ManagedReviewContext(
            plan_id=view.plan.plan_id,
            regime=view.plan.plan.regime,
            strategy=self._family,
            day_step=view.day_step,
            focus_note=view.plan.plan.note_for(con_id) if con_id is not None else None,
        )

    async def on_verdict(
        self, setup: RuleSetup, context: ManagedReviewContext | None, verdict: SetupVerdict
    ) -> None:
        fingerprint = setup.fingerprint()
        view = self._view
        self._reviewed[fingerprint] = _Reviewed(
            verdict=verdict if context is not None else NEUTRAL_VERDICT,
            context=context,
            focus=view.focus if context is not None else None,
            gates=dict(self._gates),
        )
        if context is not None and not verdict.valid:
            return  # the review failed closed with the verdict's token
        await self._verdict_writer(
            run_id=setup.run_id,
            setup_fingerprint=fingerprint,
            plan_id=context.plan_id if context is not None else None,
            mode=MANAGED if context is not None else NEUTRAL,
            step=verdict.step if context is not None else NEUTRAL_STEP,
            note=verdict.note if context is not None else None,
            created_at=self._now(),
        )

    # -- sizing (the pipeline's ManagedSizing) -----------------------------------------

    async def size_multiplier(self, *, setup: RuleSetup, review: AIReview) -> Decimal:
        reviewed = self._reviewed.get(setup.fingerprint())
        if reviewed is None or reviewed.context is None:
            return NEUTRAL_STEP
        return reviewed.context.day_step * reviewed.verdict.step

    async def after_decision(
        self,
        *,
        decision: Decision,
        setup: RuleSetup,
        candidate: Candidate,
        policy: RiskPolicy,
        portfolio: PortfolioSnapshot,
        size_multiplier: Decimal,
    ) -> None:
        reviewed = self._reviewed.pop(setup.fingerprint(), None)
        if reviewed is None:
            view = self._view
            reviewed = _Reviewed(verdict=NEUTRAL_VERDICT, context=None, focus=None, gates=dict(self._gates))
            # A review that never reached the controller ran neutral.
            neutral_reason = view.neutral_reason or PLAN_ERROR
        else:
            neutral_reason = None if reviewed.context is not None else (self._view.neutral_reason or PLAN_ERROR)
        managed = reviewed.context is not None
        policy_qty = (
            policy_quantity(policy=policy, portfolio=portfolio, setup=setup)
            if decision.risk is not None
            else None
        )
        intent = decision.risk.intent if decision.risk is not None else None
        final_qty = intent.quantity if intent is not None else None
        day_step = reviewed.context.day_step if reviewed.context is not None else NEUTRAL_STEP
        setup_step = reviewed.verdict.step if managed else NEUTRAL_STEP
        eligible = self._eligible or frozenset({candidate.con_id})
        record = ManagedDecisionRecord(
            decision_id=decision.id,
            run_id=decision.run_id,
            candidate_id=decision.candidate_id,
            con_id=candidate.con_id,
            setup_fingerprint=setup.fingerprint(),
            session_date=self._session_date,
            outcome=decision.outcome.value,
            mode=MANAGED if managed else NEUTRAL,
            neutral_reason=None if managed else neutral_reason,
            mark=(
                None
                if managed
                else (MANAGER_FALLBACK_NEUTRAL if neutral_reason in FALLBACK_REASONS else None)
            ),
            plan_id=reviewed.context.plan_id if reviewed.context is not None else None,
            eligible=tuple(sorted(eligible)),
            focus=tuple(sorted(reviewed.focus or ())) if managed else None,
            gates={**reviewed.gates, "latch": self._latch.mode},
            day_step=day_step,
            setup_step=setup_step,
            policy_qty=policy_qty,
            final_qty=final_qty,
            created_at=self._now(),
        )
        problems = narrowing_problems(record, size_multiplier=size_multiplier)
        if problems:
            logger.critical(
                "%s: decision %s %s: %s",
                MANAGER_WIDENING_DETECTED,
                decision.id,
                candidate.symbol,
                "; ".join(problems),
                stack_info=True,
            )
        await self._decision_writer(record)


def narrowing_problems(record: ManagedDecisionRecord, *, size_multiplier: Decimal) -> list[str]:
    """Every way one managed decision would break Principle 2; empty when it holds."""
    problems: list[str] = []
    if record.con_id not in record.eligible:
        problems.append("contract outside the eligible set")
    if record.mode == MANAGED and record.con_id not in (record.focus or ()):
        problems.append("contract outside the plan focus")
    if record.mode == NEUTRAL and (record.day_step != NEUTRAL_STEP or record.setup_step != NEUTRAL_STEP):
        problems.append("neutral mode scaled the size")
    if record.mode == MANAGED and size_multiplier != record.day_step * record.setup_step:
        problems.append("the applied multiplier differs from the recorded steps")
    if record.final_qty is not None:
        product = record.day_step * record.setup_step
        if record.policy_qty is None or record.final_qty > record.policy_qty:
            problems.append("final quantity above the policy quantity")
        elif product <= 0:
            problems.append("an approval with a zero step")
        elif record.final_qty != scaled_quantity(record.policy_qty, product):
            problems.append("final quantity is not floor(policy x steps)")
    return problems


__all__ = [
    "MANAGED",
    "MANAGER_WIDENING_DETECTED",
    "NEUTRAL",
    "ManagedStrategy",
    "ManagedView",
    "narrowing_problems",
]

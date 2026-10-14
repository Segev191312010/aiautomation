"""The AI overlay scorecard: managed MOMO against MOMO_HOD_V2 on the same setups.

Phase 3 of the autopilot (AP_MOMO_HOD_V1 taking MOMO's real paper orders)
needs evidence first that the AI-managed shadow does no harm. Both
identities run MOMO_HOD_V2's rules over the same hot-35 refreshes, and the
day manager only ever narrows (a focus inside the eligible set, a day step,
a setup step), so every setup managed MOMO forms is one MOMO_HOD_V2 formed on
the same contract and bar. A setup's fingerprint hashes its run and
strategy, so the two sides are paired on (session, contract, bar) instead,
with the prices compared as a check. The nightly tracker's outcome
(``autotrader_manager_signal_outcomes``, net R after costs) is the result of
the setup, whichever side it is read from.

Per session and over the range the scorecard reports:

* net R per trade on all paired setups, on the AI's picks (a managed PROPOSE
  with both steps above zero), on the plan's focus (setups managed MOMO
  assessed under a plan) and outside it (setups it never assessed on a day a
  plan was in force);
* TAKE against ABSTAIN hit rates among the setups the managed reviewer judged;
* the size-step effect: net R scaled by the steps against net R at full size;
* the shares of managed decisions marked ``manager_fallback_neutral`` and
  ``manager_not_in_focus``;
* plans per day, their validity, latency p50/p95 and spend;
* day-clustered 90% bootstrap bounds (``paper_promotion``'s own) on each
  sample and on the paired differences;
* the phase-3 do-no-harm check exactly as ``paper_promotion.ai_do_no_harm``
  implements it (the AI's picks against every setup it judged), plus named
  reasons phase 3 is not ready.

Reads only: one read-only SQLite handle (``mode=ro``), the way the paper
promotion rule reads its live evidence. Nothing here writes, and a database
without the manager's tables reads as an unavailable scorecard. The output
carries no identifiers: no run, plan, decision or contract ids and no symbols.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from contextlib import closing
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
import json
import math
from pathlib import Path
import sqlite3
from typing import Any
from urllib.parse import quote

from trading.manager.constants import (
    MANAGER_DAILY_BUDGET_USD,
    MANAGER_FALLBACK_NEUTRAL,
    MANAGER_NOT_IN_FOCUS,
    PLAN_SPEND_PURPOSE,
    PLAN_VALID,
)
from trading.research.paper_promotion import (
    MIN_LIVE_SETUPS,
    MIN_MANAGED_SETUPS,
    MIN_SESSIONS,
    RULE_VERSION,
    WINDOW_IN_RULES,
    AIDoNoHarm,
    BootstrapBound,
    EvidenceIdentity,
    EvidenceTrade,
    ai_do_no_harm,
    day_clustered_bootstrap,
    identity_from_run,
    paired_difference_bootstrap,
)
from trading.research.replay import FILL_MODEL_ID
from trading.storage.migrations import SCHEMA_READ_TIMEOUT_SECONDS

SCORECARD_VERSION = "overlay-scorecard-v1"
# The names as the setups table records them; pinned to the session runner's
# constants by a test (this module must not import the runner).
MANAGED_STRATEGY = "AP_MOMO_HOD_V1"
BASELINE_STRATEGY = "MOMO_HOD_V2"
# The plan's phase-3 rule F: the AI's picks may trail all setups by at most
# this much per trade. The binding check is paper_promotion's bootstrap
# (advisory when the upper bound of picks - all is below zero); the tolerance
# is reported beside it on the point estimate.
PHASE3_TOLERANCE_R = Decimal("-0.02")
DEFAULT_DAYS = MIN_SESSIONS

# A managed decision that accepted the setup (the shadow's PROPOSE).
ACCEPTED_OUTCOMES = frozenset({"shadow_accepted", "approved", "submitted"})
# The managed review failed closed: the AI never judged the setup.
REVIEW_FAILED_OUTCOMES = frozenset({"ai_blocked"})

# Named reasons phase 3 is not ready, in check order.
MANAGED_SESSIONS_BELOW_MINIMUM = "managed_sessions_below_minimum"
JUDGED_SETUPS_BELOW_MINIMUM = "judged_setups_below_minimum"
PICKED_SETUPS_BELOW_MINIMUM = "picked_setups_below_minimum"
DIFFERENCE_BOUNDS_UNAVAILABLE = "difference_bounds_unavailable"
AI_PICKS_COST_R = "ai_picks_cost_r"
PICKS_BELOW_TOLERANCE = "picks_below_tolerance"
SCORECARD_UNREADABLE = "scorecard_unreadable"

# The tables the scorecard reads (migrations 13, 15 and 16).
REQUIRED_TABLES = (
    "autotrader_manager_setups",
    "autotrader_manager_signal_outcomes",
    "autotrader_manager_plans",
    "autotrader_manager_verdicts",
    "autotrader_manager_decisions",
    "autotrader_manager_spend",
)

_R_QUANTUM = Decimal("0.0001")
_ZERO = Decimal("0")
_ONE = Decimal("1")


# --- the rows --------------------------------------------------------------------------


_SETUPS = """
    SELECT s.run_id, s.setup_fingerprint, s.strategy_name, s.con_id, s.session_date, s.bar_ts,
           s.detected_at, s.entry_price, s.stop_price, s.target_price,
           o.filled, o.r_multiple, o.cost_r, o.details_json
    FROM autotrader_manager_setups s
    LEFT JOIN autotrader_manager_signal_outcomes o
      ON o.run_id = s.run_id AND o.setup_fingerprint = s.setup_fingerprint
     AND o.fill_model_id = ?
    WHERE s.strategy_name IN (?, ?) AND s.session_date <= ?
      AND (? IS NULL OR s.session_date >= ?)
    ORDER BY s.session_date, s.bar_ts, s.con_id, s.id
"""
_DECISIONS = """
    SELECT run_id, setup_fingerprint, reason, session_date, outcome, mode, mark, day_step, setup_step
    FROM autotrader_manager_decisions
    WHERE session_date <= ? AND (? IS NULL OR session_date >= ?)
    ORDER BY id
"""
_VERDICTS = """
    SELECT v.run_id, v.setup_fingerprint, v.mode
    FROM autotrader_manager_verdicts v
    JOIN autotrader_manager_setups s
      ON s.run_id = v.run_id AND s.setup_fingerprint = v.setup_fingerprint
    WHERE s.strategy_name = ? AND s.session_date <= ? AND (? IS NULL OR s.session_date >= ?)
"""
_PLANS = """
    SELECT session_date, outcome, latency_ms, cost_usd
    FROM autotrader_manager_plans
    WHERE session_date <= ? AND (? IS NULL OR session_date >= ?)
    ORDER BY session_date, created_at, plan_id
"""
_SPEND = """
    SELECT spend_date, spent_usd FROM autotrader_manager_spend
    WHERE purpose = ? AND spend_date <= ? AND (? IS NULL OR spend_date >= ?)
"""
_IDENTITY = """
    SELECT r.policy_fingerprints_json
    FROM autotrader_runs r
    JOIN autotrader_manager_setups s ON s.run_id = r.id
    WHERE s.strategy_name = ? AND s.session_date <= ? AND (? IS NULL OR s.session_date >= ?)
    ORDER BY s.session_date, s.id
    LIMIT 1
"""


@dataclass(frozen=True, slots=True)
class _SetupRow:
    run_id: str
    fingerprint: str
    strategy: str
    con_id: int
    session_date: date
    bar_ts: str
    detected_at: datetime
    prices: tuple[str, str, str]
    tracked: bool
    net_r: Decimal | None
    exit_at: datetime | None


@dataclass(frozen=True, slots=True)
class _DecisionRow:
    run_id: str
    fingerprint: str | None
    reason: str | None
    session_date: date
    outcome: str
    mode: str
    mark: str | None
    day_step: Decimal
    setup_step: Decimal


def _decimal(value: object) -> Decimal:
    return Decimal(str(value))


def _moment(value: object) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _setup_row(row: Sequence[Any]) -> _SetupRow | None:
    (
        run_id, fingerprint, strategy, con_id, session_date, bar_ts, detected_at,
        entry, stop, target, filled, gross, cost, details_json,
    ) = row
    detected = _moment(detected_at)
    if detected is None:
        return None
    net_r: Decimal | None = None
    exit_at: datetime | None = None
    tracked = filled is not None
    if tracked and int(filled) == 1:
        try:
            net_r = _decimal(gross) - _decimal(cost)
        except InvalidOperation:
            return None
        try:
            details = json.loads(str(details_json))
        except (TypeError, ValueError):
            details = {}
        exit_at = _moment(details.get("exit_at")) if isinstance(details, dict) else None
    return _SetupRow(
        run_id=str(run_id),
        fingerprint=str(fingerprint),
        strategy=str(strategy),
        con_id=int(con_id),
        session_date=date.fromisoformat(str(session_date)),
        bar_ts=str(bar_ts),
        detected_at=detected,
        prices=(str(entry), str(stop), str(target)),
        tracked=tracked,
        net_r=net_r,
        exit_at=exit_at,
    )


def _decision_row(row: Sequence[Any]) -> _DecisionRow:
    run_id, fingerprint, reason, session_date, outcome, mode, mark, day_step, setup_step = row
    return _DecisionRow(
        run_id=str(run_id),
        fingerprint=None if fingerprint is None else str(fingerprint),
        reason=None if reason is None else str(reason),
        session_date=date.fromisoformat(str(session_date)),
        outcome=str(outcome),
        mode=str(mode),
        mark=None if mark is None else str(mark),
        day_step=_decimal(day_step),
        setup_step=_decimal(setup_step),
    )


# --- the pairs -------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PairedSetup:
    """One MOMO_HOD_V2 setup and what managed MOMO did with the same bar."""

    session_date: date
    exit_at: datetime
    net_r: Decimal | None  # None: not filled, or not tracked yet
    tracked: bool
    has_managed: bool  # managed MOMO formed the same setup
    managed: bool  # ... and decided it under a plan
    judged: bool  # ... and the managed reviewer returned a verdict
    selected: bool  # ... a PROPOSE with both steps above zero
    step: Decimal  # day step x setup step (1 outside a plan)
    non_focus: bool  # a plan was in force that day and managed MOMO never assessed it
    price_mismatch: bool

    @property
    def filled(self) -> bool:
        return self.net_r is not None


def _pair(
    baseline: _SetupRow,
    managed: _SetupRow | None,
    decision: _DecisionRow | None,
    judged: bool,
    *,
    managed_day: bool,
) -> PairedSetup:
    tracked = baseline.tracked or (managed is not None and managed.tracked)
    # The baseline's outcome is the pair's result; the managed side's stands in
    # until the tracker has simulated the baseline's (identical prices).
    source = baseline if baseline.tracked or managed is None else managed
    under_plan = decision is not None and decision.mode == "managed"
    judged = under_plan and judged and decision is not None and decision.outcome not in REVIEW_FAILED_OUTCOMES
    step = _ONE
    selected = False
    if under_plan and decision is not None:
        step = decision.day_step * decision.setup_step
        selected = judged and decision.outcome in ACCEPTED_OUTCOMES and step > 0
    return PairedSetup(
        session_date=baseline.session_date,
        exit_at=source.exit_at or baseline.detected_at,
        net_r=source.net_r,
        tracked=tracked,
        has_managed=managed is not None,
        managed=under_plan,
        judged=judged,
        selected=selected,
        step=step,
        non_focus=managed_day and not under_plan,
        price_mismatch=managed is not None and managed.prices != baseline.prices,
    )


# --- the statistics ----------------------------------------------------------------------


def _mean(values: Sequence[Decimal]) -> Decimal | None:
    return sum(values, _ZERO) / len(values) if values else None


def _percentile(values: Sequence[int], share: Decimal) -> int | None:
    """Nearest-rank percentile of integer samples (``share`` in (0, 1])."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(math.ceil(float(share) * len(ordered)) - 1, 0)
    return ordered[min(rank, len(ordered) - 1)]


def _text(value: Decimal | None) -> str | None:
    return None if value is None else str(value.quantize(_R_QUANTUM, rounding=ROUND_HALF_EVEN))


@dataclass(frozen=True, slots=True)
class Sample:
    """Net R over a set of filled paired setups."""

    trades: int
    total_r: Decimal
    wins: int = 0

    @property
    def mean_r(self) -> Decimal | None:
        return self.total_r / self.trades if self.trades else None

    @property
    def hit_rate(self) -> Decimal | None:
        return Decimal(self.wins) / self.trades if self.trades else None

    def payload(self) -> dict[str, object]:
        return {
            "trades": self.trades,
            "total_r": _text(self.total_r),
            "mean_r": _text(self.mean_r),
            "wins": self.wins,
            "hit_rate": _text(self.hit_rate),
        }


def _sample(values: Iterable[Decimal]) -> Sample:
    items = list(values)
    return Sample(trades=len(items), total_r=sum(items, _ZERO), wins=sum(1 for item in items if item > 0))


@dataclass(frozen=True, slots=True)
class PlanStats:
    attempts: int
    valid: int
    latencies_ms: tuple[int, ...]
    cost_usd: Decimal
    spent_usd: Decimal

    @property
    def validity(self) -> Decimal | None:
        return Decimal(self.valid) / self.attempts if self.attempts else None

    @property
    def latency_p50_ms(self) -> int | None:
        return _percentile(self.latencies_ms, Decimal("0.5"))

    @property
    def latency_p95_ms(self) -> int | None:
        return _percentile(self.latencies_ms, Decimal("0.95"))

    def payload(self) -> dict[str, object]:
        return {
            "attempts": self.attempts,
            "valid": self.valid,
            "validity": _text(self.validity),
            "latency_p50_ms": self.latency_p50_ms,
            "latency_p95_ms": self.latency_p95_ms,
            "plan_cost_usd": _text(self.cost_usd),
            "spent_usd": _text(self.spent_usd),
        }


@dataclass(frozen=True, slots=True)
class DecisionStats:
    total: int
    fallback_neutral: int
    not_in_focus: int

    @property
    def fallback_share(self) -> Decimal | None:
        return Decimal(self.fallback_neutral) / self.total if self.total else None

    @property
    def not_in_focus_share(self) -> Decimal | None:
        return Decimal(self.not_in_focus) / self.total if self.total else None

    def payload(self) -> dict[str, object]:
        return {
            "total": self.total,
            "fallback_neutral": self.fallback_neutral,
            "fallback_share": _text(self.fallback_share),
            "not_in_focus": self.not_in_focus,
            "not_in_focus_share": _text(self.not_in_focus_share),
        }


@dataclass(frozen=True, slots=True)
class DayScore:
    """One session's paired setups and the manager's day around them."""

    session_date: date
    pairs: int
    filled: int
    untracked: int
    managed_only: int  # managed MOMO formed a setup the baseline did not
    price_mismatches: int
    managed_day: bool  # at least one decision under a plan
    all: Sample
    picked: Sample
    focus: Sample
    non_focus: Sample
    take: Sample
    abstain: Sample
    judged: int
    scaled_r: Decimal  # net R x steps over the filled picks
    unscaled_r: Decimal  # net R at full size over the same picks
    decisions: DecisionStats
    plans: PlanStats

    def payload(self) -> dict[str, object]:
        return {
            "session_date": self.session_date.isoformat(),
            "pairs": self.pairs,
            "filled": self.filled,
            "untracked": self.untracked,
            "managed_only": self.managed_only,
            "price_mismatches": self.price_mismatches,
            "managed_day": self.managed_day,
            "judged": self.judged,
            "r": {
                "all": self.all.payload(),
                "picked": self.picked.payload(),
                "focus": self.focus.payload(),
                "non_focus": self.non_focus.payload(),
            },
            "hit_rates": {"take": self.take.payload(), "abstain": self.abstain.payload()},
            "size_steps": {"scaled_r": _text(self.scaled_r), "unscaled_r": _text(self.unscaled_r)},
            "decisions": self.decisions.payload(),
            "plans": self.plans.payload(),
        }


def _day_score(
    day: date,
    pairs: Sequence[PairedSetup],
    *,
    managed_only: int,
    decisions: Sequence[_DecisionRow],
    plans: PlanStats,
) -> DayScore:
    filled = [pair for pair in pairs if pair.filled]
    picks = [pair for pair in filled if pair.selected]
    managed_day = any(item.mode == "managed" for item in decisions)
    return DayScore(
        session_date=day,
        pairs=len(pairs),
        filled=len(filled),
        untracked=sum(1 for pair in pairs if not pair.tracked),
        managed_only=managed_only,
        price_mismatches=sum(1 for pair in pairs if pair.price_mismatch),
        managed_day=managed_day,
        all=_sample(pair.net_r for pair in filled),  # type: ignore[misc]
        picked=_sample(pair.net_r for pair in picks),  # type: ignore[misc]
        focus=_sample(pair.net_r for pair in filled if pair.managed),  # type: ignore[misc]
        non_focus=_sample(pair.net_r for pair in filled if pair.non_focus),  # type: ignore[misc]
        take=_sample(pair.net_r for pair in picks),  # type: ignore[misc]
        abstain=_sample(pair.net_r for pair in filled if pair.judged and not pair.selected),  # type: ignore[misc]
        judged=sum(1 for pair in pairs if pair.judged),
        scaled_r=sum((pair.net_r * pair.step for pair in picks), _ZERO),  # type: ignore[operator]
        unscaled_r=sum((pair.net_r for pair in picks), _ZERO),  # type: ignore[misc]
        decisions=DecisionStats(
            total=len(decisions),
            fallback_neutral=sum(1 for item in decisions if item.mark == MANAGER_FALLBACK_NEUTRAL),
            not_in_focus=sum(1 for item in decisions if item.reason == MANAGER_NOT_IN_FOCUS),
        ),
        plans=plans,
    )


# --- phase-3 readiness -------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Phase3Readiness:
    """The do-no-harm verdict (paper_promotion's) and every reason phase 3 is not ready."""

    ai: AIDoNoHarm
    managed_sessions: int
    reasons: tuple[str, ...]

    @property
    def ready(self) -> bool:
        return not self.reasons

    def payload(self) -> dict[str, object]:
        return {
            "rule": RULE_VERSION,
            "status": self.ai.status,
            "ready": self.ready,
            "reasons": list(self.reasons),
            "managed_sessions": self.managed_sessions,
            "judged_setups": self.ai.all_trades,
            "picked_setups": self.ai.selected_trades,
            "difference": self.ai.difference.payload(),
            "tolerance_r": str(PHASE3_TOLERANCE_R),
            "thresholds": {
                "min_managed_sessions": str(MIN_SESSIONS),
                "min_judged_setups": str(MIN_LIVE_SETUPS),
                "min_picked_setups": str(MIN_MANAGED_SETUPS),
            },
            "grants": "nothing: phase 3 needs the owner's review and sign-off",
        }


def phase3_readiness(
    trades: Sequence[EvidenceTrade], *, identity: EvidenceIdentity, managed_sessions: int
) -> Phase3Readiness:
    """Pure: the rule on the paired trades (``reviewed`` = judged, ``selected`` = picked)."""
    from trading.research.paper_promotion import AI_ADVISORY

    ai = ai_do_no_harm(trades, identity=identity)
    reasons: list[str] = []
    if managed_sessions < MIN_SESSIONS:
        reasons.append(MANAGED_SESSIONS_BELOW_MINIMUM)
    if ai.all_trades < MIN_LIVE_SETUPS:
        reasons.append(JUDGED_SETUPS_BELOW_MINIMUM)
    if ai.selected_trades < MIN_MANAGED_SETUPS:
        reasons.append(PICKED_SETUPS_BELOW_MINIMUM)
    if ai.difference.upper is None:
        reasons.append(DIFFERENCE_BOUNDS_UNAVAILABLE)
    if ai.status == AI_ADVISORY:
        reasons.append(AI_PICKS_COST_R)
    estimate = ai.difference.estimate
    if estimate is not None and estimate < PHASE3_TOLERANCE_R:
        reasons.append(PICKS_BELOW_TOLERANCE)
    return Phase3Readiness(ai=ai, managed_sessions=managed_sessions, reasons=tuple(reasons))


# --- the scorecard -----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OverlayScorecard:
    """The paired evidence over a range of sessions. A report only: it grants nothing."""

    start: date | None
    end: date
    readable: bool = True
    note: str = ""
    days: tuple[DayScore, ...] = ()
    all: Sample = field(default_factory=lambda: Sample(0, _ZERO))
    picked: Sample = field(default_factory=lambda: Sample(0, _ZERO))
    focus: Sample = field(default_factory=lambda: Sample(0, _ZERO))
    non_focus: Sample = field(default_factory=lambda: Sample(0, _ZERO))
    take: Sample = field(default_factory=lambda: Sample(0, _ZERO))
    abstain: Sample = field(default_factory=lambda: Sample(0, _ZERO))
    scaled_r: Decimal = _ZERO
    unscaled_r: Decimal = _ZERO
    bounds: dict[str, BootstrapBound] = field(default_factory=dict)
    phase3: Phase3Readiness | None = None
    identity: EvidenceIdentity | None = None

    @property
    def sessions(self) -> int:
        return len(self.days)

    @property
    def managed_sessions(self) -> int:
        return sum(1 for day in self.days if day.managed_day)

    @property
    def pairs(self) -> int:
        return sum(day.pairs for day in self.days)

    @property
    def filled(self) -> int:
        return sum(day.filled for day in self.days)

    @property
    def untracked(self) -> int:
        return sum(day.untracked for day in self.days)

    @property
    def managed_only(self) -> int:
        return sum(day.managed_only for day in self.days)

    @property
    def price_mismatches(self) -> int:
        return sum(day.price_mismatches for day in self.days)

    @property
    def judged(self) -> int:
        return sum(day.judged for day in self.days)

    @property
    def decisions(self) -> DecisionStats:
        return DecisionStats(
            total=sum(day.decisions.total for day in self.days),
            fallback_neutral=sum(day.decisions.fallback_neutral for day in self.days),
            not_in_focus=sum(day.decisions.not_in_focus for day in self.days),
        )

    @property
    def plans(self) -> PlanStats:
        latencies: list[int] = []
        for day in self.days:
            latencies.extend(day.plans.latencies_ms)
        return PlanStats(
            attempts=sum(day.plans.attempts for day in self.days),
            valid=sum(day.plans.valid for day in self.days),
            latencies_ms=tuple(latencies),
            cost_usd=sum((day.plans.cost_usd for day in self.days), _ZERO),
            spent_usd=sum((day.plans.spent_usd for day in self.days), _ZERO),
        )

    def payload(self) -> dict[str, object]:
        return {
            "schema": "autotrader-v1/manager-scorecard",
            "version": 1,
            "scorecard": SCORECARD_VERSION,
            "managed_strategy": MANAGED_STRATEGY,
            "baseline_strategy": BASELINE_STRATEGY,
            "fill_model_id": FILL_MODEL_ID,
            "readable": self.readable,
            "note": self.note,
            "range": {
                "start": None if self.start is None else self.start.isoformat(),
                "end": self.end.isoformat(),
                "sessions": self.sessions,
                "managed_sessions": self.managed_sessions,
            },
            "pairs": {
                "total": self.pairs,
                "filled": self.filled,
                "untracked": self.untracked,
                "managed_only": self.managed_only,
                "price_mismatches": self.price_mismatches,
                "judged": self.judged,
            },
            "r": {
                "all": self.all.payload(),
                "picked": self.picked.payload(),
                "focus": self.focus.payload(),
                "non_focus": self.non_focus.payload(),
            },
            "bounds": {name: bound.payload() for name, bound in self.bounds.items()},
            "hit_rates": {"take": self.take.payload(), "abstain": self.abstain.payload()},
            "size_steps": {"scaled_r": _text(self.scaled_r), "unscaled_r": _text(self.unscaled_r)},
            "decisions": self.decisions.payload(),
            "plans": self.plans.payload(),
            "phase3": None if self.phase3 is None else self.phase3.payload(),
            "days": [day.payload() for day in self.days],
        }


def _seed_identity(prints_json: object) -> EvidenceIdentity:
    """The managed identity the bootstrap seeds from; a placeholder before any managed run."""
    try:
        prints = json.loads(str(prints_json)) if prints_json is not None else {}
    except (TypeError, ValueError):
        prints = {}
    identity = identity_from_run(MANAGED_STRATEGY, "momo_hod", prints) if isinstance(prints, dict) else None
    return identity or EvidenceIdentity(
        strategy=MANAGED_STRATEGY, rules="unknown", entry_window=WINDOW_IN_RULES, risk_policy="unknown"
    )


def build_scorecard(
    *,
    start: date | None,
    end: date,
    setups: Sequence[_SetupRow],
    decisions: Sequence[_DecisionRow],
    verdicts: Iterable[tuple[str, str]],
    plans: Sequence[tuple[date, str, int | None, Decimal]],
    spend: dict[date, Decimal],
    identity: EvidenceIdentity,
) -> OverlayScorecard:
    """Pure: pair the rows and score them (``read_scorecard`` reads them)."""
    by_key: dict[tuple[date, int, str], dict[str, _SetupRow]] = defaultdict(dict)
    for row in setups:
        by_key[(row.session_date, row.con_id, row.bar_ts)].setdefault(row.strategy, row)
    decision_by_setup = {
        (item.run_id, item.fingerprint): item for item in decisions if item.fingerprint is not None
    }
    judged_keys = set(verdicts)
    decisions_by_day: dict[date, list[_DecisionRow]] = defaultdict(list)
    for item in decisions:
        decisions_by_day[item.session_date].append(item)
    managed_days = {day for day, items in decisions_by_day.items() if any(i.mode == "managed" for i in items)}

    pairs_by_day: dict[date, list[PairedSetup]] = defaultdict(list)
    managed_only: dict[date, int] = defaultdict(int)
    for (day, _con_id, _bar), sides in sorted(by_key.items(), key=lambda item: (item[0][0], item[0][2], item[0][1])):
        baseline = sides.get(BASELINE_STRATEGY)
        managed = sides.get(MANAGED_STRATEGY)
        if baseline is None:
            managed_only[day] += 1
            continue
        decision = judged = None
        if managed is not None:
            decision = decision_by_setup.get((managed.run_id, managed.fingerprint))
            judged = (managed.run_id, managed.fingerprint) in judged_keys
        pairs_by_day[day].append(_pair(baseline, managed, decision, bool(judged), managed_day=day in managed_days))

    plans_by_day: dict[date, list[tuple[str, int | None, Decimal]]] = defaultdict(list)
    for day, outcome, latency, cost in plans:
        plans_by_day[day].append((outcome, latency, cost))
    days_seen = sorted(set(pairs_by_day) | set(managed_only) | set(decisions_by_day) | set(plans_by_day) | set(spend))
    day_scores: list[DayScore] = []
    for day in days_seen:
        attempts = plans_by_day.get(day, [])
        stats = PlanStats(
            attempts=len(attempts),
            valid=sum(1 for outcome, _latency, _cost in attempts if outcome == PLAN_VALID),
            latencies_ms=tuple(latency for _outcome, latency, _cost in attempts if latency is not None),
            cost_usd=sum((cost for _outcome, _latency, cost in attempts), _ZERO),
            spent_usd=spend.get(day, _ZERO),
        )
        day_scores.append(
            _day_score(
                day,
                pairs_by_day.get(day, []),
                managed_only=managed_only.get(day, 0),
                decisions=decisions_by_day.get(day, []),
                plans=stats,
            )
        )

    pairs = [pair for day in days_seen for pair in pairs_by_day.get(day, [])]
    filled = [pair for pair in pairs if pair.filled]
    picks = [pair for pair in filled if pair.selected]
    trades = [
        EvidenceTrade(
            session_date=pair.session_date,
            exit_at=pair.exit_at,
            net_r=pair.net_r,  # type: ignore[arg-type]
            reviewed=pair.judged,
            selected=pair.selected,
        )
        for pair in filled
    ]
    focus_trades = [
        EvidenceTrade(session_date=t.session_date, exit_at=t.exit_at, net_r=t.net_r, selected=pair.managed)
        for t, pair in zip(trades, filled, strict=True)
    ]
    seed = identity.fingerprint()
    bounds = {
        "all": day_clustered_bootstrap(trades, seed_label=f"{seed}:overlay:all"),
        "picked": day_clustered_bootstrap([t for t in trades if t.selected], seed_label=f"{seed}:overlay:picked"),
        "focus": day_clustered_bootstrap([t for t in focus_trades if t.selected], seed_label=f"{seed}:overlay:focus"),
        "non_focus": day_clustered_bootstrap(
            [t for t, pair in zip(trades, filled, strict=True) if pair.non_focus],
            seed_label=f"{seed}:overlay:non_focus",
        ),
        "picked_minus_all": paired_difference_bootstrap(
            [t for t in trades if t.reviewed], seed_label=f"{seed}:overlay:picked_minus_all"
        ),
        "focus_minus_all": paired_difference_bootstrap(focus_trades, seed_label=f"{seed}:overlay:focus_minus_all"),
    }
    managed_sessions = sum(1 for day in day_scores if day.managed_day)
    return OverlayScorecard(
        start=start,
        end=end,
        days=tuple(day_scores),
        all=_sample(pair.net_r for pair in filled),  # type: ignore[misc]
        picked=_sample(pair.net_r for pair in picks),  # type: ignore[misc]
        focus=_sample(pair.net_r for pair in filled if pair.managed),  # type: ignore[misc]
        non_focus=_sample(pair.net_r for pair in filled if pair.non_focus),  # type: ignore[misc]
        take=_sample(pair.net_r for pair in picks),  # type: ignore[misc]
        abstain=_sample(pair.net_r for pair in filled if pair.judged and not pair.selected),  # type: ignore[misc]
        scaled_r=sum((pair.net_r * pair.step for pair in picks), _ZERO),  # type: ignore[operator]
        unscaled_r=sum((pair.net_r for pair in picks), _ZERO),  # type: ignore[misc]
        bounds=bounds,
        phase3=phase3_readiness(trades, identity=identity, managed_sessions=managed_sessions),
        identity=identity,
    )


def sessions_back(end: date, days: int) -> date:
    """The first of the ``days`` XNYS sessions ending at ``end`` (``end`` itself counts)."""
    from trading.research.bars_store import previous_session

    if days < 1:
        raise ValueError("the range is at least one session")
    start = end
    for _ in range(days - 1):
        start = previous_session(start)
    return start


def read_scorecard(
    database: str | Path,
    *,
    end: date,
    days: int | None = DEFAULT_DAYS,
    fill_model_id: str = FILL_MODEL_ID,
) -> OverlayScorecard:
    """The scorecard for the ``days`` sessions ending at ``end`` (all of them for None), read-only."""
    start = None if days is None else sessions_back(end, days)
    path = Path(database).expanduser()
    if not path.is_file():
        return OverlayScorecard(start=start, end=end, readable=False, note="no database")
    uri = f"file:{quote(str(path.resolve()))}?mode=ro"
    bounds_args = (end.isoformat(), None if start is None else start.isoformat(), None if start is None else start.isoformat())
    try:
        with closing(sqlite3.connect(uri, uri=True, timeout=SCHEMA_READ_TIMEOUT_SECONDS)) as db:
            marks = ", ".join("?" for _ in REQUIRED_TABLES)
            present = {
                str(row[0])
                for row in db.execute(
                    f"SELECT name FROM sqlite_master WHERE type='table' AND name IN ({marks})", REQUIRED_TABLES
                )
            }
            missing = [name for name in REQUIRED_TABLES if name not in present]
            if missing:
                return OverlayScorecard(
                    start=start, end=end, readable=False, note=f"schema_migration_pending: no {missing[0]}"
                )
            setup_rows = db.execute(_SETUPS, (fill_model_id, MANAGED_STRATEGY, BASELINE_STRATEGY, *bounds_args)).fetchall()
            decision_rows = db.execute(_DECISIONS, bounds_args).fetchall()
            verdict_rows = db.execute(_VERDICTS, (MANAGED_STRATEGY, *bounds_args)).fetchall()
            plan_rows = db.execute(_PLANS, bounds_args).fetchall()
            spend_rows = db.execute(_SPEND, (PLAN_SPEND_PURPOSE, *bounds_args)).fetchall()
            identity_row = db.execute(_IDENTITY, (MANAGED_STRATEGY, *bounds_args)).fetchone()
    except sqlite3.Error as exc:
        return OverlayScorecard(start=start, end=end, readable=False, note=type(exc).__name__)
    setups = [row for row in (_setup_row(item) for item in setup_rows) if row is not None]
    decisions = [_decision_row(item) for item in decision_rows]
    verdicts = [(str(run_id), str(fingerprint)) for run_id, fingerprint, mode in verdict_rows if str(mode) == "managed"]
    plans = []
    for session_date, outcome, latency, cost in plan_rows:
        try:
            plans.append(
                (
                    date.fromisoformat(str(session_date)),
                    str(outcome),
                    None if latency is None else int(latency),
                    _decimal(cost),
                )
            )
        except (ValueError, InvalidOperation):
            continue
    spend: dict[date, Decimal] = {}
    for spend_date, spent in spend_rows:
        try:
            spend[date.fromisoformat(str(spend_date))] = _decimal(spent)
        except (ValueError, InvalidOperation):
            continue
    return build_scorecard(
        start=start,
        end=end,
        setups=setups,
        decisions=decisions,
        verdicts=verdicts,
        plans=plans,
        spend=spend,
        identity=_seed_identity(None if identity_row is None else identity_row[0]),
    )


# --- plain text -------------------------------------------------------------------------


def _r(value: Decimal | None) -> str:
    if value is None:
        return "n/a"
    return f"{value.quantize(Decimal('0.01'), rounding=ROUND_HALF_EVEN):+}R"


def _pct(value: Decimal | None) -> str:
    if value is None:
        return "n/a"
    return f"{(value * 100).quantize(Decimal('0.1'), rounding=ROUND_HALF_EVEN)}%"


def _seconds(value: int | None) -> str:
    if value is None:
        return "n/a"
    return f"{(Decimal(value) / 1000).quantize(Decimal('0.1'), rounding=ROUND_HALF_EVEN)} s"


def _usd(value: Decimal) -> str:
    return f"${value.quantize(Decimal('0.01'), rounding=ROUND_HALF_EVEN)}"


def _with_count(sample: Sample) -> str:
    return f"{_r(sample.mean_r)} ({sample.trades})"


def _bound(bound: BootstrapBound) -> str:
    if bound.lower is None or bound.upper is None:
        return "n/a"
    return f"{_r(bound.lower)}..{_r(bound.upper)}"


def _hits(sample: Sample) -> str:
    return f"{sample.wins}/{sample.trades} won ({_pct(sample.hit_rate)})" if sample.trades else "none filled"


_AI_TEXT = {
    "advisory": "AI advisory (its picks cost R)",
    "active": "AI review keeps its say",
    "insufficient_data": "too little data",
}
_REASON_TEXT = {
    MANAGED_SESSIONS_BELOW_MINIMUM: "managed sessions",
    JUDGED_SETUPS_BELOW_MINIMUM: "judged setups",
    PICKED_SETUPS_BELOW_MINIMUM: "AI-picked setups",
    DIFFERENCE_BOUNDS_UNAVAILABLE: "no bounds yet (two managed days with trades needed)",
    AI_PICKS_COST_R: "the AI's picks measurably cost R",
    PICKS_BELOW_TOLERANCE: f"picks trail all setups by more than {abs(PHASE3_TOLERANCE_R)}R",
}


def _reason_texts(readiness: Phase3Readiness) -> list[str]:
    counts = {
        MANAGED_SESSIONS_BELOW_MINIMUM: f"{readiness.managed_sessions}/{MIN_SESSIONS}",
        JUDGED_SETUPS_BELOW_MINIMUM: f"{readiness.ai.all_trades}/{MIN_LIVE_SETUPS}",
        PICKED_SETUPS_BELOW_MINIMUM: f"{readiness.ai.selected_trades}/{MIN_MANAGED_SETUPS}",
    }
    texts = []
    for reason in readiness.reasons:
        label = _REASON_TEXT.get(reason, reason)
        texts.append(f"{label} {counts[reason]}" if reason in counts else label)
    return texts


def phase3_line(readiness: Phase3Readiness) -> str:
    verdict = "READY for the owner's phase-3 review" if readiness.ready else "not ready: " + ", ".join(_reason_texts(readiness))
    return f"Phase 3 ({RULE_VERSION}, picks vs all judged): {verdict}"


def scorecard_lines(card: OverlayScorecard) -> list[str]:
    """The owner's plain-text scorecard (no Markdown, no identifiers)."""
    span = f"{card.start.isoformat() if card.start else 'all sessions'}..{card.end.isoformat()}"
    title = (
        f"AI overlay scorecard ({SCORECARD_VERSION}): {MANAGED_STRATEGY} vs {BASELINE_STRATEGY}"
        " paired on the same contract and bar, net R after costs"
    )
    if not card.readable:
        return [title, f"Range {span}: unavailable ({card.note})"]
    lines = [
        title,
        f"Range {span}: {card.sessions} sessions with data, {card.managed_sessions} managed by a plan",
        (
            f"Paired setups {card.pairs}: {card.filled} filled, {card.untracked} not tracked yet,"
            f" {card.managed_only} managed-only, {card.price_mismatches} price mismatches"
        ),
        (
            f"Net R per trade: all {_with_count(card.all)} · AI-picked {_with_count(card.picked)}"
            f" · focus {_with_count(card.focus)} · non-focus {_with_count(card.non_focus)}"
        ),
        (
            f"90% day-clustered bounds: all {_bound(card.bounds['all'])}"
            f" · AI-picked {_bound(card.bounds['picked'])} · focus {_bound(card.bounds['focus'])}"
            f" · non-focus {_bound(card.bounds['non_focus'])}"
        ),
        (
            f"Differences: picks - all {_r(card.bounds['picked_minus_all'].estimate)}"
            f" ({_bound(card.bounds['picked_minus_all'])}) · focus - all"
            f" {_r(card.bounds['focus_minus_all'].estimate)} ({_bound(card.bounds['focus_minus_all'])})"
        ),
        f"Managed reviewer: TAKE {_hits(card.take)} · ABSTAIN {_hits(card.abstain)} · judged {card.judged}",
        (
            f"Size steps on picks: {_r(card.scaled_r)} with steps vs {_r(card.unscaled_r)} at full size"
            f" ({_r(card.scaled_r - card.unscaled_r)})"
        ),
        (
            f"Managed decisions {card.decisions.total}: {_pct(card.decisions.not_in_focus_share)} not in focus"
            f" · {_pct(card.decisions.fallback_share)} fallback neutral"
        ),
        (
            f"Plans {card.plans.attempts}: {_pct(card.plans.validity)} valid · latency p50"
            f" {_seconds(card.plans.latency_p50_ms)}, p95 {_seconds(card.plans.latency_p95_ms)}"
            f" · spend {_usd(card.plans.spent_usd)} (budget {_usd(MANAGER_DAILY_BUDGET_USD)}/day)"
        ),
    ]
    if card.phase3 is not None:
        ai = card.phase3.ai
        lines.append(
            f"Do no harm: {_AI_TEXT.get(ai.status, ai.status)}"
            + (
                f" (picks - all {_r(ai.difference.estimate)}, upper bound {_r(ai.difference.upper)})"
                if ai.difference.upper is not None
                else ""
            )
        )
        lines.append(phase3_line(card.phase3))
    lines.append("Per session:")
    for day in card.days:
        lines.append("  " + day_line(day))
    if not card.days:
        lines.append("  none")
    return lines


def day_line(day: DayScore) -> str:
    parts = [f"{day.session_date.isoformat()}: {day.pairs} pairs ({day.filled} filled)", f"all {_r(day.all.mean_r)}"]
    if day.managed_day:
        parts += [
            f"picks {_with_count(day.picked)}",
            f"focus {_with_count(day.focus)}",
            f"non-focus {_with_count(day.non_focus)}",
            f"TAKE {day.take.wins}/{day.take.trades}",
            f"ABSTAIN {day.abstain.wins}/{day.abstain.trades}",
            f"steps {_r(day.scaled_r - day.unscaled_r)}",
        ]
    elif day.decisions.total:
        parts.append("neutral all day")
    parts.append(f"fallback {_pct(day.decisions.fallback_share)}")
    parts.append(f"plans {day.plans.valid}/{day.plans.attempts}")
    parts.append(f"p50 {_seconds(day.plans.latency_p50_ms)}")
    parts.append(_usd(day.plans.spent_usd))
    return " · ".join(parts)


def scorecard_text(card: OverlayScorecard) -> str:
    return "\n".join(scorecard_lines(card))


def readiness_lines(card: OverlayScorecard) -> list[str]:
    """The weekly summary's section: what the phone needs to know about phase 3."""
    head = f"Phase-3 readiness ({MANAGED_STRATEGY} vs {BASELINE_STRATEGY} on the same setups, net R)"
    if not card.readable:
        return [f"{head}: unavailable ({card.note})"]
    if card.phase3 is None or not card.pairs:
        return [
            f"{head}: no paired setups yet",
            "  "
            + (
                phase3_line(card.phase3).split(": ", 1)[1]
                if card.phase3 is not None
                else f"not ready: managed sessions 0/{MIN_SESSIONS}"
            ),
        ]
    ai = card.phase3.ai
    difference = (
        f"{_r(ai.difference.estimate)} (90% bounds {_bound(ai.difference)})"
        if ai.difference.estimate is not None
        else "n/a"
    )
    return [
        head,
        (
            f"  {card.managed_sessions} managed sessions of {card.sessions} · {card.pairs} paired setups"
            f" · {card.judged} judged · {card.picked.trades} AI-picked filled"
        ),
        (
            f"  all {_r(card.all.mean_r)}/trade · AI-picked {_r(card.picked.mean_r)}"
            f" · focus {_r(card.focus.mean_r)} · non-focus {_r(card.non_focus.mean_r)}"
        ),
        f"  picks - all {difference}: {_AI_TEXT.get(ai.status, ai.status)}",
        "  " + phase3_line(card.phase3).split(": ", 1)[1],
    ]


__all__ = [
    "ACCEPTED_OUTCOMES",
    "AI_PICKS_COST_R",
    "BASELINE_STRATEGY",
    "DEFAULT_DAYS",
    "DIFFERENCE_BOUNDS_UNAVAILABLE",
    "JUDGED_SETUPS_BELOW_MINIMUM",
    "MANAGED_SESSIONS_BELOW_MINIMUM",
    "MANAGED_STRATEGY",
    "PHASE3_TOLERANCE_R",
    "PICKED_SETUPS_BELOW_MINIMUM",
    "PICKS_BELOW_TOLERANCE",
    "REQUIRED_TABLES",
    "REVIEW_FAILED_OUTCOMES",
    "SCORECARD_UNREADABLE",
    "SCORECARD_VERSION",
    "DayScore",
    "DecisionStats",
    "OverlayScorecard",
    "PairedSetup",
    "Phase3Readiness",
    "PlanStats",
    "Sample",
    "build_scorecard",
    "day_line",
    "phase3_line",
    "phase3_readiness",
    "read_scorecard",
    "readiness_lines",
    "scorecard_lines",
    "scorecard_text",
    "sessions_back",
]

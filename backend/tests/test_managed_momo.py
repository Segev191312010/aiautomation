"""Managed MOMO (AP_MOMO_HOD_V1, shadow) against MOMO_HOD_V2 on a recorded session.

The replays use the real IBKR PAPER bars of 2026-10-12 (tests/fixtures/momo):
BF B bought at 11:50 with V2's wide stop (3726 shares), FIGS skipped as
extended. Required by the plan (Principles 2 and 3):

* the neutral-manager equivalence test: a plan whose focus is every eligible
  name with steps of 1, and an always-PROPOSE stub, gives decisions byte for
  byte identical to MOMO_HOD_V2's; so do the switch off, a missing, expired
  or unreadable plan (neutral mode, never an ABSTAIN);
* managed pauses are a superset of V2's pauses;
* the per-decision narrow-only property: over random plans and verdicts every
  managed decision keeps final = floor(policy x day x setup) <= policy, inside
  the eligible set and, when managed, inside the focus;
* the day latch: flip, crash, restart gives the same latch; a mid-day OFF is
  neutral for the rest of the day.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import itertools
import logging
import random
import secrets
import sqlite3
from types import SimpleNamespace
from typing import Any
import uuid

import pytest

import auth  # noqa: F401 - imported before any test: its import draws a random token
from tests.test_momo_v2 import BF_B, FIGS, PRIOR, SESSION, _et, _proposal, _ReplayMarket
from trading.contracts import AIReview, AccountSnapshot, RiskPolicy, Run, ScannerRun, fingerprint_payload
from trading.intelligence.managed_review import ManagedReviewProvider, SetupVerdict
from trading.intelligence.review_provider import ReviewSessionLimits, ReviewSessionUsage
from trading.manager.constants import (
    MANAGER_FALLBACK_NEUTRAL,
    PLAN_EXPIRED,
    PLAN_MISSING,
    PLAN_TTL,
    SWITCH_OFF,
    SWITCH_OFF_MIDDAY,
)
from trading.manager.managed import ManagedStrategy, narrowing_problems
from trading.manager.plan import DayPlan, FocusEntry, StrategyStep
from trading.manager.store import PlanRecord, insert_plan, managed_decisions, plan_id_for
from trading.manager.switch import latch_day_mode, read_day_mode, set_switch
from trading.market.ibkr_boundary import LiquidityProfile
from trading.orchestration import RepositoryBackedShadowRunner, ShadowPipeline
from trading.orchestration.session_runner import (
    AP_MOMO_STRATEGY_NAME,
    PollSchedule,
    ShadowSessionRunner,
    ap_momo_strategy_spec,
    ensure_strategy,
    momo_strategy_spec,
)
from trading.risk.engine import scaled_quantity
from trading.rules.momo import MomoConfig, MomoStrategy
from trading.scheduler.sessions import market_session
from trading.sendable_identities import SENDABLE_IDENTITIES, carries_sendable_prefix

RUNNING = ("momo", "orb", "vwap", "gap")
STEPS = (Decimal("0"), Decimal("0.25"), Decimal("0.5"), Decimal("0.75"), Decimal("1"))
PLAN_SLOTS = ((9, 46), (10, 30), (11, 30), (12, 30), (13, 30), (14, 30))
ELIGIBLE = (BF_B.con_id, FIGS.con_id)


# --- a deterministic world: two databases can be compared byte for byte ------------------


class _Frozen:
    """``datetime.now`` follows the simulated clock; every id and token is a counter."""

    def __init__(self, clock: dict[str, datetime]) -> None:
        self.clock = clock
        self.ids = itertools.count(1)
        self.tokens = itertools.count(1)

    def install(self, monkeypatch) -> None:
        import trading.contracts as contracts
        import trading.orchestration.pipeline as pipeline
        import trading.risk.engine as risk_engine
        import trading.storage.repository as repository

        clock = self.clock

        class FrozenDatetime(datetime):
            @classmethod
            def now(cls, tz=None):  # noqa: ANN001 - datetime's own signature
                moment = clock["now"]
                return moment.astimezone(tz) if tz is not None else moment.replace(tzinfo=None)

        def ids() -> uuid.UUID:
            return uuid.UUID(int=next(self.ids))

        for module in (contracts, pipeline, risk_engine):
            monkeypatch.setattr(module, "datetime", FrozenDatetime)
        monkeypatch.setattr(contracts, "uuid4", ids)
        monkeypatch.setattr(repository, "uuid4", ids)
        monkeypatch.setattr(repository, "_timestamp", lambda: clock["now"])
        monkeypatch.setattr(secrets, "token_urlsafe", lambda _n: f"token-{next(self.tokens):040d}")


def _account(clock: dict[str, datetime]):
    async def read_account() -> AccountSnapshot:
        return AccountSnapshot(
            paper_account_id="DU1234567",
            client_id=24,
            observed_at=clock["now"],
            net_liquidation=Decimal("665000"),
            available_funds=Decimal("665000"),
            realized_day_pnl=Decimal("0"),
            unrealized_pnl=Decimal("0"),
        )

    return read_account


def _hot_list(clock: dict[str, datetime]):
    async def hot_list(run: Run):
        parameters: dict[str, object] = {"source": "screener_hotlist", "top": 35}
        scanner = ScannerRun(
            id=f"{run.id}-hotlist",
            run_id=run.id,
            parameters=parameters,
            parameters_fingerprint=fingerprint_payload(parameters),
            status="completed",
            started_at=clock["now"],
        )
        candidates = tuple(replay.candidate(scanner.id, rank) for rank, replay in enumerate((BF_B, FIGS), 1))
        liquidity = {
            replay.con_id: LiquidityProfile(
                con_id=replay.con_id,
                reference_price=replay.reference_price,
                average_dollar_volume=replay.average_dollar_volume,
                sessions_used=20,
                last_session_date=PRIOR,
            )
            for replay in (BF_B, FIGS)
        }
        return scanner, candidates, liquidity

    return hot_list


def _risk_off_reader(clock: dict[str, datetime]):
    """A risk_off half hour (11:00-11:30 ET) so the gate pauses both runs."""

    async def reader(_session_date: date, *, now: datetime, max_age: timedelta):
        regime = "risk_off" if _et(SESSION, 11, 0) <= now < _et(SESSION, 11, 30) else "neutral"
        return SimpleNamespace(regime=regime, summary=f"SPY regime {regime}")

    return reader


class _AlwaysPropose:
    """V2's reviewer stub: PROPOSE every setup (a fixed, byte-stable review)."""

    def __init__(self) -> None:
        self.setups: list[str] = []
        self.usage = ReviewSessionUsage(calls=0, cost_usd=Decimal("0"))

    async def propose_trade(self, *, setup) -> AIReview:
        self.setups.append(setup.symbol)
        return _proposal(setup)


@dataclass
class _ManagedReviewer:
    """The managed reviewer stub: PROPOSE (or ABSTAIN) with a chosen setup step."""

    step_for: Callable[[str], Decimal] = lambda _symbol: Decimal("1")
    abstain: Callable[[str], bool] = lambda _symbol: False
    contexts: list[Any] = field(default_factory=list)

    @property
    def usage(self) -> ReviewSessionUsage:
        return ReviewSessionUsage(calls=len(self.contexts), cost_usd=Decimal("0"))

    async def review(self, *, setup, context):
        self.contexts.append(context)
        review = _proposal(setup)
        if self.abstain(setup.symbol):
            review = AIReview.model_validate({**review.model_dump(), "decision": "ABSTAIN"})
        return review, SetupVerdict(step=self.step_for(setup.symbol), note="stub verdict")


def _plan(focus: Sequence[int], *, momo_step: Decimal = Decimal("1"), offered: Sequence[int] = ELIGIBLE) -> DayPlan:
    return DayPlan(
        regime="trend_up",
        strategies={name: StrategyStep(step=momo_step if name == "momo" else Decimal("1"), reason="test")
                    for name in RUNNING},
        focus=tuple(FocusEntry(con_id=con_id, strategies=("momo",), note=f"focus {con_id}") for con_id in focus),
    )


async def _store_plans(plans: Sequence[tuple[tuple[int, int], DayPlan]], offered: Sequence[int] = ELIGIBLE) -> None:
    for (hour, minute), plan in plans:
        created = _et(SESSION, hour, minute, 30)
        await insert_plan(PlanRecord(
            plan_id=plan_id_for(SESSION, f"{hour:02d}:{minute:02d}", 1),
            session_date=SESSION,
            slot=f"{hour:02d}:{minute:02d}",
            created_at=created,
            valid_until=created + PLAN_TTL,
            manager_version="manager-v1",
            prompt_id="day-plan-v1",
            model="claude-opus-5-5",
            outcome="valid",
            plan=plan,
            candidates=tuple(sorted(set(offered) | {entry.con_id for entry in plan.focus})),
            gates={},
            context_digest="e" * 64,
        ))


async def _day(
    tmp_path,
    monkeypatch,
    label: str,
    *,
    managed: bool,
    spec=None,
    switch: Sequence[tuple[tuple[int, int], str]] = (),
    plans: Sequence[tuple[tuple[int, int], DayPlan]] = (),
    reviewer: _ManagedReviewer | None = None,
    plan_reader=None,
    offered: Sequence[int] = ELIGIBLE,
):
    """One recorded session on its own database; returns its tables and the controller."""
    from config import cfg
    from db.core import init_db

    path = tmp_path / f"{label}.db"
    monkeypatch.setattr(cfg, "DB_PATH", str(path))
    clock = {"now": _et(SESSION, 9, 0)}
    _Frozen(clock).install(monkeypatch)
    await init_db()
    strategy = await ensure_strategy(owner_id="owner-a", spec=spec or momo_strategy_spec())
    for (hour, minute), action in switch:
        if (hour, minute) <= (9, 55):
            await set_switch(action, source="cli", actor="test", reason="test", now=_et(SESSION, hour, minute))
    await _store_plans(plans, offered)
    clock["now"] = _et(SESSION, 9, 50)

    async def sleep(seconds: float) -> None:
        clock["now"] += timedelta(seconds=seconds)
        for (hour, minute), action in switch:
            at = _et(SESSION, hour, minute)
            if (hour, minute) > (9, 55) and clock["now"] - timedelta(seconds=seconds) < at <= clock["now"]:
                await set_switch(action, source="cli", actor="test", reason="test", now=at)

    from trading.operations.shadow_service import risk_off_entry_gate

    risk_off_gate = risk_off_entry_gate(SESSION, clock=lambda: clock["now"], reader=_risk_off_reader(clock))
    neutral = _AlwaysPropose()
    controller = None
    provider: Any = neutral
    entry_gate = risk_off_gate
    candidate_filter = None
    if managed:
        extra = {"plan_reader": plan_reader} if plan_reader is not None else {}
        controller = await ManagedStrategy.open(
            session_date=SESSION, family="momo", clock=lambda: clock["now"], **extra
        )
        provider = ManagedReviewProvider(
            neutral=neutral,
            managed=reviewer or _ManagedReviewer(),
            context_for=controller.review_context,
            on_verdict=controller.on_verdict,
            limits=ReviewSessionLimits(),
            neutral_model_id="test-model",
            neutral_prompt_id="momo-review-v2",
            managed_model_id="test-model",
            managed_prompt_id="momo-managed-review-v1",
        )
        entry_gate = controller.entry_gate(risk_off_gate)
        candidate_filter = controller.admits
    runner = ShadowSessionRunner(
        market=_ReplayMarket(clock),
        read_account=_account(clock),
        runner=RepositoryBackedShadowRunner(
            pipeline=ShadowPipeline(proposal_provider=provider, strategy=MomoStrategy(), sizing=controller),
            clock=lambda: clock["now"],
            lease_ttl=timedelta(hours=6),
            lease_renew_interval=timedelta(hours=1),
        ),
        clock=lambda: clock["now"],
        poll_schedule=PollSchedule(offset=timedelta(seconds=15), bar_max_age=timedelta(seconds=240)),
        sleep=sleep,
        entry_gate=entry_gate,
        entry_gate_name="momo",
        holder_id="holder-test",
        candidate_filter=candidate_filter,
    )
    from trading.operations.shadow_service import strategy_entry_window

    report = await runner.run(
        session=market_session(SESSION),
        strategy=strategy,
        policy=RiskPolicy(),
        source_sha="abcdef1",
        policy_fingerprints={"risk": RiskPolicy().fingerprint(), "momo_hod": MomoConfig().fingerprint()},
        run_id="momo-equivalence",
        candidate_source=_hot_list(clock),
        window_start=strategy_entry_window("momo", SESSION)[0],
    )
    if controller is not None:
        # As the managed session does after its cutoff sweep.
        await controller.record_unfocused(run_id=report.run_id, owner_id="owner-a")
    with sqlite3.connect(path) as db:
        tables = {
            table: db.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
            for table in ("autotrader_candidate_decisions", "autotrader_candidates", "autotrader_scanner_runs")
        }
    audits = await managed_decisions(SESSION) if managed else []
    return SimpleNamespace(report=report, tables=tables, audits=audits, controller=controller,
                           neutral=neutral, path=path)


ALL_ELIGIBLE = [(slot, _plan(ELIGIBLE)) for slot in PLAN_SLOTS]


@pytest.mark.parametrize(
    "scenario",
    [
        # The required case: the switch on, a plan in force all day that keeps
        # every eligible name at steps of 1, and an always-PROPOSE reviewer.
        dict(switch=[((9, 0), "on")], plans=ALL_ELIGIBLE, mode="managed"),
        # The switch off (no event at all): neutral by the owner's choice.
        dict(switch=[], plans=ALL_ELIGIBLE, mode=SWITCH_OFF),
        # The switch on, the manager down: no plan at all.
        dict(switch=[((9, 0), "on")], plans=[], mode=PLAN_MISSING),
        # One early plan that expires at 11:01; nothing after.
        dict(switch=[((9, 0), "on")], plans=ALL_ELIGIBLE[:1], mode=PLAN_EXPIRED),
        # A plan lookup that raises on every poll.
        dict(switch=[((9, 0), "on")], plans=ALL_ELIGIBLE, mode="plan_error", broken_reader=True),
        # Turned off mid-session: neutral for the rest of the day.
        dict(switch=[((9, 0), "on"), ((11, 40), "off")], plans=ALL_ELIGIBLE, mode=SWITCH_OFF_MIDDAY),
    ],
    ids=["neutral-manager", "switch-off", "plan-missing", "plan-expired", "plan-error", "off-mid-day"],
)
@pytest.mark.anyio
async def test_neutral_manager_equivalence_is_byte_identical_to_momo_hod_v2(tmp_path, monkeypatch, scenario):
    """Required test: the managed machinery adds nothing when the plan keeps
    everything at steps of 1, or when it falls back to neutral mode."""

    async def broken(_day, _now):
        raise RuntimeError("database locked")

    v2 = await _day(tmp_path, monkeypatch, "v2", managed=False)
    managed = await _day(
        tmp_path,
        monkeypatch,
        "managed",
        managed=True,
        switch=scenario["switch"],
        plans=scenario["plans"],
        plan_reader=broken if scenario.get("broken_reader") else None,
    )

    # V2's known day: BF B bought (3726) at 11:50, FIGS a rule rejection.
    outcomes = sorted(row[6] for row in v2.tables["autotrader_candidate_decisions"])
    assert outcomes == ["rule_rejected", "shadow_accepted"]
    # Byte for byte: every decision row (ids, payloads, timestamps), every candidate.
    assert managed.tables == v2.tables
    assert managed.report.summary() == v2.report.summary()
    assert v2.report.paused_polls == managed.report.paused_polls > 0  # the risk_off half hour
    # One managed decision formed a setup: BF B, at full policy size.
    [audit] = managed.audits
    assert (audit["con_id"], audit["outcome"], audit["policy_qty"], audit["final_qty"]) == (
        BF_B.con_id, "shadow_accepted", 3726, 3726,
    )
    assert (audit["day_step"], audit["setup_step"]) == (1.0, 1.0)
    assert audit["eligible_json"] == sorted(ELIGIBLE)
    if scenario["mode"] == "managed":
        assert audit["mode"] == "managed" and audit["mark"] is None
        assert audit["plan_id"] == plan_id_for(SESSION, "11:30", 1)
        assert audit["focus_json"] == sorted(ELIGIBLE)
    else:
        # Neutral: V2's reviewer and prompt, steps 1, never an ABSTAIN for a plan failure.
        assert audit["mode"] == "neutral" and audit["plan_id"] is None
        assert audit["neutral_reason"] == scenario["mode"]
        fallback = scenario["mode"] in {PLAN_MISSING, PLAN_EXPIRED, "plan_error"}
        assert audit["mark"] == (MANAGER_FALLBACK_NEUTRAL if fallback else None)
        assert managed.neutral.setups == ["BF B"]


@pytest.mark.anyio
async def test_the_ap_identity_decides_as_v2_and_is_never_sendable(tmp_path, monkeypatch):
    v2 = await _day(tmp_path, monkeypatch, "v2", managed=False)
    ap = await _day(tmp_path, monkeypatch, "ap", managed=True, spec=ap_momo_strategy_spec(),
                    switch=[((9, 0), "on")], plans=ALL_ELIGIBLE)

    def projection(day):
        import json

        rows = []
        for row in day.tables["autotrader_candidate_decisions"]:
            payload = json.loads(row[10])
            review = payload.get("ai_review") or {}
            risk = payload.get("risk") or {}
            intent = risk.get("intent") or {}
            rows.append((
                payload["candidate_id"].rsplit("-", 1)[-1], row[6], row[11], review.get("decision"),
                review.get("rationale"), risk.get("approved"), tuple(risk.get("codes") or ()),
                tuple(intent.get(key) for key in ("quantity", "entry_limit", "stop_price", "target_price")),
            ))
        return sorted(rows)

    assert projection(ap) == projection(v2)
    assert AP_MOMO_STRATEGY_NAME == "AP_MOMO_HOD_V1"
    assert not carries_sendable_prefix(AP_MOMO_STRATEGY_NAME)
    assert AP_MOMO_STRATEGY_NAME not in SENDABLE_IDENTITIES
    assert "MOMO_HOD_AP_V1" not in SENDABLE_IDENTITIES  # phase 3, reviewed, not here
    with sqlite3.connect(ap.path) as db:
        names = {row[0] for row in db.execute("SELECT json_extract(spec_json, '$.name') FROM autotrader_strategy_versions")}
    assert names == {"AP_MOMO_HOD_V1"}


@pytest.mark.anyio
async def test_a_narrow_plan_skips_names_outside_its_focus_without_a_review(tmp_path, monkeypatch):
    focus_figs_only = [(slot, _plan([FIGS.con_id])) for slot in PLAN_SLOTS]
    reviewer = _ManagedReviewer()
    day = await _day(tmp_path, monkeypatch, "narrow", managed=True, switch=[((9, 0), "on")],
                     plans=focus_figs_only, reviewer=reviewer)

    outcomes = {row[2].rsplit("-", 1)[-1]: row[6] for row in day.tables["autotrader_candidate_decisions"]}
    # BF B was never assessed: no data, no review; it ends data_blocked at the cutoff...
    assert outcomes == {str(BF_B.con_id): "data_blocked", str(FIGS.con_id): "rule_rejected"}
    assert reviewer.contexts == [] and day.neutral.setups == []
    # ...and the manager's record says why, so evidence never reads a data failure.
    [skip] = day.audits
    assert (skip["con_id"], skip["outcome"], skip["reason"], skip["mode"]) == (
        BF_B.con_id, "data_blocked", "manager_not_in_focus", "managed",
    )
    assert skip["focus_json"] == [FIGS.con_id] and BF_B.con_id in skip["eligible_json"]
    assert (skip["policy_qty"], skip["final_qty"]) == (None, None)
    assert skip["plan_id"] == plan_id_for(SESSION, "14:30", 1)


@pytest.mark.anyio
async def test_steps_scale_the_policy_quantity_down_and_zero_skips(tmp_path, monkeypatch):
    half = [(slot, _plan(ELIGIBLE, momo_step=Decimal("0.5"))) for slot in PLAN_SLOTS]
    day = await _day(tmp_path, monkeypatch, "half", managed=True, switch=[((9, 0), "on")], plans=half,
                     reviewer=_ManagedReviewer(step_for=lambda _symbol: Decimal("0.75")))
    [audit] = day.audits
    assert (audit["policy_qty"], audit["day_step"], audit["setup_step"], audit["final_qty"]) == (3726, 0.5, 0.75, 1397)

    skip = await _day(tmp_path, monkeypatch, "skip", managed=True, switch=[((9, 0), "on")], plans=half,
                      reviewer=_ManagedReviewer(step_for=lambda _symbol: Decimal("0")))
    [audit] = skip.audits
    assert (audit["outcome"], audit["policy_qty"], audit["final_qty"]) == ("risk_blocked", 3726, None)
    import json

    [bf_b] = [row for row in skip.tables["autotrader_candidate_decisions"] if row[6] == "risk_blocked"]
    assert json.loads(bf_b[10])["risk"]["codes"] == ["size_scaled_to_zero"]


@pytest.mark.anyio
async def test_a_day_step_of_zero_pauses_before_any_review(tmp_path, monkeypatch):
    off = [(slot, _plan(ELIGIBLE, momo_step=Decimal("0"))) for slot in PLAN_SLOTS]
    reviewer = _ManagedReviewer()
    day = await _day(tmp_path, monkeypatch, "off", managed=True, switch=[((9, 0), "on")], plans=off,
                     reviewer=reviewer)

    assert reviewer.contexts == [] and day.neutral.setups == [] and day.audits == []
    assert day.report.polls == day.report.paused_polls
    assert sorted(row[6] for row in day.tables["autotrader_candidate_decisions"]) == ["data_blocked", "data_blocked"]


# --- managed pauses are a superset of V2's -----------------------------------------------


@pytest.mark.parametrize("risk_off", ["open", "paused", "raises"])
@pytest.mark.parametrize("plan_state", ["none", "step0", "step05", "step1", "expired", "reader_raises"])
@pytest.mark.parametrize("switch_on", [True, False])
@pytest.mark.anyio
async def test_managed_pauses_whenever_v2_pauses(tmp_path, monkeypatch, risk_off, plan_state, switch_on):
    """Through the runner's own gate handling (fail-open on a raising gate)."""
    from tests.manager_support import migrated_async

    await migrated_async(tmp_path, monkeypatch)
    now = _et(SESSION, 11, 0)
    if switch_on:
        await set_switch("on", source="cli", actor="test", reason="test", now=now - timedelta(hours=2))
    steps = {"step0": Decimal("0"), "step05": Decimal("0.5"), "step1": Decimal("1")}
    if plan_state in steps or plan_state == "expired":
        created_hour = (8, 0) if plan_state == "expired" else (10, 30)
        await _store_plans([(created_hour, _plan(ELIGIBLE, momo_step=steps.get(plan_state, Decimal("1"))))])

    async def risk_off_gate():
        if risk_off == "raises":
            raise RuntimeError("context unreadable")
        return risk_off == "open", "regime"

    async def broken(_day, _now):
        raise RuntimeError("database locked")

    controller = await ManagedStrategy.open(
        session_date=SESSION, family="momo", clock=lambda: now,
        **({"plan_reader": broken} if plan_state == "reader_raises" else {}),
    )

    def runner_for(gate) -> ShadowSessionRunner:
        return ShadowSessionRunner(
            market=object(), read_account=_account({"now": now}), runner=object(),  # type: ignore[arg-type]
            clock=lambda: now, poll_schedule=PollSchedule(offset=timedelta(seconds=15), bar_max_age=timedelta(seconds=240)),
            entry_gate=gate,
        )

    v2_allowed = await runner_for(risk_off_gate)._entries_allowed()
    managed_allowed = await runner_for(controller.entry_gate(risk_off_gate))._entries_allowed()

    if not v2_allowed:
        assert not managed_allowed
    # The plan adds a pause only when it is in force with a day step of 0.
    plan_pauses = switch_on and plan_state == "step0"
    assert managed_allowed == (v2_allowed and not plan_pauses)


# --- the per-decision narrow-only property -------------------------------------------------


def _random_plans(rng: random.Random) -> list[tuple[tuple[int, int], DayPlan]]:
    plans = []
    for slot in PLAN_SLOTS:
        if rng.random() < 0.2:
            continue  # a missing plan: neutral until the next
        focus = [con_id for con_id in ELIGIBLE if rng.random() < 0.7]
        focus += rng.sample([111, 222, 333], rng.randint(0, 2))  # offered names that are not eligible
        plans.append((slot, _plan(focus, momo_step=rng.choice(STEPS))))
    return plans


@pytest.mark.anyio
async def test_property_every_managed_decision_only_narrows(tmp_path, monkeypatch):
    """Required property test, per decision, over random plans, verdicts and switches."""
    rng = random.Random(20261013)
    checked = 0
    seen: set[str] = set()
    for index in range(10):
        switch = [((9, 0), "on")]
        if rng.random() < 0.3:
            switch.append(((rng.choice((10, 11, 12, 13)), 5), "off"))
        day = await _day(
            tmp_path, monkeypatch, f"random-{index}", managed=True, switch=switch,
            plans=_random_plans(rng), offered=(*ELIGIBLE, 111, 222, 333),
            reviewer=_ManagedReviewer(
                step_for=lambda _symbol, rng=rng: rng.choice(STEPS),
                abstain=lambda _symbol, rng=rng: rng.random() < 0.2,
            ),
        )
        decisions = {row[0]: row for row in day.tables["autotrader_candidate_decisions"]}
        for audit in day.audits:
            checked += 1
            seen.add(audit["mode"])
            if audit["reason"] is not None:
                # The focus left it out until the cutoff: outside its focus, never sized.
                assert audit["reason"] == "manager_not_in_focus" and audit["mode"] == "managed"
                assert audit["con_id"] not in audit["focus_json"]
                assert audit["final_qty"] is None and audit["policy_qty"] is None
                seen.add("not_in_focus")
                continue
            if audit["final_qty"] is not None and audit["final_qty"] < audit["policy_qty"]:
                seen.add("scaled_down")
            if audit["outcome"] == "risk_blocked":
                seen.add("skipped")
            decision = decisions[audit["decision_id"]]
            assert audit["con_id"] in audit["eligible_json"] == sorted(ELIGIBLE)
            if audit["mode"] == "managed":
                assert audit["con_id"] in audit["focus_json"]
            else:
                assert (audit["day_step"], audit["setup_step"]) == (1.0, 1.0)
            if audit["final_qty"] is not None:
                product = Decimal(str(audit["day_step"])) * Decimal(str(audit["setup_step"]))
                assert audit["final_qty"] == scaled_quantity(audit["policy_qty"], product) <= audit["policy_qty"]
                assert decision[6] == "shadow_accepted"
            else:
                assert decision[6] != "shadow_accepted"
    assert checked >= 5
    # The random days exercised both modes, a scaled-down approval and a skip.
    assert seen >= {"managed", "neutral", "scaled_down", "not_in_focus"}, seen


def test_the_narrowing_check_names_every_widening():
    from trading.manager.store import ManagedDecisionRecord

    base = dict(
        decision_id="d", run_id="r", candidate_id="c", con_id=1, setup_fingerprint="a" * 64,
        session_date=SESSION, outcome="shadow_accepted", mode="managed", neutral_reason=None,
        mark=None, plan_id="p", eligible=(1, 2), focus=(1,), gates={}, day_step=Decimal("0.5"),
        setup_step=Decimal("1"), policy_qty=100, final_qty=50, created_at=_et(SESSION, 11, 0),
    )
    assert narrowing_problems(ManagedDecisionRecord(**base), size_multiplier=Decimal("0.5")) == []
    cases = {
        "contract outside the eligible set": dict(con_id=3, focus=(3,)),
        "contract outside the plan focus": dict(focus=(2,)),
        "final quantity above the policy quantity": dict(final_qty=150),
        "final quantity is not floor(policy x steps)": dict(final_qty=60),
        "the applied multiplier differs from the recorded steps": {},
    }
    for problem, overrides in cases.items():
        multiplier = Decimal("1") if not overrides else Decimal("0.5")
        record = ManagedDecisionRecord(**{**base, **overrides})
        assert problem in narrowing_problems(record, size_multiplier=multiplier), problem
    neutral = ManagedDecisionRecord(**{**base, "mode": "neutral", "plan_id": None, "focus": None,
                                       "neutral_reason": "plan_missing", "day_step": Decimal("0.5")})
    assert "neutral mode scaled the size" in narrowing_problems(neutral, size_multiplier=Decimal("1"))


# --- the latch ----------------------------------------------------------------------------


@pytest.mark.anyio
async def test_flip_crash_restart_gives_the_same_latch(tmp_path, monkeypatch):
    from tests.manager_support import migrated_async

    await migrated_async(tmp_path, monkeypatch)
    morning = _et(SESSION, 9, 55)
    await set_switch("on", source="telegram", actor="owner", reason="/autopilot on", now=morning - timedelta(hours=1))

    first = await ManagedStrategy.open(session_date=SESSION, family="momo", clock=lambda: morning)
    assert first.latch.managed and first.latch.created
    # Flip, then crash: the restarted child reuses the morning's latch.
    await set_switch("off", source="cli", actor="owner", reason="off", now=morning + timedelta(minutes=30))
    restarted = await ManagedStrategy.open(
        session_date=SESSION, family="momo", clock=lambda: morning + timedelta(minutes=31)
    )
    assert restarted.latch.managed and not restarted.latch.created
    assert restarted.latch.latched_at == first.latch.latched_at
    assert restarted.latch.switch_event_id == first.latch.switch_event_id
    # The identity stays; the OFF makes it neutral for the rest of the day.
    view = await restarted.refresh()
    assert (view.mode, view.neutral_reason) == ("neutral", SWITCH_OFF_MIDDAY)
    await set_switch("on", source="cli", actor="owner", reason="on again", now=morning + timedelta(minutes=40))
    assert (await restarted.refresh()).neutral_reason == SWITCH_OFF_MIDDAY
    # The next session latches the switch as it is then.
    tomorrow = date(2026, 10, 14)
    latch = await latch_day_mode(tomorrow, "momo", now=_et(tomorrow, 9, 55))
    assert latch.managed and latch.created
    assert (await read_day_mode(SESSION, "momo")).mode == "managed"


@pytest.mark.anyio
async def test_off_at_the_latch_is_neutral_all_day_even_if_turned_on_later(tmp_path, monkeypatch):
    from tests.manager_support import migrated_async

    await migrated_async(tmp_path, monkeypatch)
    await _store_plans(ALL_ELIGIBLE)
    now = _et(SESSION, 9, 55)
    controller = await ManagedStrategy.open(session_date=SESSION, family="momo", clock=lambda: now)
    assert controller.latch.mode == "v2" and controller.latch.switch_event_id is None
    await set_switch("on", source="cli", actor="owner", reason="late", now=now + timedelta(minutes=5))
    later = await ManagedStrategy.open(session_date=SESSION, family="momo", clock=lambda: now + timedelta(hours=2))
    view = await later.refresh()
    assert later.latch.mode == "v2" and (view.mode, view.neutral_reason) == ("neutral", SWITCH_OFF)
    assert view.mark is None  # the owner's choice, not an outage


@pytest.mark.anyio
async def test_a_fallback_is_logged_once_per_outage(tmp_path, monkeypatch, caplog):
    from tests.manager_support import migrated_async

    await migrated_async(tmp_path, monkeypatch)
    await set_switch("on", source="cli", actor="owner", reason="on", now=_et(SESSION, 9, 0))
    await _store_plans([((10, 30), _plan(ELIGIBLE))])
    clock = {"now": _et(SESSION, 10, 0)}
    controller = await ManagedStrategy.open(session_date=SESSION, family="momo", clock=lambda: clock["now"])

    with caplog.at_level(logging.INFO, logger="autotrader.manager.managed"):
        for minutes in range(0, 180, 5):  # 10:00 .. 12:55
            clock["now"] = _et(SESSION, 10, 0) + timedelta(minutes=minutes)
            await controller.refresh()

    critical = [record.getMessage() for record in caplog.records if record.levelno == logging.CRITICAL]
    # 10:00-10:30 missing, 10:31-11:45 managed, then expired: two outages, two lines.
    assert [line.split(":")[0] for line in critical] == [MANAGER_FALLBACK_NEUTRAL] * 2
    assert "plan_missing" in critical[0] and "plan_expired" in critical[1]
    assert sum("manager recovered" in record.getMessage() for record in caplog.records) == 1


# --- the registry family -----------------------------------------------------------------


def test_managed_momo_keeps_momos_gates_and_v2s_fingerprints_are_unchanged():
    from tests.test_shadow_service import runtime_config
    from trading.operations import shadow_service

    assert shadow_service.strategy_family("momo-ap") == "momo"
    assert shadow_service.strategy_family("momo") == "momo"
    assert shadow_service.strategy_family("orb") == "orb"
    assert shadow_service.MARKET_AWARE_STRATEGIES == {"momo", "momo-ap"}
    assert shadow_service.MANAGED_STRATEGIES == {"momo-ap"}
    assert shadow_service.STRATEGIES["momo-ap"][0] is shadow_service.STRATEGIES["momo"][0]
    assert shadow_service.STRATEGIES["momo-ap"][2] == shadow_service.STRATEGIES["momo"][2] == "momo-review-v2"
    assert shadow_service.STRATEGY_CLIENT_OFFSET["momo-ap"] == 7
    config = runtime_config()
    policy = RiskPolicy()
    momo = shadow_service.policy_fingerprints(config, policy, prompt_id="momo-review-v2", strategy_name="momo")
    # Golden: MOMO_HOD_V2's run fingerprints are exactly the pre-manager set.
    assert set(momo) == {
        "risk", "opening_range_breakout", "execution", "review_prompt", "review_model",
        "runtime_config", "momo_hod", "market_regime_rule",
    }
    assert momo["momo_hod"] == MomoConfig().fingerprint()
    managed = shadow_service.policy_fingerprints(config, policy, prompt_id="momo-review-v2", strategy_name="momo-ap")
    assert {key: managed[key] for key in momo} == momo
    assert set(managed) - set(momo) == {"manager_version", "manager_prompt_id", "managed_review_prompt"}
    assert (managed["manager_version"], managed["manager_prompt_id"]) == ("manager-v1", "day-plan-v1")


def test_the_plans_running_strategies_are_the_scheduled_families_with_a_window_today():
    from trading.manager.planner import running_strategies

    # The supervisor's strategy children: the V2 simulations replace V1.
    assert running_strategies(date(2026, 10, 13)) == ("orb-v2", "momo", "gap-v2", "vwap-v2")
    # Managed MOMO is MOMO's family: it adds no strategy key of its own.
    assert "momo-ap" not in running_strategies(date(2026, 10, 13))
    # A half day keeps every strategy with an entry window.
    assert running_strategies(date(2026, 11, 27)) == ("orb-v2", "momo", "gap-v2", "vwap-v2")


def test_managed_momo_polls_last_clear_of_every_child_including_the_v2_simulations():
    """The V2 simulations take +75/+90/+105 s (the V1 entries keep +30/+45/+60):
    managed MOMO's +120 s stays 15 s from each, inside its 240 s freshness limit."""
    from trading.operations.shadow_service import (
        MIN_POLL_SEPARATION,
        STRATEGY_POLL_OFFSETS,
        poll_offset_problems,
        poll_schedule,
    )

    managed = STRATEGY_POLL_OFFSETS["momo-ap"]
    assert managed >= timedelta(seconds=120)
    planned = {**STRATEGY_POLL_OFFSETS, **{name: timedelta(seconds=seconds) for name, seconds in (
        ("orb-v2", 75), ("vwap-v2", 90), ("gap-v2", 105))}}
    for name, offset in planned.items():
        if name != "momo-ap":
            assert abs(managed - offset) >= MIN_POLL_SEPARATION, name
    assert poll_offset_problems(STRATEGY_POLL_OFFSETS) == []
    schedule = poll_schedule("momo-ap", "watchlist")
    assert schedule.offset == managed and schedule.late_poll_limit == timedelta(seconds=60)

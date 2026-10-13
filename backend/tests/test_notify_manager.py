"""The phone and the AI day manager: real MOMO at once, shadow decisions in a
half-hourly digest, a plan summary after the 09:20 and 09:46 plans, and one
alert per outage. Nothing here sends to Telegram: a fake notifier records."""
from __future__ import annotations

import asyncio
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from tests.test_notify_service import (
    SESSION,
    FakeTelegram,
    FakeTime,
    _add_decision,
    _approved_risk,
    _environment,
    _et,
    _review,
    _seed_run,
    _use_database,
)
from trading.manager.plan import DayPlan, FocusEntry, StrategyStep
from trading.manager.store import ManagedDecisionRecord, PlanRecord, insert_managed_decision, insert_plan
from trading.operations.notify_service import (
    NotifyState,
    SignalDecision,
    _NotifierSession,
    digest_boundary,
    is_real_decision,
)
from trading.operations.paths import UserPaths


class _FakeNotifier:
    def __init__(self) -> None:
        self.texts: list[str] = []

    def notify(self, text: str) -> asyncio.Future[bool]:
        self.texts.append(text)
        future: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        future.set_result(True)
        return future

    async def aclose(self, *, drain_timeout: float = 0) -> None:
        return None


def _session(tmp_path: Path, clock: FakeTime, state: NotifyState | None = None):
    paths = UserPaths(home=tmp_path / "home")
    notifier = _FakeNotifier()
    session = _NotifierSession(
        session_date=SESSION,
        environment=_environment(paths, FakeTelegram(), clock),
        notifier=notifier,  # type: ignore[arg-type]
        state=state or NotifyState(path=tmp_path / "notify.json", session_date=SESSION),
    )
    return session, notifier


async def _cycle(session: _NotifierSession) -> None:
    await session._poll_decisions()
    await session._maybe_send_digest()
    await asyncio.sleep(0)
    session._collect_outcomes()


def test_only_bridge_sendable_names_are_real_decisions():
    def decision(strategy: str) -> SignalDecision:
        return SignalDecision("d", None, strategy, "BF B", "shadow_accepted", {}, None)

    assert is_real_decision(decision("MOMO_HOD_V2"))
    assert not is_real_decision(decision("AP_MOMO_HOD_V1"))
    assert not is_real_decision(decision("ORB_V1"))
    assert [f"{digest_boundary(_et(10, minute)):%H:%M}" for minute in (0, 29, 30, 59)] == [
        "10:00", "10:00", "10:30", "10:30",
    ]


@pytest.mark.anyio
async def test_real_momo_goes_at_once_and_shadow_decisions_wait_for_the_digest(tmp_path, monkeypatch):
    await _use_database(tmp_path, monkeypatch)
    await _seed_run("run-momo", "strategy-momo", "MOMO_HOD_V2")
    await _seed_run("run-ap", "strategy-ap", "AP_MOMO_HOD_V1")
    await _seed_run("run-orb", "strategy-orb", "ORB_V1")
    clock = FakeTime(_et(10, 12))
    await _add_decision("d-momo", run_id="run-momo", strategy_id="strategy-momo", symbol="BF B",
                        outcome="shadow_accepted", created_at=_et(10, 10),
                        review=_review("PROPOSE", "Clean."), risk=_approved_risk())
    await _add_decision("d-ap", run_id="run-ap", strategy_id="strategy-ap", symbol="BF B",
                        outcome="shadow_accepted", created_at=_et(10, 10, 30),
                        review=_review("PROPOSE", "Clean."), risk=_approved_risk())
    await _add_decision("d-orb", run_id="run-orb", strategy_id="strategy-orb", symbol="MMED",
                        outcome="risk_blocked", created_at=_et(10, 11),
                        review=_review("PROPOSE", "Break."),
                        risk={"approved": False, "codes": ["max_open_positions_reached"], "intent": None})
    await insert_managed_decision(ManagedDecisionRecord(
        decision_id="d-ap", run_id="run-ap", candidate_id="cand-d-ap", con_id=1001,
        setup_fingerprint="f" * 64, session_date=SESSION, outcome="shadow_accepted", mode="neutral",
        neutral_reason="plan_expired", mark="manager_fallback_neutral", plan_id=None, eligible=(1001,),
        focus=None, gates={}, day_step=Decimal("1"), setup_step=Decimal("1"), policy_qty=3741,
        final_qty=3741, created_at=_et(10, 10, 30),
    ))
    session, notifier = _session(tmp_path, clock)

    await _cycle(session)
    # 10:12: real MOMO at once; the shadow ones wait for the 10:30 digest...
    # ...but this half hour (10:00) has no digest yet, so it goes now with both.
    assert notifier.texts[0].startswith("🔔 MOMO_HOD_V2 · BF B · WOULD BUY")
    digest = notifier.texts[1]
    assert digest.startswith("🗂 Shadow digest 10:12 ET · 2 decisions (no orders)")
    assert "AP_MOMO_HOD_V1:\n• BF B WOULD BUY 3741 @" in digest
    assert "neutral (plan_expired)" in digest
    assert "ORB_V1:\n• MMED risk blocked (max_open_positions_reached)" in digest
    assert len(notifier.texts) == 2

    # A new shadow decision in the same half hour waits for the next one.
    await _add_decision("d-orb2", run_id="run-orb", strategy_id="strategy-orb", symbol="NEW",
                        outcome="ai_abstained", created_at=_et(10, 20), review=_review("ABSTAIN", "No."))
    clock.now = _et(10, 21)
    await _cycle(session)
    assert len(notifier.texts) == 2
    clock.now = _et(10, 30, 5)
    await _cycle(session)
    assert notifier.texts[2].startswith("🗂 Shadow digest 10:30 ET · 1 decisions")
    assert "• NEW Claude abstained" in notifier.texts[2]

    # A restart with the saved state sends nothing again.
    state = session._state
    again, resent = _session(tmp_path, clock, state)
    clock.now = _et(11, 1)
    await _cycle(again)
    assert resent.texts == []
    assert {key for key in state.delivered if key.startswith("decision:")} == {
        "decision:d-momo", "decision:d-ap", "decision:d-orb", "decision:d-orb2",
    }


def _plan_record(slot: str, created, *, outcome: str = "valid", attempt: int = 1) -> PlanRecord:
    plan = DayPlan(
        regime="range",
        strategies={"momo": StrategyStep(step=Decimal("1"), reason="r"),
                    "vwap": StrategyStep(step=Decimal("0"), reason="r")},
        focus=(FocusEntry(con_id=4931, strategies=("momo",), note="leader"),),
    ) if outcome == "valid" else None
    return PlanRecord(
        plan_id=f"20261013-{slot.replace(':', '')}-{attempt}", session_date=SESSION, slot=slot,
        created_at=created, valid_until=created + timedelta(minutes=75) if plan else None,
        manager_version="manager-v1", prompt_id="day-plan-v1", model="claude-opus-5-5",
        outcome=outcome, plan=plan, candidates=(4931,), gates={},
        context_digest="e" * 64 if plan else None, cost_usd=Decimal("0.11"),
    )


@pytest.mark.anyio
async def test_plan_summaries_follow_the_0920_and_0946_plans_once(tmp_path, monkeypatch):
    await _use_database(tmp_path, monkeypatch)
    clock = FakeTime(_et(9, 21))
    await insert_plan(_plan_record("09:20", _et(9, 20, 40)))
    session, notifier = _session(tmp_path, clock)

    await session._poll_manager()
    await asyncio.sleep(0)
    session._collect_outcomes()
    await session._poll_manager()
    assert len(notifier.texts) == 1
    assert notifier.texts[0].startswith("🧭 Day plan 09:20 ET · regime range")
    assert "Strategies: momo on, vwap OFF" in notifier.texts[0]

    clock.now = _et(9, 47)
    await insert_plan(_plan_record("09:46", _et(9, 46, 50)))
    await insert_plan(_plan_record("10:30", _et(10, 30, 50)))
    await session._poll_manager()
    clock.now = _et(10, 31)
    await session._poll_manager()
    assert [text.split(" · ")[0] for text in notifier.texts] == ["🧭 Day plan 09:20 ET", "🧭 Day plan 09:46 ET"]


@pytest.mark.anyio
async def test_one_alert_per_outage_and_per_budget_exhaustion(tmp_path, monkeypatch):
    await _use_database(tmp_path, monkeypatch)
    clock = FakeTime(_et(9, 24))
    session, notifier = _session(tmp_path, clock)

    await session._poll_manager()
    assert notifier.texts == []  # inside the first slot's grace
    clock.now = _et(9, 26)
    await session._poll_manager()
    await session._poll_manager()
    assert len(notifier.texts) == 1
    assert notifier.texts[0].startswith("⚠️ AI day manager: no plan in force since 09:20 ET (no plan written")

    # A plan arrives: the outage ends silently; when it expires with a failed
    # refresh behind it, a second outage gets its own single alert.
    await insert_plan(_plan_record("09:46", _et(9, 46, 50)))
    clock.now = _et(9, 50)
    await session._poll_manager()
    await insert_plan(_plan_record("10:30", _et(10, 31, 30), outcome="ai_timeout"))
    await insert_plan(_plan_record("10:30", _et(10, 34, 30), outcome="ai_malformed", attempt=2))
    clock.now = _et(11, 2)
    await session._poll_manager()
    await session._poll_manager()
    alerts = [text for text in notifier.texts if text.startswith("⚠️")]
    assert len(alerts) == 2
    assert "no plan in force since 11:01 ET (last attempt 10:30 ET: ai_malformed)" in alerts[1]
    assert "exactly as MOMO_HOD_V2" in alerts[1]

    await insert_plan(_plan_record("11:30", _et(11, 30, 1), outcome="budget_exhausted"))
    clock.now = _et(11, 31)
    await session._poll_manager()
    # Still the 11:01 outage: no second alert for it.
    assert len([text for text in notifier.texts if text.startswith("⚠️")]) == 2


@pytest.mark.anyio
async def test_a_budget_outage_says_the_budget_is_spent(tmp_path, monkeypatch):
    await _use_database(tmp_path, monkeypatch)
    await insert_plan(_plan_record("09:20", _et(9, 20, 1), outcome="budget_exhausted"))
    clock = FakeTime(_et(9, 30))
    session, notifier = _session(tmp_path, clock)

    await session._poll_manager()

    [alert] = notifier.texts
    assert alert.startswith("⚠️ AI day manager: today's $5 plan budget is spent")


@pytest.mark.anyio
async def test_while_migration_16_is_pending_the_manager_parts_are_skipped(tmp_path, monkeypatch):
    from config import cfg
    from tests.schema_support import apply_up_to

    path = await apply_up_to(tmp_path / "behind.db", 15)
    monkeypatch.setattr(cfg, "DB_PATH", str(path))
    clock = FakeTime(_et(11, 0))
    session, notifier = _session(tmp_path, clock)

    await session._poll_manager()

    assert notifier.texts == []

"""GATING tests: the safety stage of the order-proposal chain must reject.

These tests guard the live flip. They drive a proposed order all the way
through ``order_proposal.place_proposed_order`` and prove that when the runtime
safety kernel's hard kill switches are tripped, the proposal is rejected at the
``safety_gate`` stage and the broker is NEVER touched.

Honesty / mocking level
------------------------
We do NOT stub out the thing under test. The real chain runs:

    order_proposal.place_proposed_order
      -> services.safety_gate.evaluate_runtime_safety   (real)
        -> safety_kernel.check_all                       (real)
          -> assert_not_killed / assert_daily_loss_not_locked  (real)
            -> ai_guardrails._load_guardrails_from_db    (lowest honest seam)

The only things patched are:
  * ``ai_guardrails._load_guardrails_from_db`` — the DB seam the real asserts
    read. We set the actual guardrail flags (emergency_stop / autopilot_mode OFF
    / daily_loss_locked) a tripped kill switch would have in the DB.
  * ``risk_manager.check_trade_risk`` / ``check_portfolio_impact`` — earlier,
    unrelated pre-gates. The proposal feeds them an account-value *floor* of 1.0
    (see ``order_proposal._account_value_floor``) which would otherwise block on
    position size before we ever reach the safety stage we are testing. We force
    them to PASS so the safety stage is what decides.
  * ``order_executor.place_order`` — replaced with a spy that FAILS the test if
    it is ever called. This is the load-bearing assertion: a tripped kill switch
    must never reach the broker.

A second pair of tests patches ``safety_kernel.check_all`` to raise the real
``SafetyViolation`` (the other honest seam the brief allows), proving the
rejection mapping at the gate is correct regardless of which underlying assert
fires.
"""
from __future__ import annotations

import pytest

import order_proposal
from models import Rule, TradeAction
from safety_kernel import SafetyViolation


# ── helpers ──────────────────────────────────────────────────────────────────

def _rule(symbol="AAPL", action="BUY", qty=10, limit=150.0) -> Rule:
    """A normal long entry. Not an exit, so the autopilot-authority kill
    switches (assert_not_killed / assert_daily_loss_not_locked) are armed.
    """
    return Rule(
        name="test-proposal-safety",
        symbol=symbol,
        enabled=False,
        conditions=[],
        action=TradeAction(
            type=action, asset_type="STK", quantity=qty,
            order_type="LMT", limit_price=limit,
        ),
        status="paper",
    )


class _Risk:
    def __init__(self, status="PASS", reasons=("ok",)):
        self.status = status
        self.reasons = list(reasons)


class _Impact:
    def __init__(self, allowed=True, reason="pass", details=None):
        self.allowed = allowed
        self.reason = reason
        self.details = details


class _GuardrailStub:
    """Stand-in for GuardrailConfigResponse holding only the flags the real
    kill-switch asserts read. Defaults are the SAFE (not-tripped) state so each
    test trips exactly one switch and nothing else accidentally blocks.
    """

    def __init__(
        self,
        *,
        autopilot_mode="LIVE",
        emergency_stop=False,
        daily_loss_locked=False,
    ):
        self.autopilot_mode = autopilot_mode
        self.emergency_stop = emergency_stop
        self.daily_loss_locked = daily_loss_locked


def _patch_pre_gates_and_spy_place_order(monkeypatch):
    """Force the two pre-safety gates to PASS and install a place_order spy.

    Returns the spy dict whose ``called`` flag MUST stay False on every reject
    path. The fake raises if invoked so the failure is loud even if an assertion
    is forgotten.
    """
    monkeypatch.setattr(
        order_proposal.risk_manager, "check_trade_risk",
        lambda *a, **kw: _Risk("PASS", ["ok"]),
    )
    monkeypatch.setattr(
        order_proposal.risk_manager, "check_portfolio_impact",
        lambda *a, **kw: _Impact(True),
    )

    spy = {"called": False, "calls": []}

    async def fake_place_order(rule, *, source="rule", skip_safety=False, **kw):
        spy["called"] = True
        spy["calls"].append({"source": source, "skip_safety": skip_safety})
        raise AssertionError(
            "order_executor.place_order was called on a safety-rejected "
            "proposal — the kill switch was bypassed."
        )

    monkeypatch.setattr(order_proposal.order_executor, "place_order", fake_place_order)
    return spy


# ── (1) kill switch tripped — real chain, DB seam ─────────────────────────────

async def test_emergency_stop_rejects_at_safety_and_never_places_order(monkeypatch):
    """Kill switch active (emergency_stop=True) => rejected at safety_gate, and
    order_executor.place_order is NEVER called.
    """
    spy = _patch_pre_gates_and_spy_place_order(monkeypatch)

    async def fake_load(*a, **kw):
        return _GuardrailStub(autopilot_mode="LIVE", emergency_stop=True)

    # Patch the DB seam the REAL assert_not_killed reads.
    monkeypatch.setattr("ai_guardrails._load_guardrails_from_db", fake_load)

    result = await order_proposal.place_proposed_order(
        _rule(), source="claude_worker", user_id="demo",
    )

    assert result.status == "rejected"
    assert result.stage == "safety_gate"
    assert "Kill switch" in result.reason
    # Load-bearing GATING assertion: the broker was never reached.
    assert spy["called"] is False
    assert spy["calls"] == []
    # The safety stage recorded the block in the snapshot.
    assert result.risk_snapshot["safety_gate"]["allowed"] is False


async def test_autopilot_off_rejects_at_safety_and_never_places_order(monkeypatch):
    """Autopilot mode OFF is also a tripped kill switch => rejected at
    safety_gate, broker never reached.
    """
    spy = _patch_pre_gates_and_spy_place_order(monkeypatch)

    async def fake_load(*a, **kw):
        return _GuardrailStub(autopilot_mode="OFF", emergency_stop=False)

    monkeypatch.setattr("ai_guardrails._load_guardrails_from_db", fake_load)

    result = await order_proposal.place_proposed_order(
        _rule(), source="claude_worker",
    )

    assert result.status == "rejected"
    assert result.stage == "safety_gate"
    assert "Autopilot is OFF" in result.reason
    assert spy["called"] is False
    assert spy["calls"] == []


# ── (2) daily-loss lock tripped — real chain, DB seam ─────────────────────────

async def test_daily_loss_lock_rejects_at_safety_and_never_places_order(monkeypatch):
    """Daily loss lock active (kill switches otherwise clear) => rejected at
    safety_gate, broker never reached.

    assert_not_killed passes (mode LIVE, no emergency stop) so execution reaches
    assert_daily_loss_not_locked, which is the assert under test here.
    """
    spy = _patch_pre_gates_and_spy_place_order(monkeypatch)

    async def fake_load(*a, **kw):
        return _GuardrailStub(
            autopilot_mode="LIVE",
            emergency_stop=False,
            daily_loss_locked=True,
        )

    monkeypatch.setattr("ai_guardrails._load_guardrails_from_db", fake_load)

    result = await order_proposal.place_proposed_order(
        _rule(), source="claude_worker",
    )

    assert result.status == "rejected"
    assert result.stage == "safety_gate"
    assert "Daily loss lock" in result.reason
    assert spy["called"] is False
    assert spy["calls"] == []
    assert result.risk_snapshot["safety_gate"]["allowed"] is False


# ── second honest seam: real SafetyViolation raised from check_all ────────────
# Proves the gate -> ProposalResult mapping regardless of which underlying
# assert fires, without depending on the guardrails DB shape.

async def test_check_all_killswitch_violation_maps_to_rejected(monkeypatch):
    spy = _patch_pre_gates_and_spy_place_order(monkeypatch)

    async def boom(*a, **kw):
        raise SafetyViolation("Kill switch active - all new AI entries blocked")

    # Patch the real kernel entrypoint the real safety_gate awaits.
    monkeypatch.setattr(order_proposal.safety_gate, "check_all", boom)

    result = await order_proposal.place_proposed_order(_rule(), source="claude_worker")

    assert result.status == "rejected"
    assert result.stage == "safety_gate"
    assert "Kill switch active" in result.reason
    assert spy["called"] is False


async def test_check_all_daily_loss_violation_maps_to_rejected(monkeypatch):
    spy = _patch_pre_gates_and_spy_place_order(monkeypatch)

    async def boom(*a, **kw):
        raise SafetyViolation("Daily loss lock active - new AI entries blocked")

    monkeypatch.setattr(order_proposal.safety_gate, "check_all", boom)

    result = await order_proposal.place_proposed_order(_rule(), source="claude_worker")

    assert result.status == "rejected"
    assert result.stage == "safety_gate"
    assert "Daily loss lock active" in result.reason
    assert spy["called"] is False


# ── negative control: when the kernel does NOT raise, the gate passes ─────────
# Guards against a false-positive suite where everything is "rejected" no matter
# what. The real ``safety_gate.evaluate_runtime_safety`` wrapper runs; we make
# the real kernel entrypoint ``check_all`` a no-op (it raises nothing) so the
# gate must report allowed and the chain must advance to place_order.
#
# We patch check_all (rather than feeding clear guardrail flags) because the
# proposal hands the kernel an account-value *floor* of 1.0, which the unrelated
# assert_risk_budget would reject regardless of the kill switches — that pre-floor
# is out of scope for these kill-switch GATING tests. Patching the kernel to pass
# isolates exactly the "no violation => not rejected at safety" property.

async def test_no_violation_passes_safety_and_reaches_place_order(monkeypatch):
    # Pre-gates PASS, but DO NOT use the raising spy — we WANT place_order here.
    monkeypatch.setattr(
        order_proposal.risk_manager, "check_trade_risk",
        lambda *a, **kw: _Risk("PASS", ["ok"]),
    )
    monkeypatch.setattr(
        order_proposal.risk_manager, "check_portfolio_impact",
        lambda *a, **kw: _Impact(True),
    )

    async def no_violation(*a, **kw):
        return None  # kernel passes — no SafetyViolation raised

    monkeypatch.setattr(order_proposal.safety_gate, "check_all", no_violation)

    reached = {"place_order": False}

    class _Trade:
        order_id = 7777

    async def fake_place_order(rule, *, source="rule", skip_safety=False, **kw):
        reached["place_order"] = True
        # The chain must never bypass the in-executor safety re-run.
        assert skip_safety is False
        return _Trade()

    monkeypatch.setattr(order_proposal.order_executor, "place_order", fake_place_order)

    result = await order_proposal.place_proposed_order(_rule(), source="claude_worker")

    # Safety did NOT block — the chain advanced past the gate to the broker.
    assert reached["place_order"] is True
    assert result.stage == "place_order"
    assert result.status == "approved"
    assert result.risk_snapshot["safety_gate"]["allowed"] is True

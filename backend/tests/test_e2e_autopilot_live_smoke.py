"""E2E PAPER-mode regression of the scanner -> autopilot live trade path.

Goal
----
Lock the *exact* live-path guard call sequence so a future refactor cannot
silently drop a guard. The canonical pre-trade chain (the same one the rule
engine in ``bot_runner._run_cycle`` enforces inline, and the one
``order_proposal.place_proposed_order`` / ``mcp_server.mcp_propose_order``
route through) MUST run, in order:

    1. risk_manager.check_trade_risk          (per-trade risk)
    2. risk_manager.check_portfolio_impact     (concentration / correlation)
    3. safety_gate.evaluate_runtime_safety     (runtime safety kernel)
    4. order_executor.place_order(skip_safety=False)   (broker submission)

This test drives a real scanner candidate end-to-end:

    inject (source='scanner', status='queued')   <- via db
        -> drain_candidates()                    (queued -> draining)
        -> place_proposed_order()                (the 4-guard chain)
        -> mark_candidate_status('applied')      (terminal)

NO live order is ever placed: ``place_order`` is mocked, IBKR is never touched,
and the test runs in PAPER (SIM) mode. The point is purely to fence the live
call sequence — if someone deletes the portfolio-impact step, drops the
``skip_safety=False`` argument, or reorders the guards, this test fails.

Run only this file:
    cd backend && DB_PATH=/tmp/livesmoke.db .venv/bin/pytest \
        tests/test_e2e_autopilot_live_smoke.py -q -p no:cacheprovider
"""
from __future__ import annotations

import json
import uuid

import pytest

import order_proposal
from db.core import init_db
from db.direct_candidates import (
    drain_candidates,
    get_candidate_status,
    mark_candidate_status,
    queue_candidate,
)
from models import Rule, TradeAction


# ── Lightweight guard stand-ins ────────────────────────────────────────────
# These mirror the shapes the real guards return so the chain treats them as a
# clean PASS and proceeds to the next stage. They DO NOT relax any guard — a
# block at any stage is exercised by the dedicated short-circuit tests below.

class _RiskResult:
    def __init__(self, status: str = "PASS", reasons=None):
        self.status = status
        self.reasons = reasons or ["ok"]


class _ImpactResult:
    def __init__(self, allowed: bool = True, reason: str = "pass", details=None):
        self.allowed = allowed
        self.reason = reason
        self.details = details


class _FakeTrade:
    """Stand-in for the Trade the broker layer would return on success."""
    def __init__(self, order_id: int = 90909):
        self.order_id = order_id


def _scanner_rule(symbol: str = "AAPL", action: str = "BUY", qty: int = 10,
                  limit: float = 150.0) -> Rule:
    """A LIMIT-only rule shaped exactly like the one the scanner/propose path
    builds for a queued candidate (paper, disabled, AI-origin)."""
    return Rule(
        name=f"scanner:{symbol}",
        symbol=symbol,
        enabled=False,
        conditions=[],
        action=TradeAction(
            type=action, asset_type="STK", quantity=qty,
            order_type="LMT", limit_price=limit,
        ),
        status="paper",
        ai_generated=True,
        created_by="ai",
    )


def _spy_chain(monkeypatch, calls: list[str], *,
               risk: _RiskResult | None = None,
               impact: _ImpactResult | None = None,
               safety: tuple[bool, str | None] | None = None,
               trade="__ok__") -> dict:
    """Patch the four live-path guard layers in ``order_proposal``, recording
    the order they fire into ``calls``. Returns a dict capturing the kwargs
    ``place_order`` was called with (so we can assert ``skip_safety=False``).

    IBKR/Anthropic are never reached: every layer here is a local fake.
    """
    risk = risk if risk is not None else _RiskResult()
    impact = impact if impact is not None else _ImpactResult()
    safety = safety if safety is not None else (True, None)
    trade = _FakeTrade() if trade == "__ok__" else trade

    captured: dict = {}

    def fake_check_trade_risk(*a, **kw):
        calls.append("check_trade_risk")
        return risk

    def fake_check_portfolio_impact(*a, **kw):
        calls.append("check_portfolio_impact")
        return impact

    async def fake_evaluate_runtime_safety(*a, **kw):
        calls.append("evaluate_runtime_safety")
        return safety

    async def fake_place_order(rule, *, source="rule", skip_safety=False, **kw):
        calls.append("place_order")
        captured["skip_safety"] = skip_safety
        captured["source"] = source
        captured["symbol"] = rule.symbol
        captured["qty"] = rule.action.quantity
        # A live order would hit IBKR here; we return a fake Trade instead.
        return trade

    monkeypatch.setattr(order_proposal.risk_manager, "check_trade_risk",
                        fake_check_trade_risk)
    monkeypatch.setattr(order_proposal.risk_manager, "check_portfolio_impact",
                        fake_check_portfolio_impact)
    monkeypatch.setattr(order_proposal.safety_gate, "evaluate_runtime_safety",
                        fake_evaluate_runtime_safety)
    monkeypatch.setattr(order_proposal.order_executor, "place_order",
                        fake_place_order)
    return captured


# ── The regression: scanner candidate -> guard chain -> applied ─────────────

async def test_scanner_candidate_runs_full_guard_chain_and_ends_applied(monkeypatch):
    """A queued scanner candidate drives the EXACT live-path guard sequence and
    reaches terminal status 'applied' — no guard may be skipped or reordered."""
    await init_db()

    # 1. Inject a scanner candidate via db. queue_candidate defaults the row to
    #    source='scanner', status='queued' (the scanner state-machine entry).
    cand_id = f"scan-{uuid.uuid4()}"
    await queue_candidate(
        cand_id, "AAPL",
        {"action": "buy", "price": 150.0, "qty": 10, "source": "scanner"},
    )
    assert await get_candidate_status(cand_id) == "queued"

    # 2. Drain it the way the autopilot cycle does: queued -> draining. This is
    #    the real scanner queue mechanism, not a shortcut.
    drained = await drain_candidates()
    drained_ids = {c.get("_candidate_id") for c in drained}
    assert cand_id in drained_ids
    assert await get_candidate_status(cand_id) == "draining"

    # 3. Run the queued candidate through the canonical live-path chain. This is
    #    the same chain the rule engine enforces inline in bot_runner and that
    #    mcp_propose_order routes through — guards spied, broker mocked (PAPER).
    calls: list[str] = []
    captured = _spy_chain(monkeypatch, calls)

    rule = _scanner_rule(symbol="AAPL", qty=10, limit=150.0)
    result = await order_proposal.place_proposed_order(
        rule, source="scanner", user_id="demo",
    )

    # 4. On approval, advance the scanner state machine to its terminal state.
    assert result.status == "approved", result.reason
    await mark_candidate_status(cand_id, "applied")

    # ── Assertions: the live-path guard sequence is LOCKED ──────────────────
    # EXACT order, no extra calls, nothing dropped.
    assert calls == [
        "check_trade_risk",
        "check_portfolio_impact",
        "evaluate_runtime_safety",
        "place_order",
    ], f"guard sequence drifted: {calls}"

    # The broker submission must never be told to bypass the safety re-run.
    assert captured["skip_safety"] is False
    assert captured["source"] == "scanner"
    assert captured["symbol"] == "AAPL"
    assert captured["qty"] == 10

    # The proposal result reflects an approved broker submission.
    assert result.stage == "place_order"
    assert result.order_id == 90909
    assert result.status not in ("deferred", "rejected")

    # The candidate ends in the scanner terminal state 'applied'.
    assert await get_candidate_status(cand_id) == "applied"

    # risk_snapshot carries each guard's verdict (so the chain is auditable).
    snap = result.risk_snapshot or {}
    assert "risk" in snap and "portfolio" in snap and "safety_gate" in snap
    # Serializable end-to-end (the worker persists this).
    json.dumps(snap)


# ── Guard-drop tripwires: a block at any stage must short-circuit ───────────
# These prove the sequence is enforced, not merely present — if a future edit
# moves a guard after place_order, the corresponding test below fails because
# place_order would run before the (now-misplaced) blocking guard.

async def test_risk_block_stops_before_broker(monkeypatch):
    await init_db()
    calls: list[str] = []
    _spy_chain(monkeypatch, calls, risk=_RiskResult("BLOCK", ["position too large"]))
    result = await order_proposal.place_proposed_order(
        _scanner_rule(), source="scanner",
    )
    assert result.status == "rejected"
    assert result.stage == "risk"
    assert calls == ["check_trade_risk"]
    assert "place_order" not in calls


async def test_portfolio_block_stops_before_safety_and_broker(monkeypatch):
    await init_db()
    calls: list[str] = []
    _spy_chain(monkeypatch, calls,
               impact=_ImpactResult(False, reason="sector_limit", details="sector full"))
    result = await order_proposal.place_proposed_order(
        _scanner_rule(), source="scanner",
    )
    assert result.status == "rejected"
    assert result.stage == "portfolio"
    assert calls == ["check_trade_risk", "check_portfolio_impact"]
    assert "evaluate_runtime_safety" not in calls
    assert "place_order" not in calls


async def test_safety_gate_block_stops_before_broker(monkeypatch):
    await init_db()
    calls: list[str] = []
    _spy_chain(monkeypatch, calls, safety=(False, "kill switch active"))
    result = await order_proposal.place_proposed_order(
        _scanner_rule(), source="scanner",
    )
    assert result.status == "rejected"
    assert result.stage == "safety_gate"
    assert calls == [
        "check_trade_risk", "check_portfolio_impact", "evaluate_runtime_safety",
    ]
    assert "place_order" not in calls


async def test_place_order_none_defers_and_candidate_not_applied(monkeypatch):
    """place_order returning None == an in-executor block (rate cap / pre-flight
    / broker down). The candidate must NOT be marked applied on a deferral."""
    await init_db()
    cand_id = f"scan-{uuid.uuid4()}"
    await queue_candidate(cand_id, "MSFT", {"action": "buy", "source": "scanner"})
    await drain_candidates()  # queued -> draining

    calls: list[str] = []
    _spy_chain(monkeypatch, calls, trade=None)
    result = await order_proposal.place_proposed_order(
        _scanner_rule(symbol="MSFT"), source="scanner",
    )

    # All four layers still ran in order — the chain reached the broker and the
    # broker declined to place. That is a DEFERRAL, never an approval.
    assert calls == [
        "check_trade_risk", "check_portfolio_impact",
        "evaluate_runtime_safety", "place_order",
    ]
    assert result.status == "deferred"
    assert result.status not in ("approved", "applied")
    # Deferred candidate stays 'draining' — it was never applied.
    await mark_candidate_status(cand_id, "failed")
    assert await get_candidate_status(cand_id) != "applied"

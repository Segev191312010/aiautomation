"""Tests for the shared order-proposal chain (order_proposal.place_proposed_order).

Mocks the four chain layers and asserts:
  * the chain runs in the exact order risk -> portfolio -> safety_gate -> place_order
  * a block at any gate short-circuits and never reaches place_order
  * the approved path calls place_order(skip_safety=False)
  * place_order returning None -> status 'deferred' (never approved/applied)
  * ProposalResult is JSON-serializable
"""
from __future__ import annotations

import json
from dataclasses import asdict

import pytest

import order_proposal
from models import Rule, TradeAction


def _rule(symbol="AAPL", action="BUY", qty=10, limit=150.0) -> Rule:
    return Rule(
        name="test-proposal",
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
    def __init__(self, status, reasons):
        self.status = status
        self.reasons = reasons


class _Impact:
    def __init__(self, allowed, reason="pass", details=None):
        self.allowed = allowed
        self.reason = reason
        self.details = details


class _Trade:
    def __init__(self, order_id=4242):
        self.order_id = order_id


def _patch_chain(monkeypatch, calls, *, risk=None, impact=None, safety=None, trade="__sentinel__"):
    """Patch all four chain layers, recording call order into ``calls``."""
    risk = risk if risk is not None else _Risk("PASS", ["ok"])
    impact = impact if impact is not None else _Impact(True)
    safety = safety if safety is not None else (True, None)
    trade = _Trade() if trade == "__sentinel__" else trade

    def fake_check_trade_risk(*a, **kw):
        calls.append("risk")
        return risk

    def fake_check_portfolio_impact(*a, **kw):
        calls.append("portfolio")
        return impact

    async def fake_evaluate_runtime_safety(*a, **kw):
        calls.append("safety_gate")
        return safety

    captured = {}

    async def fake_place_order(rule, *, source="rule", skip_safety=False, **kw):
        calls.append("place_order")
        captured["skip_safety"] = skip_safety
        captured["source"] = source
        return trade

    monkeypatch.setattr(order_proposal.risk_manager, "check_trade_risk", fake_check_trade_risk)
    monkeypatch.setattr(order_proposal.risk_manager, "check_portfolio_impact", fake_check_portfolio_impact)
    monkeypatch.setattr(order_proposal.safety_gate, "evaluate_runtime_safety", fake_evaluate_runtime_safety)
    monkeypatch.setattr(order_proposal.order_executor, "place_order", fake_place_order)
    return captured


async def test_chain_order_on_approved(monkeypatch):
    calls: list[str] = []
    captured = _patch_chain(monkeypatch, calls)
    result = await order_proposal.place_proposed_order(_rule(), source="claude_worker", user_id="demo")
    assert calls == ["risk", "portfolio", "safety_gate", "place_order"]
    assert result.status == "approved"
    assert result.stage == "place_order"
    assert result.order_id == 4242
    # Approved path must call place_order with skip_safety=False (no bypass).
    assert captured["skip_safety"] is False
    assert captured["source"] == "claude_worker"


async def test_risk_block_short_circuits(monkeypatch):
    calls: list[str] = []
    _patch_chain(monkeypatch, calls, risk=_Risk("BLOCK", ["position too large"]))
    result = await order_proposal.place_proposed_order(_rule(), source="claude_worker")
    assert result.status == "rejected"
    assert result.stage == "risk"
    assert "position too large" in result.reason
    # Nothing past risk runs.
    assert calls == ["risk"]
    assert "place_order" not in calls


async def test_portfolio_block_short_circuits(monkeypatch):
    calls: list[str] = []
    _patch_chain(monkeypatch, calls, impact=_Impact(False, reason="sector_limit", details="sector full"))
    result = await order_proposal.place_proposed_order(_rule(), source="claude_worker")
    assert result.status == "rejected"
    assert result.stage == "portfolio"
    assert result.reason == "sector full"
    assert calls == ["risk", "portfolio"]
    assert "safety_gate" not in calls
    assert "place_order" not in calls


async def test_safety_gate_block_short_circuits(monkeypatch):
    calls: list[str] = []
    _patch_chain(monkeypatch, calls, safety=(False, "kill switch active"))
    result = await order_proposal.place_proposed_order(_rule(), source="claude_worker")
    assert result.status == "rejected"
    assert result.stage == "safety_gate"
    assert result.reason == "kill switch active"
    assert calls == ["risk", "portfolio", "safety_gate"]
    assert "place_order" not in calls


async def test_place_order_none_is_deferred(monkeypatch):
    calls: list[str] = []
    _patch_chain(monkeypatch, calls, trade=None)
    result = await order_proposal.place_proposed_order(_rule(), source="claude_worker")
    # place_order returning None == per-symbol rate cap / pre-flight / broker down.
    assert result.status == "deferred"
    assert result.stage == "place_order"
    assert result.status not in ("approved", "applied")
    assert calls == ["risk", "portfolio", "safety_gate", "place_order"]


async def test_result_is_serializable(monkeypatch):
    calls: list[str] = []
    _patch_chain(monkeypatch, calls)
    result = await order_proposal.place_proposed_order(_rule(), source="claude_worker")
    encoded = json.dumps(asdict(result))
    decoded = json.loads(encoded)
    assert decoded["status"] == "approved"
    assert decoded["order_id"] == 4242
    assert "risk_snapshot" in decoded

"""Tests for the MCP tool surface (mcp_server) + prompt versioning.

  * read tools return JSON-serializable dicts
  * mcp_propose_order -> rejection on a mocked safety fail, approval on green
  * mcp_mark_declined transitions a candidate to 'declined_by_ai'
  * tools_spec is a valid JSON-schema tool list
  * get_prompt_version() is stable within a session
"""
from __future__ import annotations

import json
import os

import pytest

import mcp_server
from claude_prompts import SYSTEM_PROMPT, get_prompt_version
from order_proposal import ProposalResult


@pytest.fixture
async def db(tmp_path):
    """Isolated SQLite DB for the DB-backed tools."""
    from config import cfg
    from db.core import init_db

    db_path = str(tmp_path / "mcp_tools.db")
    if os.path.exists(db_path):
        os.unlink(db_path)
    cfg.DB_PATH = db_path
    await init_db()
    yield


# ── tools_spec / prompt_version ───────────────────────────────────────────────

def test_tools_spec_is_valid_json_schema():
    # Whole spec must round-trip through JSON.
    encoded = json.dumps(mcp_server.tools_spec)
    assert json.loads(encoded) == mcp_server.tools_spec
    names = {t["name"] for t in mcp_server.tools_spec}
    assert {
        "get_signal", "get_positions", "get_account", "get_recent_signals",
        "get_market_data", "propose_order", "mark_declined",
    } <= names
    for tool in mcp_server.tools_spec:
        assert isinstance(tool["name"], str) and tool["name"]
        assert isinstance(tool["description"], str) and tool["description"]
        schema = tool["input_schema"]
        assert schema["type"] == "object"
        assert isinstance(schema["properties"], dict)
        for req in schema.get("required", []):
            assert req in schema["properties"], f"{tool['name']}: required {req} not in properties"


def test_prompt_version_stable_within_session():
    v1 = get_prompt_version()
    v2 = get_prompt_version()
    assert v1 == v2
    assert isinstance(v1, str) and len(v1) == 64  # sha256 hex
    # Sanity: it actually depends on both prompt and tools_spec.
    import hashlib
    expected = hashlib.sha256(
        (SYSTEM_PROMPT + json.dumps(mcp_server.tools_spec, sort_keys=True)).encode("utf-8")
    ).hexdigest()
    assert v1 == expected


def test_system_prompt_contracts_present():
    p = SYSTEM_PROMPT
    assert "PAPER" in p
    assert "LIMIT" in p
    assert "09:30" in p and "16:00" in p
    assert "decline" in p.lower()
    assert "retry" in p.lower()


# ── read tools serializable ────────────────────────────────────────────────────

async def test_read_tools_json_serializable(monkeypatch):
    # market_data: patch the price fetch so no IBKR/network is touched.
    import market_data
    async def fake_price(symbol):
        return 123.45
    monkeypatch.setattr(market_data, "get_latest_price", fake_price)

    # positions/account: force the disconnected branch (no IBKR in tests).
    from ibkr_client import ibkr
    monkeypatch.setattr(ibkr, "is_connected", lambda: False)

    md = await mcp_server.mcp_get_market_data("aapl")
    pos = await mcp_server.mcp_get_positions()
    acct = await mcp_server.mcp_get_account()

    for payload in (md, pos, acct):
        assert json.loads(json.dumps(payload)) == payload

    assert md == {"symbol": "AAPL", "price": 123.45, "available": True}
    assert pos == {"connected": False, "positions": []}
    assert acct == {"connected": False}


async def test_get_signal_and_recent(db):
    from db.direct_candidates import queue_candidate

    await queue_candidate("sig-1", "AAPL", {"action": "buy", "limit": 150}, user_id="demo")
    got = await mcp_server.mcp_get_signal("sig-1")
    assert got["found"] is True
    assert got["symbol"] == "AAPL"
    assert got["payload"]["action"] == "buy"
    assert json.loads(json.dumps(got)) == got

    missing = await mcp_server.mcp_get_signal("does-not-exist")
    assert missing["found"] is False

    recent = await mcp_server.mcp_get_recent_signals(limit=5)
    assert recent["count"] >= 1
    assert any(s["signal_id"] == "sig-1" for s in recent["signals"])
    assert json.loads(json.dumps(recent)) == recent


# ── propose_order: reject vs approve ───────────────────────────────────────────

async def test_propose_order_rejected_on_safety_fail(monkeypatch):
    async def fake_place(rule, source, user_id="demo"):
        return ProposalResult(
            status="rejected", stage="safety_gate",
            reason="kill switch active", risk_snapshot={"safety_gate": {"allowed": False}},
        )
    monkeypatch.setattr(mcp_server, "place_proposed_order", fake_place)

    out = await mcp_server.mcp_propose_order(
        symbol="AAPL", action="BUY", qty=10, limit_price=150.0, signal_id="sig-x",
    )
    assert out["status"] == "rejected"
    assert out["stage"] == "safety_gate"
    assert out["signal_id"] == "sig-x"
    assert json.loads(json.dumps(out)) == out


async def test_propose_order_approved_on_green(monkeypatch):
    captured = {}
    async def fake_place(rule, source, user_id="demo"):
        # Confirm a LIMIT-only rule was built and routed through the shared chain.
        captured["rule"] = rule
        captured["source"] = source
        return ProposalResult(status="approved", stage="place_order", reason="ok", order_id=99)
    monkeypatch.setattr(mcp_server, "place_proposed_order", fake_place)

    out = await mcp_server.mcp_propose_order(
        symbol="msft", action="BUY", qty=5, limit_price=400.0, signal_id="sig-y",
    )
    assert out["status"] == "approved"
    assert out["order_id"] == 99
    assert captured["source"] == "claude_worker"
    rule = captured["rule"]
    assert rule.symbol == "MSFT"
    assert rule.action.order_type == "LMT"
    assert rule.action.limit_price == 400.0
    assert rule.action.quantity == 5


async def test_dispatch_propose_order(monkeypatch):
    async def fake_place(rule, source, user_id="demo"):
        return ProposalResult(status="approved", stage="place_order", reason="ok", order_id=7)
    monkeypatch.setattr(mcp_server, "place_proposed_order", fake_place)

    out = await mcp_server.dispatch_tool_call({
        "name": "propose_order",
        "input": {"symbol": "AAPL", "action": "BUY", "qty": 1,
                  "limit_price": 10.0, "signal_id": "sig-z"},
    })
    assert out["status"] == "approved"
    assert out["order_id"] == 7


async def test_dispatch_unknown_tool():
    out = await mcp_server.dispatch_tool_call({"name": "nope", "input": {}})
    assert "error" in out
    assert "unknown tool" in out["error"]


# ── mark_declined ──────────────────────────────────────────────────────────────

async def test_mark_declined_transitions(db):
    from db.direct_candidates import queue_candidate, get_candidate_status

    await queue_candidate("sig-decline", "NVDA", {"action": "buy"}, user_id="demo")
    out = await mcp_server.mcp_mark_declined("sig-decline", "weak setup")
    assert out["status"] == "declined_by_ai"
    assert out["signal_id"] == "sig-decline"
    assert await get_candidate_status("sig-decline") == "declined_by_ai"


async def test_dispatch_mark_declined(db):
    from db.direct_candidates import queue_candidate, get_candidate_status

    await queue_candidate("sig-d2", "TSLA", {"action": "sell"}, user_id="demo")
    out = await mcp_server.dispatch_tool_call({
        "name": "mark_declined",
        "input": {"signal_id": "sig-d2", "reason": "no edge"},
    })
    assert out["status"] == "declined_by_ai"
    assert await get_candidate_status("sig-d2") == "declined_by_ai"

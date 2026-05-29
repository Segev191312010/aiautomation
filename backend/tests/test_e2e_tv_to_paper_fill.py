"""End-to-end: TradingView webhook -> Claude worker review -> paper fill.

Exercises the full live path with the broker (IBKR) and the model (Anthropic)
mocked, never hitting a real endpoint:

  1. POST /api/webhook/tv with a valid, fresh, secret-bearing payload on a
     minimal app that mounts only ``routers.webhook_routes`` -> asserts a
     ``direct_candidates`` row (source='tv_webhook', status='pending_review')
     and the matching ``tv_idempotency`` row are persisted.
  2. Drives ``claude_worker.process_candidate`` on that claimed row with a
     scripted Anthropic response that invokes the ``propose_order`` tool. The
     tool call flows through the REAL ``mcp_propose_order`` -> the REAL
     ``order_proposal.place_proposed_order`` chain (gates mocked to allow),
     down to ``order_executor.place_order``. Asserts:
       * ``place_proposed_order`` was actually invoked (the propose_order tool
         routes through the shared pre-trade chain, never around it),
       * the executor was called with ``skip_safety=False`` (defense-in-depth,
         never a bypass),
       * the candidate status flips to ``applied``.
  3. Asserts a cost/decision row was recorded in ``ai_decision_runs`` for the
     review (source='tv_webhook', mode='paper', with token usage persisted).

The (sibling-owned) ``mcp_server`` is reached through a thin shim that adapts
the worker's calling convention (``tools_spec()`` / ``dispatch_tool_call(name,
input)``) onto the real module's surface, so the propose_order path is genuinely
the real one rather than a stand-in.
"""
from __future__ import annotations

import json
import sys
import types
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import FastAPI

import claude_worker
import mcp_server
import order_executor
import order_proposal
import risk_manager
from config import cfg
from db.core import get_db, init_db
from models import Trade
from routers import webhook_routes
from services import safety_gate

TV_SECRET = "tv-shared-secret-123456"


# ── App + DB fixtures ───────────────────────────────────────────────────────

@pytest.fixture
async def app() -> FastAPI:
    """Minimal app mounting ONLY the public TV webhook router."""
    await init_db()
    async with get_db() as db:
        await db.execute("DELETE FROM tv_idempotency")
        await db.execute("DELETE FROM direct_candidates")
        await db.execute("DELETE FROM ai_decision_runs")
        await db.commit()
    a = FastAPI()
    a.include_router(webhook_routes.router)
    return a


@pytest.fixture
async def client(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


# ── Anthropic + mcp_server fakes ────────────────────────────────────────────

def _text_block(text: str) -> SimpleNamespace:
    return SimpleNamespace(
        type="text", text=text,
        model_dump=lambda **_: {"type": "text", "text": text},
    )


def _tool_use_block(name: str, tool_input: dict, block_id: str = "tu_1") -> SimpleNamespace:
    dump = {"type": "tool_use", "id": block_id, "name": name, "input": tool_input}
    return SimpleNamespace(
        type="tool_use", id=block_id, name=name, input=tool_input,
        model_dump=lambda **_: dict(dump),
    )


def _message(content: list, stop_reason: str, in_tok: int = 120, out_tok: int = 64) -> SimpleNamespace:
    return SimpleNamespace(
        content=content,
        stop_reason=stop_reason,
        usage=SimpleNamespace(input_tokens=in_tok, output_tokens=out_tok),
    )


class _FakeMessages:
    def __init__(self, responses: list):
        self._responses = list(responses)
        self.calls = 0

    async def create(self, **_kwargs):
        self.calls += 1
        if not self._responses:
            raise AssertionError("Anthropic create called more times than scripted")
        return self._responses.pop(0)


class _FakeAnthropic:
    """Replays scripted Anthropic responses; never opens a socket."""

    _script: list = []

    def __init__(self, *_a, **_k):
        self.messages = _FakeMessages(list(type(self)._script))


def _install_real_mcp_shim() -> None:
    """Expose the REAL mcp_server surface to the worker.

    The worker now matches the real contract exactly (``tools_spec`` is a list,
    ``dispatch_tool_call`` takes one dict), so no adaptation is needed — we just
    ensure the worker's lazy ``from mcp_server import ...`` resolves to the real
    module, whose ``place_proposed_order`` the test patches.
    """
    sys.modules["mcp_server"] = mcp_server


@pytest.fixture(autouse=True)
def _restore_mcp_module():
    saved = sys.modules.get("mcp_server")
    yield
    if saved is not None:
        sys.modules["mcp_server"] = saved
    else:
        sys.modules.pop("mcp_server", None)


# ── Step 1: webhook ingest ──────────────────────────────────────────────────

async def test_e2e_tv_webhook_to_paper_fill(client):
    """Full path: webhook ingest -> worker review -> propose_order -> applied."""
    # ---- Step 1: POST a valid TV alert and assert it persists ----
    payload = {
        "symbol": "AAPL",
        "action": "buy",
        "price": 195.5,
        "timenow": datetime.now(timezone.utc).isoformat(),
        "interval": "1D",
        "strategy_order_id": "e2e-1",
        "secret": TV_SECRET,
    }

    with patch.object(cfg, "TV_WEBHOOK_SECRET", TV_SECRET), \
         patch.object(cfg, "TV_IP_STRICT", False), \
         patch.object(cfg, "TV_FRESHNESS_SECONDS", 600):
        resp = await client.post("/api/webhook/tv", json=payload)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "queued"
    signal_id = body["signal_id"]
    assert signal_id

    # direct_candidates row: source='tv_webhook', status='pending_review'
    async with get_db() as db:
        async with db.execute(
            "SELECT id, symbol, status, source FROM direct_candidates WHERE id=?",
            (signal_id,),
        ) as cur:
            cand = await cur.fetchone()
        async with db.execute(
            "SELECT event_key, signal_id FROM tv_idempotency WHERE signal_id=?",
            (signal_id,),
        ) as cur:
            idem = await cur.fetchone()

    assert cand is not None, "direct_candidates row missing"
    assert cand[1] == "AAPL"
    assert cand[2] == "pending_review"
    assert cand[3] == "tv_webhook"

    assert idem is not None, "tv_idempotency row missing"
    assert idem[1] == signal_id
    assert idem[0]  # non-empty event_key

    # ---- Step 2: drive the worker on the claimed candidate ----
    # Scripted model: first turn calls propose_order, second turn ends.
    _FakeAnthropic._script = [
        _message(
            [_tool_use_block(
                "propose_order",
                {
                    "symbol": "AAPL",
                    "action": "BUY",
                    "qty": 10,
                    "limit_price": 195.5,
                    "signal_id": signal_id,
                },
            )],
            "tool_use",
        ),
        _message([_text_block("Paper order proposed and accepted.")], "end_turn"),
    ]

    _install_real_mcp_shim()

    # Allow every gate so the proposal reaches the executor boundary.
    risk_ok = SimpleNamespace(status="ALLOW", reasons=[])
    impact_ok = SimpleNamespace(allowed=True, reason="ok", details=None)

    fake_trade = Trade(
        rule_id="r-e2e",
        rule_name=f"claude_worker:{signal_id}",
        symbol="AAPL",
        action="BUY",
        asset_type="STK",
        quantity=10,
        order_type="LMT",
        limit_price=195.5,
        fill_price=195.5,
        status="FILLED",
        order_id=4242,
        timestamp=datetime.now(timezone.utc).isoformat(),
        source="ai_direct",
    )

    place_order_mock = AsyncMock(return_value=fake_trade)
    # Spy on the shared chain: wrap the REAL place_proposed_order so the
    # propose_order tool provably routes through it (not around it). mcp_server
    # binds ``place_proposed_order`` at import, so patch it where it is LOOKED
    # UP (mcp_server.place_proposed_order), not only on the defining module.
    real_chain = order_proposal.place_proposed_order
    # wraps (not side_effect): an AsyncMock with an async side_effect would return
    # the unawaited coroutine, so mcp_propose_order.asdict() would fail. wraps
    # delegates to the real coroutine, returns the real ProposalResult, and still
    # records await_count/await_args for the routing assertions below.
    chain_spy = AsyncMock(wraps=real_chain)

    with patch.object(claude_worker.cfg, "CLAUDE_WORKER_ENABLED", True), \
         patch.object(claude_worker.cfg, "CLAUDE_WORKER_MODEL", "claude-test-model"), \
         patch("anthropic.AsyncAnthropic", _FakeAnthropic), \
         patch.object(risk_manager, "check_trade_risk", return_value=risk_ok), \
         patch.object(risk_manager, "check_portfolio_impact", return_value=impact_ok), \
         patch.object(safety_gate, "evaluate_runtime_safety",
                      AsyncMock(return_value=(True, "clear"))), \
         patch.object(order_executor, "place_order", place_order_mock), \
         patch.object(mcp_server, "place_proposed_order", chain_spy), \
         patch.object(order_proposal, "place_proposed_order", chain_spy):
        candidate = await claude_worker.claim_one_candidate()
        assert candidate is not None and candidate["id"] == signal_id
        status = await claude_worker.process_candidate(candidate)

    # Worker classified the approved propose_order as 'applied'.
    assert status == "applied"

    # The propose_order tool routed through the shared pre-trade chain.
    assert chain_spy.await_count == 1, "place_proposed_order was not invoked"
    chain_rule = chain_spy.await_args.args[0]
    assert chain_rule.symbol == "AAPL"
    assert chain_rule.action.type == "BUY"
    assert chain_rule.action.quantity == 10
    assert chain_spy.await_args.kwargs.get("source") == "claude_worker"

    # The executor was reached with skip_safety=False (never a bypass).
    assert place_order_mock.await_count == 1, "order_executor.place_order not reached"
    assert place_order_mock.await_args.kwargs.get("skip_safety") is False
    assert place_order_mock.await_args.kwargs.get("source") == "claude_worker"

    # Candidate persisted as 'applied'.
    async with get_db() as db:
        async with db.execute(
            "SELECT status FROM direct_candidates WHERE id=?", (signal_id,)
        ) as cur:
            row = await cur.fetchone()
    assert row is not None and row[0] == "applied"

    # ---- Step 3: a cost/decision row was recorded for the review ----
    async with get_db() as db:
        async with db.execute(
            "SELECT source, mode, provider, model, input_tokens, output_tokens, status "
            "FROM ai_decision_runs WHERE source='tv_webhook' ORDER BY created_at DESC"
        ) as cur:
            runs = await cur.fetchall()

    assert runs, "no ai_decision_runs row recorded for the review"
    run = runs[0]
    assert run[0] == "tv_webhook"
    assert run[1] == "paper"
    assert run[2] == "anthropic"
    assert run[3] == "claude-test-model"
    # Token usage from the scripted responses accrued onto the ledger run.
    assert (run[4] or 0) > 0, "input_tokens not persisted (cost would not accrue)"
    assert (run[5] or 0) > 0, "output_tokens not persisted (cost would not accrue)"
    assert run[6] == "completed"

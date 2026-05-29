"""Paper fence: TV/Claude-sourced proposals must never reach a LIVE broker account.

Surfaced by a 3-engine review (Codex + Claude) of ULTRAPLAN v4: the TV/Claude path
and the scanner share one IBKR connection and AUTOPILOT_MODE is global, so "paper-only"
was a procedural promise, not enforced. place_proposed_order now fails closed for the
fenced sources when IS_PAPER=false (live account), unless CLAUDE_LIVE_TRADING_ENABLED.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import order_proposal
from models import Rule, TradeAction


def _rule() -> Rule:
    return Rule(
        name="fence-test",
        symbol="AAPL",
        enabled=False,
        conditions=[],
        action=TradeAction(type="BUY", asset_type="STK", quantity=1,
                           order_type="LMT", limit_price=100.0),
        status="paper",
        ai_generated=True,
        created_by="ai",
    )


def _allow_gates():
    """Patches that let the full chain pass through to place_order."""
    return [
        patch.object(order_proposal.risk_manager, "check_trade_risk",
                     return_value=SimpleNamespace(status="ALLOW", reasons=[])),
        patch.object(order_proposal.risk_manager, "check_portfolio_impact",
                     return_value=SimpleNamespace(allowed=True, reason="ok", details=None)),
        patch.object(order_proposal.safety_gate, "evaluate_runtime_safety",
                     AsyncMock(return_value=(True, "clear"))),
    ]


async def test_fence_blocks_claude_worker_on_live_account():
    """source=claude_worker + IS_PAPER=false + override off -> rejected, broker never reached."""
    po = AsyncMock()
    with patch.object(order_proposal.cfg, "IS_PAPER", False), \
         patch.object(order_proposal.cfg, "CLAUDE_LIVE_TRADING_ENABLED", False), \
         patch.object(order_proposal.order_executor, "place_order", po):
        res = await order_proposal.place_proposed_order(_rule(), source="claude_worker", user_id="demo")
    assert res.status == "rejected"
    assert res.stage == "paper_fence"
    po.assert_not_called()


async def test_fence_blocks_tv_webhook_on_live_account():
    po = AsyncMock()
    with patch.object(order_proposal.cfg, "IS_PAPER", False), \
         patch.object(order_proposal.cfg, "CLAUDE_LIVE_TRADING_ENABLED", False), \
         patch.object(order_proposal.order_executor, "place_order", po):
        res = await order_proposal.place_proposed_order(_rule(), source="tv_webhook", user_id="demo")
    assert res.status == "rejected"
    assert res.stage == "paper_fence"
    po.assert_not_called()


async def test_fence_allows_when_paper_account():
    """IS_PAPER=true -> fence inert; chain proceeds to place_order (approved)."""
    po = AsyncMock(return_value=SimpleNamespace(order_id=1))
    with patch.object(order_proposal.cfg, "IS_PAPER", True), \
         _allow_gates()[0], _allow_gates()[1], _allow_gates()[2], \
         patch.object(order_proposal.order_executor, "place_order", po):
        res = await order_proposal.place_proposed_order(_rule(), source="claude_worker", user_id="demo")
    assert res.status == "approved"
    po.assert_awaited_once()


async def test_fence_allows_with_explicit_override():
    """IS_PAPER=false but CLAUDE_LIVE_TRADING_ENABLED=true -> fence inert (explicit opt-in)."""
    po = AsyncMock(return_value=SimpleNamespace(order_id=2))
    with patch.object(order_proposal.cfg, "IS_PAPER", False), \
         patch.object(order_proposal.cfg, "CLAUDE_LIVE_TRADING_ENABLED", True), \
         _allow_gates()[0], _allow_gates()[1], _allow_gates()[2], \
         patch.object(order_proposal.order_executor, "place_order", po):
        res = await order_proposal.place_proposed_order(_rule(), source="claude_worker", user_id="demo")
    assert res.status == "approved"


async def test_fence_does_not_affect_scanner_source_on_live():
    """The scanner path is NOT fenced (it is the intended live path)."""
    po = AsyncMock(return_value=SimpleNamespace(order_id=3))
    with patch.object(order_proposal.cfg, "IS_PAPER", False), \
         patch.object(order_proposal.cfg, "CLAUDE_LIVE_TRADING_ENABLED", False), \
         _allow_gates()[0], _allow_gates()[1], _allow_gates()[2], \
         patch.object(order_proposal.order_executor, "place_order", po):
        res = await order_proposal.place_proposed_order(_rule(), source="scanner", user_id="demo")
    assert res.status == "approved"

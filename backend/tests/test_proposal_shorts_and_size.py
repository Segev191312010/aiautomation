"""Gating tests for the live flip: NO-SHORTS and POSITION-SIZE / 1%-RISK caps.

These assert that the two non-negotiable invariants hold *through the real
pre-trade chain* (``order_proposal.place_proposed_order``), not just in the leaf
helpers, so no order source (Claude worker, MCP, future callers) can short the
account or place an oversized position once trading flips live.

Where each invariant is enforced (verified by reading the source, asserted below)
--------------------------------------------------------------------------------
1. NO SHORTS  — enforced in TWO independent places, defense in depth:

   a. ``risk_manager.check_trade_risk`` (chain step 1): a SELL with no held long
      returns ``BLOCK`` "SELL rejected: no long position ... (cash account)".
      Because ``place_proposed_order`` calls step 1 with ``positions=[]``, a
      SELL-to-open is rejected here FIRST, at ``stage="risk"`` — it never even
      reaches the safety kernel.  (Tested via ``place_proposed_order`` below.)

   b. ``safety_kernel.assert_no_shorts`` (chain step 3, via the runtime safety
      gate): raises ``SafetyViolation("Short entries are disabled")`` for a
      sell-to-open.  Since step 1 already short-circuits the SELL-to-open in the
      proposal chain, this leaf is proven (i) directly and (ii) through the
      safety gate, so the kernel guard is independently load-bearing and would
      still block a short even if the cash-account check in step 1 were removed.

2. POSITION SIZE / 1% RISK — the proposal chain REJECTS an oversized order; it
   never silently resizes it down.  Two distinct caps fire:

   a. ``risk_manager.check_trade_risk`` enforces ``RiskLimits.max_position_pct``
      (the single-position notional cap, default 10%): an order whose notional
      exceeds that fraction of account value returns ``BLOCK`` (chain step 1).

   b. ``safety_kernel.assert_risk_budget`` enforces the 1% hard risk budget
      ``cfg.RISK_PER_TRADE_PCT`` (chain step 3, via the safety gate): with no
      stop it conservatively measures risk as full order notional and raises
      ``SafetyViolation`` when notional > 1% of equity.

   IMPORTANT — where ``POSITION_SIZE_PCT`` lives (documented per the task):
   ``cfg.POSITION_SIZE_PCT`` (default 0.5%) is the bot's *initial sizing target*
   used to COMPUTE a quantity, not a gate.  It is applied ONLY in
   ``bot_runner.py`` (``computed_qty = max(1, int(account_val *
   cfg.POSITION_SIZE_PCT / price))``), BEFORE the risk chain runs, so the guards
   see the final quantity.  ``order_proposal.place_proposed_order`` and
   ``order_executor.place_order`` do NOT contain any resizing logic: an oversized
   proposed quantity is REJECTED by the caps above, never silently truncated to
   POSITION_SIZE_PCT.  The last test pins that "reject, never silently resize"
   contract for the proposal path and asserts the sizing math lives in bot_runner.
"""
from __future__ import annotations

import inspect

import pytest

import order_proposal
import risk_manager
import safety_kernel
from config import cfg
from risk_config import DEFAULT_LIMITS
from models import Rule, TradeAction
from safety_kernel import SafetyViolation


# ── helpers ──────────────────────────────────────────────────────────────────

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


class _Trade:
    def __init__(self, order_id=4242):
        self.order_id = order_id


def _stub_downstream(monkeypatch, calls):
    """Stub the steps AFTER the gate under test so a leak past a gate is visible.

    These stubs deliberately return APPROVE/None-trade so that *if* a gate that
    should have blocked instead let the order through, the order would land —
    and the test's stage/status assertion would fail loudly.  The real
    ``check_trade_risk`` / ``check_portfolio_impact`` / ``assert_*`` stay live.
    """
    placed = {"called": False, "skip_safety": None, "qty": None}

    async def fake_place_order(rule, *, source="rule", skip_safety=False, **kw):
        calls.append("place_order")
        placed["called"] = True
        placed["skip_safety"] = skip_safety
        placed["qty"] = rule.action.quantity
        return _Trade()

    monkeypatch.setattr(order_proposal.order_executor, "place_order", fake_place_order)
    return placed


@pytest.fixture(autouse=True)
def _force_live_authority(monkeypatch):
    """The proposal chain's safety gate runs the runtime kernel with
    ``require_autopilot_authority=True``.  To exercise the no-shorts / risk-budget
    guards in isolation (the live-flip invariants), neutralize the upstream
    autopilot/daily-loss/duplicate gates that depend on DB + wall-clock state so
    a green test means the *short/size* guard fired, not an unrelated kill switch.
    """
    async def _ok():
        return None

    monkeypatch.setattr(safety_kernel, "assert_not_killed", _ok)

    async def _ok_daily(*, is_exit=False):
        return None

    monkeypatch.setattr(safety_kernel, "assert_daily_loss_not_locked", _ok_daily)

    async def _ok_dup(symbol, side, source):
        return None

    monkeypatch.setattr(safety_kernel, "assert_not_duplicate", _ok_dup)


# ── 1. NO SHORTS ─────────────────────────────────────────────────────────────

async def test_sell_to_open_no_position_rejected_through_chain(monkeypatch):
    """A SELL with no existing long is rejected by the proposal chain and the
    broker is NEVER called.

    The reject lands at ``stage="risk"`` because step 1
    (``check_trade_risk``, called with ``positions=[]``) blocks the cash-account
    short before the safety kernel even runs — both layers agree it is illegal.
    """
    calls: list[str] = []
    placed = _stub_downstream(monkeypatch, calls)

    result = await order_proposal.place_proposed_order(
        _rule(action="SELL", qty=10), source="claude_worker",
    )

    assert result.status == "rejected"
    assert result.stage == "risk"
    assert "no long position" in result.reason.lower()
    # Broker must never be reached for a sell-to-open.
    assert placed["called"] is False
    assert "place_order" not in calls


def test_assert_no_shorts_blocks_sell_to_open_directly():
    """safety_kernel.assert_no_shorts: sell-to-open (no position, not an exit)
    raises. This is the defense-in-depth guard the safety gate runs at step 3."""
    with pytest.raises(SafetyViolation, match="Short entries are disabled"):
        safety_kernel.assert_no_shorts("SELL", is_exit=False, has_existing_position=False)


def test_assert_no_shorts_allows_legitimate_sells():
    """assert_no_shorts must NOT block a legitimate exit/sell of a held long —
    only a naked sell-to-open. BUY is always fine."""
    # Closing an existing long (has position) is allowed.
    safety_kernel.assert_no_shorts("SELL", is_exit=False, has_existing_position=True)
    # An explicit exit is allowed.
    safety_kernel.assert_no_shorts("SELL", is_exit=True, has_existing_position=False)
    # BUY never triggers the short guard.
    safety_kernel.assert_no_shorts("BUY", is_exit=False, has_existing_position=False)


async def test_safety_gate_blocks_short_when_risk_step_bypassed(monkeypatch):
    """Prove assert_no_shorts is independently load-bearing in the chain.

    If the step-1 cash-account check were ever loosened (e.g. SHORT_ALLOWED
    handling changed), the runtime safety kernel at step 3 must STILL block a
    sell-to-open. We stub step 1 + step 2 to PASS and confirm the chain rejects
    at ``stage="safety_gate"`` with the kernel's short message.
    """
    calls: list[str] = []
    placed = _stub_downstream(monkeypatch, calls)

    def fake_risk(*a, **kw):
        calls.append("risk")
        return risk_manager.RiskCheckResult("PASS", ["forced pass"])

    def fake_impact(*a, **kw):
        calls.append("portfolio")
        return risk_manager.PortfolioImpactResult(allowed=True, reason="pass")

    monkeypatch.setattr(order_proposal.risk_manager, "check_trade_risk", fake_risk)
    monkeypatch.setattr(order_proposal.risk_manager, "check_portfolio_impact", fake_impact)

    result = await order_proposal.place_proposed_order(
        _rule(action="SELL", qty=10), source="claude_worker",
    )

    assert result.status == "rejected"
    assert result.stage == "safety_gate"
    assert "short" in result.reason.lower()
    assert placed["called"] is False
    assert "place_order" not in calls


# ── 2. OVERSIZED ORDER — rejected, never silently placed at full size ─────────

async def test_oversized_order_rejected_at_risk_max_position(monkeypatch):
    """An order whose notional blows through max_position_pct is BLOCKED by
    check_trade_risk (chain step 1) — the broker is never called at full size.

    With account_value floor = $1 (set by ``_account_value_floor``) any positive
    notional is >> ``max_position_pct``% of equity, so this is the realistic
    pre-check behavior for the proposal chain today: oversized ⇒ rejected.
    """
    calls: list[str] = []
    placed = _stub_downstream(monkeypatch, calls)

    # qty * price = 100 * 150 = $15,000 notional, vastly over any % of the floor.
    result = await order_proposal.place_proposed_order(
        _rule(action="BUY", qty=100, limit=150.0), source="claude_worker",
    )

    assert result.status == "rejected"
    assert result.stage == "risk"
    assert "exceeds limit" in result.reason.lower()
    assert placed["called"] is False
    assert "place_order" not in calls


def test_check_trade_risk_blocks_oversized_position_directly():
    """Unit-level: check_trade_risk BLOCKs when order notional exceeds
    max_position_pct, and PASSes a same-symbol order that sits under the cap.

    Pins the position-size cap independently of the chain wiring.
    """
    account_value = 100_000.0
    max_pct = DEFAULT_LIMITS.max_position_pct  # 10.0 by default
    price = 100.0

    # Oversized: 200 sh * $100 = $20,000 = 20% of $100k > 10% cap → BLOCK.
    over = risk_manager.check_trade_risk(
        symbol="AAPL", qty=200, side="BUY", positions=[],
        account_value=account_value, est_price=price, limits=DEFAULT_LIMITS,
    )
    assert over.status == "BLOCK"
    assert any("exceeds limit" in r.lower() for r in over.reasons)

    # Within cap: 50 sh * $100 = $5,000 = 5% of $100k < 10% → not blocked.
    ok = risk_manager.check_trade_risk(
        symbol="AAPL", qty=50, side="BUY", positions=[],
        account_value=account_value, est_price=price, limits=DEFAULT_LIMITS,
    )
    assert ok.status in ("PASS", "WARN")
    assert not any("exceeds limit" in r.lower() for r in ok.reasons)


def test_assert_risk_budget_enforces_one_percent_hard_cap():
    """safety_kernel.assert_risk_budget enforces the 1% (RISK_PER_TRADE_PCT)
    hard limit at chain step 3. With no stop, risk == full notional, so notional
    above 1% of equity raises; an order at/under 1% passes.

    This is the SECOND, independent size guard the proposal chain applies via
    the runtime safety gate — proving the 1% framework is enforced, not just the
    10% single-position cap.
    """
    equity = 100_000.0
    max_risk = equity * cfg.RISK_PER_TRADE_PCT / 100  # 1% = $1,000

    # Notional $1,500 (15 sh * $100) > $1,000 (1% of equity) → blocked.
    with pytest.raises(SafetyViolation, match="exceeds"):
        safety_kernel.assert_risk_budget(
            quantity=15, price_estimate=100.0, account_equity=equity,
        )

    # Notional $1,000 (10 sh * $100) == 1% of equity → allowed (no raise).
    assert max_risk == 1_000.0
    safety_kernel.assert_risk_budget(
        quantity=10, price_estimate=100.0, account_equity=equity,
    )


def test_proposal_chain_rejects_oversized_never_resizes_source_contract():
    """Document + pin WHERE sizing is enforced, per the task.

    - ``order_proposal`` / ``order_executor.place_order`` contain NO resizing of
      ``rule.action.quantity``: they REJECT an oversized order, they do not
      silently truncate it to POSITION_SIZE_PCT.
    - ``cfg.POSITION_SIZE_PCT`` initial sizing lives ONLY in ``bot_runner.py``,
      applied BEFORE the risk chain so guards see the final quantity.

    If this contract ever changes (e.g. resizing is added to the proposal path),
    this test fails and forces the size-enforcement story to be re-documented.
    """
    proposal_src = inspect.getsource(order_proposal)
    # The proposal chain never references POSITION_SIZE_PCT and never resizes.
    assert "POSITION_SIZE_PCT" not in proposal_src
    assert "model_copy" not in proposal_src  # how bot_runner rewrites quantity
    assert "computed_qty" not in proposal_src

    # The sizing target is a config knob; the canonical formula lives in bot_runner.
    assert hasattr(cfg, "POSITION_SIZE_PCT")
    import bot_runner
    bot_runner_src = inspect.getsource(bot_runner)
    assert "cfg.POSITION_SIZE_PCT" in bot_runner_src
    assert "computed_qty" in bot_runner_src

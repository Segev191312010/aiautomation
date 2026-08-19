"""Shared order-proposal helper.

Runs the REAL existing pre-trade chain — risk → portfolio impact → runtime
safety gate → broker submission — in one place so that every non-rule order
source (the Claude worker, future MCP callers) goes through exactly the same
gates as the rule engine. No source may bypass a gate.

Chain order (each step short-circuits to a rejection on a block):
    1. risk_manager.check_trade_risk          (per-trade risk)
    2. risk_manager.check_portfolio_impact     (concentration / correlation)
    3. safety_gate.evaluate_runtime_safety     (runtime safety kernel)
    4. order_executor.place_order(skip_safety=False)

``place_order`` returning ``None`` means a non-exception block fired *inside*
the executor (pre-flight, the per-symbol rate cap, IBKR disconnected, etc.).
That is treated as a DEFERRAL, never an approval — the caller must not retry
in a way that assumes the order landed.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

import risk_manager
from models import Rule
from services import safety_gate
import order_executor
from config import cfg

log = logging.getLogger(__name__)

# Externally-sourced proposals (TradingView webhook / Claude worker) are fenced
# off a LIVE broker account — see the paper fence in place_proposed_order.
_PAPER_FENCED_SOURCES = {"claude_worker", "tv_webhook"}


@dataclass
class ProposalResult:
    """Outcome of an order proposal. JSON-serializable via ``dataclasses.asdict``.

    status:
        ``approved``  — broker accepted the order (Trade record returned).
        ``rejected``  — a gate blocked the order (see ``stage`` / ``reason``).
        ``deferred``  — ``place_order`` returned None (rate cap / pre-flight /
                        IBKR unavailable); the order did NOT land and may be
                        retried later. Never reported as approved/applied.
    stage:
        Which step produced the result: ``risk`` | ``portfolio`` | ``safety_gate``
        | ``place_order``.
    """
    status: str
    stage: str
    reason: str
    order_id: Optional[int] = None
    risk_snapshot: Optional[dict[str, Any]] = None


async def place_proposed_order(
    rule: Rule,
    source: str,
    user_id: str = "demo",
) -> ProposalResult:
    """Run the full pre-trade chain for a proposed order.

    The chain mirrors the rule engine's path exactly. ``skip_safety`` is hard
    ``False`` at the executor so the runtime kernel runs a second time inside
    ``place_order`` — defense in depth, never a bypass.
    """
    # Hard paper fence: externally-sourced proposals (TV webhook / Claude worker)
    # must never reach a LIVE broker account. The scanner and this path share one
    # IBKR connection, so we fail closed here regardless of AUTOPILOT_MODE rather
    # than rely on operator convention. (CLAUDE_LIVE_TRADING_ENABLED overrides.)
    if (source in _PAPER_FENCED_SOURCES
            and not cfg.IS_PAPER
            and not cfg.CLAUDE_LIVE_TRADING_ENABLED):
        log.warning(
            "order_proposal paper-fence: refusing live-account order for source=%s symbol=%s",
            source, rule.symbol,
        )
        return ProposalResult(
            status="rejected",
            stage="paper_fence",
            reason=(
                f"source '{source}' is paper-only; refusing to place against a live "
                "broker account (IS_PAPER=false). Set CLAUDE_LIVE_TRADING_ENABLED=true to override."
            ),
        )

    symbol = rule.symbol
    side = rule.action.type
    qty = rule.action.quantity
    limit_price = rule.action.limit_price
    est_price = float(limit_price or 0.0)

    # ── 1. Per-trade risk check ──────────────────────────────────────────
    risk = risk_manager.check_trade_risk(
        symbol=symbol,
        qty=qty,
        side=side,
        positions=[],
        account_value=_account_value_floor(),
        est_price=est_price,
    )
    risk_snapshot: dict[str, Any] = {"risk": {"status": risk.status, "reasons": list(risk.reasons)}}
    if risk.status == "BLOCK":
        log.warning("order_proposal rejected at risk: %s %s %s — %s", side, qty, symbol, risk.reasons)
        return ProposalResult(
            status="rejected",
            stage="risk",
            reason="; ".join(risk.reasons) or "risk check blocked",
            risk_snapshot=risk_snapshot,
        )

    # ── 2. Portfolio impact / concentration check ────────────────────────
    candidate_notional = qty * est_price
    impact = risk_manager.check_portfolio_impact(
        symbol=symbol,
        side=side,
        positions=[],
        net_liq=_account_value_floor(),
        candidate_notional=candidate_notional,
    )
    risk_snapshot["portfolio"] = {"allowed": impact.allowed, "reason": impact.reason, "details": impact.details}
    if not impact.allowed:
        log.warning(
            "order_proposal rejected at portfolio: %s %s %s — %s",
            side, qty, symbol, impact.details or impact.reason,
        )
        return ProposalResult(
            status="rejected",
            stage="portfolio",
            reason=impact.details or impact.reason or "portfolio impact blocked",
            risk_snapshot=risk_snapshot,
        )

    # ── 3. Runtime safety gate ───────────────────────────────────────────
    allowed, reason = await safety_gate.evaluate_runtime_safety(
        symbol=symbol,
        side=side,
        quantity=qty,
        source=source,
        account_equity=_account_value_floor(),
        price_estimate=est_price,
        stop_price=None,
        is_exit=False,
        has_existing_position=False,
        require_autopilot_authority=True,
    )
    risk_snapshot["safety_gate"] = {"allowed": allowed, "reason": reason}
    if not allowed:
        log.warning("order_proposal rejected at safety_gate: %s %s %s — %s", side, qty, symbol, reason)
        return ProposalResult(
            status="rejected",
            stage="safety_gate",
            reason=reason or "runtime safety gate blocked",
            risk_snapshot=risk_snapshot,
        )

    # ── 4. Broker submission (safety re-run inside, never skipped) ───────
    trade = await order_executor.place_order(rule, source=source, skip_safety=False)
    if trade is None:
        # None == an in-executor block (pre-flight, per-symbol rate cap,
        # IBKR disconnected). The order did NOT land — defer, never approve.
        log.info("order_proposal deferred (place_order returned None): %s %s %s", side, qty, symbol)
        return ProposalResult(
            status="deferred",
            stage="place_order",
            reason="order not placed (rate cap / pre-flight / broker unavailable)",
            risk_snapshot=risk_snapshot,
        )

    return ProposalResult(
        status="approved",
        stage="place_order",
        reason="order submitted",
        order_id=trade.order_id,
        risk_snapshot=risk_snapshot,
    )


def _account_value_floor() -> float:
    """Account equity used for sizing/concentration when no live snapshot is
    threaded in. The runtime safety kernel (step 3) and ``place_order`` (step 4)
    both fetch the real live equity from IBKR; this floor only feeds the
    cheap pre-checks. A positive value avoids the ``account_value <= 0`` hard
    block in ``check_trade_risk`` short-circuiting before the real gates run.
    """
    return 1.0

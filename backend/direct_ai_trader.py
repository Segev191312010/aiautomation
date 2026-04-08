"""Direct AI trade execution for opportunities outside the stored rule inventory."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from api_contracts import AIDirectTrade
from ai_guardrails import log_ai_action
from config import cfg
from database import get_open_positions, save_trade
from market_data import get_latest_price
from models import Rule, TradeAction
from order_executor import place_order
from risk_manager import calculate_position_size
from safety_kernel import SafetyViolation, is_autopilot_live
from services import order_lifecycle, safety_gate

log = logging.getLogger(__name__)


async def preview_direct_trade(decision: AIDirectTrade | dict) -> dict:
    """Compute the estimated execution inputs for a direct AI trade without placing it."""
    if isinstance(decision, dict):
        decision = AIDirectTrade(**decision)

    symbol = decision.symbol.upper()
    open_positions = await get_open_positions()
    existing = next((pos for pos in open_positions if pos.symbol.upper() == symbol and pos.side == "BUY"), None)
    is_exit = decision.action == "SELL"

    if is_exit and existing is None:
        raise SafetyViolation(f"Cannot SELL {symbol} without an existing long position")

    entry_price = (
        decision.limit_price
        if decision.order_type == "LMT" and decision.limit_price
        else await get_latest_price(symbol)
    )
    if not entry_price or entry_price <= 0:
        raise SafetyViolation(f"Unable to determine price for {symbol}")

    if is_exit:
        quantity = int(existing.quantity)
    else:
        account_equity = await _get_account_equity()
        sizing = calculate_position_size(
            entry_price=entry_price,
            stop_price=decision.stop_price,
            account_value=account_equity,
            risk_pct=cfg.RISK_PER_TRADE_PCT,
            method="fixed_fractional",
        )
        quantity = int(sizing["shares"])
        if quantity < 1:
            raise SafetyViolation(f"Calculated size for {symbol} is 0 shares")

    return {
        "decision": decision,
        "symbol": symbol,
        "existing": existing,
        "is_exit": is_exit,
        "entry_price": float(entry_price),
        "quantity": quantity,
        "notional": float(entry_price) * quantity,
    }


async def execute_direct_trade(decision: AIDirectTrade) -> dict:
    preview = await preview_direct_trade(decision)
    symbol = preview["symbol"]
    existing = preview["existing"]
    is_exit = preview["is_exit"]
    entry_price = preview["entry_price"]
    quantity = int(preview["quantity"])

    account_equity = 0.0 if is_exit else await _get_account_equity()
    allowed, reason = await safety_gate.evaluate_runtime_safety(
        symbol=symbol,
        side=decision.action,
        quantity=quantity,
        source="ai_direct",
        account_equity=account_equity,
        price_estimate=entry_price,
        stop_price=decision.stop_price,
        is_exit=is_exit,
        has_existing_position=existing is not None,
        require_autopilot_authority=True,
    )
    if not allowed:
        raise SafetyViolation(reason or f"Runtime safety gate blocked direct AI trade for {symbol}")

    order_rule = Rule(
        id=f"ai-direct:{symbol}",
        name=f"AI Direct {decision.action} {symbol}",
        symbol=symbol,
        enabled=True,
        conditions=[],
        logic="AND",
        action=TradeAction(
            type=decision.action,
            asset_type="STK",
            quantity=quantity,
            order_type=decision.order_type,
            limit_price=decision.limit_price,
        ),
        cooldown_minutes=0,
        status="active",
        ai_generated=True,
        ai_reason=decision.reason,
        thesis=decision.reason,
        hold_style="intraday",
        created_by="ai",
    )

    if not is_autopilot_live():
        now_iso = datetime.now(timezone.utc).isoformat()
        simulated_trade = {
            "id": f"paper-{symbol}-{int(datetime.now(timezone.utc).timestamp())}",
            "rule_id": order_rule.id,
            "rule_name": order_rule.name,
            "symbol": symbol,
            "action": decision.action,
            "asset_type": "STK",
            "quantity": quantity,
            "order_type": decision.order_type,
            "limit_price": decision.limit_price,
            "fill_price": entry_price,
            "status": "FILLED",
            "order_id": None,
            "timestamp": now_iso,
            "source": "ai_direct",
            "ai_reason": decision.reason,
            "ai_confidence": decision.confidence,
            "stop_price": decision.stop_price,
            "invalidation": decision.invalidation,
            "metadata": {"paper": True},
            "mode": "PAPER",
            "opened_at": now_iso,
            "entry_price": entry_price,
            "decision_id": getattr(decision, "decision_id", None),
        }
        from models import Trade

        trade = Trade(**simulated_trade)

        if is_exit:
            trade.opened_at = None
            await order_lifecycle.stamp_exit_trade_context(
                trade,
                existing,
                fallback_mode="PAPER",
                fallback_source="ai_direct",
                fallback_decision_id=trade.decision_id,
            )
            await order_lifecycle.finalize_filled_exit_trade(
                trade,
                existing,
                close_reason="ai_direct_exit",
                fallback_exit_price=entry_price,
            )
        else:
            trade.position_id = trade.id
            await save_trade(trade)
            await order_lifecycle.register_entry_position_from_fill(trade, rule_name=trade.rule_name)

        await log_ai_action(
            action_type=f"direct_trade_{decision.action.lower()}",
            category="direct_ai",
            description=f"Paper {decision.action} {quantity} {symbol}",
            old_value=None,
            new_value=trade.model_dump(),
            reason=decision.reason,
            confidence=decision.confidence,
            status="applied",
        )
        if trade.decision_id:
            try:
                from ai_decision_ledger import mark_decision_item_applied

                await mark_decision_item_applied(trade.decision_id, created_trade_id=trade.id)
            except Exception as exc:
                log.warning("Failed to mark decision item applied: %s", exc)
        return {"mode": cfg.AUTOPILOT_MODE, "simulated": True, "status": "applied", "trade": trade.model_dump()}

    trade = await place_order(
        order_rule,
        source="ai_direct",
        skip_safety=True,
        require_autopilot_authority=True,
        stop_price=decision.stop_price,
        is_exit=is_exit,
        has_existing_position=existing is not None,
    )
    if not trade or getattr(trade, "status", "") == "ERROR":
        raise SafetyViolation(f"Failed to place direct AI trade for {symbol}")

    trade.source = "ai_direct"
    trade.ai_reason = decision.reason
    trade.ai_confidence = decision.confidence
    trade.stop_price = decision.stop_price
    trade.invalidation = decision.invalidation
    trade.mode = "LIVE"
    trade.decision_id = getattr(decision, "decision_id", None)

    if is_exit:
        if trade.fill_price is not None and trade.status == "FILLED":
            await order_lifecycle.stamp_exit_trade_context(
                trade,
                existing,
                fallback_mode="LIVE",
                fallback_source="ai_direct",
                fallback_decision_id=trade.decision_id,
            )
            await order_lifecycle.finalize_filled_exit_trade(
                trade,
                existing,
                close_reason="ai_direct_exit",
                fallback_exit_price=trade.fill_price,
            )
        else:
            # Exit not immediately filled — stamp context and mark pending
            # so bot_exits._reconcile_pending_exit can finalize later
            await order_lifecycle.stamp_exit_trade_context(
                trade,
                existing,
                fallback_mode="LIVE",
                fallback_source="ai_direct",
                fallback_decision_id=trade.decision_id,
            )
            if trade.order_id and existing:
                from database import save_open_position
                from services.order_recovery import mark_exit_pending_submitted

                now = datetime.now(timezone.utc)
                mark_exit_pending_submitted(existing, trade.order_id, now=now)
                await save_open_position(existing)
                log.info("AI direct exit PENDING for %s (order_id=%s) — marked for reconciliation",
                         symbol, trade.order_id)
    else:
        trade.opened_at = trade.timestamp
        trade.position_id = trade.id
        if trade.fill_price is not None:
            trade.entry_price = trade.fill_price
        await save_trade(trade)
        # HB1-01: register tracked open-position lifecycle (parity with paper path)
        await order_lifecycle.register_entry_position_from_fill(trade, rule_name=trade.rule_name)

    await log_ai_action(
        action_type=f"direct_trade_{decision.action.lower()}",
        category="direct_ai",
        description=f"Live {decision.action} {quantity} {symbol}",
        old_value=None,
        new_value=trade.model_dump(),
        reason=decision.reason,
        confidence=decision.confidence,
        status="applied",
    )
    if trade.decision_id:
        try:
            from ai_decision_ledger import mark_decision_item_applied

            await mark_decision_item_applied(trade.decision_id, created_trade_id=trade.id)
        except Exception as exc:
            log.warning("Failed to mark decision item applied for live trade: %s", exc)
    return {"mode": cfg.AUTOPILOT_MODE, "simulated": False, "status": "applied", "trade": trade.model_dump()}


async def _get_account_equity() -> float:
    if cfg.SIM_MODE:
        from simulation import sim_engine

        return float((await sim_engine.get_account()).net_liquidation)
    from ibkr_client import ibkr

    account = await ibkr.get_account_summary()
    return float(account.balance) if account else 0.0

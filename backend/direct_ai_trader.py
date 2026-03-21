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
from safety_kernel import SafetyViolation, check_all, is_autopilot_live

log = logging.getLogger(__name__)


async def execute_direct_trade(decision: AIDirectTrade) -> dict:
    symbol = decision.symbol.upper()
    open_positions = await get_open_positions()
    existing = next((pos for pos in open_positions if pos.symbol.upper() == symbol and pos.side == "BUY"), None)
    is_exit = decision.action == "SELL"

    if decision.action == "SELL" and existing is None:
        raise SafetyViolation(f"Cannot SELL {symbol} without an existing long position")

    entry_price = decision.limit_price if decision.order_type == "LMT" and decision.limit_price else await get_latest_price(symbol)
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
        await check_all(
            symbol,
            decision.action,
            quantity,
            "ai_direct",
            account_equity=account_equity,
            price_estimate=entry_price,
            stop_price=decision.stop_price,
            is_exit=False,
            has_existing_position=existing is not None,
        )

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
        # H-5 FIX: Paper mode still checks kill switch + daily loss
        if not is_exit:
            from ai_guardrails import _load_guardrails_from_db
            try:
                gconfig = await _load_guardrails_from_db()
                if gconfig.emergency_stop:
                    raise SafetyViolation("Kill switch active — paper entries also blocked")
                if gconfig.daily_loss_locked:
                    raise SafetyViolation("Daily loss lock — paper entries also blocked")
            except SafetyViolation:
                raise
            except Exception:
                pass

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
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "source": "ai_direct",
            "ai_reason": decision.reason,
            "ai_confidence": decision.confidence,
            "stop_price": decision.stop_price,
            "invalidation": decision.invalidation,
            "metadata": {"paper": True},
        }
        from models import Trade

        trade = Trade(**simulated_trade)
        await save_trade(trade)
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
        return {"mode": cfg.AUTOPILOT_MODE, "simulated": True, "trade": trade.model_dump()}

    trade = await place_order(order_rule)
    if not trade:
        raise SafetyViolation(f"Failed to place direct AI trade for {symbol}")

    trade.source = "ai_direct"
    trade.ai_reason = decision.reason
    trade.ai_confidence = decision.confidence
    trade.stop_price = decision.stop_price
    trade.invalidation = decision.invalidation
    await save_trade(trade)
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
    return {"mode": cfg.AUTOPILOT_MODE, "simulated": False, "trade": trade.model_dump()}


async def _get_account_equity() -> float:
    if cfg.SIM_MODE:
        from simulation import sim_engine

        return float((await sim_engine.get_account()).net_liquidation)
    from ibkr_client import ibkr

    account = await ibkr.get_account_summary()
    return float(account.balance) if account else 0.0

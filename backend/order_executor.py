"""
Order executor — places orders via IBKR and logs them to SQLite.
"""
from __future__ import annotations
import asyncio
import logging
from datetime import datetime, timezone
from typing import Callable, Optional
from ib_insync import MarketOrder, LimitOrder, Trade as IBTrade
from ibkr_client import ibkr
from database import save_trade, update_trade_status
from models import Rule, Trade
from config import cfg

log = logging.getLogger(__name__)

# Callback: called when an order is filled → used to broadcast WS events
_fill_callbacks: list[Callable[[Trade], None]] = []


def on_fill(cb: Callable[[Trade], None]) -> None:
    _fill_callbacks.append(cb)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def place_order(rule: Rule) -> Optional[Trade]:
    """
    Place an order for the given rule's action.

    Returns the Trade record (status PENDING), or None on failure.
    """
    if cfg.IS_PAPER:
        log.info("[PAPER] Would place %s %d %s for rule '%s'",
                 rule.action.type, rule.action.quantity, rule.symbol, rule.name)

    if not ibkr.is_connected():
        log.error("Cannot place order — IBKR not connected")
        return None

    # Build contract
    asset_type = rule.action.asset_type
    if asset_type == "STK":
        contract = ibkr.make_stock_contract(rule.symbol)
    else:
        log.error("Unsupported asset type '%s' for automated ordering", asset_type)
        return None

    await ibkr.ib.qualifyContractsAsync(contract)

    # Build IBKR order object
    action_str = rule.action.type  # "BUY" or "SELL"
    qty = rule.action.quantity

    if rule.action.order_type == "LMT" and rule.action.limit_price is not None:
        ib_order = LimitOrder(action_str, qty, rule.action.limit_price)
    else:
        ib_order = MarketOrder(action_str, qty)

    # Record trade in DB (PENDING status)
    trade_rec = Trade(
        rule_id=rule.id,
        rule_name=rule.name,
        symbol=rule.symbol,
        action=rule.action.type,  # type: ignore[arg-type]
        asset_type=asset_type,
        quantity=qty,
        order_type=rule.action.order_type,
        limit_price=rule.action.limit_price,
        fill_price=None,
        status="PENDING",
        order_id=None,
        timestamp=_now_iso(),
    )
    await save_trade(trade_rec)

    try:
        ib_trade: IBTrade = ibkr.ib.placeOrder(contract, ib_order)
        trade_rec.order_id = ib_trade.order.orderId

        # Update order_id in DB
        await save_trade(trade_rec)

        log.info("Order placed: %s %d %s — order_id=%s",
                 action_str, qty, rule.symbol, trade_rec.order_id)

        # Watch for fill asynchronously
        asyncio.create_task(_watch_fill(ib_trade, trade_rec))

        return trade_rec

    except Exception as exc:
        log.error("Order placement failed for rule '%s': %s", rule.name, exc)
        trade_rec.status = "ERROR"  # type: ignore[assignment]
        await save_trade(trade_rec)
        return trade_rec


async def _watch_fill(ib_trade: IBTrade, trade_rec: Trade, timeout: int = 60) -> None:
    """Poll the IBKR trade object until it fills or times out."""
    elapsed = 0
    while elapsed < timeout:
        await asyncio.sleep(2)
        elapsed += 2
        status = ib_trade.orderStatus.status
        if status == "Filled":
            fill_price = ib_trade.orderStatus.avgFillPrice
            await update_trade_status(trade_rec.id, "FILLED", fill_price)
            trade_rec.status = "FILLED"  # type: ignore[assignment]
            trade_rec.fill_price = fill_price
            log.info("Order FILLED: %s %d %s @ %.4f",
                     trade_rec.action, trade_rec.quantity, trade_rec.symbol, fill_price)
            for cb in _fill_callbacks:
                cb(trade_rec)
            return
        if status in ("Cancelled", "ApiCancelled", "Inactive"):
            await update_trade_status(trade_rec.id, "CANCELLED")
            log.warning("Order cancelled: %s", trade_rec.order_id)
            return

    log.warning("Order %s did not fill within %ds", trade_rec.order_id, timeout)


async def cancel_order(order_id: int) -> bool:
    """Cancel an open order by IBKR order ID."""
    if not ibkr.is_connected():
        return False
    for ib_trade in ibkr.ib.openTrades():
        if ib_trade.order.orderId == order_id:
            ibkr.ib.cancelOrder(ib_trade.order)
            log.info("Cancel requested for order %d", order_id)
            return True
    log.warning("Order %d not found among open trades", order_id)
    return False


async def get_open_orders() -> list[dict]:
    """Return a list of open orders as plain dicts."""
    if not ibkr.is_connected():
        return []
    return [
        {
            "order_id": t.order.orderId,
            "symbol": t.contract.symbol,
            "action": t.order.action,
            "qty": t.order.totalQuantity,
            "order_type": t.order.orderType,
            "limit_price": t.order.lmtPrice if t.order.orderType == "LMT" else None,
            "status": t.orderStatus.status,
        }
        for t in ibkr.ib.openTrades()
    ]

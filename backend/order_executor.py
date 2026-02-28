"""
Order executor — places orders via IBKR and logs them to SQLite.
"""
from __future__ import annotations
import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Callable, Optional
from ib_insync import MarketOrder, LimitOrder, Trade as IBTrade
from ibkr_client import ibkr
from database import save_trade, update_trade_status
from models import Rule, Trade
from config import cfg

log = logging.getLogger(__name__)

# ── Pre-flight order validation ──────────────────────────────────────────────
MAX_ORDER_QTY = 10_000
MIN_PRICE = 0.01
MAX_PRICE = 1_000_000
MIN_ORDER_VALUE = 100  # minimum notional value
DEDUP_WINDOW = 5  # seconds
_recent_orders: dict[str, float] = {}  # "symbol:action" -> timestamp


def _pre_flight_check(rule: Rule, price_estimate: float | None = None) -> str | None:
    """Return error message if order fails pre-flight, else None."""
    qty = rule.action.quantity
    if qty < 1 or qty > MAX_ORDER_QTY:
        return f"Quantity {qty} outside bounds [1, {MAX_ORDER_QTY}]"

    if rule.action.limit_price is not None:
        if not (MIN_PRICE <= rule.action.limit_price <= MAX_PRICE):
            return f"Limit price {rule.action.limit_price} outside [{MIN_PRICE}, {MAX_PRICE}]"

    # Min order value check
    if price_estimate and rule.action.limit_price:
        order_cost = qty * rule.action.limit_price
        if order_cost < MIN_ORDER_VALUE:
            return f"Order value {order_cost:.2f} below minimum {MIN_ORDER_VALUE}"

    # Dedup: reject same symbol+action within DEDUP_WINDOW
    key = f"{rule.symbol}:{rule.action.type}"
    now = time.time()
    last = _recent_orders.get(key)
    if last and (now - last) < DEDUP_WINDOW:
        return f"Duplicate order for {key} within {DEDUP_WINDOW}s"
    _recent_orders[key] = now

    return None

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
    if cfg.IS_PAPER and not cfg.SIM_MODE:
        log.error("[PAPER] IS_PAPER=true but SIM_MODE=False — aborting order placement")
        raise RuntimeError("IS_PAPER=true requires SIM_MODE=True for virtual trading")

    # Pre-flight validation
    err = _pre_flight_check(rule)
    if err:
        log.error("Pre-flight check failed: %s", err)
        return None

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

    if not contract.conId:
        log.error("Contract qualification failed for %s — conId=0", rule.symbol)
        return None

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

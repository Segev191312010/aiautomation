"""
Order executor — places orders via IBKR and logs them to SQLite.
"""
from __future__ import annotations
import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Callable, Optional
from ib_insync import MarketOrder, LimitOrder, StopOrder, Trade as IBTrade
from ibkr_client import ibkr
from database import save_trade, update_trade_status
from models import Rule, Trade
from config import cfg

log = logging.getLogger(__name__)


class OrderError(Exception):
    """Raised when an order cannot be placed."""


# Positions snapshot — updated by bot_runner each cycle for cash-only guard
_current_positions: dict[str, float] = {}


def update_positions_snapshot(positions: list[dict]) -> None:
    global _current_positions
    _current_positions = {p["symbol"]: p.get("qty", 0) for p in positions}


def _compute_atr(bars, fallback_price: float, period: int = 14) -> float:
    """ATR from bars DataFrame. Falls back to 2% of price."""
    if bars is None or len(bars) < period + 1:
        return fallback_price * 0.02
    try:
        import pandas as pd
        h, l, c = bars["high"], bars["low"], bars["close"]
        tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
        val = float(tr.rolling(period).mean().iloc[-1])
        return val if val > 0 else fallback_price * 0.02
    except Exception:
        return fallback_price * 0.02


# ── Pre-flight order validation ──────────────────────────────────────────────
MAX_ORDER_QTY = 10_000
MIN_PRICE = 0.01
MAX_PRICE = 1_000_000
MIN_ORDER_VALUE = 100  # minimum notional value
DEDUP_WINDOW = 60  # seconds — prevent duplicate fills in slow markets
_recent_orders: dict[str, float] = {}  # "symbol:action" -> timestamp


def _pre_flight_check(rule: Rule, price_estimate: float | None = None) -> str | None:
    """Return error message if order fails pre-flight, else None."""
    qty = rule.action.quantity
    if qty < 1 or qty > MAX_ORDER_QTY:
        return f"Quantity {qty} outside bounds [1, {MAX_ORDER_QTY}]"

    if rule.action.limit_price is not None:
        if not (MIN_PRICE <= rule.action.limit_price <= MAX_PRICE):
            return f"Limit price {rule.action.limit_price} outside [{MIN_PRICE}, {MAX_PRICE}]"

    # Cash-only guard: reject SELL if no long position held
    if rule.action.type == "SELL" and not cfg.SHORT_ALLOWED:
        held = _current_positions.get(rule.symbol, 0)
        if held <= 0:
            return f"SELL rejected for {rule.symbol}: no long position (cash account)"

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


async def place_order(rule: Rule, bars=None) -> Optional[Trade]:
    """
    Place an order for the given rule's action.

    Returns the Trade record (status PENDING), or None on failure.
    """
    if cfg.SIM_MODE:
        log.error("[SIM] SIM_MODE=true — no IBKR connection, cannot place live order")
        raise RuntimeError("SIM_MODE=true: use simulation endpoints instead")

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

    # Regular hours only, DAY orders (re-evaluate daily, don't hold stale orders)
    ib_order.outsideRth = False
    ib_order.tif = "DAY"

    # Multi-account: must set account on every order
    accounts = ibkr.ib.managedAccounts()
    if accounts:
        ib_order.account = accounts[0]

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
        asyncio.create_task(_watch_fill(ib_trade, trade_rec, contract, rule, bars=bars))

        return trade_rec

    except Exception as exc:
        log.error("Order placement failed for rule '%s': %s", rule.name, exc)
        trade_rec.status = "ERROR"  # type: ignore[assignment]
        await save_trade(trade_rec)
        return trade_rec


async def _watch_fill(ib_trade: IBTrade, trade_rec: Trade, contract, rule: Rule, timeout: int = 60, bars=None) -> None:
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

            # Notify via WebSocket
            try:
                from notification_service import notification_service
                asyncio.create_task(notification_service.notify_order_filled({
                    "symbol": trade_rec.symbol,
                    "action": trade_rec.action,
                    "qty": trade_rec.quantity,
                    "fill_price": fill_price,
                    "rule_name": trade_rec.rule_name,
                    "trade_id": trade_rec.id,
                }))
            except Exception:
                pass

            # -- ATR-based bracket orders (2×ATR stop, 2.2:1 R/R) --
            if trade_rec.action == "BUY" and trade_rec.fill_price:
                fill = trade_rec.fill_price
                atr = _compute_atr(bars, fill)
                sl_dist = 2.0 * atr
                tp_dist = 2.2 * sl_dist  # 2.2:1 reward/risk
                sl_price = round(fill - sl_dist, 2)
                tp_price = round(fill + tp_dist, 2)
                log.info("ATR=%.2f → SL=%.2f (-%s) TP=%.2f (+%s)",
                         atr, sl_price, f"{sl_dist:.2f}", tp_price, f"{tp_dist:.2f}")
                oca_group = f"OCA_{trade_rec.id[:8]}"

                sl_order = StopOrder("SELL", trade_rec.quantity, sl_price)
                sl_order.outsideRth = True
                sl_order.tif = "GTC"
                sl_order.ocaGroup = oca_group
                sl_order.ocaType = 1  # cancel all others when one fills

                tp_order = LimitOrder("SELL", trade_rec.quantity, tp_price)
                tp_order.outsideRth = True
                tp_order.tif = "GTC"
                tp_order.ocaGroup = oca_group
                tp_order.ocaType = 1

                accounts = ibkr.ib.managedAccounts()
                if accounts:
                    sl_order.account = accounts[0]
                    tp_order.account = accounts[0]

                try:
                    ibkr.ib.placeOrder(contract, sl_order)
                    ibkr.ib.placeOrder(contract, tp_order)
                    log.info(
                        "Bracket placed for %s: SL=$%.2f TP=$%.2f",
                        rule.symbol, sl_price, tp_price,
                    )
                except Exception as e:
                    log.error("Failed to place bracket for %s: %s", rule.symbol, e)

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

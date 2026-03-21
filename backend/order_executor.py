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


class OrderError(Exception):
    """Raised when an order cannot be placed."""


async def _get_limit_price(symbol: str, action: str, slip_pct: float = 0.005,
                           contract=None) -> float | None:
    """
    Get a limit price for extended-hours order placement.
    Returns last price + 0.5% for BUY, - 0.5% for SELL.
    Uses IBKR snapshot first (no rate-limit risk), falls back to yfinance.
    """
    # Try IBKR snapshot ticker
    try:
        from ib_insync import Stock
        c = contract or ibkr.make_stock_contract(symbol)
        [ticker] = await asyncio.wait_for(
            ibkr.ib.reqTickersAsync(c), timeout=5
        )
        price = ticker.last or ticker.close or ticker.bid or ticker.ask
        if price and price > 0:
            multiplier = 1 + slip_pct if action == "BUY" else 1 - slip_pct
            return round(float(price) * multiplier, 2)
    except Exception:
        pass

    # Fall back to yfinance
    try:
        import yfinance as yf
        info = await asyncio.get_running_loop().run_in_executor(
            None, lambda: yf.Ticker(symbol).fast_info
        )
        price = getattr(info, "last_price", None) or getattr(info, "regular_market_price", None)
        if price and price > 0:
            multiplier = 1 + slip_pct if action == "BUY" else 1 - slip_pct
            return round(float(price) * multiplier, 2)
    except Exception:
        pass
    return None


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
    # C-3 FIX: Evict stale entries to prevent unbounded growth
    stale = [k for k, v in _recent_orders.items() if (now - v) > DEDUP_WINDOW * 2]
    for k in stale:
        del _recent_orders[k]
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
    if cfg.SIM_MODE:
        log.error("[SIM] SIM_MODE=true — no IBKR connection, cannot place live order")
        raise RuntimeError("SIM_MODE=true: use simulation endpoints instead")

    # Pre-flight validation
    err = _pre_flight_check(rule)
    if err:
        log.error("Pre-flight check failed: %s", err)
        return None

    # Safety kernel — hard runtime checks (kill switch, daily loss, risk, dedup)
    try:
        from safety_kernel import check_all, SafetyViolation
        await check_all(
            symbol=rule.symbol,
            side=rule.action.type,
            quantity=rule.action.quantity,
            source="rule",
        )
    except SafetyViolation as exc:
        log.warning("Safety kernel REJECTED order: %s", exc)
        return None
    except Exception as exc:
        log.debug("Safety kernel check skipped: %s", exc)

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
        # IBKR rejects MKT orders outside regular hours — use LIMIT at current price
        limit_px = await _get_limit_price(rule.symbol, action_str)
        if limit_px:
            ib_order = LimitOrder(action_str, qty, limit_px)
            log.info("MKT→LIMIT conversion: %s %s lmt=%.4f (extended hours)", action_str, rule.symbol, limit_px)
        else:
            ib_order = MarketOrder(action_str, qty)

    # Extended hours + GTC so orders work outside regular trading hours
    ib_order.outsideRth = True
    ib_order.tif = "GTC"

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
        asyncio.create_task(_watch_fill(ib_trade, trade_rec, contract, rule))

        return trade_rec

    except Exception as exc:
        log.error("Order placement failed for rule '%s': %s", rule.name, exc)
        trade_rec.status = "ERROR"  # type: ignore[assignment]
        await save_trade(trade_rec)
        return trade_rec


async def _watch_fill(ib_trade: IBTrade, trade_rec: Trade, contract, rule: Rule | None = None, timeout: int = 60) -> None:
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

            # Exit management is handled by position_tracker (ATR stops + MA exits)
            log.info("Fill ready for exit tracker: %s %s @ %.4f",
                     trade_rec.action, trade_rec.symbol, fill_price)
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


async def reconcile_pending_orders() -> None:
    """
    Called once on startup. Subscribes to IBKR orderStatusEvent so that
    any PENDING orders in the DB are updated when IBKR reports a fill or
    cancellation — even if the fill happened while the server was down.
    """
    from database import get_trades, update_trade_status

    if not ibkr.is_connected():
        log.warning("reconcile_pending_orders: IBKR not connected, skipping")
        return

    # Load our PENDING trades and build a lookup by IBKR order_id
    pending = [t for t in await get_trades(limit=500) if t.status == "PENDING" and t.order_id]
    if not pending:
        return

    pending_by_oid: dict[int, Trade] = {t.order_id: t for t in pending}
    log.info("Reconciling %d PENDING order(s) with IBKR", len(pending))

    # Check current IBKR open trades for immediate status
    for ib_trade in ibkr.ib.openTrades():
        oid = ib_trade.order.orderId
        if oid not in pending_by_oid:
            continue
        rec = pending_by_oid[oid]
        status = ib_trade.orderStatus.status
        if status == "Filled":
            fill_price = ib_trade.orderStatus.avgFillPrice
            await update_trade_status(rec.id, "FILLED", fill_price)
            log.info("Reconciled FILLED: %s %s @ %.4f", rec.action, rec.symbol, fill_price)
        elif status in ("Cancelled", "ApiCancelled", "Inactive"):
            await update_trade_status(rec.id, "CANCELLED")
            log.info("Reconciled CANCELLED: %s %s", rec.action, rec.symbol)

    # Subscribe to live events for orders still open in IBKR
    def _on_order_status(ib_trade: IBTrade) -> None:
        oid = ib_trade.order.orderId
        if oid not in pending_by_oid:
            return
        rec = pending_by_oid[oid]
        status = ib_trade.orderStatus.status
        if status == "Filled":
            fill_price = ib_trade.orderStatus.avgFillPrice
            asyncio.create_task(_handle_fill(rec, fill_price))
        elif status in ("Cancelled", "ApiCancelled", "Inactive"):
            asyncio.create_task(update_trade_status(rec.id, "CANCELLED"))
            log.info("Order CANCELLED via event: %s %s", rec.action, rec.symbol)

    ibkr.ib.orderStatusEvent += _on_order_status
    log.info("Subscribed to IBKR orderStatusEvent for pending order reconciliation")

    # Convert any stuck MKT orders to LIMIT so they can trade pre-market
    await _convert_mkt_orders_to_limit()


async def _convert_mkt_orders_to_limit() -> None:
    """
    Cancel stuck MKT orders and resubmit as LIMIT so they can execute
    during pre-market / after-hours (IBKR rejects MKT→LMT type changes).
    """
    from database import get_trades
    if not ibkr.is_connected():
        return

    pending_db = {t.order_id: t for t in await get_trades(limit=500)
                  if t.status == "PENDING" and t.order_id}

    resubmitted = 0
    for ib_trade in list(ibkr.ib.openTrades()):
        order = ib_trade.order
        status = ib_trade.orderStatus.status
        if order.orderType != "MKT" or status not in ("PreSubmitted", "Submitted"):
            continue

        symbol = ib_trade.contract.symbol
        action = order.action
        qty = order.totalQuantity

        limit_px = await _get_limit_price(symbol, action, contract=ib_trade.contract)
        if not limit_px:
            log.warning("Cannot resubmit %s %s as LIMIT — no price available", action, symbol)
            continue

        # Cancel the existing MKT order
        ibkr.ib.cancelOrder(order)
        await asyncio.sleep(0.5)

        # Place fresh LIMIT order
        from ib_insync import LimitOrder as _LimitOrder
        new_order = _LimitOrder(action, qty, limit_px)
        new_order.outsideRth = True
        new_order.tif = "GTC"
        new_ib_trade = ibkr.ib.placeOrder(ib_trade.contract, new_order)
        log.info("Resubmitted %s %s as LIMIT lmt=%.2f (was MKT order %d)",
                 action, symbol, limit_px, order.orderId)

        # Update DB: mark old trade cancelled, save new order_id
        if order.orderId in pending_db:
            old_rec = pending_db[order.orderId]
            await update_trade_status(old_rec.id, "CANCELLED")
            old_rec.order_id = new_ib_trade.order.orderId
            old_rec.status = "PENDING"  # type: ignore[assignment]
            old_rec.order_type = "LMT"
            old_rec.limit_price = limit_px
            await save_trade(old_rec)
            asyncio.create_task(_watch_fill(new_ib_trade, old_rec, ib_trade.contract, None))

        resubmitted += 1

    if resubmitted:
        log.info("Resubmitted %d MKT order(s) as LIMIT for extended hours trading", resubmitted)


async def _handle_fill(trade_rec: Trade, fill_price: float) -> None:
    """Process a fill from IBKR event — update DB and fire callbacks."""
    from database import update_trade_status
    await update_trade_status(trade_rec.id, "FILLED", fill_price)
    trade_rec.status = "FILLED"  # type: ignore[assignment]
    trade_rec.fill_price = fill_price
    log.info("Order FILLED (reconciled): %s %d %s @ %.4f",
             trade_rec.action, trade_rec.quantity, trade_rec.symbol, fill_price)
    for cb in _fill_callbacks:
        cb(trade_rec)


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

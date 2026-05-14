"""
Order executor — places orders via IBKR and logs them to SQLite.
"""
from __future__ import annotations
import asyncio
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional
from ib_insync import MarketOrder, LimitOrder, Trade as IBTrade
from ibkr_client import ibkr
from database import save_trade, update_trade_status
from market_data import get_latest_price, finite_positive
from models import Rule, Trade
from config import cfg
from services import order_lifecycle, order_recovery, safety_gate

log = logging.getLogger(__name__)

# Track active fill watcher tasks for monitoring
_active_watch_tasks: dict[str, asyncio.Task] = {}

# Single time source for the rate-cap window and the orphan reaper. All
# timestamp comparisons must go through these helpers — no mixing
# time.time() with ISO strings or naive datetime.now() (Batch 5b invariant).
def _now_ts() -> float:
    """Unix seconds, UTC-anchored via time.time() (POSIX timestamp)."""
    return time.time()


def _now_utc() -> datetime:
    """tz-aware datetime in UTC."""
    return datetime.now(timezone.utc)


# Per-symbol order-rate cap. The existing _recent_orders dedup is keyed by
# `symbol:action`, so a buggy rule that alternates BUY/SELL/BUY/SELL for one
# symbol slips through. This window is keyed by symbol only and caps the
# total submissions per 60 seconds regardless of action. Initial cap is
# generous (6/min); tighten via env once we see the WARN line fire.
#
# This is a per-process counter — a multi-worker deployment would need a
# cross-process limiter (Redis / DB counter); flagged for the next batch.
_order_rate_window: dict[str, list[float]] = {}
_order_rate_lock = asyncio.Lock()
MAX_ORDERS_PER_SYMBOL_PER_MIN = int(os.getenv("MAX_ORDERS_PER_SYMBOL_PER_MIN", "6"))


async def _check_and_record_rate_cap(symbol: str) -> bool:
    """Return True if the order is permitted; False if it exceeds the cap.

    Held under a single lock so two concurrent place_order calls cannot both
    pass the check and append a new timestamp (TOCTOU). The window is also
    evicted of timestamps older than 60s on every call.
    """
    sym = symbol.upper()
    now = _now_ts()
    async with _order_rate_lock:
        window = _order_rate_window.setdefault(sym, [])
        # Evict stale entries (older than 60s)
        cutoff = now - 60.0
        window[:] = [ts for ts in window if ts >= cutoff]
        if len(window) >= MAX_ORDERS_PER_SYMBOL_PER_MIN:
            log.warning(
                "order_rate_cap_exceeded: symbol=%s window=%d cap=%d",
                sym, len(window), MAX_ORDERS_PER_SYMBOL_PER_MIN,
            )
            return False
        window.append(now)
        return True


def _safe_create_task(coro, *, name: str = "") -> asyncio.Task:
    """Create an asyncio task with error logging callback. Never silently lose exceptions."""
    task = asyncio.create_task(coro)
    def _on_done(t: asyncio.Task) -> None:
        _active_watch_tasks.pop(name, None)
        if t.cancelled():
            return
        exc = t.exception()
        if exc:
            log.critical("Background task '%s' CRASHED: %s", name, exc, exc_info=exc)
    task.add_done_callback(_on_done)
    if name:
        _active_watch_tasks[name] = task
    return task


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
        # NaN is truthy in Python, so the original `a or b or c or d` chain
        # short-circuits to NaN on the first NaN-valued field and never tries
        # the fallbacks. Filter each candidate explicitly.
        for candidate in (ticker.last, ticker.close, ticker.bid, ticker.ask):
            price = finite_positive(candidate)
            if price is not None:
                multiplier = 1 + slip_pct if action == "BUY" else 1 - slip_pct
                return round(price * multiplier, 2)
    except Exception:
        pass

    # Fall back to yfinance
    try:
        import yfinance as yf
        info = await asyncio.get_running_loop().run_in_executor(
            None, lambda: yf.Ticker(symbol).fast_info
        )
        for candidate in (
            getattr(info, "last_price", None),
            getattr(info, "regular_market_price", None),
        ):
            price = finite_positive(candidate)
            if price is not None:
                multiplier = 1 + slip_pct if action == "BUY" else 1 - slip_pct
                return round(price * multiplier, 2)
    except Exception:
        pass
    return None


# ── Pre-flight order validation ──────────────────────────────────────────────
MAX_ORDER_QTY = 10_000
MIN_PRICE = 0.01
MAX_PRICE = 1_000_000
MIN_ORDER_VALUE = 100  # minimum notional value
DEDUP_WINDOW = max(10, cfg.BOT_INTERVAL_SECONDS * 2)  # at least 2x bot interval
_recent_orders: dict[str, float] = {}  # "symbol:action" -> timestamp


def _pre_flight_check(rule: Rule, price_estimate: float | None = None, *, is_exit: bool = False) -> str | None:
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

    if not is_exit:
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


async def place_order(
    rule: Rule,
    *,
    source: str = "rule",
    skip_safety: bool = False,
    require_autopilot_authority: bool = True,
    stop_price: float | None = None,
    is_exit: bool = False,
    has_existing_position: bool = False,
) -> Optional[Trade]:
    """
    Place an order for the given rule's action.

    Returns the Trade record (status PENDING), or None on failure.
    """
    if cfg.SIM_MODE:
        log.error("[SIM] SIM_MODE=true — no IBKR connection, cannot place live order")
        raise RuntimeError("SIM_MODE=true: use simulation endpoints instead")

    # Pre-flight validation
    err = _pre_flight_check(rule, is_exit=is_exit)
    if err:
        log.error("Pre-flight check failed: %s", err)
        return None

    # Per-symbol order-rate cap (Batch 5b). Stricter than the BUY/SELL-keyed
    # dedup: a BUY/SELL/BUY/SELL flap on one symbol must not slip through.
    # Exits are exempt — emergency exits can come in bursts.
    if not is_exit:
        permitted = await _check_and_record_rate_cap(rule.symbol)
        if not permitted:
            return None

    price_estimate = rule.action.limit_price
    if price_estimate is None:
        try:
            price_estimate = await get_latest_price(rule.symbol)
        except Exception:
            price_estimate = None

    account_equity = 0.0
    try:
        acct = await ibkr.get_account_summary()
        account_equity = float(acct.balance) if acct else 0.0
    except Exception:
        account_equity = 0.0
    if not skip_safety:
        allowed, reason = await safety_gate.evaluate_runtime_safety(
            symbol=rule.symbol,
            side=rule.action.type,
            quantity=rule.action.quantity,
            source=source,
            account_equity=account_equity,
            price_estimate=float(price_estimate or 0.0),
            stop_price=stop_price,
            is_exit=is_exit,
            has_existing_position=has_existing_position,
            require_autopilot_authority=require_autopilot_authority,
        )
        if not allowed:
            log.warning("Runtime safety gate REJECTED order: %s", reason)
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
    now_iso = _now_iso()
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
        timestamp=now_iso,
        mode="LIVE",
        opened_at=now_iso,
    )
    trade_rec.position_id = trade_rec.id
    await save_trade(trade_rec)

    try:
        ib_trade: IBTrade = ibkr.ib.placeOrder(contract, ib_order)
        trade_rec.order_id = ib_trade.order.orderId

        # Update order_id in DB
        await save_trade(trade_rec)

        log.info("Order placed: %s %d %s — order_id=%s",
                 action_str, qty, rule.symbol, trade_rec.order_id)

        # Watch for fill asynchronously (with error tracking)
        _safe_create_task(
            _watch_fill(ib_trade, trade_rec, contract, rule),
            name=f"fill_watch:{trade_rec.symbol}:{trade_rec.order_id}",
        )

        return trade_rec

    except Exception as exc:
        log.error("Order placement failed for rule '%s': %s", rule.name, exc)
        trade_rec.status = "ERROR"  # type: ignore[assignment]
        await save_trade(trade_rec)
        return trade_rec


async def _watch_fill(ib_trade: IBTrade, trade_rec: Trade, contract, rule: Rule | None = None, timeout: int = 120) -> None:
    """Poll the IBKR trade object until it fills or times out."""
    elapsed = 0
    while elapsed < timeout:
        await asyncio.sleep(2)
        elapsed += 2
        status = ib_trade.orderStatus.status

        # Detect partial fills
        filled_qty = getattr(ib_trade.orderStatus, 'filled', 0) or 0
        total_qty = getattr(ib_trade.order, 'totalQuantity', 0) or trade_rec.quantity
        if 0 < filled_qty < total_qty and status not in ("Filled",):
            log.warning(
                "Partial fill detected: %d/%d for %s (order %s) — waiting for completion",
                filled_qty, total_qty, trade_rec.symbol, trade_rec.order_id,
            )

        resolved = await order_recovery.reconcile_trade_status_update(
            trade_rec,
            status,
            fill_price=ib_trade.orderStatus.avgFillPrice,
            fill_callbacks=_fill_callbacks,
        )
        if resolved == "FILLED":
            # Verify actual filled quantity matches expected
            actual_filled = int(getattr(ib_trade.orderStatus, 'filled', 0) or trade_rec.quantity)
            if actual_filled != trade_rec.quantity and actual_filled > 0:
                log.warning(
                    "Partial fill finalized: %d/%d shares for %s — updating trade quantity",
                    actual_filled, trade_rec.quantity, trade_rec.symbol,
                )
                trade_rec.quantity = actual_filled
                await save_trade(trade_rec)

            # INVARIANT: do not propagate a non-finite fill price. If the broker
            # returned NaN avgFillPrice (and the recovery path didn't already
            # supply a finite one), mark the trade ERROR and SKIP _handle_fill
            # entirely. order_recovery treats this case as "avg_fill_non_finite".
            fill_price = finite_positive(trade_rec.fill_price) or finite_positive(ib_trade.orderStatus.avgFillPrice)
            if fill_price is None:
                log.critical(
                    "avg_fill_non_finite: trade=%s symbol=%s broker_avg=%r — marking ERROR, "
                    "not calling _handle_fill (no fill inference from positions)",
                    trade_rec.id, trade_rec.symbol, ib_trade.orderStatus.avgFillPrice,
                )
                trade_rec.status = "ERROR"  # type: ignore[assignment]
                await save_trade(trade_rec)
                return
            log.info("Order FILLED: %s %d %s @ %.4f",
                     trade_rec.action, trade_rec.quantity, trade_rec.symbol, fill_price)

            try:
                from notification_service import notification_service
                _safe_create_task(notification_service.notify_order_filled({
                    "symbol": trade_rec.symbol,
                    "action": trade_rec.action,
                    "qty": trade_rec.quantity,
                    "fill_price": fill_price,
                    "rule_name": trade_rec.rule_name,
                    "trade_id": trade_rec.id,
                }), name=f"notify_fill:{trade_rec.symbol}")
            except Exception:
                pass

            log.info("Fill ready for exit tracker: %s %s @ %.4f",
                     trade_rec.action, trade_rec.symbol, fill_price)
            return
        if resolved in {"CANCELLED", "ERROR"}:
            # Check if there was a partial fill before cancellation
            partial = int(getattr(ib_trade.orderStatus, 'filled', 0) or 0)
            if partial > 0:
                partial_fill_price = finite_positive(ib_trade.orderStatus.avgFillPrice)
                if partial_fill_price is None:
                    log.critical(
                        "avg_fill_non_finite (partial-on-cancel): trade=%s symbol=%s "
                        "broker_avg=%r — marking ERROR, not recording partial",
                        trade_rec.id, trade_rec.symbol, ib_trade.orderStatus.avgFillPrice,
                    )
                    trade_rec.status = "ERROR"  # type: ignore[assignment]
                    await save_trade(trade_rec)
                    return
                log.warning(
                    "Order %s cancelled with partial fill: %d/%d shares — recording partial",
                    trade_rec.order_id, partial, trade_rec.quantity,
                )
                trade_rec.quantity = partial
                trade_rec.status = "FILLED"  # type: ignore[assignment]
                trade_rec.fill_price = partial_fill_price
                await save_trade(trade_rec)
                for cb in _fill_callbacks:
                    cb(trade_rec)
                return
            log.warning("Order resolved without fill: order_id=%s status=%s", trade_rec.order_id, resolved)
            return

    # Timeout — query broker directly to check actual state
    log.warning("Order %s did not fill within %ds — checking broker state", trade_rec.order_id, timeout)
    try:
        for open_trade in ibkr.ib.openTrades():
            if open_trade.order.orderId == trade_rec.order_id:
                final_status = open_trade.orderStatus.status
                if order_recovery.normalize_trade_status(final_status) == "FILLED":
                    broker_fill = finite_positive(open_trade.orderStatus.avgFillPrice)
                    if broker_fill is None:
                        log.critical(
                            "avg_fill_non_finite (broker-state-resync): trade=%s symbol=%s "
                            "broker_avg=%r — leaving trade unresolved",
                            trade_rec.id, trade_rec.symbol, open_trade.orderStatus.avgFillPrice,
                        )
                        return
                    log.critical("Order %s actually FILLED at broker — syncing now", trade_rec.order_id)
                    await _handle_fill(trade_rec, broker_fill)
                    return
                break
    except Exception as exc:
        log.error("Failed to query broker for order %s state: %s", trade_rec.order_id, exc)


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


async def reap_orphan_pending_trades(stale_after_seconds: int = 600) -> int:
    """Reap PENDING trade rows with no order_id older than the threshold.

    A process crash between ``save_trade(PENDING)`` and ``ibkr.ib.placeOrder``
    leaves a DB row that ``reconcile_pending_orders`` cannot resolve — it
    only looks at IBKR's open trades, but no broker order was ever sent.
    On startup we sweep these rows, mark them ERROR with reason
    ``orphan_pending_reaped``, and emit a WARN so the operator can investigate.

    Time comparison uses the single ``_now_utc()`` helper to avoid any
    naive/aware mismatch with ISO timestamps stored in the trade row.
    """
    from database import get_trades

    threshold = _now_utc() - timedelta(seconds=stale_after_seconds)
    reaped = 0
    try:
        recent = await get_trades(limit=500)
    except Exception as exc:
        log.error("orphan reaper: get_trades failed: %s", exc)
        return 0

    for trade in recent:
        if trade.status != "PENDING":
            continue
        if trade.order_id is not None:
            continue  # has a broker order — reconcile_pending_orders handles it
        try:
            ts = datetime.fromisoformat(trade.timestamp.replace("Z", "+00:00"))
        except (ValueError, TypeError, AttributeError):
            # Malformed timestamp — reap defensively
            ts = threshold - timedelta(seconds=1)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if ts >= threshold:
            continue
        log.warning(
            "orphan_pending_reaped: trade=%s symbol=%s action=%s timestamp=%s "
            "(no order_id, older than %ds)",
            trade.id, trade.symbol, trade.action, trade.timestamp, stale_after_seconds,
        )
        try:
            await update_trade_status(trade.id, "ERROR")
            reaped += 1
        except Exception as exc:
            log.error("orphan reaper: failed to mark %s as ERROR: %s", trade.id, exc)

    if reaped:
        log.warning("orphan reaper: marked %d PENDING-without-order_id trade(s) as ERROR", reaped)
    return reaped


async def reconcile_pending_orders() -> None:
    """
    Called once on startup. Subscribes to IBKR orderStatusEvent so that
    any PENDING orders in the DB are updated when IBKR reports a fill or
    cancellation - even if the fill happened while the server was down.
    """
    from database import get_trades

    if not ibkr.is_connected():
        log.warning("reconcile_pending_orders: IBKR not connected, skipping")
        return

    pending = [t for t in await get_trades(limit=500) if t.status == "PENDING" and t.order_id]
    if not pending:
        return

    pending_by_oid: dict[int, Trade] = {t.order_id: t for t in pending}
    log.info("Reconciling %d PENDING order(s) with IBKR", len(pending))

    for ib_trade in ibkr.ib.openTrades():
        oid = ib_trade.order.orderId
        if oid not in pending_by_oid:
            continue
        rec = pending_by_oid[oid]
        resolved = await order_recovery.reconcile_trade_status_update(
            rec,
            ib_trade.orderStatus.status,
            fill_price=ib_trade.orderStatus.avgFillPrice,
            fill_callbacks=_fill_callbacks,
        )
        if resolved == "FILLED":
            log.info("Reconciled FILLED: %s %s @ %.4f", rec.action, rec.symbol, float(rec.fill_price or 0.0))
        elif resolved in {"CANCELLED", "ERROR"}:
            log.info("Reconciled %s: %s %s", resolved, rec.action, rec.symbol)

    def _on_order_status(ib_trade: IBTrade) -> None:
        oid = ib_trade.order.orderId
        if oid not in pending_by_oid:
            return
        rec = pending_by_oid[oid]
        status = ib_trade.orderStatus.status
        normalized = order_recovery.normalize_trade_status(status)
        if normalized == "FILLED":
            fill_price = ib_trade.orderStatus.avgFillPrice
            async def _safe_handle_fill_event():
                from database import get_trade
                fresh = await get_trade(rec.id)
                if not fresh:
                    log.warning("Trade %s deleted before fill event — ignoring", rec.id)
                    return
                if fresh.status == "FILLED":
                    log.debug("Trade %s already FILLED — skipping duplicate event", rec.id)
                    return
                await _handle_fill(fresh, fill_price)
            _safe_create_task(_safe_handle_fill_event(), name=f"event_fill:{rec.symbol}:{oid}")
        elif normalized in {"CANCELLED", "ERROR"}:
            _safe_create_task(
                order_recovery.reconcile_trade_status_update(rec, status),
                name=f"event_resolve:{rec.symbol}:{oid}",
            )
            log.info("Order resolved via event: %s %s -> %s", rec.action, rec.symbol, normalized)

    ibkr.ib.orderStatusEvent += _on_order_status
    log.info("Subscribed to IBKR orderStatusEvent for pending order reconciliation")

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

        # Cancel the existing MKT order and wait for confirmation
        ibkr.ib.cancelOrder(order)
        # Wait for cancellation to be confirmed (up to 5s)
        for _wait in range(10):
            await asyncio.sleep(0.5)
            cancel_status = ib_trade.orderStatus.status
            if cancel_status in ("Cancelled", "ApiCancelled", "Inactive"):
                break
            if cancel_status == "Filled":
                log.info("MKT order %d filled during cancel wait — skipping resubmit", order.orderId)
                break
        else:
            log.warning("MKT order %d cancel not confirmed after 5s — skipping resubmit", order.orderId)
            continue

        # If the original order was filled during cancellation, skip resubmit
        if ib_trade.orderStatus.status == "Filled":
            if order.orderId in pending_db:
                rec = pending_db[order.orderId]
                await order_recovery.reconcile_trade_status_update(
                    rec, "Filled",
                    fill_price=ib_trade.orderStatus.avgFillPrice,
                    fill_callbacks=_fill_callbacks,
                )
            continue

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
            _safe_create_task(
                _watch_fill(new_ib_trade, old_rec, ib_trade.contract, None),
                name=f"fill_watch_resubmit:{symbol}:{new_ib_trade.order.orderId}",
            )

        resubmitted += 1

    if resubmitted:
        log.info("Resubmitted %d MKT order(s) as LIMIT for extended hours trading", resubmitted)


async def _handle_fill(trade_rec: Trade, fill_price: float) -> None:
    """Process a fill from IBKR event - update DB and fire callbacks."""
    await order_recovery.reconcile_trade_status_update(
        trade_rec,
        "Filled",
        fill_price=fill_price,
        fill_callbacks=_fill_callbacks,
    )
    log.info("Order FILLED (reconciled): %s %d %s @ %.4f",
             trade_rec.action, trade_rec.quantity, trade_rec.symbol, fill_price)


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






"""
Bot exit lifecycle — hardened exit processing, order reconciliation, retry caps.

Extracted from bot_runner.py. Positions are NEVER deleted until exit is FILLED.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Callable, Optional

from models import Rule, TradeAction

log = logging.getLogger(__name__)

MAX_EXIT_ATTEMPTS = 3
EXIT_PENDING_TIMEOUT = 90  # seconds

# Symbols that exited this cycle — prevents same-cycle re-entry churn
_exited_this_cycle: set[str] = set()

# Broadcast callback — set by bot_runner.set_exit_broadcast()
_broadcast: Optional[Callable] = None


def set_broadcast(fn: Callable) -> None:
    global _broadcast
    _broadcast = fn


def clear_exited_this_cycle() -> None:
    _exited_this_cycle.clear()


def was_exited_this_cycle(symbol: str) -> bool:
    return symbol.upper() in _exited_this_cycle


async def _emit(payload: dict) -> None:
    if _broadcast:
        await _broadcast(payload)


async def _process_exits(open_positions: list, bars_by_symbol: dict) -> None:
    """
    Hardened exit lifecycle — position NEVER deleted until exit is FILLED.

    Order of operations:
      1. Update watermarks
      2. Reconcile any pending exit orders (FILLED/CANCELLED/PENDING timeout)
      3. Evaluate positions for new exits (only if no pending exit)
      4. Retry cap: max 3 attempts, then notify for manual intervention
    """
    if not open_positions:
        return

    from database import save_open_position
    from position_tracker import update_watermarks

    for pos in update_watermarks(open_positions, bars_by_symbol):
        await save_open_position(pos)

    for pos in open_positions:
        sym = pos.symbol.upper()

        if pos.exit_pending_order_id:
            await _reconcile_pending_exit(pos)
            continue

        if pos.exit_attempts >= MAX_EXIT_ATTEMPTS:
            continue

        from position_tracker import check_exits

        df = bars_by_symbol.get(sym)
        if df is None:
            try:
                from market_data import get_historical_bars
                df = await get_historical_bars(sym, duration="60 D", bar_size="1D")
            except Exception as exc:
                log.warning("Cannot fetch bars for exit check %s: %s", sym, exc)
                continue
        if df is None or len(df) < 2:
            continue

        current_price = float(df["close"].iloc[-1])
        should_exit, reason = check_exits(pos, df, current_price)
        if not should_exit:
            continue

        qty = int(pos.quantity)
        if qty < 1:
            from database import delete_open_position
            log.warning("Position %s has qty=%s (<1) — removing from tracker", pos.symbol, pos.quantity)
            await _emit({"type": "exit", "symbol": pos.symbol, "reason": "qty_below_1",
                         "action": "SELL" if pos.side == "BUY" else "BUY",
                         "qty": 0, "entry_price": pos.entry_price, "exit_price": 0, "pnl": 0})
            await delete_open_position(pos.id)
            continue

        await _place_exit_order(pos, sym, qty, current_price, reason)


async def _reconcile_pending_exit(pos) -> None:
    """Resolve a position's pending exit order."""
    from database import get_trade_by_order_id

    now = datetime.now(timezone.utc)
    trade = await get_trade_by_order_id(pos.exit_pending_order_id, symbol=pos.symbol)
    resolution = order_recovery.evaluate_pending_exit_resolution(
        pos,
        trade,
        now=now,
        timeout_seconds=EXIT_PENDING_TIMEOUT,
    )

    if resolution.state == "filled" and trade is not None:
        order_recovery.clear_pending_exit(pos)
        await save_open_position(pos)

        current_price = trade.fill_price or pos.entry_price
        finalized = await order_lifecycle.finalize_filled_exit_trade(
            trade,
            pos,
            close_reason="pending_fill",
            fallback_exit_price=current_price,
        )
        pnl = finalized.realized_pnl if finalized else round((current_price - pos.entry_price) * pos.quantity, 2)

        await _emit({
            "type": "exit", "symbol": pos.symbol, "reason": "pending_fill",
            "action": "SELL" if pos.side == "BUY" else "BUY",
            "qty": int(pos.quantity), "entry_price": pos.entry_price,
            "exit_price": current_price, "pnl": pnl,
        })
        log.info("EXIT FILLED %s pnl=%.2f (reconciled pending)", pos.symbol, pnl)
        _exited_this_cycle.add(pos.symbol.upper())
        return

    if resolution.state == "retry":
        if resolution.should_cancel and pos.exit_pending_order_id:
            try:
                from order_executor import cancel_order

                await cancel_order(pos.exit_pending_order_id)
                log.warning("Cancelled timed-out exit order %d for %s", pos.exit_pending_order_id, pos.symbol)
            except Exception as exc:
                log.warning("Failed to cancel exit order %d: %s", pos.exit_pending_order_id, exc)
        order_recovery.mark_exit_retry_state(pos, resolution.reason or "Exit reconciliation failed", now=now)
        await save_open_position(pos)
        log.warning("Exit retry needed for %s - attempt %d: %s", pos.symbol, pos.exit_attempts, resolution.reason)
        await _check_retry_cap(pos)
        return

    log.debug("Exit pending for %s (waiting)", pos.symbol)


async def _place_exit_order(pos, sym: str, qty: int, current_price: float, reason: str) -> None:
    """Place a fresh exit order and track it on the position."""
    exit_action = "SELL" if pos.side == "BUY" else "BUY"
    exit_rule = Rule(
        id=pos.rule_id,
        name=f"EXIT:{pos.rule_name}",
        symbol=sym,
        enabled=True,
        conditions=[],
        logic="AND",
        action=TradeAction(
            type=exit_action,  # type: ignore[arg-type]
            asset_type="STK",
            quantity=qty,
            order_type="MKT",
        ),
        cooldown_minutes=0,
    )
    now = datetime.now(timezone.utc)
    try:
        exit_trade = await place_order(exit_rule, source="rule", is_exit=True, has_existing_position=True)
        if not exit_trade:
            order_recovery.mark_exit_retry_state(pos, "place_order returned None", now=now)
            await save_open_position(pos)
            return

        await order_lifecycle.stamp_exit_trade_context(exit_trade, pos)
        normalized = order_recovery.normalize_trade_status(exit_trade.status)

        if normalized == "FILLED":
            fill = exit_trade.fill_price or current_price
            finalized = await order_lifecycle.finalize_filled_exit_trade(
                exit_trade,
                pos,
                close_reason=reason,
                fallback_exit_price=fill,
            )
            pnl = finalized.realized_pnl if finalized else round((fill - pos.entry_price) * pos.quantity, 2)

            await _emit({
                "type": "exit", "symbol": sym, "reason": reason,
                "action": exit_action, "qty": qty,
                "entry_price": pos.entry_price, "exit_price": fill, "pnl": pnl,
            })
            log.info("EXIT %s qty=%d reason='%s' pnl=%.2f", sym, qty, reason, pnl)
            _exited_this_cycle.add(sym.upper())
        elif normalized == "PENDING":
            order_recovery.mark_exit_pending_submitted(pos, exit_trade.order_id, now=now)
            await save_open_position(pos)
            log.info("Exit order PENDING for %s (order_id=%s)", sym, exit_trade.order_id)
        else:
            order_recovery.mark_exit_retry_state(pos, f"Exit order {normalized}", now=now)
            await save_open_position(pos)
            await _check_retry_cap(pos)

    except (OrderError, RuntimeError) as exc:
        order_recovery.mark_exit_retry_state(pos, str(exc), now=now)
        await save_open_position(pos)
        log.error("Exit order FAILED for %s (attempt %d): %s", sym, pos.exit_attempts, exc)
        await _check_retry_cap(pos)


async def _check_retry_cap(pos) -> None:
    """Emit notification + WebSocket alert if retry cap reached."""
    if pos.exit_attempts >= MAX_EXIT_ATTEMPTS:
        msg = (
            f"Exit failed {pos.exit_attempts}x for {pos.symbol} "
            f"(rule: {pos.rule_name}). Last error: {pos.last_exit_error}. "
            f"Manual close required."
        )
        log.critical("EXIT RETRY CAP: %s", msg)
        await _emit({
            "type": "error",
            "message": f"EXIT RETRY CAP: {msg}",
            "symbol": pos.symbol,
            "severity": "critical",
        })
        try:
            from manual_intervention import raise_intervention

            await raise_intervention(
                severity="critical",
                category="exit_retry_cap",
                source="bot_runner",
                symbol=pos.symbol,
                summary=f"Exit retry cap reached for {pos.symbol}",
                required_action="Review broker state and manually close or reconcile the position",
            )
        except Exception as exc:
            log.warning("Failed to open intervention for %s retry cap: %s", pos.symbol, exc)

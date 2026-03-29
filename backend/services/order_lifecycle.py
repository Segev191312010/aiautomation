"""Shared order lifecycle helpers for entry registration and exit finalization."""
from __future__ import annotations

import logging

from config import cfg
from database import (
    delete_open_position,
    finalize_trade_outcome,
    get_trade,
    save_trade,
    update_trade_status,
)
from market_data import get_historical_bars
from models import OpenPosition, Trade
from position_tracker import register_position

log = logging.getLogger(__name__)


async def persist_filled_trade_record(trade_rec: Trade, fill_price: float) -> Trade:
    """Persist a fill consistently for any trade record."""
    await update_trade_status(trade_rec.id, "FILLED", fill_price)
    trade_rec.status = "FILLED"  # type: ignore[assignment]
    trade_rec.fill_price = fill_price
    # Preserve current behavior: filled trades mirror fill into entry_price.
    trade_rec.entry_price = fill_price
    await save_trade(trade_rec)
    return trade_rec


async def register_entry_position_from_fill(
    trade: Trade,
    *,
    rule_name: str | None = None,
) -> bool:
    """Register a tracked open position for a filled BUY trade."""
    if trade.action != "BUY" or not trade.fill_price:
        return False

    try:
        df = await get_historical_bars(trade.symbol, duration="60 D", bar_size="1D")
        if df is None or len(df) < 14:
            log.warning("Insufficient bars for position registration of %s", trade.id)
            return False
        await register_position(trade, df, rule_name or trade.rule_name)
        return True
    except Exception as exc:
        log.error("Position registration failed for %s: %s", trade.id, exc)
        return False


async def stamp_exit_trade_context(
    exit_trade: Trade,
    position: OpenPosition,
    *,
    fallback_mode: str | None = None,
    fallback_source: str | None = None,
    fallback_decision_id: str | None = None,
) -> Trade:
    """Link an exit trade to its originating position and inherit entry context."""
    entry_trade = await get_trade(position.id)
    entry_mode = entry_trade.mode if entry_trade else None
    entry_source = entry_trade.source if entry_trade else None
    entry_decision_id = entry_trade.decision_id if entry_trade else None

    exit_trade.position_id = position.id
    exit_trade.mode = (
        exit_trade.mode
        or entry_mode
        or fallback_mode
        or ("LIVE" if cfg.AUTOPILOT_MODE == "LIVE" else "PAPER")
    )
    exit_trade.source = exit_trade.source or entry_source or fallback_source or "rule"
    exit_trade.decision_id = exit_trade.decision_id or entry_decision_id or fallback_decision_id
    await save_trade(exit_trade)
    return exit_trade


async def finalize_filled_exit_trade(
    exit_trade: Trade,
    position: OpenPosition,
    *,
    close_reason: str,
    fallback_exit_price: float | None = None,
) -> Trade | None:
    """Finalize a filled exit and remove the tracked open position."""
    fill_price = float(exit_trade.fill_price or fallback_exit_price or 0.0)
    if fill_price <= 0:
        raise ValueError(f"Filled exit trade {exit_trade.id} is missing a usable exit price")

    finalized = await finalize_trade_outcome(
        exit_trade.id,
        position_side=position.side,
        entry_price=position.entry_price,
        exit_price=fill_price,
        fees=0.0,
        close_reason=close_reason,
        position_id=position.id,
    )
    await delete_open_position(position.id)
    return finalized

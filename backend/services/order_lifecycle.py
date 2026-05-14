"""Shared order lifecycle helpers for entry registration and exit finalization."""
from __future__ import annotations

import logging
import math

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

# Sentinel ATR when bar fetch fails during position registration. Encoded as a
# literal constant (not a tunable knob) and pinned by test_nan_chain.py so a
# future dev cannot silently retune it. Operators looking at the data_gap
# audit log can read "atr=fill_price * 0.02" as a known degraded-mode value.
DEGRADED_ATR_FRACTION: float = 0.02


async def persist_filled_trade_record(trade_rec: Trade, fill_price: float) -> Trade:
    """Persist a fill consistently — single transaction for status + trade update."""
    from db.core import transaction

    async with transaction() as db:
        await update_trade_status(trade_rec.id, "FILLED", fill_price, db=db)
        trade_rec.status = "FILLED"  # type: ignore[assignment]
        trade_rec.fill_price = fill_price
        # Preserve current behavior: filled trades mirror fill into entry_price.
        trade_rec.entry_price = fill_price
        await save_trade(trade_rec, db=db)
    return trade_rec


async def register_entry_position_from_fill(
    trade: Trade,
    *,
    rule_name: str | None = None,
) -> bool:
    """Register a tracked open position for a filled BUY trade.

    INVARIANT: trade.fill_price must be finite and positive. NaN is truthy
    under `not`, so the original `not trade.fill_price` guard let NaN-priced
    trades through and registered positions with NaN entry_price, which then
    silently broke every stop/target/PnL comparison.

    On bar-fetch failure (yfinance / IBKR down), the position is still
    registered with a degraded sentinel ATR (`DEGRADED_ATR_FRACTION` of fill
    price) so the exit tracker keeps running. A CRITICAL ``data_gap`` audit
    entry is emitted with the symbol and computed ATR so operators can grep
    `data_gap` to see which live positions are running on degraded data.
    """
    if trade.action != "BUY":
        return False
    fp = trade.fill_price
    if fp is None or not math.isfinite(fp) or fp <= 0:
        log.error(
            "register_entry_position_from_fill rejected non-finite fill_price: "
            "trade=%s symbol=%s fill_price=%r",
            trade.id, trade.symbol, fp,
        )
        return False

    try:
        df = await get_historical_bars(trade.symbol, duration="60 D", bar_size="1D")
        if df is None or len(df) < 14:
            # Degraded path: register with sentinel ATR so the exit tracker
            # still has a managed position. Previously this returned False
            # silently and left a live IBKR position with no exit logic.
            sentinel_atr = fp * DEGRADED_ATR_FRACTION
            log.critical(
                "data_gap: registering degraded OpenPosition — trade=%s symbol=%s "
                "fill_price=%.4f sentinel_atr=%.4f (DEGRADED_ATR_FRACTION=%.4f) "
                "reason=insufficient_bars_for_atr",
                trade.id, trade.symbol, fp, sentinel_atr, DEGRADED_ATR_FRACTION,
            )
            await register_position(
                trade,
                df,
                rule_name or trade.rule_name,
                degraded_atr=sentinel_atr,
            )
            return True
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
    db=None,
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
    await save_trade(exit_trade, db=db)
    return exit_trade


async def finalize_filled_exit_trade(
    exit_trade: Trade,
    position: OpenPosition,
    *,
    close_reason: str,
    fallback_exit_price: float | None = None,
) -> Trade | None:
    """Finalize a filled exit and remove the tracked open position — atomically."""
    from db.core import transaction

    fill_price = float(exit_trade.fill_price or fallback_exit_price or 0.0)
    if fill_price <= 0:
        raise ValueError(f"Filled exit trade {exit_trade.id} is missing a usable exit price")

    async with transaction() as db:
        finalized = await finalize_trade_outcome(
            exit_trade.id,
            position_side=position.side,
            entry_price=position.entry_price,
            exit_price=fill_price,
            fees=0.0,
            close_reason=close_reason,
            position_id=position.id,
            db=db,
        )
        await delete_open_position(position.id, db=db)

    # S10 side effect: link to decision ledger (non-critical, after commit)
    if finalized and finalized.decision_id:
        try:
            from ai_decision_ledger import attach_realized_trade
            await attach_realized_trade(
                finalized.decision_id, finalized.id,
                finalized.realized_pnl, finalized.closed_at,
            )
        except Exception as exc:
            log.warning("Failed to attach realized trade to decision item: %s", exc)

    return finalized

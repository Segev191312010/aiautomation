"""Trades CRUD — trade execution log and outcome finalization."""
from __future__ import annotations
import json
import logging
from datetime import datetime, timezone
from typing import Literal
import aiosqlite
from models import Trade
from db.core import get_db

log = logging.getLogger(__name__)

async def save_trade(
    trade: Trade, user_id: str = "demo", *, db: aiosqlite.Connection | None = None,
) -> None:
    async def _execute(conn: aiosqlite.Connection) -> None:
        await conn.execute(
            "INSERT OR REPLACE INTO trades (id, rule_id, symbol, action, timestamp, data, user_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (trade.id, trade.rule_id, trade.symbol, trade.action,
             trade.timestamp, trade.model_dump_json(), user_id),
        )
    if db is not None:
        await _execute(db)
    else:
        async with get_db() as conn:
            await _execute(conn)
            await conn.commit()


async def get_trades(limit: int = 200, user_id: str = "demo") -> list[Trade]:
    async with get_db() as db:
        async with db.execute(
            "SELECT data FROM trades WHERE user_id=? ORDER BY timestamp DESC LIMIT ?",
            (user_id, limit),
        ) as cur:
            rows = await cur.fetchall()
    return [Trade.model_validate(json.loads(r[0])) for r in rows]


async def get_trade(trade_id: str, user_id: str = "demo") -> Trade | None:
    """Fetch a single trade by its ID."""
    async with get_db() as db:
        async with db.execute(
            "SELECT data FROM trades WHERE id=? AND user_id=?",
            (trade_id, user_id),
        ) as cur:
            row = await cur.fetchone()
    if row:
        return Trade.model_validate(json.loads(row[0]))
    return None


async def get_trade_by_order_id(order_id: int, symbol: str | None = None, user_id: str = "demo") -> Trade | None:
    """Fetch a trade by IBKR order_id, optionally filtered by symbol to prevent ID reuse collisions."""
    async with get_db() as db:
        async with db.execute(
            "SELECT data FROM trades WHERE user_id=? ORDER BY timestamp DESC LIMIT 500",
            (user_id,),
        ) as cur:
            rows = await cur.fetchall()
    for r in rows:
        try:
            trade = Trade.model_validate(json.loads(r[0]))
            if trade.order_id == order_id:
                if symbol and trade.symbol.upper() != symbol.upper():
                    continue  # B5 FIX: skip order ID reuse from different symbol
                return trade
        except Exception:
            continue
    return None


async def update_trade_status(
    trade_id: str, status: str, fill_price: float | None = None, user_id: str = "demo",
    *, db: aiosqlite.Connection | None = None,
) -> None:
    async def _execute(conn: aiosqlite.Connection) -> None:
        async with conn.execute("SELECT data FROM trades WHERE id=? AND user_id=?", (trade_id, user_id)) as cur:
            row = await cur.fetchone()
        if row:
            trade = Trade.model_validate(json.loads(row[0]))
            trade.status = status  # type: ignore[assignment]
            if fill_price is not None:
                trade.fill_price = fill_price
            await conn.execute(
                "UPDATE trades SET data=? WHERE id=? AND user_id=?",
                (trade.model_dump_json(), trade_id, user_id),
            )
    if db is not None:
        await _execute(db)
    else:
        async with get_db() as conn:
            await _execute(conn)
            await conn.commit()


async def finalize_trade_outcome(
    trade_id: str,
    *,
    position_side: Literal["BUY", "SELL"],  # "BUY" (long) or "SELL" (short) — the ENTRY side
    entry_price: float,
    exit_price: float,
    fees: float = 0.0,
    close_reason: str,
    position_id: str | None = None,
    user_id: str = "demo",
    db: aiosqlite.Connection | None = None,
) -> Trade | None:
    """Finalize a trade's canonical outcome fields. Single source of truth for P&L.

    When *db* is provided, the caller manages the transaction and S10 side
    effects. When *db* is ``None``, this function auto-commits and fires
    S10 linkage as before.
    """
    if position_side not in ("BUY", "SELL"):
        raise ValueError(f"position_side must be 'BUY' or 'SELL', got '{position_side}'")

    async def _execute(conn: aiosqlite.Connection) -> Trade | None:
        async with conn.execute("SELECT data FROM trades WHERE id=? AND user_id=?", (trade_id, user_id)) as cur:
            row = await cur.fetchone()
        if not row:
            log.warning("finalize_trade_outcome: trade %s not found", trade_id)
            return None

        trade = Trade.model_validate(json.loads(row[0]))
        qty = trade.quantity

        # Side-aware P&L
        if position_side == "BUY":
            realized_pnl = round((exit_price - entry_price) * qty - fees, 2)
            pnl_pct = round(((exit_price / entry_price) - 1) * 100, 2) if entry_price > 0 else 0.0
        else:
            realized_pnl = round((entry_price - exit_price) * qty - fees, 2)
            pnl_pct = round(((entry_price / exit_price) - 1) * 100, 2) if exit_price > 0 else 0.0

        trade.entry_price = entry_price
        trade.exit_price = exit_price
        trade.fees = fees
        trade.realized_pnl = realized_pnl
        trade.pnl_pct = pnl_pct
        trade.closed_at = datetime.now(timezone.utc).isoformat()
        trade.close_reason = close_reason
        trade.outcome_quality = "canonical"
        if position_id is not None:
            trade.position_id = position_id
        # Backward compat: metadata["pnl"] for unmigrated readers
        trade.metadata["pnl"] = realized_pnl

        await conn.execute(
            "UPDATE trades SET data=? WHERE id=? AND user_id=?",
            (trade.model_dump_json(), trade_id, user_id),
        )
        return trade

    if db is not None:
        return await _execute(db)

    # Standalone mode: own connection, own commit, own S10 side effects
    async with get_db() as conn:
        trade = await _execute(conn)
        await conn.commit()

    # S10: link realized outcome back to originating decision item
    if trade and trade.decision_id:
        try:
            from ai_decision_ledger import attach_realized_trade
            await attach_realized_trade(
                trade.decision_id, trade.id, trade.realized_pnl, trade.closed_at,
            )
        except Exception as exc:
            log.warning("Failed to attach realized trade to decision item: %s", exc)

    return trade


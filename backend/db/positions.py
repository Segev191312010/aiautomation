"""Open positions CRUD — position lifecycle tracking."""
from __future__ import annotations
import json
import logging
import aiosqlite
from models import OpenPosition
from db.core import get_db

log = logging.getLogger(__name__)

async def save_open_position(
    pos: OpenPosition, user_id: str = "demo", *, db: aiosqlite.Connection | None = None,
) -> None:
    """Upsert an open position. When *db* is provided, caller manages the transaction."""
    async def _execute(conn: aiosqlite.Connection) -> None:
        await conn.execute(
            "INSERT OR REPLACE INTO open_positions "
            "(id, symbol, side, quantity, entry_price, entry_time, atr_at_entry, "
            " hard_stop_price, atr_stop_mult, atr_trail_mult, high_watermark, "
            " rule_id, rule_name, user_id, data) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (pos.id, pos.symbol, pos.side, pos.quantity, pos.entry_price,
             pos.entry_time, pos.atr_at_entry, pos.hard_stop_price,
             pos.atr_stop_mult, pos.atr_trail_mult, pos.high_watermark,
             pos.rule_id, pos.rule_name, user_id, pos.model_dump_json()),
        )
    if db is not None:
        await _execute(db)
    else:
        async with get_db() as conn:
            await _execute(conn)
            await conn.commit()


async def get_open_positions(user_id: str = "demo") -> list[OpenPosition]:
    """Return all tracked open positions for a user."""
    async with get_db() as db:
        async with db.execute(
            "SELECT data FROM open_positions WHERE user_id=?", (user_id,)
        ) as cur:
            rows = await cur.fetchall()
    return [OpenPosition.model_validate(json.loads(r[0])) for r in rows]


async def get_open_position(trade_id: str, user_id: str = "demo") -> OpenPosition | None:
    """Return a single tracked position by trade_id."""
    async with get_db() as db:
        async with db.execute(
            "SELECT data FROM open_positions WHERE id=? AND user_id=?",
            (trade_id, user_id),
        ) as cur:
            row = await cur.fetchone()
    return OpenPosition.model_validate(json.loads(row[0])) if row else None


async def delete_open_position(
    trade_id: str, user_id: str = "demo", *, db: aiosqlite.Connection | None = None,
) -> bool:
    """Remove a tracked position on exit. Returns True if a row was deleted."""
    async def _execute(conn: aiosqlite.Connection) -> bool:
        cur = await conn.execute(
            "DELETE FROM open_positions WHERE id=? AND user_id=?",
            (trade_id, user_id),
        )
        return cur.rowcount > 0
    if db is not None:
        return await _execute(db)
    else:
        async with get_db() as conn:
            result = await _execute(conn)
            await conn.commit()
            return result

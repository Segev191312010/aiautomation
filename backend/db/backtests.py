"""Backtest CRUD — save, retrieve, and delete backtest results."""
from __future__ import annotations
import json
import logging
from db.core import get_db

log = logging.getLogger(__name__)

async def save_backtest(
    backtest_id: str,
    user_id: str,
    name: str,
    strategy_data: str,
    result_data: str,
    created_at: str,
) -> None:
    async with get_db() as db:
        await db.execute(
            "INSERT OR REPLACE INTO backtests (id, user_id, name, strategy_data, result_data, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (backtest_id, user_id, name, strategy_data, result_data, created_at),
        )
        await db.commit()


async def get_backtests(user_id: str = "demo", limit: int = 50) -> list[dict]:
    """Return list of saved backtests with summary info."""
    async with get_db() as db:
        async with db.execute(
            "SELECT id, name, result_data, created_at FROM backtests "
            "WHERE user_id=? ORDER BY created_at DESC LIMIT ?",
            (user_id, limit),
        ) as cur:
            rows = await cur.fetchall()

    results = []
    for row in rows:
        try:
            result = json.loads(row[2])
            metrics = result.get("metrics", {})
            results.append({
                "id": row[0],
                "name": row[1],
                "symbol": result.get("symbol", ""),
                "created_at": row[3],
                "total_return_pct": metrics.get("total_return_pct", 0),
                "num_trades": metrics.get("num_trades", 0),
                "sharpe_ratio": metrics.get("sharpe_ratio", 0),
            })
        except (json.JSONDecodeError, KeyError):
            continue
    return results


async def get_backtest(backtest_id: str) -> dict | None:
    """Return full backtest with strategy_data and result_data."""
    async with get_db() as db:
        async with db.execute(
            "SELECT id, name, strategy_data, result_data, created_at FROM backtests WHERE id=?",
            (backtest_id,),
        ) as cur:
            row = await cur.fetchone()
    if not row:
        return None
    return {
        "id": row[0],
        "name": row[1],
        "strategy_data": json.loads(row[2]),
        "result_data": json.loads(row[3]),
        "created_at": row[4],
    }


async def delete_backtest(backtest_id: str, user_id: str = "demo") -> bool:
    async with get_db() as db:
        cur = await db.execute(
            "DELETE FROM backtests WHERE id=? AND user_id=?",
            (backtest_id, user_id),
        )
        await db.commit()
        return cur.rowcount > 0


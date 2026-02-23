"""
SQLite persistence layer using aiosqlite.

Tables:
  - rules  : automation rules stored as JSON blobs
  - trades : trade execution log
"""
from __future__ import annotations
import json
import aiosqlite
from datetime import datetime, timezone
from config import cfg
from models import Rule, Trade


DB_PATH = cfg.DB_PATH

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_CREATE_RULES = """
CREATE TABLE IF NOT EXISTS rules (
    id   TEXT PRIMARY KEY,
    data TEXT NOT NULL
);
"""

_CREATE_TRADES = """
CREATE TABLE IF NOT EXISTS trades (
    id        TEXT PRIMARY KEY,
    rule_id   TEXT NOT NULL,
    symbol    TEXT NOT NULL,
    action    TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    data      TEXT NOT NULL
);
"""


async def init_db() -> None:
    """Create tables and seed starter rules on first run."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(_CREATE_RULES)
        await db.execute(_CREATE_TRADES)
        await db.commit()
        await _seed_starter_rules(db)


# ---------------------------------------------------------------------------
# Rules CRUD
# ---------------------------------------------------------------------------

async def get_rules() -> list[Rule]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT data FROM rules") as cursor:
            rows = await cursor.fetchall()
    return [Rule.model_validate(json.loads(r[0])) for r in rows]


async def get_rule(rule_id: str) -> Rule | None:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT data FROM rules WHERE id=?", (rule_id,)) as cur:
            row = await cur.fetchone()
    return Rule.model_validate(json.loads(row[0])) if row else None


async def save_rule(rule: Rule) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO rules (id, data) VALUES (?, ?)",
            (rule.id, rule.model_dump_json()),
        )
        await db.commit()


async def delete_rule(rule_id: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("DELETE FROM rules WHERE id=?", (rule_id,))
        await db.commit()
        return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Trades CRUD
# ---------------------------------------------------------------------------

async def save_trade(trade: Trade) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO trades (id, rule_id, symbol, action, timestamp, data) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (trade.id, trade.rule_id, trade.symbol, trade.action,
             trade.timestamp, trade.model_dump_json()),
        )
        await db.commit()


async def get_trades(limit: int = 200) -> list[Trade]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT data FROM trades ORDER BY timestamp DESC LIMIT ?", (limit,)
        ) as cur:
            rows = await cur.fetchall()
    return [Trade.model_validate(json.loads(r[0])) for r in rows]


async def update_trade_status(trade_id: str, status: str, fill_price: float | None = None) -> None:
    trade = None
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT data FROM trades WHERE id=?", (trade_id,)) as cur:
            row = await cur.fetchone()
        if row:
            trade = Trade.model_validate(json.loads(row[0]))
            trade.status = status  # type: ignore[assignment]
            if fill_price is not None:
                trade.fill_price = fill_price
            await db.execute(
                "UPDATE trades SET data=? WHERE id=?",
                (trade.model_dump_json(), trade_id),
            )
            await db.commit()


# ---------------------------------------------------------------------------
# Seed starter rules
# ---------------------------------------------------------------------------

_STARTER_RULES = [
    {
        "name": "RSI Oversold Bounce",
        "symbol": "AAPL",
        "enabled": False,
        "conditions": [
            {"indicator": "RSI", "params": {"length": 14}, "operator": "crosses_below", "value": 30}
        ],
        "logic": "AND",
        "action": {"type": "BUY", "asset_type": "STK", "quantity": 100, "order_type": "MKT"},
        "cooldown_minutes": 60,
    },
    {
        "name": "Golden Cross",
        "symbol": "AAPL",
        "enabled": False,
        "conditions": [
            {"indicator": "SMA", "params": {"length": 50}, "operator": "crosses_above",
             "value": "SMA_200"},
        ],
        "logic": "AND",
        "action": {"type": "BUY", "asset_type": "STK", "quantity": 50, "order_type": "MKT"},
        "cooldown_minutes": 1440,
    },
    {
        "name": "RSI Overbought Exit",
        "symbol": "AAPL",
        "enabled": False,
        "conditions": [
            {"indicator": "RSI", "params": {"length": 14}, "operator": "crosses_above", "value": 70}
        ],
        "logic": "AND",
        "action": {"type": "SELL", "asset_type": "STK", "quantity": 100, "order_type": "MKT"},
        "cooldown_minutes": 60,
    },
]


async def _seed_starter_rules(db: aiosqlite.Connection) -> None:
    async with db.execute("SELECT COUNT(*) FROM rules") as cur:
        (count,) = await cur.fetchone()  # type: ignore[misc]
    if count == 0:
        for raw in _STARTER_RULES:
            rule = Rule.model_validate(raw)
            await db.execute(
                "INSERT INTO rules (id, data) VALUES (?, ?)",
                (rule.id, rule.model_dump_json()),
            )
        await db.commit()

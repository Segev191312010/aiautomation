"""
SQLite persistence layer using aiosqlite.

Tables:
  - users  : user accounts + settings JSON blob
  - rules  : automation rules stored as JSON blobs
  - trades : trade execution log
"""
from __future__ import annotations
import json
import logging
from contextlib import asynccontextmanager
import aiosqlite
from datetime import datetime, timezone
from config import cfg
from models import Rule, Trade, ScreenerPreset, ScanFilter, FilterValue

log = logging.getLogger(__name__)

DB_PATH = cfg.DB_PATH


@asynccontextmanager
async def get_db():
    """Open a DB connection with WAL mode and busy_timeout configured."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA busy_timeout=5000")
        yield db


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_CREATE_USERS = """
CREATE TABLE IF NOT EXISTS users (
    id            TEXT PRIMARY KEY,
    email         TEXT UNIQUE,
    password_hash TEXT,
    created_at    TEXT,
    settings      TEXT DEFAULT '{}'
);
"""

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

_CREATE_SCREENER_PRESETS = """
CREATE TABLE IF NOT EXISTS screener_presets (
    id         TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL DEFAULT 'demo',
    name       TEXT NOT NULL,
    data       TEXT NOT NULL,
    built_in   INTEGER DEFAULT 0,
    created_at TEXT NOT NULL
);
"""


async def _safe_add_column(db: aiosqlite.Connection, table: str, column: str, col_type: str, default: str) -> None:
    """Add a column if table exists and column doesn't."""
    async with db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ) as cur:
        if not await cur.fetchone():
            log.debug("Skipping ALTER on non-existent table %s", table)
            return
    try:
        await db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type} DEFAULT {default}")
        log.info("Added column %s.%s", table, column)
    except Exception:
        pass  # column already exists


async def init_db() -> None:
    """Create tables, migrate schema, and seed starter data on first run."""
    async with aiosqlite.connect(DB_PATH) as db:
        # Performance: WAL mode for better concurrent read/write
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA busy_timeout=5000")

        # Create tables
        await db.execute(_CREATE_USERS)
        await db.execute(_CREATE_RULES)
        await db.execute(_CREATE_TRADES)
        await db.execute(_CREATE_SCREENER_PRESETS)
        await db.commit()

        # Migrate: add user_id column to existing tables
        await _safe_add_column(db, "rules",         "user_id", "TEXT", "'demo'")
        await _safe_add_column(db, "trades",        "user_id", "TEXT", "'demo'")
        await _safe_add_column(db, "sim_account",   "user_id", "TEXT", "'demo'")
        await _safe_add_column(db, "sim_positions", "user_id", "TEXT", "'demo'")
        await _safe_add_column(db, "sim_orders",    "user_id", "TEXT", "'demo'")
        await db.commit()

        # Indexes for common query patterns (after migration so user_id exists)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_trades_user_ts ON trades(user_id, timestamp)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_trades_symbol_ts ON trades(symbol, timestamp DESC)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_rules_user ON rules(user_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_presets_user ON screener_presets(user_id)")
        await db.commit()

        # Seed demo user
        from auth import seed_demo_user
        await seed_demo_user(db)

        # Seed starter rules
        await _seed_starter_rules(db)

        # Seed screener presets
        await _seed_screener_presets(db)


# ---------------------------------------------------------------------------
# Rules CRUD
# ---------------------------------------------------------------------------

async def get_rules(user_id: str = "demo") -> list[Rule]:
    async with get_db() as db:
        async with db.execute(
            "SELECT data FROM rules WHERE user_id=?", (user_id,)
        ) as cursor:
            rows = await cursor.fetchall()
    return [Rule.model_validate(json.loads(r[0])) for r in rows]


async def get_rule(rule_id: str, user_id: str = "demo") -> Rule | None:
    async with get_db() as db:
        async with db.execute(
            "SELECT data FROM rules WHERE id=? AND user_id=?", (rule_id, user_id)
        ) as cur:
            row = await cur.fetchone()
    return Rule.model_validate(json.loads(row[0])) if row else None


async def save_rule(rule: Rule, user_id: str = "demo") -> None:
    async with get_db() as db:
        await db.execute(
            "INSERT OR REPLACE INTO rules (id, data, user_id) VALUES (?, ?, ?)",
            (rule.id, rule.model_dump_json(), user_id),
        )
        await db.commit()


async def delete_rule(rule_id: str, user_id: str = "demo") -> bool:
    async with get_db() as db:
        cur = await db.execute(
            "DELETE FROM rules WHERE id=? AND user_id=?", (rule_id, user_id)
        )
        await db.commit()
        return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Trades CRUD
# ---------------------------------------------------------------------------

async def save_trade(trade: Trade, user_id: str = "demo") -> None:
    async with get_db() as db:
        await db.execute(
            "INSERT OR REPLACE INTO trades (id, rule_id, symbol, action, timestamp, data, user_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (trade.id, trade.rule_id, trade.symbol, trade.action,
             trade.timestamp, trade.model_dump_json(), user_id),
        )
        await db.commit()


async def get_trades(limit: int = 200, user_id: str = "demo") -> list[Trade]:
    async with get_db() as db:
        async with db.execute(
            "SELECT data FROM trades WHERE user_id=? ORDER BY timestamp DESC LIMIT ?",
            (user_id, limit),
        ) as cur:
            rows = await cur.fetchall()
    return [Trade.model_validate(json.loads(r[0])) for r in rows]


async def update_trade_status(trade_id: str, status: str, fill_price: float | None = None) -> None:
    trade = None
    async with get_db() as db:
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
                "INSERT INTO rules (id, data, user_id) VALUES (?, ?, ?)",
                (rule.id, rule.model_dump_json(), "demo"),
            )
        await db.commit()


# ---------------------------------------------------------------------------
# Screener presets CRUD
# ---------------------------------------------------------------------------

async def get_screener_presets(user_id: str = "demo") -> list[ScreenerPreset]:
    async with get_db() as db:
        async with db.execute(
            "SELECT data FROM screener_presets WHERE user_id=? OR built_in=1 ORDER BY built_in DESC, created_at",
            (user_id,),
        ) as cur:
            rows = await cur.fetchall()
    return [ScreenerPreset.model_validate(json.loads(r[0])) for r in rows]


async def save_screener_preset(preset: ScreenerPreset) -> None:
    async with get_db() as db:
        await db.execute(
            "INSERT OR REPLACE INTO screener_presets (id, user_id, name, data, built_in, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (preset.id, preset.user_id, preset.name, preset.model_dump_json(),
             1 if preset.built_in else 0, preset.created_at),
        )
        await db.commit()


async def delete_screener_preset(preset_id: str, user_id: str = "demo") -> bool:
    async with get_db() as db:
        cur = await db.execute(
            "DELETE FROM screener_presets WHERE id=? AND user_id=? AND built_in=0",
            (preset_id, user_id),
        )
        await db.commit()
        return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Seed built-in screener presets
# ---------------------------------------------------------------------------

_BUILT_IN_PRESETS = [
    {
        "name": "RSI Oversold",
        "filters": [
            {
                "indicator": "RSI",
                "params": {"length": 14},
                "operator": "LT",
                "value": {"type": "number", "number": 30},
            }
        ],
    },
    {
        "name": "Golden Cross",
        "filters": [
            {
                "indicator": "SMA",
                "params": {"length": 50},
                "operator": "CROSSES_ABOVE",
                "value": {"type": "indicator", "indicator": "SMA", "params": {"length": 200}},
            }
        ],
    },
    {
        "name": "Volume Breakout",
        "filters": [
            {
                "indicator": "VOLUME",
                "params": {},
                "operator": "GT",
                "value": {"type": "indicator", "indicator": "VOLUME", "params": {"length": 20}, "multiplier": 2.0},
            }
        ],
    },
    {
        "name": "RSI Overbought",
        "filters": [
            {
                "indicator": "RSI",
                "params": {"length": 14},
                "operator": "GT",
                "value": {"type": "number", "number": 70},
            }
        ],
    },
]


async def _seed_screener_presets(db: aiosqlite.Connection) -> None:
    async with db.execute("SELECT COUNT(*) FROM screener_presets WHERE built_in=1") as cur:
        (count,) = await cur.fetchone()  # type: ignore[misc]
    if count > 0:
        return
    for raw in _BUILT_IN_PRESETS:
        preset = ScreenerPreset(
            name=raw["name"],
            filters=[ScanFilter.model_validate(f) for f in raw["filters"]],
            built_in=True,
            user_id="demo",
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        await db.execute(
            "INSERT INTO screener_presets (id, user_id, name, data, built_in, created_at) "
            "VALUES (?, ?, ?, ?, 1, ?)",
            (preset.id, preset.user_id, preset.name, preset.model_dump_json(), preset.created_at),
        )
    await db.commit()

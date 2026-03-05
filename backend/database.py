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
from models import Rule, Trade, ScreenerPreset, ScanFilter, FilterValue, Alert, AlertHistory

log = logging.getLogger(__name__)

DB_PATH = cfg.DB_PATH


@asynccontextmanager
async def get_db():
    """Open a DB connection with WAL mode and busy_timeout configured."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA synchronous=NORMAL")
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

_CREATE_BACKTESTS = """
CREATE TABLE IF NOT EXISTS backtests (
    id         TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL DEFAULT 'demo',
    name       TEXT NOT NULL,
    strategy_data TEXT NOT NULL,
    result_data   TEXT NOT NULL,
    created_at TEXT NOT NULL
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

_CREATE_DIAG_INDICATOR_CATALOG = """
CREATE TABLE IF NOT EXISTS diag_indicator_catalog (
    code TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    source TEXT NOT NULL,
    frequency TEXT NOT NULL,
    weight REAL NOT NULL DEFAULT 1.0,
    invert_sign INTEGER NOT NULL DEFAULT 0,
    lookback_days INTEGER NOT NULL DEFAULT 365,
    expected_lag_business_days INTEGER NOT NULL DEFAULT 0,
    stale_warn_s REAL NULL,
    stale_critical_s REAL NULL,
    active INTEGER NOT NULL DEFAULT 1,
    stage TEXT NOT NULL DEFAULT '3A',
    sector_weight_json TEXT NOT NULL DEFAULT '{}',
    heuristic_version TEXT NOT NULL DEFAULT '1.0.0',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
"""

_CREATE_DIAG_INDICATOR_VALUES = """
CREATE TABLE IF NOT EXISTS diag_indicator_values (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL,
    as_of_ts INTEGER NOT NULL,
    value REAL NULL,
    score REAL NULL,
    state TEXT NULL,
    reason_code TEXT NULL,
    freshness_status TEXT NOT NULL DEFAULT 'ok',
    age_s REAL NULL,
    source TEXT NOT NULL,
    meta_json TEXT NOT NULL DEFAULT '{}',
    created_at INTEGER NOT NULL,
    UNIQUE(code, as_of_ts)
);
"""

_CREATE_DIAG_SYSTEM_SNAPSHOTS = """
CREATE TABLE IF NOT EXISTS diag_system_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    as_of_ts INTEGER NOT NULL UNIQUE,
    composite_score REAL NULL,
    state TEXT NULL,
    indicator_count INTEGER NOT NULL DEFAULT 0,
    stale_count INTEGER NOT NULL DEFAULT 0,
    warn_count INTEGER NOT NULL DEFAULT 0,
    summary_json TEXT NOT NULL DEFAULT '{}',
    created_at INTEGER NOT NULL
);
"""

_CREATE_DIAG_SECTOR_PROJECTION_RUNS = """
CREATE TABLE IF NOT EXISTS diag_sector_projection_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_ts INTEGER NOT NULL,
    lookback_days INTEGER NOT NULL,
    heuristic_version TEXT NOT NULL,
    status TEXT NOT NULL,
    notes TEXT NULL
);
"""

_CREATE_DIAG_SECTOR_PROJECTION_VALUES = """
CREATE TABLE IF NOT EXISTS diag_sector_projection_values (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    sector TEXT NOT NULL,
    score REAL NOT NULL,
    direction TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    FOREIGN KEY(run_id) REFERENCES diag_sector_projection_runs(id) ON DELETE CASCADE
);
"""

_CREATE_DIAG_NEWS_CACHE = """
CREATE TABLE IF NOT EXISTS diag_news_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    headline TEXT NOT NULL,
    url TEXT NOT NULL UNIQUE,
    published_at INTEGER NOT NULL,
    fetched_at INTEGER NOT NULL
);
"""

_CREATE_DIAG_REFRESH_RUNS = """
CREATE TABLE IF NOT EXISTS diag_refresh_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    status TEXT NOT NULL,
    lock_holder TEXT NULL,
    locked_at INTEGER NULL,
    lock_expires_at INTEGER NULL,
    started_at INTEGER NOT NULL,
    completed_at INTEGER NULL,
    error TEXT NULL
);
"""


_CREATE_ALERTS = """
CREATE TABLE IF NOT EXISTS alerts (
    id      TEXT PRIMARY KEY,
    user_id TEXT NOT NULL DEFAULT 'demo',
    symbol  TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    data    TEXT NOT NULL
);
"""

_CREATE_ALERT_HISTORY = """
CREATE TABLE IF NOT EXISTS alert_history (
    id         TEXT PRIMARY KEY,
    alert_id   TEXT NOT NULL,
    user_id    TEXT NOT NULL DEFAULT 'demo',
    symbol     TEXT NOT NULL,
    fired_at   TEXT NOT NULL,
    data       TEXT NOT NULL
);
"""


_ALLOWED_COLUMNS: set[tuple[str, str]] = {
    ("rules", "user_id"),
    ("trades", "user_id"),
    ("sim_account", "user_id"),
    ("sim_positions", "user_id"),
    ("sim_orders", "user_id"),
}


async def _safe_add_column(db: aiosqlite.Connection, table: str, column: str, col_type: str, default: str) -> None:
    """Add a column if table exists and column doesn't."""
    if (table, column) not in _ALLOWED_COLUMNS:
        log.warning("Blocked ALTER on non-allowlisted column %s.%s", table, column)
        return
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
        await db.execute("PRAGMA synchronous=NORMAL")
        await db.execute("PRAGMA busy_timeout=5000")

        # Create tables
        await db.execute(_CREATE_USERS)
        await db.execute(_CREATE_RULES)
        await db.execute(_CREATE_TRADES)
        await db.execute(_CREATE_BACKTESTS)
        await db.execute(_CREATE_SCREENER_PRESETS)
        await db.execute(_CREATE_DIAG_INDICATOR_CATALOG)
        await db.execute(_CREATE_DIAG_INDICATOR_VALUES)
        await db.execute(_CREATE_DIAG_SYSTEM_SNAPSHOTS)
        await db.execute(_CREATE_DIAG_SECTOR_PROJECTION_RUNS)
        await db.execute(_CREATE_DIAG_SECTOR_PROJECTION_VALUES)
        await db.execute(_CREATE_DIAG_NEWS_CACHE)
        await db.execute(_CREATE_DIAG_REFRESH_RUNS)
        await db.execute(_CREATE_ALERTS)
        await db.execute(_CREATE_ALERT_HISTORY)
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
        await db.execute("CREATE INDEX IF NOT EXISTS idx_backtests_user ON backtests(user_id, created_at DESC)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_diag_values_code_ts ON diag_indicator_values(code, as_of_ts DESC)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_diag_snapshots_ts ON diag_system_snapshots(as_of_ts DESC)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_diag_projection_runs_ts ON diag_sector_projection_runs(run_ts DESC)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_diag_news_published ON diag_news_cache(published_at DESC)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_diag_refresh_status_lock ON diag_refresh_runs(status, lock_expires_at)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_alerts_user ON alerts(user_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_alerts_enabled_symbol ON alerts(enabled, symbol)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_alert_history_user_fired ON alert_history(user_id, fired_at DESC)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_alert_history_alert ON alert_history(alert_id)")
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


# ---------------------------------------------------------------------------
# Backtests CRUD
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Alerts CRUD
# ---------------------------------------------------------------------------

async def get_alerts(user_id: str = "demo", enabled_only: bool = False) -> list[Alert]:
    """Fetch alerts for a specific user."""
    async with get_db() as db:
        if enabled_only:
            sql = "SELECT data FROM alerts WHERE user_id=? AND enabled=1"
        else:
            sql = "SELECT data FROM alerts WHERE user_id=?"
        async with db.execute(sql, (user_id,)) as cursor:
            rows = await cursor.fetchall()
    return [Alert.model_validate(json.loads(r[0])) for r in rows]


async def get_enabled_alerts_all() -> list[Alert]:
    """Fetch all enabled alerts across all users (for alert engine)."""
    async with get_db() as db:
        async with db.execute("SELECT data FROM alerts WHERE enabled=1") as cursor:
            rows = await cursor.fetchall()
    return [Alert.model_validate(json.loads(r[0])) for r in rows]


async def get_alert(alert_id: str, user_id: str = "demo") -> Alert | None:
    """Fetch a single alert by id, scoped to user."""
    async with get_db() as db:
        async with db.execute(
            "SELECT data FROM alerts WHERE id=? AND user_id=?", (alert_id, user_id)
        ) as cur:
            row = await cur.fetchone()
    return Alert.model_validate(json.loads(row[0])) if row else None


async def save_alert(alert: Alert, user_id: str = "demo") -> None:
    """Insert or replace an alert."""
    async with get_db() as db:
        await db.execute(
            "INSERT OR REPLACE INTO alerts (id, user_id, symbol, enabled, data) VALUES (?, ?, ?, ?, ?)",
            (alert.id, user_id, alert.symbol.upper(), 1 if alert.enabled else 0,
             alert.model_dump_json()),
        )
        await db.commit()


async def delete_alert(alert_id: str, user_id: str = "demo") -> bool:
    """Delete an alert. Returns True if a row was removed."""
    async with get_db() as db:
        cur = await db.execute(
            "DELETE FROM alerts WHERE id=? AND user_id=?", (alert_id, user_id)
        )
        await db.commit()
        return cur.rowcount > 0


async def get_alert_history(
    user_id: str = "demo", limit: int = 100, alert_id: str | None = None
) -> list[AlertHistory]:
    """Fetch alert history entries, newest first."""
    async with get_db() as db:
        if alert_id:
            sql = (
                "SELECT data FROM alert_history "
                "WHERE user_id=? AND alert_id=? ORDER BY fired_at DESC LIMIT ?"
            )
            params: tuple = (user_id, alert_id, limit)
        else:
            sql = (
                "SELECT data FROM alert_history "
                "WHERE user_id=? ORDER BY fired_at DESC LIMIT ?"
            )
            params = (user_id, limit)
        async with db.execute(sql, params) as cursor:
            rows = await cursor.fetchall()
    return [AlertHistory.model_validate(json.loads(r[0])) for r in rows]


async def save_alert_history(entry: AlertHistory, user_id: str = "demo") -> None:
    """Persist a fired-alert history entry."""
    async with get_db() as db:
        await db.execute(
            "INSERT INTO alert_history (id, alert_id, user_id, symbol, fired_at, data) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (entry.id, entry.alert_id, user_id, entry.symbol,
             entry.fired_at, entry.model_dump_json()),
        )
        await db.commit()

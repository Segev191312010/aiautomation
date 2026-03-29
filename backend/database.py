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
from typing import Literal
import aiosqlite
from datetime import datetime, timezone
from config import cfg
from models import Rule, Trade, ScreenerPreset, ScanFilter, FilterValue, Alert, AlertHistory, OpenPosition

log = logging.getLogger(__name__)

DB_PATH = cfg.DB_PATH


@asynccontextmanager
async def get_db():
    """Open a DB connection with WAL mode and busy_timeout configured."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA synchronous=NORMAL")
        await db.execute("PRAGMA busy_timeout=5000")
        await db.execute("PRAGMA foreign_keys=ON")
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


_CREATE_OPEN_POSITIONS = """
CREATE TABLE IF NOT EXISTS open_positions (
    id              TEXT PRIMARY KEY,
    symbol          TEXT NOT NULL,
    side            TEXT NOT NULL,
    quantity        REAL NOT NULL,
    entry_price     REAL NOT NULL,
    entry_time      TEXT NOT NULL,
    atr_at_entry    REAL NOT NULL,
    hard_stop_price REAL NOT NULL,
    atr_stop_mult   REAL NOT NULL,
    atr_trail_mult  REAL NOT NULL,
    high_watermark  REAL NOT NULL,
    rule_id         TEXT NOT NULL,
    rule_name       TEXT NOT NULL,
    user_id         TEXT NOT NULL DEFAULT 'demo',
    data            TEXT NOT NULL
);
"""

_CREATE_AI_GUARDRAILS = """
CREATE TABLE IF NOT EXISTS ai_guardrails (
    id         TEXT PRIMARY KEY DEFAULT 'default',
    user_id    TEXT NOT NULL DEFAULT 'demo',
    data       TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

_CREATE_AI_AUDIT_LOG = """
CREATE TABLE IF NOT EXISTS ai_audit_log (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp                   TEXT NOT NULL,
    action_type                 TEXT NOT NULL,
    category                    TEXT NOT NULL,
    description                 TEXT NOT NULL,
    old_value                   TEXT,
    new_value                   TEXT,
    reason                      TEXT,
    confidence                  REAL,
    decision_confidence_avg     REAL,
    parameter_uncertainty_width REAL,
    input_tokens                INTEGER,
    output_tokens               INTEGER,
    status                      TEXT NOT NULL DEFAULT 'applied',
    reverted_at                 TEXT,
    user_id                     TEXT NOT NULL DEFAULT 'demo'
);
"""

_CREATE_AI_PARAMETER_SNAPSHOTS = """
CREATE TABLE IF NOT EXISTS ai_parameter_snapshots (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp  TEXT NOT NULL,
    param_type TEXT NOT NULL,
    symbol     TEXT,
    data       TEXT NOT NULL,
    source     TEXT NOT NULL DEFAULT 'ai',
    user_id    TEXT NOT NULL DEFAULT 'demo'
);
"""

_CREATE_AI_SHADOW_DECISIONS = """
CREATE TABLE IF NOT EXISTS ai_shadow_decisions (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp            TEXT NOT NULL,
    param_type           TEXT NOT NULL,
    symbol               TEXT,
    ai_suggested_value   TEXT NOT NULL,
    actual_value_used    TEXT NOT NULL,
    market_condition     TEXT,
    hypothetical_outcome TEXT,
    delta_value          REAL,
    confidence           REAL,
    regime               TEXT,
    user_id              TEXT NOT NULL DEFAULT 'demo'
);
"""

_CREATE_AI_RULE_VERSIONS = """
CREATE TABLE IF NOT EXISTS ai_rule_versions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_id      TEXT NOT NULL,
    version      INTEGER NOT NULL,
    snapshot     TEXT NOT NULL,
    diff_summary TEXT,
    created_at   TEXT NOT NULL,
    author       TEXT NOT NULL DEFAULT 'ai',
    user_id      TEXT NOT NULL DEFAULT 'demo',
    UNIQUE(rule_id, version)
);
"""

_CREATE_AI_RULE_VALIDATION_RUNS = """
CREATE TABLE IF NOT EXISTS ai_rule_validation_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_id         TEXT NOT NULL,
    version         INTEGER NOT NULL,
    validation_mode TEXT NOT NULL,
    trades_count    INTEGER NOT NULL DEFAULT 0,
    hit_rate        REAL,
    net_pnl         REAL,
    expectancy      REAL,
    max_drawdown    REAL,
    overlap_score   REAL,
    passed          INTEGER NOT NULL DEFAULT 0,
    notes           TEXT,
    details         TEXT,
    created_at      TEXT NOT NULL,
    user_id         TEXT NOT NULL DEFAULT 'demo'
);
"""

_CREATE_MANUAL_INTERVENTIONS = """
CREATE TABLE IF NOT EXISTS manual_interventions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    opened_at       TEXT NOT NULL,
    severity        TEXT NOT NULL,
    category        TEXT NOT NULL,
    symbol          TEXT,
    source          TEXT NOT NULL,
    summary         TEXT NOT NULL,
    required_action TEXT NOT NULL,
    acknowledged_at TEXT,
    resolved_at     TEXT,
    resolved_by     TEXT,
    user_id         TEXT NOT NULL DEFAULT 'demo'
);
"""

_CREATE_REGIME_SNAPSHOTS = """
CREATE TABLE IF NOT EXISTS regime_snapshots (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp        TEXT NOT NULL,
    regime           TEXT NOT NULL,
    regime_features  TEXT NOT NULL,
    confidence       REAL,
    user_id          TEXT DEFAULT 'demo'
);
"""

# ── S10: Decision Ledger Tables ──────────────────────────────────────────────

_CREATE_AI_DECISION_RUNS = """
CREATE TABLE IF NOT EXISTS ai_decision_runs (
    id                   TEXT PRIMARY KEY,
    source               TEXT NOT NULL,
    mode                 TEXT NOT NULL,
    provider             TEXT,
    model                TEXT,
    prompt_version       TEXT,
    context_hash         TEXT NOT NULL,
    context_json         TEXT NOT NULL,
    reasoning            TEXT,
    aggregate_confidence REAL,
    abstained            INTEGER NOT NULL DEFAULT 0,
    input_tokens         INTEGER,
    output_tokens        INTEGER,
    latency_ms           INTEGER,
    status               TEXT NOT NULL DEFAULT 'created',
    error                TEXT,
    created_at           TEXT NOT NULL,
    completed_at         TEXT,
    user_id              TEXT NOT NULL DEFAULT 'demo'
);
"""

_CREATE_AI_DECISION_ITEMS = """
CREATE TABLE IF NOT EXISTS ai_decision_items (
    id                TEXT PRIMARY KEY,
    run_id            TEXT NOT NULL,
    item_index        INTEGER NOT NULL,
    item_type         TEXT NOT NULL,
    action_name       TEXT,
    target_key        TEXT,
    symbol            TEXT,
    proposed_json     TEXT NOT NULL,
    applied_json      TEXT,
    gate_status       TEXT NOT NULL DEFAULT 'pending',
    gate_reason       TEXT,
    confidence        REAL,
    regime            TEXT,
    origin_rule_id    TEXT,
    created_rule_id   TEXT,
    created_trade_id  TEXT,
    realized_trade_id TEXT,
    realized_pnl      REAL,
    realized_at       TEXT,
    score_status      TEXT NOT NULL DEFAULT 'unscored',
    score_source      TEXT,
    notes             TEXT,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    user_id           TEXT NOT NULL DEFAULT 'demo',
    FOREIGN KEY(run_id) REFERENCES ai_decision_runs(id) ON DELETE CASCADE
);
"""

_CREATE_AI_EVALUATION_RUNS = """
CREATE TABLE IF NOT EXISTS ai_evaluation_runs (
    id              TEXT PRIMARY KEY,
    candidate_type  TEXT NOT NULL,
    candidate_key   TEXT NOT NULL,
    baseline_key    TEXT,
    evaluation_mode TEXT NOT NULL,
    window_start    TEXT,
    window_end      TEXT,
    request_json    TEXT NOT NULL,
    summary_json    TEXT,
    status          TEXT NOT NULL DEFAULT 'queued',
    error           TEXT,
    created_at      TEXT NOT NULL,
    completed_at    TEXT,
    user_id         TEXT NOT NULL DEFAULT 'demo'
);
"""

_CREATE_AI_EVALUATION_SLICES = """
CREATE TABLE IF NOT EXISTS ai_evaluation_slices (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    evaluation_run_id TEXT NOT NULL,
    slice_type        TEXT NOT NULL,
    slice_key         TEXT NOT NULL,
    metrics_json      TEXT NOT NULL,
    created_at        TEXT NOT NULL,
    user_id           TEXT NOT NULL DEFAULT 'demo',
    FOREIGN KEY(evaluation_run_id) REFERENCES ai_evaluation_runs(id) ON DELETE CASCADE
);
"""

_ALLOWED_COLUMNS: set[tuple[str, str]] = {
    ("rules", "user_id"),
    ("trades", "user_id"),
    ("sim_account", "user_id"),
    ("sim_positions", "user_id"),
    ("sim_orders", "user_id"),
    ("ai_shadow_decisions", "delta_value"),
    ("ai_shadow_decisions", "confidence"),
    ("ai_shadow_decisions", "regime"),
    ("ai_audit_log", "param_type"),
    ("ai_audit_log", "regime"),
    ("ai_rule_validation_runs", "details"),
    ("ai_audit_log", "decision_run_id"),
    ("ai_audit_log", "decision_item_id"),
    ("ai_shadow_decisions", "decision_run_id"),
    ("ai_shadow_decisions", "decision_item_id"),
}


_SAFE_COL_TYPES = {"TEXT", "INTEGER", "REAL"}

async def _safe_add_column(db: aiosqlite.Connection, table: str, column: str, col_type: str, default: str) -> None:
    """Add a column if table exists and column doesn't."""
    if (table, column) not in _ALLOWED_COLUMNS:
        log.warning("Blocked ALTER on non-allowlisted column %s.%s", table, column)
        return
    # Validate col_type and default to prevent SQL injection in DDL
    if col_type not in _SAFE_COL_TYPES:
        log.warning("Blocked ALTER with unsafe col_type: %s", col_type)
        return
    if not (default.startswith("'") and default.endswith("'")) and default not in ("0", "1", "NULL"):
        log.warning("Blocked ALTER with unsafe default: %s", default)
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
        await db.execute("PRAGMA foreign_keys=ON")

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
        await db.execute(_CREATE_OPEN_POSITIONS)
        await db.execute(_CREATE_AI_GUARDRAILS)
        await db.execute(_CREATE_AI_AUDIT_LOG)
        await db.execute(_CREATE_AI_PARAMETER_SNAPSHOTS)
        await db.execute(_CREATE_AI_SHADOW_DECISIONS)
        await db.execute(_CREATE_AI_RULE_VERSIONS)
        await db.execute(_CREATE_AI_RULE_VALIDATION_RUNS)
        await db.execute(_CREATE_MANUAL_INTERVENTIONS)
        await db.execute(_CREATE_REGIME_SNAPSHOTS)
        # S10: decision ledger tables
        await db.execute(_CREATE_AI_DECISION_RUNS)
        await db.execute(_CREATE_AI_DECISION_ITEMS)
        await db.execute(_CREATE_AI_EVALUATION_RUNS)
        await db.execute(_CREATE_AI_EVALUATION_SLICES)
        await db.commit()

        # Migrate: add user_id column to existing tables
        await _safe_add_column(db, "rules",         "user_id", "TEXT", "'demo'")
        await _safe_add_column(db, "trades",        "user_id", "TEXT", "'demo'")
        await _safe_add_column(db, "sim_account",   "user_id", "TEXT", "'demo'")
        await _safe_add_column(db, "sim_positions", "user_id", "TEXT", "'demo'")
        await _safe_add_column(db, "sim_orders",    "user_id", "TEXT", "'demo'")
        # Phase 3.5: extend shadow + audit tables
        await _safe_add_column(db, "ai_shadow_decisions", "delta_value", "REAL", "NULL")
        await _safe_add_column(db, "ai_shadow_decisions", "confidence",  "REAL", "NULL")
        await _safe_add_column(db, "ai_shadow_decisions", "regime",      "TEXT", "NULL")
        await _safe_add_column(db, "ai_audit_log",        "param_type",  "TEXT", "NULL")
        await _safe_add_column(db, "ai_audit_log",        "regime",      "TEXT", "NULL")
        # S9: evidence details on validation runs
        await _safe_add_column(db, "ai_rule_validation_runs", "details", "TEXT", "NULL")
        # S10: decision ledger linkage on audit/shadow tables
        await _safe_add_column(db, "ai_audit_log", "decision_run_id", "TEXT", "NULL")
        await _safe_add_column(db, "ai_audit_log", "decision_item_id", "TEXT", "NULL")
        await _safe_add_column(db, "ai_shadow_decisions", "decision_run_id", "TEXT", "NULL")
        await _safe_add_column(db, "ai_shadow_decisions", "decision_item_id", "TEXT", "NULL")
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
        await db.execute("CREATE INDEX IF NOT EXISTS idx_open_positions_user ON open_positions(user_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_open_positions_symbol ON open_positions(symbol, user_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_ai_audit_log_ts ON ai_audit_log(timestamp DESC)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_ai_audit_log_status ON ai_audit_log(status, timestamp DESC)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_ai_snapshots_ts ON ai_parameter_snapshots(timestamp DESC)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_ai_shadow_ts ON ai_shadow_decisions(timestamp DESC)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_ai_rule_versions_rule ON ai_rule_versions(rule_id, version DESC)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_ai_rule_validation_rule ON ai_rule_validation_runs(rule_id, version DESC)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_manual_interventions_opened ON manual_interventions(opened_at DESC)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_manual_interventions_resolved ON manual_interventions(resolved_at, opened_at DESC)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_regime_snapshots_ts ON regime_snapshots(timestamp DESC)")
        # S10: decision ledger indexes
        await db.execute("CREATE INDEX IF NOT EXISTS idx_ai_decision_runs_created ON ai_decision_runs(created_at DESC)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_ai_decision_items_run ON ai_decision_items(run_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_ai_decision_items_type ON ai_decision_items(item_type)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_ai_decision_items_trade ON ai_decision_items(created_trade_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_ai_evaluation_runs_created ON ai_evaluation_runs(created_at DESC)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_ai_evaluation_slices_run ON ai_evaluation_slices(evaluation_run_id)")
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
    rule.updated_at = datetime.now(timezone.utc).isoformat()
    async with get_db() as db:
        await db.execute(
            "INSERT OR REPLACE INTO rules (id, data, user_id) VALUES (?, ?, ?)",
            (rule.id, rule.model_dump_json(), user_id),
        )
        await db.commit()


async def save_rule_version(
    rule: Rule,
    diff_summary: str | None = None,
    author: str = "ai",
    user_id: str = "demo",
) -> None:
    created_at = datetime.now(timezone.utc).isoformat()
    async with get_db() as db:
        await db.execute(
            "INSERT INTO ai_rule_versions "
            "(rule_id, version, snapshot, diff_summary, created_at, author, user_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                rule.id,
                rule.version,
                rule.model_dump_json(),
                diff_summary,
                created_at,
                author,
                user_id,
            ),
        )
        await db.commit()


async def get_rule_versions(rule_id: str, user_id: str = "demo") -> list[dict]:
    async with get_db() as db:
        async with db.execute(
            "SELECT version, snapshot, diff_summary, created_at, author "
            "FROM ai_rule_versions WHERE rule_id=? AND user_id=? "
            "ORDER BY version DESC, created_at DESC",
            (rule_id, user_id),
        ) as cur:
            rows = await cur.fetchall()
    versions: list[dict] = []
    for version, snapshot, diff_summary, created_at, author in rows:
        try:
            data = json.loads(snapshot)
        except Exception:
            data = {}
        versions.append({
            "version": version,
            "rule_id": rule_id,
            "name": data.get("name", ""),
            "conditions": data.get("conditions", []),
            "logic": data.get("logic", "AND"),
            "action": data.get("action", {}),
            "cooldown_minutes": data.get("cooldown_minutes", 60),
            "created_at": created_at,
            "note": diff_summary,
            "author": author,
            "status": data.get("status", "active"),
        })
    return versions


async def persist_rule_revision(
    rule: Rule,
    *,
    author: str = "ai",
    diff_summary: str | None = None,
    user_id: str = "demo",
) -> None:
    """Atomic: bump version, save rule + version snapshot in a single transaction."""
    rule.version = max(1, rule.version) + 1
    rule.updated_at = datetime.now(timezone.utc).isoformat()
    created_at = rule.updated_at
    async with get_db() as db:
        await db.execute(
            "INSERT OR REPLACE INTO rules (id, data, user_id) VALUES (?, ?, ?)",
            (rule.id, rule.model_dump_json(), user_id),
        )
        await db.execute(
            "INSERT INTO ai_rule_versions "
            "(rule_id, version, snapshot, diff_summary, created_at, author, user_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (rule.id, rule.version, rule.model_dump_json(), diff_summary, created_at, author, user_id),
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
    trade_id: str, status: str, fill_price: float | None = None, user_id: str = "demo"
) -> None:
    trade = None
    async with get_db() as db:
        async with db.execute("SELECT data FROM trades WHERE id=? AND user_id=?", (trade_id, user_id)) as cur:
            row = await cur.fetchone()
        if row:
            trade = Trade.model_validate(json.loads(row[0]))
            trade.status = status  # type: ignore[assignment]
            if fill_price is not None:
                trade.fill_price = fill_price
            await db.execute(
                "UPDATE trades SET data=? WHERE id=? AND user_id=?",
                (trade.model_dump_json(), trade_id, user_id),
            )
            await db.commit()


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
) -> Trade | None:
    """Finalize a trade's canonical outcome fields. Single source of truth for P&L."""
    if position_side not in ("BUY", "SELL"):
        raise ValueError(f"position_side must be 'BUY' or 'SELL', got '{position_side}'")
    async with get_db() as db:
        async with db.execute("SELECT data FROM trades WHERE id=? AND user_id=?", (trade_id, user_id)) as cur:
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

        await db.execute(
            "UPDATE trades SET data=? WHERE id=? AND user_id=?",
            (trade.model_dump_json(), trade_id, user_id),
        )
        await db.commit()

    # S10: link realized outcome back to originating decision item
    if trade.decision_id:
        try:
            from ai_decision_ledger import attach_realized_trade
            await attach_realized_trade(
                trade.decision_id, trade.id, realized_pnl, trade.closed_at,
            )
        except Exception as exc:
            log.warning("Failed to attach realized trade to decision item: %s", exc)

    return trade


async def save_rule_validation_run(
    *,
    rule_id: str,
    version: int,
    validation_mode: str,
    trades_count: int,
    hit_rate: float | None,
    net_pnl: float | None,
    expectancy: float | None,
    max_drawdown: float | None,
    overlap_score: float | None,
    passed: bool,
    notes: str | None = None,
    details: dict | None = None,
    user_id: str = "demo",
) -> None:
    created_at = datetime.now(timezone.utc).isoformat()
    details_json = json.dumps(details) if details else None
    async with get_db() as db:
        await db.execute(
            "INSERT INTO ai_rule_validation_runs "
            "(rule_id, version, validation_mode, trades_count, hit_rate, net_pnl, expectancy, "
            " max_drawdown, overlap_score, passed, notes, details, created_at, user_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                rule_id,
                version,
                validation_mode,
                trades_count,
                hit_rate,
                net_pnl,
                expectancy,
                max_drawdown,
                overlap_score,
                1 if passed else 0,
                notes,
                details_json,
                created_at,
                user_id,
            ),
        )
        await db.commit()


async def get_rule_validation_runs(rule_id: str, user_id: str = "demo") -> list[dict]:
    async with get_db() as db:
        async with db.execute(
            "SELECT version, validation_mode, trades_count, hit_rate, net_pnl, expectancy, "
            "max_drawdown, overlap_score, passed, notes, details, created_at "
            "FROM ai_rule_validation_runs WHERE rule_id=? AND user_id=? "
            "ORDER BY created_at DESC",
            (rule_id, user_id),
        ) as cur:
            rows = await cur.fetchall()
    results = []
    for row in rows:
        entry = {
            "version": row[0],
            "validation_mode": row[1],
            "trades_count": row[2],
            "hit_rate": row[3],
            "net_pnl": row[4],
            "expectancy": row[5],
            "max_drawdown": row[6],
            "overlap_score": row[7],
            "passed": bool(row[8]),
            "notes": row[9],
            "created_at": row[11],
        }
        # S9: flatten details JSON into the response dict
        if row[10]:
            try:
                details = json.loads(row[10])
                entry.update(details)
            except Exception:
                pass
        results.append(entry)
    return [
        entry
        for entry in results
    ]


async def open_manual_intervention(
    *,
    severity: str,
    category: str,
    source: str,
    summary: str,
    required_action: str,
    symbol: str | None = None,
    user_id: str = "demo",
) -> int:
    opened_at = datetime.now(timezone.utc).isoformat()
    async with get_db() as db:
        cur = await db.execute(
            "INSERT INTO manual_interventions "
            "(opened_at, severity, category, symbol, source, summary, required_action, user_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (opened_at, severity, category, symbol, source, summary, required_action, user_id),
        )
        await db.commit()
        return int(cur.lastrowid or 0)


async def get_manual_interventions(user_id: str = "demo", include_resolved: bool = False) -> list[dict]:
    where = "WHERE user_id=?" if include_resolved else "WHERE user_id=? AND resolved_at IS NULL"
    params: tuple[object, ...] = (user_id,)
    async with get_db() as db:
        async with db.execute(
            f"SELECT id, opened_at, severity, category, symbol, source, summary, required_action, "
            f"acknowledged_at, resolved_at, resolved_by FROM manual_interventions {where} "
            f"ORDER BY opened_at DESC",
            params,
        ) as cur:
            rows = await cur.fetchall()
    return [
        {
            "id": row[0],
            "opened_at": row[1],
            "severity": row[2],
            "category": row[3],
            "symbol": row[4],
            "source": row[5],
            "summary": row[6],
            "required_action": row[7],
            "acknowledged_at": row[8],
            "resolved_at": row[9],
            "resolved_by": row[10],
        }
        for row in rows
    ]


async def acknowledge_manual_intervention(intervention_id: int, user_id: str = "demo") -> bool:
    acknowledged_at = datetime.now(timezone.utc).isoformat()
    async with get_db() as db:
        cur = await db.execute(
            "UPDATE manual_interventions SET acknowledged_at=? "
            "WHERE id=? AND user_id=? AND acknowledged_at IS NULL",
            (acknowledged_at, intervention_id, user_id),
        )
        await db.commit()
        return cur.rowcount > 0


async def resolve_manual_intervention(intervention_id: int, resolved_by: str = "operator", user_id: str = "demo") -> bool:
    resolved_at = datetime.now(timezone.utc).isoformat()
    async with get_db() as db:
        cur = await db.execute(
            "UPDATE manual_interventions SET resolved_at=?, resolved_by=? "
            "WHERE id=? AND user_id=? AND resolved_at IS NULL",
            (resolved_at, resolved_by, intervention_id, user_id),
        )
        await db.commit()
        return cur.rowcount > 0


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


# ---------------------------------------------------------------------------
# Open positions CRUD (exit tracker)
# ---------------------------------------------------------------------------

async def save_open_position(pos: OpenPosition, user_id: str = "demo") -> None:
    """Upsert an open position (insert on entry, replace on watermark update)."""
    async with get_db() as db:
        await db.execute(
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
        await db.commit()


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


async def delete_open_position(trade_id: str, user_id: str = "demo") -> bool:
    """Remove a tracked position on exit. Returns True if a row was deleted."""
    async with get_db() as db:
        cur = await db.execute(
            "DELETE FROM open_positions WHERE id=? AND user_id=?",
            (trade_id, user_id),
        )
        await db.commit()
        return cur.rowcount > 0

"""Database core — connection management, schema DDL, and initialization."""
from __future__ import annotations
import json
import logging
from contextlib import asynccontextmanager
import aiosqlite
from config import cfg

log = logging.getLogger(__name__)

DB_PATH = cfg.DB_PATH

@asynccontextmanager
async def get_db():
    """Open a DB connection with WAL mode and busy_timeout configured."""
    async with aiosqlite.connect(cfg.DB_PATH) as db:
        await db.execute("PRAGMA journal_mode=WAL")
        # FULL sync: trades involve real money — never lose a write on crash
        await db.execute("PRAGMA synchronous=FULL")
        await db.execute("PRAGMA busy_timeout=10000")
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
    async with aiosqlite.connect(cfg.DB_PATH) as db:
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
        from db.validation import _seed_starter_rules
        await _seed_starter_rules(db)

        # Seed screener presets
        from db.screener import _seed_screener_presets
        await _seed_screener_presets(db)


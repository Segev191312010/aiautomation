"""Schema for the watchlist universe and live screener snapshots.

The universe maps an owner's watchlist symbols to qualified IBKR contracts;
daily rows hold that day's liquidity qualification; screener snapshots are
append-only ranked sweeps the dashboard and strategies read.
"""
from __future__ import annotations


LIVE_MARKET_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS autotrader_universe (
        source TEXT NOT NULL,
        symbol TEXT NOT NULL,
        listing_exchange TEXT NOT NULL,
        ibkr_symbol TEXT NOT NULL,
        con_id INTEGER NULL CHECK(con_id IS NULL OR con_id > 0),
        primary_exchange TEXT NULL,
        status TEXT NOT NULL CHECK(status IN ('pending','resolved','unresolved')),
        last_error TEXT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY(source, symbol)
    )""",
    """CREATE INDEX IF NOT EXISTS idx_autotrader_universe_con_id
        ON autotrader_universe(con_id)""",
    """CREATE TABLE IF NOT EXISTS autotrader_universe_daily (
        session_date TEXT NOT NULL,
        con_id INTEGER NOT NULL CHECK(con_id > 0),
        symbol TEXT NOT NULL,
        prior_close TEXT NULL,
        avg_daily_volume_shares TEXT NULL,
        avg_dollar_volume TEXT NULL,
        daily_atr TEXT NULL,
        qualified INTEGER NOT NULL CHECK(qualified IN (0, 1)),
        reason TEXT NULL,
        observed_at TEXT NOT NULL,
        PRIMARY KEY(session_date, con_id)
    )""",
    """CREATE TABLE IF NOT EXISTS autotrader_screener_snapshots (
        id TEXT PRIMARY KEY,
        session_date TEXT NOT NULL,
        observed_at TEXT NOT NULL,
        payload_json TEXT NOT NULL CHECK(json_valid(payload_json))
    )""",
    """CREATE INDEX IF NOT EXISTS idx_autotrader_screener_snapshots_observed
        ON autotrader_screener_snapshots(session_date, observed_at)""",
)


__all__ = ["LIVE_MARKET_SCHEMA"]

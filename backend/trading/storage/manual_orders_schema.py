"""Schema for owner manual orders, Telegram intake, chat budget and bridge liveness.

Migration 12. Rows in ``autotrader_manual_orders`` are written only from the
deterministic order parser (never from a language model) and consumed only by
the PAPER bridge. The triggers make the ledger append-only, its order intent
immutable, and its status monotonic: an order can never return to ``queued``,
only ``queued`` can become ``sending``, and terminal rows never change status,
so no code path can resend, resize or resurrect an order.

Migration 17 (``MANUAL_ENTRY_STOP_SCHEMA``) adds a buy's protective stop.
"""
from __future__ import annotations


MANUAL_ORDERS_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS autotrader_manual_orders (
        id TEXT PRIMARY KEY CHECK(length(id) = 32 AND id NOT GLOB '*[^0-9a-f]*'),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        chat_id INTEGER NOT NULL,
        message_id INTEGER NOT NULL CHECK(message_id > 0),
        update_id INTEGER NOT NULL CHECK(update_id >= 0),
        source TEXT NOT NULL CHECK(source IN ('command','text')),
        raw_text TEXT NOT NULL CHECK(length(raw_text) BETWEEN 1 AND 4096),
        action TEXT NOT NULL CHECK(action IN (
            'BUY','SELL','SELL_STOP','CLOSE','CLOSE_ALL','CANCEL','CANCEL_ALL'
        )),
        symbol TEXT NULL,
        con_id INTEGER NULL CHECK(con_id IS NULL OR con_id > 0),
        primary_exchange TEXT NULL,
        quantity INTEGER NULL CHECK(quantity IS NULL OR quantity > 0),
        order_type TEXT NULL CHECK(order_type IS NULL OR order_type IN ('MKT','LMT','STP')),
        limit_price TEXT NULL,
        stop_price TEXT NULL,
        target_order_ref TEXT NULL,
        status TEXT NOT NULL CHECK(status IN (
            'queued','sending','sent','partially_filled','filled',
            'cancelled','rejected','expired','unknown','error'
        )),
        reject_code TEXT NULL,
        error TEXT NULL,
        ibkr_order_id INTEGER NULL CHECK(ibkr_order_id IS NULL OR ibkr_order_id > 0),
        perm_id INTEGER NULL CHECK(perm_id IS NULL OR perm_id > 0),
        filled_qty INTEGER NOT NULL DEFAULT 0 CHECK(filled_qty >= 0),
        avg_fill_price TEXT NULL,
        expires_at TEXT NOT NULL,
        cancel_requested INTEGER NOT NULL DEFAULT 0 CHECK(cancel_requested IN (0, 1)),
        audit_json TEXT NOT NULL CHECK(json_valid(audit_json)),
        UNIQUE(chat_id, message_id),
        CHECK(action NOT IN ('BUY','SELL','SELL_STOP')
              OR (symbol IS NOT NULL AND con_id IS NOT NULL
                  AND quantity IS NOT NULL AND order_type IS NOT NULL)),
        CHECK(action NOT IN ('BUY','SELL') OR order_type IN ('MKT','LMT')),
        CHECK(action != 'SELL_STOP' OR order_type = 'STP'),
        CHECK(action != 'CLOSE'
              OR (symbol IS NOT NULL AND (order_type IS NULL OR order_type IN ('MKT','LMT')))),
        CHECK(action NOT IN ('CLOSE_ALL','CANCEL','CANCEL_ALL')
              OR (symbol IS NULL AND quantity IS NULL AND order_type IS NULL)),
        CHECK(action NOT IN ('CANCEL','CANCEL_ALL') OR target_order_ref IS NOT NULL),
        CHECK(order_type IS NOT 'LMT' OR limit_price IS NOT NULL),
        CHECK(limit_price IS NULL OR order_type = 'LMT'),
        CHECK(order_type IS NOT 'STP' OR stop_price IS NOT NULL),
        CHECK(stop_price IS NULL OR order_type = 'STP')
    )""",
    """CREATE INDEX IF NOT EXISTS idx_autotrader_manual_orders_status
        ON autotrader_manual_orders(status, created_at)""",
    """CREATE TRIGGER IF NOT EXISTS autotrader_manual_orders_intent_immutable
    BEFORE UPDATE OF id, created_at, chat_id, message_id, update_id, source, raw_text,
                     action, symbol, con_id, primary_exchange, quantity, order_type,
                     limit_price, stop_price, target_order_ref, expires_at
    ON autotrader_manual_orders
    WHEN NEW.id IS NOT OLD.id
      OR NEW.created_at IS NOT OLD.created_at
      OR NEW.chat_id IS NOT OLD.chat_id
      OR NEW.message_id IS NOT OLD.message_id
      OR NEW.update_id IS NOT OLD.update_id
      OR NEW.source IS NOT OLD.source
      OR NEW.raw_text IS NOT OLD.raw_text
      OR NEW.action IS NOT OLD.action
      OR NEW.symbol IS NOT OLD.symbol
      OR NEW.con_id IS NOT OLD.con_id
      OR NEW.primary_exchange IS NOT OLD.primary_exchange
      OR NEW.quantity IS NOT OLD.quantity
      OR NEW.order_type IS NOT OLD.order_type
      OR NEW.limit_price IS NOT OLD.limit_price
      OR NEW.stop_price IS NOT OLD.stop_price
      OR NEW.target_order_ref IS NOT OLD.target_order_ref
      OR NEW.expires_at IS NOT OLD.expires_at
    BEGIN SELECT RAISE(ABORT, 'manual order intent is immutable'); END""",
    """CREATE TRIGGER IF NOT EXISTS autotrader_manual_orders_status_monotonic
    BEFORE UPDATE OF status ON autotrader_manual_orders
    WHEN NEW.status IS NOT OLD.status AND (
        NEW.status = 'queued'
        OR (NEW.status = 'sending' AND OLD.status != 'queued')
        OR (NEW.status = 'expired' AND OLD.status != 'queued')
        OR OLD.status IN ('filled','cancelled','rejected','expired','error')
    )
    BEGIN SELECT RAISE(ABORT, 'manual order status cannot move backwards'); END""",
    """CREATE TRIGGER IF NOT EXISTS autotrader_manual_orders_fills_monotonic
    BEFORE UPDATE OF filled_qty ON autotrader_manual_orders
    WHEN NEW.filled_qty < OLD.filled_qty
      OR (OLD.quantity IS NOT NULL AND NEW.filled_qty > OLD.quantity)
    BEGIN SELECT RAISE(ABORT, 'manual order fills never decrease or exceed the order'); END""",
    """CREATE TRIGGER IF NOT EXISTS autotrader_manual_orders_broker_identity_immutable
    BEFORE UPDATE OF ibkr_order_id, perm_id ON autotrader_manual_orders
    WHEN (OLD.ibkr_order_id IS NOT NULL AND NEW.ibkr_order_id IS NOT OLD.ibkr_order_id)
      OR (OLD.perm_id IS NOT NULL AND NEW.perm_id IS NOT OLD.perm_id)
    BEGIN SELECT RAISE(ABORT, 'manual order broker identity is immutable'); END""",
    """CREATE TRIGGER IF NOT EXISTS autotrader_manual_orders_cancel_one_way
    BEFORE UPDATE OF cancel_requested ON autotrader_manual_orders
    WHEN OLD.cancel_requested = 1 AND NEW.cancel_requested = 0
    BEGIN SELECT RAISE(ABORT, 'a manual order cancel request cannot be withdrawn'); END""",
    """CREATE TRIGGER IF NOT EXISTS autotrader_manual_orders_no_delete
    BEFORE DELETE ON autotrader_manual_orders
    BEGIN SELECT RAISE(ABORT, 'manual orders are append-only'); END""",
    """CREATE TABLE IF NOT EXISTS autotrader_telegram_updates (
        update_id INTEGER PRIMARY KEY CHECK(update_id >= 0),
        received_at TEXT NOT NULL,
        chat_id INTEGER NOT NULL,
        message_id INTEGER NULL,
        kind TEXT NOT NULL CHECK(length(kind) BETWEEN 1 AND 64),
        outcome TEXT NULL,
        processed_at TEXT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS autotrader_chat_usage (
        usage_date TEXT PRIMARY KEY
            CHECK(usage_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
        calls INTEGER NOT NULL DEFAULT 0 CHECK(calls >= 0),
        reserved_usd TEXT NOT NULL DEFAULT '0',
        spent_usd TEXT NOT NULL DEFAULT '0'
    )""",
    """CREATE TABLE IF NOT EXISTS autotrader_bridge_heartbeat (
        id INTEGER PRIMARY KEY CHECK(id = 1),
        updated_at TEXT NOT NULL,
        pid INTEGER NOT NULL CHECK(pid > 0),
        phase TEXT NOT NULL CHECK(length(phase) BETWEEN 1 AND 64)
    )""",
)


# Migration 17 (v7c, owner decision D5 of 2026-10-18): an owner buy must carry
# its protective stop. The stop gets its own column because migration 12's
# CHECKs keep ``stop_price`` for SELL_STOP's trigger alone. Spike V-3
# (2026-10-21, SQLite 3.50.4): ADD COLUMN with this CHECK works inside the
# migration's transaction and leaves every earlier row NULL; migration 12's
# intent trigger lists its columns (BEFORE UPDATE OF ...), so it cannot see
# the new one, and a separate trigger keeps it immutable instead of
# recreating the reviewed v12 trigger. The INSERT trigger is the database's
# own refusal of a buy without a stop; earlier buy rows are never re-checked
# (revalidate_claimed refuses to send one).
MANUAL_ENTRY_STOP_MIGRATION = 17
MANUAL_BUY_NEEDS_STOP = "manual_buy_needs_stop"
MANUAL_ENTRY_STOP_SCHEMA = (
    """ALTER TABLE autotrader_manual_orders ADD COLUMN entry_stop_price TEXT NULL
        CHECK(entry_stop_price IS NULL OR (
            action = 'BUY'
            AND CAST(entry_stop_price AS REAL) > 0
            AND (limit_price IS NULL OR CAST(entry_stop_price AS REAL) < CAST(limit_price AS REAL))
        ))""",
    f"""CREATE TRIGGER IF NOT EXISTS autotrader_manual_orders_buy_needs_stop
    BEFORE INSERT ON autotrader_manual_orders
    WHEN NEW.action = 'BUY' AND NEW.entry_stop_price IS NULL
    BEGIN SELECT RAISE(ABORT, '{MANUAL_BUY_NEEDS_STOP}'); END""",
    """CREATE TRIGGER IF NOT EXISTS autotrader_manual_orders_entry_stop_immutable
    BEFORE UPDATE OF entry_stop_price ON autotrader_manual_orders
    WHEN NEW.entry_stop_price IS NOT OLD.entry_stop_price
    BEGIN SELECT RAISE(ABORT, 'manual order intent is immutable'); END""",
)


__all__ = [
    "MANUAL_BUY_NEEDS_STOP",
    "MANUAL_ENTRY_STOP_MIGRATION",
    "MANUAL_ENTRY_STOP_SCHEMA",
    "MANUAL_ORDERS_SCHEMA",
]

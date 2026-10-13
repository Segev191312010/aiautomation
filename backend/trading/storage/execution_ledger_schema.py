"""Schema for the reviewed execution engine's ledger (the execution-ledger migration).

The engine (``trading/execution/live_executor.py``) is built but not wired:
nothing in the supervisor runs it, and the PAPER bridge stays the only order
sender until governance replaces it. These tables are therefore empty on the
owner's database until then.

Safety properties the triggers enforce, so no code path can bypass them:

* PAPER authority records are append-only; a grant binds the engine's code
  digest, so a code change needs a new explicit grant.
* A decision enters the outbox exactly once (primary key) and only as
  ``sending`` or ``skipped``; ``sending`` may become ``sent`` or ``unknown``
  and every other state is terminal, so a decision can never be sent twice.
* A bracket can only be created for a decision whose outbox row is already
  ``sending`` with that bracket id: intent is persisted before any order.
* Every leg's orderRef is ``at1:<bracket>:<leg>`` and its broker identity and
  placed intent are immutable; fills and exit fills never decrease.
* Fills are keyed by IBKR execId, so a restart that replays executions can
  never count a fill twice.
* A flatten attempt is one-way: it can never be reset to "not attempted".
* Order events and risk reservations are append-only or one-way.
"""
from __future__ import annotations


# The ledger migration's number: the ONLY place it is written. It is 13 on this branch; the main
# line takes 13 for its own tables, so this becomes the next free number when the engine merges
# (its checksum, which covers the number, is pinned again then). Everything else derives from it.
EXECUTION_LEDGER_MIGRATION = 13

EXECUTION_LEDGER_SCHEMA = (
    # ---- PAPER authority: explicit, append-only promotion records ---------------
    """CREATE TABLE IF NOT EXISTS autotrader_exec_promotions (
        seq INTEGER PRIMARY KEY,
        id TEXT UNIQUE NOT NULL CHECK(length(id) = 32 AND id NOT GLOB '*[^0-9a-f]*'),
        action TEXT NOT NULL CHECK(action IN ('grant','revoke')),
        strategy_name TEXT NOT NULL
            CHECK(length(strategy_name) BETWEEN 3 AND 120 AND strategy_name = trim(strategy_name)),
        account TEXT NOT NULL CHECK(account GLOB 'DU?*' AND length(account) <= 32),
        code_digest TEXT NULL
            CHECK(code_digest IS NULL OR (length(code_digest) = 64 AND code_digest NOT GLOB '*[^0-9a-f]*')),
        actor TEXT NOT NULL CHECK(length(trim(actor)) BETWEEN 1 AND 128),
        reason TEXT NOT NULL CHECK(length(trim(reason)) BETWEEN 1 AND 500),
        created_at TEXT NOT NULL,
        CHECK((action = 'grant') = (code_digest IS NOT NULL))
    )""",
    """CREATE TRIGGER IF NOT EXISTS autotrader_exec_promotions_no_update
    BEFORE UPDATE ON autotrader_exec_promotions
    BEGIN SELECT RAISE(ABORT, 'execution promotions are append-only'); END""",
    """CREATE TRIGGER IF NOT EXISTS autotrader_exec_promotions_no_delete
    BEFORE DELETE ON autotrader_exec_promotions
    BEGIN SELECT RAISE(ABORT, 'execution promotions are append-only'); END""",
    # ---- outbox: every approved decision the engine considered, keyed by id -----
    """CREATE TABLE IF NOT EXISTS autotrader_exec_outbox (
        decision_id TEXT PRIMARY KEY,
        book_id TEXT NOT NULL,
        account TEXT NOT NULL CHECK(account GLOB 'DU?*'),
        session_date TEXT NOT NULL
            CHECK(session_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
        strategy_name TEXT NOT NULL,
        symbol TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('sending','sent','skipped','unknown')),
        reason_code TEXT NULL CHECK(reason_code IS NULL OR length(reason_code) BETWEEN 1 AND 64),
        reason TEXT NULL,
        bracket_id TEXT NULL UNIQUE,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        CHECK(book_id = account || ':' || session_date),
        CHECK((state = 'skipped') = (bracket_id IS NULL)),
        CHECK(state NOT IN ('skipped','unknown') OR reason_code IS NOT NULL),
        FOREIGN KEY(decision_id) REFERENCES autotrader_candidate_decisions(id)
    )""",
    """CREATE INDEX IF NOT EXISTS idx_autotrader_exec_outbox_book
        ON autotrader_exec_outbox(book_id, state)""",
    """CREATE TRIGGER IF NOT EXISTS autotrader_exec_outbox_insert_state
    BEFORE INSERT ON autotrader_exec_outbox
    WHEN NEW.state NOT IN ('sending','skipped')
    BEGIN SELECT RAISE(ABORT, 'an outbox row starts as sending or skipped'); END""",
    """CREATE TRIGGER IF NOT EXISTS autotrader_exec_outbox_identity_immutable
    BEFORE UPDATE OF decision_id, book_id, account, session_date, strategy_name, symbol,
                     bracket_id, created_at
    ON autotrader_exec_outbox
    WHEN NEW.decision_id IS NOT OLD.decision_id
      OR NEW.book_id IS NOT OLD.book_id
      OR NEW.account IS NOT OLD.account
      OR NEW.session_date IS NOT OLD.session_date
      OR NEW.strategy_name IS NOT OLD.strategy_name
      OR NEW.symbol IS NOT OLD.symbol
      OR NEW.bracket_id IS NOT OLD.bracket_id
      OR NEW.created_at IS NOT OLD.created_at
    BEGIN SELECT RAISE(ABORT, 'outbox identity is immutable'); END""",
    """CREATE TRIGGER IF NOT EXISTS autotrader_exec_outbox_state_monotonic
    BEFORE UPDATE OF state ON autotrader_exec_outbox
    WHEN NEW.state IS NOT OLD.state
     AND NOT (OLD.state = 'sending' AND NEW.state IN ('sent','unknown'))
    BEGIN SELECT RAISE(ABORT, 'outbox state can only move sending -> sent|unknown'); END""",
    """CREATE TRIGGER IF NOT EXISTS autotrader_exec_outbox_no_delete
    BEFORE DELETE ON autotrader_exec_outbox
    BEGIN SELECT RAISE(ABORT, 'the execution outbox is append-only'); END""",
    # ---- brackets: one native IBKR bracket per sent decision ---------------------
    """CREATE TABLE IF NOT EXISTS autotrader_exec_brackets (
        id TEXT PRIMARY KEY CHECK(length(id) = 12 AND id NOT GLOB '*[^0-9a-f]*'),
        decision_id TEXT UNIQUE NOT NULL,
        book_id TEXT NOT NULL,
        account TEXT NOT NULL CHECK(account GLOB 'DU?*'),
        client_id INTEGER NOT NULL CHECK(client_id > 0),
        session_date TEXT NOT NULL,
        strategy_name TEXT NOT NULL,
        symbol TEXT NOT NULL CHECK(length(symbol) BETWEEN 1 AND 32),
        con_id INTEGER NOT NULL CHECK(con_id > 0),
        primary_exchange TEXT NOT NULL CHECK(length(trim(primary_exchange)) > 0),
        quantity INTEGER NOT NULL CHECK(quantity > 0),
        entry_price TEXT NOT NULL,
        stop_price TEXT NOT NULL,
        target_price TEXT NOT NULL,
        setup_fingerprint TEXT NULL,
        state TEXT NOT NULL CHECK(state IN (
            'sending','working','open','closed','cancelled','unknown','needs_attention'
        )),
        entry_filled INTEGER NOT NULL DEFAULT 0 CHECK(entry_filled >= 0 AND entry_filled <= quantity),
        avg_entry TEXT NULL,
        exit_filled INTEGER NOT NULL DEFAULT 0 CHECK(exit_filled >= 0),
        avg_exit TEXT NULL,
        exit_reason TEXT NULL,
        realized_pnl TEXT NOT NULL DEFAULT '0',
        commissions TEXT NOT NULL DEFAULT '0',
        parent_cancel_requested INTEGER NOT NULL DEFAULT 0 CHECK(parent_cancel_requested IN (0,1)),
        resized_qty INTEGER NOT NULL DEFAULT 0 CHECK(resized_qty >= 0),
        flatten_attempted INTEGER NOT NULL DEFAULT 0 CHECK(flatten_attempted IN (0,1)),
        flatten_state TEXT NOT NULL DEFAULT 'none' CHECK(flatten_state IN (
            'none','repricing','repriced','cancelling','selling','skipped','failed'
        )),
        flatten_price TEXT NULL,
        attention_reason TEXT NULL,
        attention_cleared_at TEXT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        CHECK(book_id = account || ':' || session_date),
        CHECK(attention_cleared_at IS NULL OR state = 'needs_attention'),
        CHECK(CAST(stop_price AS REAL) > 0
              AND CAST(stop_price AS REAL) < CAST(entry_price AS REAL)
              AND CAST(entry_price AS REAL) < CAST(target_price AS REAL)
              AND CAST(target_price AS REAL) < 1e308),
        CHECK(flatten_state = 'none' OR flatten_attempted = 1),
        FOREIGN KEY(decision_id) REFERENCES autotrader_exec_outbox(decision_id)
    )""",
    """CREATE INDEX IF NOT EXISTS idx_autotrader_exec_brackets_account
        ON autotrader_exec_brackets(account, state)""",
    """CREATE INDEX IF NOT EXISTS idx_autotrader_exec_brackets_book
        ON autotrader_exec_brackets(book_id)""",
    """CREATE TRIGGER IF NOT EXISTS autotrader_exec_brackets_need_sending_outbox
    BEFORE INSERT ON autotrader_exec_brackets
    WHEN NEW.state != 'sending' OR NOT EXISTS (
        SELECT 1 FROM autotrader_exec_outbox o
        WHERE o.decision_id = NEW.decision_id AND o.bracket_id = NEW.id
          AND o.state = 'sending' AND o.book_id = NEW.book_id
    )
    BEGIN SELECT RAISE(ABORT, 'a bracket needs its decision claimed as sending first'); END""",
    """CREATE TRIGGER IF NOT EXISTS autotrader_exec_brackets_intent_immutable
    BEFORE UPDATE OF id, decision_id, book_id, account, client_id, session_date, strategy_name,
                     symbol, con_id, primary_exchange, quantity, entry_price, stop_price,
                     target_price, setup_fingerprint, created_at
    ON autotrader_exec_brackets
    WHEN NEW.id IS NOT OLD.id
      OR NEW.decision_id IS NOT OLD.decision_id
      OR NEW.book_id IS NOT OLD.book_id
      OR NEW.account IS NOT OLD.account
      OR NEW.client_id IS NOT OLD.client_id
      OR NEW.session_date IS NOT OLD.session_date
      OR NEW.strategy_name IS NOT OLD.strategy_name
      OR NEW.symbol IS NOT OLD.symbol
      OR NEW.con_id IS NOT OLD.con_id
      OR NEW.primary_exchange IS NOT OLD.primary_exchange
      OR NEW.quantity IS NOT OLD.quantity
      OR NEW.entry_price IS NOT OLD.entry_price
      OR NEW.stop_price IS NOT OLD.stop_price
      OR NEW.target_price IS NOT OLD.target_price
      OR NEW.setup_fingerprint IS NOT OLD.setup_fingerprint
      OR NEW.created_at IS NOT OLD.created_at
    BEGIN SELECT RAISE(ABORT, 'bracket intent is immutable'); END""",
    """CREATE TRIGGER IF NOT EXISTS autotrader_exec_brackets_fills_monotonic
    BEFORE UPDATE OF entry_filled, exit_filled ON autotrader_exec_brackets
    WHEN NEW.entry_filled < OLD.entry_filled OR NEW.exit_filled < OLD.exit_filled
    BEGIN SELECT RAISE(ABORT, 'bracket fills never decrease'); END""",
    """CREATE TRIGGER IF NOT EXISTS autotrader_exec_brackets_flatten_one_way
    BEFORE UPDATE OF flatten_attempted, flatten_state ON autotrader_exec_brackets
    WHEN (OLD.flatten_attempted = 1 AND NEW.flatten_attempted = 0)
      OR (OLD.flatten_state != 'none' AND NEW.flatten_state = 'none')
      OR (OLD.flatten_state IN ('repriced','selling','skipped','failed')
          AND NEW.flatten_state != OLD.flatten_state)
      OR (OLD.flatten_state = 'cancelling' AND NEW.flatten_state = 'repricing')
    BEGIN SELECT RAISE(ABORT, 'a flatten attempt is never undone'); END""",
    """CREATE TRIGGER IF NOT EXISTS autotrader_exec_brackets_state_transitions
    BEFORE UPDATE OF state ON autotrader_exec_brackets
    WHEN NEW.state IS NOT OLD.state AND NOT (
           (OLD.state = 'sending' AND NEW.state IN ('working','unknown','needs_attention'))
        -- an entry can fill and exit between two saves: working -> closed passes through open
        OR (OLD.state = 'working' AND NEW.state IN ('open','closed','cancelled','needs_attention'))
        OR (OLD.state = 'open' AND NEW.state IN ('closed','needs_attention'))
        OR (OLD.state = 'closed' AND NEW.state IN ('open','needs_attention'))
        OR (OLD.state = 'cancelled' AND NEW.state IN ('open','closed','needs_attention'))
        OR (OLD.state = 'unknown' AND NEW.state = 'needs_attention')
    )
    BEGIN SELECT RAISE(ABORT, 'unsafe bracket state transition'); END""",
    """CREATE TRIGGER IF NOT EXISTS autotrader_exec_brackets_attention_cleared_once
    BEFORE UPDATE OF attention_cleared_at ON autotrader_exec_brackets
    WHEN OLD.attention_cleared_at IS NOT NULL
     AND NEW.attention_cleared_at IS NOT OLD.attention_cleared_at
    BEGIN SELECT RAISE(ABORT, 'a cleared needs-attention bracket stays cleared'); END""",
    """CREATE TRIGGER IF NOT EXISTS autotrader_exec_brackets_no_delete
    BEFORE DELETE ON autotrader_exec_brackets
    BEGIN SELECT RAISE(ABORT, 'execution brackets are append-only'); END""",
    # ---- legs: parent P, target T, stop S, and the fallback flatten sell F ------
    """CREATE TABLE IF NOT EXISTS autotrader_exec_legs (
        id TEXT PRIMARY KEY,
        bracket_id TEXT NOT NULL,
        leg TEXT NOT NULL CHECK(leg IN ('P','T','S','F')),
        client_id INTEGER NOT NULL CHECK(client_id > 0),
        order_id INTEGER NOT NULL CHECK(order_id > 0),
        order_ref TEXT UNIQUE NOT NULL,
        action TEXT NOT NULL CHECK(action IN ('BUY','SELL')),
        order_type TEXT NOT NULL CHECK(order_type IN ('LMT','STP','MKT')),
        tif TEXT NOT NULL CHECK(tif IN ('DAY','GTC')),
        quantity INTEGER NOT NULL CHECK(quantity > 0),
        price TEXT NULL,
        parent_order_id INTEGER NULL CHECK(parent_order_id IS NULL OR parent_order_id > 0),
        transmit INTEGER NOT NULL CHECK(transmit IN (0,1)),
        state TEXT NOT NULL CHECK(state IN ('staged','sent','unknown')),
        broker_status TEXT NULL,
        perm_id INTEGER NULL CHECK(perm_id IS NULL OR perm_id > 0),
        filled INTEGER NOT NULL DEFAULT 0 CHECK(filled >= 0),
        working_quantity INTEGER NULL CHECK(working_quantity IS NULL OR working_quantity >= 0),
        working_price TEXT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(bracket_id, leg),
        UNIQUE(client_id, order_id),
        CHECK(id = bracket_id || ':' || leg),
        CHECK(order_ref = 'at1:' || bracket_id || ':' || leg),
        CHECK((leg = 'P') = (action = 'BUY')),
        CHECK(leg != 'P' OR (order_type = 'LMT' AND tif = 'DAY' AND transmit = 0
                             AND parent_order_id IS NULL)),
        CHECK(leg != 'T' OR (order_type = 'LMT' AND tif = 'GTC' AND transmit = 0
                             AND parent_order_id IS NOT NULL)),
        CHECK(leg != 'S' OR (order_type = 'STP' AND tif = 'GTC' AND transmit = 1
                             AND parent_order_id IS NOT NULL)),
        CHECK(leg != 'F' OR (order_type = 'MKT' AND tif = 'DAY' AND transmit = 1
                             AND parent_order_id IS NULL)),
        CHECK((order_type = 'MKT') = (price IS NULL)),
        CHECK(price IS NULL OR (CAST(price AS REAL) > 0 AND CAST(price AS REAL) < 1e308)),
        FOREIGN KEY(bracket_id) REFERENCES autotrader_exec_brackets(id)
    )""",
    """CREATE TRIGGER IF NOT EXISTS autotrader_exec_legs_intent_immutable
    BEFORE UPDATE OF id, bracket_id, leg, client_id, order_id, order_ref, action, order_type,
                     tif, quantity, price, parent_order_id, transmit, created_at
    ON autotrader_exec_legs
    WHEN NEW.id IS NOT OLD.id
      OR NEW.bracket_id IS NOT OLD.bracket_id
      OR NEW.leg IS NOT OLD.leg
      OR NEW.client_id IS NOT OLD.client_id
      OR NEW.order_id IS NOT OLD.order_id
      OR NEW.order_ref IS NOT OLD.order_ref
      OR NEW.action IS NOT OLD.action
      OR NEW.order_type IS NOT OLD.order_type
      OR NEW.tif IS NOT OLD.tif
      OR NEW.quantity IS NOT OLD.quantity
      OR NEW.price IS NOT OLD.price
      OR NEW.parent_order_id IS NOT OLD.parent_order_id
      OR NEW.transmit IS NOT OLD.transmit
      OR NEW.created_at IS NOT OLD.created_at
    BEGIN SELECT RAISE(ABORT, 'leg intent and broker identity are immutable'); END""",
    """CREATE TRIGGER IF NOT EXISTS autotrader_exec_legs_state_monotonic
    BEFORE UPDATE OF state ON autotrader_exec_legs
    WHEN NEW.state IS NOT OLD.state
     AND NOT (OLD.state = 'staged' AND NEW.state IN ('sent','unknown'))
    BEGIN SELECT RAISE(ABORT, 'a leg is staged, then sent or unknown, never re-staged'); END""",
    """CREATE TRIGGER IF NOT EXISTS autotrader_exec_legs_fills_monotonic
    BEFORE UPDATE OF filled ON autotrader_exec_legs
    WHEN NEW.filled < OLD.filled
    BEGIN SELECT RAISE(ABORT, 'leg fills never decrease'); END""",
    """CREATE TRIGGER IF NOT EXISTS autotrader_exec_legs_perm_id_once
    BEFORE UPDATE OF perm_id ON autotrader_exec_legs
    WHEN OLD.perm_id IS NOT NULL AND NEW.perm_id IS NOT OLD.perm_id
    BEGIN SELECT RAISE(ABORT, 'a leg permId never changes once known'); END""",
    """CREATE TRIGGER IF NOT EXISTS autotrader_exec_legs_no_delete
    BEFORE DELETE ON autotrader_exec_legs
    BEGIN SELECT RAISE(ABORT, 'execution legs are append-only'); END""",
    # ---- fills: one row per IBKR execution, deduplicated by execId --------------
    """CREATE TABLE IF NOT EXISTS autotrader_exec_fills (
        exec_id TEXT PRIMARY KEY CHECK(length(exec_id) BETWEEN 1 AND 128),
        exec_base TEXT NOT NULL,
        exec_version INTEGER NOT NULL CHECK(exec_version >= 0),
        leg_id TEXT NOT NULL,
        order_id INTEGER NOT NULL,
        side TEXT NULL,
        shares INTEGER NOT NULL CHECK(shares >= 0),
        price TEXT NOT NULL CHECK(CAST(price AS REAL) >= 0 AND CAST(price AS REAL) < 1e308),
        account TEXT NULL,
        exec_time TEXT NULL,
        commission TEXT NULL,
        commission_currency TEXT NULL,
        received_at TEXT NOT NULL,
        UNIQUE(exec_base, exec_version),
        FOREIGN KEY(leg_id) REFERENCES autotrader_exec_legs(id)
    )""",
    """CREATE INDEX IF NOT EXISTS idx_autotrader_exec_fills_leg
        ON autotrader_exec_fills(leg_id)""",
    """CREATE TRIGGER IF NOT EXISTS autotrader_exec_fills_immutable
    BEFORE UPDATE ON autotrader_exec_fills
    WHEN NEW.exec_id IS NOT OLD.exec_id
      OR NEW.exec_base IS NOT OLD.exec_base
      OR NEW.exec_version IS NOT OLD.exec_version
      OR NEW.leg_id IS NOT OLD.leg_id
      OR NEW.order_id IS NOT OLD.order_id
      OR NEW.side IS NOT OLD.side
      OR NEW.shares IS NOT OLD.shares
      OR NEW.price IS NOT OLD.price
      OR NEW.account IS NOT OLD.account
      OR NEW.exec_time IS NOT OLD.exec_time
      OR NEW.received_at IS NOT OLD.received_at
      OR (OLD.commission IS NOT NULL AND NEW.commission IS NOT OLD.commission)
      OR (OLD.commission_currency IS NOT NULL
          AND NEW.commission_currency IS NOT OLD.commission_currency)
    BEGIN SELECT RAISE(ABORT, 'a fill is immutable; only a missing commission may be added'); END""",
    """CREATE TRIGGER IF NOT EXISTS autotrader_exec_fills_no_delete
    BEFORE DELETE ON autotrader_exec_fills
    BEGIN SELECT RAISE(ABORT, 'execution fills are append-only'); END""",
    # ---- order events: the append-only audit trail ------------------------------
    """CREATE TABLE IF NOT EXISTS autotrader_exec_events (
        seq INTEGER PRIMARY KEY,
        book_id TEXT NOT NULL,
        bracket_id TEXT NULL,
        leg_id TEXT NULL,
        kind TEXT NOT NULL CHECK(length(kind) BETWEEN 1 AND 64),
        payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
        created_at TEXT NOT NULL,
        FOREIGN KEY(bracket_id) REFERENCES autotrader_exec_brackets(id),
        FOREIGN KEY(leg_id) REFERENCES autotrader_exec_legs(id)
    )""",
    """CREATE INDEX IF NOT EXISTS idx_autotrader_exec_events_bracket
        ON autotrader_exec_events(bracket_id, seq)""",
    """CREATE INDEX IF NOT EXISTS idx_autotrader_exec_events_book
        ON autotrader_exec_events(book_id, seq)""",
    """CREATE TRIGGER IF NOT EXISTS autotrader_exec_events_no_update
    BEFORE UPDATE ON autotrader_exec_events
    BEGIN SELECT RAISE(ABORT, 'execution events are append-only'); END""",
    """CREATE TRIGGER IF NOT EXISTS autotrader_exec_events_no_delete
    BEFORE DELETE ON autotrader_exec_events
    BEGIN SELECT RAISE(ABORT, 'execution events are append-only'); END""",
    # ---- shared risk book: one reservation per sent bracket ---------------------
    """CREATE TABLE IF NOT EXISTS autotrader_exec_risk_reservations (
        id TEXT PRIMARY KEY CHECK(length(id) = 32 AND id NOT GLOB '*[^0-9a-f]*'),
        book_id TEXT NOT NULL,
        account TEXT NOT NULL CHECK(account GLOB 'DU?*'),
        session_date TEXT NOT NULL,
        decision_id TEXT UNIQUE NOT NULL,
        bracket_id TEXT UNIQUE NOT NULL,
        symbol TEXT NOT NULL,
        risk_amount TEXT NOT NULL CHECK(CAST(risk_amount AS REAL) > 0),
        notional_amount TEXT NOT NULL CHECK(CAST(notional_amount AS REAL) > 0),
        net_liquidation TEXT NOT NULL CHECK(CAST(net_liquidation AS REAL) > 0),
        state TEXT NOT NULL CHECK(state IN ('reserved','consumed','released')),
        created_at TEXT NOT NULL,
        settled_at TEXT NULL,
        CHECK(book_id = account || ':' || session_date),
        CHECK((state = 'reserved') = (settled_at IS NULL)),
        FOREIGN KEY(decision_id) REFERENCES autotrader_exec_outbox(decision_id),
        FOREIGN KEY(bracket_id) REFERENCES autotrader_exec_brackets(id)
    )""",
    """CREATE INDEX IF NOT EXISTS idx_autotrader_exec_risk_reservations_book
        ON autotrader_exec_risk_reservations(book_id, state)""",
    """CREATE TRIGGER IF NOT EXISTS autotrader_exec_risk_reservations_insert_reserved
    BEFORE INSERT ON autotrader_exec_risk_reservations
    WHEN NEW.state != 'reserved'
    BEGIN SELECT RAISE(ABORT, 'a risk reservation starts as reserved'); END""",
    """CREATE TRIGGER IF NOT EXISTS autotrader_exec_risk_reservations_one_way
    BEFORE UPDATE ON autotrader_exec_risk_reservations
    WHEN NEW.id IS NOT OLD.id
      OR NEW.book_id IS NOT OLD.book_id
      OR NEW.account IS NOT OLD.account
      OR NEW.session_date IS NOT OLD.session_date
      OR NEW.decision_id IS NOT OLD.decision_id
      OR NEW.bracket_id IS NOT OLD.bracket_id
      OR NEW.symbol IS NOT OLD.symbol
      OR NEW.risk_amount IS NOT OLD.risk_amount
      OR NEW.notional_amount IS NOT OLD.notional_amount
      OR NEW.net_liquidation IS NOT OLD.net_liquidation
      OR NEW.created_at IS NOT OLD.created_at
      OR (NEW.state IS NOT OLD.state
          AND NOT (OLD.state = 'reserved' AND NEW.state IN ('consumed','released')))
    BEGIN SELECT RAISE(ABORT, 'a risk reservation is reserved once, then consumed or released'); END""",
    """CREATE TRIGGER IF NOT EXISTS autotrader_exec_risk_reservations_no_delete
    BEFORE DELETE ON autotrader_exec_risk_reservations
    BEGIN SELECT RAISE(ABORT, 'risk reservations are append-only'); END""",
)


__all__ = ["EXECUTION_LEDGER_MIGRATION", "EXECUTION_LEDGER_SCHEMA"]

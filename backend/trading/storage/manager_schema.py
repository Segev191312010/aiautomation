"""The AI day manager's tables: phases 0, 1 and 2, each its own migration.

Only what a phase uses is registered as a migration, because a migration
is frozen once applied (``tests/test_migration_freeze.py``) and an unsettled
design must not be:

* migration 13 (phase 0): ``MANAGER_SETUPS_SCHEMA``, the formed setups the
  shadow pipeline writes;
* migration 15 (phase 1): ``MANAGER_PHASE1_SCHEMA``, the day universe, the
  IBKR contract facts the manager reads and the signal outcomes (14 is the
  execution engine's ledger);
* migration 16 (phase 2): ``MANAGER_PHASE2_DDL``, plans, verdicts, managed
  decisions, events with the switch view, day_mode, spend.

A later phase's design stays an unregistered draft, applied only by its
tests on scratch databases, until it lands. Conventions, each enforced by
the schema itself:

* timestamps are ISO-8601 UTC text exactly as Python writes them
  (``...+00:00``), so text order is time order;
* session and spend dates are real ``YYYY-MM-DD`` dates;
* money, prices and R are plain decimal text (no exponent), never floats;
* JSON columns hold the JSON type their name promises;
* logs are append-only: a BEFORE UPDATE/DELETE trigger aborts any change.

Tables (all ``autotrader_manager_*``):

``setups``           every setup the rules formed, written by the shadow
                     pipeline after its decision; the evidence base.
``day_universe``     the manager's own per-session names (never part of the
                     hot-35 ranking or its breadth); a working set.
``contract_info``    IBKR's stock type, industry and category per conId, kept
                     from contract details the data layer already requests
                     (watchlist import, day-universe admission).
``plans``            every day-plan attempt, valid or not, with its slot,
                     version, prompt, model, cost, outcome token, the
                     candidates it was offered (C), the deterministic gates
                     at plan time and every entry the narrow-only gate
                     dropped; a valid plan focuses only inside C.
``verdicts``         the managed reviewer's {step, note} per setup. No foreign
                     key to ``setups``: a verdict is written during the review,
                     before the pipeline records the setup.
``decisions``        each managed decision that formed a setup: mode, plan,
                     eligibility snapshot, gate states, the policy quantity
                     and the steps; final = floor(policy x day x setup) and
                     the contract stays inside the eligible set and focus.
``events``           the autopilot switch, append-only; the view
                     ``autotrader_manager_switch`` reads OFF when it is empty.
``day_mode``         the per-session latch (insert once, never change); a
                     managed latch must follow an ON event, a V2 latch an OFF
                     event or none.
``spend``            the manager's spend ledger per date and purpose
                     (reserve before the call, settle after); calls and spend
                     never decrease.
``signal_outcomes``  each saved setup simulated forward on recorded bars, one
                     row per fill model.
"""
from __future__ import annotations


def _utc(column: str) -> str:
    return (
        f"({column} GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T"
        f"[0-9][0-9]:[0-9][0-9]:[0-9][0-9]*+00:00' AND julianday({column}) IS NOT NULL)"
    )


def _day(column: str) -> str:
    return (
        f"({column} GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'"
        f" AND date({column}) IS {column})"
    )


def _decimal(column: str) -> str:
    """Non-negative plain decimal text: digits, at most one inner point."""
    return (
        f"({column} GLOB '[0-9]*' AND {column} NOT GLOB '*[^0-9.]*'"
        f" AND {column} NOT GLOB '*.*.*' AND {column} NOT GLOB '*.')"
    )


def _signed_decimal(column: str) -> str:
    return (
        f"(({column} GLOB '[0-9]*' OR {column} GLOB '-[0-9]*')"
        f" AND substr({column}, 2) NOT GLOB '*[^0-9.]*'"
        f" AND {column} NOT GLOB '*.*.*' AND {column} NOT GLOB '*.')"
    )


def _digest(column: str) -> str:
    return f"(length({column}) = 64 AND {column} NOT GLOB '*[^0-9a-f]*')"


def _token(column: str, limit: int = 64) -> str:
    """A snake_case reason or name token."""
    return (
        f"(length({column}) BETWEEN 1 AND {limit} AND {column} GLOB '[a-z]*'"
        f" AND {column} NOT GLOB '*[^a-z0-9_]*')"
    )


def _json(column: str, kind: str) -> str:
    # CASE, not AND: json_type() raises on malformed JSON instead of failing the CHECK.
    return f"(CASE WHEN json_valid({column}) THEN json_type({column}) = '{kind}' ELSE 0 END)"


def _append_only(table: str, noun: str) -> tuple[str, str]:
    return (
        f"""CREATE TRIGGER IF NOT EXISTS {table}_no_update
    BEFORE UPDATE ON {table}
    BEGIN SELECT RAISE(ABORT, '{noun} are append-only'); END""",
        f"""CREATE TRIGGER IF NOT EXISTS {table}_no_delete
    BEFORE DELETE ON {table}
    BEGIN SELECT RAISE(ABORT, '{noun} are append-only'); END""",
    )


MANAGER_SETUPS_SCHEMA = (
    f"""CREATE TABLE IF NOT EXISTS autotrader_manager_setups (
        id INTEGER PRIMARY KEY,
        run_id TEXT NOT NULL,
        setup_fingerprint TEXT NOT NULL CHECK{_digest("setup_fingerprint")},
        candidate_id TEXT NOT NULL,
        strategy_id TEXT NOT NULL,
        strategy_version INTEGER NOT NULL CHECK(strategy_version >= 1),
        strategy_name TEXT NOT NULL CHECK(length(strategy_name) BETWEEN 1 AND 128),
        strategy_kind TEXT NOT NULL CHECK{_token("strategy_kind", 32)},
        spec_fingerprint TEXT NOT NULL CHECK{_digest("spec_fingerprint")},
        symbol TEXT NOT NULL CHECK(length(symbol) BETWEEN 1 AND 32),
        con_id INTEGER NOT NULL CHECK(con_id > 0),
        session_date TEXT NOT NULL CHECK{_day("session_date")},
        bar_ts TEXT NOT NULL CHECK{_utc("bar_ts")},
        detected_at TEXT NOT NULL CHECK{_utc("detected_at")},
        hour_bucket INTEGER NOT NULL CHECK(hour_bucket BETWEEN 0 AND 23),
        entry_price TEXT NOT NULL CHECK{_decimal("entry_price")},
        stop_price TEXT NOT NULL CHECK{_decimal("stop_price")},
        target_price TEXT NOT NULL CHECK{_decimal("target_price")},
        atr TEXT NOT NULL CHECK({_decimal("atr")} AND CAST(atr AS REAL) > 0),
        levels_json TEXT NOT NULL CHECK{_json("levels_json", "object")},
        setup_json TEXT NOT NULL CHECK{_json("setup_json", "object")},
        created_at TEXT NOT NULL CHECK{_utc("created_at")},
        UNIQUE(run_id, setup_fingerprint),
        CHECK(CAST(stop_price AS REAL) > 0
              AND CAST(stop_price AS REAL) < CAST(entry_price AS REAL)
              AND CAST(entry_price AS REAL) < CAST(target_price AS REAL)),
        CHECK(julianday(bar_ts) < julianday(detected_at)),
        FOREIGN KEY(run_id) REFERENCES autotrader_runs(id),
        FOREIGN KEY(candidate_id) REFERENCES autotrader_candidates(id)
    )""",
    """CREATE INDEX IF NOT EXISTS idx_autotrader_manager_setups_session
        ON autotrader_manager_setups(session_date, strategy_name, bar_ts)""",
    # The setup belongs to the run's own candidate, strategy version (with
    # its stored rules fingerprint) and session.
    """CREATE TRIGGER IF NOT EXISTS autotrader_manager_setups_coherent
    BEFORE INSERT ON autotrader_manager_setups
    WHEN NOT EXISTS (
        SELECT 1
        FROM autotrader_runs r
        JOIN autotrader_scanner_runs sr ON sr.run_id = r.id
        JOIN autotrader_candidates c ON c.scanner_run_id = sr.id
        JOIN autotrader_strategy_versions v
          ON v.strategy_id = r.strategy_id AND v.version = r.strategy_version
        WHERE r.id = NEW.run_id AND c.id = NEW.candidate_id
          AND r.strategy_id = NEW.strategy_id AND r.strategy_version = NEW.strategy_version
          AND v.content_hash = NEW.spec_fingerprint
          AND r.session_date = NEW.session_date
          AND c.con_id = NEW.con_id AND c.symbol = NEW.symbol
    )
    BEGIN SELECT RAISE(ABORT, 'formed setup run, candidate and strategy are incoherent'); END""",
    *_append_only("autotrader_manager_setups", "formed setups"),
)

MANAGER_DAY_UNIVERSE_SCHEMA = (
    f"""CREATE TABLE IF NOT EXISTS autotrader_manager_day_universe (
        session_date TEXT NOT NULL CHECK{_day("session_date")},
        con_id INTEGER NOT NULL CHECK(con_id > 0),
        symbol TEXT NOT NULL CHECK(length(symbol) BETWEEN 1 AND 32),
        primary_exchange TEXT NULL
            CHECK(primary_exchange IS NULL OR length(primary_exchange) BETWEEN 1 AND 16),
        first_seen TEXT NOT NULL CHECK{_utc("first_seen")},
        last_seen TEXT NOT NULL CHECK{_utc("last_seen")},
        sources_json TEXT NOT NULL CHECK({_json("sources_json", "array")}
                                         AND json_array_length(sources_json) >= 1),
        best_rank INTEGER NULL CHECK(best_rank IS NULL OR best_rank >= 1),
        qualified INTEGER NOT NULL CHECK(qualified IN (0, 1)),
        reason TEXT NULL CHECK(reason IS NULL OR {_token("reason")}),
        PRIMARY KEY(session_date, con_id),
        CHECK(julianday(first_seen) <= julianday(last_seen)),
        CHECK(qualified = 1 OR reason IS NOT NULL)
    )""",
    """CREATE INDEX IF NOT EXISTS idx_autotrader_manager_day_universe_rank
        ON autotrader_manager_day_universe(session_date, qualified, best_rank)""",
    """CREATE TRIGGER IF NOT EXISTS autotrader_manager_day_universe_identity_immutable
    BEFORE UPDATE OF session_date, con_id, first_seen ON autotrader_manager_day_universe
    WHEN NEW.session_date IS NOT OLD.session_date
      OR NEW.con_id IS NOT OLD.con_id
      OR NEW.first_seen IS NOT OLD.first_seen
    BEGIN SELECT RAISE(ABORT, 'a day-universe name keeps its session, contract and first sighting'); END""",
    """CREATE TRIGGER IF NOT EXISTS autotrader_manager_day_universe_last_seen_monotonic
    BEFORE UPDATE OF last_seen ON autotrader_manager_day_universe
    WHEN julianday(NEW.last_seen) < julianday(OLD.last_seen)
    BEGIN SELECT RAISE(ABORT, 'day-universe last_seen never moves back'); END""",
)

MANAGER_CONTRACT_INFO_SCHEMA = (
    f"""CREATE TABLE IF NOT EXISTS autotrader_manager_contract_info (
        con_id INTEGER PRIMARY KEY CHECK(con_id > 0),
        symbol TEXT NOT NULL CHECK(length(symbol) BETWEEN 1 AND 32),
        primary_exchange TEXT NULL
            CHECK(primary_exchange IS NULL OR length(primary_exchange) BETWEEN 1 AND 16),
        stock_type TEXT NULL CHECK(stock_type IS NULL OR length(stock_type) <= 64),
        industry TEXT NULL CHECK(industry IS NULL OR length(industry) BETWEEN 1 AND 128),
        category TEXT NULL CHECK(category IS NULL OR length(category) BETWEEN 1 AND 128),
        source TEXT NOT NULL CHECK(source IN ('watchlist', 'day_universe')),
        updated_at TEXT NOT NULL CHECK{_utc("updated_at")}
    )""",
)

# The fixed steps the manager may scale by (0 = off / skip); the same literal
# set as trading.manager.constants.DAY_STEPS, pinned by a test.
_STEPS = "(0, 0.25, 0.5, 0.75, 1)"
# DayPlan.regime; pinned to trading.manager.constants.REGIMES by a test.
_REGIMES = "('trend_up', 'trend_down', 'range', 'high_vol', 'unclear')"
_NOTE_MAX = 140


def _one_line(column: str, limit: int = _NOTE_MAX) -> str:
    return (
        f"(length({column}) BETWEEN 1 AND {limit} AND instr({column}, char(10)) = 0"
        f" AND instr({column}, char(13)) = 0)"
    )


MANAGER_PLANS_SCHEMA = (
    f"""CREATE TABLE IF NOT EXISTS autotrader_manager_plans (
        plan_id TEXT PRIMARY KEY
            CHECK(length(plan_id) BETWEEN 1 AND 64 AND plan_id NOT GLOB '*[^0-9a-z_-]*'),
        session_date TEXT NOT NULL CHECK{_day("session_date")},
        slot TEXT NOT NULL CHECK(slot GLOB '[0-2][0-9]:[0-5][0-9]'),
        created_at TEXT NOT NULL CHECK{_utc("created_at")},
        valid_until TEXT NULL CHECK(valid_until IS NULL OR {_utc("valid_until")}),
        manager_version TEXT NOT NULL CHECK(length(manager_version) BETWEEN 1 AND 64),
        prompt_id TEXT NOT NULL CHECK(length(prompt_id) BETWEEN 1 AND 128),
        model TEXT NOT NULL CHECK(length(model) BETWEEN 1 AND 128),
        outcome TEXT NOT NULL CHECK{_token("outcome")},
        regime TEXT NULL CHECK(regime IS NULL OR regime IN {_REGIMES}),
        strategies_json TEXT NULL
            CHECK(strategies_json IS NULL OR {_json("strategies_json", "object")}),
        focus_json TEXT NULL
            CHECK(focus_json IS NULL OR ({_json("focus_json", "array")}
                                         AND json_array_length(focus_json) <= 30)),
        candidates_json TEXT NOT NULL DEFAULT '[]' CHECK{_json("candidates_json", "array")},
        gates_json TEXT NOT NULL DEFAULT '{{}}' CHECK{_json("gates_json", "object")},
        dropped_json TEXT NOT NULL DEFAULT '[]' CHECK{_json("dropped_json", "array")},
        context_digest TEXT NULL CHECK(context_digest IS NULL OR {_digest("context_digest")}),
        input_tokens INTEGER NULL CHECK(input_tokens IS NULL OR input_tokens >= 0),
        output_tokens INTEGER NULL CHECK(output_tokens IS NULL OR output_tokens >= 0),
        cost_usd TEXT NOT NULL DEFAULT '0' CHECK{_decimal("cost_usd")},
        latency_ms INTEGER NULL CHECK(latency_ms IS NULL OR latency_ms >= 0),
        CHECK((outcome = 'valid') = (regime IS NOT NULL AND strategies_json IS NOT NULL
                                     AND focus_json IS NOT NULL AND valid_until IS NOT NULL
                                     AND context_digest IS NOT NULL)),
        CHECK(valid_until IS NULL OR julianday(valid_until) > julianday(created_at))
    )""",
    """CREATE INDEX IF NOT EXISTS idx_autotrader_manager_plans_session
        ON autotrader_manager_plans(session_date, created_at)""",
    *_append_only("autotrader_manager_plans", "manager plans"),
    # Narrow only, re-checked by the database: a valid plan focuses only on
    # contracts it was offered (candidates_json, the set C).
    """CREATE TRIGGER IF NOT EXISTS autotrader_manager_plans_focus_offered
    BEFORE INSERT ON autotrader_manager_plans
    WHEN NEW.focus_json IS NOT NULL AND json_valid(NEW.focus_json) AND json_valid(NEW.candidates_json)
     AND EXISTS (
        SELECT 1 FROM json_each(NEW.focus_json) AS entry
        WHERE json_type(entry.value, '$.con_id') IS NOT 'integer'
           OR json_extract(entry.value, '$.con_id') NOT IN (
                SELECT offered.value FROM json_each(NEW.candidates_json) AS offered)
     )
    BEGIN SELECT RAISE(ABORT, 'a plan may focus only on the candidates it was offered'); END""",
)

MANAGER_VERDICTS_SCHEMA = (
    f"""CREATE TABLE IF NOT EXISTS autotrader_manager_verdicts (
        id INTEGER PRIMARY KEY,
        run_id TEXT NOT NULL,
        setup_fingerprint TEXT NOT NULL CHECK{_digest("setup_fingerprint")},
        plan_id TEXT NULL,
        mode TEXT NOT NULL CHECK(mode IN ('managed', 'neutral')),
        step REAL NOT NULL CHECK(step IN {_STEPS}),
        note TEXT NULL CHECK(note IS NULL OR {_one_line("note")}),
        created_at TEXT NOT NULL CHECK{_utc("created_at")},
        UNIQUE(run_id, setup_fingerprint),
        -- A managed verdict follows a plan; neutral mode has none and keeps step 1.
        CHECK((mode = 'managed') = (plan_id IS NOT NULL)),
        CHECK(mode = 'managed' OR (step = 1 AND note IS NULL)),
        FOREIGN KEY(run_id) REFERENCES autotrader_runs(id),
        FOREIGN KEY(plan_id) REFERENCES autotrader_manager_plans(plan_id)
    )""",
    """CREATE INDEX IF NOT EXISTS idx_autotrader_manager_verdicts_plan
        ON autotrader_manager_verdicts(plan_id)""",
    *_append_only("autotrader_manager_verdicts", "manager verdicts"),
)

# One row per managed decision that formed a setup: the narrowing, checked per
# decision (Principle 2). The final quantity is floor(policy x day x setup) and
# never above the policy quantity; neutral mode keeps both steps at 1; the
# contract is inside the eligible set and, when managed, inside the focus.
MANAGER_DECISIONS_SCHEMA = (
    f"""CREATE TABLE IF NOT EXISTS autotrader_manager_decisions (
        id INTEGER PRIMARY KEY,
        decision_id TEXT NOT NULL UNIQUE,
        run_id TEXT NOT NULL,
        candidate_id TEXT NOT NULL,
        con_id INTEGER NOT NULL CHECK(con_id > 0),
        setup_fingerprint TEXT NOT NULL CHECK{_digest("setup_fingerprint")},
        session_date TEXT NOT NULL CHECK{_day("session_date")},
        outcome TEXT NOT NULL CHECK{_token("outcome", 32)},
        mode TEXT NOT NULL CHECK(mode IN ('managed', 'neutral')),
        neutral_reason TEXT NULL CHECK(neutral_reason IS NULL OR {_token("neutral_reason")}),
        mark TEXT NULL CHECK(mark IS NULL OR mark = 'manager_fallback_neutral'),
        plan_id TEXT NULL,
        eligibility_id TEXT NOT NULL CHECK{_digest("eligibility_id")},
        eligible_json TEXT NOT NULL
            CHECK({_json("eligible_json", "array")} AND json_array_length(eligible_json) >= 1),
        focus_json TEXT NULL CHECK(focus_json IS NULL OR {_json("focus_json", "array")}),
        gates_json TEXT NOT NULL CHECK{_json("gates_json", "object")},
        day_step REAL NOT NULL CHECK(day_step IN {_STEPS}),
        setup_step REAL NOT NULL CHECK(setup_step IN {_STEPS}),
        policy_qty INTEGER NULL CHECK(policy_qty IS NULL OR policy_qty >= 0),
        final_qty INTEGER NULL CHECK(final_qty IS NULL OR final_qty >= 1),
        created_at TEXT NOT NULL CHECK{_utc("created_at")},
        CHECK((mode = 'managed') = (plan_id IS NOT NULL)),
        CHECK(mode = 'managed' OR (day_step = 1 AND setup_step = 1 AND focus_json IS NULL
                                    AND neutral_reason IS NOT NULL)),
        CHECK(mode = 'neutral' OR (neutral_reason IS NULL AND mark IS NULL
                                    AND focus_json IS NOT NULL)),
        CHECK(final_qty IS NULL OR (policy_qty IS NOT NULL AND final_qty <= policy_qty
              AND final_qty = CAST(policy_qty * day_step * setup_step AS INTEGER))),
        FOREIGN KEY(run_id) REFERENCES autotrader_runs(id),
        FOREIGN KEY(decision_id) REFERENCES autotrader_candidate_decisions(id),
        FOREIGN KEY(plan_id) REFERENCES autotrader_manager_plans(plan_id)
    )""",
    """CREATE INDEX IF NOT EXISTS idx_autotrader_manager_decisions_session
        ON autotrader_manager_decisions(session_date, run_id)""",
    """CREATE TRIGGER IF NOT EXISTS autotrader_manager_decisions_narrow_only
    BEFORE INSERT ON autotrader_manager_decisions
    WHEN NOT EXISTS (SELECT 1 FROM json_each(NEW.eligible_json) WHERE value = NEW.con_id)
      OR (NEW.mode = 'managed'
          AND NOT EXISTS (SELECT 1 FROM json_each(NEW.focus_json) WHERE value = NEW.con_id))
    BEGIN SELECT RAISE(ABORT, 'a managed decision stays inside its eligible set and plan focus'); END""",
    *_append_only("autotrader_manager_decisions", "managed decisions"),
)

MANAGER_SWITCH_SCHEMA = (
    f"""CREATE TABLE IF NOT EXISTS autotrader_manager_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL CHECK{_utc("ts")},
        action TEXT NOT NULL CHECK(action IN ('on', 'off')),
        source TEXT NOT NULL CHECK(source IN ('cli', 'telegram')),
        actor TEXT NOT NULL CHECK(length(actor) BETWEEN 1 AND 128),
        reason TEXT NOT NULL CHECK(length(reason) BETWEEN 1 AND 500)
    )""",
    *_append_only("autotrader_manager_events", "autopilot switch events"),
    # The switch is OFF until an event says otherwise: no row, no autopilot.
    """CREATE VIEW IF NOT EXISTS autotrader_manager_switch AS
    SELECT
        COALESCE((SELECT action FROM autotrader_manager_events ORDER BY id DESC LIMIT 1), 'off')
            AS state,
        (SELECT id FROM autotrader_manager_events ORDER BY id DESC LIMIT 1) AS event_id""",
    f"""CREATE TABLE IF NOT EXISTS autotrader_manager_day_mode (
        session_date TEXT NOT NULL CHECK{_day("session_date")},
        strategy_family TEXT NOT NULL CHECK{_token("strategy_family", 32)},
        mode TEXT NOT NULL CHECK(mode IN ('v2', 'managed', 'neutral')),
        latched_at TEXT NOT NULL CHECK{_utc("latched_at")},
        switch_event_id INTEGER NULL,
        PRIMARY KEY(session_date, strategy_family),
        FOREIGN KEY(switch_event_id) REFERENCES autotrader_manager_events(id)
    )""",
    # Insert once per session and family (ON CONFLICT DO NOTHING); never changed.
    *_append_only("autotrader_manager_day_mode", "day-mode latches"),
    """CREATE TRIGGER IF NOT EXISTS autotrader_manager_day_mode_follows_switch
    BEFORE INSERT ON autotrader_manager_day_mode
    WHEN (NEW.mode = 'v2' AND NEW.switch_event_id IS NOT NULL
          AND (SELECT action FROM autotrader_manager_events WHERE id = NEW.switch_event_id)
              IS NOT 'off')
      OR (NEW.mode != 'v2'
          AND (SELECT action FROM autotrader_manager_events WHERE id = NEW.switch_event_id)
              IS NOT 'on')
    BEGIN SELECT RAISE(ABORT, 'a latched mode must follow the switch event it read'); END""",
)

MANAGER_SPEND_SCHEMA = (
    f"""CREATE TABLE IF NOT EXISTS autotrader_manager_spend (
        spend_date TEXT NOT NULL CHECK{_day("spend_date")},
        purpose TEXT NOT NULL CHECK{_token("purpose", 32)},
        calls INTEGER NOT NULL DEFAULT 0 CHECK(calls >= 0),
        reserved_usd TEXT NOT NULL DEFAULT '0' CHECK{_decimal("reserved_usd")},
        spent_usd TEXT NOT NULL DEFAULT '0' CHECK{_decimal("spent_usd")},
        updated_at TEXT NOT NULL CHECK{_utc("updated_at")},
        PRIMARY KEY(spend_date, purpose)
    )""",
    """CREATE TRIGGER IF NOT EXISTS autotrader_manager_spend_identity_immutable
    BEFORE UPDATE OF spend_date, purpose ON autotrader_manager_spend
    WHEN NEW.spend_date IS NOT OLD.spend_date OR NEW.purpose IS NOT OLD.purpose
    BEGIN SELECT RAISE(ABORT, 'a spend row keeps its date and purpose'); END""",
    """CREATE TRIGGER IF NOT EXISTS autotrader_manager_spend_monotonic
    BEFORE UPDATE OF calls, spent_usd ON autotrader_manager_spend
    WHEN NEW.calls < OLD.calls OR CAST(NEW.spent_usd AS REAL) < CAST(OLD.spent_usd AS REAL)
    BEGIN SELECT RAISE(ABORT, 'manager calls and spend never decrease'); END""",
    """CREATE TRIGGER IF NOT EXISTS autotrader_manager_spend_no_delete
    BEFORE DELETE ON autotrader_manager_spend
    BEGIN SELECT RAISE(ABORT, 'manager spend rows are permanent'); END""",
)

MANAGER_OUTCOMES_SCHEMA = (
    f"""CREATE TABLE IF NOT EXISTS autotrader_manager_signal_outcomes (
        id INTEGER PRIMARY KEY,
        run_id TEXT NOT NULL,
        setup_fingerprint TEXT NOT NULL CHECK{_digest("setup_fingerprint")},
        fill_model_id TEXT NOT NULL
            CHECK(length(fill_model_id) BETWEEN 1 AND 64
                  AND fill_model_id NOT GLOB '*[^a-z0-9_.-]*'),
        simulated_at TEXT NOT NULL CHECK{_utc("simulated_at")},
        filled INTEGER NOT NULL CHECK(filled IN (0, 1)),
        exit_reason TEXT NOT NULL CHECK{_token("exit_reason", 32)},
        r_multiple TEXT NULL CHECK(r_multiple IS NULL OR {_signed_decimal("r_multiple")}),
        cost_r TEXT NOT NULL DEFAULT '0' CHECK{_decimal("cost_r")},
        details_json TEXT NOT NULL DEFAULT '{{}}' CHECK{_json("details_json", "object")},
        UNIQUE(run_id, setup_fingerprint, fill_model_id),
        CHECK((filled = 1) = (r_multiple IS NOT NULL)),
        CHECK((filled = 0) = (exit_reason = 'not_filled')),
        FOREIGN KEY(run_id, setup_fingerprint)
            REFERENCES autotrader_manager_setups(run_id, setup_fingerprint)
    )""",
    """CREATE INDEX IF NOT EXISTS idx_autotrader_manager_signal_outcomes_model
        ON autotrader_manager_signal_outcomes(fill_model_id, simulated_at)""",
    *_append_only("autotrader_manager_signal_outcomes", "signal outcomes"),
)

# Registered: migration 13 (phase 0). Frozen once applied.
MANAGER_TABLES = ("autotrader_manager_setups",)
# Registered: migration 15 (phase 1). Frozen once applied.
MANAGER_PHASE1_SCHEMA = (
    *MANAGER_DAY_UNIVERSE_SCHEMA,
    *MANAGER_CONTRACT_INFO_SCHEMA,
    *MANAGER_OUTCOMES_SCHEMA,
)
MANAGER_PHASE1_TABLES = (
    "autotrader_manager_day_universe",
    "autotrader_manager_contract_info",
    "autotrader_manager_signal_outcomes",
)

# Registered: migration 16 (phase 2, the AI day manager in shadow). Frozen
# once applied. Plans before verdicts and decisions, events before day_mode.
MANAGER_PHASE2_MIGRATION = 16
MANAGER_PHASE2_DDL = (
    *MANAGER_PLANS_SCHEMA,
    *MANAGER_VERDICTS_SCHEMA,
    *MANAGER_DECISIONS_SCHEMA,
    *MANAGER_SWITCH_SCHEMA,
    *MANAGER_SPEND_SCHEMA,
)
MANAGER_PHASE2_TABLES = (
    "autotrader_manager_plans",
    "autotrader_manager_verdicts",
    "autotrader_manager_decisions",
    "autotrader_manager_events",
    "autotrader_manager_day_mode",
    "autotrader_manager_spend",
)


__all__ = [
    "MANAGER_PHASE1_SCHEMA",
    "MANAGER_PHASE1_TABLES",
    "MANAGER_PHASE2_DDL",
    "MANAGER_PHASE2_MIGRATION",
    "MANAGER_PHASE2_TABLES",
    "MANAGER_SETUPS_SCHEMA",
    "MANAGER_TABLES",
]

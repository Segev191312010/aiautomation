"""The AI day manager's tables: one registered, the rest drafts.

Only what a phase uses is registered as a migration, because a migration
is frozen once applied (``tests/test_migration_freeze.py``) and an unsettled
design must not be:

* migration 13 (phase 0): ``MANAGER_SETUPS_SCHEMA``, the formed setups the
  shadow pipeline writes;
* ``DRAFT_PHASE1_DDL``, registered as migration 14 when phase 1 lands:
  day_universe, signal_outcomes;
* ``DRAFT_PHASE2_DDL``, registered as migration 15 when phase 2 lands:
  plans, verdicts, events with the switch view, day_mode, spend.

The drafts are applied by nothing but their tests (on scratch databases),
which keep their constraints and triggers checked until they are
registered. Conventions, each enforced by the schema itself:

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
``plans``            every day-plan attempt, valid or not, with its version,
                     prompt, model, cost and outcome token.
``verdicts``         the managed reviewer's {step, note} per setup. No foreign
                     key to ``setups``: a verdict is written during the review,
                     before the pipeline records the setup.
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

MANAGER_PLANS_SCHEMA = (
    f"""CREATE TABLE IF NOT EXISTS autotrader_manager_plans (
        plan_id TEXT PRIMARY KEY
            CHECK(length(plan_id) BETWEEN 1 AND 64 AND plan_id NOT GLOB '*[^0-9a-z_-]*'),
        session_date TEXT NOT NULL CHECK{_day("session_date")},
        created_at TEXT NOT NULL CHECK{_utc("created_at")},
        valid_until TEXT NULL CHECK(valid_until IS NULL OR {_utc("valid_until")}),
        manager_version TEXT NOT NULL CHECK(length(manager_version) BETWEEN 1 AND 64),
        prompt_id TEXT NOT NULL CHECK(length(prompt_id) BETWEEN 1 AND 128),
        model TEXT NOT NULL CHECK(length(model) BETWEEN 1 AND 128),
        outcome TEXT NOT NULL CHECK{_token("outcome")},
        regime TEXT NULL CHECK(regime IS NULL OR {_token("regime", 32)}),
        strategies_json TEXT NULL
            CHECK(strategies_json IS NULL OR {_json("strategies_json", "object")}),
        focus_json TEXT NULL
            CHECK(focus_json IS NULL OR ({_json("focus_json", "array")}
                                         AND json_array_length(focus_json) <= 30)),
        cost_usd TEXT NOT NULL DEFAULT '0' CHECK{_decimal("cost_usd")},
        latency_ms INTEGER NULL CHECK(latency_ms IS NULL OR latency_ms >= 0),
        CHECK((outcome = 'valid') = (regime IS NOT NULL AND strategies_json IS NOT NULL
                                     AND focus_json IS NOT NULL AND valid_until IS NOT NULL)),
        CHECK(valid_until IS NULL OR julianday(valid_until) > julianday(created_at))
    )""",
    """CREATE INDEX IF NOT EXISTS idx_autotrader_manager_plans_session
        ON autotrader_manager_plans(session_date, created_at)""",
    *_append_only("autotrader_manager_plans", "manager plans"),
)

MANAGER_VERDICTS_SCHEMA = (
    f"""CREATE TABLE IF NOT EXISTS autotrader_manager_verdicts (
        id INTEGER PRIMARY KEY,
        run_id TEXT NOT NULL,
        setup_fingerprint TEXT NOT NULL CHECK{_digest("setup_fingerprint")},
        plan_id TEXT NULL,
        step REAL NOT NULL CHECK(step IN (0, 0.25, 0.5, 0.75, 1)),
        note TEXT NULL CHECK(note IS NULL OR (length(note) BETWEEN 1 AND 500
                                               AND instr(note, char(10)) = 0
                                               AND instr(note, char(13)) = 0)),
        created_at TEXT NOT NULL CHECK{_utc("created_at")},
        UNIQUE(run_id, setup_fingerprint),
        FOREIGN KEY(run_id) REFERENCES autotrader_runs(id),
        FOREIGN KEY(plan_id) REFERENCES autotrader_manager_plans(plan_id)
    )""",
    """CREATE INDEX IF NOT EXISTS idx_autotrader_manager_verdicts_plan
        ON autotrader_manager_verdicts(plan_id)""",
    *_append_only("autotrader_manager_verdicts", "manager verdicts"),
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

# DRAFTS: registered by nothing; only their tests apply them, on scratch
# databases. Each becomes its own migration, unchanged or reviewed, in the
# commit that lands its phase; until then it may still change freely.
DRAFT_PHASE1_DDL = (*MANAGER_DAY_UNIVERSE_SCHEMA, *MANAGER_OUTCOMES_SCHEMA)  # -> migration 14
DRAFT_PHASE1_TABLES = ("autotrader_manager_day_universe", "autotrader_manager_signal_outcomes")
DRAFT_PHASE2_DDL = (  # -> migration 15; plans before verdicts, events before day_mode
    *MANAGER_PLANS_SCHEMA,
    *MANAGER_VERDICTS_SCHEMA,
    *MANAGER_SWITCH_SCHEMA,
    *MANAGER_SPEND_SCHEMA,
)
DRAFT_PHASE2_TABLES = (
    "autotrader_manager_plans",
    "autotrader_manager_verdicts",
    "autotrader_manager_events",
    "autotrader_manager_day_mode",
    "autotrader_manager_spend",
)


__all__ = [
    "DRAFT_PHASE1_DDL",
    "DRAFT_PHASE1_TABLES",
    "DRAFT_PHASE2_DDL",
    "DRAFT_PHASE2_TABLES",
    "MANAGER_SETUPS_SCHEMA",
    "MANAGER_TABLES",
]

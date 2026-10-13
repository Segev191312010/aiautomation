"""The day-manager tables: migrations 13, 15 and 16.

Migration 13 holds only ``autotrader_manager_setups``; migration 15 (phase 1)
the day universe, the contract facts and the signal outcomes; migration 16
(phase 2) plans, verdicts, managed decisions, the switch, the day latch and
spend. Every constraint and trigger is checked here on scratch databases.
"""
from __future__ import annotations

from collections.abc import Iterator
from datetime import date, datetime, time, timezone
import io
import json
from pathlib import Path
import sqlite3

import pytest

from tests.schema_support import database_at, migrate_database
from trading.contracts import FixedUniverse, RuleCondition, StrategySpec
from trading.operations.db_admin import run_migrate
from trading.scheduler.sessions import MARKET_TIMEZONE
from trading.storage.manager_schema import (
    MANAGER_PHASE1_SCHEMA,
    MANAGER_PHASE1_TABLES,
    MANAGER_PHASE2_DDL,
    MANAGER_PHASE2_MIGRATION,
    MANAGER_PHASE2_TABLES,
    MANAGER_SETUPS_SCHEMA,
    MANAGER_TABLES,
)
from trading.storage.migrations import MIGRATIONS, inspect_schema

TS = "2026-10-13T15:50:00+00:00"
LATER = "2026-10-13T15:55:00.250000+00:00"
FP = "a" * 64
FP2 = "b" * 64
SPEC = StrategySpec(
    name="MOMO_HOD_V2",
    universe=FixedUniverse(symbols=("BF B",)),
    conditions=(RuleCondition(feature="rvol", operator="gte", value="1.5"),),
    rationale="Schema fixture.",
)

REGISTERED_TRIGGERS = {
    "autotrader_manager_setups_coherent",
    "autotrader_manager_setups_no_update",
    "autotrader_manager_setups_no_delete",
}
REGISTERED_INDEXES = {"idx_autotrader_manager_setups_session"}
PHASE1_TRIGGERS = {
    "autotrader_manager_day_universe_identity_immutable",
    "autotrader_manager_day_universe_last_seen_monotonic",
    "autotrader_manager_signal_outcomes_no_update",
    "autotrader_manager_signal_outcomes_no_delete",
}
PHASE1_INDEXES = {
    "idx_autotrader_manager_day_universe_rank",
    "idx_autotrader_manager_signal_outcomes_model",
}
PHASE2_TRIGGERS = {
    "autotrader_manager_plans_no_update",
    "autotrader_manager_plans_no_delete",
    "autotrader_manager_plans_focus_offered",
    "autotrader_manager_verdicts_no_update",
    "autotrader_manager_verdicts_no_delete",
    "autotrader_manager_decisions_no_update",
    "autotrader_manager_decisions_no_delete",
    "autotrader_manager_decisions_narrow_only",
    "autotrader_manager_events_no_update",
    "autotrader_manager_events_no_delete",
    "autotrader_manager_day_mode_no_update",
    "autotrader_manager_day_mode_no_delete",
    "autotrader_manager_day_mode_follows_switch",
    "autotrader_manager_spend_identity_immutable",
    "autotrader_manager_spend_monotonic",
    "autotrader_manager_spend_no_delete",
}
PHASE2_INDEXES = {
    "idx_autotrader_manager_plans_session",
    "idx_autotrader_manager_verdicts_plan",
    "idx_autotrader_manager_decisions_session",
}


def _objects(connection: sqlite3.Connection) -> dict[str, set[str]]:
    return {
        kind: {
            name
            for (name,) in connection.execute(
                "SELECT name FROM sqlite_master WHERE type=? AND name LIKE '%autotrader_manager%'",
                (kind,),
            )
        }
        for kind in ("table", "trigger", "index", "view")
    }


def _decision_row(
    connection: sqlite3.Connection, decision_id: str, fingerprint: str, key: str, candidate: str = "cand-1"
) -> None:
    connection.execute(
        """INSERT INTO autotrader_candidate_decisions
           (id, run_id, candidate_id, strategy_id, strategy_version, decision_key, outcome,
            setup_fingerprint, payload_json, created_at)
           VALUES (?, 'run-1', ?, 'strategy-momo', 1, ?, 'shadow_accepted', ?, '{}', ?)""",
        (decision_id, candidate, key, fingerprint, LATER),
    )


@pytest.fixture
def db(tmp_path) -> Iterator[sqlite3.Connection]:
    """A scratch database: every migration (15 and 16 included), one MOMO run
    with BF B and its recorded decision."""
    path = migrate_database(tmp_path / "manager.db")
    # Autocommit: a refused statement is undone alone, never the rows before it.
    connection = sqlite3.connect(path, isolation_level=None)
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("INSERT INTO autotrader_strategy_identities VALUES ('strategy-momo', 'owner', ?, NULL)", (TS,))
    connection.execute(
        """INSERT INTO autotrader_strategy_versions
           (strategy_id, version, content_hash, spec_json, created_at, created_by)
           VALUES ('strategy-momo', 1, ?, ?, ?, 'owner')""",
        (SPEC.fingerprint(), json.dumps(SPEC.model_dump(mode="json")), TS),
    )
    connection.execute("INSERT INTO autotrader_strategy_lifecycle_v2 VALUES ('strategy-momo', 1, 'shadow', 0, ?)", (TS,))
    connection.execute(
        """INSERT INTO autotrader_runs
           (id, owner_id, source_sha, strategy_id, strategy_version, session_date, mode, status,
            policy_fingerprints_json)
           VALUES ('run-1', 'owner', 'abcdef1', 'strategy-momo', 1, '2026-10-13', 'shadow', 'running', '{}')"""
    )
    connection.execute(
        """INSERT INTO autotrader_scanner_runs
           (id, run_id, parameters_json, parameters_fingerprint, status, started_at)
           VALUES ('scan-1', 'run-1', '{}', ?, 'completed', ?)""",
        ("c" * 64, TS),
    )
    connection.execute(
        """INSERT INTO autotrader_candidates
           (id, scanner_run_id, con_id, symbol, rank, contract_json, admission_status)
           VALUES ('cand-1', 'scan-1', 4931, 'BF B', 1, '{}', 'admitted'),
                  ('cand-2', 'scan-1', 265598, 'AAPL', 2, '{}', 'admitted')"""
    )
    _decision_row(connection, "decision-1", FP, "d" * 64)
    connection.commit()
    yield connection
    connection.close()


def _insert(db: sqlite3.Connection, table: str, row: dict[str, object], *, upsert: str = "") -> int:
    columns = ", ".join(row)
    marks = ", ".join("?" for _ in row)
    cursor = db.execute(f"INSERT INTO {table} ({columns}) VALUES ({marks}) {upsert}", tuple(row.values()))
    return cursor.rowcount


def _setup(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "run_id": "run-1",
        "setup_fingerprint": FP,
        "candidate_id": "cand-1",
        "strategy_id": "strategy-momo",
        "strategy_version": 1,
        "strategy_name": "MOMO_HOD_V2",
        "strategy_kind": "momo_hod",
        "spec_fingerprint": SPEC.fingerprint(),
        "symbol": "BF B",
        "con_id": 4931,
        "session_date": "2026-10-13",
        "bar_ts": TS,
        "detected_at": LATER,
        "hour_bucket": 11,
        "entry_price": "26.77",
        "stop_price": "26.51",
        "target_price": "27.29",
        "atr": "0.0792",
        "levels_json": json.dumps({"stop_distance": "0.26", "vwap": "26.5586"}),
        "setup_json": json.dumps({"symbol": "BF B"}),
        "created_at": LATER,
    }
    row.update(overrides)
    return row


def _plan(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "plan_id": "plan-0920",
        "session_date": "2026-10-13",
        "created_at": "2026-10-13T13:20:00+00:00",
        "valid_until": "2026-10-13T14:35:00+00:00",
        "manager_version": "manager-v1",
        "prompt_id": "day-plan-v1",
        "model": "claude-opus-5-5",
        "slot": "09:20",
        "outcome": "valid",
        "regime": "trend_up",
        "strategies_json": json.dumps({"momo": {"step": 1, "reason": "trend day"}}),
        "focus_json": json.dumps([{"con_id": 4931, "strategies": ["momo"], "note": "gap and go"}]),
        "candidates_json": json.dumps([4931, 265598]),
        "gates_json": json.dumps({"kill_switch": False, "day_stop": False, "risk_off": False}),
        "dropped_json": "[]",
        "context_digest": "e" * 64,
        "cost_usd": "0.0412",
        "latency_ms": 8400,
    }
    row.update(overrides)
    return row


def _event(db: sqlite3.Connection, action: str) -> int:
    cursor = db.execute(
        "INSERT INTO autotrader_manager_events (ts, action, source, actor, reason) VALUES (?, ?, 'cli', 'owner', 'test')",
        (TS, action),
    )
    return int(cursor.lastrowid)


def _rejects(db: sqlite3.Connection, statement: str, parameters: tuple = (), match: str | None = None) -> None:
    with pytest.raises(sqlite3.IntegrityError, match=match):
        db.execute(statement, parameters)
    db.rollback()


def _rejects_row(db: sqlite3.Connection, table: str, row: dict[str, object]) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        _insert(db, table, row)
    db.rollback()


# --- the migration -------------------------------------------------------------------


def test_migration_13_registers_only_the_formed_setups(tmp_path):
    path = database_at(tmp_path / "registered.db", 14)
    with sqlite3.connect(path) as connection:
        objects = _objects(connection)

    assert (MIGRATIONS[12].version, MIGRATIONS[12].name) == (13, "manager-setups")
    assert objects["table"] == set(MANAGER_TABLES) == {"autotrader_manager_setups"}
    assert objects["trigger"] == REGISTERED_TRIGGERS
    assert REGISTERED_INDEXES <= objects["index"]
    assert objects["view"] == set()


def test_migration_16_registers_exactly_the_phase_2_tables():
    [migration] = [item for item in MIGRATIONS if item.version == MANAGER_PHASE2_MIGRATION]

    assert (migration.version, migration.name) == (16, "manager-day-plans")
    assert migration.schema_payload == MANAGER_PHASE2_DDL
    created = {
        statement.split("EXISTS", 1)[1].split()[0]
        for statement in MANAGER_PHASE2_DDL
        if statement.lstrip().startswith("CREATE TABLE")
    }
    assert created == set(MANAGER_PHASE2_TABLES)


def test_migration_15_registers_phase_1(tmp_path):
    path = database_at(tmp_path / "phase1.db", 15)
    with sqlite3.connect(path) as connection:
        objects = _objects(connection)

    assert MIGRATIONS[12].schema_payload == MANAGER_SETUPS_SCHEMA
    assert (MIGRATIONS[14].version, MIGRATIONS[14].name) == (15, "manager-day-universe")
    assert MIGRATIONS[14].schema_payload == MANAGER_PHASE1_SCHEMA
    assert objects["table"] == {*MANAGER_TABLES, *MANAGER_PHASE1_TABLES}
    assert objects["trigger"] == REGISTERED_TRIGGERS | PHASE1_TRIGGERS
    assert REGISTERED_INDEXES | PHASE1_INDEXES <= objects["index"]
    assert objects["view"] == set()


def test_every_manager_table_is_in_place_on_a_migrated_database(db):
    objects = _objects(db)

    assert objects["table"] == {*MANAGER_TABLES, *MANAGER_PHASE1_TABLES, *MANAGER_PHASE2_TABLES}
    assert objects["trigger"] == REGISTERED_TRIGGERS | PHASE1_TRIGGERS | PHASE2_TRIGGERS
    assert objects["index"] >= REGISTERED_INDEXES | PHASE1_INDEXES | PHASE2_INDEXES
    assert objects["view"] == {"autotrader_manager_switch"}
    assert db.execute("PRAGMA foreign_key_check").fetchall() == []


def test_the_schema_steps_and_regimes_are_the_managers_own():
    from trading.manager import constants

    text = "\n".join(MANAGER_PHASE2_DDL)
    assert "(0, 0.25, 0.5, 0.75, 1)" in text
    assert tuple(float(step) for step in constants.DAY_STEPS) == (0, 0.25, 0.5, 0.75, 1)
    assert "('" + "', '".join(constants.REGIMES) + "')" in text
    assert constants.FOCUS_MAX == 30 and "json_array_length(focus_json) <= 30" in text
    assert f"BETWEEN 1 AND {constants.NOTE_MAX}" in text


def test_migrations_15_and_16_on_a_version_14_database_keep_its_data_and_back_it_up(tmp_path):
    path = database_at(tmp_path / "autotrader.db", 14)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO autotrader_universe VALUES ('watchlist', 'AAPL', 'NASDAQ', 'AAPL', 265598, 'NASDAQ', 'resolved', NULL, 't')"
        )
        tables = _objects(connection)["table"]
    assert tables == set(MANAGER_TABLES)
    after_close = datetime.combine(date(2026, 10, 13), time(19, 0), tzinfo=MARKET_TIMEZONE).astimezone(timezone.utc)

    assert run_migrate(path, now=after_close, out=io.StringIO(), err=io.StringIO()) == 0

    assert inspect_schema(path).current
    [backup] = (tmp_path / "backups").iterdir()
    assert inspect_schema(backup).applied_version == 14
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT count(*) FROM autotrader_universe").fetchone() == (1,)
        for table in (*MANAGER_PHASE1_TABLES, *MANAGER_PHASE2_TABLES):
            assert connection.execute(f"SELECT count(*) FROM {table}").fetchone() == (0,)
        assert connection.execute("SELECT state FROM autotrader_manager_switch").fetchone() == ("off",)
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_migration_13_on_a_version_12_database_keeps_its_data_and_backs_it_up(tmp_path):
    path = database_at(tmp_path / "autotrader.db", 12)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO autotrader_universe VALUES ('watchlist', 'AAPL', 'NASDAQ', 'AAPL', 265598, 'NASDAQ', 'resolved', NULL, 't')"
        )
    after_close = datetime.combine(date(2026, 10, 13), time(19, 0), tzinfo=MARKET_TIMEZONE).astimezone(timezone.utc)

    assert run_migrate(path, now=after_close, out=io.StringIO(), err=io.StringIO()) == 0

    assert inspect_schema(path).current
    [backup] = (tmp_path / "backups").iterdir()
    assert inspect_schema(backup).applied_version == 12
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT count(*) FROM autotrader_universe").fetchone() == (1,)
        for table in MANAGER_TABLES:
            assert connection.execute(f"SELECT count(*) FROM {table}").fetchone() == (0,)
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


# --- formed setups -------------------------------------------------------------------


def test_a_formed_setup_is_recorded_once_per_run_and_fingerprint(db):
    upsert = "ON CONFLICT(run_id, setup_fingerprint) DO NOTHING"

    assert _insert(db, "autotrader_manager_setups", _setup(), upsert=upsert) == 1
    assert _insert(db, "autotrader_manager_setups", _setup(created_at="2026-10-13T16:00:00+00:00"), upsert=upsert) == 0
    assert _insert(db, "autotrader_manager_setups", _setup(setup_fingerprint=FP2), upsert=upsert) == 1
    assert db.execute("SELECT count(*) FROM autotrader_manager_setups").fetchone() == (2,)
    _rejects_row(db, "autotrader_manager_setups", _setup())  # a plain duplicate insert


@pytest.mark.parametrize(
    "overrides",
    [
        {"bar_ts": "2026-10-13T15:50:00"},  # naive
        {"bar_ts": "2026-10-13T11:50:00-04:00"},  # not UTC: text order would lie
        {"created_at": "2026-13-45T15:50:00+00:00"},
        {"session_date": "2026-02-30"},
        {"entry_price": "2.677E+1"},
        {"stop_price": "26.80"},  # stop above entry
        {"target_price": "26.70"},  # target below entry
        {"atr": "0"},
        {"hour_bucket": 24},
        {"detected_at": TS},  # not after the bar's start
        {"setup_fingerprint": "A" * 64},
        {"strategy_kind": "Momo"},
        {"levels_json": "[1, 2]"},
        {"setup_json": "not json"},
        {"con_id": 0},
    ],
)
def test_a_malformed_setup_is_refused(db, overrides):
    _rejects_row(db, "autotrader_manager_setups", _setup(**overrides))


@pytest.mark.parametrize(
    "overrides",
    [
        {"candidate_id": "cand-unknown"},
        {"con_id": 4932},
        {"symbol": "BF A"},
        {"strategy_version": 2},
        {"spec_fingerprint": "d" * 64},
        {"session_date": "2026-10-14"},
        {"run_id": "run-unknown"},
    ],
)
def test_a_setup_must_belong_to_its_runs_candidate_and_strategy(db, overrides):
    _rejects_row(db, "autotrader_manager_setups", _setup(**overrides))


# --- append-only logs ----------------------------------------------------------------


def _seed_logs(db: sqlite3.Connection) -> None:
    _insert(db, "autotrader_manager_setups", _setup())
    _insert(db, "autotrader_manager_plans", _plan())
    _insert(db, "autotrader_manager_verdicts", {
        "run_id": "run-1", "setup_fingerprint": FP, "plan_id": "plan-0920", "mode": "managed",
        "step": 0.5, "note": "size down: late in the move", "created_at": LATER,
    })
    _insert(db, "autotrader_manager_decisions", _managed_decision())
    on = _event(db, "on")
    _insert(db, "autotrader_manager_day_mode", {
        "session_date": "2026-10-13", "strategy_family": "momo", "mode": "managed",
        "latched_at": LATER, "switch_event_id": on,
    })
    _insert(db, "autotrader_manager_signal_outcomes", {
        "run_id": "run-1", "setup_fingerprint": FP, "fill_model_id": "replay-fill-v1",
        "simulated_at": LATER, "filled": 1, "exit_reason": "stop", "r_multiple": "-1.00",
        "cost_r": "0.04", "details_json": "{}",
    })
    db.commit()


@pytest.mark.parametrize(
    "table",
    [
        "autotrader_manager_setups",
        "autotrader_manager_plans",
        "autotrader_manager_verdicts",
        "autotrader_manager_decisions",
        "autotrader_manager_events",
        "autotrader_manager_day_mode",
        "autotrader_manager_signal_outcomes",
    ],
)
def test_logs_are_append_only(db, table):
    _seed_logs(db)
    column = {
        "autotrader_manager_plans": "manager_version",
        "autotrader_manager_events": "reason",
        "autotrader_manager_day_mode": "latched_at",
        "autotrader_manager_verdicts": "note",
        "autotrader_manager_decisions": "final_qty",
    }.get(table, "created_at" if table == "autotrader_manager_setups" else "exit_reason")

    _rejects(db, f"UPDATE {table} SET {column} = {column}", match="append-only")
    _rejects(db, f"DELETE FROM {table}", match="append-only")
    assert db.execute(f"SELECT count(*) FROM {table}").fetchone() == (1,)


# --- plans and verdicts --------------------------------------------------------------


def test_a_valid_plan_carries_its_whole_content_and_a_failed_one_none(db):
    _insert(db, "autotrader_manager_plans", _plan())
    _insert(db, "autotrader_manager_plans", _plan(
        plan_id="plan-0946", slot="09:46", outcome="ai_timeout", regime=None, strategies_json=None,
        focus_json=None, valid_until=None, context_digest=None, cost_usd="0.3700", latency_ms=90000,
    ))
    db.commit()

    for overrides in (
        {"plan_id": "p2", "regime": None},  # valid without a regime
        {"plan_id": "p3", "outcome": "ai_malformed"},  # failed yet carrying a full plan
        {"plan_id": "p4", "focus_json": json.dumps([{"con_id": 4931}] * 31)},  # focus over 30
        {"plan_id": "p5", "valid_until": "2026-10-13T13:20:00+00:00"},  # not after creation
        {"plan_id": "p6", "cost_usd": "-0.01"},
        {"plan_id": "p7", "outcome": "Valid"},
        {"plan_id": "P8"},
        {"plan_id": "p9", "strategies_json": "[]"},
        {"plan_id": "p10", "regime": "risk_on"},  # not a DayPlan regime
        {"plan_id": "p11", "slot": "9:20"},
        {"plan_id": "p12", "context_digest": None},  # a valid plan names what it saw
        {"plan_id": "p13", "candidates_json": "{}"},
    ):
        _rejects_row(db, "autotrader_manager_plans", _plan(**overrides))


def test_a_valid_plan_focuses_only_on_the_candidates_it_was_offered(db):
    outside = json.dumps([{"con_id": 4931, "strategies": ["momo"], "note": "ok"},
                          {"con_id": 999, "strategies": ["momo"], "note": "widening"}])
    with pytest.raises(sqlite3.IntegrityError, match="candidates it was offered"):
        _insert(db, "autotrader_manager_plans", _plan(plan_id="p-outside", focus_json=outside))
    db.rollback()
    _rejects_row(db, "autotrader_manager_plans", _plan(
        plan_id="p-text", focus_json=json.dumps([{"con_id": "4931", "strategies": [], "note": "x"}])
    ))
    assert _insert(db, "autotrader_manager_plans", _plan(plan_id="p-empty", focus_json="[]")) == 1


def test_a_verdict_is_one_quarter_step_and_one_line_per_setup(db):
    _insert(db, "autotrader_manager_plans", _plan())
    verdict = {"run_id": "run-1", "setup_fingerprint": FP, "plan_id": "plan-0920", "mode": "managed", "step": 0.75, "note": None, "created_at": LATER}
    _insert(db, "autotrader_manager_verdicts", verdict)
    db.commit()

    for overrides in (
        {"setup_fingerprint": FP},  # one verdict per setup and run
        {"setup_fingerprint": FP2, "step": 0.3},
        {"setup_fingerprint": FP2, "step": 2},
        {"setup_fingerprint": FP2, "note": "two\nlines"},
        {"setup_fingerprint": FP2, "plan_id": "plan-unknown"},
        {"setup_fingerprint": FP2, "run_id": "run-unknown"},
        {"setup_fingerprint": FP2, "note": "x" * 141},
        {"setup_fingerprint": FP2, "plan_id": None},  # managed without a plan
        {"setup_fingerprint": FP2, "mode": "neutral"},  # neutral with a plan
        {"setup_fingerprint": FP2, "mode": "neutral", "plan_id": None, "step": 0.5},
        {"setup_fingerprint": FP2, "mode": "auto"},
    ):
        _rejects_row(db, "autotrader_manager_verdicts", {**verdict, **overrides})
    # Neutral mode has no plan and keeps step 1.
    neutral = {**verdict, "setup_fingerprint": FP2, "plan_id": None, "mode": "neutral", "step": 1}
    assert _insert(db, "autotrader_manager_verdicts", neutral) == 1


# --- managed decisions: the narrowing, checked per decision ----------------------------


def _managed_decision(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "decision_id": "decision-1",
        "run_id": "run-1",
        "candidate_id": "cand-1",
        "con_id": 4931,
        "setup_fingerprint": FP,
        "session_date": "2026-10-13",
        "outcome": "shadow_accepted",
        "mode": "managed",
        "neutral_reason": None,
        "mark": None,
        "plan_id": "plan-0920",
        "eligibility_id": "f" * 64,
        "eligible_json": json.dumps([4931, 265598]),
        "focus_json": json.dumps([4931]),
        "gates_json": json.dumps({"risk_off": "open", "plan": "open"}),
        "day_step": 0.75,
        "setup_step": 0.5,
        "policy_qty": 3726,
        "final_qty": 1397,  # floor(3726 x 0.75 x 0.5)
        "created_at": LATER,
    }
    row.update(overrides)
    return row


def _neutral_decision(**overrides: object) -> dict[str, object]:
    return _managed_decision(**{
        "mode": "neutral", "neutral_reason": "plan_expired", "mark": "manager_fallback_neutral",
        "plan_id": None, "focus_json": None, "day_step": 1, "setup_step": 1, "final_qty": 3726,
        **overrides,
    })


def test_a_managed_decision_records_the_narrowing_it_applied(db):
    _insert(db, "autotrader_manager_plans", _plan())
    assert _insert(db, "autotrader_manager_decisions", _managed_decision()) == 1
    db.commit()

    for overrides in (
        {"final_qty": 3726},  # steps not applied
        {"final_qty": 1398},  # rounded up
        {"policy_qty": 1000, "day_step": 1, "setup_step": 1},  # above policy
        {"day_step": 2, "final_qty": None},  # a step outside the fixed set
        {"setup_step": 0.3, "final_qty": None},
        {"final_qty": 0},  # zero means skipped: no quantity at all
        {"plan_id": None},  # managed without a plan
        {"focus_json": None},
        {"neutral_reason": "plan_missing"},
        {"mark": "manager_fallback_neutral"},
        {"mark": "fallback"},
        {"eligible_json": "[]"},
        {"plan_id": "plan-unknown"},
        {"decision_id": "decision-unknown"},
    ):
        _rejects_row(db, "autotrader_manager_decisions", _managed_decision(**overrides))


def test_a_decision_outside_its_eligible_set_or_focus_is_refused(db):
    _insert(db, "autotrader_manager_plans", _plan())
    db.commit()
    for overrides in (
        {"eligible_json": json.dumps([265598])},  # not eligible
        {"focus_json": json.dumps([265598])},  # eligible, not in focus
    ):
        with pytest.raises(sqlite3.IntegrityError, match="eligible set and plan focus"):
            _insert(db, "autotrader_manager_decisions", _managed_decision(**overrides))
        db.rollback()
    # Neutral mode has no focus: eligibility alone binds it.
    with pytest.raises(sqlite3.IntegrityError, match="eligible set"):
        _insert(db, "autotrader_manager_decisions", _neutral_decision(eligible_json=json.dumps([265598])))
    db.rollback()


def test_a_neutral_decision_keeps_the_policy_quantity(db):
    assert _insert(db, "autotrader_manager_decisions", _neutral_decision()) == 1
    db.commit()
    _decision_row(db, "decision-2", FP2, "c" * 64, candidate="cand-2")
    db.commit()
    second = {"decision_id": "decision-2", "candidate_id": "cand-2", "con_id": 265598, "setup_fingerprint": FP2}
    for overrides in (
        {"final_qty": 1863},  # scaled in neutral mode
        {"day_step": 0.5, "final_qty": 1863},
        {"plan_id": "plan-0920"},
        {"focus_json": json.dumps([265598])},
        {"neutral_reason": None, "mark": None},
    ):
        _rejects_row(db, "autotrader_manager_decisions", _neutral_decision(**second, **overrides))
    # The switch being off is neutral by design, not a fallback: no mark.
    assert _insert(db, "autotrader_manager_decisions", _neutral_decision(
        **second, neutral_reason="switch_off", mark=None,
    )) == 1


# --- the switch and the day latch ----------------------------------------------------


def _switch(db: sqlite3.Connection) -> tuple[str, int | None]:
    return db.execute("SELECT state, event_id FROM autotrader_manager_switch").fetchone()


def test_the_switch_is_off_until_an_event_turns_it_on(db):
    assert _switch(db) == ("off", None)
    on = _event(db, "on")
    assert _switch(db) == ("on", on)
    off = _event(db, "off")
    assert _switch(db) == ("off", off) and off > on
    _rejects(db, "INSERT INTO autotrader_manager_events (ts, action, source, actor, reason) VALUES (?, 'maybe', 'cli', 'owner', 'x')", (TS,))
    _rejects(db, "INSERT INTO autotrader_manager_events (ts, action, source, actor, reason) VALUES (?, 'on', 'assistant', 'owner', 'x')", (TS,))


def test_the_day_latch_is_written_once_and_follows_the_switch(db):
    latch = "INSERT INTO autotrader_manager_day_mode VALUES ('2026-10-13', 'momo', ?, ?, ?)"
    upsert = latch + " ON CONFLICT(session_date, strategy_family) DO NOTHING"

    _rejects(db, latch, ("managed", LATER, None), "follow the switch")  # no event: OFF
    off = _event(db, "off")
    _rejects(db, latch, ("managed", LATER, off), "follow the switch")
    on = _event(db, "on")
    _rejects(db, latch, ("v2", LATER, on), "follow the switch")
    assert db.execute(upsert, ("managed", LATER, on)).rowcount == 1
    # Restarts reuse the latch: a second write is ignored, never applied.
    assert db.execute(upsert, ("v2", LATER, off)).rowcount == 0
    assert db.execute("SELECT mode, switch_event_id FROM autotrader_manager_day_mode").fetchall() == [("managed", on)]
    # Another family or day latches separately; an empty switch latches V2.
    db.execute("INSERT INTO autotrader_manager_day_mode VALUES ('2026-10-14', 'momo', 'v2', ?, NULL)", (LATER,))
    _rejects(db, latch.replace("'momo'", "'MOMO'"), ("v2", LATER, None))


# --- spend ----------------------------------------------------------------------------


def test_spend_is_reserved_then_settled_and_never_decreases(db):
    db.execute("INSERT INTO autotrader_manager_spend (spend_date, purpose, updated_at) VALUES ('2026-10-13', 'plan', ?)", (TS,))
    db.execute("UPDATE autotrader_manager_spend SET calls = 1, reserved_usd = '0.25', updated_at = ?", (LATER,))
    db.execute("UPDATE autotrader_manager_spend SET reserved_usd = '0', spent_usd = '0.0412', updated_at = ?", (LATER,))
    db.commit()

    _rejects(db, "UPDATE autotrader_manager_spend SET spent_usd = '0.01'", match="never decrease")
    _rejects(db, "UPDATE autotrader_manager_spend SET calls = 0", match="never decrease")
    _rejects(db, "UPDATE autotrader_manager_spend SET purpose = 'review'", match="date and purpose")
    _rejects(db, "DELETE FROM autotrader_manager_spend", match="permanent")
    _rejects(db, "UPDATE autotrader_manager_spend SET reserved_usd = '-1'")
    _rejects(db, "INSERT INTO autotrader_manager_spend (spend_date, purpose, updated_at) VALUES ('2026-10-13', 'plan', ?)", (TS,), "UNIQUE")
    assert db.execute("SELECT calls, reserved_usd, spent_usd FROM autotrader_manager_spend").fetchone() == (1, "0", "0.0412")


# --- the day universe -----------------------------------------------------------------


def test_the_day_universe_tracks_each_name_without_rewriting_its_history(db):
    row = {
        "session_date": "2026-10-13", "con_id": 4931, "symbol": "BF B", "first_seen": TS,
        "last_seen": TS, "sources_json": json.dumps(["watchlist"]), "best_rank": 3,
        "qualified": 1, "reason": None,
    }
    _insert(db, "autotrader_manager_day_universe", row)
    db.execute(
        "UPDATE autotrader_manager_day_universe SET last_seen = ?, best_rank = 1, sources_json = ?",
        (LATER, json.dumps(["watchlist", "ibkr_scan"])),
    )
    db.commit()

    _rejects(db, "UPDATE autotrader_manager_day_universe SET last_seen = ?", (TS,), "never moves back")
    _rejects(db, "UPDATE autotrader_manager_day_universe SET first_seen = ?", (LATER,), "keeps its session")
    _rejects(db, "UPDATE autotrader_manager_day_universe SET con_id = 1", match="keeps its session")
    for overrides in (
        {"con_id": 1, "sources_json": "[]"},
        {"con_id": 2, "qualified": 0, "reason": None},  # a name left out says why
        {"con_id": 3, "best_rank": 0},
        {"con_id": 4, "last_seen": "2026-10-13T15:00:00+00:00"},  # before the first sighting
        {"con_id": 6, "qualified": 0, "reason": "stock_type_ETF"},  # reasons are tokens
        {"con_id": 7, "primary_exchange": ""},
    ):
        _rejects_row(db, "autotrader_manager_day_universe", {**row, **overrides})
    assert _insert(db, "autotrader_manager_day_universe", {**row, "con_id": 5, "qualified": 0, "reason": "not_common_stock"}) == 1
    assert _insert(db, "autotrader_manager_day_universe", {**row, "con_id": 8, "primary_exchange": "NYSE"}) == 1


# --- contract facts -----------------------------------------------------------------------


def test_contract_facts_are_one_row_per_contract_and_name_their_source(db):
    row = {
        "con_id": 4931, "symbol": "BF B", "primary_exchange": "NYSE", "stock_type": "COMMON",
        "industry": "Consumer, Non-cyclical", "category": "Beverages", "source": "watchlist",
        "updated_at": TS,
    }
    _insert(db, "autotrader_manager_contract_info", row)
    upsert = (
        "ON CONFLICT(con_id) DO UPDATE SET industry = excluded.industry,"
        " source = excluded.source, updated_at = excluded.updated_at"
    )
    assert _insert(db, "autotrader_manager_contract_info", {**row, "source": "day_universe", "updated_at": LATER}, upsert=upsert) == 1
    db.commit()

    assert db.execute("SELECT count(*), source FROM autotrader_manager_contract_info").fetchone() == (1, "day_universe")
    for overrides in (
        {"con_id": 0},
        {"con_id": 2, "source": "assistant"},
        {"con_id": 3, "industry": ""},
        {"con_id": 4, "updated_at": "2026-10-13T15:50:00"},
        {"con_id": 5, "symbol": ""},
    ):
        _rejects_row(db, "autotrader_manager_contract_info", {**row, **overrides})
    unknown = {**row, "con_id": 6, "stock_type": None, "industry": None, "category": None}
    assert _insert(db, "autotrader_manager_contract_info", unknown) == 1


# --- simulated outcomes ---------------------------------------------------------------


def test_an_outcome_belongs_to_a_saved_setup_and_is_coherent(db):
    _insert(db, "autotrader_manager_setups", _setup())
    outcome = {
        "run_id": "run-1", "setup_fingerprint": FP, "fill_model_id": "replay-fill-v1",
        "simulated_at": LATER, "filled": 1, "exit_reason": "target", "r_multiple": "2.00",
        "cost_r": "0.04", "details_json": json.dumps({"exit_at": LATER}),
    }
    _insert(db, "autotrader_manager_signal_outcomes", outcome)
    db.commit()

    for overrides in (
        {"fill_model_id": "replay-fill-v1"},  # one row per setup and fill model
        {"fill_model_id": "v2", "setup_fingerprint": FP2},  # no saved setup
        {"fill_model_id": "v3", "filled": 0},  # not filled but an R
        {"fill_model_id": "v4", "filled": 0, "r_multiple": None, "exit_reason": "stop"},
        {"fill_model_id": "v5", "exit_reason": "not_filled"},
        {"fill_model_id": "v6", "r_multiple": "1e1"},
        {"fill_model_id": "v7", "cost_r": "-0.04"},
        {"fill_model_id": "Fill Model"},
    ):
        _rejects_row(db, "autotrader_manager_signal_outcomes", {**outcome, **overrides})
    assert _insert(db, "autotrader_manager_signal_outcomes", {
        **outcome, "fill_model_id": "replay-fill-v2", "filled": 0, "r_multiple": None, "exit_reason": "not_filled",
    }) == 1
    assert _insert(db, "autotrader_manager_signal_outcomes", {**outcome, "fill_model_id": "replay-fill-v3", "r_multiple": "-1.25"}) == 1

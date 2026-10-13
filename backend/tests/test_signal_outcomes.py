"""The nightly signal-outcome tracker: saved setups simulated forward on recorded bars.

The database is built by the registered migrations (13 holds the setups,
14 the execution ledger, 15 the day universe and the signal outcomes).
"""
from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
import sqlite3

import pytest

from tests.schema_support import database_at, migrate_database
from tests.test_research import SESSION, _capture_store
from trading.contracts import StrategySpec
from trading.orchestration.session_runner import orb_strategy_spec
from trading.research.costs import DEFAULT_COST_MODEL, CostModel
from trading.research.outcomes import (
    NO_DATABASE,
    OUTCOMES_TABLE_ABSENT,
    SavedSetup,
    simulate_setup,
    track_outcomes,
    tracker_readiness,
)
from trading.research.replay import FILL_MODEL_ID, replay_strategy
from trading.rules.all_session import vwap_pb_v2_strategy_spec
UTC = timezone.utc
TS = "2026-10-12T13:00:00+00:00"
LATER_SESSION = date(2026, 10, 13)  # not in the research store yet
SIMULATED_AT = datetime(2026, 10, 12, 22, 0, tzinfo=UTC)


def _phase1_database(path: Path) -> Path:
    """Migrations 1-14 plus the phase-1 draft (migration 15 once registered)."""
    migrate_database(path)
    return path


def _strategy(connection: sqlite3.Connection, strategy_id: str, spec: StrategySpec) -> None:
    connection.execute(
        "INSERT INTO autotrader_strategy_identities VALUES (?, 'owner', ?, NULL)", (strategy_id, TS)
    )
    connection.execute(
        """INSERT INTO autotrader_strategy_versions
           (strategy_id, version, content_hash, spec_json, created_at, created_by)
           VALUES (?, 1, ?, ?, ?, 'owner')""",
        (strategy_id, spec.fingerprint(), json.dumps(spec.model_dump(mode="json")), TS),
    )
    connection.execute(
        "INSERT INTO autotrader_strategy_lifecycle_v2 VALUES (?, 1, 'shadow', 0, ?)", (strategy_id, TS)
    )


def _run(
    connection: sqlite3.Connection,
    run_id: str,
    strategy_id: str,
    session_date: date,
    prints: dict[str, str] | None = None,
) -> None:
    connection.execute(
        """INSERT INTO autotrader_runs
           (id, owner_id, source_sha, strategy_id, strategy_version, session_date, mode, status,
            policy_fingerprints_json)
           VALUES (?, 'owner', 'abcdef1', ?, 1, ?, 'shadow', 'completed', ?)""",
        (run_id, strategy_id, session_date.isoformat(), json.dumps(prints or {})),
    )
    connection.execute(
        """INSERT INTO autotrader_scanner_runs
           (id, run_id, parameters_json, parameters_fingerprint, status, started_at)
           VALUES (?, ?, '{}', ?, 'completed', ?)""",
        (f"{run_id}-scan", run_id, "c" * 64, TS),
    )
    for rank, (con_id, symbol) in enumerate(((4931, "BF B"), (493581450, "FIGS")), start=1):
        connection.execute(
            """INSERT INTO autotrader_candidates
               (id, scanner_run_id, con_id, symbol, rank, contract_json, admission_status)
               VALUES (?, ?, ?, ?, ?, '{}', 'admitted')""",
            (f"{run_id}:{con_id}", f"{run_id}-scan", con_id, symbol, rank),
        )


def _setup(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    strategy_id: str,
    spec: StrategySpec,
    kind: str,
    fingerprint: str,
    con_id: int,
    symbol: str,
    session_date: date,
    signal_end: str,
    entry: str,
    stop: str,
    target: str,
) -> None:
    detected = datetime.fromisoformat(f"{session_date.isoformat()}T{signal_end}:00+00:00")
    bar_ts = detected - timedelta(minutes=5)
    connection.execute(
        """INSERT INTO autotrader_manager_setups
           (run_id, setup_fingerprint, candidate_id, strategy_id, strategy_version, strategy_name,
            strategy_kind, spec_fingerprint, symbol, con_id, session_date, bar_ts, detected_at,
            hour_bucket, entry_price, stop_price, target_price, atr, levels_json, setup_json,
            created_at)
           VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '0.09', '{}', '{}', ?)""",
        (
            run_id,
            fingerprint,
            f"{run_id}:{con_id}",
            strategy_id,
            spec.name,
            kind,
            spec.fingerprint(),
            symbol,
            con_id,
            session_date.isoformat(),
            bar_ts.isoformat(),
            detected.isoformat(),
            bar_ts.astimezone(timezone(timedelta(hours=-4))).hour,
            entry,
            stop,
            target,
            (detected + timedelta(seconds=40)).isoformat(),
        ),
    )


@pytest.fixture
def ledger(tmp_path) -> Iterator[Path]:
    """ORB_V1's two real 2026-10-12 setups, one unfillable, one not recorded yet."""
    path = _phase1_database(tmp_path / "autotrader.db")
    orb = orb_strategy_spec()
    vwap2 = vwap_pb_v2_strategy_spec()
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        _strategy(connection, "strategy-orb", orb)
        _strategy(connection, "strategy-vwap2", vwap2)
        _run(connection, "run-orb", "strategy-orb", SESSION)
        _run(connection, "run-vwap2", "strategy-vwap2", SESSION)
        _run(connection, "run-orb-later", "strategy-orb", LATER_SESSION)
        # The replay's ORB setups on the same bars (test_research): 10:00 and 10:15 ET.
        _setup(connection, run_id="run-orb", strategy_id="strategy-orb", spec=orb, kind="orb",
               fingerprint="a" * 64, con_id=4931, symbol="BF B", session_date=SESSION,
               signal_end="14:00", entry="26.6", stop="26.51", target="26.78")
        _setup(connection, run_id="run-orb", strategy_id="strategy-orb", spec=orb, kind="orb",
               fingerprint="b" * 64, con_id=493581450, symbol="FIGS", session_date=SESSION,
               signal_end="14:15", entry="16.93", stop="16.77", target="17.25")
        # A VWAP_PB_V2 entry far under the market at 13:25 ET: never filled.
        _setup(connection, run_id="run-vwap2", strategy_id="strategy-vwap2", spec=vwap2,
               kind="vwap_pb", fingerprint="c" * 64, con_id=493581450, symbol="FIGS",
               session_date=SESSION, signal_end="17:25", entry="15.00", stop="14.80",
               target="15.40")
        # The next session is not in the research store yet.
        _setup(connection, run_id="run-orb-later", strategy_id="strategy-orb", spec=orb,
               kind="orb", fingerprint="d" * 64, con_id=4931, symbol="BF B",
               session_date=LATER_SESSION, signal_end="14:00", entry="26.6", stop="26.51",
               target="26.78")
    yield path


def _outcomes(path: Path) -> dict[str, tuple]:
    with sqlite3.connect(path) as connection:
        rows = connection.execute(
            """SELECT setup_fingerprint, fill_model_id, filled, exit_reason, r_multiple, cost_r,
                      details_json
               FROM autotrader_manager_signal_outcomes ORDER BY id"""
        ).fetchall()
    return {row[0][:1] + ":" + row[1]: (row[2], row[3], row[4], row[5], json.loads(row[6])) for row in rows}


async def test_every_recorded_setup_is_simulated_once_with_the_replay_fill_model(ledger, tmp_path):
    store = _capture_store(tmp_path)

    report = await track_outcomes(store=store, database=ledger, now=SIMULATED_AT)

    assert report.to_payload() == {
        "status": "tracked",
        "reason": None,
        "fill_model_id": "replay-bracket-v1",
        "considered": 4,
        "written": 3,
        "filled": 2,
        "pending": 1,
        "oldest_pending": "2026-10-13",
        "errors": 0,
    }
    rows = _outcomes(ledger)
    assert set(rows) == {f"{name}:replay-bracket-v1" for name in "abc"}
    bf_b, figs, vwap = (rows[f"{name}:replay-bracket-v1"] for name in "abc")
    # BF B: filled on the 10:00 bar, stopped on the 10:05 bar's 26.51 low;
    # costs = 2 x $0.005 commission + one tick on the stop, over 0.09 of risk.
    assert bf_b[:4] == (1, "stop", "-1.0000", "0.2223")
    assert bf_b[4]["net_r"] == "-1.2223" and bf_b[4]["exit_at"] == "2026-10-12T14:10:00+00:00"
    # FIGS: the 17.25 limit target, no slippage: 0.01 / 0.16 of risk.
    assert figs[:4] == (1, "target", "2.0000", "0.0625")
    assert figs[4]["hour_bucket"] == 10 and figs[4]["cutoff_source"] == "strategy_cutoff"
    assert figs[4]["entry_cutoff"] == "2026-10-12T16:00:00+00:00"  # ORB_V1's 12:00 ET
    assert vwap[:4] == (0, "not_filled", None, "0")
    assert vwap[4]["entry_cutoff"] == "2026-10-12T19:30:00+00:00"  # VWAP_PB_V2's 15:30 ET


async def test_the_tracker_agrees_with_the_replay_on_the_same_setups(ledger, tmp_path):
    store = _capture_store(tmp_path)
    await track_outcomes(store=store, database=ledger, now=SIMULATED_AT)
    rows = _outcomes(ledger)

    replayed = {trade.symbol: trade for trade in replay_strategy("orb", store).trades}
    assert (rows["a:replay-bracket-v1"][1], rows["a:replay-bracket-v1"][2]) == (
        replayed["BF B"].exit_reason,
        str(replayed["BF B"].r_multiple),
    )
    assert (rows["b:replay-bracket-v1"][1], rows["b:replay-bracket-v1"][2]) == (
        replayed["FIGS"].exit_reason,
        str(replayed["FIGS"].r_multiple),
    )


async def test_the_tracker_is_idempotent_and_a_new_fill_model_simulates_again(ledger, tmp_path):
    store = _capture_store(tmp_path)
    await track_outcomes(store=store, database=ledger, now=SIMULATED_AT)

    again = await track_outcomes(store=store, database=ledger, now=SIMULATED_AT + timedelta(days=1))

    assert (again.considered, again.written, again.pending) == (1, 0, 1)
    assert len(_outcomes(ledger)) == 3
    other = await track_outcomes(
        store=store, database=ledger, now=SIMULATED_AT, fill_model_id="replay-bracket-v2"
    )
    assert (other.considered, other.written) == (4, 3)
    assert len(_outcomes(ledger)) == 6


async def test_outcomes_are_append_only(ledger, tmp_path):
    await track_outcomes(store=_capture_store(tmp_path), database=ledger, now=SIMULATED_AT)
    with sqlite3.connect(ledger) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute("UPDATE autotrader_manager_signal_outcomes SET exit_reason = 'target'")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute("DELETE FROM autotrader_manager_signal_outcomes")


def test_a_database_not_at_this_codes_schema_is_left_alone(tmp_path):
    store = _capture_store(tmp_path)
    missing = tmp_path / "missing.db"
    report = asyncio.run(track_outcomes(store=store, database=missing))
    assert (report.status, report.reason) == ("skipped", NO_DATABASE)
    assert not missing.exists()  # never created

    pending = database_at(tmp_path / "pending.db", 13)
    assert tracker_readiness(pending) == "schema_migration_pending"

    # Migration 15 registers the outcomes table, so a current database has it;
    # a database that lost it is still left alone.
    current = migrate_database(tmp_path / "current.db")
    with sqlite3.connect(current) as connection:
        connection.execute("DROP TABLE autotrader_manager_signal_outcomes")
    report = asyncio.run(track_outcomes(store=store, database=current))
    assert report.status == "skipped"
    assert report.reason in (OUTCOMES_TABLE_ABSENT, "schema_checksum_mismatch", "schema_unreadable")


def test_an_unknown_strategy_keeps_the_order_senders_last_entry(tmp_path):
    store = _capture_store(tmp_path)
    day = store.read_day(SESSION, 4931)
    setup = SavedSetup(
        run_id="run-x",
        setup_fingerprint="e" * 64,
        strategy_name="AP_MOMO_HOD_V1",
        strategy_kind="momo_hod",
        symbol="BF B",
        con_id=4931,
        session_date=SESSION,
        detected_at=datetime(2026, 10, 12, 14, 0, tzinfo=UTC),
        hour_bucket=9,
        entry=Decimal("26.6"),
        stop=Decimal("26.51"),
        target=Decimal("26.78"),
    )
    outcome = simulate_setup(setup, day)
    assert outcome.details["cutoff_source"] == "session_last_entry"
    assert outcome.details["entry_cutoff"] == "2026-10-12T19:30:00+00:00"
    assert outcome.net_r == Decimal("-1.2223")
    with pytest.raises(ValueError, match="session and contract"):
        simulate_setup(setup, store.read_day(SESSION, 493581450))


def test_the_research_catalogue_mirrors_the_live_registry_exactly():
    from trading.operations import shadow_service
    from trading.research import catalogue

    assert tuple(catalogue.EVALUATOR_FACTORIES) == tuple(shadow_service.STRATEGIES)
    for key, (factory, spec_factory, _prompt) in shadow_service.STRATEGIES.items():
        assert catalogue.EVALUATOR_FACTORIES[key] is factory
        assert catalogue.catalogue_name(key) == spec_factory().name
        assert catalogue.REGISTRY_KEYS[spec_factory().name] == key
        assert type(catalogue.evaluator_for(spec_factory().name)) is type(factory())
    assert catalogue.evaluator_for("AP_MOMO_HOD_V1") is None


def test_costs_are_per_share_for_simulations_and_per_order_for_real_fills():
    model = DEFAULT_COST_MODEL
    assert model.payload() == {
        "model_id": "ibkr-pro-fixed-v1",
        "commission_per_share": "0.005",
        "min_commission_per_order": "1.00",
        "max_commission_pct_of_value": "1",
        "tick": "0.01",
        "market_exit_slippage_ticks": "1",
    }
    # A limit target pays commissions only; a stop, flatten or mark adds a tick.
    assert model.per_share_cost("target") == Decimal("0.010")
    for reason in ("stop", "time", "data_end", "flatten"):
        assert model.per_share_cost(reason) == Decimal("0.020")
    # Rounded up: costs are never understated.
    assert model.cost_r(entry=Decimal("10.03"), stop=Decimal("10"), exit_reason="target") == Decimal("0.3334")
    assert model.order_commission(shares=100, price=Decimal("26.6")) == Decimal("1.00")  # minimum
    assert model.order_commission(shares=1000, price=Decimal("26.6")) == Decimal("5.00")
    assert model.order_commission(shares=50, price=Decimal("1.00")) == Decimal("0.50")  # 1% cap
    assert model.order_commission(shares=0, price=Decimal("26.6")) == Decimal("0")
    with pytest.raises(ValueError):
        CostModel(commission_per_share=Decimal("-0.001"))
    with pytest.raises(ValueError):
        model.cost_r(entry=Decimal("10"), stop=Decimal("10"), exit_reason="stop")

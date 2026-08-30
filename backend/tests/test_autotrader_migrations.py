from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal

import aiosqlite
import pytest

from trading.contracts import Decision, DecisionOutcome, FixedUniverse, RuleCondition, StrategySpec
from trading.storage.migrations import MIGRATIONS, Migration, apply_migrations
from trading.storage.repository import record_decision


async def _connect(path) -> aiosqlite.Connection:
    database = await aiosqlite.connect(path)
    await database.execute("PRAGMA foreign_keys=ON")
    return database


async def _seed_run_and_candidate(database: aiosqlite.Connection) -> tuple[str, str, str]:
    await apply_migrations(database)
    spec = StrategySpec(
        name="Migration test strategy",
        universe=FixedUniverse(symbols=("AAPL",)),
        conditions=(RuleCondition(feature="rvol", operator="gte", value=Decimal("1.5")),),
        rationale="Storage integrity fixture.",
    )
    strategy_id = "strategy-storage"
    run_id = "run-storage"
    candidate_id = "candidate-storage"
    now = datetime.now(timezone.utc).isoformat()
    await database.execute(
        "INSERT INTO autotrader_strategy_identities VALUES (?, ?, ?, NULL)",
        (strategy_id, "owner-a", now),
    )
    await database.execute(
        """
        INSERT INTO autotrader_strategy_versions
        (strategy_id, version, content_hash, spec_json, created_at, created_by)
        VALUES (?, 1, ?, ?, ?, ?)
        """,
        (strategy_id, spec.fingerprint(), json.dumps(spec.model_dump(mode="json")), now, "owner-a"),
    )
    await database.execute(
        "INSERT INTO autotrader_strategy_lifecycle_v2 VALUES (?, 1, 'shadow', 0, ?)",
        (strategy_id, now),
    )
    await database.execute(
        """
        INSERT INTO autotrader_runs
        (id, owner_id, source_sha, strategy_id, strategy_version, session_date, mode, status,
         policy_fingerprints_json)
        VALUES (?, ?, 'abcdef1', ?, 1, '2026-08-27', 'shadow', 'completed', '{}')
        """,
        (run_id, "owner-a", strategy_id),
    )
    await database.execute(
        """
        INSERT INTO autotrader_scanner_runs
        (id, run_id, parameters_json, parameters_fingerprint, status, started_at)
        VALUES ('scanner-storage', ?, '{}', ?, 'completed', ?)
        """,
        (run_id, "a" * 64, now),
    )
    await database.execute(
        """
        INSERT INTO autotrader_candidates
        (id, scanner_run_id, con_id, symbol, rank, contract_json, admission_status)
        VALUES (?, 'scanner-storage', 123, 'AAPL', 1, '{}', 'admitted')
        """,
        (candidate_id,),
    )
    await database.commit()
    return strategy_id, run_id, candidate_id


@pytest.mark.anyio
async def test_migrations_are_ordered_and_idempotent(tmp_path):
    database = await _connect(tmp_path / "fresh.db")
    try:
        await apply_migrations(database)
        await apply_migrations(database)
        async with database.execute(
            "SELECT version, checksum FROM autotrader_schema_migrations ORDER BY version"
        ) as cursor:
            rows = await cursor.fetchall()
    finally:
        await database.close()

    assert [row[0] for row in rows] == [migration.version for migration in MIGRATIONS]
    assert [row[1] for row in rows] == [migration.checksum for migration in MIGRATIONS]


@pytest.mark.anyio
async def test_legacy_strategy_is_preserved_and_live_authority_removed(tmp_path):
    database = await _connect(tmp_path / "legacy.db")
    legacy_spec = {
        "id": "legacy-id",
        "name": "Legacy strategy",
        "symbols": ["AAPL"],
        "timeframe": "5m",
        "conditions": [{"feature": "rvol", "operator": "gte", "value": "1.5"}],
        "min_reward_to_risk": "2",
        "rationale": "Preserve this strategy.",
    }
    try:
        await database.execute(
            """
            CREATE TABLE autotrader_strategies (
                id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, version INTEGER NOT NULL,
                state TEXT NOT NULL, spec_json TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )
            """
        )
        await database.execute(
            "CREATE TABLE autotrader_live_approvals (id TEXT PRIMARY KEY, owner_id TEXT)"
        )
        await database.execute(
            "CREATE TABLE autotrader_strategy_events (id TEXT PRIMARY KEY, strategy_id TEXT)"
        )
        await database.execute(
            "CREATE TABLE autotrader_decisions (id TEXT PRIMARY KEY, strategy_id TEXT)"
        )
        await database.execute(
            "INSERT INTO autotrader_strategies VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "legacy-id",
                "owner-1",
                3,
                "live",
                json.dumps(legacy_spec),
                "2026-08-01T12:00:00+00:00",
                "2026-08-02T12:00:00+00:00",
            ),
        )
        await database.execute(
            "INSERT INTO autotrader_live_approvals VALUES ('approval-1', 'owner-1')"
        )
        await database.execute(
            "INSERT INTO autotrader_strategy_events VALUES ('event-1', 'legacy-id')"
        )
        await database.execute(
            "INSERT INTO autotrader_decisions VALUES ('decision-1', 'legacy-id')"
        )
        await database.commit()

        await apply_migrations(database)

        async with database.execute(
            """
            SELECT v.version, l.state, v.spec_json
            FROM autotrader_strategy_versions v
            JOIN autotrader_strategy_lifecycle_v2 l
              ON l.strategy_id=v.strategy_id AND l.version=v.version
            WHERE v.strategy_id='legacy-id'
            """
        ) as cursor:
            migrated = await cursor.fetchone()
        async with database.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='autotrader_live_approvals'"
        ) as cursor:
            live_table = await cursor.fetchone()
        async with database.execute(
            "SELECT COUNT(*) FROM autotrader_migration_archive WHERE source_table='autotrader_live_approvals'"
        ) as cursor:
            archived_live = (await cursor.fetchone())[0]
        async with database.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='autotrader_retired_strategies'"
        ) as cursor:
            retired_strategies = await cursor.fetchone()
        async with database.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='autotrader_retired_live_approvals'"
        ) as cursor:
            retired_approvals = await cursor.fetchone()
        async with database.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='autotrader_retired_strategy_events'"
        ) as cursor:
            retired_events = await cursor.fetchone()
        async with database.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='autotrader_retired_decisions'"
        ) as cursor:
            retired_decisions = await cursor.fetchone()
    finally:
        await database.close()

    assert migrated is not None
    assert migrated[0] == 3
    assert migrated[1] == "paused"
    assert json.loads(migrated[2])["universe"] == {"kind": "fixed", "symbols": ["AAPL"]}
    assert live_table is None
    assert archived_live == 1
    assert retired_strategies is not None
    assert retired_approvals is not None
    assert retired_events is not None
    assert retired_decisions is not None


@pytest.mark.anyio
async def test_migration_checksum_mismatch_fails_closed(tmp_path):
    database = await _connect(tmp_path / "tampered.db")
    try:
        await apply_migrations(database)
        await database.execute(
            "UPDATE autotrader_schema_migrations SET checksum='tampered' WHERE version=1"
        )
        await database.commit()
        with pytest.raises(RuntimeError, match="checksum mismatch"):
            await apply_migrations(database)
    finally:
        await database.close()


@pytest.mark.anyio
async def test_migration_checksum_covers_schema_and_implementation():
    async def noop(_database):
        return None

    first = Migration(1, "same", "same", noop, ("CREATE TABLE one",))
    second = Migration(1, "same", "same", noop, ("CREATE TABLE two",))
    assert first.checksum != second.checksum


@pytest.mark.anyio
async def test_migration_creates_non_overwriting_recovery_backup(tmp_path):
    database = await _connect(tmp_path / "recoverable.db")
    backup_path = tmp_path / "backup.db"
    try:
        await database.execute("CREATE TABLE owner_data (id TEXT PRIMARY KEY, value TEXT)")
        await database.execute("INSERT INTO owner_data VALUES ('one', 'preserve-me')")
        await database.commit()
        await apply_migrations(database, backup_path=backup_path)
        with pytest.raises(FileExistsError):
            await apply_migrations(database, backup_path=backup_path)
    finally:
        await database.close()

    backup = await _connect(backup_path)
    try:
        async with backup.execute("SELECT value FROM owner_data WHERE id='one'") as cursor:
            row = await cursor.fetchone()
    finally:
        await backup.close()
    assert row == ("preserve-me",)


@pytest.mark.anyio
async def test_immutable_authority_and_policy_rows_and_bracket_checks(tmp_path):
    database = await _connect(tmp_path / "integrity.db")
    try:
        strategy_id, run_id, candidate_id = await _seed_run_and_candidate(database)
        await database.execute(
            """
            INSERT INTO autotrader_policy_versions
            VALUES ('risk', 1, 'owner-a', 'risk', ?, '{}', ?)
            """,
            ("a" * 64, datetime.now(timezone.utc).isoformat()),
        )
        await database.execute(
            """
            INSERT INTO autotrader_authority_receipts
            VALUES ('receipt-1', ?, 'abcdef1', ?, ?, 1, ?, 'paper-account', 17, ?, '{}', NULL, ?)
            """,
            (
                "b" * 64,
                "c" * 64,
                strategy_id,
                json.dumps(["d" * 64]),
                datetime.now(timezone.utc).isoformat(),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        await database.commit()
        with pytest.raises(aiosqlite.IntegrityError, match="immutable"):
            await database.execute("DELETE FROM autotrader_policy_versions WHERE policy_id='risk'")
        await database.rollback()
        with pytest.raises(aiosqlite.IntegrityError, match="immutable"):
            await database.execute(
                "UPDATE autotrader_authority_receipts SET evidence_digest=? WHERE id='receipt-1'",
                ("e" * 64,),
            )
        await database.rollback()
        await database.execute(
            """
            INSERT INTO autotrader_risk_reservations
            (id, run_id, candidate_id, symbol, execution_key, approval_token_hash,
             setup_fingerprint, ai_fingerprint, policy_fingerprint, portfolio_fingerprint,
             quantity, risk_amount, notional_amount, state, expires_at, created_at)
            VALUES ('reservation-1', ?, ?, 'AAPL', 'exec-1', ?, ?, ?, ?, ?, 10, '10', '1000', 'active', ?, ?)
            """,
            (
                run_id,
                candidate_id,
                "f" * 64,
                "a" * 64,
                "b" * 64,
                "c" * 64,
                "d" * 64,
                datetime.now(timezone.utc).isoformat(),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        await database.execute(
            """
            INSERT INTO autotrader_bracket_groups
            (id, owner_id, run_id, candidate_id, risk_reservation_id, execution_key, account,
             client_id, symbol, quantity, entry_price, stop_price, target_price, state, created_at, updated_at)
            VALUES ('group-1', 'owner-a', ?, ?, 'reservation-1', 'exec-1', 'paper-account', 17,
                    'AAPL', 10, '100', '99', NULL, 'staged', ?, ?)
            """,
            (run_id, candidate_id, datetime.now(timezone.utc).isoformat(), datetime.now(timezone.utc).isoformat()),
        )
        await database.commit()
        with pytest.raises(aiosqlite.IntegrityError):
            await database.execute(
                """
                INSERT INTO autotrader_bracket_groups
                (id, owner_id, run_id, candidate_id, risk_reservation_id, execution_key, account,
                 client_id, symbol, quantity, entry_price, stop_price, target_price, state, created_at, updated_at)
                VALUES ('group-2', 'owner-a', ?, ?, 'reservation-1', 'exec-2', 'paper-account', 17,
                        'AAPL', 10, '100', '101', NULL, 'staged', ?, ?)
                """,
                (run_id, candidate_id, datetime.now(timezone.utc).isoformat(), datetime.now(timezone.utc).isoformat()),
            )
        await database.rollback()
    finally:
        await database.close()


@pytest.mark.anyio
async def test_ai_failure_rows_and_decision_coherence_are_enforced(tmp_path, monkeypatch):
    database = await _connect(tmp_path / "coherence.db")
    try:
        strategy_id, run_id, candidate_id = await _seed_run_and_candidate(database)
        await database.execute(
            """
            INSERT INTO autotrader_ai_calls
            (id, run_id, candidate_id, setup_fingerprint, status, model_id, prompt_version,
             response_hash, latency_ms, cost_usd, decision, rationale, failure_reason, created_at)
            VALUES ('ai-failed', ?, ?, ?, 'failed', NULL, NULL, NULL, NULL, NULL, NULL, NULL,
                    'provider timeout', ?)
            """,
            (run_id, candidate_id, "a" * 64, datetime.now(timezone.utc).isoformat()),
        )
        await database.commit()
        with pytest.raises(aiosqlite.IntegrityError):
            await database.execute(
                """
                INSERT INTO autotrader_ai_calls
                (id, run_id, candidate_id, setup_fingerprint, status, model_id, prompt_version,
                 response_hash, latency_ms, cost_usd, decision, rationale, failure_reason, created_at)
                VALUES ('ai-invalid', ?, ?, ?, 'failed', NULL, NULL, NULL, NULL, NULL, 'ABSTAIN', NULL, NULL, ?)
                """,
                (run_id, candidate_id, "a" * 64, datetime.now(timezone.utc).isoformat()),
            )
        await database.rollback()
    finally:
        await database.close()

    from config import cfg

    repository_path = tmp_path / "repository.db"
    monkeypatch.setattr(cfg, "DB_PATH", str(repository_path))
    from db.core import init_db

    await init_db()
    repository_database = await _connect(repository_path)
    try:
        strategy_id, run_id, candidate_id = await _seed_run_and_candidate(repository_database)
    finally:
        await repository_database.close()
    decision = Decision(
        run_id=run_id,
        candidate_id=candidate_id,
        strategy_id=strategy_id,
        strategy_version=1,
        decision_key="b" * 64,
        outcome=DecisionOutcome.RULE_REJECTED,
    )
    with pytest.raises(ValueError, match="does not own"):
        await record_decision(decision=decision, owner_id="owner-b")
    await record_decision(decision=decision, owner_id="owner-a")

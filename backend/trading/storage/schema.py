"""Ordered SQLite schema fragments for Autotrader V1 migrations."""

MIGRATION_LEDGER_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS autotrader_schema_migrations (
        version INTEGER PRIMARY KEY,
        name TEXT UNIQUE NOT NULL,
        checksum TEXT NOT NULL,
        applied_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS autotrader_migration_archive (
        source_table TEXT NOT NULL,
        source_pk TEXT NOT NULL,
        payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
        archived_at TEXT NOT NULL,
        PRIMARY KEY(source_table, source_pk)
    )
    """,
)

STRATEGY_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS autotrader_strategy_identities (
        id TEXT PRIMARY KEY,
        owner_id TEXT NOT NULL,
        created_at TEXT NOT NULL,
        retired_at TEXT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS autotrader_strategy_versions (
        strategy_id TEXT NOT NULL,
        version INTEGER NOT NULL,
        content_hash TEXT NOT NULL,
        spec_json TEXT NOT NULL CHECK(json_valid(spec_json)),
        created_at TEXT NOT NULL,
        created_by TEXT NOT NULL,
        PRIMARY KEY(strategy_id, version),
        UNIQUE(strategy_id, content_hash),
        FOREIGN KEY(strategy_id) REFERENCES autotrader_strategy_identities(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS autotrader_strategy_lifecycle_v2 (
        strategy_id TEXT NOT NULL,
        version INTEGER NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('draft','backtesting','shadow','paper','paused','retired')),
        revision INTEGER NOT NULL DEFAULT 0 CHECK(revision >= 0),
        updated_at TEXT NOT NULL,
        PRIMARY KEY(strategy_id, version),
        FOREIGN KEY(strategy_id, version)
            REFERENCES autotrader_strategy_versions(strategy_id, version) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS autotrader_strategy_events_v2 (
        id TEXT PRIMARY KEY,
        strategy_id TEXT NOT NULL,
        strategy_version INTEGER NOT NULL,
        owner_id TEXT NOT NULL,
        event_type TEXT NOT NULL,
        from_state TEXT NULL,
        to_state TEXT NULL,
        actor TEXT NOT NULL,
        reason TEXT NOT NULL,
        expected_revision INTEGER NULL,
        new_revision INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        FOREIGN KEY(strategy_id, strategy_version)
            REFERENCES autotrader_strategy_versions(strategy_id, version) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS autotrader_policy_versions (
        policy_id TEXT NOT NULL,
        version INTEGER NOT NULL,
        owner_id TEXT NOT NULL,
        policy_type TEXT NOT NULL,
        content_hash TEXT NOT NULL,
        policy_json TEXT NOT NULL CHECK(json_valid(policy_json)),
        created_at TEXT NOT NULL,
        PRIMARY KEY(policy_id, version),
        UNIQUE(owner_id, policy_type, content_hash)
    )
    """,
    """
    CREATE TRIGGER IF NOT EXISTS autotrader_strategy_versions_no_update
    BEFORE UPDATE ON autotrader_strategy_versions BEGIN SELECT RAISE(ABORT, 'strategy versions are immutable'); END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS autotrader_strategy_versions_no_delete
    BEFORE DELETE ON autotrader_strategy_versions BEGIN SELECT RAISE(ABORT, 'strategy versions are immutable'); END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS autotrader_policy_versions_no_update
    BEFORE UPDATE ON autotrader_policy_versions BEGIN SELECT RAISE(ABORT, 'policy versions are immutable'); END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS autotrader_policy_versions_no_delete
    BEFORE DELETE ON autotrader_policy_versions BEGIN SELECT RAISE(ABORT, 'policy versions are immutable'); END
    """,
)

RUN_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS autotrader_runs (
        id TEXT PRIMARY KEY,
        owner_id TEXT NOT NULL,
        source_sha TEXT NOT NULL,
        strategy_id TEXT NOT NULL,
        strategy_version INTEGER NOT NULL,
        session_date TEXT NOT NULL,
        mode TEXT NOT NULL CHECK(mode IN ('backtest','shadow','paper')),
        status TEXT NOT NULL CHECK(status IN ('pending','running','completed','failed')),
        policy_fingerprints_json TEXT NOT NULL CHECK(json_valid(policy_fingerprints_json)),
        started_at TEXT NULL,
        closed_at TEXT NULL,
        FOREIGN KEY(strategy_id, strategy_version)
            REFERENCES autotrader_strategy_versions(strategy_id, version)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS autotrader_scanner_runs (
        id TEXT PRIMARY KEY,
        run_id TEXT UNIQUE NOT NULL,
        parameters_json TEXT NOT NULL CHECK(json_valid(parameters_json)),
        parameters_fingerprint TEXT NOT NULL,
        status TEXT NOT NULL,
        started_at TEXT NOT NULL,
        completed_at TEXT NULL,
        FOREIGN KEY(run_id) REFERENCES autotrader_runs(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS autotrader_candidates (
        id TEXT PRIMARY KEY,
        scanner_run_id TEXT NOT NULL,
        con_id INTEGER NOT NULL,
        symbol TEXT NOT NULL,
        rank INTEGER NOT NULL CHECK(rank BETWEEN 1 AND 50),
        contract_json TEXT NOT NULL CHECK(json_valid(contract_json)),
        admission_status TEXT NOT NULL CHECK(admission_status IN ('admitted','rejected')),
        rejection_reason TEXT NULL,
        UNIQUE(scanner_run_id, rank),
        UNIQUE(scanner_run_id, con_id),
        FOREIGN KEY(scanner_run_id) REFERENCES autotrader_scanner_runs(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS autotrader_market_bars (
        id TEXT PRIMARY KEY,
        con_id INTEGER NOT NULL,
        interval_start_utc TEXT NOT NULL,
        interval_end_utc TEXT NOT NULL,
        timeframe TEXT NOT NULL CHECK(timeframe='5m'),
        source TEXT NOT NULL,
        data_mode TEXT NOT NULL,
        rth INTEGER NOT NULL CHECK(rth IN (0,1)),
        is_complete INTEGER NOT NULL CHECK(is_complete=1),
        ohlcv_json TEXT NOT NULL CHECK(json_valid(ohlcv_json)),
        fingerprint TEXT UNIQUE NOT NULL,
        UNIQUE(con_id, interval_start_utc, timeframe, source, data_mode)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS autotrader_run_bars (
        run_id TEXT NOT NULL,
        candidate_id TEXT NOT NULL,
        bar_id TEXT NOT NULL,
        PRIMARY KEY(run_id, candidate_id, bar_id),
        FOREIGN KEY(run_id) REFERENCES autotrader_runs(id) ON DELETE CASCADE,
        FOREIGN KEY(candidate_id) REFERENCES autotrader_candidates(id) ON DELETE CASCADE,
        FOREIGN KEY(bar_id) REFERENCES autotrader_market_bars(id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS autotrader_feature_snapshots (
        id TEXT PRIMARY KEY,
        candidate_id TEXT NOT NULL,
        input_fingerprint TEXT NOT NULL,
        fingerprint TEXT UNIQUE NOT NULL,
        features_json TEXT NOT NULL CHECK(json_valid(features_json)),
        created_at TEXT NOT NULL,
        FOREIGN KEY(candidate_id) REFERENCES autotrader_candidates(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS autotrader_ai_calls (
        id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,
        candidate_id TEXT NOT NULL,
        setup_fingerprint TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('succeeded','failed','blocked')),
        model_id TEXT NULL,
        prompt_version TEXT NULL,
        response_hash TEXT NULL,
        latency_ms INTEGER NULL CHECK(latency_ms IS NULL OR latency_ms >= 0),
        cost_usd TEXT NULL CHECK(cost_usd IS NULL OR CAST(cost_usd AS REAL) >= 0),
        decision TEXT NULL CHECK(decision IN ('PROPOSE','ABSTAIN') OR decision IS NULL),
        rationale TEXT NULL,
        failure_reason TEXT NULL,
        created_at TEXT NOT NULL,
        CHECK(
            (status='succeeded' AND decision IS NOT NULL AND model_id IS NOT NULL
             AND prompt_version IS NOT NULL AND response_hash IS NOT NULL
             AND latency_ms IS NOT NULL AND cost_usd IS NOT NULL
             AND rationale IS NOT NULL AND failure_reason IS NULL)
            OR
            (status IN ('failed','blocked') AND decision IS NULL
             AND failure_reason IS NOT NULL)
        ),
        FOREIGN KEY(run_id) REFERENCES autotrader_runs(id) ON DELETE CASCADE,
        FOREIGN KEY(candidate_id) REFERENCES autotrader_candidates(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS autotrader_candidate_decisions (
        id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,
        candidate_id TEXT NOT NULL UNIQUE,
        strategy_id TEXT NOT NULL,
        strategy_version INTEGER NOT NULL,
        decision_key TEXT UNIQUE NOT NULL,
        outcome TEXT NOT NULL CHECK(outcome IN ('data_blocked','rule_rejected','ai_abstained','ai_blocked','risk_blocked','shadow_accepted','approved','submitted')),
        setup_fingerprint TEXT NULL,
        ai_call_id TEXT NULL,
        risk_reservation_id TEXT NULL,
        payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
        created_at TEXT NOT NULL,
        FOREIGN KEY(run_id) REFERENCES autotrader_runs(id) ON DELETE CASCADE,
        FOREIGN KEY(candidate_id) REFERENCES autotrader_candidates(id) ON DELETE CASCADE,
        FOREIGN KEY(strategy_id, strategy_version)
            REFERENCES autotrader_strategy_versions(strategy_id, version),
        FOREIGN KEY(ai_call_id) REFERENCES autotrader_ai_calls(id),
        FOREIGN KEY(risk_reservation_id) REFERENCES autotrader_risk_reservations(id)
    )
    """,
)

AUTHORITY_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS autotrader_scheduler_leases (
        name TEXT PRIMARY KEY,
        holder_id TEXT NOT NULL,
        fencing_token INTEGER NOT NULL,
        acquired_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS autotrader_risk_reservations (
        id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,
        candidate_id TEXT NOT NULL,
        symbol TEXT NOT NULL,
        execution_key TEXT UNIQUE NOT NULL,
        approval_token_hash TEXT UNIQUE NOT NULL,
        setup_fingerprint TEXT NOT NULL,
        ai_fingerprint TEXT NOT NULL,
        policy_fingerprint TEXT NOT NULL,
        portfolio_fingerprint TEXT NOT NULL,
        quantity INTEGER NOT NULL CHECK(quantity > 0),
        risk_amount TEXT NOT NULL,
        notional_amount TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('active','consumed','released','expired')),
        expires_at TEXT NOT NULL,
        created_at TEXT NOT NULL,
        consumed_at TEXT NULL,
        FOREIGN KEY(run_id) REFERENCES autotrader_runs(id) ON DELETE CASCADE,
        FOREIGN KEY(candidate_id) REFERENCES autotrader_candidates(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS autotrader_one_active_symbol_reservation
    ON autotrader_risk_reservations(run_id, symbol) WHERE state='active'
    """,
    """
    CREATE TABLE IF NOT EXISTS autotrader_authority_receipts (
        id TEXT PRIMARY KEY,
        receipt_digest TEXT UNIQUE NOT NULL,
        build_sha TEXT NOT NULL,
        evidence_digest TEXT NOT NULL,
        strategy_id TEXT NOT NULL,
        strategy_version INTEGER NOT NULL,
        policy_hashes_json TEXT NOT NULL CHECK(json_valid(policy_hashes_json)),
        paper_account TEXT NOT NULL,
        client_id INTEGER NOT NULL,
        expires_at TEXT NOT NULL,
        owner_approval_json TEXT NOT NULL CHECK(json_valid(owner_approval_json)),
        revoked_at TEXT NULL,
        created_at TEXT NOT NULL,
        FOREIGN KEY(strategy_id, strategy_version)
            REFERENCES autotrader_strategy_versions(strategy_id, version)
    )
    """,
    """
    CREATE TRIGGER IF NOT EXISTS autotrader_authority_receipts_no_update
    BEFORE UPDATE ON autotrader_authority_receipts BEGIN SELECT RAISE(ABORT, 'authority receipts are immutable'); END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS autotrader_authority_receipts_no_delete
    BEFORE DELETE ON autotrader_authority_receipts BEGIN SELECT RAISE(ABORT, 'authority receipts are immutable'); END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS autotrader_decisions_coherent_run_candidate
    BEFORE INSERT ON autotrader_candidate_decisions
    WHEN NOT EXISTS (
        SELECT 1
        FROM autotrader_runs r
        JOIN autotrader_scanner_runs sr ON sr.run_id=r.id
        JOIN autotrader_candidates c ON c.scanner_run_id=sr.id
        WHERE r.id=NEW.run_id AND c.id=NEW.candidate_id
          AND r.strategy_id=NEW.strategy_id
          AND r.strategy_version=NEW.strategy_version
    )
    BEGIN SELECT RAISE(ABORT, 'decision run, candidate, and strategy are incoherent'); END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS autotrader_decisions_coherent_risk
    BEFORE INSERT ON autotrader_candidate_decisions
    WHEN NEW.risk_reservation_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM autotrader_risk_reservations rr
        WHERE rr.id=NEW.risk_reservation_id
          AND rr.run_id=NEW.run_id AND rr.candidate_id=NEW.candidate_id
    )
    BEGIN SELECT RAISE(ABORT, 'decision risk reservation is incoherent'); END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS autotrader_decisions_coherent_ai
    BEFORE INSERT ON autotrader_candidate_decisions
    WHEN NEW.ai_call_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM autotrader_ai_calls ac
        WHERE ac.id=NEW.ai_call_id AND ac.run_id=NEW.run_id AND ac.candidate_id=NEW.candidate_id
          AND (NEW.setup_fingerprint IS NULL OR ac.setup_fingerprint=NEW.setup_fingerprint)
    )
    BEGIN SELECT RAISE(ABORT, 'decision AI call is incoherent'); END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS autotrader_candidate_decisions_no_update
    BEFORE UPDATE ON autotrader_candidate_decisions BEGIN SELECT RAISE(ABORT, 'decisions are immutable'); END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS autotrader_candidate_decisions_no_delete
    BEFORE DELETE ON autotrader_candidate_decisions BEGIN SELECT RAISE(ABORT, 'decisions are immutable'); END
    """,
)

AUTHORITY_RECEIPT_ATTESTATION_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS autotrader_authority_receipt_attestations (
        receipt_id TEXT PRIMARY KEY,
        signature TEXT NOT NULL CHECK(length(signature) IN (86, 87, 88, 128)),
        public_key_id TEXT NOT NULL CHECK(length(trim(public_key_id)) > 0),
        issued_at TEXT NOT NULL,
        expires_after_sessions INTEGER NOT NULL DEFAULT 5
            CHECK(expires_after_sessions BETWEEN 1 AND 5),
        FOREIGN KEY(receipt_id) REFERENCES autotrader_authority_receipts(id)
    )
    """,
    """
    CREATE TRIGGER IF NOT EXISTS autotrader_authority_receipt_attestations_no_update
    BEFORE UPDATE ON autotrader_authority_receipt_attestations
    BEGIN SELECT RAISE(ABORT, 'authority receipt attestations are immutable'); END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS autotrader_authority_receipt_attestations_no_delete
    BEFORE DELETE ON autotrader_authority_receipt_attestations
    BEGIN SELECT RAISE(ABORT, 'authority receipt attestations are immutable'); END
    """,
)

EXECUTION_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS autotrader_bracket_groups (
        id TEXT PRIMARY KEY,
        owner_id TEXT NOT NULL,
        run_id TEXT NOT NULL,
        candidate_id TEXT NOT NULL,
        risk_reservation_id TEXT UNIQUE NOT NULL,
        execution_key TEXT UNIQUE NOT NULL,
        account TEXT NOT NULL,
        client_id INTEGER NOT NULL,
        symbol TEXT NOT NULL,
        quantity INTEGER NOT NULL,
        entry_price TEXT NOT NULL,
        stop_price TEXT NOT NULL,
        target_price TEXT NULL,
        state TEXT NOT NULL CHECK(state IN ('staged','submitted','filled','canceled','failed','manual_intervention')),
        manual_intervention_reason TEXT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        CHECK(quantity > 0),
        CHECK(CAST(entry_price AS REAL) > 0 AND CAST(entry_price AS REAL) < 1e308),
        CHECK(CAST(stop_price AS REAL) > 0 AND CAST(stop_price AS REAL) < 1e308),
        CHECK(CAST(stop_price AS REAL) < CAST(entry_price AS REAL)),
        CHECK(target_price IS NULL OR (CAST(target_price AS REAL) > CAST(entry_price AS REAL)
                                      AND CAST(target_price AS REAL) < 1e308)),
        FOREIGN KEY(run_id) REFERENCES autotrader_runs(id),
        FOREIGN KEY(candidate_id) REFERENCES autotrader_candidates(id),
        FOREIGN KEY(risk_reservation_id) REFERENCES autotrader_risk_reservations(id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS autotrader_bracket_legs (
        id TEXT PRIMARY KEY,
        group_id TEXT NOT NULL,
        leg_type TEXT NOT NULL CHECK(leg_type IN ('parent','stop','target')),
        broker_order_id TEXT NULL,
        perm_id TEXT NULL,
        order_ref TEXT UNIQUE NOT NULL,
        oca_group TEXT NULL,
        quantity INTEGER NOT NULL CHECK(quantity > 0),
        price TEXT NULL,
        transmit INTEGER NOT NULL CHECK(transmit IN (0,1)),
        state TEXT NOT NULL CHECK(state IN ('staged','submitted','accepted','filled','canceled','rejected','unknown')),
        CHECK(leg_type='target' OR price IS NOT NULL),
        CHECK(price IS NULL OR (CAST(price AS REAL) > 0 AND CAST(price AS REAL) < 1e308)),
        UNIQUE(group_id, leg_type),
        FOREIGN KEY(group_id) REFERENCES autotrader_bracket_groups(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS autotrader_broker_events (
        id TEXT PRIMARY KEY,
        group_id TEXT NOT NULL,
        leg_id TEXT NULL,
        event_fingerprint TEXT UNIQUE NOT NULL,
        event_type TEXT NOT NULL,
        payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
        broker_time TEXT NULL,
        received_at TEXT NOT NULL,
        FOREIGN KEY(group_id) REFERENCES autotrader_bracket_groups(id),
        FOREIGN KEY(leg_id) REFERENCES autotrader_bracket_legs(id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS autotrader_reconciliation_reports (
        id TEXT PRIMARY KEY,
        account TEXT NOT NULL,
        client_id INTEGER NOT NULL,
        run_id TEXT NULL,
        status TEXT NOT NULL CHECK(status IN ('clear','blocked')),
        positions_json TEXT NOT NULL CHECK(json_valid(positions_json)),
        orders_json TEXT NOT NULL CHECK(json_valid(orders_json)),
        executions_json TEXT NOT NULL CHECK(json_valid(executions_json)),
        unknowns_json TEXT NOT NULL CHECK(json_valid(unknowns_json)),
        mismatches_json TEXT NOT NULL CHECK(json_valid(mismatches_json)),
        created_at TEXT NOT NULL,
        FOREIGN KEY(run_id) REFERENCES autotrader_runs(id)
    )
    """,
)

EXECUTION_HARDENING_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS autotrader_execution_fences (
        name TEXT PRIMARY KEY,
        stopped INTEGER NOT NULL CHECK(stopped IN (0,1)),
        reason TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
)

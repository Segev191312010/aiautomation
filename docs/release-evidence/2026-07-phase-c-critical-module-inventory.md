# Phase C Critical-Module Inventory

Date: 2026-07-14

Status: **ACCEPTED D14 BOUNDARY - OWNER APPROVED 2026-07-14**

Implementation authority: **C0 MANIFEST/VERIFICATION TOOLING ONLY; C10 NOT
AUTHORIZED**

Purpose: define the exact current runtime/trading/persistence boundary whose broad
exception handling must be classified at C10 and whose new unclassified sites are
blocked from C1 onward. This is deliberately narrower than every backend module,
but filename-independent capability triggers prevent a rename from escaping it.

## Current exact inventory

All 77 paths below exist in the current workspace. Tests are excluded from this
production inventory.

<!-- d14-current-start -->

```text
backend/advisor_api.py
backend/ai_advisor.py
backend/ai_capability.py
backend/ai_decision_ledger.py
backend/ai_guardrails.py
backend/ai_learning.py
backend/ai_model_router.py
backend/ai_optimizer.py
backend/ai_params.py
backend/ai_rule_lab.py
backend/alert_engine.py
backend/api_contracts.py
backend/auth.py
backend/auto_rule_manager.py
backend/autopilot_api.py
backend/bot_exits.py
backend/bot_health.py
backend/bot_runner.py
backend/config.py
backend/data_handler.py
backend/database.py
backend/db/__init__.py
backend/db/core.py
backend/db/direct_candidates.py
backend/db/positions.py
backend/db/retention.py
backend/db/rules.py
backend/db/trades.py
backend/db/validation.py
backend/decision_item_factory.py
backend/diagnostics_api.py
backend/diagnostics_scheduler.py
backend/diagnostics_service.py
backend/direct_ai_trader.py
backend/event_logger.py
backend/events.py
backend/execution_brain.py
backend/health.py
backend/ibkr_client.py
backend/install_smart_rules.py
backend/log_config.py
backend/main.py
backend/manual_intervention.py
backend/manual_order_validation.py
backend/market_data.py
backend/market_heartbeat.py
backend/middleware.py
backend/models.py
backend/notification_service.py
backend/order_executor.py
backend/portfolio_allocator.py
backend/position_tracker.py
backend/risk_api.py
backend/risk_config.py
backend/risk_manager.py
backend/routers/admin_routes.py
backend/routers/auth.py
backend/routers/bot_routes.py
backend/routers/market_routes.py
backend/routers/orders.py
backend/routers/positions.py
backend/routers/rules_routes.py
backend/routers/simulation_routes.py
backend/routers/status.py
backend/rule_builder_api.py
backend/rule_engine.py
backend/rule_validation.py
backend/runtime_lock.py
backend/runtime_state.py
backend/safety_kernel.py
backend/services/order_lifecycle.py
backend/services/order_recovery.py
backend/services/safety_gate.py
backend/session_api.py
backend/settings.py
backend/simulation.py
backend/startup.py
```

<!-- d14-current-end -->

## Why these files are in scope

- Startup/lifecycle/path owners: runtime ownership, readiness, process-lifetime
  tasks/callbacks, broker subscriptions, or current writable path selection.
- Broker/order/risk/safety/authority: placement, cancellation, fills, positions,
  reconciliation, limits, kill switches, executable rules, AI authority,
  interventions, or durable decision truth.
- Database/durability: schema initialization, canonical trading records,
  destructive maintenance, connection ownership, and direct DB-path consumers.
- Control entrypoints: authentication, authority, risk, rule, order, broker,
  maintenance, lifecycle, or executable-rule actions.

Conservative inclusions are intentional. Diagnostics, alerts, heartbeat, market
data, simulation, and AI loops are process-lifetime C8 resources. Health and
position surfaces can misstate safety readiness. `models.py` and
`api_contracts.py` contain live trading contracts. `data_handler.py`,
`event_logger.py`, and `settings.py` participate in the C2 path migration.

## Future Phase C modules

The implementation or any renamed equivalent is automatically in scope:

```text
backend/app_paths.py
backend/db/backup.py
backend/db/integrity.py
backend/db/maintenance.py
backend/db/migration_manifest.py
backend/db/migration_runner.py
backend/db/migrations/**/*.py
backend/db/restore.py
backend/db/schema_classifier.py
backend/process_terminator.py
backend/services/broker_adapter.py
backend/services/operation_gate.py
backend/services/order_intents.py
backend/services/reconciliation.py
backend/services/runtime_lifecycle.py
backend/services/shutdown.py
backend/services/task_registry.py
```

Any maintenance, backup, restore, runtime, order, position, or admin router/CLI
is also included.

## Filename-independent inclusion triggers

The checked-in C0 manifest is unioned with a source scan. Any module containing
one of these capabilities enters D14 regardless of its name:

- raw broker order placement or cancellation;
- runtime-lock acquisition or release;
- lifecycle, authority, or risk-state mutation;
- schema DDL, `BEGIN IMMEDIATE`, `application_id`, `user_version`, `VACUUM`, or
  WAL checkpoint operation;
- SQLite backup or DB-file publication/replacement;
- writable `AppPaths` root creation;
- production process termination such as `os._exit`.

## Explicit current exclusions

Derived analytics, backtests, screeners, indicators, stock-profile, Yahoo,
context/replay evaluation, alert CRUD, ordinary UI settings routes, WebSocket
presentation, and these persistence modules remain outside D14 while they stay
non-authoritative:

```text
backend/db/alerts.py
backend/db/backtests.py
backend/db/screener.py
```

An excluded file enters D14 immediately if it gains an inclusion trigger. The
action layer must also reject degraded or missing upstream data provenance; that
fail-closed rule is what permits derived producers to remain outside the critical
exception inventory.

## Current census

The read-only line census used these patterns:

```regex
^\s*except\s+(Exception|BaseException)(\s+as\s+\w+)?\s*:
^\s*except\s*:\s*$
^\s*pass\s*$
```

Result on C1A evidence source
`1744bdb94e0ff8fcf55ffa427e563444af16f002`:

```text
77 inventory files
46 files with at least one match
188 broad or bare catches
25 standalone pass statements
213 combined sites
```

Each matched site needs a stable classification ID:

1. containment with telemetry/degraded state;
2. typed fail-closed boundary;
3. best-effort observability;
4. prohibited trading/persistence swallow.

## C0/C10 acceptance

C0 materializes the accepted inventory as a sorted machine-readable manifest,
verifies that every named path exists, and unions it with capability-trigger
discovery. This does not authorize C10. From an authorized C1 onward, CI will
block a new or moved critical file/site without classification. C10 closes only
after every current matched site is classified and prohibited sites are removed
or redesigned.

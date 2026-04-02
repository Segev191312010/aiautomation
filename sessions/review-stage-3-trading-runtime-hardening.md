AI AUTOPILOT — STAGE 3 TRADING RUNTIME HARDENING
================================================
DATE: 2026-03-27 (updated 2026-03-31 with audit findings)
STATUS: READY AFTER STAGE 1 AND STAGE 2 FOUNDATIONS

AUDIT UPDATE (2026-03-31)
-------------------------
Full codebase audit found 18 issues in this stage. 14 were FIXED same-day, 4 remain OPEN.
See: `sessions/audit-findings-2026-03-31.md` (section: STAGE 3)

FIXED: skip_safety bypass, MKT→LMT race, fire-and-forget tasks, partial fills,
       fill watcher timeout, dedup window, stale event callbacks, fail-open risk checks,
       unrealized P&L in circuit breaker, price fallback, emergency position close,
       reconnect reconciliation, same-cycle churn, DB synchronous=FULL

OPEN (do in Phase 3.1):
  - F3-15: Stale position re-read before exit (bot_runner.py:1224)
  - F3-16: Database transaction isolation (database.py:24)
  - F3-17: Exit retry force-close when cap reached (bot_runner.py:1356)
  - F3-18: Batch pre-load sectors on startup (risk_manager.py)
OWNER: TRADING RUNTIME TEAM
GOAL: Make live trade lifecycle behavior explicit, centralized, safe, and recoverable.

PURPOSE
-------
Stage 3 exists because runtime safety depends on deterministic lifecycle behavior, not only correct models and APIs.

Current pressure points:
- order placement logic is spread across multiple modules
- entry/exit and manual/AI/rule paths are partially forked
- reconciliation and pending-exit behavior are fragile under retries and restarts
- multi-user scoping must be rechecked wherever mutations happen by ID

STAGE 3 IS NOT:
---------------
- New order types
- New trading strategies
- UI-first work

STAGE 3 IS:
-----------
- Runtime determinism
- Shared safety gates
- Better restart/reconnect behavior
- Lower operational risk

GLOBAL EXIT GATE
----------------
Stage 3 is complete only when:
[ ] trade state transitions are centralized enough to audit
[ ] manual, rule-driven, and AI-driven orders pass through the same safety concepts
[ ] exit ownership and pending-exit behavior are explicit
[ ] multi-user mutation paths honor user scope consistently
[ ] restart and reconnect behavior is documented and idempotent

================================================================
PHASE 3.1 — ORDER LIFECYCLE SERVICE EXTRACTION
================================================================
SCOPE
- Pull shared lifecycle logic out of runtime modules

FILES
- [ ] backend/order_executor.py
- [ ] backend/bot_runner.py
- [ ] backend/direct_ai_trader.py
- [ ] backend/services/order_lifecycle.py (new, recommended)
- [ ] backend/services/trade_reconciliation.py (new, recommended)

TASKS
1) Map current state transitions
   [ ] Identify submit -> pending -> filled -> cancelled -> error -> reconciled flows.
   [ ] Record which module currently owns which transition.

2) Extract shared lifecycle helpers
   [ ] Move common state transition rules into a service module.
   [ ] Avoid keeping three independent partial implementations.

3) Preserve semantics
   [ ] Do not change trade truth ownership from Stage 9.
   [ ] Keep `update_trade_status()` and `finalize_trade_outcome()` responsibilities clear.

DELIVERABLE
- [ ] Order lifecycle behavior is shared instead of reimplemented in multiple places.

================================================================
PHASE 3.2 — CENTRALIZE ENTRY / EXIT SAFETY GATING
================================================================
SCOPE
- Ensure all order-entry paths pass through the same risk and safety concepts

FILES
- [ ] backend/order_executor.py
- [ ] backend/bot_runner.py
- [ ] backend/direct_ai_trader.py
- [ ] backend/ai_guardrails.py
- [ ] backend/config.py
- [ ] backend/services/safety_gate.py (new, recommended)

TASKS
1) Inventory current gates
   [ ] Daily loss lock
   [ ] kill switch
   [ ] trading hours / market-open rules
   [ ] shorting policy
   [ ] risk budgets
   [ ] autopilot authority checks

2) Remove logic forks
   [ ] Ensure manual orders, rule orders, and AI orders do not bypass different safety logic accidentally.
   [ ] Create one ordered safety sequence where practical.

3) Surface failure reasons
   [ ] Ensure blocked trades/orders return explicit reasons.
   [ ] Avoid silent no-op behavior.

DELIVERABLE
- [ ] Entry/exit safety policy is coherent across runtime paths.

================================================================
PHASE 3.3 — EXIT MANAGEMENT HARDENING
================================================================
SCOPE
- Make exit placement, retry, timeout, and reconciliation more deterministic

FILES
- [ ] backend/bot_runner.py
- [ ] backend/order_executor.py
- [ ] backend/models.py
- [ ] backend/database.py or relevant repositories
- [ ] backend/services/exit_manager.py (new, recommended)

TASKS
1) Pending exit ownership
   [ ] Make it explicit which module owns a pending exit.
   [ ] Prevent duplicate exit placement for the same position.

2) Timeout/retry/cancel rules
   [ ] Define retry thresholds.
   [ ] Define when a pending exit is cancelled and re-placed.
   [ ] Define how current ownership is preserved across retries.

3) Reconciliation safety
   [ ] Ensure a filled exit cannot be finalized twice.
   [ ] Ensure the same position cannot be closed by two code paths.

DELIVERABLE
- [ ] Exit management is deterministic under normal and delayed fill conditions.

================================================================
PHASE 3.4 — MULTI-USER SCOPING AUDIT
================================================================
SCOPE
- Re-audit all user-scoped mutation/query paths in runtime-critical domains

FILES
- [ ] backend/database.py / repositories
- [ ] backend/order_executor.py
- [ ] backend/bot_runner.py
- [ ] backend/autopilot_api.py
- [ ] backend/ai_rule_lab.py

TASKS
1) ID-based mutation audit
   [ ] Review updates by `id` for rules, trades, positions, alerts, validation rows, and evaluation rows.
   [ ] Ensure `user_id` is consistently enforced where required.

2) Helper audit
   [ ] Review repository helpers that load or mutate by ID only.
   [ ] Confirm they do not accidentally cross user scope.

3) Regression tests
   [ ] Add tests where missing user scoping would be dangerous.

DELIVERABLE
- [ ] Runtime-critical data mutations do not rely on weak scoping assumptions.

================================================================
PHASE 3.5 — RESTART AND RECONNECT RECOVERY
================================================================
SCOPE
- Make startup and reconnect behavior explicit and idempotent

FILES
- [ ] backend/main.py
- [ ] backend/startup.py
- [ ] backend/order_executor.py
- [ ] backend/bot_runner.py
- [ ] backend/services/runtime_recovery.py (new, recommended)

TASKS
1) Startup recovery inventory
   [ ] Pending orders
   [ ] open positions
   [ ] pending exits
   [ ] reconnect loops
   [ ] websocket runtime state
   [ ] autopilot mode sync

2) Idempotent recovery rules
   [ ] Define what may safely run twice.
   [ ] Define what must be guarded against duplication.

3) Logging and observability
   [ ] Record recovery actions clearly enough to diagnose restarts.
   [ ] Do not silently “best effort” critical recovery work.

DELIVERABLE
- [ ] Runtime recovery is explicit and repeat-safe.

================================================================
PHASE 3.6 — RUNTIME VALIDATION GATE
================================================================
SCOPE
- Lock runtime hardening with focused tests and smoke checks

FILES
- [ ] backend/tests/test_exit_lifecycle.py
- [ ] backend/tests/test_trade_truth.py
- [ ] backend/tests/test_trade_truth_e2e.py
- [ ] backend/tests/test_direct_ai_trader.py
- [ ] backend/tests/test_bot_runner*.py
- [ ] backend/tests/test_order_executor*.py

TASKS
1) Lifecycle tests
   [ ] submit/pending/filled transitions
   [ ] fill persistence
   [ ] canonical finalization after exit

2) Recovery tests
   [ ] startup/restart recovery flows
   [ ] duplicate-recovery safety

3) Scoping tests
   [ ] user-scoped mutation coverage for critical helpers

VALIDATION COMMANDS
- [ ] `cd backend && python -m pytest tests -v`

STAGE 3 FINAL CHECKLIST
-----------------------
[ ] Phase 3.1 complete
[ ] Phase 3.2 complete
[ ] Phase 3.3 complete
[ ] Phase 3.4 complete
[ ] Phase 3.5 complete
[ ] Phase 3.6 complete

Once all boxes are checked, Stage 3 is DONE and the trading runtime becomes far easier to trust under stress, delay, and restart conditions.

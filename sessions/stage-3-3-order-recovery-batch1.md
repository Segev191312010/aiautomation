STAGE 3.3 — ORDER RECOVERY, RECONCILIATION, AND POST-SUBMIT TRUTH
===============================================================
DATE: 2026-03-29
STATUS: BATCH 1 IN PROGRESS
OWNER: COMMANDER LIAL + CORE TEAM
GOAL: Unify post-submit order handling so pending, filled, cancelled, timed-out, and retry flows stop drifting across runtime paths.

PURPOSE
-------
Stage 3.3 starts where Stage 3.2 stopped.

Stage 3.2 unified pre-submit safety.
Stage 3.3 unifies post-submit behavior:
- broker status normalization
- pending order reconciliation
- exit retry state
- timeout / cancel handling
- startup recovery seams

BATCH 1 SCOPE
-------------
Batch 1 extracts the shared recovery primitives only:
- broker/app status normalization
- shared filled/cancelled/error reconciliation helper
- shared pending-exit decision helper

FILES
-----
[ ] backend/services/order_recovery.py (new)
[ ] backend/services/__init__.py
[ ] backend/order_executor.py
[ ] backend/bot_runner.py
[ ] backend/tests/test_order_recovery.py (new)
[ ] backend/tests/test_exit_lifecycle.py

BATCH 1 TASKS
-------------
1) Create backend/services/order_recovery.py
   [ ] normalize_trade_status(status)
   [ ] is_pending_status(status)
   [ ] is_filled_status(status)
   [ ] is_cancelled_status(status)
   [ ] is_error_status(status)
   [ ] reconcile_trade_status_update(trade_rec, status, fill_price, fill_callbacks)
   [ ] evaluate_pending_exit_resolution(position, trade, now, timeout_seconds)
   [ ] mark_exit_retry_state(position, reason, now)
   [ ] mark_exit_pending_submitted(position, order_id, now)
   [ ] clear_pending_exit(position)

2) Rewire backend/order_executor.py
   [ ] _watch_fill() uses shared status normalization/reconciliation
   [ ] reconcile_pending_orders() uses shared status helper
   [ ] _handle_fill() delegates to shared reconciliation helper
   [ ] no duplicate filled/cancelled logic remains inline

3) Rewire backend/bot_runner.py
   [ ] _reconcile_pending_exit() uses shared pending-exit decision helper
   [ ] _place_exit_order() uses shared pending-submitted/retry-state helpers
   [ ] position mutation rules stay the same, but state transitions become centralized

4) Tests
   [ ] add focused order_recovery unit tests
   [ ] extend exit lifecycle tests for shared helper paths

DONE CONDITION
--------------
[ ] One module defines trade-status meaning across runtime paths
[ ] Entry reconciliation and exit reconciliation stop inventing their own status rules
[ ] Pending exit retry/timeout rules are explicit and tested
[ ] Focused tests pass
[ ] Full backend suite stays green

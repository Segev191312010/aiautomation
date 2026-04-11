# ADR 0001: Trade Truth Ownership

**Status:** Accepted
**Date:** 2026-04-11 (drafted during Phase A prep; codifies Stage 9 work already in production)
**Supersedes:** N/A

## Context

A trading platform has multiple possible sources of truth for "what trades happened and what state are they in":

- **The broker (IBKR)** — the ultimate record. Executions, fills, current positions.
- **The in-memory state** — what the bot process thinks is happening right now.
- **The persistent database** — the `trades`, `open_positions`, `ai_decision_runs` tables.
- **The decision ledger** — the AI decision trail (what was *proposed* vs what was *applied*).

Without a chosen source of truth, code paths drift: two modules can disagree on whether a trade is FILLED or PENDING, duplicate exits can fire, and the dashboard displays stale data that the operator treats as real. Early versions of this platform had exactly this problem — the operator could see a trade in the dashboard that the bot thought was still open, while IBKR had already filled the exit.

Stage 9 of the audit roadmap (2026-03) introduced the `ai_decision_ledger` and the canonical finalization pattern (`finalize_trade_outcome`). This ADR documents what that work decided — it is not new policy, it is written-down policy.

## Decision

**The database is the platform's trade truth.** Specifically:

1. **IBKR is authoritative for executions and positions at the broker.** When the bot connects (or reconnects) and queries IBKR, the returned state is what actually happened at the broker.
2. **The database is authoritative for *our interpretation* of IBKR state.** Once a fill event is recorded, finalized, and written to `trades` / `open_positions` / `ai_decision_items`, downstream code reads from the database, not from IBKR. The DB row is the truth the bot executes against.
3. **In-memory state is a cache of the database, never the other way around.** `bot_runner` keeps per-cycle scratch state (`_last_run`, `_running`, `_cycle_count_today`) but must not hold trade state that isn't also written to SQLite.
4. **The decision ledger records the AI's intent, separately from execution.** `ai_decision_runs` and `ai_decision_items` capture what the AI proposed; the ledger's `applied_json` column records what was actually accepted; `realized_trade_id` links a decision back to a `trades` row when execution succeeds. A decision item that is `pending` in the ledger is not proof of a real fill.

### Transition rules

- A trade becomes truth at the moment `finalize_trade_outcome()` writes it inside a `transaction()` context manager with `BEGIN IMMEDIATE`. Anything before that point (including a returned `order_id` from `place_order`) is tentative.
- `reconcile_pending_orders` at startup (`order_executor.py:370-435`) is the canonical bridge from IBKR reality back to our DB truth when the process restarts. It subscribes to `orderStatusEvent` BEFORE iterating pending trades so no fill event is lost to the race.
- Exit atomicity (P1 fix, commit `9717bd0`) requires that `save_trade` + `delete_open_position` + `save_open_position` on the same exit happen inside one `transaction()`. Partial commits leave the bot believing a position is still open while the trade was already recorded.
- Direct AI candidates persist in a separate `direct_candidates` table (P2-2, commit `8ebab67`) with queued → draining → applied|failed|expired status transitions. The queue is part of the decision layer, not the trade layer — a queued candidate is not a trade.

### Broker reconciliation

- Startup does NOT cross-check `open_positions` against IBKR broker positions automatically. The DB is trusted. This is a deliberate simplification for a cash account with one bot process.
- If the bot ever holds a broker position that isn't in the DB (e.g. a fill arrives during the restart window and `on_fill` fires after the event loop shuts down), the paper-soak runbook catches it manually via a pre- and post-restart position count cross-check.
- Future work (Phase B): add a soft cross-check in `_run_cycle` that warns if the broker position set diverges from the DB set by more than N symbols.

## Consequences

### Positive
- Every trade has ONE row readers can trust. No "where did I see this last" hunting.
- Database transactions (`transaction()` context manager, WAL mode, `PRAGMA foreign_keys=ON`) give us atomicity for multi-row updates.
- The decision ledger provides a complete audit trail from AI proposal to realized trade, which is load-bearing for post-mortem and for compliance evidence.
- `reconcile_pending_orders` is a single predictable recovery path, not a scatter of ad-hoc reconnect handlers.

### Negative
- The DB can drift from IBKR if the bot process dies at exactly the wrong moment (fill arrives while `main.py:244` is running the shutdown sequence). The paper-mode soak mid-session restart test exists specifically to catch this class of failure.
- The "DB is truth" rule requires discipline: any new code path that reads IBKR directly for trade decisions violates the invariant. Code review must enforce.
- Per-user scoping (`user_id` columns) is not yet multi-tenant safe — several routes hardcode `user_id='demo'` even though the schema allows per-user separation. Single-user localhost deployment hides this. See `sessions/phase-b-f7-01-auth-gap-analysis.md` for the full list.

### Tradeoffs considered and rejected

**Alternative A: IBKR is truth, DB is a cache.** Rejected because IBKR queries are slow, rate-limited, and require a live connection. A cash account might need to evaluate rules while the broker is briefly unreachable; forcing every read to hit IBKR would either fail-closed (the bot stops trading) or fail-open (the bot trades on assumed state). Neither is acceptable.

**Alternative B: In-memory state is truth, DB is a log.** Rejected because the bot must survive process restarts. Any in-memory-only design would lose trade state every time uvicorn reloads or the host reboots. The Phase 1 P2-2 work (persisting the direct candidate queue to SQLite) was a direct correction of this design mistake in the prior implementation.

**Alternative C: Per-trade source of truth (some trades owned by DB, some by IBKR, some by ledger).** Rejected because ambiguity is the original bug this ADR fixes. Having one canonical store per domain is non-negotiable.

**Alternative D: A dedicated message bus (Kafka, NATS, Redis streams) as the truth layer.** Rejected as over-engineering for a single-user platform. The SQLite WAL log is a serialized, durable, crash-safe event stream at zero ops cost. If the platform ever moves to multiple bot processes or a distributed worker pool, revisit.

## Compliance notes

- `db/core.py` sets `PRAGMA synchronous=FULL` on every runtime connection — trades involve real money, we do not lose writes on crash.
- `PRAGMA journal_mode=WAL` enables concurrent readers during writes (dashboard queries do not block the bot cycle).
- `PRAGMA foreign_keys=ON` enforces cascade delete on `ai_decision_runs → ai_decision_items` (verified by test `test_hb1_05_decision_run_delete_cascades_to_items`).
- HB1-06 regression test (`test_direct_ai_trader.py::test_hb1_06_failed_live_trade_does_not_mark_decision_item_applied`) is the guard for "decision ledger records intent, not outcome" — an item must stay `pending` until execution actually succeeds.

## Links

- `backend/db/core.py` — connection factory, PRAGMA config, schema
- `backend/services/order_lifecycle.py` — canonical lifecycle helpers extracted in Stage 3
- `backend/order_executor.py:370-435` — `reconcile_pending_orders`
- `backend/ai_decision_ledger.py` — decision run and item CRUD, `mark_decision_item_applied`
- `backend/db/direct_candidates.py` — persisted direct AI candidate queue (P2-2)
- `sessions/review-stage-3-trading-runtime-hardening.md` — Stage 3 runtime work this ADR captures
- HB1 regression tests: `backend/tests/test_execution_brain.py`, `backend/tests/test_ai_decision_ledger.py`, `backend/tests/test_direct_ai_trader.py`

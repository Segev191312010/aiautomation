# Stage 9B Phase 1 — SF1b: Broker Protection + Reconciliation (ADR 0007)

**Branch base:** `stage-9b-containment` in worktree `/Users/salomon/aiautomation-9b`
**Depends on:** SF1a (durable execution lease + fencing token) — merged in commits through `21aa8f3` plus follow-up fixes.
**Scope:** Harden broker-facing surfaces so stale/fenced processes cannot mutate orders, and reconcile local DB trade state with IBKR after reconnect/crash.

## Why this matters

SF1a proved a single execution owner across hosts. SF1b closes the residual gaps:

1. **Broker mutation consolidation** — today `ibkr.ib.placeOrder`/`cancelOrder` are called from `order_executor.py` and `safety_kernel.py`. A stale-fenced process could still reach TWS if it called `ibkr.ib` directly (the lease check lives in the caller, not the client).
2. **Reconciliation without fencing** — `reconcile_pending_orders()` runs on startup and reconnect, touching broker state and updating DB; it should also prove ownership.
3. **Orphan/unknown outcomes** — a crash after broker acceptance but before DB persistence leaves trades in `PENDING` or `ERROR`; ADR 0007 requires a bounded reconciliation and operator-visible UNKNOWN state.
4. **Manual routes** — `/api/ibkr/connect` is already fenced; other manual routes such as positions summary read broker state and should not be confused by stale processes.

## Invariants

1. `STAGE_9A_LIVE_RELEASE_APPROVED` remains `False`.
2. No new code path may call `ibkr.ib.placeOrder`/`cancelOrder` without a fencing check. The authoritative gate should move toward `ibkr_client.py` where practical.
3. All reconciliation loops must prove lease ownership before querying or mutating broker orders.
4. A lost lease must degrade safely: stop placing new orders, do not cancel/replace existing broker orders unless ownership is regained, and surface the state in `/api/health`.
5. Unknown outcomes (broker accepted order but local DB lost) must be recorded explicitly, not silently retried.
6. No push to remote until Phase 1 quality gates pass.

## Files to touch

- `backend/ibkr_client.py` — add `place_order_guarded` / `cancel_order_guarded` wrappers that require a valid fencing token before delegating to `ibkr.ib`.
- `backend/order_executor.py` — route all `placeOrder`/`cancelOrder` calls through the guarded wrappers; add fencing to `reconcile_pending_orders` and `reap_orphan_pending_trades` entry points; keep `cancel_order` and `place_order` behavior unchanged for callers.
- `backend/safety_kernel.py` — route emergency-close `placeOrder` calls through guarded wrappers.
- `backend/routers/status.py` — already fenced for `/api/ibkr/connect`; verify `/api/ibkr/disconnect` semantics (safe to allow from any process because it drops local socket only).
- `backend/routers/positions.py` — reads open trades for bracket summaries; add a note that reads do not require fencing but stale-process reads are acceptable because they do not mutate state.
- `backend/db/execution_lease.py` — consider adding `get_current_lease()` to health checks (already used in `/api/health`).
- `backend/tests/test_order_executor_fencing.py` — extend with tests for guarded wrappers and reconciler fencing.
- `backend/tests/test_broker_protection.py` (NEW) — tests for the guarded `ibkr_client` mutation wrappers, including stale-token rejection and unknown-outcome recording.
- `sessions/stage-9b-phase1-sf1b-broker-protection-reconciliation.md` — this file.

## Implementation order

1. Design guarded wrappers in `ibkr_client.py`.
   - `place_order_guarded(contract, order, *, fencing_token: str | None)` → `IBTrade | None`
   - `cancel_order_guarded(ib_order, *, fencing_token: str | None)` -> `bool`
   - Internally `await validate_fencing_token(fencing_token)`; on failure log CRITICAL and return `None`/`False`.
   - On success delegate to `ibkr.ib.placeOrder`/`cancelOrder`.
2. Replace direct `ibkr.ib.placeOrder` calls in `order_executor.py` and `safety_kernel.py` with the guarded wrappers.
3. Add fencing check at the top of `reconcile_pending_orders()` and `_convert_mkt_orders_to_limit()` (the latter already has per-iteration checks; add an early exit if lease lost).
4. Add `reap_orphan_pending_trades` early fencing check (it only mutates local DB, but running it without lease is unnecessary and could race with the real owner).
5. Add tests for wrappers and reconciler fencing.
6. Run quality gates.
7. Commit locally on `stage-9b-containment`.

## Evidence checklist

- [ ] `ibkr_client.py` exposes guarded `place_order_guarded` / `cancel_order_guarded` wrappers.
- [ ] Direct `ibkr.ib.placeOrder`/`cancelOrder` calls outside `ibkr_client.py` are eliminated (or documented as internal-only and impossible to reach from fenced callers).
- [ ] Wrapper rejects stale/missing token without touching TWS.
- [ ] `reconcile_pending_orders` and `_convert_mkt_orders_to_limit` fail closed when lease is lost.
- [ ] Unknown outcome path exists: broker-accepted order with no local DB record records an explicit UNKNOWN row (or marks existing PENDING as UNKNOWN after bounded timeout).
- [ ] Backend pytest passes.
- [ ] Frontend typecheck/build/vitest passes.
- [ ] No Stage 9A fence weakened.
- [ ] Commit is local-only.

## Open questions

- Should the guarded wrappers also record UNKNOWN outcomes when `placeOrder` raises after acceptance? This is the R08 residual risk from the main lifespan comment.
- Should `ibkr_client.py` own a `record_unknown_outcome` helper, or keep that in `order_executor.py`?
- Do we want an operator-visible metric/alert when a fenced mutation is rejected?

## Safety note

SF1b still does not approve LIVE trading. It is the second half of Stage 9B Phase 1. After SF1b, the remaining pre-live work includes: ADR 0008 identity/session hardening, residual risk register, fault matrix, and operator runbook sign-off.

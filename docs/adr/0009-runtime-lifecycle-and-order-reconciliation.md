# ADR 0009: Runtime Lifecycle and Order Reconciliation

Status: Accepted - owner approved 2026-07-14

Implementation authority: design policy accepted; C1-C12 implementation is not
authorized.

Date: 2026-07-12

Depends on: ADR 0001 (trade truth), ADR 0005 (authority semantics), ADR 0008
(durable schema and recovery)

## Context

The current backend can become request-ready before startup order reconciliation
finishes. Its order flow writes a pending database record, submits to IBKR, and
only then stores broker identity. A crash after broker acceptance can therefore
leave a real order that recovery does not recognize. Duplicate protections are
partly in memory and disappear on restart.

Reconciliation currently examines a bounded recent DB set and broker open
trades. It does not form one idempotent comparison across durable intent,
completed orders, executions, `orderRef`, `permId`, broker positions, and DB
positions. Fill/position and exit/pending-marker updates also cross unowned task
or crash boundaries.

Shutdown is sequential rather than supervised. An exception can skip later
cleanup, several background tasks and callbacks lack one owner, and there is no
quiescing or unclean-shutdown state. Runtime ownership can consequently end
without proving reconciliation, WAL checkpoint, and log flush.

## Decision

### Explicit persisted lifecycle

Use one lifecycle controller with these externally visible states and separate
machine-readable reason codes such as `RECONCILIATION_REQUIRED`:

```text
STARTING -> RECONCILING -> READY
                         -> DEGRADED
READY -> QUIESCING -> STOPPED_CLEAN
startup/shutdown failure or hard death -> UNCLEAN
```

After pure, non-mutating absolute `AppPaths` resolution, secure lock-parent
bootstrap as the sole pre-lock mutation, and immediate runtime-lock acquisition,
but before database mutation or other stateful resources, atomically publish
and durably flush an external
unclean marker under the runtime directory. It is authoritative for crashes that
occur before the DB is classified/migrated. The database keeps lifecycle and
reconciliation history after it is safe to open. Startup following an external
marker or unclean DB history must reconcile before `READY`.

An `OperationGate` issues tracked leases and atomically authorizes operations
against lifecycle state:

| Operation | READY | RECONCILING/DEGRADED | QUIESCING |
|---|---|---|---|
| New automated/manual entry | allow | block | block |
| Ordinary maintenance mutation | only when its safety guard passes | block | block |
| Cancel positively owned intent | allow | allow only with current broker ownership proof | allow only as an already-authorized shutdown action |
| Emergency authority stop | allow | allow | allow while process is alive |
| Verified manual exit | allow | allow only under the unambiguous-position rule | block unless it was already authorized before quiescing |
| Read/status/diagnostics | allow | allow | allow |

Broker unavailability keeps the API read-only/degraded with a reconnect path;
it never reports ready or permits new entries. If ownership/position verification
is unavailable, cancel/exit fails closed and creates or retains intervention.

### Durable order intent before submission

Add an `order_intents` model through the migration system. Before any broker
call, commit a stable UUID intent and use it as IBKR `orderRef`. Persist enough
identity and state to reconcile without guessing:

- intent UUID and source;
- normalized request and risk-decision reference;
- privacy-safe account identity hash and client ID;
- broker order ID, `permId`, and execution identifiers when known;
- lifecycle state and monotonic transition timestamps;
- linked trade/position identifiers and intervention state.

Use unique constraints for the intent UUID,
`(account_hash, client_id, broker_order_id)`, account-scoped `permId`, and
`(account_hash, execId)`, plus idempotent transitions. A client idempotency key
returns the original result only for an exact request-payload replay; conflicting
reuse returns HTTP 409. Suggested intent states are
`CREATED`, `SUBMITTING`, `ACKNOWLEDGED`, `PARTIAL`, `FILLED`,
`CANCEL_REQUESTED`, `CANCELLED`, `REJECTED`, `ABORTED`, and `UNKNOWN`.
`CREATED` may become `ABORTED` only when submit is proven not entered. Ambiguity
after `SUBMITTING` becomes `UNKNOWN`, persists intervention, blocks entry, and is
never automatically resubmitted. Repeating submit,
callback delivery, reconnect, or reconciliation for an existing intent must not
create a second broker order, logical trade, fill, or position.

The durable transition to `SUBMITTING` commits immediately before raw adapter
entry; the adapter cannot be invoked if that commit fails. A crash before that
transition can prove submit was not entered and may abort `CREATED`. A crash
after the transition but before adapter entry, a dispatched request with unknown
acceptance, or a submit timeout is conservatively `UNKNOWN`, never `ABORTED` or
automatically retried.

### Broker-authoritative reconciliation

Before readiness and after reconnect, compare as one reconciliation set:

- durable DB intents and their trades/positions;
- broker open and completed orders;
- executions and fills;
- stable `orderRef`, `permId`, client, and account identity;
- broker positions and DB open positions.

Broker facts remain authoritative for broker executions and positions; the DB
remains the canonical application interpretation under ADR 0001. Unknown or
contradictory activity creates a durable intervention and blocks new entries.
The runtime never auto-imports, cancels, resubmits, or claims ownership of an
ambiguous or external TWS order.

Reconciliation is a readiness gate, not a fire-and-forget task. It compares the
exact unresolved set; bounded or sampled scans are prohibited. Event handlers
are registered once before the first snapshot, buffer and deduplicate events,
and detach deterministically. The controller reads a complete broker/DB snapshot,
drains buffered events, and repeats snapshot-plus-drain until two consecutive
canonical digests match. A broker watermark/sequence is included when available;
without one, failure to converge within a timeout remains `DEGRADED`. Tests inject
events before subscription, between every snapshot source read, between snapshot
and drain, and during the second stability snapshot.

Only the real broker adapter may invoke raw broker place/cancel methods. Every
manual, automated, safety-kernel, and emergency liquidation path either commits
durable intent first or disables automatic liquidation while preserving the
emergency authority-stop control.

### Fill, position, exit, and manual-exit safety

Fill application is idempotent by broker execution identity. The durable intent,
trade fill, and application position interpretation transition through one
transactional service or a recoverable state machine with an explicit resume
point.

An exit intent and pending marker are durable before broker submission. Failed
or timed-out cancellation does not clear that marker until broker cancellation
is confirmed or reconciliation resolves the order. Market conversion may touch
only an order owned by the referenced TradeBot intent.

A manual exit requires an explicit DB position ID or exactly one unambiguous
matching open position, a freshly verified broker quantity, and a requested
quantity within both broker and linked DB quantities. Multiple, missing, or
mismatched DB positions block the action and create an intervention; the runtime
never silently chooses FIFO, aggregate, or proportional allocation. Its intent
links to that position, and reconciliation updates the DB interpretation after
broker status/fill changes.

### Owned tasks and failure-isolated shutdown

Use one task/callback registry that records names, ownership, failure, and stop
behavior. Startup acquisitions register cleanup immediately. A failed startup
unwinds every acquired resource.

Graceful shutdown enters `QUIESCING`, follows the operation matrix, and uses
independent reserved timeouts so one hung stage cannot consume all later stages.
The accepted 30-second backend budget is 5/10/5/5/5 seconds across the grouped
obligations below. Supported Compose/Uvicorn termination grace is configured and
tested at no less than 45 seconds in Phase C; Phase D owns packaged enforcement.
The ordered obligations are:

- 5 seconds: enter quiescing and stop/drain producers;
- 10 seconds: reconcile and preserve/resolve working intents;
- 5 seconds: detach callbacks and disconnect the broker;
- 5 seconds: persist DB result and checkpoint;
- 5 seconds: flush logs and durably clear the external marker when clean.

The controller produces a shutdown certificate with separate `clean_shutdown`
and `safe_to_release` results. Runtime-lock release follows those reserved
groups, requires `safe_to_release`, and must not perform a DB/log write. An outer
`finally` cannot release it unconditionally. A DB `STOPPED_CLEAN` row is
provisional until its required checkpoint, log flush, and durable external-marker
clear all succeed; a surviving marker always overrides that row at next startup
and forces reconciliation.

1. quiesce entry and maintenance mutations;
2. stop schedulers/producers and drain or cancel owned tasks;
3. reconcile outstanding intents, fills, exits, and positions;
4. preserve acknowledged working broker orders unless an operator explicitly
   requested an eligible cancellation;
5. detach callbacks and disconnect the broker;
6. persist the DB shutdown result (`STOPPED_CLEAN` only if every prior critical
   obligation succeeded; otherwise `UNCLEAN`);
7. run the required blocking WAL checkpoint and fail clean certification on
   busy/error;
8. flush/close application and event logs so no later stage logs through them;
9. durably clear the external marker only after clean DB/checkpoint/log results;
10. release the runtime lock last without further DB/log writes.

When a stage or critical obligation fails, retain the external marker, record
`UNCLEAN` when the DB remains writable, still attempt every later reserved
stage, and surface the exact failure at next startup.

If a mutation-capable task, broker callback, or adapter remains live after its
reserved cancellation budget, attempt every later safe stage, retain the marker,
flush diagnostics, and invoke an injected `ProcessTerminator` (`os._exit` in
production). The OS then releases the lock only with process death. If every
mutation-capable task, callback, adapter, request/operation lease, and DB handle
is positively confirmed stopped but clean certification still fails,
`safe_to_release` may be true while `clean_shutdown` is false; the final
permitted voluntary fallback is an explicitly recorded silent release with the
marker retained. No code may log or write the DB after final flush/release.

### Verification boundary

Phase C uses a dependency-injected deterministic fake broker in a separate
persistent process (`AF_PIPE` on Windows, `AF_UNIX` on POSIX) whose accepted
orders survive backend process death. Before application import, the harness
blocks `AF_INET`, `AF_INET6`, and DNS, proves that deny with a negative-control
self-test, and asserts the fake identity. It runs the exact broker-backed
services with `SIM_MODE=false`, paper authority, AI authority off, and synthetic
identity. `test_order_crash_boundaries.py` covers the 17 stable case families
`C9-K01` through `C9-K17`, with stable subcase suffixes for parameterized
barriers. Each restart must prove at most one broker submission and the
lifecycle-specific application result, or a visible intervention with entries
blocked.

`C9-K02a` kills before the `SUBMITTING` commit; `K02b` kills after it but before
adapter entry. `C9-K03a` kills after dispatch with acceptance unknown, `K03b`
covers submit timeout, and `K03c` covers broker acceptance before ID persistence.
K02b and every K03 subcase must recover as `UNKNOWN` without resubmission.

A genuinely external reviewer must approve this C9 design before implementation
and review the implementation/evidence before PASS. Parallel Codex agent reviews
are internal reviews and must never be labeled independent.

This is backend-process evidence. The actual PyInstaller/Tauri sidecar does not
exist until Phase D, and real IBKR paper restart/soak evidence remains Phase F.
Those phases must repeat the drill; Phase C must not claim literal packaged
sidecar or paper-broker proof.

## Consequences

### Positive

- A broker-accepted order has a durable identity across every crash window.
- Readiness means broker/DB reconciliation actually completed.
- Repeated events and restarts converge instead of duplicating orders/state.
- External broker activity is preserved and surfaced rather than mutated.
- Shutdown has a testable order, budget, and truthful clean/unclean result.

### Negative

- Order submission gains a durable write before the broker call.
- Ambiguity deliberately blocks new entries and may require operator work.
- A broker outage no longer permits the application to present normal readiness.
- Working orders can outlive the backend and must be made clear in the UI.
- Real packaged-sidecar and IBKR proof remains a later release gate.

## Rejected Alternatives

### Treat the broker order ID written after submission as sufficient

Rejected because the process can die before that identity is persisted.

### Automatically resubmit a pending order with no broker ID

Rejected because the broker may already have accepted it, producing a duplicate
real-money order.

### Automatically import or cancel every unknown broker order

Rejected because the order may have been placed intentionally outside TradeBot
and ownership cannot be inferred safely.

### Allow startup readiness while reconciliation runs in the background

Rejected because trading could begin from incomplete broker/DB truth.

### Cancel all working orders on ordinary application quit

Rejected because silent cancellation, especially of protective exits, changes
trading risk. Cancellation requires an explicit eligible operator action.

## Acceptance Criteria

- Durable UUID/orderRef intent is committed before every broker submission.
- Submit, status, fill, reconnect, and reconciliation transitions are idempotent.
- Readiness and new-entry authority remain blocked until reconciliation passes.
- Broker outage and contradictory/external state produce visible degraded or
  intervention states without automatic mutation.
- Manual exits verify broker quantity and reconcile the linked DB position.
- Exit cancellation uncertainty cannot permit a second exit.
- Every task and callback has one owner and is absent after same-process teardown.
- Startup failure unwinds all acquired resources.
- Shutdown attempts every cleanup stage, checkpoints, flushes logs, and releases
  the runtime lock last only with a valid shutdown certificate; a
  cancellation-resistant mutator forces process termination instead of
  voluntary lock release.
- External marker/DB state/checkpoint/log/lock hard-death boundaries preserve a
  truthful clean/unclean result.
- Fake-broker hard-kill tests cover all 17 stable C9 case families with no
  automatic duplicate order or DB position.
- Documentation clearly defers packaged-sidecar and IBKR paper proof to D/F.

## Owner Decision Record

The owner accepted all items below on 2026-07-14:

1. Ambiguous/external broker state blocks entries and is never auto-mutated.
2. Broker-unavailable startup remains read-only/degraded with reconnect.
3. Ordinary quit preserves acknowledged working orders and reconciles them.
4. Manual exits require freshly verified broker quantity and explicit/unique DB
   linkage; ambiguity blocks.
5. Reserved 5/10/5/5/5-second backend stages plus at least 45 seconds of current
   Compose/Uvicorn grace, with packaged enforcement in Phase D.
6. Fake-broker backend-process proof covers all 17 stable C9 families, with
   sidecar/IBKR repetition required in D/F and genuine external review required
   before C9 implementation and PASS.

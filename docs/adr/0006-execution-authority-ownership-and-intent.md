# ADR 0006: Execution Authority, Ownership, and Durable Intent

**Status:** Proposed — human approval required  
**Date:** 2026-07-27  
**Decision owners:** Unassigned  
**Risk approver:** Unassigned  
**Security approver:** Unassigned  
**Release authority:** Unassigned

## Context

The current platform has several checks around `place_order`, but it does not
have one execution boundary:

- normal entry submission calls `ibkr.ib.placeOrder` in `order_executor.py`;
- startup/reconnect market-to-limit conversion cancels and resubmits directly;
- emergency flattening submits directly from `safety_kernel.py`;
- manual, rule, TradingView/Claude, direct-AI, and proposal paths apply
  different authority and safety flags;
- `AUTOPILOT_MODE=OFF` is an AI control, not a global broker kill switch;
- the same safety policy can run twice, causing the first duplicate reservation
  to reject the second gateway call;
- some callers treat any non-`None` `Trade`, including `status=ERROR`, as a
  successful submission;
- in-process deduplication is not a durable execution intent;
- a local `orderRef` correlates an order but does not make the broker call
  idempotent; and
- every Uvicorn worker starts its own broker client and background loops.

ADR 0001 says the database is trade truth while the new roadmap requires
broker truth to dominate for positions, orders, executions, commissions, and
account state. ADR 0002 mixes AI authority with execution behavior. If this
proposal is accepted, it supersedes ADR 0001 and the execution-authority
portions of ADR 0002. Until acceptance, the conflict is an explicit NO-GO.

## Decision

### 1. Separate execution authority from AI authority

`AUTOPILOT_MODE` controls which AI behaviors may propose work. It does not
authorize a broker mutation and it is not a global stop.

A new durable execution-authority record controls all broker mutations:

| State | Risk-increasing entries | Reduce-only exits | Protection maintenance | Purpose |
|---|---:|---:|---:|---|
| `DISARMED` | no | no new discretionary exit | broker-native protection remains working | administrative safe state |
| `PAPER_ONLY` | approved paper account only | yes | yes | broker integration and soak |
| `LIVE_CANARY` | one approved intent inside canary envelope | yes | yes | attended first-live validation |
| `LIVE_BOUNDED` | approved sources inside permanent envelope | yes | yes | bounded steady state |
| `ENTRY_LOCKED` | no | yes | yes | loss, data, integrity, or reconciliation lock |
| `FLATTENING` | no | approved managed exposure only | coordinated with flatten | explicit containment procedure |

The record includes a monotonic version, account/environment, policy
fingerprint, actor, reason, approval reference, and timestamps. Restart never
widens authority. `ENTRY_LOCKED` does not automatically clear on a time
boundary.

Pause entries, cancel working entries, preserve/repair protection, and flatten
managed exposure are separate operations. No “kill” action may globally cancel
protective orders.

### 2. Use one gateway and one broker adapter

Every manual, rule, screener, TradingView, Claude, MCP, direct-AI, recovery,
resubmission, exit, protection, cancellation, and flatten request creates or
references a durable intent and enters the same gateway.

Only the broker adapter may call broker mutation methods. A static
architecture test inventories `.placeOrder(`, `.cancelOrder(`, and supported
modify calls and fails if they appear outside the adapter and its contract
tests.

The gateway performs, in order:

1. authenticate actor/workload and resolve role;
2. load exact account, authority state, policy version, and readiness;
3. bind the source request to an immutable intent payload;
4. classify risk-increasing, reduce-only, protection, cancel-entry, or flatten;
5. validate account/source/symbol/instrument/session/order allowlists;
6. validate finite prices/quantities, current data health, account risk,
   position truth, and protection policy;
7. atomically claim the idempotency key and reserve rate/risk capacity;
8. verify execution ownership;
9. persist `SUBMITTING` before crossing the broker boundary;
10. call the adapter once; and
11. persist the observed outcome without interpreting uncertainty as failure.

Policy evaluation must be pure. Durable reservations happen once at the
gateway, preventing today’s double-safety-check/double-dedup behavior.

### 3. Use typed gateway outcomes

`Optional[Trade]` is not an execution result. The gateway returns a
discriminated outcome such as:

- `REJECTED_POLICY` — broker boundary not crossed;
- `DUPLICATE_EXISTING` — return the existing intent;
- `ACKNOWLEDGED` — broker identity observed;
- `UNKNOWN` — boundary may have been crossed but acceptance is not proven;
- `PARTIALLY_FILLED`;
- `FILLED`;
- `CANCELLED`;
- `REJECTED_BROKER`; or
- `FAILED_PRE_SUBMIT` — proven failure before the adapter call.

HTTP and worker callers map these explicitly. No caller may report “applied,”
“approved,” or HTTP 201 solely because a `Trade` object is non-null.

### 4. Durable intent state machine

Each exact request has a caller-supplied or gateway-derived idempotency key,
unique within the approved account and intent namespace.

```text
RECEIVED
  -> VALIDATED
  -> CLAIMED
  -> SUBMITTING
  -> ACKNOWLEDGED
  -> PARTIALLY_FILLED
  -> FILLED

VALIDATED -> REJECTED_POLICY
CLAIMED -> FAILED_PRE_SUBMIT
SUBMITTING -> UNKNOWN -> RECONCILING
ACKNOWLEDGED/PARTIALLY_FILLED -> CANCEL_PENDING
CANCEL_PENDING -> CANCELLED | PARTIALLY_FILLED | FILLED | UNKNOWN
RECONCILING -> ACKNOWLEDGED | PARTIALLY_FILLED | FILLED |
               CANCELLED | REJECTED_BROKER | UNRESOLVED
```

Terminal business state and broker synchronization state are separate where
necessary. Every transition uses compare-and-swap on a monotonic version and
is appended to an audit table.

An exception, disconnect, timeout, or process loss after `SUBMITTING` produces
`UNKNOWN`. `UNKNOWN` is never automatically resubmitted. Reconciliation must
search the broker by approved identity/correlation fields and prove absence
within the defined evidence window before a human-authorized replacement
intent can be created.

`orderRef` contains a stable, non-secret correlation value. It is not treated
as a broker uniqueness constraint.

### 5. Execution ownership

Immediate topology is exactly one application replica and `WORKERS=1`. The
process uses one fixed, approved broker client ID; client-ID fallback is
forbidden once the broker-ownership work package is implemented.

The target split is:

- horizontally scalable API/read workers that cannot access broker mutation
  credentials; and
- one execution service holding broker mutation authority.

A durable lease/epoch is defense in depth, not a claim of perfect fencing
across an external broker API. The adapter validates the current owner and
epoch immediately before mutation, writes the epoch into local audit data and
correlation metadata, stops on lease uncertainty, and never performs automatic
failover until stale-owner behavior is proven against the exact broker
environment. Exclusive fixed client identity and single replica remain the
primary ownership controls.

### 6. Proposed normalized persistence

The implementation design will introduce:

- `execution_authority` — singleton/account-scoped state and version;
- `execution_authority_events` — append-only transitions and approvals;
- `executor_leases` — owner, epoch, heartbeat, expiry, account, client ID;
- `execution_intents` — unique account/namespace/idempotency key, immutable
  payload hash, classification, state, version, source, actor, policy version;
- `execution_intent_events` — append-only transition evidence;
- `broker_orders` — account, `conId`, client/order/perm IDs, `orderRef`, role,
  parent/OCA identity, revision, quantities, normalized/raw status;
- `broker_fills` — unique account/`execId`, quantity, price, time, order links;
- `broker_commissions` — unique account/`execId`, fee/currency/P&L; and
- an outbox for internal events emitted only after the related state commits.

State rows use explicit `UPDATE ... WHERE version = ?` transitions. Lifecycle
tables do not use `INSERT OR REPLACE`.

The existing `direct_candidates` queue is not reused as an intent ledger: its
claim is not compare-and-swap safe and its production drain path does not
complete terminal transitions.

### 7. Source binding and provenance

The durable payload binds account, source, authenticated actor/workload,
signal/candidate ID, symbol, `conId`, side, quantity, order type, prices,
session behavior, strategy/rule version, risk classification, and protection
policy. A model cannot approve one signal and submit a different payload.

Initial and resulting trade/order rows preserve the actual source. Manual,
rule, recovery, Claude, MCP, protection, and flatten actions cannot collapse
to a default `rule` provenance.

## Acceptance Evidence

Before this ADR can move to Accepted:

- named decision owner, risk approver, security approver, and release
  authority;
- complete source/route call graph and broker-mutation inventory;
- schema and transition review;
- tests for concurrent claims in coroutines and separate processes;
- crash tests before/after claim, `SUBMITTING`, adapter call, and broker
  acknowledgment;
- unknown-result reconcile-before-retry tests;
- lease expiry/stale-owner/client-conflict tests against the approved paper
  environment;
- negative tests for every source and authority state;
- typed-outcome contract tests for every HTTP/worker caller;
- restore-generation tests preventing stale intent replay; and
- static tests proving no broker mutation bypass.

## Consequences

This decision deliberately reduces availability: uncertainty disables new
entries instead of guessing. It adds normalized state and operational
complexity, but it gives retries, recovery, and operator messages precise
meaning.

No exactly-once claim is made. The target is at-most-once local intent handling
plus reconcile-before-retry across a broker boundary that does not provide an
application idempotency guarantee.

## Rejected Alternatives

- **Use `AUTOPILOT_MODE` as the execution state.** It does not cover manual,
  exit, protection, reconciliation, or flatten authority.
- **Trust `orderRef` to deduplicate.** It is correlation, not a broker-enforced
  uniqueness contract.
- **Retry any exception.** The broker may have accepted the order.
- **Scale the monolith after adding a shared rate limiter.** Shared rate count
  does not prevent duplicated loops, broker sessions, or stale owners.
- **Treat the database alone as broker truth.** Local state must be reconciled
  from authoritative broker executions/positions/account state.
- **Use global cancel as a kill switch.** It can remove protective orders and
  increase loss.

## Implementation Hold

While this ADR is Proposed:

- LIVE remains NO-GO;
- no new broker submit/cancel/replace behavior is implemented;
- startup requires `WORKERS=1`;
- the shared durable rate limiter may be used as defense in depth; and
- identified bypasses, recovery defects, and misleading result semantics stay
  visible in the risk register rather than being patched piecemeal.



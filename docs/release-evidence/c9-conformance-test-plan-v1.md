# C9 Conformance Test Plan v1

Status: `TEST_PLAN_ONLY_NO_RUNTIME_AUTHORITY`

This document is an executable-test design and evidence index for the C9
durable-intent and reconciliation checkpoint. It does not implement an order
adapter, connect to a broker, consume a canary authorization, or grant any
runtime authority. The tests described here may only be run after the
independent C9 design review and owner acceptance recorded in the signed
review artifact.

## Preconditions

The eventual harness must use a persistent fake broker, a network-denied
adapter, an isolated temporary trading database, and a separately deployed
external fence store. It must run with all live/AI/TV/MCP paths disabled. Each
run records the candidate commit, schema/migration hashes, harness version,
fence-store identity, and the exact failure injection point.

The harness must fail closed when any precondition is absent. In particular,
it must not substitute SQLite, an in-process counter, a mock-only CAS, or a
real broker for the external fence and persistent fake-broker requirements.

## Required crash and reconciliation families

| Case | Injection point | Required assertion |
| --- | --- | --- |
| K01 | Before durable intent persistence | No adapter call and no live order exist. |
| K02a | After intent persistence, before `SUBMITTING` commit | Recovery resumes from the durable intent without an adapter call. |
| K02b | After committed `SUBMITTING`, before adapter entry | Recovery performs at most one idempotent adapter call. |
| K03a | Adapter dispatched, acceptance unknown | Reconciliation queries broker state; it never retries with a new intent. |
| K03b | Submit timeout | Result is `UNKNOWN`/recovery-only until broker state is authoritative. |
| K03c | Broker accepted before broker ID persistence | Broker identity is recovered and bound to the existing intent. |
| K04 | Broker ID persisted before watcher registration | Snapshot/watch registration converges without a duplicate submission. |
| K05 | During partial/final-fill persistence | Filled quantity is persisted exactly once and remains reconciliable. |
| K06 | After fill persistence, before position registration | Position registration converges from durable execution evidence. |
| K07 | Before exit intent/pending-marker commit | No exit adapter call occurs without its durable marker. |
| K08 | After exit acceptance | The durable exit marker already exists before adapter entry. |
| K09 | Cancel timeout/failure and confirmed cancel | Every accepted nonterminal order is target-cancelled or escalated. |
| K10 | Broker rejection before DB transition | Rejection is durable and does not leave an apparently working order. |
| K11 | DB failure after broker acceptance | Recovery discovers and reconciles the accepted order. |
| K12 | Repeated reconnect/status events | Duplicate callbacks are idempotent and account-scoped. |
| K13 | Partial reconciliation before completion | Readiness remains blocked until convergence is proven. |
| K14 | `READY -> QUIESCING` racing a new entry | The close gate wins; no new entry is admitted. |
| K15 | Disconnect `READY -> RECONCILING` racing a new entry | The reconciliation gate wins; new entry is denied. |
| K16 | Intervention persistence failure | State is `AMBIGUOUS_INTERVENTION`; no silent continuation occurs. |
| K17 | Shutdown through lock release | Shutdown certificate, reconciliation, and lock release occur in order. |

## External-fence adversarial cases

The same harness must additionally demonstrate all of the following:

1. Concurrent consumers of one authorization produce exactly one committed
   `UNUSED -> CONSUMED_BY` transition.
2. A crash after the external CAS and before the trading-DB mirror cannot
   create a second intent or adapter call.
3. An unreachable fence store denies reservation, CAS, and submission; there
   is no soft-degrade or retry path.
4. Every trading-DB restore advances the external generation. A restored copy
   attempting to replay a consumed authorization is rejected and forced into
   recovery-only mode.
5. Account, release, nonce, intent, payload-hash, and restore-generation
   mismatches are rejected without mutating the external authorization.

## Evidence and review order

The harness emits signed, immutable reports for each case and a machine-
readable summary. Evidence is reviewed in this order:

1. Independent external design review and owner acceptance.
2. C9 implementation and persistent fake-broker test report.
3. Independent external result review naming the exact candidate and evidence
   hash.
4. C9 closeout and later Phase-C authorization.

Until all four steps are complete, this plan remains descriptive evidence
only. It cannot be used as proof of C9 PASS, candidate T, C12, paper soak,
canary authorization, or live trading authority.

# C9 External Design Review Package v1 (BLANK — for the independent reviewer)

Status: `PENDING`
`authority_granted`: `false`

> NON-AUTHORIZING. This package grants no implementation, deployment, paper,
> canary, or live-trading authority. It is the blank evidence form that the
> **independent** reviewer (not the design author, not the implementation team)
> completes against the exact, committed design hash below. It is the companion
> to `docs/release-evidence/c9-design-review-request-v1.md` and mirrors its
> required evidence list. No field in this document may be changed to `PASS`,
> add identities/signatures, or grant authority without the required external
> review and named owner acceptance.

## 0. Design binding

- Design manifest: `docs/release-evidence/manifests/canary-fence-design-v1.json`
- Design SHA-256 under review: `459610b9c3961f64d0827d8b0e076d3dcd16e0e88307ec31334ffbb805938884`
- The reviewer MUST recompute the committed manifest hash and confirm it equals
  the value above before proceeding. Confirmation: `PENDING (MATCH | MISMATCH-BLOCKED)`
- Design intent: dedicated external PostgreSQL control-plane fence, outside the
  restorable trading database and in an independent failure domain.

## 1. PostgreSQL topology / failure-domain proof

- Instance separation from trading DB (host, cluster, credentials, operator role): `PENDING`
- synchronous_commit level and rationale: `PENDING`
- primary/failover topology and failover test evidence: `PENDING`
- backup lineage independence from the trading DB: `PENDING`
- Independent-failure-domain proof (no shared storage/power/network SPOF): `PENDING`
- Phase C baseline scope-conflict disposition (PostgreSQL "out of scope" language):
  `PENDING (RESOLVED-COMPATIBLE | REQUIRES-BASELINE-AMENDMENT)`
- Reviewer finding: `PENDING`

## 2. SERIALIZABLE / CAS design review (one-and-only-one consume)

- Proposed isolation and transaction semantics (SERIALIZABLE or justified
  equivalent): `PENDING`
- Design proof that exactly one atomic `UNUSED -> CONSUMED_BY(intent_uuid)`
  transition can commit for an authorization: `PENDING`
- Design proof that no trading-DB mutation, mirror, or OperationGate check can
  act as a second consumption point: `PENDING`
- Planned conformance evidence and acceptance criteria, including exactly one
  winner under concurrent consumers: `PENDING`
- Actual concurrency evidence artifact path or immutable URI: `PENDING`
- Actual concurrency evidence SHA-256 (64 lowercase hex): `PENDING`
- Evidence execution environment / topology binding: `PENDING`
- Observed concurrency result: `PENDING (EXACTLY-ONE-WINNER | FAIL-BLOCKED)`
- Reviewer verification of artifact existence, hash, provenance, and result:
  `PENDING (PASS | FAIL-BLOCKED)`
- Reviewer design finding: `PENDING`

## 3. Restore-generation fence design review

- Design proof that every supported trading-DB restore advances the external
  generation and binds the restored snapshot to that generation: `PENDING`
- Design proof that a stale or out-of-band restored DB cannot resurrect or
  replay a consumed authorization: `PENDING`
- Design proof that any generation mismatch or unprovable generation binding
  forces UNTRUSTED recovery-only behavior with no new entry: `PENDING`
- Planned restore-replay evidence and acceptance criteria: `PENDING`
- Actual restore-replay evidence artifact path or immutable URI: `PENDING`
- Actual restore-replay evidence SHA-256 (64 lowercase hex): `PENDING`
- Evidence execution environment / restore lineage binding: `PENDING`
- Observed restore-replay result: `PENDING (REPLAY-DENIED-RECOVERY-ONLY | FAIL-BLOCKED)`
- Reviewer verification of artifact existence, hash, provenance, and result:
  `PENDING (PASS | FAIL-BLOCKED)`
- Reviewer design finding: `PENDING`

## 4. Fence-outage / control-plane failure design review

- Design proof that an unreachable or ambiguous fence denies reservation, CAS,
  and submission without soft degradation or automatic retry: `PENDING`
- Design proof that recovery remains read-only/reconciliation-only until fence
  authority is proven trustworthy again: `PENDING`
- Planned outage/partition evidence and acceptance criteria: `PENDING`
- Actual fence-outage evidence artifact path or immutable URI: `PENDING`
- Actual fence-outage evidence SHA-256 (64 lowercase hex): `PENDING`
- Evidence execution environment / fault-injection binding: `PENDING`
- Observed outage result: `PENDING (FAIL-CLOSED-NO-CAS-NO-SUBMISSION | FAIL-BLOCKED)`
- Reviewer verification of artifact existence, hash, provenance, and result:
  `PENDING (PASS | FAIL-BLOCKED)`
- Reviewer design finding: `PENDING`

## 5. Access-control / credential separation / audit / retention / operator-role review

- Credential separation (control-plane vs trading vs broker): `PENDING`
- Operator-role least privilege and separation of duties: `PENDING`
- Audit log location, immutability, retention: `PENDING`
- Reviewer finding: `PENDING`

## 6. Canonical C9 K01–K17 crash / reconciliation evidence plan

This table is the pre-implementation design-review view of the canonical 17
families in `docs/release-evidence/c9-conformance-test-plan-v1.md`,
`docs/PHASE_C_ULTRAPLAN.md`, and `docs/PHASE_C_VERIFICATION.md`. It records
planned assertions and design-review findings only. Runtime observations belong
to the later implementation evidence and independent result review, not this
package.

| ID | Canonical injection point | Required planned assertion | Design-review finding |
| --- | --- | --- | --- |
| K01 | Before durable intent persistence | No adapter call and no live order can exist. | PENDING |
| K02a | After intent persistence, before the committed `SUBMITTING` transition | Recovery resumes from the durable intent without an adapter call. | PENDING |
| K02b | After committed `SUBMITTING`, before adapter entry | Recovery permits at most one idempotent adapter call; ambiguity is never aborted or resubmitted. | PENDING |
| K03a | Adapter dispatched, acceptance unknown | Reconciliation queries authoritative broker state and never retries with a new intent. | PENDING |
| K03b | Submit timeout | State remains `UNKNOWN`/recovery-only until broker state is authoritative. | PENDING |
| K03c | Broker accepted before broker-ID persistence | Broker identity is recovered and bound to the existing intent without resubmission. | PENDING |
| K04 | Broker ID persisted before watcher/snapshot registration | Snapshot/watch registration converges without duplicate submission. | PENDING |
| K05 | During partial/final-fill persistence | Filled quantity is persisted exactly once and remains reconciliable. | PENDING |
| K06 | After fill persistence, before position registration | Position registration converges from durable execution evidence. | PENDING |
| K07 | Before exit-intent/pending-marker commit | No exit adapter call occurs without its durable marker. | PENDING |
| K08 | After exit acceptance | The durable exit marker already exists before adapter entry. | PENDING |
| K09 | Cancel timeout/failure and confirmed-cancel-before-DB transition | Every accepted nonterminal order is target-cancelled or escalated; confirmed cancellation converges durably. | PENDING |
| K10 | Broker rejection before DB transition | Rejection becomes durable and cannot leave an apparently working order. | PENDING |
| K11 | DB failure after broker acceptance | Recovery discovers and reconciles the accepted order. | PENDING |
| K12 | Repeated reconnect/status events | Duplicate callbacks are idempotent and account-scoped. | PENDING |
| K13 | Partial reconciliation before completion | Readiness remains blocked until complete-set convergence is proven. | PENDING |
| K14 | `READY -> QUIESCING` racing a new entry | The close gate wins and no new entry is admitted. | PENDING |
| K15 | Disconnect-triggered `READY -> RECONCILING` racing a new entry | The reconciliation gate wins and entry is denied. | PENDING |
| K16 | Intervention persistence failure | State becomes `AMBIGUOUS_INTERVENTION`; no silent continuation occurs. | PENDING |
| K17 | Each shutdown boundary through lock release | Shutdown reconciliation, shutdown-state write, checkpoint, log flush, external-marker handling, and lock release follow the governed order. | PENDING |

Required cross-family design assertions: `PENDING`

- persistent fake broker outside the killed backend process;
- network-denied adapter harness and asserted fake-adapter identity;
- at most one broker submission per intent and exact idempotency behavior;
- exact fill/trade/position convergence, including partial fills and exits;
- complete-set reconciliation with subscribe/buffer/snapshot/drain stability;
- unrelated external orders are surfaced but never mutated;
- every raw place/cancel path is confined to the broker adapter; and
- ambiguity creates durable intervention and blocks entry.

## 7. External-fence adversarial design cases (separate from K01–K17)

These cases extend the canonical C9 matrix; they do not replace or renumber any
K01–K17 family. This package reviews their design and planned acceptance
criteria only.

| Case | Required design property | Design-review finding |
| --- | --- | --- |
| F01 Concurrent consume | Concurrent consumers of one authorization can produce exactly one committed `UNUSED -> CONSUMED_BY` transition. | PENDING |
| F02 Crash after CAS, before DB mirror | The authorization remains consumed/burned; recovery mirrors external truth and cannot create a second intent or adapter call. | PENDING |
| F03 Fence unavailable or ambiguous | Reservation, CAS, and submission are denied; there is no soft-degrade or automatic-retry path. | PENDING |
| F04 Trading-DB restore replay | Every restore advances the external generation; replay of a consumed authorization is rejected and forced into recovery-only. | PENDING |
| F05 Binding mismatch | Account, release, nonce, intent, payload-hash, and restore-generation mismatches are rejected without mutating the authorization. | PENDING |

## 8. Mandatory pre-acceptance evidence bindings

Owner acceptance is forbidden until the independent reviewer has verified all
three actual evidence artifacts named in Sections 2–4. A design narrative,
planned test, empty report, synthetic success marker, or unhashed attachment is
not evidence. For each artifact, the path or immutable URI MUST resolve, its
bytes MUST match the recorded 64-hex SHA-256, its provenance MUST identify the
reviewed execution environment, and its observed result MUST satisfy the stated
acceptance outcome. Any missing artifact, hash mismatch, provenance gap,
ambiguous observation, or failed result keeps review status `PENDING` or
`REJECT` and keeps `authority_granted: false`.

- CAS concurrency evidence binding complete and reviewer-verified: `PENDING`
- Restore-replay evidence binding complete and reviewer-verified: `PENDING`
- Fence-outage evidence binding complete and reviewer-verified: `PENDING`
- All three evidence hashes bound into the reviewer-signed decision payload:
  `PENDING`

These are pre-implementation external-fence design-review artifacts. They do
not satisfy, replace, pre-approve, or collapse the later implementation-time
K01–K17 runtime observations and independent result review. Runtime C9 result
review remains a separate post-implementation artifact and gate.

## Decision record (independent reviewer + owner only)

- Reviewer identity and independence declaration (not design author / not implementer): `PENDING`
- Review decision: `PENDING` (`PENDING | CONDITIONAL | PASS | REJECT`)
- Conditions / findings: `PENDING`
- Review expiry / revocation policy: `PENDING`
- Reviewer-signed decision payload reference: `PENDING`
- Detached reviewer signature artifact/reference: `PENDING`
- Detached reviewer signature artifact SHA-256 (64 lowercase hex): `PENDING`
- Reviewer signer identity / key fingerprint: `PENDING`
- Approved trust-root / verification-policy reference: `PENDING`
- Signature verification result: `PENDING (PASS | FAIL-BLOCKED)`
- Signature verification evidence/reference: `PENDING`
- Signed-payload binding to the exact design hash, decision, conditions,
  expiry/revocation policy, and all three Section 8 evidence hashes: `PENDING`
- Owner acceptance naming this exact design hash
  (`459610b9c3961f64d0827d8b0e076d3dcd16e0e88307ec31334ffbb805938884`): `PENDING`
- `authority_granted`: `false`

No field in this document may be changed to `PASS`, add identities/signatures, or
grant authority without the required external review and named owner acceptance.
Authority to implement C9 is conveyed only by a separately signed owner
acceptance, never by editing this file. That owner acceptance is invalid unless
Sections 2–4 and 8 are complete and the detached reviewer signature verifies as
`PASS` against the approved trust root. Runtime observations and independent
result review are separate post-implementation artifacts and must not be added
to this design-review package.

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

## 2. SERIALIZABLE / CAS concurrency evidence (one-and-only-one consume)

- Isolation level used (SERIALIZABLE or justified equivalent): `PENDING`
- Concurrency evidence (attach probe output or equivalent conformance run): `PENDING`
- Observed winners across N concurrent workers (must be exactly 1): `PENDING`
- Cross-reference to `backend/tests/test_canary_fence_conformance.py` result: `PENDING`
- Reviewer finding: `PENDING`

## 3. Restore-generation fence evidence (replay of a consumed permit)

- Evidence that a restored trading DB cannot resurrect a consumed permit A: `PENDING`
- Generation mismatch forces recovery-only; NO second entry: `PENDING`
- Cross-reference to `backend/tests/test_restore_generation_fence.py` result: `PENDING`
- Reviewer finding: `PENDING`

## 4. Fence-outage / control-plane failure evidence (fail-closed)

- Evidence that while the fence is unreachable, NO reservation/CAS/submit proceeds: `PENDING`
- Recovery-only behavior confirmed under partition: `PENDING`
- Reviewer finding: `PENDING`

## 5. Access-control / credential separation / audit / retention / operator-role review

- Credential separation (control-plane vs trading vs broker): `PENDING`
- Operator-role least privilege and separation of duties: `PENDING`
- Audit log location, immutability, retention: `PENDING`
- Reviewer finding: `PENDING`

## 6. C9 K01–K17 crash / reconciliation evidence plan and failure paths

Each row: the crash/failure injection point, the required invariant, and the
observed result. All `observed` fields start `PENDING`.

| ID  | Injection point | Required invariant | Observed |
|-----|-----------------|--------------------|----------|
| K01 | Crash BEFORE external CAS | permit stays UNUSED; no reservation; recovery-only | PENDING |
| K02 | Crash DURING CAS txn | atomic: either UNUSED or exactly one CONSUMED_BY; never partial | PENDING |
| K03 | Crash AFTER CAS, BEFORE DB mirror | permit CONSUMED (burned); mirror reconciles to external; no 2nd intent | PENDING |
| K04 | DB mirror disagrees with external | fail closed; external is source of truth | PENDING |
| K05 | Crash AFTER CALL_INTENT written, BEFORE adapter | on restart CALL_INTENT permits reconciliation only, never a 2nd adapter call | PENDING |
| K06 | Crash DURING adapter submit (unknown outcome) | AMBIGUOUS_INTERVENTION; no re-arm; broker fallback | PENDING |
| K07 | Duplicate intent UUID | rejected by uniqueness constraint | PENDING |
| K08 | Duplicate idempotency key | rejected | PENDING |
| K09 | External-store outage at reservation | fail closed (no reservation) | PENDING |
| K10 | Trading-DB restore replay of consumed permit | refused via generation fence; no 2nd entry | PENDING |
| K11 | Account-fingerprint mismatch on order | rejected | PENDING |
| K12 | Account-fingerprint mismatch on cancel | rejected | PENDING |
| K13 | Account-fingerprint mismatch on fill callback | rejected | PENDING |
| K14 | Broker REJECTED | neutralize; NO-GO; record outcome | PENDING |
| K15 | Broker PARTIALLY_FILLED then stop | cumulative-fill ceiling respected; uniform stop | PENDING |
| K16 | Broker CANCEL_PENDING / UNKNOWN | uniform stop; reconcile to terminal or signed transfer | PENDING |
| K17 | Second-entry attempt under ANY failure branch | impossible (max_entry_* = 1 enforced) | PENDING |

## Decision record (independent reviewer + owner only)

- Reviewer identity and independence declaration (not design author / not implementer): `PENDING`
- Review decision: `PENDING` (`PENDING | CONDITIONAL | PASS | REJECT`)
- Conditions / findings: `PENDING`
- Result review (post crash/reconciliation matrix): `PENDING`
- Review expiry / revocation policy: `PENDING`
- Owner acceptance naming this exact design hash (`459610b9…`): `PENDING`
- `authority_granted`: `false`

No field in this document may be changed to `PASS`, add identities/signatures, or
grant authority without the required external review and named owner acceptance.
Authority to implement C9 is conveyed only by a separately signed owner
acceptance, never by editing this file.

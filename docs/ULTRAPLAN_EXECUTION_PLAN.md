# ULTRAPLAN Execution Plan — Final Governance-Aligned Version

## Summary

Execute from the verified implementation branch only after confirming ancestry
from the protected docs-amendment merge. Complete pre-T infrastructure, obtain
independent C9 design review and owner acceptance, implement C9 runtime
controls, execute separately authorized C1–C12 checkpoints, and then run the
isolated post-C12 scanner chain.

No implementation, paper, or live authority is implied by this plan alone.

## Governance decisions and invariants

- Use Cosign with non-exportable KMS-backed keys for artifact and OCI signatures.
- Publish immutable backend/dashboard images to GHCR by digest only, with
  provenance bound to the exact candidate commit.
- Use a dedicated PostgreSQL control-plane store outside the restorable trading
  database, with independent storage, backups, credentials, operator role, and
  failure domain. This remains subject to independent C9 review and owner
  acceptance.
- Freeze `CANARY_POLICY_SCHEMA_HASH` and hard safety ceilings before T. Create
  signed policy values Q only after S and before B/A.
- The external CAS is the sole A-consumption point:
  `PREPARE durable intent → external CAS consumes A → mirror exact tuple into
  trading DB → OperationGate validates agreement → submit`.
- Every trading-DB restore advances the external generation, so no restored
  database can present a generation matching a live A; stale authorizations are
  invalid and the process enters recovery-only mode.
- If the external fence is unreachable, no reservation, CAS, or submission may
  proceed.

## Implementation sequence

1. Verify `IMPLEMENTATION_BASE_SHA` ancestry and the clean worktree.
2. Complete exact toolchain, dependency, signature, OCI, Compose,
   scanner-schema, runtime-manifest, broker-inventory, process-artifact,
   test-outcome, and fence-design gates.
3. Obtain signed independent C9 design review and owner acceptance.
4. Implement durable intents, external CAS, DB mirror, CALL_INTENT journal,
   OperationGate, account scoping, restore tooling, broker state graph, and
   uniform stop/reconciliation behavior.
5. Execute the C9 matrix, including concurrent reservation and duplicate-
   idempotency races; crash before CAS; crash after CAS before DB mirror; crash
   after committed CALL_INTENT; external-fence outage; account mismatch;
   UNKNOWN/partial/full-fill/cancel-pending outcomes; and a trading-DB restore
   attempting to replay a consumed A, proving no second entry can occur.
6. Execute separately authorized C1–C12 checkpoints.
7. Build immutable images/manifests, establish exact T, run exact-T verification
   and external C9 result review, create E_C, owner approval, C_C, C_C CI,
   administrative C12 PASS, then create/protect the signed candidate/runtime
   tag on T.
8. Run the post-C12 chain `P → S → Q → B → A`.
9. Execute `PREPARE → external CAS consumes A → DB mirror → agreement gate →
   submit → broker reconciliation → L`.
10. Obtain signed HOLD/NO-EXPAND, merge evidence-only R, run R CI, create the
    outcome tag on R, and stop.

## Acceptance tests

- Exact ancestry, candidate-tag chronology, and immutable image binding.
- Tool versions/checksums, dependency locks, signatures, OCI provenance, and
  digest-only deployment.
- Compose build, health, isolation, and cleanup.
- Scanner schema/value chronology and signed-threshold validation.
- PostgreSQL CAS uniqueness, terminal transitions, independent durability,
  outage fail-closed behavior, and DB-mirror disagreement.
- Adversarial restore replay proving a consumed A cannot produce a second entry.
- Durable CALL_INTENT crash recovery proving no duplicate adapter call.
- Universal OperationGate deny-by-default inventory.
- Full broker state graph, account scoping, target cancellation, exposure
  disposition, and uniform stop sequence.
- Complete E_C/C_C/C12 and P/S/Q/B/A/L/HOLD/R evidence chain.

## Assumptions

- GHCR is the selected OCI target.
- Cosign KMS-backed signing is the selected trust model.
- PostgreSQL is a proposed fence-store choice pending independent C9 review.
- Exact KMS IDs, reviewer identity, policy values, soak thresholds, image
  digests, and account fingerprints come only from signed authorization
  artifacts.

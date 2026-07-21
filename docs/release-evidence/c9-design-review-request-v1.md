# C9 External Design Review Request v1

Status: `PENDING`

This is a non-authorizing review request. It grants no implementation, deployment,
paper, canary, or live-trading authority.

## Design under review

- Design manifest: `docs/release-evidence/manifests/canary-fence-design-v1.json`
- Design SHA-256: `459610b9c3961f64d0827d8b0e076d3dcd16e0e88307ec31334ffbb805938884`
- Design intent: dedicated external PostgreSQL control-plane fence, outside the
  restorable trading database and in an independent failure domain.
- Required disposition: explicitly resolve the interaction with the earlier Phase C
  baseline language that lists PostgreSQL architecture as out of scope.

## Required independent review evidence

The reviewer must be independent of implementation and must examine the exact
design hash above. The review package must include, at minimum:

1. PostgreSQL topology, synchronous-commit, primary/failover, backup, and
   independent-failure-domain proof.
2. SERIALIZABLE/CAS concurrency evidence proving one-and-only-one permit consume.
3. Restore-generation fence evidence, including replay of a consumed permit after
   trading-database restore.
4. Fence outage and control-plane failure evidence proving fail-closed behavior.
5. Access-control, credential separation, audit, retention, and operator-role review.
6. The complete C9 K01–K17 crash/reconciliation evidence plan and its failure paths.

## Decision record

- Reviewer identity and independence declaration: **PENDING**
- Review decision: `PENDING` (`PENDING|CONDITIONAL|PASS|REJECT`)
- Conditions/findings: **PENDING**
- Owner acceptance naming this exact design hash: **PENDING**
- Review expiry/revocation policy: **PENDING**
- `authority_granted`: `false`

No field in this document may be changed to `PASS`, add identities/signatures, or
grant authority without the required external review and named owner acceptance.

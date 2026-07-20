# Stage C Scanner Governance Handoff — 2026-07-20

## Scope and Authority

This handoff covers the proposed ULTRAPLAN v5.3.2.1 documentation amendment
from `origin/master` at `163c27b76119c90472f9e94d0874d465eb5e5dad`. It grants no
C1–C12, paper, or live authority.

## Validation

- The amendment preserves the canonical C1–C12 order and tracker authority.
- Phase C and scanner-release evidence chains remain separate.
- Both scanner policy paths are registered:
  `docs/release-evidence/protocols/scanner-soak-v1.json` and
  `docs/release-evidence/protocols/scanner-canary-v1.json`.
- The plan requires pre-T toolchain, dependency, Compose, OCI, signature,
  verifier, inventory, runtime-manifest, and restore-fence gates.

## Preserved Boundaries

All runtime-bearing merges and immutable builds precede T. Post-T operations
only promote already-approved digests and add evidence or authorization. The
one-live-intent canary remains bounded by the central OperationGate, external
fence CAS, durable CALL_INTENT journal, broker reconciliation, and mandatory
stop/NO-GO behavior.

## Wrap-up

The docs-stage worktree is based on the verified planning SHA. Required
implementation and pre-T artifacts are not asserted as complete by this
handoff.

## Stop Boundary

Stop before protected PR publication, checkpoint implementation, toolchain
provisioning, broker credentials, paper soak, or live authority. Resume only
with the applicable owner authorization and verified prerequisites.

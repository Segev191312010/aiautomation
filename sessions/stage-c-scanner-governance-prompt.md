# Stage C — Scanner Governance Documentation Prompt

## Scope and authority

Prepare the proposed ULTRAPLAN v5.3.2.1 scanner-governance amendment from the
verified `origin/master` baseline. This stage is documentation-only. It does
not authorize C1–C12 implementation, paper soak, broker access, live trading,
remote branch publication, or checkpoint execution.

## Required validation

Verify the exact planning SHA, clean worktree, canonical Phase C plan/tracker
paths, two policy artifact registrations, and the two separated evidence chains.
Confirm that all runtime-bearing work remains before T and that post-T actions
only promote immutable artifacts.

## Preserved boundaries

Keep every C1–C12 checkpoint separately authorized. Preserve the external
restore-generation fence, central OperationGate, one-shot intent limits,
fail-closed recovery, signed policy artifacts, and no-expansion canary rule.

## Wrap-up

Record the amendment, validation results, unresolved prerequisites, and exact
stop boundary in the handoff and learning log. Do not claim implementation or
trading readiness from documentation completion.

## Stop boundary

Stop after the docs-only change is locally validated. Protected PR opening,
merge, implementation worktree creation, provisioning, and all paper/live
operations require their separately recorded authorization.

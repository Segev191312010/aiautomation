# Phase C Planning Handoff

Date: 2026-07-12

Last revised: 2026-07-14

Status: ACCEPTED PLANNING RECORD - C0 AUTHORIZED; C1-C12 NOT AUTHORIZED

## Outcome

Roadmap Phase C has an accepted planning package for Data Durability and Runtime
Hardening. This planning package is documentation-only. Separately authorized
C1A emergency containment changed production before this record and is preserved
by its own source/evidence chain. The plan is divided into C0-C12 with explicit
dependencies, owner decisions, agent roles, focused gates, failure injection,
clean-source closeout, and Phase D-F boundaries.

Phase B B12 is PASS and Phase B is closed. ADRs 0007-0009 and D1-D21 are
accepted. GitHub defaults to protected `master`; disconnected `main` remains
archived; PRs #1, #3, and #4 were closed unmerged. The owner authorizes C0
verification only. C1-C12 implementation must not begin without later explicit
authorization.

## Planning Artifacts

- `docs/PHASE_C_ULTRAPLAN.md` (accepted master plan)
- `sessions/phase-c-data-durability-prompt.md`
- `docs/release-evidence/2026-07-phase-c-baseline.md`
- `docs/release-evidence/2026-07-phase-c-tracker.md`
- `docs/release-evidence/2026-07-phase-c-critical-module-inventory.md`
- `docs/PHASE_C_VERIFICATION.md`
- `docs/adr/0007-application-data-and-legacy-import.md`
- `docs/adr/0008-sqlite-migration-backup-restore.md`
- `docs/adr/0009-runtime-lifecycle-and-order-reconciliation.md`
- Phase C planning annotation in `docs/APPLICATION_READINESS_ROADMAP.md`

## Evidence Captured

- Historical pre-C1A planning source: `a410baeb712fbe11d4c8b1b838b2a49df70b54c3`.
- Same-source Ubuntu CI was green before planning.
- Baseline gates: backend 720; dashboard typecheck/build; 31 files/389 tests;
  frontend/OpenAPI 147 call sites/145 unique operations/190 OpenAPI operations.
- Disposable fresh schema: 31 tables and 32 indexes.
- Ignored event-log metadata only: five files, 644,946,524 bytes. Contents and
  all operational databases, credentials, prompts, trades, and account data were
  not inspected.
- Historical planning-source static inventory: 279 broad
  `Exception`/`BaseException` handlers across 68 backend Python files. The plan
  scopes C10 to the exact critical subset
  rather than promising a mechanical repository-wide rewrite.
- Historical GitHub recheck before governance: default `main`; neither `main`
  nor `master` protected; open PRs #1, #3, and #4 targeted disconnected `main`.
- Current governance: protected `master` is default with strict backend/dashboard
  checks and administrator enforcement; `main` and its archive tag preserve
  `16280057ab04bee97904e9c59b9a5143a58bb673`; all three PRs are closed unmerged.

Three parallel internal Codex read-only specialist agents reviewed
data/migrations, runtime/recovery,
and verification/documentation. Their blocking findings were incorporated:
destructive retention remains disabled through C7; existing DBs are classified
read-only against a multi-variant registry before checkpoint/verified backup and
one-transaction canonicalization; C9 uses a persistent network-denied fake
broker across 17 stable case families; and shutdown lock release requires a
certificate or forced process death. These are internal reviews, not independent
assurance. C9 separately requires genuine external review before implementation
and before PASS. Specialist subagents made no edits.

At the five-file planning checkpoint on 2026-07-14, the full gate passed:
backend 720/720 with no skips/xfails, dashboard typecheck and build (617 modules),
and 31 files/389 Vitest tests. No subagent staged, committed, or pushed.

A final pair of internal Codex reviews then found and corrected plan-level
contradictions: checkpoint is now the sole allowed pre-backup source mutation;
the strict restore journal is destination-local with exact state/file/hash
recovery; lock-parent bootstrap is the sole pre-lock exception; DB-clean state
is provisional beneath marker precedence; and K02/K03 plus snapshot/subscription
gaps are explicit. The post-correction full gate again passed backend 720/720,
dashboard typecheck/build (617 modules), and 31 files/389 tests. This remains
internal review, not the genuine external C9 review required for authorization.

The attached Ultraplan v2 was reconciled into revision 3 of the local master
candidate rather than adopted verbatim. Three additional parallel read-only
audits corrected its retention surfaces, live candidate-state vocabulary,
fresh/current/upgrade schema branches, migration checksum/seed requirements,
backup/restore barriers, shutdown certificate fallback, and C9 harness boundary.
At the resulting five-file checkpoint, backend 720/720 passed with no
skip/xfail/warning; dashboard typecheck/build passed (617 modules), and Vitest
passed 31 files/389 tests. These remain internal checks.

A final review caught an impossible unknown-database `no directory artifact`
claim that conflicted with the mandatory lock/marker lifecycle. The corrected
invariant is zero source-DB mutation and no DB/WAL/SHM/backup artifact, while the
private lock parent, external marker, and approved under-lock directories remain
mandatory. The resulting second five-file checkpoint again passed backend
720/720 and dashboard typecheck/build plus 31 files/389 tests. One contract-check
invocation inherited broker-backed settings and failed closed on the default JWT;
its explicit temporary simulation/AI-OFF rerun passed 147/145/190, followed by
workspace hygiene and diff checks.

## Completed C1A Safety Boundary

C1A now rejects destructive retention (`dry_run=false`, CLI `--execute`, admin
cleanup/preview/stats, Parquet deletion, shared-backup-directory DELETE,
diagnostics-news terminal-row pruning, and startup terminal-candidate GC) before
connection/path/file/row mutation. Queued/draining candidate TTL expiration
remains active as an execution-safety control. Destructive execution stays
disabled until the complete C7 policy/archive gate and accepted D5-D7/D21 policy
are implemented and tested, then may activate only through an authorized C9.

Four diagnostics tables store non-null Unix-second INTEGER timestamps, while
current retention compares them with an ISO TEXT cutoff. SQLite affinity and
storage-class ordering make every row eligible, regardless of age. Required
archive failure also returns control to deletion instead of failing closed. The
C-F01/C-F02 therefore remain latent defects, not resolved algorithms. C1A passed
at `6093f0f7d5f66489a2ed55e9f3998b2921b6cde5`, evidence `1744bdb`, and CI
`29324523583`; it grants no broader authority.

## Accepted Planning Shape

1. C0: Phase B closeout, protected-default-`master` governance, committed plan,
   clean worktree/gates, early Windows/Ubuntu CI, ADR/decision acceptance.
2. C1: typed retention foundations that preserve C1A containment.
3. C2: one application-path resolver without prematurely flipping defaults.
4. C3: read-only structural classifier registry, integrity, blocking
   pre-migration checkpoint, and verified `BackupManifestV1` backup.
5. C4: self-contained immutable migrations, application ID, append-only ledger,
   deterministic reference data, and one atomic canonicalization transaction.
6. C5: explicit legacy import plus journaled offline atomic restore/export.
7. C6: every supported historical structural variant and migration/restore
   matrix.
8. C7: retention rewrite, dormant scheduler, and visible maintenance state.
9. C8: supervised lifecycle/task/external-marker foundations plus
   certificate-gated release/forced termination.
10. C9: durable order intent, single broker adapter, exact broker/DB
    reconciliation, all C9-K01..K17 families, final lifecycle integration, and
    opt-in safe scheduler activation.
11. C10: critical exception gate, unified redaction, diagnostic bundle.
12. C11: authenticated temporary dashboard visibility and Windows/Ubuntu CI.
13. C12: candidate `T`, exact-source local/Windows/Ubuntu proof, external C9
    review, evidence `E`, owner approval naming T/E, closeout `C`, and CI on C.

No Tauri shell, installer/updater, dependency-wide release work, packaged-app
claim, real IBKR soak, PostgreSQL, or live-money authorization belongs to C.

## Accepted Owner Decisions

The owner accepted the 21 matching decisions (D1-D21) on 2026-07-14. The set
covers the supported schema floor; `%LOCALAPPDATA%` paths; copy-only legacy
import; fail-closed/operator-opt-in retention; maintenance schedule timezone;
offline restore; unencrypted per-user-ACL backup disclosure; ambiguous broker
state; degraded broker startup; preservation of working orders; safe manual
exits; critical-only exception scope; privacy-safe bundles; 30-second backend
shutdown; fake-broker Phase C proof; the temporary dashboard surface;
operation-specific WAL behavior; no automatic full-backup deletion in C; and
the explicit noncanonical table-retention schedule.

B12 is closed, C1A is PASS, and ADRs 0007-0009 plus D1-D21 are accepted. D2's
exact POSIX/XDG mapping and D14's exact 77-file plus capability-trigger boundary
are binding design policy. Acceptance does not authorize their C1-C12
implementation.

## Workspace and Git Boundary

The active workspace contains the owner's modified
`sessions/baselines/.gitkeep` and untracked `files/skills`. Preserve them. Do not
reset, stage, or include them in a Phase C commit. Start implementation in a
clean clone/worktree at an immutable approved `master` commit.

GitHub governance is complete: protected `master` is default, disconnected
`main` remains preserved by the archive tag, and its three PRs were triaged and
closed unmerged. No rename or unrelated-history merge occurred.

This protected documentation pull request becomes the durable accepted planning
record only after merge. C0 runs in a separate branch/PR afterward.

## Next Action

1. Merge the documentation-only Phase B closeout/Phase C planning PR after its
   local and required CI gates pass.
2. Create a clean C0 branch from the merged protected `master`.
3. Add only C0 verification tooling, tests, machine policy data, focused
   Windows/Ubuntu jobs, and evidence; make no production/runtime change.
4. Merge the protected C0 PR only after local and same-source CI proof.
5. Stop. Do not begin C1-C12 without a later explicit owner instruction.

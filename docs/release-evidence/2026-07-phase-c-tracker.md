# Phase C Tracker - Single Table of Truth

Date opened: 2026-07-12

Status: C0 PASS; C1-C12 PLANNED - NOT AUTHORIZED

Authoritative brief: `sessions/phase-c-data-durability-prompt.md`

Master plan: `docs/PHASE_C_ULTRAPLAN.md` (owner accepted 2026-07-14)

Baseline: `docs/release-evidence/2026-07-phase-c-baseline.md`

Verification manual: `docs/PHASE_C_VERIFICATION.md`

Accepted D14 inventory:
`docs/release-evidence/2026-07-phase-c-critical-module-inventory.md`

C0 evidence: `docs/release-evidence/2026-07-14-phase-c-c0.md`

## Entry Gates

| Gate | Required evidence | Current status |
|---|---|---|
| Phase B owner closeout | B12 owner acceptance and evidence-only closeout commit | PASS - closed 2026-07-14 |
| Emergency C1A choice | Explicit owner authorization or explicit decline; no implied authority | PASS - separately authorized and verified |
| Phase C policy approval | Accepted ADRs 0007-0009 and owner decision register | PASS - accepted 2026-07-14 |
| Repository governance | Live default/protected `master`, required CI, archived disconnected `main`, three PRs triaged without history merge | PASS |
| Durable planning record | Owner-approved Phase C plan committed before implementation | PASS - PR #5 merged as `92fc971` |
| Clean source | Clean worktree/clone, local HEAD equals intended remote commit | PASS - exact merged technical source `3fff984` |
| Safe mode | Simulation, Autopilot OFF, synthetic DB/fake broker | PASS FOR C0 - verifier-owned temporary root; future checkpoints must re-prove |
| Baseline gates | Backend, dashboard, contract, hygiene, versions, CI | PASS - 739 backend; 31 files/389 dashboard; 147/145/190 contract; run `29338942043` |

The entry gates are necessary but not sufficient authorization. The owner has
authorized C0 verification only, and C0 is now complete. No C1-C12 row may move
to `IN PROGRESS` without a later explicit instruction. C1A is completed
historical emergency containment and did not open C0-C12.

## Checkpoint Tracker

| ID | Outcome | Primary artifacts | Required focused proof | Lead agents | Depends on | Status |
|---|---|---|---|---|---|---|
| C1A | Pre-C0 emergency retention lockout | API/service/CLI/helpers/stats/backup DELETE/two automatic delete paths; focused tests; dated hotfix evidence | all mutation paths disabled; no DB/WAL/SHM/directory/row/Parquet/JSONL/non-JSONL sentinel mutation; full four gates | Database, Security, Test Automator, Code Reviewer | explicit owner authorization only | PASS - `6093f0f`; evidence `1744bdb` |
| C0 | Authorization, governance, clean baseline, ADR acceptance | baseline, brief, tracker, verification driver, ADRs 0007-0009, exact D14 inventory, early Windows/Ubuntu jobs | live remote default/protection/SHA/archive/PR state; clean source; full gates; metadata-only inventories | Explorer, Git Historian, Database Expert, Code Reviewer | Phase B closeout | PASS - technical source `3fff984`; evidence `2026-07-14-phase-c-c0.md` |
| C1 | Typed retention foundations behind C1A lockout | service/CLI/admin/Parquet/archive guards, query-only preview, retention tests, C1 evidence | preserve every guard; epoch/ISO boundaries; preview creates no artifact; archive failure means zero delete | Database, Data Migration, Security, Test Automator | C0 | PLANNED - NOT AUTHORIZED |
| C2 | Canonical AppPaths behind existing locations | frozen resolver, connection factory, deferred writable singletons, Compose/env docs, path tests | pure resolution; secure lock-parent sole exception then immediate lock; under-lock prep; no import/CWD/legacy mutation | Database, Deployment, Python, Test Automator | C1 | PLANNED - NOT AUTHORIZED |
| C3 | Read-only classifier, checkpoint, verified full backup | structural registry, maintenance service, strict `BackupManifestV1`, manifest-last publication, ACL evidence | unknown DB source untouched with only lock/marker/approved directories; checkpoint sole supported pre-backup mutation; WAL-only online row; integrity/FK; publication/DACL failures | Database, Data Migration, Security, Test Automator | C2 | PLANNED - NOT AUTHORIZED |
| C4 | Atomic canonical migrations and one schema owner | append-only manifest/ledger/application ID, self-contained migrations, reference seeds, checker/tests | every registered variant canonicalizes in one transaction after backup; checksum/order/future refusal; rollback | Data Migration, Database, Test Automator, Code Reviewer | C3 | PLANNED - NOT AUTHORIZED |
| C5 | Controlled import, journaled offline restore, export | import/restore service and offline CLI, opaque stage/status API, destination-local strict restore journal, tests/docs | exact state/file/hash recovery; malformed journal refusal; source preserved; every rollback/WAL-SHM barrier | Database, Data Migration, Security, Deployment | C4 | PLANNED - NOT AUTHORIZED |
| C6 | Historical fixture and migration/restore matrix | text SQL/builders for every structural variant, matrix CLI/tests, schema manifest | every supported classifier/version to latest; logical digest; latest no-op on Windows/Ubuntu | Test Automator, Data Migration, Git Historian | C3-C5 | PLANNED - NOT AUTHORIZED |
| C7 | Retention rewrite and dormant scheduler/status | typed policies, complete verified archives, maintenance ledger/disabled scheduler, health/tests | strict cutoff; atomic critical delete; stale RUNNING recovery; dormant overlap/restart; visibility | Database, Error Handler, Deployment, Test Automator | C3-C6 | PLANNED - NOT AUTHORIZED |
| C8 | Lifecycle/task/unclean-marker foundations | state/reason model, `OperationGate`, external marker, task registry, shutdown certificate/terminator | marker overrides provisional DB-clean state; clean vs safe-release proof; cancellation resistance; forced OS release; lock last | Python, Error Handler, Debugger, Order Execution, Test Automator | C3-C4 | PLANNED - NOT AUTHORIZED; FINAL INTEGRATION C9 |
| C9 | Durable intent, exact reconciliation, and lifecycle integration | intent model, single broker adapter, persistent fake broker, executor/recovery/readiness, opt-in scheduler activation | network-denied path; K02/K03 ambiguity subcases; C9-K01..K17; subscribe/snapshot convergence; external design/result reviews | Order Execution, Risk, Debugger, Database, Security, Test Automator | C4, C7-C8 | PLANNED - NOT AUTHORIZED; EXTERNAL REVIEW REQUIRED |
| C10 | Critical exception gate, redaction, bundle | inventory/checker, boundary sanitizer, fixed-entry bundle service, tests | no silent critical catch; seeded-secret byte scan at every boundary; bundle path/size safety | Error Handler, Security, Python, Test Automator | C2-C9 | PLANNED - NOT AUTHORIZED |
| C11 | Operator surface and docs | opaque backup/stage APIs, authenticated UI, product docs, expanded CI matrix, checkpoint evidence | no browser path/upload/apply/restart; contract/auth/UI; both-OS matrix; docs truth review | React/TS, UX, API, Deployment, Security | C5, C7, C9-C10 | PLANNED - NOT AUTHORIZED |
| C12 | Immutable global verification and owner closeout | candidate T, external review, evidence E, owner approval, closeout C, handoff/log/tracker | exact-T local/Windows/Ubuntu gates; T/E-named approval; successful CI on C | Quality, Code Reviewer, Security Auditor | C0-C11 | PLANNED - NOT AUTHORIZED |

## Finding Ownership

Lead-agent assignments above are non-exhaustive; the implementation brief owns
the complete supporting-agent list for each checkpoint.

| Finding | Owner checkpoint | Close condition |
|---|---|---|
| C-F01 incompatible retention timestamps | C1/C7 | destructive cleanup selects only truly expired rows for every encoding |
| C-F02 archive failure still deletes | C1/C7 | required archive failure guarantees zero deletion and visible failure |
| C-F03 relative paths/legacy ambiguity | C2/C5 | one resolver; explicit verified import; no CWD write or silent empty DB |
| C-F04 unversioned/partial migration | C3/C4/C6 | structural variant registry, classify/checkpoint/backup before write, one-transaction canonicalization, immutable ledger/checksums |
| C-F05 fragmented schema/PRAGMAs | C4 | one schema owner and consistent connection policy |
| C-F06 no full backup/restore | C3-C6 | verified manifested snapshot and offline rollback drill |
| C-F07 broker acceptance before identity | C9 | durable orderRef intent exists before submit and survives every kill barrier |
| C-F08 incomplete/nonblocking reconciliation | C8/C9 | reconciliation is readiness gate across orders/executions/positions |
| C-F09 split fill/exit crash state | C9 | idempotent atomic lifecycle and no duplicate exit |
| C-F10 unowned shutdown | C8/C9 | supervised tasks plus shutdown certificate; a live mutator forces process death rather than voluntary lock release |
| C-F11 no redaction/bundle boundary | C10 | unified filter and allowlisted bundle pass seeded-secret scans |
| C-F12 retention schedule/status incomplete | C7 | safe scheduler, maintenance ledger, operator degraded status |
| C-F13 broad exceptions stale/unclassified | C10 | critical inventory and blocking static gate |
| C-F14 fixtures/Windows CI missing | C0/C6/C11 | Windows job begins at C0; deterministic matrix and Ubuntu/Windows jobs pass |

## Owner Decision Register

| ID | Decision | Recommended disposition | Status |
|---|---|---|---|
| D1 | Supported schema floor | Explicit registry for both tagged histories and every recognized structural variant + formal C versions; unknown/ambiguous rejected | ACCEPTED 2026-07-14 |
| D2 | Native path | `%LOCALAPPDATA%\TradeBot`; exact ADR-0007 XDG split; explicit overrides | ACCEPTED 2026-07-14 |
| D3 | Legacy import | Explicit copy-and-verify; never delete source; ambiguity stops | ACCEPTED 2026-07-14 |
| D4 | Large legacy logs/cache | Leave by default; separate opt-in import | ACCEPTED 2026-07-14 |
| D5 | Canonical trade retention | Never automatically delete in first desktop release | ACCEPTED 2026-07-14 |
| D6 | Retention execution | Operator opt-in; whole critical run fails on archive/table error | ACCEPTED 2026-07-14 |
| D7 | Maintenance schedule | Daily 21:00 `America/New_York`; safe-state/off-market check still required | ACCEPTED 2026-07-14 |
| D8 | Restore | Offline apply only; online stage/validate/status | ACCEPTED 2026-07-14 |
| D9 | Backup privacy | Unencrypted until D; `pywin32` DACL allowing current user/SYSTEM/Administrators only; POSIX 0700/0600; broad roots fail closed | ACCEPTED 2026-07-14 |
| D10 | Ambiguous broker state | Block entries and require intervention; never auto-import/cancel/resubmit | ACCEPTED 2026-07-14 |
| D11 | Broker unavailable | API read-only/degraded with reconnect; not ready; no entries | ACCEPTED 2026-07-14 |
| D12 | Working orders on quit | Preserve and reconcile; do not silently cancel | ACCEPTED 2026-07-14 |
| D13 | Manual exit mismatch | Fresh broker verification plus explicit/unique DB position; ambiguity blocks | ACCEPTED 2026-07-14 |
| D14 | Exception scope | Exact 77-file inventory unioned with capability-trigger discovery | ACCEPTED 2026-07-14 |
| D15 | Diagnostic bundle defaults | Metadata/redacted logs only; exclude DB/trades/prompts/accounts | ACCEPTED 2026-07-14 |
| D16 | Shutdown budget | Reserved 5/10/5/5/5-second stages; current launcher/container >=45 seconds; certificate-gated release or forced process death; packaged D | ACCEPTED 2026-07-14 |
| D17 | Crash proof boundary | Network-denied persistent fake broker; all 17 C9-K01..K17 families in C; real sidecar/IBKR repeat in D/F | ACCEPTED 2026-07-14 |
| D18 | Temporary operator UI | Authenticated dashboard visibility/staging in C11; native actions in D | ACCEPTED 2026-07-14 |
| D19 | WAL checkpoint policy | Online backup needs no FULL checkpoint; pre-migration/clean stop require successful blocking checkpoint | ACCEPTED 2026-07-14 |
| D20 | Full-backup retention | No automatic deletion in C; protect last verified and every rollback artifact | ACCEPTED 2026-07-14 |
| D21 | Table retention policy | Owner-approved allowlist/periods for noncanonical data; canonical truth excluded | ACCEPTED 2026-07-14 |

## Evidence Rules

- C1A is recorded as a separately scoped safety-hotfix candidate, clean-source
  local proof, CI run, and dated evidence. Its PASS changes no C0-C12 status and
  grants no broader implementation authority.
- Every status transition needs a dated evidence document and exact source commit.
- C0 PASS is bound to merged technical source
  `3fff9846300beceacd77caf33834dc44d8fa69c7`, public post-merge run
  `29338942043`, and `2026-07-14-phase-c-c0.md`/`.json`.
- Evidence records live remote default/protection/master SHA, dependency versions,
  exact test/case IDs, unexpected skips/xfails, artifacts/hashes, failed runs,
  and owner-approval references in both machine-readable and short Markdown form.
- Preserve failed gates/runs and their disposition; do not overwrite history.
- Test only synthetic/temp databases and deterministic fake brokers.
- Never copy or inspect an operator database to create a fixture.
- Never treat JSONL retention export as full backup evidence.
- Never mark a checkpoint PASS on unit tests alone when it requires a process,
  Windows, crash, restore, or same-source CI drill.
- Codex subagent results are parallel internal reviews, never independent
  reviews. C9 requires a genuinely external design review before implementation
  and a genuinely external evidence review before PASS.
- `forced sidecar termination` remains a Phase D/F repetition requirement; C9
  proves the backend-process surrogate.
- Phase C closure requires candidate `T`, exact-`T` local/Windows/Ubuntu proof,
  external C9 review, evidence `E`, owner approval naming `T` and `E`, closeout
  `C`, and successful CI on `C`.

## Phase C Done Checklist

- [x] Phase B B12 owner closeout recorded.
- [x] Phase C ADRs and D1-D21 accepted.
- [x] C0 PASS recorded from clean exact source on Windows and Ubuntu.
- [ ] C0-C12 all PASS.
- [ ] Every C-F01-C-F14 close condition met.
- [ ] Full local Windows gates pass from clean source.
- [ ] Ubuntu and Windows same-source CI pass.
- [ ] Migration/restore matrix passes for every supported version.
- [ ] Fake-broker subprocess crash matrix proves no duplicate order.
- [ ] All 17 stable C9 case families and required subcases pass.
- [ ] Genuine external C9 design and result reviews are recorded.
- [ ] Logs and diagnostic bundle pass seeded-secret scans.
- [ ] Documentation matches current source and explicitly preserves D-F deferrals.
- [ ] Owner signs Phase C closeout.
- [ ] Phase D does not start automatically.

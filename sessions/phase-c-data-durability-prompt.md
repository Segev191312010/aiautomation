# TradeBot Phase C Implementation Brief

Topic: Data Durability and Runtime Hardening

Date: 2026-07-12

Status: ACCEPTED IMPLEMENTATION DESIGN - C0 PASS; C1-C12 NOT AUTHORIZED

Master plan: `docs/PHASE_C_ULTRAPLAN.md`, owner accepted 2026-07-14. The Phase C
tracker is the single table of truth. This brief defines implementation detail;
it does not authorize C1-C12.

## 1. Mission

Make SQLite, writable paths, maintenance, shutdown, and broker reconciliation
safe enough that crashes, upgrades, and restores do not silently lose, duplicate,
or abandon trading state.

Phase C is successful when:

- every supported database version upgrades deterministically;
- corruption, checksum drift, unknown schemas, and failed backups stop startup;
- existing operator data is never silently abandoned or overwritten;
- backup and restore are verified, atomic, and rollback-capable;
- destructive retention cannot delete recent or unarchived required data;
- runtime readiness is blocked until reconciliation proves a safe state;
- graceful shutdown owns tasks, persists/reconciles state, checkpoints, flushes,
  and only then releases runtime ownership;
- forced backend death at defined broker crash windows cannot create an automatic
  duplicate order;
- logs and diagnostic bundles exclude secrets and private trading context;
- the result passes Windows and Ubuntu clean-source gates with dated evidence.

## 2. Preconditions

Before C1-C12 code begins:

1. Record explicit Phase B B12 owner acceptance and close Phase B.
2. Change the public repository default branch to protected `master`, require
   the `master` CI checks, preserve the disconnected legacy `main` history at
   `archive/aiautomation-v2-2026-07-a10`, and triage its three open pull
   requests without merging the unrelated histories.
3. Commit an owner-approved Phase C planning artifact before implementation.
4. Record acceptance or revision of ADRs 0007-0009 and the C1-dependent
   decisions D5 and D6. Every other decision in section 10 must be accepted
   before its dependent checkpoint, and all D1-D21 must be resolved before C12.
5. Start a clean Phase C worktree/clone from an immutable `master` commit and
   verify the live remote default and `master` SHA rather than relying only on
   a cached remote-tracking ref.
6. Preserve the user's current `sessions/baselines/.gitkeep` and `files/skills`
   changes; do not stage them into Phase C.
7. Capture C0 clean-source gates and a metadata-only legacy path inventory.
8. Keep `AUTOPILOT_MODE=OFF`; use simulation and synthetic data by default.
   C9 is the only exception: it runs the broker-backed service with
   `SIM_MODE=false`, paper authority, and an injected persistent fake broker
   behind a hard outbound-network deny. The harness must assert that the fake
   adapter, not simulation or IBKR, is active. Never inspect or mutate an
   operator DB during development or tests.

Current disposition: Phase B is closed; protected `master` is default;
disconnected `main` and its archive tag remain preserved; PRs #1, #3, and #4
were closed unmerged; ADRs 0007-0009 and D1-D21 are accepted; and C1A passed at
`6093f0f` with evidence `1744bdb`. The planning record merged as `92fc971` and
C0 passed at merged technical source `3fff984` with post-merge CI run
`29338942043`. C1-C12 are not authorized.

## 3. Non-Negotiable Invariants

1. The runtime lock remains held before migration, broker, scheduler, alert, AI,
   reconciliation, and other stateful side effects.
2. The broker is authoritative for broker executions/positions; the DB is the
   canonical application interpretation, per ADR 0001.
3. No new entry is allowed unless lifecycle state is `READY` after successful
   reconciliation.
4. Unknown or contradictory data fails closed and creates a visible intervention;
   it is never guessed, imported, cancelled, or resubmitted automatically.
5. A broker order has a durable stable intent identity before submission.
6. Migrations are forward-only. Rollback means restoring the verified
   pre-migration backup with the compatible prior application version.
7. Existing data is copied and verified, never silently moved or deleted.
8. Full backup and retention JSONL/export are distinct products. A row export is
   never described as a full recovery backup.
9. Restore never replaces an active DB. The candidate is staged and verified,
   then applied offline/at controlled startup.
10. Every cleanup stage receives a reserved timeout and is attempted even if
    another stage fails. Runtime lock release is last, and every supported
    launcher/container grace period exceeds the backend cleanup budget.
11. Diagnostic bundles are allowlist-based and exclude the DB, `.env`, prompts,
    trades, portfolio details, account IDs, and credentials by default.
12. No Phase D-F work is pulled into Phase C to make an acceptance statement look
    complete.

## 4. Execution Plan

### C1A - Completed emergency retention lockout before C0

C1A was separately authorized and passed at implementation source
`6093f0f7d5f66489a2ed55e9f3998b2921b6cde5`, evidence source
`1744bdb94e0ff8fcf55ffa427e563444af16f002`, and CI run `29324523583`. It
hard-disables the cleanup API/service/CLI, preview/stats, table and Parquet
helpers, arbitrary backup-directory DELETE, diagnostics-news terminal-row
pruning, and startup terminal-candidate GC with no bypass.

Its zero-mutation proof covers every entry point, JSONL/non-JSONL sentinels,
absent-DB behavior, WAL/SHM, Parquet, and live terminal candidate states.
Queued/draining TTL expiration remains active as an execution-safety control.
C1A did not satisfy C0 or authorize C1-C12.

### C0 - Authorization, clean baseline, and ADR acceptance

Deliver:

- signed Phase B B12 closeout;
- clean Phase C source identity and CI link;
- current version/tool/gate evidence;
- metadata-only candidate legacy path inventory;
- schema/write-path/task/exception inventory;
- accepted ADRs 0007-0009;
- owner decisions recorded in the tracker.
- live evidence that GitHub defaults to protected `master`, its required CI is
  present, and the legacy `main` tip remains preserved by the archive tag;
- a checked-in cross-platform Python verification driver and initial focused
  Windows/Ubuntu persistence jobs so platform-specific gates start with C1,
  not at C11;
- the exact Phase C critical-module inventory used to prevent new unclassified
  exception debt from C1 onward, starting from
  `docs/release-evidence/2026-07-phase-c-critical-module-inventory.md`.

Agents: Explorer, Git Historian, Database Expert, Code Reviewer.

Gate: no production/runtime behavior changes. C0 may add only verification
tooling, tests, workflow configuration, machine policy data, and docs/evidence.

### C1 - Typed retention foundations behind C1A containment

Keep every C1A guard in place. Before broader refactoring:

- preserve the disabled service mutation, CLI `--execute`, authenticated admin
  cleanup, Parquet deletion, arbitrary backup-directory deletion, and both
  automatic terminal-row delete paths; expose preview/status only after each is
  genuinely existing-file read-only/query-only and zero-artifact;
- add typed ISO-8601 versus Unix-seconds cutoff handling;
- block every delete when required archive creation/verification fails;
- reject zero, negative, and unreasonable custom policies;
- prove recent integer timestamps are never selected;
- prove archive failure produces zero deletion;
- surface an explicit unavailable/degraded result rather than ordinary success.
- make preview use a query-only connection and prove it creates no DB, WAL,
  SHM, directory, archive, or cache artifact.

Do not redesign all retention yet. Destructive cleanup remains disabled after
C1 and cannot be re-enabled until C7 proves complete-row archives, table-class
policy, parent/child behavior, and owner-approved retention periods. Canonical
trade/order/audit truth remains permanently excluded from automatic deletion in
the first desktop release.

Agents: Database Expert, Data Migration, Security Auditor, Test Automator.

Gate: focused retention tests, full four gates, security review, dated C1 evidence.

### C2 - Canonical application paths without a default flip

Add one immutable `AppPaths` resolver for:

- data root and SQLite database;
- backups and restore staging;
- bar cache;
- event/application logs;
- diagnostic exports;
- runtime directory.

Specific overrides for tests/Compose remain supported. Native target paths use
`%LOCALAPPDATA%\TradeBot`. The exact POSIX proposal splits durable data, state,
cache, and runtime under `XDG_DATA_HOME`, `XDG_STATE_HOME`, `XDG_CACHE_HOME`, and
a valid private `XDG_RUNTIME_DIR`, with documented defaults and a state-runtime
fallback only when `XDG_RUNTIME_DIR` is unset. Path resolution adds no new
dependency; accepted D9 separately approves Windows-only `pywin32` for backup,
staging, and restore-control DACL enforcement.

Initially route code through the resolver while retaining the legacy location
chosen from a documented launch root. Resolve that compatibility location to an
absolute path once, record its provenance, and refuse ambiguous roots. No module
may consult the process CWD after `AppPaths` resolution. The native default does
not flip until C5 import/recovery is ready. Eliminate import-time cached writable
paths/default arguments and filesystem side effects. Packaged universe data
remains read-only source data.

Specific absolute resource overrides take precedence over `TRADEBOT_HOME`,
which in turn takes precedence over the platform default. Defer `EventLogger`,
simulation objects, database probes, and all writable singletons until after
the runtime lock and under-lock path preparation. The only pre-lock filesystem
mutation is idempotent secure creation/validation of the lock parent with its
private ACL, followed immediately by OS-lock acquisition; every other directory
or file operation waits under the lock. Startup validation must not create an
empty SQLite file while trying to discover legacy data.

Agents: Database Expert, Deployment, Python Backend Expert, Test Automator.

Gate: Windows/Linux path matrix; no post-resolution CWD lookup or path drift;
legacy data remains untouched. Final removal of CWD-selected compatibility is a
C5 gate.

### C3 - Integrity, checkpoint, and verified full-backup primitives

Create one maintenance coordinator used by migration, manual backup, update,
restore, and retention. Before a backup can claim a schema identity, add a
read-only structural inspector and registry that recognizes every supported
legacy variant and versioned schema from tables, columns/nullability/defaults/
primary keys, indexes, foreign keys, triggers, views, and selected normalized
constraints. Ignore only named SQLite internal objects; unknown application
objects fail closed.

The service then:

1. verify lifecycle/maintenance ownership;
2. classify the source without mutation and run `integrity_check` plus
   `foreign_key_check`;
3. apply the accepted operation-specific WAL policy, including a successful
   quiesced blocking checkpoint before a pre-migration backup;
4. use SQLite's online backup API to a destination-local `.partial` file;
5. integrity-check and reclassify the copied DB;
6. write a strict `BackupManifestV1` (`manifest_version=1`, unknown/missing
   fields rejected) with fixed product ID, backup ID, reason/UTC time, stable
   application version, schema kind/version/classifier/checksums, filename,
   size, SHA-256, integrity result, foreign-key result, and verification status;
7. publish the DB first and its matching manifest last as the commit marker.

Online manual backup does not require a FULL checkpoint and must prove committed
WAL-resident rows are included. Pre-migration and shutdown quiesce writers and
require a successful blocking checkpoint; busy/error fails closed. Readers list
only a final DB plus matching final manifest, so a crash between publications
leaves an ignored/quarantined orphan rather than a valid backup.

For a supported database that needs migration, the blocking checkpoint is the
sole permitted source mutation before backup verification. No schema, ledger,
seed, or application-row write is allowed before the backup. Unknown, future,
corrupt, or ambiguous sources are refused without even checkpointing them.

Agents: Database Expert, Data Migration, Security Auditor, Test Automator.

Gate: committed-WAL-row backup, concurrency, disk/lock/failure injection,
publication kill barriers, manifest/hash/integrity, and native ACL tests.

### C4 - Versioned migration engine and centralized schema ownership

Introduce:

- an explicit registry for the recognized pre-recovery, pre-Phase-A, current
  first-start, current restarted, and current fresh schema variants;
- an immutable, append-only ordered migration manifest with contiguous unique
  integer versions and no duplicate filenames or metadata;
- a generator with explicit `--write` and CI `--check` modes; the checker
  computes expected content in memory and never rewrites during verification;
- self-contained LF-normalized migration assets and SHA-256 checksums; a
  migration may not import mutable application helpers;
- `schema_migrations` ledger with version, name, checksum, applied UTC, stable
  app version, migration-engine version, apply kind, and classifier provenance;
- a fixed TradeBot `PRAGMA application_id`; environment `APP_VERSION` is not
  database identity;
- mirrored `PRAGMA user_version` as a secondary diagnostic, not sole authority;
- explicit classifier for every supported unversioned schema;
- future/unknown schema and checksum-drift refusal;
- centralized ownership of core, simulation, and diagnostics DDL;
- consistent connection initialization and required PRAGMAs.

Immutable required reference seeds, including built-in screener presets and the
diagnostics catalog, belong to checksummed migrations with deterministic IDs and
timestamps. The demo user and disabled AAPL starter rules are mutable onboarding
bootstrap, never migration history; they run separately and cannot overwrite
operator data.

Supported legacy classifiers explicitly allow only registered legacy
`application_id`/`user_version` header values. A structurally matching DB with a
foreign/nonzero unknown product ID or future version is rejected. Setting the
fixed application ID is part of the rollback-tested adoption transaction.

Remove `_safe_add_column()` and runtime DDL outside the migration owner. For an
existing DB the runner performs no schema, ledger, seed, or application-row
write until C3 has classified it, performed the sole allowed checkpoint, and
created a verified pre-migration backup. It then canonicalizes the recognized
source and applies every pending migration, ledger row, `application_id`, and
`user_version` update in one
runner-owned `BEGIN IMMEDIATE` transaction. Migration modules cannot commit,
rollback, vacuum, or change journal mode. Any failure rolls back the whole
upgrade, reopens and revalidates the source, and prevents broker/background
startup. A recognized legacy database is never stamped as though unexecuted DDL
had already run.

Agents: Data Migration (single schema writer), Database Expert, Test Automator,
Code Reviewer.

Gate: fresh/current/supported-legacy migration tests, checksum negative tests,
verified pre-migration backup assertion, schema invariant checker, full gates.

### C5 - Controlled legacy import, backup, restore, and export

Implement explicit copy-and-verify import:

- discover only approved candidates by metadata/schema;
- one unambiguous candidate may be selected explicitly;
- multiple candidates stop and require operator choice;
- the source remains untouched;
- the new destination is verified before use;
- import records a manifest and source metadata;
- large event logs/cache are separate opt-in imports.

Manual backup may run online. Restore may be staged/verified online but applies
only while the backend is stopped or at controlled pre-start maintenance:

- verify manifest/hash/application/schema/integrity;
- create a safety backup of the current DB;
- stage and atomically swap without active DB handles;
- migrate a supported older restore forward;
- automatically preserve/restore the original on failure;
- refuse traversal, symlink/reparse escape, active-runtime restore, and unknown
  backup types.

An external restore journal lives in a private destination-local restore-control
path derived from the selected DB parent, including when `DB_PATH` is explicitly
overridden. It uses
`PREPARED`, `OLD_PRESERVED`, `CANDIDATE_PROMOTED`, `POST_VALIDATED`, `COMMITTED`,
`ROLLBACK_REQUIRED`, and `ROLLED_BACK`. Startup resolves an incomplete journal
before opening the active DB. Restore closes every DB handle, handles stale
`-wal`/`-shm` sidecars, uses only destination-local promotion paths, verifies
all journal paths/hashes, and blocks startup if rollback cannot be proved.

The journal is a strict versioned record containing IDs/hashes, never trusted
arbitrary paths. Every state is atomically replaced and fsynced with its parent
directory only after the named file invariant is durable. Recovery accepts that
files may be one operation ahead of the journal, derives fixed names from the
stage ID, and uses hashes to choose the conservative deterministic action for
every state/file combination. `PREPARED` means verified candidate plus untouched
active DB; `OLD_PRESERVED` means the hashed prior DB is durably preserved;
`CANDIDATE_PROMOTED` means the candidate is at the active path while old remains;
`POST_VALIDATED` means the promoted DB passed all checks; `COMMITTED` finalizes
the new DB; rollback states require or prove restoration of the prior DB.
Malformed, unknown-version/state, hash-mismatched, or impossible combinations
block startup without deleting anything.

Until Phase D owns restart, the execution surface is an offline maintenance CLI
plus authenticated validation/status. Never implement a hot HTTP file swap.

Agents: Database Expert, Data Migration, Security Auditor, Deployment, Test
Automator.

Gate: complete restore negative matrix and rollback drill.

### C6 - Historical fixture and migration/restore matrix

Use deterministic text SQL/builders and synthetic sentinel records. Never commit
real operator data or binary DB copies.

Owner-approved supported floors:

- `v1.0-pre-recovery-20260408`;
- `v1.0-pre-phaseA-20260411`;
- unversioned Phase B schema;
- every formal Phase C migration version.

Text fixtures must separately represent the discovered current variants:
first-start simulation tables without `user_id`, restarted simulation tables
with nullable `user_id`, and current fresh rules/trades with `NOT NULL user_id`.
Each supported classifier converges on one canonical latest structural manifest;
raw `sqlite_master.sql` text is not used as the classifier.

For every supported `N -> latest` path, assert:

- logical row counts and canonical record digests;
- schema/table/index/constraint/foreign-key manifest;
- migration ledger and checksums;
- `integrity_check=ok` and empty `foreign_key_check`;
- latest no-op and repeated-run idempotency;
- representative trades, positions, simulation, rules/settings, AI ledger, and
  diagnostics survive.

Agents: Test Automator, Data Migration, Database Expert, Git Historian.

Gate: Windows and Ubuntu persistence matrix.

### C7 - Retention rewrite and dormant scheduler/status

Replace containment with a complete typed policy:

- explicit typed timestamp encoding and parsing per table;
- complete-row archives with a versioned manifest, hash, durable publication,
  and readback verification before any eligible source row is deleted;
- explicit parent/child handling;
- whole-run fail-closed atomicity for critical data;
- no `VACUUM` inside a transaction;
- canonical safety/trade truth is never automatically deleted;
- no automatic full-DB-backup deletion in Phase C;
- Parquet deletion and retention-archive deletion remain disabled throughout
  Phase C; C9 cannot activate either path. A later phase needs a separate typed,
  root-confined, private-permission, crash-tested file-retention policy;
- single-owner…579 tokens truncated…ock acquisition but before any DB
  mutation, atomically publish an
  external unclean marker under the runtime directory;
- one registry owns every TradeBot-created process-lifetime task/subscription;
  request-scoped work is owned and awaited by its handler;
- an `OperationGate` issues leases and atomically gates operations by state:
  new entries only in `READY`; positively
  owned cancellation and emergency controls may remain available while
  degraded; verified manual exit follows the C9 unambiguous-position rule;
- stop producers, drain/cancel tasks, detach/disconnect, write the DB shutdown
  result, checkpoint, flush logs, durably clear the external marker, and release
  the runtime lock last;
- treat DB `STOPPED_CLEAN` as provisional until its successful checkpoint, log
  flush, and durable marker clear. At next startup a surviving external marker
  always overrides a DB-clean row and forces reconciliation;
- produce a shutdown certificate with separate `clean_shutdown` and
  `safe_to_release` results;
  `RuntimeLock.release()` is allowed only when that certificate permits it;
- if a mutating task, broker callback, or adapter resists cancellation, attempt
  every later safe shutdown stage that remains possible, keep the unclean
  marker, flush diagnostics, and invoke an injected `ProcessTerminator`
  (`os._exit` in production) so only process death releases the OS lock;
- if every mutation-capable task, callback, broker adapter, request/operation
  lease, and DB handle is positively confirmed stopped but clean certification
  still fails, `safe_to_release` may be true while `clean_shutdown` is false; an
  explicitly recorded silent lock release is the final allowed fallback and the
  unclean marker remains for next-start recovery;
- outer cleanup must never unconditionally release the runtime lock, and final
  lock release performs no DB or log write after the final flush;
- reserve per-stage timeouts totaling 30 seconds (5/10/5/5/5), aggregate errors,
  and configure/test supported Compose/Uvicorn grace at least 45 seconds;
- make startup acquisition failure unwind every already-acquired resource.

C8 proves lifecycle/task/marker foundations only. Complete broker reconciliation,
final readiness, shutdown-reconcile ordering, scheduler activation, and the full
clean-shutdown acceptance are integrated and re-proved in C9.

Agents: Python Backend Expert, Error Handler, Debugger, Order Execution, Test
Automator.

Gate: exception, timeout, cancellation-resistant task, injected forced
termination, and hard-death marker boundaries; empty registry; no callback
leak; operation-transition races; certificate-gated
checkpoint/log/marker/lock ordering.

### C9 - Durable order intent and idempotent broker reconciliation

Add a migrated durable order-intent lifecycle:

- stable UUID committed before submit and sent as IBKR `orderRef`;
- commit the transition to `SUBMITTING` immediately before entering the raw
  adapter; the adapter cannot be called until that commit succeeds;
- account identity hash, client ID, broker order ID, `permId`, request payload,
  source, timestamps, and lifecycle state;
- uniqueness for `(account_hash, client_id, broker_order_id)`, account-scoped
  `permId`, and `(account_hash, execId)` in addition to the intent UUID;
- a client idempotency key whose exact-payload replay returns the original
  result and whose conflicting-payload reuse returns HTTP 409;
- idempotent submit/cancel/fill/position transitions;
- exact-set reconciliation across every unresolved DB intent, broker
  open/completed order, execution, broker position, and DB position; no bounded
  or sampled unresolved scan is acceptable;
- one deduplicated event subscription;
- close snapshot races by subscribing first, buffering/deduplicating events,
  taking a complete broker/DB snapshot, draining the buffer, and repeating full
  snapshot-plus-drain until two consecutive canonical digests are stable. If a
  broker watermark/sequence is available it is part of the fence; otherwise
  repeat-to-stability must converge within a timeout or remain `DEGRADED`;
- durable intervention and entry blocking for unknown/contradictory state;
- no automatic retry of ambiguous intent;
- no conversion/cancellation of unrelated external broker orders;
- one adapter boundary: only the real broker adapter may call raw broker
  place/cancel APIs. Every safety-kernel/emergency liquidation path must either
  create durable intent first or have automatic liquidation disabled while
  emergency authority-stop remains available;
- final integration of readiness/quiescing/shutdown reconciliation from C8;
- activation of C7 retention only when lifecycle/order-safe and off-market.

Manual exit requires an explicit DB position ID or exactly one unambiguous
matching open position, a freshly verified broker quantity, and a requested
quantity within both broker and linked DB quantity. Multiple/missing/mismatched
positions create an intervention; never infer FIFO, aggregation, or allocation.

Suggested intent states:
`CREATED`, `SUBMITTING`, `ACKNOWLEDGED`, `PARTIAL`, `FILLED`,
`CANCEL_REQUESTED`, `CANCELLED`, `REJECTED`, `ABORTED`, `UNKNOWN`.
`CREATED` may become `ABORTED` only when broker submit is proven not to have
been entered. Any ambiguity after `SUBMITTING` becomes `UNKNOWN`, persists an
intervention, blocks entries, and is never automatically resubmitted.

Agents: Order Execution, Risk Manager, Debugger, Database Expert, Security
Auditor, Test Automator.

Gate: run `test_order_crash_boundaries.py` against the exact broker-backed
services with `SIM_MODE=false`, paper authority, AI authority off, and a
persistent fake broker in a separate process (`AF_PIPE` on Windows, `AF_UNIX`
on POSIX). Before application import, deny `AF_INET`, `AF_INET6`, and DNS; prove
the deny with a negative-control self-test and assert the fake adapter identity.
At every C9-K01 through C9-K17 barrier prove no automatic duplicate,
lifecycle-specific DB state, and no ambiguous resubmission. A genuinely
external reviewer must approve the C9 design before implementation and review
the result before C9 can pass; parallel Codex reviews are internal only.

### C10 - Critical exception policy, log redaction, and diagnostic bundle

Inventory the defined critical module set. Classify each broad catch as:

- containment with telemetry/degraded state;
- fail-closed typed boundary;
- best-effort observability;
- prohibited trading/persistence swallow.

Add a static checker that blocks new unclassified broad catches, standalone
passes, and runtime DDL in durability modules. Do not mechanically rewrite all
279 `Exception`/`BaseException` handlers.

Unify logging around one sanitizer applied at capture and at every persistence,
health/API, and bundle boundary. Redact messages, arguments, extras,
exceptions/traces, URLs, health strings, and bundle inputs. Cover Bearer/JWT/session capability,
Anthropic keys, cookies/headers, account identifiers, prompts/context/reasoning,
and configured secret values.

Create a fixed-entry, allowlisted, bounded diagnostic bundle with versions, sanitized config,
lifecycle/reconciliation counts, schema/migration/integrity status, maintenance
status, redacted log tail, and manifest/hashes. Exclude DB/trades/portfolio/
prompts/accounts/secrets by default and reject traversal, symlinks/reparse points,
oversize input, and race substitution.

Agents: Error Handler, Security Auditor, Python Backend Expert, Test Automator.

Gate: critical exception inventory/checker and seeded-secret byte scan of logs
and bundle.

### C11 - Operator surface, documentation, and cross-platform CI

Expose authenticated, honest operations for:

- path/schema/migration/integrity status;
- create/list/verify backup;
- stage/validate restore by opaque backend backup ID and state that
  restart/offline apply is required;
- retention preview/policy/status;
- diagnostic bundle creation/status.

Dangerous actions use application modals and typed confirmation, never native
dialogs. Browser operations accept no arbitrary filesystem path or upload and
cannot hot-apply or restart. Restore staging returns
`current_database_changed=false`, `restart_required=true`, and
`offline_apply_required=true`; an offline CLI applies only an explicit stage
ID. The Phase B contract checker must cover new HTTP calls.

Canonical operations:

- `GET/POST /api/data-management/backups`;
- `POST /api/data-management/backups/{backup_id}/verify`;
- `POST /api/data-management/restore-stages` with opaque `backup_id` and typed
  confirmation;
- `GET/DELETE /api/data-management/restore-stages/{stage_id}`.

Retain the focused Ubuntu and Windows Python 3.12 jobs established at C0 and
expand their persistence/crash matrix while retaining the full
backend/dashboard gates. Publish current documentation:

- `docs/BACKUP_AND_RECOVERY.md`;
- `docs/DATA_AND_PRIVACY.md`;
- `docs/OPERATIONS.md`;
- updated backend architecture, security, development, deployment, roadmap, and
  dated README status.

Agents: React/TypeScript Expert, UX Reviewer, State Manager, API Designer,
Deployment, Security Auditor, Test Automator.

Gate: contract, auth, UI tests, Windows/Ubuntu matrix, docs review.

### C12 - Global verification and owner closeout

From a clean checkout:

- run every focused Phase C test and failure drill;
- run the migration/restore matrix CLI;
- run the backend subprocess crash harness;
- run full backend pytest;
- run dashboard typecheck/build/Vitest;
- run contract, hygiene, Phase C static/invariant, artifact/path, and whitespace
  gates;
- verify exact source identity and same-source Windows/Ubuntu CI;
- write technical completion evidence;
- obtain explicit owner acceptance;
- record closure in a later evidence-only commit and verify that CI.

The immutable closeout chain is: technical candidate commit `T`; exact-`T`
local Windows gates plus Windows/Ubuntu CI; genuine external C9 review;
technical evidence commit `E`; owner approval explicitly naming `T` and `E`;
then closeout commit `C` and successful CI on `C`. Internal agent passes are
recorded as parallel internal Codex reviews, never independent review.

Do not begin Phase D automatically.

## 5. Crash and Recovery Matrix

The deterministic fake broker runs outside the backend process and persists
accepted submissions. The harness must exercise the exact broker-backed service
path with `SIM_MODE=false`, paper authority, synthetic identity, and a hard
network deny; it aborts unless the injected fake adapter is active. Kill
barriers use 17 stable canonical family IDs; parameterized barriers retain a
stable suffix such as `C9-K09a` or `C9-K17g`:

1. `C9-K01` before intent persistence;
2. `C9-K02` after intent persistence: `K02a` before the `SUBMITTING` commit
   (submit provably not entered) and `K02b` after that commit but before adapter
   entry (conservatively `UNKNOWN` on restart);
3. `C9-K03` after adapter entry: `K03a` request dispatched/acceptance unknown,
   `K03b` submit timeout, and `K03c` broker accepted before ID persistence; all
   remain `UNKNOWN` until reconciliation and are never aborted or resubmitted;
4. `C9-K04` after broker ID persistence, before watcher/snapshot registration;
5. `C9-K05` during partial/final fill persistence;
6. `C9-K06` after fill persistence, before position registration;
7. `C9-K07` before exit-intent/pending-marker commit, proving zero broker
   submissions;
8. `C9-K08` after exit broker acceptance, proving the durable marker already
   exists;
9. `C9-K09` during cancel timeout/failure and after broker-confirmed
   cancellation before the DB transition (separate stable subcases);
10. `C9-K10` after broker rejection before the DB transition;
11. `C9-K11` during DB failure after broker acceptance;
12. `C9-K12` during repeated reconnect/status events;
13. `C9-K13` after partial reconciliation state but before run completion;
14. `C9-K14` at `READY -> QUIESCING` racing a new entry;
15. `C9-K15` at disconnect-triggered `READY -> RECONCILING` racing a new entry;
16. `C9-K16` when intervention persistence fails;
17. `C9-K17` during shutdown before/after reconciliation, DB shutdown-state
    write, checkpoint, log flush, external-marker clear, and lock release
    (separate stable subcases).

Also test broker unavailable, orderRef collision, reused broker order ID, external
manual TWS order/position, and contradictory DB/broker position state.
Inject broker events immediately before subscription, between every snapshot
source read, between snapshot and buffer drain, and during the second stability
snapshot; readiness must wait for the fenced digest to converge.

Acceptance: never more than one broker submission per intent. Entry fill creates
one trade and one linked open position; exit fill creates one exit trade and
removes the linked open position exactly once; partial-fill/cancel preserves the
exact executed quantity and one deterministic residual state. Unresolved
ambiguity remains visible and blocks new entries.

## 6. Migration, Backup, and Restore Matrix

Required negative cases:

- missing, duplicate, reordered, or checksum-tampered migration;
- unknown/newer schema;
- corrupt, locked, read-only, or foreign-key-invalid database;
- migration SQL, backup, checkpoint, disk, and atomic-swap failures;
- interrupted `.partial` artifact;
- committed rows present only in WAL;
- manifest/hash/application/schema mismatch;
- traversal, symlink/reparse escape, active-runtime restore;
- failed restore preserving the exact prior logical state;
- supported older restore automatically migrating forward.
- successful migration followed by restoration of its verified pre-migration
  backup and validation with the compatible prior application/source.

Compare logical digests, schema manifests, and constraints rather than raw SQLite
file bytes.

## 7. Edit and Review Protocol

- One writer owns shared schema/migration/order-state files at a time.
- Parallel agents use non-overlapping files or read-only reviews. Their output
  is labeled parallel internal Codex review, never independent review.
- After every five file edits: run all four quality gates, reread this brief, and
  check scope drift.
- Before every commit: read `LEARNED.md`; read `SOUL.md` before code; run all four
  gates; inspect tracked and untracked diff; run code review; run Security Auditor
  for migration/restore/order/auth/diagnostic changes.
- One logical checkpoint per conventional commit with a WHY body referencing
  Phase C.
- Each checkpoint gets dated evidence and tracker update.
- End with handoff, learning log, and wrap-up equivalent. Do not claim missing
  repository skills were invoked; `.agents/skills` currently does not exist.

## 8. Explicit Deferrals

- Real Tauri sidecar termination and native restart UI: Phase D/F.
- OS-backed encryption/key management for backups: Phase D unless owner expands
  Phase C.
- Installer/update rollback: Phase E.
- Real IBKR paper restart/soak and packaged application drills: Phase F.
- Dependency-wide security remediation, SBOM, accessibility, and coverage
  thresholds: later roadmap checkpoints.
- PostgreSQL and multi-user operation: outside first release.

## 9. Definition of Done

Phase C is done only when:

- C0-C12 tracker rows are PASS with immutable evidence;
- the immediate retention hazard is contained and fully rewritten;
- one canonical path and schema owner exists;
- every supported schema upgrades and restores successfully;
- unknown/tampered/corrupt inputs fail closed;
- pre-migration and manual backups are verified snapshots with manifests;
- restore is offline, staged, atomic, and rollback-capable;
- maintenance failures are visible;
- forced backend death at every defined crash window creates no duplicate order;
- lifecycle readiness and quiescing gates are enforced;
- every TradeBot-created process-lifetime task/subscription is owned and
  shutdown is failure-isolated;
- critical exceptions are classified and new silent catches are blocked;
- logs/bundles pass seeded-secret scans;
- full local and same-source Windows/Ubuntu gates pass;
- product/operator docs match the implementation;
- the owner signs the final policies and explicitly closes Phase C.

## 10. Accepted Owner Decisions

The tracker IDs below are canonical. The owner accepted ADRs 0007-0009 and all
D1-D21 on 2026-07-14. Acceptance fixes design policy but does not authorize
C1-C12 implementation:

1. **D1 - Schema floor:** support both named pre-Phase-A histories, every
   owner-recognized structural variant of them, every recognized unversioned
   Phase B variant, and all formal C versions; reject older/unknown layouts.
2. **D2 - Native path:** `%LOCALAPPDATA%\TradeBot` plus ADR 0007's exact
   POSIX/XDG data/state/cache/runtime split, with explicit test/dev/Compose
   overrides.
3. **D3 - Legacy DB import:** explicit copy-and-verify; never move/delete; stop
   for ambiguity.
4. **D4 - Large logs/cache:** leave them in place unless separately selected.
5. **D5 - Canonical truth retention:** never automatically delete trade, order,
   execution, intervention, AI audit/decision, or authority truth in the first
   desktop release.
6. **D6 - Destructive retention:** operator opt-in; whole critical run fails on
   any archive/table error.
7. **D7 - Maintenance schedule:** daily at 21:00 `America/New_York`, after the
   normal US equities extended-hours window, with the
   independent safe-state/off-market check still required before activation.
8. **D8 - Restore:** offline apply only; online validate/stage/status; native
   restart remains Phase D.
9. **D9 - Backup privacy:** accept unencrypted local SQLite protected by enforced
   private per-user/platform permissions until Phase D; approve `pywin32` as the
   recommended Windows DACL implementation (current user, SYSTEM, and
   Administrators only), with POSIX `0700`/`0600`; broadly writable, null, or
   inherited-write roots fail closed with no acknowledgement bypass.
10. **D10 - Ambiguous broker state:** block entries and require intervention;
    never auto-import/cancel/resubmit.
11. **D11 - Broker unavailable:** API read-only/degraded with reconnect, not
    ready and no new entries.
12. **D12 - Working orders on quit:** preserve/reconcile; never silently cancel.
13. **D13 - Manual exit:** require fresh broker quantity plus explicit or unique
    DB position; ambiguity blocks and creates an intervention.
14. **D14 - Exception scope:** the exact accepted 77-file inventory in
    `docs/release-evidence/2026-07-phase-c-critical-module-inventory.md`, unioned
    with filename-independent capability triggers, not every broad handler.
15. **D15 - Diagnostic bundle:** metadata/redacted logs only by default; exclude
    DB, trades, portfolio, prompts, and account identifiers.
16. **D16 - Shutdown budget:** reserved 5/10/5/5/5-second backend stages (30
    seconds total), current supported launcher/container grace at least 45
    seconds, packaged enforcement in D.
17. **D17 - Crash proof:** all 17 stable `C9-K01` through `C9-K17` families use
    an injected network-denied persistent fake broker/backend process in C;
    actual sidecar and IBKR paper repetition remains in D/F.
18. **D18 - Temporary UI:** authenticated dashboard visibility/staging in C11;
    native actions in D.
19. **D19 - WAL policy:** online manual backup uses the backup API without a
    required FULL checkpoint; pre-migration and clean shutdown require quiesced
    successful blocking checkpoint, with busy/error failing closed.
20. **D20 - Full-backup retention:** no automatic full-DB-backup deletion in C;
    manual deletion cannot remove the last verified or any required rollback
    artifact, and storage pressure is visible.
21. **D21 - Table retention:** approve the C7 class/period table for
    noncanonical derived/diagnostic data; destructive execution stays off until
    those periods and D5-D7 are accepted and tested.

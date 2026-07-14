# Phase C Verification Manual

Date created: 2026-07-12

Status: ACCEPTED VERIFICATION PLAN - C0 AUTHORIZED; PLANNED COMMANDS FOR
C1-C12 ARE NOT IMPLEMENTED OR AUTHORIZED

Master plan: `docs/PHASE_C_ULTRAPLAN.md` (owner accepted 2026-07-14).
The Phase C tracker is the single table of truth.

Mission: prove from clean source that paths, migrations, backups, restores,
retention, shutdown, reconciliation, logs, and support bundles fail closed and
preserve trade truth.

## 1. Safety Boundary

Never run Phase C verification against an operator database, broker account,
ignored `.env`, or existing application-data directory.

Default environment:

- `SIM_MODE=true`;
- `AUTOPILOT_MODE=OFF`;
- unique temporary `TRADEBOT_HOME`/`DB_PATH` per test;
- no real IBKR or Anthropic network call;
- no destructive retention command outside test-owned temporary data.

Set these values before importing the application or invoking any test or
verification process. The cross-platform Phase C verification driver owns the
temporary root, rejects an existing/non-temporary root, and deletes only the
root it created.

C9 is an isolated exception to `SIM_MODE=true`. Its subprocess runs with
`SIM_MODE=false`, paper authority, synthetic identity, an injected persistent
fake broker, and an OS/test-harness outbound-network deny. The harness must
assert the fake-adapter identity and abort if simulation or the IBKR adapter is
active.

The C1A guards reject destructive retention, CLI `--execute`, admin cleanup,
preview/stats, Parquet cleanup, backup-directory DELETE, diagnostics-news
pruning, and terminal-candidate GC before storage mutation. Do not bypass or
remove those guards as verification; queued/draining candidate TTL expiration
remains an execution-safety control.

### 1.1 Completed emergency C1A proof

C1A was separately authorized and passed at implementation commit
`6093f0f7d5f66489a2ed55e9f3998b2921b6cde5`, evidence commit
`1744bdb94e0ff8fcf55ffa427e563444af16f002`, and CI run `29324523583`.
It did not open C0-C12. Its clean-source evidence proves:

- API cleanup and preview, service mutation, CLI `--execute`, table cleanup,
  Parquet cleanup, arbitrary named-file deletion in the shared backup directory,
  diagnostics-news pruning, and startup candidate GC are all disabled;
- stats is disabled or uses an existing-file read-only/query-only connection;
- no test changes rows, creates a DB/WAL/SHM/directory, unlinks Parquet, or
  changes JSONL/non-JSONL backup sentinels;
- the live `applied`/`failed`/`expired` candidate vocabulary is covered;
- focused tests and all four global gates pass on the same clean source.

The distinct C1A chain remains historical proof. Its PASS granted no Phase B,
governance, ADR, decision, C0, or broader implementation authority.

## 2. C0 Clean-Source Preflight

Use a fresh worktree or clone. The owner's current worktree contains preserved
instruction-file changes and is intentionally unsuitable for clean C0 evidence.

During C0, re-verify from the live GitHub API rather than cached refs that:

- the repository default branch is `master`;
- `master` is protected and requires the named backend/dashboard CI checks;
- the disconnected legacy `main` tip remains preserved at the approved archive
  tag;
- PRs #1, #3, and #4 remain closed unmerged with their explicit triage
  disposition and no unrelated-history merge is planned;
- Phase B B12 and an owner-approved Phase C planning artifact are committed.

Install focused Windows and Ubuntu Python 3.12 jobs at C0. One checked-in
cross-platform Python verifier is the source of truth for environment setup,
case IDs, expected artifacts, skips/xfails, and pass/fail. PowerShell or shell
entry points may be thin wrappers only.

The verifier checks path existence/metadata only and refuses the checkout if an
ignored operator `.env`, operational DB/WAL/SHM, runtime data directory, or
other known live artifact is present. It never opens those files to inspect
contents. Both schema/crash CLIs must have negative tests proving they reject
repository, app-data, and other non-owned roots.

```powershell
$phaseCRoot = Join-Path ([System.IO.Path]::GetTempPath()) `
    ("tradebot-phase-c-" + [guid]::NewGuid().ToString("N"))
$env:SIM_MODE = "true"
$env:AUTOPILOT_MODE = "OFF"
$env:TRADEBOT_HOME = $phaseCRoot
$env:DB_PATH = Join-Path $phaseCRoot "data\tradebot.db"

$dirty = git status --porcelain=v1 --untracked-files=all
if ($LASTEXITCODE -ne 0) { throw "git status failed" }
if ($dirty) {
    throw "Phase C verification requires a clean checkout"
}

$head = git rev-parse HEAD
if ($LASTEXITCODE -ne 0) { throw "git rev-parse HEAD failed" }
$remote = git rev-parse origin/master
if ($LASTEXITCODE -ne 0) { throw "git rev-parse origin/master failed" }
if ($head -ne $remote) { throw "initial clean base and origin/master differ" }

python --version
if ($LASTEXITCODE -ne 0) { throw "python version check failed" }
node --version
if ($LASTEXITCODE -ne 0) { throw "node version check failed" }
npm.cmd --version
if ($LASTEXITCODE -ne 0) { throw "npm version check failed" }
git --version
if ($LASTEXITCODE -ne 0) { throw "git version check failed" }
```

The equality above applies to the initial clean C0 base. A C0 candidate branch
must instead prove clean candidate `HEAD` equals its pushed remote branch, then
record exact merged-`master` identity and same-source CI after protected merge.

Record OS, SQLite runtime version, FastAPI, Pydantic, aiosqlite, pytest, React,
TypeScript, Vite, and Vitest versions.

## 3. Baseline Gate

Before each checkpoint and before every commit:

```powershell
$phaseCRoot = Join-Path ([System.IO.Path]::GetTempPath()) `
    ("tradebot-phase-c-" + [guid]::NewGuid().ToString("N"))
$env:SIM_MODE = "true"
$env:AUTOPILOT_MODE = "OFF"
$env:TRADEBOT_HOME = $phaseCRoot
$env:DB_PATH = Join-Path $phaseCRoot "data\tradebot.db"

python -m pytest backend/tests -v
if ($LASTEXITCODE -ne 0) { throw "backend pytest failed" }

Push-Location dashboard
try {
    npm.cmd run typecheck
    if ($LASTEXITCODE -ne 0) { throw "typecheck failed" }
    npm.cmd run build
    if ($LASTEXITCODE -ne 0) { throw "build failed" }
    npx.cmd vitest run
    if ($LASTEXITCODE -ne 0) { throw "vitest failed" }
} finally {
    Pop-Location
}

python backend/scripts/check_contract_frontend_vs_openapi.py
if ($LASTEXITCODE -ne 0) { throw "contract check failed" }
python scripts/check_workspace_hygiene.py
if ($LASTEXITCODE -ne 0) { throw "workspace hygiene failed" }
git diff HEAD --check
if ($LASTEXITCODE -ne 0) { throw "git diff check failed" }
```

Every five edited files also triggers this gate plus a reread of the Phase C
brief. A failure blocks further checkpoint work and commits.

The verification driver must fail unexpected skips, xfails, xpasses, missing
case IDs, and empty test selection. Any intentional skip/xfail needs an
owner-reviewed allowlist entry with an expiry and is reported as an open gate,
not silently counted as PASS.

## 4. C1 Retention Foundations After C1A

Planned focused test file:
`backend/tests/test_retention.py`.

The C1A guards remain in place throughout C1. Required cases:

- recent and expired ISO-8601 timestamps;
- recent and expired Unix-second integer timestamps;
- timezone and exact-cutoff boundaries;
- zero, negative, and excessive custom policies rejected;
- required archive open/write/flush/hash/verify failure means zero delete;
- service mutation, CLI `--execute`, authenticated admin cleanup, Parquet
  deletion, arbitrary backup-directory deletion, diagnostics-news pruning,
  startup candidate GC, and every other destructive entry point are disabled
  through C7 and return explicit unavailable/degraded state;
- preview uses a SQLite query-only connection and has no DB, WAL, SHM,
  directory, archive, cache, or other file mutation;
- stats is disabled or query-only/no-create with the same zero-artifact proof;
- direct-candidate tests use `applied`/`failed`/`expired`, the live terminal
  states, rather than the stale retention predicate vocabulary;
- successful-but-incomplete archive cannot re-enable deletion;
- no operational DB/path is addressable by the test.

Planned command:

```powershell
python -m pytest backend/tests/test_retention.py -v
if ($LASTEXITCODE -ne 0) { throw "C1 retention tests failed" }
```

## 5. C2 Application-Path Matrix

Planned files:

- `backend/app_paths.py`;
- `backend/tests/test_app_paths.py`.

Required cases:

- Windows `%LOCALAPPDATA%` layout;
- Linux/XDG development/CI layout;
- explicit root and specific path overrides;
- Compose path behavior;
- missing environment roots;
- relative override policy;
- unwritable root and file-versus-directory collision;
- symlink/reparse escape where applicable;
- legacy compatibility is resolved once to an absolute documented root and no
  module consults CWD afterward;
- final native/default path after C5 has no CWD dependency;
- mixed `DB_PATH`/root/specific overrides have deterministic backup, staging,
  runtime, and child-escape behavior;
- packaged `backend/data/universes` remains source asset;
- legacy root/backend candidate ambiguity stops;
- original legacy files remain unchanged.
- pure resolution performs no filesystem or DB write;
- secure idempotent creation/validation of the private lock parent is the only
  permitted pre-lock mutation and is followed immediately by lock acquisition;
- directory creation/permission enforcement and every writable singleton occur
  only after the lock is held;
- importing modules such as `bot_runner` creates no event-log directory;
- startup validation does not probe/create an empty database before lock and
  path ownership;
- every runtime connection uses the canonical factory and no default argument
  or cached config value retains a relative/stale DB path.

Planned command:

```powershell
python -m pytest backend/tests/test_app_paths.py -v
if ($LASTEXITCODE -ne 0) { throw "C2 app-path tests failed" }
```

## 6. C3-C6 Migration, Backup, Restore, and Fixture Matrix

Planned test modules:

- `backend/tests/test_migrations.py`;
- `backend/tests/test_backup_restore.py`;
- `backend/tests/test_legacy_data_import.py`;
- `backend/tests/test_schema_matrix.py`.

Planned fixture roots contain text SQL/builders only. Force migration SQL/assets
to LF in `.gitattributes` before checksums become authoritative.

### Migration matrix

- fresh empty DB to latest;
- each owner-supported tagged schema variant to latest, including the historical
  table/index, simulation `user_id`, and rules/trades nullability variants in
  the fixture registry;
- unversioned Phase B schema to latest;
- every formal migration version `N` to latest;
- latest no-op and repeated-run idempotency;
- preserve representative trades, positions, simulation, rules/settings, AI
  ledger, and diagnostics through logical digests and row counts;
- exact required table/index/constraint/FK manifest;
- a fixed SQLite `application_id` plus migration
  ledger/checksum/user-version agreement; environment `APP_VERSION` is not a
  database identity;
- versioned backups prove ledger/checksum/schema-manifest agreement;
- supported unversioned backups prove a strict structural classifier ID plus a
  reproducible normalized schema manifest, with migration history not
  applicable. Classification checks columns, null/default/PK shape, indexes,
  FKs, triggers, views, and constraints while ignoring only named SQLite
  internals; raw `sqlite_master` text is not the classifier;
- each supported unversioned fixture has a verified backup before first mutation;
- `integrity_check=ok` and empty `foreign_key_check`.
- startup ordering is read-only classify and integrity/FK validation, then a
  successful quiesced blocking checkpoint, then verified pre-migration backup,
  then the first schema/ledger/seed/application-row write. The checkpoint is the
  sole pre-backup source mutation and runs only after supported classification;
- classification and stamping are separate: every recognized variant receives
  the required canonicalizing DDL/data migration, never a retroactive stamp;
- all pending migrations and initial `schema_migrations` creation execute in one
  runner-owned `BEGIN IMMEDIATE` transaction and roll back as a unit;
- the same transaction adopts the fixed TradeBot `application_id`; legacy
  classifiers explicitly allow only registered `application_id`/`user_version`
  headers and reject foreign nonzero or future identities;
- migrations are self-contained and cannot import mutable runtime helpers,
  commit, roll back, `VACUUM`, or change journal mode;
- append-only migration manifest entries are contiguous and unique; missing,
  reordered, duplicate, or checksum-drifted entries fail both generator
  `--check` and runtime validation;
- ledger rows contain version, name, checksum, applied UTC, stable app version,
  engine version, apply kind, and classifier provenance;
- built-in screener presets and the diagnostics catalog are deterministic
  migration reference data; demo user creation is idempotent post-migration
  bootstrap; disabled AAPL/demo starter rules are optional operator onboarding
  whose failure degrades onboarding but does not falsify schema success.

Negative cases:

- missing/duplicate/reordered/checksum-tampered migration;
- unknown/newer schema;
- corrupt, locked, read-only, or FK-invalid DB;
- SQL/disk/commit failure with schema and ledger rollback;
- rerun after interrupted `.partial`/failed migration;
- seed failure is distinguishable from schema failure.
- immutable reference seeds are migration-owned; mutable/demo/operator defaults
  are separate idempotent post-migration work with tested fail/degraded policy.
- unknown, future, corrupt, or ambiguous databases receive no checkpoint or
  source-DB mutation and create no DB/WAL/SHM/ledger/backup artifact; the secure
  lock parent, external marker, and approved under-lock directories are expected
  lifecycle artifacts and must still be proved private/root-confined.

### Backup matrix

- committed rows still in WAL appear in the backup;
- concurrent snapshot is consistent;
- online backup includes committed WAL-only rows without requiring FULL
  checkpoint;
- pre-migration and clean-shutdown blocking checkpoint busy/error fails closed;
- destination open/write/disk failure leaves no final artifact;
- `.partial` artifacts and orphan final DB/manifest files are never listed as
  valid backups;
- kill barriers before/between/after DB-final then manifest-final publication;
- strict `BackupManifestV1` includes `manifest_version=1`, fixed product ID,
  backup ID, reason/time, stable
  app version, schema kind/version/classifier, migration checksums, size,
  SHA-256, integrity/FK results, verification status, and filename, but no row
  data; missing, unknown, or unsupported-version fields are rejected;
- copied DB passes integrity and FK checks;
- backup preserves schema ledger, indexes, triggers, sequences, and logical rows;
- backup retention never removes the only pre-migration/rollback artifact.
- native and explicit backup roots are current-user private/platform-equivalent;
  broad write access fails closed with no acknowledgement bypass and is covered
  by Windows integration tests.
- publication fsyncs and atomically publishes the final DB first and the final
  manifest last; a DB without its final manifest is an invalid orphan;
- Windows DACL permits only the current user, SYSTEM, and Administrators; broad,
  null, or inherited write access fails closed. POSIX roots/files enforce
  `0700`/`0600` equivalents.

SQLite documents the online backup API as producing a consistent destination
snapshot while permitting incremental access:
https://www.sqlite.org/backup.html

### Restore/import matrix

- manifest/hash/application/schema mismatch rejected;
- corrupt/truncated/FK-invalid backup rejected before swap;
- traversal, alternate extension, symlink/reparse, and race replacement rejected;
- active-runtime restore rejected;
- multiple legacy candidates require selection;
- legacy source is never deleted or modified;
- current DB safety backup exists before swap;
- injected swap/post-swap validation failure returns to the prior logical state;
- supported older restore migrates forward;
- successful migration can restore its pre-migration backup and validate it with
  the compatible prior application/source;
- large logs/cache are not imported without explicit selection.
- before opening a database, startup resolves the strict versioned external
  restore journal from a private destination-local control path derived from
  the selected DB parent, plus stale `-wal`/`-shm` state;
- journal transitions are exactly `PREPARED`, `OLD_PRESERVED`,
  `CANDIDATE_PROMOTED`, `POST_VALIDATED`, `COMMITTED`, `ROLLBACK_REQUIRED`, and
  `ROLLED_BACK`; rollback failure blocks startup rather than guessing;
- restore uses an opaque verified backup/stage ID, never a browser-provided path,
  and only the offline CLI applies an explicit stage ID.
- journal records contain IDs/hashes and fixed derived names, never trusted
  arbitrary paths. Each state is atomically replaced/fsynced with the parent
  directory after its named file invariant is durable; recovery permits files
  one operation ahead, verifies exact hashes, and deterministically rolls back
  or completes every state/file combination. Malformed, unknown-version/state,
  hash-mismatched, or impossible combinations block without deletion.

Planned commands:

```powershell
python -m pytest `
    backend/tests/test_migrations.py `
    backend/tests/test_backup_restore.py `
    backend/tests/test_legacy_data_import.py `
    backend/tests/test_schema_matrix.py -v
if ($LASTEXITCODE -ne 0) { throw "C3-C6 persistence tests failed" }

python backend/scripts/run_phase_c_schema_matrix.py --ephemeral --json
if ($LASTEXITCODE -ne 0) { throw "C3-C6 schema matrix failed" }
```

The matrix CLI must refuse non-temporary/output paths unless an explicit test
flag and verified temporary root are present.

## 7. C7 Retention Scheduler and Status

Required cases:

- typed timestamp encodings for every table;
- complete archive rows plus versioned manifest/hash/fsync/readback proof before
  eligible deletion;
- parent/child behavior and critical whole-run rollback;
- no canonical trade auto-delete under default policy;
- archive failure means zero critical deletion;
- `VACUUM` runs only outside a transaction;
- one scheduler owner, no overlap;
- scheduler remains disabled through C7;
- restart recovers last/next status and converts stale `RUNNING` ledger rows to
  `INTERRUPTED`;
- failed/overdue maintenance creates visible degraded health;
- no automatic full-DB-backup deletion; canonical truth is never auto-deleted;
- noncanonical periods match owner-approved D21 policy.
- Parquet and retention-archive deletion remain disabled throughout Phase C;
  C9 cannot activate them without a later separately accepted typed,
  root-confined, permission-checked, crash-tested file-retention policy.

After C9 activation, unsafe lifecycle/orders must cause a recorded skip rather
than deletion. Activation requires explicit operator opt-in, maintenance lease,
current successful reconciliation, and zero nonterminal order intents; schedule
time alone is insufficient. Re-run this entire section as part of the C9 gate.

Planned command:

```powershell
python -m pytest `
    backend/tests/test_retention.py `
    backend/tests/test_retention_scheduler.py `
    backend/tests/test_maintenance_status.py -v
if ($LASTEXITCODE -ne 0) { throw "C7 retention/status tests failed" }
```

## 8. C8 Lifecycle and Shutdown Matrix

Planned modules:

- `backend/tests/test_runtime_lifecycle.py`;
- `backend/tests/test_task_supervisor.py`;
- `backend/tests/test_clean_shutdown.py`.

Inject an exception and timeout independently into every acquisition/cleanup
stage, plus cancellation resistance, injected process termination, and hard
death around the external marker/checkpoint boundaries. Verify:

- lifecycle transitions are persisted and truthful;
- no new entry outside `READY`;
- every later cleanup stage is still attempted;
- all tasks are drained/cancelled and registry is empty;
- event callbacks do not accumulate across same-process restarts/reconnects;
- the external unclean marker is durably published after lock/path validation
  and before DB mutation;
- a shutdown certificate explicitly controls `safe_to_release`, and neither
  `RuntimeLock.release()` nor an outer `finally` can bypass it;
- operation/state transitions are atomic for entry, owned cancellation,
  emergency controls, verified manual exit, maintenance, and reads;
- DB shutdown result precedes the final required checkpoint, which precedes log
  flush and durable external-marker clear;
- DB `STOPPED_CLEAN` is provisional until checkpoint, log flush, and marker
  clear succeed; a surviving marker always overrides the DB row on startup;
- the certificate records `clean_shutdown` separately from `safe_to_release`;
- runtime lock releases after cleanup, never before;
- failed startup unwinds every acquired resource;
- unclean marker remains when critical shutdown did not finish;
- a still-live mutating task/broker callback triggers the injected
  `ProcessTerminator` (`os._exit` in production) so OS process death, not
  voluntary cleanup, releases the lock;
- if all mutation-capable resources are stopped but clean certification fails,
  including every task, callback, adapter, request/operation lease, and DB
  handle, the only permitted voluntary fallback is `safe_to_release=true`,
  `clean_shutdown=false`, and a recorded silent release with the unclean marker
  retained;
- no DB or log write occurs after final flush and lock release;
- per-stage 5/10/5/5/5-second reservations allow every later stage to be
  attempted, and supported Compose/Uvicorn grace is at least 45 seconds.

C8 does not pass final broker reconciliation/shutdown integration. C9 must
re-run these tests with reconciliation before broker disconnect and scheduler
activation safety.

Planned command:

```powershell
python -m pytest `
    backend/tests/test_runtime_lifecycle.py `
    backend/tests/test_task_supervisor.py `
    backend/tests/test_clean_shutdown.py -v
if ($LASTEXITCODE -ne 0) { throw "C8 lifecycle tests failed" }
```

## 9. C9 Fake-Broker Crash and Reconciliation Drill

The fake broker must persist accepted submissions outside the backend process,
using `AF_PIPE` on Windows and `AF_UNIX` on POSIX. Before application import,
deny `AF_INET`, `AF_INET6`, and DNS, prove the deny with a negative-control
self-test, and assert that the fake adapter - not simulation or IBKR - is active.
Run the exact broker-backed services with `SIM_MODE=false`, paper authority, AI
authority off, and synthetic identity. Terminate the backend at the 17 stable
families below; parameterized barriers retain stable suffixes:

1. `C9-K01` before intent persistence;
2. `C9-K02` after intent persistence: `K02a` before the committed `SUBMITTING`
   transition and `K02b` after that commit but before adapter entry;
3. `C9-K03` after adapter entry: `K03a` dispatched/acceptance unknown, `K03b`
   submit timeout, and `K03c` accepted before broker ID persistence;
4. `C9-K04` after broker ID persistence, before watcher/snapshot registration;
5. `C9-K05` during partial/final fill persistence;
6. `C9-K06` after fill persistence, before position registration;
7. `C9-K07` before exit-intent/pending-marker commit, proving zero broker
   submissions;
8. `C9-K08` after exit acceptance, proving the durable marker already exists;
9. `C9-K09` during cancel timeout/failure and after broker-confirmed
   cancellation before DB transition;
10. `C9-K10` after broker rejection before DB transition;
11. `C9-K11` during DB failure after broker acceptance;
12. `C9-K12` during repeated reconnect/status events;
13. `C9-K13` after partial reconciliation commit before run completion;
14. `C9-K14` during `READY -> QUIESCING` versus a new entry;
15. `C9-K15` during disconnect-triggered `READY -> RECONCILING` versus entry;
16. `C9-K16` during intervention persistence failure;
17. `C9-K17` before/after shutdown reconciliation, DB shutdown-state write,
    checkpoint, log flush, external-marker clear, and lock release.

Also inject partial-fill cancellation, reused broker order IDs, orderRef
collision, broker unavailability, external TWS-like orders/positions, and
contradictory DB/broker position state.

The fixture and schema must prove uniqueness for intent UUID,
`(account_hash, client_id, broker_order_id)`, account-scoped `permId`, and
`(account_hash, execId)`. Intent states include `ABORTED`: only `CREATED` may
abort when submit is proven not entered; ambiguity after `SUBMITTING` becomes
`UNKNOWN`, persists intervention, blocks entries, and is never resubmitted.
The adapter is callable only after `SUBMITTING` commits. `K02b` and every K03
subcase are conservatively `UNKNOWN` on restart, never `ABORTED` or retried.

Reconciliation subscribes before its first snapshot, buffers and deduplicates
events, reads the complete broker/DB set, drains the buffer, and repeats until
two consecutive canonical digests are stable. Use a broker watermark/sequence
when available; otherwise failure to converge before timeout remains
`DEGRADED`. Inject events before subscription, between snapshot source reads,
between snapshot and drain, and during the second stability snapshot.

For every case assert:

- at most one broker submission per intent;
- entry fill creates exactly one trade and one linked open position;
- exit fill creates exactly one exit trade and removes the linked position once;
- partial-fill/cancel records exact executed quantity and one residual state;
- no automatic resubmission of ambiguity;
- one event subscription and idempotent transitions;
- durable intervention and blocked entry until reconciliation succeeds;
- unrelated external orders are not mutated;
- all unresolved intents are reconciled as an exact set, never through a
  bounded/sampled scan;
- only the broker adapter invokes raw place/cancel APIs; emergency liquidation
  either has durable intent or is disabled while emergency authority-stop stays
  available;
- exact idempotency-key/payload replay returns the original result and
  conflicting reuse returns HTTP 409;
- reconciliation precedes broker disconnect during clean shutdown;
- C7 scheduler activates only in an accepted safe/off-market state and records
  a skip otherwise.

Planned commands:

```powershell
python -m pytest `
    backend/tests/test_order_intents.py `
    backend/tests/test_reconciliation.py `
    backend/tests/test_order_crash_boundaries.py -v
if ($LASTEXITCODE -ne 0) { throw "C9 order/reconciliation tests failed" }

python backend/scripts/run_phase_c_crash_drill.py --ephemeral --network-deny --json
if ($LASTEXITCODE -ne 0) { throw "C9 crash drill failed" }
```

This proves backend-process termination. Repeat against the actual packaged
sidecar in Phase D/F; do not claim literal sidecar proof in Phase C.

C9 cannot begin until a genuinely external reviewer accepts the design, and it
cannot pass until a genuinely external reviewer reviews the implementation and
17-family evidence. Codex subagent reviews are recorded only as parallel
internal reviews.

## 10. C10 Exception, Redaction, and Bundle Gates

Planned files:

- exact accepted critical exception inventory at
  `docs/release-evidence/2026-07-phase-c-critical-module-inventory.md`, converted
  at C0 to a sorted machine-readable manifest and unioned with capability
  triggers;
- `backend/scripts/check_phase_c_invariants.py` and negative tests;
- `backend/tests/test_log_redaction.py`;
- `backend/tests/test_diagnostic_bundle.py`.

The static gate must reject:

- new unclassified broad catches or standalone passes in the critical set;
- runtime schema DDL outside migration assets/tests;
- CWD-relative writable production paths;
- direct durability-critical connections without the canonical policy;
- forbidden diagnostic bundle inputs.

Seed unique secret markers through message args, nested extras, exception text/
trace, URL query, multiline text, health state, account identifier, prompt/context,
and filename. Apply the same sanitizer at capture plus every persistence,
health/API, and bundle boundary. Scan every log/bundle byte and require zero
marker occurrence.

Bundle content is a fixed-entry allowlist with entry and aggregate size limits.
Negatives include traversal, symlink/reparse, oversize, race replacement,
partial creation, and concurrent creation. Default bundle must exclude `.env`,
DB, trades, portfolio, prompts, credentials, and raw account IDs.

Planned commands:

```powershell
python -m pytest `
    backend/tests/test_phase_c_invariants.py `
    backend/tests/test_log_redaction.py `
    backend/tests/test_diagnostic_bundle.py -v
if ($LASTEXITCODE -ne 0) { throw "C10 exception/redaction/bundle tests failed" }

python backend/scripts/check_phase_c_invariants.py
if ($LASTEXITCODE -ne 0) { throw "C10 invariant check failed" }
```

## 11. C11 Contract, UI, Docs, and Cross-Platform CI

Required:

- authenticated backend contracts for backup/integrity/migration/retention/
  diagnostics status and allowed actions;
- all renderer calls through the shared client;
- application modals and typed confirmations for dangerous staging/actions;
- restore accepts opaque backend backup IDs only: no arbitrary browser path,
  upload, hot apply, or restart endpoint;
- stage response explicitly returns `current_database_changed=false`,
  `restart_required=true`, and `offline_apply_required=true`; only the offline
  CLI applies an explicit stage ID;
- frontend/OpenAPI contract remains green;
- the focused Ubuntu and Windows Python 3.12 jobs established at C0, expanded
  with the complete persistence/crash matrix;
- full current backend and dashboard jobs remain green;
- current `BACKUP_AND_RECOVERY`, `DATA_AND_PRIVACY`, `OPERATIONS`, architecture,
  security, development, deployment, roadmap, and README docs.

Planned focused dashboard command:

```powershell
Push-Location dashboard
try {
    npx.cmd vitest run src/pages/__tests__/SettingsPage.dataManagement.test.tsx
    if ($LASTEXITCODE -ne 0) { throw "data-management UI test failed" }
} finally {
    Pop-Location
}
```

## 12. C12 Global Closeout

Run from a clean checkout:

```powershell
# Focused Phase C suites and both ephemeral drill CLIs first.
$phaseCRoot = Join-Path ([System.IO.Path]::GetTempPath()) `
    ("tradebot-phase-c-" + [guid]::NewGuid().ToString("N"))
$env:SIM_MODE = "true"
$env:AUTOPILOT_MODE = "OFF"
$env:TRADEBOT_HOME = $phaseCRoot
$env:DB_PATH = Join-Path $phaseCRoot "data\tradebot.db"

python -m pytest `
    backend/tests/test_retention.py `
    backend/tests/test_retention_scheduler.py `
    backend/tests/test_maintenance_status.py `
    backend/tests/test_app_paths.py `
    backend/tests/test_migrations.py `
    backend/tests/test_backup_restore.py `
    backend/tests/test_legacy_data_import.py `
    backend/tests/test_schema_matrix.py `
    backend/tests/test_runtime_lifecycle.py `
    backend/tests/test_task_supervisor.py `
    backend/tests/test_clean_shutdown.py `
    backend/tests/test_order_intents.py `
    backend/tests/test_reconciliation.py `
    backend/tests/test_order_crash_boundaries.py `
    backend/tests/test_phase_c_invariants.py `
    backend/tests/test_log_redaction.py `
    backend/tests/test_diagnostic_bundle.py -v
if ($LASTEXITCODE -ne 0) { throw "focused Phase C tests failed" }

python backend/scripts/run_phase_c_schema_matrix.py --ephemeral --json
if ($LASTEXITCODE -ne 0) { throw "schema matrix failed" }
python backend/scripts/run_phase_c_crash_drill.py --ephemeral --network-deny --json
if ($LASTEXITCODE -ne 0) { throw "crash drill failed" }

python -m pytest backend/tests -v
if ($LASTEXITCODE -ne 0) { throw "full backend pytest failed" }

Push-Location dashboard
try {
    npm.cmd run typecheck
    if ($LASTEXITCODE -ne 0) { throw "typecheck failed" }
    npm.cmd run build
    if ($LASTEXITCODE -ne 0) { throw "build failed" }
    npx.cmd vitest run
    if ($LASTEXITCODE -ne 0) { throw "vitest failed" }
} finally {
    Pop-Location
}

python backend/scripts/check_contract_frontend_vs_openapi.py
if ($LASTEXITCODE -ne 0) { throw "contract check failed" }
python backend/scripts/check_phase_c_invariants.py
if ($LASTEXITCODE -ne 0) { throw "Phase C invariant check failed" }
python scripts/check_workspace_hygiene.py
if ($LASTEXITCODE -ne 0) { throw "workspace hygiene failed" }
git diff HEAD --check
if ($LASTEXITCODE -ne 0) { throw "git diff check failed" }
$dirty = git status --porcelain=v1 --untracked-files=all
if ($LASTEXITCODE -ne 0) { throw "git status failed" }
if ($dirty) { throw "C12 evidence requires a clean checkout" }

$head = git rev-parse HEAD
if ($LASTEXITCODE -ne 0) { throw "final HEAD resolution failed" }
$remoteLine = git ls-remote --exit-code origin refs/heads/master
if ($LASTEXITCODE -ne 0) { throw "live origin/master resolution failed" }
$remoteSha = ($remoteLine -split "\s+")[0]
if ($head -ne $remoteSha) { throw "final HEAD and live origin/master differ" }
```

Expected-empty production scans must cover:

- CWD-relative DB/cache/event/log/backup/staging paths;
- runtime DDL outside migration owner;
- `_safe_add_column` and silent critical migration catches;
- unredacted seeded markers;
- `.partial` artifacts exposed as valid;
- diagnostic inclusion of forbidden files/data.

Record exact source, versions, test counts, fixture versions, logical digests,
schema manifest/checksums, backup/restore results, crash cases, artifact scans,
resolved Python and Node dependency inventories, live default/protection/master
state, Windows/Ubuntu CI URLs, deferrals, and owner approval.

The closeout chain is immutable and ordered: technical candidate `T`; exact-`T`
local Windows gates plus Windows/Ubuntu CI; genuine external C9 review;
technical evidence `E`; owner approval explicitly naming `T` and `E`; closeout
commit `C`; then successful CI on `C`. Parallel Codex agents are internal
reviews, never independent reviewers. Preserve every failed run and disposition.

## 13. Failure and Recovery Rules

- Migration, checksum, integrity, backup, restore, or reconciliation ambiguity:
  fail closed; do not start trading services.
- Failed destructive retention archive/table: zero critical deletion; persist
  failure and degrade health.
- Failed restore: keep or atomically restore the exact prior DB; never continue
  on a partly swapped file.
- Failed startup: unwind every acquired resource; retain unclean/degraded marker.
- Failed shutdown step: record it and attempt all later steps; lock release last.
- Broker unavailable: API may remain read-only/degraded with reconnect, but not
  ready and no new entries.
- Unknown external broker activity: surface intervention; do not mutate it.
- Any test touching a non-temporary or operator path is a test failure and a
  Phase C blocker.

## 14. Primary Technical References

- SQLite Online Backup API: https://www.sqlite.org/backup.html
- SQLite PRAGMA reference: https://www.sqlite.org/pragma.html
- Python 3.12 `sqlite3.Connection.backup`:
  https://docs.python.org/3.12/library/sqlite3.html

SQLite documents that `integrity_check` does not detect foreign-key violations;
Phase C must run both `integrity_check` and `foreign_key_check`. SQLite also
documents FULL checkpoint blocking behavior, so checkpoint results/timeouts must
be explicit and tested rather than assumed.

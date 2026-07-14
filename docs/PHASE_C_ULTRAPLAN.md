# Phase C Ultraplan - Master Consolidated Plan

Revision: 4

Date: 2026-07-14

Status: **ACCEPTED PLAN OF RECORD - C0 PASS**

Implementation authority: **C0 COMPLETE; C1-C12 NOT AUTHORIZED**

This document incorporates the attached Phase C Ultraplan v2, the Phase C
brief/tracker/manual, accepted ADRs 0007-0009, the verified retention audit, and
the final internal consistency reviews. It corrects the v2 issues identified
during reconciliation. The owner accepted it on 2026-07-14. Protected planning
PR #5 merged as `92fc9713d1838f8de601d4f26634ecd35eab85a1` and is the
durable planning record.

## 1. Normative hierarchy

The tracker is the single table of truth for status. Use this hierarchy:

1. explicit owner approvals and immutable release evidence;
2. accepted ADRs, including ADRs 0007-0009;
3. `docs/release-evidence/2026-07-phase-c-tracker.md` for checkpoint status;
4. this Ultraplan for consolidated scope, order, dependencies, and decisions;
5. `sessions/phase-c-data-durability-prompt.md` for implementation detail;
6. `docs/PHASE_C_VERIFICATION.md` for executable proof requirements.

Any conflict fails closed and returns to owner review. The nonexistent
`phase-c-implementation-plan.md` named by the v2 memo has no authority.

## 2. Authority and evidence boundary

Technical reviewers may assess correctness, ordering, and safety-property
completeness. They cannot accept business-risk policy for the owner. Internal
Codex agents are recorded as parallel internal reviews, never independent
assurance. C9 requires a genuinely external design review before implementation
and a genuinely external result review before PASS.

Verified current facts:

- Phase B B12 is PASS and Phase B is closed by explicit owner authorization.
- GitHub defaults to protected `master` with strict required backend/dashboard
  checks, administrator enforcement, and force-push/deletion disabled.
- Disconnected `main` and `archive/aiautomation-v2-2026-07-a10` both preserve
  `16280057ab04bee97904e9c59b9a5143a58bb673`; the histories remain unmerged.
- PRs #1, #3, and #4 were labeled superseded and closed unmerged against `main`.
- ADRs 0007-0009 and decisions D1-D21 were accepted on 2026-07-14.
- Emergency C1A passed at implementation commit
  `6093f0f7d5f66489a2ed55e9f3998b2921b6cde5`; evidence commit
  `1744bdb94e0ff8fcf55ffa427e563444af16f002` and CI run `29324523583`
  preserve its proof.
- C0 passed at merged technical source
  `3fff9846300beceacd77caf33834dc44d8fa69c7`; the tree-identical candidate
  passed 739 backend tests, dashboard typecheck/build, 389 tests in 31 files,
  the 147/145/190 contract gate, 36 verifier tests, and hygiene.
- Post-merge run `29338942043` passed backend, dashboard, Windows C0, and Ubuntu
  C0 jobs on that exact source. The dated record is
  `docs/release-evidence/2026-07-14-phase-c-c0.md`.
- The owner authorized the completed planning and C0 work. C1-C12
  implementation remains unauthorized.

## 3. Latent retention defects contained by C1A

### 3.1 C-F01 - all rows eligible in four INTEGER timestamp tables

`backend/db/retention.py` binds `cutoff.isoformat()` as TEXT to a generic
`timestamp_column < ?` predicate. The current schema and writers use INTEGER
Unix seconds for:

- `diag_indicator_values.created_at`;
- `diag_system_snapshots.created_at`;
- `diag_news_cache.fetched_at`;
- `diag_refresh_runs.started_at`.

SQLite applies INTEGER/NUMERIC affinity to the ISO value first. The ISO value
cannot be losslessly converted to a number, so it remains TEXT. Storage-class
ordering then places every INTEGER below that TEXT value. Because these columns
are non-null and the policies have no additional predicate, every row matches,
regardless of age. This is a total-eligibility defect for the four current
tables, not a cutoff edge case. All four policies also set
`backup_before_delete=False`.

### 3.2 C-F02 - archive failure still deletes

`_backup_records()` catches every exception and returns `None`.
`_cleanup_table()` records a truthy path when present but has no failure branch;
it falls through to `DELETE`. The JSONL writer is also direct and unverified: it
has no partial/final protocol, fsync, checksum, readback, or row-count proof.
Per-table exceptions are swallowed, so a multi-table cleanup may partially
commit.

### 3.3 Pre-C1A reachable destructive surfaces

Before C1A, the hazard was reachable through more than one path:

- `POST /api/admin/retention/cleanup`, gated only by authentication, accepts
  caller-controlled `dry_run`, `vacuum`, and unconstrained policy integers;
- `POST /api/admin/retention/cleanup-preview` uses the same transaction/WAL
  path and is not a query-only preview;
- `python -m db.retention --execute` has no safety gate;
- the exported programmatic service accepts `dry_run=False`;
- the same run can irreversibly unlink Parquet files while the database
  transaction is open and later attempt `VACUUM`; filesystem deletion cannot
  participate in database rollback;
- `DELETE /api/admin/retention/backups/{filename}` can unlink any named file
  inside the shared backup directory, not only listed JSONL archives, for any
  authenticated user;
- the current stats path opens the normal WAL-configuring connection and can
  create a database plus WAL/SHM artifacts, so it is not read-only proof;
- diagnostics refresh automatically deletes news rows after 72 hours, which
  conflicts with accepted D21's seven-day policy;
- startup purges terminal direct candidates after seven days outside the
  retention service.

C1A now rejects these destructive paths before connection, path, row, or file
mutation. It preserves queued/draining candidate TTL expiration because that is
an execution-safety control, while terminal-row age deletion remains suspended.
C-F01 and C-F02 are contained, not corrected: their algorithms remain latent
and require the typed C1/C7 rewrite before destructive retention can return.

### 3.4 Completed emergency containment C1A

The owner separately authorized the narrowly scoped emergency safety patch
before formal C0. It granted no other Phase C authority.

C1A:

1. hard-disable destructive cleanup at the service boundary;
2. reject API cleanup and preview until preview is genuinely query-only and
   typed;
3. disable CLI `--execute` and any programmatic destructive invocation;
4. defense-in-depth reject destructive table and Parquet helpers;
5. disable retention-archive deletion;
6. suspend the diagnostics-news and startup-candidate automatic deletes until
   they are governed by accepted D21 policy;
7. keep only constant/file-read policy and archive-list surfaces; either disable
   stats or reimplement it over an existing-file read-only/query-only connection
   that cannot create the DB, WAL, SHM, or directories;
8. provide no environment-variable or acknowledgement bypass;
9. add focused zero-mutation tests for API cleanup/preview, service, CLI,
   `_cleanup_table`, `_cleanup_parquet_files`, stats, backup DELETE,
   diagnostics-news pruning, and startup-candidate GC; prove no DB rows/files,
   DB/WAL/SHM artifacts, Parquet, JSONL/non-JSONL backup sentinel, or backup
   directory changes;
10. record a dated safety-hotfix evidence file from a clean `master` worktree.

C1A passed at `6093f0f7d5f66489a2ed55e9f3998b2921b6cde5`, with evidence at
`1744bdb94e0ff8fcf55ffa427e563444af16f002` and same-source CI run
`29324523583`. No other Phase C work followed automatically.

## 4. Entry gates

| Gate | Required state | Current state |
|---|---|---|
| Phase B B12 | Owner accepts seven boundaries; evidence-only closeout lands | PASS - closed 2026-07-14 |
| Emergency containment | Explicit owner choice on C1A | PASS - separately authorized and verified |
| Repository governance | Protected `master` default; required CI; legacy `main` archived; PRs triaged without history merge | PASS |
| Planning record | Owner accepts this plan and a planning commit records it | PASS - PR #5 merged as `92fc971` |
| ADRs | ADRs 0007-0009 accepted or revised | PASS - accepted 2026-07-14 |
| Decisions | D1-D21 accepted before dependent work | PASS - accepted 2026-07-14 |
| Clean source | Immutable clean worktree/clone; live remote SHA checked | PASS - C0 source `3fff984` |
| Safe verification | Simulation/AI OFF/temp paths, except isolated fake-broker C9 harness | PASS FOR C0 - future checkpoints re-prove |

C0 verification is complete. Entry-gate completion does not authorize C1-C12;
each remains blocked until a later explicit owner instruction.

## 5. Canonical C0-C12 execution plan

| ID | Outcome | Binding proof/stop condition |
|---|---|---|
| C0 | Authorization, branch governance, clean baseline, critical-module inventory, and early Windows/Ubuntu jobs | Live remote/default/protection/archive/PR checks; committed plan; exact source; no unexpected skips/xfails |
| C1 | Typed, fail-closed retention foundations | Preserve C1A lockout while adding query-only preview, integer/ISO boundaries, invalid-policy rejection, and archive-failure zero-delete proof |
| C2 | Immutable `AppPaths` and canonical connection ownership without default flip | Pure resolution; secure lock-parent is sole pre-lock mutation; immediate lock; no import-time/CWD/legacy mutation |
| C3 | Structural classifier, integrity/FK, checkpoint, and verified full backup | Unknown DB source is untouched; only lock/marker/approved directories may exist; checkpoint is sole supported pre-backup mutation; strict backup/ACL proof |
| C4 | Atomic canonical migrations and one schema owner | One registry entry per supported structural variant/version; self-contained immutable migrations; one transaction; no stamping |
| C5 | Copy-only legacy import and journaled offline restore | Source preserved; opaque IDs; exact destination-local journal recovery; stale WAL/SHM; rollback uncertainty blocks |
| C6 | Historical fixture and migration/restore matrix | Every supported classifier/version converges on one schema and logical digest on Windows and Ubuntu |
| C7 | Complete retention policy, archives, dormant scheduler, and maintenance ledger | Canonical truth never auto-deleted; D21 only; verified archives; strict cutoff; atomic critical delete; scheduler still disabled |
| C8 | Lifecycle, `OperationGate`, task ownership, marker, and shutdown certificate | Marker overrides provisional DB-clean row; clean vs safe-release proof; live mutator forces process death |
| C9 | Durable intent, fenced exact reconciliation, 17 crash families, and opt-in scheduler integration | External design/result reviews; network-denied injected adapter; no duplicate; ambiguity blocks; readiness convergence |
| C10 | Critical exception policy, boundary redaction, and diagnostic bundle | Exact critical inventory; no new silent critical catch; seeded-secret byte scan; fixed-entry bundle |
| C11 | Authenticated operator status/staging and documentation | Opaque backup/stage IDs only; no browser path/upload/hot apply/restart; contract/auth/UI/docs gates |
| C12 | Immutable technical and administrative closeout | Candidate T; exact-T local Windows and Windows/Ubuntu CI; external review; evidence E; owner approval naming T/E; closeout C; CI on C |

Future owner review stops may be recorded after C2 and C7/C8, but they do not
silently create authorization or a second checkpoint model.

## 6. Finding ownership

| ID | Finding | Owner checkpoint | Close condition |
|---|---|---|---|
| C-F01 | INTEGER timestamps compared with ISO TEXT | C1/C7 | Typed per-table cutoffs prove recent and exact-boundary rows are preserved |
| C-F02 | Archive failure still permits deletion | C1/C7 | Archive publication/readback failure guarantees zero critical deletion |
| C-F03 | Relative paths and legacy ambiguity | C2/C5 | One resolver; explicit copy-only import; no silent fresh DB |
| C-F04 | Unversioned/partial migration | C3/C4/C6 | Read-only variant classifier, checkpoint/backup, atomic canonicalization, immutable ledger |
| C-F05 | Fragmented schema/PRAGMA ownership | C4 | One schema owner; fixed application ID, ledger/manifest checksums, structural manifest, and secondary user version agree |
| C-F06 | No full backup/restore | C3-C6 | Strict manifested backup and journaled offline rollback drill |
| C-F07 | Broker acceptance before durable identity | C9 | Intent and committed `SUBMITTING` precede adapter entry |
| C-F08 | Incomplete/nonblocking reconciliation | C8/C9 | Subscribe/buffer/snapshot/drain repeat-to-stability gates READY |
| C-F09 | Fill/exit crash split | C9 | K05-K09 plus related DB-failure/reconciliation cases converge exactly once |
| C-F10 | Unowned shutdown | C8/C9 | Owned leases/tasks/callbacks/handles and certificate-gated release |
| C-F11 | No redaction/bundle boundary | C10 | Boundary sanitizer and allowlisted bundle pass seeded-secret scans |
| C-F12 | Retention schedule/status incomplete | C7/C9 | Dormant scheduler, maintenance ledger, explicit opt-in, lifecycle/order gate |
| C-F13 | Broad exceptions unclassified | C0/C10 | Exact critical-module inventory and blocking checker |
| C-F14 | Fixtures/Windows CI missing | C0/C6/C11 | Early Windows job and complete both-OS matrix |

## 7. ADR 0007 - application paths and legacy import

Accepted ADR 0007 is included in this protected documentation PR at
`docs/adr/0007-application-data-and-legacy-import.md`; it becomes committed
planning evidence only after merge. Its omission from an earlier external
planning review was an attachment gap, not a design omission.

Binding accepted design:

- one frozen `AppPaths` owns the DB, backups, retention archives, restore
  staging/control, cache, logs, diagnostics, runtime directory, and lock;
- precedence is specific absolute override, then `TRADEBOT_HOME`, then platform
  defaults;
- native Windows uses `%LOCALAPPDATA%\TradeBot`;
- the accepted POSIX default is exact: data under
  `${XDG_DATA_HOME:-$HOME/.local/share}/tradebot`, state under
  `${XDG_STATE_HOME:-$HOME/.local/state}/tradebot`, cache under
  `${XDG_CACHE_HOME:-$HOME/.cache}/tradebot`, and runtime under a valid private
  `${XDG_RUNTIME_DIR}/tradebot` or `state_root/runtime` only when the variable is
  unset; an explicitly insecure runtime root fails closed;
- pure path resolution performs no I/O;
- secure private lock-parent creation/validation is the sole pre-lock mutation,
  followed immediately by OS-lock acquisition;
- all other directories, DB probes, loggers, caches, and writable singletons are
  created only while the lock is held;
- C2 wires absolute compatibility locations without flipping the native default;
- C5 flips the default only after classification, verified backup, import, and
  rollback proof exist;
- legacy discovery checks only documented roots, is metadata-only, and never
  recursively hunts the disk;
- import is explicit copy-and-verify; source DB/log/cache data is never moved or
  deleted; ambiguity or unsupported identity blocks startup;
- restore control is private and destination-local even for overridden DB paths.

## 8. ADR 0008 - migration, backup, and restore

### 8.1 Schema identity and supported variants

TradeBot identity is composite, not a single pragma:

- fixed TradeBot `application_id` identifies the product;
- the append-only `schema_migrations` ledger and manifest/checksums prove history;
- `user_version` is a secondary diagnostic;
- the structural manifest proves the actual schema.

The four supported source classes are not four classifier entries. The registry
contains one explicit entry for every owner-supported structural variant of the
two tagged histories, every recognized unversioned Phase B shape, and each formal
Phase C version. Classifiers inspect columns, nullability/default/PK shape,
indexes, FKs, triggers, views, and selected constraints. Raw `sqlite_master` text
and fingerprint-and-stamp adoption are prohibited.

The minimum known registry inputs are not deferred behind the phrase
"every registered variant":

- the pre-recovery shape with 30 tables and 31 indexes;
- the pre-Phase-A/current family with 31 tables and 32 indexes;
- historical `rules.user_id` and `trades.user_id` nullable variants versus the
  current-fresh `NOT NULL` variants;
- a first-start shape whose three simulation tables lack `user_id` versus the
  restarted shape with nullable simulation `user_id` columns;
- every formal Phase C schema version.

The owner-supported floor is a policy decision, but every accepted shape gets a
separate classifier ID, provenance fixture, and real canonicalizing migration.

### 8.2 Exact startup/migration order

1. resolve immutable absolute paths without I/O;
2. securely create/validate only the private lock parent as the sole pre-lock
   filesystem mutation, then acquire the runtime lock immediately;
3. publish the durable external unclean marker, prepare remaining directories,
   discover metadata-only legacy candidates, and require any candidate selection
   while the lock is held and before SQLite is opened;
4. if the selected DB path is absent and no legacy candidate exists, classify it
   as `FRESH` without first creating a file; an existing zero-byte file is invalid,
   never fresh;
5. open an existing selected DB read-only/query-only, run integrity/FK checks,
   build the structural manifest, classify the header/schema/ledger state, and
   close every read-only handle;
6. reject unknown, future, corrupt, or ambiguous state with no checkpoint, zero
   source-DB mutation, and no DB/WAL/SHM/backup artifact; the secure lock parent,
   marker, and approved under-lock directories remain permitted and mandatory;
7. for an already-current database, re-verify identity and skip checkpoint,
   pre-migration backup, and migration;
8. only for a supported existing database requiring upgrade, quiesce writers and
   require a successful blocking checkpoint; this is the sole permitted source
   mutation before backup;
9. create, publish, and re-verify a classifier-labeled full backup;
10. execute ledger bootstrap, application-ID adoption, every pending migration,
    and `user_version` update in one runner-owned `BEGIN IMMEDIATE` transaction;
11. for `FRESH`, create the complete canonical schema and ledger in one migration
    transaction without pretending that a nonexistent source backup exists;
12. re-open and verify canonical schema, integrity, FKs, ledger, and checksums;
13. restore authority state and begin reconciliation/services.

Migrations are self-contained, LF-normalized, append-only, contiguous, unique,
checksummed assets. They cannot import mutable helpers, commit, roll back,
`VACUUM`, or change journal mode. Built-in screener presets and the diagnostics
catalog are deterministic reference migrations. Demo users and starter rules are
separate post-migration bootstrap/onboarding.

`.gitattributes` fixes LF. The manifest hashes the complete raw UTF-8/no-BOM file
bytes and supports explicit `--write` plus CI `--check`; filename, literal
metadata, ordering, and hashes are validated without importing migration modules.
Ledger rows include stable application and migration-engine versions,
`apply_kind`, and optional `source_classifier`. The initial canonical target owns
all 31 current tables (28 core plus three simulation tables) and 32 indexes.
Migration-owned seeds use fixed IDs and timestamps. Under the current auth model,
demo-user bootstrap failure is startup-fatal; starter-rule failure is a visible
degraded onboarding state and never overwrites existing operator rules. Runtime
diagnostics DDL/upsert no longer owns schema or reference-data creation.

### 8.3 Strict full backup

Use SQLite's Online Backup API. Online manual backup does not require a FULL
checkpoint and must include committed WAL-only rows. Pre-migration and clean
shutdown require the blocking checkpoint above.

One maintenance coordinator serializes backup, migration, restore, retention,
and related maintenance. Publication uses destination-local exclusive/no-follow
partial creation, validates the destination rather than the source, fsyncs files
and parent directories, and is tested against race substitution. Orphan partial
or mismatched artifacts are quarantined/listed as invalid and never offered for
restore. The application version comes from one stable packaged source, not an
environment-overridable runtime string.

`BackupManifestV1` is a strict discriminated record with
`manifest_version=1`, fixed product ID, backup ID, reason/UTC time, stable app
version, schema kind/version/classifier, migration checksums, filename, size,
SHA-256, integrity/FK results, and verification status. Missing, unknown, or
unsupported-version fields fail closed. Publish/fsync the final DB first and the
matching final manifest last; only the pair is valid.

The accepted D9 policy includes a Windows-only `pywin32` dependency. It creates
and verifies protected DACLs for the current user, SYSTEM, and Administrators
only across backup, staging, and restore-control roots; NULL DACLs, inherited
broad grants, and write ACEs for any other SID fail closed. POSIX is not a no-op:
it enforces ownership and `0700`/`0600`, and fails closed on broad permissions.

### 8.4 Offline restore

Dashboard/API operations use opaque backend backup/stage IDs only. They may
list, verify, stage, and report restart/offline-apply requirements. They accept
no arbitrary path/upload and cannot hot-apply or restart. Only the offline CLI
applies an explicit stage ID, and it acquires the same runtime lock before any
restore work.

The offline flow validates the staged candidate's ID/hash/schema/integrity/FKs,
forward-migrates a supported older candidate entirely in staging, and creates a
verified pre-restore safety backup of the current DB before touching the active
files. It then closes every DB handle, explicitly handles destination WAL/SHM,
promotes only destination-local files, and rechecks schema/integrity/FKs after
promotion.

A strict versioned journal in a private destination-local control path records:

`PREPARED -> OLD_PRESERVED -> CANDIDATE_PROMOTED -> POST_VALIDATED -> COMMITTED`

with `ROLLBACK_REQUIRED -> ROLLED_BACK` for failure recovery. Journal records
contain IDs/hashes and fixed derived names, never trusted paths. Each state is
atomically replaced/fsynced only after its file invariant is durable. Recovery
accepts files one operation ahead, verifies exact hashes, and deterministically
completes or rolls back every allowed combination. Malformed or impossible state
blocks startup without deletion. Any crash before `POST_VALIDATED` conservatively
restores the preserved old DB; rollback failure or uncertain WAL/SHM disposition
blocks startup. Cleanup occurs only after durable `COMMITTED` proof.

## 9. ADR 0009 - lifecycle and order reconciliation

### 9.1 Lifecycle and shutdown

```text
STARTING -> RECONCILING -> READY
                         -> DEGRADED
READY -> QUIESCING -> STOPPED_CLEAN
failure/hard death -> UNCLEAN
```

An `OperationGate` issues tracked leases. New entries require `READY` after
successful reconciliation. The external marker is published after lock
acquisition and before DB mutation. A surviving marker always overrides a
provisional DB `STOPPED_CLEAN` row.

Shutdown reserves **30 seconds**, not 25:

- 5 seconds: enter quiescing and stop/drain producers;
- 10 seconds: reconcile and preserve working intents;
- 5 seconds: detach callbacks and disconnect broker;
- 5 seconds: persist DB result and checkpoint;
- 5 seconds: flush logs and clear the marker only when clean.

Supported Compose/Uvicorn grace is at least 45 seconds. The certificate records
`clean_shutdown` separately from `safe_to_release`. Voluntary lock release needs
positive proof that every mutation-capable task, callback, adapter,
request/operation lease, and DB handle has stopped. Otherwise an injected
`ProcessTerminator` uses process death so the OS releases the lock.

If all mutation-capable resources are positively stopped but clean certification
still fails, record `safe_to_release=true` and `clean_shutdown=false`, retain the
external marker, and release the lock without claiming a clean stop. No DB or log
write is permitted after the final flush/release boundary.

### 9.2 Durable order intent and adapter boundary

Every broker submission uses durable intent, including any safety-kernel or
emergency liquidation path; otherwise automatic liquidation remains disabled
while emergency authority-stop stays available. Only the broker adapter calls
raw place/cancel methods.

The intent UUID/orderRef is committed before submission. The transition to
`SUBMITTING` commits immediately before adapter entry. K02b/K03 ambiguity becomes
`UNKNOWN`, creates/retains intervention, blocks entry, and is never aborted or
resubmitted. Exact idempotency-key/payload replay returns the original result;
conflicting reuse returns HTTP 409.

Uniqueness covers intent UUID, `(account_hash, client_id, broker_order_id)`,
account-scoped `permId`, and `(account_hash, execId)`. Unrelated external orders
are surfaced but never mutated. Manual exits require a fresh broker quantity and
an explicit or uniquely matching DB position; ambiguity blocks.

### 9.3 Fenced exact reconciliation

Subscribe before the first snapshot, buffer/deduplicate events, read the complete
broker/DB intent/order/execution/fill/position set, drain the buffer, and repeat
until two consecutive canonical digests are stable. Include a broker watermark
when available. Without one, failure to converge within the timeout remains
`DEGRADED`. Reconciliation precedes broker disconnect during clean shutdown.

### 9.4 C9 crash families

The authoritative count is 17 stable families. The brief and verification
manual enumerate them consistently; ADR 0009 defines their binding design.

1. `C9-K01`: before intent persistence;
2. `C9-K02`: before/after committed `SUBMITTING` (`K02a`/`K02b`);
3. `C9-K03`: dispatched unknown, timeout, accepted-before-ID
   (`K03a`/`K03b`/`K03c`);
4. `C9-K04`: broker ID persisted before watcher/snapshot registration;
5. `C9-K05`: during partial/final fill persistence;
6. `C9-K06`: fill persisted before position registration;
7. `C9-K07`: before exit-intent/pending-marker commit, proving zero submits;
8. `C9-K08`: after exit acceptance with the durable marker already present;
9. `C9-K09`: cancel timeout/failure and confirmed-cancel-before-DB subcases;
10. `C9-K10`: rejection before DB transition;
11. `C9-K11`: DB failure after acceptance;
12. `C9-K12`: repeated reconnect/status events;
13. `C9-K13`: partial reconciliation before completion;
14. `C9-K14`: `READY -> QUIESCING` versus new entry;
15. `C9-K15`: disconnect `READY -> RECONCILING` versus new entry;
16. `C9-K16`: intervention persistence failure;
17. `C9-K17`: each shutdown boundary through lock release.

The fake broker needs to persist across each backend kill/restart case, not
across unrelated tests. Use `AF_PIPE` on Windows and `AF_UNIX` on POSIX. Before
application import, the harness denies `AF_INET`, `AF_INET6`, and DNS, proves the
deny with a negative control, and asserts the fake adapter identity. Each case
runs the real broker-backed service path with `SIM_MODE=false`, paper authority,
AI authority OFF, and synthetic identity. Do not call this OS-level network
isolation unless a firewall/sandbox layer is separately implemented and tested.

## 10. Retention C7 policy

Canonical trades, open positions, order intents/orders/executions/fills, manual
interventions, and AI audit/decision/shadow/rule/guardrail/authority/parameter
history are never automatically deleted in the first desktop release. Unlisted
tables default to no deletion.

Accepted D21 periods:

| Data | Period |
|---|---:|
| Backtests | 365 days |
| Alert history | 90 days |
| Regime/diagnostic snapshots | 90 days |
| News cache | 7 days |
| Diagnostic refresh runs | 30 days |
| AI evaluation runs/slices | 90 days |
| Applied/failed/expired direct candidates | 7 days |

Archives contain complete typed rows plus versioned manifest/hash/fsync/readback
proof before eligible deletion. Critical deletes are one transaction; equality
at the cutoff is retained. `VACUUM` runs later outside that transaction. The
scheduler ships disabled and activates only after explicit operator opt-in,
maintenance lease, current successful reconciliation, zero nonterminal intents,
and lifecycle/order safety. Schedule time alone is never proof of safety.

Parquet and retention-archive deletion remain disabled throughout Phase C unless
a separate typed, root-confined, permission-checked, crash-tested policy is
approved in a later phase.

## 11. C10-C12 completion boundary

C10 starts with the accepted exact 77-file D14 inventory in
`docs/release-evidence/2026-07-phase-c-critical-module-inventory.md`, unioned with
filename-independent capability triggers. It is binding as a scope boundary,
though C10 implementation is not authorized. One sanitizer applies at capture
and every persistence,
health/API, and bundle boundary. Diagnostic bundles have a fixed-entry allowlist
and size limits and exclude DB, trades, portfolio, prompts, credentials, and raw
account identifiers by default.

C11 exposes authenticated, honest status and opaque staging operations only.
Windows CI begins at C0, not C11, and expands through the phase.

C12 uses this immutable chain:

1. technical candidate `T`;
2. clean exact-`T` local Windows verification;
3. same-`T` Windows and Ubuntu CI with no unexpected skips/xfails;
4. genuine external C9 result review on `T`;
5. evidence commit `E` naming T, CI, artifacts, failures/dispositions, reviewer;
6. owner approval explicitly naming `T` and `E`;
7. evidence-only closeout commit `C`;
8. successful CI on `C`;
9. administrative PASS, with no automatic Phase D start.

## 12. Branch strategy

Repository governance is complete: disconnected `main` remains preserved by its
archive tag; PRs #1, #3, and #4 were triaged and closed unmerged; protected
`master` requires backend/dashboard CI and is the default branch. No unrelated
history merge or rename occurred. Deleting archived `main` remains a future
owner decision and is not part of C0.

## 13. Recorded owner decisions and current authority

### 13.1 Phase B B12

The owner accepted all seven boundaries and explicitly authorized B12 PASS and
Phase B closeout on 2026-07-14:

1. manual orders are stock-only;
2. default ceilings are 10,000 shares and USD 100,000 absolute notional;
3. broker market buys use a 0.5% protective limit; simulation refuses invented
   fills for non-marketable limits;
4. manual sells are verified long-stock exits only; no opening shorts;
5. broker-backed startup requires a strong JWT and per-launch capability;
6. O'Neil/leading-industries/remote embeds and links remain unavailable without
   reviewed local contracts/data;
7. the Tauri shell, IPC, secure storage, and sidecar lifecycle remain Phase D.

The canonical closeout is
`docs/release-evidence/2026-07-12-phase-b-completion.md`.

### 13.2 Accepted D1-D21 register

| ID | Accepted disposition | Status |
|---|---|---|
| D1 | Support every registered tagged/unversioned/formal variant; reject unknown | ACCEPTED 2026-07-14 |
| D2 | `%LOCALAPPDATA%\TradeBot`; exact XDG split; specific overrides; `TRADEBOT_HOME` | ACCEPTED 2026-07-14 |
| D3 | Explicit copy-and-verify import; never delete source; ambiguity stops | ACCEPTED 2026-07-14 |
| D4 | Leave legacy logs/cache unless separately selected | ACCEPTED 2026-07-14 |
| D5 | Never auto-delete canonical safety/trading truth | ACCEPTED 2026-07-14 |
| D6 | Operator opt-in; any critical archive/table failure aborts the run | ACCEPTED 2026-07-14 |
| D7 | 21:00 `America/New_York` trigger plus off-market/safe-state, lease, reconciliation, and intent gates | ACCEPTED 2026-07-14 |
| D8 | Offline apply only; online list/verify/stage/status | ACCEPTED 2026-07-14 |
| D9 | Unencrypted until D; Windows DACL and POSIX ownership/modes fail closed | ACCEPTED 2026-07-14 |
| D10 | Ambiguity blocks entries and creates intervention; no auto-mutation | ACCEPTED 2026-07-14 |
| D11 | Broker outage means read-only/degraded with reconnect | ACCEPTED 2026-07-14 |
| D12 | Preserve/reconcile working orders on ordinary quit | ACCEPTED 2026-07-14 |
| D13 | Fresh broker quantity plus explicit/unique DB position; ambiguity creates/retains intervention | ACCEPTED 2026-07-14 |
| D14 | Exact 77-file inventory plus filename-independent capability triggers | ACCEPTED 2026-07-14 |
| D15 | Metadata/redacted logs only; exclude private trading/account data | ACCEPTED 2026-07-14 |
| D16 | 5/10/5/5/5 = 30 seconds; grace >=45; certificate-gated release or forced process death | ACCEPTED 2026-07-14 |
| D17 | Persistent-per-case fake broker; verified pre-import deny; repeat on sidecar/IBKR in D/F | ACCEPTED 2026-07-14 |
| D18 | Authenticated dashboard visibility/staging; native actions in D | ACCEPTED 2026-07-14 |
| D19 | Online backup no FULL checkpoint; migration/clean stop require blocking checkpoint | ACCEPTED 2026-07-14 |
| D20 | No automatic full-backup deletion in C; protect last verified and rollback artifacts | ACCEPTED 2026-07-14 |
| D21 | Explicit table/period allowlist; every unlisted table no-delete | ACCEPTED 2026-07-14 |

### 13.3 Current authority

- The documentation-only planning/Phase B closeout PR is merged.
- C0 verification, tooling, focused Windows/Ubuntu workflow work, and technical
  merge are complete; dated evidence records PASS.
- C1A is completed historical emergency containment.
- C1-C12 product/runtime implementation is not authorized.
- C9 still requires genuine external design approval before implementation and
  genuine external result review before PASS.

## 14. Primary references

- `docs/adr/0007-application-data-and-legacy-import.md`
- `docs/adr/0008-sqlite-migration-backup-restore.md`
- `docs/adr/0009-runtime-lifecycle-and-order-reconciliation.md`
- `docs/release-evidence/2026-07-phase-c-tracker.md`
- `docs/release-evidence/2026-07-phase-c-critical-module-inventory.md`
- `docs/PHASE_C_VERIFICATION.md`
- `docs/release-evidence/2026-07-12-phase-b-completion.md`
- SQLite datatype/comparison rules: https://www.sqlite.org/datatype3.html
- SQLite Online Backup API: https://www.sqlite.org/backup.html
- SQLite PRAGMAs: https://www.sqlite.org/pragma.html

The completed planning record and C0 PASS do not authorize live-money trading,
destructive retention, C1-C12 implementation, unrelated GitHub changes, or
Phase D.

# ADR 0008: SQLite Migration, Backup, and Restore

Status: Accepted - owner approved 2026-07-14

Implementation authority: design policy accepted; C1-C12 implementation is not
authorized.

Date: 2026-07-12

Depends on: ADR 0001 (trade truth), ADR 0007 (application-data paths)

## Context

TradeBot currently creates schema from several runtime modules and evolves it
with ad hoc `ALTER TABLE` helpers that broadly catch and ignore errors. SQLite's
application-owned `PRAGMA user_version` is unused, there is no checksummed
migration ledger, and startup cannot distinguish an old supported database from
an unknown, partially migrated, or future database.

The existing retention JSONL output is not a database backup: it does not
preserve the complete schema or complete rows for every table and has no tested
restore path. The shell commands currently shown in deployment documentation do
not coordinate active SQLite handles or WAL state and are not a supported
product restore procedure.

SQLite's online backup API can create a consistent destination snapshot while
the source remains available. SQLite also documents that `integrity_check` does
not find foreign-key violations, so a verified database needs both an integrity
check and `foreign_key_check`. FULL WAL checkpoints can wait on writers; their
busy result and timeout must be handled explicitly rather than assumed.

Primary references:

- https://www.sqlite.org/backup.html
- https://www.sqlite.org/pragma.html
- https://docs.python.org/3.12/library/sqlite3.html

## Decision

### One schema owner and immutable migrations

Move all production DDL and immutable required reference seeds into one ordered
migration manifest. Mutable/demo/operator defaults run after migration in a
separate idempotent transaction with an explicit failure/degraded policy.
Runtime services may query schema but may not create or repair it.
Each immutable migration has:

- a monotonically increasing integer version;
- a stable name;
- LF-normalized migration content;
- a SHA-256 checksum;
- an explicit validation contract. Transaction ownership belongs only to the
  runner; migration modules cannot commit, roll back, `VACUUM`, change journal
  mode, or import mutable runtime helpers.

Add a `schema_migrations` ledger recording version, name, checksum, applied UTC,
stable application version, migration-engine version, apply kind, and classifier
provenance. Mirror the latest version in `PRAGMA user_version` as a secondary
diagnostic and use a fixed TradeBot `PRAGMA application_id`; environment
`APP_VERSION` is not database identity. The ledger, application ID, and manifest,
not `user_version` alone, are authoritative.

The migration manifest is append-only, contiguous, and unique. A checked-in
generator has explicit `--write` and `--check` modes; CI rejects missing,
duplicate, reordered, or checksum-drifted entries.

Applied migration content is never edited, reordered, or removed. A correction
is a new migration. Missing, duplicate, reordered, or checksum-drifted entries;
an unknown unversioned layout; and a database newer than the application all
fail closed before trading services start.

### Supported upgrade floor

The owner-approved Phase C support floor is:

- schemas represented by tags `v1.0-pre-recovery-20260408` and
  `v1.0-pre-phaseA-20260411`;
- the unversioned Phase B schema at the accepted Phase C source;
- every formal Phase C migration version.

The registry contains every owner-supported structural variant, including the
known table/index-count, simulation `user_id`, and rules/trades nullability
shapes. Each unversioned source has a strict, read-only classifier covering
columns, null/default/PK shape, indexes, FKs, triggers, views, and constraints;
only named SQLite internals are ignored. Raw `sqlite_master` text is too brittle
to define identity. Older, future, corrupt, or ambiguous layouts are rejected
with zero mutation of the source database and no DB/WAL/SHM/backup artifact;
they are never guessed. The secure runtime-lock parent, durable external unclean
marker, and approved under-lock application directories are lifecycle artifacts,
not source-database writes, and remain mandatory.
Each legacy classifier also permits only explicit historical
`application_id`/`user_version` header values. A structurally matching database
with a foreign nonzero application ID or future version is rejected.
Text SQL/builders with synthetic records represent historical fixtures. No real
operator database or binary trade-history fixture is committed.

### Startup ordering and migration transaction

The verified backup primitive lands before the production migration runner can
activate. Startup uses this exact branch-aware order:

1. resolve static configuration and immutable absolute `AppPaths` without I/O;
2. securely create/validate only the lock parent, then acquire the runtime lock;
3. publish the durable external unclean marker, create/validate approved
   under-lock directories, inspect legacy candidates, and require any candidate
   selection before SQLite is opened;
4. classify an absent selected DB with no legacy candidate as `FRESH` without
   creating a file; reject an existing zero-byte file as invalid;
5. for an existing DB, open read-only/query-only, run integrity/FK checks, build
   the structural manifest, classify it, and close every read-only handle;
6. reject unknown, future, corrupt, or ambiguous state without checkpoint, source
   DB mutation, or DB/WAL/SHM/backup artifact;
7. for an already-current DB, re-verify and skip checkpoint, pre-migration backup,
   and migration;
8. only for a supported existing DB needing upgrade, quiesce writers, require a
   successful blocking checkpoint, and create/re-verify a classifier-labeled
   pre-migration backup;
9. execute every pending canonicalizing migration plus ledger/header updates in
   one runner-owned `BEGIN IMMEDIATE` transaction; `FRESH` creates the complete
   canonical schema/ledger in one transaction without a nonexistent-source
   backup;
10. run post-migration schema, integrity, foreign-key, ledger, and checksum checks;
11. restore persisted authority state and begin reconciliation/services.

Classification and adoption are separate atomic phases. A recognized legacy
variant receives whatever real DDL/data transformation is necessary to reach
the canonical baseline; it is never merely stamped. Initial
`schema_migrations` creation occurs only after the verified backup and inside
the same transaction as all pending migrations, so the whole upgrade rolls back
as a unit. Setting the fixed TradeBot `application_id` and `user_version` is part
of that same rollback-tested transaction.

After a database is recognized as supported, the required blocking checkpoint
is the sole permitted source mutation before backup verification. No schema,
ledger, seed, or application-row write is permitted before the backup. Unknown,
future, corrupt, or ambiguous sources are rejected without checkpointing.

Built-in screener presets and the diagnostics catalog are immutable migration
reference data. Demo-user creation is idempotent post-migration bootstrap.
Disabled AAPL/demo starter rules are optional operator onboarding; their failure
degrades onboarding but cannot be reported as schema failure or migration seed
success.

Any failure keeps authority unavailable and preserves the verified backup. SQL,
locking, disk, checksum, constraint, and unsupported-version failures are
reported distinctly. Forward migration is the only schema evolution mechanism;
rollback means restoring the pre-migration backup with the compatible prior
application version.

### Operation-specific WAL policy

- Online manual backup uses SQLite's backup API directly and does not require a
  prior FULL checkpoint. Its tests keep a committed row resident in WAL and
  prove the row appears in the snapshot.
- Pre-migration and clean shutdown first quiesce/close application writers and
  require a successful blocking checkpoint. A busy/error result blocks
  migration or clean-stop certification.
- Offline restore has no active application DB handle. Candidate and promoted
  databases still receive integrity and foreign-key validation.

The service records operation, checkpoint mode, timeout, and result. It never
reports a busy checkpoint as success.

### Verified full backup

Use SQLite's connection backup API rather than filesystem-copying an active WAL
database. A backup is written to a destination-local `.partial` file and becomes
visible only after all of these pass:

- operation-specific checkpoint policy completed with an explicit result;
- backup API completed;
- destination `integrity_check` returns `ok`;
- destination `foreign_key_check` returns no rows;
- one accepted schema-identity branch verifies;
- destination size and SHA-256 are recorded;
- DB and manifest partials are durably written.

Publication uses a realizable manifest-last commit protocol: promote the DB
partial to its final name first, then promote the matching manifest partial as
the validity marker. Listing, restore, and retention accept only a final pair
whose manifest hash matches the DB. A crash before/between/after promotions is a
kill-barrier test; orphan final DBs/manifests are ignored and quarantined, never
listed as valid.

Schema identity has two valid branches:

- **Versioned:** `schema_migrations`, LF-normalized migration checksums,
  `user_version`, and required schema manifest agree.
- **Supported unversioned:** a tracked strict classifier ID and its reproducible
  normalized structural manifest covering columns, constraints, indexes, FKs,
  triggers, and views agrees;
  migration history is explicitly empty/not-applicable.

The backup manifest records which branch was used. Every supported unversioned
fixture must produce and verify this backup before its first migration mutation.
Unknown or ambiguous unversioned schemas remain invalid.

Strict `BackupManifestV1` contains `manifest_version=1`, a fixed product ID,
backup ID, reason/UTC time, stable application version, schema
kind/version/classifier, migration checksums, filename, size, SHA-256,
integrity/foreign-key results, and verification status, but no trade rows,
credentials, prompts, or account identifiers. Missing fields, unknown fields,
and unsupported manifest versions are rejected. Pre-migration and pre-restore
backups are not automatically deleted in Phase C. Manual deletion cannot remove
the last verified backup or any pre-migration/pre-restore rollback artifact.
Storage pressure is visible. An archive/export used by retention is a separate
product and must not be described as a full backup.

### Offline staged restore

Restore never replaces a database in use. The authenticated Phase C dashboard
may select only an opaque backend backup ID, validate it, create an opaque stage
ID, and report `current_database_changed=false`, `restart_required=true`, and
`offline_apply_required=true`. It accepts no arbitrary path/upload and provides
no hot-apply or restart endpoint. Only the offline maintenance CLI may apply an
explicit stage ID while the runtime lock proves exclusive ownership. Phase D
will own the native restart experience.

Restore performs:

1. constrain the candidate to the approved staging boundary and reject path,
   symlink/reparse, extension, or replacement races;
2. verify manifest, hashes, application identity, schema support, integrity, and
   foreign keys before touching the active database;
3. create and verify a pre-restore backup of the current database;
4. copy the candidate to a destination-local temporary file;
5. migrate a supported older candidate forward and verify it;
6. atomically swap at the offline boundary;
7. reopen and repeat schema/integrity/foreign-key checks;
8. restore the exact prior logical state if promotion or post-swap validation
   fails.

A private destination-local external restore journal/control path is derived
from the selected DB parent (not general staging) and resolved before any DB
open. Its strict versioned record contains only stage/backup IDs, hashes, and
fixed-name metadata, never trusted arbitrary paths. It records exactly:
`PREPARED`, `OLD_PRESERVED`, `CANDIDATE_PROMOTED`,
`POST_VALIDATED`, `COMMITTED`, `ROLLBACK_REQUIRED`, and `ROLLED_BACK`. Recovery
also handles stale destination `-wal`/`-shm` files. If rollback cannot be proved,
startup remains blocked.

Each state transition is written only after its named file invariant is durable,
using atomic journal replacement, file fsync, and parent-directory fsync.
`PREPARED` means a verified candidate with the active DB untouched;
`OLD_PRESERVED` means the exact hashed prior DB is durably preserved;
`CANDIDATE_PROMOTED` means the candidate occupies the active path and old remains;
`POST_VALIDATED` means the promoted DB passed schema/integrity/FK checks;
`COMMITTED` finalizes the new DB; rollback states require or prove restoration
of the old DB. Recovery assumes files may be one operation ahead of the journal,
derives all names from the stage ID, and deterministically completes or rolls
back each allowed state/file/hash combination. Malformed, unknown-version/state,
hash-mismatched, or impossible combinations fail closed without deletion.

Restore cannot be combined with active trading, migration, backup, or retention.
An interrupted `.partial` or staging artifact is never treated as valid.

### Privacy boundary

The first Phase C backups are unencrypted SQLite files protected by per-user
filesystem ACLs/platform-equivalent permissions. The recommended Windows
implementation uses `pywin32` to enforce an explicit DACL granting only the
current user, SYSTEM, and Administrators; broad, null, or inherited write access
is rejected. POSIX roots/files use `0700`/`0600` equivalents. Native and explicit
backup roots must be owned/private to the current operator. Phase C provides no
warning-only or acknowledgement bypass: backup
and migration/restore operations remain unavailable/degraded until permissions
are corrected. This limitation is disclosed. OS-backed backup encryption or key
management remains Phase D unless the owner expands scope before C1. Diagnostic
bundles exclude databases by default.

## Consequences

### Positive

- Every supported schema has a deterministic, reviewable upgrade path.
- A failed migration cannot be mistaken for an already-applied column change.
- Backups include committed WAL-resident rows and are independently verified.
- Restore is rollback-capable and cannot overwrite an active database.
- Historical compatibility has a finite, testable boundary.

### Negative

- Migration assets become immutable maintenance obligations.
- Preflight and postflight checks increase startup/maintenance time.
- Restore requires a controlled restart instead of an in-process button.
- Local backup files remain sensitive and unencrypted until a later key model.
- Unsupported historical databases require a documented manual recovery path.

## Rejected Alternatives

### Continue ad hoc `CREATE IF NOT EXISTS` and `_safe_add_column`

Rejected because broad error swallowing cannot distinguish an already-applied
change from corruption, locking, disk failure, or partial migration.

### Use only `PRAGMA user_version`

Rejected because it contains only an application-defined integer and cannot
prove migration names, ordering, content checksums, or partial history.

### Copy the live `.db` file with a filesystem command

Rejected because WAL and active-handle coordination are not proven and the copy
has no product manifest or rollback procedure.

### Restore over the database while FastAPI is running

Rejected because open handles, background tasks, and trading mutations make a
safe atomic replacement contract impossible.

### Commit sanitized copies of real operator databases

Rejected because sanitization can miss private trading/account context and
binary fixtures are hard to audit. Synthetic text fixtures are sufficient.

## Acceptance Criteria

- All production DDL is owned by immutable checksummed migrations.
- Application ID, ledger, manifest, and `user_version` agreement is tested.
- Fresh, every approved tagged/structural variant, unversioned Phase B, and every
  formal Phase C schema migrate to latest while preserving logical synthetic
  records.
- Unknown, future, corrupt, FK-invalid, locked, checksum-drifted, and interrupted
  states fail closed.
- A verified pre-migration backup exists before any upgrade mutation.
- Every supported unversioned fixture has a classifier/fingerprint-identified
  verified backup before its first mutation.
- WAL-only committed rows appear in a verified backup.
- Backup failure leaves no final artifact and cannot be reported as success.
- Manifest-last kill barriers cannot expose an orphan as a valid backup.
- Restore is staged, offline, verified, atomic, and rollback-capable.
- Every restore-journal transition and stale WAL/SHM recovery barrier is tested;
  rollback uncertainty blocks startup.
- Failed restore returns to the prior logical state.
- Retention exports and diagnostic bundles are never represented as DB backups.
- Windows and Ubuntu fixture matrices pass from a clean checkout.
- A successful migration can be rolled back by restoring its verified
  pre-migration backup and validating it with the prior compatible source.

## Owner Decision Record

The owner accepted all items below on 2026-07-14:

1. The accepted supported upgrade floor, including both named tags and every
   explicitly registered structural variant.
2. Forward-only migrations with restore-plus-old-application rollback.
3. Offline-only restore with dashboard staging in Phase C and native restart in
   Phase D.
4. Unencrypted local backups protected by enforced private per-user permissions
   until Phase D; broadly writable roots fail closed with no bypass.
5. Synthetic text fixtures only; no committed operator-derived database.
6. Operation-specific D19 WAL policy: no required FULL checkpoint for online
   backup; busy/error blocks pre-migration and clean-stop certification.
7. D20 no automatic full-DB-backup deletion in Phase C.

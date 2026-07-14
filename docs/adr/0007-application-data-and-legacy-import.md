# ADR 0007: Application Data Paths and Legacy Import

Status: Accepted - owner approved 2026-07-14

Implementation authority: design policy accepted; C1-C12 implementation is not
authorized.

Date: 2026-07-12

Depends on: ADR 0001 (trade truth), ADR 0006 (canonical product surface)

## Context

TradeBot's database, bar cache, event logs, and optional log file currently
depend on the process working directory or arbitrary environment paths. Native
instructions have launched from both the repository root and `backend/`, so more
than one plausible legacy location can exist. Changing the default directly to
`%LOCALAPPDATA%` risks starting a new empty database and silently abandoning the
operator's history.

The Windows desktop target requires predictable per-user writable paths. Tests,
CI, and Compose still need explicit isolated overrides. Packaged source data such
as symbol universes is read-only product content and must not be confused with
runtime data.

At planning time the ignored event-log directory alone is about 615 MiB, so an
automatic copy of every legacy cache/log would be expensive and surprising.

## Decision

### One path owner

Add one immutable `AppPaths` value resolved purely from validated configuration
and platform defaults before any writable subsystem starts. Pure resolution does
not create directories, open files, or choose an operational legacy database.
It owns:

- application root;
- data directory and SQLite path;
- backup directory;
- restore/import staging;
- a retention-archive root distinct from full backups;
- a private destination-local restore-control path derived from the active DB
  parent;
- bar cache;
- event/application logs;
- diagnostic exports;
- runtime directory.

Production modules receive resolved paths or read them from this one value. They
must not define `Path("data/...")`, relative default arguments, or import-time
snapshots of `cfg.DB_PATH`.

Importing runtime modules must remain read-only: constructing an event logger,
cache, database helper, or other writable singleton is deferred until the lock
is held. Startup configuration validation must not probe or create the database.
Every SQLite connection comes from the canonical connection factory using the
resolved `AppPaths`; no cached configuration path may outlive resolution.

### Native Windows layout

```text
%LOCALAPPDATA%\TradeBot\
|-- data\
|   `-- trading_bot.db
|-- backups\
|-- cache\
|   `-- bars\
|-- logs\
|   |-- tradebot.jsonl
|   `-- events\
|-- diagnostics\
|-- staging\
`-- runtime\
    `-- tradebot-runtime.lock
```

The existing runtime-lock layout already establishes the per-user
`%LOCALAPPDATA%\TradeBot` convention.

### Resolution precedence

Recommended precedence:

1. test-owned explicit specific path;
2. existing specific compatibility override such as `DB_PATH`, `LOG_FILE`, or
   `RUNTIME_LOCK_PATH`;
3. explicit root override `TRADEBOT_HOME` for dev/server/Compose;
4. Windows `%LOCALAPPDATA%\TradeBot` native default;
5. the exact XDG layout below for Linux development and CI.

Specific overrides are resolved to absolute paths and validated. Phase C does
not add a path dependency solely for this purpose unless separately approved.

Compose continues using an explicit named-volume path and must not rely on a
container CWD. Tests always use unique temporary roots.

### Native POSIX/XDG layout

When no specific override or `TRADEBOT_HOME` is present, resolve:

```text
data root:    ${XDG_DATA_HOME:-$HOME/.local/share}/tradebot
state root:   ${XDG_STATE_HOME:-$HOME/.local/state}/tradebot
cache root:   ${XDG_CACHE_HOME:-$HOME/.cache}/tradebot
runtime root: ${XDG_RUNTIME_DIR}/tradebot
```

- database, full backups, retention archives, and import/restore staging live
  below the data root using the same named subdirectories as Windows;
- application/event logs and diagnostic exports live below the state root;
- rebuildable bar/cache data lives below the cache root;
- the runtime lock lives below the runtime root when `XDG_RUNTIME_DIR` is set,
  absolute, owned by the current user, and private;
- when `XDG_RUNTIME_DIR` is unset, use `state_root/runtime`; when it is set but
  invalid or insecure, fail closed rather than silently falling back;
- destination-local restore control remains derived from the selected DB parent,
  including when `DB_PATH` is explicitly overridden.

All XDG environment paths must be absolute. `TRADEBOT_HOME` intentionally
collapses data/state/cache/runtime into the Windows-shaped single-root layout for
test, development, server, and Compose isolation. A specific override still
changes only its named resource.

### Mixed override rules

An explicit override changes only the named resource:

- `DB_PATH` changes the active database, not the application root, backup,
  runtime, cache, log, or diagnostic roots;
- `LOG_FILE` changes only the application log file;
- `RUNTIME_LOCK_PATH` changes only the runtime lock;
- planned `BACKUP_DIR` changes only the durable backup root;
- `TRADEBOT_HOME` supplies defaults for resources without a specific override.

Every override must be absolute in supported production operation. A derived
child may not escape its owner root. A specifically overridden child may live
outside the root only because the operator named an absolute location, and it
receives the same permission/collision/privacy validation as the native root.

Candidate restore/import data stages under the application staging root. The
strict restore journal/control files live in the active DB parent rather than
general staging so recovery is destination-local even with an explicit
`DB_PATH`. The final atomic database promotion uses a private, destination-local
temporary file in the explicitly selected DB parent so replacement stays on one filesystem. If
`DB_PATH` is outside the application root, its parent must pass private-directory,
write, collision, symlink/reparse, and atomic-replace capability checks; the
resolver never silently relocates backup/runtime/log paths beside it.

### Read-only product assets

`backend/data/universes` and other packaged fixtures remain source/application
assets. They are not copied to the writable cache merely because they live under
a directory named `data` today.

### Staged adoption

The new resolver first reproduces an existing effective path through explicit
absolute compatibility wiring. Supported dev scripts and Compose pass an
absolute root/path that names their historical location. A direct native launch
without an explicit root does not derive identity from ambient CWD: it performs
the approved metadata-only legacy-candidate check and stops for selection when a
candidate exists or ambiguity remains. No subsystem consults CWD after the
resolver is built. The native default does not flip until:

- retention containment passes;
- versioned migration and schema classification exist;
- full verified backup exists;
- legacy candidates can be detected safely;
- import/restore rollback tests pass.

### Legacy discovery and import

Discovery is metadata-only and allowlisted. Candidate locations include only
documented historical launch roots. The application must not recursively hunt
the disk for SQLite files.

Rules:

1. If the new destination already has a DB, never overwrite it automatically.
2. If no destination exists and no approved candidate exists, create a fresh DB.
3. If exactly one supported candidate exists, require explicit selection and
   copy it through the verified SQLite backup/import service.
4. If multiple candidates exist, fail closed and require the operator to choose.
5. Verify application identity, supported schema, integrity, foreign keys, row
   digest, destination hash/manifest, and migration before activation.
6. Preserve the source exactly. Never move or delete it automatically.
7. Record source path metadata, source/destination schema, hashes, size, time,
   and result in an import manifest without recording sensitive row contents.
8. Event logs and rebuildable cache use separate opt-in import choices. They are
   left in place by default.

An unsupported, corrupt, or ambiguous legacy DB must never cause a silent fresh
start. The runtime stays blocked with a clear recovery action.

### Directory and path safety

- Use this exact bootstrap order:
  1. validate static configuration and resolve immutable absolute `AppPaths`
     without filesystem mutation;
  2. lexically/security-validate the resolved runtime-lock target;
  3. securely and idempotently create/validate only the private runtime-lock
     parent as the sole pre-lock mutation, reject symlink/reparse or broad ACL
     state, and immediately acquire the OS lock;
  4. while holding the lock, inspect/classify legacy candidates and create or
     validate every remaining writable directory.
- Never inspect, import, migrate, or initialize an application DB before runtime
  ownership is held.
- Importing `bot_runner` or any other runtime module creates no event-log,
  cache, database, WAL, SHM, or directory artifact.
- Refuse file-versus-directory collisions and paths escaping the configured
  root where a child path is expected.
- Treat symlink/reparse behavior explicitly in tests; diagnostic/restore staging
  must not follow an attacker-controlled escape.
- Use temporary files in the destination directory and atomic rename for final
  artifacts.
- Do not include secrets in file names, manifests, or logs.

## Consequences

### Positive

- Native operation no longer depends on CWD.
- Every writable subsystem shares one testable policy.
- Existing data cannot silently disappear during the default-path change.
- Compose and CI remain isolated through explicit overrides.
- Phase D can receive stable paths from the backend/shell boundary without
  redesigning persistence.

### Negative

- The first app-data launch may stop for an operator decision when multiple
  legacy candidates exist.
- Old logs/cache are not automatically available unless imported.
- Compatibility overrides remain supported until deployment/docs cleanup can
  retire them deliberately.
- Per-user filesystem protection does not encrypt data at rest; the accepted
  backup-privacy boundary is recorded in ADR 0008 and D9.

## Rejected Alternatives

### Continue using working-directory-relative paths

Rejected because shortcut, service, test, Compose, and developer launch working
directories differ and can select different databases.

### Automatically move the first `trading_bot.db` found

Rejected because discovery can choose stale/wrong data, moving is destructive,
and an interrupted move can strand both source and destination.

### Recursively copy all legacy `data/`

Rejected because source assets, caches, event logs, and databases have different
durability/privacy rules and can be large.

### Defer all path work to Tauri

Rejected because migrations, tests, Compose, CLI maintenance, and the packaged
sidecar need one backend-owned path model before the shell exists.

## Acceptance Criteria

- One canonical resolver owns every runtime-writable path.
- Pure `AppPaths` resolution precedes the runtime lock; secure lock-parent
  bootstrap is the sole pre-lock mutation, acquisition follows immediately, and
  all remaining path/legacy work occurs under it.
- Native Windows paths are under `%LOCALAPPDATA%\TradeBot`.
- Explicit tests/dev/Compose overrides remain deterministic and absolute.
- Mixed root/DB/backup/log/runtime overrides and child escapes follow the named
  resource rules and destination-local restore contract.
- No supported production write or legacy selection depends on ambient CWD.
- Packaged universe/source assets remain read-only.
- Existing data is never silently overwritten, abandoned, moved, or deleted.
- Ambiguous/unsupported/corrupt legacy data blocks startup with recovery detail.
- Import is copy-and-verify with a manifest; source remains unchanged.
- Large logs/cache require separate opt-in.
- Windows/Linux/override/permission/collision/path-escape tests pass.

## Owner Decision Record

The owner accepted all items below on 2026-07-14:

1. `%LOCALAPPDATA%\TradeBot` as the native root.
2. `TRADEBOT_HOME` as the accepted root override while retaining specific
   compatibility overrides.
3. Explicit copy-and-verify import with ambiguity refusal.
4. Leaving legacy event logs/cache in place by default.
5. The exact POSIX/XDG data/state/cache/runtime mapping above.
6. No new path dependency unless separately approved.

# Phase C Baseline and Scope Guard

Date: 2026-07-12

Last revised: 2026-07-14

Status: HISTORICAL PRE-C1A PLANNING BASELINE - PLAN ACCEPTED; C0 AUTHORIZED

Authoritative phase: Roadmap Phase C - Data Durability and Runtime Hardening.

Canonical source at planning start:
`a410baeb712fbe11d4c8b1b838b2a49df70b54c3`

Same-source CI:
https://github.com/Segev191312010/aiautomation/actions/runs/29208100462

This document preserves the dated pre-C1A planning observations at `a410bae`.
Later evidence records C1A PASS at `6093f0f`/`1744bdb`, Phase B closeout,
accepted ADRs 0007-0009 and D1-D21, and completed repository governance. C0 must
re-run every current-source count, live setting, and inventory rather than
treating this baseline as current proof.

## 1. Entry Conditions

Phase C may be planned now, but implementation must not begin until all of the
following are true:

1. Phase B B12 owner acceptance is recorded and Phase B is signed closed.
2. GitHub defaults to protected `master` with its required CI; disconnected
   legacy `main` remains archived and its three PRs are triaged without merging
   unrelated histories.
3. An owner-approved Phase C planning artifact is committed. ADRs 0007-0009 and
   D5-D6 are accepted before C1; each remaining D1-D21 decision is accepted
   before its dependent checkpoint.
4. Implementation starts from a clean clone or worktree at an immutable commit.
5. The user's current instruction-file changes are preserved and excluded from
   Phase C commits unless the user explicitly makes them part of the task.

At baseline capture, the owner working tree was intentionally not clean:

- modified, user-owned `sessions/baselines/.gitkeep`;
- untracked, user-owned `files/skills`.

Do not reset, stage, overwrite, or silently include those files. C0 must use a
clean worktree or clone rather than modifying the owner's current workspace.

## 2. Method and Evidence Boundary

The planning review:

- inventoried 83 Markdown files and separated current Roadmap Phase C from the
  unrelated historical dashboard-performance phase also named "Phase C";
- read the current roadmap, Phase A/B evidence, accepted ADRs, architecture,
  deployment, safety, security, handoff, and relevant historical planning files;
- inspected current persistence, retention, startup, shutdown, reconciliation,
  logging, diagnostics, tests, and CI code;
- used three parallel internal Codex read-only specialist reviews for
  data/migrations, runtime/recovery, and verification/documentation; these are
  not independent assurance;
- created disposable databases under the OS temporary directory to fingerprint
  fresh schema behavior;
- did not open or inspect any operational SQLite database, `.env`, credential,
  prompt, trade-history, or account-data content.

Only metadata was inspected for the ignored event-log directory. It currently
contains five files totaling 644,946,524 bytes (about 615 MiB), which makes an
explicit legacy log/cache import policy necessary.

At baseline capture, GitHub's default was the disconnected `main` history and
neither branch was protected. That historical blocker is now remediated:
protected `master` is default, disconnected `main` remains at the archive-tag
tip, and PRs #1, #3, and #4 were closed unmerged. C0 must recheck this live state.

## 3. Current Verified Quality Baseline

| Gate | Result |
|---|---|
| Backend pytest | 720 passed |
| Dashboard typecheck | Passed |
| Dashboard production build | Passed; 617 modules transformed |
| Dashboard Vitest | 31 files / 389 tests passed |
| Frontend/OpenAPI contract | 147 call sites / 145 unique operations matched 190 OpenAPI operations |
| Workspace hygiene | Passed on tracked source |
| Ubuntu CI | Backend and dashboard passed at `a410bae` |

These are dated baseline facts, not permanent counts.

## 4. Disposable Fresh-Schema Snapshot

The snapshot ran `db.core.init_db()` followed by `sim_engine.initialize()` on a
new temporary database and queried `sqlite_master`. The temporary file was then
removed.

| Property | Result |
|---|---|
| Tables | 31 |
| Indexes | 32 |

Tables:

```text
ai_audit_log, ai_decision_items, ai_decision_runs, ai_evaluation_runs,
ai_evaluation_slices, ai_guardrails, ai_parameter_snapshots,
ai_rule_validation_runs, ai_rule_versions, ai_shadow_decisions, alert_history,
alerts, backtests, diag_indicator_catalog, diag_indicator_values,
diag_news_cache, diag_refresh_runs, diag_sector_projection_runs,
diag_sector_projection_values, diag_system_snapshots, direct_candidates,
manual_interventions, open_positions, regime_snapshots, rules,
screener_presets, sim_account, sim_orders, sim_positions, trades, users
```

Phase C must define a registry of reproducible structural schema manifests,
LF-normalized migration checksums, columns/nullability/defaults/PKs, indexes,
constraints, foreign keys, triggers, and views. Raw `sqlite_master` text is not a
release identity. The planning snapshot intentionally records only reproducible
table/index counts and names.

History contains multiple recognized shapes rather than one baseline: the
pre-recovery and pre-Phase-A counts differ; simulation tables exist without
`user_id` on first startup and with nullable `user_id` after restart; historical
rules/trades nullability differs from a fresh current schema. C3/C4 must classify
each approved variant read-only, checkpoint and back it up, then execute one
atomic canonicalizing migration rather than merely stamping it.

## 5. Current Persistence and Runtime State

| Concern | Current behavior | Evidence |
|---|---|---|
| Database path | Working-directory-relative `trading_bot.db`; Compose overrides with `/data/trading_bot.db` | `backend/config.py`, `docker-compose.yml` |
| Bar cache | Working-directory-relative `data/bars` | `backend/data_handler.py` |
| Event logs | Working-directory-relative `data/event_logs` | `backend/event_logger.py` |
| Import-time mutation | Importing `bot_runner` constructs `EventLogger` and can create `data/event_logs` before lifespan lock acquisition | `backend/bot_runner.py`, `backend/event_logger.py` |
| Application log | Optional arbitrary `LOG_FILE`; current JSON logging helper is not the canonical startup path | `backend/config.py`, `backend/log_config.py`, `backend/main.py` |
| Connection policy | Shared helpers use WAL, `synchronous=FULL`, busy timeout, and foreign keys | `backend/db/core.py` |
| Direct DB connections | Auth, settings, and simulation bypass part of the shared connection policy | `backend/auth.py`, `backend/settings.py`, `backend/simulation.py` |
| Schema owner | Most DDL in `db/core.py`; simulation owns three tables; diagnostics repeats catalog DDL | `backend/db/core.py`, `backend/simulation.py`, `backend/diagnostics_service.py` |
| Migrations | Ad hoc `_safe_add_column()` catches every exception and silently passes | `backend/db/core.py` |
| Version ledger | No `schema_migrations`, checksums, or future-schema refusal | Repository scan |
| Integrity | Startup/readiness proves only that the DB can answer a basic query | `backend/startup.py`, `backend/health.py`, `backend/routers/status.py` |
| Pre-lock DB mutation | Startup validation opens `cfg.DB_PATH`; SQLite can create an empty file before legacy classification | `backend/startup.py` |
| Full backup/restore | No supported full backup or restore service | Repository scan |
| Retention archive | Selected rows exported as JSONL next to the DB; no supported round-trip | `backend/db/retention.py` |
| Retention schedule | CLI/API only; no scheduler despite "automated" module wording | `backend/db/retention.py` |
| Startup recovery | Pending-order reconciliation is fire-and-forget and not a readiness gate | `backend/main.py`, `backend/order_executor.py` |
| Shutdown | Stops some services sequentially; does not own all tasks, reconcile, checkpoint WAL, or flush logs | `backend/main.py` |
| Diagnostics export | No allowlisted redacted support bundle | Repository scan |
| CI | Ubuntu-only; no Windows persistence/crash matrix | `.github/workflows/ci.yml` |
| Branch governance | Historical baseline: default `main` was disconnected from canonical `master`; neither branch was protected | live GitHub state at baseline; remediated later and subject to C0 recheck |

## 6. Verified Phase C Findings

### C-F01 - Critical: destructive retention compares incompatible timestamp types

Several diagnostics tables store Unix-epoch `INTEGER` timestamps, while
retention compares them to an ISO-8601 text cutoff. A disposable SQLite query
confirmed that both `1 < ISO-text` and `9999999999 < ISO-text` evaluate true.
Destructive cleanup can therefore classify every row in affected tables as
expired regardless of age.

Affected policy mappings include `diag_indicator_values.created_at`,
`diag_system_snapshots.created_at`, `diag_news_cache.fetched_at`, and
`diag_refresh_runs.started_at`.

Operational boundary: C1A now rejects retention mutation, CLI `--execute`, admin
cleanup/preview/stats, Parquet cleanup, retention-archive deletion, and the two
out-of-band terminal-row deletion paths before storage mutation. C-F01 remains
latent, not corrected; C1/C7 must add typed query-only behavior before any
destructive path can return.

### C-F02 - Critical: required retention archive failure does not stop deletion

`_backup_records()` catches an error and returns `None`; `_cleanup_table()` then
continues to execute the delete. Disk-full, permission, or serialization failure
can therefore delete data without the required archive. Existing JSONL exports
also omit non-`data` columns for tables such as `trades`, so they are not a full
recovery format.

C1A makes this legacy algorithm unreachable from supported destructive entry
points. C-F02 remains contained rather than resolved until C1/C7 proves
archive-failure zero-delete behavior.

### C-F03 - High: data paths can silently select a different database

Native launch instructions use different working directories, while defaults
are relative. Plausible legacy databases may exist at the repository root or
under `backend/`. Flipping the default directly to `%LOCALAPPDATA%` could start
an empty DB and strand existing history. Legacy discovery and verified copy must
precede the default flip.

### C-F04 - High: migrations are unversioned, partially committed, and fail-open

Table creation, ad hoc column changes, indexes, and seeds are separated by
commits. `_safe_add_column()` treats locking, disk, corruption, syntax, and
duplicate-column failures identically. There is no durable explanation of which
steps completed.

The safe adoption order is read-only classification, not "stamp then migrate."
Existing DBs require
read-only structural classification and integrity/FK checks, a successful
quiesced blocking checkpoint, and a verified classifier-labeled backup before
the first ledger/schema write. All pending canonicalization and ledger changes
then belong to one runner-owned `BEGIN IMMEDIATE` transaction.

### C-F05 - High: schema ownership and connection guarantees are fragmented

Simulation and diagnostics create schema outside the primary initializer.
Several direct connections do not consistently enable the shared foreign-key,
timeout, and durability settings. ADR 0001's connection guarantee is therefore
not true for every code path.

### C-F06 - High: there is no verified full backup or safe restore

The server-oriented examples in `docs/DEPLOYMENT.md` are not an implemented
product feature and overwrite an active WAL database without application-owned
validation, manifest, rollback, or handle coordination.

### C-F07 - High: broker acceptance can precede durable order identity

`order_executor` persists `PENDING` with `order_id=None`, submits to IBKR, then
persists the broker ID. A crash after broker acceptance but before the second
write creates a broker order that startup recovery ignores. In-memory duplicate
maps also reset on restart.

### C-F08 - High: reconciliation is incomplete and not a readiness gate

Startup recovery scans at most 500 DB trades with order IDs and compares only
broker `openTrades()`. It does not reconcile completed orders, executions,
`orderRef`, `permId`, broker positions, or DB positions. Repeated reconnects can
append duplicate event handlers. The API can report ready while reconciliation
is still running or has failed.

### C-F09 - High: fill and exit state can split across crash boundaries

Fill persistence and position registration are separate, with position work
launched through an unowned callback. Exit submission can occur before its
pending marker is durable. A failed/unconfirmed cancellation can clear the
marker and permit a second exit.

### C-F10 - High: shutdown is not supervised or failure-isolated

One shutdown exception prevents later cleanup. AI loops, fill watchers,
diagnostic refreshes, WebSocket tasks, and startup reconciliation are not owned
by one task registry. There is no quiescing state, unclean-shutdown marker, WAL
checkpoint, or explicit log flush before the runtime lock releases.

Current `RuntimeLock.release()` has no shutdown-certificate precondition and
there is no forced-termination path for a cancellation-resistant mutating task.
C8 must retain ownership until a `safe_to_release` certificate exists or invoke
an injected process terminator so OS death, not voluntary cleanup, releases it.

### C-F11 - High: log redaction and diagnostic-bundle boundaries are absent

Logging setup is split, exception text/extras are not centrally redacted, and no
allowlisted bundle excludes secrets, prompts, account identifiers, trades, or
the DB by default.

### C-F12 - Medium/High: retention semantics and visibility are incomplete

`VACUUM` is attempted inside a transaction and its failure is swallowed.
Per-table errors may partially commit. No maintenance-run ledger, overlap guard,
last/next status, or operator degraded state exists.

### C-F13 - Medium: broad-exception inventory has grown and is stale in docs

A current raw scan finds 278 `except Exception` handlers across 68 backend Python
files and 41 standalone `pass` lines outside tests. These are not all bugs:
containment and best-effort observability boundaries can be valid. Phase C must
classify the trading/runtime/persistence-critical subset and block new silent
catches there rather than mechanically rewriting the whole backend.

### C-F14 - Medium: fixture and platform coverage are missing

There is no tracked historical SQLite fixture, migration matrix, backup/restore
drill, deterministic broker crash harness, or Windows CI job. The custom test
temporary-directory helper can leave residue after hard process death and must
be hardened before crash drills.

The C9 crash scope is 17 stable canonical families (`C9-K01` through
`C9-K17`), with stable suffixes for parameterized barriers. A persistent fake
broker process and a self-proving pre-import network deny are required. The raw
emergency `placeOrder` path also needs durable intent or must be disabled while
emergency authority-stop remains available.

## 7. Documentation and Instruction Truth

- `docs/baseline.md` still describes `backend/database.py` as a monolith, but it
  is now a compatibility shim over `backend/db/`.
- `docs/DEPLOYMENT.md` presents PostgreSQL migration and unsafe server backup/
  restore examples that are not the first desktop product boundary.
- ADR 0001 assigns broker/DB cross-check work to a historical "Phase B" even
  though it remains open and belongs to current Phase C.
- `task.md` and `logs/codex-review-phase-C-perf.md` use "Phase C" for completed
  dashboard performance work. They are historical and do not define this phase.
- `sessions/baselines/.gitkeep` is a large second instruction document in a
  misleading placeholder path. `files/skills` is empty, and the `.codex/agents`
  and `.agents/skills` assets it describes do not currently exist.
- `SOUL.md` says to commit `dashboard/dist`, conflicting with signed Phase A A11.
  The signed Phase A decision remains authoritative.

## 8. Phase C Scope

In scope:

- immediate retention containment;
- canonical application-data paths and explicit legacy import;
- immutable versioned SQLite migrations and schema ownership;
- integrity checks, verified backup, staged offline restore, and export;
- migration/restore fixtures and fault injection;
- safe scheduled retention and operator-visible maintenance status;
- critical exception inventory and fail-closed degraded states;
- owned tasks, quiescing, clean shutdown, and unclean-start recovery;
- durable broker order intent and idempotent reconciliation;
- genuine external C9 design review before implementation and result review
  before PASS; internal Codex reviews remain labeled internal;
- centralized redaction and privacy-safe diagnostic bundle;
- authenticated operator surfaces needed to expose Phase C state/actions;
- Windows/Ubuntu Phase C CI, documentation, and evidence.

Out of scope:

- Tauri shell, PyInstaller sidecar, native IPC, or secure OS secret storage;
- installer, signing, updater, or release channels;
- dependency-wide remediation/SBOM work;
- packaged-app and real IBKR paper soak;
- unattended or live-money authorization;
- PostgreSQL or multi-user architecture.

## 9. Planning Disposition

Phase C is required. Phase B, policy acceptance, C1A containment, and branch
governance are complete. The owner authorizes this planning record and C0
verification only; clean immutable C0 source and same-source proof remain open.
C1-C12 are planned but not authorized. C1 must preserve the C1A lockout while
building typed retention foundations, and Phase D does not begin automatically.

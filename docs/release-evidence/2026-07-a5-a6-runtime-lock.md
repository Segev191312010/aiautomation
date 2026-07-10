# Phase A5/A6 Runtime Lock Evidence

> Historical evidence notice: this file records the original v1 PID/unlink
> implementation and its `9 + 3` results. The 2026-07-10 checker found a
> concurrent stale-reclaim race. Do not use the lock/recovery behavior below as
> current operating guidance. See `docs/PHASE_A_VERIFICATION.md` and
> `docs/release-evidence/2026-07-10-phase-a-reverification.md` for the v2
> OS-held lock, scope, stop-all-v1 upgrade boundary, tests, and recovery policy.

Date: 2026-07-09
Phase: A - Truth, Safety, and Product Consolidation
Stages: A5 - Runtime process lock, A6 - Runtime lock tests and failure UX

## Goal

Prevent duplicate active TradeBot backend runtimes on the same machine while the
backend remains a stateful FastAPI monolith.

## Startup Boundary

The lock is acquired at the top of `main.lifespan`, before the original startup
body now housed in `_run_lifespan`.

This means the lock is acquired before:

- startup validation;
- database initialization;
- simulation initialization;
- runtime-state mutation;
- IBKR connection and reconnect loop;
- reconciliation tasks;
- market heartbeat;
- alert engine;
- notification wiring;
- AI optimization and learning loops.

The lock is released after `_run_lifespan` exits, so normal shutdown releases it
after the existing side-effect teardown has run.

## Lock Path

The lock path is configurable through `RUNTIME_LOCK_PATH`.

Default behavior:

- Docker Compose sets `RUNTIME_LOCK_PATH=/runtime/tradebot-runtime.lock`.
- Compose bind-mounts `./.runtime:/runtime`, so local and compose backend runs
  coordinate through one host-visible lock directory.
- Bare containers use `/data/tradebot-runtime.lock` when `/data` exists.
- Local development uses `.runtime/tradebot-runtime.lock`.

`.runtime/` is ignored by Git.

## Metadata

`backend/runtime_lock.py` writes JSON metadata:

- `lock_version`
- `pid`
- `hostname`
- `started_at_utc`
- `executable`
- `cwd`
- `mode`
- `token`

The `token` prevents one runtime from deleting another runtime's lock during an
edge-case ownership race.

## Failure Behavior

Duplicate startup raises `RuntimeLockError` with an operator-readable message:

```text
TradeBot backend already running; refusing second runtime (...)
```

The failure happens before `_run_lifespan` is entered, so the app cannot report
a half-ready health state after a lock conflict.

## Stale-Lock Policy

Stale locks are recovered only when the existing lock has an integer PID and the
process checker proves that PID is not running.

Malformed metadata, missing PID metadata, permission uncertainty, or any state
that cannot prove the owner is dead fails closed as a duplicate-runtime error.

## Logs

The lock module emits structured event names:

- `event=runtime_lock_acquired`
- `event=runtime_lock_conflict`
- `event=runtime_lock_stale_recovered`
- `event=runtime_lock_released`
- `event=runtime_lock_release_skipped`

## Tests

Targeted commands:

```text
cd backend
python -m pytest tests/test_runtime_lock.py -q
```

Result:

```text
9 passed
```

```text
cd backend
python -m pytest tests/test_startup_runtime_lock.py -q
```

Result:

```text
3 passed
```

Compile check:

```text
cd backend
python -m py_compile runtime_lock.py main.py
```

Result: PASS.

Full gate commands:

```text
cd backend
python -m pytest tests/ -q
```

Result:

```text
597 passed, 1 warning
```

```text
cd dashboard
npm run typecheck
npm run build
npx vitest run
```

Result:

```text
typecheck PASS
build PASS
27 test files, 370 tests passed
```

```text
python scripts/check_workspace_hygiene.py
```

Result:

```text
Workspace hygiene OK: no forbidden binary artifacts found.
```

## Test Coverage

Unit coverage proves:

- acquire writes metadata;
- owned release removes the lock;
- repeated release is safe;
- second acquire fails while first owner is live;
- dead-PID stale lock is reclaimed and logs recovery;
- live lock is not reclaimed;
- malformed lock fails closed;
- non-owner release does not delete another lock;
- relative configured paths resolve from the current working directory.

App-level coverage proves:

- FastAPI lifespan acquires the runtime lock on startup;
- shutdown releases the runtime lock;
- duplicate startup fails before the side-effecting lifespan body is entered;
- stale runtime lock is reclaimed before startup proceeds.

## Deferred

Phase D desktop packaging should move the default lock path from repo-local
`.runtime/` to a per-user application data directory. The `RUNTIME_LOCK_PATH`
configuration hook exists so that later desktop shell ownership can set the
installed-app path without changing lock semantics.

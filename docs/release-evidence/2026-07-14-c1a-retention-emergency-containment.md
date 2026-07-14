# C1A Emergency Retention Containment Evidence

Date: 2026-07-14

Status: **LOCAL CANDIDATE PASS - SAME-SOURCE CI PENDING**

This record covers emergency Phase C checkpoint C1A only. It does not authorize
or report implementation of C0-C12, live-money trading, desktop packaging, or
destructive retention.

## Authorization

On 2026-07-14, the owner explicitly authorized implementation and verification
of emergency C1A, including a clean-worktree conventional commit and push to
public `origin/master` for CI. The same instruction explicitly withheld
authority for every other C0-C12 implementation checkpoint.

The owner also accepted the seven Phase B B12 boundaries, ADRs 0007-0009, and
decisions D1-D21, including the exact D14 critical-module inventory. Those
acceptances do not broaden this implementation record beyond C1A.

## Source Identity

- Clean starting source: `a410baeb712fbe11d4c8b1b838b2a49df70b54c3`
- Starting remote: `origin/master` at the same commit after fetch
- Implementation branch: `c1a-retention-containment`
- C1A candidate commit: resolve with
  `git log -1 --format=%H -- docs/release-evidence/2026-07-14-c1a-retention-emergency-containment.md`
- Same-source GitHub Actions run: pending candidate push

The owner's dirty primary worktree and its preserved instruction/planning files
were not modified. C1A was implemented in a separate clean worktree.

## Containment Boundary

C1A establishes one stable fail-closed contract:

```text
code:   RETENTION_DISABLED_C1A
detail: Retention operations are temporarily unavailable pending verified
        retention implementation.
```

The following surfaces reject before database, directory, path-resolution, or
file access:

- `run_retention_cleanup()` for preview and execution;
- `get_retention_stats()`;
- table cleanup, archive creation, Parquet cleanup, and `VACUUM` helpers;
- every operational CLI invocation, including default preview, `--execute`,
  `--vacuum`, and `--stats`;
- authenticated API cleanup, preview, stats, and backup deletion.

Authenticated API calls return HTTP 503 with the stable `{error, detail}` body.
Unauthenticated requests retain the existing HTTP 401 boundary. CLI operations
exit 2 before async or storage work; `--help` remains available.

Constant policy inspection and read-only archive listing remain available.
There is no environment-variable, acknowledgement, force, test, or admin-role
bypass.

## Automatic Delete Suspension

- Diagnostics refresh continues to ingest news but no longer deletes old
  `diag_news_cache` rows.
- Startup continues to expire stale `queued`/`draining` direct candidates so
  they cannot execute after their TTL.
- Startup no longer deletes terminal `applied`, `failed`, or `expired` direct
  candidates.
- Both suspended paths emit explicit C1A telemetry.

## Verification

Focused proof on the candidate worktree:

```text
python -m pytest tests/test_retention.py -q
23 passed in 1.62s

python -m pytest tests/test_retention.py \
  tests/test_execution_brain.py::test_purge_expired_candidates_on_startup \
  tests/test_startup_config.py -v
46 passed in 2.29s
```

The focused suite proves:

- absent DB, WAL, SHM, parent, and backup directories remain absent;
- existing SQLite bytes remain unchanged for preview and execution attempts;
- old Parquet hash/mtime remains unchanged;
- JSONL and arbitrary non-JSONL backup sentinels remain byte-identical;
- authenticated API operations return the exact 503 contract;
- authentication is not bypassed;
- all CLI operation modes fail before storage access;
- policy validation remains strict;
- old diagnostics news survives refresh;
- `applied`, `failed`, and `expired` candidates survive startup cleanup while a
  stale queued candidate still transitions to `expired`.

Full local candidate gates:

```text
backend:  739 passed in 35.81s; 0 failed/skipped/xfailed; no warnings
typecheck: PASS
build:     PASS - Vite 5.4.21, 617 modules transformed
dashboard: PASS - 31 files, 389 tests in 5.90s
```

Dashboard output retained eight expected WebSocket disconnect diagnostics and
two React Router future-flag warnings. Fresh `npm ci` repeated the known audit
inventory: 10 findings (`1 critical`, `4 high`, `4 moderate`, `1 low`). These
remain a later release blocker and were not introduced or remediated by C1A.

## Failed/Interrupted Evidence Preserved

The first backend checkpoint ran before the obsolete retention tests were
replaced: 716 tests passed and four old tests failed because they expected the
now-disabled cleanup/stats behavior. After replacing those expectations with
C1A containment proof, the focused and full suites passed.

The first dashboard attempt found no `node_modules` in the clean worktree, so
the tools were unavailable and no dashboard tests ran. `npm ci` installed the
committed lockfile dependencies without source or lockfile changes; all three
dashboard gates then passed twice.

## Review

Parallel internal database, automatic-delete, and security audits identified
all reachable surfaces and the zero-artifact ordering requirements. A separate
post-source review found no CRITICAL or HIGH issue. These are internal parallel
reviews, not independent external assurance.

## Decision

Local C1A evidence is complete. Final C1A PASS requires the exact candidate
commit to pass public GitHub Actions and a final clean-diff/security review.
Until then, destructive retention remains unavailable and no broader Phase C
checkpoint may begin.

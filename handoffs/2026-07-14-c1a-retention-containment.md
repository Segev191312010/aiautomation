# Handoff: C1A Emergency Retention Containment

Date: 2026-07-14

Status: local candidate complete; same-source public CI required

## Scope and Authority

The owner authorized emergency C1A implementation/verification, a clean
conventional commit, and push to public `origin/master`. No other C0-C12
implementation is authorized. After C1A passes public CI, the owner separately
authorized protected `master` as the GitHub default and read-only triage of PRs
#1, #3, and #4 without merging disconnected histories.

## Completed

- Added a stable `RETENTION_DISABLED_C1A` fail-closed service exception.
- Blocked cleanup, preview, stats, archive creation/deletion, table/Parquet
  cleanup, `VACUUM`, and all operational CLI modes before storage access.
- Preserved authenticated 401 behavior and exposed stable authenticated 503s.
- Kept constant policy and read-only backup-list surfaces available.
- Suspended automatic diagnostics-news deletion.
- Suspended terminal direct-candidate deletion while preserving queued/draining
  TTL expiration.
- Replaced four enabled-retention tests with 23 zero-mutation containment tests.
- Recorded Phase B B12 owner acceptance.

## Verification

- Focused C1A: 23 passed.
- Focused C1A plus startup safety: 46 passed.
- Backend full suite: 739 passed, no skips/xfails/warnings.
- Dashboard typecheck: pass.
- Dashboard build: pass, 617 modules transformed.
- Dashboard Vitest: 31 files and 389 tests passed.
- Internal source review: no CRITICAL or HIGH finding.

The first backend checkpoint intentionally exposed four stale expectations; the
other 716 tests passed. The first dashboard attempt was environment-blocked by
an absent `node_modules`; exact lockfile installation followed by two complete
dashboard gate runs passed. Details are in the dated C1A evidence record.

## Known Boundaries

- The old destructive implementation remains unreachable behind guards so C1A
  is a containment patch, not the C1/C7 retention rewrite.
- Retention stats are disabled instead of being reimplemented query-only; this
  is one of the two explicitly accepted C1A designs and is the narrower change.
- Known npm audit findings remain a release blocker outside this emergency
  patch.
- C1A does not approve live operation or any additional Phase C work.

## Next Authorized Actions

1. Commit the clean candidate and push it to `origin/master`.
2. Require both GitHub Actions jobs to pass on the exact C1A commit.
3. Record exact CI identity and final C1A PASS in a documentation-only commit.
4. After that commit's CI passes, protect `master`, require the backend and
   dashboard checks, and set `master` as GitHub's default.
5. Preserve disconnected `main` and its archive tag.
6. Inspect and record a separate disposition for PRs #1, #3, and #4; do not
   merge the unrelated histories.
7. Stop. Do not begin C0-C12 implementation without new owner authority.

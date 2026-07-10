# Phase A Completion Tracker

Date opened: 2026-07-09
Phase: A - Truth, Safety, and Product Consolidation

## Runbook Usage

This table records the signed Phase A completion results from 2026-07-10. A
historical `PASS` is not an automatic pass for a later commit.

1. Start from a clean `master` that equals `origin/master`; record the full
   commit and environment versions. Never discard local work with
   `git reset --hard` to prepare a checker run.
2. Follow `docs/PHASE_A_VERIFICATION.md` in A0-A12 order. Its PowerShell blocks
   assert native exit codes, expected-empty searches, workspace shape, and
   archive-tag identity.
3. For each row, verify the listed files/evidence still exist, run the exact
   commands, perform the manual checks, and record `PASS`, `FAIL`, or `BLOCKED`
   in a new dated re-verification report.
4. A lower test count, missing test case, unexplained search hit, or failed
   manual invariant is a Phase A regression even when the historical command
   remains green.
5. Fix a regression before Phase B work continues, then rerun the affected
   stage, all downstream stages, and the global gates from a new clean commit.
   Do not rewrite dated completion evidence to conceal the failure.

## Latest Manual Audit

Audit date: 2026-07-10

Current re-verification status: **TECHNICAL AND ADMINISTRATIVE PASS; PHASE B
DEFERRED PENDING JOINT PLAN**.

Executing the manual found the A5/A6 stale-lock race and an A8 persisted-mode
startup bypass. Tested source
`e9ea6de6f43c6deffa0e7284ab9c00cfe2418df1` replaces PID/unlink ownership with a
persistent OS-held v2 lock, adds deterministic contender/subprocess/crash
coverage, and forces authority `OFF` when persisted guardrail validation or its
strict DB read fails. Clean-source local gates pass at backend `640`, dashboard
`372`, typecheck/build, and hygiene. GitHub Actions run `29091445438` passed
both Ubuntu jobs on the same source.

The supported lock invariant is one owner per shared lock path in one
OS/filesystem lock namespace, not one machine-global owner across users,
native/container boundaries, or distinct volumes. Stop every v1 runtime before
v2 starts; rolling coexistence is unsupported. Full commands, stage results,
limitations, reviews, and CI links are in
`docs/release-evidence/2026-07-10-phase-a-reverification.md`.

A later current-workspace audit found the ignored, signed Interactive Brokers
installer `ntws-latest-standalone-windows-x64.exe` at the repository root. It
was never executed by the checker and was moved, hash-preserving, to a dated
quarantine directory under `$env:USERPROFILE\Downloads`. The policy checker,
tracked scan, hidden/ignored scan, backend `640`, dashboard `372`, typecheck,
and build all passed again after quarantine. Artifact metadata and disposition
are recorded in
`docs/release-evidence/2026-07-10-a1-a2-late-binary-quarantine.md`; owner
owner approval of that disposition was recorded in-thread on `2026-07-10` and
closes the A12 follow-up.

The expanded 11-suffix policy is immutable commit
`2b4db50101b6202eb7ac0a1d631264a122ea961d`. All 11 negative probes and global
gates passed locally, and GitHub Actions run `29099407063` passed both Ubuntu
jobs on that same commit.

## Latest Re-verification Results

| Stage | Result | Current proof |
| --- | --- | --- |
| A0 | PASS | clean/synced source; full gates |
| A1 | PASS - REMEDIATED | late ignored TWS installer quarantined; tracked and hidden/ignored 11-suffix scans now zero; dated evidence recorded |
| A2 | PASS | checker rejected the ignored installer; clean scan and all 11 negative suffix probes pass; policy-source Ubuntu CI passes |
| A3 | PASS | launch-path inventory reconciled |
| A4 | PASS | 4 launch-manifest tests |
| A5 | PASS UNDER DOCUMENTED SCOPE; ACCEPTANCE PENDING | OS-held v2 lock proves one owner per shared local lock path/namespace, not the original literal machine-global wording |
| A6 | PASS | 22 lock tests, 4 lifespan tests, Ubuntu backend CI |
| A7 | PASS | 41 targeted tests and retired-ID scan |
| A8 | PASS | 73 backend tests, typecheck, 14 UI tests |
| A9 | PASS | canonical workspace shape |
| A10 | PASS | archive tag, 2 backend and 14 UI tests, global gates |
| A11 | PASS | untracked dist, clean build, doc truth checks |
| A12 | PASS | technical evidence complete; owner approval recorded; Phase B intentionally deferred pending joint planning |

## Signed Completion Results (Historical)

| Stage ID | Goal | Files touched | Evidence collected | Test command | Result | Follow-up if failed |
| --- | --- | --- | --- | --- | --- | --- |
| A0 | Freeze and baseline | `docs/release-evidence/2026-07-phase-a-baseline.md` | parent/nested commits, tree status, tracked dist baseline, gate results | `cd backend; python -m pytest tests/ -q`; `cd dashboard; npm run typecheck`; `cd dashboard; npm run build`; `cd dashboard; npx vitest run`; nested dashboard commands are historical-only after A10 removal and are preserved by the archive tag | PASS | none |
| A1 | Inventory workspace binaries | `docs/release-evidence/2026-07-workspace-inventory.md` | hashes, sizes, signatures, Git state, disposition buckets | `git ls-files \| rg -i '\.(dll\|exe\|msi\|zip\|rar\|7z\|dmg\|pkg)$'`; `python scripts/check_workspace_hygiene.py`; independent all-file scan in the verification manual | PASS | quarantine and document any new hit; never execute it |
| A2 | Quarantine binaries and add hygiene policy | `.gitignore`, `docs/DEVELOPMENT.md`, `scripts/check_workspace_hygiene.py` | quarantine path, clean scan, fake DLL probe | `python scripts/check_workspace_hygiene.py` | PASS | CI wiring can be considered later |
| A3 | Inventory backend launch paths | `docs/release-evidence/2026-07-launch-path-inventory.md` | startup matrix with worker count, host bind, reload flag, intended environment | `rg -n -e "--workers" -e "WORKERS" -e "uvicorn main:app" -e "uvicorn.run" -e "gunicorn" Dockerfile backend/Dockerfile docker-compose.yml README.md docs/DEPLOYMENT.md sessions/phase2-paper-soak-runbook.md backend/main.py .github/workflows/ci.yml dashboard/nginx.conf` | PASS | none |
| A4 | Force one-worker runtime | `Dockerfile`, `backend/Dockerfile`, `docker-compose.yml`, `README.md`, `docs/DEPLOYMENT.md`, `backend/startup.py`, `backend/tests/test_launch_manifests.py` | Docker/compose/docs edits, startup worker log, launch-manifest regression test | `cd backend; python -m pytest tests/test_launch_manifests.py -q` | PASS - 3 passed | fix any multi-worker manifest or doc reference |
| A5 | Add runtime process lock | `.gitignore`, `backend/config.py`, `backend/main.py`, `backend/runtime_lock.py`, `docker-compose.yml`, `docs/release-evidence/2026-07-a5-a6-runtime-lock.md` | lock path, metadata schema, startup insertion point, stale-lock policy, shutdown release order | `cd backend; python -m py_compile runtime_lock.py main.py` | PASS | future desktop path moves to per-user app data |
| A6 | Test runtime lock and failure UX | `backend/tests/test_runtime_lock.py`, `backend/tests/test_startup_runtime_lock.py`, `docs/release-evidence/2026-07-a5-a6-runtime-lock.md` | unit tests plus lifespan-level startup refusal and release tests | `cd backend; python -m pytest tests/test_runtime_lock.py -q`; `cd backend; python -m pytest tests/test_startup_runtime_lock.py -q` | PASS - 9 passed; PASS - 3 passed | none |
| A7 | Replace retired Anthropic default | `backend/config.py`, `backend/ai_advisor.py`, `backend/ai_learning.py`, `backend/ai_model_router.py`, `backend/tests/test_startup_config.py`, `backend/tests/test_ai_learning.py`, `backend/tests/test_ai_replay.py`, `backend/ARCHITECTURE.md`, `docs/APPLICATION_READINESS_ROADMAP.md`, `docs/release-evidence/2026-07-a7-anthropic-defaults.md` | Anthropic docs checked, Sonnet default centralized, fallback default centralized, retired dated Sonnet default removed from runtime code | `cd backend; python -m pytest tests/test_startup_config.py tests/test_ai_learning.py tests/test_ai_replay.py -q` | PASS - 33 passed | A8 still needs startup capability validation |
| A8 | Add AI capability validation | `backend/ai_capability.py`, `backend/startup.py`, `backend/ai_guardrails.py`, `backend/autopilot_api.py`, `backend/api_contracts.py`, `backend/tests/test_ai_capability.py`, `backend/tests/test_startup_config.py`, `backend/tests/test_api_contracts.py`, `backend/tests/test_autopilot_mode_semantics.py`, `dashboard/src/types/advisor.ts`, `dashboard/src/components/autopilot/__tests__/autopilot.test.tsx`, `docs/release-evidence/2026-07-a8-ai-capability.md` | explicit disabled/unconfigured/invalid_model/ready/degraded states, startup guard, status contract fields, mode-flip rejection, TS type alignment | `cd backend; python -m pytest tests/test_ai_capability.py tests/test_startup_config.py tests/test_api_contracts.py tests/test_autopilot_mode_semantics.py -q`; `cd dashboard; npm run typecheck` | PASS - 67 passed; PASS | none |
| A9 | Decide canonical product surface | `docs/adr/0006-canonical-product-surface.md`, `docs/release-evidence/2026-07-a9-canonical-product-decision.md`, `docs/APPLICATION_READINESS_ROADMAP.md`, `docs/release-evidence/2026-07-phase-a-tracker.md` | ADR accepts parent `master`, `backend/`, and `dashboard/` as canonical; nested `aiautomation/` and legacy `frontend/` move to A10 migration/archive workflow | read-only repo and nested status checks | PASS | A10 migrates keepers and removes duplicate active surfaces |
| A10 | Migrate keepers and remove duplicate products | `dashboard/src/components/autopilot/AISystemPanel.tsx`, `dashboard/src/pages/AutopilotPage.tsx`, `dashboard/src/components/autopilot/__tests__/autopilot.test.tsx`, `backend/main.py`, `backend/tests/test_product_surface.py`, `.github/workflows/ci.yml`, `DOCUMENTATION.md`, `docs/DEPLOYMENT.md`, `docs/APPLICATION_READINESS_ROADMAP.md`, `docs/release-evidence/2026-07-a10-product-migration.md`, removed `frontend/trading.*` | keeper matrix, migrated AI System observability, archive tag `archive/aiautomation-v2-2026-07-a10`, nested working repo removed, legacy static route removed | targeted A10 tests plus full backend pytest, dashboard typecheck/build/Vitest, workspace hygiene | PASS - backend 620 passed; dashboard build passed; Vitest 372 passed; hygiene passed | none |
| A11 | Clean generated artifacts and truth-pass docs | `README.md`, `DOCUMENTATION.md`, `docs/baseline.md`, `docs/APPLICATION_READINESS_ROADMAP.md`, `docs/release-evidence/2026-07-a11-artifacts-doc-truth.md`, removed tracked `dashboard/dist/assets/*` generated files | `git ls-files dashboard/dist` empty; ignored `dashboard/dist/`; README/product status and current counts updated; stale `frontend/` setup commands removed | full backend pytest, dashboard typecheck/build/Vitest, workspace hygiene | PASS - backend 620 passed; dashboard build passed; Vitest 372 passed; hygiene passed | none |
| A12 | Final regression and evidence closeout | `docs/release-evidence/2026-07-phase-a-complete.md`, `docs/release-evidence/2026-07-phase-a-tracker.md`, `docs/PHASE_A_VERIFICATION.md` | final clean source state, final gate results, archive tag, deferrals, done checklist | full backend pytest, dashboard typecheck/build/Vitest, workspace hygiene | PASS - backend 620 passed; dashboard build passed; Vitest 372 passed; hygiene passed; owner/lead sign-off recorded | none |

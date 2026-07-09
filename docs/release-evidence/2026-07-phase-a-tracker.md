# Phase A Completion Tracker

Date opened: 2026-07-09
Phase: A - Truth, Safety, and Product Consolidation

| Stage ID | Goal | Files touched | Evidence collected | Test command | Result | Follow-up if failed |
| --- | --- | --- | --- | --- | --- | --- |
| A0 | Freeze and baseline | `docs/release-evidence/2026-07-phase-a-baseline.md` | parent/nested commits, tree status, tracked dist baseline, gate results | backend pytest, dashboard typecheck/build/Vitest, nested typecheck/build/tests | PASS | none |
| A1 | Inventory workspace binaries | `docs/release-evidence/2026-07-workspace-inventory.md` | hashes, sizes, signatures, Git state, disposition buckets | binary extension scan | PASS | none |
| A2 | Quarantine binaries and add hygiene policy | `.gitignore`, `docs/DEVELOPMENT.md`, `scripts/check_workspace_hygiene.py` | quarantine path, clean scan, fake DLL probe | `python scripts/check_workspace_hygiene.py` | PASS | CI wiring can be considered later |
| A3 | Inventory backend launch paths | `docs/release-evidence/2026-07-launch-path-inventory.md` | startup matrix with worker count, host bind, reload flag, intended environment | `rg -n -e "--workers" -e "WORKERS" -e "uvicorn main:app" -e "uvicorn.run" -e "gunicorn" Dockerfile backend/Dockerfile docker-compose.yml README.md docs/DEPLOYMENT.md sessions/phase2-paper-soak-runbook.md backend/main.py .github/workflows/ci.yml dashboard/nginx.conf` | PASS | none |
| A4 | Force one-worker runtime | `Dockerfile`, `backend/Dockerfile`, `docker-compose.yml`, `README.md`, `docs/DEPLOYMENT.md`, `backend/startup.py`, `backend/tests/test_launch_manifests.py` | Docker/compose/docs edits, startup worker log, launch-manifest regression test | `cd backend; python -m pytest tests/test_launch_manifests.py -q` | PASS - 3 passed | fix any multi-worker manifest or doc reference |
| A5 | Add runtime process lock | not started | none | not run | TODO | implement lock before stateful startup |
| A6 | Test runtime lock and failure UX | not started | none | not run | TODO | unit and startup failure tests |
| A7 | Replace retired Anthropic default | not started | A0 warning proves blocker | not run | TODO | centralize supported default model |
| A8 | Add AI capability validation | not started | none | not run | TODO | explicit disabled/unconfigured/invalid/ready/degraded states |
| A9 | Decide canonical product surface | not started | roadmap recommends parent repo plus `dashboard/` | not run | TODO | ADR and owner sign-off |
| A10 | Migrate keepers and remove duplicate products | not started | none | not run | TODO | migrate value into canonical dashboard |
| A11 | Clean generated artifacts and truth-pass docs | not started | A0 records tracked `dashboard/dist` files | not run | TODO | untrack generated output and correct docs |
| A12 | Final regression and evidence closeout | not started | none | not run | TODO | final evidence report and sign-off |

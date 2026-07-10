# Phase A11 Artifact Cleanup and Documentation Truth Pass

Date: 2026-07-10

## Generated Artifact Cleanup

`dashboard/dist/` is build output and is ignored by `.gitignore`. A11 removed
the four previously tracked generated files from Git while leaving local build
output available on disk.

Untracked from Git:

```text
dashboard/dist/assets/charts-BUSuRXK4.js
dashboard/dist/assets/charts-BUSuRXK4.js.map
dashboard/dist/assets/conditionHelpers-DZZ3Eyvm.js
dashboard/dist/assets/conditionHelpers-DZZ3Eyvm.js.map
```

Verification:

```text
git ls-files dashboard/dist
<no output>

git status --short --ignored dashboard/dist
D  dashboard/dist/assets/charts-BUSuRXK4.js
D  dashboard/dist/assets/charts-BUSuRXK4.js.map
D  dashboard/dist/assets/conditionHelpers-DZZ3Eyvm.js
D  dashboard/dist/assets/conditionHelpers-DZZ3Eyvm.js.map
!! dashboard/dist/
```

The `D` entries are the intended index removals for the formerly tracked files.
The `!! dashboard/dist/` entry proves new build output is ignored.

## Documentation Truth Pass

Updated:

- `README.md`: product status dated 2026-07-10, current test counts, canonical
  `backend/` and `dashboard/`, no desktop app yet, Docker as optional rather
  than the normal desktop launch path.
- `DOCUMENTATION.md`: setup commands now use `dashboard/`; overview names the
  canonical dashboard.
- `docs/baseline.md`: 2026-03 numbers are labeled as historical, and the
  current Phase A11 checkpoint records backend 620 and dashboard 372.
- `docs/APPLICATION_READINESS_ROADMAP.md`: Phase A checklist and current gate
  counts reflect A10/A11 reality.

Search checks:

```text
rg -n "cd frontend|COPY frontend|frontend/package|frontend-build|serve_legacy_frontend|StaticFiles" DOCUMENTATION.md docs README.md .github backend dashboard -g "!docs/release-evidence/2026-07-a11-artifacts-doc-truth.md"
<no output>
```

## Full Gate Verification

```text
cd backend; python -m pytest tests/ -q
620 passed

cd dashboard; npm run typecheck
PASS

cd dashboard; npm run build
PASS - Vite built 610 modules

cd dashboard; npx vitest run
27 files passed, 372 tests passed

python scripts/check_workspace_hygiene.py
Workspace hygiene OK
```

## Pending Phase A Work

A12 remains open. It will run and record the final closeout evidence after this
A11 commit lands.

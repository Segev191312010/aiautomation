# Phase A Baseline Evidence

Date: 2026-07-09
Phase: A - Truth, Safety, and Product Consolidation
Stage: A0 - Freeze and baseline

## Scope

This note captures the before-state for Phase A before runtime safety,
AI-default cleanup, and product consolidation work. It is a dated evidence
point for comparing later Phase A changes.

## Branches and Commits

| Repository | Branch | Commit | Sync state |
| --- | --- | --- | --- |
| parent repo | master | 68119ce63e3e5d257167a94ce0f2823e0b308170 | synced with origin/master |
| nested aiautomation | main | 16280057ab04bee97904e9c59b9a5143a58bb673 | synced with origin/main |

Parent working tree before Phase A edits:

```text
## master...origin/master
?? Dismays_Chameleon_Tool_2.2.1_[unknowncheats.me]_.dll
```

Nested `aiautomation/` working tree before Phase A edits:

```text
## main...origin/main
```

## Product Shape

| Path | Baseline status |
| --- | --- |
| `backend/` | Active FastAPI backend |
| `dashboard/` | Active canonical dashboard candidate |
| `frontend/` | Legacy frontend candidate still present |
| `aiautomation/` | Nested dashboard repo still present and ignored by parent Git |
| `dashboard/dist/` | Build output ignored, but four generated files are still tracked |
| root binary artifacts | Multiple local installer/tool binaries present beside source |

Tracked `dashboard/dist/` files at baseline:

```text
dashboard/dist/assets/charts-BUSuRXK4.js
dashboard/dist/assets/charts-BUSuRXK4.js.map
dashboard/dist/assets/conditionHelpers-DZZ3Eyvm.js
dashboard/dist/assets/conditionHelpers-DZZ3Eyvm.js.map
```

## Quality Gate Baseline

Commands were run from the current workspace on 2026-07-09.

| Area | Command | Result |
| --- | --- | --- |
| backend | `cd backend; python -m pytest tests/ -q` | PASS - 582 passed, 1 warning |
| canonical dashboard | `cd dashboard; npm run typecheck` | PASS |
| canonical dashboard | `cd dashboard; npm run build` | PASS - Vite built 609 modules |
| canonical dashboard | `cd dashboard; npx vitest run` | PASS - 27 files, 370 tests |
| nested dashboard | `cd aiautomation; npm run typecheck` | PASS |
| nested dashboard | `cd aiautomation; npm run build` | PASS - Vite built 382 modules |
| nested dashboard | `cd aiautomation; npm test` | PASS - 5 files, 11 tests |

Backend warning preserved for Phase A follow-up:

```text
DeprecationWarning: The model 'claude-sonnet-4-20250514' is deprecated and will reach end-of-life on June 15th, 2026.
```

## Baseline Findings Confirmed

- Backend and both dashboards are currently runnable by their existing gates.
- The parent repo is clean except for the untracked unsigned DLL.
- The nested `aiautomation/` repo is clean and synced to its remote branch.
- `dashboard/dist/` is ignored for new output, but four generated files are still tracked.
- Phase A must still close workspace binary clutter, multi-worker startup defaults,
  duplicate runtime prevention, retired Anthropic defaults, and canonical product
  consolidation.


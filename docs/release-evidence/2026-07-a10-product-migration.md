# Phase A10 Product Migration and Repo Cleanup

Date: 2026-07-10

## Decision Carried Forward

ADR `0006-canonical-product-surface` remains the controlling decision:

- parent `master` is canonical;
- `backend/` is the canonical runtime;
- `dashboard/` is the canonical operator UI;
- nested `aiautomation/` is an archive/source reference after keeper migration;
- legacy `frontend/` is retired after verification.

## Keeper Matrix

| Surface | Finding | Disposition |
| --- | --- | --- |
| `dashboard/` | Broadest product coverage, existing authenticated API client, 370-test Vitest suite, active route/layout patterns. | Keep as the only active dashboard. |
| `aiautomation/src/pages/AiSystemPage.tsx` | Principal nested-only product value: read-only AI pipeline observability. | Migrated into canonical `dashboard/src/components/autopilot/AISystemPanel.tsx` and exposed as the Autopilot `System` tab. |
| Other nested `aiautomation/src` pages/components | Smaller v2 duplicates of dashboard pages already present in canonical `dashboard/`. | Archived with nested repo; not copied into canonical UI. |
| Legacy `frontend/trading.*` | Static HTML/CSS/JS app with duplicated dashboard, rules, market, watchlist, and trade-journal flows. Canonical dashboard already owns those product areas with tests and typed services. | Removed tracked files and backend `/trading` + `/static` legacy serving. |
| CI and setup docs | CI already ran from `dashboard/`; job name and one setup snippet still said frontend. | Renamed CI job to Dashboard and updated setup/deployment snippets to use `dashboard/`. |

## Migration Details

The migrated AI System view uses existing canonical data:

- `/api/autopilot/status` via `AIStatus`;
- audit feed from `useAutopilotStore`;
- learning metrics and economic report from existing Autopilot store loads.

The nested route/auth shell was not copied. Controls remain on the canonical
Autopilot page; the migrated panel is read-only observability.

## Archive and Removal Evidence

Nested v2 dashboard archive:

```text
git -C aiautomation status --short --branch
## main...origin/main

git -C aiautomation log -1 --oneline
1628005 docs(dashboard): document v2 setup

git -C aiautomation push origin archive/aiautomation-v2-2026-07-a10
[new tag] archive/aiautomation-v2-2026-07-a10 -> archive/aiautomation-v2-2026-07-a10
```

The nested working repository was then removed from the active workspace.

Legacy static UI cleanup:

- deleted `frontend/trading.html`;
- deleted `frontend/trading.css`;
- deleted `frontend/trading.js`;
- removed backend `/trading` route;
- removed backend `/static` mount for the legacy UI;
- added `backend/tests/test_product_surface.py` to prove `/trading` is no
  longer active and `/app` remains active.

## Targeted Verification

```text
cd backend; python -m pytest tests/test_product_surface.py -q
2 passed

cd dashboard; npx vitest run src/components/autopilot/__tests__/autopilot.test.tsx
14 passed

cd dashboard; npm run typecheck
PASS
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

## A10 Result

A10 closes the active product split:

- only `dashboard/` remains as the active dashboard in the workspace;
- nested v2 work is archived by tag and no longer present as a working repo;
- legacy `frontend/` files are removed from tracked source;
- AI System observability is preserved in canonical dashboard patterns.

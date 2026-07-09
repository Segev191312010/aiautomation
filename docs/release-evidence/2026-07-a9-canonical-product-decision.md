# Phase A9 Canonical Product Decision Evidence

Date: 2026-07-09
Phase: A - Truth, Safety, and Product Consolidation
Stage: A9 - Canonical product decision

## Goal

Remove ambiguity about which branch, backend, and dashboard are active before
product migration and duplicate-surface cleanup begins.

## Decision Artifact

`docs/adr/0006-canonical-product-surface.md` records the accepted Phase A
decision:

- parent repo `master` is canonical;
- `backend/` is the canonical runtime;
- `dashboard/` is the canonical operator UI;
- nested `aiautomation/` is source reference only until keepers are migrated;
- legacy `frontend/` is retire/archive candidate after verification.

## Evidence Collected

- Parent repo is on `master` with A8 pushed.
- Nested `aiautomation/` is clean on `main` and synced with `origin/main`.
- Existing roadmap already identifies `dashboard/` as the most complete UI with
  370 passing tests and names `AiSystemPage.tsx` as the principal nested-only
  keeper candidate.
- ADR 0006 sets the no-parallel-dashboard policy and assigns migration/removal
  to A10.

## Verification

Read-only commands:

```text
git status --short --branch
git -C aiautomation status --short --branch
git -C aiautomation log --oneline -3
rg -n "canonical|frontend/|dashboard/|aiautomation|nested|duplicate|ADR|A9" docs README.md .github sessions -g "*.md" -g "*.yml" -g "*.yaml"
```

Result:

```text
parent: clean on master
nested: clean on main...origin/main
latest nested commit: 1628005 docs(dashboard): document v2 setup
roadmap: parent master + dashboard/ already named as canonical recommendation
```

Full gate commands:

```text
cd backend
python -m pytest tests/ -q

cd dashboard
npm run typecheck
npm run build
npx vitest run

python scripts/check_workspace_hygiene.py
```

Result:

```text
backend: 617 passed
dashboard typecheck: passed
dashboard build: passed
dashboard vitest: 370 passed
workspace hygiene: passed
```

## Deferred

A10 remains open. It will migrate keeper features, archive the nested history,
remove duplicate product surfaces from active development, and clean CI
references.

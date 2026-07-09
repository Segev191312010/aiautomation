# ADR 0006: Canonical Product Surface

**Status:** Accepted
**Date:** 2026-07-09
**Supersedes:** N/A
**Depends on:** Phase A baseline evidence

## Context

Phase A found three user-interface/product surfaces in the workspace:

- `dashboard/` - the parent repo React + TypeScript operator dashboard.
- `frontend/` - a tracked legacy static HTML/CSS/JavaScript surface.
- `aiautomation/` - a nested Git repository with its own `main` history and v2
  dashboard work.

This split makes desktop packaging, CI, API contract repair, and operator
documentation ambiguous. It is easy to fix the wrong UI, test the wrong app, or
ship a backend contract against a product surface that is not the intended
desktop candidate.

The Phase A baseline records that `dashboard/` has the broadest current product
coverage and 370 passing Vitest tests. The nested `aiautomation/` repo has 11
passing tests and contains useful v2 ideas, especially AI System observability,
but it is a separate Git history inside the parent workspace. The legacy
`frontend/` surface is not the tested canonical dashboard.

## Decision

The canonical product line is:

| Domain | Canonical path/branch | Policy |
| --- | --- | --- |
| Source branch | parent repo `master` | All Phase B+ product work lands here unless a later ADR changes it. |
| Backend runtime | `backend/` | The FastAPI runtime remains the only active backend. |
| Operator UI | `dashboard/` | The React + TypeScript dashboard is the only active UI target. |
| Nested v2 dashboard | `aiautomation/` | Source reference only until keepers are migrated, then archive/remove from active workspace. |
| Legacy static UI | `frontend/` | Retire/archive after confirming no unmigrated product value remains. |

No second dashboard may continue as an active product surface without a new ADR.

## Migration Rules

A10 owns migration and cleanup. It must migrate product value, not directory
structure.

Required A10 behavior:

- Build a keeper matrix for `dashboard/`, `aiautomation/`, and `frontend/`.
- Port wanted nested-only behavior into `dashboard/` using existing dashboard
  architecture and tests.
- Treat `aiautomation/src/pages/AiSystemPage.tsx` as the first explicit keeper
  candidate for AI observability.
- Preserve the nested `main` history through a branch/tag/archive note before
  removing the nested working repository from the active tree.
- Remove or archive `frontend/` only after its value has been checked against
  the canonical dashboard.
- Keep CI pointed at the parent backend and `dashboard/`; do not add new CI for
  retired surfaces.

## Consequences

### Positive

- Desktop packaging has one frontend target.
- API contract repair has one UI consumer to satisfy first.
- Future tests and screenshots refer to one product surface.
- Nested v2 work can still be mined deliberately instead of maintained as a
  parallel product.

### Negative

- Useful nested UI ideas must be reimplemented or adapted into `dashboard/`
  rather than copied wholesale.
- Any developer who preferred the nested v2 app must now justify that direction
  through a new ADR.
- A10 needs careful migration evidence before deleting duplicate product code.

## Rejected Alternatives

**Alternative A: Keep `dashboard/` and `aiautomation/` active in parallel.**
Rejected because Phase A's mission is one truthful codebase. Parallel dashboards
would preserve the ambiguity this phase is meant to remove.

**Alternative B: Make `aiautomation/` canonical because it has newer v2 work.**
Rejected because it is a nested unrelated Git history with narrower coverage and
fewer tests. Its keeper features should be migrated into the parent product.

**Alternative C: Keep `frontend/` as a lightweight fallback UI.** Rejected
because an untested fallback UI becomes another product contract to maintain.
Fallback behavior should be built into `dashboard/` or documented as absent.

## Links

- `docs/release-evidence/2026-07-phase-a-baseline.md`
- `docs/APPLICATION_READINESS_ROADMAP.md`
- `docs/release-evidence/2026-07-phase-a-tracker.md`
- `dashboard/`
- `aiautomation/src/pages/AiSystemPage.tsx`
- `frontend/`

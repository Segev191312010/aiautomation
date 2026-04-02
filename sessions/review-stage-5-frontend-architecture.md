AI AUTOPILOT — STAGE 5 FRONTEND ARCHITECTURE
============================================
DATE: 2026-03-27 (updated 2026-03-31 with audit findings)
STATUS: READY AFTER STAGE 0 AND PARALLEL WITH STAGE 4 WHERE OWNERSHIP ALLOWS

AUDIT UPDATE (2026-03-31)
-------------------------
Full codebase audit found 6 issues in this stage. 0 fixed (frontend not modified), all OPEN.
See: `sessions/audit-findings-2026-03-31.md` (section: STAGE 5)

OPEN (do in Stage 5/6):
  - F5-01: 43 uses of `any` type across 22 files (PositionsTable, crosshairSync, etc.)
  - F5-02: No per-page error boundaries — one crash blanks the whole app
  - F5-03: Standard rules still use raw condition JSON (RulesPage.tsx:507)
  - F5-04: Risk events store slice is hardcoded empty stub (store/index.ts:1618)
  - F5-05: Autopilot page missing decision drilldown/replay flows
  - F5-06: Canvas charts lack accessibility fallbacks
OWNER: FRONTEND CORE TEAM
GOAL: Shrink page monoliths, split the API layer by domain, standardize async state handling, and make the dashboard easier to extend safely.

PURPOSE
-------
Stage 5 exists because too much frontend logic still lives in a small number of oversized files.

Current pressure points:
- `dashboard/src/services/api.ts` is too large and cross-domain
- route pages still mix data loading, state orchestration, transforms, and rendering
- async states are inconsistent across pages
- some operator pages still degrade silently instead of explicitly

STAGE 5 IS NOT:
---------------
- A full redesign
- A component-library rewrite
- Pure visual polish

STAGE 5 IS:
-----------
- Better module boundaries
- Better reuse
- Better async truthfulness
- Easier page-by-page maintenance

GLOBAL EXIT GATE
----------------
Stage 5 is complete only when:
[ ] API calls are domain-scoped instead of one-file monoliths
[ ] major route pages are thinner composition shells
[ ] domain hooks own loading/mutation logic where appropriate
[ ] async state vocabulary is consistent across major pages
[ ] frontend tests still pass after the architecture split

================================================================
PHASE 5.1 — SPLIT THE API CLIENT BY DOMAIN
================================================================
SCOPE
- Break `dashboard/src/services/api.ts` into domain-specific modules

FILES
- [ ] dashboard/src/services/api.ts
- [ ] dashboard/src/services/http.ts (new, recommended)
- [ ] dashboard/src/services/api/auth.ts (new)
- [ ] dashboard/src/services/api/market.ts (new)
- [ ] dashboard/src/services/api/rules.ts (new)
- [ ] dashboard/src/services/api/alerts.ts (new)
- [ ] dashboard/src/services/api/analytics.ts (new)
- [ ] dashboard/src/services/api/stockProfile.ts (new)
- [ ] dashboard/src/services/api/autopilot.ts (new)
- [ ] dashboard/src/services/api/diagnostics.ts (new)
- [ ] dashboard/src/services/api/simulation.ts (new)

TASKS
1) Shared transport layer
   [ ] Keep one shared fetch wrapper.
   [ ] Keep one shared error-normalization helper.
   [ ] Keep auth/header logic centralized.

2) Domain client split
   [ ] Move methods into domain files without changing endpoint behavior.
   [ ] Preserve typed contracts.

3) Compatibility layer
   [ ] If needed, keep a short compatibility export surface during the transition.

DELIVERABLE
- [ ] Frontend API calls are organized by domain instead of one giant file.

================================================================
PHASE 5.2 — ADD DOMAIN HOOKS
================================================================
SCOPE
- Move page-owned loading/mutation logic into reusable hooks

FILES
- [ ] dashboard/src/hooks/useAutopilot*.ts (new)
- [ ] dashboard/src/hooks/useAnalytics*.ts (new)
- [ ] dashboard/src/hooks/useMarketData*.ts (new)
- [ ] dashboard/src/hooks/useRules*.ts (new)
- [ ] dashboard/src/hooks/useAlerts*.ts (new)
- [ ] dashboard/src/hooks/useSimulation*.ts (new)

TASKS
1) Hook ownership
   [ ] Hooks own fetching.
   [ ] Hooks own mutation state.
   [ ] Hooks own derived async/degraded state where reasonable.

2) Avoid hook sprawl
   [ ] Split by domain, not by every tiny endpoint.
   [ ] Keep hook names explicit.

3) Preserve route simplicity
   [ ] Pages should consume hooks, not reimplement the same orchestration.

DELIVERABLE
- [ ] Pages stop owning most domain fetch/mutation orchestration directly.

================================================================
PHASE 5.3 — STANDARDIZE ASYNC STATE VOCABULARY
================================================================
SCOPE
- Make loading/error/empty/degraded behavior consistent across the dashboard

FILES
- [ ] dashboard/src/components/common/*
- [ ] dashboard/src/pages/*.tsx
- [ ] dashboard/src/hooks/*

TASKS
1) Shared vocabulary
   [ ] loading
   [ ] empty
   [ ] error
   [ ] degraded

2) Shared UI helpers
   [ ] Add or refine shared components for section error/degraded states.
   [ ] Reuse them across analytics, market, autopilot, and stock profile pages.

3) Kill hidden fallback behavior
   [ ] Do not silently use fake or stale-looking-real values in operator views.

DELIVERABLE
- [ ] Async state behavior is understandable and consistent across major pages.

================================================================
PHASE 5.4 — SHRINK ROUTE PAGES
================================================================
SCOPE
- Turn heavy route files into composition shells

FILES
- [ ] dashboard/src/pages/AnalyticsPage.tsx
- [ ] dashboard/src/pages/AutopilotPage.tsx
- [ ] dashboard/src/pages/TradeBotPage.tsx
- [ ] dashboard/src/pages/MarketRotationPage.tsx
- [ ] dashboard/src/pages/MarketPage.tsx
- [ ] dashboard/src/pages/ScreenerPage.tsx
- [ ] dashboard/src/pages/BacktestPage.tsx
- [ ] dashboard/src/pages/StockProfilePage.tsx
- [ ] dashboard/src/pages/AlertsPage.tsx
- [ ] dashboard/src/pages/SettingsPage.tsx
- [ ] dashboard/src/pages/SimulationPage.tsx
- [ ] dashboard/src/pages/Dashboard.tsx

TASKS
1) Keep route shell responsibilities only
   [ ] route layout
   [ ] panel composition
   [ ] top-level navigation/state handoff

2) Move everything else out where practical
   [ ] data loading into hooks
   [ ] transforms into helpers
   [ ] large render blocks into panel components

3) Avoid giant “one more prop” patterns
   [ ] Group related state into panel-level hooks when helpful.

DELIVERABLE
- [ ] Route pages become easier to scan, test, and own.

================================================================
PHASE 5.5 — FRONTEND VALIDATION GATE
================================================================
SCOPE
- Ensure the frontend architecture cleanup does not destabilize the product

FILES
- [ ] dashboard/src/components/**/*.test.tsx
- [ ] dashboard/src/pages/**/*.tsx
- [ ] dashboard/src/services/**/*.ts
- [ ] dashboard/src/hooks/**/*.ts

TASKS
1) Type safety
   [ ] Keep all types aligned after API/client splitting.

2) Build safety
   [ ] Ensure dead imports and stale route wiring are removed.

3) Test coverage
   [ ] Add tests where async/degraded behavior was previously implicit.

VALIDATION COMMANDS
- [ ] `cd dashboard && npm run typecheck`
- [ ] `cd dashboard && npm run build`
- [ ] `cd dashboard && npx vitest run`

STAGE 5 FINAL CHECKLIST
-----------------------
[ ] Phase 5.1 complete
[ ] Phase 5.2 complete
[ ] Phase 5.3 complete
[ ] Phase 5.4 complete
[ ] Phase 5.5 complete

Once all boxes are checked, Stage 5 is DONE and frontend architecture becomes modular enough to support the page rebuilds in Stage 6 cleanly.

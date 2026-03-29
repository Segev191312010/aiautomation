AI AUTOPILOT — STAGE 0 BASELINE, TRUTH, AND REPO HYGIENE
========================================================
DATE: 2026-03-27
STATUS: AUTOPILOT LIVE | TESTS GREEN
OWNER: CORE TEAM
GOAL: Make the codebase honest and clean before adding more autonomy.

PURPOSE
-------
Stage 0 exists to make the codebase tell the truth before it becomes more advanced.

Right now:
- All tests pass.
- Autopilot is live.
- But some behaviors and docs are out of sync with reality.

Stage 0 fixes:
- Documentation drift
- Silent mock data in analytics
- Repo hygiene drift
- Under-signaled degraded modes
- Known exception/fallback hotspots (inventory only)

CURRENT BASELINE
----------------
At time of Stage 0 start:
- Backend tests: 392/392 passing
- Frontend typecheck: passing
- Frontend build: passing
- Frontend vitest: 74/74 passing

STAGE 0 IS NOT:
---------------
- Bug-firefighting
- New features
- Deep architecture refactors

STAGE 0 IS:
-----------
- Alignment
- Clarity
- Hygiene
- Baseline truth

GLOBAL EXIT GATE
----------------
Stage 0 is complete only when:
[ ] Docs match the current system
[ ] Analytics no longer uses hidden fake runtime values
[ ] Repo hygiene is materially improved
[ ] The current green validation baseline is recorded
[ ] Known exception/fallback hotspots are cataloged

================================================================
PHASE 0.1 — DOCUMENTATION TRUTH RESET
================================================================
SCOPE
- Update README.md
- Add short architecture summary
- Document real operating modes and main pages

FILES
- [ ] README.md
- [ ] sessions/review-roadmap-index.md
- [ ] docs/architecture.md (optional, but recommended)

TASKS
1) README core structure
   [ ] Open README.md
   [ ] Replace stale architecture section with real structure:
       - FastAPI backend
       - React + TypeScript dashboard
       - diagnostics
       - analytics
       - alerts
       - stock profile
       - autopilot control plane
       - decision ledger / evaluation plumbing
   [ ] Update “Tech stack” to match actual backend/frontend choices.

2) Operating modes documentation
   [ ] Add an “Operating Modes” section:
       - Broker environment: LIVE vs PAPER (IBKR)
       - SIM_MODE: what it does, when used
       - Autopilot authority: OFF, PAPER, LIVE
   [ ] Describe how these modes interact at high level.

3) AI / Autopilot status
   [ ] Add short section “AI & Autopilot”:
       - Stage 9 trade truth layer
       - Stage 10 decision/evaluation plumbing
   [ ] Make it clear AI is already part of the real decision loop, not only “advice”.

4) Roadmap index alignment
   [ ] Update `sessions/review-roadmap-index.md` so Stage 0 is summarized consistently.
   [ ] Link later stages back to Stage 0 as the truth-reset dependency.

5) docs/architecture.md
   [ ] Create docs/architecture.md if it does not exist.
   [ ] Add a short overview of:
       - high-level components
       - data flow: market data -> backend -> advisor/autopilot -> UI
       - where AI sits in the architecture

DELIVERABLE
- [ ] README and supporting docs that a new engineer can trust.

================================================================
PHASE 0.2 — REMOVE SILENT MOCK DECEPTION FROM ANALYTICS
================================================================
SCOPE
- Eliminate silent MOCK_* substitution from AnalyticsPage
- Keep graceful UX, but make failures explicit and visible

FILES
- [ ] dashboard/src/pages/AnalyticsPage.tsx
- [ ] dashboard/src/components/common/DegradedStateCard.tsx (new, recommended)
- [ ] dashboard/src/components/common/SectionErrorMessage.tsx (optional)
- [ ] dashboard/src/components/analytics/__tests__/... (as needed)

TASKS
1) Identify current mock behavior
   [ ] Locate all `MOCK_*` definitions and uses in AnalyticsPage.tsx.
   [ ] Confirm the current logic path:
       - live fetch fails -> mock numbers still render
   [ ] Record which analytics sections are affected.

2) Introduce explicit async / degraded states
   [ ] Define page-level or section-level state that can represent:
       - loading
       - loaded
       - partially_degraded
       - unavailable
   [ ] Avoid one undifferentiated boolean when sections can fail independently.

3) Shared degraded UI helper
   [ ] Create `DegradedStateCard.tsx` with:
       - title
       - reason/description
       - optional “what this means” copy
   [ ] Reuse the same helper across analytics sections.

4) Replace mock substitution with truthful UI
   [ ] Remove production code paths that silently inject MOCK_* values.
   [ ] If design-time mock data is still useful:
       - keep it behind explicit dev-only flags
       - label it as “Mock data (dev)” in the UI
   [ ] Render metrics only when backed by real data.

5) Section-level truthfulness
   [ ] For portfolio summary
   [ ] For daily P&L
   [ ] For exposure
   [ ] For risk limits
   [ ] For trade history
   [ ] For correlation
   [ ] Render degraded cards or empty-state messaging instead of fake numbers when data is missing.

6) Error visibility
   [ ] Log failures with section name and endpoint context.
   [ ] Do not swallow errors silently.
   [ ] Ensure state transitions into degraded/unavailable on failure.

7) Tests
   [ ] Add or update frontend tests for:
       - live data path renders metrics
       - failed fetch renders degraded UI
       - prod path does not use mock data silently

DELIVERABLE
- [ ] AnalyticsPage never lies to an operator when analytics data is unavailable.

================================================================
PHASE 0.3 — REPO HYGIENE AND ARTIFACT BOUNDARY CLEANUP
================================================================
SCOPE
- Separate source, generated output, and scratch tooling

FILES TO REVIEW
- [ ] .gitignore
- [ ] dashboard/dist/
- [ ] backend/_stage13.py
- [ ] backend/_stage13_appender.py
- [ ] backend/_stage13_code.py
- [ ] backend/_rm_append.py
- [ ] any similar temporary helpers

TASKS
1) Decide what should never be versioned
   [ ] Confirm build artifacts are ignored.
   [ ] Confirm logs, cache files, tmp outputs, and local experiments are ignored.

2) Create a scripts/tools boundary
   [ ] Create `backend/scripts/` or `backend/tools/`.
   [ ] Move any surviving one-off helpers there.
   [ ] Add a short header comment to each surviving dev helper:
       - DEV TOOL
       - NOT PRODUCTION CODE

3) Delete obsolete scratch files
   [ ] Review temporary backend helper files one by one.
   [ ] Delete files that are confirmed unused.
   [ ] Move needed ones into the scripts/tools area with better names.

4) Prevent runtime coupling to scratch files
   [ ] Confirm production modules do not import ad hoc helpers.
   [ ] Run tests after cleanup to prove nothing depended on them.

5) Document the boundary
   [ ] Add a short repo rule to README or CONTRIBUTING:
       - build artifacts do not belong in source review
       - ad hoc helpers live under scripts/tools

DELIVERABLE
- [ ] Build artifacts and scratch scripts no longer pollute the core source tree.

================================================================
PHASE 0.4 — BASELINE VALIDATION AND KNOWN-RISK INVENTORY
================================================================
SCOPE
- Lock the current green baseline
- Document the main current architectural risks

FILES
- [ ] README.md or docs/baseline.md
- [ ] sessions/review-roadmap-index.md
- [ ] docs/baseline.md (recommended)

TASKS
1) Record the validation commands
   [ ] Write the standard commands explicitly:
       - python -m pytest backend/tests -v
       - npm run typecheck
       - npm run build
       - npx vitest run
   [ ] Optionally note approximate runtime and prerequisites.

2) Baseline snapshot
   [ ] Add a note “Baseline as of 2026-03-27”.
   [ ] Include:
       - backend tests green
       - frontend tests green
       - autopilot live
       - roadmap begins from a green state

3) Known architectural risks
   [ ] Document the main risks:
       - backend/main.py monolith
       - backend/database.py monolith
       - replay/evaluator coupling
       - dashboard/src/services/api.ts monolith
       - silent degraded behavior in some UI surfaces
   [ ] Write each risk in 1–2 sentences.

4) Fallback policy snapshot
   [ ] Record what is acceptable:
       - explicit degraded UI
       - explicit missing-data indicators
   [ ] Record what is not acceptable:
       - fake numeric values presented as real
       - silent semantic fallback in operator views

DELIVERABLE
- [ ] Written baseline and risk inventory that later stages can reference.

================================================================
PHASE 0.5 — EXCEPTION-HANDLING AUDIT
================================================================
SCOPE
- Identify, not yet fully fix, the most dangerous swallow-and-continue paths

PRIORITY FILES
- [ ] backend/main.py
- [ ] backend/ai_optimizer.py
- [ ] backend/ai_guardrails.py
- [ ] backend/bot_runner.py

TAG SCHEMA
----------
For each hotspot, tag with:
- CRIT_TRADING  — can directly affect order/trade decisions
- CRIT_IO       — can hide data source or broker issues
- MEDIUM        — mid-level system behavior
- LOW           — safe to defer

TASKS
1) Search for dangerous patterns
   [ ] Search for `except Exception:`
   [ ] Search for `except Exception as e:`
   [ ] Search for broad excepts that do `pass`, `return None`, or silent fallback behavior.
   [ ] For each match, record:
       - file
       - function
       - line number
       - short impact statement
       - severity tag

2) Build the hotspot list
   [ ] Create `sessions/exception-hotspots-stage0.md`.
   [ ] Group hotspots by severity.
   [ ] Keep impact statements short and action-oriented.

3) Minimal annotations
   [ ] Add TODO markers only for the highest-risk items where useful.
   [ ] Do not change runtime behavior yet.

4) Link to later stages
   [ ] Point Stage 1, Stage 2, and Stage 3 to the hotspot list so they can consume it.

DELIVERABLE
- [ ] A clear map of swallowed exceptions and silent degradation paths, tagged by severity.

================================================================
STAGE 0 FINAL CHECKLIST
================================================================
[ ] Phase 0.1 complete
[ ] Phase 0.2 complete
[ ] Phase 0.3 complete
[ ] Phase 0.4 complete
[ ] Phase 0.5 complete

Once all boxes are checked, Stage 0 is DONE and later stages can proceed against a clean, truthful baseline.

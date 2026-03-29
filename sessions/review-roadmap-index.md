# 2026 Code Review Roadmap Index

This roadmap is based on a repo-level review of the current trading platform in `C:\Users\segev\sdvesdaW\trading`.

Current baseline at review time:
- Backend tests: `392/392` passing
- Frontend typecheck: passing
- Frontend build: passing
- Frontend vitest: `74/74` passing

This means the system is stable enough to refactor deliberately. The work below is about making the platform safer, clearer, and easier to evolve.

## The 9 Files

1. `review-roadmap-index.md`
2. `review-stage-0-baseline-and-truth.md`
3. `review-stage-1-backend-composition.md`
4. `review-stage-2-ai-autopilot-correctness.md`
5. `review-stage-3-trading-runtime-hardening.md`
6. `review-stage-4-data-diagnostics-hardening.md`
7. `review-stage-5-frontend-architecture.md`
8. `review-stage-6-page-rebuild-plan.md`
9. `review-stage-7-release-ops-docs.md`

## Stage 0 dependency rule

Stage 0 is the truth-reset dependency for every later stage.

Later stages assume Stage 0 has already done the following:
- docs match the current system
- fake operator-facing numeric fallback is removed
- repo hygiene boundaries exist
- the green baseline is recorded
- exception/fallback hotspots are cataloged

## Stage Order

### Stage 0 - Baseline, Truth, and Repo Hygiene
- Fix documentation drift.
- Remove misleading mock/fallback behavior.
- Establish the baseline that later stages must preserve.

### Stage 1 - Backend Composition
- Break up the biggest backend monoliths.
- Separate app bootstrap, routers, repositories, and domain contracts.

### Stage 2 - AI / Autopilot Correctness
- Finish the Stage 9/10 architecture.
- Decouple replay, evaluation, optimizer, and learning paths.

### Stage 3 - Trading Runtime Hardening
- Reduce operational risk in order placement, bot loop, and exit handling.
- Make reconciliation and restart behavior more deterministic.

### Stage 4 - Data & Diagnostics Hardening
- Make fallback behavior explicit and observable.
- Separate adapters, freshness policy, and degraded-mode handling.

### Stage 5 - Frontend Architecture
- Split the API layer and shrink page-level orchestration.
- Introduce proper domain hooks and clearer async state handling.

### Stage 6 - Page-by-Page Rebuild
- Rework the heaviest and most operator-sensitive pages first.
- Remove silent mock UX and split oversized pages into focused modules.

### Stage 7 - Release, Ops, and Documentation Discipline
- Add ADRs, release gates, rollback notes, and runbooks.
- Make the system maintainable by a team, not just the current owner.

## Recommended order of execution

1. Complete Stage 0 first.
2. Start Stage 1 and the Analytics truth fix in parallel only after Stage 0 baseline docs exist.
3. Finish Stage 2 before expanding AI/autopilot behavior further.
4. Finish Stage 3 before increasing live trading authority.
5. Run Stage 4 and Stage 5 in parallel where file ownership does not overlap.
6. Execute Stage 6 page-by-page, starting with Analytics, Autopilot, TradeBot, and Market Rotation.
7. Treat Stage 7 as a release gate, not an optional documentation pass.

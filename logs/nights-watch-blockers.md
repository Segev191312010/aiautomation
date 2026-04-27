# Nights Watch — Blockers

## Phase F (Swing Screener Backend) — blocked 2026-04-19 02:50 UTC

- What was being attempted: implement Phase F1-F3 per task.md (universe helpers, ATR matrix, Stockbee endpoints).
- Last working commit SHA: d9287e7
- Failure mode: pre-existing state from a prior session contains substantially more scope than Phase 1:

  - **Untracked files already present in working tree:**
    - `backend/swing_screeners.py` (762 lines — contains Phase 1 ATR Matrix + Stockbee AND Phase 2+ Qullamaggie, Minervini, O'Neil stub, 97 Club, Weinstein stages, trend grades, breadth metrics, leading industries orchestrator)
    - `backend/routers/swing_routes.py` (121 lines — routes for all of the above incl. Phase 2+ endpoints)
    - `backend/data/universes/djia.json`
    - `dashboard/src/components/swing/`, `dashboard/src/pages/SwingDashboardPage.tsx`, `dashboard/src/hooks/useSwingDashboard.ts`, `dashboard/src/services/api/swing.ts`, `dashboard/src/store/swingStore.ts`
  - **Tracked-file modifications already in working tree:**
    - `backend/models.py` — adds `ATRMatrixRow`, `BreadthMetrics`, `BreadthRow`, `Club97Entry`, `GuruScreenerResult`, `StageDistribution`, `StockbeeMover`, `SwingDashboardResponse`, `TrendGradeDistribution`, `TrendGradeEntry`
    - `backend/routers/__init__.py` — already imports and `include_router(swing_router)` (line 26, 33)
    - `dashboard/src/App.tsx`, `Header.tsx`, `Sidebar.tsx`, `store/index.ts`, `types/index.ts` — add SwingDashboard route, sidebar link, store wiring, type defs

- What the operator needs to decide:
  1. Should the pre-existing Phase 2+ swing implementation be committed as-is (completes the file-registration inconsistency — `routers/__init__.py` currently includes a non-existent-in-git swing_router, so a fresh clone fails to boot)?
  2. Or should it be reverted / restructured into a proper multi-commit split (Phase 1 alone, then Phase 2+ reviewed separately)?
  3. The 542 pytest tests currently pass because swing_screeners.py is in the working tree. If routers/__init__.py were reverted without removing the include, tests would still pass — but App.tsx and the frontend swing wiring would leave dead navigation.

- Can later phases proceed independently? Not applicable — F is the last phase. All of A-E are committed and signed off by codex review.

### Rationale for the block

Task.md Phase F explicitly states: "ONLY implementation Phase 1 (ATR Matrix + Stockbee + universe helpers). Do NOT start Phase 2+ without approval." The working tree already contains Phase 2+ work (Qullamaggie, Minervini, 97 Club, etc.) from a prior session. Per rules.md:

> "If you discover unexpected state like unfamiliar files, branches, or configuration, investigate before deleting or overwriting, as it may represent the user's in-progress work."

Committing this implicitly approves Phase 2+ scope. Deleting/reverting discards in-progress operator work. Neither is safe without explicit approval. Flagging for review.

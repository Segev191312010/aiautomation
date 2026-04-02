# Session Handoff — 2026-04-02

## Status
- **Branch**: master
- **Commits this session**: ~25 (Stage 5 + Phase 0-3 + Codex review fixes)
- **Uncommitted changes**: 6 files (WatchlistGrid, TradeBotPage test fix, uiStore, package.json, settings)
- **Tests**: 476 backend + 259 frontend = **735 total, all passing**

## What's Done

### Stage 5 — Frontend Architecture (COMPLETE)
- Phase 5.1: Split api.ts (846L) into 16 domain modules + barrel re-export
- Phase 5.2: 6 domain hooks + utils/formatters.ts + types/async.ts
- Phase 5.3: AsyncStateWrapper, EmptyState, SectionSkeleton, PageErrorBanner + per-page ErrorBoundary keyed by route
- Phase 5.4a: Split store/index.ts (1,777L) into 15 domain store files + barrel
- Phase 5.4b: Extracted sub-components from AnalyticsPage (1,246→505), TradeBotPage (809→266), MarketRotationPage (963→235)
- Phase 5.4c: ConditionBuilder (F5-03) + DecisionDrilldown/EvaluationReplay stubs (F5-05)

### Phase 0 — Worktree Triage
- Untracked artifacts (server.log, dist/index.html, .exe)
- Fixed main.py _DEFAULT_WATCHLIST bug
- Committed all WIP in coherent groups

### Phase 1 — CI Pipeline
- .github/workflows/ci.yml (frontend typecheck+build+vitest, backend pytest)

### Phase 2 — Backend God-File Splits
- 2a: main.py (1,162→952) — ws_manager.py, ws_quote_state.py, market_heartbeat.py
- 2b: bot_runner.py (1,411→1,218) — bot_exits.py
- 2c: database.py (1,571→3 facade) — db/ package with 8 modules

### Phase 3 — Frontend Quality
- 3a: Wired DecisionDrilldown + EvaluationReplay into AutopilotPage (567→341)
- 3b: Added 170 new frontend tests (formatters, AsyncStateWrapper, alertStore async paths)

### Codex Review Fixes
- HIGH: Fixed split-brain timestamp bug (status.py now reads from ws_quote_state directly)
- LOW: Cleaned duplicate _emit, unused imports, direct set mutation

## What's In Progress
- Nothing actively in progress — clean stopping point

## What's Pending

### Backend
- Split bot_runner.py further (1,218L still — entry evaluation + bar fetching could extract)
- Extract WebSocket data feed from main.py (~500 lines of quote handling)
- Split diagnostics_service.py (1,110L), stock_profile_service.py (1,107L)

### Frontend
- MarketPage (680L) — relaxed target 250, chart orchestration is dense
- RulesPage (659L) — extract rule table/form components
- More page tests (Dashboard, MarketPage, BacktestPage tests were attempted but had issues)
- ConditionBuilder test needs fixing (value input change propagation)

### Production Ops
- Docker (DOCKER.md exists but not wired)
- Prometheus metrics export
- Dry-run auto-tune mode
- ADRs/runbooks/release checklist

## Key Decisions Made
1. **Hook vs Store ownership**: Hooks are thin adapters over stores, no parallel state layer
2. **Auth token in client.ts only**: Not split between client and auth modules
3. **database.py kept as facade**: All 46 consumers unchanged via `from db import *`
4. **ErrorBoundary keyed by route**: Auto-resets on navigation
5. **main.py split order**: main.py first, bot_runner second, database.py last (largest blast radius)
6. **CI before heavy refactors**: Safety net for high-coupling changes
7. **Mutable timestamps via module attr**: Fixed Codex-found split-brain by using `ws_quote_state._ws_last_*` instead of local bindings

## Gotchas for Next Session
- `vite.config.ts` proxy is on port 8000 — if IBKR Gateway is on 4001, start backend with `IBKR_PORT=4001`
- `_DEFAULT_WATCHLIST` in main.py/market_heartbeat.py — heartbeat defaults to enabled now
- bot_exits.py uses inline imports (not top-level) to avoid triggering DB init during test collection
- The db/ package uses `cfg.DB_PATH` directly in get_db() so test conftest env var overrides work

## Test Counts
- Backend: 476 passed (40 test files)
- Frontend: 259 passed (17 test files)
- Total: **735 tests, all green**

## Resume Command
> Continue on master. Stage 5 frontend architecture and backend god-file splits are complete. 735 tests green. Next priorities: extract MarketPage sub-components, add more page tests, or start production ops (Docker/Prometheus). Check MEMORY.md for full context.

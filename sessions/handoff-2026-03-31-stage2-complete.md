# Session Handoff — 2026-03-31

## Status
- **Branch**: master (37 commits ahead of origin)
- **Commits this session**: 4
  - `9dba307` feat(ai): circuit breaker, model fallback, bull/bear debate, multi-persona analysis
  - `926b608` fix(stage2): helper extraction, replay filter parity, evidence gaps
  - `055d063` fix(backend): conftest, rule_backtest filter policy, contract tests
  - `2b5065a` fix(stage2): close all 10 bugs from parallel review agents
- **Tests**: 447 passing, 0 failures
- **Frontend**: typecheck clean, build clean
- **Uncommitted**: ~18 files modified (user's own edits to config, bot_runner, safety_kernel, session docs, order_executor, risk_manager — NOT from this session's work)

## What's Done

### AI Repo Research → 5 Features (commit 9dba307)
Researched TradingAgents, ai-hedge-fund, nofx, prediction-market-analysis, pmxt. Implemented:
1. **Circuit breaker** — `safety_kernel.py`: consecutive AI failure tracking, auto emergency stop after 3 failures
2. **Model fallback chain** — new `ai_model_router.py`: primary → fallback → haiku, integrated with circuit breaker
3. **Bull/Bear debate** — `ai_advisor.py` Layer 5b: adversarial analysis with conviction gating
4. **Multi-persona analysis** — `ai_advisor.py` Layer 5c: momentum/value/growth/risk lenses, concurrent
5. **Dashboard** — `CircuitBreakerPanel.tsx` on Autopilot, `AIAnalysisModule.tsx` on Stock Profile

### Stage 2 AI/Autopilot Correctness (commits 926b608, 055d063, 2b5065a)
All 6 phases CLOSED:

**Phase 2.1 — Helper extraction**: `decision_item_factory.py`, `optimizer_prompts.py`, `make_confidence_buckets` in `evaluation_math.py`
**Phase 2.2 — Private imports eliminated**: Zero production cross-module private imports
**Phase 2.3 — Replay correctness**:
- Generate mode now applies min_confidence/symbols/action_types filters (was silently ignoring)
- Symbol filter excludes None-symbol items (was passing them through)
- Window selection uses SQL in both modes (was Python-side in existing mode)
- `model_validator` rejects filter fields for rule_backtest
**Phase 2.4 — Ledger-backed learning**: Economic report threads `_source` key from evaluation paths → `metric_source` field
**Phase 2.5 — Evidence surface**: `error`, `runs_evaluated`, `filters_applied` promoted to top-level in evaluation responses; list query includes error column
**Phase 2.6 — Tests**: 6 new test files covering all scenarios

### 10 Bug Hunt Results Fixed (commit 2b5065a)
All found by 8 parallel review agents:
1. Live optimizer inline formatter → shared `format_rule_performance()` (KeyError fix)
2. Replay scoring false match on falsy keys → compare when either side non-None
3. `metric_source` always "insufficient" → driven by explicit `_source` key
4. `get_evaluation_runs()` missing error column → added
5. `runs_evaluated`/`filters_applied` orphaned in summary → promoted to top-level
6. Window filter asymmetry → both modes use `_select_replay_contexts`
7. Symbol filter passing None items → excluded
8. Break-even counted as loss → `pnl < 0` strict negative
9. Zero drawdown returning None → returns 0.0
10. "errors" plural vs "error" singular → joined to string

## What's Pending (NOT started this session)

### Remaining Tracks (from memory)
- **A. Structural Cleanup**: Split god files (database.py, bot_runner.py, main.py ~1,400 lines each)
- **B. DuckDB**: Analytical SQL layer over Parquet (low priority)
- **C. Frontend Quality**: Error boundaries, useMemo, React.lazy, more tests
- **D. Production Ops**: CI/CD, Prometheus, dry-run auto-tune, ADRs/runbooks

### Uncommitted user changes (review before next commit)
- `backend/config.py` — ENABLE_PORTFOLIO_CONCENTRATION_ENFORCEMENT defaults to true when AUTOPILOT_MODE=LIVE
- `backend/safety_kernel.py` — `trip_circuit_breaker` gained `close_positions` param + `_emergency_close_all_positions`
- `backend/order_executor.py` — ~130 lines changed
- `backend/risk_manager.py` — ~47 lines changed
- `backend/bot_runner.py` — ~11 lines changed
- `backend/execution_brain.py` — ~18 lines changed
- Various session review docs updated

## Key Decisions Made
1. **baseline_key**: Kept in ReplayRequest contract as metadata-only, documented as reserved. Not wired into scoring logic.
2. **evaluation_query.py / evaluation_presenters.py**: Deferred — existing query functions in ai_evaluator.py are clean enough.
3. **candidate_registry → optimizer prompts**: Fully extracted to `optimizer_prompts.py`. No more private imports.
4. **Symbol filter semantics**: When symbols filter is active, items with None/empty symbol are EXCLUDED (not passed through).
5. **metric_source**: Driven by explicit `_source` key in each evaluation path, not inferred from data_quality string.

## Files Created This Session
- `backend/ai_model_router.py` — resilient LLM call layer with fallback
- `backend/decision_item_factory.py` — item normalization from AI payloads
- `backend/optimizer_prompts.py` — shared prompt templates and formatters
- `dashboard/src/components/autopilot/CircuitBreakerPanel.tsx`
- `dashboard/src/components/stock-profile/AIAnalysisModule.tsx`
- `backend/tests/test_ai_replay.py` (12 tests)
- `backend/tests/test_ai_learning.py` (9 tests)
- `backend/tests/test_ai_optimizer.py` (2 tests)
- `backend/tests/test_replay_scoring.py` (6 tests)

## Files Modified This Session
- `backend/ai_advisor.py` — bull/bear debate + multi-persona + model router for daily report
- `backend/ai_optimizer.py` — model router + shared prompt formatters
- `backend/ai_replay.py` — filter parity, window fix, symbol fix
- `backend/ai_evaluator.py` — error in list query, promoted summary fields
- `backend/ai_learning.py` — _source key, metric_source fix
- `backend/autopilot_api.py` — filter passthrough, evidence metadata, new endpoints
- `backend/api_contracts.py` — EvaluationRunResponse, EconomicReportResponse, ReplayRequest
- `backend/config.py` — AI_MODEL_FALLBACK, AI_CONSECUTIVE_FAILURE_THRESHOLD, AI_FALLBACK_ENABLED
- `backend/safety_kernel.py` — circuit breaker tracking
- `backend/replay_scoring.py` — match key fix
- `backend/evaluation_math.py` — make_confidence_buckets, expectancy fix, drawdown fix
- `dashboard/src/services/api.ts` — new API types and functions
- `dashboard/src/pages/AutopilotPage.tsx` — CircuitBreakerPanel
- `dashboard/src/pages/StockProfilePage.tsx` — AIAnalysisModule

## Gotchas for Next Session
- User has ~18 uncommitted files from their own edits (config, safety_kernel with emergency close, order_executor, risk_manager). Review before any new work.
- `_make_confidence_buckets` wrapper in ai_replay.py is dead code (no external callers). Safe to delete.
- `_build_ledger_items` wrapper in ai_optimizer.py delegates to factory. One test still imports `_apply_decisions` from ai_optimizer — not part of Stage 2 extraction scope.
- Frontend `types/advisor.ts` EconomicReport interface is missing `data_quality` and `metric_source` fields the backend now returns (Pydantic strips them but TypeScript won't see them).

## Resume Command
> Stage 2 AI/Autopilot Correctness is CLOSED (447 tests, all 10 bug-hunt findings fixed). Uncommitted user changes in config.py, safety_kernel.py, order_executor.py, risk_manager.py, and bot_runner.py need review/commit. Next: pick from Track A (structural cleanup), Track C (frontend quality), or Track D (production ops). See memory/MEMORY.md for full context.

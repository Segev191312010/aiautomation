# Full Codebase Audit Findings — 2026-03-31

Audit performed by: Claude Opus 4.6 + Codex (OpenAI)
Scope: Execution pipeline, risk management, AI autopilot, scanner, dashboard UX, general code quality
Bot status at audit time: LIVE on IBKR account U21631648, ~$5,600 equity, 19 rules, 534 stocks

---

## How to read this document

Each finding is tagged with:
- **Severity**: CRITICAL / HIGH / MEDIUM / LOW
- **Stage**: Which review-stage-X file it belongs to
- **Status**: FIXED (code changed today) or OPEN (to be done in the listed stage)
- **Found in**: Exact file and line numbers
- **Relevance**: When this should be addressed relative to the roadmap

---

## STAGE 3 — Trading Runtime Hardening
> File: `sessions/review-stage-3-trading-runtime-hardening.md`
> Relevance: **IMMEDIATE — do before increasing live trading authority**

### FIXED in this session (2026-03-31)

#### F3-01: `skip_safety=True` bypassed safety gate for live orders [CRITICAL]
- Found in: `direct_ai_trader.py:188`, `bot_runner.py:1056`
- Fix: Changed to `skip_safety=False` in both locations. Defense in depth restored.
- Tests: 414/414 pass

#### F3-02: MKT-to-LIMIT conversion race condition (double order risk) [CRITICAL]
- Found in: `order_executor.py:385-394`
- Fix: Now waits up to 5s for cancel confirmation, checks if original filled during cancel, skips resubmit if so.
- Tests: 414/414 pass

#### F3-03: Fire-and-forget `asyncio.create_task()` — silent fill watcher crashes [CRITICAL]
- Found in: `order_executor.py:237,268,346,407`
- Fix: Added `_safe_create_task()` with `add_done_callback()` error logging. All 6 task creations in order_executor now use it.
- Tests: 414/414 pass

#### F3-04: No partial fill handling [CRITICAL]
- Found in: `order_executor.py:248-287`
- Fix: Detects partial fills, adjusts quantity on cancel-with-partial, logs warnings. Also handles broker state query on timeout.
- Tests: 414/414 pass

#### F3-05: Fill watcher timeout too short (60s) [HIGH]
- Found in: `order_executor.py:248`
- Fix: Extended to 120s. On timeout, now queries broker directly to check actual fill state.
- Tests: 414/414 pass

#### F3-06: Dedup window too short (5s hardcoded) [HIGH]
- Found in: `order_executor.py:66`
- Fix: Changed to `max(10, BOT_INTERVAL_SECONDS * 2)`.
- Tests: 414/414 pass

#### F3-07: Order status event callback uses stale trade data [HIGH]
- Found in: `order_executor.py:337-370`
- Fix: Event callback now re-fetches trade from DB and checks if already FILLED before processing.
- Tests: 414/414 pass

#### F3-08: `check_portfolio_impact` was fail-OPEN on error [HIGH]
- Found in: `risk_manager.py:383-388`
- Fix: Changed to `allowed=False, reason="error_blocked"`. Fail-closed.
- Tests: 414/414 pass

#### F3-09: Daily loss circuit breaker only counted realized P&L [HIGH]
- Found in: `risk_manager.py:56`
- Fix: Now includes `UnrealizedPnL` from IBKR account values.
- Tests: 414/414 pass

#### F3-10: Position size fallback to $100 when price unavailable [HIGH]
- Found in: `risk_manager.py:139`
- Fix: Now returns BLOCK with reason "Cannot estimate price" instead of guessing.
- Tests: 414/414 pass

#### F3-11: Circuit breaker didn't close existing positions [HIGH]
- Found in: `safety_kernel.py:73-97`
- Fix: Added `_emergency_close_all_positions()` with MKT order placement for all open positions.
- Tests: 414/414 pass

#### F3-12: No order reconciliation after IBKR reconnect [HIGH]
- Found in: `ibkr_client.py:275-289`
- Fix: `_reconnect_loop` now calls `reconcile_pending_orders()` after successful reconnect.
- Tests: 414/414 pass

#### F3-13: Same-cycle exit+re-entry churn [HIGH]
- Found in: `bot_runner.py:622-970`
- Fix: Added `_exited_this_cycle` set, populated on exit fills, checked before entries.
- Tests: 414/414 pass

#### F3-14: DB `PRAGMA synchronous=NORMAL` could lose trades on crash [HIGH]
- Found in: `database.py:29`
- Fix: Changed to `synchronous=FULL`, `busy_timeout=10000`.
- Tests: 414/414 pass

### OPEN — to be done in Stage 3

#### F3-15: Stale position read before exit order placement [CRITICAL]
- Found in: `bot_runner.py:1224-1239`
- Issue: Position is read once then used later for exit. If concurrent operation deletes it, exit fires on ghost position.
- When to fix: Phase 3.1 (Order Lifecycle Service Extraction) — add position re-fetch before exit placement.
- Blocked by: Nothing. Can fix independently.

#### F3-16: Database lacks explicit transaction boundaries [HIGH]
- Found in: `database.py:24-32`
- Issue: Each `execute()` is auto-committed. No read-then-write atomicity for position state transitions.
- When to fix: Phase 5C (Monolith splitting) — when splitting `database.py` into connection/schema/repositories.
- Note: `synchronous=FULL` helps durability but doesn't fix isolation.

#### F3-17: Exit retry reaches cap but position stays open forever [HIGH]
- Found in: `bot_runner.py:1356-1385`
- Issue: After `MAX_EXIT_ATTEMPTS`, position is flagged for manual intervention but never force-closed.
- When to fix: Phase 3.1 — add MKT order as last-resort close when retry cap reached.

#### F3-18: Sector map covers only ~50 of 534+ scanned symbols [MEDIUM → partially fixed]
- Found in: `risk_manager.py:15-32`
- Partial fix: Added dynamic yfinance sector lookup with caching.
- Remaining: yfinance lookup is synchronous and slow for first call per symbol. Consider batch pre-loading sectors on startup.
- When to finish: Phase 3.2 or Stage 4 (Data Hardening).

#### F3-19: `ENABLE_PORTFOLIO_CONCENTRATION_ENFORCEMENT` was off by default [MEDIUM → FIXED]
- Found in: `config.py:109`
- Fix: Now defaults to `true` when `AUTOPILOT_MODE=LIVE`.

---

## STAGE 2 — AI / Autopilot Correctness
> File: `sessions/review-stage-2-ai-autopilot-correctness.md`
> Relevance: **Before expanding AI authority further**

### FIXED in this session (2026-03-31)

#### F2-01: `execution_brain._pending_direct_candidates` was a global mutable list [HIGH]
- Found in: `execution_brain.py:9`
- Fix: Replaced with `asyncio.Queue` for thread safety.
- Tests: 414/414 pass

#### F2-02: AI guardrails had no per-rule oscillation prevention [HIGH]
- Found in: `ai_guardrails.py:495-501`
- Fix: Added `_check_rule_oscillation()` — same rule can't be toggled >1x per day.
- Tests: 414/414 pass

#### F2-03: Rule backtest gate disabled by default [MEDIUM → FIXED]
- Found in: `config.py:110`
- Fix: Now defaults to `true` when `AUTOPILOT_MODE=LIVE`.

### OPEN — to be done in Stage 2

#### F2-04: Auto-tune only partially implemented [HIGH] (Codex finding)
- Found in: `ai_advisor.py:318,360`
- Issue: `compute_auto_tune()` produces `sizing_changes` and `new_min_score`, but `apply_auto_tune()` only disables rules. Sizing and score changes are computed but never applied.
- When to fix: Phase 2.3 (Optimizer Correctness) — either implement full auto-tune application or rename to preview-only.
- Source: Codex audit

#### F2-05: Replay/evaluation loop not reproducible [HIGH] (Codex finding)
- Found in: `ai_optimizer.py:689`, `candidate_registry.py:70`
- Issue: Optimizer persists thin context snapshot, but replay expects full fields. Public contract allows `rule_snapshot` and `decision_run` but resolver only supports `prompt_version` and `model_version`.
- When to fix: Phase 2.1 (Helper Extraction) — persist full optimizer context, align contract and implementation.
- Source: Codex audit

#### F2-06: AI input validation too permissive [MEDIUM] (Codex finding)
- Found in: `ai_rule_lab.py:75`, `api_contracts.py:315`, `safety_kernel.py:218`
- Issue: Rule lab silently rewrites missing scope to SPY. Stop price direction not validated relative to side. `abs(entry-stop)` treats both directions the same.
- When to fix: Phase 2.2 — add Pydantic validators for stop direction, reject malformed payloads.
- Source: Codex audit

#### F2-07: Direct AI candidates don't survive process restart [MEDIUM] (Codex finding) — **FIXED 2026-04-09**
- Found in: `execution_brain.py` (was asyncio.Queue, in-memory)
- Issue: Queued candidates were volatile. Process restart lost them.
- **Resolution:** New `backend/db/direct_candidates.py` CRUD module + `direct_candidates` table with TTL + queued/draining/applied/failed/expired status. `execution_brain.queue_direct_candidates` and `drain_direct_candidates` are now async DB calls. Startup purge in `main.py` lifespan. Restart-survival regression test in `tests/test_execution_brain.py`.
- Commits: `5866800` + `8ebab67` (companion module).

#### F2-08: Bull/Bear debate fails silently to NEUTRAL [MEDIUM] — **FIXED 2026-04-09 (Phase 1)**
- Found in: `ai_advisor.py:543-548`
- Issue: If LLM JSON parsing failed, conviction defaulted to 0.5 for both sides, netting NEUTRAL. API failures silently prevented all AI trades without any alert.
- **Resolution:** Parse failure path now emits `log.warning("bull_bear_parse_failed: %s", exc)`. Module counter `_debate_failure_count` resets at UTC midnight. When count crosses `cfg.AI_DEBATE_FAILURE_THRESHOLD` (default 5), publishes `MetricEvent(metric_type="ai_debate_parse_failures")` via the bot event bus. Surfaced as `ai_debate_parse_failures_24h` in `bot_health.get_bot_health()`. New env knob in `backend/config.py`. 3 regression tests in `tests/test_ai_optimizer.py`.

---

## STAGE 5 — Frontend Architecture
> File: `sessions/review-stage-5-frontend-architecture.md`
> Relevance: **Parallel with Stage 4, after Stage 0 baseline**

### OPEN — to be done in Stage 5 / Stage 6

#### F5-01: 43 uses of `any` type across 22 dashboard files [MEDIUM]
- Found in: PositionsTable.tsx (6), useCrosshairSync.ts (3), autopilot tests (3), CompanyOverview (3), etc.
- When to fix: Stage 5 or ongoing — replace with proper interfaces, especially in financial data components.

#### F5-02: No per-page error boundaries [MEDIUM]
- Found in: `App.tsx` has one top-level ErrorBoundary only.
- Issue: One component crash takes down the entire app.
- When to fix: Phase 6A of tier-1 stabilization checklist — already planned.

#### F5-03: Standard rules still use raw condition JSON [MEDIUM] (Codex finding)
- Found in: `RulesPage.tsx:507`
- Issue: No visual rule builder for standard rules. Users must edit raw JSON conditions.
- When to fix: Stage 6 (Page Rebuild) — the Rule Builder UI is Stage 6 work.
- Source: Codex audit

#### F5-04: Risk events are hardcoded empty stub [MEDIUM] (Codex finding) — **FIXED 2026-04-09 (Phase 1, removed)**
- Found in: `store/index.ts:1618` → migrated to `dashboard/src/store/riskStore.ts:59-61` after store split
- Issue: Risk events store slice returned empty array. Never populated.
- **Resolution:** Stub REMOVED. `grep -rn "riskEvents"` found zero consumers in any page or component (only the store and the unused `useAnalyticsData` hook). `riskEvents`, `riskEventsStatus`, `fetchRiskEvents`, and the `RiskEvent` type import deleted from `riskStore.ts`. `useAnalyticsData.ts` cleaned up. Per feedback_planning_safety, removal was correct call (faster, safer). `RiskEvent`/`RiskEventType` types in `types/index.ts` left in place (ambient type surface, out of scope).

#### F5-05: Autopilot page missing decision drilldown and replay flows [MEDIUM] (Codex finding)
- Found in: `AutopilotPage.tsx:451,488`
- Issue: API supports decision-item fetch, replay launch, and compare, but UI only has refresh buttons.
- When to fix: Stage 6 (Autopilot page rebuild).
- Source: Codex audit

#### F5-06: Chart accessibility [LOW]
- Found in: `TradingChart.tsx`, `DrawingCanvas.tsx`
- Issue: Canvas-based charts are inherently inaccessible. No alt text or ARIA descriptions.
- When to fix: Stage 6 or later — add sr-only data table fallback.

---

## STAGE 7 — Release, Ops, and Documentation
> File: `sessions/review-stage-7-release-ops-docs.md`
> Relevance: **CRITICAL for LIVE trading — treat as release gate**

### OPEN — to be done in Stage 7

#### F7-01: Auth is effectively unauthenticated [CRITICAL] (Codex finding)
- Found in: `auth.py:111`, `routers/auth.py:16`, `autopilot_api.py:126`, `routers/orders.py:37`
- Issue: `get_current_user()` falls back to demo user when no token present. `/api/auth/token` issues demo tokens with no credentials. Sensitive routes like `/api/autopilot/mode` and `/api/orders/manual` have no auth guard.
- Risk: Anyone on the network can control the bot, change autopilot mode, place orders.
- When to fix: **ASAP if exposed to network.** Currently mitigated by running on localhost only. But this must be fixed before any remote access or multi-user deployment.
- Source: Codex audit

#### F7-02: Hardening middleware exists but is not mounted [HIGH] (Codex finding)
- Found in: `middleware.py:25`, `main.py:296`
- Issue: Rate limiting and security headers middleware is defined but never registered in the app.
- When to fix: Stage 7 Phase 7.3 (Release Gates) — register middleware, verify 429/header behavior.
- Source: Codex audit

#### F7-03: No API rate limiting on endpoints [MEDIUM]
- Found in: All router files under `routers/`
- Issue: A buggy frontend or attacker could spam order/rule endpoints.
- When to fix: Stage 7 — mount the existing middleware.

#### F7-04: Bootstrap/placeholder files in shipping tree [LOW] (Codex finding)
- Found in: `_write_files.py:1`, `_bootstrap.py:3`
- Issue: Non-runtime artifacts still in the backend directory.
- When to fix: Stage 7 cleanup.
- Source: Codex audit

---

## STAGE 0 — Baseline and Truth
> File: `sessions/review-stage-0-baseline-and-truth.md`

### FIXED in this session (2026-03-31)

#### F0-01: 4 stale TODO(STAGE3) comments with silent `pass`
- Found in: `bot_runner.py:295,306,386,396`
- Fix: Replaced with `log.debug()` + `_record_degraded_event()`.

#### F0-02: 44+ silent `except Exception: pass` blocks in critical paths
- Found in: Throughout backend — `ai_guardrails.py`, `context_builder.py`, `direct_ai_trader.py`, etc.
- Partial fix: Fixed the ones in critical order/trade paths. Remaining ones are in non-critical paths (diagnostics, analytics) — lower priority.
- When to finish rest: Stage 0 Phase 0.3 (Exception Hotspot Catalog) — already planned.

---

## Summary of session impact

| Category | Fixed | Open | Total |
|----------|-------|------|-------|
| Stage 3 (Runtime) | 14 | 4 | 18 |
| Stage 2 (AI) | 3 | 5 | 8 |
| Stage 5 (Frontend) | 0 | 6 | 6 |
| Stage 7 (Release) | 0 | 4 | 4 |
| Stage 0 (Baseline) | 2 | 0 | 2 |
| **Total** | **19** | **19** | **38** |

Tests after all fixes: **414 passed, 0 failures**

Files modified:
- `order_executor.py` (execution safety, partial fills, error callbacks)
- `direct_ai_trader.py` (skip_safety removal)
- `bot_runner.py` (skip_safety, churn prevention, degraded state logging)
- `risk_manager.py` (fail-closed, unrealized P&L, price block, dynamic sectors)
- `safety_kernel.py` (emergency close all positions)
- `config.py` (concentration + backtest gate defaults for LIVE)
- `database.py` (synchronous=FULL, busy_timeout)
- `ibkr_client.py` (reconnect reconciliation)
- `execution_brain.py` (asyncio.Queue)
- `ai_guardrails.py` (oscillation prevention, async _check_action_limits)
- `context_builder.py` (silent except logging)
- `tests/test_execution_brain.py` (adapted to Queue API)

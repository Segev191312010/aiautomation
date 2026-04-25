# Nights Watch Task — 24h Session (2026-04-18 → 2026-04-19)

Operator: Segev. Mode: autonomous, governed by `rules.md` (hard bans apply).

Source plans — read each before executing the corresponding phase:
- `sessions/dashboard-hardening-plan.md` (master plan, Phases 2–5)
- `sessions/phase1-dedup-fmtUSD.md`
- `sessions/phase2-memoize-watchlist.md`
- `sessions/phase3-dead-code-cleanup.md`
- `sessions/phase4-encode-symbol-params.md`
- `sessions/phase5-adaptive-polling.md`
- `sessions/swing-screener-backend-plan.md` (stretch goal only)

## Goal
Close out every open phase in `sessions/` in priority order. After each phase: quality gates, codex review, commit. Safety rules in `rules.md` are non-negotiable. Never push to origin. Never touch live trading code.

## Execution Protocol (applies to EVERY phase)

1. **Plan scan** — Read the referenced session file fully. Summarize intent in a one-line comment at the top of your working notes.
2. **Replay learnings** — Invoke `/replay` (or `pro-workflow:replay-learnings`) with the task category before writing code.
3. **Delegate to domain agent** — Spawn the agent named in the phase. Brief it with: goal, exact files, acceptance criteria, quality gates. Do NOT duplicate its work.
4. **Implement** — Smallest diff that satisfies acceptance criteria. No "while I'm here" scope.
5. **Quality gates (all four)** — After code changes, before commit:
   ```
   cd dashboard && npm run typecheck
   cd dashboard && npm run build
   cd dashboard && npx vitest run
   cd backend && python -m pytest tests/ -v
   ```
   Fix root cause of any failure. Never bypass.
6. **Codex review** — After the phase's code is stable, invoke `mcp__codex-review__codex` with the diff since phase start. Save output to `logs/codex-review-phase-<N>-<slug>.md`. If codex flags blockers, fix them.
7. **Commit via `/commit`** — One logical commit per phase (sub-phases may split, e.g., 5a/5b). Conventional commit format. Do not `--no-verify`.
8. **Learning capture** — Append any non-obvious gotcha as `[LEARN] <Category>: <rule>` and to `LEARNED.md`.
9. **Progress log** — Append a line to `logs/nights-watch-progress.md` with: phase, commit SHA, agent used, gates status, codex verdict, elapsed time.

## Phase Order (strict priority — do NOT reorder)

### Phase A — Quick Wins (target: 3–4h, 5 commits)

These are low-risk, all-frontend, all independent. Do them first so early quality gates catch any environment issues.

#### A1 — Deduplicate fmtUSD (plan: `phase1-dedup-fmtUSD.md`)
- Agent: `react-typescript`
- Files (SAFE to replace — identical behavior):
  - `dashboard/src/pages/SimulationPage.tsx` (line 15)
  - `dashboard/src/components/tradebot/PositionsTable.tsx` (line 54)
  - `dashboard/src/components/analytics/PnLSummary.tsx` (line 12)
  - `dashboard/src/pages/Dashboard.tsx` (line 27)
- Files to LEAVE ALONE (different behavior, documented in plan):
  - `SectorExposure.tsx`, `PnLChart.tsx`, `AIPerformanceCard.tsx`
- Action: delete local `function fmtUSD(...)`, add `import { fmtUSD } from '@/utils/formatters'`. Check for co-located `fmtPct` — `formatters.ts` exports one.
- Verify: `grep -rn "function fmtUSD" dashboard/src/` returns exactly 3 lines (the intentionally different ones).
- Commit: `refactor(dashboard): deduplicate fmtUSD in 4 files`

#### A2 — Memoize WatchlistGrid sort (plan: `phase2-memoize-watchlist.md`)
- Agent: `react-typescript`
- File: `dashboard/src/components/ticker/WatchlistGrid.tsx` lines 104–117
- Action: wrap the sort chain in `useMemo` keyed on `[symbols, quotes, sortField, sortDir]`.
- Manual check after gates: click column headers, sort direction correct.
- Commit: `perf(dashboard): memoize WatchlistGrid sort chain`

#### A3 — Remove dead `addTrade` import (plan: `phase3-dead-code-cleanup.md`)
- Agent: `refactor`
- File: `dashboard/src/hooks/useWebSocket.ts`
- Action: delete the `const addTrade = useAccountStore((s) => s.addTrade)` line. Verify `grep -n "addTrade" dashboard/src/hooks/useWebSocket.ts` returns nothing.
- Commit: `chore(dashboard): remove dead addTrade import in useWebSocket`

#### A4 — encodeURIComponent symbol path params (plan: `phase4-encode-symbol-params.md`)
- Agent: `security-auditor`
- Files & counts:
  - `dashboard/src/services/api/stockProfile.ts` — 14 endpoints
  - `dashboard/src/services/api/market.ts` — 4 endpoints
  - `dashboard/src/services/api/indicators.ts` — 1 endpoint
- Action: wrap every `${symbol}` in path segments with `encodeURIComponent(symbol)`. Do NOT touch query params already encoded.
- Post-check: load the dashboard dev server (if feasible), verify `BTC-USD` and `BRK.B` still resolve (no double-encoding).
- Commit: `security(dashboard): encode symbol path params in API clients`

#### A5 — Adaptive polling (plan: `phase5-adaptive-polling.md`)
- Agent: `performance-engineer`
- Sub-phase A5a — `dashboard/src/services/ws.ts`: add `connected` getter to `MarketDataWsService`.
- Sub-phase A5b — `dashboard/src/hooks/useMarketData.ts`: replace fixed `QUOTE_INTERVAL=5000` with fast (5s) / slow (30s) self-rescheduling based on `wsMdService.connected`.
- Preserve exact cleanup semantics (clearInterval in return). Do not leak timers.
- Unit test: add or update a test that verifies interval switches when `connected` flips.
- Commits: `feat(dashboard): add connected getter to MarketDataWsService` then `perf(dashboard): adaptive polling based on WS connection state`

**Phase A gate:** `mcp__codex-review__codex` on diff from phase start. Save to `logs/codex-review-phase-A-quick-wins.md`.

---

### Phase B — Dashboard Hardening: Functional (target: 6h)
Source: `sessions/dashboard-hardening-plan.md` Phase 2.

#### B1 — Symbol validation utility + call sites
- Agent: `ux-reviewer` for error UI, `react-typescript` for implementation
- New file: `dashboard/src/utils/validateSymbol.ts` exporting `validateSymbol(s: string): { ok: boolean; reason?: string }`. Regex: `/^[A-Z0-9\-\.]{1,20}$/`. Reject empty, length > 20, invalid chars.
- Apply in: `QuickOrderForm`, `TradeBotPage`, any other place that accepts freeform symbol input (grep for input→symbol flows).
- Error state: inline error label below input, disabled submit. Do not show modal.
- Tests: unit tests for `validateSymbol` covering accepts (`AAPL`, `BRK.B`, `BTC-USD`, `SPY`), rejects (empty, lowercase, `AAPL*`, 21-char string).
- Commit: `feat(dashboard): symbol validation on order entry`

#### B2 — Order confirmation modal
- Agent: `react-typescript`, then `ux-reviewer` for UX feedback
- New component: `dashboard/src/components/common/ConfirmModal.tsx`
  - Props: `open`, `title`, `summary` (symbol/qty/type/side), `requirePhrase` (default `"CONFIRM"`), `onConfirm`, `onCancel`.
  - Require user to type the phrase exactly to enable the Confirm button.
  - Keyboard: Esc cancels, Enter submits only when phrase matches.
- Integrate into `QuickOrderForm` submit flow — on submit, open modal instead of sending directly.
- Tests: render modal, type wrong phrase → button disabled; type `CONFIRM` → enabled; Esc → `onCancel`.
- Commit: `feat(dashboard): order confirmation modal with typed phrase gate`

**Phase B gate:** codex review → `logs/codex-review-phase-B-functional.md`. If codex flags UX smells (focus trap missing, no aria-live, etc.), fix before moving on.

---

### Phase C — Dashboard Hardening: Performance (target: 5h)
Source: `sessions/dashboard-hardening-plan.md` Phase 3 Task 7 (Task 6 already covered in A5).

#### C1 — Indicator algorithm optimization
- Agent: `refactor` (to audit all indicators) then `performance-engineer` (benchmark before/after)
- Target: `dashboard/src/utils/indicators.ts` — convert O(n·k) → O(n) sliding windows.
- Indicators to review: SMA, EMA, RSI, MACD, Bollinger Bands, VWAP, ATR.
- For each: if current implementation is already O(n) via accumulator, skip. If O(n·k) (nested slice+reduce), rewrite using rolling sum/EMA recurrence. Preserve numeric output exactly — add a test that compares old vs new on a 500-bar fixture to prove identical values (within 1e-9).
- Before/after: include a micro-benchmark (Vitest `bench` or a one-off timing script) comparing 500-bar runtime, log to commit message.
- Commit: `perf(dashboard): sliding-window indicator algorithms`

**Phase C gate:** codex review → `logs/codex-review-phase-C-perf.md`.

---

### Phase D — Dashboard Hardening: Backend Defense (target: 3h)
Source: `sessions/dashboard-hardening-plan.md` Phase 4.

#### D1 — CORS verification
- Agent: `backend-architect`
- Task: verify `CORSMiddleware` in `backend/app.py` (or equivalent). Allowed origins must be env-driven (`FRONTEND_ORIGIN`) plus `http://localhost:5173` for dev. `allow_credentials=True`. `allow_methods=["GET","POST","PUT","DELETE","OPTIONS","PATCH"]` (not `["*"]` with credentials — browsers reject that combo).
- If already correct: no code change, write a one-paragraph note to `logs/cors-audit-2026-04-18.md` and skip commit.
- If change needed: smallest diff, explicit origin list, test with curl simulating an `Origin:` header.
- Commit (only if changed): `fix(backend): tighten CORS allowed methods`

#### D2 — WebSocket origin validation
- Agent: `security-auditor`
- File: wherever WS endpoints are registered (`backend/routers/ws*.py` or similar). Add an origin-check dependency or inline check that rejects connections whose `Origin` header is not in `ALLOWED_ORIGINS`.
- Edge cases: empty Origin (same-origin tools like Postman) — allow only if explicit env flag is set; missing Origin in prod — reject.
- Tests: unit test using `TestClient` WebSocket that asserts 403 on bad origin.
- Commit: `security(backend): validate Origin on WebSocket connect`

**Phase D gate:** codex review → `logs/codex-review-phase-D-backend-defense.md`.

---

### Phase E — Testing & Validation (target: 5h)
Source: `sessions/dashboard-hardening-plan.md` Phase 5.

#### E1 — Indicator unit tests
- Agent: `test-automator`
- File: `dashboard/src/utils/__tests__/indicators.spec.ts` (create if missing).
- Cover: SMA, EMA, RSI, MACD, Bollinger Bands, VWAP — golden-value fixtures (use known textbook outputs or computed once and pinned).
- Target coverage: 100% for `utils/indicators.ts`. Check with `npx vitest run --coverage`.
- Commit: `test(dashboard): comprehensive indicator unit tests`

#### E2 — Auth token revocation flow test
- Agent: `test-automator`
- File: `dashboard/src/services/api/__tests__/client.spec.ts` (create or extend).
- Scenarios: 401 response → token cleared from localStorage AND `api:unauthorized` event dispatched. 200 response → no side effects.
- Commit: `test(dashboard): auth token revocation on 401`

#### E3 — WebSocket reconnection test
- Agent: `test-automator`
- File: `dashboard/src/services/__tests__/ws.spec.ts` (create or extend).
- Scenarios: close → reconnect attempt fires; multiple closes → backoff increases; clean unmount → no reconnect.
- Commit: `test(dashboard): WebSocket reconnection backoff`

#### E4 — Backend integration smoke
- Agent: `api-tester`
- File: `backend/tests/test_integration_smoke.py` (create or extend).
- Scenarios: full order place → cancel round trip with mocked broker; watchlist persistence (write → restart app → read same list).
- Commit: `test(backend): integration smoke for order lifecycle + watchlist persistence`

**Phase E gate:** codex review → `logs/codex-review-phase-E-tests.md`.

---

### Phase F — STRETCH: Swing Screener Backend Phase 1 (target: remaining time, STOP cleanly if short)
Source: `sessions/swing-screener-backend-plan.md` — ONLY implementation Phase 1 (ATR Matrix + Stockbee + universe helpers). Do NOT start Phase 2+ without approval.

Only enter this phase if Phases A–E are complete AND ≥4 hours remain on the 24h window.

#### F1 — Universe helpers
- Agent: `python-backend`
- New/extended file: `backend/universes.py` (or equivalent). Helpers for DJIA (use existing `backend/data/universes/djia.json`), Composite, $1B+.
- Commit: `feat(backend): universe helpers for DJIA/composite/1B+`

#### F2 — ATR Matrix endpoint
- Agent: `python-backend` with `market-data` for yfinance wiring
- New file: `backend/swing_screeners.py` (function `atr_matrix()`).
- New route: `GET /api/swing/atr-matrix` in `backend/routers/swing_routes.py`.
- 13 fixed symbols (11 Sector SPDRs + RSP + QQQE, from `sector_rotation.SECTORS`). ATR(14), EMA(21), close, `price_vs_21ema_atr = (close - EMA21) / ATR14`. Sort by extension desc.
- Test: pytest covering shape, numeric stability, sort order.
- Commit: `feat(backend): swing screener ATR matrix endpoint`

#### F3 — Stockbee scans endpoint
- Agent: `python-backend`
- Scans: 9M Movers, 20% Weekly, 4% Daily. Reuse existing scan helpers if present (check `backend/screener.py`).
- Route: `GET /api/swing/stockbee/{scan}`.
- Test: pytest with a small fixture universe.
- Commit: `feat(backend): swing screener Stockbee scans endpoint`

**Phase F gate:** codex review → `logs/codex-review-phase-F-swing-p1.md`. Leave Phase 2+ for the next session — do NOT start.

---

## Hard Stops (abort the whole run)

- Quality gates fail and you can't root-cause in ≤3 attempts → stop, write blocker, move on to next phase if independent; otherwise exit cleanly.
- Codex review returns a critical finding you can't fix without expanding scope → stop, write blocker.
- Any rule in `rules.md` Autonomous Execution Constraints would need to be violated → stop, write blocker.
- Disk full, network failure, repeated tool crash → stop, write blocker.

## Blocker Protocol

When aborting a phase, append to `logs/nights-watch-blockers.md`:
```
## <Phase ID> — blocked <UTC timestamp>
- What was being attempted:
- Last working commit SHA:
- Failure mode (copy error output):
- What the operator needs to decide:
- Can later phases proceed independently? yes/no
```
Then skip to the next independent phase.

## End of Run

At window end (or when all phases complete):
1. Append final progress summary to `logs/nights-watch-progress.md` (commits made, phases completed, phases skipped, blockers, test counts).
2. Run `/wrap-up` to generate the session summary.
3. Do NOT push. Do NOT open PRs. The operator will review commits in the morning.

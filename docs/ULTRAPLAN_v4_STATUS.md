# ULTRAPLAN v4 — Live Status Ledger

> **HISTORICAL STATUS — LIVE INSTRUCTIONS BLOCKED.** This ledger predates the
> 2026-07-27 Stage 9A audit. Do not execute its canary/flip steps or treat its
> `orderRef` claims as idempotency evidence. See `ROADMAP_TEAM_PLAN.md` and
> `docs/risk/stage-9a-residual-risk-register.md`.

> Durable source of truth for this build. Updated as work proceeds. Read this first when resuming.

- **Branch:** `feature/ultraplan-v4` (off `feature/deep-review-p0-batch` @ `e1f42bd`)
- **Plan:** `docs/ULTRAPLAN_v4.md`  ·  **Memory:** `~/.claude/.../memory/ultraplan-v4-trading-build.md`

## Committed (feature/ultraplan-v4)
- **`9f252bc` W1 foundation**: py3.11 pin (Dockerfile+CI), TV statuses + `tv_idempotency` table, `retention.py` GC bug fix, TV/Claude config + `.env` keys, deps pinned, backup/restore scripts, +7 tests. 672 → 679.
- **`10ad9cd` W2-6 backend paper pipeline**: webhook ingest + /api/signals, order_proposal, mcp_server, claude_worker (default-OFF), metrics, rate_limits, health_extended, account day-pnl; routers + lifespan wired; interface bug fixed (tools_spec list / 1-arg dispatch / result-aware classify); +docs. 679 → 778.
- **`045b8cc` W7 frontend**: Signals tab + components + API client/types; O(n) indicators (parity-tested) + VWAP session reset + a11y table; validateSymbol/ConfirmModal; Day-P&L badge. tsc clean, vitest 355 → 364, build OK.
- **`ea0404c` orderRef idempotency (M1 closed)**: `ib_order.orderRef = trade_rec.id` before placeOrder; idempotency dedupe test promoted xfail → pass. 778 → 779.
- **`0bde712` hard paper fence (review-driven CRITICAL fix)**: `place_proposed_order` fails closed for `source in {claude_worker, tv_webhook}` when `IS_PAPER=false` (→ `rejected`/`paper_fence`, broker never reached) unless `CLAUDE_LIVE_TRADING_ENABLED=true`. Scanner source unaffected. +flag +5 tests. 779 → 784.

STATE: backend **784 green / 0 xfail**, frontend **364 green**, app imports (201 routes). No live-behaviour change yet (worker default-off; rate-cap + direct_ai untouched).

## Independent review (Codex gpt-5.3-codex + Claude cross-check)
Ran a multi-engine review of the v4 diff. CONVERGENT verdict: core verified clean (4-gate chain order/short-circuits/skip_safety=False; rejected|deferred never 'applied'; orderRef; async non-blocking worker + bounded tool loop; parameterized SQL; HMAC secret; no scanner-path behaviour change). ONE real CRITICAL (all engines): TV/Claude path was paper-only by convention, not code → **fixed in `0bde712` (paper fence)**. Overstated/dismissed: "secret hash-collision" (sha256+compare_digest is fine) and "IP fail-open" (it fails closed). Minor/known: in_review stranding (bounded by TTL purge), Claude-chooses-qty mapping, deferred-vs-rejected cause semantics.

## Remaining live-path work (deferred — decisions/recommendations)
- **Metrics call-site wiring** (additive observability in bot_runner/order_executor/webhook): low-risk; do as a follow-up commit. Not blocking.
- **Cross-process rate-cap swap** (replace order_executor in-process asyncio.Lock with `db/rate_limits.try_acquire_order_slot`; fold `order_rate_window` DDL into init_db): BEHAVIOUR CHANGE to the live rate limiter. REQUIRED before `WORKERS>1`. Recommend wiring + a 2-worker smoke during the soak; **hold WORKERS=1 until then**.
- **direct_ai_trader reroute**: RECOMMEND **NOT** rerouting through `place_proposed_order` — its signature `(rule, source, user_id)` drops `stop_price/is_exit/has_existing_position`, which would REGRESS exit/stop handling. Recon confirmed the safety kernel already runs on this path (explicit + inside place_order), so there is no true live bypass to close. Leave as-is (optional: extend the helper to forward those params + tests, then reroute).
- **/api/signals user filter**: prod/multi-user only; the demo writes `user_id='demo'`, so filtering by the authed user could return empty in the single-user demo. Defer until multi-user.

## Flip decision (preconditions NOT all met)
Per protocol, do NOT flip the scanner LIVE yet: the cross-process rate cap isn't wired (so WORKERS>1 is unsafe) and the modified order path hasn't had a paper soak. Recommended path: run the paper soak (scanner on PAPER with the new helper + orderRef; TV/Claude path PAPER) per `paper_review_protocol.md`, wire the rate-cap swap, then flip per `LIVE_FLIP_RUNBOOK.md`.

## In flight — parallel agent fleets (do NOT relaunch agents on these files)
**Fleet 1** (`wvwmmx31k`, 8): `routers/webhook_routes.py`(+test,+pine) · `order_proposal.py`+`mcp_server.py`+`claude_prompts.py`(+tests) · `claude_worker.py`+`claude_context.py`(+test) · `metrics.py`(+test) · Signals UI (`AutopilotPage.tsx`, `components/autopilot/*`, `types/signals.ts`, `services/api/signals.ts`) · charts (`indicators.ts`, `indicators_session.ts`, `chart/TradingChart.tsx`, `chart/AccessibleDataTable.tsx`) · polish (`validateSymbol.ts`, `ConfirmModal.tsx`, `tradebot/QuickOrderForm.tsx`, `pages/TradeBotPage.tsx`, `hooks/useMarketData.ts`) · docs (`LIVE_FLIP_RUNBOOK.md`, `rollback_tv_claude.md`, `DEPLOYMENT_v4.md`, `scripts/run_quality_gates.sh`).

**Fleet 2** (`we1niul4l`, 4): `db/rate_limits.py`(+test) · `rule_engine.py` recovery(+test) · `routers/health_extended.py`(+test) · integration playbook (read-only).

**Fleet 3** (`wave3`, 5): `routers/account_routes.py` day-PnL(+test) · day-PnL frontend (`layout/Header.tsx`, `AccountPanel.tsx`, `services/api/account.ts`) · integration-review (RO) · safety-coverage-audit (RO) · frontend-arch-review (RO).

## Orchestrator-owned integration (I wire these; agents are forbidden to touch)
- `backend/routers/__init__.py` — register webhook + health_extended + account routers.
- `backend/main.py` lifespan — `if cfg.CLAUDE_WORKER_ENABLED: asyncio.create_task(claude_worker_loop())`.
- `backend/db/core.py` — fold `order_rate_window` table into `init_db` (rate_limits.py self-creates it for now).
- **Live path (careful):** close the `direct_ai_trader.py` `skip_safety` bypass by routing through `order_proposal.place_proposed_order`; add `orderRef=<candidate id>` at the `ibkr.ib.placeOrder` call (`order_executor.py:~315`) for execution idempotency; wire `metrics` helper call sites.

## Gate protocol (every integration; nothing commits on red)
- backend: `cd backend && .venv/bin/pytest -q`  (≥679 + new lanes' tests, all green)
- frontend: `cd dashboard && npm run typecheck && npm run build && npx vitest run`

## LIVE flip — gated terminal step (user authorized full autonomy incl. flip)
all gates green → `scripts/backup_db.sh` snapshot → `AUTOPILOT_MODE=LIVE` (scanner path only) → restart → verify lifespan log `mode=LIVE shadow_mode=False` → 1-share BUY canary on LIVE port **7496** → rollback ready (`AUTOPILOT_MODE=PAPER`, restart). TV/Claude path stays **PAPER (7497)** for a 7-day soak. **Hold `WORKERS=1` until the cross-process rate cap is wired.** If any precondition fails at the gate → hold PAPER and report.

## Verified facts (do not re-litigate)
backend `.venv` = py3.11.15 · repo already uses `AsyncAnthropic` · all new DB access via `db.core` `get_db()`/`transaction()` (PRAGMAs set there) · reuse `ai_decision_ledger` for cost · active frontend = `dashboard/` · models `claude-sonnet-4-20250514` / `claude-haiku-4-5-20251001` · NO `forwarded-allow-ips`.

## Landed (Fleets 1+2, on disk, tests green per-lane)
webhook_routes.py(16✓, exports `router`+`signals_router`) · order_proposal.py+mcp_server.py+claude_prompts.py(6+11✓; safety_gate lives at `services/safety_gate.py`; `place_order(rule, *, source=, skip_safety=False)`) · claude_worker.py+claude_context.py(8✓; self-gates on CLAUDE_WORKER_ENABLED, lazy imports) · metrics.py · rate_limits.py(9✓; `transaction()` already does BEGIN IMMEDIATE @core.py:38; cold-start lock retry added) · rule_engine recovery(8✓; was already correct, test-only) · health_extended.py(7✓).

## Integration checklist (ORCHESTRATOR-ONLY; reconcile, then gate)
1. `routers/__init__.py`: after Batch B add `webhook_router`, `signals_router`, `health_extended_router` (+ `account_routes` from Fleet 3). signals_router already has `Depends(get_current_user)`.
2. `main.py` lifespan (after ~line 301, before `yield`): `if cfg.CLAUDE_WORKER_ENABLED: asyncio.create_task(claude_worker_loop())`.
3. `db/core.py` init_db: fold `order_rate_window` table+`idx_order_rate_symbol_ts` (rate_limits self-creates for now).
4. **RECONCILE direct_ai_trader bypass:** playbook's suggested `place_proposed_order(...)` kwargs DO NOT match the built signature `(rule, source, user_id)`. Decide: extend the helper to accept force_safety/stop_price/is_exit/has_existing_position, OR keep direct_ai on `place_order(skip_safety=False)` and route only the NEW claude path through the helper. Do NOT break the existing `force_safety` dual-gate.
5. `order_executor.py` ~line 314: `ib_order.orderRef = trade_rec.id` before `placeOrder` (execution idempotency; check interaction with `reap_orphan_pending_trades`).
6. metrics call sites: bot_runner (order placed, cycle seconds), order_executor (filled, rate-cap hit), webhook_routes (outcomes), claude_worker (cost/calls).
7. Gate: full backend pytest + dashboard typecheck/build/vitest. Hold `WORKERS=1` until rate cap is wired into order_executor.

## Verification findings (Wave 4 `w4mqm1uks` — fix during integration)
- **REAL BUG (must fix): `claude_worker` ↔ `mcp_server` interface mismatch.** claude_worker calls `tools_spec()` (as a callable) + `dispatch_tool_call(name, input)` (2 positional args); mcp_server exposes `tools_spec` as a **list** + `dispatch_tool_call(tool_call: dict)` (1 arg). Per-lane tests passed only via a `sys.modules` shim, so the full suite stays green (worker is default-off), but it WOULD break at runtime when `CLAUDE_WORKER_ENABLED=true`. Reconcile by aligning `claude_worker` to the real mcp_server contract (list + 1-arg dict). Also: `mcp_server` does `from order_proposal import place_proposed_order` at import → patch/inspect at `mcp_server.place_proposed_order`.
  - PRECISE FIXES in `claude_worker.py`: (1) `:233` `tools=tools_spec()` → `tools=tools_spec` (it's a list). (2) `:256` `dispatch_tool_call(tu.name, tu.input)` → `dispatch_tool_call({"name": tu.name, "input": tu.input})`. (3) **3rd bug** `:165` `_classify_outcome` checks `{decline, decline_order, decline_signal}` but the real decline tool is `mark_declined` → declines misclassified as `failed`; change the set to `{"mark_declined"}`. Real mcp tool names: get_signal/get_positions/get_account/get_recent_signals/get_market_data/propose_order/mark_declined. (4) Update `tests/test_claude_worker.py` shim to the corrected contract (list `tools_spec`, 1-arg dict `dispatch_tool_call`, `mark_declined`).
- order_proposal: pass ALL `safety_gate.evaluate_runtime_safety` params explicitly (stop_price/is_exit/has_existing_position/require_autopilot_authority) — safe-by-default today but fragile (review: MEDIUM).
- `/api/signals`: does not filter by `user_id` (returns all). Fine for single-user demo; add a `user_id` filter (or document shared) before any multi-user use (review: HIGH-in-prod-only).
- webhook: add a "never log the raw body (may contain secret)" guard/comment above the parse (review: HIGH future-regression; current code does NOT leak).
- **CONFIRMED CLEAN:** webhook secret(HMAC constant-time)/IP(no XFF spoof)/freshness/idempotency/parameterized-SQL all hardened; `claude_worker` is PAPER-only and cannot reach the live broker; **e2e TV→paper-fill PASSES**; **autopilot guard-sequence locked** (check_trade_risk→portfolio→safety_gate→place_order(skip_safety=False), 5 tripwire tests). New e2e files: `tests/test_e2e_tv_to_paper_fill.py`, `tests/test_e2e_autopilot_live_smoke.py`.
- The integration-safety-review's "double-kernel-run" BLOCKER is OVERSTATED: it's the EXISTING intentional dual-gate (bot_runner also runs safety_gate then place_order(skip_safety=False)). Keep the explicit-then-place_order pattern; do NOT collapse to skip_safety=True.

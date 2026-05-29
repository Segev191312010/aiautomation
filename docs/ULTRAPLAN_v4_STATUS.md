# ULTRAPLAN v4 — Live Status Ledger

> Durable source of truth for this build. Updated as work proceeds. Read this first when resuming.

- **Branch:** `feature/ultraplan-v4` (off `feature/deep-review-p0-batch` @ `e1f42bd`)
- **Plan:** `docs/ULTRAPLAN_v4.md`  ·  **Memory:** `~/.claude/.../memory/ultraplan-v4-trading-build.md`

## Committed
- **W1 foundation — `9f252bc`**: py3.11 pin (Dockerfile+CI), TV statuses + `tv_idempotency` table, `retention.py` GC bug fix, TV/Claude config + `.env` keys, `anthropic`/`prometheus_client` pinned, backup/restore scripts, +7 tests. Suite **672 → 679 green**.

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


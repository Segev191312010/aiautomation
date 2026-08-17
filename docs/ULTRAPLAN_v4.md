# ULTRAPLAN v4 — TV Webhook + MCP + Claude Worker + Signals UI (code-grounded)

> **SUPERSEDED FOR RELEASE PURPOSES.** Stage 9A (2026-07-27) makes this a
> historical build plan. Its LIVE-flip and multi-worker claims must not be
> executed; `ROADMAP_TEAM_PLAN.md` and the Stage 9A risk register are current,
> and the application has a code-owned LIVE release fence.

> Supersedes ULTRAPLAN v3. v3 was a document-only spec; v4 is rewritten against the
> **actual** `/Users/salomon/aiautomation` repo after an 8-lane read-only recon. Where v3's
> assumptions were wrong, v4 corrects them (see §2). Account: LIVE IBKR ~$5,600.

## 1. Execution model (how this is actually being built)

- **Branch:** all work on `feature/ultraplan-v4` (off `e1f42bd`). NOT merged to `main` this
  sprint — `main` is 145 commits ahead via an unrelated `src/`-to-root reorg; reconciling it
  is a separate task.
- **Who does the work:** the autonomous multi-agent fan-out with forced structured output
  proved unreliable on this machine (2 build waves thrashed ~37 min and failed the schema
  gate). On a live-money repo that trade isn't worth it. So: **Claude drives each wave in
  verified, committed increments**, using sub-agents only for genuinely independent,
  failure-cheap parallel chunks (recon, review, isolated UI lanes) — and without forced
  schemas.
- **Quality gate every wave:** `cd backend && .venv/bin/pytest -q` stays green (baseline 672)
  + new tests; `cd dashboard && npm run typecheck && npm run build && npx vitest run` for
  frontend waves. Nothing commits on a red gate.
- **LIVE flip = gated terminal step (full autonomy granted, executed safely):**
  all gates green → `scripts/backup_db.sh` snapshot → set `AUTOPILOT_MODE=LIVE` (scanner
  path only) → restart → verify lifespan log → 1-share canary on LIVE port 7496 → watch →
  rollback path ready (`AUTOPILOT_MODE=PAPER`, restart). The TV/Claude path stays
  `AUTOPILOT_MODE=PAPER` for a 7-day soak regardless. If any precondition fails at the gate,
  **hold PAPER and report** rather than flip.

## 2. Verified H+0 (real code) — corrections to v3

| v3 said | Reality (cited) | Action |
|---|---|---|
| venv is 3.11.15; pin Docker 3.12→3.11 | `backend/.venv`=3.11.15 ✓; `backend/Dockerfile:11`=3.12-slim, `ci.yml:49`=3.12 | **Done W1**: pinned both to 3.11 |
| sync `Anthropic()` blocks loop (H2) | repo uses `AsyncAnthropic` (`ai_model_router.py:87`, `candidate_registry.py:109`), awaited | worker reuses async pattern → H2 cannot arise |
| add `--forwarded-allow-ips="*"` (L1.2) | Dockerfiles have NO proxy flags | **Do NOT add** — would create the spoof hole |
| per-conn PRAGMAs missing (M5) | `db/core.py get_db()/transaction()/init_db()` set FK=ON, busy_timeout=10000, synchronous=FULL, WAL | **all new DB code uses `get_db()`/`transaction()`**, never raw `aiosqlite.connect()` |
| ai_guardrails dropped (M4) | `safety_kernel.check_all()` enforces kill-switch/daily-loss/no-shorts (`:387-389`); ai_guardrails = AI-action audit | safe; add explicit reject tests anyway |
| new `claude_calls` table for cost | `ai_decision_ledger` + `ai_learning.py` already track tokens+cost (`MODEL_PRICING`) | **reuse the ledger**, don't duplicate |
| flat `backend/webhook_routes.py` | routers live in `backend/routers/` + `register_routers()` (`main.py:331`); public-route pattern in `routers/auth.py` | webhook → `backend/routers/webhook_routes.py` |
| model `claude-sonnet-4-6` | repo uses `claude-sonnet-4-20250514` + `claude-haiku-4-5-20251001` | reuse existing IDs |
| — (new bug found) | `retention.py:201` filtered non-existent statuses → `applied`/`failed` leaked | **Done W1**: fixed |
| order idempotency (M1) | no `orderRef`; `order_executor.py:312/315` save-then-place; `reap_orphan_pending_trades()` at startup (`main.py:225`) | add `orderRef=candidate_id` in W3 |
| frontend `dashboard/` (355 tests) | active=`dashboard/` (React 18.3.1/TS5.5/Vite/Vitest, 22 test files); chart=`components/chart/TradingChart.tsx` | target `dashboard/`; `frontend/`+`stocksdashboard/` dead |

## 3. Architecture (real files)

```
TradingView ──HTTPS──> POST /api/webhook/tv   (backend/routers/webhook_routes.py, PUBLIC + HMAC + IP + freshness)
  → tv_idempotency (UNIQUE event_key)  +  direct_candidates(source='tv_webhook', status='pending_review')   [one tx, parent before child]
  → (only if CLAUDE_WORKER_ENABLED) claude_worker  → AsyncAnthropic + tools → order_proposal.place_proposed_order(...)
       └─ order_executor.place_order(skip_safety=False)  [reuses existing safety_kernel + per-symbol rate cap]
       └─ ──PAPER PORT 7497── (TV/Claude path, stays paper through soak)

Existing scanner → direct_candidates(source='scanner', status='queued') → bot_runner._run_cycle
       → order_executor.place_order(skip_safety=False) → ──LIVE PORT 7496── (AUTOPILOT_MODE=LIVE)
```
Status values share one table, disjoint by value. Scanner: `queued→draining→applied|failed|expired`.
TV: `pending_review→in_review→applied|declined_by_ai|failed|expired`.

## 4. Waves (each = a gated increment)

**W1 — Foundation (IN PROGRESS).** Files: `backend/Dockerfile`, `.github/workflows/ci.yml`,
`backend/config.py` (TV_*/CLAUDE_WORKER_* keys), `backend/.env.example`, `backend/requirements.txt`
(pin anthropic/cachetools/prometheus_client), `scripts/backup_db.sh`+`restore_db.sh`,
`backend/db/core.py` (tv_idempotency table — done), `backend/db/direct_candidates.py` (TV statuses +
purge — done), `backend/db/retention.py` (GC bug — done). Tests: tv-statuses, idempotency-migration,
retention-fix. **Gate:** pytest ≥672+new green.

**W2 — TV webhook ingest.** `backend/routers/webhook_routes.py` (NEW): `TVAlertPayload`
(SecretStr+`field_serializer` redaction, NaN/inf reject, `action∈{buy,sell}`); auth order IP→secret→
freshness→idempotency; HMAC `compare_digest`; **insert direct_candidates (parent) BEFORE tv_idempotency
(child)** via `transaction()`; `IntegrityError→duplicate`. `GET /api/signals` (authed). Register in
`routers/__init__.py`. `docs/tv_alert_template.pine` with a real ISO `{{timenow}}`. ~15 adversarial
tests + curl smoke (uses a **real** ISO timenow, not `""`). **Gate:** 200/401/403/422/duplicate.

**W3 — order_proposal + MCP + idempotency (most sensitive).** `backend/order_proposal.py`
`place_proposed_order(rule, source, user_id)→ProposalResult` reusing the existing guard chain
(check_trade_risk→portfolio→safety_gate→`place_order(skip_safety=False)`); explicit `deferred` status
when the rate cap returns None (no phantom `applied`). Add `orderRef=candidate_id` at the
`ibkr.placeOrder` call for execution idempotency (M1). `backend/mcp_server.py` tool defs +
`mcp_propose_order`/`mcp_mark_declined`. Tests incl. kill-switch/daily-loss/no-shorts/oversized reject
through the helper (closes M4). **Gate:** grep no `skip_safety=True` new callers; reject tests green.

**W4 — Claude worker.** `backend/claude_worker.py` modeled on `ai_learning_loop()`
(`asyncio.create_task` in `main.py` lifespan, `while True`+sleep), **AsyncAnthropic** (await; no
blocking — H2), cross-process candidate claim via `transaction()` `UPDATE…RETURNING`, cost tracked
through `ai_decision_ledger`/`ai_learning` (`CLAUDE_DAILY_COST_USD_CAP`), proper tool-result loop
(not single-turn — M11), defined outcome for empty-tool/reject (no spurious `declined_by_ai`),
in_review reaper. Default `CLAUDE_WORKER_ENABLED=false`. **Gate:** claim atomicity + approve/decline/
error/cap tests.

**W5 — Cross-process rate cap + Signals UI + obs.** SQLite-backed `try_acquire_order_slot`
(replaces the per-process `asyncio.Lock` so `WORKERS=2` is safe); Prometheus `/metrics` incl.
webhook-outcome counters + "seconds since last accepted signal" (M9). `dashboard/`: Signals tab on
`AutopilotPage.tsx` (add to `ConsoleTab` union + tabs array), `SignalsTable`/`TVSignalDetail`/
`ClaudeDecisionPanel`, `services/api/signals.ts`. **Gate:** WORKERS=2 cap test; typecheck+build+vitest.

**W6 — QA + security + docs + flip.** e2e (TV→paper fill, mocked broker + a real-key smoke);
`/security-review`; `scripts/run_quality_gates.sh`; `docs/DEPLOYMENT.md`+`LIVE_FLIP_RUNBOOK.md`+
`rollback`. Then the **gated LIVE flip** (§1).

## 5. Risk register (updated vs reality)
- Per-process rate cap + `--workers 2` default in Dockerfile CMD → real multi-worker over-trade risk
  until W5 lands the SQLite cap. Mitigation: keep WORKERS=1 until W5, or land W5 before flip.
- M1 (no orderRef): bounded today by `reap_orphan_pending_trades()`; closed in W3.
- 145-behind-`main`: do not merge this sprint; reconcile separately.
- Stale nested `aiautomation/`, `aiautomation-pr3/`: left untouched (not ours to delete).

## 6. Status log
- 2026-05-29: recon complete; v4 authored; **W1 edits landed** (Docker/CI 3.11, TV statuses,
  retention fix, config keys, tv_idempotency table). Remaining W1: requirements pin, .env.example,
  backup scripts, 3 test files, suite run + commit.

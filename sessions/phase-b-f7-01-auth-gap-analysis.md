# Phase B — F7-01 Auth Gap Analysis (2026-04-11, updated 2026-04-17)

**Original analysis:** 2026-04-11 by `security-auditor` agent
**Fix status:** 6/8 DONE, 1 mitigated, 1 deferred

---

## Fix Status

| # | Fix | Status | Commit |
|---|-----|--------|--------|
| 1 | `POST /api/auth/token` requires `JWT_BOOTSTRAP_SECRET` | **DONE** | `ba6937e` |
| 2 | Router-level auth on 4 orphan modules (`risk_api`, `advisor_api`, `rule_builder_api`, `diagnostics_api`) | **DONE** | `f7b471e` |
| 3 | Route prefix collision `/api/rules/*` (rule_builder_api vs rules_routes) | **MITIGATED** by Fix 2 (auth on both). Semantic collision remains but not a security issue. |
| 4 | Protect mutating market routes (`subscribe`/`unsubscribe`) | **DONE** | `f7b471e` |
| 5 | Protect event log (`/api/events/log`) | **DONE** | `f7b471e` |
| 6 | Auth-gate `/api/health/detailed` (leaks PID + IBKR port) | **OPEN** — low risk on localhost |
| 7 | Tighten rate limits (was 1000/min) | **DONE** — 300/min general, 10/min auth | `08440ad` |
| 8 | Multi-tenancy hardcoding (`user_id="demo"`) | **PARTIAL** — screener + backtest routes fixed (`08440ad`). Others remain (single-user acceptable). |

## Still OPEN

### C5: JWT in WebSocket query params (deferred from `cbc8c0f`)
- WS connections pass JWT as `?token=...` query param (visible in server logs, browser history)
- Requires first-message auth rewrite of both server and client WS code
- **Priority:** P3-level. Not a risk on localhost; required before remote deployment.

### Fix 6: `/api/health/detailed` leaks PID + IBKR port
- Low risk on localhost. Should add `Depends(get_current_user)` before any remote exposure.

### Fix 8 remainder: `user_id="demo"` hardcoding
- Acceptable for single-user localhost. Block for multi-user deployment.

## Verdict
**Safe for localhost-only operation: YES.**
**Safe for ANY remote exposure: NO** until C5 + Fix 6 are closed.

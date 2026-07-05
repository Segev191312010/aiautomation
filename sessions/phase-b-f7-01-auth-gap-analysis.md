# Phase B — F7-01 Auth Gap Analysis (2026-04-11, updated 2026-04-17)

**Original analysis:** 2026-04-11 by `security-auditor` agent
**Fix status:** 7/8 DONE, 1 mitigated, 1 partial. C5 is also DONE.

---

## Fix Status

| # | Fix | Status | Commit |
|---|-----|--------|--------|
| 1 | `POST /api/auth/token` requires `JWT_BOOTSTRAP_SECRET` | **DONE** | `ba6937e` |
| 2 | Router-level auth on 4 orphan modules (`risk_api`, `advisor_api`, `rule_builder_api`, `diagnostics_api`) | **DONE** | `f7b471e` |
| 3 | Route prefix collision `/api/rules/*` (rule_builder_api vs rules_routes) | **MITIGATED** by Fix 2 (auth on both). Semantic collision remains but not a security issue. |
| 4 | Protect mutating market routes (`subscribe`/`unsubscribe`) | **DONE** | `f7b471e` |
| 5 | Protect event log (`/api/events/log`) | **DONE** | `f7b471e` |
| 6 | Auth-gate `/api/health/detailed` (leaks PID + IBKR port) and `/api/health/bot` | **DONE** — `/api/health` stays open for probes; deep operator health moved to authenticated `/api/health/deep` |
| 7 | Tighten rate limits (was 1000/min) | **DONE** — 300/min general, 10/min auth | `08440ad` |
| 8 | Multi-tenancy hardcoding (`user_id="demo"`) | **PARTIAL** — screener + backtest routes fixed (`08440ad`). Others remain (single-user acceptable). |

## Recently Closed

### C5: JWT in WebSocket query params
- **DONE** — WS clients now send JWTs through `Sec-WebSocket-Protocol` (`['bearer', token]`) instead of URL query params.
- Regression coverage: `test_validate_ws_token_query_string_no_longer_accepted`.

### Fix 6: `/api/health/detailed` leaks PID + IBKR port
- **DONE** — `/api/health/detailed` and `/api/health/bot` require `Depends(get_current_user)`.
- The previously shadowed deep health payload is now reachable at authenticated `/api/health/deep`.

## Still OPEN

### Fix 8 remainder: `user_id="demo"` hardcoding
- Acceptable for single-user localhost. Block for multi-user deployment.

## Verdict
**Safe for localhost-only operation: YES.**
**Safe for ANY remote exposure: NOT YET** until the remaining multi-user and production-hardening work is complete.

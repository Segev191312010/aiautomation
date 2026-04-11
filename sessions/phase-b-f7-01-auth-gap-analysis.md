# Phase B — F7-01 Auth Gap Analysis (2026-04-11)

**Status:** CRITICAL. Worse than the 2026-03-31 audit suggested. Produced during Phase A market-closed downtime via `security-auditor` agent.

**Source:** Full `backend/` route enumeration + `get_current_user` trace + middleware audit.

**TL;DR:** The original F7-01 finding ("demo user fallback in `get_current_user`") has been CLOSED — `backend/auth.py:118-135` now strictly rejects unauthenticated and invalid-token requests with 401. **However**, the auth bypass has moved: `POST /api/auth/token` issues a valid JWT to anyone unconditionally, and four entire API modules never attach an auth dependency at all. The end result is identical to the original finding: localhost is fine, remote exposure is catastrophic.

---

## Current state of `get_current_user`

`backend/auth.py:118-135` — **strictly enforcing.**
- No `Authorization` header → 401 (line 121)
- Wrong scheme / empty token → 401 (line 125)
- Invalid / expired JWT → 401 (line 129)
- User row missing in DB → 401 (line 133)

Phase 0 closed the original in-dependency fallback. `_raise_unauthorized` now occupies the line the 2026-03-31 audit cited. `seed_demo_user` (`auth.py:76`) is idempotent and only called from `db/core.py::init_db` once per process start.

---

## Critical issue #1: `POST /api/auth/token` is publicly reachable

**File:** `backend/routers/auth.py:18-25`

**Behavior:** No client credential check. Returns `create_token("demo")` to any caller. The only log-level hint is a `warning` when `SIM_MODE=false`. Rate-limited by `auth_limit=200/min` bucket (`main.py:277`), which is a DoS cap, not an auth gate.

**Impact:** Every route in the "AUTH_OK" column below is theoretically protected but practically bypassable. An attacker reaching port 8000 runs:
```
curl -sf http://target:8000/api/auth/token | jq -r .access_token
```
and then has a valid bearer for every authenticated endpoint. This single line undoes the entire Phase 0 auth hardening for any remote deployment.

**Fix priority:** #1. Until this is closed, items #2–#7 below are theoretical improvements. They only start mattering once the token bootstrap requires a shared secret.

---

## Critical issue #2: Four API modules with zero auth

These routers register routes with `app.include_router(...)` in `main.py` but have NO `dependencies=[Depends(get_current_user)]` at router level AND no per-route `Depends` anywhere. Every route in each of these modules is fully anonymous.

### `backend/risk_api.py`

| Method | Path | Line | Impact |
|---|---|---|---|
| GET | `/api/risk/portfolio` | 43 | Leaks full PnL / positions |
| GET | `/api/risk/check/{symbol}` | 65 | Low |
| POST | `/api/risk/position-size` | 72 | Compute only, low mutation |
| GET | `/api/risk/drawdown` | 79 | Leaks drawdown |
| GET | `/api/risk/correlation` | 95 | Low |
| GET | `/api/risk/sector-exposure` | 104 | Leaks exposure |
| GET | `/api/analytics/pnl` | 109 | Leaks PnL |
| GET | `/api/analytics/pnl/daily` | 117 | Leaks PnL |
| GET | `/api/analytics/performance` | 125 | Leaks performance |
| GET | `/api/analytics/trades/matched` | 133 | Leaks closed trades |
| **PUT** | **`/api/risk/settings`** | **148** | **DANGEROUS — mutates live risk limits in-process** |
| GET | `/api/risk/settings` | 158 | Leaks current risk config |

### `backend/advisor_api.py`

| Method | Path | Line | Impact |
|---|---|---|---|
| GET | `/api/advisor/report` | 38 | Leaks advice |
| GET | `/api/advisor/recommendations` | 47 | Leaks advice |
| GET | `/api/advisor/analysis` | 60 | Leaks analysis |
| GET | `/api/advisor/daily-report` | 73 | Leaks report |
| **POST** | **`/api/advisor/auto-tune`** | **80** | **DANGEROUS — when `apply=true`, mutates live rule parameters via `build_full_report(apply_tune=True)`** |
| GET | `/api/advisor/rule/{id}` | 95 | Low |

### `backend/rule_builder_api.py`

| Method | Path | Line | Impact |
|---|---|---|---|
| GET | `/api/rules/templates` | 43 | Low |
| GET | `/api/rules/templates/{id}` | 48 | Low |
| **POST** | **`/api/rules/from-template`** | **56** | **Creates DB rule** |
| POST | `/api/rules/validate` | 78 | Validation only |
| **POST** | **`/api/rules/{id}/clone`** | **92** | **Creates DB rule** |
| POST | `/api/rules/export` | 108 | Full rule export (info leak) |
| **POST** | **`/api/rules/import`** | **114** | **Bulk-creates DB rules from caller-supplied JSON** |

### `backend/diagnostics_api.py`

| Method | Path | Line | Impact |
|---|---|---|---|
| GET | `/api/diagnostics/overview` | 19 | Low |
| GET | `/api/diagnostics/indicators` | 24 | Low |
| GET | `/api/diagnostics/indicators/{c}` | 29 | Low |
| GET | `/api/diagnostics/indicators/{c}/history` | 37 | Low |
| GET | `/api/diagnostics/market-map` | 42 | Low |
| GET | `/api/diagnostics/sector-projections/latest` | 47 | Low |
| GET | `/api/diagnostics/sector-projections/history` | 57 | Low |
| GET | `/api/diagnostics/news` | 62 | Low |
| **POST** | **`/api/diagnostics/refresh`** | **70** | **Triggers expensive refresh job (DoS lever)** |
| GET | `/api/diagnostics/refresh/{id}` | 91 | Low |

---

## Critical issue #3: Route prefix collision at `/api/rules/*`

**Problem:** `routers/rules_routes.py` declares router-level `Depends(get_current_user)` for prefix `/api/rules` (CRUD endpoints). `rule_builder_api.py` ALSO uses prefix `/api/rules` with no auth.

**Registration order in `main.py`:**
1. `rule_builder_router` included at line 260
2. `register_routers(app)` (which adds `rules_routes.py`) at line 269

**FastAPI matching:** First registered route wins for exact matches. The non-overlapping subpaths in `rule_builder_api.py` (`/templates`, `/validate`, `/from-template`, `/{id}/clone`, `/export`, `/import`) are therefore reachable WITHOUT auth, while the CRUD paths (`GET /`, `POST /`, `DELETE /{id}`) fall through to the auth'd router.

**Trap:** Anyone reviewing the rules module will see `rules_routes.py` has `Depends(get_current_user)` and conclude the whole namespace is protected. It is not.

**Fix options:**
1. Re-prefix `rule_builder_api.py` to `/api/rule-builder/*` (cleanest, cosmetic break)
2. Add `dependencies=[Depends(get_current_user)]` to `rule_builder_api.py`'s `APIRouter(...)` call
3. Change include order so `rules_routes` registers first (fragile — depends on FastAPI internals)

Recommend option 2 as part of critical-issue-#2 fix pass.

---

## Critical issue #4: Unauth'd mutating routes in other routers

### `backend/routers/market_routes.py`

No router-level `Depends`, no per-route auth on these state-mutating routes:

| Method | Path | Line | Impact |
|---|---|---|---|
| **POST** | **`/api/market/{sym}/subscribe`** | **72** | **Mutates `_active_rt_subs`, creates IBKR realtime subscription (consumes broker quota)** |
| **POST** | **`/api/market/{sym}/unsubscribe`** | **93** | **Mutates `_active_rt_subs`** |

Read-only routes in the same file (`/bars`, `/price`, `/watchlist`, `/yahoo/{sym}/bars`, `/indicators`) are AUTH_NONE, which is a PII/data posture decision — not a hard bug.

### `backend/routers/events.py`

Zero auth. Leaks event-bus history and session metadata:
- `GET /api/events/metrics` (line 7)
- `GET /api/events/log` (line 18) — **leaks trade decisions**
- `GET /api/events/sessions` (line 27)

### `backend/routers/sectors.py`

Zero auth on 3 read-only routes. Low risk; public-data patterns. Acceptable for localhost.

---

## Information disclosure (read-only, no mutation)

These routes are unauth'd and reveal state an attacker could use:
- `GET /api/status` (`routers/status.py:76`) — leaks `autopilot_mode`, `autopilot_emergency_stop`, `autopilot_daily_loss_locked`, `sim_mode`, `is_paper`
- `GET /api/health/detailed` (`health.py:142`) — leaks PID, Python version, IBKR port, paper/live mode
- All 13 `GET /api/stock/{sym}/*` routes (`stock_profile_api.py:14-105`) — public data, acceptable

---

## Middleware confirmation

- **RateLimitMiddleware** (`main.py:277`, `middleware.py:62`): ACTIVE. `general_limit=1000/min`, `auth_limit=200/min` per IP. Path-based check applies to all methods. **Note:** 1000/min is loose; should be tightened before remote exposure.
- **SecurityHeadersMiddleware** (`main.py:278`): ACTIVE. Adds `X-Content-Type-Options`, `X-Frame-Options`, `X-XSS-Protection`, `Referrer-Policy`, `Cache-Control: no-store` for `/api/*`. Missing: CSP, HSTS.
- **CORSMiddleware** (`main.py:279-292`): ACTIVE. Restricted to `localhost:5173/5174/8000` + `127.0.0.1:*`, `allow_credentials=True`, `allow_methods=["*"]`, `allow_headers=["*"]`.
- **WebSocket auth** (`main.py:421-430`, `main.py:792-802`): verifies JWT via `verify_token(query_params["token"])` BEFORE accepting the upgrade. Origin check applies after. Phase 0 hardening intact. **No regression.**

---

## Bonus finding: multi-tenancy hardcoded user_id

Even on auth'd routes, several handlers hardcode `user_id="demo"` instead of reading from the authenticated user:
- `routers/screener_routes.py:60` (preset save)
- `routers/backtest_routes.py:65` (backtest save)
- `routers/backtest_routes.py:76` (backtest history)

Breaks any future multi-user deployment. Acceptable for single-user localhost.

## Bonus finding: `/api/alerts/test` cross-user broadcast

`routers/alerts_routes.py:61` broadcasts `alert_fired` to ALL connected WebSocket clients via `mgr.broadcast`. Auth'd today, but multi-tenancy bug latent if more than one user ever connects. Should scope to the requesting user.

---

## Recommended fix ordering for Phase B

Each item is independently mergeable. Gates run after each.

### Fix 1 (BLOCKER — do first) — Close `POST /api/auth/token` bootstrap
- Add a server-side bootstrap secret in `cfg` (e.g. `JWT_BOOTSTRAP_SECRET` from env)
- Require clients to present it as a header or body field
- Reject the request if the secret is missing or wrong
- Optionally: disable the endpoint entirely when `SIM_MODE=false` AND not in test mode
- Impact: flips every AUTH_OK route from theoretical to actual protection

**Files:** `backend/routers/auth.py`, `backend/config.py`

### Fix 2 — Add router-level auth to the four orphan modules
- `risk_api.py`: `APIRouter(prefix="/api", dependencies=[Depends(get_current_user)])`
- `advisor_api.py`: same
- `rule_builder_api.py`: same (also solves critical issue #3 — the `/api/rules/*` collision)
- `diagnostics_api.py`: same

**Impact:** closes the four worst mutation gaps + the info-disclosure PnL leaks.

**Tests:** each route needs a regression test that asserts 401 without token. Can be one parametrized fixture.

**Files:** the four modules above + 1 new pytest file.

### Fix 3 — Route prefix hygiene
- Either re-prefix `rule_builder_api.py` to `/api/rule-builder/*` OR rely on Fix 2 to close the collision
- Document the decision in `sessions/review-stage-1-backend-composition.md`

### Fix 4 — Protect mutating market routes
- `POST /api/market/{sym}/subscribe|unsubscribe` need `Depends(get_current_user)`

**Files:** `backend/routers/market_routes.py`

### Fix 5 — Protect event log
- `/api/events/log` reveals trade decisions. Add router-level `Depends`.

**Files:** `backend/routers/events.py`

### Fix 6 — Consider auth-gating `/api/health/detailed`
- Reveals PID + IBKR port. Low risk on localhost, higher before any remote access.
- Leave `/api/health` and `/api/health/ready` public (liveness/readiness probes).

**Files:** `backend/health.py`

### Fix 7 — Tighten rate limits
- `general_limit` 1000/min is loose. Consider 300/min for authed routes, 10/min specifically on `/api/auth/token` once it requires the bootstrap secret.
- Add burst controls.

**Files:** `backend/middleware.py`, `backend/config.py`

### Fix 8 (optional) — Multi-tenancy hardening
- Remove `user_id="demo"` hardcoding in screener + backtest route handlers
- Scope `alerts_routes.py` test broadcast to single user

**Files:** `backend/routers/screener_routes.py`, `backend/routers/backtest_routes.py`, `backend/routers/alerts_routes.py`

---

## Scope for Phase A

**Zero.** None of F7-01 work is in scope for Phase A. Phase A is commit-soak-push for the Phase 1 diff. This analysis exists to give Phase B a clean punch list when the operator is ready.

**Phase A hard rule:** the soak must run on `localhost only`, never exposed via ngrok/tunnel/reverse proxy/LAN. Do not touch any of the fix items above until after Phase A is green and pushed.

---

## Verdict

**Safe for localhost-only operation: YES.** Loopback bind + local-only CORS keeps the bootstrap bypass and the four orphan modules contained to same-machine callers.

**Safe for ANY remote exposure: NO.** `POST /api/auth/token` hands out a valid bearer to any anonymous caller. Even with Fix 1 in place, Fixes 2–5 must land before the first remote deployment.

**Phase B sequencing:** Fix 1 before any remote exposure. Fixes 2–5 together in the same sprint. Fixes 6–8 are follow-ups.

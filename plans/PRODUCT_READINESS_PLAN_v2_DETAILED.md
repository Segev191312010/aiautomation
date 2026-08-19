# Product Readiness Plan v2 — Docker/PAPER Baseline (Ticket-Level)

**Repo:** `Segev191312010/aiautomation`
**Branch:** `integration/post-reconciliation`
**Baseline SHA:** `4d8db93b60e4ec55d45e840babf03a7f3ab749cf` (`4d8db93`) — *verified against the remote; HEAD commit message: `fix(auth): allow docker bridge bootstrap locally`*
**Execution mode:** PAPER only. LIVE hard-blocked by the Stage 9A fence.
**Dashboard:** http://localhost:3000 (nginx → backend:8000)

**Release strategy:** All v2 ticket PRs target `integration/post-reconciliation`. `master` is promotion-only and remains unchanged until Phase 7. After every gate passes, promote the complete verified history once, without force-push or squash.
**Baseline anchor:** The annotated tag `v2-baseline` points to `4d8db93b60e4ec55d45e840babf03a7f3ab749cf`. A repository ruleset matching `refs/tags/v2-*` must block update and deletion before implementation begins.

This is v2 of the plan. v1 was directionally correct but written at "objective" granularity — an engineer could not pick up a phase and start working without re-deriving the work. v2 decomposes every phase into **independently assignable tickets**, each with: verified file references, the exact change, the API/data contract, the tests that must exist, machine-checkable acceptance criteria, and the evidence artifact it must produce.

---

## How to read this document

Every ticket uses the same block:

| Field | Meaning |
|---|---|
| **ID** | `P<phase>-T<n>` — use this as the GitHub issue title prefix and the branch suffix (`fix/p2-t3-timeframe-map`) |
| **Owner** | E1 Frontend/Charts · E2 Backend/Notifications · E3 Screener/Data · E4 AI/Ops |
| **Depends** | Tickets that must be merged first. Anything without a dependency can start day 1. |
| **Files** | Verified paths at `4d8db93`. Line numbers are indicative — **grep the symbol, don't trust the line**. |
| **Change** | What to actually do, at implementation granularity. |
| **Contract** | The interface this ticket freezes (HTTP shape, TS type, DB column, env var). Once merged, changing it needs an ADR. |
| **Tests** | Test file + named cases. A ticket is not done without these. |
| **Accept** | Machine-checkable. If a human has to "look and feel good about it", rewrite the criterion. |
| **Evidence** | Artifact path under `docs/evidence/<AUDITED_SHA>/`. Phase 7 verifies these exist. |
| **Size** | S ≈ ≤0.5d · M ≈ 1–2d · L ≈ 3–5d |

**Ticket exit rule (applies to every ticket, no exceptions):**
1. `scripts/run_quality_gates.sh` green (or `SKIP_DOCKER=1` locally, full run in CI).
2. New tests fail on the parent commit and pass on the ticket commit — prove it in the PR body.
3. No secret values in code, logs, error bodies, or test fixtures.
4. Evidence artifact committed under `docs/evidence/`.
5. PR body records the SHA it was branched from.

**Evidence-SHA convention:** `AUDITED_SHA` is the exact source/configuration commit tested and used to build the runtime image. Evidence generated from it is stored under `docs/evidence/<AUDITED_SHA>/` and committed afterward in evidence-only commits. Those commits may change only that evidence directory. Any executable, dependency, configuration, migration, Docker, or gate-script change invalidates the bundle and requires a new `AUDITED_SHA`. Release receipts record both `AUDITED_SHA` and the evidence-carrier tip; deployment uses the image built from `AUDITED_SHA`.

---

## Reference corrections (read this before assigning work)

v1 cited several paths that have moved or were already fixed. Assigning tickets from stale references is how you burn a senior engineer's first day.

| v1 claim | Verified state at `4d8db93` | Impact |
|---|---|---|
| `dashboard/src/pages/ChartsPage.tsx:11` holds the sidecar dependency | **Stale.** `ChartsPage.tsx:9` imports `buildIbMultiChartUrl` / `buildTradingViewUrl` from `dashboard/src/utils/tradingView.ts`. The `127.0.0.1:5001` default lives at `dashboard/src/utils/tradingView.ts:6` (`DEFAULT_IB_CHART_BASE`), overridable via `VITE_IB_CHART_BASE`. | The sidecar is already isolated behind a util + env var and documented in `docs/TRADINGVIEW_CHARTS.md`. Phase 2 is *narrower* than v1 assumed: it's a deletion + mode removal, not an extraction. |
| "Earlier audits found no matching backend route" for `/api/push/subscribe` | **Stale.** `backend/routers/push_routes.py:35` mounts `prefix="/api/push"` with `GET /status`, `POST /subscribe`, `POST /subscription/status`, `DELETE /subscribe`, `GET/PUT /preferences`, `POST /test`. Registered in `backend/routers/__init__.py` (Batch B). `backend/push_service.py` already has VAPID handling, `is_allowed_push_endpoint()` origin allowlist, per-`user_id` subscription lists, preference lookup, and failure recording. | Phase 3 is **verification + gap-closing**, not greenfield. Re-scoped below. |
| `backend/models.py:387` excludes `us_all` from the request contract | **Stale.** `backend/models.py:33` — `_VALID_UNIVERSES = {"sp500","nasdaq100","etfs","all","us_all"}`. | Ticket removed; replaced by a *bounding* ticket (P4-T5). |
| `dashboard/src/components/screener/UniverseSelector.tsx:5` doesn't expose the full universe | **Stale.** `UniverseSelector.tsx:6-12` lists `sp500, nasdaq100, etfs, us_all, custom`. | Ticket removed. |
| `dashboard/src/store/screenerStore.ts:132` clears results before replacement | **Partially fixed.** `runScan` (`screenerStore.ts:162`) guards re-entry with `if (get().scanning) return` and sets results only on success — but it **does** wipe `enriched: {}` on every scan and has **no cancellation / stale-response guard** (a plain re-entry guard, not an AbortController). | P4-T1/T2 rewritten to target the real gap. |
| Plan proposes new gate scripts | **Already exist.** `scripts/run_quality_gates.sh` (6 gates), `scripts/check_db_path.sh`, `scripts/backup_db.sh`, `scripts/restore_db.sh`, `scripts/run_ws_isolation_drill.py`, `scripts/validate_paper_readiness.py`, `scripts/paper_lifecycle_simulator.py`. | Phase 7 extends existing scripts instead of writing new ones. |
| — | **New finding.** `scripts/check_db_path.sh` defaults `canonical_path=backend/trading_bot.db`, but `docker-compose.yml` sets `DB_PATH=/data/trading_bot.db`. The DB-path guard therefore validates a *different* canonical path than the Docker runtime uses. | Blocking bug → **P1-T2**. |

Also already in the repo and worth reusing rather than reinventing: `docs/adr/0001`–`0008` (trade truth, autopilot mode semantics, decision ledger & replay, degraded-data policy, execution authority, broker reconciliation, security/release boundary), `docs/LIVE_FLIP_RUNBOOK.md`, `docs/AI_WALK_FORWARD_EVIDENCE.md`, `docs/PAPER_OFFLINE_LIFECYCLE_DRILL.md`, `docs/evidence/ws-isolation-drill-template.md`, `docs/evidence/2026-08-18-paper-readiness-audit.md`.

---

## Phase 0.5 — Repository hygiene and secret-scan scope

### P0.5-T1 — Audit, migrate, then retire legacy artifacts
- **Owner:** E4 · **Depends:** — · **Size:** M
- **Files:** `backend/main.py`, `frontend/`, `logs/`, `sessions/`, `handoffs/`, `.gitignore`, workflow documentation, `docs/evidence/`
- **Change:** Inventory and classify every tracked artifact first. Deprecate and test `/trading` and `/static`; migrate any supported legacy behavior to the dashboard before removing the three legacy `frontend/` files. Relocate authoritative records from `logs/`, `sessions/`, and `handoffs/` into `docs/history/` or `docs/evidence/`, update every reference and workflow instruction, then prune only vacated paths after explicit owner sign-off and green quality gates. P1-T8 scans tracked HEAD with value-aware rules; history scanning is a separate, redacted finding and never prints credential values.
- **Contract:** The active frontend is `dashboard/`. Protected historical artifacts are not deleted until their references and governing value have been reviewed and migrated. Durable release evidence lives under `docs/evidence/<AUDITED_SHA>/`.
- **Tests:** backend route tests freeze the replacement behavior for `/trading` and confirm legacy static mounting is removed only after migration; the secret scanner includes a safe canary self-test proving a fake secret fails without treating variable names or empty placeholders as secrets.
- **Accept:** classification manifest approved; replacement route behavior tested; no stale references remain before pruning; tracked-HEAD secret scan passes; historical-scan disposition contains only redacted metadata; full gates green. Any deletion under `logs/` or `sessions/` requires explicit owner approval.
- **Evidence:** `docs/evidence/<AUDITED_SHA>/phase0/repository-hygiene.md`

---

## Phase 1 — Lock the baseline and authentication

**Goal:** one documented command produces a byte-identical PAPER environment on any engineer's machine, and identity is unambiguous.
**Owner:** E1 (auth UX) + E4 (diagnostics/guards). **Exit gate:** `docs/evidence/<AUDITED_SHA>/phase1/`.

### P1-T1 — Single documented startup command
- **Owner:** E4 · **Depends:** — · **Size:** S
- **Files:** `docker-compose.yml`, `README.md`, `docs/DEPLOYMENT.md`, `backend/.env.example` (create if absent)
- **Change:** Declare exactly one canonical command and delete every competing variant from docs:
  ```
  PAPER_ENV_FILE=./backend/.env.docker docker compose up --build
  ```
  Ship `backend/.env.docker.example` listing every required key with empty values and a one-line comment each: `AUTOPILOT_MODE`, `IS_PAPER`, `SIM_MODE`, `MOCK_MODE`, `IBKR_HOST`, `IBKR_PORT`, `JWT_BOOTSTRAP_SECRET`, `JWT_SECRET`, `DB_PATH`, `BOOTSTRAP_ALLOW_REMOTE`, `VAPID_*`, `LOG_LEVEL`, `WORKERS`. Add `.env.docker` to `.gitignore`.
- **Contract:** `PAPER_ENV_FILE` is the only supported way to point at an env file (already the compose default at `docker-compose.yml` `env_file`).
- **Tests:** `backend/tests/test_env_contract.py::test_example_env_lists_every_required_key` — parse `.env.docker.example` and assert it is a superset of the keys `backend/config.py` reads.
- **Accept:** `grep -rn "docker compose up" README.md docs/ | grep -v "PAPER_ENV_FILE"` returns nothing; `git check-ignore backend/.env.docker` exits 0.
- **Evidence:** `phase1/startup-command.md`

### P1-T2 — Reconcile the DB-path guard with the Docker path (blocking bug)
- **Owner:** E4 · **Depends:** — · **Size:** S
- **Files:** `scripts/check_db_path.sh`, `docker-compose.yml`, `backend/config.py`, `backend/Dockerfile`, `.github/workflows/ci.yml`, `backend/tests/test_database_path.py`
- **Change:** First add a regression test that fails on `v2-baseline`, proving the current false-green: the guard ignores `DB_PATH` and parses `--docker` as its positional legacy filename. Replace the positional API with strict `--host` and `--docker` modes. Resolve the canonical path from `DB_PATH`, falling back only to the context-specific default; require `/data/trading_bot.db` in Docker; reject unknown arguments and legacy root-level databases. Make CI invoke the intended context explicitly, and copy/invoke the guard in the backend image if in-container enforcement remains an acceptance requirement.
- **Contract:** exactly one DB path per execution context, resolved from `DB_PATH`; no module may build a DB path by string concatenation outside `backend/config.py`.
- **Tests:** extend `backend/tests/test_database_path.py`: `test_docker_path_is_data_volume`, `test_legacy_root_db_rejected`, `test_no_module_hardcodes_db_filename` (repo grep for `trading_bot.db` outside config/tests/compose).
- **Accept:** the regression test fails on `v2-baseline` and passes on the ticket commit; `scripts/check_db_path.sh --docker` validates `/data/trading_bot.db` inside the container; `--host` validates the backend development path; mismatched `DB_PATH`, unknown arguments, and a planted legacy file all return non-zero; CI runs the explicit intended mode.
- **Evidence:** `phase1/db-path-guard.md`

### P1-T3 — Startup diagnostics endpoint
- **Owner:** E4 · **Depends:** P1-T2 · **Size:** M
- **Files:** `backend/health.py` (has `GET /api/health`, `/ready`, `/detailed:144`, `/bot`), `backend/routers/health_extended.py`, `backend/config.py`, `backend/Dockerfile`, `docker-compose.yml`
- **Change:** Add `GET /api/health/startup` (unauthenticated, safe-by-construction) returning **only** non-secret facts:
  ```json
  {
    "commit_sha": "4d8db93b60e4ec55d45e840babf03a7f3ab749cf",
    "build_time": "2026-08-19T12:38:41Z",
    "execution_mode": "PAPER",
    "sim_mode": false,
    "mock_mode": false,
    "db_path": "/data/trading_bot.db",
    "db_writable": true,
    "broker": {"configured_host": "host.docker.internal", "port": 7497, "state": "connected|disconnected|degraded"},
    "auth_mode": "bootstrap_demo",
    "live_allowed": false
  }
  ```
  Inject the SHA at build time: `ARG GIT_SHA` in both Dockerfiles → `ENV GIT_SHA` → `docker-compose.yml` `args: GIT_SHA: "${GIT_SHA:-unknown}"`. Log the same payload once at startup through `backend/log_config.py`.
- **Contract:** this endpoint is the single source of truth for "which build am I looking at". It must never include secrets, tokens, account numbers, or full env dumps. `/detailed` stays authenticated.
- **Tests:** `backend/tests/test_health_extended.py::test_startup_payload_shape`, `::test_startup_never_leaks_secret_keys` (assert no key matching `SECRET|TOKEN|PASSWORD|KEY|ACCOUNT` appears in the serialized body), `::test_startup_reports_paper_when_is_paper_true`.
- **Accept:** `curl -s localhost:3000/api/health/startup | jq -e '.commit_sha|length==40'` exits 0; secret-leak test green.
- **Evidence:** `phase1/startup-diagnostics.json` (captured live response)

### P1-T4 — Surface build identity in the dashboard
- **Owner:** E1 · **Depends:** P1-T3 · **Size:** S
- **Files:** `dashboard/src/App.tsx`, new `dashboard/src/components/ui/BuildBanner.tsx`, `dashboard/src/hooks/useDiagnostics.ts`
- **Change:** Persistent footer/status chip: `PAPER · SHA 4d8db93 · DB /data/trading_bot.db · TWS connected`. Colour-code broker state (connected/degraded/disconnected). Non-PAPER modes render a red banner. Poll `/api/health/startup` once at bootstrap plus on reconnect.
- **Contract:** TS type `StartupDiagnostics` mirrors P1-T3's JSON exactly, declared once in `dashboard/src/types/`.
- **Tests:** `dashboard/src/components/ui/__tests__/BuildBanner.test.tsx` — renders SHA short form; shows red banner when `execution_mode !== 'PAPER'`; shows degraded state when broker disconnected.
- **Accept:** vitest green; banner visible on every route in a Docker run screenshot.
- **Evidence:** `phase1/build-banner.png`

### P1-T5 — Replace the misleading credential form
- **Owner:** E1 · **Depends:** — · **Size:** M
- **Files:** `dashboard/src/components/auth/LoginPage.tsx` (220 lines; username state `:17`, password `:18`, `POST /api/auth/token` `:34`, "Invalid username or password" `:44`, placeholder `demo` `:128`), `dashboard/src/components/auth/AuthGuard.tsx`, `backend/routers/auth.py` (`GET /me:28`, `POST /token:33`), `backend/auth.py` (`DEMO_USER_ID = "demo":29`, random demo password at startup `:31-33`)
- **Change:** The form implies arbitrary credentials are validated; the backend actually mints a demo token gated on `JWT_BOOTSTRAP_SECRET` + loopback (or `BOOTSTRAP_ALLOW_REMOTE=1`, which compose currently sets to `1` — see `docker-compose.yml`). Remove the username/password inputs from the local build entirely. Replace with a single explicit panel:
  - Title: "Local development build".
  - Body: "Authentication is demo-identity only. Remote login is disabled in this build."
  - One primary action: "Continue as demo".
  - If the bootstrap secret is missing or the mint fails, show the real backend reason (401 invalid bootstrap secret / 403 non-loopback origin) rather than "Invalid username or password".
  Gate the real login form behind `VITE_AUTH_MODE=login`, defaulting to `bootstrap` — do not implement a half-real password flow now.
- **Contract:** `VITE_AUTH_MODE ∈ {bootstrap, login}`; `bootstrap` renders no credential inputs. `/api/auth/token` error semantics preserved: 401 bad/missing secret, 403 non-loopback when remote disallowed.
- **Tests:** `dashboard/src/components/auth/__tests__/LoginPage.test.tsx` — `test_no_password_input_in_bootstrap_mode` (query by role/label returns null), `test_shows_backend_reason_on_401`, `test_shows_origin_error_on_403`. Backend: extend `backend/tests/test_auth.py` / `test_auth_gaps.py` with `test_token_requires_bootstrap_secret`, `test_token_rejects_remote_when_flag_unset`.
- **Accept:** no element with `autoComplete="current-password"` exists in the bootstrap build (`grep` + test); unauthorized `/api/auth/me` still returns 401.
- **Evidence:** `phase1/auth-ux.md` + before/after screenshots

### P1-T6 — Decide and document `BOOTSTRAP_ALLOW_REMOTE=1`
- **Owner:** E4 · **Depends:** P1-T5 · **Size:** S
- **Files:** `docker-compose.yml`, `backend/routers/auth.py:66`, `docs/adr/0009-local-bootstrap-identity.md` (new), `docs/security/`
- **Change:** Compose defaults `BOOTSTRAP_ALLOW_REMOTE=1` so nginx (a non-loopback Docker-bridge origin) can mint tokens. That is a deliberate weakening of the loopback fence and must be an ADR, not a comment. ADR must state: why it's needed, that it is valid only while the stack is single-user/local, and that remote deployment requires `BOOTSTRAP_ALLOW_REMOTE=0` plus a real login flow. Add a release-gate check that fails if `BOOTSTRAP_ALLOW_REMOTE=1` and `AUTOPILOT_MODE!=PAPER`.
- **Contract:** `BOOTSTRAP_ALLOW_REMOTE=1` is legal only when `IS_PAPER=true` and `live_allowed=false`.
- **Tests:** `backend/tests/test_lifespan_safety.py::test_remote_bootstrap_forbidden_outside_paper` — startup raises when both flags conflict.
- **Accept:** app refuses to boot with `BOOTSTRAP_ALLOW_REMOTE=1` + `IS_PAPER=false`; ADR 0009 merged.
- **Evidence:** `docs/adr/0009-local-bootstrap-identity.md`

### P1-T7 — Phase 1 smoke script
- **Owner:** E4 · **Depends:** P1-T3, P1-T5 · **Size:** M
- **Files:** new `scripts/smoke_phase1.sh`, `scripts/run_quality_gates.sh`
- **Change:** Script asserts, against a running stack: dashboard root returns 200 and serves the SPA; `/api/health` 200; `/api/health/startup` reports `PAPER` + 40-char SHA + expected `db_path`; bootstrap mint succeeds; `/api/auth/me` returns `demo`; an unauthenticated call to a protected route (`/api/positions`) returns 401; `/api/auth/token` with a wrong secret returns 401. Print a pass/fail table; non-zero exit on any failure. Never echo secret values.
- **Contract:** `scripts/smoke_phase1.sh [BASE_URL]`, default `http://localhost:3000`; exit 0 = Phase 1 green.
- **Tests:** shellcheck clean; a CI job runs it against `docker compose up -d`.
- **Accept:** script green on a clean clone using only the P1-T1 command.
- **Evidence:** `phase1/smoke-output.txt`

### P1-T8 — Secret-leak scan in CI
- **Owner:** E4 · **Depends:** — · **Size:** S
- **Files:** `.github/workflows/` (add job), `scripts/scan_secrets.sh` (new)
- **Change:** Fail CI when known key patterns (`JWT_SECRET=`, `JWT_BOOTSTRAP_SECRET=`, `VAPID_PRIVATE`, `IBKR_ACCOUNT`, PEM headers) appear in tracked files, or when captured container logs contain a live secret value. Wire into `run_quality_gates.sh` as gate 7.
- **Accept:** planting a fake secret in a tracked file turns CI red; removing it turns it green.
- **Evidence:** `phase1/secret-scan.txt`

**Phase 1 acceptance (rollup):** P1-T1…T8 merged · `scripts/smoke_phase1.sh` green · `scripts/check_db_path.sh --docker` green · `git status --porcelain` empty at the release SHA (note: build artifacts are produced inside Docker and never committed) · both container healthchecks green.

---

## Phase 2 — TradingView chart integration

**Goal:** one trustworthy market-truth surface with no localhost dependency and no ambiguity about data source or latency.
**Owner:** E1. **Exit gate:** `docs/evidence/<SHA>/phase2/`.
**Re-scoped:** the sidecar is already behind `dashboard/src/utils/tradingView.ts` + `VITE_IB_CHART_BASE`, so this phase is mostly deletion, normalization, and state hygiene.

### P2-T1 — ADR: chart integration decision
- **Owner:** E1 · **Depends:** — · **Size:** M
- **Files:** `docs/adr/0010-chart-integration.md` (new), `docs/TRADINGVIEW_CHARTS.md`
- **Change:** Decide and record: hosted TradingView widget (current: `VITE_TRADINGVIEW_WIDGET_URL`, default `https://www.tradingview.com/widgetembed/`) vs licensed Advanced Charts vs backend-fed charts. Cover licensing/ToS for embedding in a product, whether the user's personal TradingView Pro entitlement transfers (it does not, for a hosted product), which party owns real-time entitlement, and the fallback when entitlement is absent. Explicitly record that IBKR market-data entitlement and TradingView entitlement are separate.
- **Accept:** ADR merged with a chosen option and rejected alternatives; `docs/TRADINGVIEW_CHARTS.md` updated to match.
- **Evidence:** `docs/adr/0010-chart-integration.md`

### P2-T2 — Delete the ib_chart sidecar path
- **Owner:** E1 · **Depends:** P2-T1 · **Size:** M
- **Files:** `dashboard/src/utils/tradingView.ts:6` (`DEFAULT_IB_CHART_BASE = 'http://127.0.0.1:5001'`), `buildIbMultiChartUrl`, `dashboard/src/utils/__tests__/tradingView.test.ts:21`, `dashboard/src/pages/ChartsPage.tsx` (multi-mode toggle `:11-33`), `docs/TRADINGVIEW_CHARTS.md:3`, `docs/SECURE_CONSOLIDATION_PLAN.md:42`
- **Change:** Remove `buildIbMultiChartUrl`, `VITE_IB_CHART_BASE`, the `ChartMode='multi'` branch, the multi-symbol input, and the sidecar test assertion. If multi-symbol layout is a product requirement, reimplement as N hosted TradingView embeds in a grid — never as a localhost iframe. Update both docs.
- **Contract:** no browser-originating request may target a non-routable host. Allowed chart origins: the TradingView widget origin and the app's own origin.
- **Tests:** `dashboard/src/utils/__tests__/tradingView.test.ts` — `test_no_loopback_origin_in_any_built_url`; repo guard test `test_no_localhost_urls_in_dashboard_src` (grep `127.0.0.1|localhost` under `dashboard/src`, allowlisting test files and the dev proxy config).
- **Accept:** `grep -rn "5001" dashboard/ docs/` returns nothing outside changelogs; DevTools network log from a Docker run shows zero `127.0.0.1` chart requests.
- **Evidence:** `phase2/sidecar-removal.md` + network-log capture

### P2-T3 — Canonical timeframe map
- **Owner:** E1 · **Depends:** — · **Size:** M
- **Files:** new `dashboard/src/utils/timeframes.ts`, `dashboard/src/pages/ChartsPage.tsx:12` (`type Timeframe = 'D'|'W'|'M'|'5'|'1'`) and the inline ternary chain at `:25`, `dashboard/src/pages/MarketPage.tsx:245,281`, `dashboard/src/hooks/useMarketData.ts:73,140`, `dashboard/src/store/screenerStore.ts` (`interval`, `period`)
- **Change:** `ChartsPage` currently converts timeframes with a nested ternary (`'1'→'1m'`, `'5'→'5m'`, `'D'→'1d'`, `'W'→'1wk'`, `'M'→'1mo'`) while screener/market code carries its own interval strings. Define one canonical union and one map per consumer:
  ```ts
  export type Timeframe = '1m'|'5m'|'15m'|'1h'|'1d'|'1wk'|'1mo'
  export const TF_LABEL: Record<Timeframe,string>
  export const TF_TRADINGVIEW: Record<Timeframe,string>   // 1,5,15,60,D,W,M
  export const TF_BACKEND: Record<Timeframe,string>        // yfinance/IBKR interval
  export const DEFAULT_PERIOD: Record<Timeframe,string>
  export function parseTimeframe(raw: string): Timeframe | null
  ```
  Delete every other timeframe literal. Canonical form is what appears in the URL.
- **Contract:** `Timeframe` is the only timeframe type crossing a module boundary; backend interval strings are produced solely by `TF_BACKEND`.
- **Tests:** `dashboard/src/utils/__tests__/timeframes.test.ts` — every `Timeframe` maps to a distinct TradingView value and a distinct backend value; `parseTimeframe` rejects garbage; `test_no_stray_timeframe_literals` greps `'1wk'|'1mo'|'1d'` outside `timeframes.ts`.
- **Accept:** distinctness + grep tests green; typecheck green.
- **Evidence:** `phase2/timeframe-map.md` (table of canonical → TV → backend)

### P2-T4 — URL as chart state
- **Owner:** E1 · **Depends:** P2-T3 · **Size:** M
- **Files:** `dashboard/src/pages/ChartsPage.tsx`, `dashboard/src/App.tsx` (routing), `dashboard/src/store/marketStore.ts`
- **Change:** Chart state currently lives in `useState` + `useMarketStore`, so a refresh loses it and a bug report can't be reproduced from a link. Move to query params: `/charts?symbol=AAPL&tf=1d&from=2026-01-01&to=2026-08-19`. URL is the source of truth; store mirrors it. Invalid params fall back to defaults with a visible non-blocking notice. Symbol changes elsewhere in the app push a new history entry.
- **Contract:** `?symbol` (normalized, see P2-T5) · `?tf` (canonical `Timeframe`) · `?from`/`?to` (ISO dates, optional).
- **Tests:** `dashboard/src/pages/__tests__/ChartsPage.test.tsx` — deep link renders the requested symbol/timeframe; changing timeframe updates the URL; refresh preserves state; `?tf=banana` falls back to default and shows the notice.
- **Accept:** vitest green; a pasted deep link reproduces the exact chart in a fresh browser profile.
- **Evidence:** `phase2/url-state.md`

### P2-T5 — Symbol normalization layer
- **Owner:** E1 (with E3 review — shared with screener) · **Depends:** — · **Size:** L
- **Files:** new `dashboard/src/utils/symbols.ts`, plus a backend twin at `backend/symbols.py` if the backend also normalizes; consumers: `ChartsPage.tsx`, `MarketPage.tsx`, `MarketsPage.tsx`, `dashboard/src/store/screenerStore.ts`, `backend/screener.py`, `backend/ibkr_client.py`
- **Change:** One canonical instrument identity used by charts, screener, alerts and orders:
  ```ts
  interface Instrument { ticker: string; exchange: string; assetType: 'STK'|'ETF'|'FUT'|'OPT'|'CRYPTO'|'FX'; currency: string }
  toTradingViewSymbol(i: Instrument): string   // "NASDAQ:AAPL"
  toIbkrContract(i: Instrument): IbkrContract
  parseUserSymbol(raw: string): Instrument | null   // "aapl", "NASDAQ:AAPL", "BRK.B"
  ```
  Handle: case, whitespace, class shares (`BRK.B` vs `BRK-B`), exchange prefixes, unknown exchange → explicit `unknown` rather than a guess.
- **Contract:** `Instrument` is the only symbol representation crossing module boundaries. No raw user string reaches TradingView or IBKR.
- **Tests:** `dashboard/src/utils/__tests__/symbols.test.ts` — table-driven: `AAPL`, `aapl`, `NASDAQ:AAPL`, `BRK.B`, `SPY`, `BTC-USD`, `""`, `"; DROP"`, 40-char junk. Backend mirror in `backend/tests/test_symbols.py` asserting both sides agree on the same table (share the fixture as JSON so they cannot drift).
- **Accept:** shared fixture passes on both sides; no consumer calls a chart or broker API with an unnormalized string (grep test).
- **Evidence:** `phase2/symbol-normalization.md`

### P2-T6 — Explicit chart data states
- **Owner:** E1 · **Depends:** P2-T2, P2-T4 · **Size:** M
- **Files:** `dashboard/src/pages/ChartsPage.tsx`, `dashboard/src/components/ui/ErrorBoundary.tsx`, `dashboard/src/hooks/useChart.ts`
- **Change:** Render one of five explicit states, each with a visible badge naming the data source and latency: `loading` (skeleton, no layout shift) · `realtime` ("Real-time · TradingView") · `delayed` ("Delayed 15m · TradingView") · `unentitled` (chart still renders last close; badge explains the missing entitlement) · `unavailable` (actionable message + link to screener; never a blank iframe). Invalid symbol and market-closed are sub-states with their own copy.
- **Contract:** `type ChartState = 'loading'|'realtime'|'delayed'|'unentitled'|'unavailable'`; every state must render a source badge.
- **Tests:** `dashboard/src/pages/__tests__/ChartsPage.test.tsx` — one case per state asserting the badge text; `test_unavailable_never_renders_empty_iframe`.
- **Accept:** all five states reachable via a test/dev override query param and screenshotted.
- **Evidence:** `phase2/chart-states/*.png` (5 screenshots)

### P2-T7 — Manual browser validation matrix
- **Owner:** E1 · **Depends:** P2-T2…T6 · **Size:** M
- **Files:** `docs/evidence/browser-validation-checklist-<SHA>.md` (follow the existing `browser-validation-checklist-4d8bbda.md` format)
- **Change:** Execute and record, against Docker: AAPL / NVDA / SPY · invalid symbol · market closed · delayed-feed simulation · refresh with deep link · back/forward navigation · mobile width (375px) · slow-3G throttle. Each row: input, expected, actual, pass/fail, screenshot, SHA.
- **Accept:** every row pass or an open ticket linked; artifact committed.
- **Evidence:** `phase2/browser-validation.md`

**Phase 2 acceptance (rollup):** no loopback chart requests · all timeframes distinct and documented · source + latency always visible · chart usable without real-time entitlement · typecheck/build/vitest green · ADR 0010 merged.

---

## Phase 3 — Browser notifications (verify-then-close-gaps)

**Goal:** a notification the user receives with the browser closed, or an honest "unsupported".
**Owner:** E2. **Exit gate:** `docs/evidence/<SHA>/phase3/`.
**Re-scoped:** the backend largely exists — `backend/routers/push_routes.py` (`/status`, `/subscribe`, `/subscription/status`, `DELETE /subscribe`, `/preferences` GET+PUT, `/test`), `backend/push_service.py` (VAPID via `Vapid`, `allowed_push_hosts()`, `is_allowed_push_endpoint()`, `get_push_readiness()`, `deliver_push(user_id=...)`, `deliver_alert_push(user_id=...)`, `_record_failure`), and `backend/tests/test_push_notifications.py`. Phase 3 audits it against the promise and closes what's missing.

### P3-T1 — Push implementation audit against the promise
- **Owner:** E2 · **Depends:** — · **Size:** M
- **Files:** `backend/routers/push_routes.py`, `backend/push_service.py`, `backend/notification_service.py`, `backend/alert_engine.py`, `backend/alerts.py`, `backend/db/` (subscription schema), `dashboard/src/hooks/useNotifications.ts`, `dashboard/public/` service worker
- **Change:** Produce a gap table — for each promise, the code path that satisfies it or the gap: durable persistence and columns present (`endpoint`, `keys`, `user_id`, device/browser id, `created_at`, `updated_at`, failure count, `last_error`) · every subscription bound to the authenticated `user_id` · endpoint origin validated (`is_allowed_push_endpoint`) · preferences enforced server-side (categories, quiet hours, severity, symbol filters) · duplicate endpoint ownership conflict → 409 · expired subscription (410/404 from the push service) pruned · delivery failures recorded and visible. **Output the table before writing code** — it determines T2…T6 scope.
- **Accept:** `phase3/push-audit.md` committed, every row citing `file:symbol` or "GAP".
- **Evidence:** `phase3/push-audit.md`

### P3-T2 — Subscription persistence and ownership
- **Owner:** E2 · **Depends:** P3-T1 · **Size:** M
- **Files:** `backend/db/` migration, `backend/push_service.py`, `backend/routers/push_routes.py:139,175,186`
- **Change:** Close any T2 gaps: unique index on `endpoint`; `user_id` NOT NULL FK; store device/browser label and timestamps; `POST /subscribe` upserts for the same owner (200) and returns **409** when the endpoint belongs to another user; `DELETE /subscribe` only ever deletes rows owned by the caller; logout wipes local subscription state and calls unsubscribe.
- **Contract:** `POST /api/push/subscribe` → 201 new · 200 updated · 400/422 malformed · 401 unauthenticated · 403 disallowed endpoint origin · 409 ownership conflict · 503 push not configured (no VAPID).
- **Tests:** `backend/tests/test_push_notifications.py` — `test_subscribe_creates_201`, `test_resubscribe_same_user_200`, `test_other_user_endpoint_409`, `test_delete_only_own_subscription`, `test_disallowed_origin_403`, `test_missing_vapid_503`, `test_expired_subscription_pruned`.
- **Accept:** all named cases green; migration applies to and rolls back cleanly on a copy of the PAPER DB.
- **Evidence:** `phase3/subscription-lifecycle.md`

### P3-T3 — Server-side preference enforcement as middleware
- **Owner:** E2 · **Depends:** P3-T2 · **Size:** L
- **Files:** `backend/push_service.py:233` (`deliver_push`), `:273` (`deliver_alert_push`), `backend/alert_engine.py`, `backend/notification_service.py`
- **Change:** Preferences must be enforced in exactly one chokepoint that every emitter must pass through — not per-call-site, or the next alert type silently bypasses it. Implement `should_deliver(user_id, alert) -> Decision{deliver: bool, reason: str}` covering category, severity floor, quiet hours (user timezone, DST-aware), symbol allow/deny list, and per-alert-type rate cap. Every suppression is logged with its reason. Add a guard test that no module calls the low-level sender directly.
- **Contract:** `Decision.reason ∈ {delivered, category_off, below_severity, quiet_hours, symbol_filtered, rate_capped, no_subscription, push_unconfigured}`; the reason is persisted with the delivery attempt.
- **Tests:** `backend/tests/test_push_notifications.py` — `test_quiet_hours_suppresses_across_midnight`, `test_quiet_hours_respects_user_tz`, `test_two_users_different_preferences_diverge`, `test_symbol_filter_blocks`, `test_severity_floor`, `test_no_direct_sender_calls_outside_chokepoint`.
- **Accept:** frontend preference tampering cannot cause delivery (test posts a delivery request with permissive client state and asserts suppression).
- **Evidence:** `phase3/preference-enforcement.md`

### P3-T4 — Service worker background delivery
- **Owner:** E2 (with E1 for UI wiring) · **Depends:** P3-T2 · **Size:** M
- **Files:** service worker in `dashboard/public/`, `dashboard/src/hooks/useNotifications.ts` (`createSubscription:75`, `subscribePush`, `verifiedUnsubscribePush`), `dashboard/src/hooks/useWebSocket.ts:251-261`
- **Change:** Split the two delivery channels cleanly: in-tab WebSocket toasts (`useWebSocket.ts:251-261`) vs background Web Push via the service worker. Both must respect server-decided suppression — the tab must not re-notify what the server suppressed. In the SW: `tag` notifications by `alert_id`/symbol so repeats replace rather than stack; `renotify` only on severity escalation; click routes to the right deep link (`/alerts?id=…`, `/charts?symbol=…&tf=…` using the P2-T3/T4 contract).
- **Contract:** push payload `{alert_id, category, severity, symbol, title, body, url, ts}`; `tag = alert_id`.
- **Tests:** `dashboard/src/hooks/__tests__/useNotifications.test.ts` — permission denied path; subscribe/unsubscribe round trip; duplicate payload replaces rather than duplicates; click handler resolves the correct route.
- **Accept:** manual drill (P3-T6) shows a notification with all tabs closed.
- **Evidence:** `phase3/service-worker.md`

### P3-T5 — Notification health surface
- **Owner:** E2 · **Depends:** P3-T2, P3-T3 · **Size:** S
- **Files:** `backend/routers/push_routes.py:126` (`GET /status`), `dashboard/src/pages/AlertsPage.tsx`, `dashboard/src/pages/SettingsPage.tsx`
- **Change:** Extend `/api/push/status` with `{configured, subscriptions_count, last_delivery_at, last_error, last_error_at, suppressed_last_24h_by_reason}`. Surface in Settings: subscription state, last delivery, last error, and a "Send test notification" button hitting `POST /api/push/test`. Delete every silent best-effort success path — if delivery is impossible, the UI must say so.
- **Contract:** `/api/push/status` never returns 200 with a truthy "enabled" while VAPID is unconfigured.
- **Tests:** `backend/tests/test_push_notifications.py::test_status_reports_unconfigured_when_no_vapid`; `dashboard/.../SettingsPage.test.tsx::shows_unsupported_when_unconfigured`.
- **Accept:** with VAPID keys removed, the UI shows "Notifications unavailable" and no toggle claims enabled.
- **Evidence:** `phase3/notification-health.png`

### P3-T6 — Notification delivery drill
- **Owner:** E2 · **Depends:** P3-T2…T5 · **Size:** M
- **Files:** `docs/evidence/notification-drill-<SHA>.md` (follow existing `notification-drill-c850786.md`)
- **Change:** Run and record: subscribe → close all tabs → trigger a PAPER alert → receive notification · permission denied · unsubscribe then trigger (no delivery) · expired subscription (delete on the push service side) · two users with different preferences (only the entitled one receives) · duplicate endpoint claimed by a second user (409) · delivery failure while the push service is unreachable (recorded, retryable, visible). Each row: timestamp, SHA, input, expected, actual, pass/fail.
- **Accept:** every row pass; cross-user delivery attempt count is zero.
- **Evidence:** `phase3/notification-drill.md`

**Phase 3 acceptance (rollup):** background delivery proven with browser closed · no cross-user delivery · preferences enforced server-side with logged reasons · failures visible and retryable · `test_push_notifications.py` and full backend suite green.

---

## Phase 4 — Screener redesign and performance

**Goal:** a research workflow with a stated contract, bounded cost, and explainable ranking.
**Owner:** E3. **Exit gate:** `docs/evidence/<SHA>/phase4/`.

### P4-T1 — Freeze the screener contract
- **Owner:** E3 · **Depends:** — · **Size:** M
- **Files:** `backend/routers/screener_routes.py:33` (`POST /scan`), `:45` (`/universes`), `:50/:56/:66` (presets), `:73` (`/enrich`), `:200/:210/:220/:231` (pipeline status/snapshot/scan-now/quarantine), `backend/models.py:33` (`_VALID_UNIVERSES`), `backend/api_contracts.py`, `dashboard/src/services/api/screener.ts`
- **Change:** `ScanRequest`/`ScanResponse` are currently implied by call sites (`screenerStore.runScan` sends `universe, symbols, filters, interval, period, limit: 100`). Freeze them explicitly:
  ```
  ScanRequest  { universe, symbols?, filters[], interval, period, limit, offset, sort{field,dir}, scan_id? }
  ScanResponse { scan_id, state, results[], total_symbols, returned, truncated,
                 skipped_symbols[], elapsed_ms, scanned_at, data_freshness{source, as_of, delayed_seconds},
                 partial_reasons[] }
  ScanResult   { symbol, instrument, score, factors[{name,value,weight,contribution}],
                 missing_fields[], enriched?, as_of }
  ```
  Register in `backend/api_contracts.py` so `backend/tests/test_api_contracts.py` locks the shape. Mirror the TS types in `dashboard/src/services/api/screener.ts` from a single generated source if feasible.
- **Contract:** additive changes only after freeze; removals/renames need an ADR.
- **Tests:** `backend/tests/test_api_contracts.py::test_scan_response_contract`; `test_scan_request_rejects_unknown_universe`; TS typecheck.
- **Accept:** contract tests green; no call site constructs a scan body inline outside the API module.
- **Evidence:** `phase4/screener-contract.md`

### P4-T2 — Scan state machine, cancellation, stale-response protection
- **Owner:** E3 · **Depends:** P4-T1 · **Size:** L
- **Files:** `dashboard/src/store/screenerStore.ts:162` (`runScan`), `:199` (`enrichResults`), `dashboard/src/pages/ScreenerPage.tsx`, `dashboard/src/pages/MarketsPage.tsx`, `dashboard/src/components/screener/`
- **Change:** Today `runScan` returns early while `scanning` is true (a re-entry guard, not cancellation), sets `enriched: {}` on every scan — so enrichment visibly disappears mid-workflow — and swallows enrichment errors silently (`catch {}` at `:210`). Implement:
  - `scanState: 'idle'|'running'|'succeeded'|'partial'|'failed'` replacing the boolean.
  - Previous `results` and `enriched` stay rendered (dimmed, with "results from HH:MM:SS") while a new scan runs; replaced atomically on success.
  - An `AbortController` per scan plus a monotonic `requestSeq`; responses with a stale seq are dropped, never merged.
  - Explicit "Cancel scan" control that aborts the request and returns to the prior successful snapshot.
  - Enrichment failure sets `partial` with a visible reason — never a silent empty catch.
- **Contract:** `scanState` is the single UI source of truth; every non-`idle` state renders a scan timestamp.
- **Tests:** `dashboard/src/store/__tests__/screenerStore.test.ts` — `test_previous_results_survive_new_scan`, `test_stale_response_ignored`, `test_cancel_restores_previous_snapshot`, `test_enrich_failure_sets_partial_with_reason`, `test_enriched_not_cleared_until_success`.
- **Accept:** all named cases green; double-clicking Scan cannot render older results.
- **Evidence:** `phase4/scan-state-machine.md`

### P4-T3 — Server-side scan jobs
- **Owner:** E3 · **Depends:** P4-T1 · **Size:** L
- **Files:** `backend/screener_pipeline.py:122` (`run_scan_now`), `:129` (`_scan_loop`), `:139` (`_run_single_scan`), `:200` (`_run_ibkr_scan`), `:239` (`_run_yfinance_fallback`), `backend/routers/screener_routes.py`
- **Change:** Persist each scan as a job row: `scan_id`, request contract JSON, state, `started_at`, `finished_at`, `symbol_count`, `elapsed_ms`, source (`ibkr`/`yfinance_fallback`), `partial_reasons`, result fingerprint. Add `GET /api/screener/scan/{scan_id}` and `POST /api/screener/scan/{scan_id}/cancel`. Enables replay, latency benchmarking per universe/filter combination, and feeding Phase 5 evidence.
- **Contract:** `scan_id` is a UUID returned synchronously; results are always retrievable by `scan_id` for a retention window.
- **Tests:** `backend/tests/test_screener*.py` — job persisted with the exact request; cancel marks `cancelled` and stops enrichment; replay by `scan_id` returns identical results for a fixed fixture.
- **Accept:** every scan in a PAPER session appears as a job row with an elapsed time.
- **Evidence:** `phase4/scan-jobs.md`

### P4-T4 — Bounded-concurrency enrichment + cache
- **Owner:** E3 · **Depends:** P4-T3 · **Size:** L
- **Files:** `backend/screener_pipeline.py:297` (`_enrich_candidates`, currently `symbols = [c.symbol for c in candidates[:100]]` then one `enrich_symbols(symbols)` call inside a broad `try/except` that only warns), `backend/screener.py` (`enrich_symbols`), `backend/ibkr_client.py`, `backend/market_data.py`
- **Change:** Replace the single opaque bulk call with a bounded worker pool (`asyncio.Semaphore`, default 8, env-tunable), per-symbol timeout, per-symbol error capture into `missing_fields`, and a TTL cache keyed `(symbol, field_set)` — sector/market-cap TTL hours, price TTL seconds. Respect IBKR and yfinance rate limits with explicit backoff; expose throttle/backoff counters via `/api/screener/pipeline/status`. One slow symbol must not stall the scan, and the broad `except` must no longer be able to erase all enrichment silently.
- **Contract:** `SCREENER_ENRICH_CONCURRENCY` (default 8), `SCREENER_ENRICH_TIMEOUT_S` (default 5), `SCREENER_CACHE_TTL_*`; partial enrichment is a first-class success with populated `missing_fields`.
- **Tests:** `backend/tests/test_screener_enrichment.py` — concurrency never exceeds the semaphore (instrumented counter); one hanging symbol yields `missing_fields` for that symbol only; warm cache issues zero upstream calls; rate-limit error triggers backoff and is reported.
- **Accept:** benchmark (P4-T7) shows warm-cache P95 materially below cold; concurrency cap test green.
- **Evidence:** `phase4/enrichment-concurrency.md`

### P4-T5 — Bound and paginate `us_all`
- **Owner:** E3 · **Depends:** P4-T1, P4-T3 · **Size:** M
- **Files:** `backend/models.py:33`, `backend/screener.py`, `backend/routers/screener_routes.py:33,45`, `dashboard/src/components/screener/UniverseSelector.tsx:10`, `dashboard/src/store/screenerStore.ts` (`limit: 100` at `runScan`)
- **Change:** `us_all` is already valid and selectable, so the risk is unbounded cost, not exposure. Enforce a server-side hard cap (`SCREENER_MAX_SYMBOLS_PER_SCAN`, default 500) applied **after** filtering and **before** enrichment; return `truncated: true` with `total_symbols` so the UI can state "Showing 500 of 4,213 matches". Implement deterministic `offset` pagination with a stable sort so page 2 cannot repeat or skip rows. Show the universe symbol count in the selector via `GET /api/screener/universes`.
- **Contract:** server truncation is always reported; a client `limit` above the cap is clamped, never honoured silently.
- **Tests:** `test_us_all_truncates_and_reports`, `test_pagination_stable_across_pages`, `test_client_limit_clamped_to_cap`.
- **Accept:** a full `us_all` scan completes within the recorded benchmark budget and never returns more than the cap.
- **Evidence:** `phase4/us-all-bounds.md`

### P4-T6 — Ranking explainability
- **Owner:** E3 · **Depends:** P4-T1 · **Size:** M
- **Files:** `backend/screener.py`, `backend/indicators.py`, `backend/custom_indicators.py`, `dashboard/src/components/screener/`, `dashboard/src/pages/ScreenerPage.tsx`
- **Change:** Every row exposes `score` plus a `factors[]` breakdown (`name`, `value`, `weight`, `contribution`), `missing_fields[]`, and `as_of`. UI: score cell with a hover/drawer breakdown ("RSI 0.31 · MA slope 0.24 · rel-volume 0.18"), an explicit badge when a factor is missing (score computed on partial data), and a freshness indicator. No score may be displayed without its factor breakdown.
- **Contract:** `sum(contribution) ≈ score` within float tolerance; missing factors contribute 0 and are listed in `missing_fields`.
- **Tests:** `backend/tests/test_screener_scoring.py::test_contributions_sum_to_score`, `::test_missing_factor_recorded_not_imputed`; `dashboard/.../__tests__` renders the breakdown and the partial-data badge.
- **Accept:** for any row, a user can state why it ranked where it did, from the UI alone.
- **Evidence:** `phase4/ranking-explainability.md`

### P4-T7 — Screener UX restructure
- **Owner:** E3 (design review with E1) · **Depends:** P4-T2, P4-T6 · **Size:** L
- **Files:** `dashboard/src/pages/ScreenerPage.tsx`, `dashboard/src/pages/MarketsPage.tsx`, `dashboard/src/components/screener/*`
- **Change:** Restructure into a workflow, not a raw table: left filter panel (collapsible, grouped, active-filter chips with one-click removal) · sticky scan bar (universe, interval, period, Scan, Cancel, last-scan timestamp, state pill) · result summary line (`N matches of M scanned · 3.4s · source ibkr · as of 15:42:11`) · virtualized sortable table with pinned symbol column and per-column sort · detail drawer (chart deep link using P2-T4's URL contract, factor breakdown, enrichment) · saved presets using the existing preset endpoints (`screener_routes.py:50,56,66`). Verify contrast and keyboard navigation; wire `useKeyboardShortcuts.ts` for scan/cancel/focus-filter.
- **Contract:** the results table never blanks on refresh (enforced by P4-T2); every visible number carries a timestamp or a freshness badge.
- **Tests:** `dashboard/src/pages/__tests__/ScreenerPage.test.tsx` — sort persists across scans; filter chip removal re-runs; drawer opens the correct symbol; keyboard shortcuts fire.
- **Accept:** vitest green; UI stays interactive during a 500-symbol scan (recorded interaction latency); before/after screenshots.
- **Evidence:** `phase4/screener-ux/*.png`

### P4-T8 — Benchmarks as committed artifacts
- **Owner:** E3 · **Depends:** P4-T4, P4-T5 · **Size:** M
- **Files:** new `scripts/bench_screener.py`, `docs/evidence/<SHA>/phase4/benchmarks.json`
- **Change:** Script runs the matrix — 50 / 500 / full `us_all` × cold/warm cache × IBKR available/unavailable — and emits per-cell `p50`, `p95`, `max`, `symbols_scanned`, `upstream_calls`, `cache_hit_rate`, `errors`, `source`, plus SHA and timestamp. Commit the JSON. Phase 7 fails if the file is missing or its SHA doesn't match the release SHA.
- **Contract:** benchmark JSON schema is stable so runs are comparable across SHAs.
- **Tests:** `test_bench_script_emits_valid_schema` on a tiny fixture universe.
- **Accept:** `benchmarks.json` committed for the release SHA with all matrix cells populated.
- **Evidence:** `phase4/benchmarks.json`

**Phase 4 acceptance (rollup):** results never disappear during refresh · every result carries score, factors, timestamp, source, missing fields · full-universe scans bounded and cancellable · P95 recorded per matrix cell · UI responsive during scans · backend + frontend screener tests green.

---

## Phase 5 — AI evidence and decision quality

**Goal:** every PAPER decision reproducible from stored inputs; AI compared against baselines on chronologically honest folds.
**Owner:** E4. **Exit gate:** `docs/evidence/<SHA>/phase5/`.
**Existing foundation (verified):** `backend/ai_decision_ledger.py` (`start_decision_run:25`, `record_decision_items:84`, `mark_decision_item_applied:122`, `mark_decision_item_blocked:142`, `mark_decision_item_shadow:153`, `attach_realized_trade:164`), `backend/ai_walk_forward.py` (`build_walk_forward_folds:64` with contiguity/ordering validation and `_dataset_fingerprint:59`, `run_walk_forward_evaluation:147`, `create_walk_forward_run:180`), `backend/ai_evaluator.py`, `backend/ai_guardrails.py`, `backend/ai_replay.py`, `backend/ai_optimizer.py`, `backend/claude_prompts.py`, `backend/mcp_server.py`, `backend/claude_worker.py`, `docs/adr/0003-decision-ledger-and-replay.md`, `docs/AI_WALK_FORWARD_EVIDENCE.md`. Existing tests: `test_ai_decision_ledger.py`, `test_ai_walk_forward.py`, `test_ai_fail_closed.py`, `test_ai_replay.py`, `test_ai_evaluator.py`, `test_ai_optimizer.py`.

### P5-T1 — Versioned decision schema + reproducibility hash
- **Owner:** E4 · **Depends:** — · **Size:** L
- **Files:** `backend/ai_decision_ledger.py`, `backend/decision_item_factory.py`, `backend/db/` migration, `docs/adr/0003-decision-ledger-and-replay.md` (amend)
- **Change:** Add explicit versioning and an input fingerprint to every decision item: `schema_version`, `model_id`, `model_version`, `prompt_version` (from `claude_prompts.py`), `feature_snapshot_ref`, `feature_hash`, `market_ts`, `decision_ts`, `decision`, `confidence`, `risk_result`, `execution_result`, `realized_outcome`, `git_sha`, `execution_mode`. `feature_hash` = stable hash of the exact model input, so replay divergence is detectable rather than debatable.
- **Contract:** `record_decision_items` rejects items missing any required field — no nullable-by-default columns for evidence fields. Migration backfills legacy rows with `schema_version=0` and marks them `evidence_eligible=false`.
- **Tests:** `backend/tests/test_ai_decision_ledger.py` — `test_missing_required_field_rejected`, `test_feature_hash_stable_across_processes`, `test_legacy_rows_marked_ineligible`, `test_git_sha_recorded`.
- **Accept:** `SELECT count(*) FROM decision_items WHERE schema_version >= 1 AND git_sha IS NULL` returns 0.
- **Evidence:** `phase5/decision-schema.md`

### P5-T2 — Record every signal, including rejects
- **Owner:** E4 · **Depends:** P5-T1 · **Size:** M
- **Files:** `backend/bot_runner.py:464` (decision emission), `backend/direct_ai_trader.py`, `backend/execution_brain.py`, `backend/order_proposal.py`, `backend/risk_manager.py`, `backend/services/safety_gate.py`
- **Change:** Survivorship bias is the silent killer here. Persist every candidate decision with its terminal disposition: `applied`, `blocked_by_risk`, `blocked_by_gate`, `shadow`, `insufficient_data`, `stale_data`, `model_timeout`, `invalid_output`, `duplicate`, `cooldown`. Rejections carry the rejecting component and reason code. Existing `mark_decision_item_blocked`/`_shadow` are the sinks — ensure no path can exit without calling one.
- **Contract:** every decision reaches exactly one terminal disposition; "no row" is a bug, not a state.
- **Tests:** `test_ai_decision_ledger.py::test_every_path_reaches_terminal_disposition` (parametrized over the disposition list), `test_risk_rejection_recorded_with_component`.
- **Accept:** in a PAPER session, `applied + blocked + shadow + failed == candidates_generated` exactly.
- **Evidence:** `phase5/signal-completeness.md`

### P5-T3 — Deterministic baseline strategy
- **Owner:** E4 · **Depends:** P5-T1 · **Size:** M
- **Files:** new `backend/strategies/baseline_deterministic.py`, `backend/backtest_engine.py`, `backend/evaluation_math.py`
- **Change:** A frozen, fully specified, seed-free comparator registered as a "model version" so the same evidence pipeline evaluates it: fixed entry/exit rules, fixed 1% risk sizing, no shorting, no overnight holds, explicit session hours, explicit slippage and commission assumptions. Config committed as a versioned JSON/YAML file, hashed like any other model input.
- **Contract:** `baseline_key` values: `no_trade`, `deterministic_v1`, `<prior_model_version>` — the same three comparators everywhere (`ai_walk_forward.create_walk_forward_run` already takes `baseline_key`).
- **Tests:** `backend/tests/test_baseline_strategy.py` — identical output on identical fixture data across two runs; config hash matches the committed file.
- **Accept:** baseline produces a full metric set on the same fixture the AI is evaluated on.
- **Evidence:** `phase5/baseline-spec.md`

### P5-T4 — Persisted walk-forward runs (not rolling recompute)
- **Owner:** E4 · **Depends:** P5-T1, P5-T2, P5-T3 · **Size:** L
- **Files:** `backend/ai_walk_forward.py:64,147,180`, `backend/ai_optimizer.py:496` (the six-hour loop), `backend/ai_evaluator.py`, `docs/AI_WALK_FORWARD_EVIDENCE.md`
- **Change:** The existing six-hour loop recomputes rolling metrics — that is monitoring, not validation, and must never be cited as model evidence. Make the distinction structural: rename/label the rolling output `rolling_metrics` (explicitly "not validation") and require `run_walk_forward_evaluation` to persist immutable runs with fold boundaries, `dataset_fingerprint`, `git_sha`, model/prompt versions, and per-fold metrics. `build_walk_forward_folds` already validates contiguity and `train_start < train_end <= test_start < test_end`; add an explicit leakage assertion that no test-fold input timestamp precedes its `test_start` and no training window overlaps a later test window.
- **Contract:** persisted runs are append-only and immutable; any change of code, prompt, or dataset produces a new run, never an update.
- **Tests:** `test_ai_walk_forward.py` — `test_no_future_data_in_training_window`, `test_folds_immutable_after_finalize`, `test_dataset_fingerprint_changes_with_data`, `test_rolling_metrics_not_labeled_validation`.
- **Accept:** at least one persisted walk-forward run exists for the release SHA, with ≥3 folds and both baselines evaluated on identical folds.
- **Evidence:** `phase5/walk-forward-run-<run_id>.json`

### P5-T5 — Metric set and calibration
- **Owner:** E4 · **Depends:** P5-T4 · **Size:** M
- **Files:** `backend/evaluation_math.py`, `backend/ai_walk_forward.py:141` (`_metrics`), `backend/performance_ledger.py`
- **Change:** Per fold and aggregate: hit rate, expectancy per trade, profit factor, max drawdown, turnover, estimated transaction cost (explicit commission + slippage model, stated), signal freshness distribution (`decision_ts - market_ts`), confidence calibration (reliability bins + Brier score), and sample size with a confidence interval on expectancy. Report every metric net of costs — a gross-only edge is not evidence.
- **Contract:** metric names and definitions frozen in `docs/AI_WALK_FORWARD_EVIDENCE.md`; the cost model version is recorded in each run.
- **Tests:** `backend/tests/test_evaluation_math.py` — known-answer fixtures per metric; `test_calibration_bins_sum_to_n`; `test_expectancy_net_of_costs`.
- **Accept:** every metric reproducible from the persisted run JSON alone.
- **Evidence:** `phase5/metrics-definitions.md`

### P5-T6 — Fail-closed AI failure states
- **Owner:** E4 · **Depends:** P5-T2 · **Size:** M
- **Files:** `backend/ai_guardrails.py`, `backend/ai_model_router.py`, `backend/claude_worker.py`, `backend/mcp_server.py`, `backend/data_health.py`, existing `backend/tests/test_ai_fail_closed.py`
- **Change:** Each failure mode — insufficient data, stale data (age threshold per timeframe), model timeout, invalid/unparseable output, schema violation, risk rejection — must produce **no trade**, a ledger row with the reason, and a visible dashboard state ("AI paused: stale data"). Explicitly forbid a neutral default score on failure; a mid-range "safe-looking" score is the most dangerous possible output. Cross-check against `docs/adr/0004-degraded-data-and-fallback-policy.md`.
- **Contract:** on any failure, `decision = 'none'`, `confidence = null` (never 0.5), plus a reason code.
- **Tests:** extend `test_ai_fail_closed.py` with one case per failure mode asserting no order proposal, a ledger row with the reason, and a non-null UI status; `test_no_neutral_score_on_failure`.
- **Accept:** all failure modes covered; injecting each in a PAPER session produces zero proposals and a visible banner.
- **Evidence:** `phase5/fail-closed-matrix.md`

### P5-T7 — Evidence bar for any authority increase
- **Owner:** E4 · **Depends:** P5-T4, P5-T5 · **Size:** S
- **Files:** `docs/adr/0011-ai-evidence-bar.md` (new), `backend/ai_guardrails.py`, `docs/LIVE_FLIP_RUNBOOK.md`
- **Change:** Write the numbers down before you see results, or you will rationalize afterwards. Minimum bar to even *discuss* more AI authority: ≥N PAPER decisions with realized outcomes (propose N=200), ≥3 months of chronological coverage, ≥3 walk-forward folds, expectancy net of costs above `deterministic_v1` with a stated confidence interval, max drawdown within the risk budget, calibration error below a stated threshold, zero unexplained ledger gaps. Enforce as a code check: `ai_guardrails` refuses authority escalation unless the persisted run satisfies the bar.
- **Contract:** thresholds live in one config file, referenced by ADR 0011 and the runbook.
- **Tests:** `backend/tests/test_ai_guardrails.py::test_authority_increase_blocked_below_evidence_bar`.
- **Accept:** ADR merged; guardrail test green.
- **Evidence:** `docs/adr/0011-ai-evidence-bar.md`

### P5-T8 — Replay verification
- **Owner:** E4 · **Depends:** P5-T1, P5-T4 · **Size:** M
- **Files:** `backend/ai_replay.py`, `backend/replay_scoring.py`, existing `backend/tests/test_ai_replay.py`
- **Change:** `scripts/replay_decisions.py --run <id>` re-executes stored `feature_snapshot`s against the pinned model/prompt version and reports per-item match / divergence with a divergence class (nondeterministic model, code drift, data drift, feature-hash mismatch). Divergence above a stated threshold invalidates the run as evidence.
- **Contract:** replay report JSON: `{run_id, git_sha, items, matched, diverged, divergence_by_class, verdict}`.
- **Tests:** `test_ai_replay.py::test_replay_matches_for_deterministic_fixture`, `::test_feature_hash_mismatch_flagged`.
- **Accept:** replay report committed for the release SHA with a stated verdict.
- **Evidence:** `phase5/replay-report.json`

**Phase 5 acceptance (rollup):** every PAPER decision reproducible from stored inputs · zero future data in training windows (asserted) · failures fail closed with no neutral score · no AI output bypasses risk controls · every artifact tagged with the immutable SHA · no LIVE authority discussion until ADR 0011's bar is met.

---

## Phase 6 — PAPER operational drills

**Goal:** prove the system is operationally safe, not merely statistically promising.
**Owner:** E4 (E2 for notification/WS drills). **Exit gate:** `docs/evidence/<SHA>/phase6/`.
**Reuse:** `scripts/paper_lifecycle_simulator.py`, `scripts/run_ws_isolation_drill.py`, `scripts/validate_paper_readiness.py`, `scripts/backup_db.sh`, `scripts/restore_db.sh`, `docs/PAPER_OFFLINE_LIFECYCLE_DRILL.md`, `docs/evidence/ws-isolation-drill-template.md`, `docs/paper_review_protocol.md`, `backend/tests/test_paper_lifecycle_simulator.py`, `test_order_recovery.py`, `test_execution_idempotency.py`, `test_orphan_reaper_and_rate_cap.py`, `test_emergency_close.py`.

### P6-T1 — Drill harness and report format
- **Owner:** E4 · **Depends:** — · **Size:** M
- **Files:** new `scripts/run_drill.py`, `docs/evidence/<SHA>/phase6/`, `docs/paper_review_protocol.md`
- **Change:** One harness for all drills. Every drill emits the same record: `drill_id`, `title`, `git_sha`, `execution_mode`, `started_at`, `finished_at`, `preconditions`, `injected_fault`, `expected`, `actual`, `verdict`, `log_excerpt_path`, `db_snapshot_hash_before/after`. Markdown report generated from the JSON so humans and the release gate read the same source.
- **Contract:** `docs/evidence/<SHA>/phase6/<drill_id>.json` + `.md`; verdict ∈ `pass|fail|blocked`.
- **Tests:** `test_drill_harness.py::test_record_schema_enforced`.
- **Accept:** harness produces a valid record for a trivial no-op drill.
- **Evidence:** `phase6/harness.md`

### P6-T2 — Automatable drills (D1–D7)
- **Owner:** E4 · **Depends:** P6-T1 · **Size:** L
- **Files:** new `backend/tests/drills/`, `backend/services/order_lifecycle.py`, `backend/services/order_recovery.py`, `backend/order_executor.py`, `backend/decision_ledger.py`, `backend/ws_manager.py`, `backend/main.py` (WS route + `_push_loop:1036`)
- **Change:** Implement as automated tests where possible, each producing a P6-T1 record:
  - **D1 Normal PAPER session** — signal → proposal → risk → order → fill → ledger → reconcile.
  - **D2 Backend restart mid-session** — restart between proposal and fill; assert no duplicate order and correct recovery (`order_recovery.py`).
  - **D3 Partial fill** — partial then remainder; position and ledger consistent at each step.
  - **D4 Cancel/replace** — no orphan, no double exposure (`test_orphan_reaper_and_rate_cap.py`).
  - **D5 Duplicate event** — same broker event twice; idempotency holds (`test_execution_idempotency.py`).
  - **D6 Delayed WebSocket message** — out-of-order/late event; no state regression, stale event dropped.
  - **D7 Historical data timeout** — upstream stalls; fail-closed, no trade, reason recorded (ties to P5-T6).
- **Contract:** every drill asserts the invariant, not just the absence of an exception.
- **Tests:** `backend/tests/drills/test_d1..d7`.
- **Accept:** all seven green in CI; records committed.
- **Evidence:** `phase6/d1..d7.json|md`

### P6-T3 — Semi-manual drills (D8–D11)
- **Owner:** E4 (D11 with E2) · **Depends:** P6-T1 · **Size:** L
- **Files:** `docs/LIVE_FLIP_RUNBOOK.md`, `scripts/run_ws_isolation_drill.py`, `scripts/backup_db.sh`, `scripts/restore_db.sh`
- **Change:**
  - **D8 TWS restart** — restart TWS mid-session; assert reconnect, no phantom positions, reconciliation clean (`docs/adr/0007`).
  - **D9 Network interruption** — sever the backend↔TWS path (e.g. block the port), then restore; assert degraded state surfaced, no silent trading.
  - **D10 Reconciliation mismatch** — inject a deliberate broker/ledger divergence; assert progression is **blocked**, not auto-healed.
  - **D11 Notification delivery failure** — push service unreachable during an alert; assert failure recorded, surfaced, retryable (uses P3-T5).
  Provide helper commands so the operator's manual step is a single command, not a checklist of clicks.
- **Accept:** each drill recorded with operator name, timestamp, SHA; D10 demonstrably blocks progression.
- **Evidence:** `phase6/d8..d11.json|md`

### P6-T4 — Database restart, backup, restore drill (D12)
- **Owner:** E4 · **Depends:** P6-T1, P1-T2 · **Size:** M
- **Files:** `scripts/backup_db.sh`, `scripts/restore_db.sh`, `backend/database.py`, `backend/tests/test_database_path.py`
- **Change:** v1 treated this as "database restart/reopen"; that is not enough. Full cycle: take a backup mid-session → stop the stack → restore into a clean volume → restart → assert positions, orders, ledger and decision items reconcile exactly, and that the restored DB is at the expected `/data/trading_bot.db` path. Record row-count and checksum comparisons before/after.
- **Contract:** restore is a documented, tested single command; a restored DB is byte-verifiable against its backup.
- **Tests:** `backend/tests/drills/test_d12_backup_restore.py`.
- **Accept:** ledger and position state identical post-restore (hash comparison recorded).
- **Evidence:** `phase6/d12-backup-restore.json|md`

### P6-T5 — WebSocket user-isolation drill (D13)
- **Owner:** E2 · **Depends:** P6-T1 · **Size:** M
- **Files:** `scripts/run_ws_isolation_drill.py`, `backend/ws_manager.py` (`_UNTRUSTED_IDENTITY_FIELDS:39`, `_require_user_id:49`, `send_to_user:91`, `route_event:104`, `_sanitize:136`), `docs/evidence/ws-isolation-drill-template.md`, `backend/tests/test_ws_*`
- **Change:** Two concurrent authenticated sessions with different identities; emit private events (orders, positions, alerts, AI decisions) for user A and assert user B receives **zero** of them; assert client-supplied `user_id`/`owner_user_id` fields are stripped (`_sanitize`) and cannot spoof routing; assert public broadcasts contain no owner-scoped fields.
- **Contract:** private events route only via `send_to_user`; `broadcast_public` payloads are schema-checked to exclude owner-scoped keys.
- **Tests:** `test_ws_isolation.py::test_no_cross_user_delivery`, `::test_client_supplied_user_id_ignored`, `::test_public_broadcast_has_no_owner_fields`.
- **Accept:** zero cross-user events across a full drill session; drill artifact committed.
- **Evidence:** `phase6/d13-ws-isolation.json|md`

**Phase 6 acceptance (rollup):** all 13 drills recorded with verdicts · no private event crosses a user boundary · orders and positions reconcile after every restart class · ledger durable across restore · any mismatch blocks progression.

---

## Phase 7 — Release gate and reassessment

**Goal:** the gate is code, not prose. Extend what exists instead of writing a parallel system.
**Owner:** E4. **Exit gate:** `docs/evidence/<SHA>/release/`.

### P7-T1 — Extend `run_quality_gates.sh`
- **Owner:** E4 · **Depends:** Phase 1–6 · **Size:** M
- **Files:** `scripts/run_quality_gates.sh` (currently 6 gates: backend pytest via `backend/.venv`, dashboard typecheck, dashboard build, vitest, backend docker build, full-stack docker build)
- **Change:** Add gates 7–10: secret scan (P1-T8) · `scripts/check_db_path.sh --docker` (P1-T2) · working tree clean (`git status --porcelain` empty) · SHA recorded to `docs/evidence/<SHA>/release/sha.txt`. Keep `SKIP_DOCKER=1` for local runs; CI must run the full set.
- **Accept:** script exits non-zero if any gate fails; passing run prints the SHA it validated.
- **Evidence:** `release/quality-gates.txt`

### P7-T2 — `scripts/check_release_gate.sh` (evidence completeness)
- **Owner:** E4 · **Depends:** P7-T1 · **Size:** M
- **Files:** new `scripts/check_release_gate.sh`, `scripts/validate_paper_readiness.py` (already checks `AUTOPILOT_MODE`, PAPER ports `{7497,4002}`, and required evidence bundle files: `session.json`, `metrics.jsonl`, `signals.jsonl`, `health.jsonl`, `restart-check.json`, `logs.txt`)
- **Change:** Verify the *evidence*, not just the tests: `run_quality_gates.sh` green · `validate_paper_readiness.py` green · docker healthchecks green for both containers · `docs/evidence/<SHA>/` contains every artifact named in this document (phase1/…/phase6 + release) · every artifact's embedded `git_sha` equals the release SHA · at least one persisted walk-forward run + replay report exist and meet ADR 0011 · all 13 drill records present with verdict `pass` · `benchmarks.json` present for this SHA. Output a pass/fail table; non-zero on any gap.
- **Contract:** `scripts/check_release_gate.sh <AUDITED_SHA>` is the single command that answers "is this SHA a release candidate". It verifies that `AUDITED_SHA` is an ancestor of HEAD, every embedded `git_sha` equals it, and `git diff --name-only <AUDITED_SHA>..HEAD` contains only `docs/evidence/<AUDITED_SHA>/**`.
- **Tests:** `test_release_gate_detects_missing_artifact` — remove one artifact from a fixture tree, assert failure.
- **Accept:** green only when every artifact exists and carries the release SHA.
- **Evidence:** `release/release-gate.txt`

### P7-T3 — Enforce the LIVE fence in code
- **Owner:** E4 · **Depends:** P7-T2 · **Size:** M
- **Files:** `backend/config.py`, `backend/services/safety_gate.py`, `backend/order_proposal.py` (see `test_order_proposal_paper_fence.py`), `backend/routers/bot_routes.py`, `docs/LIVE_FLIP_RUNBOOK.md`, `docs/adr/0008-security-data-and-release-boundary.md`
- **Change:** LIVE must be refused by code, not by policy memory. Require **all** of: `LIVE_ALLOWED=true` · `AUTOPILOT_MODE=LIVE` explicitly set · the running `GIT_SHA` present in a committed allowlist file · a release-gate receipt file for that SHA · no NO-GO condition flagged. Missing any → refuse to arm and log the specific reason. Keep `BOOTSTRAP_ALLOW_REMOTE=1` incompatible with LIVE (P1-T6).
- **Contract:** LIVE arming is a conjunction of independently verifiable conditions; there is no single override flag.
- **Tests:** `backend/tests/test_lifespan_safety.py` / `test_order_proposal_paper_fence.py` — parametrized: each condition individually false → arming refused with that reason.
- **Accept:** every parametrized case refuses; no test can arm LIVE by setting one variable.
- **Evidence:** `release/live-fence.md`

### P7-T4 — CI release-candidate workflow
- **Owner:** E4 · **Depends:** P7-T1…T3 · **Size:** M
- **Files:** `.github/workflows/release-candidate.yml` (new)
- **Change:** On PRs into `integration/post-reconciliation` and on tags: run `run_quality_gates.sh`, `smoke_phase1.sh` against a composed stack, the drill test suite, and `check_release_gate.sh`. Upload evidence as build artifacts. Red pipeline = the SHA is not a candidate. Add a branch-protection requirement on this workflow.
- **Accept:** workflow green on the release SHA; artifacts downloadable.
- **Evidence:** `release/ci-run.md`

### P7-T5 — NO-GO register
- **Owner:** E4 · **Depends:** — (maintained continuously) · **Size:** S
- **Files:** `docs/evidence/<SHA>/release/no-go-register.md`
- **Change:** Living table of the NO-GO conditions from v1 — auth/identity ambiguity, WebSocket isolation, notification ownership enforcement, chart feed/entitlement uncertainty, incomplete reconciliation drills, insufficient AI walk-forward evidence, unresolved risk/ledger failures, missing restore proof, any failing gate — each with owner, current state (`open`/`closed`), closing evidence path, and date. `check_release_gate.sh` parses it and fails on any `open` row.
- **Accept:** register machine-parseable and wired into P7-T2.
- **Evidence:** `release/no-go-register.md`

---

## Cross-cutting concerns (assign owners now, not later)

These sit between phases and are exactly where four parallel engineers collide.

| Concern | Owner of last resort | Consumers | Frozen by |
|---|---|---|---|
| Symbol/instrument identity | E1 | charts, screener, alerts, orders | P2-T5 |
| Timeframe/interval vocabulary | E1 | charts, screener, market data, backtests | P2-T3 |
| Auth identity model + `user_id` propagation | E2 | push, WS, presets, ledger | P1-T5, P1-T6 |
| WebSocket event schema + isolation | E2 | screener quotes, alerts, orders, AI status | P6-T5 |
| Evidence artifact layout + SHA tagging | E4 | every phase, release gate | P6-T1, P7-T2 |
| Scan/decision contracts in `backend/api_contracts.py` | E3 (screener), E4 (AI) | dashboard, tests | P4-T1, P5-T1 |
| Env var registry | E4 | compose, Dockerfiles, config, docs | P1-T1 |

**Definition of Done (every ticket):** tests named and green · quality gates green · evidence artifact committed under `docs/evidence/<SHA>/` · docs/ADR updated when a contract changed · no new `localhost`/`127.0.0.1` in shipped frontend code · no secret in code, logs, or error bodies · PR body states the parent SHA and the specific invariant proven.

---

## Sequencing and parallelization

**Week 1 — no cross-blocking work**
- E1: P1-T5 (auth UX), P2-T1 (chart ADR), P2-T3 (timeframes)
- E2: P3-T1 (push audit), P6-T5 scaffolding (WS isolation drill)
- E3: P4-T1 (screener contract), P4-T2 (scan state machine)
- E4: P1-T1, P1-T2 (DB-path bug), P1-T3 (startup diagnostics), P1-T8 (secret scan)

**Week 2**
- E1: P2-T2 (sidecar removal), P2-T5 (symbols, E3 reviews), P1-T4 (build banner)
- E2: P3-T2, P3-T3
- E3: P4-T3 (scan jobs), P4-T4 (enrichment concurrency)
- E4: P1-T6, P1-T7 (smoke), P5-T1 (decision schema)

**Week 3**
- E1: P2-T4 (URL state), P2-T6 (chart states)
- E2: P3-T4, P3-T5
- E3: P4-T5 (us_all bounds), P4-T6 (explainability)
- E4: P5-T2, P5-T3, P5-T6

**Week 4**
- E1: P2-T7 (browser matrix)
- E2: P3-T6 (notification drill), P6-T5
- E3: P4-T7 (UX), P4-T8 (benchmarks)
- E4: P5-T4, P5-T5, P5-T7, P5-T8, P6-T1

**Week 5** — P6-T2/T3/T4 drills, then P7-T1…T5. Phase 7 is a verification pass; if it takes longer than two days, an earlier phase was not actually done.

**Hard rule:** no phase is "done" until its evidence directory exists for the current SHA. A phase with green tests and no artifacts is not done — it is undocumented.

---

## GitHub setup

1. Milestone **"PAPER Baseline v2"**, one issue per ticket, title `P2-T3 — Canonical timeframe map`.
2. Labels: `phase:1-baseline` … `phase:7-gate`, `owner:e1`…`owner:e4`, `type:contract`, `type:evidence`, `type:bugfix`, `blocking:live`.
3. Branch naming: `fix/p2-t3-timeframe-map`, `feat/p4-t3-scan-jobs` — one ticket per PR, no bundling.
4. PR template requiring: parent SHA · ticket ID · invariant proven · evidence path · gate output pasted.
5. Project board columns: Blocked · Ready · In progress · In review · Evidence pending · Done. **"Evidence pending" is the column that stops this plan from rotting** — code merged without its artifact sits there visibly.
6. Protect `integration/post-reconciliation` and require the `release-candidate` workflow. Ticket PRs must not target `master`.
7. Add a tag ruleset matching `refs/tags/v2-*` that blocks update and deletion and restricts bypass to named release owners.

---

## Immediate next actions (today)

1. **E4** — fix the DB-path guard contradiction (P1-T2). It is a live inconsistency between `scripts/check_db_path.sh` (`backend/trading_bot.db`) and `docker-compose.yml` (`/data/trading_bot.db`); today the guard validates a path the container does not use.
2. **E4** — land the startup diagnostics endpoint (P1-T3) so every later bug report carries a SHA.
3. **E1** — strip the credential form (P1-T5) and open the chart ADR (P2-T1).
4. **E2** — publish the push audit table (P3-T1) before writing any push code; the backend is further along than v1 assumed and rewriting it would be waste.
5. **E3** — freeze the screener contract (P4-T1) so the UX work in P4-T7 does not chase a moving API.
6. **Nobody** — starts the new autonomous AI machine. Phase 5 evidence gates that conversation.

Stack stays in PAPER. LIVE remains blocked by the Stage 9A fence and, after P7-T3, by code that requires four independent conditions to be simultaneously true.

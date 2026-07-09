# TradeBot Desktop Application Readiness Roadmap

Audit date: 2026-07-09

Status: ACTIVE - master product plan

Current release classification: development and paper/simulation use only

Target: an installable Windows desktop application, not a browser-hosted website

## 1. Executive Decision

TradeBot already has a substantial trading backend and a broad operator dashboard.
It is not yet a desktop application and it is not ready for an unattended live-money
release.

The recommended product direction is:

1. Keep `dashboard/` as the canonical React user interface.
2. Keep `backend/` as the canonical Python trading runtime.
3. Add a Tauri 2 desktop shell under `desktop/`.
4. Package the Python backend as a single supervised sidecar process.
5. Bind the backend only to an ephemeral loopback port.
6. Replace the browser bootstrap-secret flow with a per-launch session issued by
   the desktop shell.
7. Store data under the Windows per-user application-data directory and secrets
   in OS-backed secure storage.
8. Ship a signed Windows installer and signed updater artifacts.
9. Keep Docker only as an optional development/server deployment path.

The application must open from a normal desktop shortcut. The user must not need
to run `uvicorn`, `npm run dev`, Docker, or manually open a browser.

## 2. Product Goal

A release is successful when a single operator can:

- install TradeBot with a normal Windows installer;
- launch one application window from the Start menu or desktop;
- complete safe first-run setup for simulation, IBKR paper, and optional AI;
- see explicit backend, database, market-data, IBKR, and AI health;
- use all supported dashboard workflows without broken API calls;
- stop, restart, update, back up, and restore the application safely;
- run in simulation or paper mode by default;
- enable live-money behavior only after explicit release gates and confirmations;
- uninstall the program without silently deleting trading history or backups.

### Initial Product Boundary

The first desktop release is:

- Windows 10/11, x64;
- single operator;
- local-only backend;
- SQLite persistence;
- IBKR TWS or IB Gateway as an external prerequisite;
- Anthropic integration optional and disabled without a key.

The first release is not:

- a public website;
- a remote multi-user SaaS product;
- a mobile application;
- a PostgreSQL deployment;
- a claim of profitable or risk-free trading.

## 3. Audit Scope and Evidence

This review covered both Git repositories in the workspace:

- Parent `master`: 567 tracked files.
- Parent source inventory: 421 Python/TypeScript/JavaScript files and about
  73,044 source lines, excluding generated dashboard output.
- Nested `aiautomation/main`: 65 tracked files.
- Nested source inventory: 53 TypeScript/JavaScript/CSS files and about
  8,942 source lines.
- FastAPI application: 191 registered HTTP/WebSocket route declarations.
- Documentation, Docker files, CI, configuration, source structure, route
  contracts, persistence paths, auth flow, and release runbooks were inspected.
- All tracked files were included in repository inventories and static pattern
  scans. High-risk and product-boundary modules were read directly.

Validation run on 2026-07-09:

| Area | Result |
|---|---|
| Backend pytest | 582 passed, 1 deprecation warning |
| Main dashboard typecheck | Passed |
| Main dashboard production build | Passed |
| Main dashboard Vitest | 370 passed in 27 files |
| Nested dashboard typecheck | Passed |
| Nested dashboard production build | Passed |
| Nested dashboard Vitest | 11 passed in 5 files |
| Main dashboard `npm audit` | 10 findings: 1 critical, 4 high, 4 moderate, 1 low |
| Nested dashboard `npm audit` | 12 findings: 1 critical, 4 high, 6 moderate, 1 low |
| Python dependency audit | Not available; `pip-audit` is not installed or in CI |
| UI/desktop end-to-end suite | Missing |
| Packaged application smoke test | Missing |
| Full-session paper soak | Not recorded as completed |

Passing tests are evidence that the tested behavior is stable. They do not prove
desktop packaging, frontend/backend contract completeness, live release safety,
or recovery behavior.

## 4. What Is Already Strong

The project does not need to be rewritten.

### Backend

- FastAPI runtime with broad API coverage.
- IBKR integration and reconnect handling.
- Simulation and paper/live mode separation.
- Order lifecycle, reconciliation, and trade-truth logic.
- Safety kernel, risk controls, emergency stop, and daily-loss controls.
- AI decision ledger, replay, evaluation, and guardrails.
- Retention-table SQL interpolation is now allowlisted and tested.
- Health, CORS, WebSocket auth/origin, and route-auth regression coverage.
- 582 passing backend tests.

### Main Dashboard

- React 18, TypeScript, Vite, Zustand, and lightweight-charts.
- Fifteen main application routes.
- Market, charting, screening, backtest, rules, alerts, analytics, simulation,
  stock-profile, swing, and autopilot workflows.
- Typed API modules split by domain.
- Error boundaries, confirmation modal, symbol validation, adaptive polling,
  WebSocket reconnect tests, and watchlist persistence.
- 370 passing frontend tests.

### Delivery Foundation

- GitHub Actions runs backend tests and dashboard typecheck/build/tests.
- Docker development/deployment assets exist.
- Important safety decisions are recorded in ADRs.

## 5. Readiness Scorecard

| Area | Current State | Desktop Release State |
|---|---|---|
| Trading backend | Substantial, tests green | Harden single-process runtime |
| Main dashboard | Broad and usable | Fix contracts, auth, accessibility, E2E |
| Desktop shell | Not present | Required |
| Installer | Not present | Signed MSI or NSIS installer |
| Updater | Not present | Signed in-app updater |
| Local process supervision | Not present | Required |
| Secrets | `.env` and build-time frontend secret | OS-backed secure storage |
| Data paths | Working-directory relative | `%LOCALAPPDATA%/TradeBot/...` |
| Database migrations | Ad hoc `ALTER TABLE` helpers | Versioned, tested migrations |
| Backup/restore | Retention JSONL backup only | Full DB backup and restore |
| API contract | Several verified mismatches | Generated/validated contract |
| UI E2E | Missing | Required |
| Paper soak | Checklist not completed | Required before live authority |
| Documentation | Extensive but contradictory/stale | One accurate documentation set |
| Dependency security | Current high/critical npm findings | No unresolved high/critical findings |

## 6. Verified Findings

### P0 - Must Be Resolved Before Desktop or Live Release

#### APP-P0-01: No Desktop Application Exists

There is no Tauri, Electron, Wails, PyWebView, installer, updater, application
manifest, native process supervisor, single-instance guard, tray integration, or
OS credential storage.

The existing product is a Vite browser application plus a FastAPI web server.
`PWAInstallPrompt.tsx` is not wired, and there is no manifest or service-worker
registration. A PWA would still not provide the required backend lifecycle,
installer, secret storage, or signed update model.

#### SAFE-P0-01: Production Containers Default to Multiple Stateful Workers

`Dockerfile`, `backend/Dockerfile`, and `docker-compose.yml` default to two Uvicorn
workers. Each worker executes the FastAPI lifespan and creates independent IBKR,
WebSocket, alert, market-heartbeat, and AI-loop state.

Evidence:

- `Dockerfile:65`
- `backend/Dockerfile:55`
- `docker-compose.yml:55`
- `backend/main.py:170`

This runtime must be single-process unless stateful services are separated into
dedicated workers with distributed coordination. For the desktop release, enforce
exactly one backend process and one application instance.

#### SAFE-P0-02: The Required Paper Soak Has Not Been Completed

Every runtime observation item in `sessions/phase2-paper-soak-runbook.md` remains
unchecked. The runbook explicitly states that live authority remains gated.

This is a release blocker, not optional documentation.

#### AI-P0-01: Default Anthropic Model Is Past Its Recorded End-of-Life

Four defaults in `backend/config.py:69-72` use
`claude-sonnet-4-20250514`. The current test suite emits an Anthropic SDK warning
that this model reached end-of-life on 2026-06-15.

Replace the model defaults with currently supported, operator-configurable model
IDs. Add a startup capability check and a test that fails before a configured
model reaches retirement.

#### SEC-P0-01: Unknown Unsigned DLL Is in the Workspace

Untracked file:

`Dismays_Chameleon_Tool_2.2.1_[unknowncheats.me]_.dll`

- Size: 2,333,184 bytes.
- Authenticode: not signed.
- SHA-256:
  `EDA1182C770737575437CC5C5C1109702AA7554DBD967AB088E4B9E355A88EDE`
- Public hash search during this audit returned no result.

Do not execute it. Quarantine or remove it after confirming ownership. Move all
unrelated installers and binaries outside the project workspace.

### P1 - Required Product and Correctness Work

#### APP-P1-01: Three Frontend Products and Two Unrelated Git Histories

The workspace contains:

- `dashboard/`: the most complete UI, 370 passing tests;
- `frontend/`: a tracked legacy HTML/CSS/JavaScript application;
- `aiautomation/`: a nested, separately tracked v2 dashboard with 11 tests.

The parent `master` and nested `main` branches use the same GitHub remote but have
no common ancestor. The nested dashboard has only 15 paths not present in the
main dashboard; the principal unique product surface is `AiSystemPage.tsx`.

Decision:

- Keep parent `master` and `dashboard/` canonical.
- Port any wanted nested-only features, especially AI System observability.
- Preserve the old `main` branch as an archive tag/branch.
- Remove the nested working repository and tracked legacy `frontend/` after the
  migration is verified.

#### API-P1-01: Verified Frontend/Backend Contract Mismatches

The main dashboard references backend routes that do not exist:

| Frontend Reference | Missing Backend Contract |
|---|---|
| `PositionsTable.tsx:201` | `GET /api/positions/brackets` |
| `PositionsTable.tsx:221` | `PUT /api/orders/{id}/modify` |
| `services/api/alerts.ts:19` | `GET /api/alerts/stats` |
| `services/api/alerts.ts:23` | `POST /api/push/subscribe` |
| `services/api/swing.ts:35` | `GET /api/swing/industries` |

Additional protected endpoints are called with raw `fetch()` and therefore omit
the bearer token:

- `components/analytics/PositionSizer.tsx:23`
- `components/tradebot/EODSummary.tsx:27`
- the protected/missing calls in `PositionsTable.tsx`

The O'Neil screener is explicitly stubbed at
`backend/swing_screeners.py:679` and `:756`.

Required resolution:

- implement the route and test it end to end; or
- remove/disable the UI with an explicit unavailable state.

Do not preserve a button that silently falls back or always fails.

#### AUTH-P1-01: Authentication UI and Runtime Flow Are Incomplete

`AuthGuard` is tested but never mounted by `main.tsx` or `App.tsx`.
`App.tsx:75` attempts bootstrap auth, swallows failure, and renders the trading
workspace anyway.

The login and registration screens are demo facades:

- username and password are not sent to a login endpoint;
- registration does not create a user;
- `VITE_JWT_BOOTSTRAP_SECRET` is compiled into frontend JavaScript;
- JWTs are persisted in browser localStorage;
- store data is not globally reset on unauthorized/logout.

Desktop resolution:

- the shell generates a strong per-launch session secret;
- the backend receives it through a protected process channel;
- the renderer receives only a short-lived token through Tauri IPC;
- no bootstrap secret is compiled into JavaScript;
- production has no fake login or registration UI;
- all in-memory stores reset when the session ends.

#### TRADE-P1-01: Manual Order Validation Is Not Complete

`ManualOrderRequest` validates positive quantity but has no:

- strict symbol validator;
- maximum quantity or maximum notional;
- required positive limit price for limit orders;
- asset-specific validation;
- duplicate/fat-finger confirmation at the backend boundary.

Frontend confirmation is useful but cannot be the safety boundary. The backend
must reject malformed or excessive orders independently.

#### DATA-P1-01: Persistence Is Not Desktop-Grade

Current paths are relative to the process working directory:

- database: `trading_bot.db`;
- bar cache: `data/bars`;
- event logs: `data/event_logs`;
- optional log file: arbitrary environment path.

Schema changes use `_safe_add_column()` and broad exception swallowing rather
than a versioned migration ledger. There is no supported full-database restore
workflow, migration rollback test, or automatic verified backup schedule.

Required:

- per-user application-data directories;
- versioned forward migrations with checksums;
- startup backup before migration;
- SQLite integrity check;
- WAL checkpoint before backup/update;
- operator-visible backup, restore, and export;
- restore drills in CI or integration tests;
- retention scheduling and failure visibility.

#### DEP-P1-01: Dependency Security and Reproducibility Are Incomplete

Current `npm audit` results include critical and high findings in both dashboards.
The critical main-dashboard finding includes the installed Vitest version.

Python dependencies use lower bounds only and have no lock or hashes. CI does
not run `pip-audit`, `npm audit`, Dependabot, Renovate, or SBOM generation.

Required:

- upgrade Vite, Vitest, React Router, PostCSS, Rollup/transitives, and lockfiles;
- remove the nested dashboard before maintaining a second dependency tree;
- add a reproducible Python lock for the packaged sidecar;
- add Python and Node vulnerability checks to CI;
- generate an SBOM and third-party notices for each release;
- block releases on unresolved high/critical production-impact findings.

#### SEC-P1-01: Desktop Secret and Privacy Model Is Missing

The Anthropic key and JWT secrets are environment-file settings. AI context can
include recent trades and portfolio-derived information.

Required:

- store API keys in Windows Credential Manager or Tauri Stronghold;
- document exactly what data leaves the machine;
- provide an AI-off mode that makes no Anthropic calls;
- redact secrets, tokens, account identifiers, and prompts from diagnostic logs;
- add an operator consent screen before enabling cloud AI;
- use constant-time comparison for any retained secret comparison;
- remove `BOOTSTRAP_ALLOW_REMOTE` from desktop builds.

### P2 - Required Quality and Maintainability Work

#### CODE-P2-01: Broad Exception Handling Remains High

Static scan result:

- 267 `except Exception` handlers;
- spread across 67 backend files;
- 43 bare `pass` statements matched in backend runtime code.

Some broad catches are valid at background-loop containment boundaries. Trading,
order, migration, auth, risk, and persistence paths need a line-by-line inventory
with typed exceptions, explicit degraded states, and tests.

#### UI-P2-01: Accessibility Is Incomplete

Charts now have basic `role="img"` and labels, but there is no accessible OHLCV
data alternative, keyboard drawing workflow, or complete non-color representation
for charts, heatmaps, and correlation views.

The remaining audit item F5-06 is valid. Add:

- hidden or toggleable data tables;
- keyboard navigation and focus behavior;
- screen-reader summaries;
- color-blind-safe status encoding;
- automated axe checks plus manual NVDA testing.

#### TEST-P2-01: Test Depth Is Uneven

The suites are large, but:

- nine of fifteen main pages have no matching page-level test;
- no Playwright/Cypress browser workflow suite exists;
- no packaged desktop smoke test exists;
- no test coverage threshold is enforced;
- no backend lint, Ruff, mypy/pyright, or Bandit gate exists;
- API mocks allow missing backend endpoints to look healthy.

Add contract tests generated from FastAPI OpenAPI, browser E2E tests, desktop
sidecar integration tests, migration/restore tests, and packaged installer smoke
tests.

#### DOC-P2-01: Documentation Is Contradictory and Stale

Examples:

- `README.md` and `docs/baseline.md` report 392 backend and 78 frontend tests;
- `docs/baseline.md` describes a monolithic `services/api.ts` that was split;
- `docs/DEPLOYMENT.md` uses obsolete `frontend/` paths;
- deployment examples reference files that do not exist;
- deployment documentation claims PostgreSQL readiness that the code does not have;
- deployment examples use `chmod 777` and expose backend port 8000;
- `docs/RISK_MANAGEMENT.md` lists routes that are not registered;
- `docs/RULES.md` lists version restore routes that are not registered;
- ADRs still describe auth gaps that later code fixed;
- `SECURITY_AUDIT_REPORT.md` mixes stale open statuses with later fixes.

Documentation must be generated or verified against current manifests, OpenAPI,
test output, and packaging configuration.

#### REPO-P2-01: Generated and Local Artifacts Need Cleanup

- Four `dashboard/dist` files remain tracked despite `.gitignore`.
- The legacy `frontend/` remains tracked.
- The nested `aiautomation/` repository is ignored by the parent but uses the
  same remote with unrelated history.
- Local installers and executables live beside source files.
- The root includes a large collection of agent-definition files unrelated to
  the shipped product.

Keep release source, generated artifacts, local tools, and application data in
separate locations.

## 7. Target Desktop Architecture

```text
TradeBot.exe (Tauri 2, single instance)
|
|-- Bundled React assets from dashboard/dist
|
|-- Tauri IPC
|   |-- runtime status
|   |-- secure secret access
|   |-- app-data paths
|   |-- update/install actions
|   `-- diagnostic export
|
`-- tradebot-backend.exe (PyInstaller sidecar, one process)
    |-- FastAPI on 127.0.0.1:<ephemeral-port>
    |-- per-launch short-lived auth session
    |-- SQLite in %LOCALAPPDATA%\TradeBot\data
    |-- logs in %LOCALAPPDATA%\TradeBot\logs
    |-- backups in %LOCALAPPDATA%\TradeBot\backups
    `-- IBKR connection to local TWS/Gateway
```

### Desktop Runtime Rules

- Only one desktop instance and one backend sidecar may run.
- The backend binds to `127.0.0.1`, never `0.0.0.0`.
- The shell chooses an available port; no fixed public port is required.
- The renderer learns the endpoint and token only through IPC.
- The backend must be ready before trading screens become interactive.
- Backend death moves the UI into a blocking degraded state and offers restart.
- Application quit performs an orderly bot stop, order reconciliation, WAL
  checkpoint, log flush, and sidecar termination.
- Update installation is blocked while live orders or unreconciled positions
  require attention.
- Production builds disable remote navigation and restrict Tauri permissions.
- The first launch starts in simulation mode until the operator completes setup.

### Why Tauri 2

Tauri can bundle a Python/PyInstaller API server as a sidecar, supports Windows
installers, has an updater plugin, and provides explicit capability permissions.
It preserves the existing React investment without shipping a public web server.

Primary references:

- Sidecars: https://v2.tauri.app/develop/sidecar/
- Windows installer: https://v2.tauri.app/distribute/windows-installer/
- Updater API: https://v2.tauri.app/reference/javascript/updater/
- Stronghold secure storage:
  https://v2.tauri.app/reference/javascript/stronghold/

## 8. Implementation Roadmap

### Phase A - Truth, Safety, and Product Consolidation

Goal: one trustworthy codebase and no known immediate runtime/release blocker.

- [ ] Quarantine/remove the unknown unsigned DLL.
- [ ] Move unrelated installers and binaries outside the repository workspace.
- [ ] Force all current deployments to one Uvicorn worker.
- [ ] Add a process lock preventing duplicate trading runtimes.
- [ ] Replace the end-of-life Anthropic model defaults.
- [ ] Choose `dashboard/` as canonical in an ADR.
- [ ] Port wanted nested-only features, especially `AiSystemPage`.
- [ ] Archive nested `main` and remove the nested working repository.
- [ ] Remove or archive legacy `frontend/`.
- [ ] Untrack `dashboard/dist`.
- [ ] Update README test counts and product status.

Acceptance:

- one canonical branch and one canonical UI;
- no unrelated binary in the workspace;
- no supported launch path starts multiple trading runtimes;
- all existing tests and builds pass.

### Phase B - Contract, Auth, and Product Correctness

Goal: every visible workflow has a real, authenticated backend contract.

- [ ] Resolve every missing route listed in API-P1-01.
- [ ] Move all protected calls through the shared authenticated client.
- [ ] Add OpenAPI-to-TypeScript generation or schema contract validation.
- [ ] Add CI that fails when a frontend endpoint is absent from OpenAPI.
- [ ] Remove fake login/register screens or implement a real supported flow.
- [ ] Implement desktop-shell session bootstrap.
- [ ] Add global store reset on session loss.
- [ ] Add backend order symbol, quantity, notional, and limit-price validation.
- [ ] Replace native `window.confirm`/`window.prompt` with application modals.
- [ ] Decide and label O'Neil/leading-industries support honestly.
- [ ] Add content-security policy and remove remote renderer navigation.

Acceptance:

- no UI action calls a missing route;
- no protected call omits auth;
- no build-time secret is present in renderer assets;
- order validation fails closed at the backend.

### Phase C - Data Durability and Runtime Hardening

Goal: crashes, updates, and migrations do not lose or corrupt trading state.

- [ ] Introduce versioned SQLite migrations.
- [ ] Add pre-migration backup and post-migration integrity checks.
- [ ] Move database, logs, caches, and backups to per-user app-data paths.
- [ ] Implement full database backup and restore.
- [ ] Test backup restore against representative historical databases.
- [ ] Schedule retention and expose failures in diagnostics.
- [ ] Inventory broad exceptions in trading-critical modules.
- [ ] Replace silent migration catches with explicit known-error handling.
- [ ] Add clean shutdown and crash-restart reconciliation tests.
- [ ] Add log redaction and diagnostic-bundle export.

Acceptance:

- upgrade from each supported schema version succeeds;
- rollback/restore drill succeeds;
- forced sidecar termination does not create duplicate orders;
- restart reconciles DB and broker state.

### Phase D - Desktop Shell and First-Run Experience

Goal: the project runs as a real local Windows application.

- [ ] Create `desktop/` Tauri 2 project.
- [ ] Package backend with PyInstaller using a reproducible lock.
- [ ] Bundle the backend as a Tauri sidecar.
- [ ] Add single-instance behavior.
- [ ] Add dynamic loopback port and readiness handshake.
- [ ] Add shell-to-renderer runtime status IPC.
- [ ] Replace BrowserRouter with a desktop-safe routing strategy.
- [ ] Add first-run setup for simulation, IBKR, AI, and storage location.
- [ ] Add IBKR prerequisite detection and connection diagnostics.
- [ ] Add secure secret storage.
- [ ] Add native notifications and optional tray controls.
- [ ] Add a backend-crashed recovery screen.
- [ ] Add About, version, diagnostics, logs, and data-folder actions.

Acceptance:

- a clean Windows machine can install and launch the app;
- no terminal or browser is required;
- backend startup and shutdown are owned by the shell;
- no inbound firewall rule is required.

### Phase E - Installer, Updates, and Release Engineering

Goal: signed, repeatable, recoverable desktop releases.

- [ ] Choose MSI or NSIS and define upgrade/uninstall semantics.
- [ ] Add application icon, metadata, publisher, and version source of truth.
- [ ] Obtain Windows code-signing capability.
- [ ] Sign installer, application, sidecar, and updater artifacts.
- [ ] Configure signed Tauri updates.
- [ ] Add staged update channels: internal, paper/beta, stable.
- [ ] Block update install during unsafe runtime states.
- [ ] Preserve database/backups during uninstall unless explicitly selected.
- [ ] Generate checksums, SBOM, and third-party notices.
- [ ] Add GitHub release workflow and artifact retention.

Acceptance:

- install, upgrade, rollback, and uninstall are tested on a clean VM;
- signatures validate;
- update cannot leave an orphan backend process;
- user data survives upgrade.

### Phase F - Verification and Live-Readiness Gate

Goal: prove the packaged application behaves safely under realistic use.

- [ ] Add Playwright tests for critical operator workflows.
- [ ] Add packaged desktop startup/quit/restart smoke tests.
- [ ] Add sidecar crash and port-conflict tests.
- [ ] Add API contract tests against the real FastAPI app.
- [ ] Add backend and frontend coverage thresholds.
- [ ] Add Ruff, Python type checking, ESLint, accessibility, and security gates.
- [ ] Resolve high/critical dependency findings.
- [ ] Run Python dependency audit and secret scan.
- [ ] Complete keyboard and NVDA accessibility review.
- [ ] Complete one full packaged-app simulation soak.
- [ ] Complete one full US-session IBKR paper soak.
- [ ] Complete mid-session backend and desktop restart drills.
- [ ] Complete emergency-stop and daily-loss-lock drills.
- [ ] Perform an independent security review.
- [ ] Record sign-off evidence in a dated release report.

Acceptance:

- no open P0/P1 finding;
- all automated gates pass from a clean checkout;
- the packaged paper soak passes;
- live mode remains impossible until the release report is signed off.

## 9. Required Documentation Set

The final application should have this documentation structure:

| Document | Purpose |
|---|---|
| `README.md` | Product status, install, launch, and safe-mode quick start |
| `docs/PRODUCT.md` | Audience, supported workflows, non-goals |
| `docs/DESKTOP_ARCHITECTURE.md` | Shell, sidecar, IPC, ports, paths, lifecycle |
| `docs/BACKEND_ARCHITECTURE.md` | Trading runtime and service boundaries |
| `docs/API.md` | Generated OpenAPI reference and compatibility policy |
| `docs/SECURITY.md` | Threat model, auth, secrets, local-only boundary |
| `docs/DATA_AND_PRIVACY.md` | Local data and data sent to IBKR/Anthropic/providers |
| `docs/TRADING_SAFETY.md` | Modes, authority, kill switch, release gates |
| `docs/BACKUP_AND_RECOVERY.md` | Backup, restore, integrity, disaster recovery |
| `docs/OPERATIONS.md` | Logs, diagnostics, IBKR setup, troubleshooting |
| `docs/RELEASE.md` | Versioning, signing, updater, rollback, release checklist |
| `docs/DEVELOPMENT.md` | Reproducible developer setup and quality gates |
| `CHANGELOG.md` | User-facing changes by version |
| `SECURITY.md` | Vulnerability reporting policy |
| `LICENSE` | Project usage and distribution terms |
| `THIRD_PARTY_NOTICES.md` | Dependency and data-provider notices |

Documentation rules:

- commands must be executed from a clean checkout before publication;
- endpoint lists must come from OpenAPI, not manually invented examples;
- test counts are dated evidence, not permanent claims;
- unsupported features must be labeled planned or unavailable;
- desktop operation is primary; Docker/browser instructions are secondary;
- every live-money instruction includes an explicit safety gate.

## 10. Definition of Done

The goal is complete only when all of the following are true:

### Product

- [ ] TradeBot installs and opens as a Windows desktop application.
- [ ] No browser, dev server, Docker command, or terminal is required.
- [ ] All visible controls have working backend behavior.
- [ ] Simulation is the first-run default.

### Safety

- [ ] Exactly one trading runtime can execute.
- [ ] Backend is loopback-only and uses an ephemeral session.
- [ ] No renderer bundle contains long-lived secrets.
- [ ] Manual and AI orders enforce backend risk and validation limits.
- [ ] Emergency stop remains available during degraded states.
- [ ] Paper soak and restart drills pass.

### Reliability

- [ ] Versioned migrations, backup, restore, and integrity checks pass.
- [ ] Crash recovery does not duplicate or lose order state.
- [ ] Updates are signed and recoverable.
- [ ] Logs and diagnostic bundles redact sensitive data.

### Quality

- [ ] Unit, integration, contract, E2E, accessibility, and packaged smoke tests pass.
- [ ] Coverage thresholds are enforced.
- [ ] No unresolved release-blocking dependency/security finding remains.
- [ ] Clean-machine installer and upgrade tests pass.

### Documentation

- [ ] Documentation matches the shipped version.
- [ ] First-run, IBKR, AI privacy, backup, recovery, and update behavior are documented.
- [ ] Release evidence is dated and archived.

## 11. Recommended Execution Order

Do not start with installer styling.

Use this order:

1. Fix single-process safety, the end-of-life model, and workspace binary hygiene.
2. Consolidate the repositories and choose the canonical UI.
3. Fix API/auth/order contract gaps.
4. Make migrations, data paths, backup, and recovery desktop-safe.
5. Add the Tauri shell and sidecar lifecycle.
6. Add installer, signing, and updater.
7. Add E2E and packaged-app verification.
8. Complete simulation and paper soaks.
9. Consider live authority only after recorded sign-off.

## 12. Current Go/No-Go Decision

| Use | Decision |
|---|---|
| Local development | GO |
| Automated tests | GO |
| Simulation experimentation | GO with normal caution |
| IBKR paper use | CONDITIONAL; use one backend process and complete soak |
| Public/remote web exposure | NO-GO |
| Installable desktop release | NO-GO; desktop shell is not implemented |
| Unattended live-money release | NO-GO |

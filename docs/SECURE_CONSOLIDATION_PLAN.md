# Secure Consolidation and Product Readiness Plan

**Status:** Draft governing plan; LIVE **NO-GO**
**Captured:** 2026-08-18 (Asia/Jerusalem)
**Integration branch:** `integration/post-reconciliation`
**Last pushed baseline:** `6234fec54619a797e4851c93c61aa54d2b4efa6a`
**Candidate SHA:** TBD — current working tree still contains the notification frontend delta
**Authority:** This document supplements `ROADMAP_TEAM_PLAN.md`; it does not authorize live trading.

## 1. Executive Picture

The repository is in a substantially safer state than the first review, but it
is not a release candidate. The work separates into three tracks:

| Track | Current state | Release consequence |
|---|---|---|
| Secure platform | WebSocket isolation and backend Web Push are merged; frontend push/auth hardening is implemented in the dirty candidate | Finish gates, manual browser drills, and SHA-pinned audit |
| Product quality | Chart correctness and screener reliability quick wins are merged; UX/performance redesign remains | Product milestone remains open; it does not authorize LIVE |
| Execution safety | AI is fail-closed/PAPER-oriented, but durable broker intent, reconciliation, account risk, and restore fencing are absent | LIVE remains blocked with no waiver |

The immediate outcome is a clean, immutable secure-consolidation candidate. The
next outcome is evidence for the screener, AI, and IBKR PAPER tracks. Only after
those tracks and the LIVE foundation are independently accepted can a future
attended canary be considered.

## 2. Status Ledger

Use these states consistently: `MERGED`, `IN PROGRESS (DIRTY)`, `PENDING
RUNTIME EVIDENCE`, `NOT IMPLEMENTED`, and `BLOCKED`.

### 2.1 Confirmed complete or substantially complete

- **Source reconciliation:** reviewed integration line is pushed; the current
  candidate must still absorb or discard every remaining dirty path. No force
  push and no squash blob.
- **Database path lifecycle:** relative `DB_PATH` resolves against `backend/`;
  Compose uses `/data/trading_bot.db`; startup validates accessibility and
  rejects ephemeral paths for execution-sensitive use.
- **WebSocket isolation:** merged at `6882084`; authenticated sockets retain
  server-resolved user identity; private events are routed per user; unknown
  private events fail closed; public fanout remains allowlisted.
- **Chart correctness foundations:** dead `127.0.0.1:5001` sidecar dependency
  removed; resolution-aware market data and timeframe handling are integrated.
- **Screener quick wins:** result replacement, ranking/universe contract, and
  related reliability fixes are integrated. The page redesign and capacity
  work are still open.
- **AI fail-closed behavior:** optimizer/bot failure paths no longer silently
  authorize a neutral trade; scheduled learning is still metrics recompute,
  not persisted walk-forward evidence.

### 2.2 In progress or pending evidence

- **Frontend Web Push:** service worker, authenticated ownership checks, shared
  notification controller, explicit per-device enrollment, VAPID rotation,
  fallback behavior, and generation-aware cleanup are in the current dirty
  candidate. Focused validation is green: 40 tests across auth, browser push,
  hook, provider, and API-client suites; typecheck is green. Full gates and a
  real browser/provider drill remain.
- **Charts:** the code uses TradingView Lightweight Charts as the rendering
  library and backend/IBKR data as the feed. A TradingView Pro account must not
  be treated as an in-app API/data entitlement; the feed, exchange entitlement,
  licensing, and latency contract must be verified before claiming “no delay.”
- **Screener:** page information architecture, loading/error/empty states,
  retained results, `us_all`, parallel data access, caching, and P50/P95
  capacity evidence remain open.
- **AI:** no persisted, reproducible walk-forward/replay artifact with realistic
  costs, baselines, calibration, abstention, sample-size, and regime gates has
  been accepted. AI remains PAPER-only.
- **IBKR PAPER:** entitlement, order lifecycle, partial fill, cancel/replace,
  reconnect, and reconciliation drills are not yet accepted.

### 2.3 Not implemented and release-blocking

- Durable execution authority and fencing across restarts/hosts.
- Immutable execution intent ledger with transactional outbox and UNKNOWN state.
- Broker order/fill/commission truth and complete reconciliation cursors.
- Durable account-risk state with broker-P&L sampling and automatic latches.
- Broker-native protective-order coverage and partial-fill containment proof.
- Restore-generation fencing and consumed-intent replay prevention.
- Production identity, RBAC, step-up approval, and non-demo authentication.

## 3. Database Truth Versus Roadmap

### 3.1 Files that exist

Two SQLite files remain in the original checkout and are intentionally not
auto-merged:

| File | Role | SHA-256 prefix | Finding |
|---|---|---|---|
| `/Users/salomon/aiautomation/backend/trading_bot.db` | Current local operational path | `fcf475…` | Treat as canonical local state pending a documented migration |
| `/Users/salomon/aiautomation/trading_bot.db` | Legacy cwd-relative artifact | `dcb664…` | Quarantine and archive; never select by process CWD |

Both files pass SQLite integrity checks and expose the same 33 visible tables,
but their hashes and migrations differ. The backend copy contains user-scoped
simulation columns that the root copy lacks. Neither historical file contains
the later `push_subscriptions` or lazy `order_rate_window` state. They are
evidence artifacts, not interchangeable production databases.

### 3.2 What the current schema supports

The schema is adequate for dashboard, alert, screener, simulation, and AI
proposal/evaluation persistence. It includes users, rules, trades, simulated
orders/positions, alerts/history, push subscriptions, screener presets,
diagnostics, AI guardrails/audit/snapshots, direct-candidate/TradingView
idempotency, and AI decision/evaluation tables.

The current `trades` row is a lifecycle record written before broker submission;
it is not an immutable execution intent. `direct_candidates` and
`tv_idempotency` provide inbound deduplication, not safe broker execution
ownership. AI decision tables are proposal/evaluation evidence, not broker
execution truth.

### 3.3 What the LIVE roadmap still requires

The following responsibilities are absent and must be designed and migrated as
new, versioned schema—not inferred from existing JSON rows:

- authority state/events, executor leases, fencing epochs;
- unique execution intents and append-only intent events;
- broker orders, fills, commissions, and account snapshots;
- transactional outbox with lease/retry/dead-letter state;
- protection groups and filled-quantity coverage;
- reconciliation runs, discrepancies, high-water marks, and readiness state;
- versioned risk policies/sessions, P&L samples, threshold events, and signed
  reset/containment records; and
- restore generation, migration/checksum metadata, and last-consumed sequence
  per stream.

### 3.4 Safe database consolidation procedure

1. Freeze both files, record hashes, schema/table/row counts, WAL sidecars, and
   integrity results.
2. Choose the source by documented runtime provenance, not row count or newest
   modification time; default local authority is the backend path.
3. Produce a table-by-table diff and classify every collision. Do not merge
   users, trades, rules, or simulated positions automatically.
4. Restore into a new target path, apply migrations offline, run foreign-key and
   integrity checks, and exercise the application against the copy.
5. Record the migration ID, source hashes, target hash, operator, and rollback
   archive. Only then quarantine the legacy root file.
6. Update backup/restore scripts and deployment configuration to require the
   exact configured path/volume; never fall back to a cwd-relative filename.

## 4. Ownership and Tracking

These internal IDs are the minimum tracking contract. Real issue/PR URLs and
human handles remain `TBD`; a release cannot be approved while the DRI and
independent reviewer fields are blank.

| ID | Workstream | DRI role | Independent reviewer | Exit evidence |
|---|---|---|---|---|
| `SC-WS-01` | WebSocket isolation/replay | WebSocket specialist | Security reviewer | two-user isolation drill + CI suite |
| `SC-PUSH-01` | Web Push/backend/frontend | Alerts backend + React specialist | Security reviewer | closed-tab delivery, ownership, fallback, disable |
| `SC-REL-01` | Branch/tree consolidation | Release/integration engineer | QA/release reviewer | clean SHA, disposition, diff check |
| `SC-GATE-01` | Unified quality gates | QA/quality engineer | Release approver | CI artifacts + manual checklist |
| `NEXT-SCR-01` | Screener UX/performance | Screener specialist | UX + performance reviewers | benchmark report and screenshots |
| `NEXT-AI-01` | AI evidence/governance | AI/trading-strategy specialist | risk reviewer | persisted walk-forward evidence |
| `NEXT-PAPER-01` | IBKR PAPER drills | Market-data/order-execution specialists | risk reviewer | signed drill matrix |
| `LIVE-LEDGER-01` | Intent/outbox/fencing | Backend/database specialist | security + risk | ADR 0006 implementation/evidence |
| `LIVE-RISK-01` | Account risk/protection | Risk manager/order-execution specialist | risk approver | ADR 0007 implementation/evidence |
| `LIVE-RESTORE-01` | Restore/replay fencing | Deployment/database specialist | release + risk | restore drill and sequence proof |

Each PR body must cite its internal ID, base SHA, exact test commands/results,
feature-flag impact, rollback behavior, and evidence paths.

## 5. Non-Negotiable Invariants

- No private event is broadcast without a server-trusted owner.
- No notification endpoint is claimed by a new account without authenticated
  ownership verification and verified local quarantine.
- A global browser-push preference permits delivery but does not silently create
  a new device subscription; “This browser” is explicit consent.
- Alert history is persisted before delivery. WebSocket and push failures are
  independent; in-app/history fallback remains available.
- No unknown event type, missing owner, broker mismatch, ledger write failure,
  stale decision/risk input, or reconciliation ambiguity can increase risk.
- AI stays OFF/PAPER until evidence gates pass; it cannot weaken deterministic
  protection.
- Database disagreement blocks entries; broker truth dominates local state.
- LIVE remains disabled regardless of UI state, branch state, or a passing unit
  suite.

### Failure classification

**Soft failures** are recoverable transport/display failures: one socket send,
an expired push endpoint, browser permission denial, or a chart/screener fetch.
They produce bounded telemetry, preserve history or an HTTP/in-app fallback, and
never fabricate trading truth.

**Hard failures** are security, authority, integrity, broker-account,
reconciliation, risk, lease/fence, or restore-sequence failures. They fail
closed, latch `ENTRY_LOCKED`, use `EXIT_ONLY` only when authenticated exposure
reduction is safe, and require operator evidence before rearming.

## 6. Secure Consolidation Delivery Sequence

### Phase A — Finish the current candidate

1. Inventory the dirty tree and remove only the temporary `dashboard/node_modules`
   symlink; preserve user source/tests/docs deliberately.
2. Finish the frontend push delta on top of `6234fec`. Candidate-token ownership
   checks must complete before `setAuthToken` or private children mount.
3. Run focused WebSocket/push/auth tests and both independent reviews.
4. Commit with a conventional message; push the integration branch without force
   or squash.

### Phase B — Unified gates

**CI-enforced:** backend pytest, frontend typecheck, production build, Vitest,
`git diff --check`, dependency installation from manifests, and a clean status
check. Run backend tests from `backend/` and validate DB-path independence.

**Manual:** two authenticated browser profiles, reconnect/replay ownership,
permission denial, closed-tab/background push, click-through, disable, expired
endpoint cleanup, provider failure fallback, chart timeframe/feed smoke,
screener retained-results/`us_all` smoke, and metrics inspection.

The release candidate is not accepted from a dirty tree, a mutable branch tip,
or a test count copied from a prior SHA. Record exact counts, duration, command,
environment, and SHA in an evidence file.

### Phase C — Immutable audit

1. Tag the exact clean candidate SHA only after CI and manual gates pass.
2. Record configuration, dependency lockfile, image, broker environment, and
   VAPID/config fingerprints.
3. Re-audit that SHA only. Any source/config change invalidates the candidate.

## 7. Product Tracks After Secure Consolidation

### 7.1 Charts / TradingView decision

The immediate engineering path is TradingView Lightweight Charts with a
server-owned, entitlement-checked market-data feed. The current app must finish
the data contract, timeframe mapping, loading/error states, and live-versus-
historical reconciliation.

Before adopting a hosted TradingView Charting Library or widget, obtain written
confirmation of licensing, permitted embedding, exchange data entitlements,
symbol coverage, and latency. A TradingView Pro subscription on the public
website must not be assumed to provide programmatic chart data or eliminate
delays in this application. “No delay” is a feed/entitlement acceptance test,
not a UI setting.

### 7.2 Screener redesign and performance

1. Define the information architecture: universe, filters, ranking, saved
   presets, scan status, stale/error state, and retained previous results.
2. Fix the contract so `us_all` is selectable and validated end-to-end.
3. Replace clear-before-replace with generation-tagged results and visible
   stale/refresh state.
4. Move bulk work to bounded concurrent server execution with cache/rate-limit
   controls; do not create a 20-symbol sequential waterfall.
5. Benchmark representative universes with P50/P95 latency, error rate, memory,
   provider call count, and UI render time. Store the dataset, SHA, and run
   configuration with the report.
6. Run UX review on desktop and narrow layouts; screenshots are manual evidence,
   not a substitute for API tests.

### 7.3 AI evidence and governance

The six-hour learning loop is not a backtest gate. Build a persisted evaluation
run containing immutable input snapshot, train/test windows, walk-forward slices,
cost/slippage assumptions, benchmark, calibration, abstention, sample-size,
regime, drawdown, and failure metadata. Require reproducibility from the same
SHA/model/prompt/data fingerprint. Keep scheduled optimization proposal-only
until the risk approver accepts the evidence.

### 7.4 IBKR PAPER drills

Run only in a dedicated PAPER account with captured account/port/config identity:
market and limit entry, partial fill, cancel/replace, rejection, disconnect,
reconnect, duplicate retry, stale data, broker/local mismatch, and operator
reduce-only recovery. A passing API test is not a broker drill.

## 8. LIVE Foundation (Downstream, Blocking)

### Transaction and concurrency boundary

Before any broker call, one database transaction must validate authority,
policy/risk/data versions; claim unique `(account, namespace, idempotency_key)`;
create a stable `internal_intent_id`; reserve capacity; append an immutable
intent event; persist the risk snapshot; and write an outbox row. Broker
submission is eventually consistent and deduplicated by
`(internal_intent_id, broker_account)`, with the internal ID in `orderRef`.

The dispatcher claims outbox rows with a lease/fencing epoch. It never marks an
intent sent/applied from local intent alone. Broker orders, fills, commissions,
positions, and reconciliation outcomes are separate truth records.

### Risk and protection

Implement durable `DISARMED`, `ENTRY_LOCKED`, `EXIT_ONLY`, and approved armed
states. Persist broker-derived equity baseline/peak, currency and cash-flow
adjustments, freshness, threshold events, protection coverage, and authorized
resets. Any ambiguity keeps entries locked. Filled quantity must have
acknowledged broker-native protection; a submitted child order is not proof.

### Restore sequence

1. Stop execution and enter `DISARMED`; disable submissions and AI.
2. Verify backup checksum, schema/app version, configured path, and key
   prerequisites; restore to a new generation/path.
3. Run integrity/FK checks and migrations offline.
4. Invalidate old leases, increment the fencing/restore epoch, and persist a
   restore marker.
5. Load and verify the last-consumed sequence for each outbox/event stream so
   consumed intents cannot replay.
6. Install idempotent broker handlers, query broker truth, and reconcile by
   intent, account, orderRef, broker IDs, and execution IDs.
7. Keep `ENTRY_LOCKED`/`EXIT_ONLY` for every unresolved discrepancy; require
   signed operator evidence before rearming PAPER, then any future canary.

## 9. Rollout, Rollback, and Telemetry

| Component | Default/flag | Rollout | Rollback |
|---|---|---|---|
| Web Push | `WEB_PUSH_ENABLED=false` | VAPID + browser smoke first | Disable flag and restart; history/in-app/WS remain |
| Private IBKR streams | `IBKR_PRIVATE_ACCOUNT_STREAMING_ENABLED=false` | Enable only with owner mapping | Disable producer/private stream; never broadcast globally |
| AI | `AUTOPILOT_MODE=OFF/PAPER` | Evidence and operator approval | Return to OFF/PAPER; no LIVE compatibility mode |
| WebSocket isolation | No insecure compatibility flag | Cut over after two-user drill | Drop private delivery or redeploy prior isolated SHA |
| Database migration | Backward-compatible schema | Backup, new generation, offline validation | Restore prior immutable DB/config generation |

Telemetry must stay bounded and privacy-safe:

- WebSocket auth/origin failures, unknown events, missing-owner drops, replay
  conflicts, and private delivery outcomes; zero private isolation violations.
- Push enabled/ready gauges, success/failure/expired outcomes, bounded error
  classes, and subscription cleanup counts; never endpoint or user labels.
- Screener scan duration/error/cache/provider-call metrics and benchmark
  metadata.
- AI evaluation run IDs, evidence fingerprints, abstention/sample/regime
  counts, and proposal/rejection reasons.
- Risk state transitions, reconciliation discrepancies, outbox age/retry/dead
  letter, lease/fence changes, and operator acknowledgements in protected audit
  storage.

## 10. Exit Criteria and Non-Authorization

Secure Consolidation is complete only when:

- owners/issues and independent reviewers are assigned;
- frontend push is committed on the integration line;
- CI and manual gates are green on one clean SHA;
- telemetry and rollback behavior are recorded;
- the database path/disposition is documented without destructive merging; and
- the final audit cites the immutable candidate SHA only.

That completion authorizes continued PAPER validation and product work. It does
**not** authorize `AUTOPILOT_MODE=LIVE`, real-money orders, AI profitability
claims, a finished screener redesign, or restore/ledger readiness.

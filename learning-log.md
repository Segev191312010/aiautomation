# Learning Log

Chronological record of sessions, discoveries, and decisions.

---

### 2026-02-24 — Stage 1: Foundation, Auth Scaffold & Polish
- Completed: Auth scaffold (JWT + bcrypt), settings system (JSON blob + deep merge), toast notifications, error boundaries, loading skeletons, 15 backend tests
- Learned: bcrypt 5.x dropped passlib compatibility — use bcrypt directly, not passlib[bcrypt]
- Learned: All CRUD functions default user_id="demo" so existing callers need zero changes
- Gotchas: ALTER TABLE ADD COLUMN fails silently if column exists — desired behavior for safe migration
- Next: Stage 2a (Chart Core & Volume)

### 2026-02-25 — Stage 2a: Chart Core & Volume
- Completed: ChartToolbar (9 timeframes, 6 chart types, indicator dropdown), VolumePanel, useChart hook, Heikin-Ashi utility, trade marker helpers, indicator endpoint
- Learned: React 18 useRef<HTMLDivElement | null> type incompatibility with JSX ref prop — cast with `as React.RefObject<HTMLDivElement>`
- Learned: lightweight-charts v4.2 takeScreenshot() returns canvas element — use canvas.toBlob() + URL.createObjectURL() for download
- Learned: Bidirectional time-axis sync needs syncingRef + setTimeout(0) to prevent infinite loops
- Gotchas: yfinance does not support native 4h interval — use 1h with 3mo period instead
- Next: Stage 2b (Drawing Tools)

### 2026-02-25 — Stage 2b: Drawing Tools
- Completed: H-line, trendline, Fibonacci retracement via HTML5 Canvas overlay on lightweight-charts
- Learned: Canvas overlay must re-render on chart scroll/zoom via subscribeVisibleTimeRangeChange
- Next: Stage 2c (Multi-Pane Sync)

### 2026-02-26 — Stage 2c: Multi-Pane Sync
- Completed: Crosshair sync across panes, time-axis sync, resizable pane heights via drag handles
- Next: Stage 3 (Stock Screener & Scanner)

### 2026-07-27 — Stage 9A: Live-Safety Foundation
- Completed: launched the evidence-gated safety program; drafted three proposed ADRs, a threat model, residual-risk register, fault matrix, and sanitized baseline manifest
- Completed: added a code-owned real-money release fence, lifespan-held local execution lock, one-worker defaults, shared SQLite order cap with bounded fail-closed contention, strict startup/auth/database checks, a hard SIM broker boundary, default-off metrics, and scheduled AI proposal-only behavior
- Verified: isolated backend suite 829 passed; dashboard typecheck/build passed; Vitest 364 passed; `git diff --check` passed; independent code, security, and broker/risk reviews found no unresolved Critical or High regression
- Learned: local `orderRef` correlation is not broker idempotency; process locks are not cross-host fencing; a passing unit suite cannot substitute for broker-contract, deployment, restore, or operator-drill evidence
- Gotchas: concurrent pytest processes share repository-local temporary SQLite paths and can interfere; final evidence must be collected sequentially or with isolated test roots
- Limitation: Docker image gates were not run because the daemon was unavailable, and no commit was created because the worktree already contained overlapping user changes
- Safety state: LIVE remains NO-GO; no broker operation, runtime-mode mutation, or `.env` change occurred
- Next: assign human ADR/risk/security/release owners, isolate a clean reviewed commit, then implement the single gateway plus durable intent/UNKNOWN reconciliation before any broker-protection or live-canary work

### 2026-07-27 — Stage 9B Sub-Stages A/B: Containment Commit Isolated
- Completed: snapshotted dirty worktree, created clean worktree `/Users/salomon/aiautomation-9b` from `origin/feature/ultraplan-v4`, isolated Stage 9A containment into local commit `76f62f0` on branch `stage-9b-containment`, tagged `v4-containment`
- Completed: ran quality gates in the clean worktree — dashboard typecheck/build green, Vitest 364 passed, backend pytest 818 passed
- Decided: Phase 1 SF1a base is `stage-9b-containment`; `feature/ultraplan-v5` was rejected because it lacks Stage 9A fences (still auto-promotes paper rules, no proposal-only mode, no execution lock, no real-money fence)
- Created: `sessions/stage-9b-containment-and-phase1-gateway.md` and `sessions/stage-9b-phase1-sf1a-execution-lease.md`
- Wrote: `handoffs/2026-07-28-stage-9b-containment-commit.md` in the clean worktree
- Learned: a clean local worktree is the only safe way to commit containment when the main worktree mixes unrelated changes
- Gotchas: accidentally staged `.venv` and `node_modules` symlinks on first commit attempt — reset and removed them before final commit
- Safety state: LIVE remains NO-GO; commit is local-only, not pushed; all Stage 9A fences remain intact
- Next: open Phase 1 session in `/Users/salomon/aiautomation-9b` to implement cross-host execution lease + fencing token per ADR 0006

### 2026-07-28 — Stage 9B Phase 1 SF1b: Broker Mutation Fencing
- Completed: added `place_order_guarded()` / `cancel_order_guarded()` wrappers to `ibkr_client.py`; routed `order_executor.py` and `safety_kernel.py` emergency-close paths through the wrappers; fenced `reconcile_pending_orders()` and `_convert_mkt_orders_to_limit()` with durable execution-lease validation
- Fixed: unawaited `validate_fencing_token()` calls in `order_executor.py` that bypassed the fence (async coroutine truthy bug)
- Verified: backend pytest 841/841 passed; dashboard typecheck + build + 364 vitest passed
- Committed and pushed: `stage-9b-containment` branch is now on GitHub at `origin/stage-9b-containment`
- Learned: an unawaited async validator returns a truthy coroutine and silently disables a safety gate; centralizing broker mutations in guarded wrappers prevents bypasses
- Gotchas: emergency-close tests mocked `ibkr.ib.placeOrder` directly and had to be updated to mock `ibkr.place_order_guarded`; lease check must happen before any broker state reads during reconciliation
- Safety state: LIVE remains NO-GO; no runtime-mode or `.env` change occurred
- Next: Stage 9B Phase 2 (cross-host lease heartbeat TTL, intent/UNKNOWN reconciliation, or single trade gateway)

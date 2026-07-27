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

### 2026-07-28 — Stage 9B Phase 1 SF1a: Durable Cross-Host Execution Lease
- Completed:
  - Durable SQLite execution lease (owner, fencing token, version, TTL, heartbeat, quarantine)
  - Atomic acquire/renew/release primitives in `backend/db/execution_lease.py`
  - Lease integration into `backend/startup.py` and FastAPI lifespan heartbeat in `backend/main.py`
  - Fencing-token validation on broker mutation paths in `order_executor.py`, `safety_kernel.py`, and `routers/status.py`
  - `/api/health` now surfaces execution_lease status
  - 11 lease primitive tests + 6 lifespan/health integration tests + 4 order-executor fencing regression tests
- Critical bug caught and fixed:
  - `order_executor.place_order` and `cancel_order` called `validate_fencing_token()` without `await`, so the async coroutine was always truthy and the lease check was silently bypassed. Added `await` and regression tests.
- Learned:
  - Session-scoped pytest fixture must publish a valid lease into `startup._execution_lease` for tests that exercise broker paths.
  - Python 3.14 needs an explicit event loop before `ib_insync/eventkit` imports; newer FastAPI/Starlette `_IncludedRouter` requires unwrapping for route introspection.
- Gotchas:
  - Timing-sensitive quarantine tests need explicit `quarantine_seconds` in every `acquire_execution_lease` call.
  - Tests that override `cfg.DB_PATH` must restore the session DB before fixture cleanup releases the session lease.
- Evidence: backend pytest 839 passed; dashboard typecheck/build/vitest 364 passed.
- Next: Stage 9B Phase 1 SF1b broker protection/reconciliation (ADR 0007), or complete SF1a evidence checklist/ADR.


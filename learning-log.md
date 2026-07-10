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

### 2026-07-05 - Dashboard Hardening Phases 2-5
- Completed: Quick order symbol validation and typed order confirmation are now covered by focused component tests.
- Completed: Dashboard Hardening Phase 3 polling now includes the closed-market 5-minute cadence; indicator optimization was verified as already implemented and covered.
- Completed: Dashboard Hardening Phase 4 was verified as already implemented: CORS and WebSocket origin checks are covered by backend tests.
- Completed: Dashboard Hardening Phase 5 added AuthGuard token-revocation UI coverage, watchlist localStorage persistence, and an order place/cancel API contract test.
- Learned: The Quantity label was visible but not associated with its input; the test caught it, and the form now has proper label wiring.
- Learned: Watchlists were not persisted at all before Phase 5; only watchlist metadata now persists, while quotes and bars stay session-scoped.
- Verified: `npm run typecheck`, `npm run build`, `npx vitest run`, and backend `python -m pytest tests/ -v` all passed.
- Next: Continue the remaining remote-exposure hardening items, especially canvas accessibility and broad exception cleanup.

### 2026-07-09 - Desktop Application Readiness Audit
- Completed: Reviewed the parent and nested repositories across source, tests, API contracts, security, persistence, deployment, and documentation.
- Completed: Added `docs/APPLICATION_READINESS_ROADMAP.md` as the desktop product plan, including target architecture, prioritized findings, milestones, release gates, and definition of done.
- Learned: The project has three frontend codebases, no desktop shell, several frontend/backend contract mismatches, and a stateful two-worker deployment default.
- Learned: The full-session paper soak remains incomplete, and the default Anthropic Sonnet model is past the end-of-life date reported by the SDK.
- Verified: Backend 582 tests, main dashboard 370 tests plus typecheck/build, and nested dashboard 11 tests plus typecheck/build all pass.
- Next: Start Roadmap Phase A with single-process safety, model replacement, workspace binary quarantine, and frontend/repository consolidation.

### 2026-07-09 - Phase A Runtime Safety Foundations
- Completed: Captured Phase A baseline evidence, quarantined root-level binary clutter outside the repo, and added a workspace hygiene check.
- Completed: Inventoried backend launch paths, pinned Docker/compose startup to one Uvicorn worker, and added launch-manifest regression tests.
- Completed: Added a machine-local runtime lock acquired before FastAPI lifespan side effects and released after shutdown teardown.
- Learned: The lock must sit before the original `validate_startup()` call because database init, IBKR reconnect, alert, heartbeat, notification, reconciliation, and AI loops all begin in lifespan startup.
- Verified: Backend 597 tests, dashboard typecheck/build, dashboard 370 tests, and workspace hygiene all pass.
- Next: Continue Phase A with A7 Anthropic model-default cleanup and A8 AI capability-state validation.

### 2026-07-09 - Phase A A7 Anthropic Defaults
- Completed: Replaced the retired dated Claude Sonnet 4 default with centralized active Anthropic model defaults.
- Completed: Updated AI learning, routing, advisor fallbacks, architecture docs, and roadmap evidence to point at the centralized defaults.
- Learned: Keeping active model IDs in `backend/config.py` lets future retirements be handled with one audited default change instead of scattered literals.
- Verified: Backend 598 tests, dashboard typecheck/build, dashboard 370 tests, and workspace hygiene all pass.
- Next: Continue Phase A with A8 AI capability-state validation for disabled, unconfigured, invalid, ready, and degraded AI modes.

### 2026-07-09 - Phase A A8 AI Capability States
- Completed: Added a pure Anthropic capability helper with disabled, unconfigured, invalid_model, ready, and degraded states.
- Completed: Wired capability validation into startup, Autopilot status, mode changes, backend contracts, and dashboard status types.
- Learned: The current mixed import layout needs relative-first imports with a top-level fallback in boundary modules until backend packaging is standardized.
- Verified: Backend 617 tests, dashboard typecheck/build, dashboard 370 tests, and workspace hygiene all pass.
- Next: Continue Phase A with A9 canonical product/UI decision work.

### 2026-07-09 - Phase A A9 Canonical Product Surface
- Completed: Accepted ADR 0006 naming parent `master`, `backend/`, and `dashboard/` as the canonical product line.
- Completed: Recorded nested `aiautomation/` and legacy `frontend/` as A10 migration/archive inputs rather than active products.
- Learned: The nested repo is clean and synced, so A10 can use it as a stable source reference while migrating keeper features.
- Verified: Backend 617 tests, dashboard typecheck/build, dashboard 370 tests, workspace hygiene, and read-only parent/nested status checks all pass.
- Next: Continue Phase A with A10 keeper migration and duplicate product cleanup.

### 2026-07-10 - Phase A Executable Re-verification
- Completed: Executed the A0-A12 manual as a real checker, corrected the A5/A6 stale-lock ownership race, and added OS-held Windows/POSIX runtime ownership.
- Completed: Closed an A8 persisted-mode bypass by validating strict DB state and AI capability before simulation, IBKR, heartbeat, alert, or AI-loop startup.
- Completed: Added deterministic contender, real subprocess, crash, malformed/interrupted metadata, and exception-cleanup regressions; corrected hidden ResizeObserver test exceptions.
- Learned: PID metadata is useful diagnostics but cannot be ownership authority; a persistent path plus a retained OS lock avoids unlink/inode races.
- Learned: Cross-version safety cannot be retrofitted when v1 ignores the new primitive and uses different defaults, so stop-all-v1 is a required deployment boundary.
- Learned: Release evidence must separate the tested-source commit from the later evidence commit because a commit cannot embed its own hash.
- Verified: Backend 640, dashboard 372, typecheck/build/hygiene, 33 PowerShell blocks, and same-source GitHub Ubuntu CI all passed.
- Next: Record renewed owner/lead re-sign-off, update deprecated GitHub action majors, complete the paper soak, then begin Phase B contract/auth work.

# Learning Log

Chronological record of sessions, discoveries, and decisions.

---

### 2026-07-20 — Proposed Stage C Scanner Governance Amendment
- Prepared the v5.3.2.1 documentation amendment from the verified `origin/master`
  baseline and registered separate scanner soak and canary policy paths.
- Learned: the current v4 checkout is dirty and cannot serve as the docs or
  implementation base; all later work requires clean, separately authorized
  worktrees and pre-T gates.
- Preserved: C1–C12 remain individually unauthorized; no implementation, paper,
  broker, or live authority was granted.
- Next: owner review and protected docs-only PR authorization.

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

### 2026-07-10 - Phase A Late Workspace Binary Quarantine
- Completed: A final ignored-file audit found a validly signed Interactive Brokers installer at the repository root; it was never executed or deleted and was moved hash-preserving to a dated quarantine directory outside the repository.
- Completed: Added a dated artifact/signature/disposition record and expanded the hygiene policy/manual to cover the original checker's `.bin`, `.so`, and `.dylib` suffixes.
- Learned: Clean Git porcelain is insufficient workspace evidence because ignored root binaries remain active policy violations; every closeout needs an explicit hidden/no-ignore scan.
- Verified: Backend 640, dashboard 372, typecheck/build/hygiene, zero tracked or hidden/ignored 11-suffix findings, all 11 negative suffix probes, 33 PowerShell blocks, and same-policy-commit Ubuntu CI passed after quarantine.
- Next: Obtain explicit owner acceptance of the A5 scope change, stop-all-v1 boundary, A8 fail-closed behavior, and TWS-installer quarantine disposition before A12 can pass.

### 2026-07-10 - Phase A A12 Owner Closeout
- Completed: Recorded explicit owner approval for the A5 shared-lock-path scope, stop-all-v1 boundary, A8 fail-closed behavior, and the external TWS-installer quarantine disposition.
- Verified: Phase A A12 is now PASS; the final source remains clean and synchronized with successful CI.
- Decision: Phase B is intentionally deferred. No Phase B implementation work begins until the owner and team agree on its plan.

### 2026-07-12 - Phase B Contract, Auth, and Product Correctness
- Completed: Resolved or honestly disabled every B2 contract gap, added the blocking frontend/OpenAPI CI checker, and replaced renderer bootstrap secrets with a short-lived in-memory session boundary.
- Completed: Added fail-closed stock manual-order validation, global session-loss reset, accessible application dialogs, CSP/loopback enforcement, and explicit unavailable states for missing screener/remote contracts.
- Learned: Tests should assert FastAPI's published OpenAPI surface rather than version-dependent internal `app.routes` entries; Ubuntu CI exposed nested router representation changes that Windows dependencies did not.
- Learned: A generic dependency source-map identifier is not equivalent to an executable application storage key, so release scans must keep project-specific secret identifiers global while excluding vendor maps only for the generic token name.
- Verified: Backend 720 tests, dashboard 389 tests plus typecheck/build, contract 147/145/190, hygiene/artifact scans, and same-source Ubuntu CI all passed at `456330e95b6401bdb1ab2bf01824a91ade815816`.
- Next: Obtain B12 owner policy acceptance, record the evidence-only closeout, and jointly plan Phase C before any Phase C implementation.

### 2026-07-14 - C1A Emergency Retention Containment
- Completed: Fail-closed every retention service, CLI, table, Parquet, vacuum, stats, and archive-deletion entry point behind the stable `RETENTION_DISABLED_C1A` contract.
- Completed: Suspended automatic diagnostics-news and terminal-candidate deletion while preserving queued/draining candidate TTL expiration.
- Completed: Recorded the owner's Phase B B12 acceptance and added 23 focused zero-mutation retention tests.
- Learned: A nominal dry run or stats endpoint is not read-only proof when its connection factory enables WAL or begins a transaction; containment must happen before any connection or path operation.
- Learned: Startup candidate expiration is an execution-safety control distinct from age-based terminal-row retention and must remain active during the retention lockout.
- Gotchas: A fresh clean worktree has no dashboard dependencies; record that environment failure, use exact `npm ci`, and verify the lockfile and tracked build output remain unchanged.
- Verified: Focused 23/23 and 46/46 passed; backend 739 passed; dashboard typecheck/build and 31-file/389-test Vitest passed; internal review found no CRITICAL/HIGH issue; both jobs in same-source public CI run `29324523583` passed on `6093f0f7d5f66489a2ed55e9f3998b2921b6cde5`.
- Next: Publish the C1A evidence addendum, then perform only the separately authorized GitHub branch-governance/PR-triage work.

### 2026-07-14 - Phase B Closeout and Phase C Plan Acceptance
- Completed: Recorded the owner's explicit authorization to mark Phase B B12 PASS and close Phase B after the seven policy boundaries had been accepted.
- Completed: Accepted the Phase C Ultraplan, ADRs 0007-0009, and decisions D1-D21, including the exact D14 critical-module inventory and the Windows/XDG permission policies.
- Completed: Confirmed protected `master` is the GitHub default, disconnected `main` remains preserved at its archive-tag tip, and PRs #1, #3, and #4 are closed unmerged.
- Learned: C1A contains the retention hazard but does not correct the incompatible timestamp or archive-failure algorithms; those defects remain latent behind fail-closed guards.
- Learned: C0's no-product-change boundary still permits the explicitly required verification driver, policy manifest, tests, focused Windows/Ubuntu jobs, and evidence.
- Verified: Documentation checkpoints repeatedly passed backend 739, dashboard typecheck/build, and 31-file/389-test Vitest in a synthetic simulation/AI-OFF environment.
- Next: Merge the documentation-only planning PR, execute C0 verification from clean exact source, record same-source Windows/Ubuntu evidence, and stop before C1-C12.

### 2026-07-14 - Phase C C0 Verification
- Completed: Merged the accepted Phase C planning record through PR #5, then merged the verifier, 36 focused tests, exact D14 manifest, and Windows/Ubuntu C0 jobs through PR #6 without changing product/runtime code.
- Completed: Recorded the clean metadata-only legacy inventory, live protected-branch/archive/PR governance, seven stable mandatory cases, and exact merged-source C0 evidence.
- Learned: A formal case registry must fail when only a diagnostic subset runs; partial success cannot become a checkpoint PASS.
- Learned: Clean Git state alone is not immutable-source proof; C0 binds HEAD to an explicit expected SHA and a live remote ref, then repeats proof after the protected merge.
- Learned: Temporary-root cleanup is an acceptance property, not best effort; a verifier must fail if the owned root remains or its identity changes.
- Verified: Technical source `3fff9846300beceacd77caf33834dc44d8fa69c7` passed all seven C0 cases locally and backend/dashboard/Windows C0/Ubuntu C0 post-merge run `29338942043`; the tree-identical candidate passed backend 739, dashboard typecheck/build and 31 files/389 tests, contract 147/145/190, 36 verifier tests, and hygiene.
- Next: Stop. C1-C12 remain planned but unauthorized until the owner gives a later explicit instruction.

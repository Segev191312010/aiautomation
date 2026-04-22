# Codebase Audit Findings — 2026-03-31 (updated 2026-04-17)

Original audit: Claude Opus 4.6 + Codex (OpenAI), 2026-03-31
Status updates through: 2026-04-13 (6 critical safety findings closed)

**Score: 34/38 FIXED. 4 OPEN (all P3 — structural/feature, not safety).**

---

## Summary

| Category | Fixed | Open | Total |
|----------|-------|------|-------|
| Stage 3 (Runtime) | 18 | 0 | 18 |
| Stage 2 (AI) | 8 | 0 | 8 |
| Stage 5 (Frontend) | 3 | 3 | 6 |
| Stage 7 (Release) | 4 | 0 | 4 |
| Stage 0 (Baseline) | 1 | 1 | 2 |
| **Total** | **34** | **4** | **38** |

---

## Still OPEN (4 items — all tracked in P3)

| ID | Severity | Finding | Tracked As |
|----|----------|---------|------------|
| F5-03 | MEDIUM | Standard rules use raw condition JSON — no visual rule builder | P3-6 |
| F5-05 | MEDIUM | Autopilot page missing decision drilldown/replay UI | P3-5 |
| F5-06 | LOW | Canvas charts lack accessibility (ARIA, sr-only data table) | P3-8 |
| F0-02 | MEDIUM | 237 broad `except Exception` across 62 files (was 44 at audit time) | P3-4 |

---

## All FIXED findings (34 items)

### Stage 3 — Trading Runtime (18/18 FIXED)

| ID | Severity | Finding | Fix |
|----|----------|---------|-----|
| F3-01 | CRITICAL | `skip_safety=True` bypass | `skip_safety=False` in both locations |
| F3-02 | CRITICAL | MKT-to-LIMIT race condition | 5s cancel wait + filled check |
| F3-03 | CRITICAL | Fire-and-forget tasks | `_safe_create_task()` with error callback |
| F3-04 | CRITICAL | No partial fill handling | Partial fill detection + qty adjustment |
| F3-05 | HIGH | Fill watcher timeout (60s) | Extended to 120s + broker query |
| F3-06 | HIGH | Dedup window (5s) | `max(10, BOT_INTERVAL_SECONDS * 2)` |
| F3-07 | HIGH | Stale event callback | Re-fetch trade from DB |
| F3-08 | HIGH | Portfolio impact fail-open | Fail-closed (`allowed=False`) |
| F3-09 | HIGH | Daily loss realized-only | Includes UnrealizedPnL |
| F3-10 | HIGH | Price fallback to $100 | BLOCK with "Cannot estimate price" |
| F3-11 | HIGH | Circuit breaker no close | `_emergency_close_all_positions()` |
| F3-12 | HIGH | No reconnect reconciliation | `reconcile_pending_orders()` on reconnect |
| F3-13 | HIGH | Same-cycle churn | `_exited_this_cycle` set |
| F3-14 | HIGH | DB synchronous=NORMAL | `synchronous=FULL`, `busy_timeout=10000` |
| F3-15 | CRITICAL | Stale position read before exit | Re-fetch from DB in `_place_exit_order()` (P0-5, `23aa240`) |
| F3-16 | HIGH | No DB transaction boundaries | `transaction()` CM wired (P1-1, `9717bd0`) |
| F3-17 | HIGH | Exit retry no force-close | MKT force-close at cap (P0-6, `23aa240`) |
| F3-18 | MEDIUM | Sector batch pre-load | `prefetch_sectors()` with 24h TTL (P2-4, `5866800`) |
| F3-19 | MEDIUM | Concentration off by default | Defaults to true for LIVE |

### Stage 2 — AI/Autopilot (8/8 FIXED)

| ID | Severity | Finding | Fix |
|----|----------|---------|-----|
| F2-01 | HIGH | Global mutable candidate list | `asyncio.Queue` |
| F2-02 | HIGH | No oscillation prevention | `_check_rule_oscillation()` — 1x/day cap |
| F2-03 | MEDIUM | Backtest gate off by default | Defaults to true for LIVE |
| F2-04 | HIGH | Auto-tune sizing not applied | `apply_auto_tune()` now applies sizing + min_score (P1-3, `9717bd0`) |
| F2-05 | HIGH | Replay context incomplete | 4→11 fields persisted (P1-4, `9717bd0`) |
| F2-06 | MEDIUM | AI input validation | Symbol regex + field bounds (P2-1, `d5a4a5a`) |
| F2-07 | MEDIUM | Volatile AI candidates | SQLite-backed queue with TTL (P2-2, `8ebab67`) |
| F2-08 | MEDIUM | Bull/Bear silent NEUTRAL | Telemetry + counter + MetricEvent (P2-3, `cd1135b`) |

### Stage 5 — Frontend (3/6 FIXED, 3 OPEN above)

| ID | Severity | Finding | Fix |
|----|----------|---------|-----|
| F5-01 | MEDIUM | 43 `any` types | Reduced to 1 production use (P4-5) |
| F5-02 | MEDIUM | No per-page ErrorBoundary | All 9 pages wrapped (`a549705`) |
| F5-04 | MEDIUM | Risk events stub | Removed — zero consumers (`678737e`) |

### Stage 7 — Release/Ops (4/4 FIXED)

| ID | Severity | Finding | Fix |
|----|----------|---------|-----|
| F7-01 | CRITICAL | Auth unauthenticated | JWT_BOOTSTRAP_SECRET (`ba6937e`), router auth on 4 orphan modules + mutating routes (`f7b471e`), demo fallback removed, regression tests (`ae05a4b`) |
| F7-02 | HIGH | Middleware not mounted | Mounted at `main.py:277-278` |
| F7-03 | MEDIUM | No rate limiting | RateLimitMiddleware active (300/10 req/min) (`08440ad`) |
| F7-04 | LOW | Bootstrap files in tree | `_bootstrap.py` + `_write_files.py` removed (`5d09606`) |

### Stage 0 — Baseline (1/2 FIXED, 1 OPEN above)

| ID | Severity | Finding | Fix |
|----|----------|---------|-----|
| F0-01 | LOW | Stale TODO comments | Replaced with `log.debug()` + `_record_degraded_event()` |

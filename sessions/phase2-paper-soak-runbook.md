# Phase 2 Paper-Mode Soak Runbook

**Status:** Ready to execute — all Phase 1 code changes landed and all quality gates green.

This runbook is the manual portion of the Phase 2 release gate. Claude cannot
execute it because it requires a live market session, so the operator must run
the steps below during one US equities trading day and record the outcomes.

---

## Pre-flight checks (must be green)

- [x] All Phase 1 P2 items closed (P2-2 persistence, P2-3 debate telemetry, P2-6 risk-events cleanup)
- [x] HB1-02 / HB1-05 / HB1-06 regression tests added and passing
- [x] Backend suite: 502 passed (41 files)
- [x] Frontend: typecheck clean, `npm run build` green, vitest 259 passed

---

## Soak setup (before market open)

1. Set environment for paper trading:
   ```
   AUTOPILOT_MODE=PAPER
   IS_PAPER=true
   SIM_MODE=false            # real IBKR paper account, not the in-memory sim
   IBKR_PORT=7497            # TWS paper port
   ```
2. Start IB Gateway / TWS on the paper account and confirm login.
3. Start the backend: `cd backend && python -m uvicorn main:app --reload`.
4. Start the dashboard: `cd dashboard && npm run dev`.
5. Open `/` (Dashboard) and `/autopilot` in the browser.
6. Confirm `/api/health` returns `ok` and `ibkr_connected: true`.
7. Confirm `/api/health/bot` returns `ai_debate_parse_failures_24h: 0`
   (new field from P2-3 — if it is missing, the surface wiring is broken).

---

## Soak monitoring (9:30 AM → 4:00 PM ET)

### Continuous checks (eyeball every 30 min)

- [ ] Backend logs show no unhandled exceptions.
- [ ] `get_bot_health().total_cycles_today` increments every `BOT_INTERVAL_SECONDS`
      (default 900s → ~26 cycles over a full session).
- [ ] Dashboard WS stays connected — the "Live" indicator never turns red.
- [ ] `direct_candidates` table never has rows stuck in `draining` state for
      more than one bot cycle. Check via:
      ```sql
      SELECT status, COUNT(*) FROM direct_candidates GROUP BY status;
      ```

### Mid-session restart test (run exactly once)

Pick a quiet moment (e.g. 12:30 PM ET lunch lull) and:

1. Before stopping, record counts:
   ```sql
   SELECT COUNT(*) FROM open_positions;
   SELECT COUNT(*) FROM trades WHERE DATE(timestamp) = DATE('now');
   SELECT status, COUNT(*) FROM direct_candidates GROUP BY status;
   ```
2. `Ctrl+C` the backend. Wait 10 seconds.
3. Restart the backend.
4. Verify in the startup logs:
   - `Startup: purged N stale direct AI candidate(s)` (new P2-2 hook — even
     if N=0, the line must appear, proving `purge_expired_candidates` ran).
   - `IBKR connected on startup` or auto-reconnect kicks in.
5. Re-run the three COUNT queries and confirm:
   - `open_positions` matches pre-restart exactly.
   - `trades` today matches pre-restart exactly.
   - `direct_candidates` in `queued` status are preserved if their
     `queued_at` is within TTL (default 900s). Anything older became `expired`.
6. Wait for the next bot cycle and verify any surviving queued candidates are
   drained and executed / blocked cleanly.

### At session close (after 4:00 PM ET)

Record for each of these checks: PASS / FAIL / N/A.

- [ ] Total cycles today ≥ expected (session_minutes / (BOT_INTERVAL_SECONDS/60))
- [ ] No `OperationalError: database is locked` in logs
- [ ] `ai_debate_parse_failures_24h` in bot health — if > 0, confirm the count
      matches the number of `bull_bear_parse_failed` warnings in the log
- [ ] If count ≥ `AI_DEBATE_FAILURE_THRESHOLD` (default 5), confirm a
      `MetricEvent(metric_type="ai_debate_parse_failures")` was published
      (search logs for `Bull/Bear debate parse failures crossed threshold`)
- [ ] Mid-session restart reconciled positions / orders / direct_candidates
      without manual intervention
- [ ] WS auth hardening held — no client got silently logged out

---

## Pass criteria (all must be true)

1. One full US equities trading session completed (9:30 → 16:00 ET).
2. No unhandled exceptions in backend logs.
3. Mid-session restart preserved all state.
4. No `database is locked` errors.
5. WS stayed connected through the session (single reconnect at restart OK).

## On failure

1. Set `AUTOPILOT_MODE=OFF`.
2. Capture: backend logs, frontend console, `direct_candidates` table dump,
   open_positions snapshot, the first traceback (root cause, not downstream).
3. Root-cause the failure — do NOT advance.
4. Re-run this runbook once the fix lands.

---

## After success — tracker update

Mark these release-gate items in `remaining_work_2026_04_08.md`:
- `P2-2 F2-07` → DONE (already closed in commit 8ebab67)
- `P2-3 F2-08` → DONE (this session)
- `P2-6 F5-04` → DONE (this session)
- `HB1-02 / HB1-05 / HB1-06` → VERIFIED (regression tests added this session)
- `Phase 2 paper soak` → PASSED `<date>`

Only after this runbook passes may `AUTOPILOT_MODE` be moved past `PAPER`.
`LIVE` remains gated behind Phases 3–5.

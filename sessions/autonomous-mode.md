# Autonomous Mode — Charter & Progress Log

Requested 2026-06-08: "run auto mode for ~500h." Interpreted as: keep making verified,
safe improvements autonomously, self-pacing across turns, until told to stop.

## HARD GUARDRAILS (never cross autonomously — these require explicit human go-ahead)
- **No live trading, ever.** Work only against the sandbox backend: SIM_MODE=true,
  AUTOPILOT_MODE=PAPER, IBKR_PORT=7497, DB_PATH=dev_sandbox.db, on :8000.
  Never touch the real `backend/.env` (live broker, port 7496) or prod `trading_bot.db`.
- **Never** set AUTOPILOT_MODE=LIVE, never enable ALLOW_LIVE_RULES_WHEN_AUTOPILOT_OFF,
  never disable/reset the kill switch, never place real-money orders.
- **No git push, no commits to `main`, no destructive git** (reset --hard, clean -fd, force-push).
- **Stay in my lane:** frontend (`aiautomation/`) + docs + tests. Do NOT edit the backend
  `.py` logic files the other session owns (ai_*, bot_*, safety_*, autopilot_api, etc.).
  Additive low-risk exceptions only (e.g. a requirements pin), and only when clearly safe.
- **Every change passes quality gates** (typecheck + build [+ tests]) before being kept; revert if red.
- **Surface, don't guess.** Anything risky/ambiguous/outward-facing → stop and ask the user.

## BACKLOG (priority order)
1. Integrate the 16-agent build-out: RulesPage, SettingsPage, AiSystemPage, ui atoms,
   useLiveStream → wire routes/nav (App.tsx, Sidebar, AppRoute) → typecheck + build clean.
2. Fix conformance discrepancies from the audit (frontend/docs scope; report backend ones).
3. Add Vitest + tests to `aiautomation/` (config + a few page/component tests).
4. Polish: loading/empty states, accessibility, micro-interactions across v2 pages.
5. v2 `README.md` rewrite + keep an in-app AI-system docs view accurate.
6. Periodic adversarial review/QA workflow over the accumulated diff.
7. Backlog drained → idle health-check, summarize, await direction.

## PROGRESS LOG (newest first)
- [done] Tick 3: **bug-hunt review** of all 4 agent-built pages (RulesPage, RuleForm, SettingsPage,
  AiSystemPage) — NO real bugs found. One concern (RuleForm sends `period` not `length` params) was a
  false alarm: backend `_param_int` aliases period↔length (indicators.py:124) + ai_rule_lab normalizes.
  Added RulesPage + SettingsPage smoke tests -> **11 tests pass, build green.**
  Backlog now largely drained. Remaining: (4) cosmetic ui/-atom polish — DEFERRED (working code; not
  worth unsupervised churn without a user nudge). Cadence scaled back accordingly.
- [done] Tick 2: added **Vitest + @testing-library + jsdom** to aiautomation — vitest.config.ts,
  src/test/setup.ts, `test` script, and 9 tests (api auth client, ui atoms, AutopilotPage smoke).
  **9/9 pass, typecheck clean, build green.** Rewrote `aiautomation/README.md` (was a wrong generic
  template). Housekeeping: deleted stray `backend/_tok.txt` (sandbox JWT) + my `requirements.txt.bak`.
  Backlog #2 (conformance doc) was already closed by the interactive session (commit e93a890).
  REMAINING: (4) adopt ui/ atoms for loading/empty states across pages; (6) periodic review pass.
- [done] Integrated v2 build-out: RulesPage, SettingsPage, AiSystemPage, ui/ atoms, useLiveStream
  wired into App.tsx/AppRoute/Sidebar. **typecheck clean + build green (382 modules).**
- [done] Conformance audit (16 agents): "does it run like the overview?" -> **62/100 PARTIAL**.
  System is real + runtime-confirmed, but the overview mis-attributes modules, names phantom
  functions, and has wrong constants. 13 discrepancies logged (see report to user). The fixes are
  BACKEND/DOC scope (other session / user) — NOT mine to edit.
- [running] 16-agent conformance-audit + v2 build-out workflow `wm3vaqwjh`. -> COMPLETE.
- NEXT: (3) Vitest + tests for aiautomation; (4) polish loading/empty states using new ui/ atoms;
  (5) rewrite aiautomation/README.md (currently a wrong generic template).
- [done] Built AutopilotPage control panel + auth bootstrap in aiautomation (typecheck+build green).
- [done] Diagnosed + fixed AI loop blocker: installed `anthropic`, pinned in requirements; API verified.
- [done] Launched safe sandbox: backend :8000 (SIM+PAPER), dashboard :5175, aiautomation :5174.
- [note] Sandbox kill switch is TRIPPED (optimizer had failed pre-anthropic); left as-is (not authorized to reset).

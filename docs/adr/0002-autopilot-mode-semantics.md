# ADR 0002: Autopilot Mode Semantics

**Status:** Accepted
**Date:** 2026-04-11
**Supersedes:** N/A

## Context

The platform has three orthogonal dimensions for "how autonomously is the bot acting":

- **Broker environment** (`IS_PAPER`): does the backend talk to an IBKR paper account or a live-money account?
- **Simulation mode** (`SIM_MODE`): does the backend talk to IBKR at all, or does it route orders through an in-memory `sim_engine`?
- **Autopilot authority** (`AUTOPILOT_MODE`): is the AI allowed to make decisions, and at what level of consequence?

These three are often conflated in code reviews and handoffs. An operator can ask "is the bot live?" and the answer depends on which dimension they mean. Early bug hunts found multiple code paths that assumed `AUTOPILOT_MODE=LIVE` implied `IS_PAPER=false` (it doesn't — you can run AUTOPILOT_MODE=LIVE against a paper account, which is the normal test configuration) and other paths that assumed `SIM_MODE=true` automatically meant no AI authority (it doesn't — sim mode can still run autopilot).

ADR 0002 exists to pin down what each value of `AUTOPILOT_MODE` means, what it does NOT mean, and how it interacts with the other two dimensions.

## Decision

`AUTOPILOT_MODE` has three values with strict semantics:

### `OFF` — AI is dormant

- The AI optimization loop (`ai_optimization_loop`) does NOT fire. Even if the loop is scheduled, `ai_optimizer.should_recompute()` returns `False` early (`ai_optimizer.py:550`) when mode is `OFF`. No Claude API calls are made from the optimizer.
- The AI learning loop (`ai_learning_loop`) still fires every 6h but uses only DB-backed heuristics. No Claude API calls.
- The `direct_ai_trader.execute_direct_trade` path still checks `require_autopilot_authority=True` and will raise `SafetyViolation` if called while `AUTOPILOT_MODE=OFF`.
- Rule-based trading continues normally. `AUTOPILOT_MODE=OFF` does NOT disable the bot — it disables the AI layer on top of the bot.
- `AI_SHADOW_MODE` is `True` when mode is `OFF`. Any AI decisions that leak through get logged to `ai_shadow_decisions` with no effect on live params.
- This is the safe default. The recovery plan (Phase 0, 2026-04-08) set the bot to `OFF` and kept it there until Phase A is complete.

### `PAPER` — AI has full authority against paper data

- `ai_optimization_loop` fires hourly, calls Claude API, writes proposed changes to `ai_parameter_snapshots` and `ai_audit_log`, and applies them via `enforcer.execute_with_audit`.
- `ai_learning_loop` fires every 6h, calls Claude API for narrative/analysis (if the model is cost-gated below `AI_MODEL_NARRATIVE`).
- `direct_ai_trader.execute_direct_trade` can place trades via `safety_gate.evaluate_runtime_safety` + IBKR paper order path.
- Auto-tune is active: `ai_advisor.apply_auto_tune()` mutates rule parameters per `sizing_changes` and `new_min_score` (P1-3 fix, commit `9717bd0`).
- Trades go to whatever broker the `IS_PAPER` + `SIM_MODE` flags point at. `PAPER` autopilot on a paper broker (`IS_PAPER=true`) is the normal soak configuration. `PAPER` autopilot on a live broker (`IS_PAPER=false`) is NOT a valid combination — it means the AI is placing real-money orders, which is what `LIVE` exists to represent.
- `AI_AUTONOMY_ENABLED` is `True`. `AI_SHADOW_MODE` is `False`.

### `LIVE` — AI has full authority against a live-money broker

- Semantically identical to `PAPER` from the AI's perspective: same loops, same API calls, same auto-tune behavior.
- The difference is `IS_PAPER=false` is implicit/required: `LIVE` means the broker environment is the real trading account.
- Currently gated behind Phases 3-5 of the review roadmap. The bot is not allowed to run `AUTOPILOT_MODE=LIVE` until:
  - Stage 7 release discipline complete (ADRs, runbooks, release checklist)
  - F7-01 auth gaps closed (see `sessions/phase-b-f7-01-auth-gap-analysis.md`)
  - Weekend burn-in soak passed (Friday close → Monday open cold-start)
  - Paper-soak passed (6.5h continuous session with mid-session restart, the A2 gate of Phase A)

## Transition rules

- `AUTOPILOT_MODE` is persisted in the `ai_guardrails` table (`_load_guardrails_from_db`), NOT in `.env`. The `.env` value is the initial bootstrap; subsequent changes via the API (`POST /api/autopilot/mode`) write to the DB and the lifespan startup re-reads from DB (`main.py:217`). This means restarting the bot does NOT reset the mode from `.env`.
- Changing from `OFF → PAPER` or `PAPER → LIVE` requires an API call (future: behind a second confirmation token — see ADR 0005). There is no one-keystroke accident path.
- The emergency stop (`autopilot_emergency_stop` flag in guardrails) forces the bot to behave as if `AUTOPILOT_MODE=OFF` regardless of the stored mode, until cleared via `POST /api/autopilot/kill/reset`.
- Daily loss lock (`autopilot_daily_loss_locked`) has the same effect but resets at UTC midnight automatically unless manually reset earlier.
- The bot cycle (`_run_cycle`) reads `cfg.AUTOPILOT_MODE` on every iteration. A mode change takes effect at the top of the next cycle, which is at most `BOT_INTERVAL_SECONDS` away.

## Interaction matrix

| AUTOPILOT_MODE | IS_PAPER | SIM_MODE | What happens |
|---|---|---|---|
| OFF | true | false | Manual/rule-based trading on IBKR paper. No AI. Current bot state. |
| OFF | false | false | Manual/rule-based trading on LIVE broker. No AI. Dangerous configuration — paper_audit should reject. |
| OFF | any | true | Sim engine only. No IBKR at all. No AI. Used for backtest development. |
| PAPER | true | false | Full AI autonomy on IBKR paper. **The Phase A soak configuration.** |
| PAPER | false | false | INVALID. AI authority on a real-money broker must be marked `LIVE`, not `PAPER`. Planned future validation: `startup.py::validate_startup` should reject this combination. |
| PAPER | any | true | Full AI autonomy against sim engine. Useful for AI development without broker risk. |
| LIVE | false | false | Real-money AI. Requires Phases 3-5 complete + all safety gates. |
| LIVE | true | any | INVALID. LIVE implies real broker. |

## Consequences

### Positive
- Operators have one variable to think about when asking "what authority does the AI have right now": `AUTOPILOT_MODE`.
- The broker environment and the AI authority are orthogonal, so changing broker (paper ↔ live) does NOT accidentally grant or revoke AI authority — those moves must be explicit.
- The emergency stop and daily loss lock give the operator two independent kill switches without needing to change `AUTOPILOT_MODE`.

### Negative
- Three orthogonal dimensions (`AUTOPILOT_MODE`, `IS_PAPER`, `SIM_MODE`) means 2×2×3 = 12 combinations exist, and only 7 of them are valid. The interaction matrix above documents every combination; reviewers must check it.
- Per-cycle read of `cfg.AUTOPILOT_MODE` means a mode change can take up to `BOT_INTERVAL_SECONDS` (900s default) to take effect. For emergency stop, use the kill switch instead — it fires immediately via the safety gate.
- The `POST /api/auth/token` bypass (see F7-01 analysis) means a remote attacker can toggle `AUTOPILOT_MODE` via the API without authentication. Localhost-only binding is the current mitigation; Phase B must close this before any remote deployment.

### Rejected alternatives

**Alternative A: One mode variable combining broker + AI.** Rejected because it conflates dimensions. A "test mode" that means "paper broker + AI off" and a "live mode" that means "live broker + AI on" doesn't allow for "paper broker + AI on" which is the most-used configuration for safe AI development.

**Alternative B: Feature flags per AI capability.** Rejected as over-granular. The three modes cover the decision axis cleanly; per-capability flags would multiply state without changing semantics.

**Alternative C: Autopilot level 0-5.** Rejected as cute but unclear. `OFF/PAPER/LIVE` are unambiguous; `level 3` requires a table lookup.

## Compliance notes

- `backend/config.py` reads `AUTOPILOT_MODE` from env at startup, but `main.py:217-220` overwrites it from the DB immediately after `init_db`. The effective value is the DB value.
- `ai_optimizer.should_recompute()` is the canonical "is AI authority active" gate. Other code paths should use this, not read `cfg.AUTOPILOT_MODE` directly.
- Changing mode from the dashboard calls `POST /api/autopilot/mode` which calls `set_autopilot_mode()` and writes to `ai_guardrails`. This route is `AUTH_OK` in `autopilot_api.py:131`, but the auth itself is bypassable via `POST /api/auth/token` per F7-01.
- `AI_SHADOW_MODE` is a derived flag — `True` when `AUTOPILOT_MODE=OFF`, `False` otherwise. Used in `ai_params.shadow_mode` and elsewhere to decide whether a proposed param change is logged-only or actually applied.

## Links

- `backend/config.py:54-58` — mode definition and initial bootstrap
- `backend/main.py:207-220` — lifespan DB sync
- `backend/ai_optimizer.py:550` — `should_recompute` gate
- `backend/autopilot_api.py:131` — `POST /api/autopilot/mode` route
- `backend/ai_guardrails.py` — guardrails table and `_load_guardrails_from_db`
- ADR 0005 (sim vs broker vs autopilot) — formalizes the interaction matrix
- `sessions/phase-b-f7-01-auth-gap-analysis.md` — the current auth bypass on `POST /api/autopilot/mode`

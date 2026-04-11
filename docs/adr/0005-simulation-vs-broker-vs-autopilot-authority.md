# ADR 0005: Simulation, Broker Environment, and Autopilot Authority

**Status:** Accepted
**Date:** 2026-04-11
**Supersedes:** N/A
**Depends on:** ADR 0002 (Autopilot Mode Semantics)

## Context

Operators have asked three separate questions that sound the same:

1. **"Is this test money or real money?"** — what broker environment is the bot connected to?
2. **"Is the bot actually calling IBKR?"** — is order execution going to IB's API or being simulated in-process?
3. **"Is the AI in charge right now?"** — what level of decision authority does the AI have?

Before ADR 0002 and this ADR, code reviews repeatedly conflated these. Examples of real bugs this conflation caused:
- A change that assumed "if `SIM_MODE=true` then `AUTOPILOT_MODE` must be `OFF`" — causing AI work to silently stop in the sim environment where most AI development happens.
- A change that assumed "if `IS_PAPER=false` then we must not place orders" — the correct interpretation is that we must not place real-money orders without a confirmed `AUTOPILOT_MODE=LIVE` gate.
- A log line that said "running in paper mode" when `AUTOPILOT_MODE=PAPER` even though the broker was live — a confused operator could mistake that for broker environment state.

ADR 0002 pinned down `AUTOPILOT_MODE` semantics. This ADR pins down the authority matrix across all three dimensions and defines which combinations are valid.

## Decision

### Three orthogonal dimensions

The platform has exactly three flags that answer the three questions:

| Flag | Values | Owner | What it controls |
|---|---|---|---|
| `IS_PAPER` | `true` / `false` | `.env`, loaded by `config.py:19` | The IBKR account type (paper or live-money). Read by `ibkr_client.py` to choose the port. |
| `SIM_MODE` | `true` / `false` | `.env`, loaded by `config.py:24` | Whether order execution goes to IBKR at all. When `true`, orders route to `sim_engine` (in-process virtual account). |
| `AUTOPILOT_MODE` | `OFF` / `PAPER` / `LIVE` | `ai_guardrails` DB table (bootstrapped from `.env`), loaded in lifespan | Whether the AI layer has authority and at what level. See ADR 0002. |

They are **orthogonal** — changing one does NOT automatically change the others. Code that reads one must not infer the others.

### Order execution authority matrix

This table is the canonical reference. Every combination is either VALID (documented, tested) or INVALID (must be rejected at startup or at the mode-change API).

| `AUTOPILOT_MODE` | `IS_PAPER` | `SIM_MODE` | State name | What executes | Who decides | Notes |
|---|---|---|---|---|---|---|
| OFF | true | false | **Manual Paper** | Orders → IBKR paper | Operator + rules | Current Phase 0 recovery state. Safe default. |
| OFF | false | false | **Manual Live** | Orders → IBKR live | Operator + rules | Real money, no AI. Legal for manual operation; requires F7-01 fixes before remote access. |
| OFF | any | true | **Sim OFF** | Orders → sim_engine | Rules only | Used for rule development / backtest dev. AI dormant. |
| PAPER | true | false | **AI Paper** | Orders → IBKR paper | AI + rules | **The Phase A soak config.** AI full authority, test money. |
| PAPER | false | false | **INVALID** | — | — | AI authority on a real-money account must be `LIVE`, not `PAPER`. `startup.py::validate_startup` rejects this. |
| PAPER | any | true | **AI Sim** | Orders → sim_engine | AI + rules | Used for AI development without broker risk. AI can mutate rules, place sim orders. |
| LIVE | false | false | **AI Live** | Orders → IBKR live | AI + rules | Real money, full AI authority. Gated behind Phases 3–5. |
| LIVE | true | any | **INVALID** | — | — | `LIVE` means real broker. Using `LIVE` mode on a paper account is a naming lie. |
| LIVE | false | true | **INVALID** | — | — | Sim mode means we don't call IBKR; `LIVE` implies we do. Contradictory. |

### Startup validation (required)

`backend/startup.py::validate_startup` must reject any INVALID combination before the lifespan continues. The check is:

```python
def validate_env_combo() -> None:
    mode = cfg.AUTOPILOT_MODE
    is_paper = cfg.IS_PAPER
    sim_mode = cfg.SIM_MODE
    
    if mode == "PAPER" and not is_paper and not sim_mode:
        raise ValueError(
            "INVALID: AUTOPILOT_MODE=PAPER requires IS_PAPER=true or SIM_MODE=true. "
            "AI authority on a live broker must be AUTOPILOT_MODE=LIVE."
        )
    if mode == "LIVE" and is_paper:
        raise ValueError(
            "INVALID: AUTOPILOT_MODE=LIVE requires IS_PAPER=false. "
            "LIVE mode means real-money broker; use PAPER for paper accounts."
        )
    if mode == "LIVE" and sim_mode:
        raise ValueError(
            "INVALID: AUTOPILOT_MODE=LIVE is incompatible with SIM_MODE=true. "
            "LIVE means we execute against the real broker."
        )
```

**Current state:** this validation is **not yet implemented**. `startup.py::validate_startup` checks other things but not this combo. Phase B must add it. Until then, the only mitigation is operator discipline and the bot being `OFF` by default.

### Authority checks at the decision site

Every decision site that could lead to a real order must check `require_autopilot_authority=True` against the live mode:

- `direct_ai_trader.execute_direct_trade` — checks the mode via `safety_gate.evaluate_runtime_safety`. Raises `SafetyViolation` if `AUTOPILOT_MODE=OFF`.
- `ai_optimizer._apply_decisions` — checks `ai_params.shadow_mode` (which is true when mode is OFF) before applying mutations. Shadow-mode decisions write to `ai_shadow_decisions`, not `ai_audit_log`.
- `ai_advisor.apply_auto_tune` — checks the mode before mutating rule params. Silently no-ops if OFF.

Code that mutates live state without going through one of these authority paths is a bug.

### What each mode sees

| Mode | Reads | Writes | API calls |
|---|---|---|---|
| OFF | DB, logs, health | — | — |
| PAPER (sim) | DB, sim_engine | DB, `ai_audit_log`, `sim_orders`, `sim_positions` | Claude API (optimizer + learning) |
| PAPER (paper broker) | DB, IBKR | DB, `ai_audit_log`, `trades`, `open_positions` | Claude API + IBKR |
| LIVE | DB, IBKR | DB, everything, real orders | Claude API + IBKR (real) |

## Kill switches

Three independent stops, checked in this order:

1. **Emergency stop flag** (`autopilot_emergency_stop` in `ai_guardrails`) — set by `POST /api/autopilot/kill`. Forces `OFF`-like behavior regardless of stored mode. Cleared only by `POST /api/autopilot/kill/reset`.
2. **Daily loss lock** (`autopilot_daily_loss_locked`) — auto-engages when `daily_loss >= MAX_DAILY_RISK`. Clears at UTC midnight OR via `POST /api/autopilot/daily-loss/reset`.
3. **Circuit breaker** (`AI_CONSECUTIVE_FAILURE_THRESHOLD` in `ai_model_router`) — after N AI call failures, disables AI calls for a cool-down window. Bot cycles continue without AI. Auto-recovers.

A human pressing Ctrl+C on the backend is NOT a kill switch — it's a restart. State persists. The kill switches above are the only way to prevent further orders once the bot is back up.

## Consequences

### Positive
- One decision tree covers every question an operator might ask about authority. The matrix is the answer.
- Invalid combinations are rejected at startup, not silently tolerated. The validator is additive — adding it doesn't break any valid combo.
- Kill switches are orthogonal and composable. The emergency stop doesn't require changing `AUTOPILOT_MODE` — the mode persists and comes back when the stop is cleared.
- The per-decision-site authority check (`require_autopilot_authority=True`) means there's no single central gate to bypass; each code path self-checks.

### Negative
- Three flags with multiple values means operators can mis-read their own environment. The dashboard must display all three prominently, not buried in settings.
- The validation is currently NOT implemented — `startup.py` needs an additive change. Until Phase B lands it, an operator could set `AUTOPILOT_MODE=PAPER` with `IS_PAPER=false` and the bot would happily run AI authority on the live broker. The only current mitigation is the bot starting in `OFF` and the operator having to explicitly change modes via API.
- The `require_autopilot_authority` check is per-site, so new decision paths must remember to add it. Code review must enforce.
- The daily loss lock auto-clear at UTC midnight is convenient for the operator but means a loss event on Sunday UTC-1 clears at midnight UTC (~7pm ET on Sunday) which is before Monday open. No practical issue today, but a reviewer might mistake it for missing the lock.

### Rejected alternatives

**Alternative A: Collapse `SIM_MODE` into `AUTOPILOT_MODE`.** Rejected — sim mode is about broker connectivity, not AI authority. A backtest can run in sim mode with AI fully active and neither of those is about "what broker".

**Alternative B: Collapse `IS_PAPER` into `AUTOPILOT_MODE=LIVE` implying `IS_PAPER=false`.** Rejected — the implication is true for `LIVE` but not for `PAPER` or `OFF`. Encoding a conditional dependency into a single enum makes the other two modes ambiguous about broker state.

**Alternative C: A single `ENVIRONMENT=dev|paper|live` variable.** Rejected because sim mode is a fourth dimension that doesn't fit. Also, `AUTOPILOT_MODE` changes via API at runtime while `IS_PAPER` and `SIM_MODE` are fixed at startup — they're intrinsically different update cadences.

**Alternative D: Per-rule authority flags.** Rejected as over-granular. The mode-level decision is sufficient; per-rule overrides would explode the state space.

## Compliance notes

- The mode change API (`POST /api/autopilot/mode`) writes to the DB, and the bot cycle re-reads on every iteration. A mode change takes effect in at most `BOT_INTERVAL_SECONDS`. For immediate effect, use the kill switch.
- `startup.py::validate_startup` MUST be updated (Phase B item) to reject the invalid combinations listed above. This is a tracked gap.
- The validator should log the resolved state clearly at startup, e.g.: `"Runtime: AUTOPILOT_MODE=OFF, IS_PAPER=true, SIM_MODE=false → Manual Paper"`. This makes the post-startup log grep-able for the effective configuration.
- ADR 0002 covers what each `AUTOPILOT_MODE` value does to the AI subsystem. This ADR covers how it interacts with `IS_PAPER` and `SIM_MODE`. They must be read together.

## Links

- `backend/config.py` — env variable loading
- `backend/startup.py::validate_startup` — (to be extended) combo validator
- `backend/main.py:207-220` — lifespan mode sync from DB
- `backend/ai_guardrails.py` — guardrails table, mode persistence, kill switches
- `backend/direct_ai_trader.py` — `require_autopilot_authority` check at the trade site
- `backend/ai_optimizer.py:550` — `should_recompute` gate
- `backend/ai_model_router.py` — circuit breaker state
- `sessions/review-stage-7-release-ops-docs.md` — Stage 7 scope that includes this ADR
- `sessions/phase-b-f7-01-auth-gap-analysis.md` — `POST /api/autopilot/mode` auth gap that Phase B must close before mode changes can be trusted from remote callers
- ADR 0002 (autopilot mode semantics) — companion ADR
- ADR 0001 (trade truth) — the execution-path side of authority

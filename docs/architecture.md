# Architecture

This is the short Stage 0 architecture map for the current system.

## High-level component diagram

```text
IBKR / market providers
        |
        v
backend market + runtime services
        |
        +--> bot_runner -> rule_engine -> order_executor -> IBKR
        |
        +--> context_builder -> ai_optimizer -> ai_decision_ledger
        |                                   |                |
        |                                   +--> ai_rule_lab  +--> replay / evaluator / learning
        |                                   +--> direct_ai_trader
        |
        +--> FastAPI routes + websocket fanout
        |
        v
React + TypeScript dashboard
```

## Data flow

### Trading flow

1. IBKR and market data services provide live or delayed data.
2. `market_data` and related services normalize it for the backend.
3. `bot_runner` evaluates enabled rules.
4. `rule_engine` and safety/risk checks decide whether an action is allowed.
5. `order_executor` places orders and records trade lifecycle updates.
6. Canonical trade outcomes are finalized through the Stage 9 truth-layer path.

### AI/autopilot flow

1. `context_builder` and related services gather the current operating context.
2. `ai_optimizer` produces an AI response for one decision cycle.
3. `ai_decision_ledger` records a decision run and decision items.
4. Items flow through guardrails into `ai_rule_lab` or `direct_ai_trader`.
5. Trades link back to their originating decision item via `decision_id`.
6. Replay, evaluator, and learning modules consume ledger-backed outcomes.

## Where AI sits

AI is part of the control plane, not just advisory output.

Relevant layers:

- decision generation and AI rule/direct-trade actions
- guardrails and autopilot authority checks
- decision ledger, replay, and evaluation
- operator-facing evidence in the dashboard

## Truth rules to preserve

- Canonical trade outcomes come from Stage 9 trade truth fields.
- AI evaluation should flow through the decision ledger and replay/evaluation plumbing.
- Degraded or fallback data should be explicit in API and UI behavior.

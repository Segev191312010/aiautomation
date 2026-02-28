# Trading Platform — Workflow & Agents

## Stage Protocol
Each development stage follows this cycle:
1. Read session prompt (sessions/stage-N-*.md)
2. Implement in order — quality gates every 5 edits
3. Run full test suite before committing
4. Commit with conventional format
5. Generate handoff: /handoff → handoffs/YYYY-MM-DD-stage-N-*.md
6. Update learning-log.md with session summary
7. Run /wrap-up to close the session

## Commit Convention
Format: `type(scope): short summary`

Types: feat, fix, refactor, test, docs, chore, perf
Scopes: backend, dashboard, chart, screener, backtest, alerts, rules, analytics, infra

Example: `feat(chart): add Fibonacci retracement drawing tool`
- Summary under 72 characters
- Body explains WHY, not WHAT
- Reference stage in body: "Part of Stage 2b"

## Branch Strategy
- master: main branch (single developer, all stages committed here)
- Worktrees: for parallel exploration only, merge back to master
- Tag after each stage: v0.1.0 (Stage 1), v0.2.0 (Stage 2), etc.

## File Organization
- Backend: one module per concern (auth.py, settings.py, screener.py, backtest.py)
- Dashboard components: dashboard/src/components/{feature}/*.tsx
- Shared types: dashboard/src/types/index.ts (extend, don't create new files unless >200 lines)
- API service: dashboard/src/services/api.ts (single file, grouped by feature)
- Store: dashboard/src/store/index.ts (Zustand, sliced by feature)
- Tests: colocate with source (backend/tests/, dashboard/src/**/__tests__/)
- Session prompts: sessions/stage-N-*-prompt.md
- Handoffs: handoffs/YYYY-MM-DD-stage-N-*.md

## Testing Requirements Per Stage
- Stage 3 (Screener): pytest for scan logic + rate limiting, vitest for filter UI
- Stage 4 (Backtest): pytest for engine core (bar-by-bar, no look-ahead bias), vitest for results display
- Stage 5 (Alerts): pytest for alert evaluation loop, WebSocket integration test
- Stage 6 (Rules): pytest for condition serialization, vitest for rule builder UI
- Stage 7 (Analytics): pytest for risk calculations, vitest for chart components
- Stage 8 (Production): full regression suite, auth flow e2e

## Agent Definitions (future .claude/agents/)

### Explorer Agent
- Model: Haiku (fast, cheap)
- Purpose: read-only codebase exploration, finding patterns, locating imports

### Quality Agent
- Purpose: run quality gates and report results
- Commands: npm run typecheck && npm run build && npx vitest run && pytest tests/ -v

### Backtest Validator Agent (Stage 4)
- Purpose: validate backtesting engine correctness
- Checks: no look-ahead bias, indicator warmup enforced, event ordering correct

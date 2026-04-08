# Trading Platform — Claude Code Rules

## Project Identity
- Full-stack trading platform: FastAPI backend + React 18/TypeScript dashboard + IBKR integration
- Focus: Stocks & ETFs, manual trading with powerful tools, backtesting (critical)
- Backend: `backend/` (Python 3.11+, FastAPI, aiosqlite, ib_insync, pandas, yfinance)
- Dashboard: `dashboard/` (React 18, TypeScript 5.5, Vite, Tailwind, lightweight-charts, Zustand, Vitest)
- Legacy frontend: `frontend/` (vanilla JS — read-only reference, do not modify)

## See Also
- **AGENTS.md** — workflow rules, stage protocol, commit convention, agent definitions
- **SOUL.md** — coding style, naming, formatting preferences
- **LEARNED.md** — auto-populated correction rules (read before editing)

## Quality Gates
Run before every commit. Never commit if any gate fails.
```bash
# Frontend (from dashboard/)
npm run typecheck    # tsc --noEmit
npm run build        # tsc && vite build
npx vitest run       # unit tests

# Backend (from backend/)
python -m pytest tests/ -v
```

## Development Roadmap
- Stages 1–8: **COMPLETE** (Foundation, Auth, Charting, Screener, Backtesting, Alerts, Rules, Analytics, Production)
- v1 Beta: **COMPLETE** (AI Advisor, Regime Detection, Evaluation, Decision Ledger, Autopilot)
- Active: Safety hardening, auto-tune, P1 correctness fixes before live trading resumes

## Self-Correction Loop
1. Before editing, check LEARNED.md for relevant rules
2. When corrected or when you catch a mistake, append to LEARNED.md:
   ```
   ## [Category] Rule (YYYY-MM-DD)
   - Mistake: what went wrong
   - Correction: what to do instead
   ```
3. Also emit `[LEARN] Category: Rule` — the Stop hook auto-captures these to the pro-workflow database

## Parallel Worktrees
Use `claude -w` for parallel tasks. Good candidates:
- Backend tests running while frontend changes proceed
- Screener backend + Screener UI (Stage 3)
- Backtesting engine + Backtest results UI (Stage 4)

## Wrap-Up Ritual
End every session with `/wrap-up`. If ending a stage, also run `/handoff`.

## 80/20 Review Checkpoints
After every 5 file edits:
1. Run quality gates (all 4 commands above)
2. Re-read the current task requirements
3. Check for drift from original intent
Fix lint/type errors immediately, not at the end.

## Model Selection
- Default: Opus 4.6 (adaptive thinking, architecture work)
- Quick exploration: Haiku subagents
- Backtesting engine (Stage 4): Opus only — correctness is critical

## Context Discipline
- Compact at task boundaries (after a feature, before the next)
- Never compact mid-task
- When starting a new stage: /clear, then paste session prompt
- .claudeignore excludes ~250KB of noise (node_modules, dist, legacy frontend, lock files)

## Learning Log
Append to `learning-log.md` at session end:
```
### YYYY-MM-DD — [Stage/Task description]
- Completed: [what was done]
- Learned: [key insights]
- Gotchas: [things that tripped you up]
- Next: [what comes next]
```

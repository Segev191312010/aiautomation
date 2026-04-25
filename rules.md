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

---

## Autonomous Execution Constraints (Nights Watch Daemon)

These rules are HARD BANS for any Nights Watch run. Violation = stop and abort the task.

### Git / Remote
- **Never `git push`**, `git push --force`, `git push origin`, or push to any remote. Commits stay local only.
- Never create, delete, or rename remote branches (`git push origin :branch`, `git branch -D` on a branch that tracks remote).
- Never run `git reset --hard`, `git clean -fd`, `git checkout .`, or anything that discards uncommitted work without explicit approval.
- Never rewrite published history (`git rebase -i`, `git commit --amend` on pushed commits, `git filter-branch`).
- Never use `--no-verify`, `--no-gpg-sign`, or other flags that skip pre-commit hooks/signing.
- Never modify `.git/config`, `.git/hooks/`, or git identity.

### Live Trading / IBKR
- **Never modify live bot runtime code** without the full test suite passing. Specifically treat these directories as high-risk:
  - `backend/trading/` (order execution, position management)
  - `backend/ibkr/`, `backend/brokers/` (IBKR integration)
  - `backend/live_*.py`, `backend/autopilot*.py`
- Never start, stop, or signal the live bot process. Do not run `python -m backend.live_trading`, do not `kill` bot processes, do not touch systemd/PM2 units.
- Never connect to IBKR TWS or Gateway. Do not run scripts that import `ib_insync` at top level unless in a test under `pytest` fixtures.
- Never change `AUTOPILOT_MODE` — it stays OFF.

### Secrets / Config
- Never read, log, print, or commit contents of `.env`, `.env.local`, `.env.production`, `secrets/`, or any file matching `*.key`, `*.pem`, `credentials*`.
- Never modify `JWT_BOOTSTRAP_SECRET`, `JWT_SECRET`, database URLs, or IBKR credentials.
- Never commit files that could contain secrets — if `git status` shows an env/credential file is staged, unstage it and stop.

### Destructive Filesystem
- Never `rm -rf` anything outside a build output directory (`dist/`, `build/`, `node_modules/`, `.pytest_cache/`, `__pycache__/`).
- Never delete files under `backend/data/`, `data/`, `sessions/`, `memory/`, or `logs/`.
- Never truncate or overwrite SQLite DB files.

### Dependencies
- Never upgrade or downgrade dependencies (`npm install <pkg>@X`, `pip install -U`, modifying `package.json`/`requirements.txt` version pins) without explicit approval. Adding new top-level dependencies counts as a change requiring approval.
- Lockfile edits (`package-lock.json`, `poetry.lock`) are only allowed as a byproduct of an approved dependency change.

### Commit Hygiene
- Every commit MUST pass all four quality gates (typecheck, build, vitest, pytest). If any gate fails, FIX the root cause — never bypass.
- Use the `/commit` skill (runs gates + conventional format) for every commit. Do not craft raw `git commit` calls.
- One logical change per commit. Do not bundle unrelated phases.
- Never commit `.claude/settings.local.json` unless the change was the explicit purpose of the task.

### Scope Discipline
- Only work on tasks listed in `task.md`. If a task appears blocked or ambiguous, skip to the next and note the blocker in `logs/nights-watch-blockers.md` — do NOT invent scope.
- No refactors, cleanups, or "while I'm here" fixes outside the listed task's files.
- If a task requires schema migrations, new dependencies, or touches the live trading modules listed above, STOP and write a blocker note — do not proceed.

### Checkpoints
- After each phase completes: run `mcp__codex-review__codex` on the diff since the phase started. Log codex output to `logs/codex-review-phase-N.md`. If codex flags a critical issue, fix before moving on.
- Every 5 file edits: run all four quality gates. Fix failures immediately.
- Append a `[LEARN] <category>: <rule>` line whenever you notice a recurring pitfall.

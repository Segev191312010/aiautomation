# Learning Log

## 2026-08-26 — V1 repository reset
- Froze dirty worktrees and removed generated dependencies, caches, build output, and stale worktrees.
- Removed the legacy AI, bot, screener, rules, alerts, analytics, broker execution, and dashboard implementations with their obsolete tests.
- Promoted the V1 FastAPI service to the canonical `backend/main.py` entry point.
- Reduced persistence to users plus V1 strategy, approval, event, and decision tables.
- Reduced the dashboard to a five-file status shell and removed unused runtime dependencies.
- Preserved local `.env`, SQLite databases, and the owner-modified paper review protocol.
- Current authority remains backtest and shadow only; PAPER and LIVE execution are unavailable.

# Learned Rules

Rules are added here when mistakes are corrected during sessions.
Format: `## [Category] Rule (YYYY-MM-DD)`

Read this file before editing code. If a rule applies to your current task, follow it.

<!-- Entries will be added below this line -->

## [Prompting] Phase-by-phase execution means phase-by-phase (2026-04-17)
- Mistake: User said "start only phase one" but I later launched agents for all 5 phases when they said "call all agents"
- Correction: "Call all agents" means multiple agents for the CURRENT phase, not jumping ahead. When per-phase session files exist, the user controls the pace.

## [Claude-Code] Ultraplan analyzes a remote stale copy (2026-04-17)
- Mistake: Ultraplan claimed files like QuickOrderForm.tsx and formatters.ts didn't exist — they did locally
- Correction: Always cross-reference ultraplan findings against the local codebase before executing. The remote copy may be outdated.

## [Backtester] Column ordering with reset_index (2026-03-03)
- Mistake: Lowercased column names before `reset_index()`, so the DatetimeIndex 'Date' kept its uppercase name and the check `"date" in raw.columns` failed, causing `time = range(len(raw))` — all trade dates showed as 1970-01-01
- Correction: Always call `reset_index()` first, then lowercase all columns. Order matters when index names differ from column names.

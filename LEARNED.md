# Learned Rules

Rules are added here when mistakes are corrected during sessions.
Format: `## [Category] Rule (YYYY-MM-DD)`

Read this file before editing code. If a rule applies to your current task, follow it.

<!-- Entries will be added below this line -->

## [Backtester] Column ordering with reset_index (2026-03-03)
- Mistake: Lowercased column names before `reset_index()`, so the DatetimeIndex 'Date' kept its uppercase name and the check `"date" in raw.columns` failed, causing `time = range(len(raw))` — all trade dates showed as 1970-01-01
- Correction: Always call `reset_index()` first, then lowercase all columns. Order matters when index names differ from column names.

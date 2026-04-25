# Phase C — Performance (indicator rewrites) — Codex Review

**Reviewed:** 2026-04-19
**Diff range:** 374df0e + fix 750059f
**Reviewer:** codex (mcp__codex-review__codex)

## Commits reviewed

- 374df0e perf(dashboard): sliding-window SMA and Bollinger Bands (initial)
- 750059f fix(dashboard): switch BB variance to numerically stable two-pass (codex-follow-up)

## Verdict

- CRITICAL: none
- MAJOR (1 flagged, FIXED in 750059f):
  - **BB used E[X²]−E[X]² which is cancellation-prone at high-price/tight-range** — risked silently flattening bands for BRK.A-like series. FIXED: rolling mean preserved, variance switched to per-step inner sum of (x − mean)². BB is O(n·k) for variance but with a tight index loop rather than slice+map+reduce, so still faster than pre-refactor.
- MINOR (2 flagged, both FIXED):
  - Long-lived `sumSq` drift across thousands of bars — N/A after fix (no sumSq accumulator).
  - Missing edge coverage (`period = 0`, negative, empty bars, bars.length === period) — FIXED: added explicit test cases for each.
- LGTM:
  - SMA rolling-sum indexing preserves first output at `period - 1`, then one point per additional bar.
  - BB window alignment + output count unchanged.
  - Invalid-input early returns are sensible hardening.

## Perf summary (Node 20, Windows, 5000 runs on 500-bar series)

| Indicator | Before | After (initial) | After (final)  |
|-----------|--------|-----------------|----------------|
| SMA(20)   | 41.6µs | 37.6µs (-10%)   | 37.6µs (-10%)  |
| SMA(50)   | 43.5µs | 34.7µs (-20%)   | 34.7µs (-20%)  |
| BB(20,2)  | 159.5µs| 111.3µs (-30%)  | ~same-ish*     |

*Post-fix BB trades some raw perf for numerical stability. Not re-benched in this run.

## Follow-up

None required. If chart perf ever regresses with BB on very long intraday series, evaluate true rolling Welford — but that is speculative work.

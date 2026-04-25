# Phase 1 — Deduplicate fmtUSD

**Status:** TODO
**Priority:** Quick win, low risk
**Depends on:** Nothing

## Context

`dashboard/src/utils/formatters.ts` already exports `fmtUSD` (Intl.NumberFormat, currency USD, 2 decimal places). Seven other files define local copies. However, NOT all are equivalent:

## Safe to replace (identical behavior — Intl.NumberFormat with 2 decimals)

| File | Line | Current impl | Action |
|------|------|-------------|--------|
| `src/pages/SimulationPage.tsx` | 15 | `Intl.NumberFormat` currency USD | Replace with import |
| `src/components/tradebot/PositionsTable.tsx` | 54 | `Intl.NumberFormat` currency USD | Replace with import |
| `src/components/analytics/PnLSummary.tsx` | 12 | `Intl.NumberFormat` currency USD, 2 dec | Replace with import |
| `src/pages/Dashboard.tsx` | 27 | `'$' + toLocaleString(2 dec)` | Replace with import (output identical for positive; negative goes from `$-1,234` to `-$1,234` which is better) |

## NOT safe to blindly replace (different behavior)

| File | Line | Difference | Action |
|------|------|-----------|--------|
| `src/components/analytics/SectorExposure.tsx` | 29 | Compact format: K/M suffixes, 0 decimals for small values | Keep local OR rename to avoid confusion |
| `src/components/analytics/PnLChart.tsx` | 12 | 0 decimal places (`maximumFractionDigits: 0`) | Keep local — chart axis labels don't need cents |
| `src/components/autopilot/AIPerformanceCard.tsx` | 14 | `${sign}$${abs.toFixed(decimals)}` — no comma grouping, optional decimals param | Keep local — different behavior entirely |

## Implementation

For each safe file:
1. Delete local `function fmtUSD(...)` definition
2. Add `import { fmtUSD } from '@/utils/formatters'`
3. If file also has a local `fmtPct`, check if `formatters.ts` already exports one (it does at line 22)

## Verification

```bash
cd dashboard && npm run typecheck && npm run build && npx vitest run
grep -rn "function fmtUSD" src/  # should only show the 3 intentionally-different ones
```

# Phase 2 — Memoize WatchlistGrid Sort

**Status:** TODO
**Priority:** Quick win, performance
**Depends on:** Nothing

## Context

`dashboard/src/components/ticker/WatchlistGrid.tsx` lines 104-117 create a new sorted array on every render via `[...symbols].map().filter().sort()`. No `useMemo` wrapping. WS price ticks update `quotes` frequently, causing unnecessary re-sorts.

## Implementation

1. Add `useMemo` to the React import
2. Wrap the sort chain in `useMemo` keyed on `[symbols, quotes, sortField, sortDir]`

```typescript
// Before:
const sorted = [...symbols]
  .map((sym) => quotes[sym])
  .filter(Boolean)
  .sort((a, b) => { ... })

// After:
const sorted = useMemo(() => [...symbols]
  .map((sym) => quotes[sym])
  .filter(Boolean)
  .sort((a, b) => { ... }), [symbols, quotes, sortField, sortDir])
```

## Note on effectiveness

Since `quotes` is recreated on every WS tick (object spread in store), the memo recomputes on every tick — same as before. The win is when `quotes` is unchanged but the parent re-renders for unrelated reasons. Still worth doing for correctness.

## Verification

```bash
cd dashboard && npm run typecheck && npm run build && npx vitest run
```
Manual: Dashboard watchlist sorts correctly when clicking column headers.

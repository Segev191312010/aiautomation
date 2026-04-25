# Phase 3 — Remove Dead Code in useWebSocket

**Status:** TODO
**Priority:** Quick win, cleanup
**Depends on:** Nothing

## Context

`dashboard/src/hooks/useWebSocket.ts` imports `addTrade` from the account store but never calls it. The `filled` event handler already calls `fetchTrades().then(setTrades)` to refresh the full trade list from the server. The `addTrade` import is dead code.

## Implementation

Delete the line:
```typescript
const addTrade = useAccountStore((s) => s.addTrade)
```

## Verification

```bash
cd dashboard && npm run typecheck
grep -n "addTrade" dashboard/src/hooks/useWebSocket.ts  # should return nothing
```

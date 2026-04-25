# Phase 5 — Adaptive Polling When WS Connected

**Status:** TODO
**Priority:** Performance optimization
**Depends on:** Nothing (but has internal 5a → 5b dependency)

## Context

`dashboard/src/hooks/useMarketData.ts` polls `/api/watchlist` at fixed 5s intervals (`QUOTE_INTERVAL = 5_000`) even when WebSocket delivers live price ticks. When WS is connected, the REST poll is mostly redundant — reduce cadence to 30s. Revert to 5s when WS drops.

## Phase 5a — Add `connected` getter to MarketDataWsService

**File:** `dashboard/src/services/ws.ts`

The general `WebSocketService` class already has a `connected` getter. The `MarketDataWsService` class does NOT. Add one:

```typescript
get connected(): boolean {
  return this.ws?.readyState === WebSocket.OPEN
}
```

## Phase 5b — Adaptive interval in useMarketData

**File:** `dashboard/src/hooks/useMarketData.ts`

Replace:
```typescript
const QUOTE_INTERVAL = 5_000
```

With:
```typescript
const QUOTE_INTERVAL_FAST = 5_000    // WS disconnected
const QUOTE_INTERVAL_SLOW = 30_000   // WS connected, live ticks flowing
```

Replace the polling `useEffect` to self-reschedule based on `wsMdService.connected`:
```typescript
useEffect(() => {
  refreshQuotes()
  const scheduleNext = () => {
    if (quoteTimer.current) clearInterval(quoteTimer.current)
    const interval = wsMdService.connected ? QUOTE_INTERVAL_SLOW : QUOTE_INTERVAL_FAST
    quoteTimer.current = setInterval(() => {
      refreshQuotes()
      scheduleNext()
    }, interval)
  }
  scheduleNext()
  return () => { if (quoteTimer.current) clearInterval(quoteTimer.current) }
}, [refreshQuotes])
```

## Worst-case latency

When WS drops mid-session, up to 30s before the next tick re-evaluates and switches to 5s. Acceptable tradeoff for significantly reduced REST traffic.

## Verification

```bash
cd dashboard && npm run typecheck && npm run build && npx vitest run
```
Manual:
1. Dashboard with backend running → Network tab shows `/api/watchlist` every ~30s
2. Kill backend → cadence speeds to ~5s
3. Restart backend → cadence returns to ~30s

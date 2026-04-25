# Phase 4 — Add encodeURIComponent to Symbol Path Params

**Status:** TODO
**Priority:** Defense-in-depth security
**Depends on:** Nothing

## Context

API modules in `dashboard/src/services/api/` interpolate `${symbol}` directly into URL paths. Some endpoints already use `encodeURIComponent` for query params (like `duration`) but not for the symbol path segment. Defense-in-depth for symbols that might contain special chars (e.g., crypto `BTC/USD`).

## Files to modify

### `src/services/api/stockProfile.ts` — 14 endpoints
All use pattern: `` `/api/stock/${symbol}/...` ``
Change to: `` `/api/stock/${encodeURIComponent(symbol)}/...` ``

### `src/services/api/market.ts` — 4 endpoints
- `fetchYahooBars`: `/api/yahoo/${symbol}/bars`
- `fetchIBKRBars`: `/api/market/${symbol}/bars` (duration already encoded)
- `fetchPrice`: `/api/market/${symbol}/price`
- `subscribeRtBars` / `unsubscribeRtBars`: `/api/market/${symbol}/subscribe|unsubscribe`

### `src/services/api/indicators.ts` — 1 endpoint
- `/api/market/${symbol}/indicators`

## Server compatibility

FastAPI auto-decodes percent-encoded path params. Common symbols (AAPL, BRK.B, BTC-USD) are unaffected by encoding since letters, dots, and hyphens are not percent-encoded.

## Verification

```bash
cd dashboard && npm run typecheck && npm run build
```
Manual: Load chart for `BTC-USD`, verify API calls resolve correctly in Network tab.

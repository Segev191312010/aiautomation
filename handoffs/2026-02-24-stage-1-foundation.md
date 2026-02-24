# HANDOFF: Stage 1 Foundation Review → Stage 2 Implementation

**Date:** 2026-02-24
**Session:** Code Review - Stage 1 Foundation Audit
**Status:** ✅ Complete

## What We Built

Completed a full codebase review of the TradeBot Dashboard (React 18 + TypeScript + Zustand + Vite). Read and analyzed all 34 source files (~4,100 lines). Identified a critical build blocker (src/ directory mismatch), 7 high-severity issues, 10 medium issues, and documented security gaps. Created this handoff with prioritized fix recommendations.

## Files Created/Modified

- `handoffs/2026-02-24-stage-1-foundation.md` (created - this document)

## Architecture Overview

**App:** TradeBot Dashboard (`trading-dashboard` v2.0.0)
**Stack:** React 18.3, TypeScript 5.5, Vite 5.4, Zustand 4.5, Tailwind 3.4, lightweight-charts 4.2

```
index.html → main.tsx → App.tsx → Layout (Sidebar + Header + PageSwitch)
                                     ↓
                          Pages: Dashboard | Market | TradeBot | Simulation
                                     ↓
                          Hooks: useMarketData (REST polling + WS) + useWebSocket
                                     ↓
                          Services: api.ts (fetch) + ws.ts (WebSocket) + mockService.ts (GBM fallback)
                                     ↓
                          Stores: Market | Account | Bot | Sim | UI  (Zustand)
```

**Data flow:** Backend (FastAPI @ :8000) → REST + WebSocket → Services → Hooks → Zustand Stores → React Components. Falls back to client-side GBM mock data when backend is offline.

## Testing Completed

- ✅ Read all 34 source files + 3 legacy files
- ✅ Verified type system completeness (20+ interfaces in `types/index.ts`)
- ✅ Verified store architecture (5 Zustand stores, well-separated)
- ✅ Verified API client (40+ endpoints in `services/api.ts`)
- ✅ Verified WebSocket services (dual WS: main events + market data)
- ✅ Verified indicator math (SMA, EMA, BB, VWAP, RSI, MACD)
- ✅ Checked all config files for consistency
- ❌ Cannot run `npm install` / `npm run build` (no Node.js in environment)
- ❌ Cannot verify runtime behavior (no browser)

## Critical Findings

### BLOCKER: `src/` Directory Mismatch

**The project cannot build.** All config files expect source under `src/`:

```typescript
// vite.config.ts → alias: { '@': path.resolve(__dirname, './src') }
// tsconfig.json  → paths: { "@/*": ["./src/*"] }
// index.html     → <script src="/src/main.tsx">
// tailwind.config→ content: ['./src/**/*.{ts,tsx}']
```

But all source files live in the project root. Every `@/` import will fail.

**Fix:** Move `App.tsx`, `main.tsx`, `index.css`, `components/`, `hooks/`, `pages/`, `services/`, `store/`, `types/`, `utils/` into a `src/` directory.

### HIGH: 6 More Issues

1. **No Error Boundaries** → app crashes to white screen on any React error
2. **`useWebSocket` StrictMode bug** → WebSocket permanently disconnects in dev mode (mountedRef prevents reconnection after StrictMode remount)
3. **Duplicate `RuleCreate_`** in `api.ts:22-31` → shadows the imported `RuleCreate` type
4. **Unused `react-router-dom`** in package.json → never imported, routing is via Zustand
5. **TypeScript strictness gaps** → `noUnusedLocals: false`, `noUnusedParameters: false`, many `as any` casts
6. **No auth/CSRF** → all API calls unauthenticated, no CSRF tokens on POST/PUT/DELETE

### MEDIUM: 10 Issues

1. Zero test files, no test framework
2. No ESLint/Prettier config (despite eslint-disable comments)
3. README references `pip install` (wrong stack)
4. Legacy `trading.html/js/css` (91KB JS) cluttering root
5. No `.env` / environment config
6. No WebSocket connection status in UI
7. `WatchlistGrid` re-parses symbols on every keystroke (no memoization)
8. Chart recreates all indicator series on toggle (flickering)
9. No accessibility (ARIA, keyboard nav, screen reader labels)
10. Missing favicon (`/icon.svg` referenced but doesn't exist)

### LOW: Code Duplication

- `fmtUSD()` defined 4 times across pages/components
- `isSimAccount()` type guard defined 2 times
- Zustand object destructuring in `MarketPage` and `Sidebar` causes unnecessary re-renders

## What's NOT Done (Future Work)

- [ ] Fix src/ directory structure (BLOCKER)
- [ ] Add React Error Boundaries
- [ ] Fix useWebSocket StrictMode bug
- [ ] Remove unused react-router-dom
- [ ] Remove duplicate RuleCreate_ interface
- [ ] Set up Vitest + React Testing Library
- [ ] Add ESLint + Prettier
- [ ] Extract shared utils (fmtUSD, isSimAccount)
- [ ] Update README for actual tech stack
- [ ] Add authentication layer
- [ ] Add .env configuration
- [ ] Remove legacy trading.* files
- [ ] Add CI/CD pipeline
- [ ] Add accessibility basics

## Open Questions

- [ ] Should the legacy `trading.html/js/css` files be kept as a fallback or removed entirely?
- [ ] Is there a backend repo? The frontend expects FastAPI at `:8000` but no backend code is in this repo.
- [ ] What authentication method is planned? (JWT, session cookies, OAuth?)
- [ ] Should `react-router-dom` be wired up properly or removed in favor of the Zustand-based routing?

## What Works Well (Keep These)

1. **Type system** — comprehensive interfaces in `types/index.ts`
2. **Zustand stores** — clean separation across 5 domain-specific stores
3. **Bloomberg terminal UI** — polished dark theme, consistent design language
4. **Mock data service** — GBM-based simulation with per-asset volatility
5. **Technical indicators** — pure TS, no deps, mathematically correct
6. **WebSocket architecture** — dual WS with auto-reconnect and subscriptions
7. **Live chart updates** — real-time candle construction from WS ticks
8. **Bulk symbol import** — TradingView format + crypto alias conversion

## Next Session Should Be

**Fix Session:** Resolve the build blocker and critical issues.

**Tasks (in order):**
1. Create `src/` directory and move all source files into it
2. Verify `npm install && npm run build` succeeds
3. Remove unused `react-router-dom` from package.json
4. Delete duplicate `RuleCreate_` from `services/api.ts` (use imported `RuleCreate`)
5. Add a root-level React Error Boundary in `App.tsx`
6. Fix `useWebSocket` StrictMode bug (remove mountedRef, use proper cleanup)
7. Enable `noUnusedLocals: true` and `noUnusedParameters: true` in tsconfig

**Files to modify:**
- All files (move into `src/`)
- `package.json` (remove react-router-dom)
- `services/api.ts` (remove RuleCreate_)
- `App.tsx` (add ErrorBoundary wrapper)
- `hooks/useWebSocket.ts` (fix StrictMode reconnection)
- `tsconfig.json` (enable strict checks)

## File Inventory (34 React/TS files, ~4,101 lines)

| Category | Files | Lines |
|----------|-------|-------|
| Entry / Config | `App.tsx`, `main.tsx`, `index.html`, `index.css`, `vite.config.ts`, `tsconfig.json`, `tailwind.config.ts`, `postcss.config.js`, `package.json` | ~290 |
| Types | `types/index.ts` | 237 |
| Store | `store/index.ts` | 291 |
| Services | `api.ts`, `mockService.ts`, `ws.ts` | 611 |
| Hooks | `useMarketData.ts`, `useWebSocket.ts` | 223 |
| Utils | `indicators.ts` | 152 |
| Pages (4) | `Dashboard`, `MarketPage`, `SimulationPage`, `TradeBotPage` | 825 |
| Components (11) | Layout (3), Chart (2), Indicators (1), Ticker (2), TradeBot (3), Sim (1) | 1,472 |
| Legacy | `trading.html`, `trading.js`, `trading.css` | ~3,200 |

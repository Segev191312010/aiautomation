# Stage 1 Session Prompt: Foundation & Infrastructure

**Generated:** 2026-02-24
**For use in:** Fresh isolated session on branch `claude/review-stage-1-foundation-Nhboq`

---

We're working on the **TradeBot Dashboard** — a React 18 + TypeScript + Vite + Zustand + Tailwind + lightweight-charts trading platform with a FastAPI backend. This is a **FOUNDATION & INFRASTRUCTURE** session.

## SCOPE OF THIS SESSION

- **Fix critical build blocker:** Move all source files into `src/` directory (configs already expect `src/`)
- **Auth scaffold:** Create `backend/auth.py` with users table, demo user seed, JWT `login()`/`verify_token()`, `GET /api/auth/me`
- **Add `user_id TEXT`** column to ALL existing backend tables (rules, trades, sim_account, sim_positions, sim_orders), default to demo user ID
- **Settings system:** `backend/settings.py` with user preferences as JSON blob in `users.settings` column. Endpoints: `GET/PUT /api/settings`
- **Consistent error responses:** Harden all backend error responses to `{error: string, detail: string}` format
- **Basic request logging middleware** in FastAPI
- **Frontend — ToastProvider + useToast hook:** Global notification system (success/error/warning/info)
- **Frontend — ErrorBoundary.tsx:** Catch React crashes with fallback UI
- **Frontend — SettingsPage.tsx:** Replace stub with working settings page (default symbol, bar size, theme, bot interval)
- **Wire toasts into ALL existing API calls** (order placement, bot toggle, IBKR connect/disconnect)
- **Loading skeletons** on Dashboard, TradeBotPage, MarketPage
- **Auth header interceptor** in `api.ts` (uses demo token for now, real auth in Stage 8)
- **Fix useWebSocket StrictMode bug** (mountedRef prevents reconnection after StrictMode remount)
- **Remove unused `react-router-dom`** from package.json
- **Delete duplicate `RuleCreate_`** interface in `services/api.ts` (use imported `RuleCreate`)
- **Extract shared utils:** `fmtUSD()` (defined 4x) and `isSimAccount()` (defined 2x) into `utils/format.ts`
- **Remove legacy files:** `trading.html`, `trading.js`, `trading.css`
- **Tests:** pytest for settings CRUD, auth token generation/validation, error response format

## OUT OF SCOPE (don't touch these)

- Advanced charting features (Stage 2)
- Stock screener (Stage 3)
- Backtesting engine (Stage 4)
- Alerts system (Stage 5)
- Rule builder UI (Stage 6)
- Portfolio analytics (Stage 7)
- Full registration flow / password reset (Stage 8)
- CORS / rate limiting middleware (Stage 8)
- Docker / deployment config (Stage 8)

## CONTEXT YOU NEED

**Critical build blocker:** All config files expect source under `src/`:
- `vite.config.ts` → `alias: { '@': './src' }`
- `tsconfig.json` → `paths: { "@/*": ["./src/*"] }`
- `index.html` → `<script src="/src/main.tsx">`
- `tailwind.config.ts` → `content: ['./src/**/*.{ts,tsx}']`

But all source files currently live in project root. Every `@/` import fails. **Fix this first** before any other work.

**Existing architecture:**
```
index.html → main.tsx → App.tsx → Layout (Sidebar + Header + PageSwitch)
                                     ↓
                          Pages: Dashboard | Market | TradeBot | Simulation | Rules (stub) | Settings (stub)
                                     ↓
                          Hooks: useMarketData (REST polling + WS) + useWebSocket
                                     ↓
                          Services: api.ts (fetch ~40 endpoints) + ws.ts (dual WebSocket) + mockService.ts (GBM fallback)
                                     ↓
                          Stores: Market | Account | Bot | Sim | UI  (Zustand)
```

**Existing stores (5 Zustand):** useMarketStore, useAccountStore, useBotStore, useSimStore, useUIStore

**Backend:** FastAPI at `:8000` — already has ~40 endpoints including full Rules CRUD, WebSocket events, simulation, IBKR integration. Backend code is NOT in this repo (separate repo), but you need to know the API contract.

**Known bugs to fix:**
- `useWebSocket.ts` uses `mountedRef` that prevents reconnection after React StrictMode remount — remove mountedRef, use proper cleanup return
- `services/api.ts` line ~22-31 has duplicate `RuleCreate_` interface that shadows the imported `RuleCreate` type — delete it
- `fmtUSD()` defined in 4 files — extract to `src/utils/format.ts`
- `isSimAccount()` type guard defined in 2 files — extract to `src/utils/format.ts`
- `react-router-dom` in package.json but never imported (routing is Zustand-based) — remove it

**Legacy files to delete:** `trading.html`, `trading.js`, `trading.css` (~91KB total, old standalone dashboard)

## FILES TO FOCUS ON

**Move into `src/` (all source directories):**
- `App.tsx`, `main.tsx`, `index.css` → `src/`
- `components/`, `hooks/`, `pages/`, `services/`, `store/`, `types/`, `utils/` → `src/`

**Create new:**
- `src/components/ToastProvider.tsx` — toast context + provider + toast container UI
- `src/hooks/useToast.ts` — `useToast()` → `{ toast, dismiss }`
- `src/components/ErrorBoundary.tsx` — React error boundary with fallback
- `src/pages/SettingsPage.tsx` — full settings page (replace stub)
- `src/utils/format.ts` — extracted `fmtUSD()`, `isSimAccount()`
- `src/components/LoadingSkeleton.tsx` — reusable skeleton component

**Modify:**
- `src/services/api.ts` — add auth header interceptor, delete `RuleCreate_`, wire toast on errors
- `src/hooks/useWebSocket.ts` — fix StrictMode bug (remove mountedRef)
- `src/App.tsx` — wrap with ErrorBoundary
- `src/pages/Dashboard.tsx` — add loading skeletons
- `src/pages/TradeBotPage.tsx` — add loading skeletons, replace inline fmtUSD
- `src/pages/MarketPage.tsx` — add loading skeletons, replace inline fmtUSD
- `src/components/layout/Header.tsx` — add toast container mount point
- `package.json` — remove `react-router-dom`

**Delete:**
- `trading.html`, `trading.js`, `trading.css`

## CURRENT TASK

Execute in this order:

1. **Move all source into `src/`** — create `src/` directory, move `App.tsx`, `main.tsx`, `index.css`, `components/`, `hooks/`, `pages/`, `services/`, `store/`, `types/`, `utils/` into it. Verify `index.html` already points to `/src/main.tsx`.
2. **Delete legacy files** — `trading.html`, `trading.js`, `trading.css`
3. **Remove `react-router-dom`** from `package.json`
4. **Extract shared utils** — create `src/utils/format.ts` with `fmtUSD()` and `isSimAccount()`, update all imports
5. **Fix `useWebSocket.ts`** — remove mountedRef, use proper cleanup
6. **Fix `api.ts`** — delete duplicate `RuleCreate_` interface
7. **Create `ErrorBoundary.tsx`** — wrap app in `App.tsx`
8. **Create toast system** — `ToastProvider.tsx` + `useToast.ts`, wire into app
9. **Wire toasts into existing API calls** — order placement, bot toggle, IBKR connect/disconnect, all error paths
10. **Create `LoadingSkeleton.tsx`** — add to Dashboard, TradeBotPage, MarketPage
11. **Build `SettingsPage.tsx`** — default symbol, bar size, theme preference, bot interval. `GET/PUT /api/settings` integration.
12. **Add auth header interceptor** in `api.ts` — reads token from localStorage, attaches `Authorization: Bearer <token>`. Uses hardcoded demo token for now.
13. **Add basic request logging middleware** — implement request/response logging in FastAPI (method, path, status code, response time)
14. **Harden error responses** — ensure all backend error handlers return consistent `{error: string, detail: string}` JSON format across all endpoints
15. **Run `npm install && npm run build`** to verify everything compiles
16. **Write pytest tests** for settings CRUD, auth token, error format (if backend is accessible)

**Focus ONLY on foundation infrastructure.** Don't add new features, new pages (except Settings), or change the trading/charting logic. Every other stage depends on this being solid.

## DONE WHEN

- `npm run build` succeeds with all files under `src/`
- Every user action shows a toast (success or error)
- React errors caught by ErrorBoundary (not white screen)
- Settings page loads/saves preferences
- `api.ts` sends Authorization header on every request
- `fmtUSD()` and `isSimAccount()` exist in one place only
- Legacy `trading.*` files gone
- `useWebSocket` reconnects properly in StrictMode
- No duplicate type definitions
- All error responses use consistent `{error: string, detail: string}` JSON format (if backend is accessible)
- Request logging middleware active in FastAPI (if backend is accessible)
- pytest passes (if backend available)

# Stage 1 Session Prompt: Foundation, Auth Scaffold & Polish

You are working on a trading platform built with **FastAPI** (backend) and **React 18 + TypeScript + Zustand + TailwindCSS** (dashboard). The project is at `C:\Users\segev\sdvesdaW\trading`.

## Current State
- **Backend** (`backend/`): FastAPI with 40+ endpoints, IBKR integration (ib_insync), 8 technical indicators (RSI, SMA, EMA, MACD, BBANDS, ATR, STOCH, PRICE), rule engine with AND/OR logic + cooldown, order execution, virtual trading simulation, historical replay, mock GBM data, real-time WebSocket, SQLite persistence.
- **Dashboard** (`dashboard/`): React 18 + Vite + Zustand + TailwindCSS. 4 pages: Dashboard, TradeBotPage, MarketPage, SimulationPage. Uses lightweight-charts for candlesticks. Dark terminal theme. Watchlist grid, comparison overlay.
- **Database**: SQLite with tables: `rules` (id, data JSON), `trades` (id, rule_id, symbol, action, timestamp, data JSON), `sim_account`, `sim_positions`, `sim_orders`.
- **3 operating modes**: IBKR Live, IBKR Paper, Simulation (offline with mock data).

## What to Build (Stage 1)

### 1. Auth Scaffold (Backend)

**Create `backend/auth.py`:**
- `users` table schema: `id TEXT PRIMARY KEY, email TEXT UNIQUE, password_hash TEXT, created_at TEXT, settings TEXT` (settings is a JSON blob)
- Seed a default "demo" user on first run (id: `demo`, email: `demo@local`, password: hashed "demo")
- Simple JWT: `create_token(user_id) -> str`, `verify_token(token) -> user_id`
- `get_current_user()` FastAPI dependency that extracts user from Authorization header (returns demo user if no token)
- Use `python-jose[cryptography]` for JWT, `passlib[bcrypt]` for password hashing
- Add `JWT_SECRET`, `JWT_ALGORITHM="HS256"`, `JWT_ACCESS_EXPIRE_MINUTES=1440` to `config.py`

**Add `user_id` column to ALL existing tables:**
- `rules`: add `user_id TEXT DEFAULT 'demo'`
- `trades`: add `user_id TEXT DEFAULT 'demo'`
- `sim_account`: add `user_id TEXT DEFAULT 'demo'`
- `sim_positions`: add `user_id TEXT DEFAULT 'demo'`
- `sim_orders`: add `user_id TEXT DEFAULT 'demo'`
- Update all database.py CRUD functions to accept and filter by `user_id`
- Use `ALTER TABLE ... ADD COLUMN` in the init function for migration

### 2. Settings System (Backend)

**Create `backend/settings.py`:**
- Settings are stored in the `users.settings` JSON column
- Default settings: `{"theme": "dark", "default_symbol": "SPY", "default_bar_size": "1D", "bot_interval": 60, "watchlist": ["BTC-USD","ETH-USD","AAPL","TSLA","SPY","QQQ","NVDA"]}`
- `get_settings(user_id) -> dict` — returns merged defaults + saved settings
- `update_settings(user_id, partial_settings) -> dict` — deep merges partial update into existing

**Add endpoints in `main.py`:**
- `GET /api/settings` — returns current user's settings
- `PUT /api/settings` — accepts partial JSON, merges into existing settings
- `GET /api/auth/me` — returns current user info (id, email, settings)

### 3. Error Response Hardening (Backend)

**In `main.py`:**
- Add a global exception handler that catches all exceptions and returns `{"error": "type", "detail": "message"}` format
- Add specific handlers for `HTTPException`, `ValidationError`, and generic `Exception`
- Add basic request logging middleware (method, path, status code, duration)

### 4. Toast Notification System (Frontend)

**Create `dashboard/src/components/ui/ToastProvider.tsx`:**
- Toast context + provider wrapping the app
- `useToast()` hook returning `{success(msg), error(msg), warning(msg), info(msg)}`
- Toast renders as a fixed stack in bottom-right corner
- Auto-dismiss after 4 seconds, manual dismiss on click
- Styled with terminal theme colors (green=success, red=error, amber=warning, blue=info)

**Wire toasts into existing functionality:**
- `TradeBotPage.tsx`: toast on bot start/stop, order placement success/failure, IBKR connect/disconnect
- `SimulationPage.tsx`: toast on sim order, sim reset, replay load/play/stop
- `MarketPage.tsx`: toast on subscription changes
- `api.ts`: add a wrapper that toasts on API errors automatically

### 5. Error Boundary (Frontend)

**Create `dashboard/src/components/ui/ErrorBoundary.tsx`:**
- React class component error boundary
- Catches render errors, shows fallback UI with error message + "Reload" button
- Wrap each page in `<ErrorBoundary>` in App.tsx

### 6. Settings Page (Frontend)

**Create `dashboard/src/pages/SettingsPage.tsx`:**
- General section: default symbol, default bar size, default watchlist
- Bot section: bot evaluation interval, auto-connect IBKR on startup
- Display section: theme selector (dark only for now, light coming in Stage 8)
- About section: app version, mode (IBKR/SIM/MOCK), connection status
- Save button calls `PUT /api/settings`
- Toast on save success/failure

**Add to routing:**
- Add Settings nav item in `Sidebar.tsx` (gear icon)
- Add `/settings` route in `App.tsx`

### 7. Loading Skeletons (Frontend)

**Create `dashboard/src/components/ui/Skeleton.tsx`:**
- Reusable skeleton component (animated pulse, configurable height/width)
- Add loading states to `Dashboard.tsx`, `TradeBotPage.tsx`, `MarketPage.tsx`
- Show skeletons while initial data is loading (accounts, positions, watchlist)

### 8. API Auth Header (Frontend)

**Modify `dashboard/src/services/api.ts`:**
- Add `Authorization: Bearer <token>` header to all requests
- For now, hardcode demo token or fetch on app init via a simple `/api/auth/token` endpoint
- Add response interceptor: if 401, clear token (prep for Stage 8 login redirect)

### 9. Tests (Backend)

**Create `backend/tests/` directory with:**
- `test_auth.py`: test token creation, verification, demo user seeding
- `test_settings.py`: test get/update settings, JSON merge logic
- `test_error_handling.py`: test that API errors return consistent format
- Use `pytest` + `httpx` (FastAPI TestClient)

## Dependencies to Install

**Backend** (add to `requirements.txt`):
```
python-jose[cryptography]>=3.3.0
passlib[bcrypt]>=1.7.4
httpx>=0.27.0
pytest>=8.0.0
pytest-asyncio>=0.23.0
```

**Frontend** (no new packages needed — all UI is built with existing TailwindCSS)

## Files to Create
- `backend/auth.py`
- `backend/settings.py`
- `backend/tests/__init__.py`
- `backend/tests/test_auth.py`
- `backend/tests/test_settings.py`
- `backend/tests/test_error_handling.py`
- `dashboard/src/components/ui/ToastProvider.tsx`
- `dashboard/src/components/ui/ErrorBoundary.tsx`
- `dashboard/src/components/ui/Skeleton.tsx`
- `dashboard/src/pages/SettingsPage.tsx`

## Files to Modify
- `backend/config.py` — add JWT config vars
- `backend/database.py` — add user_id columns, update all CRUD to filter by user_id, create users table
- `backend/main.py` — add auth dependency, settings endpoints, error handlers, logging middleware
- `backend/models.py` — add User, Settings, AuthToken models
- `backend/requirements.txt` — add new dependencies
- `dashboard/src/App.tsx` — add settings route, wrap pages in ErrorBoundary
- `dashboard/src/components/layout/Sidebar.tsx` — add Settings nav item
- `dashboard/src/services/api.ts` — add auth header, error toast wrapper, settings API functions
- `dashboard/src/store/index.ts` — add useSettingsStore (or extend useUIStore with settings)
- `dashboard/src/types/index.ts` — add Settings, User types
- `dashboard/src/main.tsx` — wrap App with ToastProvider
- `dashboard/src/pages/TradeBotPage.tsx` — add toasts to actions
- `dashboard/src/pages/SimulationPage.tsx` — add toasts to actions
- `dashboard/src/pages/Dashboard.tsx` — add loading skeletons
- `dashboard/src/pages/MarketPage.tsx` — add loading skeletons, toasts

## Definition of Done
1. `users` table exists with demo user seeded on startup
2. All existing tables have `user_id` column defaulting to 'demo'
3. `GET /api/settings` returns merged default + saved settings
4. `PUT /api/settings` saves partial settings update
5. `GET /api/auth/me` returns demo user info
6. ALL API errors return `{error, detail}` JSON format
7. Toast notifications appear on: bot start/stop, order placement, IBKR connect/disconnect, sim actions, API errors
8. Error boundary catches React crashes with fallback UI
9. Settings page allows editing default symbol, bar size, watchlist, bot interval
10. Loading skeletons show on initial page load
11. `pytest backend/tests/` passes all tests
12. All existing functionality still works (IBKR, simulation, mock mode, WebSocket)

## Important Notes
- Do NOT break existing functionality. This is additive.
- The auth system is a SCAFFOLD — no login page, no registration flow. Just the database schema and token infrastructure. The demo user auto-authenticates.
- Settings are stored as a JSON blob, not individual rows. Use deep merge for partial updates.
- Keep the terminal dark theme consistent for all new UI components.
- Test everything with `SIM_MODE=true` and `MOCK_MODE=true` (no IBKR needed).

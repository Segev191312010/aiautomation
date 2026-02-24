# Stage 8 Session Prompt: Production Hardening & Deployment

You are working on a trading platform built with **FastAPI** (backend) and **React 18 + TypeScript + Zustand + TailwindCSS** (dashboard). The project is at `C:\Users\segev\sdvesdaW\trading`.

## Current State
- **Backend** (`backend/`): FastAPI with 40+ endpoints, IBKR integration (ib_insync), 8 technical indicators (RSI, SMA, EMA, MACD, BBANDS, ATR, STOCH, PRICE), rule engine with AND/OR logic + cooldown, order execution, virtual trading simulation, historical replay, mock GBM data, real-time WebSocket, SQLite persistence via aiosqlite.
- **Dashboard** (`dashboard/`): React 18 + Vite + Zustand + TailwindCSS. Pages: Dashboard, TradeBotPage, MarketPage, SimulationPage, ScreenerPage, BacktestPage, AlertsPage, RulesPage, AnalyticsPage, SettingsPage. Uses lightweight-charts for candlesticks. Dark terminal theme. Watchlist grid, comparison overlay, multi-pane charts with drawing tools, crosshair sync, resizable panes.
- **Database**: SQLite with tables: `rules`, `trades`, `sim_account`, `sim_positions`, `sim_orders`, `users`, `screener_presets`, `backtests`, `alerts`, `alert_history` — all with `user_id` columns defaulting to `'demo'`.
- **Auth scaffold**: `users` table exists with seeded demo user (id: `demo`, email: `demo@local`). `user_id TEXT DEFAULT 'demo'` on all tables. `get_current_user()` dependency returns demo user if no token. JWT infrastructure (`python-jose`, `passlib[bcrypt]`) installed but no registration/login flow yet.
- **Completed stages**: Stage 1 (foundation + auth scaffold + toast/error/settings), Stage 2a-2c (advanced charting: multi-pane, drawing tools, volume, crosshair sync), Stage 3 (screener with S&P 500/NASDAQ 100 scanning), Stage 4 (backtesting engine with equity curves + metrics), Stage 5 (alerts with WebSocket notifications + browser push), Stage 6 (rule builder UI with visual conditions + "backtest this rule"), Stage 7 (portfolio analytics + risk metrics + trade journal).
- **3 operating modes**: IBKR Live, IBKR Paper, Simulation (offline with mock data).

## What to Build (Stage 8)

### 1. Complete Auth System (Backend)

**Extend `backend/auth.py`** (already exists with scaffold):
- `POST /api/auth/register` — accepts `{email, password}`, validates email format + password strength (min 8 chars), bcrypt hash password, create user row, return `{access_token, refresh_token, token_type: "bearer", expires_in: 900}`
- `POST /api/auth/login` — accepts `{email, password}`, verify bcrypt hash, return `{access_token, refresh_token, token_type: "bearer", expires_in: 900}`
- `POST /api/auth/refresh` — accepts `{refresh_token}`, validate against DB, return new `{access_token, refresh_token, token_type: "bearer", expires_in: 900}`
- `GET /api/auth/me` — already exists from Stage 1, returns user info + settings
- `POST /api/auth/logout` — accepts `{refresh_token}`, mark token as revoked in DB
- Password hashing: bcrypt via `passlib[bcrypt]` (already in requirements.txt)
- JWT: `python-jose[cryptography]` (already in requirements.txt), access token 15min expiry, refresh token 7 day expiry
- New table `refresh_tokens`: `token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, expires_at TEXT NOT NULL, revoked INTEGER DEFAULT 0`
- Store SHA-256 hash of refresh token in DB (never store raw token)
- In dev mode (`ENVIRONMENT=dev`): auto-create demo user on startup if no users exist (backward compat with Stage 1)

### 2. Auth Middleware & Guards (Backend)

**Modify `get_current_user()` in `backend/auth.py`:**
- Extract JWT from `Authorization: Bearer <token>` header
- Decode and verify JWT signature, check expiry
- Return user object from DB based on `sub` claim (user_id)
- If no valid token: raise `HTTPException(401, "Not authenticated")`
- ALL `/api/*` endpoints require valid token EXCEPT: `POST /api/auth/login`, `POST /api/auth/register`, `GET /api/status`
- All database queries already filter by `user_id` (from Stage 1) — now the `user_id` comes from the JWT instead of hardcoded `'demo'`

### 3. Security Middleware (Backend)

**Create `backend/middleware.py`:**
- **CORS middleware**: use FastAPI's `CORSMiddleware`. Read origins from `CORS_ORIGINS` env var (comma-separated, default: `http://localhost:5173,http://localhost:8000`). Allow methods: `*`. Allow headers: `*`. Allow credentials: `true`.
- **Rate limiting middleware**: per-user rate limiting based on JWT `sub` claim (or IP for unauthenticated endpoints). `RATE_LIMIT_PER_MINUTE` env var (default: `60`). Track request counts in-memory dict with sliding window. Return `429 Too Many Requests` with `Retry-After` header when exceeded.
- **Request logging middleware**: log every request as structured JSON to stdout: `{"timestamp", "method", "path", "user_id", "status_code", "duration_ms"}`. In dev mode: human-readable single-line format. In production: JSON format.
- **Input validation**: ensure all existing Pydantic models have proper `Field()` constraints (min/max lengths, value ranges)

### 4. Database Migrations (Backend)

**Create `backend/migrations/` directory with versioned SQL scripts:**
- `001_initial_schema.sql` — `rules`, `trades` tables (original schema)
- `002_sim_tables.sql` — `sim_account`, `sim_positions`, `sim_orders`
- `003_users_auth.sql` — `users` table, `user_id` columns on all existing tables
- `004_screener.sql` — `screener_presets`, `cached_bars` tables
- `005_backtests.sql` — `backtests` table
- `006_alerts.sql` — `alerts`, `alert_history` tables
- `007_refresh_tokens.sql` — `refresh_tokens` table
- Each migration is idempotent (`CREATE TABLE IF NOT EXISTS`, `ALTER TABLE ... ADD COLUMN` with try/catch for "column already exists")

**Add migration runner to `backend/database.py`:**
- New table `_migrations` (id INTEGER PRIMARY KEY, filename TEXT UNIQUE, applied_at TEXT)
- On startup (`init_db()`): scan `migrations/` directory, sort by filename, execute any not yet in `_migrations` table
- Log each migration applied

### 5. Docker Deployment

**Create `backend/Dockerfile`:**
```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

**Create `dashboard/Dockerfile`:**
```dockerfile
FROM node:20-slim AS build
WORKDIR /app
COPY package*.json .
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
```

**Create `dashboard/nginx.conf`:**
```nginx
server {
    listen 80;
    server_name _;
    root /usr/share/nginx/html;
    index index.html;

    # SPA routing — try file, then directory, then fallback to index.html
    location / {
        try_files $uri $uri/ /index.html;
    }

    # Proxy API requests to backend
    location /api/ {
        proxy_pass http://backend:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # Proxy WebSocket requests to backend
    location /ws {
        proxy_pass http://backend:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

**Create `docker-compose.yml` at project root:**
```yaml
version: "3.8"
services:
  backend:
    build: ./backend
    ports:
      - "8000:8000"
    volumes:
      - ./data:/app/data
    env_file:
      - .env
    environment:
      - DB_PATH=/app/data/trading_bot.db
    restart: unless-stopped

  frontend:
    build: ./dashboard
    ports:
      - "80:80"
    depends_on:
      - backend
    restart: unless-stopped
```

**Update `.env.example` at project root** with ALL env vars from all stages:
```env
# ── Environment ──────────────────────────────────────────────────────────
ENVIRONMENT=dev                    # dev | staging | production

# ── Auth / JWT ───────────────────────────────────────────────────────────
JWT_SECRET=change-me-in-production  # REQUIRED in production (no default)
JWT_ALGORITHM=HS256
JWT_ACCESS_EXPIRE_MINUTES=15
JWT_REFRESH_EXPIRE_DAYS=7

# ── Security ─────────────────────────────────────────────────────────────
CORS_ORIGINS=http://localhost:5173,http://localhost:8000
RATE_LIMIT_PER_MINUTE=60

# ── IBKR connection ─────────────────────────────────────────────────────
IBKR_HOST=127.0.0.1
IBKR_PORT=7496
IBKR_CLIENT_ID=1
IS_PAPER=true

# ── Simulation ───────────────────────────────────────────────────────────
SIM_MODE=false
SIM_INITIAL_CASH=100000.0
SIM_COMMISSION=1.0

# ── Mock data ────────────────────────────────────────────────────────────
MOCK_MODE=true

# ── Bot ──────────────────────────────────────────────────────────────────
BOT_INTERVAL_SECONDS=60
RECONNECT_INTERVAL=30

# ── Database ─────────────────────────────────────────────────────────────
DB_PATH=trading_bot.db

# ── API server ───────────────────────────────────────────────────────────
HOST=0.0.0.0
PORT=8000

# ── Dashboard ────────────────────────────────────────────────────────────
DASHBOARD_BUILD_DIR=../dashboard/dist
```

### 6. Frontend Auth Flow

**Create `dashboard/src/pages/LoginPage.tsx`:**
- Email input + password input + "Sign In" button
- Link to register: "Don't have an account? Create one"
- On submit: call `POST /api/auth/login`, store tokens, redirect to dashboard
- Error display below form (invalid credentials, network error)
- Styled with terminal theme (dark card on dark background, green accent button)

**Create `dashboard/src/pages/RegisterPage.tsx`:**
- Email input + password input + confirm password input + "Create Account" button
- Link to login: "Already have an account? Sign in"
- Client-side validation: email format, password min 8 chars, passwords match
- On submit: call `POST /api/auth/register`, store tokens, redirect to dashboard
- Error display below form

**Create `dashboard/src/components/auth/AuthGuard.tsx`:**
- Wraps all routes except login/register
- On mount: check if access token exists in localStorage and is not expired (decode JWT payload, check `exp` claim)
- If no valid token: redirect to `/login`
- If token expires within 2 minutes: silently refresh using refresh token
- Render children only when authenticated

**Create `dashboard/src/components/auth/AuthProvider.tsx`:**
- React context providing: `{user, isAuthenticated, login, register, logout, refreshToken}`
- Store `access_token` and `refresh_token` in localStorage
- `login(email, password)` — calls API, stores tokens, sets user
- `register(email, password)` — calls API, stores tokens, sets user
- `logout()` — calls API logout, clears localStorage, redirect to login
- Auto-refresh: set a timer to refresh the access token 1 minute before expiry
- On app init: attempt to load user from stored token (call `GET /api/auth/me`)

**Create `dashboard/src/services/auth.ts`:**
- `loginApi(email, password)` — `POST /api/auth/login`
- `registerApi(email, password)` — `POST /api/auth/register`
- `refreshApi(refreshToken)` — `POST /api/auth/refresh`
- `logoutApi(refreshToken)` — `POST /api/auth/logout`
- `fetchMe()` — `GET /api/auth/me`
- All return typed responses

**Modify `dashboard/src/services/api.ts`:**
- In the `req()` function: read access token from localStorage, attach `Authorization: Bearer <token>` header to all requests
- On 401 response: attempt to refresh the token using the stored refresh token. If refresh succeeds, retry the original request. If refresh fails, clear tokens from localStorage and redirect to `/login` (use `window.location.href` to force full navigation).
- Do NOT attach Authorization header to `/api/auth/login` or `/api/auth/register` requests

**Add user menu to `dashboard/src/components/layout/Header.tsx`:**
- After the clock element: add a user avatar button showing first letter of email (e.g., "D" for demo@local) in a circular badge
- On click: dropdown menu with "Settings" link and "Logout" button
- Logout calls `AuthProvider.logout()`
- Add sun/moon theme toggle icon button to the left of the user avatar

### 7. Light/Dark Theme Toggle

**Modify `dashboard/tailwind.config.ts`:**
- `darkMode: 'class'` is already set
- Add light theme color overrides alongside terminal colors. Define a `light` color namespace:
  ```
  light: {
    bg:       '#f8f9fb',   // page background
    surface:  '#ffffff',   // card / panel surface
    elevated: '#f0f2f5',   // modals, dropdowns
    border:   '#e2e8f0',   // borders
    muted:    '#edf2f7',   // subtle fills
    input:    '#ffffff',   // input fields
    text:     '#1a202c',   // primary text
    dim:      '#718096',   // secondary text
    ghost:    '#a0aec0',   // placeholder text
  }
  ```

**Modify `dashboard/src/index.css`:**
- Add light mode base styles. When `<html>` does NOT have `class="dark"`, use light background/text colors:
  ```css
  html:not(.dark) body, html:not(.dark) #root {
    background: #f8f9fb;
    color: #1a202c;
  }
  ```
- Light scrollbar styles
- Default is dark (add `class="dark"` to `<html>` in `index.html`)

**Theme toggle logic:**
- Toggle `dark` class on `document.documentElement`
- Persist theme choice in user settings via `PUT /api/settings` (key: `theme`, value: `"dark"` or `"light"`)
- On app load: read theme from user settings, apply `dark` class accordingly
- Default: `"dark"` (current theme)
- Add toggle in Settings page (radio or select: Dark / Light)
- Add toggle in Header (sun icon when dark, moon icon when light)

### 8. Code Splitting & Performance

**Modify `dashboard/src/App.tsx`:**
- Use `React.lazy()` for ALL page components:
  ```tsx
  const Dashboard = React.lazy(() => import('@/pages/Dashboard'))
  const TradeBotPage = React.lazy(() => import('@/pages/TradeBotPage'))
  const MarketPage = React.lazy(() => import('@/pages/MarketPage'))
  const SimulationPage = React.lazy(() => import('@/pages/SimulationPage'))
  const ScreenerPage = React.lazy(() => import('@/pages/ScreenerPage'))
  const BacktestPage = React.lazy(() => import('@/pages/BacktestPage'))
  const AlertsPage = React.lazy(() => import('@/pages/AlertsPage'))
  const RulesPage = React.lazy(() => import('@/pages/RulesPage'))
  const AnalyticsPage = React.lazy(() => import('@/pages/AnalyticsPage'))
  const SettingsPage = React.lazy(() => import('@/pages/SettingsPage'))
  const LoginPage = React.lazy(() => import('@/pages/LoginPage'))
  const RegisterPage = React.lazy(() => import('@/pages/RegisterPage'))
  ```
- Wrap each lazy page in `<Suspense fallback={<PageSkeleton />}>` where `PageSkeleton` is an animated loading skeleton
- Add login/register routes that render WITHOUT Layout/AuthGuard
- All other routes render inside AuthGuard + Layout

**Modify `dashboard/vite.config.ts`:**
- Add `build.rollupOptions.output.manualChunks`:
  ```ts
  manualChunks: {
    vendor: ['react', 'react-dom', 'react-router-dom'],
    charts: ['lightweight-charts'],
    state: ['zustand'],
  }
  ```

### 9. Responsive Mobile Layout

**Modify `dashboard/src/components/layout/Sidebar.tsx`:**
- On screens < 768px (`md` breakpoint): hide the side navigation entirely
- Show a fixed bottom tab bar instead: icon-only nav items in a horizontal row at the bottom of the screen
- Bottom bar: same NAV_ITEMS icons, active state highlighted, no labels (icon only)
- Use Tailwind responsive classes: `hidden md:flex` for sidebar, `flex md:hidden` for bottom bar
- Watchlist section hidden on mobile

**Modify `dashboard/src/components/layout/Layout.tsx`:**
- Adjust layout: on mobile, main content takes full width, bottom tab bar sits fixed at the bottom
- Add `pb-16 md:pb-0` padding to main content on mobile to avoid bottom bar overlap

**General responsive adjustments:**
- Charts stack vertically on narrow screens (single column)
- Tables become horizontally scrollable on mobile (`overflow-x-auto`)
- KPI grids: `grid-cols-2` on mobile, `grid-cols-4` on desktop
- Forms: single column on mobile, multi-column on desktop

### 10. API Documentation

**Modify `backend/main.py`:**
- Add `tags` to the FastAPI app:
  ```python
  tags_metadata = [
      {"name": "auth", "description": "Authentication & user management"},
      {"name": "account", "description": "Account summary & positions"},
      {"name": "market", "description": "Market data, quotes & bars"},
      {"name": "rules", "description": "Automation rule CRUD"},
      {"name": "bot", "description": "Trading bot control"},
      {"name": "simulation", "description": "Virtual trading simulation & replay"},
      {"name": "backtest", "description": "Strategy backtesting engine"},
      {"name": "screener", "description": "Stock screener & scanner"},
      {"name": "alerts", "description": "Price & technical alerts"},
      {"name": "analytics", "description": "Portfolio analytics & risk metrics"},
      {"name": "settings", "description": "User settings & preferences"},
  ]
  ```
- Add `openapi_tags=tags_metadata` to `FastAPI()` constructor
- Add `tags=["auth"]`, `tags=["market"]`, etc. to every `@app.get()`/`@app.post()` decorator
- Add `summary` and `description` parameters or docstrings to every endpoint
- Verify Swagger UI at `/docs` shows clean, organized, grouped documentation

### 11. Environment Config

**Modify `backend/config.py`:**
- Add these config fields to the `Config` class:
  ```python
  ENVIRONMENT: str = os.getenv("ENVIRONMENT", "dev")  # dev | staging | production
  JWT_SECRET: str = os.getenv("JWT_SECRET", "")
  JWT_ALGORITHM: str = os.getenv("JWT_ALGORITHM", "HS256")
  JWT_ACCESS_EXPIRE_MINUTES: int = int(os.getenv("JWT_ACCESS_EXPIRE_MINUTES", "15"))
  JWT_REFRESH_EXPIRE_DAYS: int = int(os.getenv("JWT_REFRESH_EXPIRE_DAYS", "7"))
  CORS_ORIGINS: str = os.getenv("CORS_ORIGINS", "http://localhost:5173,http://localhost:8000")
  RATE_LIMIT_PER_MINUTE: int = int(os.getenv("RATE_LIMIT_PER_MINUTE", "60"))
  ```
- In `JWT_SECRET` handling: if `ENVIRONMENT == "production"` and `JWT_SECRET` is empty, raise an error at startup (do NOT use a default secret in production). In `dev`/`staging`, auto-generate a random secret if not set (with a log warning).
- Structured logging setup: JSON format (`json.dumps` log entries) when `ENVIRONMENT == "production"`, human-readable single-line when `dev`.

### 12. Tests (Backend)

**Create `backend/tests/test_auth_flow.py`:**
- Test full auth flow: register new user -> login -> access protected endpoint (`GET /api/auth/me`) -> refresh token -> logout -> verify refresh token is invalidated
- Test registration validation: reject short password, reject duplicate email, reject invalid email format
- Test login failure: wrong password returns 401, non-existent email returns 401
- Test token expiry: expired access token returns 401
- Test unauthenticated access: requests without token to protected endpoints return 401

**Create `backend/tests/test_rate_limit.py`:**
- Test rate limiting: make `RATE_LIMIT_PER_MINUTE + 1` requests within a minute, verify last request returns 429
- Test `Retry-After` header is present on 429 response
- Test rate limit resets after window expires

**Create `backend/tests/test_user_isolation.py`:**
- Register user A and user B
- User A creates a rule, user B creates a different rule
- User A fetches rules -> sees only their own
- User B fetches rules -> sees only their own
- Repeat for: trades, alerts, backtests, screener presets
- Verify user A cannot access user B's data by ID (returns 404 or 403)

**Use `pytest` + `httpx` (FastAPI TestClient). Configure test database (separate SQLite file) via environment variable override.**

## Dependencies to Install

**Backend** (add to `requirements.txt` if not already present):
```
python-jose[cryptography]>=3.3.0
passlib[bcrypt]>=1.7.4
httpx>=0.27.0
pytest>=8.0.0
pytest-asyncio>=0.23.0
```
Note: `python-jose` and `passlib[bcrypt]` should already be in requirements.txt from Stage 1. Verify and add `httpx`, `pytest`, `pytest-asyncio` if missing.

**Frontend** (no new npm packages needed — all UI is built with existing TailwindCSS + react-router-dom):

## Files to Create
- `backend/middleware.py`
- `backend/migrations/001_initial_schema.sql`
- `backend/migrations/002_sim_tables.sql`
- `backend/migrations/003_users_auth.sql`
- `backend/migrations/004_screener.sql`
- `backend/migrations/005_backtests.sql`
- `backend/migrations/006_alerts.sql`
- `backend/migrations/007_refresh_tokens.sql`
- `backend/Dockerfile`
- `backend/tests/test_auth_flow.py`
- `backend/tests/test_rate_limit.py`
- `backend/tests/test_user_isolation.py`
- `dashboard/Dockerfile`
- `dashboard/nginx.conf`
- `docker-compose.yml` (project root)
- `.env.example` (project root — updated with ALL env vars)
- `dashboard/src/pages/LoginPage.tsx`
- `dashboard/src/pages/RegisterPage.tsx`
- `dashboard/src/components/auth/AuthGuard.tsx`
- `dashboard/src/components/auth/AuthProvider.tsx`
- `dashboard/src/services/auth.ts`

## Files to Modify
- `backend/auth.py` — complete with register, login, refresh, logout, JWT token management, password hashing
- `backend/config.py` — add ENVIRONMENT, JWT_SECRET, JWT_ALGORITHM, JWT_ACCESS_EXPIRE_MINUTES, JWT_REFRESH_EXPIRE_DAYS, CORS_ORIGINS, RATE_LIMIT_PER_MINUTE
- `backend/main.py` — add CORS middleware, rate limiter, request logger, auth guards on all endpoints, migration runner on startup, API tags + descriptions for all endpoints
- `backend/database.py` — add migration runner logic (`_migrations` table, scan + apply SQL files), add `refresh_tokens` CRUD (store hash, check revocation, cleanup expired)
- `backend/models.py` — add LoginRequest, RegisterRequest, TokenResponse, RefreshRequest, LogoutRequest Pydantic models
- `backend/requirements.txt` — ensure httpx, pytest, pytest-asyncio are listed
- `dashboard/src/App.tsx` — wrap with AuthProvider, add AuthGuard around protected routes, add login/register routes, React.lazy() all page imports, Suspense fallback
- `dashboard/src/services/api.ts` — add Authorization Bearer header to req(), add 401 interceptor with token refresh + retry, skip auth header for login/register
- `dashboard/src/store/index.ts` — add useAuthStore: `{user, accessToken, refreshToken, isAuthenticated, theme, setUser, setTokens, clearAuth, setTheme}`
- `dashboard/src/types/index.ts` — add User, TokenResponse, AuthState types; update AppRoute to include `'login' | 'register' | 'screener' | 'backtest' | 'alerts' | 'analytics'`
- `dashboard/src/components/layout/Header.tsx` — add theme toggle icon (sun/moon), add user avatar dropdown with Settings + Logout
- `dashboard/src/components/layout/Sidebar.tsx` — add responsive bottom tab bar for mobile, hide sidebar on <768px
- `dashboard/src/components/layout/Layout.tsx` — adjust for mobile: full-width content, bottom padding for tab bar
- `dashboard/src/main.tsx` — wrap App with AuthProvider
- `dashboard/tailwind.config.ts` — add `light` color namespace alongside terminal colors (already has `darkMode: 'class'`)
- `dashboard/src/index.css` — add light mode base styles (background, text, scrollbars), keep dark as default
- `dashboard/index.html` — add `class="dark"` to `<html>` element for default dark theme
- `dashboard/vite.config.ts` — add manualChunks for code splitting (vendor, charts, state)
- `dashboard/src/pages/SettingsPage.tsx` — add theme toggle section (Dark/Light radio buttons or toggle switch)
- `backend/.env.example` — update with all new env vars (JWT_SECRET, CORS_ORIGINS, RATE_LIMIT_PER_MINUTE, ENVIRONMENT)

## Definition of Done
1. User can register with email/password via `POST /api/auth/register` and receives JWT tokens
2. User can login with email/password via `POST /api/auth/login` and receives JWT tokens
3. Unauthenticated requests to `/api/*` return 401 (except `/api/auth/login`, `/api/auth/register`, `/api/status`)
4. Access token auto-refreshes in the frontend before expiry (silent refresh via refresh token)
5. Logout invalidates the refresh token in the database
6. User A cannot see User B's rules, trades, alerts, backtests, screener presets, or analytics
7. Rate limiting returns 429 with `Retry-After` header after exceeding `RATE_LIMIT_PER_MINUTE` requests
8. CORS configured correctly: frontend on `:5173` can reach backend on `:8000`
9. Light/dark theme toggle works from both Settings page and Header icon, persists in user settings
10. All pages lazy-load with React.lazy + Suspense (verify in browser Network tab: separate JS chunks)
11. `docker compose up` builds both services and serves the full app at `http://localhost:80`
12. Sidebar collapses to bottom tab bar on mobile (<768px)
13. Swagger UI at `/docs` shows all endpoints organized by tag with descriptions
14. All previous features still work: charting, screener, backtesting, alerts, rules, analytics, simulation, IBKR integration
15. `pytest backend/tests/` passes: auth flow, rate limiting, and user isolation tests all green

## Important Notes
- Auth scaffold (users table, user_id columns on all tables) already exists from Stage 1 — do NOT recreate or duplicate. Extend what is there.
- This is the CAPSTONE stage — every feature from stages 1-7 must still work after these changes. Do not break existing functionality.
- `JWT_SECRET` must be configurable via env var, NOT hardcoded. In production, require it (fail startup if missing). In dev, auto-generate with a warning.
- In dev mode, auto-create a demo user if no users exist on startup (backward compatibility with the Stage 1 demo approach so existing development workflows are not disrupted).
- The migration system should be simple: numbered SQL files in `backend/migrations/` + a `_migrations` tracking table. Do NOT use Alembic.
- Each migration SQL file must be idempotent (safe to run multiple times). Use `CREATE TABLE IF NOT EXISTS` and handle "column already exists" errors gracefully.
- Test CORS with actual cross-origin requests: frontend on `:5173` calling backend on `:8000`.
- Docker: mount `./data` volume for SQLite persistence outside the container. The DB file must survive container restarts.
- `dashboard/nginx.conf` must handle SPA routing (`try_files $uri /index.html`) AND proxy `/api` + `/ws` to the backend service.
- For the theme toggle: the current dark terminal theme is the default. Light theme should be professional (white/gray backgrounds, dark text, colored accents) — not just an inverted terminal.
- All Pydantic models for auth requests/responses should have proper validation constraints (email format, password min length, etc.).
- The refresh token stored in the database should be a SHA-256 hash, never the raw token. The raw token is only sent to the client.
- Rate limiting state is in-memory (dict). It resets on server restart. This is acceptable for single-instance deployment.
- Keep the terminal dark theme consistent for all new auth UI components (LoginPage, RegisterPage use the same design language).
- Test everything with `SIM_MODE=true` and `MOCK_MODE=true` (no IBKR needed).

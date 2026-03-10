# Stage Plan: TradeBot Dashboard → Production Trading Platform

**Created:** 2026-02-24
**Project:** TradeBot Dashboard (`trading-dashboard` v2.0.0)
**Stack:** React 18, TypeScript 5.5, Vite 5.4, Zustand 4.5, Tailwind 3.4, lightweight-charts 4.2, FastAPI (backend)

## Vision

Evolve the working prototype (FastAPI backend + React dashboard + IBKR integration) into a serious, publishable trading platform focused on Stocks & ETFs, manual trading with powerful tools, and critical backtesting capability. Personal use now, product later.

## Key Design Decisions (Applied Across All Stages)

- **Auth scaffolding in Stage 1** — create users table + user_id columns from the start with a default "demo" user. Stage 8 becomes security hardening, not schema refactoring.
- **Settings as JSON column** — settings stored as a JSON blob per user, not strict key-value. Trading platforms accumulate many unstructured preferences.
- **Event-driven backtesting** — bar-by-bar (not vectorized) to model slippage/fills correctly. Indicator warmup periods enforced. No look-ahead bias.
- **Data caching strategy** — cached bar data in DB for screener. Batch scheduled updates, not on-demand fetches. Respect IBKR (100 req/s) and Yahoo Finance rate limits.
- **Execution-agnostic rule engine** — design rule interface so the same rules work in live, paper, sim, and backtest. Backtest-to-live bridge from day one.
- **Testing per stage** — each stage includes unit tests for new backend logic and API contract tests (FastAPI + pytest).

---

## Stage Dependency Graph

```
Stage 1 (Foundation + Auth Scaffold)
  ├── Stage 2a (Chart Core + Volume)
  │     ├── Stage 2b (Drawing Tools)
  │     └── Stage 2c (Multi-Pane + Sync)
  │           ├── Stage 3 (Screener) — needs charting destination
  │           ├── Stage 5 (Alerts) — needs chart right-click
  │           └── Stage 7 (Analytics) — needs chart components
  ├── Stage 4 (Backtesting) — needs indicators + rule engine
  │     └── Stage 6 (Rule Builder) — needs "Backtest this rule" link
  └── Stage 8 (Production Hardening) — depends on ALL
```

---

## Stage 1: Foundation, Auth Scaffold & Polish

**Type:** refactoring + backend-endpoint
**Dependencies:** None
**Status:** Pending

### Goal

Fix gaps, add error handling, toast notifications, settings, and auth skeleton — the infrastructure every other stage depends on.

### Backend

- New `backend/auth.py` — user model, users table (id, email, password_hash, created_at, settings JSON). Seed a default "demo" user on first run. Simple JWT `login()`/`verify_token()` — no registration flow yet (Stage 8).
- Add `user_id TEXT` column to ALL existing tables (rules, trades, sim_account, sim_positions, sim_orders). Default to demo user ID for existing data.
- New `backend/settings.py` — user preferences as JSON blob in `users.settings` column
- Endpoints: `GET/PUT /api/settings`, `GET /api/auth/me`
- Harden error responses with consistent `{error: string, detail: string}` JSON format
- Basic request logging middleware
- Tests: pytest tests for settings CRUD, auth token generation/validation, error response format

### Frontend

- `ToastProvider.tsx` + `useToast` hook — global notification system (success/error/warning/info)
- `ErrorBoundary.tsx` — catch React crashes gracefully with fallback UI
- `SettingsPage.tsx` — manage preferences (default symbol, bar size, theme, bot interval)
- Wire toasts into ALL existing API calls (order placement, bot toggle, IBKR connect/disconnect)
- Add loading skeletons to Dashboard, TradeBotPage, MarketPage
- Add Authorization header interceptor in `api.ts` (uses demo token for now, real auth in Stage 8)

### Deliverables

- `backend/auth.py` — user model + JWT helpers
- `backend/settings.py` — settings CRUD
- `src/components/ToastProvider.tsx` + `src/hooks/useToast.ts`
- `src/components/ErrorBoundary.tsx`
- `src/pages/SettingsPage.tsx` (full replacement of stub)
- Updated `src/services/api.ts` with auth header + toast wiring
- Loading skeleton components
- pytest test files

### Done When

Every user action shows feedback (toast), errors are caught gracefully, settings page works, users table exists with demo user, all tables have user_id, pytest passes.

### Hands Off to Stage 2

Auth infrastructure, toast system, error boundaries, and settings persistence ready for all subsequent stages to use.

---

## Stage 2a: Chart Core & Volume

**Type:** frontend-component + backend-endpoint
**Dependencies:** Stage 1
**Status:** Pending

### Goal

Upgrade the chart to a professional-grade tool — the centerpiece of manual trading.

### Backend

- Add `GET /api/market/{symbol}/indicators` — compute indicators server-side
- Extend yahoo bars endpoint with more intervals/periods

### Frontend

- `ChartToolbar.tsx` — timeframe buttons, chart type toggle (candle/line/area/baseline), indicator quick-add dropdown
- `VolumePanel.tsx` — volume histogram below main chart (separate pane)
- Refactor `TradingChart.tsx` — split into chart core + toolbar + pane manager
- Chart annotations API: markers for buy/sell signals (used later by backtest + alerts)

### Deliverables

- `src/components/chart/ChartToolbar.tsx`
- `src/components/chart/VolumePanel.tsx`
- Refactored `src/components/chart/TradingChart.tsx`
- Chart annotations utility
- Backend indicator endpoint

### Done When

Chart has volume pane, chart type switching, clean toolbar, and annotation capability.

### Hands Off to Stage 2b

Chart core with annotation API and pane architecture ready for drawing tools and multi-pane.

---

## Stage 2b: Drawing Tools

**Type:** frontend-component
**Dependencies:** Stage 2a
**Status:** Pending

### Goal

Add essential drawing tools for technical analysis.

### Frontend

- `DrawingTools.tsx` — horizontal lines, trendlines, Fibonacci retracement
- Canvas overlay architecture on top of lightweight-charts (or evaluate lightweight-charts plugins)
- Drawing persistence (save/load drawings per symbol in settings JSON)

### Deliverables

- `src/components/chart/DrawingTools.tsx`
- Canvas overlay system
- Drawing serialization/persistence logic

### Done When

User can draw horizontal lines, trendlines, and Fib retracement on any chart. Drawings persist across sessions.

### Hands Off to Stage 2c

Drawing layer complete, ready for multi-pane layout integration.

---

## Stage 2c: Multi-Pane & Crosshair Sync

**Type:** frontend-component
**Dependencies:** Stage 2a
**Status:** Pending

### Goal

Professional multi-pane chart layout with synchronized crosshairs.

### Frontend

- Multi-pane layout: price chart + volume + oscillator panels stacked vertically
- Crosshair sync across all panes (shared time axis)
- Resizable pane heights (drag dividers)
- Refine `IndicatorPanel.tsx` to work as independent synced panes

### Deliverables

- Multi-pane container component
- Crosshair sync logic
- Resizable pane dividers
- Updated `src/components/chart/IndicatorPanel.tsx`

### Done When

RSI/MACD panels sit below price chart with synced crosshair, panes are resizable.

### Tests (All Stage 2)

Snapshot tests for chart component structure, API contract test for indicator endpoint.

### Hands Off to Stages 3, 5, 7

Complete charting system ready for screener result navigation, alert chart integration, and analytics charts.

---

## Stage 3: Stock Screener & Scanner

**Type:** frontend-component + backend-endpoint
**Dependencies:** Stage 2c (charting destination for results)
**Status:** Pending

### Goal

Find trading opportunities by scanning stocks against technical/fundamental filters.

### Backend

- New `backend/screener.py` — bulk scan engine with server-side caching:
  - Cache bar data in DB/memory for universe symbols (refresh on schedule, not per-request)
  - Batch yfinance downloads (`yf.download(tickers=[...])`) to minimize API calls
  - Respect rate limits: throttle requests, fallback gracefully
- New DB table `screener_presets` (user_id, name, filters JSON)
- Endpoints: `POST /api/screener/scan`, `GET/POST/DELETE /api/screener/presets`
- Built-in presets: "RSI Oversold", "Golden Cross", "Volume Breakout"
- Static universe JSON files for S&P 500 and NASDAQ 100 symbol lists (refreshed periodically)
- Tests: pytest for scan filter logic, rate limit handling

### Frontend

- `ScreenerPage.tsx` — main screener page
- `FilterBuilder.tsx` — visual filter rows: [Indicator] [Operator] [Value] with +/- buttons
- `ScanResultsTable.tsx` — sortable table (symbol, price, change%, RSI, volume)
- `PresetSelector.tsx` — save/load filter configurations
- `UniverseSelector.tsx` — choose S&P 500, NASDAQ 100, ETFs, or custom list
- Loading state with progress indication (scans can take 10-30s for large universes)
- Click a result row to navigate to Market chart with that symbol

### Deliverables

- `backend/screener.py`
- Universe JSON files (S&P 500, NASDAQ 100)
- `src/pages/ScreenerPage.tsx`
- `src/components/screener/FilterBuilder.tsx`
- `src/components/screener/ScanResultsTable.tsx`
- `src/components/screener/PresetSelector.tsx`
- `src/components/screener/UniverseSelector.tsx`
- pytest test files

### Done When

Scan S&P 500 for "RSI < 30" returns sorted results. Cached data serves subsequent scans fast. Rate limits never exceeded.

### Hands Off to Stage 4

Screener infrastructure (FilterBuilder pattern) reusable for strategy builder.

---

## Stage 4: Backtesting Engine (CRITICAL)

**Type:** backend-endpoint + frontend-component
**Dependencies:** Stage 1 (indicators + rule engine)
**Status:** Pending

### Goal

Test strategies against historical data with equity curves, trade logs, and performance metrics.

### Architecture Decisions

- **Event-driven (bar-by-bar)** — not vectorized. Processes each bar sequentially to model realistic execution: evaluate conditions with data available at that point, place orders, track fills.
- **Indicator warmup** — skip first N bars per indicator (e.g., SMA(200) needs 200 bars before valid signal). No signals generated during warmup period.
- **Look-ahead bias prevention** — rule engine receives `df[:current_bar_index+1]` slice only. Cannot peek at future bars.
- **Execution-agnostic** — backtester uses the same `_evaluate_condition()` as live bot, ensuring backtest results approximate live behavior.

### Backend

- New `backend/backtester.py` — bar-by-bar engine:
  - Iterates bars chronologically
  - At each bar: slice DataFrame up to current index, compute indicators, evaluate entry/exit conditions
  - Track position state (entry price, qty, commission)
  - Apply stop-loss / take-profit if set
  - Compute equity curve (mark-to-market after each bar)
- New DB table `backtests` (id, user_id, name, strategy_data JSON, result_data JSON, created_at)
- Endpoints: `POST /api/backtest/run`, `GET /api/backtest/history`, `GET /api/backtest/{id}`, `POST /api/backtest/save`
- Metrics: total return, CAGR, Sharpe (252 trading days), Sortino, max drawdown, win rate, profit factor, # trades, avg win/loss, longest winning/losing streak
- Refactor `rule_engine._evaluate_condition()` to accept a DataFrame slice parameter (backward-compatible: defaults to full df for live use)
- Tests: pytest for backtester with known historical data and expected results. Test indicator warmup. Test look-ahead bias prevention.

### Frontend

- `BacktestPage.tsx` — strategy builder + results dashboard
- `StrategyBuilder.tsx` — entry/exit condition builder (reuses Condition model from rules)
- `BacktestParams.tsx` — symbol, date range, initial capital, position size %, stop-loss/TP
- `EquityCurve.tsx` — line chart with buy-and-hold comparison overlay
- `MetricsPanel.tsx` — KPI grid (return, Sharpe, Sortino, drawdown, win rate, profit factor, etc.)
- `BacktestTradeLog.tsx` — trade table with entry/exit details, P&L per trade
- Trade entry/exit markers on the price chart (using Stage 2a annotation API)

### Deliverables

- `backend/backtester.py`
- DB migration for backtests table
- `src/pages/BacktestPage.tsx`
- `src/components/backtest/StrategyBuilder.tsx`
- `src/components/backtest/BacktestParams.tsx`
- `src/components/backtest/EquityCurve.tsx`
- `src/components/backtest/MetricsPanel.tsx`
- `src/components/backtest/BacktestTradeLog.tsx`
- pytest test files

### Done When

"Buy AAPL when RSI < 30, sell when RSI > 70" over 2Y daily data produces correct equity curve, all metrics, and marked-up chart. Indicator warmup enforced. No look-ahead bias.

### Hands Off to Stage 6

Backtesting engine ready for "Backtest this rule" integration from rule builder.

---

## Stage 5: Alerts & Notifications

**Type:** backend-endpoint + frontend-component
**Dependencies:** Stage 2c (chart right-click integration)
**Status:** Pending

### Goal

Never miss a move — alert when price/indicator conditions are met.

### Backend

- New `backend/alert_engine.py` — separate async loop (independent of bot runner). Runs on configurable interval.
  - For price alerts: check cached prices or poll yfinance
  - For technical alerts: fetch bars, compute indicators via `indicators.py`
  - When fired: mark triggered, log to history, broadcast `alert_fired` via WebSocket
  - Consider: if symbol count grows large, run as separate async worker
- New DB tables: `alerts` (user_id, data JSON), `alert_history` (alert_id, fired_at, data JSON)
- Endpoints: `GET/POST/PUT/DELETE /api/alerts`, `POST /api/alerts/{id}/toggle`, `GET /api/alerts/history`
- WebSocket event: `alert_fired` with `{alert_id, symbol, condition, price, timestamp}`
- Browser push notifications support (Web Push API)
- Tests: pytest for alert evaluation logic, fire/history logging

### Frontend

- `AlertsPage.tsx` — alert management (active list, create form, history tab)
- `AlertForm.tsx` — symbol + condition type (Price/RSI/MACD/etc.) + operator + value
- `AlertBell.tsx` — header bell icon with unread badge + recent alerts dropdown
- Wire `alert_fired` WS event to toast + bell badge + store
- Right-click chart price level to pre-fill alert form (integrates with Stage 2 chart)

### Deliverables

- `backend/alert_engine.py`
- DB migrations for alerts + alert_history tables
- `src/pages/AlertsPage.tsx`
- `src/components/alerts/AlertForm.tsx`
- `src/components/alerts/AlertBell.tsx`
- Updated `src/components/layout/Header.tsx` (bell icon)
- Updated `src/hooks/useWebSocket.ts` (alert_fired event)
- pytest test files

### Done When

Price alert "AAPL > $250" fires toast + bell notification + logged in history. Technical alert "SPY RSI < 30" works. Browser notification appears if permission granted.

### Hands Off to Stage 6

Alert system provides notification patterns reusable by rule builder.

---

## Stage 6: Strategy / Rule Builder UI

**Type:** frontend-component + backend-endpoint
**Dependencies:** Stage 4 (backtest integration)
**Status:** Pending

### Goal

Replace the "coming soon" rules page with a visual rule builder.

### Backend (minimal — rules CRUD already exists)

- Add `POST /api/rules/{id}/duplicate` — deep copy with new ID
- Add `GET /api/rules/{id}/preview` — evaluates conditions against current bars, returns `{conditions: [{indicator, current_value, target, met: bool}], would_fire: bool}`
- Tests: pytest for duplicate and preview endpoints

### Frontend

- `RulesPage.tsx` — full replacement of placeholder
- `RuleBuilder.tsx` — visual condition builder modal/slide-over
- `ConditionBlock.tsx` — [Indicator dropdown] [Params inputs] [Operator] [Value] row
  - Dynamic params: RSI shows length, MACD shows fast/slow/signal, BBANDS shows length/std/band
- `ConditionGroup.tsx` — AND/OR toggle with visual grouping
- `ActionConfig.tsx` — BUY/SELL, quantity (fixed or % of portfolio), order type, limit price
- `RulePreview.tsx` — live indicator values showing whether each condition currently passes
- `RuleTemplates.tsx` — one-click preset strategies (seed rules + "Bollinger Squeeze", "MACD Crossover", "Volume Breakout")
- "Backtest this rule" button — navigates to BacktestPage with conditions pre-filled (Stage 4 integration)
- Reuse FilterBuilder pattern from Stage 3 screener where possible

### Deliverables

- `src/pages/RulesPage.tsx` (full replacement)
- `src/components/rules/RuleBuilder.tsx`
- `src/components/rules/ConditionBlock.tsx`
- `src/components/rules/ConditionGroup.tsx`
- `src/components/rules/ActionConfig.tsx`
- `src/components/rules/RulePreview.tsx`
- `src/components/rules/RuleTemplates.tsx`
- pytest test files

### Done When

User can visually create a multi-condition AND/OR rule, see live preview, save it, the bot evaluates it when enabled. "Backtest this rule" works.

### Hands Off to Stage 7

Complete rule builder with backtest integration ready.

---

## Stage 7: Risk Management & Portfolio Analytics

**Type:** backend-endpoint + frontend-component
**Dependencies:** Stage 2c (chart components)
**Status:** Pending

### Goal

Understand exposure, risk, and P&L at a glance.

### Backend

- New `backend/analytics.py` — portfolio allocation, sector breakdown (yfinance ticker info, cached), correlation matrix (60-day returns)
- New `backend/risk.py` — beta (vs SPY), VaR (95%, historical simulation), Sharpe, Sortino, max drawdown, position sizing calculators (Kelly criterion, fixed fractional)
- Endpoints: `GET /api/analytics/portfolio`, `GET /api/analytics/risk`, `GET /api/analytics/pnl`, `POST /api/analytics/position-size`
- Extend trades: add `notes TEXT`, `tags TEXT` to JSON blob. `PUT /api/trades/{id}/annotate`
- Tests: pytest for risk metric calculations with known test data

### Frontend

- `AnalyticsPage.tsx` — portfolio analytics dashboard
- `AllocationChart.tsx` — pie/donut chart by symbol and sector (SVG-based, no extra dependency, or recharts if complexity warrants)
- `PnLChart.tsx` — daily P&L histogram + cumulative return line (lightweight-charts)
- `RiskMetricsGrid.tsx` — KPI cards (beta, VaR, Sharpe, Sortino, drawdown)
- `PositionSizer.tsx` — calculator: method selector (Kelly/fixed fractional), inputs, recommended size output
- `TradeJournal.tsx` — annotated trade log with inline note editing and tag chips

### Deliverables

- `backend/analytics.py`
- `backend/risk.py`
- `src/pages/AnalyticsPage.tsx`
- `src/components/analytics/AllocationChart.tsx`
- `src/components/analytics/PnLChart.tsx`
- `src/components/analytics/RiskMetricsGrid.tsx`
- `src/components/analytics/PositionSizer.tsx`
- `src/components/analytics/TradeJournal.tsx`
- pytest test files

### Done When

Analytics page shows allocation pie, risk metrics, P&L charts. Position sizer calculates correctly. Trade journal supports notes/tags.

### Hands Off to Stage 8

All features complete, ready for production hardening.

---

## Stage 8: Production Hardening & Deployment

**Type:** refactoring + integration
**Dependencies:** ALL previous stages
**Status:** Pending

### Goal

Full auth flow, security hardening, deployment config — ready to publish. (Auth schema already exists from Stage 1.)

### Backend

- Complete `backend/auth.py` — full registration flow, password reset, token refresh, bcrypt hardening
- New `backend/middleware.py` — CORS (configurable origins), rate limiting (per-user), structured logging
- New `backend/migrations/` — versioned SQL migration scripts for all schema changes across stages
- Harden all queries: ensure user_id scoping is enforced everywhere (audit pass)
- Dockerfile (backend), `docker-compose.yml` (backend + frontend nginx + reverse proxy)
- Environment-based config profiles (dev/staging/production)
- Add OpenAPI endpoint descriptions for all routes (FastAPI auto-docs)
- Tests: full integration test suite, auth flow tests, rate limit tests

### Frontend

- `LoginPage.tsx`, `RegisterPage.tsx` — clean auth forms
- `AuthGuard.tsx` — redirect to login if unauthenticated
- Complete `AuthProvider.tsx` — token storage, auto-refresh, 401 interceptor
- Light/dark theme toggle (Tailwind `darkMode: 'class'`, settings-persisted)
- `React.lazy()` + Suspense for all pages (code splitting)
- Responsive mobile layout (tablet-minimum)
- Performance audit: bundle size, lighthouse score

### Deliverables

- Complete auth system (login, register, password reset)
- `backend/middleware.py`
- `backend/migrations/` directory with all migrations
- `Dockerfile`, `docker-compose.yml`
- `src/pages/LoginPage.tsx`, `src/pages/RegisterPage.tsx`
- `src/components/auth/AuthGuard.tsx`, `src/components/auth/AuthProvider.tsx`
- Theme toggle system
- Code splitting for all pages
- Mobile-responsive layouts

### Done When

Register/login works, data is user-scoped and enforced, Docker builds run, light theme works, mobile layout is usable, all previous features intact, test suite green.

---

## Testing Strategy (Per Stage)

Each stage includes:
- **Unit tests (pytest):** indicator calculations, engine logic, API response contracts
- **API contract tests:** FastAPI TestClient for all new endpoints
- **Frontend smoke tests:** verify pages render without crash (optional per stage, mandatory Stage 8)
- **Manual verification:** specific "done when" scenarios tested end-to-end

## Execution Approach

For each stage:
1. Generate a **session prompt** (using `@session-prompt-generator`) with full context + previous handoff
2. Execute the stage in an **isolated session**
3. Generate a **handoff document** (using `@handoff-generator`) capturing what was built, tested, and what's next
4. The handoff feeds into the next stage's session prompt

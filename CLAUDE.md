# CLAUDE.md — TradeBot Dashboard

## Project Overview

**TradeBot Dashboard** is a Bloomberg-terminal-inspired React SPA for automated stock/crypto trading. It provides real-time market data, candlestick charting with technical indicators, an automated trading bot with rule-based execution, and a historical replay simulation engine.

The frontend is self-contained. It expects a **FastAPI backend** at the same origin (`/api/*`, `/ws`, `/ws/market-data`). When the backend is unreachable, the UI falls back to **client-side mock data** (GBM price simulation) automatically — no configuration needed.

## Tech Stack

| Layer | Technology |
|---|---|
| Framework | React 18 with TypeScript (strict mode) |
| Build | Vite 5 (`vite.config.ts`) |
| Styling | Tailwind CSS 3 with custom `terminal-*` design tokens |
| State | Zustand (5 stores, no Redux) |
| Charts | `lightweight-charts` v4 (TradingView) |
| Routing | Custom store-based routing (`useUIStore.activeRoute`) — not react-router-dom (dependency exists but is unused) |
| Date utils | `date-fns` |
| CSS utility | `clsx` for conditional class merging |

## Quick Start

```bash
npm install
npm run dev        # Vite dev server on http://localhost:5173
npm run build      # tsc + vite build → dist/
npm run typecheck  # tsc --noEmit (type checking only)
```

The dev server proxies `/api` → `http://localhost:8000` and `/ws` → `ws://localhost:8000` (configured in `vite.config.ts`).

## Directory Structure

```
src/
├── main.tsx                          # React entry point
├── App.tsx                           # Root component, route switch, status bootstrap
├── index.css                         # Tailwind directives + scrollbar/base resets
├── types/index.ts                    # All TypeScript interfaces and type aliases
├── store/index.ts                    # 5 Zustand stores (market, account, bot, sim, UI)
├── services/
│   ├── api.ts                        # REST client (thin fetch wrapper, all /api/* endpoints)
│   ├── ws.ts                         # WebSocket services (wsService + wsMdService singletons)
│   └── mockService.ts                # Client-side GBM price simulation (offline fallback)
├── hooks/
│   ├── useMarketData.ts              # Drives quotes, bars, account data into stores (REST + WS)
│   └── useWebSocket.ts               # Wires main WS events (bot, IBKR, replay) into stores
├── utils/
│   └── indicators.ts                 # Pure TS technical indicator calculations (SMA, EMA, BB, VWAP, RSI, MACD)
├── pages/
│   ├── Dashboard.tsx                 # Home: watchlist grid + chart + KPI rail
│   ├── MarketPage.tsx                # Full chart: timeframes, indicators, comparison overlay
│   ├── TradeBotPage.tsx              # Bot control: KPIs, toggle, quick order, positions, trade log
│   └── SimulationPage.tsx            # Replay engine: virtual account, positions, order history
├── components/
│   ├── layout/
│   │   ├── Layout.tsx                # Shell: sidebar + header + main area; wires global hooks
│   │   ├── Sidebar.tsx               # Nav, status pills (IBKR/BOT/SIM), quick watchlist
│   │   └── Header.tsx                # Page title, mode badges, IBKR/bot buttons, clock
│   ├── chart/
│   │   ├── TradingChart.tsx          # Candlestick + volume + overlays + live WS + replay
│   │   └── IndicatorPanel.tsx        # RSI / MACD oscillator sub-charts
│   ├── ticker/
│   │   ├── TickerCard.tsx            # Bloomberg-style asset card with flash animation
│   │   └── WatchlistGrid.tsx         # Card grid with tabs, sort, bulk add, remove
│   ├── tradebot/
│   │   ├── BotToggle.tsx             # Master on/off switch with live-trading confirmation
│   │   ├── KPICard.tsx               # Reusable metric card (label + value + color)
│   │   └── PositionsTable.tsx        # Open positions table (live + sim compatible)
│   ├── indicators/
│   │   └── IndicatorSelector.tsx     # Pill buttons to toggle overlays/oscillators
│   └── simulation/
│       └── SimController.tsx         # Floating replay controller (load, play/pause, speed)
```

## Architecture & Key Patterns

### State Management (Zustand)

All state lives in 5 Zustand stores in `src/store/index.ts`:

| Store | Purpose |
|---|---|
| `useMarketStore` | Quotes, OHLCV bars, comparison bars, watchlists, selected symbol, sort, indicators |
| `useAccountStore` | Account summary, positions, open orders, trade log |
| `useBotStore` | System status, IBKR connection, bot running state, rules, sim/mock mode flags |
| `useSimStore` | Simulation account, virtual positions/orders, replay playback state, replay bars |
| `useUIStore` | Sidebar collapsed, active route, order modal state |

Access pattern: `const value = useXxxStore((s) => s.field)` — always use selectors for render optimization.

### Routing

The app uses a **store-based route system**, not react-router. Routes are defined as the `AppRoute` union type:
```
'dashboard' | 'tradebot' | 'market' | 'simulation' | 'rules' | 'settings'
```
Navigation: `useUIStore.setRoute('market')`. The `PageSwitch` component in `App.tsx` renders the active page.

### Data Flow

1. **`Layout.tsx`** mounts two global hooks: `useWebSocket()` and `useMarketData()`
2. **`useMarketData`** drives all REST polling and WS subscriptions:
   - Quotes refresh every 5s via REST (`/api/watchlist`)
   - Account refreshes every 10s via REST (`/api/account/summary`)
   - Live price ticks arrive via `wsMdService` (per-symbol WebSocket)
   - Chart bars load on symbol/timeframe change via REST (`/api/yahoo/{symbol}/bars`)
3. **`useWebSocket`** connects to `/ws` for system events (IBKR state, bot cycle, replay bars)
4. **Mock fallback**: Every `try/catch` in data-loading code falls back to `mockService.ts` functions

### WebSocket Services (`src/services/ws.ts`)

Two singleton WebSocket clients:
- **`wsService`** → `/ws` — system events (IBKR state, bot status, order fills, replay bars)
- **`wsMdService`** → `/ws/market-data` — per-symbol live quote stream with auto-reconnect

Both have auto-reconnect (3s delay) and 25s ping keepalive.

### REST API Client (`src/services/api.ts`)

Thin `fetch` wrapper. All endpoints under `/api/*`:
- **Status**: `GET /api/status`, `GET /api/bot/status`
- **IBKR**: `POST /api/ibkr/connect`, `POST /api/ibkr/disconnect`
- **Account**: `GET /api/account/summary`, `GET /api/positions`, `GET /api/orders`, `GET /api/trades`
- **Market Data**: `GET /api/watchlist`, `GET /api/yahoo/{symbol}/bars`, `GET /api/market/{symbol}/price`
- **Simulation**: `GET|POST /api/simulation/*` (account, positions, orders, reset, order placement)
- **Playback**: `POST /api/simulation/playback/*` (load, play, pause, stop, speed)
- **Rules**: CRUD at `GET|POST|PUT|DELETE /api/rules/*`
- **Bot**: `POST /api/bot/start`, `POST /api/bot/stop`

### Technical Indicators (`src/utils/indicators.ts`)

Pure TypeScript, zero dependencies. Available indicators:
- **Overlays**: SMA(20), SMA(50), EMA(12), EMA(26), Bollinger Bands(20,2), VWAP
- **Oscillators**: RSI(14), MACD(12,26,9)

Registry: `INDICATOR_DEFS` array with id, label, type, and color. `IndicatorId` type = `'sma20' | 'sma50' | 'ema12' | 'ema26' | 'bb' | 'vwap' | 'rsi' | 'macd'`.

### Mock Service (`src/services/mockService.ts`)

Client-side GBM (Geometric Brownian Motion) simulation. Provides:
- `getMockQuote(symbol)` / `getMockQuotes(symbols[])` — full quote objects
- `getMockBars(symbol, numBars, barSeconds)` — OHLCV history
- `getMockAccount()` / `getMockSimAccount()` — static account snapshots

Pre-configured with 15 assets (AAPL, TSLA, NVDA, BTC-USD, ETH-USD, etc.) with realistic base prices, volatility (sigma), market caps, and volumes.

## Design System

### Theme: Bloomberg Terminal

Dark-only (`class="dark"` on `<html>`). All colors use the `terminal-*` Tailwind namespace defined in `tailwind.config.ts`:

| Token | Hex | Usage |
|---|---|---|
| `terminal-bg` | `#080d18` | Deepest background |
| `terminal-surface` | `#0e1726` | Cards, panels |
| `terminal-elevated` | `#131f33` | Modals, dropdowns |
| `terminal-border` | `#1c2e4a` | All borders |
| `terminal-text` | `#dce8f5` | Primary text |
| `terminal-dim` | `#5f7a9d` | Secondary text |
| `terminal-ghost` | `#384d6b` | Placeholder text |
| `terminal-green` | `#00e07a` | Gains, buy, positive |
| `terminal-red` | `#ff3d5a` | Losses, sell, negative |
| `terminal-blue` | `#4f91ff` | Accent, info, active states |
| `terminal-amber` | `#f59e0b` | Warnings, simulation badge |

### Typography

- **Sans**: Inter (headings, body)
- **Mono**: JetBrains Mono (prices, data, labels — nearly everything)
- Loaded via Google Fonts in `index.html`

### UI Conventions

- All labels: `text-[10px] font-mono text-terminal-ghost uppercase tracking-widest`
- Prices/numbers: `font-mono tabular-nums` (ensures aligned columns)
- Cards: `bg-terminal-surface border border-terminal-border rounded-lg p-3|p-4`
- Buttons: `text-xs font-mono px-N py-N rounded border border-terminal-border text-terminal-ghost hover:text-terminal-dim`
- Active/selected state: `border-terminal-blue/50 text-terminal-blue bg-terminal-blue/10`
- Status pills: `text-[10px] font-mono px-1.5 py-0.5 rounded font-semibold` with green/red/amber backgrounds
- Price flash: `animate-tick-up` / `animate-tick-down` (300ms color transitions defined in Tailwind config)

## Path Alias

`@/` maps to `./src/` (configured in both `tsconfig.json` and `vite.config.ts`). Always use `@/` imports:
```ts
import { useMarketStore } from '@/store'
import type { MarketQuote } from '@/types'
```

## Development Conventions

### Code Style

- **Functional components only** — no class components
- **Named exports for pages**, default exports for components
- Prefer `clsx()` for conditional Tailwind classes
- Inline SVG icons (no icon library)
- Format currency with `Intl.NumberFormat` or manual `toLocaleString` helpers (defined locally per component)
- Each file has a JSDoc block at the top describing purpose and features

### TypeScript

- Strict mode enabled
- `noUnusedLocals` and `noUnusedParameters` are disabled (relaxed)
- All domain types in `src/types/index.ts` — import with `import type { ... }`
- Store interfaces are co-located in `src/store/index.ts`

### File Organization

- One component per file
- Components are grouped by feature domain: `chart/`, `ticker/`, `tradebot/`, `indicators/`, `simulation/`, `layout/`
- Pages go in `src/pages/`
- Hooks in `src/hooks/`, utilities in `src/utils/`, services in `src/services/`

### State Updates

- Never mutate Zustand state directly — always use `set()` or spread patterns
- Use selectors: `useStore((s) => s.field)` not `useStore.getState().field` in components
- Trade log capped at 500 entries: `[t, ...s.trades].slice(0, 500)`
- Replay bars capped at 1000: `[...s.replayBars, bar].slice(-1000)`

### API Error Handling

Pattern used everywhere:
```ts
try {
  const data = await fetchSomething()
  setStoreData(data)
} catch {
  setStoreData(getMockData())  // fallback to mock
}
```

## Legacy Files

The following files in the project root are from an earlier vanilla JS version and are **not part of the current React app**:
- `trading.html` — standalone HTML trading page
- `trading.js` — vanilla JS trading logic (~91KB)
- `trading.css` — standalone CSS (~22KB)

These are not imported or bundled by Vite.

## Build & Deployment

- `npm run build` → runs `tsc` then `vite build` → outputs to `dist/`
- Source maps enabled in production (`sourcemap: true` in `vite.config.ts`)
- Output is a static SPA; deploy behind any web server or CDN
- Backend must serve `/api/*` and `/ws*` endpoints at the same origin (or configure CORS)

## Pages Overview

| Route | Page | Description |
|---|---|---|
| `dashboard` | `Dashboard.tsx` | Watchlist card grid + candlestick chart + account KPI sidebar |
| `market` | `MarketPage.tsx` | Full-screen chart with symbol search, 8 timeframes, indicator selector, comparison overlay |
| `tradebot` | `TradeBotPage.tsx` | Account KPIs, bot master toggle, quick order form, positions table, trade log |
| `simulation` | `SimulationPage.tsx` | Virtual account KPIs, replay chart, positions, order history, floating replay controller |
| `rules` | — | Placeholder ("coming soon") |
| `settings` | — | Placeholder ("coming soon") |

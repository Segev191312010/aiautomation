# AIAutomation — TradeBot Dashboard

An automated AI-assisted trading dashboard for U.S. equities and crypto with:

- 📊 Live charts (TradingView Lightweight Charts **or** embedded TradingView widget)
- 🔎 24/7 stock screener across S&P 500 / NASDAQ 100 / Russell 1000 universes
- 🔔 Configurable price & volume alerts with desktop notifications
- 🤖 Rules engine for automated entries/exits (RSI, SMA, EMA, MACD, Bollinger, ATR, Stochastic, Price)
- 🧪 Built-in paper-trading simulator (no real money risked)
- 🏦 IBKR integration scaffolding (host/port/client-id, paper-trade gating)
- 🛟 **Mock-mode fallback** — every API call gracefully falls back to an in-browser simulated backend when the FastAPI server is unavailable, so the UI is always usable

## Quick start (frontend only)

```bash
npm install
npm run dev
```

Open <http://localhost:5173>. Without a backend the app runs entirely in mock mode (synthetic prices, simulated fills, localStorage persistence). All rules, alerts, settings, simulated trades, and positions survive page reloads.

Add `?mock=1` to the URL to force mock mode even when a backend is running (useful for UI testing).

## Build

```bash
npm run typecheck   # tsc --noEmit
npm run build       # tsc && vite build → dist/
npm run preview     # serve the production build
```

## Architecture

| Layer | Tech |
| --- | --- |
| UI | React 18 + TypeScript + Tailwind CSS |
| State | Zustand stores (`src/store/index.ts`) |
| Charts | TradingView Lightweight Charts **or** TradingView Widget (Settings → Chart Engine) |
| Real-time | WebSocket `/ws/market-data` (mock fallback runs client-side GBM) |
| API client | `src/services/api.ts` — every call has a mock fallback in `mockBackend.ts` |
| Backend (optional) | FastAPI server expected at `localhost:8000` exposing `/api/*` and `/ws*` |

### Pages

- **Dashboard** — watchlist grid + main chart + account KPIs
- **TradeBot** — bot master toggle, KPIs, quick order, positions, recent trades
- **Market** — full-screen chart with timeframes, indicators, comparison overlay
- **Screener** — multi-universe scanner with price/%/volume filters and live refresh
- **Alerts** — CRUD UI for price/volume alerts, fires browser notifications + in-page toasts
- **Simulation** — virtual account, replay controller, P&L tracking
- **Rules** — automation rules CRUD (conditions + actions)
- **Settings** — IBKR config, data provider, chart engine, bot timing, screener universe

## Safety

This project will **never** place real trades unless:

1. A real FastAPI backend is running and replies to `/api/status` with `ibkr_connected: true` and `mock_mode: false`.
2. The user has explicitly enabled "Live" (paper mode toggled off in Settings → IBKR).
3. The user accepts the in-app confirmation dialog before flipping the master bot toggle.

In mock mode (the default), every order routes to the in-browser simulator and is recorded as a "simulated" trade. No external API receives a real order.

## Connecting a real IBKR backend

1. Install IB Gateway or Trader Workstation.
2. File → Global Configuration → API → Settings:
   - Enable ActiveX and Socket Clients
   - Trusted IP: `127.0.0.1`
   - Socket port: `7497` (paper) or `7496` (live)
3. Start your FastAPI backend on `localhost:8000` (this repo currently ships only the frontend; the backend is expected to expose the endpoints documented in `src/services/api.ts`).
4. In the dashboard → Settings → Interactive Brokers, confirm host/port and click **Connect to IBKR**.

## License

MIT.

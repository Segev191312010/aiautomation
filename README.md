# aiautomation — Trading Platform v2 Dashboard

A React 18 + TypeScript + Vite single-page dashboard for the trading platform's
FastAPI backend. Bloomberg-style dark "terminal" theme, Zustand state, Tailwind,
lightweight-charts. It talks to the same backend as the legacy `dashboard/` app
but is a cleaner v2 focused on the **AI automation** surface.

> Personal/dev tool. Runs against a backend you control — see the safety note below.

## Pages

| Route | Page | What it does |
|-------|------|--------------|
| `dashboard` | Dashboard | Overview / market snapshot |
| `tradebot` | TradeBot | Account KPIs, bot toggle, positions, trades, quick order |
| `market` | Market | Quotes, charts, indicators, watchlists |
| `simulation` | Simulation | Sim account + replay playback |
| `autopilot` | **Autopilot** | AI control panel: mode (OFF/PAPER/LIVE), kill-switch + daily-loss reset, engine KPIs, learned performance, AI/auto rules with promote/pause/retire, AI activity feed |
| `aisystem` | **AI System** | Observability view of the AI pipeline (router → optimizer/advisor → ledger → evaluator → learning loop) with live status |
| `rules` | Rules | Full rule CRUD: list, enable/disable, create/edit/delete |
| `settings` | Settings | System status, IBKR connect/disconnect, sim reset, autopilot config |

Routing is state-based via `useUIStore.activeRoute` (no react-router) — see `src/App.tsx`.

## Setup

```bash
npm install
cp .env.example .env.local      # then fill in VITE_JWT_BOOTSTRAP_SECRET
npm run dev                     # http://localhost:5174  (proxies /api + /ws → :8000)
```

The backend must be running on `http://localhost:8000` (Vite proxies `/api` and `/ws`
there — see `vite.config.ts`).

### Auth

Protected endpoints (`/api/autopilot/*`, `/api/rules`, …) require a Bearer token.
On startup the app mints one from the backend's loopback bootstrap endpoint, so
`.env.local` must set:

```
VITE_JWT_BOOTSTRAP_SECRET=<must match backend JWT_BOOTSTRAP_SECRET>
```

The `/api/auth/token` endpoint is loopback-only and refuses to issue tokens while
`AUTOPILOT_MODE=LIVE`, so this dev convenience never works against a live-authority
process. Without the secret, protected pages show an auth notice instead of 401-looping.
`.env.local` / `.env` are gitignored — never commit the secret.

## Scripts

```bash
npm run dev         # Vite dev server (port 5174)
npm run build       # tsc && vite build → dist/
npm run typecheck   # tsc --noEmit
npm test            # vitest (unit/component tests)
```

## Architecture

- `src/services/api.ts` — auth-aware fetch client (bootstrap token + `Authorization`
  header, 401 handling) plus all typed endpoint wrappers.
- `src/services/auth.ts` — `fetchAuthToken()` bootstrap.
- `src/services/ws.ts` + `src/hooks/useLiveStream.ts` — WebSocket live updates.
- `src/store/index.ts` — Zustand stores (market, account, bot, sim, UI).
- `src/types/index.ts` — shared API/domain types.
- `src/components/ui/` — terminal-themed primitives (Badge, Skeleton, Spinner, EmptyState, StatusDot).
- Tailwind theme tokens (`terminal-*`) live in `tailwind.config.ts`.

## ⚠️ Safety

This dashboard can flip autopilot mode, toggle/promote rules, and place orders against
whatever backend it points to. For development, run the backend in a **sandbox**
(`SIM_MODE=true`, `AUTOPILOT_MODE=PAPER`, a paper IBKR port, a throwaway `DB_PATH`) so
nothing touches a real brokerage account. Switching to `LIVE` mode trades real money.

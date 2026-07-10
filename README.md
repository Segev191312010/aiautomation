# TradeBot Trading Platform

TradeBot is a full-stack trading platform with a FastAPI backend, a React + TypeScript dashboard, Interactive Brokers connectivity, analytics, diagnostics, alerts, stock research, and an AI/autopilot control plane.

This README describes the system that is actually in the repo today.

> **Product status (2026-07-10):** The core platform is suitable for development,
> simulation, and controlled paper testing. It is not yet an installable desktop
> application or approved for unattended live-money use. Docker is optional for
> development/server-style deployment; it is not the target desktop launch path. See
> [Desktop Application Readiness Roadmap](docs/APPLICATION_READINESS_ROADMAP.md)
> for the verified gaps, target architecture, milestones, and release gates.

## Current baseline

As of 2026-07-10:

- Backend tests: `620/620` passing
- Dashboard typecheck: passing
- Dashboard build: passing
- Dashboard Vitest: `372/372` passing

## Tech stack

- Backend: Python 3.11+, FastAPI, aiosqlite, ib_insync
- Dashboard: React 18, TypeScript 5.5, Vite, Tailwind, Zustand, lightweight-charts
- Broker/runtime: Interactive Brokers / IB Gateway
- AI/autopilot: optimizer, decision ledger, replay, evaluation, rule lab

## Product Boundary

Today the active product is:

- canonical backend: `backend/`
- canonical dashboard: `dashboard/`
- current release class: development, simulation, and controlled paper testing

Today the product is not:

- an installable desktop app;
- a public website or hosted SaaS;
- approved for unattended live-money trading;
- split across the removed legacy `frontend/` or archived nested dashboard.

## Architecture

```text
trading/
|- backend/                 FastAPI backend, broker/runtime services, AI/autopilot, persistence
|- dashboard/               canonical React + TypeScript operator dashboard
|- ib_chart/                chart sidecar assets
|- sessions/                roadmap and review specs
|- docs/                    architecture, baseline, ADRs, runbooks (being filled in)
`- backend/tests/           backend regression and integration coverage
```

Main backend responsibilities today:

- broker and simulation runtime
- order execution and reconciliation
- rule engine and validation
- analytics and diagnostics APIs
- stock profile and research APIs
- alerts and notifications
- AI/autopilot decisioning
- decision ledger, replay, and evaluation plumbing

Main dashboard responsibilities today:

- dashboard, market, charts, screener, and backtest workflows
- rules, trade bot, analytics, and stock profile pages
- autopilot control plane
- alerts, settings, and simulation pages

See `docs/architecture.md` for the short system map and `docs/baseline.md` for the current validation baseline.

## Operating modes

The platform has multiple mode layers. They are related, but they are not the same thing.

### Broker environment

- `PAPER`: IBKR paper account / paper broker environment
- `LIVE`: IBKR live account / live broker environment

### Simulation mode

- `SIM_MODE` means the dashboard/backend are operating against simulation state instead of broker state for parts of the workflow.
- This is useful for UI/runtime rehearsal and flows that must not touch a broker account.

### Autopilot authority

- `OFF`: AI/autopilot can observe and score, but it does not take execution authority
- `PAPER`: autopilot can act in paper-mode paths
- `LIVE`: autopilot is allowed to participate in live decision paths

These layers must be interpreted together. A paper broker session does not automatically mean autopilot is active, and an active autopilot mode does not replace broker mode semantics.

## AI and autopilot

The AI/autopilot system is not just advisory anymore.

Important current architecture points:

- Stage 9 introduced the trade truth layer so realized trade outcomes come from canonical fields, not ad hoc metadata parsing.
- Stage 10 introduced decision ledger and evaluation plumbing so AI runs, items, replay, and scoring can be inspected and compared.
- The operator dashboard already contains autopilot controls, status, evaluation, and rule-lab surfaces.

## Main product surfaces

- `Dashboard`: summary, watchlists, and live quote overview
- `Trade Bot`: runtime operations and bot state
- `Market`: charting and symbol analysis
- `Charts`: dedicated chart workflows
- `Screener`: market scan and filter workflows
- `Backtest`: rule and strategy testing
- `Rules`: manual and AI-assisted rule management
- `Autopilot`: AI activity, rule lab, evaluation, interventions
- `Analytics`: portfolio KPIs, equity, exposure, risk, trade history, correlation
- `Stock Profile`: fundamentals, analyst, events, financials, and narrative
- `Alerts`: alert creation and alert history
- `Settings`: operator and runtime configuration

## Setup

### 1. Install backend dependencies

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Install dashboard dependencies

```bash
cd dashboard
npm ci
```

### 3. Configure environment

Create the backend `.env` from the example and set your IBKR / runtime values.

Typical paper defaults:

```env
IBKR_HOST=127.0.0.1
IBKR_PORT=4002
IBKR_CLIENT_ID=1
IS_PAPER=true
BOT_INTERVAL_SECONDS=60
```

### 4. Run backend

The backend is intentionally single-process while it owns IBKR, alert,
WebSocket, heartbeat, and AI loops in one runtime. Do not launch it with
multiple Uvicorn workers.

```bash
cd backend
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

### 5. Run dashboard

```bash
cd dashboard
npm run dev
```

## Quality gates

Backend:

```bash
cd backend
python -m pytest tests -v
```

Dashboard:

```bash
cd dashboard
npm run typecheck
npm run build
npx vitest run
```

## Repo boundary rules

- Build output like `dashboard/dist/` should not be treated as core source.
- Ad hoc backend helper scripts belong under `backend/scripts/`, not mixed into runtime modules.
- Operator-facing degraded state must be explicit. Fake numeric fallback in production UI is not acceptable.

## Safety notes

- Always test new runtime or rule behavior in paper/sim paths first.
- Do not treat green tests as proof that degraded data behavior is acceptable.
- Treat autopilot authority changes like release events, not casual toggles.

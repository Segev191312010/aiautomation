# Autotrader V1

Autotrader V1 is a clean rebuild of the trading system around typed strategy contracts, deterministic risk, explicit lifecycle states, and durable decision evidence.

## Current Status

- Available: strategy drafts, deterministic scanning, conservative backtests, shadow decisions, risk evaluation, lifecycle persistence, local auth, and the V1 status API.
- Disabled: automated PAPER order submission.
- Unsupported: LIVE has no V1 runtime type, route, transition, or configuration.
- There is no legacy bot, autopilot router, direct-AI trader, rule engine, screener UI, alert engine, or broker executor in the active application.

## Architecture

```text
backend/main.py                    FastAPI entry point
backend/trading/contracts.py       canonical domain models
backend/trading/authority.py       code-owned release authority
backend/trading/market/            scanner and data-feed port
backend/trading/risk/              deterministic risk engine
backend/trading/backtest/          conservative bracket simulator
backend/trading/storage/           V1 schema and repository
dashboard/src/                     minimal operator status shell
plans/AUTOTRADER_V1_SPEC.md        governing product contract
```

## Local Setup

```bash
python3.11 -m venv backend/.venv
backend/.venv/bin/pip install -r backend/requirements-dev.txt
cp backend/.env.example backend/.env

cd dashboard
npm ci
```

Run the API:

```bash
cd backend
.venv/bin/uvicorn main:app --reload
```

Run the dashboard:

```bash
cd dashboard
npm run dev
```

Or run the loopback-only Compose stack:

```bash
docker compose up --build
```

The dashboard is available at `http://127.0.0.1:3000`.

## Quality Gates

```bash
SKIP_DOCKER=1 scripts/run_quality_gates.sh
scripts/run_quality_gates.sh
```

The first command runs backend tests, frontend typecheck/build/tests, and skips image builds. The second also builds both service images.

## Data Safety

- Docker writes the V1 database to `/data/autotrader_v1.db`.
- Local runtime defaults to `backend/trading_bot.db` only for compatibility with the preserved local file; use `DB_PATH` for a new V1 database.
- `.env` and SQLite files are local artifacts and are never committed.
- `docs/paper_review_protocol.md` is preserved owner work from the retired system and is not a current V1 runbook.

## Next Work

The next product slice is a real market-data adapter and evidence-producing shadow scheduler. PAPER execution comes later through a separately reviewed broker adapter and release gate.

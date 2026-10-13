# Autotrader V1

Autotrader V1 is a clean rebuild of the trading system around typed strategy contracts, deterministic risk, explicit lifecycle states, and durable decision evidence.

## Current Status

- **Daily automation:** `autotraderctl run` supervises a trading day — the
  watchlist screener (07:55–16:00 ET, with market regime), the PAPER order
  bridge, strategies (MOMO_HOD_V2 sends paper orders; ORB_V1, GAP_GO_V1 and
  VWAP_PB_V1 are shadow-only), Telegram notifications, and the always-on
  Telegram assistant. A LaunchAgent can start it at login.
- **AI:** Claude reviews every deterministic setup (PROPOSE/ABSTAIN) and answers
  owner questions with read-only tools; it never creates orders.
- **Owner orders:** any order from the pinned Telegram chat via a strict
  deterministic parser and validator; sent by the single order sender.
- **Evidence:** every decision is recorded; `trading/research/` records real
  bars and replays strategies.
- **Unsupported:** LIVE money has no runtime type, route, or configuration.

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
- `docs/archive/` holds owner work from the retired system (including the old TradingView/Claude paper runbook); it is not a current V1 contract.

## Operations

```bash
cd backend
.venv/bin/python autotraderctl.py run --dry-run --date YYYY-MM-DD   # planned day
.venv/bin/python autotraderctl.py shadow agent install|enable|disable|status
.venv/bin/python autotraderctl.py notify test                        # Telegram check
.venv/bin/python autotraderctl.py paper stop                         # kill switch
```

Owner prerequisites: TWS or IB Gateway logged into the PAPER account on
127.0.0.1:7497 with auto-restart enabled, the Anthropic and Telegram keys in the
macOS Keychain, `~/.config/autotrader-v1/config.toml`, the Mac on AC power during
market hours, and no other IBKR session receiving market data.

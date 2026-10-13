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
- **AI day manager (shadow):** the `manager` child writes day plans (09:20,
  09:46, hourly 10:30–14:30 ET; at most $5 a day) that may only narrow: a
  focus inside the deterministic candidates, strategies off for the day, size
  steps down. Managed MOMO (`momo-ap`, AP_MOMO_HOD_V1, never sendable) follows
  them in shadow; without a valid plan it runs exactly as MOMO_HOD_V2.
  `autotraderctl manager on|off|status` and Telegram `/autopilot`, `/plan` are
  the owner's switch; real MOMO_HOD_V2 is unchanged either way.
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
(cd backend && .venv/bin/python autotraderctl.py db migrate)   # creates the database

cd dashboard
npm ci
```

No process creates or migrates the database by itself (see
[Schema migrations](#schema-migrations)).

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

Or run the loopback-only Compose stack (migrate its database first, and again
after an update that adds a migration):

```bash
docker compose run --rm backend python autotraderctl.py db migrate
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
- The local runtime uses `~/.local/share/autotrader-v1/autotrader.db` unless
  `DB_PATH` is set. That is the live database even from a git worktree: run
  tests and experiments with `DB_PATH` (and `HOME`) pointing at temporary files.
- `.env` and SQLite files are local artifacts and are never committed.
- `docs/archive/` holds owner work from the retired system (including the old TradingView/Claude paper runbook); it is not a current V1 contract.

## Operations

```bash
cd backend
.venv/bin/python autotraderctl.py run --dry-run --date YYYY-MM-DD   # planned day
.venv/bin/python autotraderctl.py shadow agent install|enable|disable|status
.venv/bin/python autotraderctl.py notify test                        # Telegram check
.venv/bin/python autotraderctl.py paper stop                         # kill switch
.venv/bin/python autotraderctl.py db status                          # schema, read-only
.venv/bin/python autotraderctl.py db migrate                         # after the close
```

Owner prerequisites: TWS or IB Gateway logged into the PAPER account on
127.0.0.1:7497 with auto-restart enabled, the Anthropic and Telegram keys in the
macOS Keychain, `~/.config/autotrader-v1/config.toml`, the Mac on AC power during
market hours, and no other IBKR session receiving market data.

### Schema migrations

No process migrates the database by itself. Every process that uses it (the
screener, the strategies, the recorder, the Telegram assistant, the API)
checks the migration ledger read-only at start. If a migration is pending, or
the ledger does not match the code, it logs one CRITICAL line with the reason
(`schema_migration_pending`, `schema_checksum_mismatch`,
`schema_newer_than_code`, `schema_history_gap`, `schema_unreadable`) and exits
75. The supervisor retries it with backoff (15 s doubling to 5 minutes), so it
starts by itself once the database is migrated.

The Telegram assistant is the exception for a pending migration: its tables
come from migrations up to 12, so it keeps serving (`/stop`, manual orders and
exits, questions) while only newer migrations are pending. It still refuses a
missing migration it needs and every other reason above.

The owner hears of it without any process migrating:

- the always-on Telegram assistant sends one alert per day as it starts (or
  refuses), so a supervisor restarted without the migration reaches the
  phone within minutes, at any hour;
- the notifier keeps running (it only reads tables older than any pending
  migration), sends one alert per session, and drops the children's repeated
  exit-75 restarts instead of one message per restart;
- `autotraderctl run --dry-run` prints `!!! DATABASE NOT READY` on its second line.

`autotraderctl paper stop` works on any existing database, migrated or not.

`autotraderctl db migrate` is the only way the schema changes:

1. it takes an exclusive lock beside the database;
2. it refuses a ledger that does not match the code (deploy the matching code
   or restore a backup) and does nothing when no migration is pending;
3. it refuses inside the supervised trading day (07:55–18:30 ET on trading
   days, half days included, from the supervisor's own plan) unless
   `--during-session-i-know` is given;
4. it writes a timestamped backup to `backups/` beside the database with
   SQLite's backup API, verifies it and prints its path; no backup, no migration;
5. it applies the pending migrations in one transaction and prints the
   version before and after.

Applied migrations are frozen (`tests/test_migration_freeze.py`): a schema
change is always a new migration, never an edit. So a migration holds only
what its phase uses: migration 13 adds `autotrader_manager_setups` (every
setup the rules form), and migration 14 the PAPER execution engine's ledger
(`autotrader_exec_*`, empty until the engine, reviewed and unwired on its own
branch, runs a supervised drill; `backend/trading/storage/execution_ledger_schema.py`).
Migration 16 holds phase 2's tables (`backend/trading/storage/manager_schema.py`):
plans, verdicts, managed decisions, the autopilot switch with its day latch,
and the spend ledger. Phase 1's tables (15: day universe and signal outcomes)
are registered when phase 1 lands. While 16 is pending, `autotraderctl manager
on|off|status`, `/autopilot` and `/plan` refuse with a reply naming
`autotraderctl db migrate`; nothing else changes. To undo a migration, stop the supervisor,
delete `autotrader.db-wal` and `autotrader.db-shm`, and copy the backup over
`autotrader.db`.

Deploy runbook (every phase):

1. Merge after 18:30 ET.
2. `autotraderctl db migrate` (note the backup path it prints; for phase 0 it
   is v12 to v13, `backups/autotrader.<UTC time>.v12-to-v13.db`, or v12 to v14
   when the execution-ledger migration has merged too).
3. Restart the supervisor (it keeps the old plan in memory).
4. `autotraderctl run --dry-run`: no `!!! DATABASE NOT READY` line.
5. Check the next morning's `CHILD_START` lines in `supervisor.log`.
6. Watch the first session.

Rollback restores the code and the database together. Older code refuses a
database migrated past it, so reverting only the code would take down
`autotraderctl paper stop`, Telegram `/stop`, manual orders, the screener and
every strategy (only the pinned bridge would keep running):

1. Stop the supervisor: `autotraderctl shadow agent disable`.
2. Delete `autotrader.db-wal` and `autotrader.db-shm`, then copy the backup
   that `db migrate` printed over `autotrader.db`.
3. Check out the commit that was running before the deploy.
4. The older code has no `db` command and its dry run prints no schema line,
   so check the restored database directly. `sqlite3 -readonly
   ~/.local/share/autotrader-v1/autotrader.db "select max(version) from
   autotrader_schema_migrations"` must print the version that older code
   expects (12 for the phase-0 deploy), and `autotraderctl paper status` must
   exit 0.
5. Start the supervisor again: `autotraderctl shadow agent enable`.

Rows written between the migration and the rollback are lost. If
`db migrate` itself fails, the database is untouched: just revert the merge.

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
screener, the strategies, the `manager` child, the recorder, the Telegram
assistant, the API) checks the migration ledger read-only at start. If a migration is pending, or
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
setup the rules form; applied in production since the phase-0 deploy), and
migration 14 the PAPER execution engine's ledger (`autotrader_exec_*`, empty
until the engine, reviewed and unwired on its own branch, runs a supervised
drill; `backend/trading/storage/execution_ledger_schema.py`). Migration 15
holds phase 1's tables (the day universe, the IBKR contract facts and the
signal outcomes) and migration 16 phase 2's
(`backend/trading/storage/manager_schema.py`): plans, verdicts, managed
decisions, the autopilot switch with its day latch, and the spend ledger.
Migrations 14 to 16 are applied in production since the autopilot deploy
after the 2026-10-13 close, so the owner's database is at 16 and every
migration the code knows is frozen; the backup that deploy printed
(`v13-to-v16`) is the restore target of a pre-session rollback.
While a migration a command needs is pending (16 for `autotraderctl manager
on|off|status`, `/autopilot` and `/plan`), that command refuses with a reply
naming `autotraderctl db migrate`; nothing else changes.

Deploy runbook (every phase; the v13 to v16 autopilot deploy ran it):

1. Merge after 18:30 ET.
2. Stop the supervisor FIRST: `autotraderctl shadow agent disable`. A
   supervisor left running keeps the old plan in memory, and every child it
   restarts before the migration refuses with exit 75 (the screener, the
   strategies, the `manager` child and the recorder alike).
3. `autotraderctl db status` (read-only: the pending migrations and whether
   `db migrate` is allowed now), then `autotraderctl db migrate` (note the
   backup path it prints, e.g. `backups/autotrader.<UTC time>.v13-to-v16.db`),
   then `autotraderctl db status` again: `state` current, no `pending` line.
4. `autotraderctl run --dry-run` while the supervisor is still stopped: no
   `!!! DATABASE NOT READY` line, the bridge line `reviewed`, no
   `WARNING: running outside the supervisor` (with the supervisor stopped,
   any such process is a stray: stop it before the next step).
5. Start the supervisor: `autotraderctl shadow agent enable`.
6. Check the next morning's `CHILD_START` lines in `supervisor.log`.
7. Watch the first session.

A mid-session deploy needs the owner's own two commands,
`autotraderctl db migrate --during-session-i-know` and
`autotraderctl shadow agent enable`; the agents' auto-mode classifier blocks
both, by design.

Checking a database's version directly (the older code has no `db` command,
and the dry run of a stopped system is the only other read): open it
immutable, never `-readonly`. Right after a migration the database is in WAL
mode with its `-wal`/`-shm` files beside it, and `sqlite3 -readonly` fails on
it with `unable to open database file (14)`; a plain read-only open also
creates `-wal`/`-shm` files beside the database. This form works and writes
nothing:

```bash
sqlite3 'file:/Users/<you>/.local/share/autotrader-v1/autotrader.db?immutable=1' \
  "select max(version) from autotrader_schema_migrations"
```

Rollback before any session ran on the new schema restores the code and the
database together. Older code refuses a database migrated past it, so
reverting only the code would take down `autotraderctl paper stop`, Telegram
`/stop`, manual orders, the screener and every strategy (only the pinned
bridge would keep running):

1. Stop the supervisor: `autotraderctl shadow agent disable`.
2. Delete `autotrader.db-wal` and `autotrader.db-shm`, then copy the backup
   that `db migrate` printed over `autotrader.db`.
3. Check out the commit that was running before the deploy.
4. Check the restored database with the `?immutable=1` query above: it must
   print the version the older code expects (13 when rolling the autopilot
   phases back to the phase-0 code; 12 only for the phase-0 deploy itself),
   and `autotraderctl paper status` must exit 0.
5. Start the supervisor again: `autotraderctl shadow agent enable`.

Rows written between the migration and the rollback are lost; that is
acceptable only while no session has run on the new schema. If `db migrate`
itself fails, the database is untouched: just revert the merge.

Rollback after a session ran on the new schema preserves the data. Never
copy a backup over a database a session has written to: it would discard
that session's decisions, manual orders, bridge heartbeats and the kill-switch
fence. Instead, with the supervisor stopped and the old code checked out,
undo the migration surgically, in one transaction, rehearsed on a copy first:
drop the tables the migration added (`backend/trading/storage/manager_schema.py`
lists them for 15 and 16, `execution_ledger_schema.py` for 14), then delete
its rows from `autotrader_schema_migrations`, then run the `?immutable=1`
query and `autotraderctl paper status` before starting the supervisor. Rows
the session wrote to the older tables stay.

# Deployment Guide — ULTRAPLAN v4

> **Stage 9A safety hold (2026-07-27):** Historical v4 instructions cannot
> authorize LIVE. The code-owned release fence rejects
> `AUTOPILOT_MODE=LIVE`; use this addendum only for isolated development/PAPER
> work while the Stage 9A risk register is open.

> v4 addendum to `docs/DEPLOYMENT.md`. This file covers ONLY what changed in
> ULTRAPLAN v4: the Python 3.11 pin, the new TradingView (`TV_*`) and Claude
> worker (`CLAUDE_*`) environment variables, DB backup/restore, and registering
> the TradingView webhook router. For base deployment (reverse proxy, SSL,
> systemd, scaling) see `docs/DEPLOYMENT.md`.

---

## 1. Python version — pinned to 3.11

The backend is pinned to **Python 3.11** end to end. Do NOT build on 3.12.

- `backend/.venv` is Python 3.11.15.
- `backend/Dockerfile` and the root `Dockerfile` runtime stage both use
  `python:3.11-slim`.
- CI (`.github/workflows/ci.yml`) runs on 3.11.

Create / recreate the venv:

```bash
cd backend
python3.11 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
```

Verify:

```bash
backend/.venv/bin/python --version   # → Python 3.11.x
```

---

## 2. New environment variables (v4)

All v4 keys live in `backend/.env.example`; copy it to `.env` and fill the
secrets. They are read by `backend/config.py` (`from config import cfg`).

### TradingView webhook ingest (`TV_*`)

| Variable | Default | Meaning |
|---|---|---|
| `TV_WEBHOOK_SECRET` | *(empty)* | Shared HMAC secret. The webhook verifies the signature with `compare_digest`. **Empty disables acceptance** — must be set for the TV path to work. |
| `TV_ALLOWED_IPS` | *(empty)* | Comma-separated **exact** TradingView egress IPs (no CIDR). |
| `TV_IP_STRICT` | `true` | When `true`, an empty `TV_ALLOWED_IPS` **rejects all** requests (fail-closed). |
| `TV_FRESHNESS_SECONDS` | `90` | Max age (seconds) of the alert's `timenow` timestamp before it is rejected as stale. |

### Claude review worker (`CLAUDE_*`) — opt-in, default OFF

| Variable | Default | Meaning |
|---|---|---|
| `CLAUDE_WORKER_ENABLED` | `false` | Master switch for the Claude review worker. Keep `false` until the soak is approved. |
| `CLAUDE_WORKER_POLL_SECONDS` | `5` | Poll interval for claiming `pending_review` candidates. |
| `CLAUDE_DAILY_COST_USD_CAP` | `20.0` | Daily spend cap (tracked through `ai_decision_ledger`/`ai_learning`). Worker stops claiming when exceeded. |
| `CLAUDE_WORKER_MODEL` | `claude-sonnet-4-20250514` | Model ID for the worker. |

The worker reuses the existing `ANTHROPIC_API_KEY` (already in `.env.example`);
no new API key var is introduced.

> Copy from the template, never hand-type:
> ```bash
> cp backend/.env.example .env   # then edit secrets
> ```

---

## 3. Database backup & restore

SQLite lives at `DB_PATH` (default `backend/trading_bot.db`; `/data/trading_bot.db`
inside the container). Two helper scripts handle safe backup/restore:

### Backup (`scripts/backup_db.sh`)

Atomic online `.backup` (no write lock on a live DB) + gzip. Prints the output path.

```bash
# default: backups/trading_bot.<UTC-timestamp>.db.gz from backend/trading_bot.db
scripts/backup_db.sh

# explicit DB + output
scripts/backup_db.sh backend/trading_bot.db backups/manual.db.gz
```

Take a snapshot **before every deploy, schema change, or mode flip.** It creates
the `backups/` directory if missing.

### Restore (`scripts/restore_db.sh`)

Verifies `PRAGMA integrity_check` on the decompressed copy **before** swapping it
in, and saves a `.pre-restore.<timestamp>` copy of the current DB. If integrity
fails the live DB is left untouched and the script exits non-zero.

```bash
scripts/restore_db.sh backups/trading_bot.20260529T120000Z.db.gz
scripts/restore_db.sh backups/snapshot.db.gz backend/trading_bot.db
```

Stop the backend before restoring so it reopens the swapped file.

---

## 4. Registering the TradingView webhook router

The webhook lives at `backend/routers/webhook_routes.py` and follows the existing
router pattern: routers are wired in `backend/routers/__init__.py` via
`register_routers(app)`, which `backend/main.py` calls
(`from routers import register_routers; register_routers(app)`).

The orchestrator adds the include line to `register_routers()` — it is **not**
edited in any individual lane. The expected addition is a new include alongside
the other `app.include_router(...)` calls, e.g.:

```python
from routers.webhook_routes import router as webhook_router
app.include_router(webhook_router)
```

After registration, the public webhook endpoint is `POST /api/webhook/tv`
(HMAC + IP allowlist + freshness, no bearer auth) and the authenticated read
endpoint is `GET /api/signals`. Confirm both appear in the OpenAPI schema:

```bash
curl -s http://localhost:8000/openapi.json | grep -o '/api/webhook/tv\|/api/signals'
```

---

## 5. Quality gates before deploy

Run the full gate runner; nothing deploys on a red gate:

```bash
scripts/run_quality_gates.sh            # pytest + dashboard typecheck/build/vitest + both docker builds
SKIP_DOCKER=1 scripts/run_quality_gates.sh   # skip docker builds (faster local pass)
```

---

## 6. Deploy-time safety notes (v4)

- **Keep one replica and `WORKERS=1`.** Both Dockerfiles and Compose now
  default to one worker. A SQLite rate cap is shared across local processes,
  but broker clients/background loops still require the same-host process lock
  and do not have cross-host lease/fencing. Set in `.env`: `WORKERS=1`.
- **TV/Claude path stays PAPER** (IBKR port 7497) for a 7-day soak regardless of
  the scanner-path mode. See `docs/LIVE_FLIP_RUNBOOK.md`.
- **Claude worker stays OFF** (`CLAUDE_WORKER_ENABLED=false`) until the soak is
  approved.
- `docs/LIVE_FLIP_RUNBOOK.md` is retained as blocked historical material. It
  must not be executed while the Stage 9A release fence is present.

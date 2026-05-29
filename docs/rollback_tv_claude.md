# Rollback — TradingView / Claude worker path

> Use this to safely disable the **TV/Claude path** (the Claude review worker
> that consumes `direct_candidates` rows from the webhook). This is independent
> of the scanner-path LIVE flip — see `docs/LIVE_FLIP_RUNBOOK.md` for that.
>
> The TV/Claude path runs on the **PAPER** broker (IBKR port 7497) during its
> 7-day soak, so this rollback does not affect any LIVE positions. It stops the
> worker, drains in-flight reviews to a terminal state, and snapshots the DB.

---

## When to use

- The Claude worker is misbehaving (loops, over-spend approaching
  `CLAUDE_DAILY_COST_USD_CAP`, bad decisions, errors).
- You want to pause the soak.
- Before any change to the worker / webhook code.

---

## Procedure

### Step 1 — Disable the worker

Set the master switch OFF in `.env`:

```bash
# .env
CLAUDE_WORKER_ENABLED=false
```

Restart so the lifespan does not start the worker task:

```bash
sudo systemctl restart trading-backend      # or
docker compose up -d --force-recreate backend
```

With `CLAUDE_WORKER_ENABLED=false` no new `pending_review` candidates are
claimed and no new orders are proposed on the TV path.

### Step 2 — Flush stranded `in_review` rows to `failed`

When the worker stops mid-claim, candidates can be left in the non-terminal
`in_review` state. Move them to the terminal `failed` state so the TTL/GC and UI
do not show them as live. `in_review -> failed` is a valid TV-state transition
(`backend/db/direct_candidates.py: mark_candidate_status`).

Inspect first:

```bash
sqlite3 backend/trading_bot.db \
  "SELECT id, symbol, status, queued_at FROM direct_candidates WHERE status='in_review';"
```

Flush them (run with the backend stopped, or right after the restart so the
worker can no longer touch them):

```bash
sqlite3 backend/trading_bot.db \
  "UPDATE direct_candidates SET status='failed' WHERE status='in_review';"
```

Verify none remain:

```bash
sqlite3 backend/trading_bot.db \
  "SELECT COUNT(*) FROM direct_candidates WHERE status='in_review';"   # → 0
```

> Note: `pending_review` rows (never claimed) are left as-is — they are
> TTL-expirable and harmless with the worker off. Only the claimed-but-stranded
> `in_review` rows need flushing.

### Step 3 — DB snapshot

Capture the post-rollback state and record the printed path:

```bash
scripts/backup_db.sh
# → backups/trading_bot.<UTC-timestamp>.db.gz   (record this path)
```

---

## Verification

- Worker is not running: no Claude-worker poll/claim log lines after restart.
- `SELECT COUNT(*) ... WHERE status='in_review'` returns `0`.
- The webhook `POST /api/webhook/tv` still accepts and queues
  `pending_review` rows (ingest is unaffected; only the consumer is off) — this
  is expected. To also stop ingest, the webhook can be left unregistered, but
  that is an orchestrator-level change, not part of this rollback.

---

## Re-enabling (after the fix)

1. Resolve the underlying issue.
2. Set `CLAUDE_WORKER_ENABLED=true` in `.env`.
3. Restart and confirm the worker resumes claiming `pending_review` rows.

The `failed` rows from Step 2 are terminal and will not be re-processed; the GC
reaps terminal rows older than 7 days.

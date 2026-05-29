# LIVE Flip Runbook — ULTRAPLAN v4 (scanner path only)

> Account: LIVE IBKR (~$5,600). This runbook flips ONLY the **scanner path** to
> LIVE (`AUTOPILOT_MODE=LIVE`, IBKR LIVE port 7496). The **TradingView / Claude
> path stays PAPER** (IBKR port 7497) for a 7-day soak regardless of this flip.
>
> This is a gated terminal step. If ANY precondition below is not green,
> **HOLD on PAPER and report** — do not flip. The rollback path
> (`AUTOPILOT_MODE=PAPER` + restart) must be ready before you start.

---

## Pre-flip preconditions (ALL must be green)

1. **Quality gates green.** Run the full gate runner and confirm it exits 0:
   ```bash
   scripts/run_quality_gates.sh
   ```
   (Backend pytest, dashboard typecheck/build/vitest, both docker builds.)

2. **`WORKERS=1`.** The cross-process rate cap (W5) is the prerequisite for
   `WORKERS>=2`. Until it lands, hold `WORKERS=1` in `.env` so the per-process
   `asyncio.Lock` is authoritative. Confirm:
   ```bash
   grep -E '^WORKERS=' .env || echo "WORKERS unset — set WORKERS=1"
   ```

3. **TV/Claude path is and stays PAPER.** Confirm the broker paper port and that
   the Claude worker is OFF:
   ```bash
   grep -E '^IBKR_PORT=' .env          # expect 7497 for the soak service
   grep -E '^CLAUDE_WORKER_ENABLED=' .env   # expect false
   ```
   The LIVE scanner authority comes from `AUTOPILOT_MODE`, not from this port.

4. **Rollback rehearsed.** You know the rollback is `AUTOPILOT_MODE=PAPER` +
   restart (step "Rollback" below) and can execute it in under a minute.

---

## Flip procedure

### Step 1 — DB snapshot

Take an atomic backup immediately before the flip and record the printed path:

```bash
scripts/backup_db.sh
# → backups/trading_bot.<UTC-timestamp>.db.gz   (record this path)
```

### Step 2 — Set LIVE mode

Set the scanner-path authority to LIVE in `.env`:

```bash
# .env
AUTOPILOT_MODE=LIVE
```

Because `AUTOPILOT_MODE=LIVE`, `config.py` defaults the live-broker safety envs
on; ensure `JWT_SECRET` is a real secret (not the `MUST-SET` default) or startup
will refuse LIVE. Keep `WORKERS=1`.

### Step 3 — Restart

Restart the backend so the new mode is read at boot.

```bash
# bare-metal / systemd
sudo systemctl restart trading-backend
# or docker compose
docker compose up -d --force-recreate backend
```

### Step 4 — Verify the lifespan log

On startup the lifespan re-runs the autopilot matrix validator and syncs the
mode from the DB. Confirm this EXACT log line appears (from `backend/main.py`):

```
Autopilot mode synced from DB: mode=LIVE shadow_mode=False
```

```bash
# systemd
journalctl -u trading-backend --since "2 min ago" | grep "Autopilot mode synced from DB"
# docker
docker compose logs --since 2m backend | grep "Autopilot mode synced from DB"
```

- If you instead see `mode=OFF` with a `SECURITY: DB requested autopilot mode=...
  but matrix check failed` line, the matrix validator rejected LIVE (e.g.
  default `JWT_SECRET`, wrong `IS_PAPER`/`SIM_MODE`). **HOLD, fix .env/DB,
  restart.** Do NOT proceed to the canary.
- If `shadow_mode=True` while `mode=LIVE`, do not proceed — that is an authority
  invariant violation; HOLD and report.

### Step 5 — 1-share BUY canary on LIVE port 7496

With the scanner path LIVE and pointed at the **LIVE IBKR port 7496**, place a
single **1-share BUY** of a liquid, low-priced symbol as a canary, through the
normal order path (so it passes `safety_kernel` + the rate cap). Watch:

- the order is accepted and fills at the LIVE account (not paper),
- it appears in positions / the order ledger,
- no safety-kernel reject, no orphan-pending warning,
- the LIVE account cash decreases by ~1 share + commission.

If the canary does not fill cleanly, or anything looks wrong, **roll back
immediately** (below).

### Step 6 — Watch

Monitor for a full trading cycle: order outcomes, safety-kernel decisions,
daily-loss / kill-switch behavior, and that the TV/Claude PAPER service is
unaffected. Keep the rollback ready throughout.

---

## Rollback (scanner path → PAPER)

Fast, single-step. Use it on any anomaly.

```bash
# .env
AUTOPILOT_MODE=PAPER
```

Then restart:

```bash
sudo systemctl restart trading-backend      # or
docker compose up -d --force-recreate backend
```

Confirm the lifespan log now shows:

```
Autopilot mode synced from DB: mode=PAPER shadow_mode=True
```

PAPER forces `shadow_mode=True`, removing LIVE trade authority. If the DB still
holds `LIVE`, also clear it via the autopilot admin path (or restore the
pre-flip snapshot with `scripts/restore_db.sh <snapshot>` after stopping the
backend) so a restart cannot re-enter LIVE.

---

## Reminders

- **TV/Claude path stays PAPER (port 7497) for a 7-day soak** — this flip does
  not touch it.
- **Hold `WORKERS=1`** until the cross-process rate cap (W5) lands.
- **`CLAUDE_WORKER_ENABLED=false`** stays until the soak is signed off; its
  rollback is `docs/rollback_tv_claude.md`.
- If any precondition fails at the gate, **hold PAPER and report.**

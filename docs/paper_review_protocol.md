# Paper Review Protocol — 7-Day TV/Claude PAPER Soak

> **Stage 9A scope note (2026-07-27):** This protocol may collect PAPER
> development evidence only. It cannot authorize a LIVE flip, and the
> application currently enforces that NO-GO with a runtime release fence.

Operator's **daily checklist** for the 7-day TradingView -> Claude **paper-trading**
soak. The goal of the soak is to prove the full inbound path —
`POST /api/webhook/tv` ingest, Claude review worker, and PAPER order proposal —
behaves correctly, safely, and economically before any future release review.

Throughout the soak the system is **PAPER-only**:
- `AUTOPILOT_MODE=PAPER` (the `trading_autopilot_mode` gauge should read `1`).
- `CLAUDE_WORKER_ENABLED=true`.
- The Claude worker proposes orders against the virtual SimEngine account, never
  the live broker (`claude_worker.py` module docstring).

> Source of truth for this protocol: `backend/routers/webhook_routes.py`,
> `backend/metrics.py`, `backend/routers/health_extended.py`,
> `backend/claude_worker.py`, plus `db/direct_candidates.py` (status machine)
> and `config.py` (caps/flags).

---

## 0. Reference — what the pieces do

### Candidate status machine (`db/direct_candidates.py`)
TradingView candidates flow through:

```
pending_review -> in_review -> applied | declined_by_ai | failed | expired
```

- `pending_review` — queued by the webhook, not yet claimed. **Non-terminal, TTL-expirable.**
- `in_review` — claimed by the Claude worker. **Non-terminal, TTL-expirable.**
- `applied` — Claude called `propose_order` (paper order proposed). **Terminal.**
- `declined_by_ai` — Claude explicitly declined (`decline`/`decline_order`/`decline_signal`). **Terminal.**
- `failed` — empty/no-tool result, API error, or timeout. **Terminal.**
- `expired` — TTL elapsed while still `pending_review`/`in_review` (purge sweep). **Terminal.**

Source rows are inserted with `source='tv_webhook'`, `status='pending_review'`,
`ttl_seconds=300` (`webhook_routes.py` `tv_webhook`).

### Webhook outcome counters (`metrics.py` `WEBHOOK_OUTCOMES`)
`accepted`, `ip_reject`, `secret_reject`, `freshness_reject`, `duplicate`.

### Claude call outcomes (`metrics.py`, `claude_calls_total{outcome}`)
Incremented by the worker; cost accrues to `claude_cost_usd_total` and the
`ai_decision_runs` ledger.

### Key config defaults (`config.py`)
- `CLAUDE_DAILY_COST_USD_CAP` = **20.0** USD (cap checked before each claim;
  `<= 0` disables the cap — `claude_worker._daily_cap_reached`).
- `CLAUDE_WORKER_MODEL` = `claude-sonnet-4-20250514`.
- `CLAUDE_WORKER_POLL_SECONDS` = 5.
- `TV_FRESHNESS_SECONDS` = 90 (alert `timenow` must be within this window).
- `TV_IP_STRICT` = true; `TV_ALLOWED_IPS` must list TradingView's egress IPs.
- `TV_WEBHOOK_SECRET` — must be set (empty fails closed: every alert -> 401).

### Endpoints used in this checklist
| Endpoint | Auth | Returns |
|---|---|---|
| `GET /api/signals?source=&status=&limit=&offset=` | bearer | queued candidates, newest first |
| `GET /metrics` | none; isolated profile only | Prometheus text exposition |
| `GET /api/health` | none | lightweight liveness |
| `GET /api/health/ibkr` | bearer | `{connected, last_heartbeat_age_s}` |
| `GET /api/health/database-integrity` | bearer | `{integrity, journal_mode}` |

Set these once per shell (replace host/token):

```bash
export BASE=https://YOUR-HOST
export TOK="Bearer YOUR_OPERATOR_TOKEN"
```

Metrics checks additionally require `METRICS_EXPOSURE_PROFILE=isolated` on a
monitoring-only listener. Do not enable the route on the public application
listener.

---

## 1. DAILY CHECKLIST (run once per day, same time each day)

Do every step. Record the numbers in a running log so day-over-day trends are
visible (the go/no-go decision in §2 depends on the 7-day trend, not a single day).

### 1.1 Signals received & terminal-status breakdown — `GET /api/signals`

```bash
# Everything from the TV path (newest first)
curl -s -H "Authorization: $TOK" "$BASE/api/signals?source=tv_webhook&limit=500" | jq '.signals | length'

# Per-status counts (the numbers you log every day)
for S in pending_review in_review applied declined_by_ai failed expired; do
  N=$(curl -s -H "Authorization: $TOK" "$BASE/api/signals?source=tv_webhook&status=$S&limit=500" | jq '.signals | length')
  printf "%-16s %s\n" "$S" "$N"
done
```

Record and check:
- **applied vs declined_by_ai** — the approve/decline split. A healthy reviewer
  declines *some* candidates. **All-approve (0 declines) or all-decline (0 approves)
  over a full day is a red flag** (§3): Claude is rubber-stamping or refusing
  everything, so the soak is not actually exercising judgment.
- **failed** — should be near zero. Each `failed` is an empty/no-tool result, an
  API error, or a 90s timeout (`process_candidate`). Investigate every one.
- **expired** — should be zero. An `expired` row means a candidate sat in
  `pending_review`/`in_review` past its 300s TTL — i.e. the worker is not draining
  the queue fast enough (or is disabled / cost-capped).
- **pending_review / in_review** — should both be ~0 at check time. A growing
  `pending_review` backlog means ingest is outrunning the worker. A row stuck in
  `in_review` for more than a couple minutes means a candidate is wedged (the
  worker is designed to never leave anything in `in_review` — investigate).

### 1.2 Webhook-outcome counters — `GET /metrics`

```bash
curl -s "$BASE/metrics" | grep -E '^trading_webhook_events_total'
```

Log each outcome (`accepted`, `ip_reject`, `secret_reject`, `freshness_reject`,
`duplicate`) and compute today's deltas vs yesterday:
- `accepted` delta should roughly equal new TV alerts you expect to have sent.
- `accepted` delta should match the count of new `direct_candidates` rows from §1.1.
- `duplicate` — non-zero is normal (TradingView retries non-2xx), but a *spike*
  suggests the webhook is returning errors and TV is hammering retries.
- `secret_reject` / `ip_reject` — should be **zero** in steady state. Non-zero =
  either a misconfigured alert (wrong secret), a rotated `TV_ALLOWED_IPS`, or an
  unauthorized sender probing the endpoint (§3).
- `freshness_reject` — non-zero means alerts arrive older than
  `TV_FRESHNESS_SECONDS` (90s). A few may indicate clock skew or TV delivery lag;
  a steady stream means real alerts are being dropped before they're ever queued.

### 1.3 Seconds-since-last-accepted-signal (staleness gauge) — `GET /metrics`

```bash
curl -s "$BASE/metrics" | grep -E '^trading_seconds_since_last_accepted_signal'
```

This is the time since the last **accepted** signal was processed. During market
hours when alerts are expected, a large and growing value means the inbound path
has silently stopped (TV not firing, webhook 4xx/5xx, or worker stalled).
Interpret against market hours — a high value overnight/weekends is expected.

### 1.4 Claude calls & cost vs daily cap — `GET /metrics`

```bash
curl -s "$BASE/metrics" | grep -E '^trading_claude_(calls_total|cost_usd_total)'
```

- **`trading_claude_cost_usd_total`** is cumulative since process start. To get
  *today's* spend, take the delta from yesterday's logged value (and reset your
  baseline on any process restart, since the counter resets).
- Compare today's spend against **`CLAUDE_DAILY_COST_USD_CAP` ($20.00 default)**.
  The worker stops claiming new candidates once today's ledger spend reaches the
  cap and logs `daily cost cap reached ... idling` (`_daily_cap_reached`).
- **If you hit the cap**, candidates queue up unreviewed and will `expire` at TTL.
  Hitting the cap is itself a signal: either alert volume is higher than planned
  or per-review token usage is too high. Note it; it factors into go/no-go (§2).
- Cross-check `trading_claude_calls_total{outcome=...}` deltas against the
  applied/declined/failed deltas from §1.1 — they should move together.

### 1.5 Failed / expired rows — explicit drill-down

Already counted in §1.1; now actually look at them:

```bash
curl -s -H "Authorization: $TOK" "$BASE/api/signals?source=tv_webhook&status=failed&limit=50"  | jq '.signals[] | {id, symbol, queued_at, payload}'
curl -s -H "Authorization: $TOK" "$BASE/api/signals?source=tv_webhook&status=expired&limit=50" | jq '.signals[] | {id, symbol, queued_at, payload}'
```

For each: note the symbol, `queued_at`, and correlate with backend logs
(`claude_worker:` lines — `API timeout`, `error reviewing candidate`,
`corrupt payload`). Any **repeating** failure mode is a red flag (§3).

### 1.6 IBKR health — `GET /api/health/ibkr`

```bash
curl -s -H "Authorization: $TOK" "$BASE/api/health/ibkr" | jq
```

- Expect `{"connected": true, "last_heartbeat_age_s": <positive, increasing>}`.
- `connected: false` during market hours is a red flag — even in PAPER, market
  data / sim fills depend on the IBKR session, and a LIVE flip requires a stable
  connection. Also check `trading_ibkr_disconnects_total` in `/metrics`; its
  delta over 24h should be **0** in a healthy soak.
- `last_heartbeat_age_s` is the continuous-uptime proxy. If it keeps **resetting
  to a small number** day over day, the socket is flapping (reconnecting) even
  though it reads "connected" at the instant you check.

### 1.7 DB integrity — `GET /api/health/database-integrity`

```bash
curl -s -H "Authorization: $TOK" "$BASE/api/health/database-integrity" | jq
```

- Expect `{"integrity": "ok", "journal_mode": "wal"}`.
- `integrity` anything other than `"ok"` is an **immediate hard stop** (§3) —
  the candidate ledger / decision ledger may be corrupt.
- `journal_mode` should be `wal` (write-ahead logging) for the concurrent
  read/write pattern; a change to `delete`/`truncate` is worth flagging.

### 1.8 Liveness sanity — `GET /api/health`

```bash
curl -s "$BASE/api/health" | jq
```

A trivial up-check; if it fails, nothing else in this list is meaningful — the
process is down. Confirm the Claude worker loop and webhook router came back up
after any restart.

### 1.9 Confirm mode is still PAPER

```bash
curl -s "$BASE/metrics" | grep -E '^trading_autopilot_mode'
```

Must read `1` (PAPER) every single day of the soak. A value of `2` (LIVE) before
the §2 decision means someone flipped LIVE early — **stop and investigate**.

---

## 2. PAPER EVIDENCE CRITERIA (evaluate after the full 7 days)

These criteria can qualify the PAPER evidence bundle only if every item holds
across the entire seven-day window. They do not authorize LIVE. If any single
criterion fails, extend or restart the soak; if all pass, submit the evidence
to the Stage 9A governance path while the LIVE fence remains in place.

**GO requires ALL of:**

1. **End-to-end path proven.** A meaningful number of real TV alerts were
   `accepted` (`trading_webhook_events_total{outcome="accepted"}`) and every one
   reached a *terminal* status (`applied` / `declined_by_ai` / `failed` /
   `expired`) — nothing left stuck in `pending_review`/`in_review`.
2. **Reviewer exercised real judgment.** Both `applied` **and** `declined_by_ai`
   are non-zero, with a plausible split (e.g. neither side is < ~5% of decided
   candidates). The worker is deciding, not rubber-stamping or blanket-refusing.
3. **Negligible failure rate.** `failed` candidates are ≤ ~2% of decided
   candidates over the 7 days, and there is **no recurring** failure signature
   (no repeated timeouts, API errors, or corrupt payloads).
4. **Zero unexplained expiries.** `expired` count is 0 (or every expiry has a
   known, resolved root cause such as a one-off worker restart).
5. **Cost within budget, with headroom.** Daily Claude spend stayed **under
   `CLAUDE_DAILY_COST_USD_CAP` ($20)** every day, ideally with comfortable
   headroom. The cap was **never** hit (no `daily cost cap reached` events that
   caused queue starvation).
6. **Security clean.** `secret_reject` and `ip_reject` were **0** in steady state
   for the full window (any non-zero was traced to a known config change, not an
   unknown sender).
7. **IBKR stable.** `trading_ibkr_disconnects_total` delta ≈ 0 over the window;
   `/api/health/ibkr` reported `connected: true` during all market hours with a
   monotonically increasing heartbeat age (no flapping).
8. **DB healthy throughout.** `/api/health/database-integrity` returned
   `integrity: "ok"` (and `journal_mode: "wal"`) on every daily check.
9. **Staleness sane.** `trading_seconds_since_last_accepted_signal` tracked
   market hours — it rose only when no alerts were expected and dropped promptly
   when alerts resumed (no silent multi-hour gaps during active sessions).
10. **Mode discipline.** `trading_autopilot_mode` read `1` (PAPER) for the whole
    soak — no premature LIVE flip.

If the PAPER evidence passes, archive it with the exact commit/configuration
identity and request review. Do not set `AUTOPILOT_MODE=LIVE`; the historical
deployment runbook is blocked and the remaining execution, protection,
reconciliation, risk, identity, and approval gates still apply.

---

## 3. RED FLAGS — PAUSE THE SOAK

Any of these means **pause / restart the soak clock** (do not flip LIVE). Several
warrant an immediate stop of the worker (`CLAUDE_WORKER_ENABLED=false`) until
diagnosed.

**Hard stop (set `CLAUDE_WORKER_ENABLED=false`, investigate before resuming):**
- `GET /api/health/database-integrity` `integrity != "ok"` — possible ledger
  corruption.
- `GET /api/health` failing / process down, or repeated unexplained restarts.
- `trading_autopilot_mode` reads `2` (LIVE) at any point during the PAPER soak.
- Any indication a **real/live** order was placed (the worker is PAPER-only; a
  live fill means a wiring bug — stop everything).

**Pause and diagnose (worker may keep running):**
- **Rising `failed` rate** or a **recurring** failure signature in logs
  (repeated `API timeout`, `error reviewing candidate`, `corrupt payload`).
- **Any `expired` rows** caused by the worker not draining (growing
  `pending_review` backlog, or `daily cost cap reached` starving the queue).
- **Cost-cap hit** — today's Claude spend reached `CLAUDE_DAILY_COST_USD_CAP`.
  Indicates volume or per-review tokens are higher than planned; the LIVE budget
  assumption is wrong until this is understood.
- **Approve/decline degenerate** — a full day of **all-approve (0 declines)** or
  **all-decline (0 approves)**. The reviewer is not exercising judgment.
- **Non-zero `secret_reject` / `ip_reject`** that isn't explained by a known
  config change — possible unauthorized sender, rotated TV IPs, or a misconfigured
  alert. (Empty `TV_WEBHOOK_SECRET` fails *every* alert closed -> 401.)
- **Sustained `freshness_reject`** during active sessions — real alerts being
  dropped before they queue (clock skew or TV delivery latency vs the 90s window).
- **`trading_seconds_since_last_accepted_signal` large and growing during market
  hours** — the inbound path has silently stopped (TV not firing, webhook
  returning 4xx/5xx, or the worker stalled).
- **IBKR disconnects** — `trading_ibkr_disconnects_total` climbing, or
  `/api/health/ibkr` `connected: false` / a heartbeat age that keeps resetting
  (socket flapping) during market hours.
- **`duplicate` spike** in `trading_webhook_events_total` — the webhook is likely
  erroring and TradingView is retry-storming.
- **Stuck `in_review`** — a candidate in `in_review` for more than a couple
  minutes (the worker is designed to always reach a terminal status; this means
  it wedged).

When a red flag pauses the soak: record the date, the root cause, and the fix,
then **restart the 7-day clock** once resolved — the soak only counts clean,
uninterrupted days.

---

## 4. Daily log template

Copy one block per day:

```
### SOAK DAY N — YYYY-MM-DD (check time HH:MM TZ)
signals (tv_webhook): pending_review=__ in_review=__ applied=__ declined_by_ai=__ failed=__ expired=__
webhook deltas: accepted=__ duplicate=__ secret_reject=__ ip_reject=__ freshness_reject=__
claude: calls(applied/declined/failed)=__/__/__  today_cost_usd=__ / cap=20.00
staleness: seconds_since_last_accepted_signal=__  (market open? Y/N)
ibkr: connected=__ heartbeat_age_s=__ disconnects_total_delta=__
db: integrity=__ journal_mode=__
autopilot_mode gauge: __ (must be 1=PAPER)
failed/expired drill-down notes: ____
red flags raised: none | ____
```

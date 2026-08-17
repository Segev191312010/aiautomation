# Operator API Reference — v4 Endpoints

Reference for the new endpoints added in v4. Covers method, auth, parameters,
and example request/response for each.

Source of truth:
- `backend/routers/webhook_routes.py` — `POST /api/webhook/tv`, `GET /api/signals`
- `backend/routers/health_extended.py` — `GET /api/health/ibkr`, `GET /api/health/database-integrity`
- `backend/routers/account_routes.py` — `GET /api/account/day-pnl`
- `backend/metrics.py` — `GET /metrics`

## Authentication models

Two distinct auth models are used across v4 endpoints.

### Bearer token (operator endpoints)

`GET /api/signals`, `GET /api/health/ibkr`, `GET /api/health/database-integrity`,
and `GET /api/account/day-pnl` all depend on `get_current_user` (see
`backend/auth.py`). Each request must carry an `Authorization: Bearer <token>`
header. The token is a JWT obtained via the `/api/auth/token` bootstrap flow.

Failure modes from `get_current_user`:
- Missing `Authorization` header -> `401 {"detail": "Missing bearer token"}`
- Non-bearer scheme or empty token -> `401 {"detail": "Invalid authorization header"}`
- Bad/expired token -> `401 {"detail": "Invalid or expired token"}`

All 401s include the `WWW-Authenticate: Bearer` response header.

### Public webhook and isolated metrics

`POST /api/webhook/tv` is a public route (no bearer dependency) because
TradingView cannot send an `Authorization` header. Authenticity is established
by three layered factors (see below). `/metrics` is absent by default. It is
mounted only when `METRICS_EXPOSURE_PROFILE=isolated`; that profile requires a
monitoring-only network/reverse-proxy boundary.

---

## POST /api/webhook/tv

Ingest a single TradingView alert. Accepted alerts are persisted to
`direct_candidates` with `status = "pending_review"` and `source = "tv_webhook"`
for downstream human/AI review. Redelivered webhooks are de-duplicated by a
stable `event_key`, so a retry never re-queues a second candidate.

- Method: `POST`
- Path: `/api/webhook/tv`
- Auth: PUBLIC (no bearer). Three layered factors, checked in this exact order:
  1. Source IP allowlist (`403` on failure)
  2. Shared secret (`401` on failure)
  3. Freshness window (`422` on failure)

  Then schema validation runs (`422` on failure).

### Auth factor details

| Factor | Config (`backend/config.py`) | Behavior |
| --- | --- | --- |
| IP allowlist | `TV_ALLOWED_IPS` (CSV, default `""`), `TV_IP_STRICT` (default `true`) | Exact match against the allowlist passes. When `TV_IP_STRICT` is `false`, loopback origins (`127.0.0.1`, `::1`, `localhost`) are always allowed (dev convenience). |
| Shared secret | `TV_WEBHOOK_SECRET` (default `""`) | Constant-time SHA-256 compare. Secret may arrive in the `X-TV-Secret` header **or** the JSON body `secret` field; the **header takes precedence**. An empty configured secret always fails closed (`401`). |
| Freshness | `TV_FRESHNESS_SECONDS` (default `90`) | `timenow` (the alert fire time, **not** `bar_time`) must be within +/- this many seconds of server time. `Z` suffixes are normalized to `+00:00`; naive timestamps are assumed UTC. Missing/unparseable `timenow` is stale. |

The secret is verified BEFORE schema parsing, so an invalid-but-secret-bearing
payload still authenticates first and the secret never leaks in a 422.

### Request body — `TVAlertPayload`

`Content-Type: application/json`. The body must be a JSON object.

| Field | Type | Required | Constraints / default |
| --- | --- | --- | --- |
| `symbol` | string | yes | pattern `^[A-Z0-9.\-]+$`, length 1–16 |
| `action` | string | yes | one of `"buy"`, `"sell"` (`"close"` is rejected at the schema level) |
| `price` | number \| null | no | default `null`; must be finite (NaN/inf rejected) |
| `timenow` | string \| null | no | default `null`; ISO timestamp; used for freshness |
| `bar_time` | string \| null | no | default `null`; stored, not used for freshness |
| `interval` | string | no | default `"1D"` |
| `strategy_order_id` | string \| null | no | default `null` |
| `secret` | string (`SecretStr`) \| null | no | default `null`, min length 8 |

Redaction behavior: `secret` is a Pydantic `SecretStr` and is always serialized
as `"[REDACTED]"` (or `null` when unset). It can never be echoed into logs or
error responses. The `422` validation handler additionally strips `input` and
`ctx` from each error entry so a rejected payload never reflects the submitted
secret back to the caller.

Dedupe `event_key` is the SHA-256 of
`symbol|action|interval|timenow|strategy_order_id`.

### Example request

```http
POST /api/webhook/tv HTTP/1.1
Host: trader.example.com
Content-Type: application/json
X-TV-Secret: my-super-secret-shared-key

{
  "symbol": "AAPL",
  "action": "buy",
  "price": 187.42,
  "timenow": "2026-05-29T14:31:05Z",
  "bar_time": "2026-05-29T14:30:00Z",
  "interval": "5",
  "strategy_order_id": "strat-123"
}
```

(The secret may instead be supplied in the body as `"secret": "..."`; if both
are present the `X-TV-Secret` header wins.)

### Responses

200 — queued (new alert accepted and persisted):

```json
{
  "status": "queued",
  "signal_id": "0f8c2c1e-7a4b-4d9a-9e2f-1c3b5a7d9e11"
}
```

200 — duplicate (redelivery matching an existing `event_key`; returns the
original signal id):

```json
{
  "status": "duplicate",
  "signal_id": "0f8c2c1e-7a4b-4d9a-9e2f-1c3b5a7d9e11"
}
```

401 — invalid/missing secret:

```json
{ "detail": "Invalid webhook secret" }
```

403 — source IP not allowed:

```json
{ "detail": "Source IP not allowed" }
```

422 — malformed body / schema validation / stale alert. Several shapes:

```json
{ "detail": "Invalid JSON body" }
```
```json
{ "detail": "Body must be a JSON object" }
```
```json
{ "detail": "Stale or missing timenow" }
```
```json
{
  "detail": [
    { "loc": ["action"], "msg": "Input should be 'buy' or 'sell'", "type": "literal_error" }
  ]
}
```

(Schema-error entries carry only `loc`, `msg`, `type` — `input` and `ctx` are
stripped.)

---

## GET /api/signals

List queued candidates (operator-facing), newest first.

- Method: `GET`
- Path: `/api/signals`
- Auth: Bearer token (router-level `Depends(get_current_user)`)

### Query parameters

| Param | Type | Default | Constraints |
| --- | --- | --- | --- |
| `source` | string | `null` (no filter) | exact match on `direct_candidates.source` (e.g. `tv_webhook`) |
| `status` | string | `null` (no filter) | exact match on `direct_candidates.status` (e.g. `pending_review`) |
| `limit` | integer | `100` | `1 <= limit <= 500` |
| `offset` | integer | `0` | `offset >= 0` |

Results are ordered by `queued_at DESC`.

### Example request

```http
GET /api/signals?source=tv_webhook&status=pending_review&limit=50&offset=0 HTTP/1.1
Host: trader.example.com
Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
```

### Response — 200

`payload` is the parsed JSON candidate blob (falls back to `{}` if unparseable).

```json
{
  "signals": [
    {
      "id": "0f8c2c1e-7a4b-4d9a-9e2f-1c3b5a7d9e11",
      "user_id": "demo",
      "symbol": "AAPL",
      "payload": {
        "action": "buy",
        "price": 187.42,
        "timenow": "2026-05-29T14:31:05Z",
        "bar_time": "2026-05-29T14:30:00Z",
        "interval": "5",
        "strategy_order_id": "strat-123",
        "received_ip": "52.89.214.238"
      },
      "queued_at": "2026-05-29T14:31:06.012345+00:00",
      "ttl_seconds": 300,
      "status": "pending_review",
      "source": "tv_webhook"
    }
  ],
  "limit": 50,
  "offset": 0
}
```

`401` on missing/invalid bearer token (see Authentication models).

---

## GET /api/health/ibkr

Read-only IBKR socket connectivity probe plus a heartbeat-age liveness proxy.
Never connects, disconnects, mutates the client, or performs network I/O.

- Method: `GET`
- Path: `/api/health/ibkr`
- Auth: Bearer token (`Depends(get_current_user)`)
- Params: none

### Example request

```http
GET /api/health/ibkr HTTP/1.1
Host: trader.example.com
Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
```

### Response — 200

`last_heartbeat_age_s` is the connection's elapsed up-duration in seconds
(rounded to 3 dp) used as a liveness proxy; it is `null` when not connected or
the stat is unavailable.

```json
{
  "connected": true,
  "last_heartbeat_age_s": 1543.219
}
```

Disconnected example:

```json
{
  "connected": false,
  "last_heartbeat_age_s": null
}
```

`401` on missing/invalid bearer token.

---

## GET /api/health/database-integrity

Runs SQLite `PRAGMA integrity_check` and reports the journal mode. Strictly
read-only, executed inside the shared `get_db()` context.

- Method: `GET`
- Path: `/api/health/database-integrity`
- Auth: Bearer token (`Depends(get_current_user)`)
- Params: none

### Example request

```http
GET /api/health/database-integrity HTTP/1.1
Host: trader.example.com
Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
```

### Response — 200

```json
{
  "integrity": "ok",
  "journal_mode": "wal"
}
```

`integrity` is the raw `PRAGMA integrity_check` result (`"ok"` when healthy,
otherwise the first reported problem string; `null` if no row). `journal_mode`
is the raw `PRAGMA journal_mode` value (e.g. `"wal"`, `"delete"`; `null` if no
row).

`401` on missing/invalid bearer token.

---

## GET /api/account/day-pnl

Realized + unrealized P&L for the current Eastern-Time (DST-aware) trading day.
Realized P&L sums fills on `trades` since the most recent ET midnight. Unrealized
is currently always `0.0` (no readily-readable live mark source) and the caveat
is surfaced in `note`.

- Method: `GET`
- Path: `/api/account/day-pnl`
- Auth: Bearer token (`Depends(get_current_user)`)
- Params: none

### Example request

```http
GET /api/account/day-pnl HTTP/1.1
Host: trader.example.com
Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
```

### Response — 200

`realized` and `total` are rounded to 2 dp. `count_trades_today` is the number
of `trades` rows since the ET day start.

```json
{
  "realized": 412.55,
  "unrealized": 0.0,
  "total": 412.55,
  "count_trades_today": 7,
  "note": "unrealized=0.0: no readily-readable current-price source; open_positions stores entry data only (no live mark)"
}
```

`401` on missing/invalid bearer token.

---

## GET /metrics

Prometheus text-exposition of all process metrics the process has touched.

- Method: `GET`
- Path: `/metrics`
- Availability: absent by default (`404`). Set
  `METRICS_EXPOSURE_PROFILE=isolated` only on an isolated monitoring listener.
- Auth: no application bearer token after that explicit mount; the network
  boundary is mandatory.
- Params: none
- Response content type: `text/plain; version=0.0.4; charset=utf-8`
  (`CONTENT_TYPE_LATEST`).

### Exposed series (`trading_*` prefix)

| Metric | Type | Labels | Meaning |
| --- | --- | --- | --- |
| `trading_orders_placed_total` | counter | `source`, `action` | Total orders placed |
| `trading_orders_filled_total` | counter | `source` | Total orders filled |
| `trading_ibkr_disconnects_total` | counter | — | IBKR disconnect events |
| `trading_bot_cycle_seconds` | histogram | — | Bot run-cycle duration (seconds) |
| `trading_rate_cap_hits_total` | counter | — | Aggregate rate-cap blocks; symbols are not exported |
| `trading_autopilot_mode` | gauge | — | Autopilot mode (0=OFF, 1=PAPER, 2=LIVE) |
| `trading_claude_calls_total` | counter | `outcome` | Claude worker LLM calls |
| `trading_claude_cost_usd_total` | counter | — | Cumulative Claude spend (USD) |
| `trading_webhook_events_total` | counter | `outcome` | Webhook events; `outcome` in `accepted`, `ip_reject`, `secret_reject`, `freshness_reject`, `duplicate` |
| `trading_seconds_since_last_accepted_signal` | gauge | — | Staleness of last accepted signal (seconds) |

### Example request

```http
GET /metrics HTTP/1.1
Host: trader.example.com
```

### Response — 200 (Prometheus text format, excerpt)

```text
# HELP trading_webhook_events_total Total webhook events.
# TYPE trading_webhook_events_total counter
trading_webhook_events_total{outcome="accepted"} 128.0
trading_webhook_events_total{outcome="duplicate"} 4.0
trading_webhook_events_total{outcome="ip_reject"} 2.0
# HELP trading_orders_placed_total Total orders placed.
# TYPE trading_orders_placed_total counter
trading_orders_placed_total{action="buy",source="tv_webhook"} 17.0
# HELP trading_autopilot_mode Current autopilot mode (0=OFF, 1=PAPER, 2=LIVE).
# TYPE trading_autopilot_mode gauge
trading_autopilot_mode 1.0
```

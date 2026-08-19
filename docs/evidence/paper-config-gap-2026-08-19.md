# PAPER configuration gap (2026-08-19)

This is a redacted inventory of the operator environment compared with
`scripts/validate_paper_readiness.py`. It records names and status only; no
secret or account value is included.

## Present in the operator environment

- `AUTOPILOT_MODE`
- `IS_PAPER`
- `SIM_MODE`
- `IBKR_HOST`
- `IBKR_PORT`
- `IBKR_CLIENT_ID`

## Missing required preflight settings

The following names are required for the real PAPER preflight and were not
present in the operator environment at audit time:

- `CLAUDE_WORKER_ENABLED` (must be `true` for the TV/Claude PAPER path)
- `TV_IP_STRICT` (must be `true`)
- `TV_ALLOWED_IPS` (must contain the approved TradingView egress IPs)
- `METRICS_EXPOSURE_PROFILE` (must be `isolated`)

`TV_WEBHOOK_SECRET` is also absent. It is intentionally not classified as a
non-secret gap here: the operator must provision it privately and must never
place its value in this document, chat, or source control.

## Not required by the preflight

`DB_PATH` is absent, so runtime resolution uses the backend canonical path
(`backend/trading_bot.db`) when started from this repository. Set it only when
an explicit absolute path is required. `IBKR_ACCOUNT_OWNER_USER_ID` is
conditional and is required only when private account streaming is enabled.

Until the missing settings and secret are provisioned, the preflight must
remain FAIL and `LIVE authorized: NO`.

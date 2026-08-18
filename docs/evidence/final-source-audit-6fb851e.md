# Final Source Audit — `6fb851e`

**Branch:** `integration/post-reconciliation`  
**Audited source SHA:** `6fb851ebe653f02f79f801d2a58cb675ea0839b4`  
**Remote parity:** confirmed against `origin/integration/post-reconciliation`  
**Working tree:** clean  
**LIVE:** `NO-GO`

## Verified

- Backend automated suite: `917 passed`, one existing dependency warning.
- Frontend typecheck and full suite: `437 passed`.
- Database policy: canonical `backend/trading_bot.db`; legacy root path absent.
- WebSocket isolation harness: private events scoped, unknown/missing-owner events dropped.
- Offline PAPER lifecycle simulator: normal, partial-fill, cancel/replace,
  disconnect/reconnect, restart/reconciliation, and mismatch scenarios pass.
- Offline screener benchmark: deterministic fixture, latency/concurrency and
  result-integrity checks pass without network access.

## Still required

- Operator-run IBKR PAPER market session with approved account/port fingerprint.
- Seven-day signal, cost, staleness, calibration, and abstention evidence.
- Real browser push enrollment and closed-tab delivery evidence.
- Real two-user browser WebSocket drill and signed artifact bundle.
- Broker reconciliation evidence after restart and lifecycle fault drills.

The example environment intentionally fails preflight until the operator supplies
the secret and approved TradingView egress IPs. No credentials are stored in the
repository. Passing the offline checks does not authorize PAPER or LIVE; any
unresolved discrepancy keeps the system disabled.

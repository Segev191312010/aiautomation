# Secure Consolidation — Governing Memo

**Status:** Secure Consolidation implemented on a dirty candidate; immutable
release SHA pending. LIVE trading remains **NO-GO**.

## Current position

- WebSocket private-event routing is server-scoped per authenticated user and
  reviewed against cross-user delivery.
- Browser notifications use durable per-user subscriptions, preference gates,
  endpoint ownership checks, SSRF/provider controls, fallback delivery, and
  generation-aware cleanup. The focused notification suite has 40 passing tests.
- Unified automated gates are green: backend **901 passed**, frontend **423
  passed**, TypeScript typecheck passed, and the production build passed.
- Remaining automated output is non-blocking: one FastAPI/httpx deprecation
  warning and existing React `act()` test warnings.

## Canonical database decision

`backend/trading_bot.db` is the canonical local operational path. The root
`trading_bot.db` is a divergent legacy cwd-relative artifact. It must not be
merged, selected by modification time, or used for migrations/ledger work.

CI and local checks run `scripts/check_db_path.sh`; deployment must pass the
explicit configured database/volume path. Any future migration requires a
hash-recorded backup, table-by-table comparison, new-target restore, integrity
and foreign-key checks, and a documented rollback archive.

## Immutable-SHA rule

The current integration branch is not a release candidate while its working
tree is dirty. The candidate sequence is:

1. Classify every modified/untracked path.
2. Commit only the reviewed source, tests, assets, and governance documents.
3. Push without force or squash and record the exact SHA.
4. Re-run CI and the manual browser/WebSocket/chart/screener checks.
5. Re-audit that immutable SHA only; any later source/config change invalidates
   the evidence.

## LIVE non-authorization

Passing these gates authorizes continued PAPER validation only. It does not
authorize real-money orders, `AUTOPILOT_MODE=LIVE`, or a live canary. LIVE stays
blocked until durable execution intent/outbox/fencing, broker-native protection,
authoritative reconciliation, durable account risk, restore replay fencing,
production identity/step-up controls, and signed IBKR PAPER evidence are all
implemented and independently approved.

## Product constraints

TradingView Pro is not assumed to transfer programmatic data entitlements or
embedding rights to this application. Chart latency claims require a verified
feed contract, exchange entitlements, licensing, and runtime evidence.

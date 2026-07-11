# Phase B B3 Contract Resolution

Date: 2026-07-11
Status: IMPLEMENTED — verification pending B4 gate

## Resolved contract gaps

- Added authenticated `GET /api/alerts/stats` with the `AlertStats` response shape.
- Routed position sizing, end-of-day position summary, and diagnostics refresh through the shared authenticated client.
- Removed the unsupported push-subscription call; browser notifications are explicitly local-only.
- Removed the unsupported industries API call and show an explicit unavailable state.
- Removed unsupported broker bracket fetch and order-edit calls. Position and P&L data remain visible, while bracket display/editing is labeled unavailable.

## Verification

- Dashboard typecheck: passed.
- Alert tests: 17 passed.
- Full dashboard Vitest and production build remain required before commit.
- Backend full suite, hygiene, and the regenerated contract matrix are required for B3/B4 closeout.

## Deliberate non-implementation

Broker bracket modification, web-push persistence, and O'Neil industry ranking were not invented in this phase. Their UI now communicates unavailable behavior instead of calling absent or unsafe contracts.

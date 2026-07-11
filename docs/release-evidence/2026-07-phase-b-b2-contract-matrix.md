# Phase B B2 Frontend/Backend Contract Matrix Evidence

Date: 2026-07-11

Status: **B2 PASS; B3 FIXES NOT STARTED**

## Inventory Scope

The matrix at
`docs/contract/2026-07-phase-b-frontend-backend-matrix.md` inventories runtime
TypeScript/TSX under `dashboard/src`. Test fixtures under `__tests__`,
`.test.`, and `.spec.` are excluded from the runtime matrix and remain covered
by the existing Vitest suite.

The inventory used the brief's searches:

```text
rg -n "/api/" dashboard/src
rg -n "fetch\(" dashboard/src
rg -n "services/api" dashboard/src
```

Each row records frontend file/line, function, method, normalized path, client
and authentication status, OpenAPI operation, and a backend decorator/source
hint where available. Template parameters are represented as `{param}`.

## Results

```text
runtime rows: 145
OK: 135
MISSING ROUTE: 3
MISSING ROUTE + AUTH BYPASS: 2
AUTH BYPASS: 2
RAW FETCH - REFACTOR: 1
RAW FETCH - PUBLIC: 1
BOOTSTRAP - B6/B7: 1
```

The five missing-route calls are:

- `GET /api/alerts/stats`;
- `POST /api/push/subscribe`;
- `GET /api/swing/industries`;
- `GET /api/positions/brackets`;
- `PUT /api/orders/{order_id}/modify`.

The two missing routes under raw unauthenticated fetches are the final two
positions-table calls. The additional auth-bypass rows are:

- `POST /api/risk/position-size` in `PositionSizer.tsx`;
- `GET /api/positions/summary` in `EODSummary.tsx`.

The diagnostics refresh call manually attaches a token but bypasses the shared
client and is marked `RAW FETCH - REFACTOR`. The Yahoo sparkline call is a
public-data raw fetch with an existing OpenAPI operation. The existing
`POST /api/auth/token` bootstrap flow is explicitly marked `BOOTSTRAP - B6/B7`
and remains temporary until the session boundary is implemented.

## B2 Decision

B2 passes as an inventory stage. No B3 implementation is hidden in this
checkpoint. B3 must implement each missing route or disable its UI explicitly,
move protected calls through the shared authenticated client, and update the
matrix to contain no `MISSING ROUTE`, `AUTH BYPASS`, or `RAW FETCH - REFACTOR`
rows.

# Phase B B1 OpenAPI Snapshot Evidence

Date: 2026-07-11

Status: **B1 PASS**

## Source and Artifact

```text
source commit: d67da67bd6a5d8d733f67e759bc8cb399a10cc49
snapshot: docs/openapi/2026-07-phase-b-openapi.json
title: Trading Dashboard
version: 2.0.0
paths: 176
operations: 123 GET, 54 POST, 5 PUT, 6 DELETE
```

The snapshot is referenced by `docs/API.md` as a dated verification schema.

## Safety-Conscious Export

The brief's live-server example was not used because entering FastAPI lifespan
would acquire the runtime lock and may initialize the database, IBKR connection,
alerts, heartbeat, notifications, reconciliation, and AI loops. Instead, B1
imported the canonical `main.app` and called `app.openapi()` without entering
lifespan. This produces the same static OpenAPI contract without runtime side
effects.

## Critical Route Families

The snapshot contains:

- `/api/positions/...` (2 paths);
- `/api/orders/...` (2 paths);
- `/api/alerts/...` (4 paths);
- `/api/swing/...` (8 paths);
- `/api/autopilot/...` (42 paths).

`POST /api/session/bootstrap` is absent by design at B1. It is a B7 deliverable,
not a B1 failure.

## B1 Decision

B1 passes. B2 must inventory every frontend `/api/` usage against this snapshot,
including method, authentication client, OpenAPI operation, backend route, and
status. Missing routes and auth bypasses become B3 work; they must not be hidden
by treating the snapshot as proof that the frontend is already correct.

# Phase B B4 Frontend/OpenAPI Contract CI Gate

Date: 2026-07-11

Status: **B4 PASS - local gate and negative-path tests pass; CI wiring present**

## Decision

The repository now has a deterministic merge gate that fails when a dashboard
runtime HTTP method/path is absent from the current FastAPI OpenAPI document.
The same command is wired into the backend CI job before the full pytest suite.

## Implementation

- Added `backend/scripts/check_contract_frontend_vs_openapi.py`.
- Added six focused tests in
  `backend/tests/test_contract_frontend_vs_openapi.py`.
- Added the `Frontend/OpenAPI contract` step to `.github/workflows/ci.yml`.
- Added the executable B0-B4 runbook in `docs/PHASE_B_VERIFICATION.md`.

The scanner inventories runtime TypeScript/TSX, excluding tests and the shared
transport implementation. It understands shared-client helper imports and raw
`fetch`, preserves HTTP methods, removes query strings, and normalizes template
segments against FastAPI parameter names. Missing routes and wrong methods are
reported with deterministic source file/line diagnostics.

Shared-client calls whose URL cannot be reduced from a string or template
literal fail closed. Direct external `fetch` calls outside `/api` are out of the
backend contract scope.

## Schema source decision

The default comparison calls `main.app.openapi()` without entering FastAPI
lifespan. This has no runtime-service startup side effects and reflects the
current code under review.

The signed B1 snapshot remains immutable historical evidence. It is not the B4
CI input because B3 legitimately added `GET /api/alerts/stats` after that
snapshot; using B1 as the merge schema would create a known false failure.
The checker accepts `--openapi` for fixtures or explicit historical replay.

## Verification results

Commands run from `backend/`:

```text
python -m pytest tests/test_contract_frontend_vs_openapi.py -q
......                                                                   [100%]
6 passed

python scripts/check_contract_frontend_vs_openapi.py
Contract check passed: 148 call sites / 146 unique frontend operations matched 190 OpenAPI operations.

python -m py_compile scripts/check_contract_frontend_vs_openapi.py
PASS (exit 0, no output)
```

The tests prove extraction of literal/template/query routes, helper aliases,
raw-fetch method handling, structural parameter matching, runtime-file
exclusions, unresolved dynamic-call failure, missing-route failure, and
method-drift failure. The CLI test supplies a synthetic schema, observes a
passing GET contract, replaces it with POST-only OpenAPI, and requires exit `1`
with the missing GET diagnostic.

B12 integration note (2026-07-12): the final renderer intentionally removed
its legacy `/api/auth/token` bootstrap call. The integrated count is therefore
147 call sites / 145 unique frontend operations against the same 190 OpenAPI
operations. The reduction is the reviewed B6 removal, not lost scanner coverage.

## CI enforcement

The backend job installs the repository requirements and then runs:

```text
python scripts/check_contract_frontend_vs_openapi.py
```

No continue-on-error or stale snapshot override is configured. A contract
failure therefore blocks the backend job and the pull request gate.

## Boundary

B4 verifies method/path presence only. Authentication, response-model
compatibility, order validation, WebSocket behavior, CSP, and session lifecycle
remain separate Phase B controls. No auth/session, order-validation, or
dashboard runtime file was modified by B4.

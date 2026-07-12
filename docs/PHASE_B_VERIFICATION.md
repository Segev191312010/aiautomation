# Phase B Verification Manual

Date: 2026-07-11

Phase: B - Contract, Auth, Trading Correctness, UI/Security

Current coverage: B0-B12.

Mission: from a clean checkout, re-prove the Phase B contract baseline and
prevent the dashboard from merging a runtime method/path call that FastAPI does
not expose.

Source artifacts:

- `docs/release-evidence/2026-07-phase-b-baseline.md`
- `docs/openapi/2026-07-phase-b-openapi.json`
- `docs/contract/2026-07-phase-b-frontend-backend-matrix.md`
- `docs/release-evidence/2026-07-phase-b-b1-openapi.md`
- `docs/release-evidence/2026-07-phase-b-b2-contract-matrix.md`
- `docs/release-evidence/2026-07-phase-b-b3-contract-resolution.md`
- `docs/release-evidence/2026-07-phase-b-b4-contract-ci-gate.md`

The B1 OpenAPI JSON and B2 matrix are dated evidence. They must remain stable
records of what was observed at those checkpoints. They are not the schema used
by the B4 merge gate after B3 changed the contract.

## 0. Run protocol

Run verification in a fresh clone without production credentials, broker
connectivity, or a production database. The contract export imports the FastAPI
application and calls `app.openapi()` without entering lifespan, so it does not
acquire the runtime lock or start IBKR, alerts, reconciliation, or AI loops.

Do not discard a dirty worktree to prepare a check. Preserve it and use a fresh
clone or clean worktree instead.

From the repository root:

```powershell
$dirty = @(git status --porcelain=v1 --untracked-files=all)
if ($LASTEXITCODE -ne 0) { throw "git status failed" }
if ($dirty.Count -ne 0) {
    $dirty
    throw "Phase B verification requires a clean checkout"
}

git status --short --branch
git show -s --format="%H %cI %s" HEAD
python --version
node --version
```

Record the full commit, timestamp, OS, Python version, and Node version in the
new dated verification evidence. A failure is a Phase B regression; do not
rewrite earlier evidence to make a later checkout appear green.

## 1. B0-B3 evidence checks

These checks establish provenance for B4. They do not replace the automated
contract gate.

### B0 - Baseline

Open `docs/release-evidence/2026-07-phase-b-baseline.md` and confirm it records a
clean commit plus the backend, typecheck, build, Vitest, and hygiene results.
Treat its test counts as historical comparison points, not permanent expected
counts.

### B1 - OpenAPI snapshot

Confirm that the dated snapshot is valid JSON and contains a `paths` object:

```powershell
python -c "import json, pathlib; p=pathlib.Path('docs/openapi/2026-07-phase-b-openapi.json'); d=json.loads(p.read_text(encoding='utf-8')); assert isinstance(d.get('paths'), dict) and d['paths']; print('B1 snapshot paths:', len(d['paths']))"
if ($LASTEXITCODE -ne 0) { throw "B1 snapshot validation failed" }
```

Expected for the signed B1 artifact: `B1 snapshot paths: 176`. A changed count
means the historical artifact changed and must be investigated, not silently
accepted.

### B2 - Frontend/backend matrix

Open `docs/contract/2026-07-phase-b-frontend-backend-matrix.md` and its B2
evidence. Confirm the matrix is explicitly dated and that its missing-route and
authentication-bypass findings agree with the B2 evidence. The matrix is an
inventory of the pre-B3 state; old `MISSING ROUTE` labels are not evidence of a
current regression by themselves.

### B3 - Contract resolution

Open the B3 evidence and confirm every B2 missing runtime route was either
implemented or its unsupported UI action was removed/labeled unavailable. The
B4 checker below is authoritative for the current method/path invariant. Auth
client use, capability labeling, and response-shape correctness remain separate
review and test concerns.

## 2. B4 - Frontend/OpenAPI CI gate

### What the gate checks

`backend/scripts/check_contract_frontend_vs_openapi.py` scans runtime `.ts` and
`.tsx` files under `dashboard/src`. It excludes test/spec files, `__tests__`
trees, and the shared transport implementation itself. It recognizes:

- imported shared-client calls: `get`, `post`, `postWithStatus`, `put`, `del`,
  including local import aliases and multiline generic calls;
- direct `fetch(...)` calls whose literal or template URL is under `/api`;
- static raw-fetch methods, with `GET` as the browser default;
- query strings and template path parameters, normalized structurally so a
  frontend `{param}` matches any FastAPI parameter name.

The default schema comes from the current `main.app.openapi()` result. This is
necessary because B3 legitimately added routes after the dated B1 snapshot.
`--openapi <file>` is available for deterministic fixtures and historical
replay.

The checker exits:

- `0` when every discovered runtime call has the same method and structural
  path in current OpenAPI;
- `1` for a missing operation or an unresolved shared-client API expression;
- `2` for configuration, import, or OpenAPI input errors.

### Run the gate

From the repository root:

```powershell
Push-Location backend
try {
    python scripts/check_contract_frontend_vs_openapi.py
    if ($LASTEXITCODE -ne 0) { throw "frontend/OpenAPI contract failed" }

    python -m pytest tests/test_contract_frontend_vs_openapi.py -q
    if ($LASTEXITCODE -ne 0) { throw "contract checker tests failed" }
} finally {
    Pop-Location
}
```

Expected output shape:

```text
Contract check passed: <nonzero> call sites / <nonzero> unique frontend operations matched <nonzero> OpenAPI operations.
......                                                                   [100%]
6 passed
```

The 2026-07-11 B4 shared-worktree run found 148 call sites, 146 unique frontend
operations, and 190 OpenAPI operations. Counts may increase as supported
workflows are added. The invariant is exit code `0`, at least one discovered
frontend call, and no missing or unresolved operation. A lower count requires
review because it can mean calls stopped being discovered.

The reviewed B12 integration count is 147 / 145 / 190: B6 removed the one
legacy renderer call to `/api/auth/token`. This specific decrease is expected.

### Prove the failure paths

The dedicated tests use synthetic frontend files and OpenAPI documents; they do
not mutate production source. They prove that a missing path, a method mismatch,
an unresolved dynamic shared-client URL, and a stale schema all return a failed
result. To run only those negative cases:

```powershell
Push-Location backend
try {
    python -m pytest tests/test_contract_frontend_vs_openapi.py -q -k "missing_route or dynamic_helper or drift"
    if ($LASTEXITCODE -ne 0) { throw "negative-path contract tests failed" }
} finally {
    Pop-Location
}
```

The pytest command itself must exit `0`: each test passes only after observing
the checker's expected non-zero contract result.

### Confirm CI wiring

`.github/workflows/ci.yml` runs the same checker in the backend job after Python
dependencies are installed and before the full pytest suite. Inspect it with:

```powershell
rg -n -A 2 -B 2 "Frontend/OpenAPI contract" .github/workflows/ci.yml
```

A pull request is not B4-verifiable if this step is skipped, marked
continue-on-error, pointed at the B1 snapshot, or run against a generated
dashboard fixture instead of `dashboard/src`.

## 3. Failure playbook

When the checker lists `Missing FastAPI operations`:

1. Open the reported frontend file and verify the intended HTTP method/path.
2. Inspect current FastAPI route decorators and `app.openapi()`; do not infer a
   route from a similarly named Python function.
3. Decide explicitly whether the product supports the action. Implement and
   test a safe authenticated backend route, correct the frontend contract, or
   remove/label the unsupported UI action.
4. Add a regression test for the affected feature and rerun the B4 gate plus
   both full backend/dashboard quality gates before recording new evidence.

When the checker lists `Unresolved frontend API calls`, keep the URL visible as
a literal/template at the shared-client call site or extend the checker with a
deterministic, tested syntax rule. Do not weaken the gate by silently ignoring
an expression it cannot prove.

When the checker exits `2`, fix the checkout/dependencies/schema input before
investigating product contract drift. Never point CI at a stale schema merely
to make the result green.

## 4. B5-B7 - Auth and session boundary

Verify backend bootstrap, expiry, loopback, capability, and rate-limit behavior:

```powershell
python -m pytest backend/tests/test_session_bootstrap.py backend/tests/test_rate_limit_session.py backend/tests/test_auth_gaps.py backend/tests/test_ws_auth.py -q
```

Verify the renderer session lifecycle:

```powershell
Push-Location dashboard
try {
    npx.cmd vitest run src/__tests__/App.session.test.tsx src/components/auth/__tests__/AuthGuard.test.tsx src/services/api/__tests__/client.spec.ts src/services/api/__tests__/session.spec.ts src/store/__tests__/sessionStore.test.ts
    if ($LASTEXITCODE -ne 0) { throw "session tests failed" }
} finally { Pop-Location }
```

The production renderer must have no legacy secret or persisted auth token:

```powershell
rg -n "VITE_JWT_BOOTSTRAP_SECRET|fetchAuthToken|setAuthToken|remember_me" dashboard/src
rg -n "VITE_JWT_BOOTSTRAP_SECRET|JWT_BOOTSTRAP_SECRET|auth_token|remember_me" dashboard/dist
```

Both searches must return no production matches. Non-secret preferences may
still use local storage. The ignored operator-owned `.env.local` is not release
source and must not contribute a value to built assets.

## 5. B8 - Manual-order boundary

```powershell
python -m pytest backend/tests/test_manual_order_validation.py backend/tests/test_orders.py backend/tests/test_integration_smoke.py -q
```

Confirm malformed symbols, non-integer or excessive quantities, invalid limit
prices, excessive notional, unavailable market quotes, unknown fields, and
non-stock asset types fail before any simulator or broker side effect.

## 6. B9-B11 - UI and security

```powershell
rg -n "window\.(confirm|prompt|alert)" dashboard/src
rg -n "s3\.tradingview\.com|127\.0\.0\.1:5001" dashboard/src
python -m pytest backend/tests/test_error_handling.py backend/tests/test_nginx_security.py backend/tests/test_swing_screeners.py -q
Push-Location dashboard
try {
    npx.cmd vitest run src/components/common/__tests__/ConfirmModal.test.tsx src/components/common/__tests__/ReasonModal.test.tsx
} finally { Pop-Location }
```

The source searches must be empty. Security-header tests must prove the CSP,
and the O'Neil endpoint/UI plus leading-industries UI must state that their
missing data contracts are unavailable.

## 7. B12 - Global completion gate

From a clean checkout, run:

```powershell
python -m pytest backend/tests -q
Push-Location dashboard
try {
    npm.cmd run typecheck
    npm.cmd run build
    npx.cmd vitest run
} finally { Pop-Location }
python scripts/check_workspace_hygiene.py
python backend/scripts/check_contract_frontend_vs_openapi.py
git diff --check
```

Record exact versions, commit identity, test counts, build module count, scans,
explicit Phase C-F deferrals, and owner approval in a dated B12 report. Code
completion may be recorded before owner sign-off, but Phase B is not signed
closed until the owner approves the B12 acceptance and remaining policy notes.

## 8. Scope boundary

B4 proves only runtime HTTP method/path existence. It does not prove response
schema compatibility, authorization policy, WebSocket contracts, business
validation, order safety, CSP, or desktop session lifecycle. Those invariants
remain owned by their Phase B checkpoints and their dedicated tests. B4 must be
rerun after any frontend API call or FastAPI route change.

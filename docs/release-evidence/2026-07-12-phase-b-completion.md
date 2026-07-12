# Phase B B12 Completion Evidence

Date: 2026-07-12

Status: TECHNICAL PASS - OWNER SIGN-OFF PENDING

Tested source commit:
`456330e95b6401bdb1ab2bf01824a91ade815816`

Implementation anchor commit:
`084b13943371caf3fe3f17013b7b9ffc13f36834`

Evidence commit: intentionally later than the tested source so this document
can name the immutable source and CI run that it describes.

Remote: public `Segev191312010/aiautomation`, branch `master`.

## Decision

Phase B implementation checkpoints B0-B11 are complete, and the B12 technical
gate passes locally and in same-source Ubuntu CI. Phase B remains
administratively open until the owner explicitly accepts the policy boundaries
in this report and authorizes recording B12 as PASS.

This is not approval for an installable desktop release, unattended operation,
or live-money authority. The desktop shell remains Phase D, and the packaged
simulation/paper soaks remain Phase F.

## Immutable Source Chain

| Commit | Purpose | Result |
|---|---|---|
| `084b13943371caf3fe3f17013b7b9ffc13f36834` | Integrated B4-B11 contract, session, order, UI, CSP, and honesty implementation | Local gates passed; CI found one version-sensitive test |
| `9c25e62a7a96c0dd25c119cb25799416e4a85407` | Made the route assertion tolerate FastAPI sentinel entries | Local gates passed; CI proved the route list is nested on its FastAPI version |
| `456330e95b6401bdb1ab2bf01824a91ade815816` | Asserted the published session route through OpenAPI instead of FastAPI internals | Local gates and CI passed |

The two corrective commits alter only
`backend/tests/test_session_bootstrap.py`. Product implementation is unchanged
from the anchor commit.

## Local Verification

Final local environment:

- Windows, Python 3.12.10;
- FastAPI 0.129.0, Pydantic 2.12.5, pytest 9.0.2;
- Node 24.13.1 and npm 11.8.0;
- React 18.3.1, TypeScript 5.9.3, Vite 5.4.21, Vitest 4.0.18.

Results:

| Gate | Result |
|---|---|
| Backend full pytest | PASS - 720 passed; final evidence-tree run completed in 45.36 seconds |
| Dashboard typecheck | PASS |
| Dashboard production build | PASS - 617 modules transformed |
| Dashboard Vitest | PASS - 31 files / 389 tests |
| Frontend/OpenAPI contract | PASS - 147 call sites / 145 unique frontend operations matched 190 OpenAPI operations |
| Contract checker tests | PASS - 6 tests |
| Workspace hygiene | PASS - no forbidden binary artifact |
| `git diff --check` | PASS - no whitespace error |

The contract command was run locally with a temporary process-only
`SIM_MODE=true` override because the ignored operator environment is currently
an intentionally rejected broker/default-secret combination. No ignored
configuration was changed or printed.

## Renderer Artifact and Source Scans

The final production build was scanned with these boundaries:

- project-specific bootstrap-secret identifiers and `remember_me` across every
  built file, including source maps;
- the generic `auth_token` identifier across executable production artifacts,
  excluding third-party source maps;
- native browser dialogs, retired TradingView embeds, the removed loopback
  iframe target, and Google Fonts across runtime source;
- retired remote targets across non-map production artifacts.

Results:

- zero project-specific bootstrap-secret artifact files;
- zero non-map persisted-auth-token artifact files;
- zero native-dialog or retired-remote runtime-source files;
- zero retired-remote non-map production files.

A generic `auth_token` string exists only in a third-party vendor source map.
It is not executable application code, a TradeBot storage key, or a secret. The
verification manual now separates that generic scan from the strong
project-specific identifier scan so the result is reproducible.

## Same-Source GitHub CI

Successful run:

- Run: https://github.com/Segev191312010/aiautomation/actions/runs/29207930965
- Source: `456330e95b6401bdb1ab2bf01824a91ade815816`
- Backend (Python 3.12 + pytest): PASS
- Frontend/OpenAPI contract step: PASS
- Dashboard typecheck/build/Vitest: PASS
- Overall conclusion: SUCCESS

Failed attempts are preserved rather than erased:

| Run | Source | Result | Disposition |
|---|---|---|---|
| `29207663590` | `084b139` | Backend 719 passed / 1 failed; dashboard passed | CI FastAPI exposed a non-route sentinel without `.path`; corrected in `9c25e62` |
| `29207832254` | `9c25e62` | Backend 719 passed / 1 failed; dashboard passed | CI FastAPI used a nested route representation; assertion moved to stable OpenAPI in `456330e` |
| `29207930965` | `456330e` | Backend and dashboard passed | Final technical evidence |

The green run still reports GitHub's Node 20 action-runtime deprecation warning
for `actions/checkout@v4`, `actions/setup-python@v5`, and
`actions/setup-node@v4`. It did not affect a gate. Updating action majors is a
separate CI-maintenance task and must not be confused with a Phase B product
failure.

## Phase B Acceptance Evidence

### Contract correctness

- The B1 OpenAPI snapshot remains immutable historical baseline evidence.
- Every supported renderer HTTP method/path is checked against current FastAPI
  OpenAPI in CI.
- Missing B2 actions were either implemented or removed and labeled
  unavailable; no silent fallback remains.
- O'Neil and leading-industries workflows state their missing data dependency
  explicitly instead of pretending to return complete results.

### Session and authentication

- `AuthGuard` gates the application workspace.
- The renderer obtains a short-lived session through
  `POST /api/session/bootstrap`, keeps it only in memory, and resets every
  domain store on expiry or authorization loss.
- Request-generation checks prevent an old session response from clearing or
  repopulating a newer session.
- Broker-backed operation requires a per-launch bootstrap capability and a
  strong JWT secret; the placeholder JWT is rejected even with Autopilot OFF.
- The legacy `/api/auth/token` endpoint is renderer-unused and simulation-only.
- Validation errors sanitize input so a submitted launch capability cannot be
  echoed into responses or logs.

### Manual-order correctness

- Symbols, integer quantities, quantity ceiling, notional ceiling, order type,
  positive finite limit price, unknown fields, and supported asset type are
  validated before simulator or broker side effects.
- Quote failure is fail-closed.
- Broker market buys become protective limit orders and are rechecked against
  the notional cap at the actual protective price.
- Manual sells are allowed only as verified long-stock exits and carry the
  correct safety-kernel exit context.
- Simulation does not falsely report a non-marketable limit order as filled.

### Renderer and local boundary

- Native browser confirm/prompt/alert calls were replaced with application
  modals, including keyboard focus containment for reason entry.
- FastAPI and Nginx serve a restrictive CSP.
- Compose dashboard exposure is loopback-only, and trusted proxy handling is
  explicit and rejects spoofed chains/public peers.
- Remote TradingView scripts, loopback iframe navigation, remote fonts, and
  data-supplied external navigation were removed from supported runtime paths.

## Owner Policy Boundaries Requiring Acceptance

1. Manual orders are stock-only. Options and futures remain unavailable until
   multiplier-aware quantity and notional validation exists.
2. Default manual-order ceilings are 10,000 shares and USD 100,000 absolute
   notional. These are safety policy defaults, not buying-power guarantees.
3. Broker market buys use a 0.5% protective limit offset. Simulation does not
   model resting non-marketable limit orders and returns an explicit conflict
   instead of inventing a fill.
4. Manual sells are verified long-position exits only; opening shorts through
   this endpoint is unsupported.
5. Broker-backed startup requires a strong JWT and per-launch capability. The
   tracked defaults are simulation plus loopback. Unsafe ignored local
   configuration fails closed rather than silently starting.
6. O'Neil, leading-industries, remote TradingView embeds, and remote diagnostic
   links are unavailable until a real local contract and reviewed data source
   exist.
7. The Phase B session boundary is desktop-compatible, but the Tauri shell,
   native IPC delivery, secure OS secret storage, and sidecar lifecycle remain
   Phase D work.

## Operator-Owned Configuration Follow-up

The ignored local backend environment currently selects broker-backed behavior
while retaining the placeholder JWT; startup rejects that combination by
design. Before the next backend launch, the operator must choose one:

- set `SIM_MODE=true` for simulation; or
- configure a strong unique `JWT_SECRET` and the per-launch capability for
  controlled broker-backed use.

The ignored `dashboard/.env.local` may still contain the retired
`VITE_JWT_BOOTSTRAP_SECRET` name. The renderer no longer reads it and the built
assets prove it is absent, but the operator should remove that obsolete local
entry. Neither ignored file was changed during Phase B.

## Explicit Deferrals

- Phase C: versioned migrations, app-data paths, backup/restore, crash recovery,
  redacted diagnostic bundles.
- Phase D: Tauri shell, PyInstaller sidecar, native IPC, secure storage, first
  run, and desktop lifecycle ownership.
- Phase E: installer, signing, updater, SBOM, release channels, and upgrade/
  uninstall drills.
- Phase F: browser/packaged E2E, dependency-release remediation, accessibility
  sign-off, packaged simulation soak, IBKR paper soak, restart/emergency drills,
  and independent security review.

Docker was not installed locally, so a live Compose/Nginx container smoke was
not performed. Static Compose/Nginx regression tests and Ubuntu CI passed.

## Owner Sign-off Required

Phase B must remain `TECHNICAL PASS - OWNER SIGN-OFF PENDING` until the owner
explicitly accepts the seven policy boundaries above and authorizes B12 as
PASS. After that approval, a later evidence-only commit may record Phase B as
closed. Phase C must not begin automatically; plan it with the owner first.

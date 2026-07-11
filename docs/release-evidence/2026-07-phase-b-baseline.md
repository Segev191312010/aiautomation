# Phase B Baseline and Scope Guard

Date: 2026-07-11

Phase: B - Contract, Auth, Trading Correctness, UI/Security

Status: **B0 PASS; IMPLEMENTATION NOT YET STARTED**

## Baseline Identity

The baseline was captured from a clean `master` checkout synchronized with
`origin/master`:

```text
branch: master
commit: d67da67bd6a5d8d733f67e759bc8cb399a10cc49
origin/master: d67da67bd6a5d8d733f67e759bc8cb399a10cc49
status --porcelain: empty
```

Environment:

```text
OS: Windows x64
Python: 3.12.10
Node: v24.13.1
npm: 11.8.0
```

The first PowerShell attempt to invoke `npm` resolved to the host's disabled
`npm.ps1` wrapper. The successful commands below use explicit `npm.cmd` and
`npx.cmd` executables; this is a shell invocation detail, not a test failure.

## Global Gate Results

Commands executed from the baseline checkout:

```text
cd backend; python -m pytest tests/ -q
640 passed in 46.04s

cd dashboard; npm.cmd run typecheck
PASS

cd dashboard; npm.cmd run build
PASS - Vite transformed 610 modules

cd dashboard; npx.cmd vitest run
27 files passed; 372 tests passed

python scripts/check_workspace_hygiene.py
Workspace hygiene OK: no forbidden binary artifacts found.
```

The Vitest run emitted only the eight expected WebSocket disconnect diagnostics
from lifecycle tests. No test failed and no unhandled exception or
`ResizeObserver` stack appeared.

## Phase B Scope

In scope for B0-B12:

- OpenAPI snapshot and frontend/backend endpoint matrix;
- resolution of missing routes and raw protected-call bypasses;
- contract validation in CI;
- auth baseline, session bootstrap, token lifecycle, and global store reset;
- backend manual-order symbol, quantity, notional, and limit-price validation;
- replacement of native prompts with accessible application modals;
- CSP and remote-navigation restrictions;
- honest O'Neil/leading-industries capability labeling.

The completion invariant is: every visible workflow has a real authenticated
backend contract, and malformed manual orders fail closed at the backend.

## Explicit Deferrals

The following remain outside Phase B and are not authorized by this baseline:

- Phase C: versioned migrations, data paths, backup/restore, crash recovery,
  retention, and diagnostic bundles;
- Phase D: Tauri shell, PyInstaller sidecar, first-run desktop setup, and native
  secret storage;
- Phase E: installer, signing, updater, rollback, SBOM, and release channels;
- Phase F: packaged E2E/smoke tests, accessibility review, dependency release
  gate, simulation/paper soaks, restart drills, and live-readiness sign-off.

No Phase B source files were changed while capturing this baseline. A failed
future B-stage check is a Phase B regression and requires dated evidence plus
affected/downstream reruns before completion can be claimed.

## B0 Decision

B0 passes. The next authorized checkpoint is B1: generate the backend OpenAPI
snapshot and reference it as a dated verification artifact. Do not implement
contract, auth, or UI changes until the B1/B2 inventory is reviewed.

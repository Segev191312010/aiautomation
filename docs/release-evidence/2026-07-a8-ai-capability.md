# Phase A8 AI Capability Evidence

Date: 2026-07-09
Phase: A - Truth, Safety, and Product Consolidation
Stage: A8 - AI capability validation and health-state semantics

## Goal

Make AI readiness explicit so the backend never reports AI as ready when the
provider key is missing, a configured model is unknown or retired, or the
runtime is degraded.

## Source Check

The model lifecycle registry in `backend/ai_capability.py` is based on the
Anthropic model overview and model deprecation pages checked during A7/A8.
Anthropic documents that retired model requests fail, that deprecated models
should be migrated before retirement, and that current Claude API model IDs
include the active Sonnet and Haiku defaults used by the backend.

Sources:

- `https://platform.claude.com/docs/en/about-claude/model-deprecations`
- `https://platform.claude.com/docs/en/about-claude/models/overview`

## Changes

- Added `backend/ai_capability.py` as a pure, testable AI capability model.
- Added explicit states: `disabled`, `unconfigured`, `invalid_model`, `ready`,
  and `degraded`.
- Added a static Anthropic lifecycle registry with active, deprecated, and
  retired model IDs plus replacement guidance.
- Startup validation now blocks PAPER/LIVE AI modes when Anthropic is
  unconfigured or a configured model is invalid.
- `/api/autopilot/status` now exposes AI capability fields for future desktop
  and dashboard health displays.
- Runtime mode changes to PAPER/LIVE now refuse unconfigured or invalid AI
  capability instead of silently arming Autopilot.
- Dashboard `AIStatus` types were extended to match the backend contract.

## Targeted Verification

Backend command:

```text
cd backend
python -m pytest tests/test_ai_capability.py tests/test_startup_config.py tests/test_api_contracts.py tests/test_autopilot_mode_semantics.py -q
```

Result:

```text
67 passed
```

Dashboard type contract command:

```text
cd dashboard
npm run typecheck
```

Result:

```text
passed
```

Full gate commands:

```text
cd backend
python -m pytest tests/ -q

cd dashboard
npm run typecheck
npm run build
npx vitest run

python scripts/check_workspace_hygiene.py
```

Result:

```text
backend: 617 passed
dashboard typecheck: passed
dashboard build: passed
dashboard vitest: 370 passed
workspace hygiene: passed
```

## State Coverage

- `disabled`: OFF mode does not require `ANTHROPIC_API_KEY`.
- `unconfigured`: PAPER/LIVE without `ANTHROPIC_API_KEY` fails startup and mode
  flips.
- `invalid_model`: unknown, retired, or within-block-window deprecated models
  fail when AI is enabled.
- `ready`: configured active models report ready.
- `degraded`: deprecated-but-not-blocked models, disabled fallback, and tripped
  circuit breaker state report degraded without being marked ready.

## Deferred

A9 remains open. It will record the canonical product/UI decision before
duplicate product cleanup begins.

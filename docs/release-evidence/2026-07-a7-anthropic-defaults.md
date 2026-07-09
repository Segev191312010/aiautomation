# Phase A7 Anthropic Default Evidence

Date: 2026-07-09
Phase: A - Truth, Safety, and Product Consolidation
Stage: A7 - Anthropic default cleanup

## Goal

Remove the retired Claude Sonnet 4 dated snapshot from backend defaults and
centralize current AI model defaults so future model retirements are one-change
updates.

## Source Check

Anthropic's model deprecation documentation lists the old dated Claude Sonnet 4
snapshot as retired on 2026-06-15 and lists `claude-sonnet-4-6` as the
recommended replacement. Anthropic's model overview lists `claude-sonnet-4-6`
and `claude-haiku-4-5-20251001` as active Claude API model IDs.

Sources:

- `https://platform.claude.com/docs/en/about-claude/model-deprecations`
- `https://platform.claude.com/docs/en/about-claude/models/overview`

## Changes

- Added `DEFAULT_AI_PRIMARY_MODEL = "claude-sonnet-4-6"`.
- Added `DEFAULT_AI_FALLBACK_MODEL = "claude-haiku-4-5-20251001"`.
- Updated optimizer, narrative, regime, and portfolio defaults to use the
  primary constant.
- Updated fallback and router last-resort model usage to use the fallback
  constant.
- Updated advisor narrative fallback usage to use the primary model constant.
- Updated model-aware learning cost tests and replay tests to avoid the retired
  dated Sonnet snapshot as fixture data.
- Updated backend architecture documentation sample config.
- Updated the readiness roadmap so AI-P0-01 records A7 as fixed while A8
  capability validation remains open.

## Verification

Targeted command:

```text
cd backend
python -m pytest tests/test_startup_config.py tests/test_ai_learning.py tests/test_ai_replay.py -q
```

Result:

```text
33 passed
```

Warning regression command:

```text
cd backend
python -m pytest 'tests/test_auth_gaps.py::test_route_works_with_token[GET-/api/advisor/report-advisor report (authed)]' -q
```

Result:

```text
1 passed
```

Search command:

```text
rg -n "claude-sonnet-4-20250514" backend docs README.md sessions --glob '!docs/release-evidence/2026-07-phase-a-baseline.md' --glob '!docs/release-evidence/2026-07-a7-anthropic-defaults.md'
```

Result:

```text
no matches
```

Centralization check:

```text
rg -n "claude-haiku-4-5-20251001|claude-sonnet-4-6" backend --glob '!backend/tests/**'
```

Result:

```text
backend/ARCHITECTURE.md: sample config
backend/config.py: DEFAULT_AI_PRIMARY_MODEL and DEFAULT_AI_FALLBACK_MODEL
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
backend: 598 passed
dashboard typecheck: passed
dashboard build: passed
dashboard vitest: 370 passed
workspace hygiene: passed
```

## Deferred

A8 remains open. It will add explicit AI capability states and startup
validation for disabled, unconfigured, invalid model, ready, and degraded modes.

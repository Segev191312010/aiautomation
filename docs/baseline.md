# Baseline Snapshot

Date: 2026-03-27
Stage: 0 - Baseline, Truth, and Repo Hygiene
Status: Recorded before further autonomy work

## Validation Commands

Backend:

```powershell
cd backend
python -m pytest tests/ -v
```

Frontend typecheck:

```powershell
cd dashboard
npm run typecheck
```

Frontend build:

```powershell
cd dashboard
npm run build
```

Frontend tests:

```powershell
cd dashboard
npx vitest run
```

## Baseline At Stage 0 Start

- Backend tests: 392/392 passing
- Frontend typecheck: passing
- Frontend build: passing
- Frontend vitest: 78/78 passing
- Autopilot: live and operator-visible

## Known Architectural Risks

### main.py monolith

`backend/main.py` is still carrying too many responsibilities at once: app bootstrap, websocket fanout, lifecycle, fallbacks, diagnostics, helpers, and route surfaces. That makes runtime changes harder to reason about and raises regression risk when operator APIs change.

### database.py monolith

`backend/database.py` still owns schema, migrations, repositories, and business helpers in one place. That keeps persistence changes high-risk because trade truth, rule versioning, and AI ledger updates all touch the same large file.

### api.ts monolith

`dashboard/src/services/api.ts` is the frontend's single large service surface. It centralizes every domain call, which makes typing, caching, error handling, and page-level ownership blur together.

### Replay and evaluator coupling

The Stage 10 AI path still has coupling between replay, evaluator, optimizer, and ledger seams. Some of that logic is already extracted, but replay semantics and shared helpers still need to be made explicit and independently testable.

### Silent degraded behavior in analytics

Before Stage 0, the analytics page silently rendered fake-looking runtime values when data fetches failed. Stage 0 replaces that with explicit degraded states so the operator can tell when data is unavailable.

## Fallback Policy Snapshot

Acceptable:
- Explicit degraded UI with an explanation
- Empty states that tell the operator what failed
- Logged warnings where the operator-visible surface remains truthful

Not acceptable:
- Fake numeric runtime values presented as real
- Silent substitution of mock analytics or portfolio metrics in production
- Broad exception swallowing in trading-critical paths without inventory or follow-up ownership



AI AUTOPILOT — STAGE 1 BACKEND COMPOSITION
==========================================
DATE: 2026-03-27
STATUS: READY AFTER STAGE 0 BASELINE LOCK
OWNER: BACKEND CORE TEAM
GOAL: Break up the backend monoliths into stable seams without breaking the green baseline.

PURPOSE
-------
Stage 1 exists to stop backend work from landing in a few giant files.

Current pain points:
- `backend/main.py` mixes bootstrap, runtime wiring, websocket behavior, route composition, and helpers.
- `backend/database.py` mixes schema, migrations, repositories, and business logic.
- `backend/models.py` and `backend/api_contracts.py` span too many domains.
- Cross-domain imports are too easy and too implicit.

STAGE 1 IS NOT:
---------------
- A behavior rewrite
- A DB technology migration
- A switch to a new framework

STAGE 1 IS:
-----------
- Composition cleanup
- Module boundary enforcement
- Dependency reduction
- Safer future refactors

GLOBAL EXIT GATE
----------------
Stage 1 is complete only when:
[ ] `main.py` is primarily wiring/composition
[ ] `database.py` is no longer a god-module
[ ] models and API contracts are domain-scoped
[ ] startup and router boundaries are explicit
[ ] imports are cleaner and less circular
[ ] the backend still boots and tests remain green

================================================================
PHASE 1.1 — MAP THE CURRENT BACKEND RESPONSIBILITIES
================================================================
SCOPE
- Inventory what the backend actually owns today before splitting it

FILES
- [ ] backend/main.py
- [ ] backend/database.py
- [ ] backend/models.py
- [ ] backend/api_contracts.py
- [ ] backend/startup.py

TASKS
1) main.py responsibility map
   [ ] Identify bootstrap responsibilities.
   [ ] Identify route-domain groupings.
   [ ] Identify websocket/runtime-specific helpers.
   [ ] Identify any business logic that should not live in the entrypoint.

2) database.py responsibility map
   [ ] Separate mentally into:
       - DDL / migrations
       - connection helpers
       - repositories / queries
       - business-side helpers
   [ ] Mark the functions that mutate multiple domains at once.

3) models/contracts inventory
   [ ] Group the Pydantic/data models by domain.
   [ ] Group API contracts by domain.
   [ ] Record where domain boundaries are currently blurred.

4) Produce target module map
   [ ] Write a short target module tree in this stage file or a scratch note before moving code.

DELIVERABLE
- [ ] A clear before/after backend composition map.

================================================================
PHASE 1.2 — SPLIT THE FASTAPI ENTRYPOINT
================================================================
SCOPE
- Turn `backend/main.py` into a composition module instead of a god-file

FILES
- [ ] backend/main.py
- [ ] backend/routers/auth.py
- [ ] backend/routers/status.py
- [ ] backend/routers/market.py
- [ ] backend/routers/bot.py
- [ ] backend/routers/rules.py
- [ ] backend/routers/alerts.py
- [ ] backend/routers/simulation.py
- [ ] backend/routers/diagnostics.py
- [ ] backend/routers/stock_profile.py
- [ ] backend/routers/autopilot.py
- [ ] backend/routers/__init__.py

TASKS
1) Define router domains
   [ ] Decide router ownership by domain.
   [ ] Keep high-cohesion endpoints together.
   [ ] Avoid one “misc” router.

2) Extract route definitions
   [ ] Move route handlers out of `main.py` into domain routers.
   [ ] Keep request/response shape unchanged unless a bug forces a fix.

3) Leave composition in main.py
   [ ] Keep app creation.
   [ ] Keep middleware registration.
   [ ] Keep router inclusion.
   [ ] Keep top-level lifespan hookup.
   [ ] Remove domain-specific helpers wherever possible.

4) Preserve tests and imports
   [ ] Update imports carefully.
   [ ] Avoid changing route paths or tags unless necessary.

DELIVERABLE
- [ ] `main.py` is mostly app assembly and includes routers instead of owning them.

================================================================
PHASE 1.3 — EXTRACT BOOTSTRAP AND LIFECYCLE WIRING
================================================================
SCOPE
- Isolate startup side effects and background task registration

FILES
- [ ] backend/main.py
- [ ] backend/startup.py
- [ ] backend/app_bootstrap.py (new, recommended)
- [ ] backend/app_lifecycle.py (new, recommended)
- [ ] backend/websocket_runtime.py (new, if needed)

TASKS
1) Startup validation
   [ ] Move environment/runtime validation into a dedicated bootstrap module.
   [ ] Keep failure mode explicit.

2) Background task registration
   [ ] Move scheduled/background loop registration into a lifecycle module.
   [ ] Make it clearer what starts on app boot.

3) Websocket/runtime wiring
   [ ] Move fanout/runtime-specific setup into a dedicated runtime module if it is currently mixed into main.py.
   [ ] Keep the websocket manager boundary explicit.

4) Lifespan clarity
   [ ] Make startup/shutdown order visible and testable.
   [ ] Ensure repeated startup/shutdown flows remain safe.

DELIVERABLE
- [ ] Backend startup behavior is centralized and easier to reason about.

================================================================
PHASE 1.4 — SPLIT PERSISTENCE BY DOMAIN
================================================================
SCOPE
- Break `database.py` into migrations plus domain repositories

FILES
- [ ] backend/database.py
- [ ] backend/db/migrations.py (new)
- [ ] backend/db/connection.py (new)
- [ ] backend/repositories/trades.py (new)
- [ ] backend/repositories/rules.py (new)
- [ ] backend/repositories/alerts.py (new)
- [ ] backend/repositories/autopilot.py (new)
- [ ] backend/repositories/diagnostics.py (new)
- [ ] backend/repositories/backtests.py (new, if needed)

TASKS
1) Separate schema/bootstrap from repositories
   [ ] Move DDL and migration helpers into `db/migrations.py`.
   [ ] Move connection helpers into `db/connection.py`.

2) Split domain repositories
   [ ] Move trade CRUD/query helpers into `repositories/trades.py`.
   [ ] Move rule/version/validation helpers into `repositories/rules.py`.
   [ ] Move AI/autopilot ledger and evaluation helpers into `repositories/autopilot.py`.
   [ ] Move alerts and diagnostics helpers into their own repositories.

3) Keep business helpers explicit
   [ ] If a helper is really business logic and not persistence, do not leave it in repositories by accident.
   [ ] Only repository-shaped functions belong in repository modules.

4) Minimize churn
   [ ] Preserve existing function signatures where practical.
   [ ] Add compatibility re-exports temporarily only if required.

DELIVERABLE
- [ ] Persistence responsibilities are split by concern, not all owned by `database.py`.

================================================================
PHASE 1.5 — SPLIT MODELS AND API CONTRACTS BY DOMAIN
================================================================
SCOPE
- Make models/contracts easier to navigate and safer to evolve

FILES
- [ ] backend/models.py
- [ ] backend/api_contracts.py
- [ ] backend/models/trading.py (new)
- [ ] backend/models/rules.py (new)
- [ ] backend/models/autopilot.py (new)
- [ ] backend/models/diagnostics.py (new)
- [ ] backend/models/settings.py (new)
- [ ] backend/contracts/trading.py (new)
- [ ] backend/contracts/rules.py (new)
- [ ] backend/contracts/autopilot.py (new)
- [ ] backend/contracts/diagnostics.py (new)
- [ ] backend/contracts/settings.py (new)

TASKS
1) Domain grouping
   [ ] Group trade/order/position models together.
   [ ] Group rule/rule-version/validation models together.
   [ ] Group AI/autopilot/evaluation contracts together.
   [ ] Group diagnostics/settings/auth contracts together.

2) Keep import ergonomics sane
   [ ] Provide stable import surfaces.
   [ ] Avoid forcing every caller to import from many deep files immediately.

3) Preserve validation behavior
   [ ] Ensure model defaults, literals, and backward compatibility stay intact.
   [ ] Do not break JSON round-trip behavior during the split.

DELIVERABLE
- [ ] Models and contracts are domain-scoped instead of one-file monoliths.

================================================================
PHASE 1.6 — ENFORCE DEPENDENCY BOUNDARIES
================================================================
SCOPE
- Reduce circular imports and private-helper coupling in backend composition

FILES
- [ ] backend/main.py
- [ ] backend/routers/*.py
- [ ] backend/repositories/*.py
- [ ] backend/services/*.py (new, if needed)
- [ ] backend/models/*
- [ ] backend/contracts/*

TASKS
1) Router boundary rule
   [ ] Routers may call services/repositories.
   [ ] Routers should not reach into unrelated router modules.

2) Repository boundary rule
   [ ] Repositories own storage access.
   [ ] Repositories should not become cross-domain business engines.

3) Service boundary rule
   [ ] Shared multi-domain business flows belong in services.
   [ ] Keep service naming explicit.

4) Import cleanup
   [ ] Remove obvious circular imports.
   [ ] Replace private cross-module helper use with explicit shared modules where needed.

DELIVERABLE
- [ ] Backend dependencies are easier to reason about and safer to change.

================================================================
PHASE 1.7 — VALIDATION AND MERGE GATE
================================================================
SCOPE
- Prove the backend composition refactor is safe

TASKS
1) Test gate
   [ ] Run backend tests after each extraction batch.
   [ ] Run full backend suite before merge.

2) Boot gate
   [ ] Confirm app boot still works.
   [ ] Confirm lifespan/startup tasks still register.

3) Import gate
   [ ] Confirm no broken import paths remain.
   [ ] Confirm route registration still matches the current API surface.

VALIDATION COMMANDS
- [ ] `cd backend && python -m pytest tests -v`

STAGE 1 FINAL CHECKLIST
-----------------------
[ ] Phase 1.1 complete
[ ] Phase 1.2 complete
[ ] Phase 1.3 complete
[ ] Phase 1.4 complete
[ ] Phase 1.5 complete
[ ] Phase 1.6 complete
[ ] Phase 1.7 complete

Once all boxes are checked, Stage 1 is DONE and later backend work stops piling into a handful of god-files.

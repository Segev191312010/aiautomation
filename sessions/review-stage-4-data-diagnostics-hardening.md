AI AUTOPILOT — STAGE 4 DATA & DIAGNOSTICS HARDENING
====================================================
DATE: 2026-03-27
STATUS: READY AFTER STAGE 0 TRUTH RESET
OWNER: DATA / DIAGNOSTICS TEAM
GOAL: Make degraded data behavior explicit, observable, and consistent across providers, services, APIs, and UI.

PURPOSE
-------
Stage 4 exists because silent data fallback is dangerous in a trading platform even when the app stays technically “up”.

Current pressure points:
- provider fallback behavior is not always explicit
- freshness semantics are inconsistent
- caches and retries can hide degraded source quality
- diagnostics and stock profile logic are large and likely mixing concerns

STAGE 4 IS NOT:
---------------
- A new market data provider rollout
- A new diagnostics dashboard from scratch
- Cosmetic UI work only

STAGE 4 IS:
-----------
- Data truthfulness
- Freshness discipline
- Better degraded-mode signaling
- Clearer provider boundaries

GLOBAL EXIT GATE
----------------
Stage 4 is complete only when:
[ ] provider fallback behavior is explicit in code and API responses
[ ] freshness semantics are shared across services
[ ] caches and retries do not mask degraded state
[ ] diagnostics/operator pages can show source and health clearly

================================================================
PHASE 4.1 — DATA ADAPTER BOUNDARIES
================================================================
SCOPE
- Separate provider adapters from orchestration logic

FILES
- [ ] backend/main.py
- [ ] backend/diagnostics_service.py
- [ ] backend/stock_profile_service.py
- [ ] backend/services/market_data/* (new, recommended)
- [ ] backend/services/provider_adapters/* (new, recommended)

TASKS
1) Provider inventory
   [ ] Identify where IBKR, Yahoo, Coinbase, and any other external provider logic currently lives.
   [ ] Record which modules combine orchestration and provider-specific code.

2) Adapter boundary
   [ ] Move provider-specific code into adapter modules.
   [ ] Keep orchestration modules provider-agnostic where possible.

3) Standard result shape
   [ ] Define a common result shape containing at least:
       - data payload
       - provider/source
       - freshness state
       - degraded/unavailable marker
       - optional reason

DELIVERABLE
- [ ] Data-provider behavior is structured through adapters instead of being mixed into orchestration code.

================================================================
PHASE 4.2 — FRESHNESS POLICY NORMALIZATION
================================================================
SCOPE
- Give the system one shared vocabulary for freshness and availability

FILES
- [ ] backend/api_contracts.py
- [ ] backend/diagnostics_service.py
- [ ] backend/stock_profile_service.py
- [ ] dashboard/src/types/*
- [ ] dashboard/src/pages/MarketPage.tsx
- [ ] dashboard/src/pages/StockProfilePage.tsx
- [ ] dashboard/src/pages/Dashboard.tsx

TASKS
1) Define shared freshness states
   [ ] fresh
   [ ] delayed
   [ ] stale
   [ ] unavailable

2) Use the same semantics everywhere
   [ ] quotes
   [ ] market bars
   [ ] diagnostics checks
   [ ] stock profile modules
   [ ] analytics/summary data where relevant

3) Expose source and freshness together
   [ ] Make sure source/freshness can be surfaced by API and UI.

DELIVERABLE
- [ ] “Fresh”, “stale”, and “unavailable” mean the same thing across the platform.

================================================================
PHASE 4.3 — CACHE AND RETRY POLICY CLEANUP
================================================================
SCOPE
- Stop cache/retry behavior from hiding degraded provider health

FILES
- [ ] backend/diagnostics_service.py
- [ ] backend/stock_profile_service.py
- [ ] backend/services/cache/* (new, if needed)
- [ ] backend/services/provider_adapters/*

TASKS
1) TTL inventory
   [ ] Record current TTLs and retry behavior.
   [ ] Identify places where stale cache can masquerade as live data.

2) Normalize policies
   [ ] Align retry rules with provider type and risk.
   [ ] Align cache expiry with freshness semantics.

3) Preserve degraded truth
   [ ] Cached fallback should still report freshness/degraded status accurately.

DELIVERABLE
- [ ] Cache and retry behavior is explicit and does not lie about source quality.

================================================================
PHASE 4.4 — API TRUTHFULNESS
================================================================
SCOPE
- Make APIs expose degraded-mode and source metadata instead of hiding it

FILES
- [ ] backend/api_contracts.py
- [ ] backend/main.py or extracted routers
- [ ] backend/diagnostics_service.py
- [ ] backend/stock_profile_service.py
- [ ] dashboard/src/services/api.ts or domain API clients

TASKS
1) Response metadata
   [ ] Add source/provider fields where operators need them.
   [ ] Add freshness state where relevant.
   [ ] Add degraded/unavailable reasons where relevant.

2) Remove silent semantic swaps
   [ ] Do not return silently different behavior with the same “healthy-looking” response shape.
   [ ] Make degraded mode first-class.

DELIVERABLE
- [ ] APIs tell the truth about where data came from and how trustworthy it is.

================================================================
PHASE 4.5 — DIAGNOSTICS AND UI SURFACING
================================================================
SCOPE
- Ensure operator-facing pages can render degraded state clearly

FILES
- [ ] dashboard/src/pages/AnalyticsPage.tsx
- [ ] dashboard/src/pages/MarketPage.tsx
- [ ] dashboard/src/pages/StockProfilePage.tsx
- [ ] dashboard/src/pages/Dashboard.tsx
- [ ] dashboard/src/components/common/*

TASKS
1) UI degraded-state patterns
   [ ] Reuse common components for degraded and unavailable state.
   [ ] Make source/freshness visible where operators need it.

2) Avoid hidden fallback numbers
   [ ] Confirm no operator page quietly swaps in fake or stale-looking-real values.

DELIVERABLE
- [ ] Degraded mode is visible in the UI instead of being hidden by “best effort” behavior.

================================================================
PHASE 4.6 — VALIDATION GATE
================================================================
SCOPE
- Lock the data truthfulness work with backend and frontend checks

TASKS
1) Backend tests
   [ ] Add tests for freshness and degraded metadata where relevant.

2) Frontend tests
   [ ] Add tests that degraded sections render truthful UI.

VALIDATION COMMANDS
- [ ] `cd backend && python -m pytest tests -v`
- [ ] `cd dashboard && npm run typecheck`
- [ ] `cd dashboard && npm run build`
- [ ] `cd dashboard && npx vitest run`

STAGE 4 FINAL CHECKLIST
-----------------------
[ ] Phase 4.1 complete
[ ] Phase 4.2 complete
[ ] Phase 4.3 complete
[ ] Phase 4.4 complete
[ ] Phase 4.5 complete
[ ] Phase 4.6 complete

Once all boxes are checked, Stage 4 is DONE and the platform stops hiding partial, stale, or fallback data behind healthy-looking surfaces.

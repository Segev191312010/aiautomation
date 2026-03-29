AI AUTOPILOT — STAGE 6 PAGE-BY-PAGE REBUILD PLAN
=================================================
DATE: 2026-03-27
STATUS: EXECUTE ONLY AFTER STAGE 0 TRUTH RESET IS MERGED
OWNER: PAGE OWNERS + FRONTEND CORE TEAM
GOAL: Rebuild the heaviest pages in waves so operator-facing UX becomes truthful, modular, and maintainable.

PURPOSE
-------
Stage 6 applies the architecture cleanup to the actual surfaces users work with every day.

This stage is ordered by:
- operator risk
- page size
- coupling to backend truth
- value of making the page easier to own

GLOBAL EXIT GATE
----------------
Stage 6 is complete only when:
[ ] the heaviest pages are materially thinner
[ ] operator pages tell the truth about backend/degraded state
[ ] large route files are broken into focused panels/hooks/helpers
[ ] page ownership is clearer and test coverage improves where behavior changed

================================================================
WAVE 1 — HIGHEST OPERATOR RISK
================================================================

----------------------------------------------------------------
PAGE 6.1 — ANALYTICS PAGE
----------------------------------------------------------------
FILES
- [ ] dashboard/src/pages/AnalyticsPage.tsx
- [ ] dashboard/src/components/analytics/*
- [ ] dashboard/src/hooks/useAnalytics*.ts
- [ ] dashboard/src/components/common/DegradedStateCard.tsx

TASKS
[ ] Remove any remaining silent mock/runtime deception.
[ ] Split KPI strip into its own panel.
[ ] Split equity/performance chart into its own panel.
[ ] Split exposure/risk blocks into their own panels.
[ ] Split trade history and correlation into their own panels.
[ ] Ensure range/freshness/degraded state propagates consistently.
[ ] Add tests for degraded sections and truthful missing-data handling.

DELIVERABLE
- [ ] Analytics page is truthful and panelized.

----------------------------------------------------------------
PAGE 6.2 — AUTOPILOT PAGE
----------------------------------------------------------------
FILES
- [ ] dashboard/src/pages/AutopilotPage.tsx
- [ ] dashboard/src/components/autopilot/*
- [ ] dashboard/src/hooks/useAutopilot*.ts

TASKS
[ ] Split feed/activity view into its own panel.
[ ] Split performance/learning view into its own panel.
[ ] Split rule lab / interventions into dedicated panels.
[ ] Split evaluation/replay UI into dedicated panels.
[ ] Move load and mutation orchestration into hooks.
[ ] Improve evidence surfacing for replay, promotion, and data quality.

DELIVERABLE
- [ ] Autopilot page becomes a composition shell, not a monolith.

----------------------------------------------------------------
PAGE 6.3 — TRADE BOT PAGE
----------------------------------------------------------------
FILES
- [ ] dashboard/src/pages/TradeBotPage.tsx
- [ ] dashboard/src/components/tradebot/*
- [ ] dashboard/src/hooks/useTradeBot*.ts

TASKS
[ ] Split tab content into focused modules.
[ ] Move position/order/activity orchestration into hooks or stores.
[ ] Standardize runtime labels: live, paper, sim, autopilot authority.
[ ] Reduce coupling between view tabs and backend polling logic.

DELIVERABLE
- [ ] TradeBot page is easier to reason about under live operations.

----------------------------------------------------------------
PAGE 6.4 — MARKET ROTATION PAGE
----------------------------------------------------------------
FILES
- [ ] dashboard/src/pages/MarketRotationPage.tsx
- [ ] dashboard/src/components/rotation/*
- [ ] dashboard/src/hooks/useMarketRotation*.ts

TASKS
[ ] Move sector/rotation transforms out of the route file.
[ ] Split tables and charts into dedicated panels.
[ ] Improve drill-down reuse and reduce prop sprawl.
[ ] Keep source/freshness state explicit where relevant.

DELIVERABLE
- [ ] Market Rotation page is smaller and less transform-heavy.

================================================================
WAVE 2 — CORE RESEARCH / BUILD TOOLS
================================================================

----------------------------------------------------------------
PAGE 6.5 — MARKET PAGE
----------------------------------------------------------------
FILES
- [ ] dashboard/src/pages/MarketPage.tsx
- [ ] dashboard/src/components/market/*
- [ ] dashboard/src/hooks/useMarketData*.ts

TASKS
[ ] Separate quote, history, indicator, and drawing concerns.
[ ] Make source and freshness labeling explicit.
[ ] Reduce chart/tool state coupling inside the route component.

DELIVERABLE
- [ ] Market page is modular and clearer about data quality.

----------------------------------------------------------------
PAGE 6.6 — SCREENER PAGE
----------------------------------------------------------------
FILES
- [ ] dashboard/src/pages/ScreenerPage.tsx
- [ ] dashboard/src/components/screener/*
- [ ] dashboard/src/hooks/useScreener*.ts

TASKS
[ ] Extract filter-builder state.
[ ] Extract scan execution/orchestration logic.
[ ] Clarify result persistence/session behavior.
[ ] Improve test coverage for filter and scan flows.

DELIVERABLE
- [ ] Screener page separates filter editing, execution, and results cleanly.

----------------------------------------------------------------
PAGE 6.7 — BACKTEST PAGE
----------------------------------------------------------------
FILES
- [ ] dashboard/src/pages/BacktestPage.tsx
- [ ] dashboard/src/components/backtest/*
- [ ] dashboard/src/hooks/useBacktest*.ts

TASKS
[ ] Split strategy builder, execution panel, and results viewer.
[ ] Clarify saved backtest vs ad hoc backtest behavior.
[ ] Align result viewer with replay/evaluation semantics where useful.

DELIVERABLE
- [ ] Backtest page becomes easier to extend and less state-heavy.

----------------------------------------------------------------
PAGE 6.8 — RULES PAGE / AUTOPILOT RULE LAB
----------------------------------------------------------------
FILES
- [ ] dashboard/src/pages/RulesPage.tsx
- [ ] dashboard/src/components/rules/AutopilotRuleLab.tsx
- [ ] dashboard/src/components/rules/*
- [ ] dashboard/src/hooks/useRules*.ts

TASKS
[ ] Align manual rule CRUD with AI rule lifecycle.
[ ] Surface replay metadata more clearly.
[ ] Surface validation evidence and promotion state more clearly.
[ ] Reduce dense logic inside the Rule Lab component.

DELIVERABLE
- [ ] Rule management becomes more understandable and less hidden behind dense UI logic.

================================================================
WAVE 3 — SECONDARY PAGES AND POLISH
================================================================

----------------------------------------------------------------
PAGE 6.9 — STOCK PROFILE PAGE
----------------------------------------------------------------
FILES
- [ ] dashboard/src/pages/StockProfilePage.tsx
- [ ] dashboard/src/components/stock-profile/*
- [ ] dashboard/src/hooks/useStockProfile*.ts

TASKS
[ ] Centralize module loading and freshness state.
[ ] Lazy-load heavier modules.
[ ] Standardize degraded markers across profile sections.

----------------------------------------------------------------
PAGE 6.10 — ALERTS PAGE
----------------------------------------------------------------
FILES
- [ ] dashboard/src/pages/AlertsPage.tsx
- [ ] dashboard/src/components/alerts/*
- [ ] dashboard/src/hooks/useAlerts*.ts

TASKS
[ ] Split form, history, stats, and notification permission sections.
[ ] Improve optimistic mutation and rollback behavior.

----------------------------------------------------------------
PAGE 6.11 — SETTINGS PAGE
----------------------------------------------------------------
FILES
- [ ] dashboard/src/pages/SettingsPage.tsx
- [ ] dashboard/src/components/settings/*
- [ ] dashboard/src/hooks/useSettings*.ts

TASKS
[ ] Organize settings by domain.
[ ] Improve dirty-state handling.
[ ] Surface runtime-impact warnings before save.

----------------------------------------------------------------
PAGE 6.12 — SIMULATION PAGE
----------------------------------------------------------------
FILES
- [ ] dashboard/src/pages/SimulationPage.tsx
- [ ] dashboard/src/components/simulation/*
- [ ] dashboard/src/hooks/useSimulation*.ts

TASKS
[ ] Separate playback controls from account state.
[ ] Align language and behavior with TradeBot and Backtest.

----------------------------------------------------------------
PAGE 6.13 — DASHBOARD HOME
----------------------------------------------------------------
FILES
- [ ] dashboard/src/pages/Dashboard.tsx
- [ ] dashboard/src/components/dashboard/*
- [ ] dashboard/src/hooks/useDashboard*.ts

TASKS
[ ] Simplify watchlist/summary composition.
[ ] Standardize quote freshness and source labeling.
[ ] Remove unnecessary orchestration from the page root.

================================================================
STAGE 6 EXECUTION RULES
================================================================
[ ] Do Wave 1 first.
[ ] Do not start a page rebuild until Stage 0 truth requirements for that page are satisfied.
[ ] Prefer panel extraction + hook extraction before visual redesign.
[ ] Keep file ownership per page explicit.
[ ] Run tests after each page migration batch.

VALIDATION COMMANDS
-------------------
[ ] `cd dashboard && npm run typecheck`
[ ] `cd dashboard && npm run build`
[ ] `cd dashboard && npx vitest run`

STAGE 6 FINAL CHECKLIST
-----------------------
[ ] Wave 1 complete
[ ] Wave 2 complete
[ ] Wave 3 complete
[ ] major page monoliths materially reduced
[ ] operator-facing pages no longer hide degraded state

Once all boxes are checked, Stage 6 is DONE and the dashboard stops depending on giant route files as its main unit of architecture.

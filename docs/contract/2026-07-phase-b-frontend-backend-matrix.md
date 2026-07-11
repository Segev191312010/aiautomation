# Phase B Frontend/Backend Contract Matrix

Date: 2026-07-11

Status: **B2 INVENTORY COMPLETE; B3 FIXES NOT STARTED**

Scope: runtime `dashboard/src` TypeScript/TSX files. Test fixtures under `__tests__`, `.test.`, and `.spec.` files are excluded from the runtime call matrix and remain covered by existing test suites.

Template parameters are normalized to `{param}` and compared by method/path against `docs/openapi/2026-07-phase-b-openapi.json`. Backend route hints come only from parsed FastAPI decorators and router prefixes. `RAW FETCH - REFACTOR` means a token is manually attached but the shared client is bypassed; `AUTH BYPASS` means no token path was found. `BOOTSTRAP - B6/B7` is the known temporary auth flow.

Summary: **145 runtime call rows**.

| Status | Rows |
| --- | ---: |
| AUTH BYPASS | 2 |
| BOOTSTRAP - B6/B7 | 1 |
| MISSING ROUTE | 3 |
| MISSING ROUTE + AUTH BYPASS | 2 |
| OK | 135 |
| RAW FETCH - PUBLIC | 1 |
| RAW FETCH - REFACTOR | 1 |

| Frontend file | Line | Function | Method | Path | Client/auth status | OpenAPI operation | Backend route hint | Status |
| --- | ---: | --- | --- | --- | --- | --- | --- | --- |
| dashboard/src/components/analytics/PositionSizer.tsx | 23 | PositionSizer | POST | /api/risk/position-size | raw fetch; no shared auth | POST /api/risk/position-size | backend/risk_api.py:73 | AUTH BYPASS |
| dashboard/src/components/tradebot/EODSummary.tsx | 27 | EODSummary | GET | /api/positions/summary | raw fetch; no shared auth | GET /api/positions/summary | backend/routers/positions.py:49 | AUTH BYPASS |
| dashboard/src/components/tradebot/PositionsTable.tsx | 27 | PositionSparkline | GET | /api/yahoo/{param}/bars | raw fetch; public data | GET /api/yahoo/{symbol}/bars | backend/routers/market_routes.py:116 | RAW FETCH - PUBLIC |
| dashboard/src/components/tradebot/PositionsTable.tsx | 201 | PositionsTable | PUT | /api/positions/brackets | raw fetch; no shared auth | none | not present | MISSING ROUTE + AUTH BYPASS |
| dashboard/src/components/tradebot/PositionsTable.tsx | 221 | PositionsTable | PUT | /api/orders/{param}/modify | raw fetch; no shared auth | none | not present | MISSING ROUTE + AUTH BYPASS |
| dashboard/src/services/api/alerts.ts | 11 | fetchAlerts | GET | /api/alerts | shared apiClient | GET /api/alerts | OpenAPI snapshot | OK |
| dashboard/src/services/api/alerts.ts | 12 | fetchAlert | GET | /api/alerts/{param} | shared apiClient | GET /api/alerts/{alert_id} | backend/routers/alerts_routes.py:98 | OK |
| dashboard/src/services/api/alerts.ts | 13 | createAlert | POST | /api/alerts | shared apiClient | POST /api/alerts | OpenAPI snapshot | OK |
| dashboard/src/services/api/alerts.ts | 14 | updateAlert | PUT | /api/alerts/{param} | shared apiClient | PUT /api/alerts/{alert_id} | backend/routers/alerts_routes.py:106 | OK |
| dashboard/src/services/api/alerts.ts | 15 | deleteAlert | DELETE | /api/alerts/{param} | shared apiClient | DELETE /api/alerts/{alert_id} | backend/routers/alerts_routes.py:119 | OK |
| dashboard/src/services/api/alerts.ts | 16 | toggleAlert | POST | /api/alerts/{param}/toggle | shared apiClient | POST /api/alerts/{alert_id}/toggle | backend/routers/alerts_routes.py:127 | OK |
| dashboard/src/services/api/alerts.ts | 17 | fetchAlertHistory | GET | /api/alerts/history | shared apiClient | GET /api/alerts/history | backend/routers/alerts_routes.py:55 | OK |
| dashboard/src/services/api/alerts.ts | 18 | testAlertNotification | POST | /api/alerts/test | shared apiClient | POST /api/alerts/test | backend/routers/alerts_routes.py:61 | OK |
| dashboard/src/services/api/alerts.ts | 19 | fetchAlertStats | GET | /api/alerts/stats | shared apiClient | none | not present | MISSING ROUTE |
| dashboard/src/services/api/alerts.ts | 23 | subscribePush | POST | /api/push/subscribe | shared apiClient | none | not present | MISSING ROUTE |
| dashboard/src/services/api/analytics.ts | 149 | fetchPortfolioAnalytics | GET | /api/risk/portfolio | shared apiClient | GET /api/risk/portfolio | backend/risk_api.py:44 | OK |
| dashboard/src/services/api/analytics.ts | 175 | fetchRiskLimits | GET | /api/risk/settings | shared apiClient | GET /api/risk/settings | backend/risk_api.py:159 | OK |
| dashboard/src/services/api/analytics.ts | 179 | fetchRiskLimits | GET | /api/risk/drawdown | shared apiClient | GET /api/risk/drawdown | backend/risk_api.py:80 | OK |
| dashboard/src/services/api/auth.ts | 5 | fetchAuthToken | POST | /api/auth/token | raw fetch; bootstrap secret | POST /api/auth/token | backend/routers/auth.py:33 | BOOTSTRAP - B6/B7 |
| dashboard/src/services/api/auth.ts | 17 | fetchAuthMe | GET | /api/auth/me | shared apiClient | GET /api/auth/me | backend/routers/auth.py:28 | OK |
| dashboard/src/services/api/autopilot.ts | 29 | fetchGuardrails | GET | /api/autopilot/config | shared apiClient | GET /api/autopilot/config | OpenAPI snapshot | OK |
| dashboard/src/services/api/autopilot.ts | 32 | updateGuardrails | PUT | /api/autopilot/config | shared apiClient | PUT /api/autopilot/config | OpenAPI snapshot | OK |
| dashboard/src/services/api/autopilot.ts | 35 | postEmergencyStop | POST | /api/autopilot/kill | shared apiClient | POST /api/autopilot/kill | OpenAPI snapshot | OK |
| dashboard/src/services/api/autopilot.ts | 38 | resetEmergencyStop | POST | /api/autopilot/kill/reset | shared apiClient | POST /api/autopilot/kill/reset | OpenAPI snapshot | OK |
| dashboard/src/services/api/autopilot.ts | 41 | setAutopilotMode | POST | /api/autopilot/mode | shared apiClient | POST /api/autopilot/mode | OpenAPI snapshot | OK |
| dashboard/src/services/api/autopilot.ts | 44 | resetDailyLossLock | POST | /api/autopilot/daily-loss/reset | shared apiClient | POST /api/autopilot/daily-loss/reset | OpenAPI snapshot | OK |
| dashboard/src/services/api/autopilot.ts | 48 | fetchAuditLog | GET | /api/autopilot/feed | shared apiClient | GET /api/autopilot/feed | OpenAPI snapshot | OK |
| dashboard/src/services/api/autopilot.ts | 51 | revertAIAction | POST | /api/autopilot/feed/{param}/revert | shared apiClient | POST /api/autopilot/feed/{entry_id}/revert | OpenAPI snapshot | OK |
| dashboard/src/services/api/autopilot.ts | 55 | fetchAIStatus | GET | /api/autopilot/status | shared apiClient | GET /api/autopilot/status | OpenAPI snapshot | OK |
| dashboard/src/services/api/autopilot.ts | 58 | fetchAICosts | GET | /api/autopilot/costs | shared apiClient | GET /api/autopilot/costs | OpenAPI snapshot | OK |
| dashboard/src/services/api/autopilot.ts | 61 | fetchLearningMetrics | GET | /api/autopilot/learning-metrics | shared apiClient | GET /api/autopilot/learning-metrics | OpenAPI snapshot | OK |
| dashboard/src/services/api/autopilot.ts | 64 | fetchEconomicReport | GET | /api/autopilot/economic-report | shared apiClient | GET /api/autopilot/economic-report | OpenAPI snapshot | OK |
| dashboard/src/services/api/autopilot.ts | 68 | fetchAutopilotRules | GET | /api/autopilot/rules | shared apiClient | GET /api/autopilot/rules | OpenAPI snapshot | OK |
| dashboard/src/services/api/autopilot.ts | 71 | fetchAutopilotRule | GET | /api/autopilot/rules/{param} | shared apiClient | GET /api/autopilot/rules/{rule_id} | OpenAPI snapshot | OK |
| dashboard/src/services/api/autopilot.ts | 74 | fetchAutopilotRuleVersions | GET | /api/autopilot/rules/{param}/versions | shared apiClient | GET /api/autopilot/rules/{rule_id}/versions | OpenAPI snapshot | OK |
| dashboard/src/services/api/autopilot.ts | 77 | fetchAutopilotRuleValidations | GET | /api/autopilot/rules/{param}/validations | shared apiClient | GET /api/autopilot/rules/{rule_id}/validations | OpenAPI snapshot | OK |
| dashboard/src/services/api/autopilot.ts | 80 | fetchAutopilotRulePromotionReadiness | GET | /api/autopilot/rules/{param}/promotion-readiness | shared apiClient | GET /api/autopilot/rules/{rule_id}/promotion-readiness | OpenAPI snapshot | OK |
| dashboard/src/services/api/autopilot.ts | 83 | manualPauseAutopilotRule | POST | /api/autopilot/rules/{param}/manual-pause | shared apiClient | POST /api/autopilot/rules/{rule_id}/manual-pause | OpenAPI snapshot | OK |
| dashboard/src/services/api/autopilot.ts | 86 | manualRetireAutopilotRule | POST | /api/autopilot/rules/{param}/manual-retire | shared apiClient | POST /api/autopilot/rules/{rule_id}/manual-retire | OpenAPI snapshot | OK |
| dashboard/src/services/api/autopilot.ts | 96 | executeDirectAITrade | POST | /api/autopilot/direct-trades/execute | shared apiClient | POST /api/autopilot/direct-trades/execute | OpenAPI snapshot | OK |
| dashboard/src/services/api/autopilot.ts | 100 | fetchAutopilotPerformance | GET | /api/autopilot/performance | shared apiClient | GET /api/autopilot/performance | OpenAPI snapshot | OK |
| dashboard/src/services/api/autopilot.ts | 103 | fetchAutopilotSourcePerformance | GET | /api/autopilot/performance/sources | shared apiClient | GET /api/autopilot/performance/sources | OpenAPI snapshot | OK |
| dashboard/src/services/api/autopilot.ts | 106 | fetchAutopilotRulePerformance | GET | /api/autopilot/performance/rules | shared apiClient | GET /api/autopilot/performance/rules | OpenAPI snapshot | OK |
| dashboard/src/services/api/autopilot.ts | 110 | fetchAutopilotInterventions | GET | /api/autopilot/interventions | shared apiClient | GET /api/autopilot/interventions | OpenAPI snapshot | OK |
| dashboard/src/services/api/autopilot.ts | 113 | acknowledgeAutopilotIntervention | POST | /api/autopilot/interventions/{param}/ack | shared apiClient | POST /api/autopilot/interventions/{intervention_id}/ack | OpenAPI snapshot | OK |
| dashboard/src/services/api/autopilot.ts | 116 | resolveAutopilotIntervention | POST | /api/autopilot/interventions/{param}/resolve | shared apiClient | POST /api/autopilot/interventions/{intervention_id}/resolve | OpenAPI snapshot | OK |
| dashboard/src/services/api/autopilot.ts | 120 | fetchDecisionRuns | GET | /api/autopilot/decision-runs | shared apiClient | GET /api/autopilot/decision-runs | OpenAPI snapshot | OK |
| dashboard/src/services/api/autopilot.ts | 123 | fetchDecisionRun | GET | /api/autopilot/decision-runs/{param} | shared apiClient | GET /api/autopilot/decision-runs/{run_id} | OpenAPI snapshot | OK |
| dashboard/src/services/api/autopilot.ts | 126 | fetchDecisionRunItems | GET | /api/autopilot/decision-runs/{param}/items | shared apiClient | GET /api/autopilot/decision-runs/{run_id}/items | OpenAPI snapshot | OK |
| dashboard/src/services/api/autopilot.ts | 130 | launchEvaluationReplay | POST | /api/autopilot/evaluation/replay | shared apiClient | POST /api/autopilot/evaluation/replay | OpenAPI snapshot | OK |
| dashboard/src/services/api/autopilot.ts | 133 | fetchEvaluationRuns | GET | /api/autopilot/evaluation/runs | shared apiClient | GET /api/autopilot/evaluation/runs | OpenAPI snapshot | OK |
| dashboard/src/services/api/autopilot.ts | 136 | fetchEvaluationRun | GET | /api/autopilot/evaluation/{param} | shared apiClient | GET /api/autopilot/evaluation/{evaluation_id} | OpenAPI snapshot | OK |
| dashboard/src/services/api/autopilot.ts | 139 | fetchEvaluationSlices | GET | /api/autopilot/evaluation/{param}/slices | shared apiClient | GET /api/autopilot/evaluation/{evaluation_id}/slices | OpenAPI snapshot | OK |
| dashboard/src/services/api/autopilot.ts | 142 | fetchEvaluationCompare | GET | /api/autopilot/evaluation/compare | shared apiClient | GET /api/autopilot/evaluation/compare | OpenAPI snapshot | OK |
| dashboard/src/services/api/autopilot.ts | 153 | fetchCircuitBreakerStatus | GET | /api/autopilot/circuit-breaker | shared apiClient | GET /api/autopilot/circuit-breaker | OpenAPI snapshot | OK |
| dashboard/src/services/api/autopilot.ts | 156 | resetCircuitBreaker | POST | /api/autopilot/circuit-breaker/reset | shared apiClient | POST /api/autopilot/circuit-breaker/reset | OpenAPI snapshot | OK |
| dashboard/src/services/api/autopilot.ts | 175 | runBullBearDebate | POST | /api/autopilot/debate | shared apiClient | POST /api/autopilot/debate | OpenAPI snapshot | OK |
| dashboard/src/services/api/autopilot.ts | 197 | runPersonaAnalysis | POST | /api/autopilot/persona-analysis | shared apiClient | POST /api/autopilot/persona-analysis | OpenAPI snapshot | OK |
| dashboard/src/services/api/backtest.ts | 5 | runBacktest | POST | /api/backtest/run | shared apiClient | POST /api/backtest/run | backend/routers/backtest_routes.py:18 | OK |
| dashboard/src/services/api/backtest.ts | 8 | saveBacktest | POST | /api/backtest/save | shared apiClient | POST /api/backtest/save | backend/routers/backtest_routes.py:48 | OK |
| dashboard/src/services/api/backtest.ts | 11 | fetchBacktestHistory | GET | /api/backtest/history | shared apiClient | GET /api/backtest/history | backend/routers/backtest_routes.py:73 | OK |
| dashboard/src/services/api/backtest.ts | 14 | fetchBacktest | GET | /api/backtest/{param} | shared apiClient | GET /api/backtest/{backtest_id} | backend/routers/backtest_routes.py:79 | OK |
| dashboard/src/services/api/backtest.ts | 17 | deleteBacktest | DELETE | /api/backtest/{param} | shared apiClient | DELETE /api/backtest/{backtest_id} | backend/routers/backtest_routes.py:88 | OK |
| dashboard/src/services/api/diagnostics.ts | 13 | fetchDiagnosticsOverview | GET | /api/diagnostics/overview | shared apiClient | GET /api/diagnostics/overview | backend/diagnostics_api.py:20 | OK |
| dashboard/src/services/api/diagnostics.ts | 16 | fetchDiagnosticsIndicators | GET | /api/diagnostics/indicators | shared apiClient | GET /api/diagnostics/indicators | backend/diagnostics_api.py:25 | OK |
| dashboard/src/services/api/diagnostics.ts | 19 | fetchDiagnosticsIndicator | GET | /api/diagnostics/indicators/{param} | shared apiClient | GET /api/diagnostics/indicators/{code} | backend/diagnostics_api.py:30 | OK |
| dashboard/src/services/api/diagnostics.ts | 22 | fetchDiagnosticsIndicatorHistory | GET | /api/diagnostics/indicators/{param}/history | shared apiClient | GET /api/diagnostics/indicators/{code}/history | backend/diagnostics_api.py:38 | OK |
| dashboard/src/services/api/diagnostics.ts | 25 | fetchDiagnosticsMarketMap | GET | /api/diagnostics/market-map | shared apiClient | GET /api/diagnostics/market-map | backend/diagnostics_api.py:43 | OK |
| dashboard/src/services/api/diagnostics.ts | 28 | fetchDiagnosticsSectorProjectionsLatest | GET | /api/diagnostics/sector-projections/latest | shared apiClient | GET /api/diagnostics/sector-projections/latest | backend/diagnostics_api.py:48 | OK |
| dashboard/src/services/api/diagnostics.ts | 31 | fetchDiagnosticsSectorProjectionsHistory | GET | /api/diagnostics/sector-projections/history | shared apiClient | GET /api/diagnostics/sector-projections/history | backend/diagnostics_api.py:58 | OK |
| dashboard/src/services/api/diagnostics.ts | 34 | fetchDiagnosticsNews | GET | /api/diagnostics/news | shared apiClient | GET /api/diagnostics/news | backend/diagnostics_api.py:63 | OK |
| dashboard/src/services/api/diagnostics.ts | 41 | runDiagnosticsRefresh | POST | /api/diagnostics/refresh | raw fetch; manual Authorization | POST /api/diagnostics/refresh | backend/diagnostics_api.py:71 | RAW FETCH - REFACTOR |
| dashboard/src/services/api/diagnostics.ts | 56 | fetchDiagnosticsRefreshRun | GET | /api/diagnostics/refresh/{param} | shared apiClient | GET /api/diagnostics/refresh/{run_id} | backend/diagnostics_api.py:92 | OK |
| dashboard/src/services/api/market.ts | 5 | connectIBKR | POST | /api/ibkr/connect | shared apiClient | POST /api/ibkr/connect | backend/routers/status.py:140 | OK |
| dashboard/src/services/api/market.ts | 6 | disconnectIBKR | POST | /api/ibkr/disconnect | shared apiClient | POST /api/ibkr/disconnect | backend/routers/status.py:149 | OK |
| dashboard/src/services/api/market.ts | 10 | fetchWatchlist | GET | /api/watchlist | shared apiClient | GET /api/watchlist | backend/routers/market_routes.py:102 | OK |
| dashboard/src/services/api/market.ts | 13 | fetchYahooBars | GET | /api/yahoo/{param}/bars | shared apiClient | GET /api/yahoo/{symbol}/bars | backend/routers/market_routes.py:116 | OK |
| dashboard/src/services/api/market.ts | 16 | fetchIBKRBars | GET | /api/market/{param}/bars | shared apiClient | GET /api/market/{symbol}/bars | backend/routers/market_routes.py:38 | OK |
| dashboard/src/services/api/market.ts | 19 | fetchPrice | GET | /api/market/{param}/price | shared apiClient | GET /api/market/{symbol}/price | backend/routers/market_routes.py:58 | OK |
| dashboard/src/services/api/market.ts | 21 | subscribeRtBars | POST | /api/market/{param}/subscribe | shared apiClient | POST /api/market/{symbol}/subscribe | backend/routers/market_routes.py:73 | OK |
| dashboard/src/services/api/market.ts | 22 | unsubscribeRtBars | POST | /api/market/{param}/unsubscribe | shared apiClient | POST /api/market/{symbol}/unsubscribe | backend/routers/market_routes.py:94 | OK |
| dashboard/src/services/api/rules.ts | 4 | fetchRules | GET | /api/rules | shared apiClient | GET /api/rules | OpenAPI snapshot | OK |
| dashboard/src/services/api/rules.ts | 5 | fetchRule | GET | /api/rules/{param} | shared apiClient | GET /api/rules/{rule_id} | OpenAPI snapshot | OK |
| dashboard/src/services/api/rules.ts | 6 | createRule | POST | /api/rules | shared apiClient | POST /api/rules | OpenAPI snapshot | OK |
| dashboard/src/services/api/rules.ts | 7 | updateRule | PUT | /api/rules/{param} | shared apiClient | PUT /api/rules/{rule_id} | OpenAPI snapshot | OK |
| dashboard/src/services/api/rules.ts | 8 | deleteRule | DELETE | /api/rules/{param} | shared apiClient | DELETE /api/rules/{rule_id} | OpenAPI snapshot | OK |
| dashboard/src/services/api/rules.ts | 9 | toggleRule | POST | /api/rules/{param}/toggle | shared apiClient | POST /api/rules/{rule_id}/toggle | OpenAPI snapshot | OK |
| dashboard/src/services/api/rules.ts | 10 | fetchRuleTemplates | GET | /api/rules/templates | shared apiClient | GET /api/rules/templates | backend/rule_builder_api.py:44 | OK |
| dashboard/src/services/api/screener.ts | 20 | runScan | POST | /api/screener/scan | shared apiClient | POST /api/screener/scan | backend/routers/screener_routes.py:33 | OK |
| dashboard/src/services/api/screener.ts | 22 | fetchUniverses | GET | /api/screener/universes | shared apiClient | GET /api/screener/universes | backend/routers/screener_routes.py:45 | OK |
| dashboard/src/services/api/screener.ts | 24 | fetchScreenerPresets | GET | /api/screener/presets | shared apiClient | GET /api/screener/presets | backend/routers/screener_routes.py:50 | OK |
| dashboard/src/services/api/screener.ts | 27 | saveScreenerPreset | POST | /api/screener/presets | shared apiClient | POST /api/screener/presets | backend/routers/screener_routes.py:56 | OK |
| dashboard/src/services/api/screener.ts | 30 | deleteScreenerPreset | DELETE | /api/screener/presets/{param} | shared apiClient | DELETE /api/screener/presets/{preset_id} | backend/routers/screener_routes.py:66 | OK |
| dashboard/src/services/api/screener.ts | 33 | enrichSymbols | POST | /api/screener/enrich | shared apiClient | POST /api/screener/enrich | backend/routers/screener_routes.py:73 | OK |
| dashboard/src/services/api/screener.ts | 36 | fetchIBKRScans | GET | /api/screener/ibkr-scans | shared apiClient | GET /api/screener/ibkr-scans | backend/routers/screener_routes.py:81 | OK |
| dashboard/src/services/api/screener.ts | 39 | runIBKRScan | GET | /api/screener/ibkr-scan/{param} | shared apiClient | GET /api/screener/ibkr-scan/{scan_name} | backend/routers/screener_routes.py:89 | OK |
| dashboard/src/services/api/sectorRotation.ts | 5 | fetchSectorRotation | GET | /api/sectors/rotation | shared apiClient | GET /api/sectors/rotation | backend/routers/sectors.py:9 | OK |
| dashboard/src/services/api/sectorRotation.ts | 8 | fetchSectorLeaders | GET | /api/sectors/{param}/leaders | shared apiClient | GET /api/sectors/{sector_etf}/leaders | backend/routers/sectors.py:21 | OK |
| dashboard/src/services/api/sectorRotation.ts | 11 | fetchSectorHeatmap | GET | /api/sectors/heatmap | shared apiClient | GET /api/sectors/heatmap | backend/routers/sectors.py:15 | OK |
| dashboard/src/services/api/settings.ts | 4 | fetchSettings | GET | /api/settings | shared apiClient | GET /api/settings | OpenAPI snapshot | OK |
| dashboard/src/services/api/settings.ts | 5 | updateSettings | PUT | /api/settings | shared apiClient | PUT /api/settings | OpenAPI snapshot | OK |
| dashboard/src/services/api/simulation.ts | 5 | fetchSimAccount | GET | /api/simulation/account | shared apiClient | GET /api/simulation/account | OpenAPI snapshot | OK |
| dashboard/src/services/api/simulation.ts | 6 | fetchSimPositions | GET | /api/simulation/positions | shared apiClient | GET /api/simulation/positions | OpenAPI snapshot | OK |
| dashboard/src/services/api/simulation.ts | 7 | fetchSimOrders | GET | /api/simulation/orders | shared apiClient | GET /api/simulation/orders | OpenAPI snapshot | OK |
| dashboard/src/services/api/simulation.ts | 8 | resetSimAccount | POST | /api/simulation/reset | shared apiClient | POST /api/simulation/reset | OpenAPI snapshot | OK |
| dashboard/src/services/api/simulation.ts | 11 | placeSimOrder | POST | /api/simulation/order | shared apiClient | POST /api/simulation/order | OpenAPI snapshot | OK |
| dashboard/src/services/api/simulation.ts | 14 | fetchPlaybackState | GET | /api/simulation/playback | shared apiClient | GET /api/simulation/playback | OpenAPI snapshot | OK |
| dashboard/src/services/api/simulation.ts | 16 | loadReplay | POST | /api/simulation/playback/load | shared apiClient | POST /api/simulation/playback/load | OpenAPI snapshot | OK |
| dashboard/src/services/api/simulation.ts | 17 | playReplay | POST | /api/simulation/playback/play | shared apiClient | POST /api/simulation/playback/play | OpenAPI snapshot | OK |
| dashboard/src/services/api/simulation.ts | 18 | pauseReplay | POST | /api/simulation/playback/pause | shared apiClient | POST /api/simulation/playback/pause | OpenAPI snapshot | OK |
| dashboard/src/services/api/simulation.ts | 19 | stopReplay | POST | /api/simulation/playback/stop | shared apiClient | POST /api/simulation/playback/stop | OpenAPI snapshot | OK |
| dashboard/src/services/api/simulation.ts | 21 | setReplaySpeed | POST | /api/simulation/playback/speed | shared apiClient | POST /api/simulation/playback/speed | OpenAPI snapshot | OK |
| dashboard/src/services/api/stockProfile.ts | 20 | fetchStockOverview | GET | /api/stock/{param}/overview | shared apiClient | GET /api/stock/{symbol}/overview | backend/stock_profile_api.py:14 | OK |
| dashboard/src/services/api/stockProfile.ts | 23 | fetchStockKeyStats | GET | /api/stock/{param}/key-stats | shared apiClient | GET /api/stock/{symbol}/key-stats | backend/stock_profile_api.py:21 | OK |
| dashboard/src/services/api/stockProfile.ts | 26 | fetchStockFinancials | GET | /api/stock/{param}/financials | shared apiClient | GET /api/stock/{symbol}/financials | backend/stock_profile_api.py:28 | OK |
| dashboard/src/services/api/stockProfile.ts | 29 | fetchStockAnalyst | GET | /api/stock/{param}/analyst | shared apiClient | GET /api/stock/{symbol}/analyst | backend/stock_profile_api.py:35 | OK |
| dashboard/src/services/api/stockProfile.ts | 32 | fetchStockOwnership | GET | /api/stock/{param}/ownership | shared apiClient | GET /api/stock/{symbol}/ownership | backend/stock_profile_api.py:42 | OK |
| dashboard/src/services/api/stockProfile.ts | 35 | fetchStockEvents | GET | /api/stock/{param}/events | shared apiClient | GET /api/stock/{symbol}/events | backend/stock_profile_api.py:49 | OK |
| dashboard/src/services/api/stockProfile.ts | 38 | fetchStockNarrative | GET | /api/stock/{param}/narrative | shared apiClient | GET /api/stock/{symbol}/narrative | backend/stock_profile_api.py:56 | OK |
| dashboard/src/services/api/stockProfile.ts | 41 | fetchStockFinancialStatements | GET | /api/stock/{param}/financial-statements | shared apiClient | GET /api/stock/{symbol}/financial-statements | backend/stock_profile_api.py:63 | OK |
| dashboard/src/services/api/stockProfile.ts | 44 | fetchStockAnalystDetail | GET | /api/stock/{param}/analyst-detail | shared apiClient | GET /api/stock/{symbol}/analyst-detail | backend/stock_profile_api.py:70 | OK |
| dashboard/src/services/api/stockProfile.ts | 47 | fetchStockRatingScorecard | GET | /api/stock/{param}/rating-scorecard | shared apiClient | GET /api/stock/{symbol}/rating-scorecard | backend/stock_profile_api.py:77 | OK |
| dashboard/src/services/api/stockProfile.ts | 50 | fetchStockCompanyInfo | GET | /api/stock/{param}/company-info | shared apiClient | GET /api/stock/{symbol}/company-info | backend/stock_profile_api.py:84 | OK |
| dashboard/src/services/api/stockProfile.ts | 53 | fetchStockSplits | GET | /api/stock/{param}/stock-splits | shared apiClient | GET /api/stock/{symbol}/stock-splits | backend/stock_profile_api.py:91 | OK |
| dashboard/src/services/api/stockProfile.ts | 56 | fetchStockEarningsDetail | GET | /api/stock/{param}/earnings-detail | shared apiClient | GET /api/stock/{symbol}/earnings-detail | backend/stock_profile_api.py:98 | OK |
| dashboard/src/services/api/stockProfile.ts | 59 | fetchStockProfile | GET | /api/stock/{param}/profile | shared apiClient | GET /api/stock/{symbol}/profile | backend/stock_profile_api.py:105 | OK |
| dashboard/src/services/api/swing.ts | 17 | fetchSwingDashboard | GET | /api/swing/dashboard | shared apiClient | GET /api/swing/dashboard | OpenAPI snapshot | OK |
| dashboard/src/services/api/swing.ts | 20 | fetchSwingBreadth | GET | /api/swing/breadth | shared apiClient | GET /api/swing/breadth | OpenAPI snapshot | OK |
| dashboard/src/services/api/swing.ts | 23 | fetchGuruScreener | GET | /api/swing/screener/{param} | shared apiClient | GET /api/swing/screener/{name} | OpenAPI snapshot | OK |
| dashboard/src/services/api/swing.ts | 26 | fetchATRMatrix | GET | /api/swing/atr-matrix | shared apiClient | GET /api/swing/atr-matrix | OpenAPI snapshot | OK |
| dashboard/src/services/api/swing.ts | 29 | fetchClub97 | GET | /api/swing/club97 | shared apiClient | GET /api/swing/club97 | OpenAPI snapshot | OK |
| dashboard/src/services/api/swing.ts | 32 | fetchStockbeeScan | GET | /api/swing/stockbee/{param} | shared apiClient | GET /api/swing/stockbee/{scan} | OpenAPI snapshot | OK |
| dashboard/src/services/api/swing.ts | 35 | fetchIndustries | GET | /api/swing/industries | shared apiClient | none | not present | MISSING ROUTE |
| dashboard/src/services/api/swing.ts | 38 | fetchStages | GET | /api/swing/stages | shared apiClient | GET /api/swing/stages | OpenAPI snapshot | OK |
| dashboard/src/services/api/swing.ts | 41 | fetchGrades | GET | /api/swing/grades | shared apiClient | GET /api/swing/grades | OpenAPI snapshot | OK |
| dashboard/src/services/api/trading.ts | 14 | fetchStatus | GET | /api/status | shared apiClient | GET /api/status | backend/routers/status.py:76 | OK |
| dashboard/src/services/api/trading.ts | 15 | fetchBotStatus | GET | /api/bot/status | shared apiClient | GET /api/bot/status | backend/routers/bot_routes.py:29 | OK |
| dashboard/src/services/api/trading.ts | 18 | fetchAccountSummary | GET | /api/account/summary | shared apiClient | GET /api/account/summary | backend/routers/positions.py:18 | OK |
| dashboard/src/services/api/trading.ts | 19 | fetchPositions | GET | /api/positions | shared apiClient | GET /api/positions | backend/routers/positions.py:39 | OK |
| dashboard/src/services/api/trading.ts | 20 | fetchOrders | GET | /api/orders | shared apiClient | GET /api/orders | OpenAPI snapshot | OK |
| dashboard/src/services/api/trading.ts | 21 | fetchTrades | GET | /api/trades | shared apiClient | GET /api/trades | backend/routers/bot_routes.py:11 | OK |
| dashboard/src/services/api/trading.ts | 22 | cancelOrder | DELETE | /api/orders/{param} | shared apiClient | DELETE /api/orders/{order_id} | OpenAPI snapshot | OK |
| dashboard/src/services/api/trading.ts | 31 | placeManualOrder | POST | /api/orders/manual | shared apiClient | POST /api/orders/manual | OpenAPI snapshot | OK |
| dashboard/src/services/api/trading.ts | 34 | startBot | POST | /api/bot/start | shared apiClient | POST /api/bot/start | backend/routers/bot_routes.py:17 | OK |
| dashboard/src/services/api/trading.ts | 35 | stopBot | POST | /api/bot/stop | shared apiClient | POST /api/bot/stop | backend/routers/bot_routes.py:23 | OK |

## B2 Decision

B2 passes as an inventory stage. B3 must resolve every `MISSING ROUTE`, `AUTH BYPASS`, and `RAW FETCH - REFACTOR` row, or explicitly disable the UI with an unavailable state. The `BOOTSTRAP - B6/B7` row remains until the session boundary is implemented.

/**
 * API client — thin fetch wrapper around the FastAPI backend.
 * All methods throw on non-2xx responses.
 */
import type {
  AccountSummary,
  Alert,
  AlertCreate,
  AlertHistory,
  AlertStats,
  AlertTestResult,
  AlertUpdate,
  BacktestHistoryItem,
  BacktestRequest,
  BacktestResult,
  BotStatus,
  CorrelationMatrix,
  DailyPnL,
  EnrichResult,
  ExposureBreakdown,
  DiagnosticIndicator,
  DiagnosticIndicatorHistoryPoint,
  DiagnosticMarketMap,
  DiagnosticNewsArticle,
  DiagnosticOverview,
  DiagnosticRefreshRun,
  DiagnosticSectorProjection,
  MarketQuote,
  OHLCVBar,
  OpenOrder,
  PlaybackState,
  PortfolioAnalytics,
  Position,
  RiskLimits,
  Rule,
  RuleCreate,
  ScanFilter,
  ScanResponse,
  ScreenerPreset,
  SectorHeatmapRow,
  SectorLeadersResponse,
  SectorRotation,
  SimAccountState,
  SimOrderRecord,
  SimPosition,
  StockAnalyst,
  StockAnalystDetail,
  StockCompanyInfo,
  StockEarningsDetail,
  StockEvents,
  StockFinancials,
  StockFinancialStatements,
  StockKeyStats,
  StockNarrative,
  StockOverview,
  StockOwnership,
  StockProfileBundle,
  StockRatingScorecard,
  StockSplits,
  SystemStatus,
  Trade,
  TradeHistoryRow,
  UniverseInfo,
  User,
  UserSettings,
} from '@/types'
import type {
  AdvisorReport,
  AdvisorAnalysis,
  AutoTuneResult,
  GuardrailConfig,
  AuditLogPage,
  AIStatus,
  Recommendation,
  RulePerformance,
  CostReport,
  ShadowDecisionsPage,
  ShadowPerformance,
  LearningMetrics,
  EconomicReport,
} from '@/types/advisor'

const BASE = ''  // same origin in prod; Vite proxy handles /api in dev

// Auth token storage — demo token bootstrapped on app init
let _authToken: string | null = null
export function setAuthToken(token: string | null) { _authToken = token }
export function getAuthToken() { return _authToken }

async function req<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = {}
  if (body) headers['Content-Type'] = 'application/json'
  if (_authToken) headers['Authorization'] = `Bearer ${_authToken}`

  const resp = await fetch(`${BASE}${path}`, {
    method,
    headers,
    body: body ? JSON.stringify(body) : undefined,
  })

  // 401 → clear token (prep for Stage 8 login redirect)
  if (resp.status === 401) {
    _authToken = null
  }

  if (!resp.ok) {
    const text = await resp.text().catch(() => resp.statusText)
    throw new Error(`${method} ${path} → ${resp.status}: ${text}`)
  }
  return resp.json() as Promise<T>
}

const get  = <T>(p: string)            => req<T>('GET',    p)
const post = <T>(p: string, b?: unknown) => req<T>('POST', p, b)
const put  = <T>(p: string, b?: unknown) => req<T>('PUT',  p, b)
const del  = <T>(p: string)            => req<T>('DELETE', p)

// ── Status ────────────────────────────────────────────────────────────────────

export const fetchStatus   = () => get<SystemStatus>('/api/status')
export const fetchBotStatus= () => get<BotStatus>('/api/bot/status')

// —— Diagnostics ———————————————————————————————————————————————————————

export const fetchDiagnosticsOverview = (lookbackDays: 90 | 180 | 365 = 90) =>
  get<DiagnosticOverview>(`/api/diagnostics/overview?lookback_days=${lookbackDays}`)

export const fetchDiagnosticsIndicators = () =>
  get<DiagnosticIndicator[]>('/api/diagnostics/indicators')

export const fetchDiagnosticsIndicator = (code: string) =>
  get<DiagnosticIndicator>(`/api/diagnostics/indicators/${code}`)

export const fetchDiagnosticsIndicatorHistory = (code: string, days = 365) =>
  get<DiagnosticIndicatorHistoryPoint[]>(`/api/diagnostics/indicators/${code}/history?days=${days}`)

export const fetchDiagnosticsMarketMap = (days = 5) =>
  get<DiagnosticMarketMap[]>(`/api/diagnostics/market-map?days=${days}`)

export const fetchDiagnosticsSectorProjectionsLatest = (lookbackDays: 90 | 180 | 365 = 90) =>
  get<DiagnosticSectorProjection>(`/api/diagnostics/sector-projections/latest?lookback_days=${lookbackDays}`)

export const fetchDiagnosticsSectorProjectionsHistory = (days = 365) =>
  get<DiagnosticSectorProjection[]>(`/api/diagnostics/sector-projections/history?days=${days}`)

export const fetchDiagnosticsNews = (hours = 24, limit = 200) =>
  get<DiagnosticNewsArticle[]>(`/api/diagnostics/news?hours=${hours}&limit=${limit}`)

export async function runDiagnosticsRefresh(): Promise<
  | { status: 202; data: { run_id: number; status: string } }
  | { status: 409; data: { run_id: number; locked_by: string; lock_expires_at: number } }
> {
  const resp = await fetch(`${BASE}/api/diagnostics/refresh`, {
    method: 'POST',
    headers: _authToken ? { Authorization: `Bearer ${_authToken}` } : {},
  })
  const body = await resp.json().catch(() => ({}))
  if (resp.status === 202) {
    return { status: 202, data: body as { run_id: number; status: string } }
  }
  if (resp.status === 409) {
    return { status: 409, data: body as { run_id: number; locked_by: string; lock_expires_at: number } }
  }
  throw new Error(`POST /api/diagnostics/refresh -> ${resp.status}: ${JSON.stringify(body)}`)
}

export const fetchDiagnosticsRefreshRun = (runId: number) =>
  get<DiagnosticRefreshRun>(`/api/diagnostics/refresh/${runId}`)

// ── IBKR ──────────────────────────────────────────────────────────────────────

export const connectIBKR    = () => post<{ connected: boolean }>('/api/ibkr/connect')
export const disconnectIBKR = () => post<{ connected: boolean }>('/api/ibkr/disconnect')

// ── Account ───────────────────────────────────────────────────────────────────

export const fetchAccountSummary = () => get<AccountSummary | SimAccountState>('/api/account/summary')
export const fetchPositions      = () => get<(Position | SimPosition)[]>('/api/positions')
export const fetchOrders         = () => get<OpenOrder[]>('/api/orders')
export const fetchTrades         = (limit = 200) => get<Trade[]>(`/api/trades?limit=${limit}`)
export const cancelOrder         = (id: number)  => del<{ cancelled: boolean }>(`/api/orders/${id}`)

export const placeManualOrder = (body: {
  symbol: string
  action: 'BUY' | 'SELL'
  quantity: number
  order_type?: 'MKT' | 'LMT'
  limit_price?: number
  asset_type?: 'STK' | 'OPT' | 'FUT'
}) => post<{ success?: boolean; message?: string }>('/api/orders/manual', body)

// ── Market data ───────────────────────────────────────────────────────────────

export const fetchWatchlist = (symbols?: string) =>
  get<MarketQuote[]>(`/api/watchlist${symbols ? `?symbols=${encodeURIComponent(symbols)}` : ''}`)

export const fetchYahooBars = (symbol: string, period = '5d', interval = '5m') =>
  get<OHLCVBar[]>(`/api/yahoo/${symbol}/bars?period=${period}&interval=${interval}`)

export const fetchIBKRBars = (symbol: string, barSize = '1D', duration = '60 D') =>
  get<OHLCVBar[]>(`/api/market/${symbol}/bars?bar_size=${barSize}&duration=${encodeURIComponent(duration)}`)

export const fetchPrice = (symbol: string) =>
  get<{ symbol: string; price: number }>(`/api/market/${symbol}/price`)

export const subscribeRtBars   = (symbol: string) => post<{ subscribed: boolean }>(`/api/market/${symbol}/subscribe`)
export const unsubscribeRtBars = (symbol: string) => post<{ subscribed: boolean }>(`/api/market/${symbol}/unsubscribe`)

// ── Simulation ────────────────────────────────────────────────────────────────

export const fetchSimAccount   = () => get<SimAccountState>('/api/simulation/account')
export const fetchSimPositions = () => get<SimPosition[]>('/api/simulation/positions')
export const fetchSimOrders    = (limit = 100) => get<SimOrderRecord[]>(`/api/simulation/orders?limit=${limit}`)
export const resetSimAccount   = () => post<{ reset: boolean }>('/api/simulation/reset')

export const placeSimOrder = (body: { symbol: string; action: 'BUY' | 'SELL'; qty: number; price: number }) =>
  post<{ success: boolean; message: string }>('/api/simulation/order', body)

// ── Playback ──────────────────────────────────────────────────────────────────

export const fetchPlaybackState  = () => get<PlaybackState>('/api/simulation/playback')
export const loadReplay = (symbol: string, period = '1y', interval = '1d') =>
  post<PlaybackState>('/api/simulation/playback/load', { symbol, period, interval })
export const playReplay          = () => post<PlaybackState>('/api/simulation/playback/play')
export const pauseReplay         = () => post<PlaybackState>('/api/simulation/playback/pause')
export const stopReplay          = () => post<PlaybackState>('/api/simulation/playback/stop')
export const setReplaySpeed      = (speed: number) =>
  post<{ speed: number }>('/api/simulation/playback/speed', { speed })

// ── Rules ─────────────────────────────────────────────────────────────────────

export const fetchRules         = () => get<Rule[]>('/api/rules')
export const fetchRule          = (id: string) => get<Rule>(`/api/rules/${id}`)
export const createRule         = (body: RuleCreate) => post<Rule>('/api/rules', body)
export const updateRule         = (id: string, body: Partial<Rule>) => put<Rule>(`/api/rules/${id}`, body)
export const deleteRule         = (id: string) => del<{ deleted: boolean }>(`/api/rules/${id}`)
export const toggleRule         = (id: string) => post<{ id: string; enabled: boolean }>(`/api/rules/${id}/toggle`)
export const fetchRuleTemplates = () => get<import('@/types').RuleTemplate[]>('/api/rules/templates')

// ── Bot ───────────────────────────────────────────────────────────────────────

export const startBot = () => post<{ running: boolean }>('/api/bot/start')
export const stopBot  = () => post<{ running: boolean }>('/api/bot/stop')

// ── Auth ──────────────────────────────────────────────────────────────────

export const fetchAuthToken = () => post<{ access_token: string; token_type: string }>('/api/auth/token')
export const fetchAuthMe    = () => get<User>('/api/auth/me')

// ── Settings ──────────────────────────────────────────────────────────────

export const fetchSettings  = () => get<UserSettings>('/api/settings')
export const updateSettings = (partial: Partial<UserSettings>) => put<UserSettings>('/api/settings', partial)

// ── Screener ─────────────────────────────────────────────────────────────

export const runScan = (request: {
  universe: string
  symbols?: string[]
  filters: ScanFilter[]
  interval: string
  period: string
  limit: number
}) => post<ScanResponse>('/api/screener/scan', request)

export const fetchUniverses = () => get<UniverseInfo[]>('/api/screener/universes')

export const fetchScreenerPresets = () => get<ScreenerPreset[]>('/api/screener/presets')

export const saveScreenerPreset = (name: string, filters: ScanFilter[]) =>
  post<ScreenerPreset>('/api/screener/presets', { name, filters })

export const deleteScreenerPreset = (id: string) =>
  del<{ deleted: boolean }>(`/api/screener/presets/${id}`)

export const enrichSymbols = (symbols: string[]) =>
  post<EnrichResult[]>('/api/screener/enrich', { symbols })

// ── Backtesting ─────────────────────────────────────────────────────────

export const runBacktest = (body: BacktestRequest) =>
  post<BacktestResult>('/api/backtest/run', body)

export const saveBacktest = (name: string, result: BacktestResult) =>
  post<{ id: string; saved: boolean }>('/api/backtest/save', { name, result })

export const fetchBacktestHistory = () =>
  get<BacktestHistoryItem[]>('/api/backtest/history')

export const fetchBacktest = (id: string) =>
  get<BacktestResult>(`/api/backtest/${id}`)

export const deleteBacktest = (id: string) =>
  del<{ deleted: boolean }>(`/api/backtest/${id}`)

// ── Alerts ─────────────────────────────────────────────────────────────────

export const fetchAlerts       = () => get<Alert[]>('/api/alerts')
export const fetchAlert        = (id: string) => get<Alert>(`/api/alerts/${id}`)
export const createAlert       = (body: AlertCreate) => post<Alert>('/api/alerts', body)
export const updateAlert       = (id: string, body: AlertUpdate) => put<Alert>(`/api/alerts/${id}`, body)
export const deleteAlert       = (id: string) => del<{ deleted: boolean }>(`/api/alerts/${id}`)
export const toggleAlert       = (id: string) => post<{ id: string; enabled: boolean }>(`/api/alerts/${id}/toggle`)
export const fetchAlertHistory = (limit = 100) => get<AlertHistory[]>(`/api/alerts/history?limit=${limit}`)
export const testAlertNotification = (body: AlertCreate) => post<AlertTestResult>('/api/alerts/test', body)
export const fetchAlertStats   = () => get<AlertStats>('/api/alerts/stats')

/** Subscribe this browser to Web Push notifications. */
export const subscribePush = (subscription: PushSubscriptionJSON) =>
  post<{ subscribed: boolean }>('/api/push/subscribe', subscription)

// ── Indicators ──────────────────────────────────────────────────────────

export const fetchIndicatorData = (
  symbol: string,
  indicator: string,
  params: { length?: number; period?: string; interval?: string; fast?: number; slow?: number; signal?: number; band?: string } = {},
) => {
  const qs = new URLSearchParams({ indicator })
  if (params.length != null)   qs.set('length',   String(params.length))
  if (params.period)           qs.set('period',   params.period)
  if (params.interval)         qs.set('interval', params.interval)
  if (params.fast != null)     qs.set('fast',     String(params.fast))
  if (params.slow != null)     qs.set('slow',     String(params.slow))
  if (params.signal != null)   qs.set('signal',   String(params.signal))
  if (params.band)             qs.set('band',     params.band)
  return get<Array<{ time: number; value: number }>>(`/api/market/${symbol}/indicators?${qs}`)
}

// ── Stock Profile ─────────────────────────────────────────────────────────

export const fetchStockOverview = (symbol: string) =>
  get<StockOverview>(`/api/stock/${symbol}/overview`)

export const fetchStockKeyStats = (symbol: string) =>
  get<StockKeyStats>(`/api/stock/${symbol}/key-stats`)

export const fetchStockFinancials = (symbol: string) =>
  get<StockFinancials>(`/api/stock/${symbol}/financials`)

export const fetchStockAnalyst = (symbol: string) =>
  get<StockAnalyst>(`/api/stock/${symbol}/analyst`)

export const fetchStockOwnership = (symbol: string) =>
  get<StockOwnership>(`/api/stock/${symbol}/ownership`)

export const fetchStockEvents = (symbol: string) =>
  get<StockEvents>(`/api/stock/${symbol}/events`)

export const fetchStockNarrative = (symbol: string) =>
  get<StockNarrative>(`/api/stock/${symbol}/narrative`)

export const fetchStockFinancialStatements = (symbol: string) =>
  get<StockFinancialStatements>(`/api/stock/${symbol}/financial-statements`)

export const fetchStockAnalystDetail = (symbol: string) =>
  get<StockAnalystDetail>(`/api/stock/${symbol}/analyst-detail`)

export const fetchStockRatingScorecard = (symbol: string) =>
  get<StockRatingScorecard>(`/api/stock/${symbol}/rating-scorecard`)

export const fetchStockCompanyInfo = (symbol: string) =>
  get<StockCompanyInfo>(`/api/stock/${symbol}/company-info`)

export const fetchStockSplits = (symbol: string) =>
  get<StockSplits>(`/api/stock/${symbol}/stock-splits`)

export const fetchStockEarningsDetail = (symbol: string) =>
  get<StockEarningsDetail>(`/api/stock/${symbol}/earnings-detail`)

export const fetchStockProfile = (symbol: string) =>
  get<StockProfileBundle>(`/api/stock/${symbol}/profile`)

// ── Sector Rotation ─────────────────────────────────────────────────────────

export const fetchSectorRotation = (lookbackDays = 90): Promise<SectorRotation[]> =>
  get<SectorRotation[]>(`/api/sectors/rotation?lookback_days=${lookbackDays}`)

export const fetchSectorLeaders = (sectorEtf: string, topN = 10, period = '3mo'): Promise<SectorLeadersResponse> =>
  get<SectorLeadersResponse>(`/api/sectors/${sectorEtf}/leaders?top_n=${topN}&period=${period}`)

export const fetchSectorHeatmap = (): Promise<SectorHeatmapRow[]> =>
  get<SectorHeatmapRow[]>('/api/sectors/heatmap')

// ── Portfolio Analytics & Risk ───────────────────────────────────────────────

export const fetchPortfolioAnalytics = (range: '1W' | '1M' | '3M' | '6M' | '1Y' | 'ALL' = '3M') =>
  get<PortfolioAnalytics>(`/api/risk/portfolio?range=${range}`)

export const fetchDailyPnL = (days = 90) =>
  get<DailyPnL[]>(`/api/risk/pnl/daily?days=${days}`)

export const fetchExposureBreakdown = () =>
  get<ExposureBreakdown>('/api/risk/exposure')

export const fetchRiskLimits = () =>
  get<RiskLimits>('/api/risk/limits')

export const fetchTradeHistory = (limit = 20) =>
  get<TradeHistoryRow[]>(`/api/risk/trades?limit=${limit}`)

export const fetchCorrelationMatrix = () =>
  get<CorrelationMatrix>('/api/risk/correlation')

// ── AI Advisor ────────────────────────────────────────────────────────────────

export const fetchAdvisorReport = (lookbackDays = 90, refresh = false) =>
  get<AdvisorReport>(`/api/advisor/report?lookback_days=${lookbackDays}&refresh=${refresh}`)

export const fetchAdvisorRecommendations = (
  lookbackDays = 90,
  maxPriority: 'high' | 'medium' | 'low' = 'low',
) =>
  get<{ recommendations: Recommendation[]; total: number }>(
    `/api/advisor/recommendations?lookback_days=${lookbackDays}&max_priority=${maxPriority}`
  )

export const fetchAdvisorAnalysis = (lookbackDays = 90) =>
  get<AdvisorAnalysis>(`/api/advisor/analysis?lookback_days=${lookbackDays}`)

export const fetchAdvisorDailyReport = (lookbackDays = 90) =>
  get<{ report: string }>(`/api/advisor/daily-report?lookback_days=${lookbackDays}`)

export const postAdvisorAutoTune = (apply = false, lookbackDays = 90) =>
  post<AutoTuneResult>(`/api/advisor/auto-tune?apply=${apply}&lookback_days=${lookbackDays}`)

export const fetchAdvisorRuleAnalysis = (ruleId: string, lookbackDays = 90) =>
  get<RulePerformance>(`/api/advisor/rule/${ruleId}?lookback_days=${lookbackDays}`)

export const fetchGuardrails = () =>
  get<GuardrailConfig>('/api/advisor/guardrails')

export const updateGuardrails = (config: Partial<GuardrailConfig>) =>
  put<GuardrailConfig>('/api/advisor/guardrails', config)

export const postEmergencyStop = () =>
  post<{ emergency_stop: boolean; message: string }>('/api/advisor/emergency-stop')

export const fetchAuditLog = (limit = 50, offset = 0) =>
  get<AuditLogPage>(`/api/advisor/audit-log?limit=${limit}&offset=${offset}`)

export const revertAIAction = (entryId: number) =>
  post<{ reverted: boolean }>(`/api/advisor/audit-log/${entryId}/revert`)

export const fetchAIStatus = () =>
  get<AIStatus>('/api/advisor/ai-status')

export const fetchAICosts = (days = 30) =>
  get<CostReport>(`/api/advisor/costs?days=${days}`)

export const fetchShadowDecisions = (
  limit = 50, offset = 0,
  paramType?: string, symbol?: string, regime?: string, minConfidence?: number,
) => {
  const qs = new URLSearchParams({ limit: String(limit), offset: String(offset) })
  if (paramType) qs.set('param_type', paramType)
  if (symbol) qs.set('symbol', symbol)
  if (regime) qs.set('regime', regime)
  if (minConfidence != null) qs.set('min_confidence', String(minConfidence))
  return get<ShadowDecisionsPage>(`/api/advisor/shadow-decisions?${qs}`)
}

export const fetchShadowPerformance = () =>
  get<ShadowPerformance>('/api/advisor/shadow-performance')

export const toggleShadowMode = (enable: boolean, force = false) =>
  post<{ shadow_mode: boolean; message: string }>('/api/advisor/shadow-mode', { enable, force })

export const fetchLearningMetrics = (windowDays = 30) =>
  get<LearningMetrics>(`/api/advisor/learning-metrics?window_days=${windowDays}`)

export const fetchEconomicReport = (days = 30) =>
  get<EconomicReport>(`/api/advisor/economic-report?days=${days}`)

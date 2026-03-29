/**
 * API client â€” thin fetch wrapper around the FastAPI backend.
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
  AutopilotConfig,
  AutopilotIntervention,
  AutopilotPerformance,
  AuditLogPage,
  AIStatus,
  AIRuleAction,
  AIDirectTrade,
  RulePerformanceRow,
  RulePromotionReadiness,
  RuleValidationRecord,
  RuleVersionRecord,
  CostReport,
  SourcePerformance,
  LearningMetrics,
  EconomicReport,
  DecisionRun,
  DecisionItem,
  EvaluationRun,
  EvaluationSlice,
  EvaluationCompare,
  ReplayRequest,
} from '@/types/advisor'

const BASE = ''  // same origin in prod; Vite proxy handles /api in dev

// Auth token storage â€” demo token bootstrapped on app init
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

  // 401 â†’ clear token (prep for Stage 8 login redirect)
  if (resp.status === 401) {
    _authToken = null
  }

  if (!resp.ok) {
    const text = await resp.text().catch(() => resp.statusText)
    throw new Error(`${method} ${path} â†’ ${resp.status}: ${text}`)
  }
  return resp.json() as Promise<T>
}

const get  = <T>(p: string)            => req<T>('GET',    p)
const post = <T>(p: string, b?: unknown) => req<T>('POST', p, b)
const put  = <T>(p: string, b?: unknown) => req<T>('PUT',  p, b)
const del  = <T>(p: string)            => req<T>('DELETE', p)

// â”€â”€ Status â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

export const fetchStatus   = () => get<SystemStatus>('/api/status')
export const fetchBotStatus= () => get<BotStatus>('/api/bot/status')

// â€”â€” Diagnostics â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”

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

// â”€â”€ IBKR â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

export const connectIBKR    = () => post<{ connected: boolean }>('/api/ibkr/connect')
export const disconnectIBKR = () => post<{ connected: boolean }>('/api/ibkr/disconnect')

// â”€â”€ Account â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

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

// â”€â”€ Market data â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

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

// â”€â”€ Simulation â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

export const fetchSimAccount   = () => get<SimAccountState>('/api/simulation/account')
export const fetchSimPositions = () => get<SimPosition[]>('/api/simulation/positions')
export const fetchSimOrders    = (limit = 100) => get<SimOrderRecord[]>(`/api/simulation/orders?limit=${limit}`)
export const resetSimAccount   = () => post<{ reset: boolean }>('/api/simulation/reset')

export const placeSimOrder = (body: { symbol: string; action: 'BUY' | 'SELL'; qty: number; price: number }) =>
  post<{ success: boolean; message: string }>('/api/simulation/order', body)

// â”€â”€ Playback â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

export const fetchPlaybackState  = () => get<PlaybackState>('/api/simulation/playback')
export const loadReplay = (symbol: string, period = '1y', interval = '1d') =>
  post<PlaybackState>('/api/simulation/playback/load', { symbol, period, interval })
export const playReplay          = () => post<PlaybackState>('/api/simulation/playback/play')
export const pauseReplay         = () => post<PlaybackState>('/api/simulation/playback/pause')
export const stopReplay          = () => post<PlaybackState>('/api/simulation/playback/stop')
export const setReplaySpeed      = (speed: number) =>
  post<{ speed: number }>('/api/simulation/playback/speed', { speed })

// â”€â”€ Rules â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

export const fetchRules         = () => get<Rule[]>('/api/rules')
export const fetchRule          = (id: string) => get<Rule>(`/api/rules/${id}`)
export const createRule         = (body: RuleCreate) => post<Rule>('/api/rules', body)
export const updateRule         = (id: string, body: Partial<Rule>) => put<Rule>(`/api/rules/${id}`, body)
export const deleteRule         = (id: string) => del<{ deleted: boolean }>(`/api/rules/${id}`)
export const toggleRule         = (id: string) => post<{ id: string; enabled: boolean }>(`/api/rules/${id}/toggle`)
export const fetchRuleTemplates = () => get<import('@/types').RuleTemplate[]>('/api/rules/templates')

// â”€â”€ Bot â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

export const startBot = () => post<{ running: boolean }>('/api/bot/start')
export const stopBot  = () => post<{ running: boolean }>('/api/bot/stop')

// â”€â”€ Auth â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

export const fetchAuthToken = () => post<{ access_token: string; token_type: string }>('/api/auth/token')
export const fetchAuthMe    = () => get<User>('/api/auth/me')

// â”€â”€ Settings â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

export const fetchSettings  = () => get<UserSettings>('/api/settings')
export const updateSettings = (partial: Partial<UserSettings>) => put<UserSettings>('/api/settings', partial)

// â”€â”€ Screener â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

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

// â”€â”€ Backtesting â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

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

// â”€â”€ Alerts â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

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

// â”€â”€ Indicators â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

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

// â”€â”€ Stock Profile â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

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

// â”€â”€ Sector Rotation â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

export const fetchSectorRotation = (lookbackDays = 90): Promise<SectorRotation[]> =>
  get<SectorRotation[]>(`/api/sectors/rotation?lookback_days=${lookbackDays}`)

export const fetchSectorLeaders = (sectorEtf: string, topN = 10, period = '3mo'): Promise<SectorLeadersResponse> =>
  get<SectorLeadersResponse>(`/api/sectors/${sectorEtf}/leaders?top_n=${topN}&period=${period}`)

export const fetchSectorHeatmap = (): Promise<SectorHeatmapRow[]> =>
  get<SectorHeatmapRow[]>('/api/sectors/heatmap')

// â”€â”€ Portfolio Analytics & Risk â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

type RiskPortfolioResponse = {
  pnl?: {
    total_pnl?: number
    win_rate?: number
  }
  daily_pnl?: Array<{
    date: string
    pnl: number
    cumulative: number
  }>
  performance?: {
    total_return?: number
    total_return_pct?: number
    sharpe_ratio?: number
    win_rate?: number
  }
  drawdown?: {
    current_pct?: number
    max_pct?: number
  }
}

type RiskSettingsResponse = {
  max_position_pct: number
  max_sector_pct: number
  max_daily_loss_pct: number
  max_drawdown_pct: number
  max_open_positions: number
}

type CorrelationApiResponse = CorrelationMatrix & { error?: string }

const ANALYTICS_BASELINE_EQUITY = 100_000

function toChartTime(date: string): number {
  const parsed = Date.parse(`${date}T00:00:00Z`)
  return Number.isFinite(parsed) ? Math.floor(parsed / 1000) : 0
}

function buildEquityCurve(
  rows: Array<{ date: string; cumulative: number }> | undefined,
): PortfolioAnalytics['equity_curve'] {
  return (rows ?? []).map((row) => ({
    time: toChartTime(row.date),
    value: ANALYTICS_BASELINE_EQUITY + Number(row.cumulative ?? 0),
  }))
}

function mapPortfolioAnalytics(payload: RiskPortfolioResponse): PortfolioAnalytics {
  const daily = payload.daily_pnl ?? []
  const equityCurve = buildEquityCurve(daily)
  const latest = daily[daily.length - 1]
  const previousValue = equityCurve.length >= 2
    ? equityCurve[equityCurve.length - 2].value
    : ANALYTICS_BASELINE_EQUITY
  const dayPnl = Number(latest?.pnl ?? 0)
  const totalPnl = Number(payload.pnl?.total_pnl ?? payload.performance?.total_return ?? 0)

  return {
    total_value: equityCurve.length > 0
      ? equityCurve[equityCurve.length - 1].value
      : ANALYTICS_BASELINE_EQUITY + totalPnl,
    day_pnl: dayPnl,
    day_pnl_pct: previousValue > 0 ? (dayPnl / previousValue) * 100 : 0,
    total_pnl: totalPnl,
    total_pnl_pct: Number(payload.performance?.total_return_pct ?? 0),
    win_rate: Number(payload.performance?.win_rate ?? payload.pnl?.win_rate ?? 0),
    sharpe_ratio: Number(payload.performance?.sharpe_ratio ?? 0),
    max_drawdown_pct: -Math.abs(Number(payload.drawdown?.max_pct ?? 0)),
    equity_curve: equityCurve,
    benchmark_curve: [],
  }
}

function mapDailyPnL(rows: Array<{ date: string; pnl: number }> | undefined): DailyPnL[] {
  return (rows ?? []).map((row) => ({
    date: row.date,
    pnl: Number(row.pnl ?? 0),
    trades: 0,
  }))
}

function normalizePositionExposure(position: Position | SimPosition) {
  const value = Math.abs(Number(position.market_value ?? 0))
  const pnl = Number(position.unrealized_pnl ?? 0) + Number('realized_pnl' in position ? position.realized_pnl ?? 0 : 0)
  const qty = Math.abs(Number(position.qty ?? 0))
  const avgCost = Number(position.avg_cost ?? 0)
  const costBasis = qty * avgCost
  const pnlPct = 'pnl_pct' in position && typeof position.pnl_pct === 'number'
    ? position.pnl_pct
    : costBasis > 0 ? (pnl / costBasis) * 100 : 0

  return {
    symbol: position.symbol,
    sector: 'Unknown',
    value,
    weight_pct: 0,
    pnl,
    pnl_pct: pnlPct,
  }
}

function toTradeHistoryRow(trade: Trade, index: number): TradeHistoryRow {
  const rawMetadata = (trade.metadata ?? {}) as Record<string, unknown>
  const rawRealized = typeof trade.realized_pnl === 'number'
    ? trade.realized_pnl
    : typeof rawMetadata.realized_pnl === 'number'
      ? rawMetadata.realized_pnl
      : typeof rawMetadata.pnl === 'number'
        ? rawMetadata.pnl
        : undefined

  const openedAt = typeof trade.opened_at === 'string' ? trade.opened_at : undefined
  const closedAt = typeof trade.closed_at === 'string' ? trade.closed_at : undefined
  const holdingDays = openedAt && closedAt
    ? Math.max(0, Math.round((Date.parse(closedAt) - Date.parse(openedAt)) / 86400000))
    : undefined

  return {
    id: trade.id || `${trade.symbol}-${trade.timestamp}-${index}`,
    symbol: trade.symbol,
    action: trade.action,
    quantity: trade.quantity,
    fill_price: Number(trade.fill_price ?? trade.exit_price ?? trade.entry_price ?? 0),
    pnl: rawRealized,
    timestamp: closedAt ?? trade.timestamp,
    holding_days: holdingDays,
  }
}

export const fetchPortfolioAnalytics = async (
  range: '1W' | '1M' | '3M' | '6M' | '1Y' | 'ALL' = '3M',
) => {
  const payload = await get<RiskPortfolioResponse>(`/api/risk/portfolio?range=${range}`)
  return mapPortfolioAnalytics(payload)
}

export const fetchDailyPnL = async (days = 90) => {
  const rows = await get<Array<{ date: string; pnl: number; cumulative?: number }>>(`/api/analytics/pnl/daily?days=${days}`)
  return mapDailyPnL(rows)
}

export const fetchExposureBreakdown = async () => {
  const positions = await fetchPositions()
  const normalized = positions.map(normalizePositionExposure)
  const totalValue = normalized.reduce((sum, position) => sum + position.value, 0)
  // No sector API available â€” leave empty rather than faking "Unknown: 100%"
  const sectorWeights: Record<string, number> = {}

  return {
    positions: normalized.map((position) => ({
      ...position,
      weight_pct: totalValue > 0 ? (position.value / totalValue) * 100 : 0,
    })),
    sector_weights: sectorWeights,
  } satisfies ExposureBreakdown
}

export const fetchRiskLimits = async () => {
  const [settings, positions, account, dailyPnl, drawdown] = await Promise.all([
    get<RiskSettingsResponse>('/api/risk/settings'),
    fetchPositions(),
    fetchAccountSummary(),
    get<Array<{ date: string; pnl: number }>>('/api/analytics/pnl/daily?days=1'),
    get<{ current_pct?: number }>('/api/risk/drawdown'),
  ])

  const accountValue = 'net_liquidation' in account
    ? Number(account.net_liquidation ?? 0)
    : Number(account.balance ?? 0)
  const largestPositionValue = positions.reduce(
    (largest, position) => Math.max(largest, Math.abs(Number(position.market_value ?? 0))),
    0,
  )
  const latestDailyPnl = Number(dailyPnl[dailyPnl.length - 1]?.pnl ?? 0)
  const dailyLossLimit = accountValue > 0
    ? (accountValue * Number(settings.max_daily_loss_pct ?? 0)) / 100
    : 0
  const currentDrawdown = Math.abs(Number(drawdown.current_pct ?? 0))

  return {
    limits: [
      {
        label: 'Max Position Size',
        used: accountValue > 0 ? (largestPositionValue / accountValue) * 100 : 0,
        limit: Number(settings.max_position_pct ?? 0),
        unit: '%',
      },
      {
        label: 'Daily Loss Limit',
        used: latestDailyPnl < 0 ? Math.abs(latestDailyPnl) : 0,
        limit: dailyLossLimit,
        unit: '$',
      },
      {
        label: 'Max Drawdown',
        used: currentDrawdown,
        limit: Number(settings.max_drawdown_pct ?? 0),
        unit: '%',
      },
      {
        label: 'Open Positions',
        used: positions.length,
        limit: Number(settings.max_open_positions ?? 0),
        unit: 'count',
      },
    ],
    max_position_size_pct: Number(settings.max_position_pct ?? 0),
    daily_loss_limit: dailyLossLimit,
    drawdown_limit_pct: Number(settings.max_drawdown_pct ?? 0),
    max_open_positions: Number(settings.max_open_positions ?? 0),
  } satisfies RiskLimits
}

export const fetchTradeHistory = async (limit = 20) => {
  const trades = await fetchTrades(limit)
  return trades.map(toTradeHistoryRow)
}

export const fetchCorrelationMatrix = async () => {
  const positions = await fetchPositions()
  const symbols = Array.from(
    new Set(
      positions
        .map((position) => position.symbol?.trim().toUpperCase())
        .filter((symbol): symbol is string => Boolean(symbol)),
    ),
  )

  if (symbols.length < 3) {
    return { symbols, matrix: [] } satisfies CorrelationMatrix
  }

  const payload = await get<CorrelationApiResponse>(`/api/risk/correlation?symbols=${encodeURIComponent(symbols.join(','))}`)
  if (payload.error) {
    throw new Error(payload.error)
  }
  if (!Array.isArray(payload.symbols) || !Array.isArray(payload.matrix)) {
    throw new Error('Invalid correlation payload')
  }

  const expectedSize = payload.symbols.length
  const hasValidMatrix =
    expectedSize >= 3 &&
    payload.matrix.length === expectedSize &&
    payload.matrix.every(
      (row) => Array.isArray(row) && row.length === expectedSize && row.every((value) => typeof value === 'number' && Number.isFinite(value)),
    )

  if (!hasValidMatrix) {
    throw new Error('Invalid correlation payload')
  }

  return payload satisfies CorrelationMatrix
}

// â”€â”€ Autopilot â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

export const fetchGuardrails = () =>
  get<AutopilotConfig>('/api/autopilot/config')

export const updateGuardrails = (config: Partial<AutopilotConfig>) =>
  put<AutopilotConfig>('/api/autopilot/config', config)

export const postEmergencyStop = () =>
  post<{ emergency_stop: boolean; message: string }>('/api/autopilot/kill')

export const resetEmergencyStop = () =>
  post<{ emergency_stop: boolean; message: string }>('/api/autopilot/kill/reset')

export const setAutopilotMode = (mode: 'OFF' | 'PAPER' | 'LIVE', reason = '') =>
  post<AutopilotConfig>('/api/autopilot/mode', { mode, reason })

export const resetDailyLossLock = () =>
  post<AutopilotConfig>('/api/autopilot/daily-loss/reset')

export const fetchAuditLog = (limit = 50, offset = 0) =>
  get<AuditLogPage>(`/api/autopilot/feed?limit=${limit}&offset=${offset}`)

export const revertAIAction = (entryId: number) =>
  post<{ reverted: boolean }>(`/api/autopilot/feed/${entryId}/revert`)

export const fetchAIStatus = () =>
  get<AIStatus>('/api/autopilot/status')

export const fetchAICosts = (days = 30) =>
  get<CostReport>(`/api/autopilot/costs?days=${days}`)

export const fetchLearningMetrics = (windowDays = 30) =>
  get<LearningMetrics>(`/api/autopilot/learning-metrics?window_days=${windowDays}`)

export const fetchEconomicReport = (days = 30) =>
  get<EconomicReport>(`/api/autopilot/economic-report?days=${days}`)

export const fetchAutopilotRules = () =>
  get<Rule[]>('/api/autopilot/rules')

export const fetchAutopilotRule = (id: string) =>
  get<Rule>(`/api/autopilot/rules/${id}`)

export const fetchAutopilotRuleVersions = (id: string) =>
  get<RuleVersionRecord[]>(`/api/autopilot/rules/${id}/versions`)

export const fetchAutopilotRuleValidations = (id: string) =>
  get<RuleValidationRecord[]>(`/api/autopilot/rules/${id}/validations`)

export const fetchAutopilotRulePromotionReadiness = (id: string) =>
  get<RulePromotionReadiness>(`/api/autopilot/rules/${id}/promotion-readiness`)

export const manualPauseAutopilotRule = (id: string, reason = '') =>
  post<Rule>(`/api/autopilot/rules/${id}/manual-pause`, { reason })

export const manualRetireAutopilotRule = (id: string, reason = '') =>
  post<Rule>(`/api/autopilot/rules/${id}/manual-retire`, { reason })

export const applyRuleLabActions = (actions: AIRuleAction[], author = 'ai', allowActive = false) =>
  post<{ results: Array<Record<string, unknown>> }>('/api/autopilot/rule-lab/apply', {
    actions,
    author,
    allow_active: allowActive,
  })

export const executeDirectAITrade = (decision: AIDirectTrade) =>
  post<{ mode: string; simulated: boolean; trade: Trade }>('/api/autopilot/direct-trades/execute', decision)

export const fetchAutopilotPerformance = (window = 30) =>
  get<AutopilotPerformance>(`/api/autopilot/performance?window=${window}`)

export const fetchAutopilotSourcePerformance = (window = 30) =>
  get<SourcePerformance[]>(`/api/autopilot/performance/sources?window=${window}`)

export const fetchAutopilotRulePerformance = (window = 30) =>
  get<RulePerformanceRow[]>(`/api/autopilot/performance/rules?window=${window}`)

export const fetchAutopilotInterventions = (includeResolved = false) =>
  get<AutopilotIntervention[]>(`/api/autopilot/interventions?include_resolved=${includeResolved}`)

export const acknowledgeAutopilotIntervention = (id: number) =>
  post<{ acknowledged: boolean }>(`/api/autopilot/interventions/${id}/ack`)

export const resolveAutopilotIntervention = (id: number, resolvedBy = 'operator') =>
  post<{ resolved: boolean; resolved_by: string }>(`/api/autopilot/interventions/${id}/resolve`, { resolved_by: resolvedBy })

// â”€â”€ S10: Decision Ledger â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

export const fetchDecisionRuns = (limit = 50, offset = 0) =>
  get<DecisionRun[]>(`/api/autopilot/decision-runs?limit=${limit}&offset=${offset}`)

export const fetchDecisionRun = (runId: string) =>
  get<DecisionRun>(`/api/autopilot/decision-runs/${runId}`)

export const fetchDecisionRunItems = (runId: string) =>
  get<DecisionItem[]>(`/api/autopilot/decision-runs/${runId}/items`)

// â”€â”€ S10: Evaluation â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

export const launchEvaluationReplay = (request: ReplayRequest) =>
  post<EvaluationRun>('/api/autopilot/evaluation/replay', request)

export const fetchEvaluationRuns = (limit = 50, offset = 0) =>
  get<EvaluationRun[]>(`/api/autopilot/evaluation/runs?limit=${limit}&offset=${offset}`)

export const fetchEvaluationRun = (evaluationId: string) =>
  get<EvaluationRun>(`/api/autopilot/evaluation/${evaluationId}`)

export const fetchEvaluationSlices = (evaluationId: string) =>
  get<EvaluationSlice[]>(`/api/autopilot/evaluation/${evaluationId}/slices`)

export const fetchEvaluationCompare = (baselineId: string, candidateId: string) =>
  get<EvaluationCompare>(`/api/autopilot/evaluation/compare?baseline=${baselineId}&candidate=${candidateId}`)


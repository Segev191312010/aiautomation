/**
 * API client — thin fetch wrapper around the FastAPI backend.
 * All methods throw on non-2xx responses.
 */
import type {
  AccountSummary,
  BacktestHistoryItem,
  BacktestRequest,
  BacktestResult,
  BotStatus,
  EnrichResult,
  MarketQuote,
  OHLCVBar,
  OpenOrder,
  PlaybackState,
  Position,
  Rule,
  RuleCreate,
  ScanFilter,
  ScanResponse,
  ScreenerPreset,
  SimAccountState,
  SimOrderRecord,
  SimPosition,
  SystemStatus,
  Trade,
  UniverseInfo,
  User,
  UserSettings,
} from '@/types'

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
  get<{ symbol: string; price: number; is_mock?: boolean }>(`/api/market/${symbol}/price`)

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

export const fetchRules    = () => get<Rule[]>('/api/rules')
export const fetchRule     = (id: string) => get<Rule>(`/api/rules/${id}`)
export const createRule    = (body: RuleCreate) => post<Rule>('/api/rules', body)
export const updateRule    = (id: string, body: Partial<Rule>) => put<Rule>(`/api/rules/${id}`, body)
export const deleteRule    = (id: string) => del<{ deleted: boolean }>(`/api/rules/${id}`)
export const toggleRule    = (id: string) => post<{ id: string; enabled: boolean }>(`/api/rules/${id}/toggle`)

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

// ── Indicators ──────────────────────────────────────────────────────────

export const fetchIndicatorData = (
  symbol: string,
  indicator: string,
  params: { length?: number; period?: string; interval?: string; fast?: number; slow?: number; signal?: number; band?: string } = {},
) => {
  const qs = new URLSearchParams({ indicator })
  if (params.length)   qs.set('length',   String(params.length))
  if (params.period)   qs.set('period',   params.period)
  if (params.interval) qs.set('interval', params.interval)
  if (params.fast)     qs.set('fast',     String(params.fast))
  if (params.slow)     qs.set('slow',     String(params.slow))
  if (params.signal)   qs.set('signal',   String(params.signal))
  if (params.band)     qs.set('band',     params.band)
  return get<Array<{ time: number; value: number }>>(`/api/market/${symbol}/indicators?${qs}`)
}

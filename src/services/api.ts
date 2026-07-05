/**
 * API client — thin fetch wrapper around the FastAPI backend.
 * All methods throw on non-2xx responses.
 */
import type {
  AccountSummary,
  BotStatus,
  MarketQuote,
  OHLCVBar,
  OpenOrder,
  PlaybackState,
  Position,
  Rule,
  RuleCreate,
  SimAccountState,
  SimOrderRecord,
  SimPosition,
  SystemStatus,
  Trade,
  AutopilotStatus,
  AutopilotConfig,
  AutopilotMode,
  AutopilotRule,
  AutopilotPerformance,
  AuditFeed,
  PromotionReadiness,
} from '@/types'

// Not exported from types/index.ts yet — define locally
interface RuleCreate_ {
  name: string
  symbol: string
  enabled?: boolean
  conditions: Rule['conditions']
  logic?: 'AND' | 'OR'
  action: Rule['action']
  cooldown_minutes?: number
}

const BASE = ''  // same origin in prod; Vite proxy handles /api in dev

// ── Auth token (loopback bootstrap) ───────────────────────────────────────────
// Protected endpoints (/api/autopilot/*, /api/rules, …) require a Bearer token.
// Requests block on a one-time bootstrap gate so the first protected calls on
// startup don't 401-cascade before App's auth bootstrap completes.
const AUTH_TOKEN_KEY = 'aiauto_auth_token'
let _resolveToken: ((t: string) => void) | null = null
let _tokenReady = false
const _tokenPromise: Promise<string> = new Promise((resolve) => {
  const existing = localStorage.getItem(AUTH_TOKEN_KEY)
  if (existing) { _tokenReady = true; resolve(existing) }
  else { _resolveToken = (t) => { _tokenReady = true; resolve(t) } }
})

export function setAuthToken(token: string | null): void {
  if (token) {
    localStorage.setItem(AUTH_TOKEN_KEY, token)
    _tokenReady = true
    _resolveToken?.(token)
  } else {
    localStorage.removeItem(AUTH_TOKEN_KEY)
  }
}
export function getAuthToken(): string | null {
  return localStorage.getItem(AUTH_TOKEN_KEY)
}

async function _waitForToken(): Promise<string | null> {
  const existing = localStorage.getItem(AUTH_TOKEN_KEY)
  if (existing) return existing
  if (_tokenReady) return null  // post-bootstrap with empty storage = logged out
  return Promise.race([
    _tokenPromise,
    new Promise<null>((resolve) => setTimeout(() => resolve(null), 5000)),
  ])
}

async function req<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = {}
  if (body) headers['Content-Type'] = 'application/json'
  const token = path === '/api/auth/token' ? null : await _waitForToken()
  if (token) headers['Authorization'] = `Bearer ${token}`

  const resp = await fetch(`${BASE}${path}`, {
    method,
    headers,
    body: body ? JSON.stringify(body) : undefined,
  })
  if (!resp.ok) {
    if (resp.status === 401) {
      localStorage.removeItem(AUTH_TOKEN_KEY)
      window.dispatchEvent(new Event('api:unauthorized'))
    }
    const text = await resp.text().catch(() => resp.statusText)
    throw new Error(`${method} ${path} → ${resp.status}: ${text}`)
  }
  const text = await resp.text()
  return (text ? JSON.parse(text) : undefined) as T
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
  get<MarketQuote[]>(`/api/watchlist${symbols ? `?symbols=${symbols}` : ''}`)

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
export const createRule    = (body: RuleCreate_) => post<Rule>('/api/rules', body)
export const updateRule    = (id: string, body: Partial<Rule>) => put<Rule>(`/api/rules/${id}`, body)
export const deleteRule    = (id: string) => del<{ deleted: boolean }>(`/api/rules/${id}`)
export const toggleRule    = (id: string) => post<{ id: string; enabled: boolean }>(`/api/rules/${id}/toggle`)

// ── Bot ───────────────────────────────────────────────────────────────────────

export const startBot = () => post<{ running: boolean }>('/api/bot/start')
export const stopBot  = () => post<{ running: boolean }>('/api/bot/stop')

// ── Autopilot / AI automation ──────────────────────────────────────────────────

export const fetchAutopilotStatus      = () => get<AutopilotStatus>('/api/autopilot/status')
export const fetchAutopilotConfig      = () => get<AutopilotConfig>('/api/autopilot/config')
export const fetchAutopilotRules       = () => get<AutopilotRule[]>('/api/autopilot/rules')
export const fetchAutopilotPerformance = () => get<AutopilotPerformance>('/api/autopilot/performance')
export const fetchAutopilotFeed        = (limit = 25) =>
  get<AuditFeed>(`/api/autopilot/feed?limit=${limit}`)
export const fetchPromotionReadiness   = (id: string) =>
  get<PromotionReadiness>(`/api/autopilot/rules/${id}/promotion-readiness`)

export const setAutopilotMode = (mode: AutopilotMode, reason?: string) =>
  post<AutopilotConfig>('/api/autopilot/mode', { mode, reason })
export const activateKillSwitch = () =>
  post<{ emergency_stop: boolean; message: string }>('/api/autopilot/kill')
export const resetKillSwitch    = () =>
  post<{ emergency_stop: boolean; message: string }>('/api/autopilot/kill/reset')
export const resetDailyLossLock = () => post<AutopilotConfig>('/api/autopilot/daily-loss/reset')

export const promoteRule = (id: string, reason: string)  => post<AutopilotRule>(`/api/autopilot/rules/${id}/promote`, { reason })
export const pauseRule   = (id: string, reason?: string) => post<AutopilotRule>(`/api/autopilot/rules/${id}/manual-pause`, { reason })
export const retireRule  = (id: string, reason?: string) => post<AutopilotRule>(`/api/autopilot/rules/${id}/manual-retire`, { reason })

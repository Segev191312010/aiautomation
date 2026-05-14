/**
 * API client — thin fetch wrapper around the FastAPI backend.
 *
 * Every endpoint falls back to the in-memory `mockBackend` when the real
 * server is unreachable, so the UI works without a running Python backend.
 * The fallback is also used when `?mock=1` is in the URL, which is useful
 * for testing.
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
  SimAccountState,
  SimOrderRecord,
  SimPosition,
  SystemStatus,
  Trade,
} from '@/types'
import * as mock from './mockBackend'

interface RuleCreate_ {
  name: string
  symbol: string
  enabled?: boolean
  conditions: Rule['conditions']
  logic?: 'AND' | 'OR'
  action: Rule['action']
  cooldown_minutes?: number
}

const FORCE_MOCK =
  typeof window !== 'undefined' &&
  new URLSearchParams(window.location.search).get('mock') === '1'

let BACKEND_ALIVE: boolean | null = null
type Listener = (alive: boolean) => void
const listeners = new Set<Listener>()

export function onBackendStateChange(l: Listener): () => void {
  listeners.add(l)
  if (BACKEND_ALIVE !== null) l(BACKEND_ALIVE)
  return () => listeners.delete(l)
}

export function isBackendAlive(): boolean {
  return BACKEND_ALIVE === true
}

function setBackendAlive(v: boolean): void {
  if (BACKEND_ALIVE === v) return
  BACKEND_ALIVE = v
  listeners.forEach((l) => l(v))
}

const BASE = ''

async function req<T>(method: string, path: string, body?: unknown, fallback?: () => T): Promise<T> {
  if (FORCE_MOCK && fallback) return fallback()

  try {
    // Short timeout so we fail fast and fall back to mocks
    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), 4_000)
    let resp: Response
    try {
      resp = await fetch(`${BASE}${path}`, {
        method,
        headers: body ? { 'Content-Type': 'application/json' } : {},
        body:    body ? JSON.stringify(body) : undefined,
        signal:  controller.signal,
      })
    } finally {
      clearTimeout(timer)
    }
    if (!resp.ok) throw new Error(`${method} ${path} → ${resp.status}`)
    setBackendAlive(true)
    return (await resp.json()) as T
  } catch (err) {
    setBackendAlive(false)
    if (fallback) return fallback()
    throw err
  }
}

const get   = <T>(p: string, f?: () => T)                  => req<T>('GET', p, undefined, f)
const post  = <T>(p: string, b?: unknown, f?: () => T)     => req<T>('POST', p, b, f)
const put   = <T>(p: string, b?: unknown, f?: () => T)     => req<T>('PUT', p, b, f)
const del   = <T>(p: string, f?: () => T)                  => req<T>('DELETE', p, undefined, f)

// ── Status ────────────────────────────────────────────────────────────────────

export const fetchStatus    = () => get<SystemStatus>('/api/status',    () => mock.getStatus())
export const fetchBotStatus = () => get<BotStatus>('/api/bot/status', () => mock.getBotStatus())

// ── IBKR ──────────────────────────────────────────────────────────────────────

export const connectIBKR    = () => post<{ connected: boolean }>('/api/ibkr/connect',    undefined, () => mock.connectIBKR())
export const disconnectIBKR = () => post<{ connected: boolean }>('/api/ibkr/disconnect', undefined, () => mock.disconnectIBKR())

// ── Account ───────────────────────────────────────────────────────────────────

export const fetchAccountSummary = () => get<AccountSummary | SimAccountState>('/api/account/summary', () => mock.getAccountSummary())
export const fetchPositions      = () => get<(Position | SimPosition)[]>('/api/positions',           () => mock.getPositions())
export const fetchOrders         = () => get<OpenOrder[]>('/api/orders',                              () => mock.getOrders())
export const fetchTrades         = (limit = 200) => get<Trade[]>(`/api/trades?limit=${limit}`,        () => mock.getTrades(limit))
export const cancelOrder         = (id: number)  => del<{ cancelled: boolean }>(`/api/orders/${id}`, () => mock.cancelOrder(id))

export const placeManualOrder = (body: {
  symbol: string
  action: 'BUY' | 'SELL'
  quantity: number
  order_type?: 'MKT' | 'LMT'
  limit_price?: number
  asset_type?: 'STK' | 'OPT' | 'FUT'
}) => post<{ success?: boolean; message?: string }>('/api/orders/manual', body, () => mock.placeManualOrder(body))

// ── Market data ───────────────────────────────────────────────────────────────

export const fetchWatchlist = (symbols?: string) =>
  get<MarketQuote[]>(`/api/watchlist${symbols ? `?symbols=${symbols}` : ''}`, () => mock.fetchWatchlist(symbols))

export const fetchYahooBars = (symbol: string, period = '5d', interval = '5m') =>
  get<OHLCVBar[]>(`/api/yahoo/${symbol}/bars?period=${period}&interval=${interval}`,
                  () => mock.fetchYahooBars(symbol, period, interval))

export const fetchIBKRBars = (symbol: string, barSize = '1D', duration = '60 D') =>
  get<OHLCVBar[]>(`/api/market/${symbol}/bars?bar_size=${barSize}&duration=${encodeURIComponent(duration)}`,
                  () => mock.fetchIBKRBars(symbol))

export const fetchPrice = (symbol: string) =>
  get<{ symbol: string; price: number; is_mock?: boolean }>(`/api/market/${symbol}/price`,
                  () => mock.fetchPrice(symbol))

export const subscribeRtBars   = (symbol: string) => post<{ subscribed: boolean }>(`/api/market/${symbol}/subscribe`,   undefined, () => ({ subscribed: true }))
export const unsubscribeRtBars = (symbol: string) => post<{ subscribed: boolean }>(`/api/market/${symbol}/unsubscribe`, undefined, () => ({ subscribed: false }))

// ── Simulation ────────────────────────────────────────────────────────────────

export const fetchSimAccount   = () => get<SimAccountState>('/api/simulation/account',                  () => mock.getSimAccount())
export const fetchSimPositions = () => get<SimPosition[]>('/api/simulation/positions',                  () => mock.getSimPositions())
export const fetchSimOrders    = (limit = 100) => get<SimOrderRecord[]>(`/api/simulation/orders?limit=${limit}`, () => mock.getSimOrders(limit))
export const resetSimAccount   = () => post<{ reset: boolean }>('/api/simulation/reset', undefined,    () => mock.resetSimAccount())

export const placeSimOrder = (body: { symbol: string; action: 'BUY' | 'SELL'; qty: number; price: number }) =>
  post<{ success: boolean; message: string }>('/api/simulation/order', body, () => mock.placeSimOrder(body))

// ── Playback ──────────────────────────────────────────────────────────────────

export const fetchPlaybackState  = () => get<PlaybackState>('/api/simulation/playback',          () => mock.getPlaybackState())
export const loadReplay = (symbol: string, period = '1y', interval = '1d') =>
  post<PlaybackState>('/api/simulation/playback/load', { symbol, period, interval }, () => mock.loadReplay(symbol))
export const playReplay  = () => post<PlaybackState>('/api/simulation/playback/play',  undefined, () => mock.playReplay())
export const pauseReplay = () => post<PlaybackState>('/api/simulation/playback/pause', undefined, () => mock.pauseReplay())
export const stopReplay  = () => post<PlaybackState>('/api/simulation/playback/stop',  undefined, () => mock.stopReplay())
export const setReplaySpeed = (speed: number) =>
  post<{ speed: number }>('/api/simulation/playback/speed', { speed }, () => ({ speed }))

// ── Rules ─────────────────────────────────────────────────────────────────────

export const fetchRules  = () => get<Rule[]>('/api/rules',                () => mock.getRules())
export const fetchRule   = (id: string) => get<Rule>(`/api/rules/${id}`,  () => {
  const r = mock.getRule(id); if (!r) throw new Error('Not found'); return r
})
export const createRule  = (body: RuleCreate_) => post<Rule>('/api/rules', body, () => mock.createRule({
  name:             body.name,
  symbol:           body.symbol,
  enabled:          body.enabled ?? true,
  conditions:       body.conditions,
  logic:            body.logic ?? 'AND',
  action:           body.action,
  cooldown_minutes: body.cooldown_minutes ?? 30,
}))
export const updateRule  = (id: string, body: Partial<Rule>) => put<Rule>(`/api/rules/${id}`, body, () => mock.updateRule(id, body))
export const deleteRule  = (id: string) => del<{ deleted: boolean }>(`/api/rules/${id}`,           () => mock.deleteRule(id))
export const toggleRule  = (id: string) => post<{ id: string; enabled: boolean }>(`/api/rules/${id}/toggle`, undefined, () => mock.toggleRule(id))

// ── Bot ───────────────────────────────────────────────────────────────────────

export const startBot = () => post<{ running: boolean }>('/api/bot/start', undefined, () => mock.startBot())
export const stopBot  = () => post<{ running: boolean }>('/api/bot/stop',  undefined, () => mock.stopBot())

// ── Settings & alerts (mock-only) ─────────────────────────────────────────────

export const fetchSettings  = () => Promise.resolve(mock.getSettings())
export const updateSettings = (patch: Parameters<typeof mock.updateSettings>[0]) =>
  Promise.resolve(mock.updateSettings(patch))

export const fetchAlerts  = () => Promise.resolve(mock.getAlerts())
export const createAlert  = (a: Parameters<typeof mock.createAlert>[0])  => Promise.resolve(mock.createAlert(a))
export const updateAlert  = (id: string, b: Parameters<typeof mock.updateAlert>[1]) => Promise.resolve(mock.updateAlert(id, b))
export const deleteAlert  = (id: string) => Promise.resolve(mock.deleteAlert(id))

/**
 * Mock backend — in-memory simulation of the FastAPI server.
 *
 * Activates automatically whenever a real backend call fails. The data is
 * persisted to localStorage so user edits to rules/alerts/settings survive
 * page reloads.
 *
 * IMPORTANT: This NEVER places real trades. All "order" calls produce
 * simulated fills against the mock price feed.
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
import {
  getMockAccount,
  getMockBars,
  getMockPrice,
  getMockQuote,
  getMockQuotes,
  getMockSimAccount,
} from './mockService'

// ── Storage helpers ───────────────────────────────────────────────────────────

const KEY = 'tradebot.mockBackend.v1'

interface PersistedState {
  rules:           Rule[]
  alerts:          Alert[]
  settings:        AppSettings
  trades:          Trade[]
  simPositions:    SimPosition[]
  simOrders:       SimOrderRecord[]
  simAccount:      SimAccountState
  botRunning:      boolean
  ibkrConnected:   boolean
}

export interface Alert {
  id:         string
  symbol:     string
  condition:  'price_above' | 'price_below' | 'pct_change_up' | 'pct_change_down' | 'volume_spike'
  value:      number
  enabled:    boolean
  triggered:  boolean
  created:    string
  last_fired?: string
  message?:   string
}

export interface AppSettings {
  ibkr_host:           string
  ibkr_port:           number
  ibkr_client_id:      number
  is_paper:            boolean
  bot_interval_seconds: number
  data_provider:       'mock' | 'yahoo' | 'ibkr'
  chart_engine:        'lightweight' | 'tradingview'
  tradingview_theme:   'dark' | 'light'
  enable_alerts:       boolean
  enable_screener:     boolean
  screener_universe:   'sp500' | 'nasdaq100' | 'russell1k' | 'custom'
}

const DEFAULT_SETTINGS: AppSettings = {
  ibkr_host:           '127.0.0.1',
  ibkr_port:           7497,
  ibkr_client_id:      1,
  is_paper:            true,
  bot_interval_seconds: 60,
  data_provider:       'mock',
  chart_engine:        'lightweight',
  tradingview_theme:   'dark',
  enable_alerts:       true,
  enable_screener:     true,
  screener_universe:   'sp500',
}

function load(): PersistedState {
  try {
    const raw = localStorage.getItem(KEY)
    if (raw) {
      const parsed = JSON.parse(raw) as Partial<PersistedState>
      return {
        rules:         parsed.rules         ?? [],
        alerts:        parsed.alerts        ?? [],
        settings:      { ...DEFAULT_SETTINGS, ...(parsed.settings ?? {}) },
        trades:        parsed.trades        ?? [],
        simPositions:  parsed.simPositions  ?? [],
        simOrders:     parsed.simOrders     ?? [],
        simAccount:    parsed.simAccount    ?? getMockSimAccount(),
        botRunning:    parsed.botRunning    ?? false,
        ibkrConnected: parsed.ibkrConnected ?? false,
      }
    }
  } catch { /* ignore */ }
  return {
    rules:         [],
    alerts:        [],
    settings:      { ...DEFAULT_SETTINGS },
    trades:        [],
    simPositions:  [],
    simOrders:     [],
    simAccount:    getMockSimAccount(),
    botRunning:    false,
    ibkrConnected: false,
  }
}

function save(state: PersistedState): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(state))
  } catch { /* localStorage full or unavailable */ }
}

const state: PersistedState = load()

function uuid(): string {
  return typeof crypto !== 'undefined' && crypto.randomUUID
    ? crypto.randomUUID()
    : Math.random().toString(36).slice(2) + Date.now().toString(36)
}

// ── Status ────────────────────────────────────────────────────────────────────

export function getStatus(): SystemStatus {
  return {
    ibkr_connected:      state.ibkrConnected,
    ibkr_host:           state.settings.ibkr_host,
    ibkr_port:           state.settings.ibkr_port,
    is_paper:            state.settings.is_paper,
    sim_mode:            false,
    mock_mode:           true,
    bot_running:         state.botRunning,
    bot_interval_seconds: state.settings.bot_interval_seconds,
    last_run:            state.botRunning ? new Date().toISOString() : undefined,
    next_run:            state.botRunning
      ? new Date(Date.now() + state.settings.bot_interval_seconds * 1000).toISOString()
      : undefined,
  }
}

export function getBotStatus(): BotStatus {
  return {
    running:  state.botRunning,
    last_run: state.botRunning ? new Date().toISOString() : undefined,
    next_run: state.botRunning
      ? new Date(Date.now() + state.settings.bot_interval_seconds * 1000).toISOString()
      : undefined,
  }
}

// ── IBKR (simulated connect/disconnect) ───────────────────────────────────────

export function connectIBKR(): { connected: boolean } {
  // In mock mode we report "connected" optimistically to allow the rest of the
  // UI to function. Real IBKR connectivity requires the backend.
  state.ibkrConnected = true
  save(state)
  return { connected: true }
}

export function disconnectIBKR(): { connected: boolean } {
  state.ibkrConnected = false
  save(state)
  return { connected: false }
}

// ── Account / positions / trades ──────────────────────────────────────────────

export function getAccountSummary(): AccountSummary {
  return getMockAccount()
}

export function getPositions(): (Position | SimPosition)[] {
  return state.simPositions
}

export function getOrders(): OpenOrder[] {
  return []
}

export function getTrades(limit: number): Trade[] {
  return state.trades.slice(0, limit)
}

export function cancelOrder(_id: number): { cancelled: boolean } {
  return { cancelled: true }
}

export function placeManualOrder(body: {
  symbol: string
  action: 'BUY' | 'SELL'
  quantity: number
  order_type?: 'MKT' | 'LMT'
  limit_price?: number
}): { success: boolean; message: string } {
  const symbol = body.symbol.toUpperCase()
  const price  = body.limit_price ?? getMockPrice(symbol)
  const trade: Trade = {
    id:           uuid(),
    rule_id:      'manual',
    rule_name:    'Manual',
    symbol,
    action:       body.action,
    asset_type:   'STK',
    quantity:     body.quantity,
    order_type:   body.order_type ?? 'MKT',
    limit_price:  body.limit_price,
    fill_price:   price,
    status:       'FILLED',
    timestamp:    new Date().toISOString(),
  }
  state.trades = [trade, ...state.trades].slice(0, 500)
  applyFillToSim(symbol, body.action, body.quantity, price)
  save(state)
  return { success: true, message: `Simulated ${body.action} ${body.quantity} ${symbol} @ ${price.toFixed(2)}` }
}

function applyFillToSim(symbol: string, action: 'BUY' | 'SELL', qty: number, price: number): void {
  const existing = state.simPositions.find((p) => p.symbol === symbol)
  if (action === 'BUY') {
    if (existing) {
      const totalQty   = existing.qty + qty
      const totalCost  = existing.qty * existing.avg_cost + qty * price
      existing.qty     = totalQty
      existing.avg_cost = totalCost / totalQty
      existing.current_price = price
      existing.market_value  = totalQty * price
      existing.unrealized_pnl = (price - existing.avg_cost) * totalQty
      existing.pnl_pct = existing.avg_cost ? (price - existing.avg_cost) / existing.avg_cost * 100 : 0
    } else {
      state.simPositions.push({
        symbol, qty, avg_cost: price, current_price: price,
        market_value: qty * price, unrealized_pnl: 0, pnl_pct: 0,
      })
    }
    state.simAccount.cash -= qty * price
  } else {
    if (existing) {
      const sellQty = Math.min(qty, existing.qty)
      const realized = (price - existing.avg_cost) * sellQty
      existing.qty -= sellQty
      existing.market_value = existing.qty * price
      existing.unrealized_pnl = (price - existing.avg_cost) * existing.qty
      state.simAccount.cash += sellQty * price
      state.simAccount.realized_pnl += realized
      if (existing.qty <= 0) {
        state.simPositions = state.simPositions.filter((p) => p.symbol !== symbol)
      }
    }
  }
  const posValue = state.simPositions.reduce((s, p) => s + p.market_value, 0)
  state.simAccount.positions_value = posValue
  state.simAccount.unrealized_pnl  = state.simPositions.reduce((s, p) => s + p.unrealized_pnl, 0)
  state.simAccount.net_liquidation = state.simAccount.cash + posValue
  state.simAccount.total_return_pct =
    ((state.simAccount.net_liquidation - state.simAccount.initial_cash) / state.simAccount.initial_cash) * 100

  // Mirror as sim order record
  state.simOrders.unshift({
    id:         uuid(),
    symbol,
    action,
    qty,
    price,
    commission: 0,
    pnl:        action === 'SELL' ? (price - (existing?.avg_cost ?? price)) * qty : undefined,
    timestamp:  new Date().toISOString(),
  })
  state.simOrders = state.simOrders.slice(0, 200)
}

// ── Market data ───────────────────────────────────────────────────────────────

export function fetchWatchlist(symbols?: string): MarketQuote[] {
  if (!symbols) return []
  const list = symbols.split(',').map((s) => s.trim()).filter(Boolean)
  return getMockQuotes(list)
}

export function fetchYahooBars(symbol: string, _period?: string, interval = '1d'): OHLCVBar[] {
  const sec =
    interval === '5m'  ? 300   :
    interval === '15m' ? 900   :
    interval === '30m' ? 1800  :
    interval === '1h'  ? 3600  :
    interval === '1wk' ? 604_800 :
    interval === '1mo' ? 2_592_000 : 86_400
  return getMockBars(symbol, 240, sec)
}

export function fetchIBKRBars(symbol: string): OHLCVBar[] {
  return getMockBars(symbol, 120, 86_400)
}

export function fetchPrice(symbol: string): { symbol: string; price: number; is_mock: boolean } {
  return { symbol, price: getMockPrice(symbol), is_mock: true }
}

// ── Simulation ────────────────────────────────────────────────────────────────

export function getSimAccount(): SimAccountState {
  return state.simAccount
}

export function getSimPositions(): SimPosition[] {
  // refresh prices
  state.simPositions.forEach((p) => {
    const px = getMockPrice(p.symbol)
    p.current_price = px
    p.market_value  = p.qty * px
    p.unrealized_pnl = (px - p.avg_cost) * p.qty
    p.pnl_pct = p.avg_cost ? (px - p.avg_cost) / p.avg_cost * 100 : 0
  })
  const posValue = state.simPositions.reduce((s, p) => s + p.market_value, 0)
  state.simAccount.positions_value = posValue
  state.simAccount.unrealized_pnl  = state.simPositions.reduce((s, p) => s + p.unrealized_pnl, 0)
  state.simAccount.net_liquidation = state.simAccount.cash + posValue
  state.simAccount.total_return_pct =
    ((state.simAccount.net_liquidation - state.simAccount.initial_cash) / state.simAccount.initial_cash) * 100
  return [...state.simPositions]
}

export function getSimOrders(limit: number): SimOrderRecord[] {
  return state.simOrders.slice(0, limit)
}

export function resetSimAccount(): { reset: boolean } {
  state.simAccount   = getMockSimAccount()
  state.simPositions = []
  state.simOrders    = []
  save(state)
  return { reset: true }
}

export function placeSimOrder(body: { symbol: string; action: 'BUY' | 'SELL'; qty: number; price: number }): { success: boolean; message: string } {
  applyFillToSim(body.symbol, body.action, body.qty, body.price || getMockPrice(body.symbol))
  save(state)
  return { success: true, message: `Sim ${body.action} ${body.qty} ${body.symbol}` }
}

// ── Playback (very simple — not used heavily without backend) ─────────────────

const playback: PlaybackState = {
  active: false, symbol: '', speed: 1, current_index: 0, total_bars: 0, progress: 0,
}

export function getPlaybackState(): PlaybackState { return playback }
export function loadReplay(symbol: string): PlaybackState {
  playback.symbol = symbol
  playback.total_bars = 240
  playback.current_index = 0
  playback.progress = 0
  return playback
}
export function playReplay():  PlaybackState { playback.active = true;  return playback }
export function pauseReplay(): PlaybackState { playback.active = false; return playback }
export function stopReplay():  PlaybackState {
  playback.active = false
  playback.current_index = 0
  playback.progress = 0
  return playback
}

// ── Rules ─────────────────────────────────────────────────────────────────────

export function getRules(): Rule[] { return [...state.rules] }
export function getRule(id: string): Rule | undefined {
  return state.rules.find((r) => r.id === id)
}
export function createRule(body: Omit<Rule, 'id'>): Rule {
  const rule: Rule = {
    ...body,
    id:               uuid(),
    enabled:          body.enabled ?? true,
    cooldown_minutes: body.cooldown_minutes ?? 30,
    logic:            body.logic ?? 'AND',
  }
  state.rules.push(rule)
  save(state)
  return rule
}
export function updateRule(id: string, body: Partial<Rule>): Rule {
  const r = state.rules.find((x) => x.id === id)
  if (!r) throw new Error(`Rule ${id} not found`)
  Object.assign(r, body)
  save(state)
  return r
}
export function deleteRule(id: string): { deleted: boolean } {
  state.rules = state.rules.filter((r) => r.id !== id)
  save(state)
  return { deleted: true }
}
export function toggleRule(id: string): { id: string; enabled: boolean } {
  const r = state.rules.find((x) => x.id === id)
  if (!r) throw new Error(`Rule ${id} not found`)
  r.enabled = !r.enabled
  save(state)
  return { id: r.id, enabled: r.enabled }
}

// ── Bot ───────────────────────────────────────────────────────────────────────

export function startBot(): { running: boolean } {
  state.botRunning = true
  save(state)
  return { running: true }
}
export function stopBot(): { running: boolean } {
  state.botRunning = false
  save(state)
  return { running: false }
}

// ── Alerts ────────────────────────────────────────────────────────────────────

export function getAlerts(): Alert[] { return [...state.alerts] }
export function createAlert(a: Omit<Alert, 'id' | 'triggered' | 'created'>): Alert {
  const alert: Alert = { ...a, id: uuid(), triggered: false, created: new Date().toISOString() }
  state.alerts.push(alert)
  save(state)
  return alert
}
export function updateAlert(id: string, body: Partial<Alert>): Alert {
  const a = state.alerts.find((x) => x.id === id)
  if (!a) throw new Error(`Alert ${id} not found`)
  Object.assign(a, body)
  save(state)
  return a
}
export function deleteAlert(id: string): { deleted: boolean } {
  state.alerts = state.alerts.filter((a) => a.id !== id)
  save(state)
  return { deleted: true }
}

/** Evaluate all alerts against latest quotes; fires `onTrigger` for newly-triggered ones. */
export function evaluateAlerts(quotes: Record<string, MarketQuote>, onTrigger: (a: Alert) => void): void {
  for (const a of state.alerts) {
    if (!a.enabled) continue
    const q = quotes[a.symbol]
    if (!q) continue
    let fired = false
    switch (a.condition) {
      case 'price_above':     fired = q.price >= a.value; break
      case 'price_below':     fired = q.price <= a.value; break
      case 'pct_change_up':   fired = q.change_pct >= a.value; break
      case 'pct_change_down': fired = q.change_pct <= -Math.abs(a.value); break
      case 'volume_spike':    fired = !!(q.volume && q.avg_volume && q.volume / q.avg_volume >= a.value); break
    }
    if (fired && !a.triggered) {
      a.triggered  = true
      a.last_fired = new Date().toISOString()
      onTrigger(a)
    } else if (!fired && a.triggered) {
      // re-arm when the condition releases
      a.triggered = false
    }
  }
  save(state)
}

// ── Settings ──────────────────────────────────────────────────────────────────

export function getSettings(): AppSettings { return { ...state.settings } }
export function updateSettings(patch: Partial<AppSettings>): AppSettings {
  state.settings = { ...state.settings, ...patch }
  save(state)
  return { ...state.settings }
}

// ── Screener — built-in US universes ──────────────────────────────────────────

export const SP500_SAMPLE = [
  'AAPL','MSFT','GOOGL','GOOG','AMZN','NVDA','META','TSLA','BRK.B','UNH',
  'XOM','JNJ','JPM','V','PG','MA','LLY','HD','CVX','MRK',
  'ABBV','AVGO','PEP','KO','COST','WMT','BAC','TMO','MCD','CSCO',
  'CRM','ACN','ABT','LIN','DHR','PFE','ADBE','NFLX','NKE','TXN',
  'ORCL','DIS','VZ','WFC','INTC','PM','RTX','AMD','UNP','UPS',
  'HON','QCOM','LOW','IBM','CAT','SPGI','MS','GS','BLK','PLD',
  'AXP','BA','BKNG','SBUX','MDT','GE','AMGN','LMT','DE','SCHW',
  'AMT','GILD','TJX','SYK','MMC','C','ELV','VRTX','ADI','ISRG',
  'CB','ZTS','MO','REGN','ETN','BSX','MDLZ','PYPL','DUK','SO',
  'ICE','PNC','CL','CME','EOG','BDX','EQIX','NSC','APD','SHW',
]

export const NASDAQ100_SAMPLE = [
  'AAPL','MSFT','GOOGL','AMZN','NVDA','META','TSLA','AVGO','PEP','COST',
  'CSCO','NFLX','AMD','ADBE','TMUS','CMCSA','QCOM','INTC','AMGN','HON',
  'INTU','TXN','AMAT','SBUX','MDLZ','BKNG','ISRG','GILD','ADI','VRTX',
  'REGN','LRCX','PYPL','MU','PANW','ADP','SNPS','KLAC','CDNS','MELI',
]

export const RUSSELL1K_SAMPLE = [
  ...SP500_SAMPLE,
  'F','GM','UAL','DAL','AAL','LUV','CCL','RCL','NCLH','MGM',
  'WYNN','LVS','HLT','MAR','CMG','YUM','DRI','LULU','GPS','M',
  'ANF','URBN','BBY','DLTR','DG','KR','SYY','TGT','WBA','CVS',
  'HUM','CI','CNC','MOH','UHS','HCA','THC','MDT','BSX','SYK',
]

export function getUniverse(universe: AppSettings['screener_universe']): string[] {
  switch (universe) {
    case 'nasdaq100':  return NASDAQ100_SAMPLE
    case 'russell1k':  return [...new Set(RUSSELL1K_SAMPLE)]
    case 'sp500':
    default:           return SP500_SAMPLE
  }
}

// Reset for testing
export function _reset(): void {
  try { localStorage.removeItem(KEY) } catch { /* ignore */ }
}

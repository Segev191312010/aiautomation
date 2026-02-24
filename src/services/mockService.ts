/**
 * Mock Service — client-side GBM price simulation.
 * Mirrors the backend mock_data.py logic so the UI works without a server.
 *
 * Activated when the API is unreachable. The React hooks fall back here
 * automatically.
 */
import type { MarketQuote, OHLCVBar, AccountSummary, SimAccountState } from '@/types'

// ── Base prices ───────────────────────────────────────────────────────────────

const BASE_PRICES: Record<string, number> = {
  'AAPL':    220,
  'TSLA':    340,
  'NVDA':    890,
  'MSFT':    415,
  'AMZN':    210,
  'GOOGL':   175,
  'META':    580,
  'SPY':     575,
  'QQQ':     495,
  'IWM':     220,
  'BTC-USD': 98_000,
  'ETH-USD':  3_300,
  'SOL-USD':    200,
  'GLD':     185,
  'TLT':      88,
}

const SIGMA: Record<string, number> = {
  'AAPL':    0.015,
  'TSLA':    0.035,
  'NVDA':    0.030,
  'MSFT':    0.015,
  'AMZN':    0.018,
  'GOOGL':   0.016,
  'META':    0.022,
  'SPY':     0.010,
  'QQQ':     0.012,
  'IWM':     0.013,
  'BTC-USD': 0.045,
  'ETH-USD': 0.050,
  'SOL-USD': 0.060,
  'GLD':     0.010,
  'TLT':     0.008,
}

const MARKET_CAP: Record<string, number | undefined> = {
  'AAPL':    3.30e12,
  'TSLA':    1.00e12,
  'NVDA':    2.20e12,
  'MSFT':    3.05e12,
  'AMZN':    2.25e12,
  'GOOGL':   2.05e12,
  'META':    1.45e12,
  'BTC-USD': 1.95e12,
  'ETH-USD': 4.00e11,
  'SOL-USD': 9.00e10,
}

const AVG_VOLUME: Record<string, number> = {
  'AAPL':    6.0e7,
  'TSLA':    1.0e8,
  'NVDA':    5.0e7,
  'MSFT':    2.5e7,
  'AMZN':    4.0e7,
  'GOOGL':   3.0e7,
  'META':    2.0e7,
  'SPY':     8.0e7,
  'QQQ':     5.0e7,
  'BTC-USD': 3.0e10,
  'ETH-USD': 1.5e10,
  'SOL-USD': 5.0e9,
}

// ── Internal state ────────────────────────────────────────────────────────────

const prices: Record<string, number> = {}
const prevClose: Record<string, number> = {}

function gaussRand(): number {
  // Box-Muller transform
  const u = 1 - Math.random()
  const v = Math.random()
  return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v)
}

function gbmStep(price: number, mu: number, sigma: number, dt: number): number {
  return price * Math.exp((mu - 0.5 * sigma ** 2) * dt + sigma * Math.sqrt(dt) * gaussRand())
}

function sigma(symbol: string): number {
  return SIGMA[symbol] ?? 0.02
}

function base(symbol: string): number {
  return BASE_PRICES[symbol] ?? 100
}

function currentPrice(symbol: string): number {
  if (!prices[symbol]) prices[symbol] = base(symbol) * (0.92 + Math.random() * 0.16)
  return prices[symbol]
}

function advance(symbol: string): number {
  const dt  = 1 / 78  // 5-minute step within 6.5-hour session
  const mu  = 0.00005
  prices[symbol] = gbmStep(currentPrice(symbol), mu, sigma(symbol), dt)
  return prices[symbol]
}

function ensurePrevClose(symbol: string): number {
  if (!prevClose[symbol]) {
    prevClose[symbol] = currentPrice(symbol) * (0.98 + Math.random() * 0.04)
  }
  return prevClose[symbol]
}

// ── Public API ────────────────────────────────────────────────────────────────

export function getMockPrice(symbol: string): number {
  return +advance(symbol).toFixed(4)
}

export function getMockQuote(symbol: string): MarketQuote {
  const price = advance(symbol)
  const prev  = ensurePrevClose(symbol)
  const change     = price - prev
  const change_pct = prev ? (change / prev) * 100 : 0
  const b      = base(symbol)
  const spread = price * 0.0001

  return {
    symbol,
    price:      +price.toFixed(4),
    change:     +change.toFixed(4),
    change_pct: +change_pct.toFixed(2),
    year_high:  +(b * (1.1 + Math.random() * 0.35)).toFixed(2),
    year_low:   +(b * (0.65 + Math.random() * 0.2)).toFixed(2),
    market_cap: MARKET_CAP[symbol],
    avg_volume: AVG_VOLUME[symbol],
    volume:     AVG_VOLUME[symbol]
      ? Math.round(AVG_VOLUME[symbol] * (0.6 + Math.random() * 0.8))
      : undefined,
    bid: +(price - spread).toFixed(4),
    ask: +(price + spread).toFixed(4),
    last_update: new Date().toISOString(),
    is_mock: true,
  }
}

export function getMockQuotes(symbols: string[]): MarketQuote[] {
  return symbols.map(getMockQuote)
}

export function getMockBars(
  symbol: string,
  numBars = 120,
  barSeconds = 86_400,
): OHLCVBar[] {
  const sig    = sigma(symbol)
  const b      = base(symbol)
  const avgVol = AVG_VOLUME[symbol] ?? 1_000_000
  const now    = Math.floor(Date.now() / 1000)
  const end    = Math.floor(now / barSeconds) * barSeconds
  const dt     = barSeconds / (252 * 86_400)

  const bars: OHLCVBar[] = []
  let price = b * (0.80 + Math.random() * 0.40)

  for (let i = numBars; i > 0; i--) {
    const ts = end - i * barSeconds

    // Skip weekends for daily bars
    if (barSeconds >= 86_400) {
      const dow = new Date(ts * 1000).getDay()
      if (dow === 0 || dow === 6) continue
    }

    const open = price
    const subs = [open]
    let p = open
    for (let j = 0; j < 4; j++) {
      p = gbmStep(p, 0.0001, sig, dt / 4)
      subs.push(p)
    }
    const high   = Math.max(...subs)
    const low    = Math.min(...subs)
    const close  = subs[subs.length - 1]
    const volume = Math.max(
      Math.round(avgVol * (0.75 + Math.random() * 0.5) + gaussRand() * avgVol * 0.1),
      1_000,
    )

    bars.push({
      time:   ts,
      open:   +open.toFixed(4),
      high:   +high.toFixed(4),
      low:    +low.toFixed(4),
      close:  +close.toFixed(4),
      volume,
    })
    price = close
  }

  return bars
}

export function getMockAccount(): AccountSummary {
  return {
    balance:        125_847.32,
    cash:            85_234.18,
    margin_used:          0,
    unrealized_pnl:  4_213.45,
    realized_pnl:   36_399.69,
    currency:       'USD',
    is_mock:         true,
  }
}

export function getMockSimAccount(): SimAccountState {
  return {
    cash:             85_000,
    initial_cash:    100_000,
    net_liquidation: 105_847,
    positions_value:  20_847,
    unrealized_pnl:   2_150,
    realized_pnl:     3_697,
    total_return_pct:  5.85,
    is_sim:           true,
  }
}

/**
 * AutoTrader — client-side automated trading engine.
 *
 * Activated when the dashboard is running without a live backend
 * (mockMode=true) and the user flips the "Automated Trading" toggle.
 * In that state we cannot reach the FastAPI rules engine, so this
 * module IS the rules engine: it consumes the same live mock quotes
 * the rest of the UI uses, evaluates entry/exit rules, opens/closes
 * virtual positions in the existing account & trade stores, and
 * emits Trade records that the trade-log UI already renders.
 *
 * Strategy (deliberately simple and deterministic so the UX is
 * easy to reason about, not because it is a good strategy):
 *
 *   Entry (long):
 *     - SMA(5) > SMA(20)            (short-term uptrend)
 *     - RSI(14) < OVERBOUGHT (70)   (not blown off)
 *     - No existing position in this symbol
 *     - Position size = entry_fraction * cash, floored to whole shares
 *
 *   Exit (any one of):
 *     - Stop loss:     price ≤ avg_cost * (1 - stop_loss_pct)
 *     - Take profit:   price ≥ avg_cost * (1 + take_profit_pct)
 *     - Trailing stop: price ≤ peak_since_entry * (1 - trailing_pct)
 *     - Max hold:      ticks_since_entry ≥ max_hold_ticks
 *
 * Live mode (ibkrConnected=true && !mockMode) is intentionally NOT
 * driven by this loop — real broker execution still flows through
 * the backend `/api/bot/start` path the existing toggle already calls.
 */
import { useAccountStore, useBotStore, useMarketStore } from '@/store'
import type { AccountSummary, Position, Trade } from '@/types'
import { getMockAccount, getMockBars } from '@/services/mockService'

// ── Engine configuration ─────────────────────────────────────────────────────

export interface AutoTraderConfig {
  tickIntervalMs:    number   // how often to evaluate rules
  shortWindow:       number   // SMA short period
  longWindow:        number   // SMA long period
  rsiPeriod:         number
  rsiOverbought:     number
  rsiOversold:       number
  entryFraction:     number   // fraction of cash to deploy per entry
  stopLossPct:       number
  takeProfitPct:     number
  trailingStopPct:   number
  maxHoldTicks:      number
  minBarsForEntry:   number   // need this many price points before trading
  maxOpenPositions:  number
}

export const DEFAULT_CONFIG: AutoTraderConfig = {
  tickIntervalMs:   3_000,
  shortWindow:      3,
  longWindow:       8,
  rsiPeriod:        7,
  rsiOverbought:    70,
  rsiOversold:      30,
  entryFraction:    0.05,
  stopLossPct:      0.02,
  takeProfitPct:    0.05,
  trailingStopPct:  0.015,
  maxHoldTicks:     120,
  minBarsForEntry:  10,
  maxOpenPositions: 5,
}

// ── Per-symbol state held by the engine ──────────────────────────────────────

interface PriceHistory {
  prices: number[]      // capped, most recent last
  lastTs: number
}

interface PositionMeta {
  symbol:   string
  entryTs:  number
  entryPx:  number
  peakPx:   number      // running max for trailing stop
  qty:      number
  ticks:    number      // bars since entry
}

// ── Indicator helpers (lightweight, on number[]) ─────────────────────────────

function sma(prices: number[], period: number): number | null {
  if (prices.length < period) return null
  let sum = 0
  for (let i = prices.length - period; i < prices.length; i++) sum += prices[i]
  return sum / period
}

function rsi(prices: number[], period: number): number | null {
  if (prices.length < period + 1) return null
  let gain = 0, loss = 0
  for (let i = prices.length - period; i < prices.length; i++) {
    const d = prices[i] - prices[i - 1]
    if (d >= 0) gain += d; else loss -= d
  }
  const avgG = gain / period
  const avgL = loss / period
  if (avgL === 0) return 100
  const rs = avgG / avgL
  return 100 - 100 / (1 + rs)
}

// ── The engine ───────────────────────────────────────────────────────────────

class AutoTrader {
  private timer:   ReturnType<typeof setInterval> | null = null
  private history: Map<string, PriceHistory> = new Map()
  private positions: Map<string, PositionMeta> = new Map()
  private config:  AutoTraderConfig = DEFAULT_CONFIG
  private running = false

  isRunning(): boolean {
    return this.running
  }

  setConfig(patch: Partial<AutoTraderConfig>): void {
    this.config = { ...this.config, ...patch }
  }

  getConfig(): AutoTraderConfig {
    return this.config
  }

  start(config?: Partial<AutoTraderConfig>): void {
    if (this.running) return
    if (config) this.setConfig(config)
    this.running = true

    // Seed account if there isn't one yet (mock mode).
    const account = useAccountStore.getState().account
    if (!account) {
      useAccountStore.getState().setAccount(getMockAccount())
    }

    // Seed price history from mock bars so we can evaluate signals
    // immediately rather than waiting for tick-rate to accumulate.
    const market = useMarketStore.getState()
    const wl = market.watchlists.find((w) => w.id === market.activeWatchlist)
    const symbols = wl?.symbols ?? []
    for (const sym of symbols) {
      if (this.history.has(sym)) continue
      const bars = getMockBars(sym, 60)
      this.history.set(sym, {
        prices: bars.map((b) => b.close),
        lastTs: Date.now(),
      })
    }

    // Reconcile our internal map with whatever positions the store has,
    // so a restart doesn't immediately re-enter symbols we already hold.
    this.positions.clear()
    for (const p of useAccountStore.getState().positions) {
      if ('avg_cost' in p && 'qty' in p) {
        this.positions.set(p.symbol, {
          symbol:  p.symbol,
          entryTs: Date.now(),
          entryPx: p.avg_cost,
          peakPx:  p.avg_cost,
          qty:     p.qty,
          ticks:   0,
        })
      }
    }

    this.timer = setInterval(() => this.tick(), this.config.tickIntervalMs)
  }

  stop(): void {
    if (this.timer) clearInterval(this.timer)
    this.timer = null
    this.running = false
  }

  /** Reset engine state (useful for tests / sim resets). */
  reset(): void {
    this.stop()
    this.history.clear()
    this.positions.clear()
  }

  /** One evaluation cycle. Exposed so tests / replay can step manually. */
  tick(): void {
    const bot = useBotStore.getState()
    // Auto-trading only drives positions in mock/sim mode. In live mode,
    // the backend rules engine is authoritative.
    if (!bot.botRunning || (!bot.mockMode && !bot.simMode)) return

    const market = useMarketStore.getState()
    const wl = market.watchlists.find((w) => w.id === market.activeWatchlist)
    const symbols = wl?.symbols ?? []
    if (symbols.length === 0) return

    // 1. Update price history from the latest quotes.
    for (const sym of symbols) {
      const q = market.quotes[sym]
      if (!q) continue
      const h = this.history.get(sym) ?? { prices: [], lastTs: 0 }
      const ts = Date.parse(q.last_update) || Date.now()
      if (ts !== h.lastTs && Number.isFinite(q.price) && q.price > 0) {
        h.prices.push(q.price)
        if (h.prices.length > 200) h.prices.shift()
        h.lastTs = ts
      }
      this.history.set(sym, h)
    }

    // 2. Manage open positions first (exits free up cash for new entries).
    this.managePositions()

    // 3. Evaluate entries.
    this.evaluateEntries(symbols)

    // 4. Refresh derived account fields (unrealized P&L, positions value).
    this.refreshAccount()
  }

  // ── Position management ────────────────────────────────────────────────────

  private managePositions(): void {
    const market = useMarketStore.getState()
    const cfg = this.config

    for (const meta of [...this.positions.values()]) {
      const q = market.quotes[meta.symbol]
      if (!q) continue
      const px = q.price
      meta.ticks += 1
      if (px > meta.peakPx) meta.peakPx = px

      const stopLoss   = meta.entryPx * (1 - cfg.stopLossPct)
      const takeProfit = meta.entryPx * (1 + cfg.takeProfitPct)
      const trailStop  = meta.peakPx  * (1 - cfg.trailingStopPct)

      let reason: string | null = null
      if      (px <= stopLoss)            reason = 'STOP_LOSS'
      else if (px >= takeProfit)          reason = 'TAKE_PROFIT'
      else if (px <= trailStop &&
               meta.peakPx > meta.entryPx) reason = 'TRAILING_STOP'
      else if (meta.ticks >= cfg.maxHoldTicks) reason = 'MAX_HOLD'

      if (reason) this.closePosition(meta, px, reason)
    }
  }

  private closePosition(meta: PositionMeta, fillPx: number, reason: string): void {
    const acc = useAccountStore.getState()
    const proceeds = meta.qty * fillPx
    const pnl      = (fillPx - meta.entryPx) * meta.qty

    // Remove from store positions.
    acc.setPositions(acc.positions.filter((p) => p.symbol !== meta.symbol))

    // Credit cash & realized P&L on the (mock) account summary.
    const account = acc.account
    if (account && 'balance' in account) {
      const updated: AccountSummary = {
        ...account,
        cash:          account.cash + proceeds,
        balance:       account.balance + pnl,
        realized_pnl:  account.realized_pnl + pnl,
        unrealized_pnl: account.unrealized_pnl, // refreshed below
      }
      acc.setAccount(updated)
    }

    // Append a trade record.
    const trade: Trade = {
      id:         `auto-${Date.now()}-${meta.symbol}-SELL`,
      rule_id:    'auto-trader',
      rule_name:  `Auto Exit (${reason})`,
      symbol:     meta.symbol,
      action:     'SELL',
      asset_type: 'STK',
      quantity:   meta.qty,
      order_type: 'MKT',
      fill_price: +fillPx.toFixed(4),
      status:     'FILLED',
      timestamp:  new Date().toISOString(),
    }
    acc.addTrade(trade)

    this.positions.delete(meta.symbol)
  }

  // ── Entry evaluation ───────────────────────────────────────────────────────

  private evaluateEntries(symbols: string[]): void {
    const cfg    = this.config
    const market = useMarketStore.getState()
    const acc    = useAccountStore.getState()

    if (this.positions.size >= cfg.maxOpenPositions) return

    const account = acc.account
    const cash    = account?.cash ?? 0
    if (cash <= 0) return

    for (const sym of symbols) {
      if (this.positions.has(sym)) continue
      if (this.positions.size >= cfg.maxOpenPositions) break

      const h = this.history.get(sym)
      if (!h || h.prices.length < Math.max(cfg.minBarsForEntry, cfg.longWindow + 1)) continue

      const shortMA = sma(h.prices, cfg.shortWindow)
      const longMA  = sma(h.prices, cfg.longWindow)
      const r       = rsi(h.prices, cfg.rsiPeriod)
      if (shortMA == null || longMA == null || r == null) continue

      const bullish = shortMA > longMA && r < cfg.rsiOverbought
      if (!bullish) continue

      const q = market.quotes[sym]
      if (!q || q.price <= 0) continue

      const budget = cash * cfg.entryFraction
      const qty    = Math.floor(budget / q.price)
      if (qty <= 0) continue

      this.openPosition(sym, q.price, qty)
    }
  }

  private openPosition(symbol: string, fillPx: number, qty: number): void {
    const acc  = useAccountStore.getState()
    const cost = fillPx * qty

    // Push into store as a real Position so the existing UI renders it.
    const newPos: Position = {
      symbol,
      asset_type:    'STK',
      qty,
      avg_cost:      +fillPx.toFixed(4),
      market_price:  +fillPx.toFixed(4),
      market_value:  +cost.toFixed(2),
      unrealized_pnl: 0,
      realized_pnl:   0,
    }
    acc.setPositions([...acc.positions, newPos])

    // Debit cash on the mock account summary.
    const account = acc.account
    if (account && 'balance' in account) {
      const updated: AccountSummary = {
        ...account,
        cash: account.cash - cost,
      }
      acc.setAccount(updated)
    }

    // Append trade record.
    const trade: Trade = {
      id:         `auto-${Date.now()}-${symbol}-BUY`,
      rule_id:    'auto-trader',
      rule_name:  'Auto Entry (SMA cross + RSI)',
      symbol,
      action:     'BUY',
      asset_type: 'STK',
      quantity:   qty,
      order_type: 'MKT',
      fill_price: +fillPx.toFixed(4),
      status:     'FILLED',
      timestamp:  new Date().toISOString(),
    }
    acc.addTrade(trade)

    this.positions.set(symbol, {
      symbol,
      entryTs: Date.now(),
      entryPx: fillPx,
      peakPx:  fillPx,
      qty,
      ticks:   0,
    })
  }

  // ── Account refresh ────────────────────────────────────────────────────────

  private refreshAccount(): void {
    const acc    = useAccountStore.getState()
    const market = useMarketStore.getState()

    let posValue = 0
    let unreal   = 0

    const updatedPositions = acc.positions.map((p) => {
      if (!('market_price' in p)) return p
      const q = market.quotes[p.symbol]
      const px = q?.price ?? p.market_price
      const value = px * p.qty
      const pl = (px - p.avg_cost) * p.qty
      posValue += value
      unreal   += pl
      return {
        ...p,
        market_price:   +px.toFixed(4),
        market_value:   +value.toFixed(2),
        unrealized_pnl: +pl.toFixed(2),
      } as Position
    })

    acc.setPositions(updatedPositions)

    const account = acc.account
    if (account && 'balance' in account) {
      acc.setAccount({
        ...account,
        unrealized_pnl: +unreal.toFixed(2),
      })
    }
  }
}

export const autoTrader = new AutoTrader()

/**
 * Quick smoke test for the AutoTrader engine.
 *
 * Boots zustand stores, primes a deterministic price stream that
 * (a) trends up then down so SMA crossover triggers and then a
 * stop-loss / trailing-stop fires, and verifies the engine opened
 * AND closed a position autonomously without any human action.
 *
 * Run:   node --experimental-vm-modules scripts/test-auto-trader.mjs
 *
 * The script transpiles the TS sources on the fly via esbuild; we
 * avoid pulling in any new dev deps by inlining behavior here.
 */

import { execSync } from 'node:child_process'
import { mkdtempSync, writeFileSync, readFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { pathToFileURL } from 'node:url'

const root = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..')
const outDir = mkdtempSync(path.join(tmpdir(), 'autotrader-test-'))

// Build only what we need with vite's bundled esbuild.
execSync(
  `npx esbuild ${path.join(root, 'src/services/autoTrader.ts')} ` +
  `--bundle --format=esm --platform=neutral ` +
  `--alias:@=${path.join(root, 'src')} ` +
  `--outfile=${path.join(outDir, 'autoTrader.mjs')} ` +
  `--log-level=warning`,
  { stdio: 'inherit' },
)

const mod = await import(pathToFileURL(path.join(outDir, 'autoTrader.mjs')).href)
const { autoTrader } = mod

// Pull zustand stores out of the bundle by re-importing.
execSync(
  `npx esbuild ${path.join(root, 'src/store/index.ts')} ` +
  `--bundle --format=esm --platform=neutral ` +
  `--alias:@=${path.join(root, 'src')} ` +
  `--outfile=${path.join(outDir, 'store.mjs')} ` +
  `--log-level=warning`,
  { stdio: 'inherit' },
)

const store = await import(pathToFileURL(path.join(outDir, 'store.mjs')).href)
const { useBotStore, useMarketStore, useAccountStore } = store

// NOTE: re-importing the store creates a *separate* instance from the
// one the autoTrader bundle linked to. So we drive state via the
// autoTrader's view of the world: read it back through autoTrader.tick().
// Easier path: re-bundle BOTH together.

writeFileSync(
  path.join(outDir, 'entry.ts'),
  `
  export * from '${path.join(root, 'src/store/index.ts').replace(/\\\\/g, '/')}'
  export { autoTrader, DEFAULT_CONFIG } from '${path.join(root, 'src/services/autoTrader.ts').replace(/\\\\/g, '/')}'
  `,
)

execSync(
  `npx esbuild ${path.join(outDir, 'entry.ts')} ` +
  `--bundle --format=esm --platform=neutral ` +
  `--alias:@=${path.join(root, 'src')} ` +
  `--outfile=${path.join(outDir, 'bundle.mjs')} ` +
  `--log-level=warning`,
  { stdio: 'inherit' },
)

const all = await import(pathToFileURL(path.join(outDir, 'bundle.mjs')).href)
const { autoTrader: at, useBotStore: bs, useMarketStore: ms, useAccountStore: as } = all

let failures = 0
function assert(cond, msg) {
  if (!cond) { console.error('  ✗', msg); failures++ }
  else        console.log('  ✓', msg)
}

// ── Set up a deterministic environment ────────────────────────────────────
bs.setState({ botRunning: true, mockMode: true, simMode: false, ibkrConnected: false })
as.setState({ account: {
  balance: 100_000, cash: 100_000, margin_used: 0,
  unrealized_pnl: 0, realized_pnl: 0, currency: 'USD', is_mock: true,
}, positions: [], orders: [], trades: [], loading: false })

ms.setState({
  watchlists: [{ id: 'default', name: 'WL', symbols: ['TEST'] }],
  activeWatchlist: 'default',
  quotes: {},
  bars: {}, compBars: {}, selectedSymbol: 'TEST', compSymbol: '',
  compMode: false, sortField: 'change_pct', sortDir: 'desc',
  loading: false, lastUpdated: null, selectedIndicators: [],
})

// Configure for fast deterministic signals.
at.setConfig({
  tickIntervalMs:  10,
  shortWindow:     3,
  longWindow:      6,
  rsiPeriod:       5,
  rsiOverbought:   101,
  rsiOversold:     10,
  entryFraction:   0.1,
  stopLossPct:     0.05,
  takeProfitPct:   0.5,
  trailingStopPct: 0.1,
  maxHoldTicks:    1000,
  minBarsForEntry: 6,
  maxOpenPositions: 5,
})
at.reset()

console.log('Phase 1: monotonic uptrend → should trigger entry')

// Helper to push a quote and tick.
let ts = Date.now()
function push(price) {
  ts += 1000
  ms.setState((s) => ({
    quotes: { ...s.quotes, TEST: {
      symbol: 'TEST', price, change: 0, change_pct: 0,
      last_update: new Date(ts).toISOString(),
    } },
  }))
  at.tick()
}

bs.setState({ botRunning: true, mockMode: true })

// Strong uptrend.
const upTrend = [100, 101, 102, 103, 104, 105, 106, 107, 108, 109, 110, 111]
for (const p of upTrend) push(p)

let positions = as.getState().positions
assert(positions.length === 1, `entered exactly one position (got ${positions.length})`)
assert(positions[0]?.symbol === 'TEST', 'symbol is TEST')
const entryPx = positions[0]?.avg_cost
assert(entryPx > 0, `entry price > 0 (got ${entryPx})`)

let trades = as.getState().trades
const buys = trades.filter((t) => t.action === 'BUY')
assert(buys.length === 1, `exactly one BUY trade recorded (got ${buys.length})`)
assert(buys[0]?.rule_name?.includes('Auto Entry'), 'BUY trade has Auto Entry rule_name')

console.log('Phase 2: sharp drop → should trigger stop-loss / trailing-stop exit')
const downTrend = [110, 108, 105, 100, 95, 90]
for (const p of downTrend) push(p)

positions = as.getState().positions
assert(positions.length === 0, `position closed after drop (got ${positions.length} remaining)`)
trades = as.getState().trades
const sells = trades.filter((t) => t.action === 'SELL')
assert(sells.length === 1, `exactly one SELL trade recorded (got ${sells.length})`)
assert(
  ['STOP_LOSS', 'TRAILING_STOP'].some((r) => sells[0]?.rule_name?.includes(r)),
  `SELL trade marked as STOP_LOSS or TRAILING_STOP (got "${sells[0]?.rule_name}")`,
)

const account = as.getState().account
assert(account.realized_pnl < 0, `realized P&L is negative after losing trade (got ${account.realized_pnl})`)
assert(account.cash > 0 && account.cash < 100_000 + 1, 'cash returned to roughly initial range')

console.log('Phase 2b: another uptrend → re-enter, then take-profit on big spike')
at.reset()
// Re-set cash so we have a clean run.
as.setState({
  account: { balance: 100_000, cash: 100_000, margin_used: 0,
             unrealized_pnl: 0, realized_pnl: 0, currency: 'USD', is_mock: true },
  positions: [], orders: [], trades: [], loading: false,
})
at.setConfig({
  takeProfitPct: 0.05,  // 5% gain triggers take-profit
})
ms.setState({ quotes: {} })
for (const p of [100, 101, 102, 103, 104, 105, 106, 107]) push(p)

let phase2bTrades = as.getState().trades
const phase2bBuys = phase2bTrades.filter((t) => t.action === 'BUY')
assert(phase2bBuys.length === 1, `Phase 2b: re-entered after reset (BUYs=${phase2bBuys.length})`)
const phase2bEntryPx = phase2bBuys[0].fill_price

// Spike up >5% above entry.
for (const p of [phase2bEntryPx * 1.06, phase2bEntryPx * 1.07]) push(p)

phase2bTrades = as.getState().trades
const phase2bSells = phase2bTrades.filter((t) => t.action === 'SELL')
assert(phase2bSells.length === 1, `Phase 2b: take-profit fired (SELLs=${phase2bSells.length})`)
assert(
  phase2bSells[0].rule_name.includes('TAKE_PROFIT'),
  `Phase 2b: exit reason is TAKE_PROFIT (got "${phase2bSells[0].rule_name}")`,
)
assert(
  as.getState().account.realized_pnl > 0,
  `Phase 2b: realized P&L > 0 on winning trade (got ${as.getState().account.realized_pnl})`,
)
// Engine may immediately re-enter on the same tick because conditions
// are still bullish — that is the intended behavior of an auto-trader.
// What matters here is the take-profit fired and realized P&L flipped
// positive, which we already asserted.

console.log('Phase 3: stop bot → no further trades on new ticks')
at.stop()
bs.setState({ botRunning: false })
const tradesBeforeStop = as.getState().trades.length
for (const p of [88, 86, 84]) push(p)
assert(as.getState().trades.length === tradesBeforeStop, 'no trades fire while bot is stopped')

if (failures > 0) {
  console.error(`\n${failures} assertion(s) failed`)
  process.exit(1)
} else {
  console.log('\nAll auto-trader assertions passed.')
}

import { beforeEach, describe, expect, it } from 'vitest'
import { useMarketStore } from '@/store'
import type { MarketQuote, OHLCVBar } from '@/types'

function makeQuote(symbol: string, price: number): MarketQuote {
  return {
    symbol,
    price,
    change: 0,
    change_pct: 0,
    last_update: new Date(0).toISOString(),
  }
}

function makeBars(): OHLCVBar[] {
  return [
    { time: 1_700_000_040, open: 100, high: 101, low: 99, close: 100, volume: 10 },
    { time: 1_700_000_100, open: 100, high: 102, low: 100, close: 101, volume: 12 },
  ]
}

describe('useMarketStore.applyLiveQuote', () => {
  beforeEach(() => {
    useMarketStore.setState({
      quotes: { AAPL: makeQuote('AAPL', 101) },
      bars: { AAPL: makeBars() },
      compBars: {},
      selectedSymbol: 'AAPL',
      compSymbol: '',
      compMode: false,
      watchlists: [{ id: 'default', name: 'Watchlist', symbols: ['AAPL'] }],
      activeWatchlist: 'default',
      sortField: 'change_pct',
      sortDir: 'desc',
      loading: false,
      lastUpdated: null,
      selectedIndicators: [],
      chartType: 'candlestick',
    })
  })

  it('updates quote metadata and current bar in-place', () => {
    const state = useMarketStore.getState()
    state.applyLiveQuote('AAPL', 103.5, 1_700_000_130, 60, 'ibkr', 0.3, 'open')

    const next = useMarketStore.getState()
    expect(next.quotes.AAPL.price).toBe(103.5)
    expect(next.quotes.AAPL.live_source).toBe('ibkr')
    expect(next.quotes.AAPL.stale_s).toBe(0.3)
    expect(next.quotes.AAPL.market_state).toBe('open')

    const lastBar = next.bars.AAPL[next.bars.AAPL.length - 1]
    expect(lastBar.time).toBe(1_700_000_100)
    expect(lastBar.close).toBe(103.5)
    expect(lastBar.high).toBe(103.5)
  })

  it('appends a new bar when quote crosses into next bucket', () => {
    const state = useMarketStore.getState()
    state.applyLiveQuote('AAPL', 104, 1_700_000_170, 60, 'yahoo', 1.2, 'extended')

    const next = useMarketStore.getState()
    expect(next.bars.AAPL).toHaveLength(3)
    const lastBar = next.bars.AAPL[next.bars.AAPL.length - 1]
    expect(lastBar.time).toBe(1_700_000_160)
    expect(lastBar.open).toBe(101)
    expect(lastBar.close).toBe(104)
    expect(next.quotes.AAPL.live_source).toBe('yahoo')
    expect(next.quotes.AAPL.market_state).toBe('extended')
  })
})

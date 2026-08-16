import { beforeEach, describe, expect, it } from 'vitest'
import { useMarketStore } from '@/store'
import type { MarketQuote, OHLCVBar } from '@/types'
import { chartBarsKey } from '@/utils/chartTimeframes'

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
      bars: { [chartBarsKey('AAPL', '1m')]: makeBars() },
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
      chartResolution: '1m',
    })
  })

  it('updates quote metadata and current bar in-place', () => {
    const state = useMarketStore.getState()
    state.applyLiveQuote('AAPL', 103.5, 1_700_000_130, '1m', 'ibkr', 0.3, 'open')

    const next = useMarketStore.getState()
    expect(next.quotes.AAPL.price).toBe(103.5)
    expect(next.quotes.AAPL.live_source).toBe('ibkr')
    expect(next.quotes.AAPL.stale_s).toBe(0.3)
    expect(next.quotes.AAPL.market_state).toBe('open')

    const bars = next.bars[chartBarsKey('AAPL', '1m')]
    const lastBar = bars[bars.length - 1]
    expect(lastBar.time).toBe(1_700_000_100)
    expect(lastBar.close).toBe(103.5)
    expect(lastBar.high).toBe(103.5)
  })

  it('appends a new bar when quote crosses into next bucket', () => {
    const state = useMarketStore.getState()
    state.applyLiveQuote('AAPL', 104, 1_700_000_170, '1m', 'yahoo', 1.2, 'extended')

    const next = useMarketStore.getState()
    const bars = next.bars[chartBarsKey('AAPL', '1m')]
    expect(bars).toHaveLength(3)
    const lastBar = bars[bars.length - 1]
    expect(lastBar.time).toBe(1_700_000_160)
    expect(lastBar.open).toBe(101)
    expect(lastBar.close).toBe(104)
    expect(next.quotes.AAPL.live_source).toBe('yahoo')
    expect(next.quotes.AAPL.market_state).toBe('extended')
  })

  it('keeps histories for different resolutions separate', () => {
    const daily = [{ ...makeBars()[0], time: 1_699_920_000 }]
    useMarketStore.getState().setBars('AAPL', '1d', daily)

    expect(useMarketStore.getState().bars[chartBarsKey('AAPL', '1m')]).toEqual(makeBars())
    expect(useMarketStore.getState().bars[chartBarsKey('AAPL', '1d')]).toEqual(daily)
  })

  it('preserves broker OHLCV while aggregating into the selected resolution', () => {
    useMarketStore.getState().applyRealtimeBar('AAPL', {
      time: 1_700_000_130,
      open: 101,
      high: 106,
      low: 98,
      close: 105,
      volume: 7,
    }, '1m')

    const bars = useMarketStore.getState().bars[chartBarsKey('AAPL', '1m')]
    expect(bars[bars.length - 1]).toEqual({
      time: 1_700_000_100,
      open: 100,
      high: 106,
      low: 98,
      close: 105,
      volume: 19,
    })
  })
})

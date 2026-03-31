import { create } from 'zustand'
import type {
  ChartType,
  MarketQuote,
  OHLCVBar,
  SortDir,
  SortField,
  Watchlist,
} from '@/types'
import type { IndicatorId } from '@/utils/indicators'

interface MarketState {
  quotes:           Record<string, MarketQuote>
  bars:             Record<string, OHLCVBar[]>
  compBars:         Record<string, OHLCVBar[]>   // comparison overlay bars
  selectedSymbol:   string
  compSymbol:       string                        // symbol overlaid on chart
  compMode:         boolean
  watchlists:       Watchlist[]
  activeWatchlist:  string                         // watchlist id
  sortField:        SortField
  sortDir:          SortDir
  loading:          boolean
  lastUpdated:      number | null

  selectedIndicators: IndicatorId[]
  chartType:          ChartType

  setQuotes:          (quotes: MarketQuote[]) => void
  updateQuote:        (q: MarketQuote) => void
  updateQuotePrice:   (symbol: string, price: number) => void
  applyLiveQuote: (
    symbol: string,
    price: number,
    time: number,
    barSeconds: number,
    source: 'ibkr' | 'yahoo',
    staleS: number,
    marketState?: 'open' | 'extended' | 'closed' | 'unknown',
  ) => void
  setBars:            (symbol: string, bars: OHLCVBar[]) => void
  setCompBars:        (symbol: string, bars: OHLCVBar[]) => void
  setSelectedSymbol:  (symbol: string) => void
  setCompSymbol:      (symbol: string) => void
  toggleCompMode:     () => void
  toggleIndicator:    (id: IndicatorId) => void
  setChartType:       (ct: ChartType) => void
  addWatchlist:       (name: string) => void
  removeWatchlist:    (id: string) => void
  addToWatchlist:     (listId: string, symbol: string) => void
  removeFromWatchlist:(listId: string, symbol: string) => void
  setActiveWatchlist: (id: string) => void
  setSort:            (field: SortField, dir: SortDir) => void
  setLoading:         (v: boolean) => void
}

export const useMarketStore = create<MarketState>((set, get) => ({
  quotes:          {},
  bars:            {},
  compBars:        {},
  selectedSymbol:  'AAPL',
  compSymbol:      '',
  compMode:        false,
  watchlists: [
    { id: 'default', name: 'Watchlist', symbols: ['BTC-USD', 'ETH-USD', 'AAPL', 'TSLA', 'SPY', 'QQQ', 'NVDA'] },
    { id: 'crypto',  name: 'Crypto',    symbols: ['BTC-USD', 'ETH-USD', 'SOL-USD'] },
    { id: 'tech',    name: 'Tech',      symbols: ['AAPL', 'MSFT', 'GOOGL', 'META', 'NVDA', 'AMZN'] },
  ],
  activeWatchlist:    'default',
  sortField:          'change_pct',
  sortDir:            'desc',
  loading:            false,
  lastUpdated:        null,
  selectedIndicators: [],
  chartType:          'candlestick',

  setQuotes: (quotes) => {
    const map: Record<string, MarketQuote> = {}
    quotes.forEach((q) => { map[q.symbol] = q })
    set((s) => ({ quotes: { ...s.quotes, ...map }, lastUpdated: Date.now() }))
  },
  updateQuote: (q) =>
    set((s) => ({ quotes: { ...s.quotes, [q.symbol]: q } })),
  updateQuotePrice: (symbol, price) =>
    set((s) => {
      const existing = s.quotes[symbol]
      if (!existing) return {}
      return {
        quotes: {
          ...s.quotes,
          [symbol]: { ...existing, price, last_update: new Date().toISOString() },
        },
      }
    }),
  applyLiveQuote: (symbol, price, time, barSeconds, source, staleS, marketState) =>
    set((s) => {
      const ts = Number.isFinite(time) ? Math.max(1, Math.floor(time)) : Math.floor(Date.now() / 1000)
      const bucket = Math.max(1, Math.floor(barSeconds))
      const barTime = Math.floor(ts / bucket) * bucket

      const patchSeries = (series: OHLCVBar[] | undefined): OHLCVBar[] | null => {
        if (!series?.length) return null
        const last = series[series.length - 1]
        if (barTime <= 0) return null
        if (last.time === barTime) {
          const updatedLast: OHLCVBar = {
            ...last,
            high: Math.max(last.high, price),
            low: Math.min(last.low, price),
            close: price,
          }
          return [...series.slice(0, -1), updatedLast]
        }
        if (barTime > last.time) {
          const next: OHLCVBar = {
            time: barTime,
            open: last.close,
            high: price,
            low: price,
            close: price,
            volume: 0,
          }
          return [...series, next].slice(-5000)
        }
        return null
      }

      const nextBars = patchSeries(s.bars[symbol])
      const nextCompBars = patchSeries(s.compBars[symbol])
      const prevQuote = s.quotes[symbol]
      const quote: MarketQuote = prevQuote
        ? {
            ...prevQuote,
            symbol,
            price,
            last_update: new Date(ts * 1000).toISOString(),
            live_source: source,
            stale_s: staleS,
            market_state: marketState ?? prevQuote.market_state,
          }
        : {
            symbol,
            price,
            change: 0,
            change_pct: 0,
            last_update: new Date(ts * 1000).toISOString(),
            live_source: source,
            stale_s: staleS,
            market_state: marketState ?? 'unknown',
          }

      return {
        quotes: {
          ...s.quotes,
          [symbol]: quote,
        },
        bars: nextBars ? { ...s.bars, [symbol]: nextBars } : s.bars,
        compBars: nextCompBars ? { ...s.compBars, [symbol]: nextCompBars } : s.compBars,
        lastUpdated: Date.now(),
      }
    }),
  setBars: (symbol, bars) =>
    set((s) => ({ bars: { ...s.bars, [symbol]: bars } })),
  setCompBars: (symbol, bars) =>
    set((s) => ({ compBars: { ...s.compBars, [symbol]: bars } })),
  setSelectedSymbol: (symbol) => set({ selectedSymbol: symbol }),
  setCompSymbol:     (symbol) => set({ compSymbol: symbol }),
  toggleCompMode:    () => set((s) => ({ compMode: !s.compMode })),
  toggleIndicator:   (id) =>
    set((s) => ({
      selectedIndicators: s.selectedIndicators.includes(id)
        ? s.selectedIndicators.filter((x) => x !== id)
        : [...s.selectedIndicators, id],
    })),
  setChartType: (ct) => set({ chartType: ct }),

  addWatchlist: (name) =>
    set((s) => ({
      watchlists: [
        ...s.watchlists,
        { id: crypto.randomUUID(), name, symbols: [] },
      ],
    })),
  removeWatchlist: (id) =>
    set((s) => ({ watchlists: s.watchlists.filter((w) => w.id !== id) })),
  addToWatchlist: (listId, symbol) =>
    set((s) => ({
      watchlists: s.watchlists.map((w) =>
        w.id === listId && !w.symbols.includes(symbol)
          ? { ...w, symbols: [...w.symbols, symbol] }
          : w,
      ),
    })),
  removeFromWatchlist: (listId, symbol) =>
    set((s) => ({
      watchlists: s.watchlists.map((w) =>
        w.id === listId ? { ...w, symbols: w.symbols.filter((s) => s !== symbol) } : w,
      ),
    })),
  setActiveWatchlist: (id) => set({ activeWatchlist: id }),
  setSort: (field, dir) => set({ sortField: field, sortDir: dir }),
  setLoading: (v) => set({ loading: v }),
}))

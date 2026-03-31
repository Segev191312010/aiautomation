import { create } from 'zustand'
import type {
  CorrelationMatrix,
  DailyPnL,
  ExposureBreakdown,
  MatchedTrade,
  PnLSummary,
  PortfolioAnalytics,
  SectorExposureRow,
  TradeHistoryRow,
} from '@/types'
import * as api from '@/services/api'

interface AnalyticsState {
  analytics:         PortfolioAnalytics | null
  pnlSummary:        PnLSummary | null
  dailyPnL:          DailyPnL[]
  matchedTrades:     MatchedTrade[]
  exposure:          ExposureBreakdown | null
  sectorExposure:    SectorExposureRow[]
  correlationMatrix: CorrelationMatrix | null
  tradeHistory:      TradeHistoryRow[]
  range:             '1W' | '1M' | '3M' | '6M' | '1Y' | 'ALL'
  loading:           boolean
  error:             string | null

  setRange:                (r: '1W' | '1M' | '3M' | '6M' | '1Y' | 'ALL') => void
  fetchPortfolioAnalytics: () => Promise<void>
  fetchPnL:                () => Promise<void>
  fetchSectorExposure:     () => Promise<void>
  fetchCorrelation:        () => Promise<void>
  fetchTradeHistory:       (limit?: number) => Promise<void>
  fetchAll:                () => Promise<void>
}

export const useAnalyticsStore = create<AnalyticsState>((set, get) => ({
  analytics:         null,
  pnlSummary:        null,
  dailyPnL:          [],
  matchedTrades:     [],
  exposure:          null,
  sectorExposure:    [],
  correlationMatrix: null,
  tradeHistory:      [],
  range:             '3M',
  loading:           false,
  error:             null,

  setRange: (r) => set({ range: r }),

  fetchPortfolioAnalytics: async () => {
    const { range } = get()
    try {
      const data = await api.fetchPortfolioAnalytics(range)
      const { tradeHistory } = get()
      const wins = tradeHistory.filter((t) => (t.pnl ?? 0) > 0)
      const losses = tradeHistory.filter((t) => (t.pnl ?? 0) < 0)
      const grossProfit = wins.reduce((s, t) => s + (t.pnl ?? 0), 0)
      const grossLoss = Math.abs(losses.reduce((s, t) => s + (t.pnl ?? 0), 0))
      const bestTrade = tradeHistory.reduce(
        (best, t) => (!best || (t.pnl ?? 0) > (best.pnl ?? 0) ? t : best),
        tradeHistory[0] as TradeHistoryRow | undefined,
      )
      const worstTrade = tradeHistory.reduce(
        (worst, t) => (!worst || (t.pnl ?? 0) < (worst.pnl ?? 0) ? t : worst),
        tradeHistory[0] as TradeHistoryRow | undefined,
      )
      const summary: PnLSummary = {
        realized_pnl:      data.total_pnl,
        realized_pnl_pct:  data.total_pnl_pct,
        unrealized_pnl:    0,
        today_pnl:         data.day_pnl,
        today_pnl_pct:     data.day_pnl_pct,
        win_rate:          data.win_rate,
        profit_factor:     grossLoss > 0 ? grossProfit / grossLoss : grossProfit > 0 ? Infinity : 0,
        best_trade_pnl:    bestTrade?.pnl ?? 0,
        best_trade_symbol: bestTrade?.symbol ?? '—',
        worst_trade_pnl:   worstTrade?.pnl ?? 0,
        worst_trade_symbol:worstTrade?.symbol ?? '—',
        total_trades:      tradeHistory.length,
      }
      set({ analytics: data, pnlSummary: summary })
    } catch (err) {
      set({ error: err instanceof Error ? err.message : 'Analytics load failed' })
    }
  },

  fetchPnL: async () => {
    try {
      const data = await api.fetchDailyPnL(90)
      set({ dailyPnL: data })
    } catch { /* backend offline */ }
  },

  fetchSectorExposure: async () => {
    try {
      const data = await api.fetchExposureBreakdown()
      set({ exposure: data })
      const sectorMap: Record<string, SectorExposureRow> = {}
      for (const pos of data.positions) {
        const s = pos.sector || 'Unknown'
        if (!sectorMap[s]) {
          sectorMap[s] = { sector: s, weight_pct: 0, value: 0, position_count: 0, pnl: 0 }
        }
        sectorMap[s].weight_pct += pos.weight_pct
        sectorMap[s].value      += pos.value
        sectorMap[s].position_count += 1
        sectorMap[s].pnl        += pos.pnl
      }
      set({ sectorExposure: Object.values(sectorMap).sort((a, b) => b.weight_pct - a.weight_pct) })
    } catch { /* backend offline */ }
  },

  fetchCorrelation: async () => {
    try {
      const data = await api.fetchCorrelationMatrix()
      set({ correlationMatrix: data })
    } catch { /* backend offline */ }
  },

  fetchTradeHistory: async (limit = 200) => {
    try {
      const data = await api.fetchTradeHistory(limit)
      // FIFO-match BUY/SELL pairs per symbol
      const bySymbol: Record<string, TradeHistoryRow[]> = {}
      for (const t of data) {
        if (!bySymbol[t.symbol]) bySymbol[t.symbol] = []
        bySymbol[t.symbol].push(t)
      }
      const matched: MatchedTrade[] = []
      for (const rows of Object.values(bySymbol)) {
        const sorted = [...rows].sort((a, b) => new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime())
        let i = 0
        while (i < sorted.length - 1) {
          const entry = sorted[i]
          const exit  = sorted[i + 1]
          if (entry.action === 'BUY' && exit.action === 'SELL') {
            const entryMs = new Date(entry.timestamp).getTime()
            const exitMs  = new Date(exit.timestamp).getTime()
            const holdDays = Math.max(0, Math.round((exitMs - entryMs) / 86_400_000))
            const pnl = exit.pnl ?? (exit.fill_price - entry.fill_price) * entry.quantity
            const pnlPct = entry.fill_price > 0
              ? ((exit.fill_price - entry.fill_price) / entry.fill_price) * 100
              : 0
            matched.push({
              id: `${entry.id}_${exit.id}`,
              symbol:      entry.symbol,
              entry_date:  entry.timestamp,
              exit_date:   exit.timestamp,
              entry_price: entry.fill_price,
              exit_price:  exit.fill_price,
              qty:         entry.quantity,
              pnl,
              pnl_pct: pnlPct,
              hold_days: holdDays,
            })
            i += 2
          } else {
            i++
          }
        }
      }
      set({ tradeHistory: data, matchedTrades: matched })
    } catch { /* backend offline */ }
  },

  fetchAll: async () => {
    set({ loading: true, error: null })
    try {
      await get().fetchTradeHistory(200)
      await Promise.all([
        get().fetchPortfolioAnalytics(),
        get().fetchPnL(),
        get().fetchSectorExposure(),
        get().fetchCorrelation(),
      ])
    } finally {
      set({ loading: false })
    }
  },
}))

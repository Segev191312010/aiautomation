import { create } from 'zustand'
import type {
  StockAnalyst,
  StockAnalystDetail,
  StockCompanyInfo,
  StockEarningsDetail,
  StockEvents,
  StockFinancials,
  StockFinancialStatements,
  StockKeyStats,
  StockNarrative,
  StockOverview,
  StockOwnership,
  StockRatingScorecard,
  StockSplits,
} from '@/types'
import * as api from '@/services/api'

interface StockProfileState {
  symbol: string | null
  loading: boolean
  error: string | null
  overview: StockOverview | null
  keyStats: StockKeyStats | null
  financials: StockFinancials | null
  analyst: StockAnalyst | null
  ownership: StockOwnership | null
  events: StockEvents | null
  narrative: StockNarrative | null
  financialStatements: StockFinancialStatements | null
  analystDetail: StockAnalystDetail | null
  ratingScorecard: StockRatingScorecard | null
  companyInfo: StockCompanyInfo | null
  stockSplits: StockSplits | null
  earningsDetail: StockEarningsDetail | null
  lastFetched: number | null

  setSymbol: (symbol: string) => void
  loadAll: (symbol: string) => Promise<void>
  clearProfile: () => void
  clearError: () => void
}

export const useStockProfileStore = create<StockProfileState>((set) => ({
  symbol: null,
  loading: false,
  error: null,
  overview: null,
  keyStats: null,
  financials: null,
  analyst: null,
  ownership: null,
  events: null,
  narrative: null,
  financialStatements: null,
  analystDetail: null,
  ratingScorecard: null,
  companyInfo: null,
  stockSplits: null,
  earningsDetail: null,
  lastFetched: null,

  setSymbol: (symbol) => set({ symbol }),
  clearError: () => set({ error: null }),
  clearProfile: () => set({
    symbol: null, overview: null, keyStats: null, financials: null,
    analyst: null, ownership: null, events: null, narrative: null,
    financialStatements: null, analystDetail: null, ratingScorecard: null,
    companyInfo: null, stockSplits: null, earningsDetail: null,
    lastFetched: null, error: null,
  }),

  loadAll: async (symbol: string) => {
    set({
      loading: true, error: null, symbol,
      overview: null, keyStats: null, financials: null,
      analyst: null, ownership: null, events: null, narrative: null,
      financialStatements: null, analystDetail: null, ratingScorecard: null,
      companyInfo: null, stockSplits: null, earningsDetail: null,
      lastFetched: null,
    })
    try {
      const profile = await api.fetchStockProfile(symbol)
      set({
        overview: profile.overview,
        keyStats: profile.key_stats,
        financials: profile.financials,
        analyst: profile.analyst,
        ownership: profile.ownership,
        events: profile.events,
        narrative: profile.narrative,
        financialStatements: profile.financial_statements,
        analystDetail: profile.analyst_detail,
        ratingScorecard: profile.rating_scorecard,
        companyInfo: profile.company_info,
        stockSplits: profile.stock_splits,
        earningsDetail: profile.earnings_detail,
        lastFetched: Date.now(),
      })
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Stock profile load failed'
      set({ error: msg })
    } finally {
      set({ loading: false })
    }
  },
}))

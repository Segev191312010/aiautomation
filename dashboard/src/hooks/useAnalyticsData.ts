import { useEffect, useCallback } from 'react'
import { useAnalyticsStore, useRiskStore } from '@/store'

/**
 * Thin adapter over useAnalyticsStore + useRiskStore — manages mount trigger, range param, and refresh.
 * Stores own data + loading/error state + fetch actions.
 */
export function useAnalyticsData(range: '1W' | '1M' | '3M' | '6M' | '1Y' | 'ALL' = '3M') {
  const analyticsStore = useAnalyticsStore()
  const riskStore = useRiskStore()

  useEffect(() => {
    analyticsStore.setRange(range)
  }, [range, analyticsStore.setRange])

  const loadAll = useCallback(async () => {
    analyticsStore.fetchTradeHistory()
    analyticsStore.fetchPnL()
    analyticsStore.fetchSectorExposure()
    analyticsStore.fetchCorrelation()
    analyticsStore.fetchPortfolioAnalytics()
    riskStore.fetchRiskLimits()
  }, [analyticsStore, riskStore])

  useEffect(() => {
    loadAll()
  }, [loadAll])

  return {
    analytics: analyticsStore.analytics,
    pnlSummary: analyticsStore.pnlSummary,
    dailyPnL: analyticsStore.dailyPnL,
    exposure: analyticsStore.exposure,
    sectorExposure: analyticsStore.sectorExposure,
    correlationMatrix: analyticsStore.correlationMatrix,
    tradeHistory: analyticsStore.tradeHistory,
    riskLimits: riskStore.riskLimits,
    loading: analyticsStore.loading,
    error: analyticsStore.error,
    refresh: loadAll,
  }
}

import { useEffect, useCallback } from 'react'
import { useAnalyticsStore, useRiskStore } from '@/store'

/**
 * Thin adapter over useAnalyticsStore + useRiskStore — manages mount trigger, range param, and refresh.
 * Stores own data + loading/error state + fetch actions.
 *
 * Risk events are intentionally surfaced as degraded today because the backend
 * endpoint is still a stub and returns an explicit empty array.
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
    riskStore.fetchRiskEvents()
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
    riskEvents: riskStore.riskEvents,
    riskEventsStatus: riskStore.riskEventsStatus,
    riskEventsNote: riskStore.riskEventsStatus === 'degraded'
      ? 'Risk events are currently an empty stub because the backend endpoint is not implemented.'
      : null,
    loading: analyticsStore.loading,
    error: analyticsStore.error,
    refresh: loadAll,
  }
}

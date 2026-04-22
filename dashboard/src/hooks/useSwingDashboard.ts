import { useEffect, useCallback, useRef } from 'react'
import { useSwingStore } from '@/store'
import { fetchSwingDashboard } from '@/services/api'
import { getMockSwingDashboard } from '@/components/swing/mockData'

const AUTO_REFRESH_MS = 5 * 60 * 1000

/**
 * Whether to use mock data when the backend endpoint doesn't exist yet.
 * Set to false once the backend is wired up and returning real data.
 */
const USE_MOCK_FALLBACK = true

export function useSwingDashboard() {
  const setDashboard = useSwingStore((s) => s.setDashboard)
  const setLoading = useSwingStore((s) => s.setLoading)
  const setError = useSwingStore((s) => s.setError)

  const breadth = useSwingStore((s) => s.breadth)
  const guruResults = useSwingStore((s) => s.guruResults)
  const atrMatrix = useSwingStore((s) => s.atrMatrix)
  const club97 = useSwingStore((s) => s.club97)
  const stockbeeResults = useSwingStore((s) => s.stockbeeResults)
  const industries = useSwingStore((s) => s.industries)
  const stages = useSwingStore((s) => s.stages)
  const grades = useSwingStore((s) => s.grades)
  const loading = useSwingStore((s) => s.loading)
  const error = useSwingStore((s) => s.error)
  const lastUpdate = useSwingStore((s) => s.lastUpdate)
  const activeGuruTab = useSwingStore((s) => s.activeGuruTab)
  const activeStockbeeTab = useSwingStore((s) => s.activeStockbeeTab)
  const setGuruTab = useSwingStore((s) => s.setGuruTab)
  const setStockbeeTab = useSwingStore((s) => s.setStockbeeTab)

  const inFlightRef = useRef(false)
  const mountedRef = useRef(true)

  const loadData = useCallback(async () => {
    if (inFlightRef.current || document.hidden) return
    inFlightRef.current = true
    setLoading(true)
    setError(null)
    try {
      const data = await fetchSwingDashboard()
      if (mountedRef.current) setDashboard(data)
    } catch (err) {
      if (!mountedRef.current) return
      const msg = err instanceof Error ? err.message : 'Failed to load swing data'

      // Only fall back to mock data if endpoint doesn't exist (404) or backend is offline
      // Use precise pattern match to avoid false positives (e.g. a 500 body containing "404")
      const is404 = msg.includes('\u2192 404:') || msg.includes('-> 404:')
      const isOffline = msg.includes('Failed to fetch') || msg.includes('NetworkError')

      if ((is404 || isOffline) && USE_MOCK_FALLBACK) {
        setDashboard(getMockSwingDashboard())
      } else {
        // Real error — show it, do NOT load fake data
        setError(msg)
      }
    } finally {
      inFlightRef.current = false
      if (mountedRef.current) setLoading(false)
    }
  }, [setDashboard, setLoading, setError])

  useEffect(() => {
    mountedRef.current = true
    loadData()
    const t = setInterval(loadData, AUTO_REFRESH_MS)

    const onVisibility = () => {
      if (!document.hidden) loadData()
    }
    document.addEventListener('visibilitychange', onVisibility)

    return () => {
      mountedRef.current = false
      clearInterval(t)
      document.removeEventListener('visibilitychange', onVisibility)
      setLoading(false)
    }
  }, [loadData])

  return {
    breadth, guruResults, atrMatrix, club97, stockbeeResults,
    industries, stages, grades, loading, error, lastUpdate,
    activeGuruTab, activeStockbeeTab, setGuruTab, setStockbeeTab,
    refresh: loadData,
  }
}

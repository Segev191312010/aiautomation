import { useEffect } from 'react'
import { useAlertStore } from '@/store'

/**
 * Thin adapter over useAlertStore — triggers load on mount.
 * Store owns alerts, history, stats, loading state.
 */
export function useAlertsData() {
  const store = useAlertStore()

  useEffect(() => {
    store.loadAlerts()
    store.loadHistory()
    store.fetchAlertStats()
  }, [store.loadAlerts, store.loadHistory, store.fetchAlertStats])

  return {
    alerts: store.alerts,
    history: store.history,
    alertStats: store.alertStats,
    loading: store.loading,
    unreadCount: store.unreadCount,
    refresh: store.loadAlerts,
  }
}

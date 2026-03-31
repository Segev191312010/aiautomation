import { create } from 'zustand'
import type {
  Alert,
  AlertFiredEvent,
  AlertHistory,
  AlertStats,
  NotificationPrefs,
} from '@/types'
import { DEFAULT_NOTIFICATION_PREFS } from '@/types'
import * as api from '@/services/api'

interface AlertState {
  alerts:            Alert[]
  history:           AlertHistory[]
  unreadCount:       number
  recentFired:       AlertFiredEvent[]
  loading:           boolean
  notificationPrefs: NotificationPrefs
  alertStats:        AlertStats | null

  setAlerts:                (a: Alert[]) => void
  setHistory:               (h: AlertHistory[]) => void
  pushFired:                (e: AlertFiredEvent) => void
  markRead:                 () => void
  updateAlert:              (a: Alert) => void
  removeAlert:              (id: string) => void
  setLoading:               (v: boolean) => void
  loadAlerts:               () => Promise<void>
  loadHistory:              () => Promise<void>
  fetchAlertStats:          () => Promise<void>
  updateNotificationPrefs:  (partial: Partial<NotificationPrefs>) => void
}

export const useAlertStore = create<AlertState>((set) => ({
  alerts:            [],
  history:           [],
  unreadCount:       0,
  recentFired:       [],
  loading:           false,
  notificationPrefs: (() => {
    try {
      const stored = localStorage.getItem('alertNotificationPrefs')
      if (stored) return { ...DEFAULT_NOTIFICATION_PREFS, ...JSON.parse(stored) as Partial<NotificationPrefs> }
    } catch { /* ignore */ }
    return { ...DEFAULT_NOTIFICATION_PREFS }
  })(),
  alertStats: null,

  setAlerts: (a) => set({ alerts: a }),
  setHistory: (h) => set({ history: h }),
  pushFired: (e) =>
    set((s) => ({
      recentFired: [e, ...s.recentFired].slice(0, 20),
      unreadCount: s.unreadCount + 1,
    })),
  markRead: () => set({ unreadCount: 0 }),
  updateAlert: (a) => set((s) => ({ alerts: s.alerts.map((x) => (x.id === a.id ? a : x)) })),
  removeAlert: (id) => set((s) => ({ alerts: s.alerts.filter((x) => x.id !== id) })),
  setLoading: (v) => set({ loading: v }),

  loadAlerts: async () => {
    set({ loading: true })
    try {
      const alerts = await api.fetchAlerts()
      set({ alerts, loading: false })
    } catch {
      set({ loading: false })
    }
  },
  loadHistory: async () => {
    try {
      const history = await api.fetchAlertHistory()
      set({ history })
    } catch {
      // backend offline
    }
  },
  fetchAlertStats: async () => {
    try {
      const stats = await api.fetchAlertStats()
      set({ alertStats: stats })
    } catch {
      // backend offline — compute client-side fallback below
      set((s) => {
        const now = Date.now()
        const dayMs   = 86_400_000
        const weekMs  = 7 * dayMs
        const monthMs = 30 * dayMs

        const total_today = s.history.filter((h) => now - new Date(h.fired_at).getTime() < dayMs).length
        const total_week  = s.history.filter((h) => now - new Date(h.fired_at).getTime() < weekMs).length
        const total_month = s.history.filter((h) => now - new Date(h.fired_at).getTime() < monthMs).length

        const symbolCounts: Record<string, number> = {}
        for (const h of s.history) {
          symbolCounts[h.symbol] = (symbolCounts[h.symbol] ?? 0) + 1
        }
        const top_symbols = Object.entries(symbolCounts)
          .sort((a, b) => b[1] - a[1])
          .slice(0, 5)
          .map(([symbol, count]) => ({ symbol, count }))

        // Daily counts: last 14 days
        const daily_counts: { date: string; count: number }[] = []
        for (let i = 13; i >= 0; i--) {
          const d = new Date(now - i * dayMs)
          const dateStr = d.toISOString().slice(0, 10)
          const count = s.history.filter((h) => h.fired_at.startsWith(dateStr)).length
          daily_counts.push({ date: dateStr, count })
        }

        return {
          alertStats: { total_today, total_week, total_month, top_symbols, daily_counts },
        }
      })
    }
  },
  updateNotificationPrefs: (partial) =>
    set((s) => {
      const next = { ...s.notificationPrefs, ...partial }
      try { localStorage.setItem('alertNotificationPrefs', JSON.stringify(next)) } catch { /* ignore */ }
      return { notificationPrefs: next }
    }),
}))

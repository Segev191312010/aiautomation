import { create } from 'zustand'
import type {
  DiagnosticIndicator,
  DiagnosticMarketMap,
  DiagnosticNewsArticle,
  DiagnosticOverview,
  DiagnosticRefreshRun,
  DiagnosticSectorProjection,
} from '@/types'
import * as api from '@/services/api'

interface DiagnosticsState {
  enabled: boolean
  loading: boolean
  error: string | null
  lookbackDays: 90 | 180 | 365
  overview: DiagnosticOverview | null
  indicators: DiagnosticIndicator[]
  marketMap: DiagnosticMarketMap[]
  projections: DiagnosticSectorProjection | null
  news: DiagnosticNewsArticle[]
  refreshRun: DiagnosticRefreshRun | null
  refreshing: boolean
  lastFetched: number | null

  setEnabled: (enabled: boolean) => void
  setLookbackDays: (days: 90 | 180 | 365) => void
  loadAll: () => Promise<void>
  refreshNow: () => Promise<void>
  pollRefreshRun: (runId: number) => Promise<void>
  clearError: () => void
}

export const useDiagnosticsStore = create<DiagnosticsState>((set, get) => ({
  enabled: false,
  loading: false,
  error: null,
  lookbackDays: 90,
  overview: null,
  indicators: [],
  marketMap: [],
  projections: null,
  news: [],
  refreshRun: null,
  refreshing: false,
  lastFetched: null,

  setEnabled: (enabled) => set({ enabled }),
  setLookbackDays: (days) => set({ lookbackDays: days }),
  clearError: () => set({ error: null }),

  loadAll: async () => {
    const { enabled, lookbackDays } = get()
    if (!enabled) return
    set({ loading: true, error: null })
    try {
      const [overview, indicators, marketMap, projections, news] = await Promise.all([
        api.fetchDiagnosticsOverview(lookbackDays),
        api.fetchDiagnosticsIndicators(),
        api.fetchDiagnosticsMarketMap(5),
        api.fetchDiagnosticsSectorProjectionsLatest(lookbackDays),
        api.fetchDiagnosticsNews(24, 200),
      ])
      set({
        overview,
        indicators,
        marketMap,
        projections,
        news,
        lastFetched: Date.now(),
      })
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Diagnostics load failed'
      set({ error: msg })
    } finally {
      set({ loading: false })
    }
  },

  refreshNow: async () => {
    const { enabled } = get()
    if (!enabled) return
    set({ refreshing: true, error: null })
    try {
      const resp = await api.runDiagnosticsRefresh()
      if (resp.status === 202) {
        set({ refreshRun: { run_id: resp.data.run_id, status: resp.data.status } })
      } else {
        set({
          refreshRun: {
            run_id: resp.data.run_id,
            status: 'locked',
            locked_by: resp.data.locked_by,
            lock_expires_at: resp.data.lock_expires_at,
          },
        })
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Diagnostics refresh failed'
      set({ error: msg, refreshing: false })
    }
  },

  pollRefreshRun: async (runId: number) => {
    try {
      const run = await api.fetchDiagnosticsRefreshRun(runId)
      const status = run.status.toLowerCase()
      const nowTs = Math.floor(Date.now() / 1000)
      const lockExpired = status === 'running'
        && typeof run.lock_expires_at === 'number'
        && run.lock_expires_at <= nowTs
      const done = status === 'completed' || status === 'failed' || lockExpired
      set({
        refreshRun: run,
        refreshing: !done,
        error: done
          ? (
              status === 'failed'
                ? run.error ?? 'Diagnostics refresh failed'
                : lockExpired
                  ? 'Refresh lock expired; re-sync required.'
                  : null
            )
          : null,
      })
      if (done && status === 'completed') {
        await get().loadAll()
      } else if (done && lockExpired) {
        await get().loadAll()
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Refresh run polling failed'
      set({ error: msg, refreshing: false })
    }
  },
}))

import { useEffect } from 'react'
import { useBotStore, useDiagnosticsStore } from '@/store'

const DIAG_POLL_MS = 5 * 60 * 1000
const REFRESH_POLL_MS = 2 * 1000

export function useDiagnostics(): void {
  const diagnosticsEnabled = useBotStore((s) => s.status?.features?.market_diagnostics ?? false)
  const setEnabled = useDiagnosticsStore((s) => s.setEnabled)
  const loadAll = useDiagnosticsStore((s) => s.loadAll)
  const refreshing = useDiagnosticsStore((s) => s.refreshing)
  const refreshRun = useDiagnosticsStore((s) => s.refreshRun)
  const pollRefreshRun = useDiagnosticsStore((s) => s.pollRefreshRun)

  useEffect(() => {
    setEnabled(diagnosticsEnabled)
  }, [diagnosticsEnabled, setEnabled])

  useEffect(() => {
    if (!diagnosticsEnabled) return
    loadAll()
    const t = setInterval(() => {
      loadAll()
    }, DIAG_POLL_MS)
    return () => clearInterval(t)
  }, [diagnosticsEnabled, loadAll])

  useEffect(() => {
    if (!diagnosticsEnabled) return
    const onVisible = () => {
      if (document.visibilityState === 'visible') {
        loadAll()
      }
    }
    const onOnline = () => loadAll()
    document.addEventListener('visibilitychange', onVisible)
    window.addEventListener('online', onOnline)
    return () => {
      document.removeEventListener('visibilitychange', onVisible)
      window.removeEventListener('online', onOnline)
    }
  }, [diagnosticsEnabled, loadAll])

  useEffect(() => {
    if (!diagnosticsEnabled || !refreshing || !refreshRun?.run_id) return
    const runId = refreshRun.run_id
    const t = setInterval(() => {
      pollRefreshRun(runId)
    }, REFRESH_POLL_MS)
    return () => clearInterval(t)
  }, [diagnosticsEnabled, refreshing, refreshRun?.run_id, pollRefreshRun])
}

import { useEffect } from 'react'
import { useStockProfileStore } from '@/store'

const PROFILE_POLL_MS = 15 * 60_000 // 15 min — matches backend cache TTL

export function useStockProfile(symbol: string): void {
  const loadAll = useStockProfileStore((s) => s.loadAll)

  // Load on mount + symbol change
  useEffect(() => {
    if (!symbol) return
    loadAll(symbol)
    const t = setInterval(() => loadAll(symbol), PROFILE_POLL_MS)
    return () => clearInterval(t)
  }, [symbol, loadAll])

  // Visibility-aware reload
  useEffect(() => {
    if (!symbol) return
    const onVisible = () => {
      if (document.visibilityState === 'visible') loadAll(symbol)
    }
    const onOnline = () => loadAll(symbol)
    document.addEventListener('visibilitychange', onVisible)
    window.addEventListener('online', onOnline)
    return () => {
      document.removeEventListener('visibilitychange', onVisible)
      window.removeEventListener('online', onOnline)
    }
  }, [symbol, loadAll])
}

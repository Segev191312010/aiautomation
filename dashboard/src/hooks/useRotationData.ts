import { useEffect, useState, useCallback } from 'react'
import type { SectorRotation, SectorHeatmapRow } from '@/types'
import { fetchSectorRotation, fetchSectorHeatmap } from '@/services/api'

const AUTO_REFRESH_MS = 5 * 60 * 1000

/**
 * Sector rotation data hook — wraps REST calls + auto-refresh timer.
 * Unlike other hooks, this doesn't wrap a store since there's no dedicated rotation store.
 */
export function useRotationData(lookbackDays = 90) {
  const [rotation, setRotation] = useState<SectorRotation[]>([])
  const [heatmap, setHeatmap] = useState<SectorHeatmapRow[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [lastUpdate, setLastUpdate] = useState<Date | null>(null)

  const loadData = useCallback(async () => {
    try {
      setLoading(true)
      setError(null)
      const [rot, heat] = await Promise.all([
        fetchSectorRotation(lookbackDays),
        fetchSectorHeatmap(),
      ])
      setRotation(rot)
      setHeatmap(heat)
      setLastUpdate(new Date())
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load sector data')
    } finally {
      setLoading(false)
    }
  }, [lookbackDays])

  useEffect(() => {
    loadData()
    const t = setInterval(loadData, AUTO_REFRESH_MS)
    return () => clearInterval(t)
  }, [loadData])

  return { rotation, heatmap, loading, error, lastUpdate, refresh: loadData }
}

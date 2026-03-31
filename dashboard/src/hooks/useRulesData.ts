import { useEffect } from 'react'
import { useBotStore } from '@/store'

/**
 * Thin adapter over useBotStore — triggers rules load on mount.
 * Store owns rules state + CRUD actions.
 */
export function useRulesData() {
  const rules = useBotStore((s) => s.rules)
  const setRules = useBotStore((s) => s.setRules)
  const loading = useBotStore((s) => s.status === null)

  useEffect(() => {
    import('@/services/api').then((api) => {
      api.fetchRules().then(setRules).catch(() => {})
    })
  }, [setRules])

  return {
    rules,
    loading,
    refresh: () => import('@/services/api').then((api) => api.fetchRules().then(setRules)),
  }
}

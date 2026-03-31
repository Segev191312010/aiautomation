import { useEffect } from 'react'
import { useAutopilotStore } from '@/store'

/**
 * Thin adapter over useAutopilotStore — triggers load on mount.
 * Store owns guardrails, AI status, audit log, performance, costs.
 */
export function useAutopilotData() {
  const store = useAutopilotStore()

  useEffect(() => {
    store.fetchGuardrails()
    store.fetchAIStatus()
    store.fetchAuditLog()
    store.fetchCostReport()
    store.fetchEconomicReport()
    store.fetchLearningMetrics()
  }, [store.fetchGuardrails, store.fetchAIStatus, store.fetchAuditLog,
      store.fetchCostReport, store.fetchEconomicReport, store.fetchLearningMetrics])

  return {
    guardrails: store.guardrails,
    aiStatus: store.aiStatus,
    auditLog: store.auditLog,
    costReport: store.costReport,
    economicReport: store.economicReport,
    learningMetrics: store.learningMetrics,
    error: store.error,
  }
}

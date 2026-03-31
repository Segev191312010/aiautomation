import { create } from 'zustand'
import type {
  AIStatus,
  AuditLogEntry,
  AutopilotConfig,
  CostReport,
  EconomicReport,
  LearningMetrics,
} from '@/types/advisor'
import * as api from '@/services/api'

interface AutopilotState {
  guardrails: AutopilotConfig | null
  auditLog: AuditLogEntry[]
  auditLogTotal: number
  aiStatus: AIStatus | null
  learningMetrics: LearningMetrics | null
  costReport: CostReport | null
  economicReport: EconomicReport | null
  learningWindow: 7 | 30 | 90
  error: string | null

  fetchGuardrails: () => Promise<void>
  updateGuardrails: (config: Partial<AutopilotConfig>) => Promise<void>
  emergencyStop: () => Promise<void>
  fetchAuditLog: (limit?: number, offset?: number) => Promise<void>
  revertAction: (id: number) => Promise<void>
  fetchAIStatus: () => Promise<void>
  fetchLearningMetrics: () => Promise<void>
  fetchCostReport: () => Promise<void>
  fetchEconomicReport: () => Promise<void>
  setLearningWindow: (days: 7 | 30 | 90) => void
}

export const useAutopilotStore = create<AutopilotState>((set, get) => ({
  guardrails: null,
  auditLog: [],
  auditLogTotal: 0,
  aiStatus: null,
  learningMetrics: null,
  costReport: null,
  economicReport: null,
  learningWindow: 30,
  error: null,

  fetchGuardrails: async () => {
    try {
      const config = await api.fetchGuardrails()
      set({ guardrails: config })
    } catch (err) {
      set({ error: err instanceof Error ? err.message : 'Failed to load autopilot config' })
    }
  },

  updateGuardrails: async (config) => {
    try {
      const updated = await api.updateGuardrails(config)
      set({ guardrails: updated })
    } catch (err) {
      set({ error: err instanceof Error ? err.message : 'Failed to update autopilot config' })
    }
  },

  emergencyStop: async () => {
    try {
      await api.postEmergencyStop()
      await get().fetchGuardrails()
      await get().fetchAIStatus()
    } catch (err) {
      set({ error: err instanceof Error ? err.message : 'Emergency stop failed' })
    }
  },

  fetchAuditLog: async (limit = 50, offset = 0) => {
    try {
      const data = await api.fetchAuditLog(limit, offset)
      set({ auditLog: data.entries, auditLogTotal: data.total })
    } catch (err) {
      set({ error: err instanceof Error ? err.message : 'Failed to load audit log' })
    }
  },

  revertAction: async (id) => {
    try {
      await api.revertAIAction(id)
      await get().fetchAuditLog()
    } catch (err) {
      set({ error: err instanceof Error ? err.message : 'Failed to revert action' })
    }
  },

  fetchAIStatus: async () => {
    try {
      const status = await api.fetchAIStatus()
      set({ aiStatus: status })
    } catch {
      // Silently fail — status bar can handle null
    }
  },

  // ── Shadow Mode ──────────────────────────────────────────────────────────
  // ── Learning + Economics ──────────────────────────────────────────────────
  fetchLearningMetrics: async () => {
    try {
      const metrics = await api.fetchLearningMetrics(get().learningWindow)
      set({ learningMetrics: metrics })
    } catch (err) {
      set({ error: err instanceof Error ? err.message : 'Failed to load learning metrics' })
    }
  },

  fetchCostReport: async () => {
    try {
      const report = await api.fetchAICosts(30)
      set({ costReport: report })
    } catch (err) {
      set({ error: err instanceof Error ? err.message : 'Failed to load cost report' })
    }
  },

  fetchEconomicReport: async () => {
    try {
      const report = await api.fetchEconomicReport(30)
      set({ economicReport: report })
    } catch (err) {
      set({ error: err instanceof Error ? err.message : 'Failed to load economic report' })
    }
  },

  setLearningWindow: (days) => {
    set({ learningWindow: days })
    get().fetchLearningMetrics()
  },
}))

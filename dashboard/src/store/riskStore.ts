import { create } from 'zustand'
import type {
  PortfolioRisk,
  RiskCheckResult,
  RiskEvent,
  RiskLimits,
  RiskSettings,
} from '@/types'
import * as api from '@/services/api'

const DEFAULT_RISK_SETTINGS: RiskSettings = {
  max_position_size_pct: 20,
  daily_loss_limit:      2_000,
  drawdown_limit_pct:    10,
  max_open_positions:    10,
  max_sector_pct:        30,
  max_corr_threshold:    0.8,
}

interface RiskState {
  riskLimits:    RiskLimits | null
  riskChecks:    RiskCheckResult[]
  riskEvents:    RiskEvent[]
  portfolioRisk: PortfolioRisk | null
  riskSettings:  RiskSettings
  loading:       boolean
  error:         string | null

  fetchRiskLimits:    () => Promise<void>
  fetchRiskEvents:    () => Promise<void>
  updateRiskSettings: (partial: Partial<RiskSettings>) => void
  computeRiskChecks:  (limits: RiskLimits) => void
}

export const useRiskStore = create<RiskState>((set, get) => ({
  riskLimits:    null,
  riskChecks:    [],
  riskEvents:    [],
  portfolioRisk: null,
  riskSettings:  DEFAULT_RISK_SETTINGS,
  loading:       false,
  error:         null,

  fetchRiskLimits: async () => {
    set({ loading: true })
    try {
      const limits = await api.fetchRiskLimits()
      set({ riskLimits: limits })
      get().computeRiskChecks(limits)
    } catch (err) {
      set({ error: err instanceof Error ? err.message : 'Risk load failed' })
    } finally {
      set({ loading: false })
    }
  },

  fetchRiskEvents: async () => {
    // Backend endpoint not yet implemented — stub returns empty array
    set({ riskEvents: [] })
  },

  updateRiskSettings: (partial) =>
    set((s) => ({ riskSettings: { ...s.riskSettings, ...partial } })),

  computeRiskChecks: (limits) => {
    const checks: RiskCheckResult[] = limits.limits.map((item) => {
      const ratio = item.limit > 0 ? item.used / item.limit : 0
      const status: RiskCheckResult['status'] =
        ratio >= 1 ? 'BREACH' : ratio >= 0.8 ? 'WARN' : 'OK'
      return {
        name:        item.label,
        current:     item.used,
        limit:       item.limit,
        unit:        item.unit,
        status,
        description: `${item.label}: ${item.used}${item.unit} of ${item.limit}${item.unit} limit`,
      }
    })
    set({ riskChecks: checks })
  },
}))

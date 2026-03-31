import { create } from 'zustand'
import type {
  BotStatus,
  Rule,
  SystemStatus,
} from '@/types'

interface BotCycleStats {
  rulesEnabled:   number
  rulesChecked:   number
  symbolsScanned: number
  signals:        number
  lastRun:        string | null
  nextRun:        string | null
}

interface BotState {
  status:        SystemStatus | null
  botStatus:     BotStatus
  rules:         Rule[]
  ibkrConnected: boolean
  simMode:       boolean
  botRunning:    boolean
  cycleStats:    BotCycleStats

  setStatus:     (s: SystemStatus) => void
  setBotStatus:  (s: BotStatus) => void
  setRules:      (r: Rule[]) => void
  updateRule:    (r: Rule) => void
  setIBKR:       (v: boolean) => void
  setBotRunning: (v: boolean) => void
  setCycleStats: (s: Partial<BotCycleStats>) => void
}

export const useBotStore = create<BotState>((set) => ({
  status:        null,
  botStatus:     { running: false },
  rules:         [],
  ibkrConnected: false,
  simMode:       false,
  botRunning:    false,
  cycleStats:    { rulesEnabled: 0, rulesChecked: 0, symbolsScanned: 0, signals: 0, lastRun: null, nextRun: null },

  setStatus: (s) =>
    set({
      status:        s,
      ibkrConnected: s.ibkr_connected,
      simMode:       s.sim_mode,
      botRunning:    s.bot_running,
    }),
  setBotStatus:  (s) => set({ botStatus: s, botRunning: s.running }),
  setRules:      (r) => set({ rules: r }),
  updateRule:    (r) =>
    set((s) => ({ rules: s.rules.map((x) => (x.id === r.id ? r : x)) })),
  setIBKR:       (v) => set({ ibkrConnected: v }),
  setBotRunning: (v) => set({ botRunning: v }),
  setCycleStats: (s) => set((prev) => ({ cycleStats: { ...prev.cycleStats, ...s } })),
}))

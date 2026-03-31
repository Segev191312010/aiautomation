import { create } from 'zustand'
import type {
  BacktestHistoryItem,
  BacktestResult,
  Condition,
  ExitMode,
} from '@/types'

const DEFAULT_ENTRY: Condition[] = [
  { indicator: 'RSI', params: { length: 14 }, operator: '<', value: 30 },
]
const DEFAULT_EXIT: Condition[] = [
  { indicator: 'RSI', params: { length: 14 }, operator: '>', value: 70 },
]

interface BacktestState {
  // Strategy configuration
  entryConditions: Condition[]
  exitConditions: Condition[]
  conditionLogic: 'AND' | 'OR'
  symbol: string
  period: string
  interval: string
  initialCapital: number
  positionSizePct: number
  stopLossPct: number
  takeProfitPct: number
  exitMode: ExitMode
  atrStopMult: number
  atrTrailMult: number
  startDate: string | null
  endDate: string | null

  // Results
  result: BacktestResult | null
  loading: boolean
  error: string | null

  // History
  savedBacktests: BacktestHistoryItem[]

  // Actions
  setEntryConditions: (c: Condition[]) => void
  setExitConditions: (c: Condition[]) => void
  setConditionLogic: (l: 'AND' | 'OR') => void
  setSymbol: (s: string) => void
  setPeriod: (p: string) => void
  setInterval: (i: string) => void
  setInitialCapital: (v: number) => void
  setPositionSizePct: (v: number) => void
  setStopLossPct: (v: number) => void
  setTakeProfitPct: (v: number) => void
  setExitMode: (m: ExitMode) => void
  setAtrStopMult: (v: number) => void
  setAtrTrailMult: (v: number) => void
  setStartDate: (d: string | null) => void
  setEndDate: (d: string | null) => void
  setResult: (r: BacktestResult | null) => void
  setLoading: (v: boolean) => void
  setError: (e: string | null) => void
  setSavedBacktests: (b: BacktestHistoryItem[]) => void
  reset: () => void
}

export const useBacktestStore = create<BacktestState>((set) => ({
  entryConditions: DEFAULT_ENTRY,
  exitConditions: DEFAULT_EXIT,
  conditionLogic: 'AND',
  symbol: 'AAPL',
  period: '2y',
  interval: '1d',
  initialCapital: 100_000,
  positionSizePct: 100,
  stopLossPct: 0,
  takeProfitPct: 0,
  exitMode: 'simple',
  atrStopMult: 3.0,
  atrTrailMult: 2.0,
  startDate: null,
  endDate: null,

  result: null,
  loading: false,
  error: null,

  savedBacktests: [],

  setEntryConditions: (c) => set({ entryConditions: c }),
  setExitConditions: (c) => set({ exitConditions: c }),
  setConditionLogic: (l) => set({ conditionLogic: l }),
  setSymbol: (s) => set({ symbol: s }),
  setPeriod: (p) => set({ period: p }),
  setInterval: (i) => set({ interval: i }),
  setInitialCapital: (v) => set({ initialCapital: v }),
  setPositionSizePct: (v) => set({ positionSizePct: v }),
  setStopLossPct: (v) => set({ stopLossPct: v }),
  setTakeProfitPct: (v) => set({ takeProfitPct: v }),
  setExitMode: (m) => set({ exitMode: m }),
  setAtrStopMult: (v) => set({ atrStopMult: v }),
  setAtrTrailMult: (v) => set({ atrTrailMult: v }),
  setStartDate: (d) => set({ startDate: d }),
  setEndDate: (d) => set({ endDate: d }),
  setResult: (r) => set({ result: r }),
  setLoading: (v) => set({ loading: v }),
  setError: (e) => set({ error: e }),
  setSavedBacktests: (b) => set({ savedBacktests: b }),
  reset: () => set({
    entryConditions: DEFAULT_ENTRY,
    exitConditions: DEFAULT_EXIT,
    conditionLogic: 'AND',
    symbol: 'AAPL',
    period: '2y',
    interval: '1d',
    initialCapital: 100_000,
    positionSizePct: 100,
    stopLossPct: 0,
    takeProfitPct: 0,
    exitMode: 'simple',
    atrStopMult: 3.0,
    atrTrailMult: 2.0,
    startDate: null,
    endDate: null,
    result: null,
    error: null,
    loading: false,
  }),
}))

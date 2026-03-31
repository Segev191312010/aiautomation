import { create } from 'zustand'
import type {
  OHLCVBar,
  PlaybackState,
  SimAccountState,
  SimOrderRecord,
  SimPosition,
} from '@/types'

interface SimState {
  simAccount:   SimAccountState | null
  simPositions: SimPosition[]
  simOrders:    SimOrderRecord[]
  playback:     PlaybackState
  replayBars:   OHLCVBar[]          // bars received during active replay

  setSimAccount:   (a: SimAccountState | null) => void
  setSimPositions: (p: SimPosition[]) => void
  setSimOrders:    (o: SimOrderRecord[]) => void
  setPlayback:     (p: PlaybackState) => void
  pushReplayBar:   (bar: OHLCVBar) => void
  resetReplayBars: () => void
}

export const useSimStore = create<SimState>((set) => ({
  simAccount:   null,
  simPositions: [],
  simOrders:    [],
  playback: {
    active:        false,
    symbol:        '',
    speed:         1,
    current_index: 0,
    total_bars:    0,
    progress:      0,
  },
  replayBars: [],

  setSimAccount:   (a) => set({ simAccount: a }),
  setSimPositions: (p) => set({ simPositions: p }),
  setSimOrders:    (o) => set({ simOrders: o }),
  setPlayback:     (p) => set({ playback: p }),
  pushReplayBar:   (bar) =>
    set((s) => ({ replayBars: [...s.replayBars, bar].slice(-1000) })),
  resetReplayBars: () => set({ replayBars: [] }),
}))

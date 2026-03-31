import { create } from 'zustand'
import type {
  ActivityEvent,
  AnyAccount,
  OpenOrder,
  Position,
  SimPosition,
  Trade,
} from '@/types'

interface AccountState {
  account:   AnyAccount | null
  positions: (Position | SimPosition)[]
  orders:    OpenOrder[]
  trades:    Trade[]
  activityFeed: ActivityEvent[]
  loading:   boolean

  setAccount:   (a: AnyAccount | null) => void
  setPositions: (p: (Position | SimPosition)[]) => void
  setOrders:    (o: OpenOrder[]) => void
  addTrade:     (t: Trade) => void
  setTrades:    (t: Trade[]) => void
  pushActivity: (e: ActivityEvent) => void
  setLoading:   (v: boolean) => void
}

export const useAccountStore = create<AccountState>((set) => ({
  account:   null,
  positions: [],
  orders:    [],
  trades:    [],
  activityFeed: [],
  loading:   false,

  setAccount:   (a) => set({ account: a }),
  setPositions: (p) => set({ positions: p }),
  setOrders:    (o) => set({ orders: o }),
  addTrade:     (t) => set((s) => ({ trades: [t, ...s.trades].slice(0, 500) })),
  setTrades:    (t) => set({ trades: t }),
  pushActivity: (e) => set((s) => {
    // Dedup: skip if same symbol+rule within last 5 seconds
    const dup = s.activityFeed.find(
      (a) => a.symbol === e.symbol && a.ruleName === e.ruleName &&
        Math.abs(new Date(a.timestamp).getTime() - new Date(e.timestamp).getTime()) < 5000
    )
    if (dup) return {}
    return { activityFeed: [e, ...s.activityFeed].slice(0, 20) }
  }),
  setLoading:   (v) => set({ loading: v }),
}))

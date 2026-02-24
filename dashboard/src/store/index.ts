/**
 * Zustand stores — single source of truth for the whole dashboard.
 *
 * Stores:
 *  useMarketStore  — quotes, bars, watchlists, selected symbol
 *  useAccountStore — account KPIs, positions, orders, trades
 *  useBotStore     — bot status, IBKR connection, rules
 *  useSimStore     — sim account, replay playback state
 *  useUIStore      — sidebar, active route, theme, comparison mode
 */
import { create } from 'zustand'
import type {
  AnyAccount,
  AppRoute,
  BotStatus,
  MarketQuote,
  OHLCVBar,
  OpenOrder,
  PlaybackState,
  Position,
  Rule,
  SimAccountState,
  SimOrderRecord,
  SimPosition,
  SortDir,
  SortField,
  SystemStatus,
  Trade,
  UserSettings,
  Watchlist,
} from '@/types'
import type { IndicatorId } from '@/utils/indicators'

// ── Market store ─────────────────────────────────────────────────────────────

interface MarketState {
  quotes:           Record<string, MarketQuote>
  bars:             Record<string, OHLCVBar[]>
  compBars:         Record<string, OHLCVBar[]>   // comparison overlay bars
  selectedSymbol:   string
  compSymbol:       string                        // symbol overlaid on chart
  compMode:         boolean
  watchlists:       Watchlist[]
  activeWatchlist:  string                         // watchlist id
  sortField:        SortField
  sortDir:          SortDir
  loading:          boolean
  lastUpdated:      number | null

  selectedIndicators: IndicatorId[]

  setQuotes:          (quotes: MarketQuote[]) => void
  updateQuote:        (q: MarketQuote) => void
  updateQuotePrice:   (symbol: string, price: number) => void
  setBars:            (symbol: string, bars: OHLCVBar[]) => void
  setCompBars:        (symbol: string, bars: OHLCVBar[]) => void
  setSelectedSymbol:  (symbol: string) => void
  setCompSymbol:      (symbol: string) => void
  toggleCompMode:     () => void
  toggleIndicator:    (id: IndicatorId) => void
  addWatchlist:       (name: string) => void
  removeWatchlist:    (id: string) => void
  addToWatchlist:     (listId: string, symbol: string) => void
  removeFromWatchlist:(listId: string, symbol: string) => void
  setActiveWatchlist: (id: string) => void
  setSort:            (field: SortField, dir: SortDir) => void
  setLoading:         (v: boolean) => void
}

export const useMarketStore = create<MarketState>((set, get) => ({
  quotes:          {},
  bars:            {},
  compBars:        {},
  selectedSymbol:  'BTC-USD',
  compSymbol:      '',
  compMode:        false,
  watchlists: [
    { id: 'default', name: 'Watchlist', symbols: ['BTC-USD', 'ETH-USD', 'AAPL', 'TSLA', 'SPY', 'QQQ', 'NVDA'] },
    { id: 'crypto',  name: 'Crypto',    symbols: ['BTC-USD', 'ETH-USD', 'SOL-USD'] },
    { id: 'tech',    name: 'Tech',      symbols: ['AAPL', 'MSFT', 'GOOGL', 'META', 'NVDA', 'AMZN'] },
  ],
  activeWatchlist:    'default',
  sortField:          'change_pct',
  sortDir:            'desc',
  loading:            false,
  lastUpdated:        null,
  selectedIndicators: [],

  setQuotes: (quotes) => {
    const map: Record<string, MarketQuote> = {}
    quotes.forEach((q) => { map[q.symbol] = q })
    set({ quotes: { ...get().quotes, ...map }, lastUpdated: Date.now() })
  },
  updateQuote: (q) =>
    set((s) => ({ quotes: { ...s.quotes, [q.symbol]: q } })),
  updateQuotePrice: (symbol, price) =>
    set((s) => {
      const existing = s.quotes[symbol]
      if (!existing) return {}
      return {
        quotes: {
          ...s.quotes,
          [symbol]: { ...existing, price, last_update: new Date().toISOString() },
        },
      }
    }),
  setBars: (symbol, bars) =>
    set((s) => ({ bars: { ...s.bars, [symbol]: bars } })),
  setCompBars: (symbol, bars) =>
    set((s) => ({ compBars: { ...s.compBars, [symbol]: bars } })),
  setSelectedSymbol: (symbol) => set({ selectedSymbol: symbol }),
  setCompSymbol:     (symbol) => set({ compSymbol: symbol }),
  toggleCompMode:    () => set((s) => ({ compMode: !s.compMode })),
  toggleIndicator:   (id) =>
    set((s) => ({
      selectedIndicators: s.selectedIndicators.includes(id)
        ? s.selectedIndicators.filter((x) => x !== id)
        : [...s.selectedIndicators, id],
    })),

  addWatchlist: (name) =>
    set((s) => ({
      watchlists: [
        ...s.watchlists,
        { id: crypto.randomUUID(), name, symbols: [] },
      ],
    })),
  removeWatchlist: (id) =>
    set((s) => ({ watchlists: s.watchlists.filter((w) => w.id !== id) })),
  addToWatchlist: (listId, symbol) =>
    set((s) => ({
      watchlists: s.watchlists.map((w) =>
        w.id === listId && !w.symbols.includes(symbol)
          ? { ...w, symbols: [...w.symbols, symbol] }
          : w,
      ),
    })),
  removeFromWatchlist: (listId, symbol) =>
    set((s) => ({
      watchlists: s.watchlists.map((w) =>
        w.id === listId ? { ...w, symbols: w.symbols.filter((s) => s !== symbol) } : w,
      ),
    })),
  setActiveWatchlist: (id) => set({ activeWatchlist: id }),
  setSort: (field, dir) => set({ sortField: field, sortDir: dir }),
  setLoading: (v) => set({ loading: v }),
}))

// ── Account store ─────────────────────────────────────────────────────────────

interface AccountState {
  account:   AnyAccount | null
  positions: (Position | SimPosition)[]
  orders:    OpenOrder[]
  trades:    Trade[]
  loading:   boolean

  setAccount:   (a: AnyAccount | null) => void
  setPositions: (p: (Position | SimPosition)[]) => void
  setOrders:    (o: OpenOrder[]) => void
  addTrade:     (t: Trade) => void
  setTrades:    (t: Trade[]) => void
  setLoading:   (v: boolean) => void
}

export const useAccountStore = create<AccountState>((set) => ({
  account:   null,
  positions: [],
  orders:    [],
  trades:    [],
  loading:   false,

  setAccount:   (a) => set({ account: a }),
  setPositions: (p) => set({ positions: p }),
  setOrders:    (o) => set({ orders: o }),
  addTrade:     (t) => set((s) => ({ trades: [t, ...s.trades].slice(0, 500) })),
  setTrades:    (t) => set({ trades: t }),
  setLoading:   (v) => set({ loading: v }),
}))

// ── Bot store ─────────────────────────────────────────────────────────────────

interface BotState {
  status:        SystemStatus | null
  botStatus:     BotStatus
  rules:         Rule[]
  ibkrConnected: boolean
  simMode:       boolean
  mockMode:      boolean
  botRunning:    boolean

  setStatus:     (s: SystemStatus) => void
  setBotStatus:  (s: BotStatus) => void
  setRules:      (r: Rule[]) => void
  updateRule:    (r: Rule) => void
  setIBKR:       (v: boolean) => void
  setBotRunning: (v: boolean) => void
}

export const useBotStore = create<BotState>((set) => ({
  status:        null,
  botStatus:     { running: false },
  rules:         [],
  ibkrConnected: false,
  simMode:       false,
  mockMode:      true,
  botRunning:    false,

  setStatus: (s) =>
    set({
      status:        s,
      ibkrConnected: s.ibkr_connected,
      simMode:       s.sim_mode,
      mockMode:      s.mock_mode,
      botRunning:    s.bot_running,
    }),
  setBotStatus:  (s) => set({ botStatus: s, botRunning: s.running }),
  setRules:      (r) => set({ rules: r }),
  updateRule:    (r) =>
    set((s) => ({ rules: s.rules.map((x) => (x.id === r.id ? r : x)) })),
  setIBKR:       (v) => set({ ibkrConnected: v }),
  setBotRunning: (v) => set({ botRunning: v }),
}))

// ── Simulation store ──────────────────────────────────────────────────────────

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

// ── UI store ──────────────────────────────────────────────────────────────────

interface UIState {
  sidebarCollapsed: boolean
  activeRoute:      AppRoute
  showOrderModal:   boolean
  orderModalSymbol: string

  setSidebarCollapsed: (v: boolean) => void
  toggleSidebar:       () => void
  setRoute:            (r: AppRoute) => void
  openOrderModal:      (symbol?: string) => void
  closeOrderModal:     () => void
}

export const useUIStore = create<UIState>((set) => ({
  sidebarCollapsed: false,
  activeRoute:      'dashboard',
  showOrderModal:   false,
  orderModalSymbol: '',

  setSidebarCollapsed: (v) => set({ sidebarCollapsed: v }),
  toggleSidebar:       () => set((s) => ({ sidebarCollapsed: !s.sidebarCollapsed })),
  setRoute:            (r) => set({ activeRoute: r }),
  openOrderModal:      (symbol = '') => set({ showOrderModal: true, orderModalSymbol: symbol }),
  closeOrderModal:     () => set({ showOrderModal: false }),
}))

// ── Settings store ───────────────────────────────────────────────────────

interface SettingsState {
  settings:    UserSettings | null
  loading:     boolean

  setSettings: (s: UserSettings) => void
  setLoading:  (v: boolean) => void
}

export const useSettingsStore = create<SettingsState>((set) => ({
  settings: null,
  loading:  false,

  setSettings: (s) => set({ settings: s }),
  setLoading:  (v) => set({ loading: v }),
}))

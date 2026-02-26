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
  ChartType,
  Drawing,
  DrawingType,
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
import { DEFAULT_DRAWING_COLOR } from '@/utils/drawingEngine'
import { validateDrawingsMap, validateDrawingsExport } from '@/utils/drawingSchema'

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
  chartType:          ChartType

  setQuotes:          (quotes: MarketQuote[]) => void
  updateQuote:        (q: MarketQuote) => void
  updateQuotePrice:   (symbol: string, price: number) => void
  setBars:            (symbol: string, bars: OHLCVBar[]) => void
  setCompBars:        (symbol: string, bars: OHLCVBar[]) => void
  setSelectedSymbol:  (symbol: string) => void
  setCompSymbol:      (symbol: string) => void
  toggleCompMode:     () => void
  toggleIndicator:    (id: IndicatorId) => void
  setChartType:       (ct: ChartType) => void
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
  chartType:          'candlestick',

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
  setChartType: (ct) => set({ chartType: ct }),

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

// ── Drawing store ───────────────────────────────────────────────────────────

export type SaveStatus = 'idle' | 'saving' | 'saved' | 'error'

interface DrawingState {
  /** All drawings keyed by `${symbol}_${timeframe}`. */
  drawings:          Record<string, Drawing[]>
  activeTool:        DrawingType | null
  selectedDrawingId: string | null
  drawingColor:      string
  saveStatus:        SaveStatus

  /** Undo / redo stacks (dual-stack approach). */
  _undoStack: Array<Record<string, Drawing[]>>
  _redoStack: Array<Record<string, Drawing[]>>

  /** Clipboard for copy/paste. */
  clipboard: Drawing | null

  /** Internal debounce timer. */
  _saveTimer: ReturnType<typeof setTimeout> | null

  // ── Actions ──────────────────────────────────────────────────────────────
  setActiveTool:    (tool: DrawingType | null) => void
  setSelectedDrawing: (id: string | null) => void
  setDrawingColor:  (color: string) => void

  addDrawing:       (drawing: Drawing) => void
  updateDrawing:    (id: string, updates: Partial<Drawing>) => void
  removeDrawing:    (id: string) => void
  clearDrawings:    (key: string) => void
  loadDrawings:     (drawings: Record<string, Drawing[]>) => void

  toggleLock:       (id: string) => void

  undo:             () => void
  redo:             () => void
  _pushHistory:     () => void

  copySelected:     () => void
  paste:            (key: string, crosshairPrice: number, crosshairTime: number) => void

  exportDrawings:   () => string
  importDrawings:   (json: string) => { ok: boolean; errors: string[] }

  _scheduleSave:    () => void
  _flushSave:       () => void
}

/** Max undo history entries. */
const MAX_HISTORY = 50

export const useDrawingStore = create<DrawingState>((set, get) => ({
  drawings:          {},
  activeTool:        null,
  selectedDrawingId: null,
  drawingColor:      DEFAULT_DRAWING_COLOR,
  saveStatus:        'idle' as SaveStatus,

  _undoStack:    [],
  _redoStack:    [],
  clipboard:     null,
  _saveTimer:    null,

  // ── Tool state ───────────────────────────────────────────────────────────

  setActiveTool: (tool) => set({
    activeTool: tool,
    selectedDrawingId: tool ? null : get().selectedDrawingId,
  }),

  setSelectedDrawing: (id) => set({ selectedDrawingId: id }),

  setDrawingColor: (color) => set({ drawingColor: color }),

  // ── CRUD ─────────────────────────────────────────────────────────────────

  addDrawing: (drawing) => {
    get()._pushHistory()
    set((s) => {
      const key = `${drawing.symbol}_${drawing.timeframe}`
      const existing = s.drawings[key] ?? []
      return { drawings: { ...s.drawings, [key]: [...existing, drawing] } }
    })
    get()._scheduleSave()
  },

  updateDrawing: (id, updates) => {
    get()._pushHistory()
    set((s) => {
      const newDrawings: Record<string, Drawing[]> = {}
      for (const [key, list] of Object.entries(s.drawings)) {
        newDrawings[key] = list.map((d) => d.id === id ? { ...d, ...updates } : d)
      }
      return { drawings: newDrawings }
    })
    get()._scheduleSave()
  },

  removeDrawing: (id) => {
    get()._pushHistory()
    set((s) => {
      const newDrawings: Record<string, Drawing[]> = {}
      for (const [key, list] of Object.entries(s.drawings)) {
        newDrawings[key] = list.filter((d) => d.id !== id)
      }
      return {
        drawings: newDrawings,
        selectedDrawingId: s.selectedDrawingId === id ? null : s.selectedDrawingId,
      }
    })
    get()._scheduleSave()
  },

  clearDrawings: (key) => {
    get()._pushHistory()
    set((s) => ({
      drawings: { ...s.drawings, [key]: [] },
      selectedDrawingId: null,
    }))
    get()._scheduleSave()
  },

  loadDrawings: (drawings) => {
    const { valid } = validateDrawingsMap(drawings)
    set({ drawings: valid, _undoStack: [], _redoStack: [] })
  },

  toggleLock: (id) => {
    set((s) => {
      const newDrawings: Record<string, Drawing[]> = {}
      for (const [key, list] of Object.entries(s.drawings)) {
        newDrawings[key] = list.map((d) => d.id === id ? { ...d, locked: !d.locked } : d)
      }
      return { drawings: newDrawings }
    })
    get()._scheduleSave()
  },

  // ── Undo / Redo (dual-stack) ────────────────────────────────────────────
  //
  // _pushHistory: snapshot current state → undoStack, clear redoStack
  // undo: push current → redoStack, pop undoStack → apply
  // redo: push current → undoStack, pop redoStack → apply

  _pushHistory: () => {
    set((s) => {
      const snapshot = JSON.parse(JSON.stringify(s.drawings)) as Record<string, Drawing[]>
      return {
        _undoStack: [...s._undoStack, snapshot].slice(-MAX_HISTORY),
        _redoStack: [],  // new action clears redo
      }
    })
  },

  undo: () => {
    const { _undoStack, drawings } = get()
    if (_undoStack.length === 0) return
    const prev = _undoStack[_undoStack.length - 1]
    const currentSnapshot = JSON.parse(JSON.stringify(drawings)) as Record<string, Drawing[]>
    set({
      drawings: prev,
      _undoStack: _undoStack.slice(0, -1),
      _redoStack: [...get()._redoStack, currentSnapshot],
      selectedDrawingId: null,
    })
    get()._scheduleSave()
  },

  redo: () => {
    const { _redoStack, drawings } = get()
    if (_redoStack.length === 0) return
    const next = _redoStack[_redoStack.length - 1]
    const currentSnapshot = JSON.parse(JSON.stringify(drawings)) as Record<string, Drawing[]>
    set({
      drawings: next,
      _redoStack: _redoStack.slice(0, -1),
      _undoStack: [...get()._undoStack, currentSnapshot],
      selectedDrawingId: null,
    })
    get()._scheduleSave()
  },

  // ── Copy / Paste ─────────────────────────────────────────────────────────

  copySelected: () => {
    const { selectedDrawingId, drawings } = get()
    if (!selectedDrawingId) return
    for (const list of Object.values(drawings)) {
      const found = list.find((d) => d.id === selectedDrawingId)
      if (found) {
        set({ clipboard: JSON.parse(JSON.stringify(found)) as Drawing })
        return
      }
    }
  },

  paste: (key, crosshairPrice, crosshairTime) => {
    const { clipboard } = get()
    if (!clipboard) return

    const [symbol, timeframe] = key.split('_')
    const newDrawing: Drawing = {
      ...JSON.parse(JSON.stringify(clipboard)) as Drawing,
      id: crypto.randomUUID(),
      symbol,
      timeframe,
    }

    // Translate points to crosshair position
    if (newDrawing.type === 'horizontal_line') {
      newDrawing.points = [{ time: crosshairTime, price: crosshairPrice }]
    } else if (newDrawing.points.length === 2) {
      const dx = crosshairTime - newDrawing.points[0].time
      const dy = crosshairPrice - newDrawing.points[0].price
      newDrawing.points = newDrawing.points.map((p) => ({
        time: p.time + dx,
        price: p.price + dy,
      }))
    }

    get().addDrawing(newDrawing)
  },

  // ── Export / Import ──────────────────────────────────────────────────────

  exportDrawings: () => {
    return JSON.stringify({ version: 1, drawings: get().drawings }, null, 2)
  },

  importDrawings: (json) => {
    try {
      const data = JSON.parse(json)
      const result = validateDrawingsExport(data)
      if (result.valid) {
        get()._pushHistory()
        set((s) => {
          // Merge imported drawings with existing
          const merged = { ...s.drawings }
          for (const [key, list] of Object.entries(result.valid!.drawings)) {
            const existing = merged[key] ?? []
            const existingIds = new Set(existing.map((d) => d.id))
            const newOnes = list.filter((d) => !existingIds.has(d.id))
            merged[key] = [...existing, ...newOnes]
          }
          return { drawings: merged }
        })
        get()._scheduleSave()
        return { ok: true, errors: result.errors }
      }
      return { ok: false, errors: result.errors }
    } catch (e) {
      return { ok: false, errors: [`Invalid JSON: ${e instanceof Error ? e.message : String(e)}`] }
    }
  },

  // ── Persistence ──────────────────────────────────────────────────────────

  _scheduleSave: () => {
    const s = get()
    if (s._saveTimer) clearTimeout(s._saveTimer)
    set({ saveStatus: 'idle' })
    const timer = setTimeout(async () => {
      set({ saveStatus: 'saving' })
      try {
        const { updateSettings } = await import('@/services/api')
        await updateSettings({ drawings: get().drawings } as Partial<UserSettings>)
        set({ saveStatus: 'saved' })
        // Reset to idle after 2s
        setTimeout(() => {
          if (get().saveStatus === 'saved') set({ saveStatus: 'idle' })
        }, 2000)
      } catch {
        set({ saveStatus: 'error' })
      }
    }, 2000)
    set({ _saveTimer: timer })
  },

  _flushSave: () => {
    const s = get()
    if (s._saveTimer) {
      clearTimeout(s._saveTimer)
      set({ _saveTimer: null })
    }
    // Synchronous-ish save via sendBeacon for beforeunload
    try {
      const body = JSON.stringify({ drawings: s.drawings })
      navigator.sendBeacon('/api/settings', body)
    } catch {
      // Best effort
    }
  },
}))

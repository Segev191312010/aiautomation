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
  Alert,
  AlertFiredEvent,
  AlertHistory,
  AnyAccount,
  AppRoute,
  BacktestHistoryItem,
  BacktestResult,
  BotStatus,
  DiagnosticIndicator,
  DiagnosticMarketMap,
  DiagnosticNewsArticle,
  DiagnosticOverview,
  DiagnosticRefreshRun,
  DiagnosticSectorProjection,
  ChartType,
  Condition,
  Drawing,
  DrawingType,
  EnrichResult,
  MarketQuote,
  OHLCVBar,
  OpenOrder,
  PlaybackState,
  Position,
  Rule,
  ScanFilter,
  ScanResultRow,
  ScreenerPreset,
  SimAccountState,
  SimOrderRecord,
  SimPosition,
  SortDir,
  SortField,
  StockAnalyst,
  StockAnalystDetail,
  StockCompanyInfo,
  StockEarningsDetail,
  StockEvents,
  StockFinancials,
  StockFinancialStatements,
  StockKeyStats,
  StockNarrative,
  StockOverview,
  StockOwnership,
  StockRatingScorecard,
  StockSplits,
  SystemStatus,
  Trade,
  UniverseInfo,
  UserSettings,
  Watchlist,
} from '@/types'
import type { IndicatorId } from '@/utils/indicators'
import { DEFAULT_DRAWING_COLOR } from '@/utils/drawingEngine'
import { validateDrawingsMap, validateDrawingsExport } from '@/utils/drawingSchema'
import * as api from '@/services/api'

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
  applyLiveQuote: (
    symbol: string,
    price: number,
    time: number,
    barSeconds: number,
    source: 'ibkr' | 'yahoo',
    staleS: number,
    marketState?: 'open' | 'extended' | 'closed' | 'unknown',
  ) => void
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
  selectedSymbol:  'AAPL',
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
    set((s) => ({ quotes: { ...s.quotes, ...map }, lastUpdated: Date.now() }))
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
  applyLiveQuote: (symbol, price, time, barSeconds, source, staleS, marketState) =>
    set((s) => {
      const ts = Number.isFinite(time) ? Math.max(1, Math.floor(time)) : Math.floor(Date.now() / 1000)
      const bucket = Math.max(1, Math.floor(barSeconds))
      const barTime = Math.floor(ts / bucket) * bucket

      const patchSeries = (series: OHLCVBar[] | undefined): OHLCVBar[] | null => {
        if (!series?.length) return null
        const last = series[series.length - 1]
        if (barTime <= 0) return null
        if (last.time === barTime) {
          const updatedLast: OHLCVBar = {
            ...last,
            high: Math.max(last.high, price),
            low: Math.min(last.low, price),
            close: price,
          }
          return [...series.slice(0, -1), updatedLast]
        }
        if (barTime > last.time) {
          const next: OHLCVBar = {
            time: barTime,
            open: last.close,
            high: price,
            low: price,
            close: price,
            volume: 0,
          }
          return [...series, next].slice(-5000)
        }
        return null
      }

      const nextBars = patchSeries(s.bars[symbol])
      const nextCompBars = patchSeries(s.compBars[symbol])
      const prevQuote = s.quotes[symbol]
      const quote: MarketQuote = prevQuote
        ? {
            ...prevQuote,
            symbol,
            price,
            last_update: new Date(ts * 1000).toISOString(),
            live_source: source,
            stale_s: staleS,
            market_state: marketState ?? prevQuote.market_state,
          }
        : {
            symbol,
            price,
            change: 0,
            change_pct: 0,
            last_update: new Date(ts * 1000).toISOString(),
            live_source: source,
            stale_s: staleS,
            market_state: marketState ?? 'unknown',
          }

      return {
        quotes: {
          ...s.quotes,
          [symbol]: quote,
        },
        bars: nextBars ? { ...s.bars, [symbol]: nextBars } : s.bars,
        compBars: nextCompBars ? { ...s.compBars, [symbol]: nextCompBars } : s.compBars,
        lastUpdated: Date.now(),
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
  botRunning:    false,

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

// ── Diagnostics store ─────────────────────────────────────────────────────

interface DiagnosticsState {
  enabled: boolean
  loading: boolean
  error: string | null
  lookbackDays: 90 | 180 | 365
  overview: DiagnosticOverview | null
  indicators: DiagnosticIndicator[]
  marketMap: DiagnosticMarketMap[]
  projections: DiagnosticSectorProjection | null
  news: DiagnosticNewsArticle[]
  refreshRun: DiagnosticRefreshRun | null
  refreshing: boolean
  lastFetched: number | null

  setEnabled: (enabled: boolean) => void
  setLookbackDays: (days: 90 | 180 | 365) => void
  loadAll: () => Promise<void>
  refreshNow: () => Promise<void>
  pollRefreshRun: (runId: number) => Promise<void>
  clearError: () => void
}

export const useDiagnosticsStore = create<DiagnosticsState>((set, get) => ({
  enabled: false,
  loading: false,
  error: null,
  lookbackDays: 90,
  overview: null,
  indicators: [],
  marketMap: [],
  projections: null,
  news: [],
  refreshRun: null,
  refreshing: false,
  lastFetched: null,

  setEnabled: (enabled) => set({ enabled }),
  setLookbackDays: (days) => set({ lookbackDays: days }),
  clearError: () => set({ error: null }),

  loadAll: async () => {
    const { enabled, lookbackDays } = get()
    if (!enabled) return
    set({ loading: true, error: null })
    try {
      const [overview, indicators, marketMap, projections, news] = await Promise.all([
        api.fetchDiagnosticsOverview(lookbackDays),
        api.fetchDiagnosticsIndicators(),
        api.fetchDiagnosticsMarketMap(5),
        api.fetchDiagnosticsSectorProjectionsLatest(lookbackDays),
        api.fetchDiagnosticsNews(24, 200),
      ])
      set({
        overview,
        indicators,
        marketMap,
        projections,
        news,
        lastFetched: Date.now(),
      })
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Diagnostics load failed'
      set({ error: msg })
    } finally {
      set({ loading: false })
    }
  },

  refreshNow: async () => {
    const { enabled } = get()
    if (!enabled) return
    set({ refreshing: true, error: null })
    try {
      const resp = await api.runDiagnosticsRefresh()
      if (resp.status === 202) {
        set({ refreshRun: { run_id: resp.data.run_id, status: resp.data.status } })
      } else {
        set({
          refreshRun: {
            run_id: resp.data.run_id,
            status: 'running',
            locked_by: resp.data.locked_by,
            lock_expires_at: resp.data.lock_expires_at,
          },
        })
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Diagnostics refresh failed'
      set({ error: msg, refreshing: false })
    }
  },

  pollRefreshRun: async (runId: number) => {
    try {
      const run = await api.fetchDiagnosticsRefreshRun(runId)
      const status = run.status.toLowerCase()
      const nowTs = Math.floor(Date.now() / 1000)
      const lockExpired = status === 'running'
        && typeof run.lock_expires_at === 'number'
        && run.lock_expires_at <= nowTs
      const done = status === 'completed' || status === 'failed' || lockExpired
      set({
        refreshRun: run,
        refreshing: !done,
        error: done
          ? (
              status === 'failed'
                ? run.error ?? 'Diagnostics refresh failed'
                : lockExpired
                  ? 'Refresh lock expired; re-sync required.'
                  : null
            )
          : null,
      })
      if (done && status === 'completed') {
        await get().loadAll()
      }
      if (done && lockExpired) {
        await get().loadAll()
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Refresh run polling failed'
      set({ error: msg, refreshing: false })
    }
  },
}))

// ── Screener store ──────────────────────────────────────────────────────────

interface ScreenerState {
  results:          ScanResultRow[]
  skippedSymbols:   string[]
  enriched:         Record<string, EnrichResult>
  presets:          ScreenerPreset[]
  universes:        UniverseInfo[]
  filters:          ScanFilter[]
  selectedUniverse: string
  customSymbols:    string
  interval:         string
  period:           string
  scanning:         boolean
  enriching:        boolean
  presetsLoaded:    boolean

  addFilter:        () => void
  removeFilter:     (index: number) => void
  updateFilter:     (index: number, filter: ScanFilter) => void
  setFilters:       (filters: ScanFilter[]) => void
  setUniverse:      (universe: string) => void
  setCustomSymbols: (symbols: string) => void
  setInterval:      (interval: string) => void
  setPeriod:        (period: string) => void

  loadPresets:      () => Promise<void>
  loadUniverses:    () => Promise<void>
  applyPreset:      (preset: ScreenerPreset) => void
  savePreset:       (name: string) => Promise<void>
  deletePreset:     (id: string) => Promise<void>

  runScan:          () => Promise<void>
  enrichResults:    () => Promise<void>
}

function makeDefaultFilter(): ScanFilter {
  return {
    id: crypto.randomUUID(),
    indicator: 'RSI',
    params: { length: 14 },
    operator: 'LT',
    value: { type: 'number', number: 30 },
  }
}

export const useScreenerStore = create<ScreenerState>((set, get) => ({
  results:          [],
  skippedSymbols:   [],
  enriched:         {},
  presets:          [],
  universes:        [],
  filters:          [makeDefaultFilter()],
  selectedUniverse: 'sp500',
  customSymbols:    '',
  interval:         '1d',
  period:           '1y',
  scanning:         false,
  enriching:        false,
  presetsLoaded:    false,

  addFilter: () =>
    set((s) => ({ filters: [...s.filters, makeDefaultFilter()] })),

  removeFilter: (index) =>
    set((s) => ({
      filters: s.filters.length > 1
        ? s.filters.filter((_, i) => i !== index)
        : s.filters,
    })),

  updateFilter: (index, filter) =>
    set((s) => ({
      filters: s.filters.map((f, i) => (i === index ? filter : f)),
    })),

  setFilters: (filters) => set({ filters }),

  setUniverse: (universe) => set({ selectedUniverse: universe }),

  setCustomSymbols: (symbols) => set({ customSymbols: symbols }),

  setInterval: (interval) => set({ interval }),

  setPeriod: (period) => set({ period }),

  loadPresets: async () => {
    try {
      const presets = await api.fetchScreenerPresets()
      set({ presets, presetsLoaded: true })
    } catch {
      set({ presetsLoaded: true })
    }
  },

  loadUniverses: async () => {
    try {
      const universes = await api.fetchUniverses()
      set({ universes })
    } catch {
      // backend offline
    }
  },

  applyPreset: (preset) =>
    set({ filters: preset.filters.map((f) => ({ ...f, id: crypto.randomUUID() })) }),

  savePreset: async (name) => {
    const preset = await api.saveScreenerPreset(name, get().filters)
    set((s) => ({ presets: [...s.presets, preset] }))
  },

  deletePreset: async (id) => {
    await api.deleteScreenerPreset(id)
    set((s) => ({ presets: s.presets.filter((p) => p.id !== id) }))
  },

  runScan: async () => {
    const { filters, selectedUniverse, customSymbols, interval, period } = get()
    set({ scanning: true, results: [], skippedSymbols: [], enriched: {} })
    try {
      const symbols = selectedUniverse === 'custom'
        ? customSymbols.split(',').map((s) => s.trim()).filter(Boolean)
        : undefined
      const resp = await api.runScan({
        universe: selectedUniverse,
        symbols,
        filters,
        interval,
        period,
        limit: 100,
      })
      set({ results: resp.results, skippedSymbols: resp.skipped_symbols })
      // Auto-enrich (await so scanning spinner covers enrichment)
      if (resp.results.length > 0) {
        await get().enrichResults()
      }
    } finally {
      set({ scanning: false })
    }
  },

  enrichResults: async () => {
    const { results } = get()
    if (results.length === 0) return
    set({ enriching: true })
    try {
      const symbols = results.map((r) => r.symbol)
      const enriched = await api.enrichSymbols(symbols)
      const map: Record<string, EnrichResult> = {}
      enriched.forEach((e) => { map[e.symbol] = e })
      set({ enriched: map })
    } catch {
      // error
    } finally {
      set({ enriching: false })
    }
  },
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
    set((s) => {
      if (s._undoStack.length === 0) return {}
      const prev = s._undoStack[s._undoStack.length - 1]
      const snapshot = JSON.parse(JSON.stringify(s.drawings))
      return {
        drawings: prev,
        _undoStack: s._undoStack.slice(0, -1),
        _redoStack: [...s._redoStack, snapshot],
        selectedDrawingId: null,
      }
    })
    get()._scheduleSave()
  },

  redo: () => {
    set((s) => {
      if (s._redoStack.length === 0) return {}
      const next = s._redoStack[s._redoStack.length - 1]
      const snapshot = JSON.parse(JSON.stringify(s.drawings))
      return {
        drawings: next,
        _redoStack: s._redoStack.slice(0, -1),
        _undoStack: [...s._undoStack, snapshot],
        selectedDrawingId: null,
      }
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
      const blob = new Blob([body], { type: 'application/json' })
      if (!navigator.sendBeacon('/api/settings', blob)) {
        console.warn('sendBeacon failed')
      }
    } catch {
      // Best effort
    }
  },
}))


// ── Backtest store ──────────────────────────────────────────────────────────

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
    result: null,
    error: null,
  }),
}))

// ── Alert store ──────────────────────────────────────────────────────────────

interface AlertState {
  alerts: Alert[]
  history: AlertHistory[]
  unreadCount: number
  recentFired: AlertFiredEvent[]
  loading: boolean

  setAlerts: (a: Alert[]) => void
  setHistory: (h: AlertHistory[]) => void
  pushFired: (e: AlertFiredEvent) => void
  markRead: () => void
  updateAlert: (a: Alert) => void
  removeAlert: (id: string) => void
  setLoading: (v: boolean) => void
  loadAlerts: () => Promise<void>
  loadHistory: () => Promise<void>
}

export const useAlertStore = create<AlertState>((set) => ({
  alerts: [],
  history: [],
  unreadCount: 0,
  recentFired: [],
  loading: false,

  setAlerts: (a) => set({ alerts: a }),
  setHistory: (h) => set({ history: h }),
  pushFired: (e) =>
    set((s) => ({
      recentFired: [e, ...s.recentFired].slice(0, 20),
      unreadCount: s.unreadCount + 1,
    })),
  markRead: () => set({ unreadCount: 0 }),
  updateAlert: (a) => set((s) => ({ alerts: s.alerts.map((x) => (x.id === a.id ? a : x)) })),
  removeAlert: (id) => set((s) => ({ alerts: s.alerts.filter((x) => x.id !== id) })),
  setLoading: (v) => set({ loading: v }),

  loadAlerts: async () => {
    set({ loading: true })
    try {
      const alerts = await api.fetchAlerts()
      set({ alerts, loading: false })
    } catch {
      set({ loading: false })
    }
  },
  loadHistory: async () => {
    try {
      const history = await api.fetchAlertHistory()
      set({ history })
    } catch {
      // backend offline
    }
  },
}))

// ── Stock Profile store ──────────────────────────────────────────────────

interface StockProfileState {
  symbol: string | null
  loading: boolean
  error: string | null
  overview: StockOverview | null
  keyStats: StockKeyStats | null
  financials: StockFinancials | null
  analyst: StockAnalyst | null
  ownership: StockOwnership | null
  events: StockEvents | null
  narrative: StockNarrative | null
  financialStatements: StockFinancialStatements | null
  analystDetail: StockAnalystDetail | null
  ratingScorecard: StockRatingScorecard | null
  companyInfo: StockCompanyInfo | null
  stockSplits: StockSplits | null
  earningsDetail: StockEarningsDetail | null
  lastFetched: number | null

  setSymbol: (symbol: string) => void
  loadAll: (symbol: string) => Promise<void>
  clearProfile: () => void
  clearError: () => void
}

export const useStockProfileStore = create<StockProfileState>((set) => ({
  symbol: null,
  loading: false,
  error: null,
  overview: null,
  keyStats: null,
  financials: null,
  analyst: null,
  ownership: null,
  events: null,
  narrative: null,
  financialStatements: null,
  analystDetail: null,
  ratingScorecard: null,
  companyInfo: null,
  stockSplits: null,
  earningsDetail: null,
  lastFetched: null,

  setSymbol: (symbol) => set({ symbol }),
  clearError: () => set({ error: null }),
  clearProfile: () => set({
    symbol: null, overview: null, keyStats: null, financials: null,
    analyst: null, ownership: null, events: null, narrative: null,
    financialStatements: null, analystDetail: null, ratingScorecard: null,
    companyInfo: null, stockSplits: null, earningsDetail: null,
    lastFetched: null, error: null,
  }),

  loadAll: async (symbol: string) => {
    set({ loading: true, error: null, symbol })
    try {
      const api = await import('@/services/api')
      const results = await Promise.allSettled([
        api.fetchStockOverview(symbol),
        api.fetchStockKeyStats(symbol),
        api.fetchStockFinancials(symbol),
        api.fetchStockAnalyst(symbol),
        api.fetchStockOwnership(symbol),
        api.fetchStockEvents(symbol),
        api.fetchStockNarrative(symbol),
        api.fetchStockFinancialStatements(symbol),
        api.fetchStockAnalystDetail(symbol),
        api.fetchStockRatingScorecard(symbol),
        api.fetchStockCompanyInfo(symbol),
        api.fetchStockSplits(symbol),
        api.fetchStockEarningsDetail(symbol),
      ])
      const val = <T,>(r: PromiseSettledResult<T>): T | null =>
        r.status === 'fulfilled' ? r.value : null
      set({
        overview: val(results[0]),
        keyStats: val(results[1]),
        financials: val(results[2]),
        analyst: val(results[3]),
        ownership: val(results[4]),
        events: val(results[5]),
        narrative: val(results[6]),
        financialStatements: val(results[7]),
        analystDetail: val(results[8]),
        ratingScorecard: val(results[9]),
        companyInfo: val(results[10]),
        stockSplits: val(results[11]),
        earningsDetail: val(results[12]),
        lastFetched: Date.now(),
      })
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Stock profile load failed'
      set({ error: msg })
    } finally {
      set({ loading: false })
    }
  },
}))

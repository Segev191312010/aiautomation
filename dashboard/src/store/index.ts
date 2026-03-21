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
  AlertStats,
  AnyAccount,
  AppRoute,
  BacktestHistoryItem,
  NotificationPrefs,
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
  RuleTemplate,
  RuleVersion,
  ValidationResult,
  RulePerformanceStats,
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
  // Stage 7
  PortfolioAnalytics,
  DailyPnL,
  ExposureBreakdown,
  RiskLimits,
  TradeHistoryRow,
  CorrelationMatrix,
  PnLSummary,
  MatchedTrade,
  SectorExposureRow,
  PortfolioRisk,
  RiskCheckResult,
  RiskEvent,
  RiskSettings,
} from '@/types'
import type { IndicatorId } from '@/utils/indicators'
import { DEFAULT_DRAWING_COLOR } from '@/utils/drawingEngine'
import { validateDrawingsMap, validateDrawingsExport } from '@/utils/drawingSchema'
import { DEFAULT_NOTIFICATION_PREFS } from '@/types'  // value import (const)
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
  activityFeed: import('@/types').ActivityEvent[]
  loading:   boolean

  setAccount:   (a: AnyAccount | null) => void
  setPositions: (p: (Position | SimPosition)[]) => void
  setOrders:    (o: OpenOrder[]) => void
  addTrade:     (t: Trade) => void
  setTrades:    (t: Trade[]) => void
  pushActivity: (e: import('@/types').ActivityEvent) => void
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

// ── Bot store ─────────────────────────────────────────────────────────────────

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

export type ThemePreference = 'light' | 'dark' | 'system'

interface UIState {
  sidebarCollapsed: boolean
  activeRoute:      AppRoute
  showOrderModal:   boolean
  orderModalSymbol: string
  /** The user's chosen preference — 'system' means follow OS */
  theme:            ThemePreference
  tradebotTab:      'positions' | 'rules' | 'insights' | 'activity'

  setSidebarCollapsed: (v: boolean) => void
  toggleSidebar:       () => void
  setRoute:            (r: AppRoute) => void
  openOrderModal:      (symbol?: string) => void
  closeOrderModal:     () => void
  setTheme:            (t: ThemePreference) => void
  setTradebotTab:      (tab: 'positions' | 'rules' | 'insights' | 'activity') => void
}

/** Apply a theme preference, persisting to localStorage and updating the DOM. */
function applyTheme(pref: ThemePreference) {
  localStorage.setItem('theme', pref)
  const resolved: 'light' | 'dark' =
    pref === 'system'
      ? window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
      : pref
  document.documentElement.setAttribute('data-theme', resolved)
}

export const useUIStore = create<UIState>((set) => ({
  sidebarCollapsed: false,
  activeRoute:      'dashboard',
  showOrderModal:   false,
  orderModalSymbol: '',
  tradebotTab:      'positions',
  theme: ((): ThemePreference => {
    try {
      const stored = localStorage.getItem('theme')
      if (stored === 'dark' || stored === 'light' || stored === 'system') return stored
    } catch { /* SSR / test env */ }
    return 'system'
  })(),

  setSidebarCollapsed: (v) => set({ sidebarCollapsed: v }),
  toggleSidebar:       () => set((s) => ({ sidebarCollapsed: !s.sidebarCollapsed })),
  setRoute:            (r) => set({ activeRoute: r }),
  openOrderModal:      (symbol = '') => set({ showOrderModal: true, orderModalSymbol: symbol }),
  closeOrderModal:     () => set({ showOrderModal: false }),
  setTheme: (t) => {
    applyTheme(t)
    set({ theme: t })
  },
  setTradebotTab: (tab) => set({ tradebotTab: tab }),
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
            status: 'locked',
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
      } else if (done && lockExpired) {
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

    const lastUnderscore = key.lastIndexOf('_')
    const symbol = key.slice(0, lastUnderscore)
    const timeframe = key.slice(lastUnderscore + 1)
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
        await api.updateSettings({ drawings: get().drawings } as Partial<UserSettings>)
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
    // Synchronous XHR is allowed in beforeunload and supports Authorization headers.
    // sendBeacon cannot send custom headers so it would fail on authenticated endpoints.
    try {
      const body = JSON.stringify({ drawings: s.drawings })
      const xhr = new XMLHttpRequest()
      xhr.open('PUT', '/api/settings', false) // false = synchronous
      xhr.setRequestHeader('Content-Type', 'application/json')
      const token = api.getAuthToken()
      if (token) xhr.setRequestHeader('Authorization', `Bearer ${token}`)
      xhr.send(body)
    } catch {
      // Best effort — page is unloading anyway
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
    loading: false,
  }),
}))

// ── Alert store ──────────────────────────────────────────────────────────────

interface AlertState {
  alerts:            Alert[]
  history:           AlertHistory[]
  unreadCount:       number
  recentFired:       AlertFiredEvent[]
  loading:           boolean
  notificationPrefs: NotificationPrefs
  alertStats:        AlertStats | null

  setAlerts:                (a: Alert[]) => void
  setHistory:               (h: AlertHistory[]) => void
  pushFired:                (e: AlertFiredEvent) => void
  markRead:                 () => void
  updateAlert:              (a: Alert) => void
  removeAlert:              (id: string) => void
  setLoading:               (v: boolean) => void
  loadAlerts:               () => Promise<void>
  loadHistory:              () => Promise<void>
  fetchAlertStats:          () => Promise<void>
  updateNotificationPrefs:  (partial: Partial<NotificationPrefs>) => void
}

export const useAlertStore = create<AlertState>((set) => ({
  alerts:            [],
  history:           [],
  unreadCount:       0,
  recentFired:       [],
  loading:           false,
  notificationPrefs: (() => {
    try {
      const stored = localStorage.getItem('alertNotificationPrefs')
      if (stored) return { ...DEFAULT_NOTIFICATION_PREFS, ...JSON.parse(stored) as Partial<NotificationPrefs> }
    } catch { /* ignore */ }
    return { ...DEFAULT_NOTIFICATION_PREFS }
  })(),
  alertStats: null,

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
  fetchAlertStats: async () => {
    try {
      const stats = await api.fetchAlertStats()
      set({ alertStats: stats })
    } catch {
      // backend offline — compute client-side fallback below
      set((s) => {
        const now = Date.now()
        const dayMs   = 86_400_000
        const weekMs  = 7 * dayMs
        const monthMs = 30 * dayMs

        const total_today = s.history.filter((h) => now - new Date(h.fired_at).getTime() < dayMs).length
        const total_week  = s.history.filter((h) => now - new Date(h.fired_at).getTime() < weekMs).length
        const total_month = s.history.filter((h) => now - new Date(h.fired_at).getTime() < monthMs).length

        const symbolCounts: Record<string, number> = {}
        for (const h of s.history) {
          symbolCounts[h.symbol] = (symbolCounts[h.symbol] ?? 0) + 1
        }
        const top_symbols = Object.entries(symbolCounts)
          .sort((a, b) => b[1] - a[1])
          .slice(0, 5)
          .map(([symbol, count]) => ({ symbol, count }))

        // Daily counts: last 14 days
        const daily_counts: { date: string; count: number }[] = []
        for (let i = 13; i >= 0; i--) {
          const d = new Date(now - i * dayMs)
          const dateStr = d.toISOString().slice(0, 10)
          const count = s.history.filter((h) => h.fired_at.startsWith(dateStr)).length
          daily_counts.push({ date: dateStr, count })
        }

        return {
          alertStats: { total_today, total_week, total_month, top_symbols, daily_counts },
        }
      })
    }
  },
  updateNotificationPrefs: (partial) =>
    set((s) => {
      const next = { ...s.notificationPrefs, ...partial }
      try { localStorage.setItem('alertNotificationPrefs', JSON.stringify(next)) } catch { /* ignore */ }
      return { notificationPrefs: next }
    }),
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
    set({
      loading: true, error: null, symbol,
      overview: null, keyStats: null, financials: null,
      analyst: null, ownership: null, events: null, narrative: null,
      financialStatements: null, analystDetail: null, ratingScorecard: null,
      companyInfo: null, stockSplits: null, earningsDetail: null,
      lastFetched: null,
    })
    try {
      const profile = await api.fetchStockProfile(symbol)
      set({
        overview: profile.overview,
        keyStats: profile.key_stats,
        financials: profile.financials,
        analyst: profile.analyst,
        ownership: profile.ownership,
        events: profile.events,
        narrative: profile.narrative,
        financialStatements: profile.financial_statements,
        analystDetail: profile.analyst_detail,
        ratingScorecard: profile.rating_scorecard,
        companyInfo: profile.company_info,
        stockSplits: profile.stock_splits,
        earningsDetail: profile.earnings_detail,
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

// ── Analytics store (Stage 7) ────────────────────────────────────────────────

interface AnalyticsState {
  analytics:         PortfolioAnalytics | null
  pnlSummary:        PnLSummary | null
  dailyPnL:          DailyPnL[]
  matchedTrades:     MatchedTrade[]
  exposure:          ExposureBreakdown | null
  sectorExposure:    SectorExposureRow[]
  correlationMatrix: CorrelationMatrix | null
  tradeHistory:      TradeHistoryRow[]
  range:             '1W' | '1M' | '3M' | '6M' | '1Y' | 'ALL'
  loading:           boolean
  error:             string | null

  setRange:                (r: '1W' | '1M' | '3M' | '6M' | '1Y' | 'ALL') => void
  fetchPortfolioAnalytics: () => Promise<void>
  fetchPnL:                () => Promise<void>
  fetchSectorExposure:     () => Promise<void>
  fetchCorrelation:        () => Promise<void>
  fetchTradeHistory:       (limit?: number) => Promise<void>
  fetchAll:                () => Promise<void>
}

export const useAnalyticsStore = create<AnalyticsState>((set, get) => ({
  analytics:         null,
  pnlSummary:        null,
  dailyPnL:          [],
  matchedTrades:     [],
  exposure:          null,
  sectorExposure:    [],
  correlationMatrix: null,
  tradeHistory:      [],
  range:             '3M',
  loading:           false,
  error:             null,

  setRange: (r) => set({ range: r }),

  fetchPortfolioAnalytics: async () => {
    const { range } = get()
    try {
      const data = await api.fetchPortfolioAnalytics(range)
      const { tradeHistory } = get()
      const wins = tradeHistory.filter((t) => (t.pnl ?? 0) > 0)
      const losses = tradeHistory.filter((t) => (t.pnl ?? 0) < 0)
      const grossProfit = wins.reduce((s, t) => s + (t.pnl ?? 0), 0)
      const grossLoss = Math.abs(losses.reduce((s, t) => s + (t.pnl ?? 0), 0))
      const bestTrade = tradeHistory.reduce(
        (best, t) => (!best || (t.pnl ?? 0) > (best.pnl ?? 0) ? t : best),
        tradeHistory[0] as TradeHistoryRow | undefined,
      )
      const worstTrade = tradeHistory.reduce(
        (worst, t) => (!worst || (t.pnl ?? 0) < (worst.pnl ?? 0) ? t : worst),
        tradeHistory[0] as TradeHistoryRow | undefined,
      )
      const summary: PnLSummary = {
        realized_pnl:      data.total_pnl,
        realized_pnl_pct:  data.total_pnl_pct,
        unrealized_pnl:    0,
        today_pnl:         data.day_pnl,
        today_pnl_pct:     data.day_pnl_pct,
        win_rate:          data.win_rate,
        profit_factor:     grossLoss > 0 ? grossProfit / grossLoss : grossProfit > 0 ? Infinity : 0,
        best_trade_pnl:    bestTrade?.pnl ?? 0,
        best_trade_symbol: bestTrade?.symbol ?? '—',
        worst_trade_pnl:   worstTrade?.pnl ?? 0,
        worst_trade_symbol:worstTrade?.symbol ?? '—',
        total_trades:      tradeHistory.length,
      }
      set({ analytics: data, pnlSummary: summary })
    } catch (err) {
      set({ error: err instanceof Error ? err.message : 'Analytics load failed' })
    }
  },

  fetchPnL: async () => {
    try {
      const data = await api.fetchDailyPnL(90)
      set({ dailyPnL: data })
    } catch { /* backend offline */ }
  },

  fetchSectorExposure: async () => {
    try {
      const data = await api.fetchExposureBreakdown()
      set({ exposure: data })
      const sectorMap: Record<string, SectorExposureRow> = {}
      for (const pos of data.positions) {
        const s = pos.sector || 'Unknown'
        if (!sectorMap[s]) {
          sectorMap[s] = { sector: s, weight_pct: 0, value: 0, position_count: 0, pnl: 0 }
        }
        sectorMap[s].weight_pct += pos.weight_pct
        sectorMap[s].value      += pos.value
        sectorMap[s].position_count += 1
        sectorMap[s].pnl        += pos.pnl
      }
      set({ sectorExposure: Object.values(sectorMap).sort((a, b) => b.weight_pct - a.weight_pct) })
    } catch { /* backend offline */ }
  },

  fetchCorrelation: async () => {
    try {
      const data = await api.fetchCorrelationMatrix()
      set({ correlationMatrix: data })
    } catch { /* backend offline */ }
  },

  fetchTradeHistory: async (limit = 200) => {
    try {
      const data = await api.fetchTradeHistory(limit)
      // FIFO-match BUY/SELL pairs per symbol
      const bySymbol: Record<string, TradeHistoryRow[]> = {}
      for (const t of data) {
        if (!bySymbol[t.symbol]) bySymbol[t.symbol] = []
        bySymbol[t.symbol].push(t)
      }
      const matched: MatchedTrade[] = []
      for (const rows of Object.values(bySymbol)) {
        const sorted = [...rows].sort((a, b) => new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime())
        let i = 0
        while (i < sorted.length - 1) {
          const entry = sorted[i]
          const exit  = sorted[i + 1]
          if (entry.action === 'BUY' && exit.action === 'SELL') {
            const entryMs = new Date(entry.timestamp).getTime()
            const exitMs  = new Date(exit.timestamp).getTime()
            const holdDays = Math.max(0, Math.round((exitMs - entryMs) / 86_400_000))
            const pnl = exit.pnl ?? (exit.fill_price - entry.fill_price) * entry.quantity
            const pnlPct = entry.fill_price > 0
              ? ((exit.fill_price - entry.fill_price) / entry.fill_price) * 100
              : 0
            matched.push({
              id: `${entry.id}_${exit.id}`,
              symbol:      entry.symbol,
              entry_date:  entry.timestamp,
              exit_date:   exit.timestamp,
              entry_price: entry.fill_price,
              exit_price:  exit.fill_price,
              qty:         entry.quantity,
              pnl,
              pnl_pct: pnlPct,
              hold_days: holdDays,
            })
            i += 2
          } else {
            i++
          }
        }
      }
      set({ tradeHistory: data, matchedTrades: matched })
    } catch { /* backend offline */ }
  },

  fetchAll: async () => {
    set({ loading: true, error: null })
    try {
      await get().fetchTradeHistory(200)
      await Promise.all([
        get().fetchPortfolioAnalytics(),
        get().fetchPnL(),
        get().fetchSectorExposure(),
        get().fetchCorrelation(),
      ])
    } finally {
      set({ loading: false })
    }
  },
}))

// ── Risk store (Stage 7) ─────────────────────────────────────────────────────

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


// ── Autopilot store ───────────────────────────────────────────────────────────

import type {
  AutopilotConfig,
  AuditLogEntry,
  AIStatus,
  LearningMetrics,
  EconomicReport,
  CostReport,
} from '@/types/advisor'

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

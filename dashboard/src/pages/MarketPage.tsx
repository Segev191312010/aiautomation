/**
 * MarketPage — full-screen chart with symbol selector, toolbar (timeframe,
 * chart type, indicators), comparison overlay, volume pane, and oscillator panels.
 *
 * Bars auto-refresh on an interval (faster for intraday, slower for daily+).
 * Live candle updates arrive via /ws/market-data WebSocket.
 */
import { useState, useEffect, useRef, useMemo, useCallback } from 'react'
import type { IChartApi, ISeriesApi } from 'lightweight-charts'
import clsx from 'clsx'
import TradingChart from '@/components/chart/TradingChart'
import VolumePanel from '@/components/chart/VolumePanel'
import IndicatorPanel from '@/components/chart/IndicatorPanel'
import ChartToolbar, { TOOLBAR_TIMEFRAMES } from '@/components/chart/ChartToolbar'
import DrawingTools from '@/components/chart/DrawingTools'
import ResizeHandle from '@/components/chart/ResizeHandle'
import TickerCard from '@/components/ticker/TickerCard'
import Skeleton from '@/components/ui/Skeleton'
import AlertForm from '@/components/alerts/AlertForm'
import { useToast } from '@/components/ui/ToastProvider'
import { useMarketStore, useDrawingStore, useBotStore } from '@/store'
import { fetchYahooBars, fetchSettings } from '@/services/api'
import { calcRSI, calcMACD } from '@/utils/indicators'
import { useCrosshairSync, type ChartPane } from '@/hooks/useCrosshairSync'

// ── Auto-refresh intervals ──────────────────────────────────────────────────

const AUTO_REFRESH_MS: Record<string, number> = {
  '1m':  10_000,       // 10s
  '5m':  20_000,       // 20s
  '15m': 30_000,       // 30s
  '30m': 60_000,       // 1 min
  '1h':  120_000,      // 2 min
  '1d':  300_000,      // 5 min
  '1wk': 600_000,      // 10 min
  '1mo': 1_800_000,    // 30 min
}

// ── Component ─────────────────────────────────────────────────────────────────

export default function MarketPage() {
  const toast = useToast()
  const {
    selectedSymbol,
    setSelectedSymbol,
    quotes,
    setBars,
    compMode,
    compSymbol,
    setCompSymbol,
    setCompBars,
    toggleCompMode,
  } = useMarketStore()

  const [tfIdx, setTfIdx]         = useState(6)   // default 1D
  const [searchInput, setSearch]   = useState(selectedSymbol)
  const [loading, setLoading]      = useState(false)
  const [showAlertForm, setShowAlertForm] = useState(false)
  const refreshTimerRef            = useRef<ReturnType<typeof setInterval> | null>(null)
  const [mainChartApi, setMainChartApi] = useState<IChartApi | null>(null)
  const chartContainerRef          = useRef<HTMLDivElement>(null)

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  type AnySeries = ISeriesApi<any>

  // Pane refs for crosshair sync
  const [mainSeries, setMainSeries]   = useState<AnySeries | null>(null)
  const [volChart, setVolChart]       = useState<IChartApi | null>(null)
  const [volSeries, setVolSeries]     = useState<AnySeries | null>(null)
  const [rsiChart, setRsiChart]       = useState<IChartApi | null>(null)
  const [rsiSeries, setRsiSeries]     = useState<AnySeries | null>(null)
  const [macdChart, setMacdChart]     = useState<IChartApi | null>(null)
  const [macdSeries, setMacdSeries]   = useState<AnySeries | null>(null)

  const loadDrawings = useDrawingStore((s) => s.loadDrawings)

  const quote      = quotes[selectedSymbol]
  const currentTF  = TOOLBAR_TIMEFRAMES[tfIdx]
  const bars       = useMarketStore((s) => s.bars[selectedSymbol] ?? [])
  const staleAgeS = (() => {
    if (quote?.stale_s != null) return quote.stale_s
    if (!quote?.last_update) return Number.POSITIVE_INFINITY
    const age = (Date.now() - new Date(quote.last_update).getTime()) / 1000
    return Number.isFinite(age) ? Math.max(0, age) : Number.POSITIVE_INFINITY
  })()
  const badge = (() => {
    const marketState = quote?.market_state ?? 'unknown'
    if (marketState === 'open' && staleAgeS <= 10) {
      return {
        label: 'LIVE',
        age: '',
        dotClass: 'bg-terminal-green animate-pulse',
        textClass: 'text-terminal-green',
      }
    }
    if (marketState === 'extended' && staleAgeS <= 10) {
      return {
        label: 'EXTENDED',
        age: '',
        dotClass: 'bg-terminal-blue animate-pulse',
        textClass: 'text-terminal-blue',
      }
    }
    return {
      label: 'CLOSED / STALE',
      age: Number.isFinite(staleAgeS) ? `${Math.floor(staleAgeS)}s` : '--',
      dotClass: staleAgeS > 30 ? 'bg-terminal-red' : 'bg-terminal-amber',
      textClass: staleAgeS > 30 ? 'text-terminal-red' : 'text-terminal-amber',
    }
  })()

  // Build time→price data maps for crosshair sync
  const mainDataMap = useMemo(() => {
    const m = new Map<number, number>()
    for (const b of bars) m.set(b.time, b.close)
    return m
  }, [bars])

  const volDataMap = useMemo(() => {
    const m = new Map<number, number>()
    for (const b of bars) m.set(b.time, b.volume)
    return m
  }, [bars])

  const rsiDataMap = useMemo(() => {
    const m = new Map<number, number>()
    if (!bars.length) return m
    for (const pt of calcRSI(bars, 14)) m.set(pt.time, pt.value)
    return m
  }, [bars])

  const macdDataMap = useMemo(() => {
    const m = new Map<number, number>()
    if (!bars.length) return m
    for (const pt of calcMACD(bars).macd) m.set(pt.time, pt.value)
    return m
  }, [bars])

  // Assemble panes for crosshair sync
  const mainPane: ChartPane | null = mainChartApi && mainSeries
    ? { chart: mainChartApi, series: mainSeries, data: mainDataMap } : null
  const volPane: ChartPane | null = volChart && volSeries
    ? { chart: volChart, series: volSeries, data: volDataMap } : null
  const rsiPane: ChartPane | null = rsiChart && rsiSeries
    ? { chart: rsiChart, series: rsiSeries, data: rsiDataMap } : null
  const macdPane: ChartPane | null = macdChart && macdSeries
    ? { chart: macdChart, series: macdSeries, data: macdDataMap } : null

  // Clear stale chart refs when indicators are toggled off
  const selectedIndicators = useMarketStore((s) => s.selectedIndicators)
  useEffect(() => {
    if (!selectedIndicators.includes('rsi')) {
      setRsiChart(null)
      setRsiSeries(null)
    }
    if (!selectedIndicators.includes('macd')) {
      setMacdChart(null)
      setMacdSeries(null)
    }
  }, [selectedIndicators])

  useCrosshairSync([mainPane, volPane, rsiPane, macdPane])

  // Callbacks for pane chart-ready events
  const handleMainChartReady = useCallback((chart: IChartApi, series: AnySeries) => {
    setMainChartApi(chart)
    setMainSeries(series)
  }, [])

  const handleVolReady = useCallback((chart: IChartApi, series: AnySeries) => {
    setVolChart(chart)
    setVolSeries(series)
  }, [])

  const handleRsiReady = useCallback((chart: IChartApi, series: AnySeries) => {
    setRsiChart(chart)
    setRsiSeries(series)
  }, [])

  const handleMacdReady = useCallback((chart: IChartApi, series: AnySeries) => {
    setMacdChart(chart)
    setMacdSeries(series)
  }, [])

  // ── Resizable pane heights ──────────────────────────────────────────────

  const DEFAULT_VOL_HEIGHT = 70
  const DEFAULT_IND_HEIGHT = 144
  const [volumeHeight, setVolumeHeight]       = useState(DEFAULT_VOL_HEIGHT)
  const [indicatorHeight, setIndicatorHeight] = useState(DEFAULT_IND_HEIGHT)

  const handleVolResize = useCallback((dy: number) => {
    // Drag down → handle moves down → pane below (volume) shrinks → subtract dy
    setVolumeHeight((h) => Math.min(150, Math.max(40, h - dy)))
  }, [])

  const handleIndResize = useCallback((dy: number) => {
    // Drag down → handle moves down → pane below (indicators) shrinks → subtract dy
    setIndicatorHeight((h) => Math.min(300, Math.max(80, h - dy)))
  }, [])

  const resetVolHeight = useCallback(() => setVolumeHeight(DEFAULT_VOL_HEIGHT), [])
  const resetIndHeight = useCallback(() => setIndicatorHeight(DEFAULT_IND_HEIGHT), [])

  // ── Bar loading ───────────────────────────────────────────────────────────

  const loadBars = async (sym: string, idx: number) => {
    setLoading(true)
    const tf = TOOLBAR_TIMEFRAMES[idx]
    try {
      const bars = await fetchYahooBars(sym, tf.period, tf.interval)
      setBars(sym, bars)
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Failed to load bars'
      console.warn('[MarketPage] Bar load failed:', msg)
      if (msg.includes('400') || msg.includes('interval')) {
        toast.error(msg)
      }
    } finally {
      setLoading(false)
    }
  }

  const loadCompBars = async (sym: string, idx: number) => {
    const tf = TOOLBAR_TIMEFRAMES[idx]
    try {
      const bars = await fetchYahooBars(sym, tf.period, tf.interval)
      setCompBars(sym, bars)
    } catch (err) {
      console.warn('[MarketPage] Comp bar load failed:', err)
    }
  }

  const refreshActiveCharts = useCallback(() => {
    loadBars(selectedSymbol, tfIdx)
    if (compMode && compSymbol) loadCompBars(compSymbol, tfIdx)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedSymbol, tfIdx, compMode, compSymbol])

  // ── Initial + symbol/timeframe-change load ────────────────────────────────

  useEffect(() => {
    loadBars(selectedSymbol, tfIdx)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedSymbol, tfIdx])

  useEffect(() => {
    if (compMode && compSymbol) loadCompBars(compSymbol, tfIdx)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [compMode, compSymbol, tfIdx])

  // ── 24/7 auto-refresh ─────────────────────────────────────────────────────

  useEffect(() => {
    if (refreshTimerRef.current) clearInterval(refreshTimerRef.current)
    const interval = AUTO_REFRESH_MS[currentTF.interval] ?? 300_000
    refreshTimerRef.current = setInterval(() => {
      refreshActiveCharts()
    }, interval)
    return () => {
      if (refreshTimerRef.current) clearInterval(refreshTimerRef.current)
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedSymbol, tfIdx, compMode, compSymbol, refreshActiveCharts])

  // Catch up immediately when user returns to tab/network comes back.
  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState === 'visible') refreshActiveCharts()
    }
    const onOnline = () => refreshActiveCharts()
    document.addEventListener('visibilitychange', onVisible)
    window.addEventListener('online', onOnline)
    return () => {
      document.removeEventListener('visibilitychange', onVisible)
      window.removeEventListener('online', onOnline)
    }
  }, [refreshActiveCharts])

  // ── Load saved drawings from settings ─────────────────────────────────────

  useEffect(() => {
    fetchSettings()
      .then((s) => {
        if (s.drawings) loadDrawings(s.drawings as Record<string, import('@/types/drawing').Drawing[]>)
      })
      .catch(() => { /* drawings failed to load — non-critical */ })
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // ── Handlers ──────────────────────────────────────────────────────────────

  const handleSearch = (e: React.FormEvent) => {
    e.preventDefault()
    const sym = searchInput.trim().toUpperCase()
    if (sym) {
      setSelectedSymbol(sym)
      setSearch(sym)
      toast.info(`Loading ${sym}`)
    }
  }

  return (
    <div className="flex flex-col h-full gap-2">
      {/* ── Row 1: symbol search + compare ─────────────────────────── */}
      <div className="flex items-center gap-3 flex-wrap">
        {/* Symbol search */}
        <form onSubmit={handleSearch} className="flex items-center gap-2">
          <input
            value={searchInput}
            onChange={(e) => setSearch(e.target.value.toUpperCase())}
            placeholder="Enter symbol…"
            className="w-28 text-sm font-mono bg-terminal-input border border-terminal-border rounded-l-xl px-3 py-1.5 text-terminal-text focus:border-indigo-500 focus:outline-none"
          />
          <button
            type="submit"
            className="text-xs font-sans px-3 py-1.5 rounded-r-xl bg-indigo-500/15 border border-l-0 border-indigo-500/30 text-indigo-400 hover:bg-indigo-500/25 transition-colors"
          >
            Go
          </button>
        </form>

        {/* Compare controls */}
        <div className="ml-auto flex items-center gap-2">
          {compMode && (
            <form
              onSubmit={(e) => {
                e.preventDefault()
                const inp = (e.currentTarget.elements.namedItem('comp') as HTMLInputElement)
                  .value.toUpperCase()
                setCompSymbol(inp)
              }}
              className="flex"
            >
              <input
                name="comp"
                defaultValue={compSymbol}
                placeholder="vs. MSFT"
                className="w-24 text-xs font-mono bg-terminal-input border border-terminal-border rounded-l-xl px-2 py-1 text-terminal-text focus:border-terminal-amber focus:outline-none"
              />
              <button
                type="submit"
                className="text-xs font-sans px-2 py-1 rounded-r-xl bg-terminal-amber/10 border border-l-0 border-terminal-amber/40 text-terminal-amber"
              >
                Set
              </button>
            </form>
          )}
          <button
            onClick={toggleCompMode}
            className={clsx(
              'text-[11px] font-sans px-2.5 py-1 rounded-xl border transition-colors',
              compMode
                ? 'border-terminal-amber/40 text-terminal-amber bg-terminal-amber/5'
                : 'border-terminal-border text-terminal-ghost hover:text-terminal-dim',
            )}
          >
            ⊕ Compare
          </button>
        </div>
      </div>

      {/* ── Row 2: chart toolbar ───────────────────────────────────── */}
      <ChartToolbar
        activeTfIdx={tfIdx}
        onTfChange={setTfIdx}
        chartContainer={chartContainerRef.current}
        chartRef={mainChartApi}
        isLoading={loading}
        onCreateAlert={() => setShowAlertForm(true)}
      />

      {/* ── Row 2b: drawing tools ──────────────────────────────────── */}
      <DrawingTools symbol={selectedSymbol} timeframe={currentTF.interval} />

      {/* ── Chart + quote card ──────────────────────────────────────── */}
      <div className="flex gap-4 flex-1 min-h-0">
        {/* Main chart + volume + oscillator panels stacked */}
        <div className="flex-1 min-w-0 flex flex-col gap-1 min-h-0" ref={chartContainerRef}>
          {/* Main chart */}
          <div className="flex-1 min-h-0 glass rounded-2xl shadow-glass-lg overflow-hidden relative">
            {loading && (
              <div className="absolute inset-0 bg-terminal-bg/50 flex items-center justify-center z-10">
                <span className="text-xs font-sans text-terminal-dim animate-pulse">Loading…</span>
              </div>
            )}
            <div className="flex items-center gap-2 px-4 py-2.5 border-b border-white/[0.06]">
              <span className="font-mono font-bold text-terminal-text">{selectedSymbol}</span>
              {compMode && compSymbol && (
                <>
                  <span className="text-terminal-ghost font-mono text-xs">vs.</span>
                  <span className="font-mono font-bold text-terminal-amber">{compSymbol}</span>
                  <span className="text-[11px] font-sans text-terminal-dim ml-1">[normalized %]</span>
                </>
              )}
              {/* Live pulse indicator */}
              <span className={clsx('ml-auto flex items-center gap-1.5 text-[11px] font-sans', badge.textClass)}>
                <span className={clsx('w-1.5 h-1.5 rounded-full', badge.dotClass)} />
                {badge.label}
                {badge.age && <span className="text-terminal-ghost">({badge.age})</span>}
              </span>
            </div>
            <div className="h-[calc(100%-44px)]">
              <TradingChart
                symbol={selectedSymbol}
                timeframe={currentTF.interval}
                className="h-full"
                onChartReady={handleMainChartReady}
                onStale={refreshActiveCharts}
              />
            </div>
          </div>

          {/* Resize handle: main ↔ volume */}
          <ResizeHandle onDelta={handleVolResize} onDoubleClick={resetVolHeight} />

          {/* Volume pane */}
          <div
            className="shrink-0 glass rounded-2xl overflow-hidden"
            style={{ height: volumeHeight }}
          >
            <VolumePanel
              symbol={selectedSymbol}
              mainChart={mainChartApi}
              onChartReady={handleVolReady}
              className="h-full"
            />
          </div>

          {/* Resize handle: volume ↔ indicators */}
          <ResizeHandle onDelta={handleIndResize} onDoubleClick={resetIndHeight} />

          {/* Oscillator sub-panels (RSI / MACD) */}
          <IndicatorPanel
            symbol={selectedSymbol}
            mainChart={mainChartApi}
            onRSIReady={handleRsiReady}
            onMACDReady={handleMacdReady}
            className="shrink-0"
            style={{ height: indicatorHeight }}
          />
        </div>

        {/* Quote card sidebar */}
        <aside className="w-52 shrink-0">
          {quote ? (
            <TickerCard quote={quote} />
          ) : (
            <div className="glass rounded-2xl shadow-glass p-4 space-y-3">
              <Skeleton className="h-4 w-20" />
              <Skeleton className="h-8 w-28" />
              <Skeleton className="h-3 w-16" />
              <Skeleton className="h-3 w-24" />
            </div>
          )}

          {compMode && compSymbol && quotes[compSymbol] && (
            <div className="mt-3">
              <TickerCard quote={quotes[compSymbol]} />
            </div>
          )}
        </aside>
      </div>

      {/* ── Alert form modal ─────────────────────────────────────────── */}
      {showAlertForm && (
        <AlertForm
          initialSymbol={selectedSymbol}
          onClose={() => setShowAlertForm(false)}
        />
      )}
    </div>
  )
}

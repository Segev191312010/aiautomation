import { useState, useEffect, useRef, useMemo, useCallback } from 'react'
import type { IChartApi } from 'lightweight-charts'
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
import ErrorBoundary from '@/components/ui/ErrorBoundary'
import { useMarketStore, useDrawingStore, useUIStore, useBotStore } from '@/store'
import { fetchYahooBars, fetchIBKRBars, fetchSettings } from '@/services/api'
import { calcRSI, calcMACD } from '@/utils/indicators'
import { useCrosshairSync, type ChartPane } from '@/hooks/useCrosshairSync'

const AUTO_REFRESH_MS: Record<string, number> = {
  '1m': 10_000,
  '5m': 20_000,
  '15m': 30_000,
  '30m': 60_000,
  '1h': 120_000,
  '1d': 300_000,
  '1wk': 600_000,
  '1mo': 1_800_000,
}

function MarketSignalCard({
  label,
  value,
  tone = 'default',
}: {
  label: string
  value: string
  tone?: 'default' | 'success' | 'warning'
}) {
  const toneClass =
    tone === 'success'
      ? 'border-[rgba(31,157,104,0.18)] bg-[rgba(31,157,104,0.1)] text-[var(--success)]'
      : tone === 'warning'
        ? 'border-[rgba(245,158,11,0.2)] bg-[rgba(245,158,11,0.1)] text-[var(--accent)]'
        : 'border-[var(--border)] bg-[var(--bg-hover)] text-[var(--text-primary)]'

  return (
    <div className={`rounded-[24px] border p-4 ${toneClass}`}>
      <div className="shell-kicker">{label}</div>
      <div className="mt-3 text-2xl font-semibold leading-none">{value}</div>
    </div>
  )
}

export default function MarketPage() {
  const toast = useToast()
  const setRoute = useUIStore((s) => s.setRoute)
  const ibkrConnected = useBotStore((s) => s.ibkrConnected)
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

  const [tfIdx, setTfIdx] = useState(5)
  const [searchInput, setSearch] = useState(selectedSymbol)
  const [loading, setLoading] = useState(false)
  const [showAlertForm, setShowAlertForm] = useState(false)
  const refreshTimerRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const [mainChartApi, setMainChartApi] = useState<IChartApi | null>(null)
  const chartContainerRef = useRef<HTMLDivElement>(null)

  const [mainSeries, setMainSeries] = useState<ChartPane['series'] | null>(null)
  const [volChart, setVolChart] = useState<IChartApi | null>(null)
  const [volSeries, setVolSeries] = useState<ChartPane['series'] | null>(null)
  const [rsiChart, setRsiChart] = useState<IChartApi | null>(null)
  const [rsiSeries, setRsiSeries] = useState<ChartPane['series'] | null>(null)
  const [macdChart, setMacdChart] = useState<IChartApi | null>(null)
  const [macdSeries, setMacdSeries] = useState<ChartPane['series'] | null>(null)

  const loadDrawings = useDrawingStore((s) => s.loadDrawings)

  const quote = quotes[selectedSymbol]
  const currentTF = TOOLBAR_TIMEFRAMES[tfIdx]
  const bars = useMarketStore((s) => s.bars[selectedSymbol] ?? [])

  const isStockLike = useCallback((symbol: string) => {
    const normalized = symbol.trim().toUpperCase()
    return !!normalized && !normalized.endsWith('-USD')
  }, [])

  const staleAgeS = useMemo(() => {
    if (quote?.stale_s != null) return quote.stale_s
    if (!quote?.last_update) return Number.POSITIVE_INFINITY
    const age = (Date.now() - new Date(quote.last_update).getTime()) / 1000
    return Number.isFinite(age) ? Math.max(0, age) : Number.POSITIVE_INFINITY
  }, [quote?.stale_s, quote?.last_update])

  const badge = useMemo(() => {
    const marketState = quote?.market_state ?? 'unknown'
    if (marketState === 'open' && staleAgeS <= 10) {
      return {
        label: 'LIVE',
        age: '',
        dotClass: 'bg-emerald-600',
        textClass: 'text-emerald-400',
      }
    }
    if (marketState === 'extended' && staleAgeS <= 10) {
      return {
        label: 'EXTENDED',
        age: '',
        dotClass: 'bg-sky-600',
        textClass: 'text-sky-600',
      }
    }
    return {
      label: 'CLOSED / STALE',
      age: Number.isFinite(staleAgeS) ? `${Math.floor(staleAgeS)}s` : '--',
      dotClass: staleAgeS > 30 ? 'bg-red-600' : 'bg-amber-600',
      textClass: staleAgeS > 30 ? 'text-red-400' : 'text-amber-600',
    }
  }, [quote?.market_state, staleAgeS])

  const feedLabel = useMemo(
    () => quote?.live_source === 'ibkr' ? 'IBKR stream' : 'Yahoo fallback',
    [quote?.live_source],
  )
  const historyFeedLabel = useMemo(
    () => ibkrConnected && isStockLike(selectedSymbol) ? 'IBKR history + Yahoo fallback' : 'Yahoo history',
    [ibkrConnected, isStockLike, selectedSymbol],
  )

  const mainDataMap = useMemo(() => {
    const map = new Map<number, number>()
    for (const bar of bars) map.set(bar.time, bar.close)
    return map
  }, [bars])

  const volDataMap = useMemo(() => {
    const map = new Map<number, number>()
    for (const bar of bars) map.set(bar.time, bar.volume)
    return map
  }, [bars])

  const rsiDataMap = useMemo(() => {
    const map = new Map<number, number>()
    if (!bars.length) return map
    for (const point of calcRSI(bars, 14)) map.set(point.time, point.value)
    return map
  }, [bars])

  const macdDataMap = useMemo(() => {
    const map = new Map<number, number>()
    if (!bars.length) return map
    for (const point of calcMACD(bars).macd) map.set(point.time, point.value)
    return map
  }, [bars])

  const mainPane = useMemo<ChartPane | null>(
    () => mainChartApi && mainSeries ? { chart: mainChartApi, series: mainSeries, data: mainDataMap } : null,
    [mainChartApi, mainSeries, mainDataMap],
  )
  const volPane = useMemo<ChartPane | null>(
    () => volChart && volSeries ? { chart: volChart, series: volSeries, data: volDataMap } : null,
    [volChart, volSeries, volDataMap],
  )
  const rsiPane = useMemo<ChartPane | null>(
    () => rsiChart && rsiSeries ? { chart: rsiChart, series: rsiSeries, data: rsiDataMap } : null,
    [rsiChart, rsiSeries, rsiDataMap],
  )
  const macdPane = useMemo<ChartPane | null>(
    () => macdChart && macdSeries ? { chart: macdChart, series: macdSeries, data: macdDataMap } : null,
    [macdChart, macdSeries, macdDataMap],
  )

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

  const handleMainChartReady = useCallback((chart: IChartApi, series: ChartPane['series']) => {
    setMainChartApi(chart)
    setMainSeries(series)
  }, [])

  const handleVolReady = useCallback((chart: IChartApi, series: ChartPane['series']) => {
    setVolChart(chart)
    setVolSeries(series)
  }, [])

  const handleRsiReady = useCallback((chart: IChartApi, series: ChartPane['series']) => {
    setRsiChart(chart)
    setRsiSeries(series)
  }, [])

  const handleMacdReady = useCallback((chart: IChartApi, series: ChartPane['series']) => {
    setMacdChart(chart)
    setMacdSeries(series)
  }, [])

  const DEFAULT_VOL_HEIGHT = 70
  const DEFAULT_IND_HEIGHT = 144
  const [volumeHeight, setVolumeHeight] = useState(DEFAULT_VOL_HEIGHT)
  const [indicatorHeight, setIndicatorHeight] = useState(DEFAULT_IND_HEIGHT)

  const handleVolResize = useCallback((dy: number) => {
    setVolumeHeight((height) => Math.min(150, Math.max(40, height - dy)))
  }, [])

  const handleIndResize = useCallback((dy: number) => {
    setIndicatorHeight((height) => Math.min(300, Math.max(80, height - dy)))
  }, [])

  const resetVolHeight = useCallback(() => setVolumeHeight(DEFAULT_VOL_HEIGHT), [])
  const resetIndHeight = useCallback(() => setIndicatorHeight(DEFAULT_IND_HEIGHT), [])

  const loadBars = async (sym: string, idx: number) => {
    setLoading(true)
    const tf = TOOLBAR_TIMEFRAMES[idx]

    try {
      let nextBars
      if (ibkrConnected && isStockLike(sym)) {
        try {
          const barSize =
            tf.interval === '1d' ? '1 day'
              : tf.interval === '1wk' ? '1 week'
                : tf.interval === '1mo' ? '1 month'
                  : '1 hour'
          const duration =
            tf.interval === '1d' ? '1 Y'
              : tf.interval === '1wk' ? '2 Y'
                : tf.interval === '1mo' ? '5 Y'
                  : '30 D'
          nextBars = await fetchIBKRBars(sym, barSize, duration)
        } catch {
          nextBars = await fetchYahooBars(sym, tf.period, tf.interval)
        }
      } else {
        nextBars = await fetchYahooBars(sym, tf.period, tf.interval)
      }

      setBars(sym, nextBars)
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Failed to load bars'
      console.warn('[MarketPage] Bar load failed:', message)
      if (message.includes('400') || message.includes('interval')) {
        toast.error(message)
      }
    } finally {
      setLoading(false)
    }
  }

  const loadCompBars = async (sym: string, idx: number) => {
    const tf = TOOLBAR_TIMEFRAMES[idx]
    try {
      let nextBars
      if (ibkrConnected && isStockLike(sym)) {
        try {
          const barSize =
            tf.interval === '1d' ? '1 day'
              : tf.interval === '1wk' ? '1 week'
                : tf.interval === '1mo' ? '1 month'
                  : '1 hour'
          const duration =
            tf.interval === '1d' ? '1 Y'
              : tf.interval === '1wk' ? '2 Y'
                : tf.interval === '1mo' ? '5 Y'
                  : '30 D'
          nextBars = await fetchIBKRBars(sym, barSize, duration)
        } catch {
          nextBars = await fetchYahooBars(sym, tf.period, tf.interval)
        }
      } else {
        nextBars = await fetchYahooBars(sym, tf.period, tf.interval)
      }

      setCompBars(sym, nextBars)
    } catch (error) {
      console.warn('[MarketPage] Comp bar load failed:', error)
    }
  }

  const refreshActiveCharts = useCallback(() => {
    void loadBars(selectedSymbol, tfIdx)
    if (compMode && compSymbol) void loadCompBars(compSymbol, tfIdx)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedSymbol, tfIdx, compMode, compSymbol])

  useEffect(() => {
    void loadBars(selectedSymbol, tfIdx)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedSymbol, tfIdx])

  useEffect(() => {
    if (compMode && compSymbol) void loadCompBars(compSymbol, tfIdx)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [compMode, compSymbol, tfIdx])

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

  useEffect(() => {
    fetchSettings()
      .then((settings) => {
        if (settings.drawings) {
          loadDrawings(settings.drawings as Record<string, import('@/types/drawing').Drawing[]>)
        }
      })
      .catch(() => {
        // Drawings are optional.
      })
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const handleSearch = (e: React.FormEvent) => {
    e.preventDefault()
    const sym = searchInput.trim().toUpperCase()
    if (!sym) return
    setSelectedSymbol(sym)
    setSearch(sym)
    toast.info(`Loading ${sym}`)
  }

  const formatCompact = (value?: number): string => {
    if (value == null) return '--'
    if (value >= 1e12) return `$${(value / 1e12).toFixed(2)}T`
    if (value >= 1e9) return `$${(value / 1e9).toFixed(2)}B`
    if (value >= 1e6) return `$${(value / 1e6).toFixed(2)}M`
    return `$${value.toLocaleString('en-US')}`
  }

  const formatPrice = (value?: number): string => {
    if (value == null) return '--'
    return value >= 1000
      ? value.toLocaleString('en-US', { maximumFractionDigits: 2 })
      : value.toFixed(2)
  }

  return (
    <div className="flex min-h-0 flex-col gap-6 pb-4">
      <ErrorBoundary>
      <section className="grid gap-6 xl:grid-cols-[minmax(0,1.25fr)_minmax(320px,0.75fr)]">
        <div className="shell-panel relative overflow-hidden p-6 sm:p-7">
          <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_top_right,rgba(245,158,11,0.16),transparent_34%)]" />
          <div className="relative">
            <div className="shell-kicker">Live market workspace</div>
            <div className="mt-3 flex flex-wrap items-end gap-x-4 gap-y-2">
              <div className="display-font text-[3rem] leading-none text-[var(--text-primary)] sm:text-[3.6rem]">
                {selectedSymbol}
              </div>
              <div className="text-2xl font-semibold text-[var(--text-primary)] sm:text-3xl">
                {formatPrice(quote?.price)}
              </div>
              {quote && (
                <div
                  className={clsx(
                    'rounded-full border px-3 py-1 text-sm font-semibold',
                    quote.change_pct >= 0
                      ? 'border-[rgba(31,157,104,0.2)] bg-[rgba(31,157,104,0.1)] text-[var(--success)]'
                      : 'border-[rgba(217,76,61,0.2)] bg-[rgba(217,76,61,0.1)] text-[var(--danger)]',
                  )}
                >
                  {quote.change_pct >= 0 ? '+' : ''}{quote.change?.toFixed(2) ?? '--'} / {quote.change_pct >= 0 ? '+' : ''}{quote.change_pct.toFixed(2)}%
                </div>
              )}
            </div>

            <p className="mt-4 max-w-3xl text-sm leading-7 text-[var(--text-secondary)]">
              Switch symbols fast, compare relative performance, draw on the chart, and keep the live feed in the same workspace.
              The trading surface stays dense without falling back to the old dark chrome.
            </p>

            <div className="mt-5 flex flex-wrap gap-2">
              <span className="shell-chip text-[11px] font-medium">{feedLabel}</span>
              <span className="shell-chip text-[11px] font-medium">{historyFeedLabel}</span>
              <span className="shell-chip text-[11px] font-medium">{TOOLBAR_TIMEFRAMES[tfIdx]?.label ?? '1D'}</span>
              <span
                className={clsx(
                  'shell-chip text-[11px] font-semibold',
                  badge.label === 'CLOSED / STALE'
                    ? 'border-[rgba(245,158,11,0.24)] bg-[rgba(245,158,11,0.12)] text-[var(--accent)]'
                    : 'border-[rgba(31,157,104,0.2)] bg-[rgba(31,157,104,0.1)] text-[var(--success)]',
                )}
              >
                <span className={clsx('h-2 w-2 rounded-full', badge.dotClass)} />
                {badge.label}
                {badge.age && <span className="text-[var(--text-muted)]">({badge.age})</span>}
              </span>
            </div>
          </div>
        </div>

        <div className="grid gap-4 sm:grid-cols-3 xl:grid-cols-1">
          <MarketSignalCard label="Market Cap" value={formatCompact(quote?.market_cap)} />
          <MarketSignalCard label="Volume" value={quote?.volume?.toLocaleString('en-US') ?? '--'} />
          <MarketSignalCard
            label="52W Range"
            value={quote?.year_low != null && quote?.year_high != null
              ? `$${quote.year_low.toFixed(0)} - $${quote.year_high.toFixed(0)}`
              : '--'}
            tone="warning"
          />
        </div>
      </section>
      </ErrorBoundary>

      <ErrorBoundary>
      <section className="shell-panel p-5 sm:p-6">
        <div className="flex flex-col gap-4">
          <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
            <div className="max-w-2xl">
              <div className="shell-kicker">Controls</div>
              <h2 className="display-font mt-2 text-[1.75rem] leading-none text-[var(--text-primary)]">
                Chart command rail
              </h2>
              <p className="mt-2 text-sm leading-6 text-[var(--text-secondary)]">
                Search, compare, set alerts, and adjust chart context without losing the active symbol state.
              </p>
            </div>

            <div className="flex flex-wrap items-center gap-3">
              <form onSubmit={handleSearch} className="flex flex-wrap items-center gap-2">
                <input
                  value={searchInput}
                  onChange={(e) => setSearch(e.target.value.toUpperCase())}
                  placeholder="Enter symbol..."
                  className="min-w-[11rem] rounded-2xl border border-[var(--border)] bg-[var(--bg-input)] px-4 py-3 text-sm font-medium text-[var(--text-primary)] outline-none transition-colors placeholder:text-[var(--text-muted)] focus:border-[var(--accent)]"
                />
                <button
                  type="submit"
                  className="rounded-2xl bg-[var(--accent)] px-4 py-3 text-sm font-semibold text-white transition-colors hover:bg-[var(--accent-hover)]"
                >
                  Open symbol
                </button>
              </form>

              <div className="flex flex-wrap items-center gap-2">
                {compMode && (
                  <form
                    onSubmit={(e) => {
                      e.preventDefault()
                      const inp = e.currentTarget.elements.namedItem('comp') as HTMLInputElement
                      setCompSymbol(inp.value.toUpperCase())
                    }}
                    className="flex flex-wrap items-center gap-2"
                  >
                    <input
                      name="comp"
                      defaultValue={compSymbol}
                      placeholder="Compare vs MSFT"
                      className="min-w-[12rem] rounded-2xl border border-[var(--border)] bg-[var(--bg-input)] px-4 py-3 text-sm font-medium text-[var(--text-primary)] outline-none transition-colors placeholder:text-[var(--text-muted)] focus:border-[var(--accent)]"
                    />
                    <button
                      type="submit"
                      className="rounded-2xl border border-[var(--border)] bg-[var(--bg-hover)] px-4 py-3 text-sm font-semibold text-[var(--text-primary)] transition-colors hover:border-[var(--accent)] hover:text-[var(--accent)]"
                    >
                      Set compare
                    </button>
                  </form>
                )}

                <button
                  onClick={toggleCompMode}
                  className={clsx(
                    'rounded-2xl border px-4 py-3 text-sm font-semibold transition-colors',
                    compMode
                      ? 'border-[rgba(245,158,11,0.3)] bg-[rgba(245,158,11,0.12)] text-[var(--accent)]'
                      : 'border-[var(--border)] bg-transparent text-[var(--text-secondary)] hover:border-[var(--accent)] hover:text-[var(--text-primary)]',
                  )}
                >
                  {compMode ? 'Hide compare' : 'Add compare'}
                </button>
              </div>
            </div>
          </div>

          <ChartToolbar
            activeTfIdx={tfIdx}
            onTfChange={setTfIdx}
            chartContainer={chartContainerRef.current}
            chartRef={mainChartApi}
            isLoading={loading}
            onCreateAlert={() => setShowAlertForm(true)}
            className="!rounded-[22px] !border-[var(--border)] !bg-[var(--bg-hover)]"
          />

          <DrawingTools
            symbol={selectedSymbol}
            timeframe={currentTF.interval}
            className="rounded-[22px] border border-[var(--border)] bg-[var(--bg-hover)] px-3 py-2"
          />
        </div>
      </section>
      </ErrorBoundary>

      <section className="grid min-h-0 flex-1 gap-6 xl:grid-cols-[minmax(0,1fr)_280px]">
        <ErrorBoundary>
        <div className="flex min-h-0 min-w-0 flex-col gap-2" ref={chartContainerRef}>
          <div className="shell-panel shell-grid relative flex-1 overflow-hidden">
            {loading && (
              <div className="absolute inset-0 z-10 flex items-center justify-center bg-[rgba(243,237,227,0.35)] backdrop-blur-sm">
                <span className="text-xs font-sans text-[var(--text-muted)]">Loading...</span>
              </div>
            )}

            <div className="flex flex-wrap items-center gap-2 border-b border-[var(--border)] px-5 py-4">
              <div>
                <div className="shell-kicker">Main chart</div>
                <div className="mt-1 flex flex-wrap items-center gap-2">
                  <span className="text-xl font-semibold text-[var(--text-primary)]">{selectedSymbol}</span>
                  <span className="shell-chip px-3 py-1 text-[10px] font-mono">{historyFeedLabel}</span>
                </div>
              </div>

              {compMode && compSymbol && (
                <div className="shell-chip px-3 py-1 text-[10px] font-mono xl:ml-auto">
                  vs {compSymbol} [normalized %]
                </div>
              )}

              <span className={clsx('flex items-center gap-1.5 text-[11px] font-sans xl:ml-auto', badge.textClass)}>
                <span className={clsx('h-1.5 w-1.5 rounded-full', badge.dotClass)} />
                {badge.label}
                {badge.age && <span className="text-[var(--text-muted)]">({badge.age})</span>}
              </span>
            </div>

            <div className="h-[calc(100%-76px)] min-h-[30rem]">
              <TradingChart
                symbol={selectedSymbol}
                timeframe={currentTF.interval}
                className="h-full"
                onChartReady={handleMainChartReady}
                onStale={refreshActiveCharts}
              />
            </div>
          </div>

          <ResizeHandle onDelta={handleVolResize} onDoubleClick={resetVolHeight} />

          <div className="shell-panel shrink-0 overflow-hidden" style={{ height: volumeHeight }}>
            <VolumePanel
              symbol={selectedSymbol}
              mainChart={mainChartApi}
              onChartReady={handleVolReady}
              className="h-full"
            />
          </div>

          <ResizeHandle onDelta={handleIndResize} onDoubleClick={resetIndHeight} />

          <div className="shell-panel shrink-0 overflow-hidden" style={{ height: indicatorHeight }}>
            <IndicatorPanel
              symbol={selectedSymbol}
              mainChart={mainChartApi}
              onRSIReady={handleRsiReady}
              onMACDReady={handleMacdReady}
              className="h-full"
              style={{ height: indicatorHeight }}
            />
          </div>
        </div>
        </ErrorBoundary>

        <ErrorBoundary>
        <aside className="space-y-4">
          <div className="shell-panel p-4">
            <div className="mb-3">
              <div className="shell-kicker">Snapshot</div>
              <div className="mt-1 text-base font-semibold text-[var(--text-primary)]">Quote card</div>
            </div>
            {quote ? (
              <TickerCard quote={quote} />
            ) : (
              <div className="space-y-3 rounded-[24px] border border-[var(--border)] bg-[var(--bg-hover)] p-4">
                <Skeleton className="h-4 w-20" />
                <Skeleton className="h-8 w-28" />
                <Skeleton className="h-3 w-16" />
                <Skeleton className="h-3 w-24" />
              </div>
            )}
          </div>

          <div className="shell-panel p-5">
            <div className="mb-3 flex items-center justify-between gap-3">
              <div>
                <div className="shell-kicker">Research handoff</div>
                <h3 className="mt-1 text-base font-semibold text-[var(--text-primary)]">Fundamentals Preview</h3>
              </div>
              <button
                type="button"
                onClick={() => setRoute('stock')}
                className="text-xs font-semibold text-[var(--text-secondary)] transition-colors hover:text-[var(--accent)]"
              >
                Open full stock analysis
              </button>
            </div>

            <div className="space-y-2 text-[11px]">
              <div className="flex items-center justify-between">
                <span className="text-[var(--text-secondary)]">Live Feed</span>
                <span className="font-mono text-[var(--text-primary)]">{feedLabel}</span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-[var(--text-secondary)]">History Feed</span>
                <span className="font-mono text-[var(--text-primary)]">{historyFeedLabel}</span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-[var(--text-secondary)]">Market Cap</span>
                <span className="font-mono text-[var(--text-primary)]">{formatCompact(quote?.market_cap)}</span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-[var(--text-secondary)]">Volume</span>
                <span className="font-mono text-[var(--text-primary)]">{quote?.volume?.toLocaleString('en-US') ?? '--'}</span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-[var(--text-secondary)]">52W High</span>
                <span className="font-mono text-[var(--text-primary)]">
                  {quote?.year_high != null ? `$${quote.year_high.toFixed(2)}` : '--'}
                </span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-[var(--text-secondary)]">52W Low</span>
                <span className="font-mono text-[var(--text-primary)]">
                  {quote?.year_low != null ? `$${quote.year_low.toFixed(2)}` : '--'}
                </span>
              </div>
            </div>
          </div>

          {compMode && compSymbol && quotes[compSymbol] && (
            <div className="shell-panel p-4">
              <div className="mb-3">
                <div className="shell-kicker">Comparison</div>
                <div className="mt-1 text-base font-semibold text-[var(--text-primary)]">{compSymbol}</div>
              </div>
              <TickerCard quote={quotes[compSymbol]} />
            </div>
          )}
        </aside>
        </ErrorBoundary>
      </section>

      <ErrorBoundary>
      {showAlertForm && (
        <AlertForm
          initialSymbol={selectedSymbol}
          onClose={() => setShowAlertForm(false)}
        />
      )}
      </ErrorBoundary>
    </div>
  )
}

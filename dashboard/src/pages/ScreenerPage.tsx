import React, { useEffect, useRef, useCallback } from 'react'
import { useScreenerStore } from '@/store'
import { useToast } from '@/components/ui/ToastProvider'
import UniverseSelector from '@/components/screener/UniverseSelector'
import PresetSelector from '@/components/screener/PresetSelector'
import FilterBuilder from '@/components/screener/FilterBuilder'
import ScanResultsTable from '@/components/screener/ScanResultsTable'
import IBKRQuickScans from '@/components/screener/IBKRQuickScans'
import ErrorBoundary from '@/components/ui/ErrorBoundary'

const INTERVALS = [
  { value: '1d', label: '1D' },
  { value: '1h', label: '1H' },
  { value: '15m', label: '15m' },
  { value: '5m', label: '5m' },
]

const PERIODS: Record<string, { value: string; label: string }[]> = {
  '1d':  [{ value: '1mo', label: '1M' }, { value: '3mo', label: '3M' }, { value: '6mo', label: '6M' }, { value: '1y', label: '1Y' }, { value: '2y', label: '2Y' }],
  '1h':  [{ value: '5d', label: '5D' }, { value: '1mo', label: '1M' }, { value: '3mo', label: '3M' }],
  '15m': [{ value: '1d', label: '1D' }, { value: '5d', label: '5D' }, { value: '1mo', label: '1M' }],
  '5m':  [{ value: '1d', label: '1D' }, { value: '5d', label: '5D' }, { value: '1mo', label: '1M' }],
}

function SectionHeader({ eyebrow, title, meta }: { eyebrow: string; title: string; meta?: React.ReactNode }) {
  return (
    <div className="flex items-end justify-between gap-4">
      <div>
        <div className="text-[10px] font-sans uppercase tracking-[0.22em] text-zinc-500">{eyebrow}</div>
        <h2 className="mt-1 text-lg font-sans font-semibold text-zinc-50">{title}</h2>
      </div>
      {meta}
    </div>
  )
}

function EmptyState() {
  return (
    <div className="flex flex-col items-center justify-center py-16 px-6 text-center">
      <div className="mb-4 flex h-14 w-14 items-center justify-center rounded-lg border border-zinc-800 bg-zinc-900">
        <svg
          xmlns="http://www.w3.org/2000/svg"
          className="h-6 w-6 text-zinc-500"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.5"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <circle cx="11" cy="11" r="8" />
          <path d="M21 21l-4.35-4.35" />
          <path d="M8 11h6M11 8v6" />
        </svg>
      </div>
      <p className="text-sm font-sans font-medium text-zinc-400">No scan results yet</p>
      <p className="mt-1 max-w-sm text-xs font-sans text-zinc-500">
        Build your filter stack, choose a universe, then hit <kbd className="rounded border border-zinc-700 bg-zinc-800 px-1.5 py-0.5 font-mono text-zinc-300">Ctrl+Enter</kbd> to scan.
      </p>
    </div>
  )
}

export default function ScreenerPage() {
  const toast = useToast()
  const {
    scanning,
    results,
    filters,
    presetsLoaded,
    selectedUniverse,
    interval,
    period,
    elapsedMs,
    totalSymbols,
    loadPresets,
    loadUniverses,
    runScan,
    setInterval,
    setPeriod,
    setUniverse,
    setCustomSymbols,
  } = useScreenerStore()

  const hasScannedRef = useRef(false)

  useEffect(() => {
    if (!presetsLoaded) {
      loadPresets()
      loadUniverses()
    }
  }, [presetsLoaded, loadPresets, loadUniverses])

  const handleScan = useCallback(async () => {
    if (filters.length === 0) {
      toast.error('Add at least one filter before scanning')
      return
    }
    hasScannedRef.current = true
    try {
      await runScan()
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Scan failed'
      toast.error(msg)
    }
  }, [filters.length, runScan, toast])

  // Keyboard shortcut: Ctrl+Enter to run scan
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
        e.preventDefault()
        handleScan()
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [handleScan])

  const availablePeriods = PERIODS[interval] ?? PERIODS['1d']
  const topResult = results[0] ?? null

  const handleIntervalChange = (newInterval: string) => {
    setInterval(newInterval)
    const periods = PERIODS[newInterval] ?? PERIODS['1d']
    const validPeriod = periods.find((p) => p.value === period)
    if (!validPeriod) {
      setPeriod(periods[periods.length - 1].value)
    }
  }

  const handleIBKRSymbols = (symbols: string[]) => {
    setUniverse('custom')
    setCustomSymbols(symbols.join(', '))
    toast.success(`Loaded ${symbols.length} symbols from IBKR scan`)
  }

  const showPrescanEmpty = !hasScannedRef.current && !scanning && results.length === 0

  // Setup distribution from results
  const setupCounts = results.reduce<Record<string, number>>((acc, r) => {
    acc[r.setup] = (acc[r.setup] || 0) + 1
    return acc
  }, {})

  return (
    <div className="flex flex-col gap-5 h-full overflow-y-auto">
      {/* Header */}
      <ErrorBoundary>
        <section className="card rounded-lg p-5">
          <div className="flex flex-col gap-5 xl:flex-row xl:items-end xl:justify-between">
            <div className="min-w-0">
              <div className="text-[10px] font-sans uppercase tracking-[0.24em] text-zinc-500">Scanner</div>
              <h1 className="mt-1 text-3xl font-sans font-semibold tracking-tight text-zinc-50">Stock Screener</h1>
              <p className="mt-2 max-w-2xl text-sm font-sans text-zinc-400">
                Scan a defined universe, layer technical filters, save reusable presets, and jump directly into charting or full stock analysis.
              </p>
            </div>

            <div className="grid gap-3 sm:grid-cols-4">
              <div className="rounded-lg border border-zinc-800 bg-zinc-900 px-3 py-2.5">
                <div className="text-[10px] font-sans uppercase tracking-[0.18em] text-zinc-400">Universe</div>
                <div className="mt-1 text-sm font-mono font-semibold text-zinc-50">{selectedUniverse.toUpperCase()}</div>
              </div>
              <div className="rounded-lg border border-zinc-800 bg-zinc-900 px-3 py-2.5">
                <div className="text-[10px] font-sans uppercase tracking-[0.18em] text-zinc-400">Filters</div>
                <div className="mt-1 text-sm font-mono font-semibold text-zinc-50">{filters.length}</div>
              </div>
              <div className="rounded-lg border border-zinc-800 bg-zinc-900 px-3 py-2.5">
                <div className="text-[10px] font-sans uppercase tracking-[0.18em] text-zinc-400">Window</div>
                <div className="mt-1 text-sm font-mono font-semibold text-zinc-50">{interval.toUpperCase()} / {period.toUpperCase()}</div>
              </div>
              <div className="rounded-lg border border-zinc-800 bg-zinc-900 px-3 py-2.5">
                <div className="text-[10px] font-sans uppercase tracking-[0.18em] text-zinc-400">Top Setup</div>
                <div className="mt-1 text-sm font-mono font-semibold text-zinc-50">
                  {topResult ? `${topResult.symbol} ${topResult.screener_score.toFixed(0)}` : '--'}
                </div>
              </div>
            </div>
          </div>

          {/* Setup distribution pills */}
          {results.length > 0 && (
            <div className="mt-4 flex flex-wrap gap-2">
              {Object.entries(setupCounts).sort((a, b) => b[1] - a[1]).map(([setup, count]) => (
                <div key={setup} className="rounded-md border border-zinc-700 bg-zinc-800 px-2.5 py-1 text-[11px] font-sans">
                  <span className="text-zinc-400">{setup}</span>{' '}
                  <span className="font-mono font-bold text-zinc-200">{count}</span>
                </div>
              ))}
              {elapsedMs > 0 && (
                <div className="rounded-md border border-zinc-700 bg-zinc-800 px-2.5 py-1 text-[11px] font-mono text-zinc-500">
                  {totalSymbols} scanned in {elapsedMs < 1000 ? `${elapsedMs}ms` : `${(elapsedMs / 1000).toFixed(1)}s`}
                </div>
              )}
            </div>
          )}
        </section>
      </ErrorBoundary>

      {/* Universe + Scan Settings */}
      <section className="grid gap-4 xl:grid-cols-[minmax(0,1.4fr)_minmax(320px,0.9fr)]">
        <ErrorBoundary>
          <div className="card rounded-lg p-5">
            <SectionHeader eyebrow="Universe" title="Scan Coverage" />
            <div className="mt-4">
              <UniverseSelector />
            </div>
          </div>
        </ErrorBoundary>

        <ErrorBoundary>
          <div className="card rounded-lg p-5">
            <SectionHeader eyebrow="Window" title="Scan Settings" />
            <div className="mt-4 space-y-4">
              <div>
                <div className="text-[10px] font-sans uppercase tracking-[0.18em] text-zinc-500">Interval</div>
                <div className="mt-2 flex flex-wrap gap-2">
                  {INTERVALS.map((item) => (
                    <button
                      key={item.value}
                      type="button"
                      onClick={() => handleIntervalChange(item.value)}
                      className={
                        interval === item.value
                          ? 'rounded-lg border border-zinc-700 bg-zinc-800 px-3 py-2 text-[11px] font-sans font-medium text-white'
                          : 'rounded-lg border border-zinc-800 bg-zinc-900 px-3 py-2 text-[11px] font-sans font-medium text-zinc-400 transition-colors hover:border-zinc-700 hover:text-zinc-50'
                      }
                    >
                      {item.label}
                    </button>
                  ))}
                </div>
              </div>

              <div>
                <div className="text-[10px] font-sans uppercase tracking-[0.18em] text-zinc-500">Lookback</div>
                <div className="mt-2 flex flex-wrap gap-2">
                  {availablePeriods.map((item) => (
                    <button
                      key={item.value}
                      type="button"
                      onClick={() => setPeriod(item.value)}
                      className={
                        period === item.value
                          ? 'rounded-lg border border-zinc-700 bg-zinc-800 px-3 py-2 text-[11px] font-sans font-medium text-zinc-50'
                          : 'rounded-lg border border-zinc-800 bg-zinc-900 px-3 py-2 text-[11px] font-sans font-medium text-zinc-400 transition-colors hover:border-zinc-700 hover:text-zinc-50'
                      }
                    >
                      {item.label}
                    </button>
                  ))}
                </div>
              </div>

              <div className="rounded-lg border border-zinc-700 bg-zinc-800/50 p-4">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <div className="text-[10px] font-sans uppercase tracking-[0.18em] text-zinc-500">Execution</div>
                    <div className="mt-1 text-sm font-sans text-zinc-400">
                      Run filters against the selected universe.
                    </div>
                    <div className="mt-1 text-[10px] font-sans text-zinc-600">
                      Ctrl+Enter
                    </div>
                  </div>
                  <button
                    type="button"
                    onClick={handleScan}
                    disabled={scanning}
                    className={
                      scanning
                        ? 'rounded-lg border border-zinc-700 bg-zinc-800 px-5 py-2.5 text-[11px] font-sans font-medium text-zinc-500 cursor-not-allowed'
                        : 'rounded-lg border border-cyan-500/40 bg-cyan-500/15 px-5 py-2.5 text-[11px] font-sans font-semibold text-cyan-300 transition-colors hover:bg-cyan-500/25'
                    }
                  >
                    {scanning ? (
                      <span className="flex items-center gap-2">
                        <span className="h-3 w-3 animate-spin rounded-full border-2 border-zinc-600 border-t-cyan-400" />
                        Scanning...
                      </span>
                    ) : 'Run Scan'}
                  </button>
                </div>
              </div>
            </div>
          </div>
        </ErrorBoundary>
      </section>

      {/* Filter Stack + Presets + IBKR Quick Scans */}
      <section className="grid gap-4 xl:grid-cols-[minmax(0,1.2fr)_minmax(320px,0.8fr)]">
        <ErrorBoundary>
          <div className="card rounded-lg p-5">
            <SectionHeader eyebrow="Rules" title="Filter Stack" />
            <div className="mt-4">
              <FilterBuilder />
            </div>
          </div>
        </ErrorBoundary>

        <div className="flex flex-col gap-4">
          <ErrorBoundary>
            <div className="card rounded-lg p-5">
              <SectionHeader eyebrow="Presets" title="Saved Screens" />
              <div className="mt-4">
                <PresetSelector />
              </div>
            </div>
          </ErrorBoundary>

          <ErrorBoundary>
            <div className="card rounded-lg p-5">
              <SectionHeader eyebrow="IBKR" title="Quick Scans" />
              <div className="mt-4">
                <IBKRQuickScans onSelectSymbols={handleIBKRSymbols} />
              </div>
            </div>
          </ErrorBoundary>
        </div>
      </section>

      {/* Results */}
      <ErrorBoundary>
        <section className="card rounded-lg flex-1 min-h-0">
          <div className="flex items-center justify-between gap-4 border-b border-zinc-800 px-5 py-4">
            <SectionHeader
              eyebrow="Results"
              title="Scan Output"
              meta={(
                <div className="flex items-center gap-2">
                  {results.length > 0 && (
                    <a
                      href={`http://127.0.0.1:5001/ib_multichart.html?symbols=${results.slice(0, 9).map(r => r.symbol).join(',')}&tf=D`}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="rounded-lg border border-cyan-500/30 bg-cyan-500/10 px-3 py-2 text-[11px] font-semibold text-cyan-300 hover:bg-cyan-500/20 transition-colors"
                    >
                      Multi-Chart ({Math.min(results.length, 9)})
                    </a>
                  )}
                </div>
              )}
            />
          </div>

          {showPrescanEmpty ? (
            <EmptyState />
          ) : (
            <div className="p-5">
              <ScanResultsTable />
            </div>
          )}
        </section>
      </ErrorBoundary>
    </div>
  )
}

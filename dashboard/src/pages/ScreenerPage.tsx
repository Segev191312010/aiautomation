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
        <div className="text-[10px] font-sans uppercase tracking-[0.22em] text-[var(--text-muted)]">{eyebrow}</div>
        <h2 className="mt-1 text-lg font-sans font-semibold text-[var(--text-primary)]">{title}</h2>
      </div>
      {meta}
    </div>
  )
}

function EmptyState() {
  return (
    <div className="flex flex-col items-center justify-center py-16 px-6 text-center">
      <div className="mb-4 flex h-14 w-14 items-center justify-center rounded-lg border border-[var(--border)] bg-[var(--bg-primary)]">
        <svg
          xmlns="http://www.w3.org/2000/svg"
          className="h-6 w-6 text-[var(--text-muted)]"
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
      <p className="text-sm font-sans font-medium text-[var(--text-secondary)]">No scan results yet</p>
      <p className="mt-1 max-w-sm text-xs font-sans text-[var(--text-muted)]">
        Build your filter stack, choose a universe, then hit <kbd className="rounded border border-[var(--border)] bg-[var(--bg-hover)] px-1.5 py-0.5 font-mono text-[var(--text-secondary)]">Ctrl+Enter</kbd> to scan.
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
              <div className="text-[10px] font-sans uppercase tracking-[0.24em] text-[var(--text-muted)]">Scanner</div>
              <h1 className="mt-1 text-3xl font-sans font-semibold tracking-tight text-[var(--text-primary)]">Stock Screener</h1>
              <p className="mt-2 max-w-2xl text-sm font-sans text-[var(--text-secondary)]">
                Scan a defined universe, layer technical filters, save reusable presets, and jump directly into charting or full stock analysis.
              </p>
            </div>

            <div className="grid gap-3 sm:grid-cols-4">
              <div className="rounded-lg border border-[var(--border)] bg-[var(--bg-primary)] px-3 py-2.5">
                <div className="text-[10px] font-sans uppercase tracking-[0.18em] text-[var(--text-secondary)]">Universe</div>
                <div className="mt-1 text-sm font-mono font-semibold text-[var(--text-primary)]">{selectedUniverse.toUpperCase()}</div>
              </div>
              <div className="rounded-lg border border-[var(--border)] bg-[var(--bg-primary)] px-3 py-2.5">
                <div className="text-[10px] font-sans uppercase tracking-[0.18em] text-[var(--text-secondary)]">Filters</div>
                <div className="mt-1 text-sm font-mono font-semibold text-[var(--text-primary)]">{filters.length}</div>
              </div>
              <div className="rounded-lg border border-[var(--border)] bg-[var(--bg-primary)] px-3 py-2.5">
                <div className="text-[10px] font-sans uppercase tracking-[0.18em] text-[var(--text-secondary)]">Window</div>
                <div className="mt-1 text-sm font-mono font-semibold text-[var(--text-primary)]">{interval.toUpperCase()} / {period.toUpperCase()}</div>
              </div>
              <div className="rounded-lg border border-[var(--border)] bg-[var(--bg-primary)] px-3 py-2.5">
                <div className="text-[10px] font-sans uppercase tracking-[0.18em] text-[var(--text-secondary)]">Top Setup</div>
                <div className="mt-1 text-sm font-mono font-semibold text-[var(--text-primary)]">
                  {topResult ? `${topResult.symbol} ${topResult.screener_score.toFixed(0)}` : '--'}
                </div>
              </div>
            </div>
          </div>

          {/* Setup distribution pills */}
          {results.length > 0 && (
            <div className="mt-4 flex flex-wrap gap-2">
              {Object.entries(setupCounts).sort((a, b) => b[1] - a[1]).map(([setup, count]) => (
                <div key={setup} className="rounded-md border border-[var(--border)] bg-[var(--bg-hover)] px-2.5 py-1 text-[11px] font-sans">
                  <span className="text-[var(--text-secondary)]">{setup}</span>{' '}
                  <span className="font-mono font-bold text-[var(--text-primary)]">{count}</span>
                </div>
              ))}
              {elapsedMs > 0 && (
                <div className="rounded-md border border-[var(--border)] bg-[var(--bg-hover)] px-2.5 py-1 text-[11px] font-mono text-[var(--text-muted)]">
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
                <div className="text-[10px] font-sans uppercase tracking-[0.18em] text-[var(--text-muted)]">Interval</div>
                <div className="mt-2 flex flex-wrap gap-2">
                  {INTERVALS.map((item) => (
                    <button
                      key={item.value}
                      type="button"
                      onClick={() => handleIntervalChange(item.value)}
                      className={
                        interval === item.value
                          ? 'rounded-lg border border-[var(--accent)] bg-[var(--accent-soft)] px-3 py-2 text-[11px] font-sans font-medium text-white'
                          : 'rounded-lg border border-[var(--border)] bg-[var(--bg-primary)] px-3 py-2 text-[11px] font-sans font-medium text-[var(--text-secondary)] transition-colors hover:border-[var(--accent)] hover:text-[var(--text-primary)]'
                      }
                    >
                      {item.label}
                    </button>
                  ))}
                </div>
              </div>

              <div>
                <div className="text-[10px] font-sans uppercase tracking-[0.18em] text-[var(--text-muted)]">Lookback</div>
                <div className="mt-2 flex flex-wrap gap-2">
                  {availablePeriods.map((item) => (
                    <button
                      key={item.value}
                      type="button"
                      onClick={() => setPeriod(item.value)}
                      className={
                        period === item.value
                          ? 'rounded-lg border border-[var(--accent)] bg-[var(--accent-soft)] px-3 py-2 text-[11px] font-sans font-medium text-white'
                          : 'rounded-lg border border-[var(--border)] bg-[var(--bg-primary)] px-3 py-2 text-[11px] font-sans font-medium text-[var(--text-secondary)] transition-colors hover:border-[var(--accent)] hover:text-[var(--text-primary)]'
                      }
                    >
                      {item.label}
                    </button>
                  ))}
                </div>
              </div>

              <div className="rounded-lg border border-[var(--border)] bg-[var(--bg-hover)] p-4">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <div className="text-[10px] font-sans uppercase tracking-[0.18em] text-[var(--text-muted)]">Execution</div>
                    <div className="mt-1 text-sm font-sans text-[var(--text-secondary)]">
                      Run filters against the selected universe.
                    </div>
                    <div className="mt-1 text-[10px] font-sans text-[var(--text-muted)]">
                      Ctrl+Enter
                    </div>
                  </div>
                  <button
                    type="button"
                    onClick={handleScan}
                    disabled={scanning}
                    className={
                      scanning
                        ? 'rounded-lg border border-[var(--border)] bg-[var(--bg-primary)] px-5 py-2.5 text-[11px] font-sans font-medium text-[var(--text-muted)] cursor-not-allowed'
                        : 'rounded-lg border border-[var(--accent)] bg-[var(--accent-soft)] px-5 py-2.5 text-[11px] font-sans font-semibold text-white transition-colors hover:bg-[color:rgba(245,158,11,0.24)]'
                    }
                  >
                    {scanning ? (
                      <span className="flex items-center gap-2">
                        <span className="h-3 w-3 animate-spin rounded-full border-2 border-[var(--border)] border-t-[var(--accent)]" />
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
          <div className="flex items-center justify-between gap-4 border-b border-[var(--border)] px-5 py-4">
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
                      className="rounded-lg border border-[var(--accent)] bg-[var(--accent-soft)] px-3 py-2 text-[11px] font-semibold text-white transition-colors hover:bg-[color:rgba(245,158,11,0.24)]"
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

import React, { useEffect, useRef } from 'react'
import { useScreenerStore } from '@/store'
import { useToast } from '@/components/ui/ToastProvider'
import UniverseSelector from '@/components/screener/UniverseSelector'
import PresetSelector from '@/components/screener/PresetSelector'
import FilterBuilder from '@/components/screener/FilterBuilder'
import ScanResultsTable from '@/components/screener/ScanResultsTable'

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
        <div className="text-[10px] font-sans uppercase tracking-[0.22em] text-gray-400">{eyebrow}</div>
        <h2 className="mt-1 text-lg font-sans font-semibold text-gray-900">{title}</h2>
      </div>
      {meta}
    </div>
  )
}

function EmptyState() {
  return (
    <div className="flex flex-col items-center justify-center py-16 px-6 text-center">
      <div className="mb-4 flex h-14 w-14 items-center justify-center rounded-lg border border-gray-200 bg-gray-50">
        <svg
          xmlns="http://www.w3.org/2000/svg"
          className="h-6 w-6 text-gray-400"
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
      <p className="text-sm font-sans font-medium text-gray-600">No scan results yet</p>
      <p className="mt-1 max-w-sm text-xs font-sans text-gray-400">
        Build your filter stack, choose a universe, then run a scan to rank names and drill straight into market or stock analysis.
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
    loadPresets,
    loadUniverses,
    runScan,
    setInterval,
    setPeriod,
  } = useScreenerStore()

  const hasScannedRef = useRef(false)

  useEffect(() => {
    if (!presetsLoaded) {
      loadPresets()
      loadUniverses()
    }
  }, [presetsLoaded, loadPresets, loadUniverses])

  const handleScan = async () => {
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
  }

  const availablePeriods = PERIODS[interval] ?? PERIODS['1d']

  const handleIntervalChange = (newInterval: string) => {
    setInterval(newInterval)
    const periods = PERIODS[newInterval] ?? PERIODS['1d']
    const validPeriod = periods.find((p) => p.value === period)
    if (!validPeriod) {
      setPeriod(periods[periods.length - 1].value)
    }
  }

  const showPrescanEmpty = !hasScannedRef.current && !scanning && results.length === 0

  return (
    <div className="flex flex-col gap-5 h-full overflow-y-auto">
      <section className="card rounded-lg p-5 shadow-card">
        <div className="flex flex-col gap-5 xl:flex-row xl:items-end xl:justify-between">
          <div className="min-w-0">
            <div className="text-[10px] font-sans uppercase tracking-[0.24em] text-gray-400">Scanner</div>
            <h1 className="mt-1 text-3xl font-sans font-semibold tracking-tight text-gray-900">Stock Screener</h1>
            <p className="mt-2 max-w-2xl text-sm font-sans text-gray-600">
              Scan a defined universe, layer technical filters, save reusable presets, and jump directly into the market workspace or full stock analysis.
            </p>
          </div>

          <div className="grid gap-3 sm:grid-cols-4">
            <div className="rounded-lg border border-gray-200 bg-gray-50 px-3 py-2.5">
              <div className="text-[10px] font-sans uppercase tracking-[0.18em] text-gray-500">Universe</div>
              <div className="mt-1 text-sm font-mono font-semibold text-gray-900">{selectedUniverse.toUpperCase()}</div>
            </div>
            <div className="rounded-lg border border-gray-200 bg-gray-50 px-3 py-2.5">
              <div className="text-[10px] font-sans uppercase tracking-[0.18em] text-gray-500">Filters</div>
              <div className="mt-1 text-sm font-mono font-semibold text-gray-900">{filters.length}</div>
            </div>
            <div className="rounded-lg border border-gray-200 bg-gray-50 px-3 py-2.5">
              <div className="text-[10px] font-sans uppercase tracking-[0.18em] text-gray-500">Window</div>
              <div className="mt-1 text-sm font-mono font-semibold text-gray-900">{interval.toUpperCase()} / {period.toUpperCase()}</div>
            </div>
            <div className="rounded-lg border border-gray-200 bg-gray-50 px-3 py-2.5">
              <div className="text-[10px] font-sans uppercase tracking-[0.18em] text-gray-500">Matches</div>
              <div className="mt-1 text-sm font-mono font-semibold text-gray-900">{results.length}</div>
            </div>
          </div>
        </div>
      </section>

      <section className="grid gap-4 xl:grid-cols-[minmax(0,1.4fr)_minmax(320px,0.9fr)]">
        <div className="card rounded-lg p-5 shadow-card">
          <SectionHeader eyebrow="Universe" title="Scan Coverage" />
          <div className="mt-4">
            <UniverseSelector />
          </div>
        </div>

        <div className="card rounded-lg p-5 shadow-card">
          <SectionHeader eyebrow="Window" title="Scan Settings" />
          <div className="mt-4 space-y-4">
            <div>
              <div className="text-[10px] font-sans uppercase tracking-[0.18em] text-gray-400">Interval</div>
              <div className="mt-2 flex flex-wrap gap-2">
                {INTERVALS.map((item) => (
                  <button
                    key={item.value}
                    type="button"
                    onClick={() => handleIntervalChange(item.value)}
                    className={
                      interval === item.value
                        ? 'rounded-lg border border-gray-900 bg-gray-900 px-3 py-2 text-[11px] font-sans font-medium text-white'
                        : 'rounded-lg border border-gray-200 bg-white px-3 py-2 text-[11px] font-sans font-medium text-gray-600 transition-colors hover:border-gray-400 hover:text-gray-900'
                    }
                  >
                    {item.label}
                  </button>
                ))}
              </div>
            </div>

            <div>
              <div className="text-[10px] font-sans uppercase tracking-[0.18em] text-gray-400">Lookback</div>
              <div className="mt-2 flex flex-wrap gap-2">
                {availablePeriods.map((item) => (
                  <button
                    key={item.value}
                    type="button"
                    onClick={() => setPeriod(item.value)}
                    className={
                      period === item.value
                        ? 'rounded-lg border border-gray-900 bg-gray-100 px-3 py-2 text-[11px] font-sans font-medium text-gray-900'
                        : 'rounded-lg border border-gray-200 bg-white px-3 py-2 text-[11px] font-sans font-medium text-gray-600 transition-colors hover:border-gray-400 hover:text-gray-900'
                    }
                  >
                    {item.label}
                  </button>
                ))}
              </div>
            </div>

            <div className="rounded-lg border border-[#E8E4DF] bg-[#FAF8F5] p-4">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <div className="text-[10px] font-sans uppercase tracking-[0.18em] text-gray-400">Execution</div>
                  <div className="mt-1 text-sm font-sans text-gray-700">
                    Run the current filter stack against the selected universe.
                  </div>
                </div>
                <button
                  type="button"
                  onClick={handleScan}
                  disabled={scanning}
                  className={
                    scanning
                      ? 'rounded-lg border border-gray-200 bg-gray-100 px-4 py-2 text-[11px] font-sans font-medium text-gray-500 cursor-not-allowed'
                      : 'rounded-lg border border-gray-900 bg-gray-900 px-4 py-2 text-[11px] font-sans font-medium text-white transition-colors hover:bg-gray-800'
                  }
                >
                  {scanning ? 'Scanning...' : 'Run Scan'}
                </button>
              </div>
            </div>
          </div>
        </div>
      </section>

      <section className="grid gap-4 xl:grid-cols-[minmax(0,1.2fr)_minmax(320px,0.8fr)]">
        <div className="card rounded-lg p-5 shadow-card">
          <SectionHeader eyebrow="Rules" title="Filter Stack" />
          <div className="mt-4">
            <FilterBuilder />
          </div>
        </div>

        <div className="card rounded-lg p-5 shadow-card">
          <SectionHeader eyebrow="Presets" title="Saved Screens" />
          <div className="mt-4">
            <PresetSelector />
          </div>
        </div>
      </section>

      <section className="card rounded-lg shadow-card flex-1 min-h-0">
        <div className="flex items-center justify-between gap-4 border-b border-gray-200 px-5 py-4">
          <SectionHeader
            eyebrow="Results"
            title="Scan Output"
            meta={(
              <div className="rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 text-[11px] font-mono text-gray-600">
                {results.length} match{results.length === 1 ? '' : 'es'}
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
    </div>
  )
}

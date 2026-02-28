import React, { useEffect } from 'react'
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

export default function ScreenerPage() {
  const toast = useToast()
  const {
    scanning, results, filters, presetsLoaded,
    loadPresets, loadUniverses, runScan,
    interval, period, setInterval, setPeriod,
  } = useScreenerStore()

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

  return (
    <div className="flex flex-col gap-4 p-4 h-full overflow-y-auto">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-mono font-semibold text-terminal-text">
          Stock Screener
          {results.length > 0 && (
            <span className="ml-2 text-xs font-normal bg-terminal-blue/15 text-terminal-blue px-2 py-0.5 rounded">
              {results.length} matches
            </span>
          )}
        </h1>
      </div>

      {/* Universe */}
      <div className="bg-terminal-surface border border-terminal-border rounded-lg p-3 space-y-3">
        <p className="text-[10px] font-mono text-terminal-ghost uppercase tracking-widest">Universe</p>
        <UniverseSelector />
      </div>

      {/* Timeframe + Presets */}
      <div className="flex gap-4 flex-wrap">
        <div className="bg-terminal-surface border border-terminal-border rounded-lg p-3 space-y-2 flex-1 min-w-[200px]">
          <p className="text-[10px] font-mono text-terminal-ghost uppercase tracking-widest">Timeframe</p>
          <div className="flex gap-2">
            <div className="flex gap-1">
              {INTERVALS.map((i) => (
                <button
                  key={i.value}
                  onClick={() => handleIntervalChange(i.value)}
                  className={`px-2 py-1 rounded text-[10px] font-mono font-semibold transition-colors ${
                    interval === i.value
                      ? 'bg-terminal-blue/15 text-terminal-blue'
                      : 'text-terminal-dim hover:text-terminal-text'
                  }`}
                >
                  {i.label}
                </button>
              ))}
            </div>
            <div className="w-px bg-terminal-border" />
            <div className="flex gap-1">
              {availablePeriods.map((p) => (
                <button
                  key={p.value}
                  onClick={() => setPeriod(p.value)}
                  className={`px-2 py-1 rounded text-[10px] font-mono font-semibold transition-colors ${
                    period === p.value
                      ? 'bg-terminal-blue/15 text-terminal-blue'
                      : 'text-terminal-dim hover:text-terminal-text'
                  }`}
                >
                  {p.label}
                </button>
              ))}
            </div>
          </div>
        </div>

        <div className="bg-terminal-surface border border-terminal-border rounded-lg p-3 space-y-2 flex-1 min-w-[300px]">
          <p className="text-[10px] font-mono text-terminal-ghost uppercase tracking-widest">Presets</p>
          <PresetSelector />
        </div>
      </div>

      {/* Filters */}
      <div className="bg-terminal-surface border border-terminal-border rounded-lg p-3 space-y-2">
        <p className="text-[10px] font-mono text-terminal-ghost uppercase tracking-widest">Filters</p>
        <FilterBuilder />
      </div>

      {/* Scan button */}
      <button
        onClick={handleScan}
        disabled={scanning}
        className="self-start px-6 py-2 rounded-lg text-sm font-mono font-semibold bg-terminal-blue text-white hover:bg-terminal-blue/90 disabled:opacity-50 transition-colors flex items-center gap-2"
      >
        {scanning && (
          <svg className="animate-spin w-4 h-4" viewBox="0 0 24 24" fill="none">
            <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3" className="opacity-25" />
            <path d="M4 12a8 8 0 018-8" stroke="currentColor" strokeWidth="3" strokeLinecap="round" className="opacity-75" />
          </svg>
        )}
        {scanning ? 'Scanning...' : 'Scan'}
      </button>

      {/* Results */}
      <div className="bg-terminal-surface border border-terminal-border rounded-lg p-3 flex-1 min-h-0">
        <ScanResultsTable />
      </div>
    </div>
  )
}

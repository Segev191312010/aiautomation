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
    <div className="flex flex-col gap-5 p-5 h-full overflow-y-auto">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-sans font-semibold text-terminal-text">
          Stock Screener
          {results.length > 0 && (
            <span className="ml-2.5 text-xs font-sans font-medium bg-indigo-500/15 text-indigo-400 px-2.5 py-0.5 rounded-xl">
              {results.length} matches
            </span>
          )}
        </h1>
      </div>

      {/* Universe */}
      <div className="glass rounded-2xl shadow-glass p-5 space-y-3">
        <p className="text-xs font-sans font-medium text-terminal-dim tracking-wide uppercase">Universe</p>
        <UniverseSelector />
      </div>

      {/* Timeframe + Presets */}
      <div className="flex gap-4 flex-wrap">
        <div className="glass rounded-2xl shadow-glass p-5 space-y-3 flex-1 min-w-[200px]">
          <p className="text-xs font-sans font-medium text-terminal-dim tracking-wide uppercase">Timeframe</p>
          <div className="flex gap-2">
            <div className="flex gap-1">
              {INTERVALS.map((i) => (
                <button
                  key={i.value}
                  onClick={() => handleIntervalChange(i.value)}
                  className={`px-2.5 py-1 rounded-xl text-xs font-sans font-medium transition-colors ${
                    interval === i.value
                      ? 'bg-indigo-500/15 text-indigo-400'
                      : 'text-terminal-dim hover:text-terminal-text hover:bg-white/[0.04]'
                  }`}
                >
                  {i.label}
                </button>
              ))}
            </div>
            <div className="w-px bg-white/[0.06]" />
            <div className="flex gap-1">
              {availablePeriods.map((p) => (
                <button
                  key={p.value}
                  onClick={() => setPeriod(p.value)}
                  className={`px-2.5 py-1 rounded-xl text-xs font-sans font-medium transition-colors ${
                    period === p.value
                      ? 'bg-indigo-500/15 text-indigo-400'
                      : 'text-terminal-dim hover:text-terminal-text hover:bg-white/[0.04]'
                  }`}
                >
                  {p.label}
                </button>
              ))}
            </div>
          </div>
        </div>

        <div className="glass rounded-2xl shadow-glass p-5 space-y-3 flex-1 min-w-[300px]">
          <p className="text-xs font-sans font-medium text-terminal-dim tracking-wide uppercase">Presets</p>
          <PresetSelector />
        </div>
      </div>

      {/* Filters */}
      <div className="glass rounded-2xl shadow-glass p-5 space-y-3">
        <p className="text-xs font-sans font-medium text-terminal-dim tracking-wide uppercase">Filters</p>
        <FilterBuilder />
      </div>

      {/* Scan button */}
      <button
        onClick={handleScan}
        disabled={scanning}
        className="self-start px-6 py-2 rounded-xl text-sm font-sans font-medium bg-gradient-to-r from-indigo-500 to-purple-500 text-white hover:opacity-90 disabled:opacity-50 transition-opacity flex items-center gap-2 shadow-glow-blue"
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
      <div className="glass rounded-2xl shadow-glass p-5 flex-1 min-h-0">
        <ScanResultsTable />
      </div>
    </div>
  )
}

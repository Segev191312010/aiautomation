import React, { useEffect, useState } from 'react'
import clsx from 'clsx'
import * as api from '@/services/api'
import type { IBKRScanTemplate, IBKRScanResult } from '@/types'

const SCAN_ICONS: Record<string, string> = {
  hot_us_stocks: 'H',
  top_gainers: 'G',
  top_losers: 'L',
  most_active: 'A',
  high_opt_volume: 'V',
  gap_up: 'U',
  gap_down: 'D',
  new_highs: 'N',
}

const SCAN_COLORS: Record<string, string> = {
  hot_us_stocks: 'border-orange-500/30 bg-orange-500/10 text-orange-300',
  top_gainers: 'border-emerald-500/30 bg-emerald-500/10 text-emerald-300',
  top_losers: 'border-red-500/30 bg-red-500/10 text-red-400',
  most_active: 'border-blue-500/30 bg-blue-500/10 text-blue-300',
  high_opt_volume: 'border-purple-500/30 bg-purple-500/10 text-purple-300',
  gap_up: 'border-emerald-500/30 bg-emerald-500/10 text-emerald-300',
  gap_down: 'border-red-500/30 bg-red-500/10 text-red-400',
  new_highs: 'border-cyan-500/30 bg-cyan-500/10 text-cyan-300',
}

interface Props {
  onSelectSymbols?: (symbols: string[]) => void
}

export default function IBKRQuickScans({ onSelectSymbols }: Props) {
  const [templates, setTemplates] = useState<IBKRScanTemplate[]>([])
  const [activeScan, setActiveScan] = useState<string | null>(null)
  const [scanResults, setScanResults] = useState<IBKRScanResult[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.fetchIBKRScans().then(setTemplates).catch(() => {
      // IBKR not connected — templates remain empty
    })
  }, [])

  const runScan = async (scanName: string) => {
    setLoading(true)
    setActiveScan(scanName)
    setError(null)
    try {
      const resp = await api.runIBKRScan(scanName, 30)
      setScanResults(resp.results)
    } catch {
      setError('IBKR not connected')
      setScanResults([])
    } finally {
      setLoading(false)
    }
  }

  const handleUseAsUniverse = () => {
    if (scanResults.length > 0 && onSelectSymbols) {
      onSelectSymbols(scanResults.map((r) => r.symbol))
    }
  }

  if (templates.length === 0) {
    return (
      <div className="rounded-lg border border-zinc-800 bg-zinc-900/50 p-4 text-center">
        <p className="text-xs font-sans text-zinc-500">IBKR Scanner</p>
        <p className="mt-1 text-[11px] font-sans text-zinc-600">
          Connect to IBKR to unlock real-time server-side market scans
        </p>
      </div>
    )
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-2">
        {templates.map((t) => (
          <button
            key={t.id}
            type="button"
            onClick={() => runScan(t.id)}
            disabled={loading}
            className={clsx(
              'rounded-lg border px-3 py-2 text-[11px] font-sans font-medium transition-all',
              activeScan === t.id
                ? SCAN_COLORS[t.id] ?? 'border-zinc-600 bg-zinc-700 text-zinc-100'
                : 'border-zinc-800 bg-zinc-900 text-zinc-400 hover:border-zinc-700 hover:text-zinc-200',
              loading && 'opacity-60 cursor-not-allowed',
            )}
          >
            <span className="font-mono font-bold mr-1.5">{SCAN_ICONS[t.id] ?? '#'}</span>
            {t.name}
          </button>
        ))}
      </div>

      {error && (
        <div className="rounded-md border border-red-500/30 bg-red-500/10 px-3 py-2 text-[11px] text-red-400">
          {error}
        </div>
      )}

      {loading && (
        <div className="flex items-center gap-2 py-3">
          <div className="h-3 w-3 animate-spin rounded-full border-2 border-zinc-600 border-t-cyan-400" />
          <span className="text-[11px] text-zinc-400">Scanning via IBKR...</span>
        </div>
      )}

      {!loading && scanResults.length > 0 && (
        <div className="space-y-2">
          <div className="flex items-center justify-between gap-3">
            <span className="text-[10px] font-sans uppercase tracking-wider text-zinc-500">
              {scanResults.length} results
            </span>
            {onSelectSymbols && (
              <button
                type="button"
                onClick={handleUseAsUniverse}
                className="rounded-md border border-cyan-500/30 bg-cyan-500/10 px-2.5 py-1 text-[11px] font-sans font-medium text-cyan-300 hover:bg-cyan-500/20 transition-colors"
              >
                Use as Custom Universe
              </button>
            )}
          </div>
          <div className="grid grid-cols-2 gap-1.5 sm:grid-cols-3 md:grid-cols-4">
            {scanResults.slice(0, 20).map((r) => (
              <div
                key={`${r.symbol}-${r.rank}`}
                className="flex items-center justify-between rounded-md border border-zinc-800 bg-zinc-900 px-2.5 py-1.5"
              >
                <span className="font-mono text-[11px] font-bold text-zinc-100">{r.symbol}</span>
                <span className="font-mono text-[10px] text-zinc-500">#{r.rank + 1}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

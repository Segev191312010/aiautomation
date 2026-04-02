import React, { useEffect, useState } from 'react'
import clsx from 'clsx'
import * as api from '@/services/api'
import type { IBKRScanTemplate, IBKRScanResult } from '@/types'

// ── static metadata ─────────────────────────────────────────────────────────

const SCAN_ICONS: Record<string, string> = {
  hot_us_stocks:  'H',
  top_gainers:    'G',
  top_losers:     'L',
  most_active:    'A',
  high_opt_volume:'V',
  gap_up:         'U',
  gap_down:       'D',
  new_highs:      'N',
}

const SCAN_COLORS: Record<string, string> = {
  hot_us_stocks:   'border-orange-500/30 bg-orange-500/10 text-orange-300',
  top_gainers:     'border-emerald-500/30 bg-emerald-500/10 text-emerald-300',
  top_losers:      'border-red-500/30 bg-red-500/10 text-red-400',
  most_active:     'border-blue-500/30 bg-blue-500/10 text-blue-300',
  high_opt_volume: 'border-purple-500/30 bg-purple-500/10 text-purple-300',
  gap_up:          'border-emerald-500/30 bg-emerald-500/10 text-emerald-300',
  gap_down:        'border-red-500/30 bg-red-500/10 text-red-400',
  new_highs:       'border-cyan-500/30 bg-cyan-500/10 text-cyan-300',
}

const MAX_RESULTS_OPTIONS = [10, 30, 50] as const
type MaxResultsOption = typeof MAX_RESULTS_OPTIONS[number]

// ── props ────────────────────────────────────────────────────────────────────

interface Props {
  /** Called with the symbol list extracted from an IBKR scan. */
  onSelectSymbols?: (symbols: string[]) => void
  /**
   * Called after onSelectSymbols to immediately trigger the main filter scan.
   * When provided, the CTA becomes "Load & Scan" rather than "Use as Universe".
   */
  onRunScan?: () => void
}

// ── component ────────────────────────────────────────────────────────────────

export default function IBKRQuickScans({ onSelectSymbols, onRunScan }: Props) {
  const [templates, setTemplates]     = useState<IBKRScanTemplate[]>([])
  const [activeScan, setActiveScan]   = useState<string | null>(null)
  const [scanResults, setScanResults] = useState<IBKRScanResult[]>([])
  const [loading, setLoading]         = useState(false)
  const [error, setError]             = useState<string | null>(null)
  const [maxResults, setMaxResults]   = useState<MaxResultsOption>(30)

  // Load available templates once on mount (silently handles IBKR offline)
  useEffect(() => {
    api.fetchIBKRScans()
      .then(setTemplates)
      .catch(() => { /* IBKR not connected — leave templates empty */ })
  }, [])

  const handleRunScan = async (scanName: string) => {
    setLoading(true)
    setActiveScan(scanName)
    setError(null)
    setScanResults([])
    try {
      const resp = await api.runIBKRScan(scanName, maxResults)
      setScanResults(resp.results)
    } catch {
      setError('IBKR scanner unavailable — ensure TWS/Gateway is running')
    } finally {
      setLoading(false)
    }
  }

  const handleLoadAndScan = () => {
    if (scanResults.length === 0 || !onSelectSymbols) return
    onSelectSymbols(scanResults.map((r) => r.symbol))
    // If a scan trigger is wired, fire it in the next tick so the store
    // has time to update customSymbols/universe before running.
    if (onRunScan) {
      setTimeout(onRunScan, 0)
    }
  }

  // ── empty state (IBKR not connected) ──────────────────────────────────────

  if (templates.length === 0) {
    return (
      <div className="rounded-lg border border-[var(--border)] bg-[var(--bg-primary)] p-4 text-center">
        <p className="text-xs font-sans font-medium text-[var(--text-secondary)]">IBKR Scanner</p>
        <p className="mt-1 text-[11px] font-sans text-[var(--text-muted)]">
          Connect TWS or IB Gateway to unlock real-time server-side market scans
        </p>
      </div>
    )
  }

  // ── main UI ───────────────────────────────────────────────────────────────

  const ctaLabel = onRunScan ? 'Load & Scan' : 'Use as Custom Universe'

  return (
    <div className="space-y-3">

      {/* Max-results picker */}
      <div className="flex items-center gap-2">
        <span className="text-[10px] font-sans uppercase tracking-[0.18em] text-[var(--text-muted)]">Max</span>
        <div className="flex gap-1">
          {MAX_RESULTS_OPTIONS.map((n) => (
            <button
              key={n}
              type="button"
              onClick={() => setMaxResults(n)}
              className={clsx(
                'rounded px-2 py-0.5 text-[10px] font-mono font-medium transition-colors',
                maxResults === n
                  ? 'border border-[var(--accent)] bg-[var(--accent-soft)] text-white'
                  : 'border border-[var(--border)] bg-[var(--bg-primary)] text-[var(--text-muted)] hover:text-[var(--text-secondary)]',
              )}
            >
              {n}
            </button>
          ))}
        </div>
      </div>

      {/* Scan template buttons */}
      <div className="flex flex-wrap gap-2">
        {templates.map((t) => (
          <button
            key={t.id}
            type="button"
            onClick={() => handleRunScan(t.id)}
            disabled={loading}
            title={`Run "${t.name}" scan (up to ${maxResults} results)`}
            className={clsx(
              'rounded-lg border px-3 py-2 text-[11px] font-sans font-medium transition-all',
              activeScan === t.id && !loading
                ? SCAN_COLORS[t.id] ?? 'border-[var(--accent)]/30 bg-[var(--accent-soft)] text-white'
                : 'border-[var(--border)] bg-[var(--bg-primary)] text-[var(--text-secondary)] hover:border-[var(--accent)]/40 hover:text-[var(--text-primary)]',
              loading && activeScan === t.id && 'opacity-70 cursor-not-allowed',
              loading && activeScan !== t.id && 'opacity-50 cursor-not-allowed',
            )}
          >
            <span className="font-mono font-bold mr-1.5">
              {loading && activeScan === t.id
                ? <span className="inline-block h-2.5 w-2.5 animate-spin rounded-full border border-current border-t-transparent align-middle" />
                : SCAN_ICONS[t.id] ?? '#'}
            </span>
            {t.name}
          </button>
        ))}
      </div>

      {/* Error banner */}
      {error && (
        <div className="rounded-md border border-red-500/30 bg-red-500/10 px-3 py-2 text-[11px] font-sans text-red-400">
          {error}
        </div>
      )}

      {/* Global loading indicator (when no active scan button selected yet) */}
      {loading && !activeScan && (
        <div className="flex items-center gap-2 py-2">
          <div className="h-3 w-3 animate-spin rounded-full border-2 border-[var(--border)] border-t-[var(--accent)]" />
          <span className="text-[11px] font-sans text-[var(--text-muted)]">Scanning via IBKR...</span>
        </div>
      )}

      {/* Results preview */}
      {!loading && scanResults.length > 0 && (
        <div className="space-y-2">
          {/* Header row */}
          <div className="flex items-center justify-between gap-3">
            <span className="text-[10px] font-sans uppercase tracking-[0.18em] text-[var(--text-muted)]">
              {scanResults.length} result{scanResults.length !== 1 ? 's' : ''} — {activeScan?.replace(/_/g, ' ')}
            </span>

            {onSelectSymbols && (
              <button
                type="button"
                onClick={handleLoadAndScan}
                className="rounded-md border border-[var(--accent)]/40 bg-[var(--accent-soft)] px-2.5 py-1 text-[11px] font-sans font-medium text-white hover:bg-[color:rgba(245,158,11,0.24)] transition-colors"
              >
                {ctaLabel}
              </button>
            )}
          </div>

          {/* Symbol grid — show up to 20 */}
          <div className="grid grid-cols-2 gap-1.5 sm:grid-cols-3 md:grid-cols-4">
            {scanResults.slice(0, 20).map((r) => (
              <div
                key={`${r.symbol}-${r.rank}`}
                className="flex items-center justify-between rounded-md border border-[var(--border)] bg-[var(--bg-primary)] px-2.5 py-1.5"
              >
                <span className="font-mono text-[11px] font-bold text-[var(--text-primary)]">{r.symbol}</span>
                <span className="font-mono text-[10px] text-[var(--text-muted)]">#{r.rank + 1}</span>
              </div>
            ))}
          </div>

          {scanResults.length > 20 && (
            <p className="text-[10px] font-sans text-[var(--text-muted)]">
              +{scanResults.length - 20} more (all included when loading)
            </p>
          )}
        </div>
      )}
    </div>
  )
}

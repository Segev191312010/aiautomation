/**
 * SignalsTable — operator view of inbound TradingView/scanner signals.
 * Polls /api/signals on an interval and surfaces row selection to the parent
 * so the detail + Claude decision panels can render.
 */
import { useCallback, useEffect, useState } from 'react'
import clsx from 'clsx'
import { fetchSignals } from '@/services/api/signals'
import type { SignalRow, SignalSource, SignalStatus } from '@/types/signals'
import { fmtTimestamp } from '@/utils/formatters'

interface Props {
  source?: SignalSource
  status?: SignalStatus
  selectedId?: string | null
  onSelect?: (signal: SignalRow) => void
  refreshMs?: number
}

const STATUS_TONE: Record<SignalStatus, string> = {
  pending_review: 'bg-amber-500/10 text-amber-400',
  in_review: 'bg-indigo-500/10 text-indigo-400',
  applied: 'bg-emerald-500/10 text-emerald-400',
  declined_by_ai: 'bg-zinc-700/40 text-zinc-300',
  failed: 'bg-red-500/10 text-red-400',
  expired: 'bg-zinc-800 text-zinc-500',
  queued: 'bg-sky-500/10 text-sky-400',
  draining: 'bg-amber-500/10 text-amber-400',
}

function actionTone(action: string) {
  const upper = action.toUpperCase()
  if (upper.includes('BUY') || upper.includes('LONG')) return 'text-emerald-400'
  if (upper.includes('SELL') || upper.includes('SHORT') || upper.includes('CLOSE')) return 'text-red-400'
  return 'text-zinc-200'
}

export default function SignalsTable({ source, status, selectedId, onSelect, refreshMs = 10000 }: Props) {
  const [rows, setRows] = useState<SignalRow[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      const data = await fetchSignals({ source, status, limit: 50, offset: 0 })
      // Defensive: ensure rows is always an array to prevent "rows.map is not a function"
      setRows(Array.isArray(data) ? data : [])
      setError(null)
    } catch (err) {
      setRows([])
      setError(err instanceof Error ? err.message : 'Failed to load signals')
    } finally {
      setLoading(false)
    }
  }, [source, status])

  useEffect(() => {
    void load()
    if (!refreshMs) return
    const timer = window.setInterval(() => { void load() }, refreshMs)
    return () => window.clearInterval(timer)
  }, [load, refreshMs])

  if (loading && rows.length === 0) {
    return (
      <div className="animate-pulse space-y-2 p-4">
        {Array.from({ length: 4 }, (_, i) => <div key={i} className="h-8 rounded bg-zinc-800" />)}
      </div>
    )
  }

  if (error && rows.length === 0) {
    return (
      <div className="flex items-center justify-center rounded-[24px] border border-[var(--border)] bg-[var(--bg-hover)] px-5 py-12 text-sm text-[var(--text-muted)]">
        {error}
      </div>
    )
  }

  if (rows.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center gap-2 rounded-[24px] border border-[var(--border)] bg-[var(--bg-hover)] px-5 py-12 text-center text-sm text-[var(--text-muted)]">
        <span>No signals received yet.</span>
        <span className="text-xs font-mono text-[var(--text-muted)]">
          Configure your TradingView alert from docs/tv_alert_template.pine
        </span>
      </div>
    )
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[640px]">
        <thead>
          <tr className="border-b border-[var(--border)] text-left text-[11px] uppercase tracking-[0.12em] text-[var(--text-muted)]">
            <th className="py-3 pr-3 font-medium">Symbol</th>
            <th className="py-3 px-3 font-medium">Action</th>
            <th className="py-3 px-3 font-medium">Source</th>
            <th className="py-3 px-3 font-medium">Status</th>
            <th className="py-3 pl-3 text-right font-medium">Queued</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr
              key={row.id}
              role="button"
              tabIndex={0}
              onClick={() => onSelect?.(row)}
              onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); onSelect?.(row) } }}
              className={clsx(
                'cursor-pointer border-b border-[var(--border)]/60 transition-colors last:border-b-0 hover:bg-[var(--bg-hover)]',
                selectedId === row.id && 'bg-[var(--bg-hover)]',
              )}
            >
              <td className="py-3 pr-3 text-sm font-semibold text-[var(--text-primary)]">{row.symbol}</td>
              <td className={clsx('py-3 px-3 text-sm font-mono', actionTone(row.action))}>{row.action}</td>
              <td className="py-3 px-3 text-sm text-[var(--text-secondary)]">{row.source}</td>
              <td className="py-3 px-3">
                <span className={clsx('text-[10px] font-mono px-1.5 py-0.5 rounded', STATUS_TONE[row.status] ?? 'bg-zinc-800 text-zinc-400')}>
                  {row.status}
                </span>
              </td>
              <td className="py-3 pl-3 text-right text-xs font-mono text-[var(--text-muted)]">{fmtTimestamp(row.queued_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

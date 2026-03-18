import React from 'react'
import type { DiagnosticRefreshRun } from '@/types'

function fmtTs(ts?: number | null): string {
  if (!ts) return '—'
  return new Date(ts * 1000).toLocaleString()
}

interface Props {
  lookbackDays: 90 | 180 | 365
  onSetLookback: (d: 90 | 180 | 365) => void
  onRefresh: () => void
  refreshing: boolean
  refreshRun: DiagnosticRefreshRun | null
  lastRunTs?: number
}

export default function DiagnosticHeaderRow({
  lookbackDays,
  onSetLookback,
  onRefresh,
  refreshing,
  refreshRun,
  lastRunTs,
}: Props) {
  const running = refreshRun?.status?.toLowerCase() === 'running'
  const conflictRun = running && Boolean(refreshRun?.locked_by)

  return (
    <div className="flex flex-wrap items-center gap-2 card rounded-2xl  p-3">
      <div className="flex items-center gap-1">
        {[
          { label: '90D', value: 90 as const },
          { label: '6MO', value: 180 as const },
          { label: '1Y', value: 365 as const },
        ].map((item) => (
          <button
            key={item.value}
            onClick={() => onSetLookback(item.value)}
            className={`text-[10px] font-sans px-2 py-1 rounded-xl border transition-colors ${
              lookbackDays === item.value
                ? 'border-indigo-50 text-indigo-600 bg-indigo-50'
                : 'border-zinc-800 text-zinc-500 hover:text-zinc-100'
            }`}
          >
            {item.label}
          </button>
        ))}
      </div>

      <button
        onClick={onRefresh}
        disabled={refreshing}
        className="text-[10px] font-sans px-2.5 py-1 rounded-xl border border-emerald-300/40 text-emerald-400 bg-emerald-500/10 hover:bg-emerald-500/10/80 disabled:opacity-50"
      >
        {conflictRun ? 'Refresh already running' : refreshing ? 'Refreshing...' : 'Manual Refresh'}
      </button>

      <div className="ml-auto text-[10px] font-mono text-zinc-500 flex items-center gap-3">
        <span>Last run: {fmtTs(lastRunTs)}</span>
        {refreshRun && (
          <span>
            Run #{refreshRun.run_id} · {refreshRun.status}
          </span>
        )}
      </div>
    </div>
  )
}

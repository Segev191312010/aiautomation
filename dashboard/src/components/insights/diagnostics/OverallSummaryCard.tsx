import React from 'react'
import type { DiagnosticOverview } from '@/types'

function stateClass(state: string): string {
  if (state === 'GREEN') return 'text-green-600'
  if (state === 'YELLOW') return 'text-amber-600'
  if (state === 'RED') return 'text-red-600'
  return 'text-gray-400'
}

export default function OverallSummaryCard({ overview }: { overview: DiagnosticOverview | null }) {
  return (
    <section className="card rounded-2xl shadow-card p-3">
      <div className="flex items-center justify-between">
        <h3 className="text-xs font-sans font-medium text-gray-500">Market Diagnostic Summary</h3>
        <span className={`text-[11px] font-mono ${stateClass(overview?.state ?? 'unknown')}`}>
          {overview?.state ?? 'unknown'}
        </span>
      </div>

      <div className="mt-2 grid grid-cols-2 md:grid-cols-4 gap-2 text-[11px]">
        <div className="rounded-xl border border-gray-200 p-2">
          <div className="font-sans font-medium text-gray-500 tracking-wide">Composite</div>
          <div className="font-mono text-gray-800">{overview?.composite_score?.toFixed(2) ?? '—'}</div>
        </div>
        <div className="rounded-xl border border-gray-200 p-2">
          <div className="font-sans font-medium text-gray-500 tracking-wide">Indicators</div>
          <div className="font-mono text-gray-800">{overview?.indicator_count ?? 0}</div>
        </div>
        <div className="rounded-xl border border-gray-200 p-2">
          <div className="font-sans font-medium text-gray-500 tracking-wide">Warn</div>
          <div className="font-mono text-amber-600">{overview?.warn_count ?? 0}</div>
        </div>
        <div className="rounded-xl border border-gray-200 p-2">
          <div className="font-sans font-medium text-gray-500 tracking-wide">Stale</div>
          <div className="font-mono text-red-600">{overview?.stale_count ?? 0}</div>
        </div>
      </div>
    </section>
  )
}

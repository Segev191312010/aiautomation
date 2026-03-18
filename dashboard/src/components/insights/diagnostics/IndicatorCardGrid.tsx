import React from 'react'
import type { DiagnosticIndicator } from '@/types'

function stateClass(state: string | null): string {
  if (state === 'GREEN') return 'text-green-600'
  if (state === 'YELLOW') return 'text-amber-600'
  if (state === 'RED') return 'text-red-600'
  return 'text-gray-400'
}

function freshnessLabel(freshness: string, reason: string | null): string {
  if (reason === 'awaiting_source_publish') return 'AWAITING'
  if (reason === 'missing_data') return 'MISSING'
  if (freshness === 'ok') return 'LIVE'
  if (freshness === 'warn') return 'WARN'
  if (freshness === 'stale') return 'STALE'
  return 'UNKNOWN'
}

export default function IndicatorCardGrid({ indicators }: { indicators: DiagnosticIndicator[] }) {
  return (
    <section className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-2">
      {indicators.map((ind) => (
        <article key={ind.code} className="card rounded-2xl shadow-card p-3">
          <div className="flex items-center justify-between">
            <h4 className="text-[11px] font-mono text-gray-800">{ind.code}</h4>
            <span className={`text-[10px] font-mono ${stateClass(ind.state)}`}>{ind.state ?? '—'}</span>
          </div>
          <div className="text-xs font-sans font-medium text-gray-500 mt-0.5">{ind.name}</div>
          <div className="mt-2 text-lg font-mono text-gray-800">
            {ind.score != null ? ind.score.toFixed(2) : '—'}
          </div>
          <div className="text-[10px] font-mono text-gray-400">
            {freshnessLabel(ind.freshness_status, ind.reason_code)}
            {ind.age_s != null ? ` · ${Math.floor(ind.age_s)}s` : ''}
          </div>
          {ind.reason_code && (
            <div className="text-[10px] font-mono text-amber-600 mt-1">{ind.reason_code}</div>
          )}
        </article>
      ))}
    </section>
  )
}

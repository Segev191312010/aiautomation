import React from 'react'
import type { DiagnosticOverview } from '@/types'

function tone(state?: string): string {
  if (state === 'GREEN') return 'text-green-600'
  if (state === 'YELLOW') return 'text-amber-600'
  if (state === 'RED') return 'text-red-600'
  return 'text-gray-400'
}

export default function AASWidget({ overview }: { overview: DiagnosticOverview | null }) {
  const data = overview?.widgets?.aas
  return (
    <div className="card rounded-2xl shadow-card p-3">
      <div className="text-xs font-sans font-medium text-gray-500 tracking-wide">AAS</div>
      <div className="mt-1 flex items-center justify-between">
        <span className="text-sm font-mono text-gray-800">{data?.score?.toFixed(2) ?? '—'}</span>
        <span className={`text-[11px] font-mono ${tone(data?.state)}`}>{data?.state ?? 'unknown'}</span>
      </div>
    </div>
  )
}

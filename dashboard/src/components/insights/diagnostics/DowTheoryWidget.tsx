import React from 'react'
import type { DiagnosticOverview } from '@/types'

function tone(state?: string): string {
  if (state === 'GREEN') return 'text-emerald-400'
  if (state === 'YELLOW') return 'text-amber-600'
  if (state === 'RED') return 'text-red-400'
  return 'text-zinc-500'
}

export default function DowTheoryWidget({ overview }: { overview: DiagnosticOverview | null }) {
  const data = overview?.widgets?.dow_theory
  return (
    <div className="card rounded-2xl  p-3">
      <div className="text-xs font-sans font-medium text-zinc-400 tracking-wide">Dow Theory</div>
      <div className="mt-1 flex items-center justify-between">
        <span className="text-sm font-mono text-zinc-100">{data?.score?.toFixed(2) ?? '—'}</span>
        <span className={`text-[11px] font-mono ${tone(data?.state)}`}>{data?.state ?? 'unknown'}</span>
      </div>
    </div>
  )
}

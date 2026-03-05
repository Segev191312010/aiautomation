import React from 'react'
import type { DiagnosticOverview } from '@/types'

function tone(state?: string): string {
  if (state === 'GREEN') return 'text-terminal-green'
  if (state === 'YELLOW') return 'text-terminal-amber'
  if (state === 'RED') return 'text-terminal-red'
  return 'text-terminal-ghost'
}

export default function AASWidget({ overview }: { overview: DiagnosticOverview | null }) {
  const data = overview?.widgets?.aas
  return (
    <div className="glass rounded-2xl shadow-glass p-3">
      <div className="text-xs font-sans font-medium text-terminal-dim tracking-wide">AAS</div>
      <div className="mt-1 flex items-center justify-between">
        <span className="text-sm font-mono text-terminal-text">{data?.score?.toFixed(2) ?? '—'}</span>
        <span className={`text-[11px] font-mono ${tone(data?.state)}`}>{data?.state ?? 'unknown'}</span>
      </div>
    </div>
  )
}

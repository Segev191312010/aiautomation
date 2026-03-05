import React from 'react'
import type { DiagnosticOverview } from '@/types'

function tone(state?: string): string {
  if (state === 'GREEN') return 'text-terminal-green'
  if (state === 'YELLOW') return 'text-terminal-amber'
  if (state === 'RED') return 'text-terminal-red'
  return 'text-terminal-ghost'
}

export default function DowTheoryWidget({ overview }: { overview: DiagnosticOverview | null }) {
  const data = overview?.widgets?.dow_theory
  return (
    <div className="glass rounded-2xl shadow-glass p-3">
      <div className="text-xs font-sans font-medium text-terminal-dim tracking-wide">Dow Theory</div>
      <div className="mt-1 flex items-center justify-between">
        <span className="text-sm font-mono text-terminal-text">{data?.score?.toFixed(2) ?? '—'}</span>
        <span className={`text-[11px] font-mono ${tone(data?.state)}`}>{data?.state ?? 'unknown'}</span>
      </div>
    </div>
  )
}

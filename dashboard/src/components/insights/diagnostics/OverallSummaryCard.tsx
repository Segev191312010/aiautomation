import React from 'react'
import type { DiagnosticOverview } from '@/types'

function stateClass(state: string): string {
  if (state === 'GREEN') return 'text-terminal-green'
  if (state === 'YELLOW') return 'text-terminal-amber'
  if (state === 'RED') return 'text-terminal-red'
  return 'text-terminal-ghost'
}

export default function OverallSummaryCard({ overview }: { overview: DiagnosticOverview | null }) {
  return (
    <section className="glass rounded-2xl shadow-glass p-3">
      <div className="flex items-center justify-between">
        <h3 className="text-xs font-sans font-medium text-terminal-dim">Market Diagnostic Summary</h3>
        <span className={`text-[11px] font-mono ${stateClass(overview?.state ?? 'unknown')}`}>
          {overview?.state ?? 'unknown'}
        </span>
      </div>

      <div className="mt-2 grid grid-cols-2 md:grid-cols-4 gap-2 text-[11px]">
        <div className="rounded-xl border border-white/[0.06] p-2">
          <div className="font-sans font-medium text-terminal-dim tracking-wide">Composite</div>
          <div className="font-mono text-terminal-text">{overview?.composite_score?.toFixed(2) ?? '—'}</div>
        </div>
        <div className="rounded-xl border border-white/[0.06] p-2">
          <div className="font-sans font-medium text-terminal-dim tracking-wide">Indicators</div>
          <div className="font-mono text-terminal-text">{overview?.indicator_count ?? 0}</div>
        </div>
        <div className="rounded-xl border border-white/[0.06] p-2">
          <div className="font-sans font-medium text-terminal-dim tracking-wide">Warn</div>
          <div className="font-mono text-terminal-amber">{overview?.warn_count ?? 0}</div>
        </div>
        <div className="rounded-xl border border-white/[0.06] p-2">
          <div className="font-sans font-medium text-terminal-dim tracking-wide">Stale</div>
          <div className="font-mono text-terminal-red">{overview?.stale_count ?? 0}</div>
        </div>
      </div>
    </section>
  )
}

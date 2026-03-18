import React from 'react'
import type { DiagnosticSectorProjection } from '@/types'

function directionClass(direction: string): string {
  if (direction === 'BULLISH') return 'text-green-600'
  if (direction === 'NEUTRAL') return 'text-amber-600'
  if (direction === 'BEARISH') return 'text-red-600'
  return 'text-gray-400'
}

function fmtTs(ts?: number): string {
  if (!ts) return '—'
  return new Date(ts * 1000).toLocaleString()
}

export default function SectorProjectionsPanel({ projection }: { projection: DiagnosticSectorProjection | null }) {
  const values = projection?.values ?? []
  const sorted = [...values].sort((a, b) => b.score - a.score)

  return (
    <section className="card rounded-2xl shadow-card p-3">
      <div className="flex items-center justify-between">
        <h3 className="text-xs font-sans font-medium text-gray-500">Sector Projections</h3>
        <span className="text-[10px] font-mono text-gray-400">
          v{projection?.heuristic_version ?? '—'}
        </span>
      </div>

      <div className="mt-1 text-[10px] font-mono text-gray-400">
        Run: {fmtTs(projection?.run_ts)}
      </div>

      {sorted.length === 0 ? (
        <div className="mt-3 text-[11px] font-mono text-gray-400">No sector projections yet.</div>
      ) : (
        <div className="mt-3 grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-2">
          {sorted.map((row) => (
            <article key={row.sector} className="card rounded-2xl border border-gray-200 p-2.5">
              <div className="flex items-center justify-between">
                <span className="text-[11px] font-sans text-gray-500">{row.sector}</span>
                <span className={`text-[10px] font-mono ${directionClass(row.direction)}`}>{row.direction}</span>
              </div>
              <div className="mt-1 text-sm font-mono text-gray-800">{row.score.toFixed(2)}</div>
            </article>
          ))}
        </div>
      )}
    </section>
  )
}

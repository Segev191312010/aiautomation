import React, { useMemo } from 'react'
import type { DiagnosticMarketMap } from '@/types'

function colorForChange(pct: number): string {
  if (pct >= 1.5) return 'bg-terminal-green/50 border-terminal-green/70'
  if (pct >= 0) return 'bg-terminal-green/25 border-terminal-green/40'
  if (pct <= -1.5) return 'bg-terminal-red/50 border-terminal-red/70'
  return 'bg-terminal-red/25 border-terminal-red/40'
}

export default function BubbleMarketMap({ rows }: { rows: DiagnosticMarketMap[] }) {
  const points = useMemo(() => {
    if (!rows.length) return [] as Array<DiagnosticMarketMap & { x: number; y: number; size: number }>
    const min = Math.min(...rows.map((r) => r.pct_change))
    const max = Math.max(...rows.map((r) => r.pct_change))
    return rows.map((row, idx) => {
      const y = max === min ? 50 : ((max - row.pct_change) / (max - min)) * 100
      const x = ((idx + 1) / (rows.length + 1)) * 100
      const size = Math.max(34, Math.min(86, row.rel_volume * 28))
      return { ...row, x, y, size }
    })
  }, [rows])

  return (
    <section className="glass rounded-2xl shadow-glass p-3">
      <h3 className="text-xs font-sans font-medium text-terminal-dim mb-2">Bubble Market Map</h3>
      <div className="relative h-56 rounded-xl border border-white/[0.06] bg-terminal-bg overflow-hidden">
        {points.map((p) => (
          <div
            key={p.symbol}
            className={`absolute rounded-full border flex items-center justify-center text-[10px] font-mono text-terminal-text ${colorForChange(p.pct_change)}`}
            style={{
              left: `calc(${p.x}% - ${p.size / 2}px)`,
              top: `calc(${p.y}% - ${p.size / 2}px)`,
              width: `${p.size}px`,
              height: `${p.size}px`,
            }}
            title={`${p.symbol} ${p.pct_change.toFixed(2)}% · relVol ${p.rel_volume.toFixed(2)}`}
          >
            {p.symbol}
          </div>
        ))}
      </div>
      <div className="mt-2 text-[10px] font-mono text-terminal-ghost">Y = % change · Size = relative volume</div>
    </section>
  )
}

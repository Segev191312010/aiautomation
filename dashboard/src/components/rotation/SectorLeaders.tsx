import { useState, useEffect } from 'react'
import clsx from 'clsx'
import { fmtPct, fmtPrice, pctColor } from '@/utils/formatters'
import { fetchSectorLeaders } from '@/services/api'
import { QuadrantBadge } from './QuadrantBadge'
import type { SectorRotation, SectorLeadersResponse } from '@/types'

interface SectorLeadersProps {
  sector: SectorRotation
  onNavigateToStock: (symbol: string) => void
}

export function SectorLeaders({ sector, onNavigateToStock }: SectorLeadersProps) {
  const [leaders, setLeaders] = useState<SectorLeadersResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [expanded, setExpanded] = useState(false)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    fetchSectorLeaders(sector.symbol, 10, '3mo')
      .then(data => { if (!cancelled) setLeaders(data) })
      .catch(() => {})
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [sector.symbol])

  const stocks = leaders?.leaders ?? []
  const displayed = expanded ? stocks : stocks.slice(0, 5)

  return (
    <div className="rounded-lg border border-zinc-700/50 bg-zinc-900/30 p-3 flex flex-col gap-2 min-w-[240px]">
      <div className="flex items-start justify-between gap-2">
        <div>
          <div className="flex items-center gap-1.5 mb-0.5">
            <span className="font-mono font-bold text-zinc-700 text-sm">{sector.symbol}</span>
            <QuadrantBadge quadrant={sector.quadrant} />
          </div>
          <div className="text-[10px] text-zinc-400">{sector.name}</div>
        </div>
        <div className="text-right">
          <span className={clsx('font-mono font-semibold text-sm tabular-nums', pctColor(sector.perf_1m))}>
            {fmtPct(sector.perf_1m)}
          </span>
          <div className="text-[9px] text-zinc-400">1M</div>
        </div>
      </div>

      <div className="flex gap-3 text-[10px]">
        <div><span className="text-zinc-400">RS</span> <span className="font-mono text-zinc-500">{sector.rs_ratio.toFixed(3)}</span></div>
        <div><span className="text-zinc-400">Mom</span> <span className="font-mono text-zinc-500">{sector.rs_momentum.toFixed(2)}</span></div>
        <div><span className="text-zinc-400">3M</span> <span className={clsx('font-mono', pctColor(sector.perf_3m))}>{fmtPct(sector.perf_3m, 1)}</span></div>
      </div>

      <div className="border-t border-zinc-700/50 pt-2">
        <div className="text-[9px] font-mono uppercase tracking-wider text-zinc-400 mb-1.5">Top Performers</div>
        {loading && !stocks.length ? (
          <div className="space-y-1.5">
            {[...Array(5)].map((_, i) => <div key={i} className="h-3.5 bg-zinc-800/30 rounded animate-pulse" />)}
          </div>
        ) : stocks.length === 0 ? (
          <div className="text-[11px] text-zinc-400 italic">No data</div>
        ) : (
          <div className="space-y-0.5">
            {displayed.map((s, i) => (
              <button
                key={s.symbol}
                type="button"
                onClick={() => onNavigateToStock(s.symbol)}
                className="flex items-center justify-between w-full px-1 py-0.5 rounded hover:bg-zinc-800/30 group transition-colors"
              >
                <span className="flex items-center gap-1.5">
                  <span className="text-[9px] text-zinc-400 font-mono w-3">{i + 1}</span>
                  <span className="font-mono font-semibold text-[11px] text-zinc-500 group-hover:text-blue-400">{s.symbol}</span>
                </span>
                <div className="flex items-center gap-2 text-[10px]">
                  <span className="text-zinc-400">{fmtPrice(s.price)}</span>
                  <span className={clsx('font-mono font-medium tabular-nums', pctColor(s.perf))}>{fmtPct(s.perf)}</span>
                </div>
              </button>
            ))}
          </div>
        )}
      </div>

      {stocks.length > 5 && (
        <button
          type="button"
          onClick={() => setExpanded(e => !e)}
          className="text-[10px] text-zinc-400 hover:text-zinc-500 transition-colors text-center"
        >
          {expanded ? 'Show less' : `View all ${stocks.length}`}
        </button>
      )}
    </div>
  )
}

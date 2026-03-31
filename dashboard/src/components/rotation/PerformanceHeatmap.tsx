import { useState } from 'react'
import clsx from 'clsx'
import { fmtPct, heatmapCellColor } from '@/utils/formatters'
import { Q_COLORS, type HeatmapSortKey } from './constants'
import { QuadrantBadge } from './QuadrantBadge'
import type { SectorHeatmapRow, SectorRotation } from '@/types'

interface PerformanceHeatmapProps {
  rows: SectorHeatmapRow[]
  rotationMap: Map<string, SectorRotation>
  onSelectSector: (s: string) => void
}

const COLS: { key: HeatmapSortKey; label: string }[] = [
  { key: '1w', label: '1W' },
  { key: '1m', label: '1M' },
  { key: '3m', label: '3M' },
  { key: '6m', label: '6M' },
  { key: '1y', label: '1Y' },
]

export function PerformanceHeatmap({ rows, rotationMap, onSelectSector }: PerformanceHeatmapProps) {
  const [sortKey, setSortKey] = useState<HeatmapSortKey>('1m')
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc')

  const sorted = [...rows].sort((a, b) => {
    const av = a[sortKey] ?? 0, bv = b[sortKey] ?? 0
    return sortDir === 'desc' ? bv - av : av - bv
  })

  const toggleSort = (key: HeatmapSortKey) => {
    if (key === sortKey) setSortDir(d => d === 'desc' ? 'asc' : 'desc')
    else { setSortKey(key); setSortDir('desc') }
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead>
          <tr className="border-b border-zinc-700/50">
            <th className="text-left py-2 pr-3 font-mono font-medium text-zinc-400 text-[10px] uppercase tracking-wider min-w-[140px]">Sector</th>
            <th className="text-right py-2 px-2 font-mono text-[10px] text-zinc-400 uppercase tracking-wider">Quadrant</th>
            {COLS.map(col => (
              <th
                key={col.key}
                className={clsx(
                  'text-right py-2 px-2 font-mono font-medium text-[10px] uppercase tracking-wider cursor-pointer select-none transition-colors',
                  sortKey === col.key ? 'text-zinc-600' : 'text-zinc-400 hover:text-zinc-500',
                )}
                onClick={() => toggleSort(col.key)}
              >
                {col.label}{sortKey === col.key && (sortDir === 'desc' ? ' \u25BC' : ' \u25B2')}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sorted.map(row => {
            const rot = rotationMap.get(row.symbol)
            return (
              <tr
                key={row.symbol}
                className="border-b border-zinc-800/50 hover:bg-zinc-900/30 cursor-pointer transition-colors"
                onClick={() => onSelectSector(row.symbol)}
              >
                <td className="py-2 pr-3">
                  <div className="flex items-center gap-2">
                    {rot && <span className={clsx('h-2 w-2 rounded-full shrink-0', Q_COLORS[rot.quadrant].dot)} />}
                    <div>
                      <div className="font-mono font-semibold text-zinc-600 text-[11px]">{row.symbol}</div>
                      <div className="text-zinc-400 text-[10px] truncate max-w-[100px]">{row.name}</div>
                    </div>
                  </div>
                </td>
                <td className="py-2 px-2 text-right">
                  {rot && <QuadrantBadge quadrant={rot.quadrant} />}
                </td>
                {COLS.map(col => {
                  const v = row[col.key]
                  return (
                    <td key={col.key} className="py-1.5 px-2 text-right">
                      <span className={clsx(
                        'inline-block rounded px-1.5 py-0.5 font-mono text-[11px] tabular-nums',
                        v != null ? heatmapCellColor(v) : 'text-zinc-400',
                      )}>
                        {v != null ? fmtPct(v) : '--'}
                      </span>
                    </td>
                  )
                })}
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

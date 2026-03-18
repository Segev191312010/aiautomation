/**
 * SectorExposure — Donut chart + table showing portfolio sector weights.
 * SVG-based donut, no external chart lib needed.
 * Shows warning badge if any sector exceeds the max_sector_pct threshold.
 */
import React, { useMemo, useState } from 'react'
import clsx from 'clsx'
import type { SectorExposureRow } from '@/types'

// ── Palette ───────────────────────────────────────────────────────────────────

const SECTOR_COLORS = [
  '#6366f1', // indigo
  '#10b981', // emerald
  '#f59e0b', // amber
  '#3b82f6', // blue
  '#ef4444', // red
  '#8b5cf6', // violet
  '#06b6d4', // cyan
  '#f97316', // orange
  '#84cc16', // lime
  '#ec4899', // pink
  '#14b8a6', // teal
  '#a3a3a3', // neutral
]

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmtUSD(v: number): string {
  if (Math.abs(v) >= 1_000_000) return `$${(v / 1_000_000).toFixed(1)}M`
  if (Math.abs(v) >= 1_000) return `$${(v / 1_000).toFixed(0)}K`
  return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: 0 }).format(v)
}

// ── SVG Donut ─────────────────────────────────────────────────────────────────

const CX = 80
const CY = 80
const R_OUTER = 68
const R_INNER = 44

function polarToXY(angle: number, r: number): [number, number] {
  return [CX + r * Math.cos(angle), CY + r * Math.sin(angle)]
}

function buildArcPath(startAngle: number, endAngle: number): string {
  const [x1, y1] = polarToXY(startAngle, R_OUTER)
  const [x2, y2] = polarToXY(endAngle,   R_OUTER)
  const [x3, y3] = polarToXY(endAngle,   R_INNER)
  const [x4, y4] = polarToXY(startAngle, R_INNER)
  const large = endAngle - startAngle > Math.PI ? 1 : 0
  return [
    `M ${x1} ${y1}`,
    `A ${R_OUTER} ${R_OUTER} 0 ${large} 1 ${x2} ${y2}`,
    `L ${x3} ${y3}`,
    `A ${R_INNER} ${R_INNER} 0 ${large} 0 ${x4} ${y4}`,
    'Z',
  ].join(' ')
}

interface DonutProps {
  rows:     SectorExposureRow[]
  hoveredSector: string | null
  onHover:  (sector: string | null) => void
}

function DonutChart({ rows, hoveredSector, onHover }: DonutProps) {
  const total = rows.reduce((s, r) => s + r.weight_pct, 0) || 1

  let angle = -Math.PI / 2   // start at top
  const slices = rows.map((row, i) => {
    const sweep = (row.weight_pct / total) * 2 * Math.PI
    const start = angle
    const end   = angle + sweep
    angle       = end
    return { row, start, end, color: SECTOR_COLORS[i % SECTOR_COLORS.length] }
  })

  const hovered = hoveredSector ? rows.find((r) => r.sector === hoveredSector) : null

  return (
    <div className="flex flex-col items-center">
      <svg viewBox="0 0 160 160" className="w-44 h-44">
        {slices.map(({ row, start, end, color }) => {
          const isHov = hoveredSector === row.sector
          const scale = isHov ? 1.06 : 1
          const mid = (start + end) / 2
          const tx = CX + (R_INNER + R_OUTER) / 2 * Math.cos(mid) * (scale - 1)
          const ty = CY + (R_INNER + R_OUTER) / 2 * Math.sin(mid) * (scale - 1)
          return (
            <path
              key={row.sector}
              d={buildArcPath(start, end)}
              fill={color}
              opacity={hoveredSector && !isHov ? 0.4 : 1}
              transform={isHov ? `translate(${tx} ${ty})` : undefined}
              className="transition-all duration-150 cursor-pointer"
              onMouseEnter={() => onHover(row.sector)}
              onMouseLeave={() => onHover(null)}
            />
          )
        })}

        {/* Center text */}
        {hovered ? (
          <>
            <text x={CX} y={CY - 6} textAnchor="middle" fontSize="9" fill="#9ca3af" fontFamily="ui-sans-serif, sans-serif">
              {hovered.sector.slice(0, 12)}
            </text>
            <text x={CX} y={CY + 7} textAnchor="middle" fontSize="11" fontWeight="bold" fill="#1f2937" fontFamily="ui-monospace, monospace">
              {hovered.weight_pct.toFixed(1)}%
            </text>
            <text x={CX} y={CY + 20} textAnchor="middle" fontSize="8" fill="#9ca3af" fontFamily="ui-monospace, monospace">
              {fmtUSD(hovered.value)}
            </text>
          </>
        ) : (
          <>
            <text x={CX} y={CY - 4} textAnchor="middle" fontSize="8" fill="#9ca3af" fontFamily="ui-sans-serif, sans-serif">
              Portfolio
            </text>
            <text x={CX} y={CY + 10} textAnchor="middle" fontSize="10" fontWeight="bold" fill="#1f2937" fontFamily="ui-monospace, monospace">
              {rows.length} sectors
            </text>
          </>
        )}
      </svg>

      {/* Legend dots */}
      <div className="flex flex-wrap justify-center gap-x-3 gap-y-1 mt-2">
        {slices.map(({ row, color }) => (
          <div
            key={row.sector}
            className="flex items-center gap-1 cursor-pointer"
            onMouseEnter={() => onHover(row.sector)}
            onMouseLeave={() => onHover(null)}
          >
            <span className="w-2 h-2 rounded-full flex-shrink-0" style={{ background: color }} />
            <span className={clsx(
              'text-[10px] font-sans',
              hoveredSector === row.sector ? 'text-zinc-100 font-semibold' : 'text-zinc-500',
            )}>
              {row.sector.length > 12 ? row.sector.slice(0, 11) + '…' : row.sector}
            </span>
          </div>
        ))}
      </div>
    </div>
  )
}

// ── Main component ────────────────────────────────────────────────────────────

interface Props {
  rows:           SectorExposureRow[]
  maxSectorPct:   number    // limit, default 30
  loading:        boolean
}

export default function SectorExposure({ rows, maxSectorPct = 30, loading }: Props) {
  const [hoveredSector, setHoveredSector] = useState<string | null>(null)

  const breaching = useMemo(
    () => rows.filter((r) => r.weight_pct > maxSectorPct),
    [rows, maxSectorPct],
  )

  if (loading && !rows.length) {
    return (
      <div className="flex flex-col gap-4 animate-pulse">
        <div className="flex justify-center">
          <div className="w-44 h-44 rounded-full bg-zinc-800" />
        </div>
        <div className="space-y-2">
          {[1, 2, 3].map((i) => (
            <div key={i} className="flex justify-between items-center">
              <div className="h-3 w-24 rounded bg-zinc-800" />
              <div className="h-3 w-16 rounded bg-zinc-800" />
            </div>
          ))}
        </div>
      </div>
    )
  }

  if (!rows.length) {
    return (
      <div className="flex items-center justify-center py-10 text-sm text-zinc-500">
        No sector exposure data
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-4">
      {/* Warning banners */}
      {breaching.map((r) => (
        <div
          key={r.sector}
          className="flex items-center gap-2 px-3 py-2 rounded-xl bg-amber-500/10 border border-amber-500/25 text-amber-600 text-xs font-sans"
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} className="w-3.5 h-3.5 flex-shrink-0">
            <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
            <line x1="12" y1="9" x2="12" y2="13" />
            <line x1="12" y1="17" x2="12.01" y2="17" />
          </svg>
          <span>
            <strong>{r.sector}</strong> exceeds {maxSectorPct}% sector limit ({r.weight_pct.toFixed(1)}%)
          </span>
        </div>
      ))}

      {/* Donut */}
      <DonutChart rows={rows} hoveredSector={hoveredSector} onHover={setHoveredSector} />

      {/* Table */}
      <div className="overflow-x-auto">
        <table className="w-full min-w-[340px]">
          <thead>
            <tr className="border-b border-zinc-800">
              {['Sector', 'Weight', 'Value', 'Positions', 'P&L'].map((col, i) => (
                <th
                  key={col}
                  className={clsx(
                    'py-2 px-2 text-[9px] font-sans uppercase tracking-widest text-zinc-500 font-medium',
                    i === 0 ? 'text-left' : 'text-right',
                  )}
                >
                  {col}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, i) => {
              const color = SECTOR_COLORS[i % SECTOR_COLORS.length]
              const isOver = row.weight_pct > maxSectorPct
              return (
                <tr
                  key={row.sector}
                  className={clsx(
                    'border-b border-zinc-800 transition-colors cursor-default',
                    hoveredSector === row.sector ? 'bg-zinc-800/50' : 'hover:bg-zinc-900/50',
                  )}
                  onMouseEnter={() => setHoveredSector(row.sector)}
                  onMouseLeave={() => setHoveredSector(null)}
                >
                  {/* Sector */}
                  <td className="py-2 px-2 text-left">
                    <div className="flex items-center gap-2">
                      <span className="w-2 h-2 rounded-full flex-shrink-0" style={{ background: color }} />
                      <span className="text-xs font-sans text-zinc-200 truncate max-w-[110px]">
                        {row.sector}
                      </span>
                      {isOver && (
                        <span className="text-[9px] font-mono px-1 py-0.5 rounded bg-amber-500/15 text-amber-600 border border-amber-500/20">
                          OVER
                        </span>
                      )}
                    </div>
                  </td>

                  {/* Weight with mini bar */}
                  <td className="py-2 px-2 text-right">
                    <div className="flex items-center justify-end gap-1.5">
                      <div className="w-12 h-1.5 rounded-full bg-zinc-800 overflow-hidden">
                        <div
                          className={clsx('h-full rounded-full', isOver ? 'bg-amber-500' : 'bg-indigo-500')}
                          style={{ width: `${Math.min(100, row.weight_pct)}%` }}
                        />
                      </div>
                      <span className={clsx(
                        'text-[11px] font-mono tabular-nums',
                        isOver ? 'text-amber-600 font-semibold' : 'text-zinc-400',
                      )}>
                        {row.weight_pct.toFixed(1)}%
                      </span>
                    </div>
                  </td>

                  {/* Value */}
                  <td className="py-2 px-2 text-right text-[11px] font-mono tabular-nums text-zinc-400">
                    {fmtUSD(row.value)}
                  </td>

                  {/* Position count */}
                  <td className="py-2 px-2 text-right text-[11px] font-mono tabular-nums text-zinc-400">
                    {row.position_count}
                  </td>

                  {/* P&L */}
                  <td className={clsx(
                    'py-2 px-2 text-right text-[11px] font-mono tabular-nums font-medium',
                    row.pnl >= 0 ? 'text-emerald-400' : 'text-red-400',
                  )}>
                    {row.pnl >= 0 ? '+' : ''}{fmtUSD(row.pnl)}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}

import { useState, useRef } from 'react'
import clsx from 'clsx'
import { fmtPct, fmtPrice, pctColor } from '@/utils/formatters'
import { Q_COLORS } from './constants'
import { QuadrantBadge } from './QuadrantBadge'
import type { SectorRotation } from '@/types'

interface RotationQuadrantProps {
  sectors: SectorRotation[]
  selectedSector: string
  onSelectSector: (s: string) => void
}

export function RotationQuadrant({ sectors, selectedSector, onSelectSector }: RotationQuadrantProps) {
  const [tooltip, setTooltip] = useState<{ x: number; y: number; sector: SectorRotation } | null>(null)
  const svgRef = useRef<SVGSVGElement>(null)

  const W = 560, H = 420
  const PAD = { top: 30, right: 30, bottom: 45, left: 50 }
  const plotW = W - PAD.left - PAD.right
  const plotH = H - PAD.top - PAD.bottom

  const ratios = sectors.map(s => s.rs_ratio)
  const momenta = sectors.map(s => s.rs_momentum)

  const rMin = Math.min(...ratios, 0.85), rMax = Math.max(...ratios, 1.15)
  const mMin = Math.min(...momenta, -5), mMax = Math.max(...momenta, 5)

  const rPad = (rMax - rMin) * 0.12
  const mPad = (mMax - mMin) * 0.12
  const xMin = rMin - rPad, xMax = rMax + rPad
  const yMin = mMin - mPad, yMax = mMax + mPad

  const toX = (v: number) => PAD.left + ((v - xMin) / (xMax - xMin)) * plotW
  const toY = (v: number) => PAD.top + ((yMax - v) / (yMax - yMin)) * plotH

  const cx = toX(1.0)
  const cy = toY(0)

  const volumes = sectors.map(s => s.volume)
  const vMin = Math.min(...volumes), vMax = Math.max(...volumes)
  const toR = (vol: number) => vMax === vMin ? 10 : 7 + ((vol - vMin) / (vMax - vMin)) * 11

  return (
    <div className="relative">
      <svg
        ref={svgRef}
        viewBox={`0 0 ${W} ${H}`}
        className="w-full h-auto"
        style={{ fontFamily: 'ui-monospace, monospace' }}
      >
        {/* Quadrant backgrounds */}
        <rect x={cx} y={PAD.top} width={W - PAD.right - cx} height={cy - PAD.top} fill={Q_COLORS.LEADING.svgFill} />
        <rect x={PAD.left} y={PAD.top} width={cx - PAD.left} height={cy - PAD.top} fill={Q_COLORS.IMPROVING.svgFill} />
        <rect x={cx} y={cy} width={W - PAD.right - cx} height={H - PAD.bottom - cy} fill={Q_COLORS.WEAKENING.svgFill} />
        <rect x={PAD.left} y={cy} width={cx - PAD.left} height={H - PAD.bottom - cy} fill={Q_COLORS.LAGGING.svgFill} />

        {/* Grid lines */}
        <line x1={cx} y1={PAD.top} x2={cx} y2={H - PAD.bottom} stroke="#374151" strokeWidth={1} strokeDasharray="4 3" />
        <line x1={PAD.left} y1={cy} x2={W - PAD.right} y2={cy} stroke="#374151" strokeWidth={1} strokeDasharray="4 3" />

        {/* Border */}
        <rect x={PAD.left} y={PAD.top} width={plotW} height={plotH} fill="none" stroke="#1f2937" strokeWidth={1} />

        {/* Quadrant labels */}
        <text x={W - PAD.right - 6} y={PAD.top + 16} textAnchor="end" fontSize={10} fill="#059669" fontWeight={700} letterSpacing="0.1em" opacity={0.7}>LEADING</text>
        <text x={PAD.left + 6} y={PAD.top + 16} textAnchor="start" fontSize={10} fill="#2563eb" fontWeight={700} letterSpacing="0.1em" opacity={0.7}>IMPROVING</text>
        <text x={W - PAD.right - 6} y={H - PAD.bottom - 6} textAnchor="end" fontSize={10} fill="#d97706" fontWeight={700} letterSpacing="0.1em" opacity={0.7}>WEAKENING</text>
        <text x={PAD.left + 6} y={H - PAD.bottom - 6} textAnchor="start" fontSize={10} fill="#dc2626" fontWeight={700} letterSpacing="0.1em" opacity={0.7}>LAGGING</text>

        {/* Axis labels */}
        <text x={W / 2} y={H - 4} textAnchor="middle" fontSize={9} fill="#6b7280" letterSpacing="0.08em">RELATIVE STRENGTH RATIO</text>
        <text x={12} y={H / 2} textAnchor="middle" fontSize={9} fill="#6b7280" letterSpacing="0.08em" transform={`rotate(-90, 12, ${H / 2})`}>RS MOMENTUM</text>

        {/* Rotation clockwise arrows (decorative) */}
        <path
          d={`M ${cx + 30} ${cy - 30} A 40 40 0 0 1 ${cx + 30} ${cy + 30}`}
          fill="none" stroke="#374151" strokeWidth={0.5} strokeDasharray="3 3"
          markerEnd="url(#arrowhead)"
        />
        <defs>
          <marker id="arrowhead" markerWidth="6" markerHeight="6" refX="5" refY="3" orient="auto">
            <path d="M 0 0 L 6 3 L 0 6 Z" fill="#374151" />
          </marker>
        </defs>

        {/* Sector dots */}
        {sectors.map(s => {
          const dx = toX(s.rs_ratio)
          const dy = toY(s.rs_momentum)
          const r = toR(s.volume)
          const isSelected = s.symbol === selectedSector

          return (
            <g
              key={s.symbol}
              onClick={() => onSelectSector(s.symbol)}
              onMouseEnter={(e) => {
                const rect = svgRef.current?.getBoundingClientRect()
                if (rect) setTooltip({ x: e.clientX - rect.left, y: e.clientY - rect.top, sector: s })
              }}
              onMouseMove={(e) => {
                const rect = svgRef.current?.getBoundingClientRect()
                if (rect) setTooltip({ x: e.clientX - rect.left, y: e.clientY - rect.top, sector: s })
              }}
              onMouseLeave={() => setTooltip(null)}
              style={{ cursor: 'pointer' }}
            >
              {/* Selected ring */}
              {isSelected && (
                <circle cx={dx} cy={dy} r={r + 4} fill="none" stroke="#3b82f6" strokeWidth={2} opacity={0.6}>
                  <animate attributeName="r" from={r + 3} to={r + 7} dur="1.5s" repeatCount="indefinite" />
                  <animate attributeName="opacity" from="0.6" to="0.1" dur="1.5s" repeatCount="indefinite" />
                </circle>
              )}
              {/* Momentum arrow (trail) */}
              <line
                x1={dx} y1={dy}
                x2={dx + s.rs_momentum * 3} y2={dy - s.rs_momentum * 2}
                stroke={Q_COLORS[s.quadrant].svgStroke} strokeWidth={1.5} opacity={0.4}
                markerEnd="url(#arrowhead)"
              />
              {/* Dot */}
              <circle
                cx={dx} cy={dy} r={r}
                fill={Q_COLORS[s.quadrant].svgDot}
                stroke={isSelected ? '#3b82f6' : Q_COLORS[s.quadrant].svgStroke}
                strokeWidth={isSelected ? 2 : 1.5}
                opacity={0.9}
              />
              {/* Label */}
              <text x={dx} y={dy - r - 4} textAnchor="middle" fontSize={9} fill="#d1d5db" fontWeight={600}>
                {s.symbol}
              </text>
            </g>
          )
        })}
      </svg>

      {/* Tooltip */}
      {tooltip && (
        <div
          className="pointer-events-none absolute z-20 rounded-lg border border-zinc-700 bg-zinc-900 shadow-xl px-3 py-2.5 text-xs"
          style={{ left: Math.min(tooltip.x + 14, 380), top: tooltip.y - 12, minWidth: 200 }}
        >
          <div className="flex items-center gap-2 mb-1.5">
            <span className="font-mono font-bold text-zinc-700 text-sm">{tooltip.sector.symbol}</span>
            <QuadrantBadge quadrant={tooltip.sector.quadrant} />
          </div>
          <div className="text-zinc-500 mb-2 truncate">{tooltip.sector.name}</div>
          <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-[11px]">
            <div className="flex justify-between"><span className="text-zinc-400">RS Ratio</span><span className="font-mono text-zinc-500">{tooltip.sector.rs_ratio.toFixed(3)}</span></div>
            <div className="flex justify-between"><span className="text-zinc-400">Momentum</span><span className="font-mono text-zinc-500">{tooltip.sector.rs_momentum.toFixed(2)}</span></div>
            <div className="flex justify-between"><span className="text-zinc-400">1W</span><span className={clsx('font-mono', pctColor(tooltip.sector.perf_1w))}>{fmtPct(tooltip.sector.perf_1w)}</span></div>
            <div className="flex justify-between"><span className="text-zinc-400">1M</span><span className={clsx('font-mono', pctColor(tooltip.sector.perf_1m))}>{fmtPct(tooltip.sector.perf_1m)}</span></div>
            <div className="flex justify-between"><span className="text-zinc-400">3M</span><span className={clsx('font-mono', pctColor(tooltip.sector.perf_3m))}>{fmtPct(tooltip.sector.perf_3m)}</span></div>
            <div className="flex justify-between"><span className="text-zinc-400">Price</span><span className="font-mono text-zinc-500">{fmtPrice(tooltip.sector.price)}</span></div>
          </div>
        </div>
      )}
    </div>
  )
}

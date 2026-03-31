import React from 'react'
import clsx from 'clsx'
import { fmtUSD, fmtUSDCompact } from '@/utils/formatters'
import type { ExposureBreakdown } from '@/types'

// ---------------------------------------------------------------------------
// Sector colour palette
// ---------------------------------------------------------------------------

const SECTOR_PALETTE: Record<string, { bar: string; dot: string; hex: string }> = {
  Technology:               { bar: 'bg-indigo-500',  dot: 'bg-indigo-500',  hex: '#6366F1' },
  Healthcare:               { bar: 'bg-emerald-500', dot: 'bg-emerald-500', hex: '#10B981' },
  Financials:               { bar: 'bg-blue-500',    dot: 'bg-blue-500',    hex: '#3B82F6' },
  'Consumer Discretionary': { bar: 'bg-amber-500',   dot: 'bg-amber-500',   hex: '#F59E0B' },
  'Consumer Staples':       { bar: 'bg-yellow-500',  dot: 'bg-yellow-500',  hex: '#EAB308' },
  Industrials:              { bar: 'bg-cyan-500',    dot: 'bg-cyan-500',    hex: '#06B6D4' },
  Energy:                   { bar: 'bg-orange-500',  dot: 'bg-orange-500',  hex: '#F97316' },
  Materials:                { bar: 'bg-teal-500',    dot: 'bg-teal-500',    hex: '#14B8A6' },
  Utilities:                { bar: 'bg-violet-500',  dot: 'bg-violet-500',  hex: '#8B5CF6' },
  'Real Estate':            { bar: 'bg-pink-500',    dot: 'bg-pink-500',    hex: '#EC4899' },
  'Communication Services': { bar: 'bg-sky-500',     dot: 'bg-sky-500',     hex: '#0EA5E9' },
  ETF:                      { bar: 'bg-slate-400',   dot: 'bg-slate-400',   hex: '#94A3B8' },
  Unknown:                  { bar: 'bg-zinc-600',    dot: 'bg-zinc-600',    hex: '#9CA3AF' },
}

function pal(sector: string) {
  return SECTOR_PALETTE[sector] ?? SECTOR_PALETTE['Unknown']
}

// ---------------------------------------------------------------------------
// ExposurePanel
// ---------------------------------------------------------------------------

interface ExposurePanelProps {
  exposure: ExposureBreakdown
}

export function ExposurePanel({ exposure }: ExposurePanelProps) {
  const sorted = [...exposure.positions].sort((a, b) => b.value - a.value)
  const top5   = sorted.slice(0, 5)

  // conic-gradient for sector donut
  const sectorEntries = Object.entries(exposure.sector_weights).sort((a, b) => b[1] - a[1])
  let cumDeg = 0
  const conicParts = sectorEntries.map(([sector, pct]) => {
    const deg  = (pct / 100) * 360
    const part = `${pal(sector).hex} ${cumDeg.toFixed(1)}deg ${(cumDeg + deg).toFixed(1)}deg`
    cumDeg += deg
    return part
  })
  const conicGradient = `conic-gradient(${conicParts.join(', ')})`

  const totalValue = sorted.reduce((s, p) => s + p.value, 0)

  return (
    <div className="flex flex-col gap-5">
      {/* Stacked allocation bar */}
      <div>
        <div className="text-[10px] font-sans uppercase tracking-widest text-zinc-500 mb-2">
          Position Allocation
        </div>
        <div className="h-6 rounded-full overflow-hidden flex bg-zinc-800">
          {sorted.map((p) => {
            const pct = totalValue > 0 ? (p.value / totalValue) * 100 : 0
            return (
              <div
                key={p.symbol}
                title={`${p.symbol}: ${pct.toFixed(1)}%`}
                className={clsx('h-full flex items-center justify-center', pal(p.sector).bar)}
                style={{ width: `${pct}%` }}
              >
                {pct > 5 && (
                  <span className="text-[9px] font-mono font-bold text-white/90">{p.symbol}</span>
                )}
              </div>
            )
          })}
        </div>
      </div>

      {/* Donut + sector legend */}
      <div className="flex items-start gap-6">
        <div className="flex-shrink-0 relative">
          <div className="w-[88px] h-[88px] rounded-full" style={{ background: conicGradient }} />
          <div className="absolute inset-0 flex items-center justify-center">
            <div className="w-[52px] h-[52px] rounded-full bg-zinc-900 flex flex-col items-center justify-center">
              <span className="text-[8px] font-sans text-zinc-500 uppercase tracking-wider leading-none">Sectors</span>
              <span className="text-[13px] font-mono font-bold text-zinc-100 leading-none mt-0.5">
                {sectorEntries.length}
              </span>
            </div>
          </div>
        </div>
        <div className="flex flex-col gap-1.5 min-w-0">
          {sectorEntries.map(([sector, pct]) => (
            <div key={sector} className="flex items-center gap-2">
              <span className={clsx('w-2 h-2 rounded-full flex-shrink-0', pal(sector).dot)} />
              <span className="text-[11px] font-sans text-zinc-400 truncate flex-1">{sector}</span>
              <span className="text-[11px] font-mono text-zinc-400 tabular-nums">{pct.toFixed(1)}%</span>
            </div>
          ))}
        </div>
      </div>

      {/* Top 5 table */}
      <div>
        <div className="text-[10px] font-sans uppercase tracking-widest text-zinc-500 mb-2">Top Positions</div>
        <table className="w-full">
          <thead>
            <tr className="border-b border-zinc-800">
              {['Symbol', 'Sector', 'Value', 'Weight', 'P&L'].map((c, i) => (
                <th key={c} className={clsx('py-1.5 px-2 text-[10px] font-sans uppercase tracking-widest text-zinc-500', i < 2 ? 'text-left' : 'text-right')}>
                  {c}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {top5.map((p) => (
              <tr key={p.symbol} className="border-b border-zinc-800 hover:bg-zinc-900/60 transition-colors">
                <td className="py-2 px-2 font-mono text-sm font-semibold text-zinc-100">{p.symbol}</td>
                <td className="py-2 px-2">
                  <span className="flex items-center gap-1.5 text-[11px] font-sans text-zinc-400 max-w-[120px]">
                    <span className={clsx('w-1.5 h-1.5 rounded-full flex-shrink-0', pal(p.sector).dot)} />
                    <span className="truncate">{p.sector}</span>
                  </span>
                </td>
                <td className="py-2 px-2 font-mono text-sm text-zinc-400 tabular-nums text-right">
                  {fmtUSDCompact(p.value)}
                </td>
                <td className="py-2 px-2 text-right">
                  <div className="flex items-center justify-end gap-2">
                    <div className="w-12 h-1.5 rounded-full bg-zinc-800 overflow-hidden">
                      <div className="h-full rounded-full bg-indigo-500" style={{ width: `${Math.min(p.weight_pct, 100)}%` }} />
                    </div>
                    <span className="font-mono text-[11px] text-zinc-400 tabular-nums w-10 text-right">
                      {p.weight_pct.toFixed(1)}%
                    </span>
                  </div>
                </td>
                <td className="py-2 px-2 text-right">
                  <span className={clsx('text-[11px] font-mono font-semibold tabular-nums', p.pnl >= 0 ? 'text-emerald-600' : 'text-red-400')}>
                    {p.pnl >= 0 ? '+' : ''}{fmtUSD(p.pnl)}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

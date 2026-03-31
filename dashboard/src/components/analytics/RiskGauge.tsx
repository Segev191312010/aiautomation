import React from 'react'
import clsx from 'clsx'
import { fmtUSDCompact } from '@/utils/formatters'
import type { RiskLimitItem } from '@/types'

function usagePct(used: number, limit: number): number {
  if (limit === 0) return 0
  return Math.min(Math.abs(used / limit) * 100, 100)
}

function riskCol(pct: number) {
  if (pct >= 80) return { bar: 'bg-red-500',    text: 'text-red-400',    bg: 'bg-red-500/10',    border: 'border-red-200'    }
  if (pct >= 60) return { bar: 'bg-amber-500',  text: 'text-amber-600',  bg: 'bg-amber-50',  border: 'border-amber-200'  }
  return             { bar: 'bg-emerald-500', text: 'text-emerald-600', bg: 'bg-emerald-50', border: 'border-emerald-200' }
}

interface RiskGaugeProps {
  item: RiskLimitItem
}

export function RiskGauge({ item }: RiskGaugeProps) {
  const pct = usagePct(item.used, item.limit)
  const col = riskCol(pct)

  const fmt = (v: number) => {
    if (item.unit === '$')     return fmtUSDCompact(v)
    if (item.unit === '%')     return v.toFixed(1) + '%'
    return String(Math.abs(v))
  }

  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-center justify-between">
        <span className="text-[11px] font-sans text-zinc-400">{item.label}</span>
        <span className={clsx('text-[11px] font-mono font-semibold tabular-nums', col.text)}>
          {fmt(item.used)}
          <span className="text-zinc-500 font-normal"> / {fmt(item.limit)}</span>
        </span>
      </div>
      <div className="h-2 rounded-full bg-zinc-800 overflow-hidden">
        <div className={clsx('h-full rounded-full transition-all duration-700', col.bar)} style={{ width: `${pct}%` }} />
      </div>
      <div className="flex items-center justify-between">
        <span className={clsx('text-[10px] font-mono', col.text)}>{pct.toFixed(0)}% used</span>
        {pct >= 80 && (
          <span className={clsx('text-[9px] font-sans font-semibold px-1.5 py-0.5 rounded-full border', col.bg, col.border, col.text)}>
            NEAR LIMIT
          </span>
        )}
      </div>
    </div>
  )
}

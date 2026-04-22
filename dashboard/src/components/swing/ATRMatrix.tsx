import React from 'react'
import clsx from 'clsx'
import type { ATRMatrixRow } from '@/types'

interface Props {
  data: ATRMatrixRow[]
}

function extColor(val: number): string {
  if (val >= 1.5)  return 'text-[var(--success)] font-semibold'
  if (val >= 0.5)  return 'text-[var(--success)]'
  if (val >= -0.5) return 'text-[var(--text-primary)]'
  if (val >= -1.5) return 'text-[var(--danger)]'
  return 'text-[var(--danger)] font-semibold'
}

function extBar(val: number): React.ReactNode {
  const clamped = Math.max(-3, Math.min(3, val))
  const pct = Math.abs(clamped) / 3 * 100
  const isPos = clamped >= 0
  return (
    <div className="flex items-center gap-2">
      <div className="w-20 h-2.5 rounded-full bg-[var(--bg-secondary)] relative overflow-hidden">
        {isPos ? (
          <div
            className="absolute left-1/2 top-0 h-full rounded-r-full bg-[var(--success)]"
            style={{ width: `${pct / 2}%` }}
          />
        ) : (
          <div
            className="absolute top-0 h-full rounded-l-full bg-[var(--danger)]"
            style={{ width: `${pct / 2}%`, right: '50%' }}
          />
        )}
      </div>
      <span className={clsx('tabular-nums text-xs', extColor(val))}>{val >= 0 ? '+' : ''}{val.toFixed(2)}</span>
    </div>
  )
}

export default function ATRMatrix({ data }: Props) {
  if (data.length === 0) return null

  const sorted = [...data].sort((a, b) => b.price_vs_21ema_atr - a.price_vs_21ema_atr)

  return (
    <div className="card overflow-x-auto">
      <h3 className="shell-kicker mb-1">S&P Sector SPDRs — ATR Matrix</h3>
      <p className="text-xs text-[var(--text-muted)] mb-3">
        Price distance from 21EMA in ATR(14) units. Entry zone: 0-4x. Hold: 5-7x. Over-extended: 7x+ (scale out).
      </p>
      <table className="w-full text-sm font-mono">
        <thead>
          <tr className="border-b border-[var(--border)]">
            <th className="text-left py-2 pr-3 text-[var(--text-secondary)] font-medium">Symbol</th>
            <th className="text-left py-2 px-3 text-[var(--text-secondary)] font-medium">Name</th>
            <th className="text-right py-2 px-3 text-[var(--text-secondary)] font-medium">Close</th>
            <th className="text-right py-2 px-3 text-[var(--text-secondary)] font-medium">ATR%</th>
            <th className="py-2 px-3 text-[var(--text-secondary)] font-medium">ATR Extension (vs 21EMA)</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((row) => (
            <tr key={row.symbol} className="border-b border-[var(--border)] border-opacity-40 hover:bg-[var(--bg-hover)]">
              <td className="py-1.5 pr-3 font-semibold text-[var(--accent)]">{row.symbol}</td>
              <td className="py-1.5 px-3 text-[var(--text-secondary)]">{row.name}</td>
              <td className="py-1.5 px-3 text-right tabular-nums">${row.close.toFixed(2)}</td>
              <td className="py-1.5 px-3 text-right tabular-nums">{row.atr_pct.toFixed(2)}%</td>
              <td className="py-1.5 px-3">{extBar(row.price_vs_21ema_atr)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

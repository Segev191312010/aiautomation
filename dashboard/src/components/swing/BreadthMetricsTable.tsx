import React from 'react'
import clsx from 'clsx'
import type { BreadthMetrics } from '@/types'

interface Props {
  data: BreadthMetrics | null
}

const RATIO_ROWS = new Set([
  'Up/Down Ratio (Day)',
  'Up/Down Ratio (Week)',
  'Up/Down Ratio (Month)',
])

const PCT_ROWS = new Set([
  '% Above SMA 20',
  '% Above SMA 50',
  '% Above SMA 200',
])

function cellColor(label: string, value: number): string {
  if (RATIO_ROWS.has(label)) {
    if (value >= 1.5) return 'text-[var(--success)] font-semibold'
    if (value >= 1.0) return 'text-[var(--success)]'
    if (value >= 0.8) return 'text-[var(--danger)] opacity-70'
    return 'text-[var(--danger)] font-semibold'
  }
  if (PCT_ROWS.has(label)) {
    if (value >= 60) return 'text-[var(--success)] font-semibold'
    if (value >= 50) return 'text-[var(--success)]'
    if (value >= 40) return 'text-[var(--danger)] opacity-70'
    return 'text-[var(--danger)] font-semibold'
  }
  return ''
}

function fmt(label: string, value: number): string {
  if (RATIO_ROWS.has(label)) return value.toFixed(2)
  if (PCT_ROWS.has(label)) return `${value.toFixed(1)}%`
  return value.toLocaleString()
}

const COLS = [
  { key: 'nasdaq100' as const,    label: 'QQQE' },
  { key: 'sp500' as const,        label: 'RSP' },
  { key: 'composite' as const,    label: 'Composite' },
  { key: 'billion_plus' as const, label: '$1B+' },
]

export default function BreadthMetricsTable({ data }: Props) {
  if (!data) return null

  return (
    <div className="card overflow-x-auto">
      <h3 className="shell-kicker mb-3">Key Metrics</h3>
      <table className="w-full text-sm font-mono">
        <thead>
          <tr className="border-b border-[var(--border)]">
            <th className="text-left py-2 pr-4 text-[var(--text-secondary)] font-medium">Metric</th>
            {COLS.map((c) => (
              <th key={c.key} className="text-right py-2 px-3 text-[var(--text-secondary)] font-medium whitespace-nowrap">
                {c.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {data.rows.map((row) => (
            <tr key={row.label} className="border-b border-[var(--border)] border-opacity-40 hover:bg-[var(--bg-hover)]">
              <td className="py-1.5 pr-4 text-[var(--text-secondary)] whitespace-nowrap">{row.label}</td>
              {COLS.map((c) => (
                <td
                  key={c.key}
                  className={clsx('text-right py-1.5 px-3 tabular-nums', cellColor(row.label, row[c.key]))}
                >
                  {fmt(row.label, row[c.key])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

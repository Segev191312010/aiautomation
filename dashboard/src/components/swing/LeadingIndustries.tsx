import React from 'react'
import clsx from 'clsx'
import type { IndustryGroup } from '@/types'

interface Props {
  data: IndustryGroup[]
}

export default function LeadingIndustries({ data }: Props) {
  if (data.length === 0) return null

  return (
    <div className="card overflow-x-auto">
      <h3 className="shell-kicker mb-1">Leading Industries</h3>
      <p className="text-xs text-[var(--text-muted)] mb-3">
        Top 20% of industry groups by weekly and monthly relative strength.
      </p>
      <table className="w-full text-sm font-mono">
        <thead>
          <tr className="border-b border-[var(--border)]">
            <th className="text-left py-2 w-8 text-[var(--text-secondary)] font-medium">#</th>
            <th className="text-left py-2 px-3 text-[var(--text-secondary)] font-medium">Industry</th>
            <th className="text-right py-2 px-3 text-[var(--text-secondary)] font-medium">Stocks</th>
            <th className="text-right py-2 px-3 text-[var(--text-secondary)] font-medium">Week RS</th>
            <th className="text-right py-2 px-3 text-[var(--text-secondary)] font-medium">Month RS</th>
            <th className="text-right py-2 px-3 text-[var(--text-secondary)] font-medium">RS/SPY</th>
            <th className="text-left py-2 px-3 text-[var(--text-secondary)] font-medium">Top 4</th>
          </tr>
        </thead>
        <tbody>
          {data.map((row, i) => {
            const bothStrong = row.avg_weekly_return > 0 && row.avg_monthly_return > 0 && row.rs_vs_spy >= 1.5
            return (
              <tr
                key={row.industry}
                className={clsx(
                  'border-b border-[var(--border)] border-opacity-40 hover:bg-[var(--bg-hover)]',
                  bothStrong && 'bg-green-500/6',
                )}
              >
                <td className="py-1.5 text-[var(--text-muted)]">{i + 1}</td>
                <td className={clsx('py-1.5 px-3', bothStrong ? 'text-[var(--success)] font-semibold' : 'text-[var(--text-primary)]')}>
                  {row.industry}
                </td>
                <td className="py-1.5 px-3 text-right tabular-nums text-[var(--text-secondary)]">{row.stock_count}</td>
                <td className={clsx('py-1.5 px-3 text-right tabular-nums', row.avg_weekly_return >= 0 ? 'text-[var(--success)]' : 'text-[var(--danger)]')}>
                  {row.avg_weekly_return >= 0 ? '+' : ''}{row.avg_weekly_return.toFixed(1)}%
                </td>
                <td className={clsx('py-1.5 px-3 text-right tabular-nums', row.avg_monthly_return >= 0 ? 'text-[var(--success)]' : 'text-[var(--danger)]')}>
                  {row.avg_monthly_return >= 0 ? '+' : ''}{row.avg_monthly_return.toFixed(1)}%
                </td>
                <td className={clsx('py-1.5 px-3 text-right tabular-nums', row.rs_vs_spy >= 1.5 ? 'text-[var(--success)] font-semibold' : '')}>
                  {row.rs_vs_spy.toFixed(2)}x
                </td>
                <td className="py-1.5 px-3 text-xs text-[var(--accent)]">
                  {row.top_stocks.join(', ')}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

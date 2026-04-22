import React from 'react'
import clsx from 'clsx'
import type { Club97Entry } from '@/types'

interface Props {
  data: Club97Entry[]
}

function pctileColor(val: number): string {
  if (val >= 99) return 'text-[var(--success)] font-bold'
  if (val >= 98) return 'text-[var(--success)] font-semibold'
  return 'text-[var(--success)]'
}

export default function Club97Table({ data }: Props) {
  if (data.length === 0) return null

  return (
    <div className="card overflow-x-auto">
      <h3 className="shell-kicker mb-1">The 97 Club</h3>
      <p className="text-xs text-[var(--text-muted)] mb-3">
        $1B+ stocks in the top 3% on all three relative strength timeframes.
      </p>
      <table className="w-full text-sm font-mono">
        <thead>
          <tr className="border-b border-[var(--border)]">
            <th className="text-left py-2 pr-3 text-[var(--text-secondary)] font-medium">Symbol</th>
            <th className="text-right py-2 px-3 text-[var(--text-secondary)] font-medium">Price</th>
            <th className="text-right py-2 px-3 text-[var(--text-secondary)] font-medium">RS Day</th>
            <th className="text-right py-2 px-3 text-[var(--text-secondary)] font-medium">RS Week</th>
            <th className="text-right py-2 px-3 text-[var(--text-secondary)] font-medium">RS Month</th>
          </tr>
        </thead>
        <tbody>
          {data.map((row) => (
            <tr
              key={row.symbol}
              className={clsx(
                'border-b border-[var(--border)] border-opacity-40 hover:bg-[var(--bg-hover)]',
                row.is_tml && 'bg-blue-500/10',
              )}
            >
              <td className="py-1.5 pr-3">
                <span className="font-semibold text-[var(--accent)]">{row.symbol}</span>
                {row.is_tml && (
                  <span className="ml-2 text-[10px] px-1.5 py-0.5 rounded-full bg-blue-500/20 text-blue-400 font-medium">
                    TML
                  </span>
                )}
              </td>
              <td className="py-1.5 px-3 text-right tabular-nums">${row.price.toFixed(2)}</td>
              <td className={clsx('py-1.5 px-3 text-right tabular-nums', pctileColor(row.rs_day_pctile))}>
                {row.rs_day_pctile.toFixed(1)}
              </td>
              <td className={clsx('py-1.5 px-3 text-right tabular-nums', pctileColor(row.rs_week_pctile))}>
                {row.rs_week_pctile.toFixed(1)}
              </td>
              <td className={clsx('py-1.5 px-3 text-right tabular-nums', pctileColor(row.rs_month_pctile))}>
                {row.rs_month_pctile.toFixed(1)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

import React from 'react'
import clsx from 'clsx'
import type { TrendGradeDistribution } from '@/types'

interface Props {
  data: TrendGradeDistribution | null
}

const GRADE_ORDER = ['A+', 'A', 'A-', 'B+', 'B', 'B-', 'C+', 'C', 'C-', 'D+', 'D', 'D-', 'E+', 'E', 'E-', 'F']

function gradeColor(grade: string): string {
  if (grade.startsWith('A')) return 'bg-[var(--success)]'
  if (grade.startsWith('B')) return 'bg-emerald-500'
  if (grade.startsWith('C')) return 'bg-yellow-500'
  if (grade.startsWith('D')) return 'bg-orange-500'
  return 'bg-[var(--danger)]'
}

function gradeTextColor(grade: string): string {
  if (grade.startsWith('A')) return 'text-[var(--success)]'
  if (grade.startsWith('B')) return 'text-emerald-500'
  if (grade.startsWith('C')) return 'text-yellow-500'
  if (grade.startsWith('D')) return 'text-orange-500'
  return 'text-[var(--danger)]'
}

export default function TrendGrades({ data }: Props) {
  if (!data) return null

  const maxCount = Math.max(...GRADE_ORDER.map((g) => data.grades[g] ?? 0), 1)

  return (
    <div className="card">
      <h3 className="shell-kicker mb-1">Relative Trend Strength</h3>
      <p className="text-xs text-[var(--text-muted)] mb-4">
        Multi-timeframe relative strength grading (A+ strongest, F weakest).
      </p>

      {/* Distribution chart */}
      <div className="space-y-1 mb-5">
        {GRADE_ORDER.map((grade) => {
          const count = data.grades[grade] ?? 0
          const pct = (count / maxCount) * 100
          return (
            <div key={grade} className="flex items-center gap-2">
              <span className={clsx('w-6 text-xs font-mono font-semibold text-right', gradeTextColor(grade))}>{grade}</span>
              <div className="flex-1 h-3 rounded bg-[var(--bg-secondary)] overflow-hidden">
                <div
                  className={clsx('h-full rounded transition-all', gradeColor(grade))}
                  style={{ width: `${pct}%` }}
                />
              </div>
              <span className="w-10 text-xs font-mono tabular-nums text-right text-[var(--text-secondary)]">{count}</span>
            </div>
          )
        })}
      </div>

      {/* Top A+ stocks */}
      {data.top_graded.length > 0 && (
        <>
          <h4 className="text-xs font-semibold text-[var(--text-secondary)] mb-2">Top A+ Stocks</h4>
          <div className="overflow-x-auto">
            <table className="w-full text-sm font-mono">
              <thead>
                <tr className="border-b border-[var(--border)]">
                  <th className="text-left py-1.5 pr-3 text-[var(--text-secondary)] font-medium">Symbol</th>
                  <th className="text-right py-1.5 px-3 text-[var(--text-secondary)] font-medium">Price</th>
                  <th className="text-right py-1.5 px-3 text-[var(--text-secondary)] font-medium">Chg%</th>
                  <th className="text-center py-1.5 px-3 text-[var(--text-secondary)] font-medium">Grade</th>
                  <th className="text-right py-1.5 px-3 text-[var(--text-secondary)] font-medium">RS Score</th>
                </tr>
              </thead>
              <tbody>
                {data.top_graded.map((r) => (
                  <tr key={r.symbol} className="border-b border-[var(--border)] border-opacity-40 hover:bg-[var(--bg-hover)]">
                    <td className="py-1.5 pr-3 font-semibold text-[var(--accent)]">{r.symbol}</td>
                    <td className="py-1.5 px-3 text-right tabular-nums">${r.price.toFixed(2)}</td>
                    <td className={clsx('py-1.5 px-3 text-right tabular-nums', r.change_pct >= 0 ? 'text-[var(--success)]' : 'text-[var(--danger)]')}>
                      {r.change_pct >= 0 ? '+' : ''}{r.change_pct.toFixed(2)}%
                    </td>
                    <td className={clsx('py-1.5 px-3 text-center font-bold', gradeTextColor(r.grade))}>{r.grade}</td>
                    <td className="py-1.5 px-3 text-right tabular-nums font-semibold">{r.rs_composite.toFixed(1)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  )
}

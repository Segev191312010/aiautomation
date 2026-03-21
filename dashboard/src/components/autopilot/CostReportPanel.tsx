/**
 * CostReportPanel — Claude API cost breakdown for the AI Advisor.
 * Shows total cost, call count, average cost per call, and a daily bar chart.
 * Most recent day is rendered at the top. Data comes from props — no API calls.
 */
import React from 'react'
import clsx from 'clsx'
import type { CostReport } from '@/types/advisor'

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmtCost(v: number, decimals = 4): string {
  return `$${Number(v).toFixed(decimals)}`
}

// ── KPI Card ──────────────────────────────────────────────────────────────────

interface KPICardProps {
  label: string
  value: string
  sub?:  string
}

function KPICard({ label, value, sub }: KPICardProps) {
  return (
    <div className="bg-white border border-[var(--border)] rounded-xl p-4 flex flex-col gap-1.5 min-w-0">
      <span className="text-[10px] font-sans font-medium uppercase tracking-wider text-[var(--text-muted)] truncate">
        {label}
      </span>
      <span className="text-xl font-mono font-bold tabular-nums leading-none text-[var(--text-primary)]">
        {value}
      </span>
      {sub && (
        <span className="text-[11px] font-mono text-[var(--text-muted)]">{sub}</span>
      )}
    </div>
  )
}

// ── Main component ─────────────────────────────────────────────────────────────

interface Props {
  report: CostReport | null
}

export default function CostReportPanel({ report }: Props) {

  // Empty state
  if (!report || report.total_calls === 0) {
    return (
      <div className="flex items-center justify-center py-10 text-sm font-sans text-[var(--text-muted)]">
        No API calls recorded yet.
      </div>
    )
  }

  const { total_cost_usd, total_calls, daily } = report

  const avgCostPerCall = Number(total_cost_usd) / Math.max(Number(total_calls), 1)

  // Sort daily entries most-recent first
  const sortedDaily = [...daily].sort((a, b) => b.date.localeCompare(a.date))

  const maxCost = sortedDaily.reduce(
    (m, d) => Math.max(m, Number(d.estimated_cost_usd)),
    0,
  )

  return (
    <div className="space-y-5">

      {/* KPI row */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
        <KPICard
          label="Total Cost"
          value={fmtCost(total_cost_usd, 4)}
          sub={`over ${report.days} days`}
        />
        <KPICard
          label="Total Calls"
          value={String(total_calls)}
        />
        <KPICard
          label="Avg Cost / Call"
          value={fmtCost(avgCostPerCall, 4)}
        />
      </div>

      {/* Daily cost bars */}
      {sortedDaily.length > 0 && (
        <div className="space-y-2.5">
          <h4 className="text-[10px] font-sans font-semibold uppercase tracking-widest text-[var(--text-muted)]">
            Daily Cost
          </h4>
          <div className="space-y-2">
            {sortedDaily.map((day) => {
              const cost     = Number(day.estimated_cost_usd)
              const widthPct = maxCost > 0
                ? Math.max((cost / maxCost) * 100, 1)
                : 0

              return (
                <div key={day.date} className="flex items-center gap-3 min-w-0">
                  {/* Date label */}
                  <span className="text-[11px] font-mono text-[var(--text-muted)] w-24 flex-shrink-0 tabular-nums">
                    {day.date}
                  </span>

                  {/* Bar track */}
                  <div className="flex-1 h-5 bg-[var(--bg-hover)] rounded overflow-hidden relative">
                    <div
                      className={clsx(
                        'h-full rounded transition-all duration-300',
                        'bg-emerald-500',
                      )}
                      style={{ width: `${widthPct}%` }}
                    />
                  </div>

                  {/* Cost label */}
                  <span className="text-[11px] font-mono tabular-nums text-[var(--text-secondary)] w-20 flex-shrink-0 text-right">
                    {fmtCost(cost, 4)}
                  </span>

                  {/* Call count */}
                  <span className="text-[10px] font-mono text-[var(--text-muted)] w-14 flex-shrink-0 text-right">
                    {day.calls}c
                  </span>
                </div>
              )
            })}
          </div>
        </div>
      )}

    </div>
  )
}

/**
 * AIPerformanceCard — Learning performance dashboard for the AI Advisor.
 * Shows hit rate, scored decisions, P&L impact, and data quality over a
 * selectable time window. Includes by-action-type breakdown, warning banners,
 * and an economic summary row.
 * Data comes from props — no API calls.
 */
import React from 'react'
import clsx from 'clsx'
import type { LearningMetrics, EconomicReport, DataQuality } from '@/types/advisor'

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmtUSD(v: number, decimals = 2): string {
  const abs = Math.abs(Number(v))
  const sign = Number(v) < 0 ? '-' : ''
  return `${sign}$${abs.toFixed(decimals)}`
}

function hitRateColor(rate: number | null): string {
  if (rate === null) return 'text-[var(--text-muted)]'
  if (rate >= 0.65) return 'text-emerald-600'
  if (rate >= 0.55) return 'text-amber-600'
  return 'text-red-600'
}

function qualityBadgeClass(quality: DataQuality): string {
  switch (quality) {
    case 'good':         return 'bg-emerald-100 text-emerald-700 border-emerald-200'
    case 'moderate':     return 'bg-blue-100 text-blue-700 border-blue-200'
    case 'low':          return 'bg-amber-100 text-amber-700 border-amber-200'
    case 'insufficient': return 'bg-gray-100 text-gray-500 border-gray-200'
    default:             return 'bg-gray-100 text-gray-500 border-gray-200'
  }
}

function roiColor(roi: number): string {
  if (roi > 3) return 'text-emerald-600'
  if (roi > 1) return 'text-amber-600'
  return 'text-red-600'
}

// ── KPI Card ──────────────────────────────────────────────────────────────────

interface KPICardProps {
  label:      string
  value:      React.ReactNode
  valueClass?: string
  sub?:       string
}

function KPICard({ label, value, valueClass, sub }: KPICardProps) {
  return (
    <div className="bg-white border border-[var(--border)] rounded-xl p-4 flex flex-col gap-1.5 min-w-0">
      <span className="text-[10px] font-sans font-medium uppercase tracking-wider text-[var(--text-muted)] truncate">
        {label}
      </span>
      <span className={clsx(
        'text-xl font-mono font-bold tabular-nums leading-none',
        valueClass ?? 'text-[var(--text-primary)]',
      )}>
        {value}
      </span>
      {sub && (
        <span className="text-[11px] font-mono text-[var(--text-muted)]">{sub}</span>
      )}
    </div>
  )
}

// ── Section header ─────────────────────────────────────────────────────────────

function SectionHeader({ title }: { title: string }) {
  return (
    <h4 className="text-[10px] font-sans font-semibold uppercase tracking-widest text-[var(--text-muted)] pt-1">
      {title}
    </h4>
  )
}

// ── Main component ─────────────────────────────────────────────────────────────

interface Props {
  metrics:            LearningMetrics | null
  economicReport:     EconomicReport | null
  activeWindow:       7 | 30 | 90
  onWindowChange:     (days: 7 | 30 | 90) => void
  guardrailsTightened?: boolean
  tightenedReason?:   string | null
}

export default function AIPerformanceCard({
  metrics,
  economicReport,
  activeWindow,
  onWindowChange,
  guardrailsTightened = false,
  tightenedReason,
}: Props) {

  const windows = [7, 30, 90] as const

  // ── Hit rate KPI ──────────────────────────────────────────────────────────

  const hitRatePct = metrics?.hit_rate !== null && metrics?.hit_rate !== undefined
    ? `${(Number(metrics.hit_rate) * 100).toFixed(1)}%`
    : 'N/A'

  const hitRateClass = metrics?.hit_rate !== null && metrics?.hit_rate !== undefined
    ? hitRateColor(Number(metrics.hit_rate))
    : 'text-[var(--text-muted)]'

  // ── Scored KPI ────────────────────────────────────────────────────────────

  const scoredValue = metrics
    ? `${metrics.scored_decisions} / ${metrics.total_decisions}`
    : 'N/A'

  // ── P&L impact KPI ────────────────────────────────────────────────────────

  const pnlImpact     = Number(metrics?.net_pnl_impact ?? 0)
  const pnlDisplay    = metrics?.net_pnl_impact !== null && metrics?.net_pnl_impact !== undefined
    ? fmtUSD(pnlImpact)
    : 'N/A'
  const pnlClass      = pnlImpact >= 0 ? 'text-emerald-600' : 'text-red-600'

  // ── By-action-type table ──────────────────────────────────────────────────

  const actionEntries = metrics
    ? Object.entries(metrics.by_action_type ?? {})
    : []

  // ── Economic summary ──────────────────────────────────────────────────────

  const costPerDecision = Number(economicReport?.cost_per_decision ?? 0)
  const roi             = Number(economicReport?.roi_estimate ?? 0)
  const costAsPct       = Number(economicReport?.cost_as_pct_pnl ?? 0)

  return (
    <div className="space-y-5">

      {/* Window selector */}
      <div className="flex items-center gap-1 p-1 bg-[var(--bg-hover)] rounded-xl w-fit">
        {windows.map((w) => (
          <button
            key={w}
            onClick={() => onWindowChange(w)}
            className={clsx(
              'px-3 py-1.5 text-xs font-sans font-medium rounded-lg transition-colors',
              activeWindow === w
                ? 'bg-indigo-600 text-white shadow-sm'
                : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:bg-white/60',
            )}
          >
            {w}d
          </button>
        ))}
      </div>

      {/* Empty state */}
      {!metrics ? (
        <div className="flex items-center justify-center py-10 text-sm font-sans text-[var(--text-muted)]">
          Loading performance data...
        </div>
      ) : (
        <>
          {/* Auto-tighten banner */}
          {guardrailsTightened && (
            <div className="flex items-start gap-2.5 px-4 py-3 rounded-xl bg-orange-50 border border-orange-200">
              <span className="text-orange-500 flex-shrink-0 mt-px">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}
                  strokeLinecap="round" strokeLinejoin="round" className="w-4 h-4">
                  <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2" />
                </svg>
              </span>
              <div className="min-w-0">
                <p className="text-xs font-sans font-semibold text-orange-800">
                  Guardrails Auto-Tightened
                </p>
                {tightenedReason && (
                  <p className="text-xs font-sans text-orange-700 mt-0.5 leading-relaxed">
                    {tightenedReason}
                  </p>
                )}
              </div>
            </div>
          )}

          {/* Warning banner */}
          {metrics.warning && (
            <div className="flex items-start gap-2.5 px-4 py-3 rounded-xl bg-amber-50 border border-amber-200">
              <span className="text-amber-500 flex-shrink-0 mt-px">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}
                  strokeLinecap="round" strokeLinejoin="round" className="w-4 h-4">
                  <path d="M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z" />
                  <line x1="12" y1="9" x2="12" y2="13" />
                  <line x1="12" y1="17" x2="12.01" y2="17" />
                </svg>
              </span>
              <p className="text-xs font-sans text-amber-800 leading-relaxed">
                {metrics.warning}
              </p>
            </div>
          )}

          {/* KPI row */}
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            <KPICard
              label="Hit Rate"
              value={hitRatePct}
              valueClass={hitRateClass}
              sub={metrics.hit_rate !== null ? `${metrics.scored_decisions} decisions` : undefined}
            />
            <KPICard
              label="Scored"
              value={scoredValue}
              sub="scored / total"
            />
            <KPICard
              label="P&L Impact"
              value={pnlDisplay}
              valueClass={metrics.net_pnl_impact !== null ? pnlClass : 'text-[var(--text-muted)]'}
            />
            <KPICard
              label="Data Quality"
              value={
                <span className={clsx(
                  'text-xs font-sans font-semibold px-2 py-0.5 rounded-full border capitalize',
                  qualityBadgeClass(metrics.data_quality),
                )}>
                  {metrics.data_quality}
                </span>
              }
            />
          </div>

          {/* By-action-type breakdown */}
          {actionEntries.length > 0 && (
            <div className="space-y-2.5">
              <SectionHeader title="By Action Type" />
              <div className="overflow-x-auto">
                <table className="w-full min-w-[400px]">
                  <thead>
                    <tr className="border-b border-[var(--border)]">
                      {(['Action', 'Count', 'Hit Rate', 'Net P&L'] as const).map((col) => (
                        <th
                          key={col}
                          className={clsx(
                            'py-2 px-3 text-[9px] font-sans uppercase tracking-widest text-[var(--text-muted)] font-medium',
                            col === 'Action' ? 'text-left' : 'text-right',
                          )}
                        >
                          {col}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {actionEntries.map(([action, data]) => {
                      const hr     = Number(data.hit_rate)
                      const hrPct  = `${(hr * 100).toFixed(1)}%`
                      const hrClass =
                        hr >= 0.65 ? 'text-emerald-600' :
                        hr >= 0.55 ? 'text-amber-600'   :
                        'text-red-600'
                      const pnl      = Number(data.net_pnl)
                      const pnlClass = pnl >= 0 ? 'text-emerald-600' : 'text-red-600'

                      return (
                        <tr key={action} className="border-b border-[var(--border)]">
                          <td className="py-2.5 px-3 text-left">
                            <span className="text-xs font-mono text-[var(--text-primary)] capitalize">
                              {action}
                            </span>
                          </td>
                          <td className="py-2.5 px-3 text-right text-xs font-mono tabular-nums text-[var(--text-secondary)]">
                            {data.count}
                          </td>
                          <td className={clsx(
                            'py-2.5 px-3 text-right text-xs font-mono tabular-nums font-medium',
                            hrClass,
                          )}>
                            {hrPct}
                          </td>
                          <td className={clsx(
                            'py-2.5 px-3 text-right text-xs font-mono tabular-nums',
                            pnlClass,
                          )}>
                            {fmtUSD(pnl)}
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* Economic summary */}
          {economicReport && (
            <div className="space-y-2.5">
              <SectionHeader title="Economic Summary" />
              <div className="flex flex-wrap gap-4 px-1">
                <div className="flex flex-col gap-0.5">
                  <span className="text-[10px] font-sans text-[var(--text-muted)] uppercase tracking-wider">
                    Cost / Decision
                  </span>
                  <span className="text-sm font-mono font-semibold text-[var(--text-primary)] tabular-nums">
                    {fmtUSD(costPerDecision, 4)}
                  </span>
                </div>
                <div className="w-px self-stretch bg-[var(--border)]" />
                <div className="flex flex-col gap-0.5">
                  <span className="text-[10px] font-sans text-[var(--text-muted)] uppercase tracking-wider">
                    ROI
                  </span>
                  <span className={clsx(
                    'text-sm font-mono font-semibold tabular-nums',
                    economicReport.roi_estimate !== null
                      ? roiColor(Number(economicReport.roi_estimate))
                      : 'text-[var(--text-muted)]',
                  )}>
                    {economicReport.roi_estimate !== null
                      ? `${Number(economicReport.roi_estimate).toFixed(1)}x`
                      : 'N/A'}
                  </span>
                </div>
                <div className="w-px self-stretch bg-[var(--border)]" />
                <div className="flex flex-col gap-0.5">
                  <span className="text-[10px] font-sans text-[var(--text-muted)] uppercase tracking-wider">
                    Cost as % of P&L
                  </span>
                  <span className="text-sm font-mono font-semibold text-[var(--text-primary)] tabular-nums">
                    {economicReport.cost_as_pct_pnl !== null
                      ? `${Number(economicReport.cost_as_pct_pnl).toFixed(1)}%`
                      : 'N/A'}
                  </span>
                </div>
              </div>
            </div>
          )}

        </>
      )}
    </div>
  )
}

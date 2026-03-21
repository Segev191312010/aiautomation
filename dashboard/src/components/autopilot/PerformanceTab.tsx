/**
 * PerformanceTab — AI learning metrics, source/rule P&L breakdown, and cost report.
 * Reuses AIPerformanceCard and CostReportPanel. All data from useAdvisorStore.
 */
import React, { useEffect, useState } from 'react'
import AIPerformanceCard from '@/components/advisor/AIPerformanceCard'
import CostReportPanel from '@/components/advisor/CostReportPanel'
import { useAdvisorStore } from '@/store'
import type { RulePerformanceRow, SourcePerformance } from '@/types/advisor'
import {
  fetchAutopilotPerformance,
  fetchAutopilotRulePerformance,
  fetchAutopilotSourcePerformance,
} from '@/services/api'

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmtUsd(value: number | null | undefined) {
  if (value == null) return '--'
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits: 2,
  }).format(value)
}

// ── Source performance cards ──────────────────────────────────────────────────

function SourceCards({ items }: { items: SourcePerformance[] }) {
  if (!items.length) return null
  return (
    <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
      {items.map((item) => (
        <div
          key={item.source}
          className="bg-white border border-[var(--border)] rounded-xl p-4"
        >
          <div className="text-[9px] font-sans font-semibold uppercase tracking-widest text-[var(--text-muted)] mb-2">
            {item.source}
          </div>
          <div className="text-xl font-mono font-bold tabular-nums text-[var(--text-primary)] leading-none mb-1">
            {fmtUsd(item.realized_pnl)}
          </div>
          <div className="text-xs font-sans text-[var(--text-secondary)] mb-2">
            {item.trades_count} trades
            {item.hit_rate != null && ` · ${(item.hit_rate * 100).toFixed(1)}% hit rate`}
          </div>
          <div className="text-[10px] font-mono text-[var(--text-muted)]">
            Cost {fmtUsd(item.total_cost)} · ROI {item.roi != null ? item.roi.toFixed(2) : '--'}
          </div>
        </div>
      ))}
    </div>
  )
}

// ── Rule contribution table ───────────────────────────────────────────────────

function RuleContributionTable({ rows }: { rows: RulePerformanceRow[] }) {
  if (!rows.length) {
    return (
      <p className="text-sm font-sans text-[var(--text-muted)]">
        No rule-level performance history yet.
      </p>
    )
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[520px]">
        <thead>
          <tr className="border-b border-[var(--border)]">
            {['Rule', 'Source', 'Trades', 'Hit Rate', 'Net P&L'].map((col) => (
              <th
                key={col}
                className={`py-2.5 px-3 text-[9px] font-sans font-medium uppercase tracking-widest
                            text-[var(--text-muted)] ${col === 'Net P&L' ? 'text-right' : 'text-left'}`}
              >
                {col}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr
              key={`${row.rule_id}:${row.source}`}
              className="border-b border-[var(--border)]/60 last:border-b-0 hover:bg-[var(--bg-hover)] transition-colors"
            >
              <td className="py-3 px-3">
                <div className="text-xs font-sans font-medium text-[var(--text-primary)]">
                  {row.rule_name}
                </div>
                <div className="text-[9px] font-mono text-[var(--text-muted)]">{row.rule_id}</div>
              </td>
              <td className="py-3 px-3 text-xs font-sans text-[var(--text-secondary)]">
                {row.source}
              </td>
              <td className="py-3 px-3 text-xs font-mono tabular-nums text-[var(--text-secondary)]">
                {row.trades_count}
              </td>
              <td className="py-3 px-3 text-xs font-mono tabular-nums text-[var(--text-secondary)]">
                {row.hit_rate != null ? `${(row.hit_rate * 100).toFixed(1)}%` : '--'}
              </td>
              <td className={`py-3 px-3 text-right text-xs font-mono tabular-nums font-medium
                              ${row.net_pnl >= 0 ? 'text-emerald-600' : 'text-red-600'}`}>
                {fmtUsd(row.net_pnl)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// ── Main component ────────────────────────────────────────────────────────────

export default function PerformanceTab() {
  const {
    learningMetrics,
    costReport,
    economicReport,
    learningWindow,
    guardrails,
    setLearningWindow,
    fetchLearningMetrics,
    fetchCostReport,
    fetchEconomicReport,
  } = useAdvisorStore()

  const [sourcePerformance, setSourcePerformance] = useState<SourcePerformance[]>([])
  const [rulePerformance, setRulePerformance] = useState<RulePerformanceRow[]>([])

  useEffect(() => {
    void Promise.all([
      fetchLearningMetrics(),
      fetchCostReport(),
      fetchEconomicReport(),
      fetchAutopilotPerformance(30)
        .then((perf) => {
          return fetchAutopilotSourcePerformance(30).then((sources) => {
            setSourcePerformance(sources.length ? sources : perf.by_source)
          })
        })
        .catch(() => {}),
      fetchAutopilotRulePerformance(30)
        .then(setRulePerformance)
        .catch(() => {}),
    ])
  }, [fetchLearningMetrics, fetchCostReport, fetchEconomicReport])

  return (
    <div className="space-y-5">

      {/* Learning performance card */}
      <div className="bg-white border border-[var(--border)] rounded-xl p-5">
        <AIPerformanceCard
          metrics={learningMetrics}
          economicReport={economicReport}
          activeWindow={learningWindow}
          onWindowChange={setLearningWindow}
          guardrailsTightened={guardrails?.guardrails_currently_tightened}
          tightenedReason={guardrails?.tightened_reason}
        />
      </div>

      {/* Source breakdown */}
      {sourcePerformance.length > 0 && (
        <SourceCards items={sourcePerformance} />
      )}

      {/* Rule contribution + cost report */}
      <div className="grid grid-cols-1 xl:grid-cols-[1.2fr,0.8fr] gap-5">
        <div className="bg-white border border-[var(--border)] rounded-xl p-5">
          <div className="mb-4">
            <h2 className="text-sm font-semibold text-[var(--text-primary)]">Rule Contribution</h2>
            <p className="text-xs text-[var(--text-muted)] mt-0.5">
              Per-rule P&L breakdown across rule-driven and direct AI trading.
            </p>
          </div>
          <RuleContributionTable rows={rulePerformance} />
        </div>

        <div className="bg-white border border-[var(--border)] rounded-xl p-5">
          <h2 className="text-sm font-semibold text-[var(--text-primary)] mb-4">AI Cost Report</h2>
          <CostReportPanel report={costReport} />
        </div>
      </div>

    </div>
  )
}

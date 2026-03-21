/**
 * AdvisorKPIRow — 4-card KPI summary strip for the AI Advisor page.
 * Shows Total P&L, Win Rate, Profit Factor, Trade Count.
 * Data comes from props — no API calls.
 */
import React from 'react'
import clsx from 'clsx'
import type { AdvisorPnLSummary, PerformanceMetrics } from '@/types/advisor'

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmtUSD(v: number): string {
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(v)
}

// ── Single KPI card ───────────────────────────────────────────────────────────

interface CardProps {
  label:    string
  value:    string
  subValue?: string
  positive?: boolean   // true=green, false=red, undefined=neutral
}

function KPICard({ label, value, subValue, positive }: CardProps) {
  const valueColor =
    positive === true  ? 'text-emerald-600' :
    positive === false ? 'text-red-600'     :
    'text-[var(--text-primary)]'

  return (
    <div className="bg-white border border-[var(--border)] rounded-xl p-4 flex flex-col gap-1.5 min-w-0">
      <span className="text-[10px] font-sans font-medium uppercase tracking-wider text-[var(--text-muted)] truncate">
        {label}
      </span>
      <span className={clsx('text-xl font-mono font-bold tabular-nums leading-none', valueColor)}>
        {value}
      </span>
      {subValue && (
        <span className="text-[11px] font-mono text-[var(--text-muted)]">
          {subValue}
        </span>
      )}
    </div>
  )
}

// ── Main component ────────────────────────────────────────────────────────────

interface Props {
  pnlSummary:  AdvisorPnLSummary | null
  performance: PerformanceMetrics | null
  tradeCount:  number
}

export default function AdvisorKPIRow({ pnlSummary, performance, tradeCount }: Props) {
  const totalPnL       = Number(pnlSummary?.total_pnl) || 0
  const winRate        = Number(pnlSummary?.win_rate ?? performance?.win_rate) || 0
  const profitFactor   = Number(pnlSummary?.profit_factor ?? performance?.profit_factor) || 0
  const pfDisplay      = Number.isFinite(profitFactor) ? profitFactor.toFixed(2) : '\u221e'
  const pfPositive     = profitFactor >= 1 ? true : profitFactor > 0 ? undefined : false

  return (
    <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
      <KPICard
        label="Total P&L"
        value={fmtUSD(totalPnL)}
        subValue={`${tradeCount} trades`}
        positive={totalPnL >= 0}
      />
      <KPICard
        label="Win Rate"
        value={`${winRate.toFixed(1)}%`}
        positive={winRate >= 50}
      />
      <KPICard
        label="Profit Factor"
        value={pfDisplay}
        subValue={profitFactor >= 1.5 ? 'Strong' : profitFactor >= 1 ? 'Acceptable' : 'Weak'}
        positive={pfPositive}
      />
      <KPICard
        label="Trade Count"
        value={String(tradeCount)}
      />
    </div>
  )
}

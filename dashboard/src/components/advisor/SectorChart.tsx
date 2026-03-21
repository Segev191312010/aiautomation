/**
 * SectorChart — Horizontal CSS bar chart of sector P&L performance.
 * Each row shows: sector name, verdict badge, bar proportional to |total_pnl|,
 * and the P&L value. Green bars for positive, red for negative.
 * Data comes from props — no API calls.
 */
import React from 'react'
import clsx from 'clsx'
import type { SectorPerformance, SectorVerdict } from '@/types/advisor'

// ── Verdict badge ─────────────────────────────────────────────────────────────

const VERDICT_STYLES: Record<SectorVerdict, string> = {
  favor:   'bg-emerald-50 text-emerald-700 border border-emerald-200',
  avoid:   'bg-red-50 text-red-700 border border-red-200',
  neutral: 'bg-[var(--bg-hover)] text-[var(--text-secondary)] border border-[var(--border)]',
}

function VerdictBadge({ verdict }: { verdict: SectorVerdict }) {
  return (
    <span className={clsx(
      'text-[9px] font-sans font-semibold uppercase tracking-widest px-1.5 py-0.5 rounded flex-shrink-0',
      VERDICT_STYLES[verdict],
    )}>
      {verdict}
    </span>
  )
}

// ── Main component ────────────────────────────────────────────────────────────

interface Props {
  sectors: SectorPerformance[]
}

export default function SectorChart({ sectors }: Props) {
  if (!sectors || sectors.length === 0) {
    return (
      <div className="flex items-center justify-center py-8 text-sm font-sans text-[var(--text-muted)]">
        No sector performance data available.
      </div>
    )
  }

  const maxAbs = Math.max(...sectors.map((s) => Math.abs(s.total_pnl)), 1)

  const sorted = [...sectors].sort((a, b) => b.total_pnl - a.total_pnl)

  return (
    <div className="space-y-3">
      {sorted.map((sector) => {
        const pct = (Math.abs(sector.total_pnl) / maxAbs) * 100
        const isPos = sector.total_pnl >= 0

        return (
          <div key={sector.sector} className="space-y-1">
            {/* Label row */}
            <div className="flex items-center justify-between gap-3">
              <div className="flex items-center gap-2 min-w-0">
                <span className="text-xs font-sans font-medium text-[var(--text-primary)] truncate">
                  {sector.sector}
                </span>
                <VerdictBadge verdict={sector.verdict} />
              </div>
              <div className="flex items-center gap-2 flex-shrink-0">
                <span className="text-[10px] font-mono text-[var(--text-muted)]">
                  {sector.trade_count}t · {(Number(sector.win_rate) || 0).toFixed(0)}%wr
                </span>
                <span className={clsx(
                  'text-xs font-mono font-semibold tabular-nums',
                  isPos ? 'text-emerald-600' : 'text-red-600',
                )}>
                  {isPos ? '+' : ''}${(Number(sector.total_pnl) || 0).toFixed(0)}
                </span>
              </div>
            </div>

            {/* Bar */}
            <div className="h-2 bg-[var(--bg-hover)] rounded-full overflow-hidden">
              <div
                className={clsx(
                  'h-full rounded-full transition-all duration-300',
                  isPos ? 'bg-emerald-500' : 'bg-red-500',
                )}
                style={{ width: `${pct}%` }}
              />
            </div>
          </div>
        )
      })}
    </div>
  )
}

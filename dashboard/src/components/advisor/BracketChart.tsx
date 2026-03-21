/**
 * BracketChart — Stacked horizontal bar showing SL hits, TP hits, and Other exits.
 * Includes a warning badge when brackets_too_tight is true.
 * Data comes from props — no API calls.
 */
import React from 'react'
import clsx from 'clsx'
import type { BracketAnalysis } from '@/types/advisor'

// ── Main component ────────────────────────────────────────────────────────────

interface Props {
  analysis: BracketAnalysis
}

export default function BracketChart({ analysis }: Props) {
  const total = analysis.total_closed

  if (total === 0) {
    return (
      <div className="flex items-center justify-center py-8 text-sm font-sans text-[var(--text-muted)]">
        No closed trades to analyze.
      </div>
    )
  }

  const otherPct = Math.max(0, 100 - analysis.sl_hit_pct - analysis.tp_hit_pct)

  const segments = [
    { label: 'SL Hit',   count: analysis.sl_hits,    pct: analysis.sl_hit_pct, color: 'bg-red-500',     textColor: 'text-red-600'     },
    { label: 'TP Hit',   count: analysis.tp_hits,    pct: analysis.tp_hit_pct, color: 'bg-emerald-500', textColor: 'text-emerald-600' },
    { label: 'Other',    count: analysis.other_exits, pct: otherPct,            color: 'bg-gray-300',    textColor: 'text-[var(--text-secondary)]' },
  ]

  return (
    <div className="space-y-4">
      {/* Warning banner */}
      {analysis.brackets_too_tight && (
        <div className="flex items-center gap-2 px-3 py-2 rounded-xl bg-amber-50 border border-amber-200 text-amber-700">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}
            className="w-3.5 h-3.5 flex-shrink-0" strokeLinecap="round" strokeLinejoin="round">
            <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
            <line x1="12" y1="9" x2="12" y2="13" /><line x1="12" y1="17" x2="12.01" y2="17" />
          </svg>
          <span className="text-xs font-sans font-medium">
            Brackets too tight — SL hit rate is high. Consider widening your stop.
          </span>
        </div>
      )}

      {/* Stacked bar */}
      <div>
        <div className="flex h-8 rounded-lg overflow-hidden gap-px">
          {segments.map((seg) =>
            seg.pct > 0 ? (
              <div
                key={seg.label}
                className={clsx('flex items-center justify-center transition-all', seg.color)}
                style={{ width: `${seg.pct}%` }}
                title={`${seg.label}: ${seg.pct.toFixed(1)}%`}
              >
                {seg.pct >= 10 && (
                  <span className="text-[9px] font-mono font-bold text-white whitespace-nowrap px-1">
                    {seg.pct.toFixed(0)}%
                  </span>
                )}
              </div>
            ) : null,
          )}
        </div>
      </div>

      {/* Legend + stats */}
      <div className="grid grid-cols-3 gap-3">
        {segments.map((seg) => (
          <div key={seg.label} className="flex flex-col gap-1">
            <div className="flex items-center gap-1.5">
              <span className={clsx('w-2.5 h-2.5 rounded-sm flex-shrink-0', seg.color)} />
              <span className="text-[10px] font-sans text-[var(--text-muted)] uppercase tracking-wide">
                {seg.label}
              </span>
            </div>
            <span className={clsx('text-lg font-mono font-bold tabular-nums', seg.textColor)}>
              {seg.pct.toFixed(1)}%
            </span>
            <span className="text-[10px] font-mono text-[var(--text-muted)]">
              {seg.count} / {total}
            </span>
          </div>
        ))}
      </div>

      <p className="text-[10px] font-sans text-[var(--text-muted)]">
        Based on {total} closed trades. Ideal TP hit rate &gt; SL hit rate indicates working brackets.
      </p>
    </div>
  )
}

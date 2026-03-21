/**
 * ScoreBucketChart — Vertical bar chart of score bucket win rates.
 * Highlights the optimal min score bucket. Shows current vs optimal min score.
 * Data comes from props — no API calls.
 */
import React from 'react'
import clsx from 'clsx'
import type { ScoreAnalysis } from '@/types/advisor'

// ── Helpers ───────────────────────────────────────────────────────────────────

function isOptimalBucket(range: string, optimalMin: number): boolean {
  // range format: "60-70", "70-80", etc.
  const parts = range.split('-')
  if (parts.length !== 2) return false
  const lo = parseFloat(parts[0])
  return Math.abs(lo - optimalMin) < 1
}

// ── Main component ────────────────────────────────────────────────────────────

interface Props {
  analysis: ScoreAnalysis
}

export default function ScoreBucketChart({ analysis }: Props) {
  if (!analysis.available || analysis.buckets.length === 0) {
    return (
      <div className="flex items-center justify-center py-8 text-sm font-sans text-[var(--text-muted)]">
        Score analysis not available — insufficient data.
      </div>
    )
  }

  const maxWinRate = Math.max(...analysis.buckets.map((b) => b.win_rate), 1)

  return (
    <div className="space-y-4">
      {/* Current vs Optimal callout */}
      <div className="flex items-center gap-4 flex-wrap">
        <div className="flex items-center gap-2">
          <span className="text-[10px] font-sans text-[var(--text-muted)] uppercase tracking-wider">
            Current min score:
          </span>
          <span className="text-sm font-mono font-bold text-[var(--text-primary)]">
            {analysis.current_min_score}
          </span>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-[10px] font-sans text-[var(--text-muted)] uppercase tracking-wider">
            Optimal min score:
          </span>
          <span className={clsx(
            'text-sm font-mono font-bold',
            analysis.optimal_min_score > analysis.current_min_score
              ? 'text-emerald-600'
              : analysis.optimal_min_score < analysis.current_min_score
              ? 'text-amber-600'
              : 'text-[var(--text-primary)]',
          )}>
            {analysis.optimal_min_score}
          </span>
          {analysis.optimal_min_score !== analysis.current_min_score && (
            <span className="text-[9px] font-sans px-1.5 py-0.5 rounded bg-amber-50 text-amber-700 border border-amber-200">
              adjustment suggested
            </span>
          )}
        </div>
      </div>

      {/* Bar chart */}
      <div className="flex items-end gap-2 h-32">
        {analysis.buckets.map((bucket) => {
          const heightPct = (bucket.win_rate / maxWinRate) * 100
          const isOptimal = isOptimalBucket(bucket.range, analysis.optimal_min_score)

          return (
            <div key={bucket.range} className="flex-1 flex flex-col items-center gap-1">
              {/* Bar */}
              <div className="w-full flex flex-col items-center justify-end h-24 relative group">
                {/* Win rate label above bar */}
                <span className="absolute -top-5 left-1/2 -translate-x-1/2 text-[9px] font-mono text-[var(--text-secondary)] whitespace-nowrap">
                  {(Number(bucket.win_rate) || 0).toFixed(0)}%
                </span>
                <div
                  className={clsx(
                    'w-full rounded-t-md transition-all duration-300',
                    isOptimal
                      ? 'bg-emerald-500'
                      : bucket.win_rate >= 50
                      ? 'bg-emerald-300'
                      : 'bg-red-300',
                  )}
                  style={{ height: `${Math.max(heightPct, 4)}%` }}
                />
                {isOptimal && (
                  <div className="absolute -top-8 left-1/2 -translate-x-1/2 text-[8px] font-sans
                                  bg-emerald-50 text-emerald-700 border border-emerald-200
                                  px-1 py-0.5 rounded whitespace-nowrap">
                    optimal
                  </div>
                )}
              </div>

              {/* Score range label */}
              <span className="text-[9px] font-mono text-[var(--text-muted)] whitespace-nowrap">
                {bucket.range}
              </span>

              {/* Trade count */}
              <span className="text-[8px] font-sans text-[var(--text-muted)]">
                {bucket.count}t
              </span>
            </div>
          )
        })}
      </div>

      <p className="text-[10px] font-sans text-[var(--text-muted)]">
        Bar height = win rate per score bucket. Optimal bucket highlighted in green.
      </p>
    </div>
  )
}

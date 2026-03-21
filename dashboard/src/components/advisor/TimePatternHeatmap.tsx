/**
 * TimePatternHeatmap — 24-hour heatmap of trading time patterns.
 * Color intensity reflects win_rate (green=high, red=low).
 * Shows avg_pnl inside each cell and highlights market hours (9–16).
 * Data comes from props — no API calls.
 */
import React, { useState } from 'react'
import clsx from 'clsx'
import type { TimePattern } from '@/types/advisor'

// ── Helpers ───────────────────────────────────────────────────────────────────

function hourLabel(h: number): string {
  if (h === 0) return '12a'
  if (h < 12)  return `${h}a`
  if (h === 12) return '12p'
  return `${h - 12}p`
}

/** Returns a Tailwind bg class based on win_rate 0-100 */
function winRateBg(winRate: number, tradeCount: number): string {
  if (tradeCount === 0) return 'bg-[var(--bg-hover)]'
  if (winRate >= 65) return 'bg-emerald-500'
  if (winRate >= 55) return 'bg-emerald-400'
  if (winRate >= 50) return 'bg-emerald-200'
  if (winRate >= 45) return 'bg-red-200'
  if (winRate >= 35) return 'bg-red-400'
  return 'bg-red-500'
}

function winRateText(winRate: number, tradeCount: number): string {
  if (tradeCount === 0) return 'text-[var(--text-muted)]'
  if (winRate >= 55) return 'text-white'
  if (winRate >= 45) return 'text-[var(--text-primary)]'
  return 'text-white'
}

function isMarketHour(h: number): boolean {
  // NYSE 9:30-16:00 → whole hours 9-15 are market hours
  return h >= 9 && h <= 15
}

// ── Tooltip ───────────────────────────────────────────────────────────────────

interface TooltipProps {
  pattern: TimePattern
}

function Tooltip({ pattern }: TooltipProps) {
  return (
    <div className="absolute bottom-full left-1/2 -translate-x-1/2 mb-2 z-20 pointer-events-none
                    bg-white border border-[var(--border)] rounded-lg shadow-card px-2.5 py-2
                    text-[10px] font-mono whitespace-nowrap">
      <div className="font-semibold text-[var(--text-primary)]">{hourLabel(pattern.hour)}:00</div>
      <div className="text-[var(--text-secondary)]">
        Win: <span className={Number(pattern.win_rate) >= 50 ? 'text-emerald-600' : 'text-red-600'}>
          {(Number(pattern.win_rate) || 0).toFixed(0)}%
        </span>
      </div>
      <div className="text-[var(--text-secondary)]">
        Avg P&L: <span className={Number(pattern.avg_pnl) >= 0 ? 'text-emerald-600' : 'text-red-600'}>
          ${(Number(pattern.avg_pnl) || 0).toFixed(2)}
        </span>
      </div>
      <div className="text-[var(--text-muted)]">{pattern.trade_count} trades</div>
    </div>
  )
}

// ── Main component ────────────────────────────────────────────────────────────

interface Props {
  patterns: TimePattern[]
}

export default function TimePatternHeatmap({ patterns }: Props) {
  const [hovered, setHovered] = useState<number | null>(null)

  // Build a lookup map: hour -> pattern
  const byHour = new Map(patterns.map((p) => [p.hour, p]))

  const HOURS = Array.from({ length: 24 }, (_, i) => i)

  if (patterns.length === 0) {
    return (
      <div className="flex items-center justify-center py-8 text-sm font-sans text-[var(--text-muted)]">
        No time pattern data available.
      </div>
    )
  }

  return (
    <div className="space-y-3">
      {/* Legend */}
      <div className="flex items-center gap-3 text-[10px] font-sans text-[var(--text-muted)]">
        <span className="flex items-center gap-1">
          <span className="w-3 h-3 rounded-sm bg-emerald-500 inline-block" /> High win rate
        </span>
        <span className="flex items-center gap-1">
          <span className="w-3 h-3 rounded-sm bg-red-500 inline-block" /> Low win rate
        </span>
        <span className="flex items-center gap-1">
          <span className="w-3 h-3 rounded-sm border-2 border-amber-400 inline-block bg-transparent" /> Market hours
        </span>
      </div>

      {/* Grid */}
      <div className="grid grid-cols-12 gap-1">
        {HOURS.map((h) => {
          const p = byHour.get(h)
          const isMkt = isMarketHour(h)

          return (
            <div
              key={h}
              className="relative"
              onMouseEnter={() => setHovered(h)}
              onMouseLeave={() => setHovered(null)}
            >
              <div className={clsx(
                'h-10 rounded-md flex flex-col items-center justify-center cursor-default transition-opacity',
                p ? winRateBg(p.win_rate, p.trade_count) : 'bg-[var(--bg-hover)]',
                isMkt && 'ring-1 ring-amber-400 ring-inset',
                hovered === h && 'opacity-80',
              )}>
                <span className={clsx(
                  'text-[9px] font-sans font-medium leading-none',
                  p ? winRateText(p.win_rate, p.trade_count) : 'text-[var(--text-muted)]',
                )}>
                  {hourLabel(h)}
                </span>
                {p && p.trade_count > 0 && (
                  <span className={clsx(
                    'text-[8px] font-mono leading-none mt-0.5',
                    winRateText(p.win_rate, p.trade_count),
                  )}>
                    {(Number(p.win_rate) || 0).toFixed(0)}%
                  </span>
                )}
              </div>

              {/* Tooltip */}
              {hovered === h && p && p.trade_count > 0 && (
                <Tooltip pattern={p} />
              )}
            </div>
          )
        })}
      </div>

      {/* Market hours label */}
      <p className="text-[10px] font-sans text-[var(--text-muted)]">
        Market hours (9:30–16:00) highlighted in amber. Colors reflect win rate over the lookback period.
      </p>
    </div>
  )
}

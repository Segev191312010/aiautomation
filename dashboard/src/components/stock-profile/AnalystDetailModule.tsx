import { useState, useMemo } from 'react'
import clsx from 'clsx'
import type { StockAnalystDetail } from '@/types'
import FreshnessTag from './FreshnessTag'

// ── Grade color helpers ───────────────────────────────────────────────────────

type GradeTier = 'strong_buy' | 'buy' | 'hold' | 'sell' | 'strong_sell' | 'neutral'

function classifyGrade(grade: string): GradeTier {
  const g = grade.toLowerCase().trim()
  if (
    g === 'strong buy' || g === 'strong-buy' || g === 'conviction buy' ||
    g === 'strong positive'
  ) return 'strong_buy'
  if (
    g === 'buy' || g === 'overweight' || g === 'outperform' ||
    g === 'positive' || g === 'accumulate' || g === 'long-term buy' ||
    g === 'market outperform' || g === 'sector outperform' ||
    g === 'top pick' || g === 'add'
  ) return 'buy'
  if (
    g === 'hold' || g === 'neutral' || g === 'equal weight' ||
    g === 'equal-weight' || g === 'sector weight' || g === 'market perform' ||
    g === 'sector perform' || g === 'in-line' || g === 'inline' ||
    g === 'fair value' || g === 'peer perform'
  ) return 'hold'
  if (
    g === 'sell' || g === 'underweight' || g === 'underperform' ||
    g === 'negative' || g === 'reduce' || g === 'market underperform' ||
    g === 'sector underperform'
  ) return 'sell'
  if (g === 'strong sell' || g === 'strong-sell') return 'strong_sell'
  return 'neutral'
}

interface GradeStyle {
  pill: string
  dot: string
  text: string
}

const GRADE_STYLES: Record<GradeTier, GradeStyle> = {
  strong_buy:  { pill: 'bg-emerald-500/15 text-emerald-300 border-emerald-500/25', dot: 'bg-emerald-500', text: 'text-emerald-300' },
  buy:         { pill: 'bg-green-500/15 text-green-300 border-green-500/25',       dot: 'bg-green-500',   text: 'text-green-300'   },
  hold:        { pill: 'bg-amber-500/15 text-amber-300 border-amber-500/25',       dot: 'bg-amber-500',   text: 'text-amber-300'   },
  sell:        { pill: 'bg-orange-500/15 text-orange-300 border-orange-500/25',    dot: 'bg-orange-500',  text: 'text-orange-300'  },
  strong_sell: { pill: 'bg-red-500/15 text-red-300 border-red-500/25',             dot: 'bg-red-500',     text: 'text-red-300'     },
  neutral:     { pill: 'bg-white/[0.06] text-terminal-ghost border-white/[0.08]',  dot: 'bg-terminal-ghost', text: 'text-terminal-ghost' },
}

function gradePill(grade: string) {
  const tier = classifyGrade(grade)
  const style = GRADE_STYLES[tier]
  return (
    <span
      className={clsx(
        'inline-flex items-center px-2 py-0.5 rounded-full text-[9px] font-sans font-semibold border',
        style.pill,
      )}
    >
      {grade}
    </span>
  )
}

// ── Action color ──────────────────────────────────────────────────────────────

function actionStyle(action: string): string {
  const a = action.toLowerCase()
  if (a === 'upgrade' || a === 'initiated') return 'text-terminal-green'
  if (a === 'downgrade') return 'text-terminal-red'
  return 'text-terminal-ghost'
}

function actionBg(action: string): string {
  const a = action.toLowerCase()
  if (a === 'upgrade') return 'bg-terminal-green/[0.06]'
  if (a === 'downgrade') return 'bg-terminal-red/[0.06]'
  if (a === 'initiated') return 'bg-indigo-500/[0.06]'
  return 'bg-white/[0.02]'
}

// ── Grade summary tally ───────────────────────────────────────────────────────

interface GradeTally {
  strong_buy: number
  buy: number
  hold: number
  sell: number
  strong_sell: number
}

const SUMMARY_ITEMS: { key: keyof GradeTally; label: string; tier: GradeTier }[] = [
  { key: 'strong_buy',  label: 'Strong Buy',  tier: 'strong_buy'  },
  { key: 'buy',         label: 'Buy',         tier: 'buy'         },
  { key: 'hold',        label: 'Hold',        tier: 'hold'        },
  { key: 'sell',        label: 'Sell',        tier: 'sell'        },
  { key: 'strong_sell', label: 'Strong Sell', tier: 'strong_sell' },
]

// ── Trend bar constants ───────────────────────────────────────────────────────

const TREND_SEGMENTS = [
  { key: 'strong_buy'  as const, color: 'bg-emerald-500', label: 'Strong Buy'  },
  { key: 'buy'         as const, color: 'bg-green-500',   label: 'Buy'         },
  { key: 'hold'        as const, color: 'bg-amber-500',   label: 'Hold'        },
  { key: 'sell'        as const, color: 'bg-orange-500',  label: 'Sell'        },
  { key: 'strong_sell' as const, color: 'bg-red-500',     label: 'Strong Sell' },
]

// ── Analyst card ──────────────────────────────────────────────────────────────

interface GradeEntry {
  date: string
  firm: string
  to_grade: string
  from_grade: string
  action: string
}

function GradeCard({ entry }: { entry: GradeEntry }) {
  return (
    <div
      className={clsx(
        'flex flex-col gap-1.5 rounded-xl border border-white/[0.06] p-3 transition-colors',
        actionBg(entry.action),
      )}
    >
      <div className="flex items-start justify-between gap-1 min-w-0">
        <span className="text-[10px] font-sans font-medium text-terminal-text leading-snug truncate flex-1">
          {entry.firm}
        </span>
        <span className={clsx('text-[8px] font-sans capitalize shrink-0 mt-0.5', actionStyle(entry.action))}>
          {entry.action}
        </span>
      </div>

      <div className="flex items-center gap-1.5 flex-wrap">
        {entry.from_grade && (
          <>
            <span className="text-[9px] font-sans text-terminal-ghost line-through opacity-60">
              {entry.from_grade}
            </span>
            <span className={clsx('text-[9px]', actionStyle(entry.action))}>→</span>
          </>
        )}
        {gradePill(entry.to_grade)}
      </div>

      <span className="text-[8px] font-mono text-terminal-ghost">{entry.date}</span>
    </div>
  )
}

// ── Props ─────────────────────────────────────────────────────────────────────

interface Props {
  data: StockAnalystDetail | null
  loading: boolean
}

// ── Main Component ────────────────────────────────────────────────────────────

const INITIAL_SHOWN = 6

export default function AnalystDetailModule({ data, loading }: Props) {
  const [showAll, setShowAll] = useState(false)

  const tally = useMemo<GradeTally | null>(() => {
    if (!data?.upgrades_downgrades?.length) return null
    const t: GradeTally = { strong_buy: 0, buy: 0, hold: 0, sell: 0, strong_sell: 0 }
    for (const ud of data.upgrades_downgrades) {
      const tier = classifyGrade(ud.to_grade)
      if (tier === 'neutral') continue
      if (tier in t) t[tier as keyof GradeTally]++
    }
    return t
  }, [data])

  if (!data && loading) {
    return (
      <section className="glass rounded-2xl shadow-glass p-6 animate-pulse">
        <div className="h-3 w-32 bg-terminal-muted rounded-xl mb-5" />
        <div className="grid grid-cols-2 gap-2 mb-4">
          {[...Array(4)].map((_, i) => (
            <div key={i} className="h-20 bg-terminal-muted rounded-xl" />
          ))}
        </div>
        <div className="h-3 w-48 bg-terminal-muted rounded-xl mb-3" />
        <div className="h-20 bg-terminal-muted rounded-xl" />
      </section>
    )
  }
  if (!data) return null

  const upgrades   = data.upgrades_downgrades   ?? []
  const trend      = data.recommendation_trend  ?? []
  const hasGrades  = upgrades.length > 0
  const hasTrend   = trend.length > 0

  if (!hasGrades && !hasTrend) return null

  const visibleGrades = showAll ? upgrades : upgrades.slice(0, INITIAL_SHOWN)
  const hasMore = upgrades.length > INITIAL_SHOWN

  return (
    <section id="section-analyst-detail" className="glass rounded-2xl shadow-glass p-6 flex flex-col gap-5">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h3 className="text-xs font-sans font-medium text-terminal-dim tracking-wide">Analyst Grades</h3>
        <FreshnessTag fetchedAt={data.fetched_at} />
      </div>

      {/* Grade summary bar */}
      {tally && (
        <div className="flex flex-wrap gap-2 items-center">
          {SUMMARY_ITEMS.map(({ key, label, tier }) => {
            const count = tally[key]
            if (count === 0) return null
            const style = GRADE_STYLES[tier]
            return (
              <span
                key={key}
                className={clsx(
                  'inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[9px] font-sans border',
                  style.pill,
                )}
              >
                <span className={clsx('w-1.5 h-1.5 rounded-full shrink-0', style.dot)} />
                {label}
                <span className="font-mono font-bold">{count}</span>
              </span>
            )
          })}
        </div>
      )}

      {/* Grade cards grid */}
      {hasGrades && (
        <div>
          <div className="flex items-center justify-between mb-2">
            <span className="text-[9px] font-sans text-terminal-ghost uppercase tracking-wide">
              {showAll ? `All ${upgrades.length} grades` : `Top ${Math.min(INITIAL_SHOWN, upgrades.length)} of ${upgrades.length}`}
            </span>
          </div>

          <div className="grid grid-cols-2 md:grid-cols-3 gap-2">
            {visibleGrades.map((ud, i) => (
              <GradeCard key={i} entry={ud} />
            ))}
          </div>

          {hasMore && (
            <button
              onClick={() => setShowAll(v => !v)}
              className="mt-3 text-[10px] font-sans text-indigo-400 hover:text-indigo-300 transition-colors"
            >
              {showAll
                ? '▾ Show fewer'
                : `▸ Show all ${upgrades.length} grades`}
            </button>
          )}
        </div>
      )}

      {/* Recommendation Trend */}
      {hasTrend && (
        <div>
          <span className="text-[9px] font-sans text-terminal-ghost uppercase tracking-wide">
            Recommendation Trend
          </span>

          <div className="mt-3 flex flex-col gap-2">
            {trend.slice(-6).map((t) => {
              const total = t.strong_buy + t.buy + t.hold + t.sell + t.strong_sell
              if (total === 0) return null
              return (
                <div key={t.period} className="flex items-center gap-2.5">
                  <span className="text-[9px] font-mono text-terminal-ghost w-12 shrink-0">{t.period}</span>
                  <div className="flex-1 flex h-5 rounded-lg overflow-hidden">
                    {TREND_SEGMENTS.map(({ key, color, label }) => {
                      const count = t[key]
                      if (count === 0) return null
                      const pct = (count / total) * 100
                      return (
                        <div
                          key={key}
                          className={clsx('flex items-center justify-center', color)}
                          style={{ width: `${pct}%` }}
                          title={`${label}: ${count}`}
                        >
                          <span className="text-[8px] text-white font-mono font-bold leading-none">
                            {count}
                          </span>
                        </div>
                      )
                    })}
                  </div>
                  <span className="text-[9px] font-mono text-terminal-ghost w-6 text-right shrink-0">
                    {total}
                  </span>
                </div>
              )
            })}
          </div>

          {/* Legend */}
          <div className="flex flex-wrap gap-x-4 gap-y-1.5 mt-3">
            {TREND_SEGMENTS.map(({ color, label }) => (
              <div key={label} className="flex items-center gap-1.5">
                <div className={clsx('w-2 h-2 rounded-sm shrink-0', color)} />
                <span className="text-[8px] font-sans text-terminal-ghost">{label}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </section>
  )
}

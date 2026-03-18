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
  strong_buy:  { pill: 'bg-emerald-50 text-emerald-700 border-emerald-200', dot: 'bg-emerald-500', text: 'text-emerald-700' },
  buy:         { pill: 'bg-green-50 text-green-700 border-green-200',       dot: 'bg-green-500',   text: 'text-green-700'   },
  hold:        { pill: 'bg-amber-50 text-amber-700 border-amber-200',       dot: 'bg-amber-500',   text: 'text-amber-700'   },
  sell:        { pill: 'bg-orange-50 text-orange-700 border-orange-200',    dot: 'bg-orange-500',  text: 'text-orange-700'  },
  strong_sell: { pill: 'bg-red-50 text-red-700 border-red-200',             dot: 'bg-red-500',     text: 'text-red-700'     },
  neutral:     { pill: 'bg-gray-100 text-gray-400 border-gray-200',               dot: 'bg-gray-400',    text: 'text-gray-400'    },
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
  if (a === 'upgrade' || a === 'initiated') return 'text-green-600'
  if (a === 'downgrade') return 'text-red-600'
  return 'text-gray-400'
}

function actionBg(action: string): string {
  const a = action.toLowerCase()
  if (a === 'upgrade') return 'bg-green-50'
  if (a === 'downgrade') return 'bg-red-50'
  if (a === 'initiated') return 'bg-indigo-50'
  return 'bg-gray-50/70'
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
  price_target_action?: string | null
  price_target?: number | null
  prior_price_target?: number | null
}

function GradeCard({ entry }: { entry: GradeEntry }) {
  return (
    <div
      className={clsx(
        'flex flex-col gap-1.5 rounded-xl border border-gray-200 p-3 transition-colors',
        actionBg(entry.action),
      )}
    >
      <div className="flex items-start justify-between gap-1 min-w-0">
        <span className="text-[10px] font-sans font-medium text-gray-800 leading-snug truncate flex-1">
          {entry.firm}
        </span>
        <span className={clsx('text-[8px] font-sans capitalize shrink-0 mt-0.5', actionStyle(entry.action))}>
          {entry.action}
        </span>
      </div>

      <div className="flex items-center gap-1.5 flex-wrap">
        {entry.from_grade && (
          <>
            <span className="text-[9px] font-sans text-gray-400 line-through opacity-60">
              {entry.from_grade}
            </span>
            <span className={clsx('text-[9px]', actionStyle(entry.action))}>→</span>
          </>
        )}
        {gradePill(entry.to_grade)}
      </div>

      {(entry.price_target != null || entry.prior_price_target != null) && (
        <div className="flex items-center gap-1.5 flex-wrap text-[9px] font-mono text-gray-500">
          {entry.price_target_action && (
            <span className="uppercase tracking-wide text-[8px] text-gray-400">
              {entry.price_target_action}
            </span>
          )}
          {entry.prior_price_target != null && (
            <span className="line-through opacity-70">
              ${entry.prior_price_target.toFixed(0)}
            </span>
          )}
          {entry.price_target != null && (
            <span className="font-semibold text-gray-700">
              ${entry.price_target.toFixed(0)}
            </span>
          )}
        </div>
      )}

      <span className="text-[8px] font-mono text-gray-400">{entry.date}</span>
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
    const latest = data?.latest_recommendation
      ?? data?.recommendation_trend?.[data.recommendation_trend.length - 1]
    if (!latest) return null
    return {
      strong_buy: latest.strong_buy,
      buy: latest.buy,
      hold: latest.hold,
      sell: latest.sell,
      strong_sell: latest.strong_sell,
    }
  }, [data])

  if (!data && loading) {
    return (
      <section className="card rounded-lg shadow-card p-6 animate-pulse">
        <div className="h-3 w-32 bg-gray-100 rounded-xl mb-5" />
        <div className="grid grid-cols-2 gap-2 mb-4">
          {[...Array(4)].map((_, i) => (
            <div key={i} className="h-20 bg-gray-100 rounded-xl" />
          ))}
        </div>
        <div className="h-3 w-48 bg-gray-100 rounded-xl mb-3" />
        <div className="h-20 bg-gray-100 rounded-xl" />
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
    <section id="section-analyst-detail" className="card rounded-lg shadow-card p-6 flex flex-col gap-5">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h3 className="text-xs font-sans font-medium text-gray-500 tracking-wide">Analyst Grades</h3>
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
          {data?.latest_recommendation?.period && (
            <span className="text-[9px] font-mono text-gray-400 ml-auto">
              Snapshot {data.latest_recommendation.period}
            </span>
          )}
        </div>
      )}

      {/* Grade cards grid */}
      {hasGrades && (
        <div>
          <div className="flex items-center justify-between mb-2">
            <span className="text-[9px] font-sans text-gray-400 uppercase tracking-wide">
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
              className="mt-3 text-[10px] font-sans text-indigo-600 hover:text-indigo-600 transition-colors"
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
          <span className="text-[9px] font-sans text-gray-400 uppercase tracking-wide">
            Recommendation Trend
          </span>

          <div className="mt-3 flex flex-col gap-2">
            {trend.slice(-6).map((t) => {
              const total = t.strong_buy + t.buy + t.hold + t.sell + t.strong_sell
              if (total === 0) return null
              return (
                <div key={t.period} className="flex items-center gap-2.5">
                  <span className="text-[9px] font-mono text-gray-400 w-12 shrink-0">{t.period}</span>
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
                  <span className="text-[9px] font-mono text-gray-400 w-6 text-right shrink-0">
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
                <span className="text-[8px] font-sans text-gray-400">{label}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </section>
  )
}

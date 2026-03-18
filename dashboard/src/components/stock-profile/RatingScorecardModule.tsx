import clsx from 'clsx'
import type { StockRatingScorecard, ScorecardMetric } from '@/types'
import FreshnessTag from './FreshnessTag'

// ── Score → star count (1–5) ─────────────────────────────────────────────────

function scoreToStars(score: number | null): number {
  if (score == null) return 0
  if (score >= 80) return 5
  if (score >= 65) return 4
  if (score >= 50) return 3
  if (score >= 35) return 2
  return 1
}

// ── Grade colors ─────────────────────────────────────────────────────────────

function gradeTextColor(grade: string): string {
  const g = grade.replace(/[+-]/, '') // strip modifier for color lookup
  switch (g) {
    case 'A': return 'text-emerald-700'
    case 'B': return 'text-green-700'
    case 'C': return 'text-amber-600'
    case 'D': return 'text-orange-700'
    case 'F': return 'text-red-700'
    default:  return 'text-gray-400'
  }
}

function gradeBadgeBg(grade: string): string {
  const g = grade.replace(/[+-]/, '')
  switch (g) {
    case 'A': return 'bg-emerald-50 border-emerald-200'
    case 'B': return 'bg-green-50 border-green-200'
    case 'C': return 'bg-amber-50 border-amber-200'
    case 'D': return 'bg-orange-50 border-orange-200'
    case 'F': return 'bg-red-50 border-red-200'
    default:  return 'bg-gray-100 border-gray-200'
  }
}

// ── Star row component ───────────────────────────────────────────────────────

interface StarRowProps {
  metric: ScorecardMetric
}

function StarRow({ metric }: StarRowProps) {
  const stars = scoreToStars(metric.score)
  const isEmpty = stars === 0

  return (
    <div className="flex items-center justify-between py-2 border-b border-gray-100 last:border-0">
      {/* Metric name */}
      <span className="text-[11px] font-sans text-gray-500 leading-snug">
        {metric.name}
      </span>

      {/* Star display */}
      <div className="flex items-center gap-2 flex-shrink-0 ml-3">
        {isEmpty ? (
          <span className="text-[10px] font-sans text-gray-400">—</span>
        ) : (
          <div className="flex gap-0.5" aria-label={`${stars} out of 5 stars`}>
            {Array.from({ length: 5 }, (_, i) => (
              <StarIcon key={i} filled={i < stars} />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

// ── Individual star SVG ───────────────────────────────────────────────────────

function StarIcon({ filled }: { filled: boolean }) {
  return (
    <svg
      width="12"
      height="12"
      viewBox="0 0 12 12"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      aria-hidden="true"
    >
      <path
        d="M6 1L7.39 4.26L11 4.64L8.5 6.97L9.18 10.5L6 8.77L2.82 10.5L3.5 6.97L1 4.64L4.61 4.26L6 1Z"
        fill={filled ? '#f59e0b' : 'transparent'}
        stroke={filled ? '#f59e0b' : '#475569'}
        strokeWidth="0.8"
        strokeLinejoin="round"
      />
    </svg>
  )
}

// ── Overall grade badge ───────────────────────────────────────────────────────

interface GradeBadgeProps {
  grade: string
  score: number | null
}

function GradeBadge({ grade, score }: GradeBadgeProps) {
  const stars = scoreToStars(score)

  return (
    <div className="flex items-center gap-4">
      {/* Large grade badge */}
      <div
        className={clsx(
          'w-16 h-16 rounded-lg border flex items-center justify-center flex-shrink-0',
          gradeBadgeBg(grade),
        )}
      >
        <span className={clsx('text-3xl font-mono font-bold tracking-tight', gradeTextColor(grade))}>
          {grade || '—'}
        </span>
      </div>

      {/* Label + overall stars */}
      <div className="flex flex-col gap-1.5">
        <p className="text-sm font-sans font-semibold text-gray-800">Composite Fundamentals</p>
        {score != null && (
          <p className="text-[10px] font-mono text-gray-400 tabular-nums">
            Score: {score.toFixed(0)} / 100
          </p>
        )}
        {stars > 0 && (
          <div className="flex gap-0.5" aria-label={`${stars} out of 5 stars overall`}>
            {Array.from({ length: 5 }, (_, i) => (
              <StarIcon key={i} filled={i < stars} />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

// ── Skeleton ─────────────────────────────────────────────────────────────────

function LoadingSkeleton() {
  return (
    <section className="card rounded-lg shadow-card p-6 animate-pulse">
      <div className="flex items-center justify-between mb-5">
        <div className="h-3 w-36 bg-gray-100 rounded-lg" />
        <div className="h-3 w-12 bg-gray-100 rounded-lg" />
      </div>
      <div className="flex items-center gap-4 mb-5">
        <div className="w-16 h-16 bg-gray-100 rounded-lg flex-shrink-0" />
        <div className="flex flex-col gap-2 flex-1">
          <div className="h-3 w-28 bg-gray-100 rounded-lg" />
          <div className="h-3 w-20 bg-gray-100 rounded-lg" />
          <div className="h-3 w-24 bg-gray-100 rounded-lg" />
        </div>
      </div>
      <div className="space-y-2">
        {Array.from({ length: 8 }, (_, i) => (
          <div key={i} className="h-8 bg-gray-100 rounded-lg" />
        ))}
      </div>
    </section>
  )
}

// ── Main component ────────────────────────────────────────────────────────────

interface Props {
  data: StockRatingScorecard | null
  loading: boolean
}

export default function RatingScorecardModule({ data, loading }: Props) {
  if (!data && loading) return <LoadingSkeleton />
  if (!data) return null

  // Flatten all metrics from all categories into a single list
  const allMetrics: ScorecardMetric[] = data.categories.flatMap((cat) => cat.metrics)

  return (
    <section id="section-rating" className="card rounded-lg shadow-card p-6">

      {/* Header */}
      <div className="flex items-center justify-between mb-5">
        <h3 className="text-xs font-sans font-semibold text-gray-500 tracking-wide uppercase">
          Fundamental Scorecard
        </h3>
        <FreshnessTag fetchedAt={data.fetched_at} />
      </div>

      {/* Overall grade + stars */}
      <div className="mb-5 pb-4 border-b border-gray-200">
        <GradeBadge grade={data.overall_grade} score={data.overall_score} />
      </div>

      {/* Flat metrics list with star ratings */}
      {allMetrics.length > 0 ? (
        <div>
          {allMetrics.map((metric) => (
            <StarRow key={metric.name} metric={metric} />
          ))}
        </div>
      ) : (
        <p className="text-[11px] text-gray-400 text-center py-4">
          No metrics available
        </p>
      )}
    </section>
  )
}

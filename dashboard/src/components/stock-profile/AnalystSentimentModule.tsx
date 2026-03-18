import clsx from 'clsx'
import type { StockAnalyst } from '@/types'
import FreshnessTag from './FreshnessTag'

// ── Constants ────────────────────────────────────────────────────────────────
const GAUGE_RADIUS = 70
const GAUGE_STROKE = 12
const CX = 90
const CY = 88

// Arc circumference for a semi-circle (half of full circle)
const SEMI_CIRCUMFERENCE = Math.PI * GAUGE_RADIUS

// ── Helpers ───────────────────────────────────────────────────────────────────

interface RatingInfo {
  text: string
  color: string        // Tailwind text class
  hexColor: string     // raw hex for SVG
  bgClass: string
  borderClass: string
}

function getRatingInfo(mean: number): RatingInfo {
  if (mean <= 1.5) return {
    text: 'Strong Buy',
    color: 'text-emerald-600',
    hexColor: '#34d399',
    bgClass: 'bg-emerald-500/10',
    borderClass: 'border-emerald-500/20',
  }
  if (mean <= 2.5) return {
    text: 'Buy',
    color: 'text-emerald-400',
    hexColor: '#4ade80',
    bgClass: 'bg-emerald-500/10',
    borderClass: 'border-emerald-500/20',
  }
  if (mean <= 3.5) return {
    text: 'Hold',
    color: 'text-amber-600',
    hexColor: '#fbbf24',
    bgClass: 'bg-amber-500/10',
    borderClass: 'border-amber-500/20',
  }
  if (mean <= 4.5) return {
    text: 'Sell',
    color: 'text-orange-600',
    hexColor: '#fb923c',
    bgClass: 'bg-orange-500/10',
    borderClass: 'border-orange-500/20',
  }
  return {
    text: 'Strong Sell',
    color: 'text-red-400',
    hexColor: '#f87171',
    bgClass: 'bg-red-500/10',
    borderClass: 'border-red-500/20',
  }
}

// Map recommendation_mean (1–5) to a [0, 1] fraction along the semi-circle
// 1 = far left (Strong Buy), 5 = far right (Strong Sell)
function meanToFraction(mean: number): number {
  return Math.max(0, Math.min(1, (mean - 1) / 4))
}

// Convert fraction [0, 1] along the top semi-circle to (x, y) on SVG canvas
// The semi-circle goes from left (180°) to right (0°)
// angle = PI (left) down to 0 (right), so angle = PI - fraction * PI
function fractionToPoint(fraction: number): { x: number; y: number } {
  const angle = Math.PI - fraction * Math.PI
  return {
    x: CX + GAUGE_RADIUS * Math.cos(angle),
    y: CY - GAUGE_RADIUS * Math.sin(angle),
  }
}

// ── Distribution segments ─────────────────────────────────────────────────────

interface Segment {
  key: string
  label: string
  shortLabel: string
  color: string        // Tailwind bg class
  hexColor: string
}

const SEGMENTS: Segment[] = [
  { key: 'strongBuy',  label: 'Strong Buy',  shortLabel: 'S.Buy',  color: 'bg-emerald-500', hexColor: '#10b981' },
  { key: 'buy',        label: 'Buy',          shortLabel: 'Buy',    color: 'bg-emerald-500',   hexColor: '#22c55e' },
  { key: 'hold',       label: 'Hold',         shortLabel: 'Hold',   color: 'bg-amber-500',   hexColor: '#f59e0b' },
  { key: 'sell',       label: 'Sell',         shortLabel: 'Sell',   color: 'bg-orange-500',  hexColor: '#f97316' },
  { key: 'strongSell', label: 'Strong Sell',  shortLabel: 'S.Sell', color: 'bg-red-500',     hexColor: '#ef4444' },
]

// ── Sub-components ────────────────────────────────────────────────────────────

interface GaugeProps {
  mean: number
  ratingInfo: RatingInfo
}

function SemiCircleGauge({ mean, ratingInfo }: GaugeProps) {
  const fraction = meanToFraction(mean)
  const needle = fractionToPoint(fraction)

  // We draw 5 equal arc segments using dasharray tricks.
  // Each segment is 1/5 of the semi-circumference.
  const segLen = SEMI_CIRCUMFERENCE / 5
  const innerRadius = GAUGE_RADIUS - GAUGE_STROKE / 2 // for inner glow ring

  // Build an arc path for the semi-circle (left to right along top)
  // start: leftmost point (180°), end: rightmost point (0°)
  const arcStartX = CX - GAUGE_RADIUS
  const arcStartY = CY
  const arcEndX = CX + GAUGE_RADIUS
  const arcEndY = CY

  return (
    <svg
      viewBox={`0 0 ${CX * 2} ${CY + 8}`}
      className="w-full max-w-[220px] mx-auto overflow-visible"
      aria-label={`Analyst sentiment gauge: ${mean.toFixed(2)} — ${ratingInfo.text}`}
    >
      <defs>
        {/* Gradient along the arc track */}
        <linearGradient id="gaugeGrad" x1="0%" y1="0%" x2="100%" y2="0%">
          <stop offset="0%"   stopColor="#10b981" />   {/* emerald */}
          <stop offset="25%"  stopColor="#22c55e" />   {/* green */}
          <stop offset="50%"  stopColor="#f59e0b" />   {/* amber */}
          <stop offset="75%"  stopColor="#f97316" />   {/* orange */}
          <stop offset="100%" stopColor="#ef4444" />   {/* red */}
        </linearGradient>

        {/* Track background gradient (dimmed) */}
        <linearGradient id="gaugeTrack" x1="0%" y1="0%" x2="100%" y2="0%">
          <stop offset="0%"   stopColor="#10b981" stopOpacity="0.18" />
          <stop offset="50%"  stopColor="#f59e0b" stopOpacity="0.18" />
          <stop offset="100%" stopColor="#ef4444" stopOpacity="0.18" />
        </linearGradient>

        {/* Needle glow filter */}
        <filter id="needleGlow" x="-50%" y="-50%" width="200%" height="200%">
          <feGaussianBlur stdDeviation="2" result="blur" />
          <feMerge>
            <feMergeNode in="blur" />
            <feMergeNode in="SourceGraphic" />
          </feMerge>
        </filter>

        {/* Active arc clip: we use strokeDashoffset to show only up to the needle position */}
        {/* Track mask for rounded arc look */}
      </defs>

      {/* ── Background track (dim) ── */}
      <path
        d={`M ${arcStartX} ${arcStartY} A ${GAUGE_RADIUS} ${GAUGE_RADIUS} 0 0 1 ${arcEndX} ${arcEndY}`}
        fill="none"
        stroke="url(#gaugeTrack)"
        strokeWidth={GAUGE_STROKE}
        strokeLinecap="round"
      />

      {/* ── Colored gradient arc (full) ── */}
      <path
        d={`M ${arcStartX} ${arcStartY} A ${GAUGE_RADIUS} ${GAUGE_RADIUS} 0 0 1 ${arcEndX} ${arcEndY}`}
        fill="none"
        stroke="url(#gaugeGrad)"
        strokeWidth={GAUGE_STROKE}
        strokeLinecap="round"
        opacity="0.55"
      />

      {/* ── Active arc highlight — fills from left up to needle position ── */}
      <path
        d={`M ${arcStartX} ${arcStartY} A ${GAUGE_RADIUS} ${GAUGE_RADIUS} 0 0 1 ${arcEndX} ${arcEndY}`}
        fill="none"
        stroke="url(#gaugeGrad)"
        strokeWidth={GAUGE_STROKE}
        strokeLinecap="round"
        strokeDasharray={`${SEMI_CIRCUMFERENCE}`}
        strokeDashoffset={SEMI_CIRCUMFERENCE * (1 - fraction)}
        opacity="1"
      />

      {/* ── Tick marks at zone boundaries (1, 2, 3, 4, 5) ── */}
      {[0, 0.25, 0.5, 0.75, 1].map((f, i) => {
        const pt = fractionToPoint(f)
        const outerPt = {
          x: CX + (GAUGE_RADIUS + GAUGE_STROKE / 2 + 3) * Math.cos(Math.PI - f * Math.PI),
          y: CY - (GAUGE_RADIUS + GAUGE_STROKE / 2 + 3) * Math.sin(Math.PI - f * Math.PI),
        }
        return (
          <line
            key={i}
            x1={pt.x}
            y1={pt.y}
            x2={outerPt.x}
            y2={outerPt.y}
            stroke="rgba(107,114,128,0.35)"
            strokeWidth="1"
          />
        )
      })}

      {/* ── Needle ── */}
      {/* Needle base circle */}
      <circle cx={CX} cy={CY} r={6} fill="#111827" stroke="rgba(107,114,128,0.35)" strokeWidth="1.5" />

      {/* Needle line from center to arc */}
      <line
        x1={CX}
        y1={CY}
        x2={needle.x}
        y2={needle.y}
        stroke={ratingInfo.hexColor}
        strokeWidth="2.5"
        strokeLinecap="round"
        filter="url(#needleGlow)"
      />

      {/* Needle tip circle */}
      <circle
        cx={needle.x}
        cy={needle.y}
        r={4}
        fill={ratingInfo.hexColor}
        filter="url(#needleGlow)"
      />

      {/* Center hub overlay */}
      <circle cx={CX} cy={CY} r={4} fill="#111827" />
      <circle cx={CX} cy={CY} r={2} fill={ratingInfo.hexColor} opacity="0.8" />

      {/* ── Zone labels along the outer edge ── */}
      {[
        { f: 0,    label: '1' },
        { f: 0.25, label: '2' },
        { f: 0.5,  label: '3' },
        { f: 0.75, label: '4' },
        { f: 1,    label: '5' },
      ].map(({ f, label }) => {
        const angle = Math.PI - f * Math.PI
        const r2 = GAUGE_RADIUS + GAUGE_STROKE / 2 + 10
        return (
          <text
            key={label}
            x={CX + r2 * Math.cos(angle)}
            y={CY - r2 * Math.sin(angle) + 3}
            textAnchor="middle"
            fontSize="7"
            fill="rgba(148,163,184,0.6)"
            fontFamily="ui-monospace, monospace"
          >
            {label}
          </text>
        )
      })}
    </svg>
  )
}

// ── Distribution Bar ──────────────────────────────────────────────────────────

interface DistributionBarProps {
  counts: number[]
  totalAnalysts: number | null
  period: string | null
}

function DistributionBar({ counts, totalAnalysts, period }: DistributionBarProps) {
  const total = counts.reduce((sum, count) => sum + count, 0)
  if (total <= 0) return null

  return (
    <div className="mt-5">
      <div className="flex items-center justify-between gap-3 mb-2">
        <div className="text-[9px] font-sans text-zinc-500 uppercase tracking-wider">
          Latest Recommendation Mix
        </div>
        {period && (
          <div className="text-[9px] font-mono text-zinc-500">
            {period}
          </div>
        )}
      </div>

      <div className="flex h-5 rounded-full overflow-hidden bg-[#FAF8F5] border border-zinc-800">
        {SEGMENTS.map((seg, i) => {
          const count = counts[i]
          if (count <= 0) return null
          return (
            <div
              key={seg.key}
              className={clsx(seg.color, 'transition-all duration-500')}
              style={{ width: `${(count / total) * 100}%` }}
              title={`${seg.label}: ${count}`}
            />
          )
        })}
      </div>

      <div className="grid grid-cols-5 gap-1 mt-2">
        {SEGMENTS.map((seg, i) => (
          <div key={seg.key} className="rounded-lg border border-zinc-800 bg-zinc-900 px-2 py-1.5 text-center">
            <div className="text-[9px] font-mono font-semibold text-zinc-400 tabular-nums">
              {counts[i]}
            </div>
            <div className="text-[8px] font-sans text-zinc-500 truncate">
              {seg.shortLabel}
            </div>
          </div>
        ))}
      </div>

      {totalAnalysts != null && totalAnalysts !== total && (
        <div className="mt-2 text-[9px] font-sans text-zinc-500">
          Consensus buckets sum to {total}; reported analyst count is {totalAnalysts}.
        </div>
      )}
    </div>
  )
}

// ── Props & main component ────────────────────────────────────────────────────

interface Props {
  data: StockAnalyst | null
  loading: boolean
}

export default function AnalystSentimentModule({ data, loading }: Props) {
  // Loading skeleton
  if (!data && loading) {
    return (
      <section className="card rounded-lg  p-6 animate-pulse">
        <div className="flex items-center justify-between mb-4">
          <div className="h-3 w-36 bg-zinc-800 rounded-xl" />
          <div className="h-3 w-12 bg-zinc-800 rounded-xl" />
        </div>
        {/* Gauge placeholder */}
        <div className="flex justify-center mb-4">
          <div className="w-44 h-24 bg-zinc-800 rounded-xl" />
        </div>
        {/* Big number placeholder */}
        <div className="flex flex-col items-center gap-2 mb-4">
          <div className="h-8 w-16 bg-zinc-800 rounded-xl" />
          <div className="h-4 w-24 bg-zinc-800 rounded-xl" />
        </div>
        {/* Bar placeholder */}
        <div className="h-5 w-full bg-zinc-800 rounded-full" />
      </section>
    )
  }

  if (!data) return null

  const hasRating = data.recommendation_mean != null
  if (!hasRating) return null

  const mean = data.recommendation_mean!
  const ratingInfo = getRatingInfo(mean)
  const counts = [
    data.strong_buy ?? 0,
    data.buy ?? 0,
    data.hold ?? 0,
    data.sell ?? 0,
    data.strong_sell ?? 0,
  ]
  const hasDistribution = counts.some((count) => count > 0)

  return (
    <section id="section-analyst" className="card rounded-lg  p-6">
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-xs font-sans font-medium text-zinc-400 tracking-wide">
          Analyst Sentiment
        </h3>
        <FreshnessTag fetchedAt={data.fetched_at} />
      </div>

      {/* Gauge */}
      <SemiCircleGauge mean={mean} ratingInfo={ratingInfo} />

      {/* Center readout — number + label */}
      <div className="flex flex-col items-center -mt-2 mb-1">
        <span
          className={clsx(
            'text-3xl font-mono font-bold tabular-nums leading-none',
            ratingInfo.color
          )}
        >
          {mean.toFixed(2)}
        </span>
        <span
          className={clsx(
            'text-sm font-sans font-semibold mt-1 px-3 py-0.5 rounded-full border',
            ratingInfo.color,
            ratingInfo.bgClass,
            ratingInfo.borderClass,
          )}
        >
          {ratingInfo.text}
        </span>
        {data.num_analyst_opinions != null && (
          <span className="text-[10px] font-sans text-zinc-500 mt-1.5">
            Based on {data.num_analyst_opinions} analyst
            {data.num_analyst_opinions !== 1 ? 's' : ''}
          </span>
        )}
        {data.recommendation_period && (
          <span className="text-[10px] font-mono text-zinc-500 mt-1">
            Snapshot {data.recommendation_period}
          </span>
        )}
      </div>

      {/* Scale legend */}
      <div className="flex justify-between text-[8px] font-sans text-zinc-500 mt-3 px-1">
        <span className="text-emerald-500/70">Strong Buy</span>
        <span className="text-amber-500/70">Hold</span>
        <span className="text-red-400/70">Strong Sell</span>
      </div>

      {/* Divider */}
      <div className="border-t border-zinc-800 mt-4" />

      {/* Distribution bar */}
      {hasDistribution ? (
        <DistributionBar
          counts={counts}
          totalAnalysts={data.num_analyst_opinions}
          period={data.recommendation_period}
        />
      ) : (
        <p className="mt-5 text-[10px] font-sans text-zinc-500">
          No recommendation bucket breakdown available for this symbol.
        </p>
      )}
    </section>
  )
}

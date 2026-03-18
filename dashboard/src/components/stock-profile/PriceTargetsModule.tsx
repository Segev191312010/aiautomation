import type { StockAnalyst, StockOverview } from '@/types'
import FreshnessTag from './FreshnessTag'

interface Props {
  analyst: StockAnalyst | null
  overview: StockOverview | null
  loading?: boolean
}

interface TargetCardProps {
  label: string
  value: number | null
  colorClass: string
  bgClass: string
  borderClass: string
  large?: boolean
}

function TargetCard({ label, value, colorClass, bgClass, borderClass, large }: TargetCardProps) {
  return (
    <div className={`flex flex-col items-center justify-center p-4 rounded-xl border ${bgClass} ${borderClass}`}>
      <span className="text-[9px] font-sans font-medium text-gray-400 uppercase tracking-wider mb-1.5">
        {label}
      </span>
      <span
        className={[
          'font-mono font-bold tabular-nums',
          large ? 'text-2xl' : 'text-lg',
          colorClass,
        ].join(' ')}
      >
        {value != null ? `$${value.toFixed(2)}` : '—'}
      </span>
    </div>
  )
}

export default function PriceTargetsModule({ analyst, overview, loading = false }: Props) {
  if (!analyst && loading) {
    return (
      <section id="section-targets" className="card rounded-lg shadow-card p-6 animate-pulse">
        <div className="h-3 w-24 bg-gray-100 rounded mb-4" />
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-5">
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="h-24 rounded-lg bg-gray-100" />
          ))}
        </div>
        <div className="h-3 w-full rounded bg-gray-100 mb-2" />
        <div className="h-3 w-2/3 rounded bg-gray-100" />
      </section>
    )
  }
  if (!analyst) return null

  const hasTargets = analyst.target_low_price != null && analyst.target_high_price != null
  if (!hasTargets) {
    return (
      <section id="section-targets" className="card rounded-lg shadow-card p-6">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-xs font-sans font-medium text-gray-500 tracking-wide">Price Targets</h3>
          <FreshnessTag fetchedAt={analyst.fetched_at} />
        </div>
        <p className="text-[11px] font-sans text-gray-400 text-center py-4">
          No price target data available.
        </p>
      </section>
    )
  }

  const low = analyst.target_low_price!
  const high = analyst.target_high_price!
  const median = analyst.target_median_price
  const mean = analyst.target_mean_price
  const currentPrice = overview?.price ?? analyst.current_price ?? null
  const range = high - low

  // Clamp a value to [0, 100] percent of the low–high range
  const pctOf = (val: number): number =>
    range > 0 ? Math.max(0, Math.min(100, ((val - low) / range) * 100)) : 50

  return (
    <section id="section-targets" className="card rounded-lg shadow-card p-6">
      <div className="flex items-center justify-between mb-5">
        <div>
          <h3 className="text-xs font-sans font-medium text-gray-500 tracking-wide">Price Targets</h3>
          {analyst.recommendation_period && (
            <p className="mt-1 text-[10px] font-mono text-gray-400">
              Consensus snapshot {analyst.recommendation_period}
            </p>
          )}
        </div>
        <FreshnessTag fetchedAt={analyst.fetched_at} />
      </div>

      {/* 2x2 target cards grid */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-5">
        <TargetCard
          label="Target High"
          value={high}
          colorClass="text-green-600"
          bgClass="bg-green-50"
          borderClass="border-green-300/20"
        />
        <TargetCard
          label="Target Low"
          value={low}
          colorClass="text-red-600"
          bgClass="bg-red-50"
          borderClass="border-red-300/20"
        />
        <TargetCard
          label="Target Median"
          value={median}
          colorClass="text-gray-800"
          bgClass="bg-gray-100/60"
          borderClass="border-gray-200"
          large
        />
        <TargetCard
          label="Consensus"
          value={mean}
          colorClass="text-indigo-600"
          bgClass="bg-indigo-50"
          borderClass="border-indigo-100"
        />
      </div>

      {/* Visual range bar */}
      <div className="mb-1">
        {/* Labels above bar: low and high anchors */}
        <div className="flex justify-between text-[9px] font-mono text-gray-400 mb-1">
          <span>${low.toFixed(0)}</span>
          <span className="text-gray-400 text-center flex-1 px-2 truncate">
            {currentPrice != null && (
              <span className="text-amber-400">Current ${currentPrice.toFixed(2)}</span>
            )}
          </span>
          <span>${high.toFixed(0)}</span>
        </div>

        {/* The bar itself */}
        <div className="relative h-3 bg-gradient-to-r from-red-600/30 via-gray-200 to-green-600/30 rounded-full">
          {/* Median marker — white dot */}
          {median != null && (
            <div
              className="absolute w-3 h-3 bg-white border-2 border-[#FAF8F5] rounded-full top-0"
              style={{ left: `calc(${pctOf(median)}% - 6px)` }}
              title={`Median: $${median.toFixed(2)}`}
            />
          )}

          {/* Consensus marker — indigo dot */}
          {mean != null && (
            <div
              className="absolute w-3 h-3 bg-indigo-600 border-2 border-[#FAF8F5] rounded-full top-0"
              style={{ left: `calc(${pctOf(mean)}% - 6px)` }}
              title={`Consensus: $${mean.toFixed(2)}`}
            />
          )}

          {/* Current price marker — amber vertical tick */}
          {currentPrice != null && (
            <div
              className="absolute -top-1 -bottom-1 flex items-center"
              style={{ left: `calc(${pctOf(currentPrice)}% - 5px)` }}
              title={`Current: $${currentPrice.toFixed(2)}`}
            >
              <div className="w-2.5 h-5 bg-amber-400 rounded-sm border border-[#FAF8F5] opacity-90" />
            </div>
          )}
        </div>
      </div>

      {/* Legend row */}
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 mt-3">
        {median != null && (
          <div className="flex items-center gap-1.5">
            <div className="w-2.5 h-2.5 rounded-full bg-white border border-[#FAF8F5] shrink-0" />
            <span className="text-[9px] font-sans text-gray-400">Median</span>
          </div>
        )}
        {mean != null && (
          <div className="flex items-center gap-1.5">
            <div className="w-2.5 h-2.5 rounded-full bg-indigo-600 border border-[#FAF8F5] shrink-0" />
            <span className="text-[9px] font-sans text-gray-400">Consensus</span>
          </div>
        )}
        {currentPrice != null && (
          <div className="flex items-center gap-1.5">
            <div className="w-2 h-3 rounded-sm bg-amber-400 shrink-0" />
            <span className="text-[9px] font-sans text-gray-400">Current Price</span>
          </div>
        )}
        {analyst.num_analyst_opinions != null && (
          <span className="text-[9px] font-sans text-gray-400 ml-auto">
            {analyst.num_analyst_opinions} analyst{analyst.num_analyst_opinions !== 1 ? 's' : ''}
          </span>
        )}
      </div>
    </section>
  )
}

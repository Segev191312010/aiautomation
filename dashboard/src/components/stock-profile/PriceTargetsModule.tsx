import type { StockAnalyst, StockOverview } from '@/types'
import FreshnessTag from './FreshnessTag'

interface Props {
  analyst: StockAnalyst | null
  overview: StockOverview | null
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
      <span className="text-[9px] font-sans font-medium text-terminal-ghost uppercase tracking-wider mb-1.5">
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

export default function PriceTargetsModule({ analyst, overview }: Props) {
  if (!analyst) return null

  const hasTargets = analyst.target_low_price != null && analyst.target_high_price != null
  if (!hasTargets) return null

  const low = analyst.target_low_price!
  const high = analyst.target_high_price!
  const median = analyst.target_median_price
  const mean = analyst.target_mean_price
  const currentPrice = overview?.price ?? null
  const range = high - low

  // Clamp a value to [0, 100] percent of the low–high range
  const pctOf = (val: number): number =>
    range > 0 ? Math.max(0, Math.min(100, ((val - low) / range) * 100)) : 50

  return (
    <section id="section-targets" className="glass rounded-2xl shadow-glass p-6">
      <div className="flex items-center justify-between mb-5">
        <h3 className="text-xs font-sans font-medium text-terminal-dim tracking-wide">Price Targets</h3>
        <FreshnessTag fetchedAt={analyst.fetched_at} />
      </div>

      {/* 2x2 target cards grid */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-5">
        <TargetCard
          label="Target High"
          value={high}
          colorClass="text-terminal-green"
          bgClass="bg-terminal-green/10"
          borderClass="border-terminal-green/20"
        />
        <TargetCard
          label="Target Low"
          value={low}
          colorClass="text-terminal-red"
          bgClass="bg-terminal-red/10"
          borderClass="border-terminal-red/20"
        />
        <TargetCard
          label="Target Median"
          value={median}
          colorClass="text-terminal-text"
          bgClass="bg-white/[0.04]"
          borderClass="border-white/[0.08]"
          large
        />
        <TargetCard
          label="Consensus"
          value={mean}
          colorClass="text-indigo-400"
          bgClass="bg-indigo-500/10"
          borderClass="border-indigo-500/20"
        />
      </div>

      {/* Visual range bar */}
      <div className="mb-1">
        {/* Labels above bar: low and high anchors */}
        <div className="flex justify-between text-[9px] font-mono text-terminal-ghost mb-1">
          <span>${low.toFixed(0)}</span>
          <span className="text-terminal-ghost text-center flex-1 px-2 truncate">
            {currentPrice != null && (
              <span className="text-amber-400">Current ${currentPrice.toFixed(2)}</span>
            )}
          </span>
          <span>${high.toFixed(0)}</span>
        </div>

        {/* The bar itself */}
        <div className="relative h-3 bg-gradient-to-r from-terminal-red/30 via-white/[0.08] to-terminal-green/30 rounded-full">
          {/* Median marker — white dot */}
          {median != null && (
            <div
              className="absolute w-3 h-3 bg-white border-2 border-terminal-bg rounded-full top-0"
              style={{ left: `calc(${pctOf(median)}% - 6px)` }}
              title={`Median: $${median.toFixed(2)}`}
            />
          )}

          {/* Consensus marker — indigo dot */}
          {mean != null && (
            <div
              className="absolute w-3 h-3 bg-indigo-400 border-2 border-terminal-bg rounded-full top-0"
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
              <div className="w-2.5 h-5 bg-amber-400 rounded-sm border border-terminal-bg opacity-90" />
            </div>
          )}
        </div>
      </div>

      {/* Legend row */}
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 mt-3">
        {median != null && (
          <div className="flex items-center gap-1.5">
            <div className="w-2.5 h-2.5 rounded-full bg-white border border-terminal-bg shrink-0" />
            <span className="text-[9px] font-sans text-terminal-ghost">Median</span>
          </div>
        )}
        {mean != null && (
          <div className="flex items-center gap-1.5">
            <div className="w-2.5 h-2.5 rounded-full bg-indigo-400 border border-terminal-bg shrink-0" />
            <span className="text-[9px] font-sans text-terminal-ghost">Consensus</span>
          </div>
        )}
        {currentPrice != null && (
          <div className="flex items-center gap-1.5">
            <div className="w-2 h-3 rounded-sm bg-amber-400 shrink-0" />
            <span className="text-[9px] font-sans text-terminal-ghost">Current Price</span>
          </div>
        )}
        {analyst.num_analyst_opinions != null && (
          <span className="text-[9px] font-sans text-terminal-ghost ml-auto">
            {analyst.num_analyst_opinions} analyst{analyst.num_analyst_opinions !== 1 ? 's' : ''}
          </span>
        )}
      </div>
    </section>
  )
}

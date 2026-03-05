import clsx from 'clsx'
import type { StockAnalyst } from '@/types'
import FreshnessTag from './FreshnessTag'

function RatingLabel(mean: number): { text: string; color: string } {
  if (mean <= 1.5) return { text: 'Strong Buy', color: 'text-terminal-green' }
  if (mean <= 2.5) return { text: 'Buy', color: 'text-terminal-green' }
  if (mean <= 3.5) return { text: 'Hold', color: 'text-terminal-amber' }
  if (mean <= 4.5) return { text: 'Sell', color: 'text-terminal-red' }
  return { text: 'Strong Sell', color: 'text-terminal-red' }
}

interface Props { data: StockAnalyst | null; loading: boolean }

export default function AnalystSentimentModule({ data, loading }: Props) {
  if (!data && loading) {
    return (
      <section className="glass rounded-2xl shadow-glass p-6 animate-pulse">
        <div className="h-3 w-36 bg-terminal-muted rounded-xl mb-4" />
        <div className="h-10 w-full bg-terminal-muted rounded-xl mb-3" />
        <div className="h-3 w-48 bg-terminal-muted rounded-xl" />
      </section>
    )
  }
  if (!data) return null

  const hasRating = data.recommendation_mean != null
  const rating = hasRating ? RatingLabel(data.recommendation_mean!) : null
  const hasTargets = data.target_low_price != null && data.target_high_price != null

  // Target price range bar position
  let targetPct = 50
  if (hasTargets && data.target_mean_price != null) {
    const range = data.target_high_price! - data.target_low_price!
    if (range > 0) {
      targetPct = Math.max(0, Math.min(100, ((data.target_mean_price - data.target_low_price!) / range) * 100))
    }
  }

  return (
    <section id="section-analyst" className="glass rounded-2xl shadow-glass p-6">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-xs font-sans font-medium text-terminal-dim tracking-wide">Analyst Sentiment</h3>
        <FreshnessTag fetchedAt={data.fetched_at} />
      </div>

      {hasRating && rating && (
        <div className="flex items-center gap-4 mb-4">
          <div className="flex flex-col items-center">
            <span className="text-2xl font-mono font-bold tabular-nums text-terminal-text">
              {data.recommendation_mean!.toFixed(1)}
            </span>
            <span className="text-[9px] font-sans text-terminal-ghost">of 5</span>
          </div>
          <div className="flex-1">
            <span className={clsx('text-sm font-sans font-semibold', rating.color)}>
              {rating.text}
            </span>
            {data.recommendation_key && (
              <span className="text-[10px] font-sans text-terminal-ghost ml-2">
                ({data.recommendation_key})
              </span>
            )}
            {/* Gauge bar */}
            <div className="relative h-2 mt-1.5 bg-terminal-muted rounded-full overflow-hidden">
              <div
                className="absolute h-full bg-gradient-to-r from-emerald-500 via-amber-500 to-red-500 rounded-full"
                style={{ width: '100%' }}
              />
              <div
                className="absolute w-3 h-3 bg-terminal-text border-2 border-terminal-bg rounded-full -top-0.5"
                style={{ left: `calc(${((data.recommendation_mean! - 1) / 4) * 100}% - 6px)` }}
              />
            </div>
            <div className="flex justify-between text-[8px] font-sans text-terminal-ghost mt-0.5">
              <span>Strong Buy</span>
              <span>Strong Sell</span>
            </div>
          </div>
        </div>
      )}

      {hasTargets && (
        <div className="mb-3">
          <span className="text-[9px] font-sans text-terminal-ghost uppercase tracking-wide">Price Target Range</span>
          <div className="flex justify-between text-[10px] font-mono text-terminal-dim mt-1 mb-0.5">
            <span>${data.target_low_price!.toFixed(2)}</span>
            {data.target_median_price != null && (
              <span className="text-terminal-text font-semibold">
                Median: ${data.target_median_price.toFixed(2)}
              </span>
            )}
            <span>${data.target_high_price!.toFixed(2)}</span>
          </div>
          <div className="relative h-1.5 bg-terminal-muted rounded-full">
            <div className="absolute h-full bg-gradient-to-r from-terminal-red-dim to-terminal-green-dim rounded-full w-full" />
            {data.target_mean_price != null && (
              <div
                className="absolute w-2.5 h-2.5 bg-indigo-400 border border-terminal-bg rounded-full -top-0.5"
                style={{ left: `calc(${targetPct}% - 5px)` }}
                title={`Mean: $${data.target_mean_price.toFixed(2)}`}
              />
            )}
          </div>
        </div>
      )}

      {data.num_analyst_opinions != null && (
        <span className="text-[10px] font-sans text-terminal-ghost">
          Based on {data.num_analyst_opinions} analyst{data.num_analyst_opinions !== 1 ? 's' : ''}
        </span>
      )}
    </section>
  )
}

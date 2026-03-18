import type { BacktestMetrics } from '@/types'

interface Props {
  metrics: BacktestMetrics
}

interface MetricCardProps {
  label: string
  value: string | number
  subValue?: string
  accent: 'indigo' | 'green' | 'red' | 'amber' | 'neutral'
  large?: boolean
}

function MetricCard({ label, value, subValue, accent, large }: MetricCardProps) {
  const accentMap: Record<string, { bar: string; text: string; bg: string }> = {
    indigo:  { bar: 'bg-indigo-600',    text: 'text-indigo-600',    bg: 'bg-indigo-50' },
    green:   { bar: 'bg-green-600',     text: 'text-green-600',     bg: 'bg-green-50' },
    red:     { bar: 'bg-red-600',       text: 'text-red-600',       bg: 'bg-red-50' },
    amber:   { bar: 'bg-amber-600',     text: 'text-amber-600',     bg: 'bg-amber-50' },
    neutral: { bar: 'bg-gray-400',      text: 'text-gray-800',      bg: 'bg-gray-50' },
  }

  const colors = accentMap[accent] ?? accentMap.neutral

  return (
    <div className={`relative overflow-hidden rounded-xl border border-gray-200 ${colors.bg} p-3.5 flex flex-col gap-1.5`}>
      {/* Left accent bar */}
      <div className={`absolute left-0 top-0 bottom-0 w-0.5 ${colors.bar} rounded-l-xl`} />

      <span className="text-[10px] font-sans font-semibold uppercase tracking-widest text-gray-400 pl-1">
        {label}
      </span>
      <span className={`font-mono font-bold tabular-nums leading-none pl-1 ${colors.text} ${large ? 'text-2xl' : 'text-xl'}`}>
        {typeof value === 'number'
          ? value.toLocaleString('en-US', { maximumFractionDigits: 2 })
          : value}
      </span>
      {subValue && (
        <span className="text-[10px] font-mono text-gray-400 pl-1">{subValue}</span>
      )}
    </div>
  )
}

function sharpeAccent(v: number): 'green' | 'amber' | 'red' {
  if (v >= 1)   return 'green'
  if (v >= 0)   return 'amber'
  return 'red'
}

export function MetricsPanel({ metrics }: Props) {
  return (
    <div className="card rounded-2xl shadow-card p-5">
      {/* Header */}
      <div className="flex items-center gap-2 mb-4">
        <div className="w-7 h-7 rounded-lg bg-indigo-50 flex items-center justify-center">
          <svg className="w-3.5 h-3.5 text-indigo-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
              d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z"
            />
          </svg>
        </div>
        <h3 className="text-sm font-sans font-semibold text-gray-800">Performance Metrics</h3>
      </div>

      {/* Primary metrics — 3-column grid */}
      <div className="grid grid-cols-3 gap-2.5 mb-2.5">
        <MetricCard
          label="Total Return"
          value={`${metrics.total_return_pct >= 0 ? '+' : ''}${metrics.total_return_pct.toFixed(1)}%`}
          accent="indigo"
          large
        />
        <MetricCard
          label="CAGR"
          value={`${metrics.cagr >= 0 ? '+' : ''}${metrics.cagr.toFixed(1)}%`}
          accent={metrics.cagr > 0 ? 'green' : 'red'}
          large
        />
        <MetricCard
          label="Win Rate"
          value={`${metrics.win_rate.toFixed(1)}%`}
          accent={metrics.win_rate > 50 ? 'green' : 'red'}
          large
        />
      </div>

      {/* Secondary metrics — 3-column grid */}
      <div className="grid grid-cols-3 gap-2.5 mb-2.5">
        <MetricCard
          label="Sharpe Ratio"
          value={metrics.sharpe_ratio.toFixed(2)}
          subValue={metrics.sharpe_ratio >= 1 ? 'Strong' : metrics.sharpe_ratio >= 0 ? 'Acceptable' : 'Poor'}
          accent={sharpeAccent(metrics.sharpe_ratio)}
        />
        <MetricCard
          label="Sortino Ratio"
          value={metrics.sortino_ratio.toFixed(2)}
          accent={metrics.sortino_ratio > 1 ? 'green' : metrics.sortino_ratio > 0 ? 'amber' : 'red'}
        />
        <MetricCard
          label="Calmar Ratio"
          value={metrics.calmar_ratio.toFixed(2)}
          accent={metrics.calmar_ratio > 1 ? 'green' : metrics.calmar_ratio > 0 ? 'amber' : 'red'}
        />
      </div>

      {/* Drawdown + trade metrics — 3-column grid */}
      <div className="grid grid-cols-3 gap-2.5">
        <MetricCard
          label="Max Drawdown"
          value={`-${metrics.max_drawdown_pct.toFixed(1)}%`}
          accent="red"
        />
        <MetricCard
          label="Profit Factor"
          value={metrics.profit_factor >= 999 ? '\u221e' : metrics.profit_factor.toFixed(2)}
          accent={metrics.profit_factor > 1 ? 'green' : 'red'}
        />
        <MetricCard
          label="Trades"
          value={metrics.num_trades}
          subValue={`avg ${metrics.avg_trade_duration_days.toFixed(1)}d hold`}
          accent="neutral"
        />
      </div>

      {/* Compact stats footer */}
      <div className="mt-4 pt-3.5 border-t border-gray-200 grid grid-cols-2 gap-x-6 gap-y-1.5">
        <div className="flex items-center justify-between text-xs font-mono">
          <span className="text-gray-400">Avg Win</span>
          <span className="text-green-600 font-medium">+${metrics.avg_win.toFixed(2)}</span>
        </div>
        <div className="flex items-center justify-between text-xs font-mono">
          <span className="text-gray-400">Avg Loss</span>
          <span className="text-red-600 font-medium">-${Math.abs(metrics.avg_loss).toFixed(2)}</span>
        </div>
        <div className="flex items-center justify-between text-xs font-mono">
          <span className="text-gray-400">Win Streak</span>
          <span className="text-gray-500">{metrics.longest_win_streak}</span>
        </div>
        <div className="flex items-center justify-between text-xs font-mono">
          <span className="text-gray-400">Lose Streak</span>
          <span className="text-gray-500">{metrics.longest_lose_streak}</span>
        </div>
      </div>
    </div>
  )
}

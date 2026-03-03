import KPICard from '@/components/tradebot/KPICard'
import type { BacktestMetrics } from '@/types'

interface Props {
  metrics: BacktestMetrics
}

export function MetricsPanel({ metrics }: Props) {
  return (
    <div>
      <h3 className="text-sm font-semibold text-gray-200 mb-2">Performance Metrics</h3>

      {/* 3-column KPI grid */}
      <div className="grid grid-cols-3 gap-2">
        <KPICard
          label="Total Return"
          value={`${metrics.total_return_pct.toFixed(1)}%`}
          positive={metrics.total_return_pct > 0}
        />
        <KPICard
          label="CAGR"
          value={`${metrics.cagr.toFixed(1)}%`}
          positive={metrics.cagr > 0}
        />
        <KPICard
          label="Sharpe Ratio"
          value={metrics.sharpe_ratio.toFixed(2)}
          positive={metrics.sharpe_ratio > 1}
        />
        <KPICard
          label="Sortino Ratio"
          value={metrics.sortino_ratio.toFixed(2)}
          positive={metrics.sortino_ratio > 1}
        />
        <KPICard
          label="Calmar Ratio"
          value={metrics.calmar_ratio.toFixed(2)}
          positive={metrics.calmar_ratio > 1}
        />
        <KPICard
          label="Max Drawdown"
          value={`-${metrics.max_drawdown_pct.toFixed(1)}%`}
          positive={false}
        />
        <KPICard
          label="Win Rate"
          value={`${metrics.win_rate.toFixed(1)}%`}
          positive={metrics.win_rate > 50}
        />
        <KPICard
          label="Profit Factor"
          value={metrics.profit_factor >= 999 ? '∞' : metrics.profit_factor.toFixed(2)}
          positive={metrics.profit_factor > 1}
        />
        <KPICard
          label="Trades"
          value={metrics.num_trades}
        />
      </div>

      {/* Compact stats row */}
      <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs font-mono text-gray-400">
        <span>
          Avg Win: <span className="text-green-400">${metrics.avg_win.toFixed(2)}</span>
        </span>
        <span>
          Avg Loss: <span className="text-red-400">${metrics.avg_loss.toFixed(2)}</span>
        </span>
        <span>Win Streak: {metrics.longest_win_streak}</span>
        <span>Lose Streak: {metrics.longest_lose_streak}</span>
        <span>Avg Duration: {metrics.avg_trade_duration_days.toFixed(1)}d</span>
      </div>
    </div>
  )
}

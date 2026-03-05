import KPICard from '@/components/tradebot/KPICard'
import type { BacktestMetrics } from '@/types'

interface Props {
  metrics: BacktestMetrics
}

export function MetricsPanel({ metrics }: Props) {
  return (
    <div className="glass rounded-2xl shadow-glass p-5">
      <h3 className="text-sm font-sans font-medium text-terminal-dim mb-4">Performance Metrics</h3>

      {/* 3-column KPI grid */}
      <div className="grid grid-cols-3 gap-3">
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
          value={metrics.profit_factor >= 999 ? '\u221e' : metrics.profit_factor.toFixed(2)}
          positive={metrics.profit_factor > 1}
        />
        <KPICard
          label="Trades"
          value={metrics.num_trades}
        />
      </div>

      {/* Compact stats row */}
      <div className="mt-4 pt-4 border-t border-white/[0.06] flex flex-wrap gap-x-5 gap-y-1.5 text-xs font-mono text-terminal-dim">
        <span>
          Avg Win: <span className="text-terminal-green">${metrics.avg_win.toFixed(2)}</span>
        </span>
        <span>
          Avg Loss: <span className="text-terminal-red">${metrics.avg_loss.toFixed(2)}</span>
        </span>
        <span>Win Streak: {metrics.longest_win_streak}</span>
        <span>Lose Streak: {metrics.longest_lose_streak}</span>
        <span>Avg Duration: {metrics.avg_trade_duration_days.toFixed(1)}d</span>
      </div>
    </div>
  )
}

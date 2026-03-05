import clsx from 'clsx'
import type { StockFinancials } from '@/types'
import FreshnessTag from './FreshnessTag'

function fmtCompact(v: number | null): string {
  if (v == null) return '—'
  const abs = Math.abs(v)
  const sign = v < 0 ? '-' : ''
  if (abs >= 1e12) return `${sign}$${(abs / 1e12).toFixed(2)}T`
  if (abs >= 1e9) return `${sign}$${(abs / 1e9).toFixed(1)}B`
  if (abs >= 1e6) return `${sign}$${(abs / 1e6).toFixed(1)}M`
  return `${sign}$${abs.toLocaleString()}`
}

function fmtPct(v: number | null): string {
  if (v == null) return '—'
  return `${(v * 100).toFixed(1)}%`
}

function MetricRow({ label, value, highlight }: { label: string; value: string; highlight?: boolean }) {
  return (
    <div className="flex justify-between items-center py-1.5 border-b border-white/[0.06] last:border-0">
      <span className="text-[11px] font-sans text-terminal-dim">{label}</span>
      <span className={clsx('text-[11px] font-mono tabular-nums', highlight ? 'text-terminal-text font-semibold' : 'text-terminal-text')}>
        {value}
      </span>
    </div>
  )
}

function QuarterlyBars({ data, label }: { data: { period: string; value: number }[]; label: string }) {
  if (!data.length) return null
  const maxVal = Math.max(...data.map((d) => Math.abs(d.value)))

  return (
    <div className="mt-3">
      <span className="text-[9px] font-sans text-terminal-ghost uppercase tracking-wide">{label}</span>
      <div className="flex items-end gap-1 mt-1.5 h-16">
        {data.slice(-6).map((d) => {
          const pct = maxVal > 0 ? (Math.abs(d.value) / maxVal) * 100 : 0
          const positive = d.value >= 0
          return (
            <div key={d.period} className="flex-1 flex flex-col items-center gap-0.5">
              <div className="w-full flex items-end justify-center" style={{ height: '48px' }}>
                <div
                  className={clsx(
                    'w-full max-w-[24px] rounded-t',
                    positive ? 'bg-terminal-green/40' : 'bg-terminal-red/40',
                  )}
                  style={{ height: `${Math.max(pct, 4)}%` }}
                  title={`${d.period}: ${fmtCompact(d.value)}`}
                />
              </div>
              <span className="text-[8px] font-mono text-terminal-ghost truncate w-full text-center">
                {d.period.slice(-5)}
              </span>
            </div>
          )
        })}
      </div>
    </div>
  )
}

interface Props { data: StockFinancials | null; loading: boolean }

export default function FinancialHealthModule({ data, loading }: Props) {
  if (!data && loading) {
    return (
      <section className="glass rounded-2xl shadow-glass p-6 animate-pulse">
        <div className="h-3 w-32 bg-terminal-muted rounded-xl mb-4" />
        {Array.from({ length: 5 }).map((_, i) => (
          <div key={i} className="flex justify-between py-2">
            <div className="h-3 w-24 bg-terminal-muted rounded-xl" />
            <div className="h-3 w-16 bg-terminal-muted rounded-xl" />
          </div>
        ))}
      </section>
    )
  }
  if (!data) return null

  return (
    <section id="section-financials" className="glass rounded-2xl shadow-glass p-6">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-xs font-sans font-medium text-terminal-dim tracking-wide">Financial Health</h3>
        <FreshnessTag fetchedAt={data.fetched_at} />
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-x-6">
        <div>
          <MetricRow label="Total Revenue" value={fmtCompact(data.total_revenue)} highlight />
          <MetricRow label="Revenue Growth" value={fmtPct(data.revenue_growth)} />
          <MetricRow label="Net Income" value={fmtCompact(data.net_income)} />
          <MetricRow label="Profit Margin" value={fmtPct(data.profit_margins)} />
        </div>
        <div>
          <MetricRow label="Operating Margin" value={fmtPct(data.operating_margins)} />
          <MetricRow label="Gross Margin" value={fmtPct(data.gross_margins)} />
          <MetricRow label="Debt / Equity" value={data.debt_to_equity != null ? data.debt_to_equity.toFixed(0) : '—'} />
          <MetricRow label="Current Ratio" value={data.current_ratio != null ? data.current_ratio.toFixed(2) : '—'} />
        </div>
      </div>

      {data.quarterly_revenue && <QuarterlyBars data={data.quarterly_revenue} label="Quarterly Revenue" />}
      {data.quarterly_net_income && <QuarterlyBars data={data.quarterly_net_income} label="Quarterly Net Income" />}
    </section>
  )
}

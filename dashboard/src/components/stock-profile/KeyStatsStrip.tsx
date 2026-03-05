import type { StockKeyStats, StockCompanyInfo } from '@/types'
import FreshnessTag from './FreshnessTag'

function fmtCompact(v: number | null): string {
  if (v == null) return '—'
  if (v >= 1e12) return `$${(v / 1e12).toFixed(2)}T`
  if (v >= 1e9) return `$${(v / 1e9).toFixed(1)}B`
  if (v >= 1e6) return `$${(v / 1e6).toFixed(1)}M`
  return `$${v.toLocaleString()}`
}

function fmtNum(v: number | null, decimals = 2): string {
  if (v == null) return '—'
  return v.toFixed(decimals)
}

function fmtPct(v: number | null): string {
  if (v == null) return '—'
  return `${(v * 100).toFixed(2)}%`
}

function fmtVol(v: number | null): string {
  if (v == null) return '—'
  if (v >= 1e9) return `${(v / 1e9).toFixed(1)}B`
  if (v >= 1e6) return `${(v / 1e6).toFixed(1)}M`
  if (v >= 1e3) return `${(v / 1e3).toFixed(0)}K`
  return v.toLocaleString()
}

function fmtShares(v: number | null): string {
  if (v == null) return '—'
  if (v >= 1e9) return `${(v / 1e9).toFixed(2)}B`
  if (v >= 1e6) return `${(v / 1e6).toFixed(1)}M`
  return v.toLocaleString()
}

function Stat({ label, value, highlight }: { label: string; value: string; highlight?: boolean }) {
  return (
    <div className="flex flex-col gap-1 p-2 rounded-xl bg-white/[0.02] border border-white/[0.04]">
      <span className="text-[9px] font-sans text-terminal-ghost uppercase tracking-wide">{label}</span>
      <span className={`text-xs font-mono tabular-nums ${highlight ? 'text-indigo-400 font-semibold' : 'text-terminal-text'}`}>
        {value}
      </span>
    </div>
  )
}

interface Props {
  data: StockKeyStats | null
  companyInfo: StockCompanyInfo | null
  loading: boolean
}

export default function KeyStatsStrip({ data, companyInfo, loading }: Props) {
  if (!data && loading) {
    return (
      <section className="glass rounded-2xl shadow-glass p-6 animate-pulse">
        <div className="grid grid-cols-3 md:grid-cols-6 gap-2">
          {Array.from({ length: 6 }).map((_, i) => (
            <div key={i} className="h-12 bg-terminal-muted rounded-xl" />
          ))}
        </div>
      </section>
    )
  }
  if (!data) return null

  return (
    <section id="section-stats" className="glass rounded-2xl shadow-glass p-6">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-xs font-sans font-medium text-terminal-dim tracking-wide">Key Statistics</h3>
        <FreshnessTag fetchedAt={data.fetched_at} />
      </div>
      <div className="grid grid-cols-3 md:grid-cols-6 gap-2">
        <Stat label="Market Cap" value={fmtCompact(data.market_cap)} highlight />
        <Stat label="52W High" value={data.fifty_two_week_high != null ? `$${fmtNum(data.fifty_two_week_high)}` : '—'} />
        <Stat label="52W Low" value={data.fifty_two_week_low != null ? `$${fmtNum(data.fifty_two_week_low)}` : '—'} />
        <Stat label="P/E Ratio" value={fmtNum(data.trailing_pe)} />
        <Stat label="Volume" value={fmtVol(data.volume)} />
        <Stat label="Shares Out" value={fmtShares(companyInfo?.shares_outstanding ?? null)} />
      </div>

      <div className="grid grid-cols-3 md:grid-cols-6 gap-2 mt-2">
        <Stat label="EPS" value={data.trailing_eps != null ? `$${fmtNum(data.trailing_eps)}` : '—'} />
        <Stat label="Div Yield" value={fmtPct(data.dividend_yield)} />
        <Stat label="Beta" value={fmtNum(data.beta)} />
        {data.fifty_day_ma != null && <Stat label="50D MA" value={`$${fmtNum(data.fifty_day_ma)}`} />}
        {data.two_hundred_day_ma != null && <Stat label="200D MA" value={`$${fmtNum(data.two_hundred_day_ma)}`} />}
        {data.forward_pe != null && <Stat label="Fwd P/E" value={fmtNum(data.forward_pe)} />}
      </div>
    </section>
  )
}

import type { StockKeyStats, StockCompanyInfo } from '@/types'
import FreshnessTag from './FreshnessTag'

// ── Formatters ────────────────────────────────────────────────────────────────

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

// ── Base stat card ────────────────────────────────────────────────────────────

function Stat({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-1.5 p-3 rounded-xl bg-gray-50 border border-gray-200 hover:bg-gray-50/70 transition-colors">
      <span className="text-[9px] font-sans text-gray-400 uppercase tracking-wide">
        {label}
      </span>
      {children}
    </div>
  )
}

// ── Market Cap card (prominent) ───────────────────────────────────────────────

function MarketCapCard({ value }: { value: number | null }) {
  return (
    <div className="flex flex-col gap-1.5 p-3 rounded-xl bg-gray-100 border border-gray-200 hover:bg-gray-50 transition-colors">
      <span className="text-[9px] font-sans text-gray-400 uppercase tracking-wide">
        Market Cap
      </span>
      <span className="text-sm font-mono tabular-nums text-gray-900 font-semibold">
        {fmtCompact(value)}
      </span>
    </div>
  )
}

// ── 52-Week Range card ────────────────────────────────────────────────────────

function WeekRangeCard({
  low,
  high,
  currentPrice,
}: {
  low: number | null
  high: number | null
  currentPrice?: number | null
}) {
  const lowStr = low != null ? `$${fmtNum(low)}` : '—'
  const highStr = high != null ? `$${fmtNum(high)}` : '—'

  // Progress: 0–1 representing where currentPrice sits in the [low, high] range
  let progress: number | null = null
  if (currentPrice != null && low != null && high != null && high > low) {
    progress = Math.min(1, Math.max(0, (currentPrice - low) / (high - low)))
  }

  return (
    <div className="flex flex-col gap-1.5 p-3 rounded-xl bg-gray-50 border border-gray-200 hover:bg-gray-50/70 transition-colors">
      <span className="text-[9px] font-sans text-gray-400 uppercase tracking-wide">
        52-Week Range
      </span>
      <span className="text-xs font-mono tabular-nums text-gray-800">
        {lowStr} — {highStr}
      </span>
      {/* Range bar */}
      <div className="h-1 rounded-full bg-gray-200 overflow-hidden mt-0.5">
        {progress != null ? (
          <div
            className="h-full rounded-full"
            style={{
              width: `${progress * 100}%`,
              background: 'linear-gradient(to right, #ef4444, #22c55e)',
            }}
          />
        ) : (
          <div className="h-full w-0" />
        )}
      </div>
    </div>
  )
}

// ── Volume card (with avg ratio color-coding) ─────────────────────────────────

function VolumeCard({
  volume,
  avgVolume,
}: {
  volume: number | null
  avgVolume: number | null
}) {
  let valueColor = 'text-gray-800'
  let ratioLabel: string | null = null

  if (volume != null && avgVolume != null && avgVolume > 0) {
    const ratio = volume / avgVolume
    if (ratio >= 1.1) {
      valueColor = 'text-green-600'
      ratioLabel = `${ratio.toFixed(1)}x avg`
    } else if (ratio >= 0.8) {
      valueColor = 'text-amber-600'
      ratioLabel = `${ratio.toFixed(1)}x avg`
    } else {
      valueColor = 'text-red-600'
      ratioLabel = `${ratio.toFixed(1)}x avg`
    }
  }

  return (
    <div className="flex flex-col gap-1.5 p-3 rounded-xl bg-gray-50 border border-gray-200 hover:bg-gray-50/70 transition-colors">
      <span className="text-[9px] font-sans text-gray-400 uppercase tracking-wide">
        Volume
      </span>
      <span className={`text-xs font-mono tabular-nums ${valueColor}`}>
        {fmtVol(volume)}
      </span>
      {ratioLabel != null && (
        <span className={`text-[9px] font-sans ${valueColor} opacity-75`}>
          {ratioLabel}
        </span>
      )}
    </div>
  )
}

// ── Props ─────────────────────────────────────────────────────────────────────

interface Props {
  data: StockKeyStats | null
  companyInfo: StockCompanyInfo | null
  loading: boolean
  currentPrice?: number | null
}

// ── Component ─────────────────────────────────────────────────────────────────

export default function KeyStatsStrip({ data, companyInfo, loading, currentPrice }: Props) {
  if (!data && loading) {
    return (
      <section className="card rounded-lg shadow-card p-6 animate-pulse">
        <div className="grid grid-cols-3 md:grid-cols-4 gap-2">
          {Array.from({ length: 8 }).map((_, i) => (
            <div key={i} className="h-14 bg-gray-100 rounded-xl" />
          ))}
        </div>
      </section>
    )
  }
  if (!data) return null

  return (
    <section id="section-stats" className="card rounded-lg shadow-card p-6">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-xs font-sans font-medium text-gray-500 tracking-wide">
          Key Statistics
        </h3>
        <FreshnessTag fetchedAt={data.fetched_at} />
      </div>

      {/* Row 1: Market Cap, 52W Range, P/E, Volume */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
        <MarketCapCard value={data.market_cap} />
        <WeekRangeCard
          low={data.fifty_two_week_low}
          high={data.fifty_two_week_high}
          currentPrice={currentPrice}
        />
        <Stat label="P/E Ratio">
          <span className="text-xs font-mono tabular-nums text-gray-800">
            {fmtNum(data.trailing_pe)}
          </span>
        </Stat>
        <VolumeCard volume={data.volume} avgVolume={data.avg_volume} />
      </div>

      {/* Row 2: EPS, Div Yield, Beta, Shares Out + optional MAs / Fwd P/E */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-2 mt-2">
        <Stat label="EPS">
          <span className="text-xs font-mono tabular-nums text-gray-800">
            {data.trailing_eps != null ? `$${fmtNum(data.trailing_eps)}` : '—'}
          </span>
        </Stat>
        <Stat label="Div Yield">
          <span className="text-xs font-mono tabular-nums text-gray-800">
            {fmtPct(data.dividend_yield)}
          </span>
        </Stat>
        <Stat label="Beta">
          <span className="text-xs font-mono tabular-nums text-gray-800">
            {fmtNum(data.beta)}
          </span>
        </Stat>
        <Stat label="Shares Out">
          <span className="text-xs font-mono tabular-nums text-gray-800">
            {fmtShares(companyInfo?.shares_outstanding ?? null)}
          </span>
        </Stat>
        {data.fifty_day_ma != null && (
          <Stat label="50D MA">
            <span className="text-xs font-mono tabular-nums text-gray-800">
              {`$${fmtNum(data.fifty_day_ma)}`}
            </span>
          </Stat>
        )}
        {data.two_hundred_day_ma != null && (
          <Stat label="200D MA">
            <span className="text-xs font-mono tabular-nums text-gray-800">
              {`$${fmtNum(data.two_hundred_day_ma)}`}
            </span>
          </Stat>
        )}
        {data.forward_pe != null && (
          <Stat label="Fwd P/E">
            <span className="text-xs font-mono tabular-nums text-gray-800">
              {fmtNum(data.forward_pe)}
            </span>
          </Stat>
        )}
      </div>
    </section>
  )
}

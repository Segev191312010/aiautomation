import { formatAge, formatNyTime, formatPercent, snapshotAgeTone } from '../format'
import type { MarketContextView, MarketRegime, MarketSectorChange } from '../types'

type MarketRegimeBadgeProps = {
  market: MarketContextView | null
  /** Context age now: the server's age plus the time since the response arrived. */
  ageSeconds: number | null
}

const REGIMES: Record<MarketRegime, { label: string; tone: string }> = {
  risk_on: { label: 'Risk-on', tone: 'risk-on' },
  neutral: { label: 'Neutral', tone: 'neutral' },
  risk_off: { label: 'Risk-off', tone: 'risk-off' },
}

/** The summary without its leading regime label, which the badge already shows. */
export function summaryDetail(summary: string, label: string): string {
  const prefix = `${label} · `
  return summary.toLowerCase().startsWith(prefix.toLowerCase()) ? summary.slice(prefix.length) : summary
}

function sectors(entries: readonly MarketSectorChange[]): string {
  return entries.length ? entries.map((entry) => `${entry.sector} ${formatPercent(entry.change_pct)}`).join(' · ') : '—'
}

function details(market: Extract<MarketContextView, { available: true }>): string {
  const indexes = market.indexes.map((index) => `${index.symbol} ${formatPercent(index.change_pct)}`).join(' · ')
  const breadth =
    market.breadth_up_pct === null
      ? 'n/a'
      : `${formatPercent(market.breadth_up_pct).replace('+', '')} up (${market.advancers}↑/${market.decliners}↓)`
  return [
    market.summary,
    `Indexes: ${indexes}`,
    `Breadth: ${breadth} · median ${formatPercent(market.median_change_pct)}`,
    `Leaders: ${sectors(market.sector_leaders)}`,
    `Laggards: ${sectors(market.sector_laggards)}`,
    `As of ${formatNyTime(market.observed_at)} ET`,
  ].join('\n')
}

/** Header badge: today's market regime (green risk-on, grey neutral, red risk-off) and its summary. */
export function MarketRegimeBadge({ market, ageSeconds }: MarketRegimeBadgeProps) {
  if (market === null || !market.available) {
    return (
      <div className="status-item regime" aria-label="Market regime">
        <span className="badge regime-badge regime-unknown">No regime</span>
        <small className="regime-summary">{market === null ? 'loading…' : 'no market context yet today'}</small>
      </div>
    )
  }
  const regime = REGIMES[market.regime]
  const stale = snapshotAgeTone(ageSeconds) === 'stale'
  return (
    <div className={`status-item regime${stale ? ' stale' : ''}`} aria-label="Market regime" title={details(market)}>
      <span className={`badge regime-badge regime-${regime.tone}`}>{regime.label}</span>
      <small className="regime-summary">
        {summaryDetail(market.summary, regime.label)}
        {stale ? ` · stale (${formatAge(ageSeconds)} old)` : ''}
      </small>
    </div>
  )
}

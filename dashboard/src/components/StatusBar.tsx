import type { Slice } from '../hooks/useLiveData'
import { useNow } from '../hooks/useNow'
import {
  directionClass,
  formatAge,
  formatNyClock,
  formatNyDate,
  formatNyDayClock,
  formatNyTime,
  formatSignedCurrency,
  parseTime,
  snapshotAgeTone,
} from '../format'
import type { LiveStatus, MarketContextView, MarketStatus } from '../types'
import { MarketRegimeBadge } from './MarketRegime'

type StatusBarProps = {
  status: Slice<LiveStatus>
  market: Slice<MarketContextView>
  lastPollAt: number | null
  paused: boolean
  problems: string[]
  onLock: () => void
}

type MarketDescription = { label: string; detail: string; tone: 'open' | 'closed' | 'unknown' }

export function describeMarket(market: MarketStatus | undefined, now: Date): MarketDescription {
  if (!market) return { label: 'Market', detail: 'loading…', tone: 'unknown' }
  switch (market.phase) {
    case 'open': {
      const cutoff = parseTime(market.entry_cutoff_at)
      const entries = cutoff && now < cutoff ? ` · entry cutoff ${formatNyClock(cutoff)}` : ' · past entry cutoff'
      return { label: 'Market open', detail: `closes ${formatNyClock(market.closes_at)} ET${entries}`, tone: 'open' }
    }
    case 'pre_open':
      return { label: 'Pre-market', detail: `opens ${formatNyClock(market.opens_at)} ET`, tone: 'closed' }
    case 'after_close':
      return { label: 'Market closed', detail: `next open ${formatNyDayClock(market.next_open_at)} ET`, tone: 'closed' }
    case 'no_session':
      return { label: 'No session today', detail: `next open ${formatNyDayClock(market.next_open_at)} ET`, tone: 'closed' }
    default:
      return { label: 'Market hours unknown', detail: 'exchange calendar unavailable', tone: 'unknown' }
  }
}

function sumOutcomes(counts: Record<string, number> | undefined, outcomes: readonly string[]): number {
  if (!counts) return 0
  return outcomes.reduce((total, outcome) => total + (Number(counts[outcome]) || 0), 0)
}

function ageNow(serverAge: number | null | undefined, receivedAt: number | null, now: number): number | null {
  if (typeof serverAge !== 'number') return null
  return serverAge + (receivedAt === null ? 0 : Math.max(0, (now - receivedAt) / 1000))
}

/** Header strip: market session and regime, New York clock, snapshot freshness, today's counts. */
export function StatusBar({ status, market: context, lastPollAt, paused, problems, onLock }: StatusBarProps) {
  const now = useNow(1000)
  const clock = new Date(now)
  const data = status.data
  const market = describeMarket(data?.market, clock)
  const snapshotAge = ageNow(data?.snapshot_age_seconds, status.receivedAt, now)
  const regime = context.data
  const regimeAge = regime?.available ? ageNow(regime.age_seconds, context.receivedAt, now) : null
  const ageTone = snapshotAgeTone(snapshotAge)
  const counts = data?.decisions_today
  const wouldBuy = sumOutcomes(counts, ['shadow_accepted', 'approved', 'submitted'])
  const claudeSkips = sumOutcomes(counts, ['ai_abstained', 'ai_blocked'])
  const riskBlocks = sumOutcomes(counts, ['risk_blocked'])
  const sinceUpdate = lastPollAt === null ? null : Math.max(0, (now - lastPollAt) / 1000)

  return (
    <header className="status-bar">
      <div className="brand">
        <strong>Autotrader</strong>
        <span className="pill" title="This page only reads data; it has no trading controls.">
          PAPER · read-only
        </span>
      </div>

      <div className={`status-item market market-${market.tone}`} aria-label="Market session">
        <span className="dot" aria-hidden="true" />
        <div>
          <strong>{market.label}</strong>
          <small>{market.detail}</small>
        </div>
      </div>

      <MarketRegimeBadge market={regime} ageSeconds={regimeAge} />

      <div className="status-item" aria-label="New York time">
        <span className="label">New York</span>
        <strong className="mono">{formatNyTime(clock)}</strong>
        <small>{formatNyDate(clock)}</small>
      </div>

      <div className={`status-item age age-${ageTone}`} aria-label="Screener snapshot age">
        <span className="label">Snapshot</span>
        <strong className="mono">{snapshotAge === null ? 'none yet' : `${formatAge(snapshotAge)} ago`}</strong>
        <small>{ageTone === 'stale' ? 'stale' : ageTone === 'aging' ? 'aging' : ageTone === 'fresh' ? 'live' : 'waiting'}</small>
      </div>

      <dl className="status-counts" aria-label="Today's activity">
        <div>
          <dt>Signals</dt>
          <dd>{data ? data.decisions_total : '—'}</dd>
        </div>
        <div className="take">
          <dt>Would buy</dt>
          <dd>{data ? wouldBuy : '—'}</dd>
        </div>
        <div>
          <dt>Claude skips</dt>
          <dd>{data ? claudeSkips : '—'}</dd>
        </div>
        <div>
          <dt>Risk blocks</dt>
          <dd>{data ? riskBlocks : '—'}</dd>
        </div>
        <div className="take">
          <dt>Orders sent</dt>
          <dd>{data ? data.orders_today.sent : '—'}</dd>
        </div>
        <div>
          <dt>Open</dt>
          <dd>{data ? data.orders_today.open : '—'}</dd>
        </div>
        <div className={directionClass(data?.orders_today.realized_pnl)} title="Realized P&L the PAPER bridge computed from broker fills">
          <dt>Realized P&amp;L</dt>
          <dd>{data ? formatSignedCurrency(data.orders_today.realized_pnl) : '—'}</dd>
        </div>
        {data && data.orders_today.needs_attention > 0 ? (
          <div className="attention" role="alert">
            <dt>Needs attention</dt>
            <dd>{data.orders_today.needs_attention}</dd>
          </div>
        ) : null}
        {data?.bridge_state === 'unreadable' ? (
          <div className="attention">
            <dt>Bridge state</dt>
            <dd>unreadable</dd>
          </div>
        ) : null}
      </dl>

      <div className="status-item poll">
        {problems.length > 0 ? (
          <span className="inline-error" role="alert" title={problems.join('\n')}>
            {problems[0]}
            {problems.length > 1 ? ` (+${problems.length - 1} more)` : ''}
          </span>
        ) : (
          <small>
            {paused
              ? 'Paused while tab is hidden'
              : sinceUpdate === null
                ? 'Connecting…'
                : `Updated ${formatAge(sinceUpdate)} ago`}
          </small>
        )}
        <button type="button" className="ghost-button" onClick={onLock}>
          Lock
        </button>
      </div>
    </header>
  )
}

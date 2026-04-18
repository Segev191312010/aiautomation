/**
 * PnLSummary — P&L overview KPI cards for the Analytics page.
 * Displays realized/unrealized P&L, today's P&L, win rate,
 * profit factor, and best/worst trade.
 */
import React from 'react'
import clsx from 'clsx'
import { fmtUSD } from '@/utils/formatters'
import type { PnLSummary as PnLSummaryType } from '@/types'

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmtPct(v: number, decimals = 2): string {
  return (v >= 0 ? '+' : '') + v.toFixed(decimals) + '%'
}

// ── Skeleton ──────────────────────────────────────────────────────────────────

function CardSkeleton() {
  return (
    <div className="card rounded-2xl  p-5 flex flex-col gap-3 animate-pulse">
      <div className="flex items-center gap-2">
        <div className="w-6 h-6 rounded-lg bg-zinc-800" />
        <div className="h-2.5 w-28 rounded bg-zinc-800" />
      </div>
      <div className="h-7 w-32 rounded-xl bg-zinc-800" />
      <div className="h-3 w-20 rounded bg-zinc-800" />
    </div>
  )
}

// ── Single KPI card ───────────────────────────────────────────────────────────

interface CardProps {
  label:     string
  value:     string
  subValue?: string
  positive?: boolean   // true→green, false→red, undefined→neutral
  accent?:   'indigo' | 'amber'
  icon:      React.ReactNode
}

function MetricCard({ label, value, subValue, positive, accent, icon }: CardProps) {
  const leftBorder =
    accent === 'indigo' ? 'border-l-indigo-600/60' :
    accent === 'amber'  ? 'border-l-amber-500/60'  :
    positive === true   ? 'border-l-emerald-500/60' :
    positive === false  ? 'border-l-red-500/60'     :
    'border-l-zinc-800'

  const iconBg =
    accent === 'indigo' ? 'bg-indigo-50'           :
    accent === 'amber'  ? 'bg-amber-500/15'         :
    positive === true   ? 'bg-emerald-500/15'       :
    positive === false  ? 'bg-red-500/15'           :
    'bg-zinc-800/60'

  const iconColor =
    accent === 'indigo' ? 'text-indigo-600'   :
    accent === 'amber'  ? 'text-amber-600'    :
    positive === true   ? 'text-emerald-500'  :
    positive === false  ? 'text-red-400'      :
    'text-zinc-500'

  const valueColor =
    positive === true  ? 'text-emerald-400'  :
    positive === false ? 'text-red-400'    :
    accent === 'indigo'? 'text-indigo-600' :
    accent === 'amber' ? 'text-amber-600'  :
    'text-zinc-100'

  return (
    <div
      className={clsx(
        'card rounded-2xl  p-5 flex flex-col gap-2',
        'border-l-2', leftBorder,
        'hover:bg-zinc-800/40 transition-colors duration-150 cursor-default',
      )}
    >
      <div className="flex items-center gap-2">
        <div className={clsx('w-6 h-6 rounded-lg flex items-center justify-center flex-shrink-0', iconBg)}>
          <span className={clsx('w-3.5 h-3.5', iconColor)}>{icon}</span>
        </div>
        <span className="text-[10px] font-sans font-medium text-zinc-500 uppercase tracking-widest truncate">
          {label}
        </span>
      </div>
      <span className={clsx('text-xl font-mono font-bold tabular-nums leading-none', valueColor)}>
        {value}
      </span>
      {subValue && (
        <span className={clsx('text-[11px] font-mono tabular-nums', valueColor, 'opacity-70')}>
          {subValue}
        </span>
      )}
    </div>
  )
}

// ── Icons ─────────────────────────────────────────────────────────────────────

const IconDollar = (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.75} strokeLinecap="round" strokeLinejoin="round" className="w-full h-full">
    <line x1="12" y1="1" x2="12" y2="23" />
    <path d="M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6" />
  </svg>
)

const IconTrendUp = (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.75} strokeLinecap="round" strokeLinejoin="round" className="w-full h-full">
    <polyline points="23 6 13.5 15.5 8.5 10.5 1 18" />
    <polyline points="17 6 23 6 23 12" />
  </svg>
)

const IconStar = (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.75} strokeLinecap="round" strokeLinejoin="round" className="w-full h-full">
    <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2" />
  </svg>
)

const IconTarget = (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.75} strokeLinecap="round" strokeLinejoin="round" className="w-full h-full">
    <circle cx="12" cy="12" r="10" />
    <circle cx="12" cy="12" r="6" />
    <circle cx="12" cy="12" r="2" />
  </svg>
)

const IconCalendar = (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.75} strokeLinecap="round" strokeLinejoin="round" className="w-full h-full">
    <rect x="3" y="4" width="18" height="18" rx="2" ry="2" />
    <line x1="16" y1="2" x2="16" y2="6" />
    <line x1="8" y1="2" x2="8" y2="6" />
    <line x1="3" y1="10" x2="21" y2="10" />
  </svg>
)

const IconThumbUp = (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.75} strokeLinecap="round" strokeLinejoin="round" className="w-full h-full">
    <path d="M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.28a2 2 0 0 0 2-1.7l1.38-9a2 2 0 0 0-2-2.3H14z" />
    <path d="M7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3" />
  </svg>
)

const IconThumbDown = (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.75} strokeLinecap="round" strokeLinejoin="round" className="w-full h-full">
    <path d="M10 15v4a3 3 0 0 0 3 3l4-9V2H5.72a2 2 0 0 0-2 1.7l-1.38 9a2 2 0 0 0 2 2.3H10z" />
    <path d="M17 2h2.67A2.31 2.31 0 0 1 22 4v7a2.31 2.31 0 0 1-2.33 2H17" />
  </svg>
)

// ── Main component ────────────────────────────────────────────────────────────

interface Props {
  data:    PnLSummaryType | null
  loading: boolean
}

export default function PnLSummary({ data, loading }: Props) {
  if (loading && !data) {
    return (
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-4 gap-3">
        {Array.from({ length: 8 }).map((_, i) => <CardSkeleton key={i} />)}
      </div>
    )
  }

  if (!data) {
    return (
      <div className="card rounded-2xl  p-8 text-center text-zinc-500 text-sm font-sans">
        No P&L data available. Connect a broker or run some trades.
      </div>
    )
  }

  const isRealizedPos  = data.realized_pnl  >= 0
  const isUnrealizedPos = data.unrealized_pnl >= 0
  const isTodayPos     = data.today_pnl     >= 0
  const isBestPos      = data.best_trade_pnl >= 0
  const isWorstPos     = data.worst_trade_pnl >= 0
  const profitFactor   = Number.isFinite(data.profit_factor) ? data.profit_factor.toFixed(2) : '∞'

  return (
    <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3">
      {/* Realized P&L */}
      <MetricCard
        label="Realized P&L"
        value={fmtUSD(data.realized_pnl)}
        subValue={fmtPct(data.realized_pnl_pct)}
        positive={isRealizedPos}
        icon={IconDollar}
      />

      {/* Unrealized P&L */}
      <MetricCard
        label="Unrealized P&L"
        value={fmtUSD(data.unrealized_pnl)}
        positive={isUnrealizedPos}
        icon={IconTrendUp}
      />

      {/* Today's P&L */}
      <MetricCard
        label="Today's P&L"
        value={fmtUSD(data.today_pnl)}
        subValue={fmtPct(data.today_pnl_pct)}
        positive={isTodayPos}
        icon={IconCalendar}
      />

      {/* Win Rate */}
      <MetricCard
        label="Win Rate"
        value={`${(data.win_rate * 100).toFixed(1)}%`}
        subValue={`${data.total_trades} trades`}
        accent="indigo"
        icon={IconTarget}
      />

      {/* Profit Factor */}
      <MetricCard
        label="Profit Factor"
        value={profitFactor}
        positive={data.profit_factor >= 1}
        icon={IconStar}
      />

      {/* Best Trade */}
      <MetricCard
        label={`Best Trade (${data.best_trade_symbol})`}
        value={fmtUSD(data.best_trade_pnl)}
        positive={isBestPos}
        icon={IconThumbUp}
      />

      {/* Worst Trade */}
      <MetricCard
        label={`Worst Trade (${data.worst_trade_symbol})`}
        value={fmtUSD(data.worst_trade_pnl)}
        positive={isWorstPos}
        icon={IconThumbDown}
      />

      {/* Total Trades */}
      <MetricCard
        label="Total Closed Trades"
        value={String(data.total_trades)}
        accent="amber"
        icon={IconStar}
      />
    </div>
  )
}

import clsx from 'clsx'
import type { StockOverview } from '@/types'
import { useMarketStore } from '@/store'
import FreshnessTag from './FreshnessTag'

function ModuleSkeleton() {
  return (
    <section className="glass rounded-2xl shadow-glass p-6 animate-pulse">
      <div className="flex items-start justify-between gap-4 mb-4">
        <div className="flex items-center gap-3 flex-wrap">
          <div className="h-9 w-28 bg-terminal-muted rounded-xl" />
          <div className="h-5 w-48 bg-terminal-muted rounded-xl" />
          <div className="h-5 w-20 bg-terminal-muted rounded-xl" />
        </div>
        <div className="h-8 w-36 bg-terminal-muted rounded-xl shrink-0" />
      </div>
      <div className="flex items-baseline gap-4 mb-4">
        <div className="h-11 w-40 bg-terminal-muted rounded-xl" />
        <div className="h-7 w-32 bg-terminal-muted rounded-xl" />
      </div>
      <div className="flex items-center gap-2">
        <div className="h-5 w-24 bg-terminal-muted rounded-full" />
        <div className="h-5 w-32 bg-terminal-muted rounded-full" />
      </div>
    </section>
  )
}

function ExchangeBadge({ exchange }: { exchange: string }) {
  const upper = exchange.toUpperCase()
  const isNasdaq = upper.includes('NASDAQ') || upper.includes('NMS')
  const isNyse = upper.includes('NYSE')

  return (
    <span
      className={clsx(
        'inline-flex items-center px-2 py-0.5 rounded-md text-[10px] font-mono font-semibold tracking-wider border',
        isNasdaq && 'bg-indigo-500/10 text-indigo-400 border-indigo-500/25',
        isNyse && 'bg-sky-500/10 text-sky-400 border-sky-500/25',
        !isNasdaq && !isNyse && 'bg-terminal-muted text-terminal-ghost border-white/[0.06]',
      )}
    >
      {isNasdaq ? 'NASDAQ' : isNyse ? 'NYSE' : upper}
    </span>
  )
}

function WatchlistButton({ symbol }: { symbol: string }) {
  const watchlists = useMarketStore((s) => s.watchlists)
  const addToWatchlist = useMarketStore((s) => s.addToWatchlist)
  const removeFromWatchlist = useMarketStore((s) => s.removeFromWatchlist)

  const defaultWl = watchlists.find((w) => w.name === 'Watchlist') ?? watchlists[0]
  const isWatched = defaultWl?.symbols.includes(symbol) ?? false

  function handleClick() {
    if (!defaultWl) return
    if (isWatched) {
      removeFromWatchlist(defaultWl.id, symbol)
    } else {
      addToWatchlist(defaultWl.id, symbol)
    }
  }

  return (
    <button
      onClick={handleClick}
      className={clsx(
        'inline-flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-sans font-medium',
        'border transition-all duration-150',
        isWatched
          ? 'bg-terminal-green/10 text-terminal-green border-terminal-green/30 hover:bg-terminal-red/10 hover:text-terminal-red hover:border-terminal-red/30'
          : 'bg-terminal-muted text-terminal-dim border-white/[0.08] hover:bg-indigo-500/10 hover:text-terminal-text hover:border-indigo-500/30',
      )}
    >
      <svg viewBox="0 0 16 16" className="w-3.5 h-3.5 shrink-0" fill={isWatched ? 'currentColor' : 'none'} stroke="currentColor" strokeWidth={isWatched ? 0 : 1.5}>
        <path d="M8 1.5l1.854 3.756 4.146.603-3 2.922.708 4.127L8 10.786l-3.708 1.95.708-4.127-3-2.922 4.146-.603L8 1.5z" />
      </svg>
      {isWatched ? 'Watching' : 'Add to Watchlist'}
    </button>
  )
}

interface Props { data: StockOverview | null; loading: boolean }

export default function HeroModule({ data, loading }: Props) {
  if (!data && loading) return <ModuleSkeleton />
  if (!data) return null

  const change = data.change ?? 0
  const changePct = data.change_pct ?? 0
  const up = changePct >= 0
  const sign = up ? '+' : ''

  return (
    <section id="section-overview" className="glass rounded-2xl shadow-glass px-6 py-5">
      {/* Row 1: identity + watchlist */}
      <div className="flex items-start justify-between gap-4 flex-wrap mb-3">
        <div className="flex items-center gap-3 flex-wrap min-w-0">
          <span className="text-3xl font-mono font-bold tracking-tight text-terminal-text leading-none">
            {data.symbol}
          </span>
          {data.name && (
            <span className="text-base font-sans text-terminal-dim leading-none truncate max-w-xs">
              {data.name}
            </span>
          )}
          {data.exchange && <ExchangeBadge exchange={data.exchange} />}
        </div>
        <div className="flex items-center gap-3 shrink-0">
          <FreshnessTag fetchedAt={data.fetched_at} />
          <WatchlistButton symbol={data.symbol} />
        </div>
      </div>

      {/* Row 2: price + change */}
      <div className="flex items-baseline gap-4 mb-4">
        {data.price != null ? (
          <span className="text-4xl font-mono font-bold tabular-nums tracking-tight text-terminal-text leading-none">
            ${data.price.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
          </span>
        ) : (
          <span className="text-4xl font-mono font-bold text-terminal-ghost leading-none">—</span>
        )}

        {data.change != null && data.change_pct != null && (
          <span
            className={clsx(
              'inline-flex items-center gap-1.5 px-3 py-1 rounded-xl',
              'text-sm font-mono font-semibold tabular-nums ring-1',
              up
                ? 'bg-terminal-green/10 text-terminal-green ring-terminal-green/20'
                : 'bg-terminal-red/10 text-terminal-red ring-terminal-red/20',
            )}
          >
            <svg viewBox="0 0 10 10" className="w-2.5 h-2.5 shrink-0" fill="currentColor">
              {up ? <path d="M5 1l4 8H1L5 1z" /> : <path d="M5 9L1 1h8L5 9z" />}
            </svg>
            {sign}{Math.abs(change).toFixed(2)}
            <span className="opacity-60">({sign}{Math.abs(changePct).toFixed(2)}%)</span>
          </span>
        )}
      </div>

      {/* Row 3: sector, industry, employees, website */}
      <div className="flex items-center gap-2 flex-wrap">
        {data.sector && (
          <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-[11px] font-sans font-medium bg-indigo-500/10 text-indigo-400 border border-indigo-500/20">
            {data.sector}
          </span>
        )}
        {data.industry && (
          <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-[11px] font-sans font-medium bg-terminal-muted text-terminal-dim border border-white/[0.06]">
            {data.industry}
          </span>
        )}
        {data.employees != null && (
          <>
            <span className="text-terminal-ghost text-[11px]">·</span>
            <span className="text-[11px] font-sans text-terminal-ghost tabular-nums">
              {data.employees.toLocaleString()} employees
            </span>
          </>
        )}
        {data.website && (
          <a
            href={data.website}
            target="_blank"
            rel="noopener noreferrer"
            className="ml-auto text-[11px] font-sans text-terminal-ghost hover:text-indigo-400 transition-colors truncate max-w-[200px]"
          >
            {data.website.replace(/^https?:\/\//, '').replace(/\/$/, '')}
          </a>
        )}
      </div>
    </section>
  )
}

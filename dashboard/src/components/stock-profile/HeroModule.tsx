import clsx from 'clsx'
import type { StockOverview } from '@/types'
import { useMarketStore } from '@/store'
import FreshnessTag from './FreshnessTag'

function ModuleSkeleton() {
  return (
    <section className="card rounded-lg  p-6 animate-pulse">
      <div className="flex items-start justify-between gap-4 mb-4">
        <div className="flex items-center gap-3 flex-wrap">
          <div className="h-12 w-12 bg-zinc-800 rounded-lg shrink-0" />
          <div className="h-9 w-28 bg-zinc-800 rounded-lg" />
          <div className="h-5 w-48 bg-zinc-800 rounded-lg" />
          <div className="h-5 w-20 bg-zinc-800 rounded-lg" />
        </div>
        <div className="h-8 w-36 bg-zinc-800 rounded-lg shrink-0" />
      </div>
      <div className="flex items-baseline gap-4 mb-3">
        <div className="h-14 w-44 bg-zinc-800 rounded-lg" />
        <div className="h-8 w-36 bg-zinc-800 rounded-lg" />
      </div>
      <div className="h-1 w-full bg-zinc-800 rounded-full mb-4" />
      <div className="flex items-center gap-2">
        <div className="h-5 w-24 bg-zinc-800 rounded-full" />
        <div className="h-5 w-32 bg-zinc-800 rounded-full" />
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
        isNasdaq && 'bg-zinc-800 text-zinc-200 border-zinc-800',
        isNyse && 'bg-sky-50 text-sky-600 border-sky-100',
        !isNasdaq && !isNyse && 'bg-zinc-800 text-zinc-400 border-zinc-800',
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
        'inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-sans font-medium border transition-colors',
        isWatched
          ? 'bg-emerald-500/10 text-emerald-300 border-emerald-200 hover:bg-red-500/10 hover:text-red-700 hover:border-red-200'
          : 'bg-zinc-900 text-zinc-400 border-zinc-800 hover:bg-zinc-900 hover:text-zinc-50',
      )}
    >
      <svg
        viewBox="0 0 16 16"
        className="w-3.5 h-3.5 shrink-0"
        fill={isWatched ? 'currentColor' : 'none'}
        stroke="currentColor"
        strokeWidth={isWatched ? 0 : 1.5}
      >
        <path d="M8 1.5l1.854 3.756 4.146.603-3 2.922.708 4.127L8 10.786l-3.708 1.95.708-4.127-3-2.922 4.146-.603L8 1.5z" />
      </svg>
      {isWatched ? 'Watching' : 'Add to Watchlist'}
    </button>
  )
}

interface Props {
  data: StockOverview | null
  loading: boolean
  fiftyTwoWeekHigh?: number | null
  fiftyTwoWeekLow?: number | null
}

export default function HeroModule({ data, loading, fiftyTwoWeekHigh, fiftyTwoWeekLow }: Props) {
  if (!data && loading) return <ModuleSkeleton />
  if (!data) return null

  const change = data.change ?? 0
  const changePct = data.change_pct ?? 0
  const up = changePct >= 0
  const sign = up ? '+' : ''
  const avatarLetter = (data.name?.trim() || data.symbol).charAt(0).toUpperCase()

  const has52w =
    data.price != null &&
    fiftyTwoWeekLow != null &&
    fiftyTwoWeekHigh != null &&
    fiftyTwoWeekHigh > fiftyTwoWeekLow

  const rangePct = has52w
    ? Math.min(
        100,
        Math.max(
          0,
          ((data.price! - fiftyTwoWeekLow!) / (fiftyTwoWeekHigh! - fiftyTwoWeekLow!)) * 100,
        ),
      )
    : 0

  return (
    <section id="section-overview" className="card rounded-lg  px-6 py-5">
      <div className="flex items-start justify-between gap-4 flex-wrap mb-4">
        <div className="flex items-center gap-3 flex-wrap min-w-0">
          <div className="h-12 w-12 rounded-lg shrink-0 flex items-center justify-center bg-zinc-950" aria-hidden="true">
            <span className="text-xl font-bold text-white leading-none select-none">{avatarLetter}</span>
          </div>

          <div className="flex items-center gap-3 flex-wrap min-w-0">
            <span className="text-3xl font-mono font-bold tracking-tight text-zinc-100 leading-none">{data.symbol}</span>
            {data.name && <span className="text-base font-sans text-zinc-400 leading-none truncate max-w-xs">{data.name}</span>}
            {data.exchange && <ExchangeBadge exchange={data.exchange} />}
          </div>
        </div>

        <div className="flex items-center gap-3 shrink-0">
          <FreshnessTag fetchedAt={data.fetched_at} />
          <WatchlistButton symbol={data.symbol} />
        </div>
      </div>

      <div className="flex items-baseline gap-4 mb-3">
        {data.price != null ? (
          <span className="text-5xl font-mono font-bold tabular-nums tracking-tight text-zinc-100 leading-none">
            ${data.price.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
          </span>
        ) : (
          <span className="text-5xl font-mono font-bold text-zinc-500 leading-none">--</span>
        )}

        {data.change != null && data.change_pct != null && (
          <span
            className={clsx(
              'inline-flex items-center gap-2 px-4 py-1.5 rounded-lg text-base font-mono font-bold tabular-nums border',
              up ? 'bg-emerald-500/10 text-emerald-300 border-emerald-200' : 'bg-red-500/10 text-red-700 border-red-200',
            )}
          >
            {sign}{Math.abs(change).toFixed(2)}
            <span className="opacity-80 text-sm font-semibold">({sign}{Math.abs(changePct).toFixed(2)}%)</span>
          </span>
        )}
      </div>

      {has52w && (
        <div className="mb-4 flex flex-col gap-1">
          <div className="flex items-center justify-between text-[10px] font-mono text-zinc-400 tabular-nums">
            <span>52W: ${fiftyTwoWeekLow!.toFixed(2)}</span>
            <span className="text-zinc-400">52-week range</span>
            <span>${fiftyTwoWeekHigh!.toFixed(2)}</span>
          </div>
          <div className="relative h-1 w-full rounded-full bg-zinc-800 overflow-hidden">
            <div className="absolute left-0 top-0 h-full rounded-full bg-emerald-600" style={{ width: `${rangePct}%` }} />
          </div>
        </div>
      )}

      <div className="flex items-center gap-2 flex-wrap">
        {data.sector && (
          <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-[11px] font-sans font-medium bg-zinc-800 text-zinc-200 border border-zinc-800">
            {data.sector}
          </span>
        )}
        {data.industry && (
          <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-[11px] font-sans font-medium bg-zinc-800 text-zinc-400 border border-zinc-800">
            {data.industry}
          </span>
        )}
        {data.employees != null && (
          <>
            <span className="text-zinc-500 text-[11px]">-</span>
            <span className="text-[11px] font-sans text-zinc-400 tabular-nums">{data.employees.toLocaleString()} employees</span>
          </>
        )}
        {data.website && (
          <a
            href={data.website}
            target="_blank"
            rel="noopener noreferrer"
            className="ml-auto text-[11px] font-sans text-zinc-400 hover:text-zinc-50 transition-colors truncate max-w-[220px]"
          >
            {data.website.replace(/^https?:\/\//, '').replace(/\/$/, '')}
          </a>
        )}
      </div>
    </section>
  )
}

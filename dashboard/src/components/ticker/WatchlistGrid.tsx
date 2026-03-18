/**
 * WatchlistGrid — the scrollable grid of TickerCards at the top of Dashboard.
 *
 * Features:
 *  • Watchlist header with label and settings icon
 *  • Watchlist tabs with symbol count badge + indigo active state
 *  • Bulk symbol add: paste comma-separated or TradingView export format
 *  • Remove symbols by hovering a card and clicking ✕
 *  • Live prices via WS (handled in useMarketData hook)
 */
import React, { useState, useRef } from 'react'
import clsx from 'clsx'
import TickerCard from './TickerCard'
import { useMarketStore } from '@/store'
import { fetchWatchlist } from '@/services/api'
import type { SortField } from '@/types'

// ── Symbol parser (handles TradingView export + plain comma/newline lists) ────

const CRYPTO_ALIASES: Record<string, string> = {
  BTCUSDT: 'BTC-USD', BTCUSD: 'BTC-USD',
  ETHUSDT: 'ETH-USD', ETHUSD: 'ETH-USD',
  SOLUSDT: 'SOL-USD', SOLUSD: 'SOL-USD',
  BNBUSDT: 'BNB-USD', BNBUSD: 'BNB-USD',
  XRPUSDT: 'XRP-USD', XRPUSD: 'XRP-USD',
  ADAUSDT: 'ADA-USD', ADAUSD: 'ADA-USD',
  DOGEUSDT: 'DOGE-USD', DOGEUSD: 'DOGE-USD',
  AVAXUSDT: 'AVAX-USD', AVAXUSD: 'AVAX-USD',
  DOTUSDT: 'DOT-USD',  DOTUSD: 'DOT-USD',
  LINKUSDT: 'LINK-USD', LINKUSD: 'LINK-USD',
  MATICUSDT: 'MATIC-USD',
}

function parseSymbols(raw: string): string[] {
  return raw
    .split(/[\n,;|\s]+/)                      // split on newline, comma, semicolon, pipe, space
    .map((s) => {
      let sym = s.trim().toUpperCase()
      if (!sym || sym.length > 20) return ''
      if (sym.includes(':')) sym = sym.split(':')[1]   // strip exchange prefix: NASDAQ:AAPL → AAPL
      return CRYPTO_ALIASES[sym] ?? sym
    })
    .filter((s) => s.length >= 1)
    .filter((s, i, arr) => arr.indexOf(s) === i)       // deduplicate
}

// ── Sort options ──────────────────────────────────────────────────────────────

const SORT_OPTIONS: { field: SortField; label: string }[] = [
  { field: 'change_pct', label: '% Chg'   },
  { field: 'price',      label: 'Price'   },
  { field: 'volume',     label: 'Volume'  },
  { field: 'market_cap', label: 'Mkt Cap' },
  { field: 'symbol',     label: 'Symbol'  },
]

// ── Settings icon (inline SVG, no external dep) ───────────────────────────────

function IconSettings({ className }: { className?: string }) {
  return (
    <svg
      className={className}
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.4"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <circle cx="8" cy="8" r="2.2" />
      <path d="M8 1v1.5M8 13.5V15M1 8h1.5M13.5 8H15M2.93 2.93l1.06 1.06M12.01 12.01l1.06 1.06M2.93 13.07l1.06-1.06M12.01 3.99l1.06-1.06" />
    </svg>
  )
}

// ── Component ─────────────────────────────────────────────────────────────────

export default function WatchlistGrid() {
  const {
    quotes,
    setQuotes,
    watchlists,
    activeWatchlist,
    setActiveWatchlist,
    addToWatchlist,
    removeFromWatchlist,
    sortField,
    sortDir,
    setSort,
    loading,
  } = useMarketStore()

  const [showAdd, setShowAdd]       = useState(false)
  const [addInput, setAddInput]     = useState('')
  const [addLoading, setAddLoading] = useState(false)
  const [addFeedback, setFeedback]  = useState('')
  const textareaRef                 = useRef<HTMLTextAreaElement>(null)

  const watchlist = watchlists.find((w) => w.id === activeWatchlist)
  const symbols   = watchlist?.symbols ?? []

  // ── Sort ──────────────────────────────────────────────────────────────────

  const sorted = [...symbols]
    .map((sym) => quotes[sym])
    .filter(Boolean)
    .sort((a, b) => {
      const mul = sortDir === 'asc' ? 1 : -1
      switch (sortField) {
        case 'symbol':     return mul * a.symbol.localeCompare(b.symbol)
        case 'price':      return mul * (a.price - b.price)
        case 'change_pct': return mul * (a.change_pct - b.change_pct)
        case 'volume':     return mul * ((a.volume ?? a.avg_volume ?? 0) - (b.volume ?? b.avg_volume ?? 0))
        case 'market_cap': return mul * ((a.market_cap ?? 0) - (b.market_cap ?? 0))
        default:           return 0
      }
    })

  const handleSort = (field: SortField) => {
    setSort(field, field === sortField && sortDir === 'desc' ? 'asc' : 'desc')
  }

  // ── Bulk add ──────────────────────────────────────────────────────────────

  const openAdd = () => {
    setShowAdd(true)
    setFeedback('')
    setTimeout(() => textareaRef.current?.focus(), 50)
  }

  const handleBulkAdd = async () => {
    const newSymbols = parseSymbols(addInput).filter((s) => !symbols.includes(s))
    if (!newSymbols.length) {
      setFeedback('No new symbols found.')
      return
    }

    newSymbols.forEach((sym) => addToWatchlist(activeWatchlist, sym))
    setAddInput('')
    setShowAdd(false)

    // Immediately fetch quotes for newly added symbols
    setAddLoading(true)
    try {
      const newQuotes = await fetchWatchlist(newSymbols.join(','))
      setQuotes(newQuotes)
    } catch (err) {
      console.warn('[WatchlistGrid] Quote fetch failed:', err)
      // Keep stale data on fetch failure
    } finally {
      setAddLoading(false)
    }
  }

  const parsedCount = addInput.trim()
    ? parseSymbols(addInput).filter((s) => !symbols.includes(s)).length
    : 0

  // ── Remove ────────────────────────────────────────────────────────────────

  const handleRemove = (sym: string) => {
    removeFromWatchlist(activeWatchlist, sym)
  }

  return (
    <div className="bg-zinc-900 border border-[#E8E4DF] rounded-lg overflow-hidden">
      {/* Watchlist header */}
      <div className="flex items-center justify-between px-4 py-2.5 border-b border-[#E8E4DF]">
        <div className="flex items-center gap-2">
          <span className="text-[11px] font-mono font-semibold text-zinc-50 uppercase tracking-wider">
            Watchlist
          </span>
          <span className="flex items-center gap-1 text-[10px] font-mono text-zinc-500">
            <span className="w-1 h-1 rounded-full bg-emerald-500" />
            LIVE
          </span>
        </div>
        <button
          title="Watchlist settings"
          className="p-1 rounded text-zinc-500 hover:text-zinc-400 transition-colors"
        >
          <IconSettings className="w-3.5 h-3.5" />
        </button>
      </div>

      <div className="px-4 pt-3 pb-4">
        {/* Watchlist tabs + sort + add button */}
        <div className="flex items-center gap-1 mb-3 overflow-x-auto pb-1 flex-wrap">
          {watchlists.map((wl) => {
            const isActive = activeWatchlist === wl.id
            const count    = wl.symbols?.length ?? 0
            return (
              <button
                key={wl.id}
                onClick={() => setActiveWatchlist(wl.id)}
                className={clsx(
                  'shrink-0 flex items-center gap-1.5',
                  'text-[11px] font-mono px-2.5 py-1 rounded border transition-colors',
                  isActive
                    ? 'border-zinc-800 bg-zinc-950 text-white'
                    : 'border-[#E8E4DF] text-zinc-400 hover:text-zinc-50 hover:border-zinc-700',
                )}
              >
                {wl.name}
                <span
                  className={clsx(
                    'text-[9px] font-mono tabular-nums',
                    isActive ? 'text-zinc-500' : 'text-zinc-500',
                  )}
                >
                  {count}
                </span>
              </button>
            )
          })}

          <button
            onClick={openAdd}
            title="Add symbols"
            className="shrink-0 text-[11px] font-mono px-2.5 py-1 rounded border border-dashed border-[#E8E4DF] text-zinc-500 hover:text-zinc-50 hover:border-zinc-700 transition-colors"
          >
            + Add
          </button>

          <div className="ml-auto flex items-center gap-1">
            <span className="text-[10px] font-mono text-zinc-500 mr-1">Sort:</span>
            {SORT_OPTIONS.map(({ field, label }) => (
              <button
                key={field}
                onClick={() => handleSort(field)}
                className={clsx(
                  'text-[10px] font-mono px-2 py-0.5 rounded border transition-colors',
                  sortField === field
                    ? 'border-zinc-800 text-zinc-50 bg-zinc-900'
                    : 'border-[#E8E4DF] text-zinc-500 hover:text-zinc-400 hover:border-zinc-700',
                )}
              >
                {label}
                {sortField === field && (
                  <span className="ml-0.5">{sortDir === 'desc' ? '↓' : '↑'}</span>
                )}
              </button>
            ))}
          </div>
        </div>

        {/* ── Bulk add panel ───────────────────────────────────────── */}
        {showAdd && (
          <div className="mb-3 rounded-xl border border-zinc-800 bg-zinc-900/60  p-3 flex flex-col gap-2">
            <div className="flex items-center justify-between">
              <span className="text-xs font-mono text-zinc-100 font-semibold">
                Add Symbols
              </span>
              <button
                onClick={() => { setShowAdd(false); setAddInput(''); setFeedback('') }}
                className="text-[10px] font-mono text-zinc-500 hover:text-red-400 transition-colors"
              >
                ✕ Cancel
              </button>
            </div>

            <p className="text-[10px] font-sans text-zinc-500 leading-relaxed">
              Paste comma-separated symbols or TradingView export (e.g.{' '}
              <span className="text-zinc-400 font-mono">AAPL, TSLA</span> or{' '}
              <span className="text-zinc-400 font-mono">NASDAQ:AAPL</span>).
              Crypto: <span className="text-zinc-400 font-mono">BTCUSDT → BTC-USD</span> auto-converted.
            </p>

            <textarea
              ref={textareaRef}
              value={addInput}
              onChange={(e) => { setAddInput(e.target.value); setFeedback('') }}
              placeholder={'AAPL, TSLA, NVDA\nNASDAQ:MSFT\nBINANCE:BTCUSDT'}
              rows={4}
              className="w-full text-xs font-sans bg-[#FAF8F5] border border-zinc-800 rounded-xl px-3 py-2 text-zinc-100 focus:border-indigo-600/50 focus:outline-none resize-none"
            />

            {addFeedback && (
              <span className="text-[10px] font-sans text-zinc-500">{addFeedback}</span>
            )}

            <div className="flex items-center gap-2">
              <button
                onClick={handleBulkAdd}
                disabled={addLoading || parsedCount === 0}
                className="text-xs font-mono px-4 py-1.5 rounded-xl bg-emerald-500/10 border border-emerald-600/40 text-emerald-400 hover:bg-emerald-500/10 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
              >
                {addLoading
                  ? 'Loading…'
                  : parsedCount > 0
                    ? `Add ${parsedCount} symbol${parsedCount !== 1 ? 's' : ''}`
                    : 'Add'}
              </button>
              {parsedCount > 0 && (
                <span className="text-[10px] font-sans text-zinc-500">
                  {parseSymbols(addInput)
                    .filter((s) => !symbols.includes(s))
                    .slice(0, 5)
                    .join(', ')}
                  {parsedCount > 5 ? ` +${parsedCount - 5} more` : ''}
                </span>
              )}
            </div>
          </div>
        )}

        {/* ── Card grid ───────────────────────────────────────────── */}
        {loading && sorted.length === 0 ? (
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 2xl:grid-cols-7 gap-3">
            {symbols.map((sym) => (
              <SkeletonCard key={sym} />
            ))}
          </div>
        ) : (
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 2xl:grid-cols-7 gap-3">
            {sorted.map((q) => (
              <RemovableCard key={q.symbol} symbol={q.symbol} onRemove={handleRemove}>
                <TickerCard quote={q} />
              </RemovableCard>
            ))}
            {/* Placeholders for symbols without quotes yet */}
            {symbols
              .filter((s) => !quotes[s])
              .map((s) => (
                <SkeletonCard key={s} label={s} />
              ))}
          </div>
        )}
      </div>
    </div>
  )
}

// ── RemovableCard wrapper ─────────────────────────────────────────────────────

function RemovableCard({
  symbol,
  onRemove,
  children,
}: {
  symbol:   string
  onRemove: (sym: string) => void
  children: React.ReactNode
}) {
  const [hovered, setHovered] = useState(false)
  return (
    <div
      className="relative"
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
    >
      {children}
      {hovered && (
        <button
          onClick={(e) => { e.stopPropagation(); onRemove(symbol) }}
          title={`Remove ${symbol}`}
          className="absolute top-1.5 right-1.5 w-4 h-4 rounded-full bg-[#FAF8F5]/80 border border-zinc-800 text-zinc-500 hover:text-red-400 hover:border-red-300 text-[9px] flex items-center justify-center transition-colors z-10"
        >
          ✕
        </button>
      )}
    </div>
  )
}

// ── SkeletonCard ──────────────────────────────────────────────────────────────

function SkeletonCard({ label }: { label?: string }) {
  return (
    <div className="card rounded-2xl p-3 animate-pulse border-l-2 border-l-zinc-800 border-t border-r border-b border-zinc-800">
      <div className="flex justify-between mb-2">
        <div className="h-3 w-16 bg-zinc-800 rounded" />
        <div className="h-3 w-12 bg-zinc-800 rounded" />
      </div>
      <div className="h-6 w-24 bg-zinc-800 rounded mb-1" />
      <div className="h-2 w-16 bg-zinc-800 rounded mb-3" />
      <div className="h-1 w-full bg-zinc-800 rounded mb-2" />
      <div className="flex gap-4">
        <div className="h-2 w-12 bg-zinc-800 rounded" />
        <div className="h-2 w-12 bg-zinc-800 rounded" />
      </div>
      {label && (
        <div className="mt-1 text-[10px] font-mono text-zinc-500">{label}</div>
      )}
    </div>
  )
}

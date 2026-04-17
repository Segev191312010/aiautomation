/**
 * WatchlistGrid — the scrollable grid of TickerCards at the top of Dashboard.
 *
 * Features:
 *  • Watchlist tabs + sort controls
 *  • Bulk symbol add: paste comma-separated or TradingView export format
 *  • Remove symbols by hovering a card and clicking ✕
 *  • Live prices via WS (handled in useMarketData hook)
 */
import React, { useState, useRef, useMemo } from 'react'
import clsx from 'clsx'
import TickerCard from './TickerCard'
import { useMarketStore } from '@/store'
import { fetchWatchlist } from '@/services/api'
import { getMockQuotes } from '@/services/mockService'
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

  const [showAdd, setShowAdd]     = useState(false)
  const [addInput, setAddInput]   = useState('')
  const [addLoading, setAddLoading] = useState(false)
  const [addFeedback, setFeedback]  = useState('')
  const textareaRef               = useRef<HTMLTextAreaElement>(null)

  const watchlist = watchlists.find((w) => w.id === activeWatchlist)
  const symbols   = watchlist?.symbols ?? []

  // ── Sort ──────────────────────────────────────────────────────────────────

  const sorted = useMemo(() => [...symbols]
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
    }), [symbols, quotes, sortField, sortDir])

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
    } catch {
      setQuotes(getMockQuotes(newSymbols))
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
    <div>
      {/* ── Watchlist tabs + sort + add button ──────────────────────── */}
      <div className="flex items-center gap-1 mb-3 overflow-x-auto pb-1 flex-wrap">
        {watchlists.map((wl) => (
          <button
            key={wl.id}
            onClick={() => setActiveWatchlist(wl.id)}
            className={clsx(
              'shrink-0 text-[11px] font-mono px-3 py-1 rounded-full border transition-colors',
              activeWatchlist === wl.id
                ? 'border-terminal-blue/50 bg-terminal-blue/10 text-terminal-blue'
                : 'border-terminal-border text-terminal-dim hover:text-terminal-text hover:border-terminal-muted',
            )}
          >
            {wl.name}
          </button>
        ))}

        {/* Add symbols button */}
        <button
          onClick={openAdd}
          title="Add symbols (paste from TradingView or type manually)"
          className="shrink-0 text-[11px] font-mono px-2.5 py-1 rounded-full border border-dashed border-terminal-border text-terminal-ghost hover:text-terminal-green hover:border-terminal-green/50 transition-colors"
        >
          + Add
        </button>

        {/* Sync pulse */}
        <span className="ml-1 flex items-center gap-1 text-[10px] font-mono text-terminal-ghost">
          <span className="w-1.5 h-1.5 rounded-full bg-terminal-green animate-pulse" />
          LIVE
        </span>

        {/* Sort controls */}
        <div className="ml-auto flex items-center gap-1">
          <span className="text-[10px] font-mono text-terminal-ghost mr-1">Sort:</span>
          {SORT_OPTIONS.map(({ field, label }) => (
            <button
              key={field}
              onClick={() => handleSort(field)}
              className={clsx(
                'text-[10px] font-mono px-2 py-0.5 rounded border transition-colors',
                sortField === field
                  ? 'border-terminal-blue/40 text-terminal-blue bg-terminal-blue/5'
                  : 'border-terminal-border text-terminal-ghost hover:text-terminal-dim',
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

      {/* ── Bulk add panel ───────────────────────────────────────────── */}
      {showAdd && (
        <div className="mb-3 bg-terminal-surface border border-terminal-border rounded-lg p-3 flex flex-col gap-2">
          <div className="flex items-center justify-between">
            <span className="text-xs font-mono text-terminal-text font-semibold">
              Add Symbols
            </span>
            <button
              onClick={() => { setShowAdd(false); setAddInput(''); setFeedback('') }}
              className="text-[10px] font-mono text-terminal-ghost hover:text-terminal-red transition-colors"
            >
              ✕ Cancel
            </button>
          </div>

          <p className="text-[10px] font-mono text-terminal-ghost leading-relaxed">
            Paste comma-separated symbols or TradingView export (e.g.{' '}
            <span className="text-terminal-dim">AAPL, TSLA</span> or{' '}
            <span className="text-terminal-dim">NASDAQ:AAPL</span>).
            Crypto: <span className="text-terminal-dim">BTCUSDT → BTC-USD</span> auto-converted.
          </p>

          <textarea
            ref={textareaRef}
            value={addInput}
            onChange={(e) => { setAddInput(e.target.value); setFeedback('') }}
            placeholder={'AAPL, TSLA, NVDA\nNASDAQ:MSFT\nBINANCE:BTCUSDT'}
            rows={4}
            className="w-full text-xs font-mono bg-terminal-bg border border-terminal-border rounded px-3 py-2 text-terminal-text focus:border-terminal-blue focus:outline-none resize-none"
          />

          {addFeedback && (
            <span className="text-[10px] font-mono text-terminal-ghost">{addFeedback}</span>
          )}

          <div className="flex items-center gap-2">
            <button
              onClick={handleBulkAdd}
              disabled={addLoading || parsedCount === 0}
              className="text-xs font-mono px-4 py-1.5 rounded bg-terminal-green/20 border border-terminal-green/40 text-terminal-green hover:bg-terminal-green/30 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
            >
              {addLoading
                ? 'Loading…'
                : parsedCount > 0
                  ? `Add ${parsedCount} symbol${parsedCount !== 1 ? 's' : ''}`
                  : 'Add'}
            </button>
            {parsedCount > 0 && (
              <span className="text-[10px] font-mono text-terminal-ghost">
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

      {/* ── Card grid ───────────────────────────────────────────────── */}
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
          className="absolute top-1.5 right-1.5 w-4 h-4 rounded-full bg-terminal-bg/80 border border-terminal-border text-terminal-ghost hover:text-terminal-red hover:border-terminal-red/40 text-[9px] flex items-center justify-center transition-colors z-10"
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
    <div className="bg-terminal-surface border border-terminal-border rounded-lg p-3 animate-pulse">
      <div className="flex justify-between mb-2">
        <div className="h-3 w-16 bg-terminal-muted rounded" />
        <div className="h-3 w-12 bg-terminal-muted rounded" />
      </div>
      <div className="h-6 w-24 bg-terminal-muted rounded mb-1" />
      <div className="h-2 w-16 bg-terminal-muted rounded mb-3" />
      <div className="h-1 w-full bg-terminal-muted rounded mb-2" />
      <div className="flex gap-4">
        <div className="h-2 w-12 bg-terminal-muted rounded" />
        <div className="h-2 w-12 bg-terminal-muted rounded" />
      </div>
      {label && (
        <div className="mt-1 text-[10px] font-mono text-terminal-ghost">{label}</div>
      )}
    </div>
  )
}

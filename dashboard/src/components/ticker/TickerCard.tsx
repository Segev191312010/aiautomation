/**
 * TickerCard — Bloomberg-style asset card.
 *
 * Displays:
 *  • Symbol + price (large, monospace)
 *  • Daily change $ and % (colored pill)
 *  • 52-week range progress bar with position marker
 *  • Market cap and volume labels
 *  • Click → select symbol for chart
 */
import React, { useEffect, useRef, useState } from 'react'
import clsx from 'clsx'
import type { MarketQuote } from '@/types'
import { useMarketStore, useUIStore } from '@/store'

// ── 52-Week Range Bar ─────────────────────────────────────────────────────────

function RangeBar({ price, low, high }: { price: number; low: number; high: number }) {
  const pct = high > low ? ((price - low) / (high - low)) * 100 : 50
  const clamped = Math.max(0, Math.min(100, pct))

  return (
    <div className="mt-2">
      <div className="flex justify-between text-[9px] font-mono text-terminal-ghost mb-0.5">
        <span>{low.toLocaleString('en-US', { maximumFractionDigits: 0 })}</span>
        <span className="text-terminal-dim text-[9px]">52W</span>
        <span>{high.toLocaleString('en-US', { maximumFractionDigits: 0 })}</span>
      </div>
      <div className="relative h-1 bg-terminal-muted rounded-full overflow-visible">
        <div
          className="absolute h-full bg-gradient-to-r from-terminal-red-dim to-terminal-green-dim rounded-full"
          style={{ width: '100%' }}
        />
        <div
          className="absolute w-2 h-2 bg-terminal-text border border-terminal-bg rounded-full -top-0.5 shadow"
          style={{ left: `calc(${clamped}% - 4px)` }}
        />
      </div>
    </div>
  )
}

// ── Metric mini-label ─────────────────────────────────────────────────────────

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col">
      <span className="text-[9px] font-mono text-terminal-ghost uppercase tracking-wider">{label}</span>
      <span className="text-[11px] font-mono text-terminal-dim">{value}</span>
    </div>
  )
}

// ── Formatters ────────────────────────────────────────────────────────────────

function fmtPrice(v: number, symbol: string): string {
  if (v >= 1_000) return v.toLocaleString('en-US', { maximumFractionDigits: 2 })
  if (v >= 1)     return v.toFixed(2)
  return v.toFixed(4)
}

function fmtCompact(v: number | undefined | null): string {
  if (v == null) return '—'
  if (v >= 1e12) return `$${(v / 1e12).toFixed(2)}T`
  if (v >= 1e9)  return `$${(v / 1e9).toFixed(1)}B`
  if (v >= 1e6)  return `$${(v / 1e6).toFixed(1)}M`
  return v.toLocaleString()
}

function fmtVol(v: number | undefined | null): string {
  if (v == null) return '—'
  if (v >= 1e9)  return `${(v / 1e9).toFixed(1)}B`
  if (v >= 1e6)  return `${(v / 1e6).toFixed(1)}M`
  if (v >= 1e3)  return `${(v / 1e3).toFixed(0)}K`
  return v.toLocaleString()
}

// ── TickerCard ────────────────────────────────────────────────────────────────

interface Props {
  quote: MarketQuote
  compact?: boolean
}

export default function TickerCard({ quote, compact = false }: Props) {
  const { symbol, price, change, change_pct, year_high, year_low, market_cap, avg_volume } = quote
  const up = change_pct >= 0

  const setSelectedSymbol = useMarketStore((s) => s.setSelectedSymbol)
  const setRoute          = useUIStore((s) => s.setRoute)

  // Flash animation on price change
  const prevPrice = useRef(price)
  const [flash, setFlash]   = useState<'up' | 'down' | null>(null)

  useEffect(() => {
    if (price !== prevPrice.current) {
      setFlash(price > prevPrice.current ? 'up' : 'down')
      const t = setTimeout(() => setFlash(null), 600)
      prevPrice.current = price
      return () => clearTimeout(t)
    }
  }, [price])

  const handleClick = () => {
    setSelectedSymbol(symbol)
    setRoute('market')
  }

  return (
    <button
      onClick={handleClick}
      className={clsx(
        'group w-full text-left bg-terminal-surface border border-terminal-border rounded-lg p-3',
        'hover:border-terminal-blue/40 hover:bg-terminal-elevated transition-all duration-150',
        'focus:outline-none focus:ring-1 focus:ring-terminal-blue/40',
      )}
    >
      {/* ── Header row: symbol + change pill ─────────────────── */}
      <div className="flex items-start justify-between mb-1">
        <div>
          <span className="text-xs font-mono font-semibold text-terminal-text">{symbol}</span>
          {quote.is_mock && (
            <span className="ml-1 text-[9px] font-mono text-terminal-ghost">[mock]</span>
          )}
        </div>
        <span
          className={clsx(
            'text-[10px] font-mono px-1.5 py-0.5 rounded font-semibold tabular-nums',
            up
              ? 'bg-terminal-green/15 text-terminal-green'
              : 'bg-terminal-red/15 text-terminal-red',
          )}
        >
          {up ? '+' : ''}{change_pct.toFixed(2)}%
        </span>
      </div>

      {/* ── Price ─────────────────────────────────────────────── */}
      <div
        className={clsx(
          'font-mono font-bold tabular-nums leading-none transition-colors duration-300',
          compact ? 'text-lg' : 'text-2xl',
          flash === 'up'   ? 'text-terminal-green' :
          flash === 'down' ? 'text-terminal-red'   : 'text-terminal-text',
        )}
      >
        {fmtPrice(price, symbol)}
      </div>

      {/* ── Daily change ──────────────────────────────────────── */}
      <div
        className={clsx(
          'text-xs font-mono tabular-nums mt-0.5',
          up ? 'text-terminal-green' : 'text-terminal-red',
        )}
      >
        {up ? '+' : ''}{change.toFixed(2)}
      </div>

      {/* ── 52-Week range ──────────────────────────────────────── */}
      {year_low != null && year_high != null && (
        <RangeBar price={price} low={year_low} high={year_high} />
      )}

      {/* ── Market cap / volume ────────────────────────────────── */}
      {!compact && (
        <div className="flex gap-4 mt-2">
          <Metric label="Mkt Cap" value={fmtCompact(market_cap)} />
          <Metric label="Vol"     value={fmtVol(avg_volume)} />
        </div>
      )}
    </button>
  )
}

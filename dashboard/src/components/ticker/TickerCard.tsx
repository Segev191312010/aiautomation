/**
 * TickerCard — Clean editorial-style asset card.
 *
 * Minimal chrome, monospace data, dotted separators.
 * Inspired by StockTaper's clean data presentation.
 */
import React, { useEffect, useRef, useState } from 'react'
import clsx from 'clsx'
import type { MarketQuote } from '@/types'
import { useMarketStore, useUIStore } from '@/store'

// ── Formatters ────────────────────────────────────────────────────────────────

function fmtPrice(v: number): string {
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
  const liveSource = quote.live_source === 'ibkr' ? 'IBKR' : 'Yahoo'
  const freshness = quote.stale_s != null && Number.isFinite(quote.stale_s)
    ? `${Math.floor(quote.stale_s)}s`
    : quote.last_update
      ? `${Math.max(0, Math.floor((Date.now() - new Date(quote.last_update).getTime()) / 1000))}s`
      : '--'

  const setSelectedSymbol = useMarketStore((s) => s.setSelectedSymbol)
  const setRoute          = useUIStore((s) => s.setRoute)

  // Flash animation on price change
  const prevPrice = useRef(price)
  const [flash, setFlash] = useState<'up' | 'down' | null>(null)

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
      className="group w-full text-left bg-zinc-900 border border-[#E8E4DF] rounded-lg p-3.5 hover:shadow-md transition-shadow duration-150 focus:outline-none"
    >
      {/* Symbol + change */}
      <div className="flex items-baseline justify-between mb-1">
        <span className="text-sm font-mono font-bold text-zinc-50 tracking-wide">
          {symbol}
        </span>
        <span
          className={clsx(
            'text-[11px] font-mono font-medium tabular-nums',
            up ? 'text-emerald-400' : 'text-red-400',
          )}
        >
          {up ? '▲' : '▼'} {up ? '+' : ''}{change_pct.toFixed(2)}%
        </span>
      </div>

      <div className="mb-1.5 flex items-center justify-between text-[10px] font-mono uppercase tracking-[0.16em] text-zinc-500">
        <span>{liveSource}</span>
        <span>{quote.market_state ?? 'unknown'} / {freshness}</span>
      </div>

      {/* Price */}
      <div
        className={clsx(
          'font-mono font-bold tabular-nums leading-none transition-colors duration-300',
          compact ? 'text-lg' : 'text-xl',
          flash === 'up'   ? 'text-emerald-400' :
          flash === 'down' ? 'text-red-400'   : 'text-zinc-50',
        )}
      >
        {fmtPrice(price)}
      </div>

      {/* Change $ */}
      <div
        className={clsx(
          'text-[11px] font-mono tabular-nums mt-0.5',
          up ? 'text-emerald-400' : 'text-red-400',
        )}
      >
        {up ? '+' : ''}{change.toFixed(2)}
      </div>

      {/* Day range bar */}
      {!compact && quote.bid != null && quote.ask != null && quote.ask > quote.bid && (
        <div className="mt-2.5">
          <div className="flex justify-between text-[10px] font-mono text-zinc-500 mb-0.5 tabular-nums">
            <span>{quote.bid.toFixed(2)}</span>
            <span className="text-zinc-500">Day</span>
            <span>{quote.ask.toFixed(2)}</span>
          </div>
          <div className="h-px bg-[#E8E4DF] relative">
            <div
              className={clsx('absolute h-px', up ? 'bg-emerald-500' : 'bg-red-500')}
              style={{
                width: `${Math.max(0, Math.min(100, quote.ask > quote.bid ? ((price - quote.bid) / (quote.ask - quote.bid)) * 100 : 50))}%`,
              }}
            />
          </div>
        </div>
      )}

      {/* 52W range */}
      {year_low != null && year_high != null && (
        <div className="mt-2">
          <div className="flex justify-between text-[10px] font-mono text-zinc-500 mb-0.5 tabular-nums">
            <span>{year_low.toLocaleString('en-US', { maximumFractionDigits: 0 })}</span>
            <span className="text-zinc-500">52W</span>
            <span>{year_high.toLocaleString('en-US', { maximumFractionDigits: 0 })}</span>
          </div>
          <div className="h-px bg-[#E8E4DF] relative">
            <div
              className="absolute h-px bg-zinc-600"
              style={{
                width: `${Math.max(0, Math.min(100, year_high > year_low ? ((price - year_low) / (year_high - year_low)) * 100 : 50))}%`,
              }}
            />
            <div
              className="absolute w-1.5 h-1.5 bg-zinc-950 rounded-full -top-[2px]"
              style={{
                left: `${Math.max(0, Math.min(100, year_high > year_low ? ((price - year_low) / (year_high - year_low)) * 100 : 50))}%`,
                transform: 'translateX(-50%)',
              }}
            />
          </div>
        </div>
      )}

      {/* Mkt Cap / Volume — dotted separator */}
      {!compact && (
        <div className="flex gap-4 mt-2.5 pt-2 border-t border-dotted border-[#E8E4DF]">
          <div>
            <span className="text-[9px] font-mono text-zinc-500 uppercase tracking-wider">MKT CAP</span>
            <div className="text-[11px] font-mono text-zinc-400 tabular-nums">{fmtCompact(market_cap)}</div>
          </div>
          <div>
            <span className="text-[9px] font-mono text-zinc-500 uppercase tracking-wider">VOL</span>
            <div className="text-[11px] font-mono text-zinc-400 tabular-nums">{fmtVol(avg_volume)}</div>
          </div>
        </div>
      )}

      {/* View Profile — simple text link */}
      {!compact && (
        <div
          onClick={(e) => {
            e.stopPropagation()
            setSelectedSymbol(symbol)
            setRoute('stock')
          }}
          className="mt-2 text-center text-[11px] font-mono text-zinc-500 hover:text-zinc-50 border border-[#E8E4DF] rounded py-1 transition-colors cursor-pointer"
        >
          View Profile
        </div>
      )}
    </button>
  )
}

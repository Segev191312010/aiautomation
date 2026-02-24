/**
 * MarketPage — full-screen chart with symbol selector, timeframe picker,
 * comparison overlay, and technical indicators.
 *
 * Bars auto-refresh on an interval (faster for intraday, slower for daily+).
 * Live candle updates arrive via /ws/market-data WebSocket.
 */
import React, { useState, useEffect, useRef } from 'react'
import clsx from 'clsx'
import TradingChart from '@/components/chart/TradingChart'
import IndicatorPanel from '@/components/chart/IndicatorPanel'
import IndicatorSelector from '@/components/indicators/IndicatorSelector'
import TickerCard from '@/components/ticker/TickerCard'
import { useMarketStore } from '@/store'
import { fetchYahooBars } from '@/services/api'
import { getMockBars } from '@/services/mockService'
import { intervalToSeconds } from '@/utils/indicators'

// ── Timeframes ────────────────────────────────────────────────────────────────

const TIMEFRAMES = [
  { label: '1D',  period: '5d',   interval: '5m'   },
  { label: '1W',  period: '5d',   interval: '30m'  },
  { label: '1M',  period: '1mo',  interval: '1h'   },
  { label: '3M',  period: '3mo',  interval: '1d'   },
  { label: '6M',  period: '6mo',  interval: '1d'   },
  { label: '1Y',  period: '1y',   interval: '1d'   },
  { label: '2Y',  period: '2y',   interval: '1wk'  },
  { label: 'ALL', period: '5y',   interval: '1mo'  },
]

/** Bar auto-refresh intervals (ms) by Yahoo Finance interval string */
const AUTO_REFRESH_MS: Record<string, number> = {
  '5m':  60_000,       // 1 min  — intraday
  '30m': 300_000,      // 5 min
  '1h':  600_000,      // 10 min
  '1d':  1_800_000,    // 30 min — daily
  '1wk': 3_600_000,    // 1 hr
  '1mo': 7_200_000,    // 2 hr
}

// ── Component ─────────────────────────────────────────────────────────────────

export default function MarketPage() {
  const {
    selectedSymbol,
    setSelectedSymbol,
    quotes,
    setBars,
    compMode,
    compSymbol,
    setCompSymbol,
    setCompBars,
    toggleCompMode,
  } = useMarketStore()

  const [tfIdx, setTfIdx]        = useState(5)   // default 1Y
  const [searchInput, setSearch] = useState(selectedSymbol)
  const [loading, setLoading]    = useState(false)
  const refreshTimerRef          = useRef<ReturnType<typeof setInterval> | null>(null)

  const quote      = quotes[selectedSymbol]
  const currentTF  = TIMEFRAMES[tfIdx]
  const barSeconds = intervalToSeconds(currentTF.interval)

  // ── Bar loading ───────────────────────────────────────────────────────────

  const loadBars = async (sym: string, idx: number) => {
    setLoading(true)
    const tf = TIMEFRAMES[idx]
    try {
      const bars = await fetchYahooBars(sym, tf.period, tf.interval)
      setBars(sym, bars)
    } catch {
      setBars(sym, getMockBars(sym, 120, tf.interval === '1d' ? 86_400 : 300))
    } finally {
      setLoading(false)
    }
  }

  const loadCompBars = async (sym: string, idx: number) => {
    const tf = TIMEFRAMES[idx]
    try {
      const bars = await fetchYahooBars(sym, tf.period, tf.interval)
      setCompBars(sym, bars)
    } catch {
      setCompBars(sym, getMockBars(sym, 120))
    }
  }

  // ── Initial + symbol/timeframe-change load ────────────────────────────────

  useEffect(() => {
    loadBars(selectedSymbol, tfIdx)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedSymbol, tfIdx])

  useEffect(() => {
    if (compMode && compSymbol) loadCompBars(compSymbol, tfIdx)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [compMode, compSymbol, tfIdx])

  // ── 24/7 auto-refresh ─────────────────────────────────────────────────────

  useEffect(() => {
    if (refreshTimerRef.current) clearInterval(refreshTimerRef.current)
    const interval = AUTO_REFRESH_MS[currentTF.interval] ?? 300_000
    refreshTimerRef.current = setInterval(() => {
      loadBars(selectedSymbol, tfIdx)
    }, interval)
    return () => {
      if (refreshTimerRef.current) clearInterval(refreshTimerRef.current)
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedSymbol, tfIdx])

  // ── Handlers ──────────────────────────────────────────────────────────────

  const handleSearch = (e: React.FormEvent) => {
    e.preventDefault()
    const sym = searchInput.trim().toUpperCase()
    if (sym) {
      setSelectedSymbol(sym)
      setSearch(sym)
    }
  }

  return (
    <div className="flex flex-col h-full gap-2">
      {/* ── Toolbar row 1: symbol + timeframe + compare ────────────── */}
      <div className="flex items-center gap-3 flex-wrap">
        {/* Symbol search */}
        <form onSubmit={handleSearch} className="flex items-center gap-2">
          <input
            value={searchInput}
            onChange={(e) => setSearch(e.target.value.toUpperCase())}
            placeholder="Enter symbol…"
            className="w-28 text-sm font-mono bg-terminal-input border border-terminal-border rounded-l px-3 py-1.5 text-terminal-text focus:border-terminal-blue focus:outline-none"
          />
          <button
            type="submit"
            className="text-xs font-mono px-3 py-1.5 rounded-r bg-terminal-blue/20 border border-l-0 border-terminal-blue/40 text-terminal-blue hover:bg-terminal-blue/30 transition-colors"
          >
            Go
          </button>
        </form>

        {/* Timeframe selector */}
        <div className="flex gap-0.5">
          {TIMEFRAMES.map((tf, i) => (
            <button
              key={tf.label}
              onClick={() => setTfIdx(i)}
              className={clsx(
                'text-[11px] font-mono px-2 py-1 rounded border transition-colors',
                tfIdx === i
                  ? 'border-terminal-blue/50 text-terminal-blue bg-terminal-blue/10'
                  : 'border-terminal-border text-terminal-ghost hover:text-terminal-dim hover:border-terminal-muted',
              )}
            >
              {tf.label}
            </button>
          ))}
        </div>

        {/* Compare */}
        <div className="ml-auto flex items-center gap-2">
          {compMode && (
            <form
              onSubmit={(e) => {
                e.preventDefault()
                const inp = (e.currentTarget.elements.namedItem('comp') as HTMLInputElement)
                  .value.toUpperCase()
                setCompSymbol(inp)
              }}
              className="flex"
            >
              <input
                name="comp"
                defaultValue={compSymbol}
                placeholder="vs. MSFT"
                className="w-24 text-xs font-mono bg-terminal-input border border-terminal-border rounded-l px-2 py-1 text-terminal-text focus:border-terminal-amber focus:outline-none"
              />
              <button
                type="submit"
                className="text-xs font-mono px-2 py-1 rounded-r bg-terminal-amber/10 border border-l-0 border-terminal-amber/40 text-terminal-amber"
              >
                Set
              </button>
            </form>
          )}
          <button
            onClick={toggleCompMode}
            className={clsx(
              'text-[11px] font-mono px-2.5 py-1 rounded border transition-colors',
              compMode
                ? 'border-terminal-amber/40 text-terminal-amber bg-terminal-amber/5'
                : 'border-terminal-border text-terminal-ghost hover:text-terminal-dim',
            )}
          >
            ⊕ Compare
          </button>
        </div>
      </div>

      {/* ── Toolbar row 2: indicator selector ─────────────────────── */}
      <div className="flex items-center gap-2 px-1">
        <IndicatorSelector />
      </div>

      {/* ── Chart + quote card ────────────────────────────────────── */}
      <div className="flex gap-4 flex-1 min-h-0">
        {/* Main chart + oscillator panels stacked */}
        <div className="flex-1 min-w-0 flex flex-col gap-2 min-h-0">
          {/* Main candlestick chart */}
          <div className="flex-1 min-h-0 bg-terminal-surface border border-terminal-border rounded-lg overflow-hidden relative">
            {loading && (
              <div className="absolute inset-0 bg-terminal-bg/50 flex items-center justify-center z-10">
                <span className="text-xs font-mono text-terminal-dim animate-pulse">Loading…</span>
              </div>
            )}
            <div className="flex items-center gap-2 px-4 py-2.5 border-b border-terminal-border">
              <span className="font-mono font-bold text-terminal-text">{selectedSymbol}</span>
              {compMode && compSymbol && (
                <>
                  <span className="text-terminal-ghost font-mono text-xs">vs.</span>
                  <span className="font-mono font-bold text-terminal-amber">{compSymbol}</span>
                  <span className="text-[10px] font-mono text-terminal-ghost ml-1">[normalized %]</span>
                </>
              )}
              {/* Live pulse indicator */}
              <span className="ml-auto flex items-center gap-1.5 text-[10px] font-mono text-terminal-ghost">
                <span className="w-1.5 h-1.5 rounded-full bg-terminal-green animate-pulse" />
                LIVE
              </span>
            </div>
            <div className="h-[calc(100%-44px)]">
              <TradingChart symbol={selectedSymbol} barSeconds={barSeconds} className="h-full" />
            </div>
          </div>

          {/* Oscillator sub-panels (RSI / MACD) */}
          <IndicatorPanel
            symbol={selectedSymbol}
            className="h-36 shrink-0"
          />
        </div>

        {/* Quote card sidebar */}
        <aside className="w-52 shrink-0">
          {quote ? (
            <TickerCard quote={quote} />
          ) : (
            <div className="bg-terminal-surface border border-terminal-border rounded-lg p-4 text-xs font-mono text-terminal-ghost">
              No quote data for {selectedSymbol}
            </div>
          )}

          {compMode && compSymbol && quotes[compSymbol] && (
            <div className="mt-3">
              <TickerCard quote={quotes[compSymbol]} />
            </div>
          )}
        </aside>
      </div>
    </div>
  )
}

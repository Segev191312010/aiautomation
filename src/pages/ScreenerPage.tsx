/**
 * ScreenerPage — live 24/7 stock screener across the US equity universe.
 *
 * Loads a user-selected universe (S&P 500, NASDAQ 100, Russell 1000 sample,
 * or the user's custom watchlist), polls quotes in batches, applies filters
 * (price range, % change, volume, etc.) and ranks the results.
 *
 * Updates auto-refresh every 15 s while the page is open.
 */
import React, { useEffect, useMemo, useState } from 'react'
import clsx from 'clsx'
import { useMarketStore, useUIStore } from '@/store'
import { fetchWatchlist, fetchSettings, updateSettings } from '@/services/api'
import { getUniverse, type AppSettings } from '@/services/mockBackend'
import type { MarketQuote } from '@/types'

type SortKey = 'change_pct' | 'price' | 'volume' | 'market_cap' | 'symbol'

const REFRESH_MS = 15_000
const BATCH_SIZE = 50

export default function ScreenerPage() {
  const setRoute          = useUIStore((s) => s.setRoute)
  const setSelectedSymbol = useMarketStore((s) => s.setSelectedSymbol)
  const watchlists        = useMarketStore((s) => s.watchlists)
  const activeWatchlist   = useMarketStore((s) => s.activeWatchlist)

  const [settings, setSettings] = useState<AppSettings | null>(null)
  const [quotes,   setQuotes]   = useState<MarketQuote[]>([])
  const [loading,  setLoading]  = useState(false)
  const [progress, setProgress] = useState(0)
  const [lastTick, setLastTick] = useState<number | null>(null)

  const [minPrice,   setMinPrice]   = useState('')
  const [maxPrice,   setMaxPrice]   = useState('')
  const [minChange,  setMinChange]  = useState('')
  const [maxChange,  setMaxChange]  = useState('')
  const [minVol,     setMinVol]     = useState('')
  const [sortKey,    setSortKey]    = useState<SortKey>('change_pct')
  const [sortDir,    setSortDir]    = useState<'asc' | 'desc'>('desc')
  const [search,     setSearch]     = useState('')

  // Load settings
  useEffect(() => {
    fetchSettings().then(setSettings)
  }, [])

  // Build the universe to scan
  const universe = useMemo(() => {
    if (!settings) return [] as string[]
    if (settings.screener_universe === 'custom') {
      return watchlists.find((w) => w.id === activeWatchlist)?.symbols ?? []
    }
    return getUniverse(settings.screener_universe)
  }, [settings, watchlists, activeWatchlist])

  // Bulk-fetch quotes in batches
  const refresh = async () => {
    if (universe.length === 0) return
    setLoading(true)
    setProgress(0)

    const all: MarketQuote[] = []
    for (let i = 0; i < universe.length; i += BATCH_SIZE) {
      const batch = universe.slice(i, i + BATCH_SIZE)
      try {
        const q = await fetchWatchlist(batch.join(','))
        all.push(...q)
      } catch { /* batch failed — keep going */ }
      setProgress(Math.min(100, Math.round(((i + batch.length) / universe.length) * 100)))
    }
    setQuotes(all)
    setLastTick(Date.now())
    setLoading(false)
  }

  // Refresh on universe change + 15 s interval
  useEffect(() => {
    if (universe.length === 0) return
    refresh()
    const t = setInterval(refresh, REFRESH_MS)
    return () => clearInterval(t)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [universe.join(',')])

  // Apply filters + sort
  const filtered = useMemo(() => {
    const q = quotes.filter((x) => {
      if (search && !x.symbol.toLowerCase().includes(search.toLowerCase())) return false
      if (minPrice  && x.price       < Number(minPrice))  return false
      if (maxPrice  && x.price       > Number(maxPrice))  return false
      if (minChange && x.change_pct  < Number(minChange)) return false
      if (maxChange && x.change_pct  > Number(maxChange)) return false
      if (minVol    && (x.volume ?? x.avg_volume ?? 0) < Number(minVol) * 1e6) return false
      return true
    })

    const mul = sortDir === 'asc' ? 1 : -1
    q.sort((a, b) => {
      switch (sortKey) {
        case 'symbol':     return mul * a.symbol.localeCompare(b.symbol)
        case 'price':      return mul * (a.price - b.price)
        case 'change_pct': return mul * (a.change_pct - b.change_pct)
        case 'volume':     return mul * ((a.volume ?? a.avg_volume ?? 0) - (b.volume ?? b.avg_volume ?? 0))
        case 'market_cap': return mul * ((a.market_cap ?? 0) - (b.market_cap ?? 0))
      }
    })

    return q
  }, [quotes, search, minPrice, maxPrice, minChange, maxChange, minVol, sortKey, sortDir])

  const toggleSort = (k: SortKey) => {
    if (sortKey === k) setSortDir(sortDir === 'desc' ? 'asc' : 'desc')
    else { setSortKey(k); setSortDir('desc') }
  }

  const handleSymbolClick = (sym: string) => {
    setSelectedSymbol(sym)
    setRoute('market')
  }

  if (!settings) return <div className="text-xs font-mono text-terminal-ghost p-8">Loading…</div>

  return (
    <div className="flex flex-col gap-4 h-full">
      {/* ── Header / universe selector ──────────────────────────── */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h2 className="text-sm font-semibold text-terminal-text">US Stock Screener</h2>
          <p className="text-xs text-terminal-dim mt-0.5">
            Scanning {universe.length} symbols ·{' '}
            <span className="text-terminal-green">{filtered.length} matches</span>
            {lastTick && (
              <span className="text-terminal-ghost ml-2">
                · updated {new Date(lastTick).toLocaleTimeString()}
              </span>
            )}
            <span className="ml-2 flex items-center gap-1 inline-flex">
              <span className="w-1.5 h-1.5 rounded-full bg-terminal-green animate-pulse" />
              <span className="text-[10px] font-mono text-terminal-green">LIVE 24/7</span>
            </span>
          </p>
        </div>

        <div className="flex items-center gap-2">
          <select
            value={settings.screener_universe}
            onChange={async (e) => {
              const v = e.target.value as AppSettings['screener_universe']
              setSettings(await updateSettings({ screener_universe: v }))
            }}
            className="text-xs font-mono bg-terminal-input border border-terminal-border rounded px-3 py-1.5 text-terminal-text focus:border-terminal-blue focus:outline-none"
          >
            <option value="sp500">S&amp;P 500 (~100)</option>
            <option value="nasdaq100">NASDAQ 100 (~40)</option>
            <option value="russell1k">Russell 1000 (~140)</option>
            <option value="custom">My Watchlist</option>
          </select>
          <button
            onClick={refresh}
            disabled={loading}
            className="text-xs font-mono px-3 py-1.5 rounded bg-terminal-blue/20 border border-terminal-blue/40 text-terminal-blue hover:bg-terminal-blue/30 disabled:opacity-50 transition-colors"
          >
            {loading ? `Scanning ${progress}%…` : '↻ Refresh now'}
          </button>
        </div>
      </div>

      {/* ── Filter bar ──────────────────────────────────────────── */}
      <div className="bg-terminal-surface border border-terminal-border rounded-lg p-3 flex flex-wrap items-end gap-3">
        <FilterInput label="Search"      value={search}    onChange={setSearch}    placeholder="AAPL" />
        <FilterInput label="Min price"   value={minPrice}  onChange={setMinPrice}  type="number" placeholder="$" />
        <FilterInput label="Max price"   value={maxPrice}  onChange={setMaxPrice}  type="number" placeholder="$" />
        <FilterInput label="Min % chg"   value={minChange} onChange={setMinChange} type="number" placeholder="%" />
        <FilterInput label="Max % chg"   value={maxChange} onChange={setMaxChange} type="number" placeholder="%" />
        <FilterInput label="Min vol (M)" value={minVol}    onChange={setMinVol}    type="number" placeholder="0" />
        <button
          onClick={() => {
            setSearch(''); setMinPrice(''); setMaxPrice('')
            setMinChange(''); setMaxChange(''); setMinVol('')
          }}
          className="text-[11px] font-mono px-3 py-1 rounded border border-terminal-border text-terminal-dim hover:text-terminal-text transition-colors"
        >
          Clear
        </button>

        {/* Preset filters */}
        <div className="flex gap-1 ml-auto">
          <PresetButton onClick={() => { setMinChange('3'); setSortKey('change_pct'); setSortDir('desc') }}>
            🚀 Top Gainers
          </PresetButton>
          <PresetButton onClick={() => { setMaxChange('-3'); setSortKey('change_pct'); setSortDir('asc') }}>
            📉 Top Losers
          </PresetButton>
          <PresetButton onClick={() => { setMinVol('50'); setSortKey('volume'); setSortDir('desc') }}>
            🔥 High Volume
          </PresetButton>
        </div>
      </div>

      {/* ── Results table ───────────────────────────────────────── */}
      <div className="flex-1 min-h-0 bg-terminal-surface border border-terminal-border rounded-lg overflow-hidden flex flex-col">
        <div className="overflow-auto flex-1">
          <table className="w-full text-xs">
            <thead className="sticky top-0 bg-terminal-surface border-b border-terminal-border">
              <tr>
                {(['symbol','price','change_pct','volume','market_cap'] as SortKey[]).map((k) => (
                  <th
                    key={k}
                    onClick={() => toggleSort(k)}
                    className="py-2 px-3 text-[10px] font-mono uppercase tracking-widest text-terminal-ghost font-normal text-right first:text-left cursor-pointer hover:text-terminal-text"
                  >
                    {labelFor(k)}
                    {sortKey === k && <span className="ml-0.5">{sortDir === 'desc' ? '↓' : '↑'}</span>}
                  </th>
                ))}
                <th className="py-2 px-3 text-[10px] font-mono uppercase tracking-widest text-terminal-ghost font-normal text-right">Range</th>
                <th className="py-2 px-3" />
              </tr>
            </thead>
            <tbody>
              {filtered.map((q) => {
                const up = q.change_pct >= 0
                const rng = q.year_high && q.year_low && q.year_high > q.year_low
                  ? ((q.price - q.year_low) / (q.year_high - q.year_low)) * 100
                  : 50
                return (
                  <tr
                    key={q.symbol}
                    onClick={() => handleSymbolClick(q.symbol)}
                    className="border-b border-terminal-border/40 hover:bg-terminal-muted/20 cursor-pointer transition-colors"
                  >
                    <td className="py-1.5 px-3 font-mono font-semibold text-terminal-text">{q.symbol}</td>
                    <td className="py-1.5 px-3 font-mono text-terminal-text tabular-nums text-right">
                      {q.price.toFixed(2)}
                    </td>
                    <td className={clsx('py-1.5 px-3 font-mono tabular-nums text-right',
                                        up ? 'text-terminal-green' : 'text-terminal-red')}>
                      {up ? '+' : ''}{q.change_pct.toFixed(2)}%
                    </td>
                    <td className="py-1.5 px-3 font-mono text-terminal-dim tabular-nums text-right">
                      {fmtVol(q.volume ?? q.avg_volume)}
                    </td>
                    <td className="py-1.5 px-3 font-mono text-terminal-dim tabular-nums text-right">
                      {fmtCompact(q.market_cap)}
                    </td>
                    <td className="py-1.5 px-3 w-24">
                      <div className="relative h-1 bg-terminal-muted rounded-full">
                        <div
                          className="absolute w-1.5 h-1.5 bg-terminal-blue rounded-full -top-0.25"
                          style={{ left: `calc(${Math.max(0, Math.min(100, rng))}% - 3px)` }}
                        />
                      </div>
                    </td>
                    <td className="py-1.5 px-3 text-right">
                      <span className="text-[10px] font-mono text-terminal-ghost">→</span>
                    </td>
                  </tr>
                )
              })}
              {filtered.length === 0 && !loading && (
                <tr>
                  <td colSpan={7} className="text-center py-12 text-xs font-mono text-terminal-ghost">
                    {quotes.length === 0 ? 'Loading universe…' : 'No matches. Adjust filters.'}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function labelFor(k: SortKey): string {
  switch (k) {
    case 'change_pct': return '% Chg'
    case 'market_cap': return 'Mkt Cap'
    case 'volume':     return 'Volume'
    case 'price':      return 'Price'
    case 'symbol':     return 'Symbol'
  }
}

function FilterInput({
  label, value, onChange, type, placeholder,
}: {
  label: string; value: string; onChange: (v: string) => void
  type?: string; placeholder?: string
}) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-[10px] font-mono text-terminal-ghost uppercase">{label}</span>
      <input
        type={type ?? 'text'}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="w-24 text-xs font-mono bg-terminal-input border border-terminal-border rounded px-2 py-1 text-terminal-text focus:border-terminal-blue focus:outline-none"
      />
    </label>
  )
}

function PresetButton({ children, onClick }: { children: React.ReactNode; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className="text-[11px] font-mono px-2.5 py-1 rounded border border-terminal-border text-terminal-dim hover:text-terminal-text hover:border-terminal-muted transition-colors"
    >
      {children}
    </button>
  )
}

function fmtVol(v: number | undefined | null): string {
  if (v == null) return '—'
  if (v >= 1e9) return `${(v / 1e9).toFixed(2)}B`
  if (v >= 1e6) return `${(v / 1e6).toFixed(1)}M`
  if (v >= 1e3) return `${(v / 1e3).toFixed(0)}K`
  return String(v)
}

function fmtCompact(v: number | undefined | null): string {
  if (v == null) return '—'
  if (v >= 1e12) return `$${(v / 1e12).toFixed(2)}T`
  if (v >= 1e9)  return `$${(v / 1e9).toFixed(1)}B`
  if (v >= 1e6)  return `$${(v / 1e6).toFixed(1)}M`
  return `$${v.toLocaleString()}`
}

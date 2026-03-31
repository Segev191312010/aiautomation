import React, { useMemo, useState, useCallback } from 'react'
import clsx from 'clsx'
import { useMarketStore, useScreenerStore, useStockProfileStore, useUIStore } from '@/store'
import type { ScanResultRow } from '@/types'

type SortKey =
  | 'symbol'
  | 'price'
  | 'change_pct'
  | 'volume'
  | 'market_cap'
  | 'screener_score'
  | 'setup'
  | 'relative_volume'
  | 'momentum_20d'
  | string
type SortDir = 'asc' | 'desc'

// ── Formatting helpers ───────────────────────────────────────────────────────

function formatVolume(value: number): string {
  if (value >= 1_000_000_000) return `${(value / 1_000_000_000).toFixed(1)}B`
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}K`
  return String(value)
}

function formatMktCap(value: number | undefined | null): string {
  if (value == null) return '--'
  if (value >= 1e12) return `$${(value / 1e12).toFixed(1)}T`
  if (value >= 1e9) return `$${(value / 1e9).toFixed(1)}B`
  if (value >= 1e6) return `$${(value / 1e6).toFixed(0)}M`
  return `$${value.toFixed(0)}`
}

// ── Score bar component ──────────────────────────────────────────────────────

function ScoreBar({ score }: { score: number }) {
  const pct = Math.min(score, 100)
  const color =
    pct >= 75 ? 'bg-emerald-500' :
    pct >= 55 ? 'bg-cyan-500' :
    pct >= 35 ? 'bg-amber-500' :
    'bg-red-500'
  const textColor =
    pct >= 75 ? 'text-emerald-300' :
    pct >= 55 ? 'text-cyan-300' :
    pct >= 35 ? 'text-amber-300' :
    'text-red-400'

  return (
    <div className="flex items-center gap-2 min-w-[90px]">
      <span className={clsx('text-[11px] font-mono font-bold tabular-nums w-8 text-right', textColor)}>
        {score.toFixed(0)}
      </span>
      <div className="flex-1 h-1.5 rounded-full bg-zinc-800 overflow-hidden">
        <div className={clsx('h-full rounded-full transition-all', color)} style={{ width: `${pct}%` }} />
      </div>
    </div>
  )
}

// ── Setup badge component ────────────────────────────────────────────────────

const SETUP_STYLES: Record<string, string> = {
  breakout: 'bg-emerald-500/15 text-emerald-300 border-emerald-500/25',
  pullback: 'bg-cyan-500/15 text-cyan-300 border-cyan-500/25',
  reversal: 'bg-amber-500/15 text-amber-300 border-amber-500/25',
  trend:    'bg-blue-500/15 text-blue-300 border-blue-500/25',
  mixed:    'bg-zinc-700/50 text-zinc-400 border-zinc-600',
}

function SetupBadge({ setup, notes }: { setup: string; notes: string[] }) {
  return (
    <div className="space-y-1">
      <span className={clsx(
        'inline-flex rounded-md border px-2 py-0.5 text-[10px] font-sans font-semibold uppercase tracking-wider',
        SETUP_STYLES[setup] ?? SETUP_STYLES.mixed,
      )}>
        {setup}
      </span>
      {notes.length > 0 && (
        <div className="max-w-[180px] text-[10px] leading-snug text-zinc-500">
          {notes[0]}
        </div>
      )}
    </div>
  )
}

// ── Momentum indicator (replaces synthetic sparkline) ────────────────────────

function MomentumIndicator({ momentum, changePct }: { momentum: number; changePct: number }) {
  const isUp = momentum >= 0
  const strength = Math.min(Math.abs(momentum), 30) / 30 // normalize to 0-1
  const barWidth = Math.max(strength * 48, 4)

  return (
    <div className="flex items-center gap-1.5 min-w-[60px]" title={`20D: ${momentum >= 0 ? '+' : ''}${momentum.toFixed(1)}%`}>
      <svg viewBox="0 0 10 10" className={clsx('w-3 h-3', isUp ? 'text-emerald-400' : 'text-red-400')}>
        {isUp
          ? <path d="M5 1 L9 7 L1 7 Z" fill="currentColor" />
          : <path d="M5 9 L9 3 L1 3 Z" fill="currentColor" />}
      </svg>
      <div className="h-1.5 rounded-full bg-zinc-800 w-12 overflow-hidden">
        <div
          className={clsx('h-full rounded-full', isUp ? 'bg-emerald-500' : 'bg-red-500')}
          style={{ width: `${barWidth}px` }}
        />
      </div>
    </div>
  )
}

// ── Column configuration ─────────────────────────────────────────────────────

interface ColumnDef {
  key: string
  label: string
  defaultVisible: boolean
  sortable: boolean
}

const BASE_COLUMNS: ColumnDef[] = [
  { key: 'symbol',          label: 'Symbol',   defaultVisible: true,  sortable: true },
  { key: 'name',            label: 'Name',     defaultVisible: true,  sortable: false },
  { key: 'momentum_arrow',  label: '20D',      defaultVisible: true,  sortable: false },
  { key: 'screener_score',  label: 'Score',    defaultVisible: true,  sortable: true },
  { key: 'setup',           label: 'Setup',    defaultVisible: true,  sortable: true },
  { key: 'price',           label: 'Price',    defaultVisible: true,  sortable: true },
  { key: 'change_pct',      label: 'Change',   defaultVisible: true,  sortable: true },
  { key: 'relative_volume', label: 'RVOL',     defaultVisible: true,  sortable: true },
  { key: 'momentum_20d',    label: 'Mom20',    defaultVisible: true,  sortable: true },
  { key: 'volume',          label: 'Volume',   defaultVisible: true,  sortable: true },
  { key: 'market_cap',      label: 'Mkt Cap',  defaultVisible: false, sortable: true },
  { key: 'sector',          label: 'Sector',   defaultVisible: false, sortable: false },
]

// ── Quick filter bar ─────────────────────────────────────────────────────────

interface QuickFilters {
  sector: string
  minPrice: string
  maxPrice: string
  minScore: string
  setupType: string
}

function QuickFilterBar({
  filters,
  setFilters,
  sectors,
}: {
  filters: QuickFilters
  setFilters: (f: QuickFilters) => void
  sectors: string[]
}) {
  const inputClass =
    'rounded-md border border-zinc-700 bg-zinc-800 px-2 py-1.5 text-[11px] font-mono text-zinc-200 ' +
    'focus:border-zinc-600 focus:outline-none w-20'
  const selectClass =
    'rounded-md border border-zinc-700 bg-zinc-800 px-2 py-1.5 text-[11px] font-sans text-zinc-200 ' +
    'focus:border-zinc-600 focus:outline-none'

  return (
    <div className="flex flex-wrap items-center gap-3">
      <label className="flex items-center gap-1.5">
        <span className="text-[10px] font-sans uppercase tracking-wider text-zinc-500">Sector</span>
        <select
          value={filters.sector}
          onChange={(e) => setFilters({ ...filters, sector: e.target.value })}
          className={selectClass}
        >
          <option value="">All</option>
          {sectors.map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
      </label>

      <label className="flex items-center gap-1.5">
        <span className="text-[10px] font-sans uppercase tracking-wider text-zinc-500">Price</span>
        <input
          type="number"
          placeholder="Min"
          value={filters.minPrice}
          onChange={(e) => setFilters({ ...filters, minPrice: e.target.value })}
          className={inputClass}
        />
        <span className="text-zinc-600">-</span>
        <input
          type="number"
          placeholder="Max"
          value={filters.maxPrice}
          onChange={(e) => setFilters({ ...filters, maxPrice: e.target.value })}
          className={inputClass}
        />
      </label>

      <label className="flex items-center gap-1.5">
        <span className="text-[10px] font-sans uppercase tracking-wider text-zinc-500">Min Score</span>
        <input
          type="number"
          placeholder="0"
          value={filters.minScore}
          onChange={(e) => setFilters({ ...filters, minScore: e.target.value })}
          className={inputClass}
        />
      </label>

      <label className="flex items-center gap-1.5">
        <span className="text-[10px] font-sans uppercase tracking-wider text-zinc-500">Setup</span>
        <select
          value={filters.setupType}
          onChange={(e) => setFilters({ ...filters, setupType: e.target.value })}
          className={selectClass}
        >
          <option value="">All</option>
          <option value="breakout">Breakout</option>
          <option value="pullback">Pullback</option>
          <option value="reversal">Reversal</option>
          <option value="trend">Trend</option>
        </select>
      </label>

      {(filters.sector || filters.minPrice || filters.maxPrice || filters.minScore || filters.setupType) && (
        <button
          type="button"
          onClick={() => setFilters({ sector: '', minPrice: '', maxPrice: '', minScore: '', setupType: '' })}
          className="text-[11px] font-sans text-zinc-500 hover:text-zinc-300 transition-colors"
        >
          Clear
        </button>
      )}
    </div>
  )
}

// ── Column picker ────────────────────────────────────────────────────────────

function ColumnPicker({
  columns,
  visibleKeys,
  toggleColumn,
}: {
  columns: ColumnDef[]
  visibleKeys: Set<string>
  toggleColumn: (key: string) => void
}) {
  const [open, setOpen] = useState(false)

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="rounded-md border border-zinc-700 bg-zinc-800 px-2.5 py-1.5 text-[11px] font-sans text-zinc-300 hover:border-zinc-600 hover:text-zinc-100 transition-colors"
      >
        Columns
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-30" onClick={() => setOpen(false)} />
          <div className="absolute right-0 top-full mt-1 z-40 w-44 rounded-lg border border-zinc-700 bg-zinc-800 p-2 shadow-xl">
            {columns.map((col) => (
              <label
                key={col.key}
                className="flex items-center gap-2 rounded px-2 py-1.5 text-[11px] font-sans text-zinc-300 hover:bg-zinc-700/50 cursor-pointer"
              >
                <input
                  type="checkbox"
                  checked={visibleKeys.has(col.key)}
                  onChange={() => toggleColumn(col.key)}
                  className="rounded border-zinc-600 bg-zinc-900 text-cyan-500 focus:ring-0 focus:ring-offset-0"
                />
                {col.label}
              </label>
            ))}
          </div>
        </>
      )}
    </div>
  )
}

// ── Sort header ──────────────────────────────────────────────────────────────

function SortHeader({
  label,
  sortKeyVal,
  active,
  sortDir,
  onClick,
}: {
  label: string
  sortKeyVal: SortKey
  active: boolean
  sortDir: SortDir
  onClick: (key: SortKey) => void
}) {
  return (
    <th
      onClick={() => onClick(sortKeyVal)}
      className={clsx(
        'px-3 py-2.5 text-left text-[10px] font-sans font-semibold uppercase tracking-[0.18em] cursor-pointer whitespace-nowrap transition-colors',
        active ? 'text-zinc-50' : 'text-zinc-500 hover:text-zinc-400',
      )}
    >
      <span className="inline-flex items-center gap-1">
        {label}
        {active && (
          <svg viewBox="0 0 24 24" fill="currentColor" className="w-3 h-3 text-zinc-400">
            {sortDir === 'asc' ? <path d="M7 14l5-5 5 5z" /> : <path d="M7 10l5 5 5-5z" />}
          </svg>
        )}
      </span>
    </th>
  )
}

// ── Main component ───────────────────────────────────────────────────────────

export default function ScanResultsTable() {
  const { results, enriched, skippedSymbols, elapsedMs, totalSymbols } = useScreenerStore()
  const setRoute = useUIStore((s) => s.setRoute)
  const setSelectedSymbol = useMarketStore((s) => s.setSelectedSymbol)
  const setProfileSymbol = useStockProfileStore((s) => s.setSymbol)

  const [sortKey, setSortKey] = useState<SortKey>('screener_score')
  const [sortDir, setSortDir] = useState<SortDir>('desc')
  const [quickFilters, setQuickFilters] = useState<QuickFilters>({
    sector: '', minPrice: '', maxPrice: '', minScore: '', setupType: '',
  })
  const [visibleCols, setVisibleCols] = useState<Set<string>>(() => {
    const defaults = new Set<string>()
    BASE_COLUMNS.forEach((c) => { if (c.defaultVisible) defaults.add(c.key) })
    return defaults
  })
  const [expandedRow, setExpandedRow] = useState<string | null>(null)

  const handleSort = (key: SortKey) => {
    if (key === sortKey) {
      setSortDir((v) => (v === 'asc' ? 'desc' : 'asc'))
      return
    }
    setSortKey(key)
    setSortDir('desc')
  }

  const toggleColumn = useCallback((key: string) => {
    setVisibleCols((prev) => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }, [])

  // Collect all indicator columns from results
  const indicatorCols = useMemo(() => {
    const cols = new Set<string>()
    results.forEach((row) => {
      Object.keys(row.indicators).forEach((key) => cols.add(key))
    })
    return Array.from(cols).sort()
  }, [results])

  // Collect unique sectors
  const sectors = useMemo(() => {
    const set = new Set<string>()
    results.forEach((row) => {
      const s = enriched[row.symbol]?.sector
      if (s) set.add(s)
    })
    return Array.from(set).sort()
  }, [results, enriched])

  // Apply quick filters
  const filtered = useMemo(() => {
    return results.filter((row) => {
      if (quickFilters.sector) {
        const sector = enriched[row.symbol]?.sector
        if (sector !== quickFilters.sector) return false
      }
      if (quickFilters.minPrice) {
        if (row.price < Number(quickFilters.minPrice)) return false
      }
      if (quickFilters.maxPrice) {
        if (row.price > Number(quickFilters.maxPrice)) return false
      }
      if (quickFilters.minScore) {
        if (row.screener_score < Number(quickFilters.minScore)) return false
      }
      if (quickFilters.setupType) {
        if (row.setup !== quickFilters.setupType) return false
      }
      return true
    })
  }, [results, enriched, quickFilters])

  // Sort
  const sorted = useMemo(() => {
    const data = [...filtered]
    data.sort((a, b) => {
      let aVal: number
      let bVal: number

      switch (sortKey) {
        case 'symbol':
          return sortDir === 'asc' ? a.symbol.localeCompare(b.symbol) : b.symbol.localeCompare(a.symbol)
        case 'price':         aVal = a.price;          bVal = b.price;          break
        case 'screener_score': aVal = a.screener_score; bVal = b.screener_score; break
        case 'change_pct':    aVal = a.change_pct;     bVal = b.change_pct;     break
        case 'relative_volume': aVal = a.relative_volume; bVal = b.relative_volume; break
        case 'momentum_20d':  aVal = a.momentum_20d;   bVal = b.momentum_20d;   break
        case 'volume':        aVal = a.volume;         bVal = b.volume;         break
        case 'market_cap':
          aVal = enriched[a.symbol]?.market_cap ?? 0
          bVal = enriched[b.symbol]?.market_cap ?? 0
          break
        case 'setup':
          return sortDir === 'asc' ? a.setup.localeCompare(b.setup) : b.setup.localeCompare(a.setup)
        default:
          aVal = a.indicators[sortKey] ?? 0
          bVal = b.indicators[sortKey] ?? 0
      }

      return sortDir === 'asc' ? aVal - bVal : bVal - aVal
    })
    return data
  }, [enriched, filtered, sortDir, sortKey])

  const openMarket = (row: ScanResultRow) => {
    setSelectedSymbol(row.symbol)
    setRoute('market')
  }

  const openAnalysis = (row: ScanResultRow) => {
    setSelectedSymbol(row.symbol)
    setProfileSymbol(row.symbol)
    setRoute('stock')
  }

  const handleExportCSV = () => {
    // RFC 4180 CSV escaping: wrap in quotes if value contains comma, quote, or newline
    const escapeCSV = (val: string | number): string => {
      const s = String(val)
      if (s.includes(',') || s.includes('"') || s.includes('\n')) {
        return `"${s.replace(/"/g, '""')}"`
      }
      return s
    }

    const header = ['Symbol', 'Name', 'Score', 'Setup', 'Price', 'Change%', 'RVOL', 'Mom20', 'Volume', 'MktCap', 'Sector', ...indicatorCols]
    const rows = sorted.map((row) => {
      const d = enriched[row.symbol]
      return [
        row.symbol,
        escapeCSV(d?.name ?? ''),
        row.screener_score.toFixed(1),
        row.setup,
        row.price.toFixed(2),
        row.change_pct.toFixed(2),
        row.relative_volume.toFixed(2),
        row.momentum_20d.toFixed(2),
        row.volume,
        d?.market_cap ?? '',
        escapeCSV(d?.sector ?? ''),
        ...indicatorCols.map((col) => row.indicators[col]?.toFixed(4) ?? ''),
      ].join(',')
    })
    const csv = [header.join(','), ...rows].join('\n')
    const blob = new Blob([csv], { type: 'text/csv' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `screener_${new Date().toISOString().slice(0, 10)}.csv`
    a.click()
    URL.revokeObjectURL(url)
  }

  if (results.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center gap-3 py-16 text-center">
        <div className="flex h-12 w-12 items-center justify-center rounded-lg border border-zinc-800 bg-zinc-900">
          <svg viewBox="0 0 24 24" fill="currentColor" className="w-5 h-5 text-zinc-500">
            <path d="M15.5 14h-.79l-.28-.27A6.47 6.47 0 0 0 16 9.5 6.5 6.5 0 1 0 9.5 16c1.61 0 3.09-.59 4.23-1.57l.27.28v.79l5 4.99L20.49 19zM9.5 14A4.5 4.5 0 1 1 14 9.5 4.5 4.5 0 0 1 9.5 14z" />
          </svg>
        </div>
        <div>
          <p className="text-sm font-sans font-medium text-zinc-400">No matches found</p>
          <p className="mt-1 text-xs font-sans text-zinc-500">
            {skippedSymbols.length > 0
              ? `${skippedSymbols.length} symbol${skippedSymbols.length > 1 ? 's were' : ' was'} skipped because of missing data`
              : 'Adjust the filters and run the scan again'}
          </p>
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-4">
      {/* Toolbar: stats + quick filters + column picker + export */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-center gap-3">
          <div className="rounded-md border border-zinc-700 bg-zinc-800 px-2.5 py-1.5 text-[11px] font-mono text-zinc-400">
            {filtered.length}/{results.length} match{results.length === 1 ? '' : 'es'}
            {totalSymbols > 0 && ` of ${totalSymbols} scanned`}
          </div>
          {elapsedMs > 0 && (
            <div className="rounded-md border border-zinc-700 bg-zinc-800 px-2.5 py-1.5 text-[11px] font-mono text-zinc-500">
              {elapsedMs < 1000 ? `${elapsedMs}ms` : `${(elapsedMs / 1000).toFixed(1)}s`}
            </div>
          )}
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={handleExportCSV}
            className="rounded-md border border-zinc-700 bg-zinc-800 px-2.5 py-1.5 text-[11px] font-sans text-zinc-300 hover:border-zinc-600 hover:text-zinc-100 transition-colors"
          >
            Export CSV
          </button>
          <ColumnPicker columns={BASE_COLUMNS} visibleKeys={visibleCols} toggleColumn={toggleColumn} />
        </div>
      </div>

      {/* Quick filters */}
      <QuickFilterBar filters={quickFilters} setFilters={setQuickFilters} sectors={sectors} />

      {skippedSymbols.length > 0 && (
        <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-3.5 py-2.5 text-xs font-sans text-amber-300">
          {skippedSymbols.length} symbol{skippedSymbols.length > 1 ? 's' : ''} skipped due to missing data.
        </div>
      )}

      {/* Table */}
      <div className="overflow-x-auto">
        <table className="table-editorial w-full min-w-[900px] text-xs">
          <thead>
            <tr className="border-b border-zinc-800">
              {BASE_COLUMNS.filter((c) => visibleCols.has(c.key)).map((col) =>
                col.sortable ? (
                  <SortHeader
                    key={col.key}
                    label={col.label}
                    sortKeyVal={col.key}
                    active={sortKey === col.key}
                    sortDir={sortDir}
                    onClick={handleSort}
                  />
                ) : (
                  <th key={col.key} className="px-3 py-2.5 text-left text-[10px] font-sans font-semibold uppercase tracking-[0.18em] text-zinc-500">
                    {col.label}
                  </th>
                ),
              )}
              {indicatorCols.map((col) => (
                <SortHeader key={col} label={col} sortKeyVal={col} active={sortKey === col} sortDir={sortDir} onClick={handleSort} />
              ))}
              <th className="px-3 py-2.5 text-left text-[10px] font-sans font-semibold uppercase tracking-[0.18em] text-zinc-500">Actions</th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((row) => {
              const details = enriched[row.symbol]
              const up = row.change_pct >= 0
              const isExpanded = expandedRow === row.symbol

              return (
                <React.Fragment key={row.symbol}>
                  <tr
                    className={clsx(
                      'border-b border-dotted border-zinc-800 transition-colors cursor-pointer',
                      isExpanded ? 'bg-zinc-800/60' : 'hover:bg-zinc-900/80',
                    )}
                    onClick={() => setExpandedRow(isExpanded ? null : row.symbol)}
                  >
                    {visibleCols.has('symbol') && (
                      <td className="px-3 py-3 font-mono font-bold text-zinc-50">{row.symbol}</td>
                    )}
                    {visibleCols.has('name') && (
                      <td className="px-3 py-3 font-sans text-zinc-400 max-w-[140px] truncate">{details?.name ?? '--'}</td>
                    )}
                    {visibleCols.has('momentum_arrow') && (
                      <td className="px-3 py-3">
                        <MomentumIndicator momentum={row.momentum_20d} changePct={row.change_pct} />
                      </td>
                    )}
                    {visibleCols.has('screener_score') && (
                      <td className="px-3 py-3"><ScoreBar score={row.screener_score} /></td>
                    )}
                    {visibleCols.has('setup') && (
                      <td className="px-3 py-3"><SetupBadge setup={row.setup} notes={row.notes} /></td>
                    )}
                    {visibleCols.has('price') && (
                      <td className="px-3 py-3 font-mono text-zinc-100">{row.price.toFixed(2)}</td>
                    )}
                    {visibleCols.has('change_pct') && (
                      <td className="px-3 py-3">
                        <span className={clsx(
                          'rounded-md px-2 py-0.5 text-[11px] font-mono font-medium',
                          up ? 'bg-emerald-500/10 text-emerald-300' : 'bg-red-500/10 text-red-400',
                        )}>
                          {up ? '+' : ''}{row.change_pct.toFixed(2)}%
                        </span>
                      </td>
                    )}
                    {visibleCols.has('relative_volume') && (
                      <td className="px-3 py-3">
                        <span className={clsx(
                          'font-mono text-[11px]',
                          row.relative_volume >= 2.0 ? 'text-amber-300 font-bold' :
                          row.relative_volume >= 1.5 ? 'text-zinc-200' :
                          'text-zinc-400',
                        )}>
                          {row.relative_volume.toFixed(2)}x
                        </span>
                      </td>
                    )}
                    {visibleCols.has('momentum_20d') && (
                      <td className="px-3 py-3 font-mono text-zinc-300">
                        <span className={clsx(row.momentum_20d >= 0 ? 'text-emerald-300' : 'text-red-400')}>
                          {row.momentum_20d >= 0 ? '+' : ''}{row.momentum_20d.toFixed(2)}%
                        </span>
                      </td>
                    )}
                    {visibleCols.has('volume') && (
                      <td className="px-3 py-3 font-mono text-zinc-400">{formatVolume(row.volume)}</td>
                    )}
                    {visibleCols.has('market_cap') && (
                      <td className="px-3 py-3 font-mono text-zinc-400">{formatMktCap(details?.market_cap)}</td>
                    )}
                    {visibleCols.has('sector') && (
                      <td className="px-3 py-3 font-sans text-zinc-400">{details?.sector ?? '--'}</td>
                    )}
                    {indicatorCols.map((col) => (
                      <td key={col} className="px-3 py-3 font-mono text-zinc-200">
                        {row.indicators[col]?.toFixed(2) ?? '--'}
                      </td>
                    ))}
                    <td className="px-3 py-3">
                      <div className="flex items-center gap-1.5">
                        <button
                          type="button"
                          onClick={(e) => { e.stopPropagation(); openMarket(row) }}
                          className="rounded-md border border-zinc-700 bg-zinc-800 px-2 py-1 text-[11px] font-sans text-zinc-300 hover:border-zinc-600 hover:text-zinc-100 transition-colors"
                        >
                          Chart
                        </button>
                        <button
                          type="button"
                          onClick={(e) => { e.stopPropagation(); openAnalysis(row) }}
                          className="rounded-md border border-indigo-500/30 bg-indigo-500/10 px-2 py-1 text-[11px] font-sans text-indigo-300 hover:bg-indigo-500/20 transition-colors"
                        >
                          Profile
                        </button>
                      </div>
                    </td>
                  </tr>
                  {/* Expanded row detail */}
                  {isExpanded && (
                    <tr className="bg-zinc-800/40">
                      <td colSpan={BASE_COLUMNS.filter((c) => visibleCols.has(c.key)).length + indicatorCols.length + 1} className="px-6 py-3">
                        <div className="flex flex-wrap gap-x-6 gap-y-2 text-[11px]">
                          <div>
                            <span className="text-zinc-500">Trend Strength:</span>{' '}
                            <span className="text-zinc-200 font-mono">{row.trend_strength.toFixed(1)}/32</span>
                          </div>
                          {Object.entries(row.indicators).map(([k, v]) => (
                            <div key={k}>
                              <span className="text-zinc-500">{k}:</span>{' '}
                              <span className="text-zinc-200 font-mono">{v.toFixed(4)}</span>
                            </div>
                          ))}
                          {row.notes.length > 0 && (
                            <div className="basis-full">
                              <span className="text-zinc-500">Notes:</span>{' '}
                              <span className="text-zinc-300">{row.notes.join(' / ')}</span>
                            </div>
                          )}
                        </div>
                      </td>
                    </tr>
                  )}
                </React.Fragment>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}


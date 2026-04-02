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
    pct >= 75 ? 'bg-[var(--success)]' :
    pct >= 55 ? 'bg-[var(--accent)]' :
    pct >= 35 ? 'bg-[var(--warning)]' :
    'bg-[var(--danger)]'
  const textColor =
    pct >= 75 ? 'text-[var(--success)]' :
    pct >= 55 ? 'text-[var(--accent)]' :
    pct >= 35 ? 'text-[var(--warning)]' :
    'text-[var(--danger)]'

  return (
    <div className="flex items-center gap-2 min-w-[90px]">
      <span className={clsx('text-[11px] font-mono font-bold tabular-nums w-8 text-right', textColor)}>
        {score.toFixed(0)}
      </span>
      <div className="flex-1 h-1.5 rounded-full bg-[var(--border)] overflow-hidden">
        <div className={clsx('h-full rounded-full transition-all', color)} style={{ width: `${pct}%` }} />
      </div>
    </div>
  )
}

// ── Setup badge component ────────────────────────────────────────────────────

const SETUP_STYLES: Record<string, string> = {
  breakout: 'bg-[color:rgba(52,211,153,0.12)] text-[var(--success)] border-[color:rgba(52,211,153,0.2)]',
  pullback: 'bg-[var(--accent-soft)] text-[var(--accent)] border-[var(--accent)]/30',
  reversal: 'bg-[color:rgba(251,191,36,0.12)] text-[var(--warning)] border-[color:rgba(251,191,36,0.22)]',
  trend:    'bg-[color:rgba(59,130,246,0.12)] text-[color:rgba(59,130,246,0.9)] border-[color:rgba(59,130,246,0.3)]',
  mixed:    'bg-[var(--bg-hover)] text-[var(--text-secondary)] border-[var(--border)]',
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
        <div className="max-w-[180px] text-[10px] leading-snug text-[var(--text-muted)]">
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
      <svg viewBox="0 0 10 10" className={clsx('w-3 h-3', isUp ? 'text-[var(--success)]' : 'text-[var(--danger)]')}>
        {isUp
          ? <path d="M5 1 L9 7 L1 7 Z" fill="currentColor" />
          : <path d="M5 9 L9 3 L1 3 Z" fill="currentColor" />}
      </svg>
      <div className="h-1.5 rounded-full bg-[var(--border)] w-12 overflow-hidden">
        <div
          className={clsx('h-full rounded-full', isUp ? 'bg-[var(--success)]' : 'bg-[var(--danger)]')}
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
    'rounded-md border border-[var(--border)] bg-[var(--bg-hover)] px-2 py-1.5 text-[11px] font-mono text-[var(--text-primary)] ' +
    'focus:border-[var(--border)] focus:outline-none w-20'
  const selectClass =
    'rounded-md border border-[var(--border)] bg-[var(--bg-hover)] px-2 py-1.5 text-[11px] font-sans text-[var(--text-primary)] ' +
    'focus:border-[var(--border)] focus:outline-none'

  return (
    <div className="flex flex-wrap items-center gap-3">
      <label className="flex items-center gap-1.5">
        <span className="text-[10px] font-sans uppercase tracking-wider text-[var(--text-muted)]">Sector</span>
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
        <span className="text-[10px] font-sans uppercase tracking-wider text-[var(--text-muted)]">Price</span>
        <input
          type="number"
          placeholder="Min"
          value={filters.minPrice}
          onChange={(e) => setFilters({ ...filters, minPrice: e.target.value })}
          className={inputClass}
        />
        <span className="text-[var(--text-muted)]">-</span>
        <input
          type="number"
          placeholder="Max"
          value={filters.maxPrice}
          onChange={(e) => setFilters({ ...filters, maxPrice: e.target.value })}
          className={inputClass}
        />
      </label>

      <label className="flex items-center gap-1.5">
        <span className="text-[10px] font-sans uppercase tracking-wider text-[var(--text-muted)]">Min Score</span>
        <input
          type="number"
          placeholder="0"
          value={filters.minScore}
          onChange={(e) => setFilters({ ...filters, minScore: e.target.value })}
          className={inputClass}
        />
      </label>

      <label className="flex items-center gap-1.5">
        <span className="text-[10px] font-sans uppercase tracking-wider text-[var(--text-muted)]">Setup</span>
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
          className="text-[11px] font-sans text-[var(--text-muted)] hover:text-[var(--text-secondary)] transition-colors"
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
        className="rounded-md border border-[var(--border)] bg-[var(--bg-hover)] px-2.5 py-1.5 text-[11px] font-sans text-[var(--text-secondary)] hover:border-[var(--border)] hover:text-[var(--text-primary)] transition-colors"
      >
        Columns
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-30" onClick={() => setOpen(false)} />
          <div className="absolute right-0 top-full mt-1 z-40 w-44 rounded-lg border border-[var(--border)] bg-[var(--bg-hover)] p-2 shadow-xl">
            {columns.map((col) => (
              <label
                key={col.key}
                className="flex items-center gap-2 rounded px-2 py-1.5 text-[11px] font-sans text-[var(--text-secondary)] hover:bg-[var(--bg-hover)] cursor-pointer"
              >
                <input
                  type="checkbox"
                  checked={visibleKeys.has(col.key)}
                  onChange={() => toggleColumn(col.key)}
                  className="rounded border-[var(--border)] bg-[var(--bg-primary)] text-[var(--accent)] focus:ring-0 focus:ring-offset-0"
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
        active ? 'text-[var(--text-primary)]' : 'text-[var(--text-muted)] hover:text-[var(--text-secondary)]',
      )}
    >
      <span className="inline-flex items-center gap-1">
        {label}
        {active && (
          <svg viewBox="0 0 24 24" fill="currentColor" className="w-3 h-3 text-[var(--text-secondary)]">
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
        <div className="flex h-12 w-12 items-center justify-center rounded-lg border border-[var(--border)] bg-[var(--bg-primary)]">
          <svg viewBox="0 0 24 24" fill="currentColor" className="w-5 h-5 text-[var(--text-muted)]">
            <path d="M15.5 14h-.79l-.28-.27A6.47 6.47 0 0 0 16 9.5 6.5 6.5 0 1 0 9.5 16c1.61 0 3.09-.59 4.23-1.57l.27.28v.79l5 4.99L20.49 19zM9.5 14A4.5 4.5 0 1 1 14 9.5 4.5 4.5 0 0 1 9.5 14z" />
          </svg>
        </div>
        <div>
          <p className="text-sm font-sans font-medium text-[var(--text-secondary)]">No matches found</p>
          <p className="mt-1 text-xs font-sans text-[var(--text-muted)]">
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
          <div className="rounded-md border border-[var(--border)] bg-[var(--bg-hover)] px-2.5 py-1.5 text-[11px] font-mono text-[var(--text-secondary)]">
            {filtered.length}/{results.length} match{results.length === 1 ? '' : 'es'}
            {totalSymbols > 0 && ` of ${totalSymbols} scanned`}
          </div>
          {elapsedMs > 0 && (
            <div className="rounded-md border border-[var(--border)] bg-[var(--bg-hover)] px-2.5 py-1.5 text-[11px] font-mono text-[var(--text-muted)]">
              {elapsedMs < 1000 ? `${elapsedMs}ms` : `${(elapsedMs / 1000).toFixed(1)}s`}
            </div>
          )}
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={handleExportCSV}
            className="rounded-md border border-[var(--border)] bg-[var(--bg-hover)] px-2.5 py-1.5 text-[11px] font-sans text-[var(--text-secondary)] hover:border-[var(--border)] hover:text-[var(--text-primary)] transition-colors"
          >
            Export CSV
          </button>
          <ColumnPicker columns={BASE_COLUMNS} visibleKeys={visibleCols} toggleColumn={toggleColumn} />
        </div>
      </div>

      {/* Quick filters */}
      <QuickFilterBar filters={quickFilters} setFilters={setQuickFilters} sectors={sectors} />

      {skippedSymbols.length > 0 && (
        <div className="rounded-lg border border-[color:rgba(251,191,36,0.3)] bg-[color:rgba(251,191,36,0.1)] px-3.5 py-2.5 text-xs font-sans text-[var(--warning)]">
          {skippedSymbols.length} symbol{skippedSymbols.length > 1 ? 's' : ''} skipped due to missing data.
        </div>
      )}

      {/* Table */}
      <div className="overflow-x-auto">
        <table className="table-editorial w-full min-w-[900px] text-xs">
          <thead>
            <tr className="border-b border-[var(--border)]">
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
                  <th key={col.key} className="px-3 py-2.5 text-left text-[10px] font-sans font-semibold uppercase tracking-[0.18em] text-[var(--text-muted)]">
                    {col.label}
                  </th>
                ),
              )}
              {indicatorCols.map((col) => (
                <SortHeader key={col} label={col} sortKeyVal={col} active={sortKey === col} sortDir={sortDir} onClick={handleSort} />
              ))}
              <th className="px-3 py-2.5 text-left text-[10px] font-sans font-semibold uppercase tracking-[0.18em] text-[var(--text-muted)]">Actions</th>
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
                      'border-b border-dotted border-[var(--border)] transition-colors cursor-pointer',
                      isExpanded ? 'bg-[var(--bg-hover)]/60' : 'hover:bg-[var(--bg-primary)]/80',
                    )}
                    onClick={() => setExpandedRow(isExpanded ? null : row.symbol)}
                  >
                    {visibleCols.has('symbol') && (
                      <td className="px-3 py-3 font-mono font-bold text-[var(--text-primary)]">{row.symbol}</td>
                    )}
                    {visibleCols.has('name') && (
                      <td className="px-3 py-3 font-sans text-[var(--text-secondary)] max-w-[140px] truncate">{details?.name ?? '--'}</td>
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
                      <td className="px-3 py-3 font-mono text-[var(--text-primary)]">{row.price.toFixed(2)}</td>
                    )}
                    {visibleCols.has('change_pct') && (
                      <td className="px-3 py-3">
                        <span className={clsx(
                          'rounded-md px-2 py-0.5 text-[11px] font-mono font-medium',
                          up
                            ? 'bg-[color:rgba(52,211,153,0.12)] text-[var(--success)]'
                            : 'bg-[color:rgba(248,113,113,0.12)] text-[var(--danger)]',
                        )}>
                          {up ? '+' : ''}{row.change_pct.toFixed(2)}%
                        </span>
                      </td>
                    )}
                    {visibleCols.has('relative_volume') && (
                      <td className="px-3 py-3">
                        <span className={clsx(
                          'font-mono text-[11px]',
                          row.relative_volume >= 2.0 ? 'text-[var(--warning)] font-bold' :
                          row.relative_volume >= 1.5 ? 'text-[var(--text-primary)]' :
                          'text-[var(--text-secondary)]',
                        )}>
                          {row.relative_volume.toFixed(2)}x
                        </span>
                      </td>
                    )}
                    {visibleCols.has('momentum_20d') && (
                      <td className="px-3 py-3 font-mono text-[var(--text-secondary)]">
                        <span className={clsx(row.momentum_20d >= 0 ? 'text-[var(--success)]' : 'text-[var(--danger)]')}>
                          {row.momentum_20d >= 0 ? '+' : ''}{row.momentum_20d.toFixed(2)}%
                        </span>
                      </td>
                    )}
                    {visibleCols.has('volume') && (
                      <td className="px-3 py-3 font-mono text-[var(--text-secondary)]">{formatVolume(row.volume)}</td>
                    )}
                    {visibleCols.has('market_cap') && (
                      <td className="px-3 py-3 font-mono text-[var(--text-secondary)]">{formatMktCap(details?.market_cap)}</td>
                    )}
                    {visibleCols.has('sector') && (
                      <td className="px-3 py-3 font-sans text-[var(--text-secondary)]">{details?.sector ?? '--'}</td>
                    )}
                    {indicatorCols.map((col) => (
                      <td key={col} className="px-3 py-3 font-mono text-[var(--text-primary)]">
                        {row.indicators[col]?.toFixed(2) ?? '--'}
                      </td>
                    ))}
                    <td className="px-3 py-3">
                      <div className="flex items-center gap-1.5">
                        <button
                          type="button"
                          onClick={(e) => { e.stopPropagation(); openMarket(row) }}
                          className="rounded-md border border-[var(--border)] bg-[var(--bg-hover)] px-2 py-1 text-[11px] font-sans text-[var(--text-secondary)] hover:border-[var(--border)] hover:text-[var(--text-primary)] transition-colors"
                        >
                          Chart
                        </button>
                        <button
                          type="button"
                          onClick={(e) => { e.stopPropagation(); openAnalysis(row) }}
                          className="rounded-md border border-[var(--accent)] bg-[var(--accent-soft)] px-2 py-1 text-[11px] font-sans text-white hover:bg-[color:rgba(245,158,11,0.24)] transition-colors"
                        >
                          Profile
                        </button>
                      </div>
                    </td>
                  </tr>
                  {/* Expanded row detail */}
                  {isExpanded && (
                    <tr className="bg-[var(--bg-hover)]/40">
                      <td colSpan={BASE_COLUMNS.filter((c) => visibleCols.has(c.key)).length + indicatorCols.length + 1} className="px-6 py-3">
                        <div className="flex flex-wrap gap-x-6 gap-y-2 text-[11px]">
                          <div>
                            <span className="text-[var(--text-muted)]">Trend Strength:</span>{' '}
                            <span className="text-[var(--text-primary)] font-mono">{row.trend_strength.toFixed(1)}/32</span>
                          </div>
                          {Object.entries(row.indicators).map(([k, v]) => (
                            <div key={k}>
                              <span className="text-[var(--text-muted)]">{k}:</span>{' '}
                              <span className="text-[var(--text-primary)] font-mono">{v.toFixed(4)}</span>
                            </div>
                          ))}
                          {row.notes.length > 0 && (
                            <div className="basis-full">
                              <span className="text-[var(--text-muted)]">Notes:</span>{' '}
                              <span className="text-[var(--text-secondary)]">{row.notes.join(' / ')}</span>
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

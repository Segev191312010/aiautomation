import React, { useMemo, useState } from 'react'
import clsx from 'clsx'
import { useScreenerStore, useUIStore, useMarketStore } from '@/store'
import type { ScanResultRow } from '@/types'

type SortKey = 'symbol' | 'price' | 'change_pct' | 'volume' | string
type SortDir = 'asc' | 'desc'

function formatVolume(vol: number): string {
  if (vol >= 1_000_000_000) return `${(vol / 1_000_000_000).toFixed(1)}B`
  if (vol >= 1_000_000)     return `${(vol / 1_000_000).toFixed(1)}M`
  if (vol >= 1_000)         return `${(vol / 1_000).toFixed(1)}K`
  return String(vol)
}

function formatMktCap(cap: number | undefined | null): string {
  if (cap == null) return '-'
  if (cap >= 1e12)  return `$${(cap / 1e12).toFixed(1)}T`
  if (cap >= 1e9)   return `$${(cap / 1e9).toFixed(1)}B`
  if (cap >= 1e6)   return `$${(cap / 1e6).toFixed(0)}M`
  return `$${cap.toFixed(0)}`
}

export default function ScanResultsTable() {
  const { results, enriched, skippedSymbols } = useScreenerStore()
  const { setRoute } = useUIStore()
  const { setSelectedSymbol } = useMarketStore()

  const [sortKey, setSortKey] = useState<SortKey>('change_pct')
  const [sortDir, setSortDir] = useState<SortDir>('desc')

  const handleSort = (key: SortKey) => {
    if (key === sortKey) {
      setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'))
    } else {
      setSortKey(key)
      setSortDir('desc')
    }
  }

  // Collect all indicator columns from results
  const indicatorCols = useMemo(() => {
    const cols = new Set<string>()
    results.forEach((r) => {
      Object.keys(r.indicators).forEach((k) => cols.add(k))
    })
    return Array.from(cols).sort()
  }, [results])

  const sorted = useMemo(() => {
    const arr = [...results]
    arr.sort((a, b) => {
      let aVal: number, bVal: number
      switch (sortKey) {
        case 'symbol':
          return sortDir === 'asc' ? a.symbol.localeCompare(b.symbol) : b.symbol.localeCompare(a.symbol)
        case 'price':
          aVal = a.price; bVal = b.price; break
        case 'change_pct':
          aVal = a.change_pct; bVal = b.change_pct; break
        case 'volume':
          aVal = a.volume; bVal = b.volume; break
        case 'market_cap':
          aVal = enriched[a.symbol]?.market_cap ?? 0
          bVal = enriched[b.symbol]?.market_cap ?? 0
          break
        default:
          // indicator column
          aVal = a.indicators[sortKey] ?? 0
          bVal = b.indicators[sortKey] ?? 0
      }
      return sortDir === 'asc' ? aVal - bVal : bVal - aVal
    })
    return arr
  }, [results, sortKey, sortDir, enriched])

  const handleRowClick = (row: ScanResultRow) => {
    setSelectedSymbol(row.symbol)
    setRoute('market')
  }

  const SortHeader = ({ label, sortKeyVal }: { label: string; sortKeyVal: SortKey }) => (
    <th
      onClick={() => handleSort(sortKeyVal)}
      className="px-3 py-2 text-left text-[10px] font-mono uppercase tracking-wider text-terminal-ghost cursor-pointer hover:text-terminal-text select-none whitespace-nowrap"
    >
      {label}
      {sortKey === sortKeyVal && (
        <span className="ml-1">{sortDir === 'asc' ? '\u25B2' : '\u25BC'}</span>
      )}
    </th>
  )

  if (results.length === 0) {
    return (
      <div className="text-center py-12 text-terminal-ghost font-mono text-sm">
        {skippedSymbols.length > 0
          ? `No symbols match your filters. ${skippedSymbols.length} symbols skipped due to missing data.`
          : 'No symbols match your filters. Adjust criteria and scan again.'}
      </div>
    )
  }

  return (
    <div className="overflow-x-auto">
      {skippedSymbols.length > 0 && (
        <div className="mb-2 px-3 py-1.5 bg-terminal-amber/10 border border-terminal-amber/20 rounded text-xs font-mono text-terminal-amber">
          {skippedSymbols.length} symbol{skippedSymbols.length > 1 ? 's' : ''} skipped due to missing data
        </div>
      )}
      <table className="w-full text-xs font-mono">
        <thead>
          <tr className="border-b border-terminal-border">
            <SortHeader label="Symbol" sortKeyVal="symbol" />
            <th className="px-3 py-2 text-left text-[10px] font-mono uppercase tracking-wider text-terminal-ghost">Name</th>
            <SortHeader label="Price" sortKeyVal="price" />
            <SortHeader label="Chg%" sortKeyVal="change_pct" />
            <SortHeader label="Volume" sortKeyVal="volume" />
            <SortHeader label="Mkt Cap" sortKeyVal="market_cap" />
            <th className="px-3 py-2 text-left text-[10px] font-mono uppercase tracking-wider text-terminal-ghost">Sector</th>
            {indicatorCols.map((col) => (
              <SortHeader key={col} label={col} sortKeyVal={col} />
            ))}
          </tr>
        </thead>
        <tbody>
          {sorted.map((row) => {
            const enr = enriched[row.symbol]
            const up = row.change_pct >= 0
            return (
              <tr
                key={row.symbol}
                onClick={() => handleRowClick(row)}
                className="border-b border-terminal-border/50 hover:bg-terminal-muted/50 cursor-pointer transition-colors"
              >
                <td className="px-3 py-2 font-semibold text-terminal-blue">{row.symbol}</td>
                <td className="px-3 py-2 text-terminal-dim truncate max-w-[140px]">{enr?.name ?? '-'}</td>
                <td className="px-3 py-2 text-terminal-text">{row.price.toFixed(2)}</td>
                <td className={clsx('px-3 py-2', up ? 'text-terminal-green' : 'text-terminal-red')}>
                  {up ? '+' : ''}{row.change_pct.toFixed(2)}%
                </td>
                <td className="px-3 py-2 text-terminal-dim">{formatVolume(row.volume)}</td>
                <td className="px-3 py-2 text-terminal-dim">{formatMktCap(enr?.market_cap)}</td>
                <td className="px-3 py-2 text-terminal-dim truncate max-w-[100px]">{enr?.sector ?? '-'}</td>
                {indicatorCols.map((col) => (
                  <td key={col} className="px-3 py-2 text-terminal-text">
                    {row.indicators[col]?.toFixed(2) ?? '-'}
                  </td>
                ))}
              </tr>
            )
          })}
        </tbody>
      </table>
      <div className="mt-2 text-[10px] font-mono text-terminal-ghost px-3">
        {results.length} result{results.length !== 1 ? 's' : ''}
      </div>
    </div>
  )
}

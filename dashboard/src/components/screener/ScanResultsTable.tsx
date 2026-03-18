import React, { useMemo, useState } from 'react'
import clsx from 'clsx'
import { useMarketStore, useScreenerStore, useStockProfileStore, useUIStore } from '@/store'
import type { ScanResultRow } from '@/types'

type SortKey = 'symbol' | 'price' | 'change_pct' | 'volume' | string
type SortDir = 'asc' | 'desc'

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
        active ? 'text-gray-900' : 'text-gray-400 hover:text-gray-600',
      )}
    >
      <span className="inline-flex items-center gap-1">
        {label}
        {active && (
          <svg viewBox="0 0 24 24" fill="currentColor" className="w-3 h-3 text-gray-500">
            {sortDir === 'asc' ? <path d="M7 14l5-5 5 5z" /> : <path d="M7 10l5 5 5-5z" />}
          </svg>
        )}
      </span>
    </th>
  )
}

export default function ScanResultsTable() {
  const { results, enriched, skippedSymbols } = useScreenerStore()
  const setRoute = useUIStore((s) => s.setRoute)
  const setSelectedSymbol = useMarketStore((s) => s.setSelectedSymbol)
  const setProfileSymbol = useStockProfileStore((s) => s.setSymbol)

  const [sortKey, setSortKey] = useState<SortKey>('change_pct')
  const [sortDir, setSortDir] = useState<SortDir>('desc')

  const handleSort = (key: SortKey) => {
    if (key === sortKey) {
      setSortDir((value) => (value === 'asc' ? 'desc' : 'asc'))
      return
    }
    setSortKey(key)
    setSortDir('desc')
  }

  const indicatorCols = useMemo(() => {
    const cols = new Set<string>()
    results.forEach((row) => {
      Object.keys(row.indicators).forEach((key) => cols.add(key))
    })
    return Array.from(cols).sort()
  }, [results])

  const sorted = useMemo(() => {
    const data = [...results]
    data.sort((a, b) => {
      let aVal: number
      let bVal: number

      switch (sortKey) {
        case 'symbol':
          return sortDir === 'asc' ? a.symbol.localeCompare(b.symbol) : b.symbol.localeCompare(a.symbol)
        case 'price':
          aVal = a.price
          bVal = b.price
          break
        case 'change_pct':
          aVal = a.change_pct
          bVal = b.change_pct
          break
        case 'volume':
          aVal = a.volume
          bVal = b.volume
          break
        case 'market_cap':
          aVal = enriched[a.symbol]?.market_cap ?? 0
          bVal = enriched[b.symbol]?.market_cap ?? 0
          break
        default:
          aVal = a.indicators[sortKey] ?? 0
          bVal = b.indicators[sortKey] ?? 0
      }

      return sortDir === 'asc' ? aVal - bVal : bVal - aVal
    })
    return data
  }, [enriched, results, sortDir, sortKey])

  const openMarket = (row: ScanResultRow) => {
    setSelectedSymbol(row.symbol)
    setRoute('market')
  }

  const openAnalysis = (row: ScanResultRow) => {
    setSelectedSymbol(row.symbol)
    setProfileSymbol(row.symbol)
    setRoute('stock')
  }

  if (results.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center gap-3 py-16 text-center">
        <div className="flex h-12 w-12 items-center justify-center rounded-lg border border-gray-200 bg-gray-50">
          <svg viewBox="0 0 24 24" fill="currentColor" className="w-5 h-5 text-gray-400">
            <path d="M15.5 14h-.79l-.28-.27A6.47 6.47 0 0 0 16 9.5 6.5 6.5 0 1 0 9.5 16c1.61 0 3.09-.59 4.23-1.57l.27.28v.79l5 4.99L20.49 19zM9.5 14A4.5 4.5 0 1 1 14 9.5 4.5 4.5 0 0 1 9.5 14z" />
          </svg>
        </div>
        <div>
          <p className="text-sm font-sans font-medium text-gray-600">No matches found</p>
          <p className="mt-1 text-xs font-sans text-gray-400">
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
      {skippedSymbols.length > 0 && (
        <div className="rounded-lg border border-amber-200 bg-amber-50 px-3.5 py-2.5 text-xs font-sans text-amber-700">
          {skippedSymbols.length} symbol{skippedSymbols.length > 1 ? 's' : ''} skipped due to missing data.
        </div>
      )}

      <div className="overflow-x-auto">
        <table className="table-editorial w-full min-w-[980px] text-xs">
          <thead>
            <tr className="border-b border-gray-200">
              <SortHeader label="Symbol" sortKeyVal="symbol" active={sortKey === 'symbol'} sortDir={sortDir} onClick={handleSort} />
              <th className="px-3 py-2.5 text-left text-[10px] font-sans font-semibold uppercase tracking-[0.18em] text-gray-400">Name</th>
              <SortHeader label="Price" sortKeyVal="price" active={sortKey === 'price'} sortDir={sortDir} onClick={handleSort} />
              <SortHeader label="Change" sortKeyVal="change_pct" active={sortKey === 'change_pct'} sortDir={sortDir} onClick={handleSort} />
              <SortHeader label="Volume" sortKeyVal="volume" active={sortKey === 'volume'} sortDir={sortDir} onClick={handleSort} />
              <SortHeader label="Mkt Cap" sortKeyVal="market_cap" active={sortKey === 'market_cap'} sortDir={sortDir} onClick={handleSort} />
              <th className="px-3 py-2.5 text-left text-[10px] font-sans font-semibold uppercase tracking-[0.18em] text-gray-400">Sector</th>
              {indicatorCols.map((col) => (
                <SortHeader key={col} label={col} sortKeyVal={col} active={sortKey === col} sortDir={sortDir} onClick={handleSort} />
              ))}
              <th className="px-3 py-2.5 text-left text-[10px] font-sans font-semibold uppercase tracking-[0.18em] text-gray-400">Actions</th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((row) => {
              const details = enriched[row.symbol]
              const up = row.change_pct >= 0

              return (
                <tr key={row.symbol} className="border-b border-dotted border-gray-200 hover:bg-gray-50">
                  <td className="px-3 py-3 font-mono font-bold text-gray-900">{row.symbol}</td>
                  <td className="px-3 py-3 font-sans text-gray-600">{details?.name ?? '--'}</td>
                  <td className="px-3 py-3 font-mono text-gray-800">{row.price.toFixed(2)}</td>
                  <td className="px-3 py-3">
                    <span
                      className={clsx(
                        'rounded-full px-2 py-1 text-[11px] font-mono',
                        up ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-700',
                      )}
                    >
                      {up ? '+' : ''}{row.change_pct.toFixed(2)}%
                    </span>
                  </td>
                  <td className="px-3 py-3 font-mono text-gray-600">{formatVolume(row.volume)}</td>
                  <td className="px-3 py-3 font-mono text-gray-600">{formatMktCap(details?.market_cap)}</td>
                  <td className="px-3 py-3 font-sans text-gray-600">{details?.sector ?? '--'}</td>
                  {indicatorCols.map((col) => (
                    <td key={col} className="px-3 py-3 font-mono text-gray-700">
                      {row.indicators[col]?.toFixed(2) ?? '--'}
                    </td>
                  ))}
                  <td className="px-3 py-3">
                    <div className="flex items-center gap-2">
                      <button
                        type="button"
                        onClick={() => openMarket(row)}
                        className="rounded-lg border border-gray-200 bg-white px-2.5 py-1.5 text-[11px] font-sans text-gray-700 transition-colors hover:border-gray-400 hover:text-gray-900"
                      >
                        Market
                      </button>
                      <button
                        type="button"
                        onClick={() => openAnalysis(row)}
                        className="rounded-lg border border-gray-200 bg-[#FAF8F5] px-2.5 py-1.5 text-[11px] font-sans text-gray-700 transition-colors hover:border-gray-400 hover:text-gray-900"
                      >
                        Analysis
                      </button>
                    </div>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}

import React, { useState, useMemo, useEffect } from 'react'
import clsx from 'clsx'
import type { GuruScreenerResult, GuruScreenerName } from '@/types'
import { useStockProfileStore, useMarketStore } from '@/store'
import { navigateToRoute } from '@/utils/routes'

interface Props {
  results:   Partial<Record<GuruScreenerName, GuruScreenerResult[]>>
  activeTab: GuruScreenerName
  onTabChange: (tab: GuruScreenerName) => void
}

const TABS: { key: GuruScreenerName; label: string; criteria: string }[] = [
  {
    key: 'qullamaggie',
    label: 'Qullamaggie',
    criteria: 'RS >= 97 (1W/1M/3M/6M) + MA Stack (P >= EMA10 >= SMA20-200) + ATR RS >= 50 + Price in upper 50% of 20d range',
  },
  {
    key: 'minervini',
    label: 'Minervini',
    criteria: 'Trend Template (8 criteria) + Green Candle + $1B+ Cap: Price > SMA50/150/200 aligned & rising, 30%+ above 52W low, within 25% of 52W high, RS >= 70',
  },
  {
    key: 'oneil',
    label: "O'Neil",
    criteria: 'Positive TTM EPS + Forecast earnings growth 25%+ + Positive ROE + Positive profit margin + ROE + NOPM >= 25%',
  },
]

type SortKey = 'symbol' | 'price' | 'change_pct' | 'volume' | 'rs_rank'

export default function GuruScreenerPanel({ results, activeTab, onTabChange }: Props) {
  const [sortKey, setSortKey] = useState<SortKey>('rs_rank')
  const [sortAsc, setSortAsc] = useState(false)
  const setProfileSymbol = useStockProfileStore((s) => s.setSymbol)
  const setSelectedSymbol = useMarketStore((s) => s.setSelectedSymbol)

  // Reset sort when switching tabs
  useEffect(() => {
    setSortKey('rs_rank')
    setSortAsc(false)
  }, [activeTab])

  const rows = useMemo(() => {
    const list = [...(results[activeTab] ?? [])]
    list.sort((a, b) => {
      const av = a[sortKey] ?? 0
      const bv = b[sortKey] ?? 0
      if (typeof av === 'string') return sortAsc ? av.localeCompare(bv as string) : (bv as string).localeCompare(av)
      return sortAsc ? (av as number) - (bv as number) : (bv as number) - (av as number)
    })
    return list
  }, [results, activeTab, sortKey, sortAsc])

  const toggleSort = (key: SortKey) => {
    if (sortKey === key) setSortAsc(!sortAsc)
    else { setSortKey(key); setSortAsc(false) }
  }

  const sortArrow = (key: SortKey) => sortKey === key ? (sortAsc ? ' \u25B2' : ' \u25BC') : ''

  const navigateToStock = (symbol: string) => {
    setSelectedSymbol(symbol)
    setProfileSymbol(symbol)
    navigateToRoute('stock')
  }

  return (
    <div className="card">
      <h3 className="shell-kicker mb-3">Guru-Inspired Screeners</h3>

      {/* Tabs */}
      <div className="flex gap-1 mb-4" role="tablist">
        {TABS.map((t) => (
          <button
            key={t.key}
            role="tab"
            aria-selected={activeTab === t.key}
            onClick={() => onTabChange(t.key)}
            className={clsx(
              'px-3 py-1.5 rounded-full text-xs font-medium transition-colors',
              activeTab === t.key
                ? 'bg-[var(--accent)] text-white'
                : 'bg-[var(--bg-secondary)] text-[var(--text-secondary)] hover:text-[var(--text-primary)]',
            )}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* Criteria description */}
      {TABS.find((t) => t.key === activeTab)?.criteria && (
        <p className="text-xs text-[var(--text-muted)] mb-3 leading-relaxed">
          {TABS.find((t) => t.key === activeTab)!.criteria}
        </p>
      )}

      {/* Table */}
      <div className="overflow-x-auto" role="tabpanel">
        <table className="w-full text-sm font-mono">
          <thead>
            <tr className="border-b border-[var(--border)]">
              {([
                ['symbol', 'Symbol'],
                ['price', 'Price'],
                ['change_pct', 'Chg%'],
                ['volume', 'Volume'],
                ['rs_rank', 'RS Rank'],
              ] as [SortKey, string][]).map(([key, label]) => (
                <th
                  key={key}
                  scope="col"
                  onClick={() => toggleSort(key)}
                  className={clsx(
                    'py-2 px-3 font-medium text-[var(--text-secondary)] cursor-pointer select-none whitespace-nowrap',
                    key === 'symbol' ? 'text-left' : 'text-right',
                  )}
                >
                  {label}<span aria-hidden="true">{sortArrow(key)}</span>
                </th>
              ))}
              <th scope="col" className="py-2 px-3 text-left font-medium text-[var(--text-secondary)]">Notes</th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 && (
              <tr><td colSpan={6} className="py-6 text-center text-[var(--text-muted)]">No results</td></tr>
            )}
            {rows.map((r) => (
              <tr
                key={r.symbol}
                className="border-b border-[var(--border)]/40 hover:bg-[var(--bg-hover)] cursor-pointer"
                onClick={() => navigateToStock(r.symbol)}
              >
                <td className="py-1.5 px-3 font-semibold text-[var(--accent)]">{r.symbol}</td>
                <td className="py-1.5 px-3 text-right tabular-nums">
                  {r.price != null ? `$${r.price.toFixed(2)}` : '--'}
                </td>
                <td className={clsx('py-1.5 px-3 text-right tabular-nums', (r.change_pct ?? 0) >= 0 ? 'text-[var(--success)]' : 'text-[var(--danger)]')}>
                  {r.change_pct != null ? `${r.change_pct >= 0 ? '+' : ''}${r.change_pct.toFixed(2)}%` : '--'}
                </td>
                <td className="py-1.5 px-3 text-right tabular-nums">
                  {r.volume != null ? `${(r.volume / 1e6).toFixed(1)}M` : '--'}
                </td>
                <td className="py-1.5 px-3 text-right tabular-nums font-semibold">
                  {r.rs_rank ?? '--'}
                </td>
                <td className="py-1.5 px-3 text-[var(--text-secondary)] text-xs">
                  {(r.setup_notes ?? []).join(' \u00B7 ')}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

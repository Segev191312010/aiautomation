import React from 'react'
import clsx from 'clsx'
import type { StockbeeMover, StockbeeScanName } from '@/types'

interface Props {
  results:   Partial<Record<StockbeeScanName, StockbeeMover[]>>
  activeTab: StockbeeScanName
  onTabChange: (tab: StockbeeScanName) => void
}

const TABS: { key: StockbeeScanName; label: string; description: string }[] = [
  { key: '9m_movers',    label: '9M Movers',      description: 'Volume > 50-day average AND > 9M shares traded. Captures institutional-size moves.' },
  { key: 'weekly_20pct', label: '20% Weekly',      description: 'Up or down 20%+ in 5 trading sessions. Major momentum events in either direction.' },
  { key: 'daily_4pct',   label: '4% Daily Gainers', description: 'Up 4%+ today. Momentum burst — potential start of a bigger move (Pradeep Bonde).' },
]

export default function StockbeeScans({ results, activeTab, onTabChange }: Props) {
  const rows = results[activeTab] ?? []
  const tabMeta = TABS.find((t) => t.key === activeTab)

  return (
    <div className="card">
      <h3 className="shell-kicker mb-3">Stockbee Scans</h3>

      {/* Tabs */}
      <div className="flex gap-1 mb-2" role="tablist">
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
      {tabMeta && (
        <p className="text-xs text-[var(--text-muted)] mb-3">{tabMeta.description}</p>
      )}

      {/* Table */}
      <div className="overflow-x-auto">
        <table className="w-full text-sm font-mono">
          <thead>
            <tr className="border-b border-[var(--border)]">
              <th className="text-left py-2 pr-3 text-[var(--text-secondary)] font-medium">Symbol</th>
              <th className="text-right py-2 px-3 text-[var(--text-secondary)] font-medium">Price</th>
              <th className="text-right py-2 px-3 text-[var(--text-secondary)] font-medium">Chg%</th>
              <th className="text-right py-2 px-3 text-[var(--text-secondary)] font-medium">Volume</th>
              <th className="text-right py-2 px-3 text-[var(--text-secondary)] font-medium">Avg Vol</th>
              <th className="text-right py-2 px-3 text-[var(--text-secondary)] font-medium">RelVol</th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 && (
              <tr><td colSpan={6} className="py-6 text-center text-[var(--text-muted)]">No results</td></tr>
            )}
            {rows.map((r) => {
              const relVol = r.avg_volume > 0 ? r.volume / r.avg_volume : 0
              return (
                <tr key={r.symbol} className="border-b border-[var(--border)] border-opacity-40 hover:bg-[var(--bg-hover)]">
                  <td className="py-1.5 pr-3 font-semibold text-[var(--accent)]">{r.symbol}</td>
                  <td className="py-1.5 px-3 text-right tabular-nums">${r.price.toFixed(2)}</td>
                  <td className={clsx('py-1.5 px-3 text-right tabular-nums', r.change_pct >= 0 ? 'text-[var(--success)]' : 'text-[var(--danger)]')}>
                    {r.change_pct >= 0 ? '+' : ''}{r.change_pct.toFixed(2)}%
                  </td>
                  <td className="py-1.5 px-3 text-right tabular-nums">{(r.volume / 1e6).toFixed(1)}M</td>
                  <td className="py-1.5 px-3 text-right tabular-nums text-[var(--text-secondary)]">{(r.avg_volume / 1e6).toFixed(1)}M</td>
                  <td className={clsx('py-1.5 px-3 text-right tabular-nums', relVol >= 1.5 ? 'text-[var(--success)] font-semibold' : '')}>
                    {relVol.toFixed(1)}x
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

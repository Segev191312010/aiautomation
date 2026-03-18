import { useState, useEffect } from 'react'
import type { BacktestTrade } from '@/types'

interface Props {
  trades: BacktestTrade[]
}

const PAGE_SIZE = 50

const EXIT_REASON_STYLES: Record<string, string> = {
  signal:      'bg-indigo-50 text-indigo-600 border border-indigo-100',
  stop_loss:   'bg-red-50 text-red-600 border border-red-200',
  take_profit: 'bg-green-50 text-green-600 border border-green-200',
}

const EXIT_REASON_LABELS: Record<string, string> = {
  signal:      'Signal',
  stop_loss:   'Stop',
  take_profit: 'Target',
}

export function BacktestTradeLog({ trades }: Props) {
  const [showAll, setShowAll] = useState(false)

  // Reset pagination when new results arrive
  useEffect(() => { setShowAll(false) }, [trades])

  const visible = showAll ? trades : trades.slice(0, PAGE_SIZE)
  const totalPnl = trades.reduce((sum, t) => sum + t.pnl, 0)
  const avgPnlPct = trades.length > 0
    ? trades.reduce((sum, t) => sum + t.pnl_pct, 0) / trades.length
    : 0

  const wins  = trades.filter((t) => t.pnl > 0).length
  const losses = trades.length - wins

  return (
    <div className="card rounded-2xl shadow-card p-5">
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          <div className="w-7 h-7 rounded-lg bg-gray-50 flex items-center justify-center">
            <svg className="w-3.5 h-3.5 text-gray-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2"
              />
            </svg>
          </div>
          <h3 className="text-sm font-sans font-semibold text-gray-800">
            Trade Log
          </h3>
          <span className="text-xs font-mono text-gray-400">
            ({trades.length} trades)
          </span>
        </div>

        {/* Win / Loss pill summary */}
        {trades.length > 0 && (
          <div className="flex items-center gap-1.5 text-[11px] font-mono">
            <span className="flex items-center gap-1 px-2 py-0.5 rounded-md bg-green-50 text-green-600 border border-green-200">
              <span className="w-1.5 h-1.5 rounded-full bg-green-600" />
              {wins}W
            </span>
            <span className="flex items-center gap-1 px-2 py-0.5 rounded-md bg-red-50 text-red-600 border border-red-200">
              <span className="w-1.5 h-1.5 rounded-full bg-red-600" />
              {losses}L
            </span>
          </div>
        )}
      </div>

      <div className="overflow-x-auto rounded-xl border border-gray-200">
        <table className="w-full text-xs font-mono">
          <thead>
            <tr className="bg-gray-50 text-gray-400 border-b border-gray-200">
              <th className="text-left px-3 py-2.5 font-sans font-semibold uppercase tracking-wider text-[10px] w-8">#</th>
              <th className="text-left px-3 py-2.5 font-sans font-semibold uppercase tracking-wider text-[10px]">Entry</th>
              <th className="text-left px-3 py-2.5 font-sans font-semibold uppercase tracking-wider text-[10px]">Exit</th>
              <th className="text-right px-3 py-2.5 font-sans font-semibold uppercase tracking-wider text-[10px]">Entry $</th>
              <th className="text-right px-3 py-2.5 font-sans font-semibold uppercase tracking-wider text-[10px]">Exit $</th>
              <th className="text-right px-3 py-2.5 font-sans font-semibold uppercase tracking-wider text-[10px]">Qty</th>
              <th className="text-right px-3 py-2.5 font-sans font-semibold uppercase tracking-wider text-[10px]">P&amp;L $</th>
              <th className="text-right px-3 py-2.5 font-sans font-semibold uppercase tracking-wider text-[10px]">P&amp;L %</th>
              <th className="text-right px-3 py-2.5 font-sans font-semibold uppercase tracking-wider text-[10px]">Days</th>
              <th className="text-left px-3 py-2.5 font-sans font-semibold uppercase tracking-wider text-[10px]">Reason</th>
            </tr>
          </thead>
          <tbody>
            {visible.map((t, i) => {
              const isWin = t.pnl > 0
              // Alternating base + win/loss tint
              const evenBase = i % 2 === 0 ? 'bg-white' : 'bg-[#FAF8F5]/50'
              const tintClass = isWin
                ? 'bg-green-50/40 hover:bg-green-50/80'
                : 'bg-red-50/40 hover:bg-red-50/80'

              return (
                <tr
                  key={i}
                  className={`${evenBase} ${tintClass} border-b border-gray-100 transition-colors`}
                >
                  {/* Row number */}
                  <td className="px-3 py-2 text-gray-400/60 text-[10px]">{i + 1}</td>

                  {/* Dates */}
                  <td className="px-3 py-2 text-gray-500">{formatDate(t.entry_date)}</td>
                  <td className="px-3 py-2 text-gray-500">{formatDate(t.exit_date)}</td>

                  {/* Prices */}
                  <td className="px-3 py-2 text-right text-gray-800 tabular-nums">{t.entry_price.toFixed(2)}</td>
                  <td className="px-3 py-2 text-right text-gray-800 tabular-nums">{t.exit_price.toFixed(2)}</td>

                  {/* Qty */}
                  <td className="px-3 py-2 text-right text-gray-500 tabular-nums">{t.qty}</td>

                  {/* P&L $ */}
                  <td className={`px-3 py-2 text-right font-semibold tabular-nums ${isWin ? 'text-green-600' : 'text-red-600'}`}>
                    {t.pnl >= 0 ? '+' : ''}{t.pnl.toFixed(2)}
                  </td>

                  {/* P&L % */}
                  <td className={`px-3 py-2 text-right tabular-nums ${isWin ? 'text-green-600' : 'text-red-600'}`}>
                    {t.pnl_pct >= 0 ? '+' : ''}{t.pnl_pct.toFixed(1)}%
                  </td>

                  {/* Days */}
                  <td className="px-3 py-2 text-right text-gray-400 tabular-nums">{t.duration_days.toFixed(0)}</td>

                  {/* Exit reason badge */}
                  <td className="px-3 py-2">
                    <span className={`inline-flex px-1.5 py-0.5 rounded-md text-[10px] font-sans font-medium ${
                      EXIT_REASON_STYLES[t.exit_reason] ?? 'bg-gray-50 text-gray-400 border border-gray-200'
                    }`}>
                      {EXIT_REASON_LABELS[t.exit_reason] ?? t.exit_reason}
                    </span>
                  </td>
                </tr>
              )
            })}
          </tbody>

          {/* Summary / totals row */}
          <tfoot>
            <tr className="bg-gray-50 border-t border-gray-200">
              <td colSpan={6} className="px-3 py-2.5 text-gray-400 font-sans font-semibold text-[11px] uppercase tracking-wide">
                Total ({trades.length} trades)
              </td>
              <td className={`px-3 py-2.5 text-right font-bold tabular-nums ${totalPnl >= 0 ? 'text-green-600' : 'text-red-600'}`}>
                {totalPnl >= 0 ? '+' : ''}{totalPnl.toFixed(2)}
              </td>
              <td className={`px-3 py-2.5 text-right tabular-nums font-semibold ${avgPnlPct >= 0 ? 'text-green-600' : 'text-red-600'}`}>
                avg {avgPnlPct >= 0 ? '+' : ''}{avgPnlPct.toFixed(1)}%
              </td>
              <td colSpan={2} className="px-3 py-2.5 text-gray-400 text-[11px] font-sans">
                <span className="text-green-600">{wins}W</span>
                <span className="mx-1 text-gray-400">/</span>
                <span className="text-red-600">{losses}L</span>
              </td>
            </tr>
          </tfoot>
        </table>
      </div>

      {/* Show more toggle */}
      {!showAll && trades.length > PAGE_SIZE && (
        <button
          onClick={() => setShowAll(true)}
          className="mt-3 flex items-center gap-1.5 text-xs font-sans text-indigo-600/80 hover:text-indigo-600 hover:bg-indigo-50 px-3 py-1.5 rounded-lg transition-all"
        >
          <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
          </svg>
          Show all {trades.length} trades
          <span className="text-gray-400 ml-1">({trades.length - PAGE_SIZE} more)</span>
        </button>
      )}

      {showAll && trades.length > PAGE_SIZE && (
        <button
          onClick={() => setShowAll(false)}
          className="mt-3 flex items-center gap-1.5 text-xs font-sans text-gray-400 hover:text-gray-500 hover:bg-gray-50 px-3 py-1.5 rounded-lg transition-all"
        >
          <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 15l7-7 7 7" />
          </svg>
          Show fewer
        </button>
      )}
    </div>
  )
}

function formatDate(iso: string): string {
  try {
    return new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: '2-digit' })
  } catch {
    return iso
  }
}

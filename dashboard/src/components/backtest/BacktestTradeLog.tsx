import { useState, useEffect } from 'react'
import type { BacktestTrade } from '@/types'

interface Props {
  trades: BacktestTrade[]
}

const PAGE_SIZE = 50

export function BacktestTradeLog({ trades }: Props) {
  const [showAll, setShowAll] = useState(false)

  // Reset pagination when new results arrive
  useEffect(() => { setShowAll(false) }, [trades])

  const visible = showAll ? trades : trades.slice(0, PAGE_SIZE)
  const totalPnl = trades.reduce((sum, t) => sum + t.pnl, 0)
  const avgPnlPct = trades.length > 0 ? trades.reduce((sum, t) => sum + t.pnl_pct, 0) / trades.length : 0

  return (
    <div>
      <h3 className="text-sm font-semibold text-gray-200 mb-2">
        Trade Log ({trades.length} trades)
      </h3>

      <div className="overflow-x-auto rounded border border-gray-700">
        <table className="w-full text-xs font-mono">
          <thead>
            <tr className="bg-gray-800/80 text-gray-400">
              <th className="text-left px-2 py-1.5">#</th>
              <th className="text-left px-2 py-1.5">Entry</th>
              <th className="text-left px-2 py-1.5">Exit</th>
              <th className="text-right px-2 py-1.5">Entry $</th>
              <th className="text-right px-2 py-1.5">Exit $</th>
              <th className="text-right px-2 py-1.5">Qty</th>
              <th className="text-right px-2 py-1.5">P&L $</th>
              <th className="text-right px-2 py-1.5">P&L %</th>
              <th className="text-right px-2 py-1.5">Days</th>
              <th className="text-left px-2 py-1.5">Reason</th>
            </tr>
          </thead>
          <tbody>
            {visible.map((t, i) => {
              const isWin = t.pnl > 0
              const rowBg = isWin ? 'bg-green-900/10' : 'bg-red-900/10'
              return (
                <tr key={i} className={`${rowBg} border-t border-gray-800 hover:bg-gray-800/50`}>
                  <td className="px-2 py-1 text-gray-500">{i + 1}</td>
                  <td className="px-2 py-1 text-gray-300">{formatDate(t.entry_date)}</td>
                  <td className="px-2 py-1 text-gray-300">{formatDate(t.exit_date)}</td>
                  <td className="px-2 py-1 text-right text-gray-300">{t.entry_price.toFixed(2)}</td>
                  <td className="px-2 py-1 text-right text-gray-300">{t.exit_price.toFixed(2)}</td>
                  <td className="px-2 py-1 text-right text-gray-300">{t.qty}</td>
                  <td className={`px-2 py-1 text-right ${isWin ? 'text-green-400' : 'text-red-400'}`}>
                    {t.pnl >= 0 ? '+' : ''}{t.pnl.toFixed(2)}
                  </td>
                  <td className={`px-2 py-1 text-right ${isWin ? 'text-green-400' : 'text-red-400'}`}>
                    {t.pnl_pct >= 0 ? '+' : ''}{t.pnl_pct.toFixed(1)}%
                  </td>
                  <td className="px-2 py-1 text-right text-gray-400">{t.duration_days.toFixed(0)}</td>
                  <td className="px-2 py-1 text-gray-400">
                    <span className={`px-1.5 py-0.5 rounded text-[10px] ${
                      t.exit_reason === 'signal' ? 'bg-blue-900/30 text-blue-400' :
                      t.exit_reason === 'stop_loss' ? 'bg-red-900/30 text-red-400' :
                      t.exit_reason === 'take_profit' ? 'bg-green-900/30 text-green-400' :
                      'bg-gray-700/30 text-gray-400'
                    }`}>
                      {t.exit_reason}
                    </span>
                  </td>
                </tr>
              )
            })}
          </tbody>
          {/* Summary row */}
          <tfoot>
            <tr className="bg-gray-800/60 border-t border-gray-600 font-semibold">
              <td colSpan={6} className="px-2 py-1.5 text-gray-300">Total</td>
              <td className={`px-2 py-1.5 text-right ${totalPnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                {totalPnl >= 0 ? '+' : ''}{totalPnl.toFixed(2)}
              </td>
              <td className={`px-2 py-1.5 text-right ${avgPnlPct >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                avg {avgPnlPct >= 0 ? '+' : ''}{avgPnlPct.toFixed(1)}%
              </td>
              <td colSpan={2} className="px-2 py-1.5 text-gray-400">
                {trades.length} trades
              </td>
            </tr>
          </tfoot>
        </table>
      </div>

      {!showAll && trades.length > PAGE_SIZE && (
        <button
          onClick={() => setShowAll(true)}
          className="mt-2 text-xs text-blue-400 hover:text-blue-300"
        >
          Show all {trades.length} trades
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

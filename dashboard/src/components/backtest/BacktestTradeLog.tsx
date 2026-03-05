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
    <div className="glass rounded-2xl shadow-glass p-5">
      <h3 className="text-sm font-sans font-medium text-terminal-dim mb-4">
        Trade Log <span className="font-mono text-terminal-ghost">({trades.length} trades)</span>
      </h3>

      <div className="overflow-x-auto rounded-xl border border-white/[0.06]">
        <table className="w-full text-xs font-mono">
          <thead>
            <tr className="bg-terminal-elevated/80 text-terminal-dim">
              <th className="text-left px-3 py-2">#</th>
              <th className="text-left px-3 py-2">Entry</th>
              <th className="text-left px-3 py-2">Exit</th>
              <th className="text-right px-3 py-2">Entry $</th>
              <th className="text-right px-3 py-2">Exit $</th>
              <th className="text-right px-3 py-2">Qty</th>
              <th className="text-right px-3 py-2">P&amp;L $</th>
              <th className="text-right px-3 py-2">P&amp;L %</th>
              <th className="text-right px-3 py-2">Days</th>
              <th className="text-left px-3 py-2">Reason</th>
            </tr>
          </thead>
          <tbody>
            {visible.map((t, i) => {
              const isWin = t.pnl > 0
              const rowBg = isWin ? 'bg-terminal-green/5' : 'bg-terminal-red/5'
              return (
                <tr key={i} className={`${rowBg} border-t border-white/[0.04] hover:bg-terminal-elevated/40 transition-colors`}>
                  <td className="px-3 py-1.5 text-terminal-ghost">{i + 1}</td>
                  <td className="px-3 py-1.5 text-terminal-text">{formatDate(t.entry_date)}</td>
                  <td className="px-3 py-1.5 text-terminal-text">{formatDate(t.exit_date)}</td>
                  <td className="px-3 py-1.5 text-right text-terminal-text">{t.entry_price.toFixed(2)}</td>
                  <td className="px-3 py-1.5 text-right text-terminal-text">{t.exit_price.toFixed(2)}</td>
                  <td className="px-3 py-1.5 text-right text-terminal-text">{t.qty}</td>
                  <td className={`px-3 py-1.5 text-right ${isWin ? 'text-terminal-green' : 'text-terminal-red'}`}>
                    {t.pnl >= 0 ? '+' : ''}{t.pnl.toFixed(2)}
                  </td>
                  <td className={`px-3 py-1.5 text-right ${isWin ? 'text-terminal-green' : 'text-terminal-red'}`}>
                    {t.pnl_pct >= 0 ? '+' : ''}{t.pnl_pct.toFixed(1)}%
                  </td>
                  <td className="px-3 py-1.5 text-right text-terminal-dim">{t.duration_days.toFixed(0)}</td>
                  <td className="px-3 py-1.5 text-terminal-dim">
                    <span className={`px-1.5 py-0.5 rounded-md text-[10px] font-sans ${
                      t.exit_reason === 'signal'      ? 'bg-terminal-blue/20 text-terminal-blue' :
                      t.exit_reason === 'stop_loss'   ? 'bg-terminal-red/20 text-terminal-red' :
                      t.exit_reason === 'take_profit' ? 'bg-terminal-green/20 text-terminal-green' :
                      'bg-terminal-elevated text-terminal-ghost'
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
            <tr className="bg-terminal-elevated/60 border-t border-white/[0.08] font-semibold">
              <td colSpan={6} className="px-3 py-2 text-terminal-dim font-sans">Total</td>
              <td className={`px-3 py-2 text-right ${totalPnl >= 0 ? 'text-terminal-green' : 'text-terminal-red'}`}>
                {totalPnl >= 0 ? '+' : ''}{totalPnl.toFixed(2)}
              </td>
              <td className={`px-3 py-2 text-right ${avgPnlPct >= 0 ? 'text-terminal-green' : 'text-terminal-red'}`}>
                avg {avgPnlPct >= 0 ? '+' : ''}{avgPnlPct.toFixed(1)}%
              </td>
              <td colSpan={2} className="px-3 py-2 text-terminal-ghost">
                {trades.length} trades
              </td>
            </tr>
          </tfoot>
        </table>
      </div>

      {!showAll && trades.length > PAGE_SIZE && (
        <button
          onClick={() => setShowAll(true)}
          className="mt-3 text-xs font-sans text-terminal-blue/80 hover:text-terminal-blue transition-colors"
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

import React from 'react'
import clsx from 'clsx'
import { fmtUSD, fmtDate } from '@/utils/formatters'
import type { TradeHistoryRow } from '@/types'

interface TradeHistoryPanelProps {
  trades: TradeHistoryRow[]
}

export function TradeHistoryPanel({ trades }: TradeHistoryPanelProps) {
  const closed = trades.filter((t) => t.pnl !== undefined)
  const wins   = closed.filter((t) => (t.pnl ?? 0) > 0)
  const losses = closed.filter((t) => (t.pnl ?? 0) <= 0)
  const winPct = closed.length > 0 ? (wins.length / closed.length) * 100 : 0

  const best  = closed.reduce<TradeHistoryRow | null>((b, t) => !b || (t.pnl ?? 0) > (b.pnl ?? 0) ? t : b, null)
  const worst = closed.reduce<TradeHistoryRow | null>((b, t) => !b || (t.pnl ?? 0) < (b.pnl ?? 0) ? t : b, null)
  const withHold = closed.filter((t) => t.holding_days != null)
  const avgHoldDays = withHold.length > 0
    ? withHold.reduce((s, t) => s + (t.holding_days ?? 0), 0) / withHold.length
    : null

  return (
    <div className="flex flex-col gap-5">
      {/* Win/loss distribution bar */}
      <div>
        <div className="flex items-center justify-between mb-1.5">
          <span className="text-[10px] font-sans uppercase tracking-widest text-zinc-500">Win / Loss Distribution</span>
          <span className="text-[11px] font-mono text-zinc-400">{closed.length} closed trades</span>
        </div>
        <div className="h-5 rounded-full overflow-hidden flex bg-zinc-800">
          {closed.length > 0 && (
            <>
              <div
                className="h-full bg-emerald-500 flex items-center justify-center transition-all duration-700"
                style={{ width: `${winPct}%` }}
              >
                {winPct >= 20 && <span className="text-[9px] font-mono font-bold text-white">{wins.length}W</span>}
              </div>
              <div
                className="h-full bg-red-500 flex items-center justify-center transition-all duration-700"
                style={{ width: `${100 - winPct}%` }}
              >
                {(100 - winPct) >= 20 && <span className="text-[9px] font-mono font-bold text-white">{losses.length}L</span>}
              </div>
            </>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-4 mt-1.5">
          <span className="text-[11px] font-mono text-emerald-600">{winPct.toFixed(1)}% win rate</span>
          {avgHoldDays !== null && (
            <span className="text-[11px] font-mono text-zinc-400">Avg hold: {avgHoldDays.toFixed(1)} days</span>
          )}
          {best && (
            <span className="text-[11px] font-mono text-emerald-600 ml-auto">
              Best: {fmtUSD(best.pnl ?? 0)} ({best.symbol})
            </span>
          )}
          {worst && (
            <span className="text-[11px] font-mono text-red-400">
              Worst: {fmtUSD(worst.pnl ?? 0)} ({worst.symbol})
            </span>
          )}
        </div>
      </div>

      {/* Recent trades table */}
      <div>
        <div className="text-[10px] font-sans uppercase tracking-widest text-zinc-500 mb-2">Recent Trades</div>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[500px]">
            <thead>
              <tr className="border-b border-zinc-800">
                {['Time', 'Symbol', 'Side', 'Qty', 'Fill Price', 'P&L', 'Hold'].map((c, i) => (
                  <th key={c} className={clsx('py-2 px-2 text-[10px] font-sans uppercase tracking-widest text-zinc-500', i < 3 ? 'text-left' : 'text-right')}>
                    {c}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {trades.slice(0, 20).map((t) => {
                const isBuy = t.action === 'BUY'
                return (
                  <tr key={t.id} className={clsx('border-b border-zinc-800 transition-colors', isBuy ? 'hover:bg-emerald-500/[0.03]' : 'hover:bg-red-500/[0.03]')}>
                    <td className="py-2 px-2 font-mono text-[11px] text-zinc-500 whitespace-nowrap">{fmtDate(t.timestamp)}</td>
                    <td className="py-2 px-2 font-mono text-sm font-semibold text-zinc-100">{t.symbol}</td>
                    <td className="py-2 px-2">
                      <span className={clsx(
                        'inline-flex items-center gap-1 px-1.5 py-0.5 rounded-lg border text-[10px] font-mono font-semibold',
                        isBuy ? 'text-emerald-600 bg-emerald-50 border-emerald-200' : 'text-red-400 bg-red-500/10 border-red-200',
                      )}>
                        <span className={clsx('w-1 h-1 rounded-full', isBuy ? 'bg-emerald-500' : 'bg-red-500')} />
                        {t.action}
                      </span>
                    </td>
                    <td className="py-2 px-2 font-mono text-sm text-zinc-400 tabular-nums text-right">{t.quantity.toLocaleString('en-US')}</td>
                    <td className="py-2 px-2 font-mono text-sm text-zinc-400 tabular-nums text-right">{fmtUSD(t.fill_price)}</td>
                    <td className="py-2 px-2 font-mono text-sm tabular-nums text-right">
                      {t.pnl !== undefined
                        ? <span className={t.pnl >= 0 ? 'text-emerald-600' : 'text-red-400'}>{t.pnl >= 0 ? '+' : ''}{fmtUSD(t.pnl)}</span>
                        : <span className="text-zinc-500">—</span>}
                    </td>
                    <td className="py-2 px-2 font-mono text-[11px] text-zinc-500 text-right">
                      {t.holding_days !== undefined ? `${t.holding_days}d` : '—'}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}

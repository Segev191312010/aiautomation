/**
 * PositionsTable — shows open positions with live P&L coloring.
 * Works for both live IBKR positions and simulation positions.
 */
import React from 'react'
import clsx from 'clsx'
import { useAccountStore, useBotStore } from '@/store'
import { fmtUSD } from '@/utils/formatters'
import type { Position, SimPosition } from '@/types'

function isSimPos(p: Position | SimPosition): p is SimPosition {
  return 'pnl_pct' in p
}

interface RowProps {
  pos: Position | SimPosition
}

function PositionRow({ pos }: RowProps) {
  const pnl    = isSimPos(pos) ? pos.unrealized_pnl : pos.unrealized_pnl
  const price  = isSimPos(pos) ? pos.current_price  : pos.market_price
  const value  = isSimPos(pos) ? pos.market_value   : pos.market_value
  const pnlPct = isSimPos(pos) ? pos.pnl_pct        : (pos.market_price - pos.avg_cost) / pos.avg_cost * 100
  const up = pnl >= 0

  return (
    <tr className="border-b border-terminal-border hover:bg-terminal-muted/30 transition-colors">
      <td className="py-2 px-3 font-mono text-sm text-terminal-text font-semibold">{pos.symbol}</td>
      <td className="py-2 px-3 font-mono text-sm text-terminal-dim tabular-nums text-right">{pos.qty}</td>
      <td className="py-2 px-3 font-mono text-sm text-terminal-dim tabular-nums text-right">
        {fmtUSD(pos.avg_cost)}
      </td>
      <td className="py-2 px-3 font-mono text-sm text-terminal-text tabular-nums text-right">
        {fmtUSD(price)}
      </td>
      <td className="py-2 px-3 font-mono text-sm tabular-nums text-right">
        <span className={up ? 'text-terminal-green' : 'text-terminal-red'}>
          {fmtUSD(value)}
        </span>
      </td>
      <td className="py-2 px-3 font-mono text-sm tabular-nums text-right">
        <div className={clsx('flex flex-col items-end', up ? 'text-terminal-green' : 'text-terminal-red')}>
          <span>{up ? '+' : ''}{fmtUSD(pnl)}</span>
          <span className="text-[10px] opacity-80">
            {up ? '+' : ''}{pnlPct.toFixed(2)}%
          </span>
        </div>
      </td>
    </tr>
  )
}

export default function PositionsTable() {
  const { positions, loading } = useAccountStore()

  if (loading) {
    return (
      <div className="animate-pulse space-y-2">
        {[...Array(3)].map((_, i) => (
          <div key={i} className="h-10 bg-terminal-muted rounded" />
        ))}
      </div>
    )
  }

  if (positions.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-12 text-terminal-ghost">
        <svg viewBox="0 0 24 24" fill="currentColor" className="w-10 h-10 mb-2 opacity-30">
          <path d="M11.8 10.9c-2.27-.59-3-1.2-3-2.15 0-1.09 1.01-1.85 2.7-1.85 1.78 0 2.44.85 2.5 2.1h2.21c-.07-1.72-1.12-3.3-3.21-3.81V3h-3v2.16c-1.94.42-3.5 1.68-3.5 3.61 0 2.31 1.91 3.46 4.7 4.13 2.5.6 3 1.48 3 2.41 0 .69-.49 1.79-2.7 1.79-2.06 0-2.87-.92-2.98-2.1h-2.2c.12 2.19 1.76 3.42 3.68 3.83V21h3v-2.15c1.95-.37 3.5-1.5 3.5-3.55 0-2.84-2.43-3.81-4.7-4.4z" />
        </svg>
        <p className="text-sm font-mono">No open positions</p>
      </div>
    )
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[560px]">
        <thead>
          <tr className="border-b border-terminal-border">
            {['Symbol', 'Qty', 'Avg Cost', 'Mkt Price', 'Value', 'Unrealized P&L'].map((col) => (
              <th
                key={col}
                className="py-2 px-3 text-[10px] font-mono uppercase tracking-widest text-terminal-ghost font-normal text-right first:text-left"
              >
                {col}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {positions.map((p) => (
            <PositionRow key={p.symbol} pos={p} />
          ))}
        </tbody>
        <tfoot>
          <tr className="border-t border-terminal-muted">
            <td colSpan={4} className="py-2 px-3 text-[10px] font-mono text-terminal-ghost">
              TOTAL
            </td>
            <td className="py-2 px-3 font-mono text-sm text-terminal-text tabular-nums text-right">
              {fmtUSD(positions.reduce((s, p) => s + (isSimPos(p) ? p.market_value : p.market_value), 0))}
            </td>
            <td className="py-2 px-3 font-mono text-sm tabular-nums text-right">
              {(() => {
                const total = positions.reduce((s, p) => s + p.unrealized_pnl, 0)
                return (
                  <span className={total >= 0 ? 'text-terminal-green' : 'text-terminal-red'}>
                    {total >= 0 ? '+' : ''}{fmtUSD(total)}
                  </span>
                )
              })()}
            </td>
          </tr>
        </tfoot>
      </table>
    </div>
  )
}

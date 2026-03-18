/**
 * PositionsTable — live positions with P&L, SL/TP brackets, % of account.
 * Fetches bracket data from /api/positions/brackets for live mode.
 */
import React, { useState, useEffect, useCallback } from 'react'
import clsx from 'clsx'
import { useAccountStore, useBotStore } from '@/store'
import { useToast } from '@/components/ui/ToastProvider'
import type { Position, SimPosition } from '@/types'

function fmtUSD(n: number): string {
  return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(n)
}

function isSimPos(p: any): p is SimPosition {
  return 'pnl_pct' in p && 'current_price' in p
}

interface BracketInfo {
  sl_order?: { order_id: number; price: number; status: string } | null
  tp_order?: { order_id: number; price: number; status: string } | null
  pnl_pct?: number
  pct_of_account?: number
}

type EnrichedPosition = (Position | SimPosition) & BracketInfo

function EditablePrice({ value, onSave, color }: {
  value: number | null | undefined; onSave: (v: number) => void; color: string
}) {
  const [editing, setEditing] = useState(false)
  const [input, setInput] = useState('')

  if (!value && value !== 0) return <span className="text-zinc-600">—</span>

  if (editing) {
    return (
      <input
        autoFocus
        type="number"
        step="0.01"
        value={input}
        onChange={e => setInput(e.target.value)}
        onBlur={() => { if (+input > 0) onSave(+input); setEditing(false) }}
        onKeyDown={e => {
          if (e.key === 'Enter' && +input > 0) { onSave(+input); setEditing(false) }
          if (e.key === 'Escape') setEditing(false)
        }}
        className="w-20 px-1 py-0.5 bg-zinc-800 border border-zinc-600 rounded text-xs text-zinc-200 text-right"
      />
    )
  }

  return (
    <button
      onClick={() => { setInput(value.toFixed(2)); setEditing(true) }}
      className={`text-xs font-mono tabular-nums hover:underline cursor-pointer ${color}`}
      title="Click to edit"
    >
      ${value.toFixed(2)}
    </button>
  )
}

function PositionRow({ pos, onModifyOrder }: {
  pos: EnrichedPosition
  onModifyOrder: (orderId: number, price: number) => void
}) {
  const price = isSimPos(pos) ? pos.current_price : pos.market_price
  const pnl = pos.unrealized_pnl
  const pnlPct = (pos as any).pnl_pct ?? (price && pos.avg_cost ? ((price / pos.avg_cost - 1) * 100) : 0)
  const up = pnl >= 0
  const slOrder = (pos as any).sl_order
  const tpOrder = (pos as any).tp_order
  const pctAcct = (pos as any).pct_of_account

  return (
    <tr className="border-b border-zinc-800/50 hover:bg-zinc-800/20 transition-colors">
      <td className="py-2 px-2 font-mono text-sm text-zinc-100 font-semibold">{pos.symbol}</td>
      <td className="py-2 px-2 font-mono text-xs text-zinc-400 tabular-nums text-right">{pos.qty}</td>
      <td className="py-2 px-2 font-mono text-xs text-zinc-500 tabular-nums text-right">{fmtUSD(pos.avg_cost)}</td>
      <td className="py-2 px-2 font-mono text-xs text-zinc-200 tabular-nums text-right">{fmtUSD(price)}</td>
      <td className={clsx('py-2 px-2 font-mono text-xs tabular-nums text-right font-medium', up ? 'text-emerald-400' : 'text-red-400')}>
        {up ? '+' : ''}{fmtUSD(pnl)}
        <div className="text-[10px] opacity-70">{up ? '+' : ''}{pnlPct.toFixed(2)}%</div>
      </td>
      <td className="py-2 px-2 text-right">
        {slOrder ? (
          <EditablePrice
            value={slOrder.price}
            onSave={(v) => onModifyOrder(slOrder.order_id, v)}
            color="text-red-400"
          />
        ) : <span className="text-zinc-700 text-xs">—</span>}
      </td>
      <td className="py-2 px-2 text-right">
        {tpOrder ? (
          <EditablePrice
            value={tpOrder.price}
            onSave={(v) => onModifyOrder(tpOrder.order_id, v)}
            color="text-emerald-400"
          />
        ) : <span className="text-zinc-700 text-xs">—</span>}
      </td>
      <td className="py-2 px-2 text-right">
        {pctAcct != null ? (
          <span className="text-[10px] px-1.5 py-0.5 rounded bg-zinc-800 text-zinc-400">
            {pctAcct.toFixed(1)}%
          </span>
        ) : <span className="text-zinc-700 text-xs">—</span>}
      </td>
    </tr>
  )
}

export default function PositionsTable() {
  const { positions, account } = useAccountStore()
  const simMode = useBotStore((s) => s.simMode)
  const [enriched, setEnriched] = useState<EnrichedPosition[]>([])
  const toast = useToast()

  // Fetch bracket data for live positions
  const fetchBrackets = useCallback(async () => {
    if (simMode || positions.length === 0) {
      setEnriched(positions as EnrichedPosition[])
      return
    }
    try {
      const res = await fetch('/api/positions/brackets')
      if (res.ok) {
        const data = await res.json()
        setEnriched(data)
      } else {
        setEnriched(positions as EnrichedPosition[])
      }
    } catch {
      setEnriched(positions as EnrichedPosition[])
    }
  }, [positions, simMode])

  useEffect(() => { fetchBrackets() }, [fetchBrackets])

  const handleModifyOrder = async (orderId: number, newPrice: number) => {
    try {
      const res = await fetch(`/api/orders/${orderId}/modify?price=${newPrice}`, { method: 'PUT' })
      if (res.ok) {
        toast.success(`Order ${orderId} modified to $${newPrice.toFixed(2)}`)
        setTimeout(fetchBrackets, 1000) // refresh after IBKR processes
      } else {
        const data = await res.json()
        toast.error(data.detail || 'Failed to modify order')
      }
    } catch {
      toast.error('Failed to modify order')
    }
  }

  if (positions.length === 0 && enriched.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-10 text-zinc-600">
        <p className="text-sm">No open positions</p>
      </div>
    )
  }

  const totalValue = enriched.reduce((s, p) => s + (isSimPos(p) ? p.market_value : p.market_value), 0)
  const totalPnl = enriched.reduce((s, p) => s + p.unrealized_pnl, 0)

  const cols = ['Symbol', 'Qty', 'Avg Cost', 'Price', 'P&L', 'SL', 'TP', '% Acct']

  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[700px]">
        <thead>
          <tr className="border-b border-zinc-800">
            {cols.map((col) => (
              <th key={col} className="py-2 px-2 text-[10px] font-mono uppercase tracking-widest text-zinc-500 font-normal text-right first:text-left">
                {col}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {enriched.map((p) => (
            <PositionRow key={p.symbol} pos={p} onModifyOrder={handleModifyOrder} />
          ))}
        </tbody>
        <tfoot>
          <tr className="border-t border-zinc-700">
            <td colSpan={3} className="py-2 px-2 text-[10px] font-mono text-zinc-500">
              TOTAL ({enriched.length} positions)
            </td>
            <td className="py-2 px-2 font-mono text-xs text-zinc-300 tabular-nums text-right">
              {fmtUSD(totalValue)}
            </td>
            <td className={clsx('py-2 px-2 font-mono text-xs tabular-nums text-right font-medium',
              totalPnl >= 0 ? 'text-emerald-400' : 'text-red-400')}>
              {totalPnl >= 0 ? '+' : ''}{fmtUSD(totalPnl)}
            </td>
            <td colSpan={3} />
          </tr>
        </tfoot>
      </table>
    </div>
  )
}

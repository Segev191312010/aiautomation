import { useState } from 'react'
import { fmtUSD } from '@/utils/formatters'

interface PosSummary {
  symbol: string
  entry_date: string | null
  hold_time_days: number
  qty: number
  avg_cost: number
  current_price: number
  pnl: number
  pnl_pct: number
  rule_trigger: string
  sl_price: number | null
  tp_price: number | null
  pct_of_account: number
}

export function EODSummary() {
  const [open, setOpen] = useState(false)
  const [data, setData] = useState<PosSummary[]>([])
  const [loading, setLoading] = useState(false)

  const load = async () => {
    setLoading(true)
    try {
      const res = await fetch('/api/positions/summary')
      const json = await res.json()
      setData(json.positions_summary || [])
    } catch { /* ignore */ }
    setLoading(false)
  }

  return (
    <div className="mb-4">
      <button
        onClick={() => { setOpen(!open); if (!open) load() }}
        className="flex items-center gap-2 text-xs font-mono text-zinc-400 hover:text-zinc-200 px-3 py-2 bg-zinc-900/80 border border-zinc-800 rounded-xl w-full"
      >
        <span>{open ? '▾' : '▸'}</span>
        <span>End-of-Day Position Summary</span>
        {data.length > 0 && <span className="ml-auto text-zinc-600">{data.length} positions</span>}
      </button>
      {open && (
        <div className="mt-2 bg-zinc-900/60 border border-zinc-800 rounded-xl p-4 space-y-3">
          {loading ? (
            <div className="text-zinc-500 text-xs animate-pulse">Loading summary...</div>
          ) : data.length === 0 ? (
            <div className="text-zinc-600 text-xs">No positions to summarize</div>
          ) : data.map(pos => (
            <div key={pos.symbol} className="grid grid-cols-4 gap-2 text-xs border-b border-zinc-800/50 pb-2">
              <div>
                <span className="font-semibold text-zinc-100">{pos.symbol}</span>
                <span className="text-zinc-500 ml-2">{pos.hold_time_days}d</span>
              </div>
              <div className={pos.pnl >= 0 ? 'text-emerald-400' : 'text-red-400'}>
                {pos.pnl >= 0 ? '+' : ''}{fmtUSD(pos.pnl)} ({pos.pnl_pct > 0 ? '+' : ''}{pos.pnl_pct.toFixed(1)}%)
              </div>
              <div className="text-zinc-500">
                In: ${pos.avg_cost.toFixed(2)} → ${pos.current_price.toFixed(2)}
              </div>
              <div className="text-zinc-500">
                <span className="text-blue-400">{pos.rule_trigger}</span>
                {pos.sl_price && <span className="ml-2 text-red-400">SL ${pos.sl_price.toFixed(2)}</span>}
                {pos.tp_price && <span className="ml-1 text-emerald-400">TP ${pos.tp_price.toFixed(2)}</span>}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

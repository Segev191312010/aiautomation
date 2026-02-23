/**
 * SimulationPage — combines the Replay controller, a live-updating chart,
 * the sim account KPIs, and order history.
 */
import React, { useEffect } from 'react'
import SimController from '@/components/simulation/SimController'
import TradingChart from '@/components/chart/TradingChart'
import KPICard from '@/components/tradebot/KPICard'
import { useSimStore, useMarketStore } from '@/store'
import { fetchSimAccount, fetchSimPositions, fetchSimOrders, resetSimAccount } from '@/services/api'

function fmtUSD(v: number) {
  return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(v)
}

export default function SimulationPage() {
  const { simAccount, simPositions, simOrders, playback, setSimAccount, setSimPositions, setSimOrders } = useSimStore()
  const replaySymbol = playback.symbol || 'AAPL'

  useEffect(() => {
    const load = async () => {
      try {
        const [acc, pos, orders] = await Promise.all([
          fetchSimAccount(),
          fetchSimPositions(),
          fetchSimOrders(50),
        ])
        setSimAccount(acc)
        setSimPositions(pos)
        setSimOrders(orders)
      } catch { /* ignore */ }
    }
    load()
    const t = setInterval(load, 5_000)
    return () => clearInterval(t)
  }, [setSimAccount, setSimPositions, setSimOrders])

  const handleReset = async () => {
    if (!window.confirm('Reset the simulation account? All virtual positions and orders will be cleared.')) return
    try {
      await resetSimAccount()
      const [acc, pos] = await Promise.all([fetchSimAccount(), fetchSimPositions()])
      setSimAccount(acc)
      setSimPositions(pos)
      setSimOrders([])
    } catch (e) {
      console.error(e)
    }
  }

  return (
    <div className="flex flex-col gap-4 h-full pb-20">
      {/* ── KPIs ─────────────────────────────────────────────────── */}
      <section>
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-[10px] font-mono text-terminal-ghost uppercase tracking-widest">
            Virtual Account
          </h2>
          <button
            onClick={handleReset}
            className="text-[10px] font-mono px-3 py-1 rounded border border-terminal-red/30 text-terminal-red hover:bg-terminal-red/5 transition-colors"
          >
            Reset Account
          </button>
        </div>
        <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
          <KPICard
            label="Net Liquidation"
            value={simAccount ? fmtUSD(simAccount.net_liquidation) : '—'}
            highlight
          />
          <KPICard label="Cash" value={simAccount ? fmtUSD(simAccount.cash) : '—'} />
          <KPICard
            label="Unrealized P&L"
            value={simAccount ? fmtUSD(simAccount.unrealized_pnl) : '—'}
            positive={simAccount ? simAccount.unrealized_pnl >= 0 : undefined}
          />
          <KPICard
            label="Realized P&L"
            value={simAccount ? fmtUSD(simAccount.realized_pnl) : '—'}
            positive={simAccount ? simAccount.realized_pnl >= 0 : undefined}
          />
          <KPICard
            label="Total Return"
            value={simAccount ? simAccount.total_return_pct.toFixed(2) : '—'}
            suffix="%"
            positive={simAccount ? simAccount.total_return_pct >= 0 : undefined}
          />
        </div>
      </section>

      {/* ── Chart (replay symbol) ──────────────────────────────── */}
      <section className="flex-1 min-h-0 bg-terminal-surface border border-terminal-border rounded-lg overflow-hidden flex flex-col">
        <div className="flex items-center gap-2 px-4 py-2.5 border-b border-terminal-border">
          <span className="font-mono font-bold text-terminal-text">{replaySymbol}</span>
          {playback.active && (
            <span className="text-[10px] font-mono text-terminal-amber animate-pulse-slow">
              ● REPLAY
            </span>
          )}
          <span className="ml-auto text-[10px] font-mono text-terminal-ghost">
            {playback.total_bars > 0 && `${playback.current_index + 1}/${playback.total_bars} bars`}
          </span>
        </div>
        <div className="flex-1 min-h-0">
          <TradingChart symbol={replaySymbol} className="h-full" />
        </div>
      </section>

      {/* ── Positions + Orders ────────────────────────────────── */}
      <section className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {/* Sim positions */}
        <div className="bg-terminal-surface border border-terminal-border rounded-lg p-4">
          <h3 className="text-[10px] font-mono text-terminal-ghost uppercase tracking-widest mb-3">
            Virtual Positions
          </h3>
          {simPositions.length === 0 ? (
            <p className="text-xs font-mono text-terminal-ghost text-center py-4">No positions</p>
          ) : (
            <table className="w-full text-xs font-mono">
              <thead>
                <tr className="border-b border-terminal-border">
                  {['Symbol', 'Qty', 'Avg Cost', 'Price', 'P&L'].map((c) => (
                    <th key={c} className="py-1 px-2 text-terminal-ghost font-normal text-right first:text-left">{c}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {simPositions.map((p) => (
                  <tr key={p.symbol} className="border-b border-terminal-border/50">
                    <td className="py-1.5 px-2 text-terminal-text font-semibold">{p.symbol}</td>
                    <td className="py-1.5 px-2 text-terminal-dim tabular-nums text-right">{p.qty}</td>
                    <td className="py-1.5 px-2 text-terminal-dim tabular-nums text-right">{fmtUSD(p.avg_cost)}</td>
                    <td className="py-1.5 px-2 text-terminal-text tabular-nums text-right">{fmtUSD(p.current_price)}</td>
                    <td className={`py-1.5 px-2 tabular-nums text-right ${p.unrealized_pnl >= 0 ? 'text-terminal-green' : 'text-terminal-red'}`}>
                      {p.unrealized_pnl >= 0 ? '+' : ''}{fmtUSD(p.unrealized_pnl)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        {/* Sim orders */}
        <div className="bg-terminal-surface border border-terminal-border rounded-lg p-4">
          <h3 className="text-[10px] font-mono text-terminal-ghost uppercase tracking-widest mb-3">
            Order History
          </h3>
          {simOrders.length === 0 ? (
            <p className="text-xs font-mono text-terminal-ghost text-center py-4">No orders yet</p>
          ) : (
            <div className="overflow-y-auto max-h-48">
              <table className="w-full text-xs font-mono">
                <thead>
                  <tr className="border-b border-terminal-border">
                    {['Time', 'Symbol', 'Side', 'Qty', 'Price', 'P&L'].map((c) => (
                      <th key={c} className="py-1 px-2 text-terminal-ghost font-normal text-right first:text-left">{c}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {simOrders.map((o) => (
                    <tr key={o.id} className="border-b border-terminal-border/50">
                      <td className="py-1 px-2 text-terminal-ghost tabular-nums">
                        {new Date(o.timestamp).toLocaleTimeString()}
                      </td>
                      <td className="py-1 px-2 text-terminal-text font-semibold">{o.symbol}</td>
                      <td className={`py-1 px-2 font-semibold ${o.action === 'BUY' ? 'text-terminal-green' : 'text-terminal-red'}`}>
                        {o.action}
                      </td>
                      <td className="py-1 px-2 text-terminal-dim tabular-nums text-right">{o.qty}</td>
                      <td className="py-1 px-2 text-terminal-dim tabular-nums text-right">{fmtUSD(o.price)}</td>
                      <td className={`py-1 px-2 tabular-nums text-right ${(o.pnl ?? 0) >= 0 ? 'text-terminal-green' : 'text-terminal-red'}`}>
                        {o.pnl != null ? `${o.pnl >= 0 ? '+' : ''}${fmtUSD(o.pnl)}` : '—'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </section>

      {/* ── Sticky replay controller ──────────────────────────── */}
      <SimController />
    </div>
  )
}

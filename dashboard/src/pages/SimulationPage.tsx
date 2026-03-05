/**
 * SimulationPage — combines the Replay controller, a live-updating chart,
 * the sim account KPIs, and order history.
 */
import React, { useEffect, useState } from 'react'
import SimController from '@/components/simulation/SimController'
import TradingChart from '@/components/chart/TradingChart'
import KPICard from '@/components/tradebot/KPICard'
import { SkeletonCard } from '@/components/ui/Skeleton'
import { useToast } from '@/components/ui/ToastProvider'
import { useSimStore, useMarketStore } from '@/store'
import { fetchSimAccount, fetchSimPositions, fetchSimOrders, resetSimAccount } from '@/services/api'

function fmtUSD(v: number) {
  return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(v)
}

export default function SimulationPage() {
  const toast = useToast()
  const { simAccount, simPositions, simOrders, playback, setSimAccount, setSimPositions, setSimOrders } = useSimStore()
  const replaySymbol = playback.symbol || 'AAPL'
  const [initialLoad, setInitialLoad] = useState(true)

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
      setInitialLoad(false)
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
      toast.success('Simulation account reset')
    } catch (e) {
      toast.error('Failed to reset simulation')
      console.error(e)
    }
  }

  return (
    <div className="flex flex-col gap-4 h-full pb-20">
      {/* ── KPIs ─────────────────────────────────────────────────── */}
      <section>
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-xs font-sans font-medium text-terminal-dim tracking-wide">
            Virtual Account
          </h2>
          <button
            onClick={handleReset}
            className="text-xs font-sans font-medium px-3 py-1 rounded-xl border border-red-500/25 text-red-400 hover:bg-red-500/10 transition-colors"
          >
            Reset Account
          </button>
        </div>
        {initialLoad && !simAccount ? (
          <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
            <SkeletonCard /><SkeletonCard /><SkeletonCard /><SkeletonCard /><SkeletonCard />
          </div>
        ) : (
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
        )}
      </section>

      {/* ── Chart (replay symbol) ──────────────────────────────── */}
      <section className="flex-1 min-h-0 glass rounded-2xl shadow-glass overflow-hidden flex flex-col">
        <div className="flex items-center gap-2 px-4 py-2.5 border-b border-white/[0.06]">
          <span className="font-mono font-bold text-terminal-text">{replaySymbol}</span>
          {playback.active && (
            <span className="text-[10px] font-sans text-terminal-amber animate-pulse-slow">
              ● REPLAY
            </span>
          )}
          <span className="ml-auto font-mono text-[10px] text-terminal-ghost">
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
        <div className="glass rounded-2xl shadow-glass p-5">
          <h3 className="text-xs font-sans font-medium text-terminal-dim tracking-wide mb-3">
            Virtual Positions
          </h3>
          {simPositions.length === 0 ? (
            <p className="text-xs font-sans text-terminal-ghost text-center py-4">No positions</p>
          ) : (
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-white/[0.06]">
                  {['Symbol', 'Qty', 'Avg Cost', 'Price', 'P&L'].map((c) => (
                    <th key={c} className="py-1.5 px-2 font-sans font-medium text-terminal-ghost text-right first:text-left">{c}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {simPositions.map((p) => (
                  <tr key={p.symbol} className="border-b border-white/[0.04] hover:bg-white/[0.02] transition-colors">
                    <td className="py-2 px-2 font-mono text-terminal-text font-semibold">{p.symbol}</td>
                    <td className="py-2 px-2 font-mono text-terminal-dim tabular-nums text-right">{p.qty}</td>
                    <td className="py-2 px-2 font-mono text-terminal-dim tabular-nums text-right">{fmtUSD(p.avg_cost)}</td>
                    <td className="py-2 px-2 font-mono text-terminal-text tabular-nums text-right">{fmtUSD(p.current_price)}</td>
                    <td className={`py-2 px-2 font-mono tabular-nums text-right ${p.unrealized_pnl >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                      {p.unrealized_pnl >= 0 ? '+' : ''}{fmtUSD(p.unrealized_pnl)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        {/* Sim orders */}
        <div className="glass rounded-2xl shadow-glass p-5">
          <h3 className="text-xs font-sans font-medium text-terminal-dim tracking-wide mb-3">
            Order History
          </h3>
          {simOrders.length === 0 ? (
            <p className="text-xs font-sans text-terminal-ghost text-center py-4">No orders yet</p>
          ) : (
            <div className="overflow-y-auto max-h-48">
              <table className="w-full text-xs">
                <thead>
                  <tr className="border-b border-white/[0.06]">
                    {['Time', 'Symbol', 'Side', 'Qty', 'Price', 'P&L'].map((c) => (
                      <th key={c} className="py-1.5 px-2 font-sans font-medium text-terminal-ghost text-right first:text-left">{c}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {simOrders.map((o) => (
                    <tr key={o.id} className="border-b border-white/[0.04] hover:bg-white/[0.02] transition-colors">
                      <td className="py-1.5 px-2 font-mono text-terminal-ghost tabular-nums">
                        {new Date(o.timestamp).toLocaleTimeString()}
                      </td>
                      <td className="py-1.5 px-2 font-mono text-terminal-text font-semibold">{o.symbol}</td>
                      <td className={`py-1.5 px-2 font-mono font-semibold ${o.action === 'BUY' ? 'text-emerald-400' : 'text-red-400'}`}>
                        {o.action}
                      </td>
                      <td className="py-1.5 px-2 font-mono text-terminal-dim tabular-nums text-right">{o.qty}</td>
                      <td className="py-1.5 px-2 font-mono text-terminal-dim tabular-nums text-right">{fmtUSD(o.price)}</td>
                      <td className={`py-1.5 px-2 font-mono tabular-nums text-right ${(o.pnl ?? 0) >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
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

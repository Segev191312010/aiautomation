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

/** Section header with a left accent bar */
function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex items-center gap-2">
      <span className="block w-0.5 h-3.5 rounded-full bg-indigo-600/60" />
      <span className="text-[11px] font-sans font-semibold text-gray-500 tracking-widest uppercase">
        {children}
      </span>
    </div>
  )
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
    <div className="flex flex-col gap-5 h-full pb-24">

      {/* ── Page header ───────────────────────────────────────────────── */}
      <div className="flex items-start justify-between">
        <div className="flex items-center gap-3">
          {/* icon */}
          <div className="flex items-center justify-center w-9 h-9 rounded-xl bg-amber-500/10 border border-amber-500/20 text-amber-600 shrink-0">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" className="w-4.5 h-4.5">
              <path strokeLinecap="round" strokeLinejoin="round" d="M3 17l4-8 5 5 4-7 5 4" />
              <circle cx="19" cy="11" r="1.5" fill="currentColor" stroke="none" />
            </svg>
          </div>
          <div>
            <h1 className="text-base font-sans font-semibold text-gray-800 leading-tight">
              Simulation
            </h1>
            <p className="text-[11px] font-sans text-gray-400 mt-0.5">
              Historical replay with virtual paper trading
            </p>
          </div>
        </div>

        {/* Running / stopped status pill */}
        <div className="flex items-center gap-2">
          <span
            className={`inline-flex items-center gap-1.5 text-[10px] font-sans font-semibold tracking-widest uppercase px-2.5 py-1 rounded-lg border ${
              playback.active
                ? 'bg-emerald-500/10 border-emerald-500/25 text-emerald-400'
                : 'bg-gray-50 border-white/[0.07] text-gray-400'
            }`}
          >
            <span
              className={`inline-block w-1.5 h-1.5 rounded-full ${
                playback.active
                  ? 'bg-emerald-400 animate-pulse shadow-[0_0_6px_rgba(52,211,153,0.8)]'
                  : 'bg-gray-400'
              }`}
            />
            {playback.active ? 'Running' : 'Stopped'}
          </span>
        </div>
      </div>

      {/* ── KPIs ─────────────────────────────────────────────────────── */}
      <section className="card rounded-2xl shadow-card p-4">
        <div className="flex items-center justify-between mb-3.5">
          <SectionLabel>Virtual Account</SectionLabel>
          <button
            onClick={handleReset}
            className={[
              'text-[11px] font-sans font-medium px-3 py-1 rounded-xl border transition-colors',
              'border-red-500/25 text-red-400 hover:bg-red-500/10 hover:border-red-500/35',
            ].join(' ')}
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

      {/* ── Chart (replay symbol) ────────────────────────────────────── */}
      <section className="flex-1 min-h-0 card rounded-2xl shadow-card overflow-hidden flex flex-col">
        {/* Chart header */}
        <div className="flex items-center gap-3 px-4 py-2.5 border-b border-gray-200 shrink-0">
          <span className="block w-0.5 h-3.5 rounded-full bg-indigo-600/60" />
          <span className="font-mono font-bold text-gray-800 tracking-wide">{replaySymbol}</span>

          {playback.active && (
            <span className="flex items-center gap-1 text-[10px] font-sans font-semibold text-amber-600 animate-pulse-slow">
              <span className="inline-block w-1.5 h-1.5 rounded-full bg-amber-600 animate-pulse shadow-[0_0_5px_rgba(245,158,11,0.7)]" />
              REPLAY
            </span>
          )}

          <div className="ml-auto flex items-center gap-3">
            {playback.total_bars > 0 && (
              <>
                {/* progress mini-bar */}
                <div className="hidden sm:flex items-center gap-2">
                  <div className="relative w-24 h-1 bg-gray-100 rounded-full overflow-hidden">
                    <div
                      className="absolute inset-y-0 left-0 bg-indigo-600/80 rounded-full transition-all"
                      style={{ width: `${playback.progress * 100}%` }}
                    />
                  </div>
                  <span className="text-[10px] font-mono text-gray-400 tabular-nums">
                    {(playback.progress * 100).toFixed(0)}%
                  </span>
                </div>
                <span className="text-[10px] font-mono text-gray-400 tabular-nums">
                  {playback.current_index + 1}/{playback.total_bars} bars
                </span>
              </>
            )}
          </div>
        </div>

        <div className="flex-1 min-h-0">
          <TradingChart symbol={replaySymbol} className="h-full" />
        </div>
      </section>

      {/* ── Positions + Orders ─────────────────────────────────────────── */}
      <section className="grid grid-cols-1 md:grid-cols-2 gap-4">

        {/* Virtual Positions */}
        <div className="card rounded-2xl shadow-card p-5">
          <div className="flex items-center justify-between mb-3.5">
            <SectionLabel>Virtual Positions</SectionLabel>
            {simPositions.length > 0 && (
              <span className="text-[10px] font-mono text-gray-400">
                {simPositions.length} open
              </span>
            )}
          </div>

          {simPositions.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-8 gap-2">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" className="w-6 h-6 text-gray-400/40">
                <rect x="3" y="3" width="18" height="18" rx="2" />
                <path strokeLinecap="round" d="M3 9h18M9 21V9" />
              </svg>
              <p className="text-xs font-sans text-gray-400">No open positions</p>
            </div>
          ) : (
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-gray-200">
                  {['Symbol', 'Qty', 'Avg Cost', 'Price', 'P&L'].map((c) => (
                    <th key={c} className="pb-2 px-2 font-sans font-medium text-gray-400 text-right first:text-left">
                      {c}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {simPositions.map((p) => (
                  <tr key={p.symbol} className="border-b border-gray-100 hover:bg-gray-50/70 transition-colors">
                    <td className="py-2 px-2 font-mono text-gray-800 font-semibold">{p.symbol}</td>
                    <td className="py-2 px-2 font-mono text-gray-500 tabular-nums text-right">{p.qty}</td>
                    <td className="py-2 px-2 font-mono text-gray-500 tabular-nums text-right">{fmtUSD(p.avg_cost)}</td>
                    <td className="py-2 px-2 font-mono text-gray-800 tabular-nums text-right">{fmtUSD(p.current_price)}</td>
                    <td className={`py-2 px-2 font-mono tabular-nums text-right font-medium ${p.unrealized_pnl >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                      {p.unrealized_pnl >= 0 ? '+' : ''}{fmtUSD(p.unrealized_pnl)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        {/* Order History */}
        <div className="card rounded-2xl shadow-card p-5">
          <div className="flex items-center justify-between mb-3.5">
            <SectionLabel>Order History</SectionLabel>
            {simOrders.length > 0 && (
              <span className="text-[10px] font-mono text-gray-400">
                {simOrders.length} orders
              </span>
            )}
          </div>

          {simOrders.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-8 gap-2">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" className="w-6 h-6 text-gray-400/40">
                <path strokeLinecap="round" strokeLinejoin="round" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2" />
              </svg>
              <p className="text-xs font-sans text-gray-400">No orders yet</p>
            </div>
          ) : (
            <div className="overflow-y-auto max-h-48">
              <table className="w-full text-xs">
                <thead>
                  <tr className="border-b border-gray-200">
                    {['Time', 'Symbol', 'Side', 'Qty', 'Price', 'P&L'].map((c) => (
                      <th key={c} className="pb-2 px-2 font-sans font-medium text-gray-400 text-right first:text-left">
                        {c}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {simOrders.map((o) => (
                    <tr key={o.id} className="border-b border-gray-100 hover:bg-gray-50/70 transition-colors">
                      <td className="py-1.5 px-2 font-mono text-gray-400 tabular-nums">
                        {new Date(o.timestamp).toLocaleTimeString()}
                      </td>
                      <td className="py-1.5 px-2 font-mono text-gray-800 font-semibold">{o.symbol}</td>
                      <td className={`py-1.5 px-2 font-mono font-semibold ${o.action === 'BUY' ? 'text-emerald-400' : 'text-red-400'}`}>
                        {o.action}
                      </td>
                      <td className="py-1.5 px-2 font-mono text-gray-500 tabular-nums text-right">{o.qty}</td>
                      <td className="py-1.5 px-2 font-mono text-gray-500 tabular-nums text-right">{fmtUSD(o.price)}</td>
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

      {/* ── Sticky replay controller ───────────────────────────────────── */}
      <SimController />
    </div>
  )
}

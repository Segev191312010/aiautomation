/**
 * Dashboard — the home page.
 * Layout: Watchlist grid (top) → Chart (centre) → Mini account KPIs (right rail)
 */
import React from 'react'
import WatchlistGrid from '@/components/ticker/WatchlistGrid'
import TradingChart from '@/components/chart/TradingChart'
import KPICard from '@/components/tradebot/KPICard'
import { SkeletonCard } from '@/components/ui/Skeleton'
import { useMarketStore, useAccountStore, useBotStore } from '@/store'
import type { AccountSummary, SimAccountState } from '@/types'

function isSimAccount(a: AccountSummary | SimAccountState): a is SimAccountState {
  return 'is_sim' in a && a.is_sim === true
}

function fmtUSD(v: number): string {
  return '$' + v.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

export default function Dashboard() {
  const selectedSymbol = useMarketStore((s) => s.selectedSymbol)
  const compMode       = useMarketStore((s) => s.compMode)
  const compSymbol     = useMarketStore((s) => s.compSymbol)
  const toggleCompMode = useMarketStore((s) => s.toggleCompMode)
  const setCompSymbol  = useMarketStore((s) => s.setCompSymbol)
  const account        = useAccountStore((s) => s.account)
  const simMode        = useBotStore((s) => s.simMode)

  const netLiq     = account ? (isSimAccount(account) ? account.net_liquidation : account.balance) : null
  const cash       = account?.cash ?? null
  const unrealPnl  = account?.unrealized_pnl ?? null
  const realPnl    = account ? (isSimAccount(account) ? account.realized_pnl : account.realized_pnl) : null

  return (
    <div className="flex flex-col gap-4 h-full">
      {/* ── Watchlist card grid ─────────────────────────────────────── */}
      <section>
        <WatchlistGrid />
      </section>

      {/* ── Chart + KPI rail ────────────────────────────────────────── */}
      <section className="flex gap-4 flex-1 min-h-0">
        {/* Chart */}
        <div className="flex-1 min-w-0 flex flex-col bg-terminal-surface border border-terminal-border rounded-lg overflow-hidden">
          {/* Chart header */}
          <div className="flex items-center gap-3 px-4 py-2.5 border-b border-terminal-border">
            <span className="font-mono font-bold text-terminal-text">{selectedSymbol}</span>
            <span className="text-[10px] font-mono text-terminal-ghost uppercase">{useMarketStore.getState().chartType} · 1D</span>

            {/* Comparison toggle */}
            <div className="ml-auto flex items-center gap-2">
              {compMode && (
                <input
                  value={compSymbol}
                  onChange={(e) => setCompSymbol(e.target.value.toUpperCase())}
                  placeholder="vs. AAPL…"
                  className="text-xs font-mono w-24 bg-terminal-input border border-terminal-border rounded px-2 py-0.5 text-terminal-text focus:border-terminal-blue focus:outline-none"
                />
              )}
              <button
                onClick={toggleCompMode}
                className={`text-[10px] font-mono px-2.5 py-1 rounded border transition-colors ${
                  compMode
                    ? 'border-terminal-amber/40 text-terminal-amber bg-terminal-amber/5'
                    : 'border-terminal-border text-terminal-ghost hover:text-terminal-dim'
                }`}
              >
                Compare
              </button>
            </div>
          </div>
          <div className="flex-1 min-h-0">
            <TradingChart symbol={selectedSymbol} className="h-full" />
          </div>
        </div>

        {/* KPI rail */}
        <aside className="w-52 shrink-0 flex flex-col gap-3">
          {!account ? (
            <>
              <SkeletonCard /><SkeletonCard /><SkeletonCard /><SkeletonCard />
            </>
          ) : (
            <>
              <KPICard
                label={simMode ? 'Net Liq (SIM)' : 'Net Liquidation'}
                value={netLiq != null ? fmtUSD(netLiq) : '—'}
                highlight
              />
              <KPICard
                label="Cash"
                value={cash != null ? fmtUSD(cash) : '—'}
              />
              <KPICard
                label="Unrealized P&L"
                value={unrealPnl != null ? fmtUSD(unrealPnl) : '—'}
                positive={unrealPnl != null ? unrealPnl >= 0 : undefined}
              />
              <KPICard
                label="Realized P&L"
                value={realPnl != null ? fmtUSD(realPnl) : '—'}
                positive={realPnl != null ? realPnl >= 0 : undefined}
              />

              {isSimAccount(account) && (
                <KPICard
                  label="Total Return"
                  value={account.total_return_pct.toFixed(2)}
                  suffix="%"
                  positive={account.total_return_pct >= 0}
                />
              )}

              {'is_mock' in account && account.is_mock && (
                <div className="text-[9px] font-mono text-terminal-ghost text-center">
                  [ mock data ]
                </div>
              )}
            </>
          )}
        </aside>
      </section>
    </div>
  )
}

/**
 * Dashboard - home page.
 * Layout: Watchlist grid -> Signal board -> Chart -> KPI rail.
 */
import React from 'react'
import WatchlistGrid from '@/components/ticker/WatchlistGrid'
import TradingChart from '@/components/chart/TradingChart'
import OpportunityBoard from '@/components/insights/OpportunityBoard'
import DiagnosticHeaderRow from '@/components/insights/diagnostics/DiagnosticHeaderRow'
import OverallSummaryCard from '@/components/insights/diagnostics/OverallSummaryCard'
import SystemOverviewWidget from '@/components/insights/diagnostics/SystemOverviewWidget'
import DowTheoryWidget from '@/components/insights/diagnostics/DowTheoryWidget'
import SectorDivergenceWidget from '@/components/insights/diagnostics/SectorDivergenceWidget'
import AASWidget from '@/components/insights/diagnostics/AASWidget'
import IndicatorCardGrid from '@/components/insights/diagnostics/IndicatorCardGrid'
import BubbleMarketMap from '@/components/insights/diagnostics/BubbleMarketMap'
import SectorProjectionsPanel from '@/components/insights/diagnostics/SectorProjectionsPanel'
import NewsStrip from '@/components/insights/diagnostics/NewsStrip'
import KPICard from '@/components/tradebot/KPICard'
import { SkeletonCard } from '@/components/ui/Skeleton'
import { useDiagnostics } from '@/hooks/useDiagnostics'
import { useMarketStore, useAccountStore, useBotStore, useDiagnosticsStore } from '@/store'
import type { AccountSummary, SimAccountState } from '@/types'

function isSimAccount(a: AccountSummary | SimAccountState): a is SimAccountState {
  return 'is_sim' in a && a.is_sim === true
}

function fmtUSD(v: number): string {
  return '$' + v.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

export default function Dashboard() {
  useDiagnostics()

  const selectedSymbol = useMarketStore((s) => s.selectedSymbol)
  const compMode = useMarketStore((s) => s.compMode)
  const compSymbol = useMarketStore((s) => s.compSymbol)
  const toggleCompMode = useMarketStore((s) => s.toggleCompMode)
  const setCompSymbol = useMarketStore((s) => s.setCompSymbol)
  const chartType = useMarketStore((s) => s.chartType)
  const account = useAccountStore((s) => s.account)
  const simMode = useBotStore((s) => s.simMode)
  const diagnosticsEnabled = useBotStore((s) => s.status?.features?.market_diagnostics ?? false)

  const lookbackDays = useDiagnosticsStore((s) => s.lookbackDays)
  const setLookbackDays = useDiagnosticsStore((s) => s.setLookbackDays)
  const loadAllDiagnostics = useDiagnosticsStore((s) => s.loadAll)
  const refreshDiagnostics = useDiagnosticsStore((s) => s.refreshNow)
  const diagnosticsLoading = useDiagnosticsStore((s) => s.loading)
  const diagnosticsError = useDiagnosticsStore((s) => s.error)
  const overview = useDiagnosticsStore((s) => s.overview)
  const indicators = useDiagnosticsStore((s) => s.indicators)
  const marketMap = useDiagnosticsStore((s) => s.marketMap)
  const projections = useDiagnosticsStore((s) => s.projections)
  const news = useDiagnosticsStore((s) => s.news)
  const refreshing = useDiagnosticsStore((s) => s.refreshing)
  const refreshRun = useDiagnosticsStore((s) => s.refreshRun)

  const onSetLookback = React.useCallback(
    (days: 90 | 180 | 365) => {
      setLookbackDays(days)
      void loadAllDiagnostics()
    },
    [setLookbackDays, loadAllDiagnostics],
  )
  const onManualRefresh = React.useCallback(() => {
    void refreshDiagnostics()
  }, [refreshDiagnostics])

  const netLiq = account ? (isSimAccount(account) ? account.net_liquidation : account.balance) : null
  const cash = account?.cash ?? null
  const unrealPnl = account?.unrealized_pnl ?? null
  const realPnl = account ? (isSimAccount(account) ? account.realized_pnl : account.realized_pnl) : null

  return (
    <div className="flex flex-col gap-4 h-full">
      <section>
        <WatchlistGrid />
      </section>

      <OpportunityBoard />

      <section className="flex gap-4 flex-1 min-h-0">
        <div className="flex-1 min-w-0 flex flex-col glass rounded-2xl shadow-glass overflow-hidden">
          <div className="flex items-center gap-3 px-4 py-2.5 border-b border-white/[0.06]">
            <span className="font-mono font-bold text-terminal-text">{selectedSymbol}</span>
            <span className="text-xs font-sans font-medium text-terminal-dim tracking-wide uppercase">{chartType} · 1D</span>

            <div className="ml-auto flex items-center gap-2">
              {compMode && (
                <input
                  value={compSymbol}
                  onChange={(e) => setCompSymbol(e.target.value.toUpperCase())}
                  placeholder="vs. AAPL..."
                  className="text-xs font-mono w-24 bg-terminal-input border border-terminal-border rounded-xl px-2 py-0.5 text-terminal-text focus:border-terminal-blue focus:outline-none"
                />
              )}
              <button
                onClick={toggleCompMode}
                className={`text-xs font-sans font-medium px-2.5 py-1 rounded-xl border transition-colors ${
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

        <aside className="w-52 shrink-0 flex flex-col gap-3">
          {!account ? (
            <>
              <SkeletonCard />
              <SkeletonCard />
              <SkeletonCard />
              <SkeletonCard />
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

            </>
          )}
        </aside>
      </section>

      {diagnosticsEnabled && (
        <section className="flex flex-col gap-3">
          <DiagnosticHeaderRow
            lookbackDays={lookbackDays}
            onSetLookback={onSetLookback}
            onRefresh={onManualRefresh}
            refreshing={refreshing}
            refreshRun={refreshRun}
            lastRunTs={overview?.last_run_ts ?? refreshRun?.completed_at ?? undefined}
          />

          {diagnosticsError && (
            <div className="rounded-2xl border border-terminal-red/40 bg-terminal-red/10 p-5 text-xs font-sans font-medium text-terminal-red">
              Diagnostics error: {diagnosticsError}
            </div>
          )}

          {diagnosticsLoading && !overview && indicators.length === 0 ? (
            <div className="glass rounded-2xl p-5 text-xs font-sans font-medium text-terminal-ghost">
              Loading diagnostics...
            </div>
          ) : (
            <>
              <OverallSummaryCard overview={overview} />

              <section className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-2">
                <SystemOverviewWidget overview={overview} />
                <DowTheoryWidget overview={overview} />
                <SectorDivergenceWidget overview={overview} />
                <AASWidget overview={overview} />
              </section>

              <IndicatorCardGrid indicators={indicators} />
              <BubbleMarketMap rows={marketMap} />
              <SectorProjectionsPanel projection={projections} />
              <NewsStrip articles={news} />
            </>
          )}
        </section>
      )}
    </div>
  )
}

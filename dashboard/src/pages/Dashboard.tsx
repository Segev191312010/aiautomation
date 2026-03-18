import React from 'react'
import clsx from 'clsx'
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
import { useMarketStore, useAccountStore, useBotStore, useDiagnosticsStore, useUIStore } from '@/store'
import type { AccountSummary, SimAccountState } from '@/types'

function isSimAccount(a: AccountSummary | SimAccountState): a is SimAccountState {
  return 'is_sim' in a && a.is_sim === true
}

function fmtUSD(v: number): string {
  return '$' + v.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function formatCompact(value?: number): string {
  if (value == null) return '--'
  if (value >= 1e12) return `$${(value / 1e12).toFixed(2)}T`
  if (value >= 1e9) return `$${(value / 1e9).toFixed(2)}B`
  if (value >= 1e6) return `$${(value / 1e6).toFixed(2)}M`
  return `$${value.toLocaleString('en-US')}`
}

function formatPrice(value?: number): string {
  if (value == null) return '--'
  return value.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function SectionHeader({ eyebrow, title, action }: { eyebrow: string; title: string; action?: React.ReactNode }) {
  return (
    <div className="flex items-end justify-between gap-4">
      <div>
        <div className="text-[10px] font-sans uppercase tracking-[0.22em] text-zinc-500">{eyebrow}</div>
        <h2 className="mt-1 text-lg font-sans font-semibold text-zinc-50">{title}</h2>
      </div>
      {action}
    </div>
  )
}

export default function Dashboard() {
  useDiagnostics()

  const setRoute = useUIStore((s) => s.setRoute)
  const selectedSymbol = useMarketStore((s) => s.selectedSymbol)
  const selectedQuote = useMarketStore((s) => s.quotes[selectedSymbol])
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
  const [diagnosticsExpanded, setDiagnosticsExpanded] = React.useState(false)

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
  const realPnl = account ? account.realized_pnl : null

  return (
    <div className="flex flex-col gap-5 h-full">
      <section className="grid gap-4 xl:grid-cols-[minmax(0,1.55fr)_minmax(320px,0.9fr)]">
        <div className="card rounded-lg p-5 ">
          <div className="flex flex-col gap-5">
            <div className="flex flex-wrap items-start justify-between gap-4">
              <div className="min-w-0">
                <div className="text-[10px] font-sans uppercase tracking-[0.24em] text-zinc-500">
                  Market Snapshot
                </div>
                <div className="mt-2 flex flex-wrap items-end gap-x-4 gap-y-2">
                  <div className="text-4xl font-mono font-bold tracking-tight text-zinc-50">
                    {selectedSymbol}
                  </div>
                  <div className="text-2xl font-mono font-semibold text-zinc-100">
                    {formatPrice(selectedQuote?.price)}
                  </div>
                  {selectedQuote && (
                    <div
                      className={clsx(
                        'text-sm font-mono font-semibold tabular-nums',
                        selectedQuote.change_pct >= 0 ? 'text-emerald-400' : 'text-red-400',
                      )}
                    >
                      {selectedQuote.change_pct >= 0 ? '+' : ''}
                      {selectedQuote.change?.toFixed(2) ?? '--'} / {selectedQuote.change_pct >= 0 ? '+' : ''}
                      {selectedQuote.change_pct.toFixed(2)}%
                    </div>
                  )}
                </div>
                <p className="mt-3 max-w-2xl text-sm font-sans text-zinc-400">
                  Start with the active name, then drill into the live market workspace, full stock analysis, or run a broader screen.
                </p>
              </div>

              <div className="flex flex-wrap gap-2">
                <button
                  type="button"
                  onClick={() => setRoute('market')}
                  className="rounded-lg border border-zinc-800 bg-zinc-950 px-3.5 py-2 text-[11px] font-sans font-medium text-white transition-colors hover:bg-zinc-900"
                >
                  Open market workspace
                </button>
                <button
                  type="button"
                  onClick={() => setRoute('stock')}
                  className="rounded-lg border border-zinc-800 bg-zinc-900 px-3.5 py-2 text-[11px] font-sans font-medium text-zinc-200 transition-colors hover:border-zinc-700 hover:text-zinc-50"
                >
                  Open stock analysis
                </button>
                <button
                  type="button"
                  onClick={() => setRoute('screener')}
                  className="rounded-lg border border-zinc-800 bg-[#FAF8F5] px-3.5 py-2 text-[11px] font-sans font-medium text-zinc-200 transition-colors hover:border-zinc-700 hover:text-zinc-50"
                >
                  Run screener
                </button>
              </div>
            </div>

            <div className="grid gap-3 sm:grid-cols-3">
              <div className="rounded-lg border border-zinc-800 bg-zinc-900 px-3 py-3">
                <div className="text-[10px] font-sans uppercase tracking-[0.18em] text-zinc-400">Market Cap</div>
                <div className="mt-1 text-base font-mono font-semibold text-zinc-50">
                  {formatCompact(selectedQuote?.market_cap)}
                </div>
              </div>
              <div className="rounded-lg border border-zinc-800 bg-zinc-900 px-3 py-3">
                <div className="text-[10px] font-sans uppercase tracking-[0.18em] text-zinc-400">Volume</div>
                <div className="mt-1 text-base font-mono font-semibold text-zinc-50">
                  {selectedQuote?.volume?.toLocaleString('en-US') ?? '--'}
                </div>
              </div>
              <div className="rounded-lg border border-zinc-800 bg-zinc-900 px-3 py-3">
                <div className="text-[10px] font-sans uppercase tracking-[0.18em] text-zinc-400">Feed</div>
                <div className="mt-1 text-base font-mono font-semibold text-zinc-50">
                  {selectedQuote?.live_source === 'ibkr' ? 'IBKR stream' : 'Yahoo fallback'}
                </div>
              </div>
            </div>
          </div>
        </div>

        <div className="card rounded-lg p-5 ">
          <SectionHeader eyebrow="Capital" title="Account Snapshot" />
          <div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-1">
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
                  value={netLiq != null ? fmtUSD(netLiq) : '--'}
                  highlight
                />
                <KPICard label="Cash" value={cash != null ? fmtUSD(cash) : '--'} />
                <KPICard
                  label="Unrealized P&L"
                  value={unrealPnl != null ? fmtUSD(unrealPnl) : '--'}
                  positive={unrealPnl != null ? unrealPnl >= 0 : undefined}
                />
                <KPICard
                  label="Realized P&L"
                  value={realPnl != null ? fmtUSD(realPnl) : '--'}
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
          </div>
        </div>
      </section>

      <section className="animate-fade-in-up">
        <SectionHeader eyebrow="Watchlist" title="Market Entry Points" />
        <div className="mt-3">
          <WatchlistGrid />
        </div>
      </section>

      <section className="animate-fade-in-up" style={{ animationDelay: '40ms' }}>
        <SectionHeader eyebrow="Ideas" title="Opportunity Board" />
        <div className="mt-3">
          <OpportunityBoard />
        </div>
      </section>

      <section className="grid gap-4 xl:grid-cols-[minmax(0,1.55fr)_minmax(320px,0.9fr)] flex-1 min-h-0 animate-fade-in-up" style={{ animationDelay: '80ms' }}>
        <div className="card rounded-lg overflow-hidden  min-h-[28rem] flex flex-col">
          <div className="flex items-center gap-3 px-4 py-3 border-b border-[#E8E4DF]">
            <div>
              <div className="text-[10px] font-sans uppercase tracking-[0.18em] text-zinc-500">Chart</div>
              <div className="text-base font-mono font-bold text-zinc-50">{selectedSymbol}</div>
            </div>
            <span className="rounded-full border border-zinc-800 bg-zinc-900 px-2 py-0.5 text-[10px] font-mono uppercase tracking-[0.18em] text-zinc-400">
              {chartType}
            </span>
            {selectedQuote && (
              <span
                className={clsx(
                  'text-[11px] font-mono tabular-nums',
                  selectedQuote.change_pct >= 0 ? 'text-emerald-400' : 'text-red-400',
                )}
              >
                {selectedQuote.change_pct >= 0 ? '+' : ''}{selectedQuote.change_pct.toFixed(2)}%
              </span>
            )}

            <div className="ml-auto flex items-center gap-2">
              {compMode && (
                <input
                  value={compSymbol}
                  onChange={(e) => setCompSymbol(e.target.value.toUpperCase())}
                  placeholder="vs. MSFT"
                  className="w-24 rounded-lg border border-zinc-800 bg-[#FAF8F5] px-2.5 py-1.5 text-xs font-mono text-zinc-100 focus:border-zinc-700 focus:outline-none"
                />
              )}
              <button
                type="button"
                onClick={toggleCompMode}
                className={clsx(
                  'rounded-lg border px-2.5 py-1.5 text-[11px] font-sans transition-colors',
                  compMode
                    ? 'border-zinc-800 bg-zinc-800 text-zinc-50'
                    : 'border-zinc-800 text-zinc-400 hover:border-zinc-700 hover:text-zinc-100',
                )}
              >
                Compare
              </button>
              <button
                type="button"
                onClick={() => setRoute('market')}
                className="rounded-lg border border-zinc-800 bg-zinc-900 px-2.5 py-1.5 text-[11px] font-sans text-zinc-200 transition-colors hover:border-zinc-700 hover:text-zinc-50"
              >
                Open workspace
              </button>
            </div>
          </div>

          <div className="flex-1 min-h-0">
            <TradingChart symbol={selectedSymbol} className="h-full" />
          </div>
        </div>

        <div className="card rounded-lg p-5  flex flex-col gap-4">
          <SectionHeader eyebrow="Context" title="Quick Brief" />

          <div className="rounded-lg border border-zinc-800 bg-zinc-900 px-4 py-3">
            <div className="text-[10px] font-sans uppercase tracking-[0.18em] text-zinc-500">Active Name</div>
            <div className="mt-1 flex items-end justify-between gap-3">
              <div>
                <div className="text-xl font-mono font-bold text-zinc-50">{selectedSymbol}</div>
                <div className="text-sm font-mono text-zinc-400">{formatPrice(selectedQuote?.price)}</div>
              </div>
              <button
                type="button"
                onClick={() => setRoute('stock')}
                className="rounded-lg border border-zinc-800 bg-zinc-900 px-3 py-2 text-[11px] font-sans text-zinc-200 transition-colors hover:border-zinc-700 hover:text-zinc-50"
              >
                Full analysis
              </button>
            </div>
          </div>

          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-1">
            <div className="rounded-lg border border-zinc-800 px-4 py-3">
              <div className="text-[10px] font-sans uppercase tracking-[0.18em] text-zinc-500">52W Range</div>
              <div className="mt-1 text-sm font-mono font-semibold text-zinc-50">
                {selectedQuote?.year_low != null && selectedQuote?.year_high != null
                  ? `$${selectedQuote.year_low.toFixed(0)} - $${selectedQuote.year_high.toFixed(0)}`
                  : '--'}
              </div>
            </div>
            <div className="rounded-lg border border-zinc-800 px-4 py-3">
              <div className="text-[10px] font-sans uppercase tracking-[0.18em] text-zinc-500">Market State</div>
              <div className="mt-1 text-sm font-mono font-semibold text-zinc-50">
                {selectedQuote?.market_state?.toUpperCase() ?? 'UNKNOWN'}
              </div>
            </div>
            <div className="rounded-lg border border-zinc-800 px-4 py-3">
              <div className="text-[10px] font-sans uppercase tracking-[0.18em] text-zinc-500">Volume</div>
              <div className="mt-1 text-sm font-mono font-semibold text-zinc-50">
                {selectedQuote?.volume?.toLocaleString('en-US') ?? '--'}
              </div>
            </div>
            <div className="rounded-lg border border-zinc-800 px-4 py-3">
              <div className="text-[10px] font-sans uppercase tracking-[0.18em] text-zinc-500">Market Cap</div>
              <div className="mt-1 text-sm font-mono font-semibold text-zinc-50">
                {formatCompact(selectedQuote?.market_cap)}
              </div>
            </div>
          </div>
        </div>
      </section>

      {diagnosticsEnabled && (
        <section className="flex flex-col gap-4 animate-fade-in-up" style={{ animationDelay: '120ms' }}>
          <SectionHeader
            eyebrow="Diagnostics"
            title="Market Diagnostics"
            action={(
              <button
                type="button"
                onClick={() => setDiagnosticsExpanded((v) => !v)}
                className="rounded-lg border border-zinc-800 bg-zinc-900 px-3 py-1.5 text-[11px] font-sans text-zinc-400 transition-colors hover:border-zinc-700 hover:text-zinc-50"
              >
                {diagnosticsExpanded ? 'Hide' : 'Show'}
              </button>
            )}
          />

          {!diagnosticsExpanded ? (
            <div className="card rounded-lg p-4 flex items-center justify-between gap-4">
              <div>
                <p className="text-sm font-sans text-zinc-200">
                  Diagnostics stay collapsed by default so the dashboard remains focused on scanning and drilldowns.
                </p>
                {overview?.last_run_ts && (
                  <p className="text-[11px] font-mono text-zinc-400 mt-1">
                    Last run: {new Date(overview.last_run_ts).toLocaleString()}
                  </p>
                )}
              </div>
              <button
                type="button"
                onClick={() => setDiagnosticsExpanded(true)}
                className="rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-[11px] font-sans font-medium text-white transition-colors hover:bg-zinc-900"
              >
                Expand diagnostics
              </button>
            </div>
          ) : (
            <>
              <DiagnosticHeaderRow
                lookbackDays={lookbackDays}
                onSetLookback={onSetLookback}
                onRefresh={onManualRefresh}
                refreshing={refreshing}
                refreshRun={refreshRun}
                lastRunTs={overview?.last_run_ts ?? refreshRun?.completed_at ?? undefined}
              />

              {diagnosticsError && (
                <div className="rounded-lg border border-red-300 bg-red-500/10 p-5 flex items-start gap-3">
                  <svg
                    className="w-4 h-4 text-red-400 shrink-0 mt-0.5"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth={2}
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  >
                    <circle cx="12" cy="12" r="10" />
                    <line x1="12" y1="8" x2="12" y2="12" />
                    <line x1="12" y1="16" x2="12.01" y2="16" />
                  </svg>
                  <span className="text-xs font-sans font-medium text-red-400">
                    Diagnostics error: {diagnosticsError}
                  </span>
                </div>
              )}

              {diagnosticsLoading && !overview && indicators.length === 0 ? (
                <div className="card rounded-lg p-5 text-xs font-sans font-medium text-zinc-500">
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
            </>
          )}
        </section>
      )}
    </div>
  )
}

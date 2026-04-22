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
import ErrorBoundary from '@/components/ui/ErrorBoundary'
import { useDiagnostics } from '@/hooks/useDiagnostics'
import { useMarketStore, useAccountStore, useBotStore, useDiagnosticsStore } from '@/store'
import { fmtUSD } from '@/utils/formatters'
import { navigateToRoute } from '@/utils/routes'
import type { AccountSummary, SimAccountState } from '@/types'

function isSimAccount(account: AccountSummary | SimAccountState): account is SimAccountState {
  return 'is_sim' in account && account.is_sim === true
}

function formatCompact(value?: number): string {
  if (value == null) return '--'
  if (value >= 1e12) return `$${(value / 1e12).toFixed(2)}T`
  if (value >= 1e9) return `$${(value / 1e9).toFixed(2)}B`
  if (value >= 1e6) return `$${(value / 1e6).toFixed(2)}M`
  return `$${value.toLocaleString('en-US')}`
}

function formatNumber(value?: number): string {
  if (value == null) return '--'
  return value.toLocaleString('en-US', { maximumFractionDigits: 2 })
}

function formatPrice(value?: number): string {
  if (value == null) return '--'
  return `$${value.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

function formatChange(value?: number): string {
  if (value == null) return '--'
  return `${value >= 0 ? '+' : ''}${value.toFixed(2)}%`
}

function SectionHeading({
  eyebrow,
  title,
  description,
  action,
}: {
  eyebrow: string
  title: string
  description?: string
  action?: React.ReactNode
}) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-4">
      <div className="max-w-2xl">
        <div className="shell-kicker">{eyebrow}</div>
        <h2 className="display-font mt-2 text-[1.75rem] leading-none text-[var(--text-primary)]">
          {title}
        </h2>
        {description && (
          <p className="mt-2 text-sm leading-6 text-[var(--text-secondary)]">
            {description}
          </p>
        )}
      </div>
      {action}
    </div>
  )
}

function ActionCard({
  eyebrow,
  title,
  description,
  onClick,
  active,
}: {
  eyebrow: string
  title: string
  description: string
  onClick: () => void
  active?: boolean
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={clsx(
        'group rounded-[24px] border p-4 text-left transition-all',
        active
          ? 'border-[rgba(245,158,11,0.3)] bg-[rgba(245,158,11,0.12)]'
          : 'border-[var(--border)] bg-[var(--bg-hover)] hover:border-[rgba(245,158,11,0.28)] hover:bg-[rgba(245,158,11,0.08)]',
      )}
    >
      <div className="shell-kicker">{eyebrow}</div>
      <div className="mt-2 flex items-center justify-between gap-3">
        <div className="text-base font-semibold text-[var(--text-primary)]">{title}</div>
        <svg
          viewBox="0 0 20 20"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.7"
          className="h-4 w-4 text-[var(--text-muted)] transition-transform group-hover:translate-x-0.5 group-hover:-translate-y-0.5"
        >
          <path d="M6 14 14 6M7 6h7v7" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </div>
      <p className="mt-2 text-sm leading-6 text-[var(--text-secondary)]">
        {description}
      </p>
    </button>
  )
}

function DiagnosticsChip({
  label,
  value,
  tone = 'default',
}: {
  label: string
  value: React.ReactNode
  tone?: 'default' | 'success' | 'warning' | 'danger'
}) {
  const toneClass = tone === 'success'
    ? 'border-[rgba(31,157,104,0.28)] bg-[rgba(31,157,104,0.08)] text-[var(--success)]'
    : tone === 'warning'
      ? 'border-[rgba(245,158,11,0.28)] bg-[rgba(245,158,11,0.08)] text-[var(--accent)]'
      : tone === 'danger'
        ? 'border-[rgba(217,76,61,0.28)] bg-[rgba(217,76,61,0.08)] text-[var(--danger)]'
        : 'border-[var(--border)] bg-[var(--bg-hover)] text-[var(--text-primary)]'

  return (
    <div className={`rounded-2xl border px-3 py-2 ${toneClass}`}>
      <div className="text-[10px] font-mono uppercase tracking-[0.18em] text-[var(--text-muted)]">{label}</div>
      <div className="mt-1 text-sm font-mono font-semibold">{value}</div>
    </div>
  )
}

export default function Dashboard() {
  useDiagnostics()

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
  const [radarTab, setRadarTab] = React.useState<'watchlist' | 'signals'>('watchlist')

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
  const feedLabel = selectedQuote?.live_source === 'ibkr' ? 'IBKR stream' : 'Yahoo fallback'

  return (
    <div className="shell-stack pb-4">
      <section className="shell-columns shell-columns--sidebar">
        <ErrorBoundary>
          <div className="shell-panel relative overflow-hidden p-6 sm:p-7">
            <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_top_right,rgba(245,158,11,0.16),transparent_34%)]" />
            <div className="relative">
              <div className="shell-kicker">Live command deck</div>
              <div className="mt-3 flex flex-wrap items-end gap-x-5 gap-y-3">
                <div className="display-font text-[3rem] leading-none text-[var(--text-primary)] sm:text-[3.6rem]">
                  {selectedSymbol}
                </div>
                <div className="text-2xl font-semibold text-[var(--text-primary)] sm:text-3xl">
                  {formatPrice(selectedQuote?.price)}
                </div>
                <div className={clsx(
                  'rounded-full border px-3 py-1 text-sm font-semibold',
                  (selectedQuote?.change_pct ?? 0) >= 0
                    ? 'border-[rgba(31,157,104,0.2)] bg-[rgba(31,157,104,0.1)] text-[var(--success)]'
                    : 'border-[rgba(217,76,61,0.2)] bg-[rgba(217,76,61,0.1)] text-[var(--danger)]',
                )}>
                  {selectedQuote?.change != null ? `${selectedQuote.change >= 0 ? '+' : ''}${selectedQuote.change.toFixed(2)}` : '--'} / {formatChange(selectedQuote?.change_pct)}
                </div>
              </div>

              <p className="mt-4 max-w-3xl text-sm leading-7 text-[var(--text-secondary)]">
                The desk opens on the active symbol. Use this surface to push into market, research, screening,
                or automation without hunting through dense navigation first.
              </p>

              <div className="mt-5 grid gap-3 sm:grid-cols-3">
                <MetricTile label="Market cap" value={formatCompact(selectedQuote?.market_cap)} />
                <MetricTile label="Volume" value={formatNumber(selectedQuote?.volume)} />
                <MetricTile label="Data feed" value={feedLabel} />
              </div>

              <div className="mt-5 flex flex-wrap gap-3">
                <button
                  type="button"
                  onClick={() => navigateToRoute('market')}
                  className="rounded-2xl bg-[var(--accent)] px-4 py-3 text-sm font-semibold text-white transition-colors hover:bg-[var(--accent-hover)]"
                >
                  Open market workspace
                </button>
                <button
                  type="button"
                  onClick={() => navigateToRoute('stock')}
                  className="rounded-2xl border border-[var(--border)] bg-[var(--bg-hover)] px-4 py-3 text-sm font-semibold text-[var(--text-primary)] transition-colors hover:border-[var(--accent)] hover:text-[var(--accent)]"
                >
                  Open stock analysis
                </button>
                <button
                  type="button"
                  onClick={() => navigateToRoute('screener')}
                  className="rounded-2xl border border-[var(--border)] bg-transparent px-4 py-3 text-sm font-semibold text-[var(--text-secondary)] transition-colors hover:border-[var(--accent)] hover:text-[var(--text-primary)]"
                >
                  Run screener
                </button>
              </div>
            </div>
          </div>
        </ErrorBoundary>

        <div className="shell-stack">
          <ErrorBoundary>
            <div className="shell-panel p-5">
              <SectionHeading
                eyebrow="Capital"
                title="Account Snapshot"
                description="Current desk posture across live or simulation mode."
              />
              <div className="mt-4 grid gap-3 sm:grid-cols-2">
                {!account ? (
                  <>
                    <SkeletonCard />
                    <SkeletonCard />
                    <SkeletonCard />
                    <SkeletonCard />
                  </>
                ) : (
                  <>
                    <KPICard label={simMode ? 'Net Liq (SIM)' : 'Net Liquidation'} value={netLiq != null ? fmtUSD(netLiq) : '--'} highlight />
                    <KPICard label="Cash" value={cash != null ? fmtUSD(cash) : '--'} />
                    <KPICard label="Unrealized P&L" value={unrealPnl != null ? fmtUSD(unrealPnl) : '--'} positive={unrealPnl != null ? unrealPnl >= 0 : undefined} />
                    <KPICard label="Realized P&L" value={realPnl != null ? fmtUSD(realPnl) : '--'} positive={realPnl != null ? realPnl >= 0 : undefined} />
                    {isSimAccount(account) && (
                      <KPICard label="Total Return" value={account.total_return_pct.toFixed(2)} suffix="%" positive={account.total_return_pct >= 0} />
                    )}
                  </>
                )}
              </div>
            </div>
          </ErrorBoundary>

          <ErrorBoundary>
            <div className="shell-panel p-5">
              <SectionHeading
                eyebrow="Launchpad"
                title="Move Fast"
                description="Shortest path into the next tool, depending on whether you are trading, researching, or validating."
              />
              <div className="mt-4 grid gap-3 sm:grid-cols-2">
                <ActionCard eyebrow="Execution" title="TradeBot" description="Review positions, recent trades, and automation state." onClick={() => navigateToRoute('tradebot')} />
                <ActionCard eyebrow="Research" title="Stock Profile" description="Jump from the active ticker into company detail and context." onClick={() => navigateToRoute('stock')} />
                <ActionCard eyebrow="Discovery" title="Screener" description="Scan broad universes and route winners straight into charts." onClick={() => navigateToRoute('screener')} />
                <ActionCard eyebrow="Autonomy" title="Autopilot" description="Inspect AI decisions, guardrails, and intervention queues." onClick={() => navigateToRoute('advisor')} active />
              </div>
            </div>
          </ErrorBoundary>
        </div>
      </section>

      <section className="shell-columns shell-columns--sidebar-tight">
        <ErrorBoundary>
          <div className="shell-panel overflow-hidden">
            <div className="flex flex-wrap items-center gap-3 border-b border-[var(--border)] px-5 py-4">
              <div>
                <div className="shell-kicker">Live chart</div>
                <div className="mt-1 flex items-center gap-2">
                  <span className="display-font text-[1.5rem] leading-none text-[var(--text-primary)]">{selectedSymbol}</span>
                  <span className="rounded-full border border-[var(--border)] px-2.5 py-1 text-[11px] font-semibold uppercase tracking-[0.16em] text-[var(--text-secondary)]">
                    {chartType}
                  </span>
                </div>
              </div>

              <div className="ml-auto flex flex-wrap items-center gap-2">
                {compMode && (
                  <input
                    value={compSymbol}
                    onChange={(event) => setCompSymbol(event.target.value.toUpperCase())}
                    placeholder="Compare vs MSFT"
                    className="rounded-2xl border border-[var(--border)] bg-[var(--bg-input)] px-3 py-2 text-sm text-[var(--text-primary)] outline-none transition-colors placeholder:text-[var(--text-muted)] focus:border-[var(--accent)]"
                  />
                )}
                <button
                  type="button"
                  onClick={toggleCompMode}
                  className={clsx(
                    'rounded-2xl border px-3 py-2 text-sm font-semibold transition-colors',
                    compMode
                      ? 'border-[rgba(245,158,11,0.3)] bg-[rgba(245,158,11,0.12)] text-[var(--accent)]'
                      : 'border-[var(--border)] bg-[var(--bg-hover)] text-[var(--text-secondary)] hover:border-[var(--accent)] hover:text-[var(--text-primary)]',
                  )}
                >
                  Compare
                </button>
                <button
                  type="button"
                  onClick={() => navigateToRoute('market')}
                  className="rounded-2xl border border-[var(--border)] bg-[var(--bg-hover)] px-3 py-2 text-sm font-semibold text-[var(--text-primary)] transition-colors hover:border-[var(--accent)] hover:text-[var(--accent)]"
                >
                  Full workspace
                </button>
              </div>
            </div>

            <div className="h-[34rem] min-h-[28rem]">
              <TradingChart symbol={selectedSymbol} className="h-full" />
            </div>
          </div>
        </ErrorBoundary>

        <div className="shell-stack">
          <ErrorBoundary>
            <div className="shell-panel p-5">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <div className="shell-kicker">Radar</div>
                  <h2 className="display-font mt-2 text-[1.5rem] leading-none text-[var(--text-primary)]">
                    Watchlist & Signals
                  </h2>
                  <p className="mt-1 text-sm text-[var(--text-secondary)]">
                    Stay focused on one density-heavy surface at a time.
                  </p>
                </div>
                <div className="inline-flex items-center gap-2 rounded-[999px] border border-[var(--border)] bg-[var(--bg-hover)] p-1">
                  {([
                    { id: 'watchlist', label: 'Watchlist' },
                    { id: 'signals', label: 'Signals' },
                  ] as const).map((tab) => (
                    <button
                      key={tab.id}
                      type="button"
                      onClick={() => setRadarTab(tab.id)}
                      className={clsx(
                        'rounded-[999px] px-3 py-1.5 text-xs font-semibold transition-colors',
                        radarTab === tab.id
                          ? 'bg-[var(--accent)] text-white shadow'
                          : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]',
                      )}
                    >
                      {tab.label}
                    </button>
                  ))}
                </div>
              </div>

              <div className="mt-4">
                {radarTab === 'watchlist' ? <WatchlistGrid /> : <OpportunityBoard />}
              </div>
            </div>
          </ErrorBoundary>
        </div>
      </section>

      {diagnosticsEnabled && (
        <section className="animate-fade-in-up" style={{ animationDelay: '100ms' }}>
          <SectionHeading
            eyebrow="Diagnostics"
            title="Market Diagnostics"
            description="Deep macro and breadth context stays out of the way until you want the fuller read."
            action={(
              <button
                type="button"
                onClick={() => setDiagnosticsExpanded((value) => !value)}
                className="rounded-2xl border border-[var(--border)] bg-[var(--bg-hover)] px-4 py-2 text-sm font-semibold text-[var(--text-primary)] transition-colors hover:border-[var(--accent)] hover:text-[var(--accent)]"
              >
                {diagnosticsExpanded ? 'Collapse diagnostics' : 'Expand diagnostics'}
              </button>
            )}
          />

          {!diagnosticsExpanded ? (
            <ErrorBoundary>
              <div className="shell-panel gradient-surface mt-4 p-5">
                <div className="flex flex-wrap items-center justify-between gap-4">
                  <div className="space-y-3">
                    <p className="text-sm leading-6 text-[var(--text-secondary)]">
                      Stay fast by default. Expand diagnostics when you need macro + breadth detail.
                    </p>
                    <div className="grid gap-2 sm:grid-cols-3">
                      <DiagnosticsChip
                        label="State"
                        value={overview?.state ?? '—'}
                        tone={overview?.state === 'GREEN' ? 'success' : overview?.state === 'YELLOW' ? 'warning' : overview?.state === 'RED' ? 'danger' : 'default'}
                      />
                      <DiagnosticsChip
                        label="Composite"
                        value={overview?.composite_score != null ? overview.composite_score.toFixed(2) : '—'}
                      />
                      <DiagnosticsChip
                        label="Indicators"
                        value={`${overview?.warn_count ?? 0} warn / ${overview?.stale_count ?? 0} stale`}
                        tone={(overview?.warn_count ?? 0) > 0 ? 'warning' : 'default'}
                      />
                    </div>
                    {overview?.last_run_ts && (
                      <p className="text-xs font-medium uppercase tracking-[0.16em] text-[var(--text-muted)]">
                        Last run {new Date(overview.last_run_ts).toLocaleString()}
                      </p>
                    )}
                  </div>
                  <button
                    type="button"
                    onClick={() => setDiagnosticsExpanded(true)}
                    className="rounded-2xl bg-[var(--accent)] px-4 py-3 text-sm font-semibold text-white transition-colors hover:bg-[var(--accent-hover)]"
                  >
                    Open full diagnostics
                  </button>
                </div>
              </div>
            </ErrorBoundary>
          ) : (
            <div className="mt-4 shell-stack">
              <ErrorBoundary>
                <DiagnosticHeaderRow
                  lookbackDays={lookbackDays}
                  onSetLookback={onSetLookback}
                  onRefresh={onManualRefresh}
                  refreshing={refreshing}
                  refreshRun={refreshRun}
                  lastRunTs={overview?.last_run_ts ?? refreshRun?.completed_at ?? undefined}
                />
              </ErrorBoundary>

              {diagnosticsError && (
                <div className="shell-panel border-[rgba(217,76,61,0.22)] bg-[rgba(217,76,61,0.08)] p-5 text-sm font-medium text-[var(--danger)]">
                  Diagnostics error: {diagnosticsError}
                </div>
              )}

              {diagnosticsLoading && !overview && indicators.length === 0 ? (
                <div className="shell-panel p-5 text-sm text-[var(--text-secondary)]">
                  Loading diagnostics...
                </div>
              ) : (
                <>
                  <ErrorBoundary>
                    <OverallSummaryCard overview={overview} />
                  </ErrorBoundary>
                  <ErrorBoundary>
                    <section className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-4">
                      <SystemOverviewWidget overview={overview} />
                      <DowTheoryWidget overview={overview} />
                      <SectorDivergenceWidget overview={overview} />
                      <AASWidget overview={overview} />
                    </section>
                  </ErrorBoundary>
                  <ErrorBoundary>
                    <IndicatorCardGrid indicators={indicators} />
                  </ErrorBoundary>
                  <ErrorBoundary>
                    <BubbleMarketMap rows={marketMap} />
                  </ErrorBoundary>
                  <ErrorBoundary>
                    <SectorProjectionsPanel projection={projections} />
                  </ErrorBoundary>
                  <ErrorBoundary>
                    <NewsStrip articles={news} />
                  </ErrorBoundary>
                </>
              )}
            </div>
          )}
        </section>
      )}
    </div>
  )
}

function MetricTile({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-[22px] border border-[var(--border)] bg-[var(--bg-hover)] px-4 py-3">
      <div className="shell-kicker">{label}</div>
      <div className="mt-2 text-lg font-semibold text-[var(--text-primary)]">{value}</div>
    </div>
  )
}

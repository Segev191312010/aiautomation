/**
 * AnalyticsPage — Professional Portfolio Analytics Dashboard
 *
 * Sections:
 *  1. Portfolio KPI Strip (total value, day P&L, total P&L, win rate, Sharpe, max DD)
 *  2. Equity Curve (lightweight-charts, SPY benchmark, date range selector)
 *  3. Daily P&L Bar Chart (lightweight-charts histogram, green/red bars)
 *  4. Position Exposure Panel (stacked bar, sector donut via conic-gradient, top-5 table)
 *  5. Risk Metrics Panel (limit gauges with color-coded progress bars)
 *  6. Trade History Summary (recent trades, win/loss bar, best/worst)
 *  7. Correlation Matrix (CSS grid, color-coded cells — shown when 3+ positions)
 */
import React, { useEffect, useRef, useMemo, useState, useCallback } from 'react'
import TradeBotTabs from '@/components/tradebot/TradeBotTabs'
import DegradedStateCard from '@/components/common/DegradedStateCard'
import ErrorBoundary from '@/components/ui/ErrorBoundary'
import { SectionHeader } from '@/components/common/SectionHeader'
import {
  IconTrendUp,
  IconTrendDown,
  IconDollar,
  IconShield,
  IconPieChart,
  IconHistory,
  IconGrid,
  IconBarChart,
} from '@/components/icons'
import { KpiCard, KpiSkeleton, type KpiCardProps } from '@/components/analytics/KpiCard'
import { EquityCurveChart, type DateRange } from '@/components/analytics/EquityCurveChart'
import { DailyPnLChart } from '@/components/analytics/DailyPnLChart'
import { ExposurePanel } from '@/components/analytics/ExposurePanel'
import { RiskGauge } from '@/components/analytics/RiskGauge'
import { TradeHistoryPanel } from '@/components/analytics/TradeHistoryPanel'
import { CorrelationMatrixPanel } from '@/components/analytics/CorrelationMatrixPanel'
import { fmtUSD, fmtUSDCompact, fmtPct } from '@/utils/formatters'
import {
  useAccountStore,
  useBotStore,
  useSimStore,
} from '@/store'
import {
  fetchPortfolioAnalytics,
  fetchDailyPnL,
  fetchExposureBreakdown,
  fetchRiskLimits,
  fetchTradeHistory,
  fetchCorrelationMatrix,
} from '@/services/api'
import type {
  PortfolioAnalytics,
  DailyPnL,
  ExposureBreakdown,
  RiskLimits,
  TradeHistoryRow,
  CorrelationMatrix,
  AccountSummary,
} from '@/types'

// ─────────────────────────────────────────────────────────────────────────────
// Helpers (page-local, not worth extracting)
// ─────────────────────────────────────────────────────────────────────────────

type SectionStatus = 'loading' | 'loaded' | 'unavailable'
type AnalyticsTab  = 'performance' | 'risk' | 'positions' | 'history'

function warnSectionFetchFailure(section: string, error: unknown) {
  console.warn(`[AnalyticsPage] ${section} fetch failed`, error)
}

function isCorrelationMatrixPayload(value: unknown): value is CorrelationMatrix {
  if (!value || typeof value !== 'object') return false
  const payload = value as { symbols?: unknown; matrix?: unknown; error?: unknown }
  if (typeof payload.error === 'string' && payload.error.length > 0) return false
  if (!Array.isArray(payload.symbols) || !Array.isArray(payload.matrix)) return false
  const symbols = payload.symbols as unknown[]
  const matrix  = payload.matrix  as unknown[]
  if (!symbols.every((s) => typeof s === 'string' && s.length > 0)) return false
  if (symbols.length < 3) return matrix.length === 0
  if (matrix.length !== symbols.length) return false
  return matrix.every(
    (row) => Array.isArray(row)
      && row.length === symbols.length
      && row.every((cell) => typeof cell === 'number' && Number.isFinite(cell)),
  )
}

function AnalyticsSignalCard({
  label,
  value,
  tone = 'default',
}: {
  label: string
  value: string
  tone?: 'default' | 'success' | 'warning'
}) {
  const toneClass =
    tone === 'success'
      ? 'border-[rgba(31,157,104,0.18)] bg-[rgba(31,157,104,0.1)] text-[var(--success)]'
      : tone === 'warning'
        ? 'border-[rgba(245,158,11,0.2)] bg-[rgba(245,158,11,0.1)] text-[var(--accent)]'
        : 'border-[var(--border)] bg-[var(--bg-hover)] text-[var(--text-primary)]'

  return (
    <div className={`rounded-[24px] border p-4 ${toneClass}`}>
      <div className="shell-kicker">{label}</div>
      <div className="mt-3 text-2xl font-semibold leading-none">{value}</div>
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// Page
// ─────────────────────────────────────────────────────────────────────────────

export default function AnalyticsPage() {
  const { account }    = useAccountStore()
  const { simMode }    = useBotStore()
  const { simAccount } = useSimStore()

  const [range, setRange]             = useState<DateRange>('3M')
  const [loading, setLoading]         = useState(true)
  const [analytics, setAnalytics]     = useState<PortfolioAnalytics | null>(null)
  const [dailyPnL, setDailyPnL]       = useState<DailyPnL[] | null>(null)
  const [exposure, setExposure]       = useState<ExposureBreakdown | null>(null)
  const [riskLimits, setRiskLimits]   = useState<RiskLimits | null>(null)
  const [tradeHist, setTradeHist]     = useState<TradeHistoryRow[] | null>(null)
  const [correlation, setCorrelation] = useState<CorrelationMatrix | null>(null)

  const [portfolioStatus,    setPortfolioStatus]    = useState<SectionStatus>('loading')
  const [dailyPnlStatus,     setDailyPnlStatus]     = useState<SectionStatus>('loading')
  const [exposureStatus,     setExposureStatus]     = useState<SectionStatus>('loading')
  const [riskLimitsStatus,   setRiskLimitsStatus]   = useState<SectionStatus>('loading')
  const [tradeHistoryStatus, setTradeHistoryStatus] = useState<SectionStatus>('loading')
  const [correlationStatus,  setCorrelationStatus]  = useState<SectionStatus>('loading')

  const [analyticsTab, setAnalyticsTab] = useState<AnalyticsTab>('performance')

  const displayAccount = simMode ? simAccount : account
  const loadGenRef = useRef(0)

  const loadAll = useCallback(async () => {
    const gen = ++loadGenRef.current
    setLoading(true)
    setPortfolioStatus('loading')
    setDailyPnlStatus('loading')
    setExposureStatus('loading')
    setRiskLimitsStatus('loading')
    setTradeHistoryStatus('loading')
    setCorrelationStatus('loading')

    const [r0, r1, r2, r3, r4, r5] = await Promise.allSettled([
      fetchPortfolioAnalytics(range),
      fetchDailyPnL(90),
      fetchExposureBreakdown(),
      fetchRiskLimits(),
      fetchTradeHistory(20),
      fetchCorrelationMatrix(),
    ])

    // Stale guard: if a newer loadAll() was triggered, discard this result
    if (gen !== loadGenRef.current) return

    if (r0.status === 'fulfilled') { setAnalytics(r0.value); setPortfolioStatus('loaded') }
    else { setAnalytics(null); setPortfolioStatus('unavailable'); warnSectionFetchFailure('portfolio', r0.reason) }

    if (r1.status === 'fulfilled') { setDailyPnL(r1.value); setDailyPnlStatus('loaded') }
    else { setDailyPnL(null); setDailyPnlStatus('unavailable'); warnSectionFetchFailure('daily_pnl', r1.reason) }

    if (r2.status === 'fulfilled') { setExposure(r2.value); setExposureStatus('loaded') }
    else { setExposure(null); setExposureStatus('unavailable'); warnSectionFetchFailure('exposure', r2.reason) }

    if (r3.status === 'fulfilled') { setRiskLimits(r3.value); setRiskLimitsStatus('loaded') }
    else { setRiskLimits(null); setRiskLimitsStatus('unavailable'); warnSectionFetchFailure('risk_limits', r3.reason) }

    if (r4.status === 'fulfilled') { setTradeHist(r4.value); setTradeHistoryStatus('loaded') }
    else { setTradeHist(null); setTradeHistoryStatus('unavailable'); warnSectionFetchFailure('trade_history', r4.reason) }

    if (r5.status === 'fulfilled' && isCorrelationMatrixPayload(r5.value)) {
      setCorrelation(r5.value)
      setCorrelationStatus('loaded')
    } else {
      setCorrelation(null)
      setCorrelationStatus('unavailable')
      warnSectionFetchFailure(
        'correlation',
        r5.status === 'fulfilled' ? new Error('Invalid correlation payload') : r5.reason,
      )
    }

    setLoading(false)
  }, [range])

  useEffect(() => { void loadAll() }, [loadAll])

  // Override portfolio value with live account data when available
  const liveAnalytics = useMemo<PortfolioAnalytics | null>(() => {
    if (!analytics) return null
    if (!displayAccount) return analytics
    const netLiq = 'net_liquidation' in displayAccount
      ? displayAccount.net_liquidation
      : (displayAccount as AccountSummary).balance
    const prevValue = analytics.equity_curve.length >= 2
      ? analytics.equity_curve[analytics.equity_curve.length - 2].value
      : netLiq
    const dayPnl = netLiq - prevValue
    return {
      ...analytics,
      total_value:   netLiq,
      day_pnl:       dayPnl,
      day_pnl_pct:   prevValue > 0 ? (dayPnl / prevValue) * 100 : 0,
      total_pnl:     displayAccount.unrealized_pnl + (displayAccount.realized_pnl ?? 0),
      total_pnl_pct: netLiq > 0
        ? ((displayAccount.unrealized_pnl + (displayAccount.realized_pnl ?? 0)) / netLiq) * 100
        : analytics.total_pnl_pct,
    }
  }, [analytics, displayAccount])

  const kpis = useMemo<KpiCardProps[]>(() => liveAnalytics ? [
    {
      label:       'Portfolio Value',
      value:       fmtUSDCompact(liveAnalytics.total_value),
      icon:        <IconDollar className="w-3.5 h-3.5 text-indigo-600" />,
      iconBg:      'bg-indigo-50',
      accentColor: 'border-l-indigo-500/60',
    },
    {
      label:    'Day P&L',
      value:    (liveAnalytics.day_pnl >= 0 ? '+' : '') + fmtUSD(liveAnalytics.day_pnl),
      sub:      fmtPct(liveAnalytics.day_pnl_pct),
      positive: liveAnalytics.day_pnl >= 0,
      icon:     liveAnalytics.day_pnl >= 0
        ? <IconTrendUp   className="w-3.5 h-3.5 text-emerald-600" />
        : <IconTrendDown className="w-3.5 h-3.5 text-red-400" />,
      iconBg:      liveAnalytics.day_pnl >= 0 ? 'bg-emerald-50' : 'bg-red-500/10',
      accentColor: liveAnalytics.day_pnl >= 0 ? 'border-l-emerald-500/60' : 'border-l-red-500/60',
    },
    {
      label:    'Total P&L',
      value:    (liveAnalytics.total_pnl >= 0 ? '+' : '') + fmtUSD(liveAnalytics.total_pnl),
      sub:      fmtPct(liveAnalytics.total_pnl_pct),
      positive: liveAnalytics.total_pnl >= 0,
      icon:     liveAnalytics.total_pnl >= 0
        ? <IconTrendUp   className="w-3.5 h-3.5 text-emerald-600" />
        : <IconTrendDown className="w-3.5 h-3.5 text-red-400" />,
      iconBg:      liveAnalytics.total_pnl >= 0 ? 'bg-emerald-50' : 'bg-red-500/10',
      accentColor: liveAnalytics.total_pnl >= 0 ? 'border-l-emerald-500/60' : 'border-l-red-500/60',
    },
    {
      label:    'Win Rate',
      value:    liveAnalytics.win_rate.toFixed(1) + '%',
      positive: liveAnalytics.win_rate >= 50,
      icon:     <IconBarChart className="w-3.5 h-3.5 text-blue-500" />,
      iconBg:      'bg-blue-50',
      accentColor: 'border-l-blue-500/40',
    },
    {
      label:    'Sharpe Ratio',
      value:    liveAnalytics.sharpe_ratio.toFixed(2),
      positive: liveAnalytics.sharpe_ratio >= 1 ? true : liveAnalytics.sharpe_ratio >= 0 ? undefined : false,
      icon:     <IconShield className="w-3.5 h-3.5 text-violet-500" />,
      iconBg:      'bg-violet-50',
      accentColor: 'border-l-violet-400/50',
    },
    {
      label:    'Max Drawdown',
      value:    liveAnalytics.max_drawdown_pct.toFixed(1) + '%',
      positive: liveAnalytics.max_drawdown_pct >= -5 ? true : liveAnalytics.max_drawdown_pct >= -15 ? undefined : false,
      icon:     <IconTrendDown className="w-3.5 h-3.5 text-rose-500" />,
      iconBg:      'bg-rose-50',
      accentColor: 'border-l-rose-400/50',
    },
  ] : [], [liveAnalytics])

  const unavailableSections = useMemo(() => [
    portfolioStatus    === 'unavailable' ? 'portfolio KPIs'    : null,
    dailyPnlStatus     === 'unavailable' ? 'daily P&L'         : null,
    exposureStatus     === 'unavailable' ? 'position exposure' : null,
    riskLimitsStatus   === 'unavailable' ? 'risk limits'       : null,
    tradeHistoryStatus === 'unavailable' ? 'trade history'     : null,
    correlationStatus  === 'unavailable' ? 'correlation matrix': null,
  ].filter((s): s is string => s !== null), [
    portfolioStatus, dailyPnlStatus, exposureStatus,
    riskLimitsStatus, tradeHistoryStatus, correlationStatus,
  ])

  const showCorrelation = useMemo(
    () => correlation !== null
      && correlation.symbols.length >= 3
      && isCorrelationMatrixPayload(correlation),
    [correlation],
  )

  const handleTabChange = useCallback(
    (t: string) => setAnalyticsTab(t as AnalyticsTab),
    [],
  )

  return (
    <div className="flex flex-col gap-6 pb-4">
      <ErrorBoundary>
        <section className="grid gap-6 xl:grid-cols-[minmax(0,1.25fr)_minmax(320px,0.75fr)]">
          <div className="shell-panel relative overflow-hidden p-6 sm:p-7">
            <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_top_right,rgba(245,158,11,0.16),transparent_34%)]" />
            <div className="relative">
              <div className="shell-kicker">Performance intelligence</div>
              <div className="mt-3 flex flex-wrap items-center gap-2.5">
                <h1 className="display-font text-[2.7rem] leading-none text-[var(--text-primary)] sm:text-[3.2rem]">
                  Portfolio Analytics
                </h1>
                <span className="shell-chip text-[11px] font-semibold">
                  {simMode ? 'Simulation mode' : 'Live account'}
                </span>
                <span className="shell-chip text-[11px] font-semibold">
                  Range {range}
                </span>
                <span className="shell-chip text-[11px] font-semibold">
                  {loading ? 'Refreshing' : 'Stable'}
                </span>
              </div>

              <p className="mt-4 max-w-3xl text-sm leading-7 text-[var(--text-secondary)]">
                Measure realized performance, risk posture, and exposure without inventing placeholder data when feeds go down.
                The shell stays polished while unavailable sections remain explicit.
              </p>

              <div className="mt-5 flex flex-wrap gap-2">
                <span className="shell-chip text-[11px] font-medium">
                  {6 - unavailableSections.length}/6 sections available
                </span>
                {liveAnalytics && (
                  <span className="shell-chip text-[11px] font-medium">
                    Value {fmtUSDCompact(liveAnalytics.total_value)}
                  </span>
                )}
                {unavailableSections.length > 0 && (
                  <span className="shell-chip text-[11px] font-medium">
                    Partial availability
                  </span>
                )}
              </div>
            </div>
          </div>

          <div className="grid gap-4 sm:grid-cols-3 xl:grid-cols-1">
            <AnalyticsSignalCard
              label="Coverage"
              value={`${6 - unavailableSections.length}/6`}
              tone={unavailableSections.length === 0 ? 'success' : 'warning'}
            />
            <AnalyticsSignalCard
              label="Mode"
              value={simMode ? 'Simulation' : 'Live'}
              tone={simMode ? 'warning' : 'default'}
            />
            <AnalyticsSignalCard
              label="Range"
              value={range}
              tone="default"
            />
          </div>
        </section>
      </ErrorBoundary>

      <ErrorBoundary>
        {unavailableSections.length > 0 && !loading && (
          <DegradedStateCard
            title="Analytics data partially unavailable"
            reason={`Unavailable sections: ${unavailableSections.join(', ')}.`}
            description="Only sections backed by live API data are rendered. No placeholder analytics values are shown."
          />
        )}
      </ErrorBoundary>

      <ErrorBoundary>
        <section className="shell-panel p-5 sm:p-6">
          <div className="flex flex-wrap items-end justify-between gap-4">
            <div className="max-w-2xl">
              <div className="shell-kicker">KPI strip</div>
              <h2 className="display-font mt-2 text-[1.75rem] leading-none text-[var(--text-primary)]">
                Portfolio KPIs
              </h2>
              <p className="mt-2 text-sm leading-6 text-[var(--text-secondary)]">
                Front-and-center metrics for value, P&amp;L, hit rate, and risk-adjusted performance.
              </p>
            </div>
          </div>

          {loading && portfolioStatus === 'loading' && !liveAnalytics ? (
            <div className="mt-5 grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-6">
              {Array.from({ length: 6 }).map((_, i) => <KpiSkeleton key={i} />)}
            </div>
          ) : liveAnalytics ? (
            <div className="mt-5 grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-6">
              {kpis.map((k) => <KpiCard key={k.label} {...k} />)}
            </div>
          ) : (
            <div className="mt-5">
              <DegradedStateCard
                title="Portfolio KPIs unavailable"
                reason="Portfolio analytics could not be loaded from the backend."
                description="The KPI strip is hidden until live portfolio analytics are available."
              />
            </div>
          )}
        </section>
      </ErrorBoundary>

      <ErrorBoundary>
        <section className="animate-fade-in-up" style={{ animationDelay: '40ms' }}>
          <div className="flex flex-wrap items-center justify-between gap-4">
            <div>
              <div className="shell-kicker">Workspace</div>
              <p className="mt-2 max-w-xl text-sm leading-6 text-[var(--text-secondary)]">
                Move between performance, risk, exposure, and trade-history views without reloading the analytics surface.
              </p>
            </div>

            <TradeBotTabs
              activeTab={analyticsTab}
              onTabChange={handleTabChange}
              tabs={[
                { id: 'performance', label: 'Performance' },
                { id: 'risk', label: 'Risk' },
                { id: 'positions', label: 'Positions' },
                { id: 'history', label: 'History' },
              ]}
            />
          </div>
        </section>
      </ErrorBoundary>

      {analyticsTab === 'performance' && (
        <div className="grid gap-6 xl:grid-cols-[minmax(0,1.2fr)_minmax(320px,0.8fr)]">
          <ErrorBoundary>
            <section className="shell-panel animate-fade-in-up p-5 sm:p-6" style={{ animationDelay: '40ms' }}>
              <SectionHeader
                icon={<IconTrendUp className="h-3.5 w-3.5 text-indigo-500" />}
                eyebrow="Performance"
                title="Equity Curve"
                badge={liveAnalytics?.benchmark_curve.length
                  ? <span className="shell-chip px-3 py-1 text-[10px] font-mono">vs SPY</span>
                  : null}
              />
              {loading && portfolioStatus === 'loading' && !liveAnalytics ? (
                <div className="h-[300px] animate-pulse rounded-[24px] bg-[var(--bg-hover)]" />
              ) : liveAnalytics ? (
                <EquityCurveChart analytics={liveAnalytics} range={range} onRangeChange={setRange} />
              ) : (
                <DegradedStateCard
                  title="Equity curve unavailable"
                  reason="Equity-curve data could not be loaded for the selected range."
                  description="The range selector remains available, but the chart is hidden until live data returns."
                />
              )}
            </section>
          </ErrorBoundary>

          <ErrorBoundary>
            <section className="shell-panel animate-fade-in-up p-5 sm:p-6" style={{ animationDelay: '80ms' }}>
              <SectionHeader
                icon={<IconBarChart className="h-3.5 w-3.5 text-emerald-500" />}
                eyebrow="Daily"
                title="Daily P&L"
                badge={<span className="shell-chip px-3 py-1 text-[10px] font-mono">90 days</span>}
              />
              {loading && dailyPnlStatus === 'loading' && !dailyPnL ? (
                <div className="h-[220px] animate-pulse rounded-[24px] bg-[var(--bg-hover)]" />
              ) : dailyPnL ? (
                <DailyPnLChart data={dailyPnL} />
              ) : (
                <DegradedStateCard
                  title="Daily P&L unavailable"
                  reason="Daily realized performance data could not be loaded."
                  description="No fallback bars are rendered when this feed is unavailable."
                />
              )}
            </section>
          </ErrorBoundary>
        </div>
      )}

      {analyticsTab === 'positions' && (
        <ErrorBoundary>
          <section className="shell-panel animate-fade-in-up p-5 sm:p-6" style={{ animationDelay: '120ms' }}>
            <SectionHeader
              icon={<IconPieChart className="h-3.5 w-3.5 text-indigo-500" />}
              eyebrow="Allocation"
              title="Position Exposure"
              badge={exposure && (
                <span className="shell-chip px-3 py-1 text-[10px] font-mono">
                  {exposure.positions.length} positions
                </span>
              )}
            />
            {loading && exposureStatus === 'loading' && !exposure ? (
              <div className="space-y-3 animate-pulse">
                <div className="h-6 rounded-full bg-[var(--bg-hover)]" />
                <div className="h-24 rounded-[24px] bg-[var(--bg-hover)]" />
              </div>
            ) : exposure ? (
              <ExposurePanel exposure={exposure} />
            ) : (
              <DegradedStateCard
                title="Position exposure unavailable"
                reason="Open-position allocation data could not be loaded."
                description="Sector and symbol exposure are hidden until live portfolio data is available."
              />
            )}
          </section>
        </ErrorBoundary>
      )}

      {analyticsTab === 'risk' && (
        <div className="grid gap-6 xl:grid-cols-[minmax(0,0.9fr)_minmax(360px,1.1fr)]">
          <ErrorBoundary>
            <section className="shell-panel animate-fade-in-up p-5 sm:p-6" style={{ animationDelay: '120ms' }}>
              <SectionHeader
                icon={<IconShield className="h-3.5 w-3.5 text-rose-500" />}
                eyebrow="Risk Management"
                title="Risk Limit Usage"
              />
              {loading && riskLimitsStatus === 'loading' && !riskLimits ? (
                <div className="space-y-4 animate-pulse">
                  {Array.from({ length: 4 }).map((_, i) => (
                    <div key={i} className="flex flex-col gap-1.5">
                      <div className="h-3 w-40 rounded bg-[var(--bg-hover)]" />
                      <div className="h-2 rounded-full bg-[var(--bg-hover)]" />
                    </div>
                  ))}
                </div>
              ) : riskLimits ? (
                <div className="flex flex-col gap-5">
                  {riskLimits.limits.map((item) => (
                    <RiskGauge key={item.label} item={item} />
                  ))}
                  <p className="border-t border-[var(--border)] pt-1 text-[10px] font-sans text-[var(--text-muted)]">
                    Bars turn amber at 60% and red at 80% of each limit.
                  </p>
                </div>
              ) : (
                <DegradedStateCard
                  title="Risk limits unavailable"
                  reason="Risk-limit usage could not be loaded from the backend."
                  description="The gauge panel stays hidden instead of showing placeholder utilization."
                />
              )}
            </section>
          </ErrorBoundary>

          <ErrorBoundary>
            <section className="shell-panel animate-fade-in-up p-5 sm:p-6" style={{ animationDelay: '200ms' }}>
              <SectionHeader
                icon={<IconGrid className="h-3.5 w-3.5 text-slate-500" />}
                eyebrow="Diversification"
                title="Correlation Matrix"
                badge={showCorrelation && correlation ? (
                  <span className="shell-chip px-3 py-1 text-[10px] font-mono">
                    {correlation.symbols.length} assets
                  </span>
                ) : null}
              />
              {loading && correlationStatus === 'loading' && !correlation ? (
                <div className="flex items-center justify-center py-8 text-sm text-[var(--text-muted)]">
                  Loading correlation matrix...
                </div>
              ) : correlationStatus === 'unavailable' ? (
                <DegradedStateCard
                  title="Correlation matrix unavailable"
                  reason="Correlation data could not be loaded from the backend."
                  description="Correlation is shown only when live matrix data is available."
                />
              ) : showCorrelation && correlation ? (
                <CorrelationMatrixPanel matrix={correlation} />
              ) : (
                <div className="flex items-center justify-center py-8 text-sm text-[var(--text-muted)]">
                  Correlation unavailable - at least 3 symbols are required.
                </div>
              )}
            </section>
          </ErrorBoundary>
        </div>
      )}

      {analyticsTab === 'history' && (
        <ErrorBoundary>
          <section className="shell-panel animate-fade-in-up p-5 sm:p-6" style={{ animationDelay: '160ms' }}>
            <SectionHeader
              icon={<IconHistory className="h-3.5 w-3.5 text-[var(--text-secondary)]" />}
              eyebrow="Trades"
              title="Trade History Summary"
              badge={tradeHist && (
                <span className="shell-chip px-3 py-1 text-[10px] font-mono">
                  Last {Math.min(tradeHist.length, 20)}
                </span>
              )}
            />
            {loading && tradeHistoryStatus === 'loading' && !tradeHist ? (
              <div className="space-y-2.5 animate-pulse">
                {Array.from({ length: 5 }).map((_, i) => (
                  <div key={i} className="flex gap-3">
                    <div className="h-3 w-16 rounded bg-[var(--bg-hover)]" />
                    <div className="h-3 w-12 rounded bg-[var(--bg-hover)]" />
                    <div className="h-5 w-10 rounded bg-[var(--bg-hover)]" />
                    <div className="ml-auto h-3 w-8 rounded bg-[var(--bg-hover)]" />
                    <div className="h-3 w-20 rounded bg-[var(--bg-hover)]" />
                  </div>
                ))}
              </div>
            ) : tradeHist ? (
              <TradeHistoryPanel trades={tradeHist} />
            ) : (
              <DegradedStateCard
                title="Trade history unavailable"
                reason="Recent-trade analytics could not be loaded."
                description="The history panel stays hidden until live trade data is available."
              />
            )}
          </section>
        </ErrorBoundary>
      )}
    </div>
  )
}

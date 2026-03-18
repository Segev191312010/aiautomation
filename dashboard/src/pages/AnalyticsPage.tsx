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
import clsx from 'clsx'
import {
  createChart,
  type IChartApi,
  type ISeriesApi,
  type LineData,
  type HistogramData,
} from 'lightweight-charts'
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
// Mock fallback data (renders when backend endpoints are not yet implemented)
// ─────────────────────────────────────────────────────────────────────────────

function buildMockEquityCurve(days: number, start: number) {
  const now = Math.floor(Date.now() / 1000)
  const DAY = 86400
  const pts: { time: number; value: number }[] = []
  let v = start
  for (let i = days; i >= 0; i--) {
    v = v * (1 + (Math.random() - 0.47) * 0.012)
    pts.push({ time: now - i * DAY, value: Math.round(v * 100) / 100 })
  }
  return pts
}

const _mockEquity = buildMockEquityCurve(90, 100_000)

const MOCK_PORTFOLIO: PortfolioAnalytics = (() => {
  const eq = _mockEquity
  const final = eq[eq.length - 1].value
  const prev  = eq[eq.length - 2].value
  let bv = 100_000
  const bench = eq.map((pt) => {
    bv = bv * (1 + (Math.random() - 0.465) * 0.009)
    return { time: pt.time, value: Math.round(bv * 100) / 100 }
  })
  return {
    total_value:      final,
    day_pnl:          final - prev,
    day_pnl_pct:      ((final - prev) / prev) * 100,
    total_pnl:        final - 100_000,
    total_pnl_pct:    ((final - 100_000) / 100_000) * 100,
    win_rate:         58.3,
    sharpe_ratio:     1.42,
    max_drawdown_pct: -8.7,
    equity_curve:     eq,
    benchmark_curve:  bench,
  }
})()

const MOCK_DAILY_PNL: DailyPnL[] = (() => {
  const result: DailyPnL[] = []
  const now = new Date()
  for (let i = 89; i >= 0; i--) {
    const d = new Date(now)
    d.setDate(d.getDate() - i)
    result.push({
      date:   d.toISOString().slice(0, 10),
      pnl:    Math.round((Math.random() - 0.44) * 3200 * 100) / 100,
      trades: Math.floor(Math.random() * 5),
    })
  }
  return result
})()

const MOCK_EXPOSURE: ExposureBreakdown = {
  positions: [
    { symbol: 'NVDA', sector: 'Technology', value: 24_500, weight_pct: 24.5, pnl:  3_200, pnl_pct: 15.1 },
    { symbol: 'AAPL', sector: 'Technology', value: 18_200, weight_pct: 18.2, pnl:  1_100, pnl_pct:  6.4 },
    { symbol: 'SPY',  sector: 'ETF',        value: 15_000, weight_pct: 15.0, pnl:    820, pnl_pct:  5.8 },
    { symbol: 'MSFT', sector: 'Technology', value: 14_300, weight_pct: 14.3, pnl:   -420, pnl_pct: -2.9 },
    { symbol: 'JPM',  sector: 'Financials', value: 12_800, weight_pct: 12.8, pnl:    560, pnl_pct:  4.6 },
    { symbol: 'XOM',  sector: 'Energy',     value:  8_600, weight_pct:  8.6, pnl:   -310, pnl_pct: -3.5 },
    { symbol: 'JNJ',  sector: 'Healthcare', value:  6_600, weight_pct:  6.6, pnl:    180, pnl_pct:  2.8 },
  ],
  sector_weights: {
    Technology: 57.0,
    ETF:        15.0,
    Financials: 12.8,
    Energy:      8.6,
    Healthcare:  6.6,
  },
}

const MOCK_RISK_LIMITS: RiskLimits = {
  max_position_size_pct: 25,
  daily_loss_limit: -2_000,
  drawdown_limit_pct: -15,
  max_open_positions: 10,
  limits: [
    { label: 'Max Position Size', used: 24.5,  limit: 25,      unit: '%'     },
    { label: 'Daily Loss',        used: -340,   limit: -2_000,  unit: '$'     },
    { label: 'Max Drawdown',      used: -8.7,   limit: -15,     unit: '%'     },
    { label: 'Open Positions',    used: 7,      limit: 10,      unit: 'count' },
  ],
}

const MOCK_TRADES: TradeHistoryRow[] = [
  { id: '1',  symbol: 'NVDA',  action: 'SELL', quantity: 20, fill_price: 890.40, pnl:  3_200, timestamp: new Date(Date.now() - 1  * 86400000).toISOString(), holding_days: 14 },
  { id: '2',  symbol: 'AAPL',  action: 'BUY',  quantity: 50, fill_price: 192.30,              timestamp: new Date(Date.now() - 2  * 86400000).toISOString()                  },
  { id: '3',  symbol: 'MSFT',  action: 'SELL', quantity: 30, fill_price: 415.20, pnl:   -420, timestamp: new Date(Date.now() - 3  * 86400000).toISOString(), holding_days:  5 },
  { id: '4',  symbol: 'SPY',   action: 'BUY',  quantity: 40, fill_price: 520.10,              timestamp: new Date(Date.now() - 4  * 86400000).toISOString()                  },
  { id: '5',  symbol: 'JPM',   action: 'SELL', quantity: 25, fill_price: 202.70, pnl:    560, timestamp: new Date(Date.now() - 5  * 86400000).toISOString(), holding_days:  8 },
  { id: '6',  symbol: 'XOM',   action: 'SELL', quantity: 45, fill_price: 118.30, pnl:   -310, timestamp: new Date(Date.now() - 6  * 86400000).toISOString(), holding_days:  3 },
  { id: '7',  symbol: 'META',  action: 'SELL', quantity: 15, fill_price: 520.60, pnl:  1_840, timestamp: new Date(Date.now() - 7  * 86400000).toISOString(), holding_days: 21 },
  { id: '8',  symbol: 'TSLA',  action: 'SELL', quantity: 30, fill_price: 248.90, pnl: -1_200, timestamp: new Date(Date.now() - 8  * 86400000).toISOString(), holding_days:  6 },
  { id: '9',  symbol: 'AMZN',  action: 'SELL', quantity: 20, fill_price: 205.40, pnl:    940, timestamp: new Date(Date.now() - 9  * 86400000).toISOString(), holding_days: 11 },
  { id: '10', symbol: 'GOOGL', action: 'SELL', quantity: 35, fill_price: 175.20, pnl:    720, timestamp: new Date(Date.now() - 10 * 86400000).toISOString(), holding_days:  9 },
]

const MOCK_CORRELATION: CorrelationMatrix = {
  symbols: ['NVDA', 'AAPL', 'MSFT', 'SPY', 'JPM'],
  matrix: [
    [1.00, 0.72, 0.68, 0.61, 0.29],
    [0.72, 1.00, 0.81, 0.74, 0.35],
    [0.68, 0.81, 1.00, 0.78, 0.42],
    [0.61, 0.74, 0.78, 1.00, 0.58],
    [0.29, 0.35, 0.42, 0.58, 1.00],
  ],
}

// ─────────────────────────────────────────────────────────────────────────────
// Formatters
// ─────────────────────────────────────────────────────────────────────────────

function fmtUSD(v: number): string {
  return new Intl.NumberFormat('en-US', {
    style: 'currency', currency: 'USD',
    minimumFractionDigits: 2, maximumFractionDigits: 2,
  }).format(v)
}

function fmtUSDCompact(v: number): string {
  const abs  = Math.abs(v)
  const sign = v < 0 ? '-' : ''
  if (abs >= 1_000_000) return `${sign}$${(abs / 1_000_000).toFixed(2)}M`
  if (abs >= 1_000)     return `${sign}$${(abs / 1_000).toFixed(1)}K`
  return fmtUSD(v)
}

function fmtPct(v: number, decimals = 2): string {
  return (v >= 0 ? '+' : '') + v.toFixed(decimals) + '%'
}

function fmtDate(ts: string): string {
  const d = new Date(ts)
  const today = new Date()
  const isToday =
    d.getFullYear() === today.getFullYear() &&
    d.getMonth() === today.getMonth() &&
    d.getDate() === today.getDate()
  if (isToday) {
    return d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', hour12: false })
  }
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
}

// ─────────────────────────────────────────────────────────────────────────────
// Icons
// ─────────────────────────────────────────────────────────────────────────────

function IconTrendUp({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.75} strokeLinecap="round" strokeLinejoin="round" className={className}>
      <polyline points="23 6 13.5 15.5 8.5 10.5 1 18" />
      <polyline points="17 6 23 6 23 12" />
    </svg>
  )
}

function IconTrendDown({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.75} strokeLinecap="round" strokeLinejoin="round" className={className}>
      <polyline points="23 18 13.5 8.5 8.5 13.5 1 6" />
      <polyline points="17 18 23 18 23 12" />
    </svg>
  )
}

function IconDollar({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.75} strokeLinecap="round" strokeLinejoin="round" className={className}>
      <line x1="12" y1="1" x2="12" y2="23" />
      <path d="M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6" />
    </svg>
  )
}

function IconShield({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.75} strokeLinecap="round" strokeLinejoin="round" className={className}>
      <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
    </svg>
  )
}

function IconPieChart({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.75} strokeLinecap="round" strokeLinejoin="round" className={className}>
      <path d="M21.21 15.89A10 10 0 1 1 8 2.83" />
      <path d="M22 12A10 10 0 0 0 12 2v10z" />
    </svg>
  )
}

function IconHistory({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.75} strokeLinecap="round" strokeLinejoin="round" className={className}>
      <polyline points="1 4 1 10 7 10" />
      <path d="M3.51 15a9 9 0 1 0 .49-4.95L1 10" />
    </svg>
  )
}

function IconGrid({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.75} strokeLinecap="round" strokeLinejoin="round" className={className}>
      <rect x="3" y="3" width="7" height="7" />
      <rect x="14" y="3" width="7" height="7" />
      <rect x="14" y="14" width="7" height="7" />
      <rect x="3" y="14" width="7" height="7" />
    </svg>
  )
}

function IconBarChart({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.75} strokeLinecap="round" strokeLinejoin="round" className={className}>
      <line x1="18" y1="20" x2="18" y2="10" />
      <line x1="12" y1="20" x2="12" y2="4" />
      <line x1="6" y1="20" x2="6" y2="14" />
    </svg>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// Shared: Section header
// ─────────────────────────────────────────────────────────────────────────────

function SectionHeader({
  icon, eyebrow, title, badge, action,
}: {
  icon?: React.ReactNode
  eyebrow: string
  title: string
  badge?: React.ReactNode
  action?: React.ReactNode
}) {
  return (
    <div className="flex items-center gap-2.5 mb-4">
      {icon && (
        <div className="w-7 h-7 rounded-lg bg-zinc-800/60 flex items-center justify-center flex-shrink-0">
          {icon}
        </div>
      )}
      <div className="min-w-0">
        <div className="text-[10px] font-sans uppercase tracking-[0.2em] text-zinc-500">{eyebrow}</div>
        <h2 className="text-sm font-sans font-semibold text-zinc-100 tracking-wide">{title}</h2>
      </div>
      {badge && <div className="ml-1">{badge}</div>}
      {action && <div className="ml-auto">{action}</div>}
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// 1. KPI Strip
// ─────────────────────────────────────────────────────────────────────────────

interface KpiCardProps {
  label: string
  value: string
  sub?: string
  positive?: boolean
  icon: React.ReactNode
  iconBg: string
  accentColor: string
}

function KpiCard({ label, value, sub, positive, icon, iconBg, accentColor }: KpiCardProps) {
  const valueColor =
    positive === undefined ? 'text-zinc-100' : positive ? 'text-emerald-600' : 'text-red-400'
  const gradientFrom =
    positive === true ? 'from-emerald-600/[0.04]' : positive === false ? 'from-red-600/[0.04]' : 'from-zinc-50/50'

  return (
    <div className={clsx(
      'card rounded-2xl  p-4 flex flex-col gap-2 border-l-2 relative overflow-hidden',
      accentColor,
    )}>
      <div className={clsx(
        'absolute inset-x-0 bottom-0 h-1/2 bg-gradient-to-t to-transparent pointer-events-none',
        gradientFrom,
      )} />
      <div className="flex items-center gap-2">
        <div className={clsx('w-6 h-6 rounded-lg flex items-center justify-center flex-shrink-0', iconBg)}>
          {icon}
        </div>
        <span className="text-[10px] font-sans font-medium text-zinc-400 tracking-widest uppercase truncate">
          {label}
        </span>
      </div>
      <span className={clsx('text-xl font-mono font-bold tabular-nums leading-none', valueColor)}>
        {value}
      </span>
      {sub && (
        <span className="text-[11px] font-mono text-zinc-500 tabular-nums">{sub}</span>
      )}
    </div>
  )
}

function KpiSkeleton() {
  return (
    <div className="card rounded-2xl  p-4 flex flex-col gap-3 animate-pulse">
      <div className="flex items-center gap-2">
        <div className="w-6 h-6 rounded-lg bg-zinc-800" />
        <div className="h-2.5 w-24 rounded bg-zinc-800" />
      </div>
      <div className="h-6 w-32 rounded-xl bg-zinc-800" />
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// 2. Equity Curve Chart
// ─────────────────────────────────────────────────────────────────────────────

type DateRange = '1W' | '1M' | '3M' | '6M' | '1Y' | 'ALL'
const DATE_RANGES: DateRange[] = ['1W', '1M', '3M', '6M', '1Y', 'ALL']

function filterByRange(data: { time: number; value: number }[], range: DateRange) {
  if (range === 'ALL' || data.length === 0) return data
  const now    = data[data.length - 1].time
  const DAY    = 86400
  const cutoff = { '1W': now - 7 * DAY, '1M': now - 30 * DAY, '3M': now - 90 * DAY, '6M': now - 180 * DAY, '1Y': now - 365 * DAY, 'ALL': 0 }[range]
  return data.filter((d) => d.time >= cutoff)
}

interface EquityCurveProps {
  analytics: PortfolioAnalytics
  range: DateRange
  onRangeChange: (r: DateRange) => void
}

function EquityCurveChart({ analytics, range, onRangeChange }: EquityCurveProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef     = useRef<IChartApi | null>(null)
  const seriesRef    = useRef<ISeriesApi<'Line'> | null>(null)
  const benchRef     = useRef<ISeriesApi<'Line'> | null>(null)

  useEffect(() => {
    if (!containerRef.current) return
    const chart = createChart(containerRef.current, {
      width:  containerRef.current.clientWidth,
      height: 260,
      layout: { background: { color: '#FFFFFF' }, textColor: '#6B7280', fontSize: 11 },
      grid: { vertLines: { color: '#F0EDE8' }, horzLines: { color: '#F0EDE8' } },
      crosshair: { mode: 1 },
      rightPriceScale: { borderColor: '#E8E4DF' },
      timeScale: { borderColor: '#E8E4DF', timeVisible: true, secondsVisible: false },
    })
    chartRef.current = chart
    seriesRef.current = chart.addLineSeries({ color: '#4F46E5', lineWidth: 2, title: 'Portfolio', priceLineVisible: false })
    benchRef.current  = chart.addLineSeries({ color: '#9CA3AF', lineWidth: 1, lineStyle: 2, title: 'SPY', priceLineVisible: false })

    const ro = new ResizeObserver((entries) => {
      for (const e of entries) chart.applyOptions({ width: e.contentRect.width })
    })
    ro.observe(containerRef.current)

    return () => {
      ro.disconnect()
      chart.remove()
      chartRef.current = seriesRef.current = benchRef.current = null
    }
  }, [])

  useEffect(() => {
    if (!seriesRef.current || !benchRef.current || !chartRef.current) return
    seriesRef.current.setData(filterByRange(analytics.equity_curve, range) as LineData[])
    benchRef.current.setData(filterByRange(analytics.benchmark_curve, range) as LineData[])
    chartRef.current.timeScale().fitContent()
  }, [analytics, range])

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center gap-1 flex-wrap">
        {DATE_RANGES.map((r) => (
          <button
            key={r}
            type="button"
            onClick={() => onRangeChange(r)}
            className={clsx(
              'px-2.5 py-1 text-[11px] font-mono rounded-lg transition-colors',
              range === r ? 'bg-indigo-600 text-white' : 'text-zinc-400 hover:bg-zinc-800 hover:text-zinc-100',
            )}
          >
            {r}
          </button>
        ))}
        <span className="ml-auto text-[10px] font-sans text-zinc-500 flex items-center gap-3">
          <span className="flex items-center gap-1.5">
            <span className="w-5 h-0.5 bg-indigo-600 inline-block rounded" />
            Portfolio
          </span>
          <span className="flex items-center gap-1.5">
            <span className="w-5 h-px bg-zinc-600 inline-block rounded" />
            SPY
          </span>
        </span>
      </div>
      <div ref={containerRef} className="rounded-xl overflow-hidden" />
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// 3. Daily P&L Bar Chart
// ─────────────────────────────────────────────────────────────────────────────

function DailyPnLChart({ data }: { data: DailyPnL[] }) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef     = useRef<IChartApi | null>(null)
  const seriesRef    = useRef<ISeriesApi<'Histogram'> | null>(null)

  useEffect(() => {
    if (!containerRef.current) return
    const chart = createChart(containerRef.current, {
      width:  containerRef.current.clientWidth,
      height: 200,
      layout: { background: { color: '#FFFFFF' }, textColor: '#6B7280', fontSize: 11 },
      grid: { vertLines: { color: '#F0EDE8' }, horzLines: { color: '#F0EDE8' } },
      crosshair: { mode: 1 },
      rightPriceScale: { borderColor: '#E8E4DF' },
      timeScale: { borderColor: '#E8E4DF', timeVisible: true, secondsVisible: false },
    })
    chartRef.current  = chart
    seriesRef.current = chart.addHistogramSeries({ color: '#10B981', priceLineVisible: false })

    const ro = new ResizeObserver((entries) => {
      for (const e of entries) chart.applyOptions({ width: e.contentRect.width })
    })
    ro.observe(containerRef.current)
    return () => { ro.disconnect(); chart.remove(); chartRef.current = seriesRef.current = null }
  }, [])

  useEffect(() => {
    if (!seriesRef.current || !chartRef.current) return
    const bars: HistogramData[] = data.map((d) => ({
      time:  (new Date(d.date).getTime() / 1000) as number,
      value: d.pnl,
      color: d.pnl >= 0 ? '#10B981' : '#EF4444',
    }) as HistogramData)
    seriesRef.current.setData(bars)
    chartRef.current.timeScale().fitContent()
  }, [data])

  const wins   = data.filter((d) => d.pnl >= 0).length
  const losses = data.filter((d) => d.pnl < 0).length
  const winPct = data.length > 0 ? Math.round((wins / data.length) * 100) : 0

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center gap-4 text-[11px] font-mono">
        <span className="text-emerald-600 font-semibold">{wins} up days</span>
        <span className="text-red-400 font-semibold">{losses} down days</span>
        <span className="text-zinc-400">{winPct}% win rate</span>
      </div>
      <div ref={containerRef} className="rounded-xl overflow-hidden" />
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// Sector color palette
// ─────────────────────────────────────────────────────────────────────────────

const SECTOR_PALETTE: Record<string, { bar: string; dot: string; hex: string }> = {
  Technology:               { bar: 'bg-indigo-500',  dot: 'bg-indigo-500',  hex: '#6366F1' },
  Healthcare:               { bar: 'bg-emerald-500', dot: 'bg-emerald-500', hex: '#10B981' },
  Financials:               { bar: 'bg-blue-500',    dot: 'bg-blue-500',    hex: '#3B82F6' },
  'Consumer Discretionary': { bar: 'bg-amber-500',   dot: 'bg-amber-500',   hex: '#F59E0B' },
  'Consumer Staples':       { bar: 'bg-yellow-500',  dot: 'bg-yellow-500',  hex: '#EAB308' },
  Industrials:              { bar: 'bg-cyan-500',    dot: 'bg-cyan-500',    hex: '#06B6D4' },
  Energy:                   { bar: 'bg-orange-500',  dot: 'bg-orange-500',  hex: '#F97316' },
  Materials:                { bar: 'bg-teal-500',    dot: 'bg-teal-500',    hex: '#14B8A6' },
  Utilities:                { bar: 'bg-violet-500',  dot: 'bg-violet-500',  hex: '#8B5CF6' },
  'Real Estate':            { bar: 'bg-pink-500',    dot: 'bg-pink-500',    hex: '#EC4899' },
  'Communication Services': { bar: 'bg-sky-500',     dot: 'bg-sky-500',     hex: '#0EA5E9' },
  ETF:                      { bar: 'bg-slate-400',   dot: 'bg-slate-400',   hex: '#94A3B8' },
  Unknown:                  { bar: 'bg-zinc-600',    dot: 'bg-zinc-600',    hex: '#9CA3AF' },
}
const pal = (sector: string) => SECTOR_PALETTE[sector] ?? SECTOR_PALETTE['Unknown']

// ─────────────────────────────────────────────────────────────────────────────
// 4. Position Exposure Panel
// ─────────────────────────────────────────────────────────────────────────────

function ExposurePanel({ exposure }: { exposure: ExposureBreakdown }) {
  const sorted = [...exposure.positions].sort((a, b) => b.value - a.value)
  const top5   = sorted.slice(0, 5)

  // conic-gradient for sector donut
  const sectorEntries = Object.entries(exposure.sector_weights).sort((a, b) => b[1] - a[1])
  let cumDeg = 0
  const conicParts = sectorEntries.map(([sector, pct]) => {
    const deg  = (pct / 100) * 360
    const part = `${pal(sector).hex} ${cumDeg.toFixed(1)}deg ${(cumDeg + deg).toFixed(1)}deg`
    cumDeg += deg
    return part
  })
  const conicGradient = `conic-gradient(${conicParts.join(', ')})`

  const totalValue = sorted.reduce((s, p) => s + p.value, 0)

  return (
    <div className="flex flex-col gap-5">
      {/* Stacked allocation bar */}
      <div>
        <div className="text-[10px] font-sans uppercase tracking-widest text-zinc-500 mb-2">
          Position Allocation
        </div>
        <div className="h-6 rounded-full overflow-hidden flex bg-zinc-800">
          {sorted.map((p) => {
            const pct = totalValue > 0 ? (p.value / totalValue) * 100 : 0
            return (
              <div
                key={p.symbol}
                title={`${p.symbol}: ${pct.toFixed(1)}%`}
                className={clsx('h-full flex items-center justify-center', pal(p.sector).bar)}
                style={{ width: `${pct}%` }}
              >
                {pct > 5 && (
                  <span className="text-[9px] font-mono font-bold text-white/90">{p.symbol}</span>
                )}
              </div>
            )
          })}
        </div>
      </div>

      {/* Donut + sector legend */}
      <div className="flex items-start gap-6">
        <div className="flex-shrink-0 relative">
          <div className="w-[88px] h-[88px] rounded-full" style={{ background: conicGradient }} />
          <div className="absolute inset-0 flex items-center justify-center">
            <div className="w-[52px] h-[52px] rounded-full bg-zinc-900 flex flex-col items-center justify-center">
              <span className="text-[8px] font-sans text-zinc-500 uppercase tracking-wider leading-none">Sectors</span>
              <span className="text-[13px] font-mono font-bold text-zinc-100 leading-none mt-0.5">
                {sectorEntries.length}
              </span>
            </div>
          </div>
        </div>
        <div className="flex flex-col gap-1.5 min-w-0">
          {sectorEntries.map(([sector, pct]) => (
            <div key={sector} className="flex items-center gap-2">
              <span className={clsx('w-2 h-2 rounded-full flex-shrink-0', pal(sector).dot)} />
              <span className="text-[11px] font-sans text-zinc-400 truncate flex-1">{sector}</span>
              <span className="text-[11px] font-mono text-zinc-400 tabular-nums">{pct.toFixed(1)}%</span>
            </div>
          ))}
        </div>
      </div>

      {/* Top 5 table */}
      <div>
        <div className="text-[10px] font-sans uppercase tracking-widest text-zinc-500 mb-2">Top Positions</div>
        <table className="w-full">
          <thead>
            <tr className="border-b border-zinc-800">
              {['Symbol', 'Sector', 'Value', 'Weight', 'P&L'].map((c, i) => (
                <th key={c} className={clsx('py-1.5 px-2 text-[10px] font-sans uppercase tracking-widest text-zinc-500', i < 2 ? 'text-left' : 'text-right')}>
                  {c}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {top5.map((p) => (
              <tr key={p.symbol} className="border-b border-zinc-800 hover:bg-zinc-900/60 transition-colors">
                <td className="py-2 px-2 font-mono text-sm font-semibold text-zinc-100">{p.symbol}</td>
                <td className="py-2 px-2">
                  <span className="flex items-center gap-1.5 text-[11px] font-sans text-zinc-400 max-w-[120px]">
                    <span className={clsx('w-1.5 h-1.5 rounded-full flex-shrink-0', pal(p.sector).dot)} />
                    <span className="truncate">{p.sector}</span>
                  </span>
                </td>
                <td className="py-2 px-2 font-mono text-sm text-zinc-400 tabular-nums text-right">
                  {fmtUSDCompact(p.value)}
                </td>
                <td className="py-2 px-2 text-right">
                  <div className="flex items-center justify-end gap-2">
                    <div className="w-12 h-1.5 rounded-full bg-zinc-800 overflow-hidden">
                      <div className="h-full rounded-full bg-indigo-500" style={{ width: `${Math.min(p.weight_pct, 100)}%` }} />
                    </div>
                    <span className="font-mono text-[11px] text-zinc-400 tabular-nums w-10 text-right">
                      {p.weight_pct.toFixed(1)}%
                    </span>
                  </div>
                </td>
                <td className="py-2 px-2 text-right">
                  <span className={clsx('text-[11px] font-mono font-semibold tabular-nums', p.pnl >= 0 ? 'text-emerald-600' : 'text-red-400')}>
                    {p.pnl >= 0 ? '+' : ''}{fmtUSD(p.pnl)}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// 5. Risk Metrics Panel
// ─────────────────────────────────────────────────────────────────────────────

function usagePct(used: number, limit: number): number {
  if (limit === 0) return 0
  return Math.min(Math.abs(used / limit) * 100, 100)
}

function riskCol(pct: number) {
  if (pct >= 80) return { bar: 'bg-red-500',    text: 'text-red-400',    bg: 'bg-red-500/10',    border: 'border-red-200'    }
  if (pct >= 60) return { bar: 'bg-amber-500',  text: 'text-amber-600',  bg: 'bg-amber-50',  border: 'border-amber-200'  }
  return             { bar: 'bg-emerald-500', text: 'text-emerald-600', bg: 'bg-emerald-50', border: 'border-emerald-200' }
}

function RiskGauge({ item }: { item: RiskLimits['limits'][0] }) {
  const pct = usagePct(item.used, item.limit)
  const col = riskCol(pct)

  const fmt = (v: number) => {
    if (item.unit === '$')     return fmtUSDCompact(v)
    if (item.unit === '%')     return v.toFixed(1) + '%'
    return String(Math.abs(v))
  }

  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-center justify-between">
        <span className="text-[11px] font-sans text-zinc-400">{item.label}</span>
        <span className={clsx('text-[11px] font-mono font-semibold tabular-nums', col.text)}>
          {fmt(item.used)}
          <span className="text-zinc-500 font-normal"> / {fmt(item.limit)}</span>
        </span>
      </div>
      <div className="h-2 rounded-full bg-zinc-800 overflow-hidden">
        <div className={clsx('h-full rounded-full transition-all duration-700', col.bar)} style={{ width: `${pct}%` }} />
      </div>
      <div className="flex items-center justify-between">
        <span className={clsx('text-[10px] font-mono', col.text)}>{pct.toFixed(0)}% used</span>
        {pct >= 80 && (
          <span className={clsx('text-[9px] font-sans font-semibold px-1.5 py-0.5 rounded-full border', col.bg, col.border, col.text)}>
            NEAR LIMIT
          </span>
        )}
      </div>
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// 6. Trade History Summary
// ─────────────────────────────────────────────────────────────────────────────

function TradeHistoryPanel({ trades }: { trades: TradeHistoryRow[] }) {
  const closed = trades.filter((t) => t.pnl !== undefined)
  const wins   = closed.filter((t) => (t.pnl ?? 0) > 0)
  const losses = closed.filter((t) => (t.pnl ?? 0) <= 0)
  const winPct = closed.length > 0 ? (wins.length / closed.length) * 100 : 0

  const best  = closed.reduce<TradeHistoryRow | null>((b, t) => !b || (t.pnl ?? 0) > (b.pnl ?? 0) ? t : b, null)
  const worst = closed.reduce<TradeHistoryRow | null>((b, t) => !b || (t.pnl ?? 0) < (b.pnl ?? 0) ? t : b, null)
  const withHold = closed.filter((t) => t.holding_days != null)
  const avgHoldDays = withHold.length > 0
    ? withHold.reduce((s, t) => s + (t.holding_days ?? 0), 0) / withHold.length
    : null

  return (
    <div className="flex flex-col gap-5">
      {/* Win/loss distribution bar */}
      <div>
        <div className="flex items-center justify-between mb-1.5">
          <span className="text-[10px] font-sans uppercase tracking-widest text-zinc-500">Win / Loss Distribution</span>
          <span className="text-[11px] font-mono text-zinc-400">{closed.length} closed trades</span>
        </div>
        <div className="h-5 rounded-full overflow-hidden flex bg-zinc-800">
          {closed.length > 0 && (
            <>
              <div
                className="h-full bg-emerald-500 flex items-center justify-center transition-all duration-700"
                style={{ width: `${winPct}%` }}
              >
                {winPct >= 20 && <span className="text-[9px] font-mono font-bold text-white">{wins.length}W</span>}
              </div>
              <div
                className="h-full bg-red-500 flex items-center justify-center transition-all duration-700"
                style={{ width: `${100 - winPct}%` }}
              >
                {(100 - winPct) >= 20 && <span className="text-[9px] font-mono font-bold text-white">{losses.length}L</span>}
              </div>
            </>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-4 mt-1.5">
          <span className="text-[11px] font-mono text-emerald-600">{winPct.toFixed(1)}% win rate</span>
          {avgHoldDays !== null && (
            <span className="text-[11px] font-mono text-zinc-400">Avg hold: {avgHoldDays.toFixed(1)} days</span>
          )}
          {best && (
            <span className="text-[11px] font-mono text-emerald-600 ml-auto">
              Best: {fmtUSD(best.pnl ?? 0)} ({best.symbol})
            </span>
          )}
          {worst && (
            <span className="text-[11px] font-mono text-red-400">
              Worst: {fmtUSD(worst.pnl ?? 0)} ({worst.symbol})
            </span>
          )}
        </div>
      </div>

      {/* Recent trades table */}
      <div>
        <div className="text-[10px] font-sans uppercase tracking-widest text-zinc-500 mb-2">Recent Trades</div>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[500px]">
            <thead>
              <tr className="border-b border-zinc-800">
                {['Time', 'Symbol', 'Side', 'Qty', 'Fill Price', 'P&L', 'Hold'].map((c, i) => (
                  <th key={c} className={clsx('py-2 px-2 text-[10px] font-sans uppercase tracking-widest text-zinc-500', i < 3 ? 'text-left' : 'text-right')}>
                    {c}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {trades.slice(0, 20).map((t) => {
                const isBuy = t.action === 'BUY'
                return (
                  <tr key={t.id} className={clsx('border-b border-zinc-800 transition-colors', isBuy ? 'hover:bg-emerald-500/[0.03]' : 'hover:bg-red-500/[0.03]')}>
                    <td className="py-2 px-2 font-mono text-[11px] text-zinc-500 whitespace-nowrap">{fmtDate(t.timestamp)}</td>
                    <td className="py-2 px-2 font-mono text-sm font-semibold text-zinc-100">{t.symbol}</td>
                    <td className="py-2 px-2">
                      <span className={clsx(
                        'inline-flex items-center gap-1 px-1.5 py-0.5 rounded-lg border text-[10px] font-mono font-semibold',
                        isBuy ? 'text-emerald-600 bg-emerald-50 border-emerald-200' : 'text-red-400 bg-red-500/10 border-red-200',
                      )}>
                        <span className={clsx('w-1 h-1 rounded-full', isBuy ? 'bg-emerald-500' : 'bg-red-500')} />
                        {t.action}
                      </span>
                    </td>
                    <td className="py-2 px-2 font-mono text-sm text-zinc-400 tabular-nums text-right">{t.quantity.toLocaleString('en-US')}</td>
                    <td className="py-2 px-2 font-mono text-sm text-zinc-400 tabular-nums text-right">{fmtUSD(t.fill_price)}</td>
                    <td className="py-2 px-2 font-mono text-sm tabular-nums text-right">
                      {t.pnl !== undefined
                        ? <span className={t.pnl >= 0 ? 'text-emerald-600' : 'text-red-400'}>{t.pnl >= 0 ? '+' : ''}{fmtUSD(t.pnl)}</span>
                        : <span className="text-zinc-500">—</span>}
                    </td>
                    <td className="py-2 px-2 font-mono text-[11px] text-zinc-500 text-right">
                      {t.holding_days !== undefined ? `${t.holding_days}d` : '—'}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// 7. Correlation Matrix
// ─────────────────────────────────────────────────────────────────────────────

function corrBg(v: number): string {
  if (v === 1)     return 'bg-zinc-800'
  const a = Math.abs(v)
  if (a >= 0.8)    return v > 0 ? 'bg-red-200'    : 'bg-blue-200'
  if (a >= 0.6)    return v > 0 ? 'bg-red-500/15'    : 'bg-blue-100'
  if (a >= 0.4)    return v > 0 ? 'bg-orange-100' : 'bg-indigo-100'
  if (a >= 0.2)    return v > 0 ? 'bg-amber-50'   : 'bg-sky-50'
  return 'bg-zinc-900'
}

function corrText(v: number): string {
  if (v === 1) return 'text-zinc-400'
  const a = Math.abs(v)
  if (a >= 0.6) return v > 0 ? 'text-red-700'    : 'text-blue-700'
  if (a >= 0.3) return v > 0 ? 'text-orange-700' : 'text-indigo-700'
  return 'text-zinc-400'
}

function CorrelationMatrixPanel({ matrix }: { matrix: CorrelationMatrix }) {
  const { symbols, matrix: mat } = matrix
  return (
    <div className="overflow-x-auto">
      <table className="border-collapse text-center" style={{ minWidth: (symbols.length + 1) * 72 }}>
        <thead>
          <tr>
            <th className="w-16 h-8" />
            {symbols.map((s) => (
              <th key={s} className="w-16 h-8 text-[10px] font-mono font-semibold text-zinc-400 tracking-wide">{s}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {symbols.map((rowSym, i) => (
            <tr key={rowSym}>
              <td className="text-[10px] font-mono font-semibold text-zinc-400 pr-2 text-right whitespace-nowrap">{rowSym}</td>
              {symbols.map((_, j) => {
                const v = mat[i][j]
                return (
                  <td key={j} className={clsx('w-14 h-10 text-[11px] font-mono font-semibold rounded-sm border border-white/50', corrBg(v), corrText(v))}>
                    {v.toFixed(2)}
                  </td>
                )
              })}
            </tr>
          ))}
        </tbody>
      </table>
      <div className="mt-3 flex items-center gap-4 text-[10px] font-sans text-zinc-400 flex-wrap">
        <span className="flex items-center gap-1"><span className="w-3 h-3 rounded-sm bg-red-200 inline-block" /> High positive (concentrated)</span>
        <span className="flex items-center gap-1"><span className="w-3 h-3 rounded-sm bg-zinc-900 border border-zinc-800 inline-block" /> Low (diversified)</span>
        <span className="flex items-center gap-1"><span className="w-3 h-3 rounded-sm bg-blue-200 inline-block" /> Negative (hedge)</span>
      </div>
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// Page
// ─────────────────────────────────────────────────────────────────────────────

export default function AnalyticsPage() {
  const { account }   = useAccountStore()
  const { simMode }   = useBotStore()
  const { simAccount } = useSimStore()

  const [range, setRange]             = useState<DateRange>('3M')
  const [loading, setLoading]         = useState(true)
  const [analytics, setAnalytics]     = useState<PortfolioAnalytics | null>(null)
  const [dailyPnL, setDailyPnL]       = useState<DailyPnL[] | null>(null)
  const [exposure, setExposure]       = useState<ExposureBreakdown | null>(null)
  const [riskLimits, setRiskLimits]   = useState<RiskLimits | null>(null)
  const [tradeHist, setTradeHist]     = useState<TradeHistoryRow[] | null>(null)
  const [correlation, setCorrelation] = useState<CorrelationMatrix | null>(null)

  const displayAccount = simMode ? simAccount : account

  const loadAll = useCallback(async () => {
    setLoading(true)
    const [r0, r1, r2, r3, r4, r5] = await Promise.allSettled([
      fetchPortfolioAnalytics(range),
      fetchDailyPnL(90),
      fetchExposureBreakdown(),
      fetchRiskLimits(),
      fetchTradeHistory(20),
      fetchCorrelationMatrix(),
    ])
    setAnalytics    (r0.status === 'fulfilled' ? r0.value : MOCK_PORTFOLIO)
    setDailyPnL     (r1.status === 'fulfilled' ? r1.value : MOCK_DAILY_PNL)
    setExposure     (r2.status === 'fulfilled' ? r2.value : MOCK_EXPOSURE)
    setRiskLimits   (r3.status === 'fulfilled' ? r3.value : MOCK_RISK_LIMITS)
    setTradeHist    (r4.status === 'fulfilled' ? r4.value : MOCK_TRADES)
    setCorrelation  (r5.status === 'fulfilled' ? r5.value : MOCK_CORRELATION)
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
    const dayPnl    = netLiq - prevValue
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

  const kpiData = liveAnalytics ?? MOCK_PORTFOLIO

  const kpis: KpiCardProps[] = [
    {
      label: 'Portfolio Value',
      value: fmtUSDCompact(kpiData.total_value),
      icon:        <IconDollar className="w-3.5 h-3.5 text-indigo-600" />,
      iconBg:      'bg-indigo-50',
      accentColor: 'border-l-indigo-500/60',
    },
    {
      label:    'Day P&L',
      value:    (kpiData.day_pnl >= 0 ? '+' : '') + fmtUSD(kpiData.day_pnl),
      sub:      fmtPct(kpiData.day_pnl_pct),
      positive: kpiData.day_pnl >= 0,
      icon:     kpiData.day_pnl >= 0
        ? <IconTrendUp   className="w-3.5 h-3.5 text-emerald-600" />
        : <IconTrendDown className="w-3.5 h-3.5 text-red-400" />,
      iconBg:      kpiData.day_pnl >= 0 ? 'bg-emerald-50' : 'bg-red-500/10',
      accentColor: kpiData.day_pnl >= 0 ? 'border-l-emerald-500/60' : 'border-l-red-500/60',
    },
    {
      label:    'Total P&L',
      value:    (kpiData.total_pnl >= 0 ? '+' : '') + fmtUSD(kpiData.total_pnl),
      sub:      fmtPct(kpiData.total_pnl_pct),
      positive: kpiData.total_pnl >= 0,
      icon:     kpiData.total_pnl >= 0
        ? <IconTrendUp   className="w-3.5 h-3.5 text-emerald-600" />
        : <IconTrendDown className="w-3.5 h-3.5 text-red-400" />,
      iconBg:      kpiData.total_pnl >= 0 ? 'bg-emerald-50' : 'bg-red-500/10',
      accentColor: kpiData.total_pnl >= 0 ? 'border-l-emerald-500/60' : 'border-l-red-500/60',
    },
    {
      label:    'Win Rate',
      value:    kpiData.win_rate.toFixed(1) + '%',
      positive: kpiData.win_rate >= 50,
      icon:     <IconBarChart className="w-3.5 h-3.5 text-blue-500" />,
      iconBg:      'bg-blue-50',
      accentColor: 'border-l-blue-500/40',
    },
    {
      label:    'Sharpe Ratio',
      value:    kpiData.sharpe_ratio.toFixed(2),
      positive: kpiData.sharpe_ratio >= 1 ? true : kpiData.sharpe_ratio >= 0 ? undefined : false,
      icon:     <IconShield className="w-3.5 h-3.5 text-violet-500" />,
      iconBg:      'bg-violet-50',
      accentColor: 'border-l-violet-400/50',
    },
    {
      label:    'Max Drawdown',
      value:    kpiData.max_drawdown_pct.toFixed(1) + '%',
      positive: kpiData.max_drawdown_pct >= -5 ? true : kpiData.max_drawdown_pct >= -15 ? undefined : false,
      icon:     <IconTrendDown className="w-3.5 h-3.5 text-rose-500" />,
      iconBg:      'bg-rose-50',
      accentColor: 'border-l-rose-400/50',
    },
  ]

  const showCorrelation = (correlation?.symbols.length ?? 0) >= 3

  return (
    <div className="flex flex-col gap-5">

      {/* ── Page title ──────────────────────────────────────────────────── */}
      <div className="flex items-center justify-between gap-4">
        <div>
          <div className="text-[10px] font-sans uppercase tracking-[0.22em] text-zinc-500">Stage 7</div>
          <h1 className="mt-1 text-2xl font-sans font-semibold text-zinc-50">Portfolio Analytics</h1>
        </div>
        <div className="flex items-center gap-2">
          {simMode && (
            <span className="text-[10px] font-mono px-2.5 py-1 rounded-lg bg-amber-600/15 text-amber-600 border border-amber-300/30">
              SIMULATION MODE
            </span>
          )}
          {loading && (
            <span className="text-[10px] font-mono text-zinc-500 animate-pulse">Loading…</span>
          )}
        </div>
      </div>

      {/* ── 1. KPI Strip ────────────────────────────────────────────────── */}
      <section className="animate-fade-in-up">
        <div className="text-[10px] font-sans uppercase tracking-[0.2em] text-zinc-500 mb-3">
          Portfolio KPIs
        </div>
        {loading && !analytics ? (
          <div className="grid grid-cols-2 sm:grid-cols-3 xl:grid-cols-6 gap-3">
            {Array.from({ length: 6 }).map((_, i) => <KpiSkeleton key={i} />)}
          </div>
        ) : (
          <div className="grid grid-cols-2 sm:grid-cols-3 xl:grid-cols-6 gap-3">
            {kpis.map((k) => <KpiCard key={k.label} {...k} />)}
          </div>
        )}
      </section>

      {/* ── 2. Equity Curve ─────────────────────────────────────────────── */}
      <section
        className="card rounded-2xl  p-5 animate-fade-in-up"
        style={{ animationDelay: '40ms' }}
      >
        <SectionHeader
          icon={<IconTrendUp className="w-3.5 h-3.5 text-indigo-500" />}
          eyebrow="Performance"
          title="Equity Curve"
          badge={<span className="text-[10px] font-mono px-2 py-0.5 rounded-lg bg-zinc-800 text-zinc-400">vs SPY</span>}
        />
        {liveAnalytics ? (
          <EquityCurveChart analytics={liveAnalytics} range={range} onRangeChange={setRange} />
        ) : (
          <div className="h-[300px] rounded-xl bg-zinc-800/40 animate-pulse" />
        )}
      </section>

      {/* ── 3. Daily P&L Bar Chart ───────────────────────────────────────── */}
      <section
        className="card rounded-2xl  p-5 animate-fade-in-up"
        style={{ animationDelay: '80ms' }}
      >
        <SectionHeader
          icon={<IconBarChart className="w-3.5 h-3.5 text-emerald-500" />}
          eyebrow="Daily"
          title="Daily P&L"
          badge={<span className="text-[10px] font-mono px-2 py-0.5 rounded-lg bg-zinc-800 text-zinc-400">90 days</span>}
        />
        {dailyPnL ? (
          <DailyPnLChart data={dailyPnL} />
        ) : (
          <div className="h-[220px] rounded-xl bg-zinc-800/40 animate-pulse" />
        )}
      </section>

      {/* ── 4 + 5. Exposure + Risk side-by-side ─────────────────────────── */}
      <div
        className="grid gap-4 xl:grid-cols-2 animate-fade-in-up"
        style={{ animationDelay: '120ms' }}
      >
        {/* 4. Position Exposure */}
        <section className="card rounded-2xl  p-5">
          <SectionHeader
            icon={<IconPieChart className="w-3.5 h-3.5 text-indigo-500" />}
            eyebrow="Allocation"
            title="Position Exposure"
            badge={
              exposure && (
                <span className="text-[10px] font-mono px-2 py-0.5 rounded-lg bg-zinc-800 text-zinc-400">
                  {exposure.positions.length} positions
                </span>
              )
            }
          />
          {exposure ? (
            <ExposurePanel exposure={exposure} />
          ) : (
            <div className="space-y-3 animate-pulse">
              <div className="h-6 rounded-full bg-zinc-800" />
              <div className="h-24 rounded-xl bg-zinc-800" />
            </div>
          )}
        </section>

        {/* 5. Risk Metrics */}
        <section className="card rounded-2xl  p-5">
          <SectionHeader
            icon={<IconShield className="w-3.5 h-3.5 text-rose-500" />}
            eyebrow="Risk Management"
            title="Risk Limit Usage"
          />
          {riskLimits ? (
            <div className="flex flex-col gap-5">
              {riskLimits.limits.map((item) => (
                <RiskGauge key={item.label} item={item} />
              ))}
              <p className="text-[10px] font-sans text-zinc-500 pt-1 border-t border-zinc-800">
                Bars turn amber at 60% and red at 80% of each limit.
              </p>
            </div>
          ) : (
            <div className="space-y-4 animate-pulse">
              {Array.from({ length: 4 }).map((_, i) => (
                <div key={i} className="flex flex-col gap-1.5">
                  <div className="h-3 w-40 rounded bg-zinc-800" />
                  <div className="h-2 rounded-full bg-zinc-800" />
                </div>
              ))}
            </div>
          )}
        </section>
      </div>

      {/* ── 6. Trade History Summary ─────────────────────────────────────── */}
      <section
        className="card rounded-2xl  p-5 animate-fade-in-up"
        style={{ animationDelay: '160ms' }}
      >
        <SectionHeader
          icon={<IconHistory className="w-3.5 h-3.5 text-zinc-400" />}
          eyebrow="Trades"
          title="Trade History Summary"
          badge={
            tradeHist && (
              <span className="text-[10px] font-mono px-2 py-0.5 rounded-lg bg-zinc-800 text-zinc-400">
                Last {Math.min(tradeHist.length, 20)}
              </span>
            )
          }
        />
        {tradeHist ? (
          <TradeHistoryPanel trades={tradeHist} />
        ) : (
          <div className="space-y-2.5 animate-pulse">
            {Array.from({ length: 5 }).map((_, i) => (
              <div key={i} className="flex gap-3">
                <div className="h-3 w-16 rounded bg-zinc-800" />
                <div className="h-3 w-12 rounded bg-zinc-800" />
                <div className="h-5 w-10 rounded bg-zinc-800" />
                <div className="h-3 w-8 rounded bg-zinc-800 ml-auto" />
                <div className="h-3 w-20 rounded bg-zinc-800" />
              </div>
            ))}
          </div>
        )}
      </section>

      {/* ── 7. Correlation Matrix (3+ positions only) ────────────────────── */}
      {showCorrelation && correlation && (
        <section
          className="card rounded-2xl  p-5 animate-fade-in-up"
          style={{ animationDelay: '200ms' }}
        >
          <SectionHeader
            icon={<IconGrid className="w-3.5 h-3.5 text-slate-500" />}
            eyebrow="Diversification"
            title="Correlation Matrix"
            badge={
              <span className="text-[10px] font-mono px-2 py-0.5 rounded-lg bg-zinc-800 text-zinc-400">
                {correlation.symbols.length} assets
              </span>
            }
          />
          <CorrelationMatrixPanel matrix={correlation} />
        </section>
      )}

    </div>
  )
}

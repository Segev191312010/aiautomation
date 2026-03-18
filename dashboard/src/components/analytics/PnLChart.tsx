/**
 * PnLChart — Daily cumulative P&L line chart using lightweight-charts.
 * Supports daily/weekly/monthly aggregation toggle and SPY benchmark overlay.
 */
import React, { useEffect, useRef, useState, useCallback } from 'react'
import clsx from 'clsx'
import type { DailyPnL, PortfolioAnalytics } from '@/types'

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmtUSD(v: number): string {
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: 0,
    maximumFractionDigits: 0,
  }).format(v)
}

type Bucket = 'daily' | 'weekly' | 'monthly'

interface AggPoint {
  time: string   // YYYY-MM-DD
  cumPnL: number
  trades: number
}

function aggregatePnL(daily: DailyPnL[], bucket: Bucket): AggPoint[] {
  if (!daily.length) return []

  if (bucket === 'daily') {
    let cum = 0
    return daily.map((d) => {
      cum += d.pnl
      return { time: d.date, cumPnL: cum, trades: d.trades }
    })
  }

  // Weekly / monthly: bucket by ISO week or YYYY-MM
  const getKey = (date: string) => {
    if (bucket === 'monthly') return date.slice(0, 7)
    // ISO week: get monday of the week
    const d = new Date(date)
    const day = d.getUTCDay() || 7
    const mon = new Date(d)
    mon.setUTCDate(d.getUTCDate() - day + 1)
    return mon.toISOString().slice(0, 10)
  }

  const buckets: Record<string, { pnl: number; trades: number; firstDate: string }> = {}
  for (const d of daily) {
    const key = getKey(d.date)
    if (!buckets[key]) buckets[key] = { pnl: 0, trades: 0, firstDate: d.date }
    buckets[key].pnl    += d.pnl
    buckets[key].trades += d.trades
  }

  let cum = 0
  return Object.entries(buckets)
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([key, v]) => {
      cum += v.pnl
      return { time: key, cumPnL: cum, trades: v.trades }
    })
}

// ── Chart component ───────────────────────────────────────────────────────────

interface Props {
  dailyPnL:  DailyPnL[]
  analytics: PortfolioAnalytics | null
  loading:   boolean
}

export default function PnLChart({ dailyPnL, analytics, loading }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef     = useRef<unknown>(null)
  const seriesRef    = useRef<unknown>(null)
  const benchRef     = useRef<unknown>(null)
  const [bucket, setBucket] = useState<Bucket>('daily')
  const [showBench, setShowBench] = useState(true)

  const buildChart = useCallback(async () => {
    if (!containerRef.current) return
    const { createChart, ColorType, LineStyle } = await import('lightweight-charts')

    if (chartRef.current) {
      (chartRef.current as { remove(): void }).remove()
      chartRef.current = null
      seriesRef.current = null
      benchRef.current  = null
    }

    const chart = createChart(containerRef.current, {
      layout: {
        background:  { type: ColorType.Solid, color: 'transparent' },
        textColor:   '#9ca3af',
        fontFamily:  'ui-monospace, monospace',
        fontSize:    11,
      },
      grid: {
        vertLines:   { color: 'rgba(107,114,128,0.12)' },
        horzLines:   { color: 'rgba(107,114,128,0.12)' },
      },
      crosshair: { mode: 1 },
      rightPriceScale: { borderColor: 'rgba(107,114,128,0.2)' },
      timeScale: {
        borderColor:   'rgba(107,114,128,0.2)',
        timeVisible:   true,
        secondsVisible: false,
      },
      handleScroll:  true,
      handleScale:   true,
    })

    // P&L series (line, colored by sign of last point)
    const aggData = aggregatePnL(dailyPnL, bucket)
    const lastVal = aggData.length > 0 ? aggData[aggData.length - 1].cumPnL : 0
    const lineColor = lastVal >= 0 ? '#10b981' : '#ef4444'
    const areaTop   = lastVal >= 0 ? 'rgba(16,185,129,0.15)' : 'rgba(239,68,68,0.15)'

    const series = chart.addAreaSeries({
      lineColor,
      topColor:    areaTop,
      bottomColor: 'transparent',
      lineWidth:   2,
      priceLineVisible: false,
    })
    series.setData(aggData.map((d) => ({ time: d.time, value: d.cumPnL })) as any)

    // Benchmark overlay (SPY, re-scaled to same starting point as portfolio)
    if (showBench && analytics?.benchmark_curve?.length) {
      const bench = analytics.benchmark_curve
      const benchStart = bench[0]?.value ?? 1
      const pnlStart   = (analytics.equity_curve[0]?.value ?? 0) || 0

      const benchSeries = chart.addLineSeries({
        color:             'rgba(99,102,241,0.55)',
        lineWidth:         1,
        lineStyle:         LineStyle.Dashed,
        priceLineVisible:  false,
        lastValueVisible:  false,
      })
      const rescaled = bench.map((p) => ({
        time:  p.time as unknown as number,
        value: ((p.value / benchStart) - 1) * Math.abs(pnlStart || 1),
      }))
      benchSeries.setData(rescaled as any)
      benchRef.current = benchSeries
    }

    chart.timeScale().fitContent()
    chartRef.current  = chart
    seriesRef.current = series

    // Resize observer
    const ro = new ResizeObserver(() => {
      if (containerRef.current && chartRef.current) {
        (chartRef.current as { applyOptions(o: unknown): void }).applyOptions({
          width: containerRef.current.clientWidth,
        })
      }
    })
    ro.observe(containerRef.current)

    return () => {
      ro.disconnect()
      if (chartRef.current) (chartRef.current as { remove(): void }).remove()
    }
  }, [dailyPnL, bucket, showBench, analytics])

  useEffect(() => {
    let cleanup: (() => void) | undefined
    buildChart().then((fn) => { cleanup = fn })
    return () => { cleanup?.() }
  }, [buildChart])

  return (
    <div className="flex flex-col gap-3">
      {/* Toolbar */}
      <div className="flex items-center justify-between flex-wrap gap-2">
        {/* Bucket toggle */}
        <div className="flex rounded-xl overflow-hidden border border-gray-200 text-xs font-mono">
          {(['daily', 'weekly', 'monthly'] as Bucket[]).map((b) => (
            <button
              key={b}
              onClick={() => setBucket(b)}
              className={clsx(
                'px-3 py-1.5 capitalize transition-colors duration-100',
                bucket === b
                  ? 'bg-gray-100 text-gray-800 font-semibold'
                  : 'text-gray-400 hover:text-gray-600',
              )}
            >
              {b}
            </button>
          ))}
        </div>

        {/* Benchmark toggle */}
        <button
          onClick={() => setShowBench((p) => !p)}
          className={clsx(
            'flex items-center gap-1.5 text-[11px] font-mono px-3 py-1.5 rounded-xl border transition-colors duration-100',
            showBench
              ? 'border-indigo-500/30 bg-indigo-500/10 text-indigo-400'
              : 'border-gray-200 text-gray-400 hover:text-gray-600',
          )}
        >
          <span className="w-5 h-px border-t-2 border-dashed border-current inline-block" />
          SPY benchmark
        </button>
      </div>

      {/* Chart */}
      {loading && !dailyPnL.length ? (
        <div className="h-64 rounded-2xl bg-gray-100/40 animate-pulse" />
      ) : !dailyPnL.length ? (
        <div className="h-64 rounded-2xl border border-gray-200 flex items-center justify-center text-sm text-gray-400">
          No P&L history available
        </div>
      ) : (
        <div ref={containerRef} className="h-64 w-full" />
      )}
    </div>
  )
}

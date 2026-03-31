import React, { useEffect, useRef } from 'react'
import clsx from 'clsx'
import {
  createChart,
  type IChartApi,
  type ISeriesApi,
  type LineData,
} from 'lightweight-charts'
import type { PortfolioAnalytics } from '@/types'

export type DateRange = '1W' | '1M' | '3M' | '6M' | '1Y' | 'ALL'
export const DATE_RANGES: DateRange[] = ['1W', '1M', '3M', '6M', '1Y', 'ALL']

function filterByRange(data: { time: number; value: number }[], range: DateRange) {
  if (range === 'ALL' || data.length === 0) return data
  const now    = data[data.length - 1].time
  const DAY    = 86400
  const cutoff = { '1W': now - 7 * DAY, '1M': now - 30 * DAY, '3M': now - 90 * DAY, '6M': now - 180 * DAY, '1Y': now - 365 * DAY, 'ALL': 0 }[range]
  return data.filter((d) => d.time >= cutoff)
}

interface EquityCurveChartProps {
  analytics: PortfolioAnalytics
  range: DateRange
  onRangeChange: (r: DateRange) => void
}

export function EquityCurveChart({ analytics, range, onRangeChange }: EquityCurveChartProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef     = useRef<IChartApi | null>(null)
  const seriesRef    = useRef<ISeriesApi<'Line'> | null>(null)
  const benchRef     = useRef<ISeriesApi<'Line'> | null>(null)
  const hasBenchmark = analytics.benchmark_curve.length > 0

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
          {hasBenchmark && (
            <span className="flex items-center gap-1.5">
              <span className="w-5 h-px bg-zinc-600 inline-block rounded" />
              SPY
            </span>
          )}
        </span>
      </div>
      <div ref={containerRef} className="rounded-xl overflow-hidden" />
    </div>
  )
}
